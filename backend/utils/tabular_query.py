"""
表格问答引擎 (Tabular QA)

针对 xlsx/csv 结构化文档的 Text-to-SQL 问答：
问题 → 定位目标文档 → 读 doc.table_schema → sqlite3 内存表（进程内缓存）
→ LLM(qwen-flash) 生成只读 SQL → sqlparse 白名单校验 → 执行 → 结构化结果。

执行引擎选 Python 标准库 sqlite3（:memory: 内存表）而非 DuckDB：
本地 WDAC 策略拦截 duckdb 原生 DLL，sqlite3 零依赖、跨平台行为一致，
单表十万行级聚合毫秒级完成，满足本场景需求。

任一环节失败返回 fallback='rag'，由调用方静默回退到 RAG 主链路，
不向用户暴露 SQL/加载错误细节。
"""
import logging
import os
import re
import sqlite3
import tempfile
import threading
from typing import List, Optional, Tuple

import sqlparse
from django.conf import settings

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 异常
# ------------------------------------------------------------------


class TabularError(Exception):
    """表格问答引擎内部错误基类；一律触发 RAG 回退。"""


class TabularTooLarge(TabularError):
    """表格行数超过 TABULAR_MAX_ROWS，拒绝加载。"""


# ------------------------------------------------------------------
# 文档定位
# ------------------------------------------------------------------

_TABULAR_FILE_TYPES = ('xls',)  # KnowledgeDoc.file_type 归一化后 Excel 为 'xls'
_TABULAR_EXTENSIONS = ('xlsx', 'xls', 'csv')


def _visible_tabular_docs(user):
    """当前用户可见、已索引且带表格结构的文档。"""
    from utils.data_query import _visible_docs_qs

    return _visible_docs_qs(user).filter(
        status='indexed',
        file_type__in=_TABULAR_FILE_TYPES,
        table_schema__isnull=False,
    )


def _locate_doc(user, doc_id=None, keywords=None, question=''):
    """三级定位：显式 doc_id → 文件名关键词 → 轻量检索 top1 表格文档。

    多候选时不猜测，返回 ('ambiguous', queryset)；定位失败返回 (None, None)。
    权限校验包含在 _visible_tabular_docs 内：不可见即按定位失败处理，
    不泄露文档存在性。
    """
    qs = _visible_tabular_docs(user)

    if doc_id:
        doc = qs.filter(pk=doc_id).first()
        return (doc, None) if doc else (None, None)

    if keywords:
        from utils.data_query import _keyword_q

        matched = qs.filter(_keyword_q(keywords))
        count = matched.count()
        if count == 1:
            return matched.first(), None
        if count > 1:
            return 'ambiguous', matched[:10]

    if question:
        doc = _locate_by_retrieval(user, question, qs)
        if doc:
            return doc, None

    return None, None


def _locate_by_retrieval(user, question: str, qs):
    """轻量检索定位：RAG top 命中里取第一个可见表格文档。"""
    try:
        from utils import rag

        # 与 apps/chat/views.py AskView 的 user_context 构造保持一致
        user_context = {
            'user_id': getattr(user, 'id', None),
            'role': getattr(user, 'role', 'user'),
            'college_id': getattr(user, 'college_id', None),
            'department_id': getattr(user, 'department_id', None),
        }
        results = rag.retrieve(question=question, user_context=user_context, parsed_filters={})
    except Exception as exc:
        logger.warning('表格问答检索定位失败: %s', type(exc).__name__)
        return None

    for hit in results[:5]:
        doc = qs.filter(pk=hit.get('doc_id')).first()
        if doc:
            return doc
    return None


# ------------------------------------------------------------------
# sqlite3 加载与缓存
# ------------------------------------------------------------------

_CACHE = {}
_CACHE_GUARD = threading.Lock()
_MAX_CACHED = 8  # 单 worker 进程内最多缓存的表数（超出 LRU 淘汰最旧）


