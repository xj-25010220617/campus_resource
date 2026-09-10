"""统一内容抽取层（P1-2C）

为什么需要独立的一层
--------------------
P1-2B 审计确认：同一个 `extract_text_from_oss` 被 3 个调用点以 3 种不同的门禁
强度调用，其中 2 个完全无门禁；且它对"未知扩展名"的处理是**当纯文本解码**，
对"解析失败"的处理是**返回空字符串**——两者都会让上层把"没抽出内容"误当成
"内容干净"。审核链路不能建立在这种语义上。

本模块因此与 `extractor.py`（业务抽取，服务于日期提取与索引）**并存但不共用**：

    extractor.extract_text_from_oss   业务抽取：失败返回 ''，未知类型当文本读
    extraction.extract_from_oss       审核抽取：失败返回**明确状态**，未知类型拒绝

两者刻意不合并：合并会把审核语义强加给既有业务链路（P1-2C 禁止修改调用方），
也会把"失败即空串"的旧语义带进审核。后续阶段若要统一，应以本模块为准。

安全不变量
----------
1. 未知扩展名 → `unsupported`，**绝不**按文本解码。
2. 解析依赖缺失 → `dependency_missing`，**绝不**静默跳过。
3. 空内容 → `empty`，**绝不**当作"没风险"。
4. 压缩包 → `unsupported`，不做任何解压（无归档炸弹防护就不解压）。
5. 启用宏的 Office 文件（.docm/.xlsm/.pptm 等）→ `unsupported`，**绝不**交给
   外部程序执行；本模块从不执行上传文件中的宏/脚本/可执行代码。
6. 读取 OSS 一律流式 + 字节上限，不做整文件入内存。
"""
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field

from django.conf import settings

from utils.oss import OSSOperationError, oss_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 状态常量（`ExtractResult.status` 的取值）
# ---------------------------------------------------------------------------

#: 成功抽取出文本
EXTRACT_OK = 'ok'
#: 是媒体文件（图片/音视频），无文本可抽，但需要交给对应检测器（仅图片有检测器）
EXTRACT_MEDIA_ONLY = 'media_only'
#: 格式不受支持：未知扩展名、压缩包、宏文档、音视频
EXTRACT_UNSUPPORTED = 'unsupported'
#: 文件存在但内容为空
EXTRACT_EMPTY = 'empty'
#: 解析所需的第三方库/外部程序缺失
EXTRACT_DEPENDENCY_MISSING = 'dependency_missing'
#: 解析过程本身失败（损坏文件、读取中断、超限）
EXTRACT_FAILED = 'failed'

#: **可放行**的抽取状态集合。只有 `ok` 与 `media_only` 允许后续聚合把结论推向
#: approved；其余全部必须落到 unavailable/error（见 moderation.aggregate_results）。
#: 与审核状态一样使用白名单，避免将来新增状态时被静默放行。
EXTRACT_ACCEPTABLE_STATUSES = frozenset({EXTRACT_OK, EXTRACT_MEDIA_ONLY})


# ---------------------------------------------------------------------------
# 扩展名分类
# ---------------------------------------------------------------------------

#: 直接按文本读取的扩展名
TEXT_EXTENSIONS = frozenset({
    'txt', 'md', 'markdown', 'csv', 'tsv', 'json', 'xml', 'yaml', 'yml',
    'log', 'ini', 'conf', 'html', 'htm',
})

#: 需要专用解析器的扩展名 → 解析器标识
DOCUMENT_EXTENSIONS = {
    'pdf': 'pdf',
    'doc': 'doc',
    'docx': 'docx',
    'ppt': 'pptx',
    'pptx': 'pptx',
    'xls': 'xls',
    'xlsx': 'xlsx',
}

#: 图片扩展名（交给视觉检测器）
IMAGE_EXTENSIONS = frozenset({
    'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'tiff', 'tif', 'svg', 'ico',
    'heic', 'heif', 'avif',
})

#: 音视频扩展名——P1-2C 没有可靠的语音/视频内容审核能力，一律 unsupported
MEDIA_EXTENSIONS = frozenset({
    'mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a', 'amr', 'wma', 'opus',
    'mp4', 'm4v', 'webm', 'mov', 'avi', 'mkv', 'flv', 'wmv', 'mpeg', 'mpg',
})

