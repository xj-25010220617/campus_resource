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
from typing import Optional, Tuple

# 2. Django
from django.conf import settings
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
# ------------------------------------------------------------------
# 解析结果（结构化）
# ------------------------------------------------------------------
@dataclass
class ParseResult:
    """单文件解析结果。

    文档类解析器只填 text；PDF / 图片解析器额外填页级统计，让索引层能区分
    「完整解析」「部分解析（OCR 失败页 / 超页截断）」「完全没解析出正文」，
    避免把不完整解析伪装成成功索引。
    """
    text: str = ''
    error: str = ''                 # 非空 = 解析通道出错（依赖缺失 / 类型不支持 / OCR 不可用）
    pages_total: int = 0
    pages_native: int = 0           # 用原生文字层解析的页数
    pages_ocr: int = 0              # OCR 成功产出文字的页数
    pages_ocr_failed: int = 0       # OCR 报错或未识别出文字的页数
    pages_ocr_skipped: int = 0      # 因超出 OCR_MAX_PAGES 未尝试 OCR 的页数
    truncated: bool = False         # 是否发生截断（OCR 预算耗尽后仍有页面需要 OCR）
    structured: bool = False        # 表格/名单类文档已按『字段名：值』结构化
    record_count: int = 0           # 结构化记录条数（供计数类问题精确作答）
    table_schema: Optional[dict] = None  # 表格结构元数据（供表格问答引擎生成 SQL）

    @property
    def is_empty(self) -> bool:
        return not (self.text or '').strip()

    def issues(self) -> str:
        """把「解析不完整/失败」的原因压成一行，供 KnowledgeDoc.index_error 记录。"""
        parts = []
        if self.error:
            parts.append(self.error)
        if self.pages_ocr_failed:
            parts.append(f'{self.pages_ocr_failed} 页 OCR 失败/未识别出文字')
        if self.truncated:
            parts.append(f'超出 OCR_MAX_PAGES，{self.pages_ocr_skipped} 页未 OCR')
        return '；'.join(parts)


# ------------------------------------------------------------------
# 扫描件 OCR（文字层抽取为空时的兜底）
# ------------------------------------------------------------------
_easyocr_reader = None
_easyocr_reader_failed = False


def _get_easyocr_reader():
    """easyocr 懒加载单例：首次 OCR 才下载/加载模型，失败后不再重试。"""
    global _easyocr_reader, _easyocr_reader_failed
    if _easyocr_reader is None and not _easyocr_reader_failed:
        try:
            import easyocr
            _easyocr_reader = easyocr.Reader(['ch_sim', 'en'], gpu=False, verbose=False)
        except Exception as e:
            logger.error(f'easyocr 初始化失败: {e}')
            _easyocr_reader_failed = True
    return _easyocr_reader


def _render_page_to_image(page) -> Optional[bytes]:
    """把 fitz 页面渲染为 PNG 字节（200 DPI，供 OCR）。"""
    try:
        pix = page.get_pixmap(dpi=200)
        return pix.tobytes('png')
    except Exception as e:
        logger.warning(f'PDF 页面渲染失败: {e}')
        return None


def _ocr_image_detailed(image_bytes: bytes) -> Tuple[str, str]:
    """本地 easyocr 识别，返回 (text, error)。

    error 非空表示 OCR 通道本身不可用（未安装 / 初始化失败 / 解码异常），
    与「图片里本来就没有文字」（返回空文本、error 为空）区分开，
    避免后者被误记成依赖故障、前者被误记成正常空页。
    """
    reader = _get_easyocr_reader()
    if reader is None:
        return '', 'EasyOCR 不可用（未安装或初始化失败）'

    import io
    import numpy as np
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(image_bytes)).convert('RGB')
        arr = np.asarray(img)
        results = reader.readtext(arr, detail=0, paragraph=True)
        return '\n'.join(str(t) for t in results).strip(), ''
    except Exception as e:
        logger.warning(f'easyocr 识别失败: {e}')
        return '', f'OCR 识别失败: {type(e).__name__}: {e}'


def _ocr_image_local(image_bytes: bytes) -> str:
    """本地 easyocr 识别（主通道，仅取文本，丢弃失败原因）。"""
    return _ocr_image_detailed(image_bytes)[0]


def _ocr_image(image_bytes: bytes) -> str:
    """单页 OCR 的文本入口（保留旧签名）。

    需要区分「OCR 不可用」与「图中无文字」的调用方请直接用
    `_ocr_image_detailed`，它同时返回失败原因。
    """
    return _ocr_image_local(image_bytes)