def _download_to_temp(doc) -> str:
    """复用 extractor 的 OSS→临时文件路径，返回本地文件路径。"""
    from apps.knowledge.extractor import resolve_file_extension
    from utils.http_client import ExternalClient
    from utils.oss import oss_client

    download_url = oss_client.get_download_url(doc.oss_key, expires=600)
    if not download_url:
        raise TabularError('无法生成 OSS 下载 URL')

    extension = resolve_file_extension(doc.file_type, doc.file_name)
    fd, tmp_path = tempfile.mkstemp(suffix=f'.{extension or "bin"}')
    os.close(fd)
    try:
        resp = ExternalClient.get('oss_download', download_url)
        resp.raise_for_status()
        with open(tmp_path, 'wb') as f:
            f.write(resp.content)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return tmp_path


def _split_header(grid) -> Tuple[Optional[List[str]], List[List[str]]]:
    """表头识别，与 extractor.extract_schema_from_grid 规则保持一致。"""
    clean = [[str(c or '').strip() for c in r] for r in grid]
    clean = [r for r in clean if any(r)]
    if not clean:
        return None, []

    ncols = max(len(r) for r in clean)

    def _fill(row):
        return sum(1 for c in row if c)

    header_idx = 0
    if len(clean) >= 2 and _fill(clean[0]) <= 1 and _fill(clean[1]) >= 2:
        header_idx = 1
    if ncols <= 1 or len(clean) - header_idx < 2:
        return None, []

    header = [clean[header_idx][i] if i < len(clean[header_idx]) else '' for i in range(ncols)]
    from apps.knowledge.extractor import normalize_header
    header = normalize_header(header)

    rows = []
    for cells in clean[header_idx + 1:]:
        row = [cells[i] if i < len(cells) else '' for i in range(ncols)]
        if any(row):
            rows.append(row)
    return header, rows


def _load_grid(file_path: str, extension: str, sheet_name: str):
    """读取目标 sheet/CSV 的原始网格。"""
    if extension == 'csv':
        import csv

        for encoding in ('utf-8-sig', 'utf-8', 'gb18030'):
            try:
                with open(file_path, 'r', encoding=encoding, newline='') as f:
                    return list(csv.reader(f))
            except UnicodeDecodeError:
                continue
        raise TabularError('CSV 编码无法识别')

    import openpyxl

    # 不用 read_only=True：部分工具生成的 xlsx dimension 元数据损坏，
    # read_only 模式会按错误的 dimension 截断（如只能读到 1 行），
    # 与 extractor._parse_xlsx 保持一致用普通模式
    wb = openpyxl.load_workbook(file_path, data_only=True)
    try:
        if sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
        else:
            ws = wb[wb.sheetnames[0]]
        return [
            [('' if c is None else str(c)) for c in row]
            for row in ws.iter_rows(values_only=True)
        ]
    finally:
        wb.close()


def _quote_ident(name: str) -> str:
    """标识符加双引号（中文列名），内部双引号转义。"""
    return '"{}"'.format(str(name).replace('"', '""'))


def _get_conn(doc):
    """加载文档首个结构化 sheet 到 sqlite3 内存表 data（进程内缓存）。"""
    schema = doc.table_schema or {}
    sheets = schema.get('sheets') or []
    if not sheets:
        raise TabularError('文档无表格结构元数据')
    sheet = sheets[0]

    with _CACHE_GUARD:
        entry = _CACHE.get(doc.id)
        if entry and entry[1] == doc.updated_at:
            return entry[0], entry[2]

    tmp_path = _download_to_temp(doc)
    try:
        extension = doc.file_name.rsplit('.', 1)[-1].lower() if '.' in (doc.file_name or '') else 'xlsx'
        grid = _load_grid(tmp_path, extension, sheet['name'])
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    header, rows = _split_header(grid)
    if not header or not rows:
        raise TabularError('表格内容为空或无法识别表头')

    max_rows = getattr(settings, 'TABULAR_MAX_ROWS', 100000)
    if len(rows) > max_rows:
        raise TabularTooLarge(f'表格 {len(rows)} 行，超过上限 {max_rows}')

    # 列类型以 schema 为准（与索引期一致）；schema 缺失时一律 TEXT
    dtype_by_name = {c['name']: c.get('dtype', 'VARCHAR') for c in sheet.get('columns', [])}
    col_defs = ', '.join(
        '{} {}'.format(
            _quote_ident(name),
            'REAL' if dtype_by_name.get(name) == 'DOUBLE' else 'TEXT',
        )
        for name in header
    )

    # check_same_thread=False：连接在请求线程间复用，并发由 per-conn Lock 串行化
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.execute(f'CREATE TABLE data ({col_defs})')

    numeric_cols = {i for i, name in enumerate(header) if dtype_by_name.get(name) == 'DOUBLE'}

    def _convert(i, v):
        if v == '':
            return None
        if i in numeric_cols:
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        return v

    placeholders = ', '.join(['?'] * len(header))
    conn.executemany(
        f'INSERT INTO data VALUES ({placeholders})',
        [tuple(_convert(i, v) for i, v in enumerate(row)) for row in rows],
    )

    lock = threading.Lock()
    with _CACHE_GUARD:
        old = _CACHE.get(doc.id)
        if old:
            try:
                old[0].close()
            except Exception:
                pass
        if len(_CACHE) >= _MAX_CACHED:
            evict_id = min(_CACHE, key=lambda k: _CACHE[k][1].timestamp())
            try:
                _CACHE[evict_id][0].close()
            except Exception:
                pass
            _CACHE.pop(evict_id, None)
        _CACHE[doc.id] = (conn, doc.updated_at, lock)
    logger.info('表格问答加载: doc_id=%d sheet=%s rows=%d', doc.id, sheet['name'], len(rows))
    return conn, lock