#: 启用宏的 Office 文件——**永不解析**（交给外部程序可能触发宏）
MACRO_ENABLED_EXTENSIONS = frozenset({
    'docm', 'dotm', 'xlsm', 'xltm', 'xlam', 'pptm', 'potm', 'ppam', 'sldm',
})

#: 压缩包——P1-2C 不做解压（没有归档炸弹防护就不解压）
ARCHIVE_EXTENSIONS = frozenset({
    'zip', 'rar', '7z', 'tar', 'gz', 'tgz', 'bz2', 'xz', 'zst', 'iso', 'cab',
})


@dataclass
class ExtractResult:
    """统一抽取结果。

    Attributes:
        status: EXTRACT_* 之一，调用方必须按状态分支，**不要**只看 text 是否为空。
        text: 抽出的文本（仅 status=ok 时有意义；其余为 ''）
        metadata: 附带信息（extension / media_type / char_count / truncated / encoding）
        error: 失败原因（可直接落库的简短描述，**不含文件原文**）
    """
    status: str
    text: str = ''
    metadata: dict = field(default_factory=dict)
    error: str = ''

    @property
    def is_text(self) -> bool:
        return self.status == EXTRACT_OK

    @property
    def is_media_only(self) -> bool:
        return self.status == EXTRACT_MEDIA_ONLY

    @property
    def is_image(self) -> bool:
        return self.metadata.get('media_type') == 'image'

    @property
    def is_acceptable(self) -> bool:
        """是否属于"抽取本身没出问题"的状态（不代表内容安全）。"""
        return self.status in EXTRACT_ACCEPTABLE_STATUSES


# ---------------------------------------------------------------------------
# 配置读取（唯一入口，避免常量散落）
# ---------------------------------------------------------------------------

def _cfg(name: str, default):
    return getattr(settings, name, default)


def max_download_bytes() -> int:
    """单次审核允许从 OSS 读取的字节上限。"""
    return int(_cfg('MODERATION_MAX_DOWNLOAD_BYTES', 50 * 1024 * 1024))


def max_extract_chars() -> int:
    """抽取文本的字符上限（防止超大文档把内存和 LLM 输入撑爆）。"""
    return int(_cfg('MODERATION_MAX_EXTRACT_CHARS', 2_000_000))


def max_media_bytes() -> int:
    """图片送视觉检测器前的字节上限。"""
    return int(_cfg('MODERATION_MAX_MEDIA_BYTES', 20 * 1024 * 1024))


# ---------------------------------------------------------------------------
# 扩展名解析
# ---------------------------------------------------------------------------

def resolve_extension(file_name: str, file_type: str = '') -> str:
    """解析真实扩展名：优先原始文件名，其次归一化的 file_type。

    与 `extractor.resolve_file_extension` 的区别：本函数**不猜**。文件名没有
    可识别扩展名且 file_type 也不在已知集合内时返回空串，由调用方判定为
    unsupported——审核链路宁可拒绝，也不替用户假设"这大概是个文本"。
    """
    ext = os.path.splitext(file_name or '')[1].lower().lstrip('.')
    if ext:
        return ext
    return (file_type or '').lower().lstrip('.')


def classify_extension(ext: str) -> str:
    """把扩展名归类为 'text' / 'document' / 'image' / 'media' / 'archive' /
    'macro' / 'unknown'。"""
    if not ext:
        return 'unknown'
    if ext in MACRO_ENABLED_EXTENSIONS:
        return 'macro'
    if ext in ARCHIVE_EXTENSIONS:
        return 'archive'
    if ext in TEXT_EXTENSIONS:
        return 'text'
    if ext in DOCUMENT_EXTENSIONS:
        return 'document'
    if ext in IMAGE_EXTENSIONS:
        return 'image'
    if ext in MEDIA_EXTENSIONS:
        return 'media'
    return 'unknown'


# ---------------------------------------------------------------------------
# 受限下载
# ---------------------------------------------------------------------------