def _parse_pdf(file_path: str) -> ParseResult:
    """PDF 解析：逐页判定，文字层不足的页单独转图片走 OCR。

    逐页判定是必须的。早期实现按「全文文字层长度」一次性判定：
    混合型 PDF（文字页 + 扫描页）只要有 20 字文字层就整份跳过 OCR，扫描页内容
    永久丢失；纯扫描件若每页只剩页码，页码累计也能超过阈值而被误判为文字 PDF，
    正文一个字都不会 OCR。
    """
    result = ParseResult()
    page_min_chars = int(getattr(settings, 'OCR_PAGE_MIN_TEXT_CHARS', 20))
    max_ocr_pages = int(getattr(settings, 'OCR_MAX_PAGES', 20))

    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        # 依赖缺失必须显式失败：早前 _ocr_pdf 里的 import 在保护范围之外，
        # ModuleNotFoundError 被上层宽 except 吞成空字符串，最终被当成
        # 「扫描件无文字」静默降级，掩盖了环境问题。
        result.error = f'PyMuPDF 未安装，PDF 无法解析: {e}'
        logger.error(result.error)
        return result

    try:
        doc = fitz.open(file_path)
    except Exception as e:
        result.error = f'PDF 打开失败: {type(e).__name__}: {e}'
        logger.warning(result.error)
        return result

    parts = []
    ocr_attempts = 0
    try:
        result.pages_total = doc.page_count
        for page_no, page in enumerate(doc, start=1):
            native = ''
            try:
                native = (page.get_text() or '').strip()
            except Exception as e:
                logger.warning(f'PDF 第 {page_no} 页文字层提取失败: {e}')

            # 0) 表格优先：该页检测到表格时，直接输出结构化记录（字段名标注），
            #    避免正文文字层的列错位；无表格才走原生文字 / OCR 兜底。
            table_text, table_count = _pdf_page_tables(page_no, page)
            if table_text:
                result.structured = True
                result.record_count += table_count
                result.pages_native += 1
                parts.append(table_text)
                continue

            # 1) 该页文字层足够 → 直接用原生文字，不付 OCR 成本
            if len(native) >= page_min_chars:
                result.pages_native += 1
                parts.append(native)
                continue

            # 2) 该页文字层不足 → 该页转图片 OCR
            if ocr_attempts >= max_ocr_pages:
                # 预算耗尽：不再尝试，但必须让上层知道这份文档没解析完整
                result.truncated = True
                result.pages_ocr_skipped += 1
                continue
            ocr_attempts += 1

            image_bytes = _render_page_to_image(page)
            if not image_bytes:
                result.pages_ocr_failed += 1
                continue

            ocr_text, ocr_error = _ocr_image_detailed(image_bytes)
            if ocr_error:
                result.pages_ocr_failed += 1
                if not result.error:
                    result.error = ocr_error
                continue

            if ocr_text:
                result.pages_ocr += 1
                parts.append(ocr_text)
            else:
                # OCR 通道正常但该页没识别出文字
                result.pages_ocr_failed += 1
    finally:
        doc.close()

    result.text = '\n'.join(parts)
    if result.truncated:
        logger.warning(
            f'PDF 共 {result.pages_total} 页，OCR 上限 {max_ocr_pages} 页已用尽，'
            f'{result.pages_ocr_skipped} 页未 OCR'
        )
    logger.info(
        'PDF 解析: 共 %d 页，原生文字 %d 页，OCR 成功 %d 页，OCR 失败 %d 页，未 OCR %d 页',
        result.pages_total, result.pages_native, result.pages_ocr,
        result.pages_ocr_failed, result.pages_ocr_skipped,
    )
    return result


def _parse_image(file_path: str) -> ParseResult:
    """图片解析：直接复用现有本地 OCR 识别图中文字。

    图片没有任何文字层可退，所以只有两条路：OCR 出文字，或明确失败。
    绝不允许把图片二进制按 UTF-8 读成乱码当正文（那会污染向量库）。
    """
    result = ParseResult(pages_total=1)

    try:
        with open(file_path, 'rb') as f:
            image_bytes = f.read()
    except OSError as e:
        result.error = f'图片读取失败: {type(e).__name__}: {e}'
        logger.warning(result.error)
        return result

    if not image_bytes:
        result.error = '图片内容为空'
        return result

    text, ocr_error = _ocr_image_detailed(image_bytes)
    if ocr_error:
        result.error = ocr_error
        result.pages_ocr_failed = 1
        logger.warning(f'图片 OCR 不可用: {ocr_error}')
        return result

    result.text = text
    if text:
        result.pages_ocr = 1
    else:
        # 通道正常但图中没有可识别文字（纯色图/照片等）
        result.pages_ocr_failed = 1
    return result


