"""
AI 日期提取器
- 从 OSS 下载文件
- 按 file_type 抽取文本
- 调用 DeepSeek 抽取发布日期 / 适用学期
- 写回 KnowledgeDoc.freshness_date
"""
# 1. 标准库
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Optional

# 2. Django
from django.utils import timezone

# 3. 第三方
import requests

# 4. 本地
from utils.llm import llm_client
from utils.oss import oss_client

logger = logging.getLogger(__name__)


# 取前 N 字符喂给 LLM，控制 token 消耗
TEXT_SNIPPET_CHARS = 6000
# 年份合理范围（过滤掉"1990前"这种无关年份）
MIN_YEAR = 2000
MAX_YEAR = 2099


@dataclass
class FreshnessResult:
    """日期提取结果"""
    freshness_date: Optional[str]   # YYYY-MM-DD 或 None
    confidence: str                  # high / medium / low / none
    source_excerpt: str              # 出处片段
    raw_response: dict               # LLM 原始返回（便于追溯）


# ------------------------------------------------------------------
# 文本抽取（抽自 config/celery.py 的实现，复用之）
# ------------------------------------------------------------------
def _parse_pdf(file_path: str) -> str:
    # 先尝试 PyMuPDF（性能更好）
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(file_path)
        text = ''.join(page.get_text() + '\n' for page in doc)
        doc.close()
        return text
    except Exception:
        pass

    # 回退到 pdfplumber
    try:
        import pdfplumber
        text = ''
        with pdfplumber.open(file_path) as pdf:
            # 移除页数限制，读取全部内容
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text += t + '\n'
        return text
    except Exception as e:
        logger.warning(f'PDF 解析失败: {e}')
        return ''


def _parse_docx(file_path: str) -> str:
    """DOCX 解析：段落 + 表格 + 文本框"""
    try:
        from docx import Document
        doc = Document(file_path)

        # 1. 提取段落
        text_parts = [p.text for p in doc.paragraphs if p.text.strip()]

        # 2. 提取表格（行政文档核心内容常在表格里）
        for table in doc.tables:
            for row in table.rows:
                row_text = '\t'.join(cell.text.strip() for cell in row.cells)
                if row_text.strip():
                    text_parts.append(row_text)

        return '\n'.join(text_parts)
    except Exception as e:
        logger.warning(f'DOCX 解析失败: {e}')
        return ''


def _parse_pptx(file_path: str) -> str:
    """PPTX 解析：全部幻灯片"""
    try:
        from pptx import Presentation
        prs = Presentation(file_path)
        text = ''
        for slide in prs.slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    text += shape.text_frame.text + '\n'
        return text
    except Exception as e:
        logger.warning(f'PPTX 解析失败: {e}')
        return ''


def _parse_xlsx(file_path: str) -> str:
    """XLSX 解析：全部 sheet，全部行"""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(file_path, data_only=True)
        text = ''
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            # 添加 sheet 标题便于上下文
            text += f'=== 工作表: {sheet_name} ===\n'
            for row in ws.iter_rows(values_only=True):
                row_text = '\t'.join(str(c) if c is not None else '' for c in row)
                if row_text.strip():
                    text += row_text + '\n'
        return text
    except Exception as e:
        logger.warning(f'XLSX 解析失败: {e}')
        return ''


def _parse_xls(file_path: str) -> str:
    """XLS 旧格式解析（xlrd 回退方案）"""
    try:
        import xlrd
        # xlrd 2.0+ 不支持 .xlsx，这里只处理 .xls
        workbook = xlrd.open_workbook(file_path, formatting_info=False, on_demand=True)
        text = ''
        for sheet_idx in range(workbook.nsheets):
            sheet = workbook.sheet_by_index(sheet_idx)
            text += f'=== 工作表: {sheet.name} ===\n'
            for row_idx in range(sheet.nrows):
                row = sheet.row_values(row_idx)
                row_text = '\t'.join(str(cell) if cell not in ('', None) else '' for cell in row)
                if row_text.strip():
                    text += row_text + '\n'
        workbook.release_resources()
        return text
    except ImportError:
        logger.warning('xlrd 未安装，XLS 解析不可用（pip install xlrd==1.2.0）')
        return ''
    except Exception as e:
        logger.warning(f'XLS 解析失败: {e}')
        return ''