def download_to_tempfile(oss_key: str, suffix: str = '',
                         max_bytes: int = None) -> tuple:
    """把 OSS 对象流式落盘到临时文件（不整文件入内存）。

    Returns:
        (tmp_path, size) 成功
        (None, 0)        失败（此时已记日志，调用方按 `failed` 处理）

    Note:
        `iter_object_chunks` 在超过 max_bytes 时**抛异常**而不是截断——截断会让
        审核器看到"完整但其实是开头一段"的内容，从而对后半部分给出错误结论。
    """
    max_bytes = max_bytes or max_download_bytes()
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    total = 0
    try:
        with open(tmp_path, 'wb') as fh:
            for chunk in oss_client.iter_object_chunks(oss_key, max_bytes=max_bytes):
                fh.write(chunk)
                total += len(chunk)
    except OSSOperationError as e:
        logger.warning(f'审核抽取：读取 OSS 失败 oss_key={oss_key}: {e}')
        _safe_remove(tmp_path)
        return None, 0
    except OSError as e:
        logger.error(f'审核抽取：写临时文件失败 oss_key={oss_key}: {e}')
        _safe_remove(tmp_path)
        return None, 0
    return tmp_path, total


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def read_media_bytes(oss_key: str, max_bytes: int = None) -> bytes:
    """读取媒体对象的原始字节（供视觉检测器使用）。

    这是**唯一**允许把整个对象放进内存的地方，且必须带字节上限。仅用于图片，
    大小由 MODERATION_MAX_MEDIA_BYTES 控制。

    Raises:
        OSSOperationError: 读取失败或超过上限
    """
    max_bytes = max_bytes or max_media_bytes()
    buf = bytearray()
    for chunk in oss_client.iter_object_chunks(oss_key, max_bytes=max_bytes):
        buf.extend(chunk)
    return bytes(buf)


# ---------------------------------------------------------------------------
# 文本解析器
#
# 每个解析器返回 (text, status, error)：
#   status ∈ {'ok', 'dependency_missing', 'failed'}
# 解析器**不得**抛异常——异常必须被翻译成 failed，否则审核状态写入会被打断。
# ---------------------------------------------------------------------------

def _parse_text_file(path: str) -> tuple:
    """纯文本类：按编码回退读取。"""
    for encoding in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            with open(path, 'r', encoding=encoding, newline='') as f:
                return f.read(), 'ok', ''
        except UnicodeDecodeError:
            continue
        except OSError as e:
            return '', 'failed', f'读取失败: {e}'
    return '', 'failed', '无法识别的文本编码（尝试 utf-8/gb18030 均失败）'


def _parse_csv_file(path: str) -> tuple:
    """CSV：按编码回退读取，保留制表符分隔便于检索。"""
    for encoding in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            with open(path, 'r', encoding=encoding, newline='') as f:
                return f.read(), 'ok', ''
        except UnicodeDecodeError:
            continue
        except OSError as e:
            return '', 'failed', f'读取失败: {e}'
    return '', 'failed', '无法识别的 CSV 编码'


def _parse_html_file(path: str) -> tuple:
    """HTML：先读原文，再去标签（去标签只为降低噪音，不做 HTML 渲染）。"""
    text, status, error = _parse_text_file(path)
    if status != 'ok':
        return text, status, error
    # 只吃掉像标签的片段（长度设上限，避免把 "< 3 且 > 5" 这类正文整段吞掉）
    stripped = re.sub(r'<[^<>]{0,200}>', ' ', text)
    return stripped, 'ok', ''


def _parse_pdf(path: str) -> tuple:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        fitz = None
    if fitz is not None:
        try:
            doc = fitz.open(path)
            try:
                return ''.join(page.get_text() + '\n' for page in doc), 'ok', ''
            finally:
                doc.close()
        except Exception as e:  # 损坏 PDF 等
            return '', 'failed', f'PDF 解析失败: {type(e).__name__}'

    try:
        import pdfplumber
    except ImportError:
        return '', 'dependency_missing', 'PyMuPDF 与 pdfplumber 均未安装'

    try:
        with pdfplumber.open(path) as pdf:
            parts = []
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    parts.append(t)
        return '\n'.join(parts), 'ok', ''
    except Exception as e:
        return '', 'failed', f'PDF 解析失败: {type(e).__name__}'