def _records_from_grid(rows) -> Tuple[str, int, bool]:
    """把二维表格网格转成『字段名标注记录』文本，供向量化与计数。

    表头取第一个"看起来像表头"的行（满足：该行有 ≥2 个非空单元格）。
    若首行是跨列合并的标题（只有 1 个非空格），则跳过它、用第二行当表头。
    每条记录保留『字段名：值』的显式标注，避免裸数据（`3\\t李四\\t5000`）
    在向量化后字段语义丢失。

    Returns:
        (text, record_count, structured)
        - text 首行是『本表格共 N 条记录；字段：…』概要 + 逐条记录
        - structured=False 表示该网格不适合按表格解析（单列/无数据），退化为普通文本
    """
    if not rows:
        return '', 0, False

    clean = [[str(c or '').strip() for c in r] for r in rows]
    clean = [r for r in clean if any(r)]
    if not clean:
        return '', 0, False

    ncols = max(len(r) for r in clean)

    def _fill(row):
        return sum(1 for c in row if c)

    header_idx = 0
    if len(clean) >= 2 and _fill(clean[0]) <= 1 and _fill(clean[1]) >= 2:
        header_idx = 1  # 跳过跨列合并的标题行

    # 单列、或表头之后没有数据行 → 无法形成「字段→值」对应，退化为普通文本
    if ncols <= 1 or len(clean) - header_idx < 2:
        text = '\n'.join('\t'.join(r).rstrip() for r in clean)
        return text, 0, False

    header = normalize_header(
        [clean[header_idx][i] if i < len(clean[header_idx]) else '' for i in range(ncols)]
    )

    records = []
    for cells in clean[header_idx + 1:]:
        row = [cells[i] if i < len(cells) else '' for i in range(ncols)]
        parts = [f'{header[i]}：{row[i]}' for i in range(ncols) if row[i]]
        if parts:
            records.append('；'.join(parts))

    if not records:
        text = '\n'.join('\t'.join(r).rstrip() for r in clean)
        return text, 0, False

    summary = f'本表格共 {len(records)} 条记录；字段：{"、".join(header)}'
    return summary + '\n' + '\n'.join(records), len(records), True


def normalize_header(header):
    """规范化表头：换行/制表符压成空格、去重加后缀。

    表格问答链路中 schema 与 sqlite 建表必须用同一套列名，否则 LLM 生成
    SQL 时省略换行会导致列名不匹配（SQLite 双引号 fallback 会把不存在的
    列名静默当作字符串字面量返回，产生错误答案）。
    """
    import re as _re
    out, seen = [], {}
    for i, h in enumerate(header):
        name = _re.sub(r'\s+', ' ', str(h or '')).strip() or f'列{i + 1}'
        if name in seen:
            seen[name] += 1
            name = f'{name}_{seen[name]}'
        else:
            seen[name] = 1
        out.append(name)
    return out


def extract_schema_from_grid(rows, sheet_name: str) -> Optional[dict]:
    """从二维表格网格提取结构元数据，供表格问答引擎生成 SQL。

    表头识别与 _records_from_grid 保持一致（跳过跨列合并标题行）。
    dtype 按前若干样本行推断：纯数字 → DOUBLE，其余 → VARCHAR。

    Returns:
        {"name", "columns": [{"name", "dtype"}], "row_count", "sample_rows"}
        非结构化网格（单列/无数据行）返回 None。
    """
    if not rows:
        return None

    clean = [[str(c or '').strip() for c in r] for r in rows]
    clean = [r for r in clean if any(r)]
    if not clean:
        return None

    ncols = max(len(r) for r in clean)

    def _fill(row):
        return sum(1 for c in row if c)

    header_idx = 0
    if len(clean) >= 2 and _fill(clean[0]) <= 1 and _fill(clean[1]) >= 2:
        header_idx = 1

    if ncols <= 1 or len(clean) - header_idx < 2:
        return None

    header = normalize_header(
        [clean[header_idx][i] if i < len(clean[header_idx]) else '' for i in range(ncols)]
    )

    data_rows = []
    for cells in clean[header_idx + 1:]:
        row = [cells[i] if i < len(cells) else '' for i in range(ncols)]
        if any(row):
            data_rows.append(row)
    if not data_rows:
        return None

    def _is_number(value: str) -> bool:
        try:
            float(value)
            return True
        except (TypeError, ValueError):
            return False

    sample_rows = data_rows[:3]
    columns = []
    for i, name in enumerate(header):
        samples = [r[i] for r in sample_rows if r[i]]
        dtype = 'DOUBLE' if samples and all(_is_number(v) for v in samples) else 'VARCHAR'
        columns.append({'name': name, 'dtype': dtype})

    return {
        'name': sheet_name,
        'columns': columns,
        'row_count': len(data_rows),
        'sample_rows': sample_rows,
    }


