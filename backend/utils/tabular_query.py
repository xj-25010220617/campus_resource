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


def _get_conn(doc, sheet_name=None):
    """加载指定 sheet（默认第一个）到 sqlite3 内存表 data（进程内缓存）。"""
    schema = doc.table_schema or {}
    sheets = schema.get('sheets') or []
    if not sheets:
        raise TabularError('文档无表格结构元数据')
    sheet = next((s for s in sheets if s.get('name') == sheet_name), sheets[0])
    cache_key = (doc.id, sheet.get('name') or '')

    with _CACHE_GUARD:
        entry = _CACHE.get(cache_key)
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
        old = _CACHE.get(cache_key)
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
        _CACHE[cache_key] = (conn, doc.updated_at, lock)
    logger.info('表格问答加载: doc_id=%d sheet=%s rows=%d', doc.id, sheet['name'], len(rows))
    return conn, lock


# ------------------------------------------------------------------
# SQL 生成与校验
# ------------------------------------------------------------------

_SQL_SYSTEM_PROMPT = """你是 SQL 生成器。根据用户问题，为 SQLite 内存表 data 生成一条只读 SELECT 查询。

表结构：
{schema}

参考示例（历史已验证问答，仅参考写法，不要照抄表名列名）：
{examples}

规则：
- 只输出一条 SQL，不要解释，不要 Markdown 代码块
- 表名只能是 data；列名必须用双引号包裹（含中文列名）
- 问题中的实体值（如班级名、教材名）按样本行的写法精确匹配，可用 LIKE '%...%' 兜底
- 需要计数用 COUNT(*)，分组统计用 GROUP BY，排序用 ORDER BY ... DESC
- 字符串比较注意 SQLite 默认大小写敏感，中文无影响
- 只允许 SELECT / WITH，禁止任何写操作
- 拿不准的列优先用样本行中出现过的取值"""


def _tokens(text: str) -> set:
    """粗粒度分词：中文 2-gram + 英文/数字整词，用于问句与列名/表名的匹配。"""
    text = (text or '').lower()
    words = set(re.findall(r'[a-z0-9_]+', text))
    cjk = re.findall(r'[\u4e00-\u9fff]', text)
    words.update(a + b for a, b in zip(cjk, cjk[1:]))
    words.update(cjk)
    return words


def _pick_sheet(schema: dict, question: str) -> dict:
    """多 sheet 时按"表名 + 列名"与问句的重合度选 sheet；单 sheet 直接返回。"""
    sheets = (schema or {}).get('sheets') or []
    if not sheets:
        raise TabularError('文档无表格结构元数据')
    if len(sheets) == 1:
        return sheets[0]

    q_tokens = _tokens(question)
    best, best_score = sheets[0], -1
    for sheet in sheets:
        score = len(q_tokens & _tokens(sheet.get('name', ''))) * 2
        for col in sheet.get('columns', []):
            score += len(q_tokens & _tokens(col.get('name', '')))
        if score > best_score:
            best, best_score = sheet, score
    return best


def _schema_prompt(schema: dict, question: str = '') -> str:
    sheet = _pick_sheet(schema, question)
    columns = sheet.get('columns', [])
    samples = sheet.get('sample_rows') or []

    # 列很多时只注入"问句提到的列 + 其余列"，避免 Prompt 膨胀、也减少模型乱选列
    max_cols = int(getattr(settings, 'TABULAR_SCHEMA_MAX_COLUMNS', 20) or 20)
    shown = columns
    omitted = 0
    if len(columns) > max_cols:
        q_tokens = _tokens(question)
        matched = [c for c in columns if q_tokens & _tokens(c.get('name', ''))]
        rest = [c for c in columns if c not in matched]
        shown = (matched + rest)[:max_cols]
        omitted = len(columns) - len(shown)

    parts = [f'工作表「{sheet.get("name", "")}」，共 {sheet.get("row_count", 0)} 行']
    parts.append('列：' + '、'.join(
        f'"{c["name"]}"({c.get("dtype", "VARCHAR")})' for c in shown
    ))
    if omitted:
        parts.append(f'（另有 {omitted} 列未展示，可用 SELECT * 查看）')

    # 低基数列附上候选值，帮模型对齐"学院/班级/类别"等枚举写法
    enum_limit = int(getattr(settings, 'TABULAR_SCHEMA_SAMPLE_VALUES', 5) or 5)
    enums = []
    for idx, col in enumerate(columns):
        if col.get('dtype') == 'DOUBLE':
            continue
        vals = []
        for row in samples:
            if idx < len(row) and row[idx] and row[idx] not in vals:
                vals.append(row[idx])
        if 1 < len(vals) <= enum_limit:
            enums.append(f'{col["name"]}：{"、".join(vals)}')
    if enums:
        parts.append('候选值（来自样本行）：' + '；'.join(enums))

    if samples:
        header = [c['name'] for c in columns]
        for row in samples[:3]:
            pairs = '；'.join(
                f'{header[i]}={row[i]}' for i in range(min(len(header), len(row))) if row[i]
            )
            parts.append(f'样本行：{pairs}')
    return '\n'.join(parts)