def _parse_docx(path: str) -> tuple:
    try:
        from docx import Document
    except ImportError:
        return '', 'dependency_missing', 'python-docx 未安装'
    try:
        doc = Document(path)
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                row_text = '\t'.join(cell.text.strip() for cell in row.cells)
                if row_text.strip():
                    parts.append(row_text)
        return '\n'.join(parts), 'ok', ''
    except Exception as e:
        return '', 'failed', f'DOCX 解析失败: {type(e).__name__}'


def _parse_pptx(path: str) -> tuple:
    try:
        from pptx import Presentation
    except ImportError:
        return '', 'dependency_missing', 'python-pptx 未安装'
    try:
        prs = Presentation(path)
        parts = []
        for slide in prs.slides:
            for shape in slide.shapes:
                if getattr(shape, 'has_text_frame', False):
                    parts.append(shape.text_frame.text)
        return '\n'.join(parts), 'ok', ''
    except Exception as e:
        return '', 'failed', f'PPTX 解析失败: {type(e).__name__}'


def _parse_xlsx(path: str) -> tuple:
    try:
        import openpyxl
    except ImportError:
        return '', 'dependency_missing', 'openpyxl 未安装'
    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        parts = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            parts.append(f'=== 工作表: {sheet_name} ===')
            for row in ws.iter_rows(values_only=True):
                row_text = '\t'.join('' if c is None else str(c) for c in row)
                if row_text.strip():
                    parts.append(row_text)
        wb.close()
        return '\n'.join(parts), 'ok', ''
    except Exception as e:
        return '', 'failed', f'XLSX 解析失败: {type(e).__name__}'


def _parse_xls(path: str) -> tuple:
    try:
        import xlrd
    except ImportError:
        return '', 'dependency_missing', 'xlrd 未安装'
    try:
        wb = xlrd.open_workbook(path, formatting_info=False, on_demand=True)
        parts = []
        for idx in range(wb.nsheets):
            sheet = wb.sheet_by_index(idx)
            parts.append(f'=== 工作表: {sheet.name} ===')
            for r in range(sheet.nrows):
                row_text = '\t'.join(
                    '' if cell in ('', None) else str(cell)
                    for cell in sheet.row_values(r)
                )
                if row_text.strip():
                    parts.append(row_text)
        wb.release_resources()
        return '\n'.join(parts), 'ok', ''
    except Exception as e:
        return '', 'failed', f'XLS 解析失败: {type(e).__name__}'


def _run_fixed_command(argv: list, timeout: int, max_output_bytes: int) -> tuple:
    """执行**固定可执行文件 + 参数列表**的子进程（P1-2C 唯一允许的外部程序调用）。

    硬性约束（不提供绕过开关）：
    - 不接受字符串命令，只接受 argv 列表 → 结构上不可能触发 shell
    - `shell=False` 显式声明
    - 必须有 timeout
    - 捕获输出并限制字节数
    - 任何异常都翻译成失败，不向上抛

    Returns:
        (stdout_bytes, ok)
    """
    try:
        proc = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return b'', 'missing'
    except subprocess.TimeoutExpired:
        return b'', 'timeout'
    except Exception:
        return b'', 'failed'
    if proc.returncode != 0:
        return b'', 'failed'
    return proc.stdout[:max_output_bytes], 'ok'


def _parse_doc(path: str) -> tuple:
    """旧格式 DOC 解析。

    只用 antiword（argv 列表 + timeout + 输出上限）。**不使用 soffice**：
    LibreOffice 的 headless 转换会把文件当成完整文档打开并在本机执行字体/宏等
    处理链，风险面显著大于 antiword，且当前部署未验证可用。宁可 dependency_missing。
    """
    if not _cfg('MODERATION_DOC_ANTIWORD_ENABLED', True):
        return '', 'dependency_missing', 'antiword 已被配置禁用（MODERATION_DOC_ANTIWORD_ENABLED=False）'

    max_out = int(_cfg('MODERATION_SUBPROCESS_MAX_OUTPUT_BYTES', 8 * 1024 * 1024))
    timeout = int(_cfg('MODERATION_SUBPROCESS_TIMEOUT_SECONDS', 20))
    stdout, ok = _run_fixed_command(['antiword', path], timeout, max_out)
    if ok == 'missing':
        return '', 'dependency_missing', 'antiword 未安装，无法解析旧格式 DOC'
    if ok == 'timeout':
        return '', 'failed', 'antiword 解析超时'
    if ok != 'ok':
        return '', 'failed', 'antiword 解析失败'

    try:
        return stdout.decode('utf-8', errors='replace'), 'ok', ''
    except Exception as e:
        return '', 'failed', f'antiword 输出解码失败: {type(e).__name__}'