def _pdf_page_tables(page_no: int, page) -> Tuple[str, int]:
    """用 PyMuPDF find_tables 提取页面表格并转成结构化记录。

    仅当 find_tables 可用且确实检测到表格时才返回结构化文本；
    老版本 PyMuPDF、无表格页、提取异常一律返回 ('', 0)，退回原生文字/OCR。
    """
    try:
        finder = page.find_tables()
        tables = finder.tables if finder else []
    except Exception:
        return '', 0

    if not tables:
        return '', 0

    texts, total = [], 0
    for t in tables:
        try:
            rows = t.extract()
        except Exception:
            continue
        grid = [[('' if c is None else str(c)) for c in row] for row in rows]
        text, n, structured = _records_from_grid(grid)
        if structured and text:
            texts.append(f'第{page_no}页表格\n' + text)
            total += n
    return '\n\n'.join(texts), total


def _parse_docx(file_path: str) -> ParseResult:
    """DOCX 解析：段落 + 表格 + 文本框。

    表格按结构化记录输出（字段名标注），段落作普通文本附加。
    """
    result = ParseResult()
    try:
        from docx import Document
        doc = Document(file_path)

        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        para_text = '\n'.join(paragraphs)

        table_texts = []
        for table in doc.tables:
            grid = [[cell.text.strip() for cell in row.cells] for row in table.rows]
            text, n, structured = _records_from_grid(grid)
            if text:
                table_texts.append(text)
                result.record_count += n
                result.structured = result.structured or structured

        if table_texts:
            result.text = '\n\n'.join(table_texts)
            if para_text:
                result.text += '\n\n' + para_text
        else:
            result.text = para_text
    except Exception as e:
        logger.warning(f'DOCX 解析失败: {e}')
        result.error = f'DOCX 解析失败: {type(e).__name__}: {e}'
    return result


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


def _parse_xlsx(file_path: str) -> ParseResult:
    """XLSX 解析：全部 sheet；表格 sheet 按『字段名标注记录』结构化。"""
    result = ParseResult()
    try:
        import openpyxl
        wb = openpyxl.load_workbook(file_path, data_only=True)
        parts = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            grid = [
                [('' if c is None else str(c)) for c in row]
                for row in ws.iter_rows(values_only=True)
            ]
            text, n, structured = _records_from_grid(grid)
            if not text:
                continue
            result.record_count += n
            result.structured = result.structured or structured
            if structured:
                schema = extract_schema_from_grid(grid, sheet_name)
                if schema:
                    result.table_schema = result.table_schema or {'sheets': []}
                    result.table_schema['sheets'].append(schema)
            prefix = f'工作表「{sheet_name}」\n' if structured else f'=== 工作表: {sheet_name} ===\n'
            parts.append(prefix + text)
        result.text = '\n\n'.join(parts)
    except Exception as e:
        logger.warning(f'XLSX 解析失败: {e}')
        result.error = f'XLSX 解析失败: {type(e).__name__}: {e}'
    return result


def _parse_csv(file_path: str) -> ParseResult:
    """CSV 解析：兼容 UTF-8/GBK；按表头结构化。"""
    result = ParseResult()
    import csv

    for encoding in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            with open(file_path, 'r', encoding=encoding, newline='') as f:
                rows = list(csv.reader(f))
            text, n, structured = _records_from_grid(rows)
            result.text = text
            result.record_count = n
            result.structured = structured
            if structured:
                schema = extract_schema_from_grid(rows, file_path.rsplit('\\', 1)[-1].rsplit('/', 1)[-1])
                if schema:
                    result.table_schema = {'sheets': [schema]}
            return result
        except UnicodeDecodeError:
            continue
        except Exception as e:
            logger.warning(f'CSV 解析失败: {e}')
            result.error = f'CSV 解析失败: {type(e).__name__}: {e}'
            return result
    result.error = 'CSV 解析失败：不支持的字符编码'
    return result