# ------------------------------------------------------------------
# SQL 生成与校验
# ------------------------------------------------------------------

_SQL_SYSTEM_PROMPT = """你是 SQL 生成器。根据用户问题，为 SQLite 内存表 data 生成一条只读 SELECT 查询。

表结构：
{schema}

规则：
- 只输出一条 SQL，不要解释，不要 Markdown 代码块
- 表名只能是 data；列名必须用双引号包裹（含中文列名）
- 问题中的实体值（如班级名、教材名）按样本行的写法精确匹配，可用 LIKE '%...%' 兜底
- 需要计数用 COUNT(*)，分组统计用 GROUP BY，排序用 ORDER BY ... DESC
- 字符串比较注意 SQLite 默认大小写敏感，中文无影响
- 只允许 SELECT / WITH，禁止任何写操作
- 拿不准的列优先用样本行中出现过的取值"""


def _schema_prompt(schema: dict) -> str:
    parts = []
    for sheet in (schema or {}).get('sheets', [])[:1]:
        cols = '、'.join(f'"{c["name"]}"({c.get("dtype", "VARCHAR")})' for c in sheet.get('columns', []))
        parts.append(f'工作表「{sheet.get("name", "")}」，共 {sheet.get("row_count", 0)} 行')
        parts.append(f'列：{cols}')
        samples = sheet.get('sample_rows') or []
        if samples:
            header = [c['name'] for c in sheet.get('columns', [])]
            for row in samples[:3]:
                pairs = '；'.join(
                    f'{header[i]}={row[i]}' for i in range(min(len(header), len(row))) if row[i]
                )
                parts.append(f'样本行：{pairs}')
    return '\n'.join(parts)


def generate_sql(question: str, schema: dict) -> str:
    """LLM 生成 SQL；失败抛 TabularError 触发回退。"""
    from utils.llm import llm_client

    raw = llm_client.chat(
        [
            {'role': 'system', 'content': _SQL_SYSTEM_PROMPT.format(schema=_schema_prompt(schema))},
            {'role': 'user', 'content': question[:500]},
        ],
        temperature=0.0,
        max_tokens=256,
        timeout=getattr(settings, 'TABULAR_SQL_LLM_TIMEOUT', 8.0),
        max_retries=1,
    )
    sql = (raw or '').strip()
    # 去除可能的 Markdown 代码块包裹
    match = re.search(r'```(?:sql)?\s*(.*?)```', sql, re.S | re.I)
    if match:
        sql = match.group(1).strip()
    sql = sql.rstrip(';').strip()
    if not sql:
        raise TabularError('模型未生成 SQL')
    return sql