def _system_prompt(schema: dict, question: str) -> str:
    """拼 SQL 生成用 system prompt（含 schema linking 与少样本示例）。"""
    examples = ''
    try:
        from utils.tabular_fewshot import format_examples
        examples = format_examples(question)
    except Exception:
        examples = ''
    return _SQL_SYSTEM_PROMPT.format(
        schema=_schema_prompt(schema, question),
        examples=examples or '（暂无）',
    )


def _clean_sql(raw: str) -> str:
    """去掉 Markdown 包裹与结尾分号；空结果抛 TabularError。"""
    sql = (raw or '').strip()
    match = re.search(r'```(?:sql)?\s*(.*?)```', sql, re.S | re.I)
    if match:
        sql = match.group(1).strip()
    sql = sql.rstrip(';').strip()
    if not sql:
        raise TabularError('模型未生成 SQL')
    return sql


def generate_sql(question: str, schema: dict) -> str:
    """LLM 生成 SQL；失败抛 TabularError 触发回退。"""
    from utils.llm import llm_client

    raw = llm_client.chat(
        [
            {'role': 'system', 'content': _system_prompt(schema, question)},
            {'role': 'user', 'content': question[:500]},
        ],
        temperature=0.0,
        max_tokens=256,
        timeout=getattr(settings, 'TABULAR_SQL_LLM_TIMEOUT', 8.0),
        max_retries=1,
    )
    return _clean_sql(raw)


def _repair_sql(question: str, schema: dict, prev_sql: str, problem: str) -> str:
    """把错误信息/可疑原因回喂模型，要求重写 SQL（阶段 4.4 自愈）。"""
    from utils.llm import llm_client

    prompt = (
        f'原 SQL：{prev_sql}\n'
        f'问题：{problem}\n'
        '请修正该 SQL，使其在 SQLite 上能正确回答用户问题。'
        '只输出修正后的 SQL，不要解释，不要 Markdown 代码块。'
    )
    raw = llm_client.chat(
        [
            {'role': 'system', 'content': _system_prompt(schema, question)},
            {'role': 'user', 'content': prompt},
        ],
        temperature=0.0,
        max_tokens=256,
        timeout=getattr(settings, 'TABULAR_SQL_LLM_TIMEOUT', 8.0),
        max_retries=1,
    )
    return _clean_sql(raw)


_FORBIDDEN_SQL_RE = re.compile(
    r'\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|ATTACH|DETACH|COPY|'
    r'PRAGMA|INSTALL|LOAD|SET|CALL|EXPORT|IMPORT|VACUUM|GRANT)\b',
    re.IGNORECASE,
)
_FROM_JOIN_RE = re.compile(r'\b(?:FROM|JOIN)\s+"?([\w一-鿿]+)"?', re.IGNORECASE)
_LIMIT_RE = re.compile(r'\bLIMIT\b', re.IGNORECASE)


def _validate_sql_ast(sql: str) -> str:
    """sqlglot AST 级校验：单条只读查询、仅 data 表、自动补 LIMIT。

    比正则更可靠：正则挡不住 `SELECT ... INTO`、注释绕过、嵌套 DDL 等写法。
    """
    from sqlglot import exp, parse
    from sqlglot.errors import ParseError

    try:
        statements = [s for s in parse(sql, read='sqlite') if s]
    except ParseError as exc:
        raise TabularError(f'SQL 语法解析失败: {str(exc)[:80]}') from exc
    if len(statements) != 1:
        raise TabularError('只允许单条 SQL 语句')

    root = statements[0]
    if not isinstance(root, (exp.Select, exp.Union, exp.Subquery)):
        raise TabularError('只允许 SELECT 查询')

    forbidden_names = (
        'Insert', 'Update', 'Delete', 'Drop', 'Create', 'Alter', 'Command',
        'Pragma', 'Attach', 'Detach', 'Set', 'Use', 'Transaction', 'Commit',
        'Rollback', 'Grant', 'Into',
    )
    forbidden = tuple(getattr(exp, n) for n in forbidden_names if hasattr(exp, n))
    for node in root.walk():
        if isinstance(node, forbidden):
            raise TabularError('SQL 包含禁止的语句类型')

    # CTE 别名不算真实表，需排除后再校验；内层引用的真实表仍会被检查
    cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    tables = {t.name.lower() for t in root.find_all(exp.Table)} - cte_names
    if not tables or any(t != 'data' for t in tables):
        raise TabularError('只允许查询 data 表')

    if not any(isinstance(n, exp.Limit) for n in root.walk()):
        sql = f'SELECT * FROM ({sql}) AS _t LIMIT 1000'
    return sql