def _parse_xls(file_path: str) -> ParseResult:
    """XLS 旧格式解析（xlrd 回退方案）。"""
    result = ParseResult()
    try:
        import xlrd
        # xlrd 2.0+ 不支持 .xlsx，这里只处理 .xls
        workbook = xlrd.open_workbook(file_path, formatting_info=False, on_demand=True)
        parts = []
        for sheet_idx in range(workbook.nsheets):
            sheet = workbook.sheet_by_index(sheet_idx)
            grid = []
            for row_idx in range(sheet.nrows):
                grid.append([str(c) if c not in ('', None) else '' for c in sheet.row_values(row_idx)])
            text, n, structured = _records_from_grid(grid)
            if not text:
                continue
            result.record_count += n
            result.structured = result.structured or structured
            if structured:
                schema = extract_schema_from_grid(grid, sheet.name)
                if schema:
                    result.table_schema = result.table_schema or {'sheets': []}
                    result.table_schema['sheets'].append(schema)
            prefix = f'工作表「{sheet.name}」\n' if structured else f'=== 工作表: {sheet.name} ===\n'
            parts.append(prefix + text)
        workbook.release_resources()
        result.text = '\n\n'.join(parts)
    except ImportError:
        logger.warning('xlrd 未安装，XLS 解析不可用（pip install xlrd==1.2.0）')
        result.error = 'xlrd 未安装，XLS 解析不可用（pip install xlrd==1.2.0）'
    except Exception as e:
        logger.warning(f'XLS 解析失败: {e}')
        result.error = f'XLS 解析失败: {type(e).__name__}: {e}'
    return result


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


def _read_text_file(file_path: str) -> str:
    """纯文本读取（txt/md/html 等）。"""
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        return f.read()


# 文档类解析器：返回 str（非表格文档，行为保持不变）
_STR_EXTRACTORS = {
    'doc': _parse_doc,      # 旧格式 DOC
    'ppt': _parse_pptx,     # PPT 实际上也是 .pptx 格式
    'pptx': _parse_pptx,    # PPTX
    'txt': _read_text_file,
    'md': _read_text_file,
    'html': _read_text_file,
    'htm': _read_text_file,
}

# 结构化解析器：返回 ParseResult（PDF / 图片带页级统计；表格类带 structured / record_count）
_DETAILED_EXTRACTORS = {
    'pdf': _parse_pdf,
    'jpg': _parse_image,
    'jpeg': _parse_image,
    'png': _parse_image,
    'gif': _parse_image,
    'webp': _parse_image,
    'bmp': _parse_image,
    'tif': _parse_image,
    'tiff': _parse_image,
    'docx': _parse_docx,    # 含表格，需结构化
    'xls': _parse_xls,
    'xlsx': _parse_xlsx,
    'csv': _parse_csv,
}

# 允许按纯文本读取的扩展名（与 FileUploadView.TEXT_EXTRACTABLE_TYPES 对齐）。
# 只有这些扩展名可以走 UTF-8 兜底读取；其余未知扩展名一律判「不支持」，
# 杜绝 jpg/png/zip/psd 等二进制被 errors='ignore' 读成乱码后进入向量库。
_TEXT_EXTENSIONS = {'txt', 'md', 'html', 'htm', 'csv', 'json', 'xml', 'yaml', 'yml'}


def _as_text_parser(parser):
    """把结构化解析器包成旧的「只返回文本」形式，兼容既有调用方。"""
    def _run(file_path: str) -> str:
        return parser(file_path).text
    return _run


# 扩展名 → 文本解析器（返回 str）。外部模块（如 FileUploadView 提取正文喂 AI 分类）
# 按字符串使用该映射，保持既有契约不变。
_EXTRACTORS = {
    **{ext: _as_text_parser(p) for ext, p in _DETAILED_EXTRACTORS.items()},
    **_STR_EXTRACTORS,
}