_PARSERS = {
    'pdf': _parse_pdf,
    'doc': _parse_doc,
    'docx': _parse_docx,
    'pptx': _parse_pptx,
    'xlsx': _parse_xlsx,
    'xls': _parse_xls,
}


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def extract_from_oss(oss_key: str, file_type: str = '',
                     file_name: str = '') -> ExtractResult:
    """从 OSS 抽取文档内容，返回**明确状态**的结果。

    Args:
        oss_key: OSS 对象 Key（quarantine/… 或 knowledge/… 均可，本层不判断位置）
        file_type: KnowledgeDoc.file_type（归一化类别，仅作扩展名回退）
        file_name: 原始文件名（扩展名判定首选）

    Returns:
        ExtractResult。**调用方必须按 status 分支**，不要只看 text。
    """
    if not oss_key:
        return ExtractResult(EXTRACT_FAILED, error='没有关联的 OSS 对象')

    ext = resolve_extension(file_name, file_type)
    kind = classify_extension(ext)
    meta = {'extension': ext, 'kind': kind}

    # ---- 明确拒绝的类别（在读取任何字节之前就拒绝） ----
    if kind == 'macro':
        return ExtractResult(
            EXTRACT_UNSUPPORTED, metadata=meta,
            error=f'启用宏的 Office 文件不解析（.{ext}）：不执行上传文件中的宏',
        )
    if kind == 'archive':
        return ExtractResult(
            EXTRACT_UNSUPPORTED, metadata=meta,
            error=f'压缩包不解析（.{ext}）：无归档炸弹防护时不解压',
        )
    if kind == 'media':
        return ExtractResult(
            EXTRACT_UNSUPPORTED, metadata=meta,
            error=f'音视频内容 P1-2C 不具备审核能力（.{ext}）',
        )
    if kind == 'unknown':
        return ExtractResult(
            EXTRACT_UNSUPPORTED, metadata=meta,
            error=f'未知格式不按文本解析（.{ext or "无扩展名"}）',
        )

    # ---- 图片：无文本，但需要交给视觉检测器 ----
    if kind == 'image':
        meta['media_type'] = 'image'
        return ExtractResult(EXTRACT_MEDIA_ONLY, metadata=meta)

    # ---- 落盘 ----
    tmp_path, size = download_to_tempfile(oss_key, suffix=f'.{ext}')
    if tmp_path is None:
        return ExtractResult(EXTRACT_FAILED, metadata=meta,
                             error='读取 OSS 对象失败或超过大小上限')
    meta['size'] = size

    try:
        if size == 0:
            return ExtractResult(EXTRACT_EMPTY, metadata=meta, error='文件内容为空')

        if kind == 'text':
            parser = {'csv': _parse_csv_file, 'tsv': _parse_csv_file,
                      'html': _parse_html_file, 'htm': _parse_html_file}.get(
                ext, _parse_text_file)
        else:
            parser = _PARSERS.get(DOCUMENT_EXTENSIONS.get(ext))
            if parser is None:
                return ExtractResult(EXTRACT_UNSUPPORTED, metadata=meta,
                                     error=f'没有可用的解析器（.{ext}）')

        text, status, error = parser(tmp_path)
        if status != 'ok':
            return ExtractResult(status, metadata=meta, error=error)

        limit = max_extract_chars()
        if len(text) > limit:
            text = text[:limit]
            meta['truncated'] = True
        meta['char_count'] = len(text)

        if not text.strip():
            # 解析成功但没有任何文本（扫描件 PDF、纯图 PPT 等）。
            # 不能当作"干净"——没有任何检测器看到过内容。
            return ExtractResult(EXTRACT_EMPTY, metadata=meta,
                                 error='解析成功但未获得任何文本（可能是扫描件/纯图文档）')
        return ExtractResult(EXTRACT_OK, text=text, metadata=meta)
    finally:
        _safe_remove(tmp_path)