def validate_sql(sql: str) -> str:
    """白名单校验：单语句、只读、仅查 data 表；自动补 LIMIT。

    TABULAR_SQL_VALIDATOR=sqlglot 强制 AST 校验；=regex 用旧正则；
    默认 auto：优先 AST，sqlglot 未安装时自动回退正则。

    Raises:
        TabularError: 校验不通过
    """
    backend = getattr(settings, 'TABULAR_SQL_VALIDATOR', 'auto')
    if backend != 'regex':
        try:
            return _validate_sql_ast(sql)
        except TabularError:
            raise
        except ImportError:
            if backend == 'sqlglot':
                raise TabularError('sqlglot 未安装，无法进行 AST 校验')
            logger.warning('sqlglot 不可用，回退正则校验')
    return _validate_sql_regex(sql)


def _validate_sql_regex(sql: str) -> str:
    """旧版正则校验（保留作为兜底）。"""
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


_COUNT_HINTS = ('多少', '几个', '人数', '总数', '合计', '一共', '总共', '数量是')
_GROUP_HINTS = ('各', '分别', '每个', '按', '分学院', '分专业', '逐年', '历年')


def _result_suspicion(question: str, columns, rows) -> Optional[str]:
    """结果自检（阶段 4.5）：识别"问法"与"结果形状"明显矛盾的查询。

    只做形状级判断、不做语义判断，命中后最多触发一次重写；
    重写无改善则保留原结果，避免把正确结果改坏。
    """
    if not rows:
        return None
    q = question or ''
    wants_group = any(h in q for h in _GROUP_HINTS)
    wants_count = any(h in q for h in _COUNT_HINTS)

    if wants_group and not wants_count and len(rows) == 1 and len(columns) <= 2:
        return '问题要求按维度分别统计，但只返回一行，可能缺少 GROUP BY'
    if wants_count and not wants_group and len(rows) > 1 and len(columns) <= 2:
        return '问题问的是数量/总数，但返回了多行明细，可能缺少聚合函数'
    return None


# ------------------------------------------------------------------
# 编排入口
# ------------------------------------------------------------------


def _execute_with_heal(conn, lock, question, schema, sql):
    """执行 SQL；失败或结果明显可疑时重写一次（阶段 4.4/4.5）。

    返回 (sql, columns, rows, truncated)：sql 为最终实际执行的语句。
    """
    heal_enabled = bool(getattr(settings, 'TABULAR_SQL_SELF_HEAL', True))
    max_heal = int(getattr(settings, 'TABULAR_SQL_MAX_HEAL', 1) or 0)
    check_enabled = bool(getattr(settings, 'TABULAR_SQL_SELF_CHECK', True))
    healed = 0

    while True:
        try:
            columns, rows, truncated = _execute(conn, lock, sql)
        except TabularError as exc:
            if not heal_enabled or healed >= max_heal:
                raise
            healed += 1
            logger.info('[Tabular自愈] 执行失败，重写 SQL(%d/%d): %s', healed, max_heal, exc)
            sql = validate_sql(_repair_sql(question, schema, sql, str(exc)))
            continue

        reason = _result_suspicion(question, columns, rows) if check_enabled else None
        if not reason or not heal_enabled or healed >= max_heal:
            return sql, columns, rows, truncated

        healed += 1
        logger.info('[Tabular自检] %s，尝试重写(%d/%d)', reason, healed, max_heal)
        try:
            new_sql = validate_sql(_repair_sql(question, schema, sql, reason))
        except TabularError:
            return sql, columns, rows, truncated
        if new_sql == sql:
            return sql, columns, rows, truncated

        try:
            new_columns, new_rows, new_truncated = _execute(conn, lock, new_sql)
        except TabularError as exc:
            logger.info('[Tabular自检] 重写后执行失败，保留原结果: %s', exc)
            return sql, columns, rows, truncated

        # 重写后仍可疑 → 保留原始结果，不拿更差的结果替换
        if _result_suspicion(question, new_columns, new_rows):
            logger.info('[Tabular自检] 重写后仍可疑，保留原结果')
            return sql, columns, rows, truncated
        return new_sql, new_columns, new_rows, new_truncated


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
        schema = doc.table_schema
        sheet_name = _pick_sheet(schema or {}, question).get('name')
        conn, lock = _get_conn(doc, sheet_name)
        sql = validate_sql(generate_sql(question, schema))
        sql, columns, rows, truncated = _execute_with_heal(
            conn, lock, question, schema, sql,
        )

        # 成功问答登记为候选示例（verified=False，人工确认后才注入 Prompt）
        try:
            from utils.tabular_fewshot import record_example
            record_example(question, sql, doc.title)
        except Exception:
            pass

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