def resolve_file_extension(file_type: str, file_name: str = '') -> str:
    """从原始文件名恢复真实扩展名，避免 xlsx/docx 被类别字段覆盖。

    形如扩展名的 ASCII 后缀一律原样返回（含当前没有解析器的 zip/psd 等），让上层
    能报出「暂不支持解析 .zip」这种确切原因；文件名给不出可靠后缀时，回落到
    file_type 类别对应的默认值。

    后缀必须是 ASCII：中文文件名常把说明写在小数点后（「附件1.扫描件」「报告.最终版」），
    `isalnum()` 对中文同样返回 True，若不限 ASCII 会把这些词当成扩展名，
    从而让本该按 file_type=pdf 解析的文件被判「不支持 .扫描件」而失败。
    """
    name = (file_name or '').strip()
    if '.' in name:
        candidate = name.rsplit('.', 1)[-1].strip().lower()
        # 形如扩展名：ASCII 字母数字、非纯数字、长度合理（与 serializers.get_file_ext 同规则）
        if (candidate and candidate.isascii() and candidate.isalnum()
                and not candidate.isdigit() and len(candidate) <= 16):
            return candidate
    # file_type 是类别字段；仅在没有可靠文件名扩展名时使用安全默认值。
    # image 无法从类别反推具体格式，统一给 jpg：OCR 按图片内容解码，与后缀无关。
    defaults = {'pdf': 'pdf', 'doc': 'doc', 'ppt': 'pptx', 'xls': 'xls',
                'txt': 'txt', 'md': 'md', 'html': 'html', 'image': 'jpg'}
    return defaults.get((file_type or '').lower(), '')


def parse_file(file_path: str, extension: str) -> ParseResult:
    """按扩展名选择解析器，返回结构化结果。

    - PDF / 图片：逐页判定 + 必要时 OCR，带页级统计
    - 文档类（docx/pptx/xlsx/csv…）：沿用原解析器，只取文本
    - 纯文本类：UTF-8 读取
    - 其余（含所有二进制类型）：判「不支持」，绝不按文本读取
    """
    ext = (extension or '').lower().lstrip('.')

    if ext in _DETAILED_EXTRACTORS:
        return _DETAILED_EXTRACTORS[ext](file_path)

    if ext in _STR_EXTRACTORS:
        try:
            return ParseResult(text=_STR_EXTRACTORS[ext](file_path) or '')
        except Exception as e:
            logger.warning(f'{ext} 解析失败: {e}')
            return ParseResult(error=f'{ext} 解析失败: {type(e).__name__}: {e}')

    if ext in _TEXT_EXTENSIONS:
        try:
            return ParseResult(text=_read_text_file(file_path))
        except Exception as e:
            logger.warning(f'文本读取失败: {e}')
            return ParseResult(error=f'文本读取失败: {type(e).__name__}: {e}')

    if not ext:
        return ParseResult(error='无法识别文件类型：文件名无有效扩展名')

    # 无解析器的类型不读文本：二进制按 UTF-8 + errors='ignore' 读出来的是乱码，
    # 一旦进入分块/向量化就会污染向量库，必须显式判为不支持。
    return ParseResult(error=f'暂不支持解析 .{ext} 类型（二进制文件不按文本读取）')


def extract_text_from_oss_detailed(
    oss_key: str, file_type: str, file_name: str = '',
) -> ParseResult:
    """从 OSS 下载文件并解析，返回结构化结果（含失败原因与页级统计）。

    file_type 保存的是归一化类别，解析时优先使用原始文件名扩展名。
    """
    from utils.http_client import ExternalClient

    if not oss_key:
        return ParseResult(error='文档没有 OSS 对象 Key，无法下载源文件')

    # 1. 生成签名下载 URL
    download_url = oss_client.get_download_url(oss_key, expires=600)
    if not download_url:
        logger.error(f'无法生成下载 URL: {oss_key}')
        return ParseResult(error='无法生成 OSS 下载 URL')

    # 2. 下载到临时文件
    extension = resolve_file_extension(file_type, file_name)
    suffix = f'.{extension or "bin"}'
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        resp = ExternalClient.get('oss_download', download_url)
        resp.raise_for_status()
        with open(tmp_path, 'wb') as f:
            f.write(resp.content)

        # 3. 解析
        return parse_file(tmp_path, extension)
    except Exception as e:
        logger.error(f'下载/抽取失败 oss_key={oss_key}: {e}')
        return ParseResult(error=f'下载/抽取失败: {type(e).__name__}: {str(e)[:200]}')
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def extract_text_from_oss(oss_key: str, file_type: str, file_name: str = '') -> str:
    """从 OSS 下载文件并抽取文本（只要文本，兼容既有调用方）。"""
    return extract_text_from_oss_detailed(oss_key, file_type, file_name).text


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
    text = extract_text_from_oss(doc.oss_key, doc.file_type, doc.file_name)
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