def _parse_doc(file_path: str) -> str:
    """DOC 旧格式解析（antiword 或 docx2txt 回退方案）"""
    # 方案1: antiword（Linux，最快）
    try:
        import subprocess
        result = subprocess.run(['antiword', file_path], capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return result.stdout
    except (FileNotFoundError, ImportError, subprocess.TimeoutExpired, Exception):
        pass

    # 方案2: docx2txt（跨平台）
    try:
        import docx2txt
        return docx2txt.process(file_path)
    except ImportError:
        logger.warning('docx2txt 未安装，DOC 解析不可用（pip install docx2txt）')
        return ''
    except Exception as e:
        logger.warning(f'DOC 解析失败: {e}')
        return ''

    # 方案3: LibreOffice 转换（最兼容但慢）
    try:
        import subprocess
        import tempfile
        import os

        # 创建临时目录
        with tempfile.TemporaryDirectory() as tmpdir:
            # 调用 LibreOffice 转换为 txt
            subprocess.run([
                'soffice', '--headless', '--convert-to', 'txt',
                '--outdir', tmpdir, file_path
            ], capture_output=True, timeout=60)

            # 读取转换后的 txt
            txt_path = os.path.join(tmpdir, os.path.splitext(os.path.basename(file_path))[0] + '.txt')
            if os.path.exists(txt_path):
                with open(txt_path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read()
    except (FileNotFoundError, ImportError, subprocess.TimeoutExpired, Exception):
        pass

    return ''


_EXTRACTORS = {
    'pdf': _parse_pdf,
    'doc': _parse_doc,      # 旧格式 DOC
    'docx': _parse_docx,    # 新格式 DOCX
    'ppt': _parse_pptx,     # PPT 实际上也是 .pptx 格式
    'pptx': _parse_pptx,    # PPTX
    'xls': _parse_xls,      # 旧格式 XLS
    'xlsx': _parse_xlsx,    # 新格式 XLSX
    'txt': lambda p: open(p, 'r', encoding='utf-8', errors='ignore').read(),
    'md': lambda p: open(p, 'r', encoding='utf-8', errors='ignore').read(),
    'html': lambda p: open(p, 'r', encoding='utf-8', errors='ignore').read(),
    'htm': lambda p: open(p, 'r', encoding='utf-8', errors='ignore').read(),
}


def extract_text_from_oss(oss_key: str, file_type: str) -> str:
    """
    从 OSS 下载文件并抽取文本。
    """
    from utils.http_client import ExternalClient

    if not oss_key:
        return ''

    # 1. 生成签名下载 URL
    download_url = oss_client.get_download_url(oss_key, expires=600)
    if not download_url:
        logger.error(f'无法生成下载 URL: {oss_key}')
        return ''

    # 2. 下载到临时文件
    suffix = f'.{file_type}' if file_type else '.bin'
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        resp = ExternalClient.get('oss_download', download_url)
        resp.raise_for_status()
        with open(tmp_path, 'wb') as f:
            f.write(resp.content)

        # 3. 抽取文本
        extractor = _EXTRACTORS.get(file_type.lower())
        if not extractor:
            # 纯文本类型：移除截断限制，读取全部内容
            with open(tmp_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        return extractor(tmp_path)
    except Exception as e:
        logger.error(f'下载/抽取失败 oss_key={oss_key}: {e}')
        return ''
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


# ------------------------------------------------------------------
# Prompt + LLM 推理
# ------------------------------------------------------------------
_EXTRACT_PROMPT = """你是一个精确的文档元数据提取助手。请从下面的文档内容中提取"发布日期 / 适用学期 / 版本日期"。

【优先级】
1. 封面、页眉、页脚的"日期"、"版本"、"修订日期"、"发布于"、"YYYY 年 X 月"
2. 正文中的"适用对象"、"本资料针对 XXXX 年 XX 学期"、"适用于 2024-2025 学年"
3. 文件元数据中的创建时间（如可从文档属性获取）
4. 没有明确日期时返回 null，不要猜测

【判断规则】
- 必须能在文档中找到原文支撑，不能凭空推断
- 学年/学期表达（如"2024-2025 学年第一学期"）→ 取起始年第一天作为代表日期
- 多个日期时优先取"修订日期"或"适用学期"，其次取"发布日期"

【输出 JSON（不要任何额外文字）】
{
  "date": "YYYY-MM-DD"  | null,
  "confidence": "high" | "medium" | "low",
  "source_excerpt": "不超过 80 字的原文引用，包含日期上下文",
  "reasoning": "不超过 50 字，说明你为什么这么判断"
}
"""


def _try_parse_json(text: str) -> Optional[dict]:
    """从 LLM 回复中尝试提取 JSON"""
    # 1. 直接解析
    try:
        return json.loads(text)
    except Exception:
        pass

    # 2. 提取 ```json ... ``` 块
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass

    # 3. 提取第一个 { ... } 块
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass

    return None


def _validate_date(date_str: Optional[str]) -> Optional[str]:
    """校验日期合法且在合理范围内"""
    if not date_str:
        return None
    m = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})', date_str.strip())
    if not m:
        # 尝试 YYYY/MM/DD
        m = re.fullmatch(r'(\d{4})/(\d{2})/(\d{2})', date_str.strip())
        if m:
            return f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
        # 尝试 YYYY 年 M 月
        m = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?', date_str)
        if m:
            y, mo, d = m.group(1), m.group(2), m.group(3) or '01'
            return f'{y}-{int(mo):02d}-{int(d):02d}'
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (MIN_YEAR <= y <= MAX_YEAR):
        return None
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return date_str


def extract_freshness_with_llm(text: str) -> FreshnessResult:
    """
    调用 DeepSeek 提取发布日期。
    失败时返回 confidence='none' 的空结果，不抛异常。
    """
    if not text or not text.strip():
        return FreshnessResult(None, 'none', '', {})

    # 截取前 N 字符
    snippet = text[:TEXT_SNIPPET_CHARS]
    messages = [
        {'role': 'system', 'content': _EXTRACT_PROMPT},
        {'role': 'user', 'content': f'【文档内容（前 {len(snippet)} 字符）】\n{snippet}'},
    ]

    raw = llm_client.chat(messages, temperature=0.2, max_tokens=400)
    if not raw:
        return FreshnessResult(None, 'none', '', {'error': 'LLM 返回为空'})

    parsed = _try_parse_json(raw)
    if not parsed:
        return FreshnessResult(None, 'low', '', {'raw': raw})

    date = _validate_date(parsed.get('date'))
    confidence = parsed.get('confidence', 'low')
    if confidence not in ('high', 'medium', 'low'):
        confidence = 'low'
    if not date:
        confidence = 'none'

    return FreshnessResult(
        freshness_date=date,
        confidence=confidence,
        source_excerpt=(parsed.get('source_excerpt') or '').strip()[:200],
        raw_response={'parsed': parsed, 'raw': raw[:500]},
    )


# ------------------------------------------------------------------
# 对外暴露的完整提取入口
# ------------------------------------------------------------------
def extract_freshness_for_doc(doc_id: int) -> Optional[FreshnessResult]:
    """
    对指定 KnowledgeDoc 运行完整提取流程，并把结果写回数据库。

    Returns:
        FreshnessResult 或 None（doc 不存在时）
    """
    from apps.knowledge.models import KnowledgeDoc

    try:
        doc = KnowledgeDoc.objects.get(id=doc_id)
    except KnowledgeDoc.DoesNotExist:
        logger.warning(f'extract_freshness_for_doc: doc_id={doc_id} 不存在')
        return None

    if not doc.oss_key:
        logger.info(f'doc_id={doc_id} 没有 oss_key，跳过')
        return FreshnessResult(None, 'none', '该文档没有可下载的文件', {})

    # 1. 抽文本
    text = extract_text_from_oss(doc.oss_key, doc.file_type)
    if not text.strip():
        # 不支持的类型（图片/视频/音频），直接记 none
        result = FreshnessResult(None, 'none', f'文件类型 {doc.file_type} 暂不支持文本提取', {})
        _save_result(doc, result)
        return result

    # 2. 调 LLM
    result = extract_freshness_with_llm(text)

    # 3. 写回
    _save_result(doc, result)
    logger.info(
        f'doc_id={doc_id} 日期提取完成: {result.freshness_date} '
        f'(confidence={result.confidence})'
    )
    return result


def _save_result(doc, result: FreshnessResult) -> None:
    """把结果写回 doc.freshness_date，保留人工值"""
    # 将字符串日期转换为 date 对象
    if result.freshness_date:
        from datetime import datetime
        doc.freshness_date = datetime.strptime(result.freshness_date, '%Y-%m-%d').date()
    else:
        doc.freshness_date = None
    doc.save(update_fields=['freshness_date', 'updated_at'])