_FORBIDDEN_SQL_RE = re.compile(
    r'\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|ATTACH|DETACH|COPY|'
    r'PRAGMA|INSTALL|LOAD|SET|CALL|EXPORT|IMPORT|VACUUM|GRANT)\b',
    re.IGNORECASE,
)
_FROM_JOIN_RE = re.compile(r'\b(?:FROM|JOIN)\s+"?([\w一-鿿]+)"?', re.IGNORECASE)
_LIMIT_RE = re.compile(r'\bLIMIT\b', re.IGNORECASE)


def validate_sql(sql: str) -> str:
    """白名单校验：单语句、只读、仅查 data 表；自动补 LIMIT。

    Raises:
        TabularError: 校验不通过
    """
    statements = [s for s in sqlparse.parse(sql) if str(s).strip()]
    if len(statements) != 1:
        raise TabularError('只允许单条 SQL 语句')

    first = statements[0].get_type()
    if first not in ('SELECT',):
        # WITH...SELECT 在 sqlparse 中 get_type 亦为 SELECT；UNKNOWN 一律拒绝
        raise TabularError('只允许 SELECT 查询')

    if _FORBIDDEN_SQL_RE.search(sql):
        raise TabularError('SQL 包含禁止的关键字')

    tables = _FROM_JOIN_RE.findall(sql)
    if not tables or any(t.lower() != 'data' for t in tables):
        raise TabularError('只允许查询 data 表')

    if not _LIMIT_RE.search(sql):
        sql = f'SELECT * FROM ({sql}) AS _t LIMIT 1000'
    return sql


def _execute(conn, lock, sql: str):
    """带 5s 中断超时的执行；返回 (columns, rows, truncated)。"""
    timer = threading.Timer(5.0, conn.interrupt)
    timer.start()
    try:
        with lock:
            cur = conn.execute(sql)
            columns = [d[0] for d in (cur.description or [])]
            rows = cur.fetchall()
    except Exception as exc:
        raise TabularError(f'SQL 执行失败: {type(exc).__name__}') from exc
    finally:
        timer.cancel()

    truncated = len(rows) >= 1000
    return columns, [list(r) for r in rows], truncated


# ------------------------------------------------------------------
# 编排入口
# ------------------------------------------------------------------


def run_tabular_query(user, question: str, doc_id=None, keywords=None) -> dict:
    """表格问答编排入口。

    Returns:
        成功: {"success": True, "data": {...}}
        多候选: {"success": True, "data": {"ambiguous": True, "matches": [...], "total": n}}
        失败: {"success": False, "fallback": "rag", "error": str}
    """
    try:
        located, candidates = _locate_doc(
            user, doc_id=doc_id, keywords=keywords, question=question,
        )
        if located == 'ambiguous':
            matches = [
                {'id': d.id, 'title': d.title, 'file_name': d.file_name}
                for d in candidates
            ]
            return {
                'success': True,
                'data': {
                    'ambiguous': True,
                    'not_found': False,
                    'need_filename': False,
                    'total': len(matches),
                    'matches': matches,
                },
            }
        if not located:
            return {'success': False, 'fallback': 'rag', 'error': '未定位到表格文档'}

        doc = located
        conn, lock = _get_conn(doc)
        sql = validate_sql(generate_sql(question, doc.table_schema))
        columns, rows, truncated = _execute(conn, lock, sql)

        max_result = getattr(settings, 'TABULAR_RESULT_MAX_ROWS', 100)
        shown = rows[:max_result]
        return {
            'success': True,
            'data': {
                'doc_id': doc.id,
                'doc_title': doc.title,
                'file_name': doc.file_name,
                'sql': sql,
                'columns': columns,
                'rows': shown,
                'row_count': len(rows),
                'shown': len(shown),
                'truncated': truncated or len(rows) > len(shown),
            },
        }
    except TabularTooLarge as exc:
        logger.info('表格问答回退 RAG: %s', exc)
        return {'success': False, 'fallback': 'rag', 'error': str(exc)}
    except TabularError as exc:
        logger.info('表格问答回退 RAG: %s', exc)
        return {'success': False, 'fallback': 'rag', 'error': str(exc)}
    except Exception as exc:
        logger.warning('表格问答异常回退 RAG: %s: %s', type(exc).__name__, exc)
        return {'success': False, 'fallback': 'rag', 'error': f'{type(exc).__name__}: {exc}'}
