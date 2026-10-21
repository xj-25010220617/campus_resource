"""
问题意图路由 (Query Router)

判断用户问题的意图类型：
- data_query: 文件管理查询类（"我上传了多少个文件？"）
- rag: 知识检索类（需要搜索知识库）
- general: 常识/闲聊类（直接回答）

时间类问题优先走确定性的数据库查询，不依赖 RAG/模型猜测。
"""
import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional

from django.utils import timezone

from utils.timefmt import (
    day_bounds,
    month_bounds,
    recent_days_bounds,
    to_business_time,
    to_iso,
    year_bounds,
)

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 文件管理查询的关键词规则
# ------------------------------------------------------------------

# 查询类型 → 匹配关键词
_DATA_QUERY_PATTERNS = {
    "count_docs": [
        r"(多少|几个|多少个).*(文件|文档|资料)",
        r"(文件|文档|资料).*(多少|几个|多少个)",
        r"(共计|总共|一共|合计)\s*(上传|有)\s*(多少|几个)",
        r"统计.*(文件|文档|上传)",
        r"(多少|几个|多少个).*(关于|和|与|相关|涉及)",
    ],
    "recent_docs": [
        r"(最近|近期|最新|近.*(天|周|月)).*(上传|提交|创建).*(文件|文档|资料)",
        r"(文件|文档|资料).*(最近|近期|最新|近.*(天|周|月))",
        r"显示.*(上传|我).*(文件|文档)",
    ],
    "storage_stats": [
        r"(总大小|存储|空间|占用|容量)",
        r"(文件|文档)\s*(大小|容量|存储|空间)",
        r"(多大|多少.*MB|多少.*GB)",
    ],
    "doc_categories": [
        r"(分类|类别|类型|种类)\s*(分布|统计|情况)",
        r"(文件|文档)\s*(分类|类别|类型)",
        r"哪些\s*(分类|类别)",
    ],
    "find_docs": [
        r"(找|查|搜索|检索|查找|看看).*(文件|文档|资料)",
        r"(文件|文档|资料).*(找|查|搜索|检索|查找)",
        r"帮[我你].*(找|查|搜索|检索|查找)",
        r"(有[没]?有)\s*(关于|.*的)?\s*(文件|文档|资料)",
        r"(哪里|帮我)\s*(有|可以找到)",
        r"(下载|查看|打开|获取).*(文件|文档|资料)",
        r"(我要|我想|帮我).*(下载|查看|打开|获取)",
        r"(下载|查看|打开|获取).*(复习|笔记|大纲|教程)",
    ],
}

# 知识文档查询关键词（KnowledgeDoc 热门/排行/统计）
_KNOWLEDGE_DOC_QUERY_PATTERNS = {
    "popular_docs": [
        r"(热门|最火|最受欢迎|下载最多|最热|最多人看).*(资源|文件|资料|课件)",
        r"(资源|文件|资料|课件).*(热门|最火|最受欢迎|下载最多|排行)",
        r"(排行榜|排名|排行|TOP\d*).*(资源|下载|热度)",
        r"(哪些|什么).*(资源|文件).*(最|比较).*(受欢迎|热门|多人)",
    ],
    "recent_docs_platform": [
        r"(最新|近期|最近).*(发布|上传|新增).*(资源|文件|课件)",
        r"(资源|文件|课件).*(最新|近期).*(发布|上传|更新)",
        r"(最近|有什么|有哪些).*(新资源|新文件|新课件)",
    ],
    "doc_ranking": [
        r"(哪个|哪些|什么).*(学院|专业|学科).*(上传|发布|资源).*(多|最多|丰富)",
        r"(学院|专业|学科).*(上传|发布|资源).*(排名|排行|最多)",
        r"(看看|查看|各).*(学院|专业|学科).*(资源|情况|统计)",
    ],
    "doc_stats": [
        r"(资源|资料|课件).*(统计|总数|总量|概况|总览)",
        r"(多少|几个|多少).*(资源|资料|课件).*(总计|总共|一共|共有)",
        r"(平台|系统).*(资源|资料).*(统计|概况|状况)",
    ],
}

# 时间范围关键词（保留给旧调用方；实际区间解析见 _extract_date_range）
_TIME_PATTERNS = {
    "this_year": [r"今年", r"本年度", r"202[4-9]年"],
    "last_year": [r"去年", r"上一年度"],
    "this_month": [r"本月", r"这个月"],
    "last_month": [r"上月", r"上个月"],
    "this_week": [r"本周", r"这个星期", r"这周"],
    "last_7_days": [r"最近7天", r"近7天", r"近一周", r"过去7天"],
    "last_30_days": [r"最近30天", r"近30天", r"近一个月", r"过去30天"],
    "last_90_days": [r"最近90天", r"近90天", r"近三个月", r"近3个月"],
    "custom_days": [r"近(\d+)天", r"过去(\d+)天", r"最近(\d+)天"],
}

# 明确日期 / 年月（业务时区自然日、自然月）
_FULL_DATE_RE = re.compile(r"((?:19|20)\d{2})\s*[年\-/.]\s*(\d{1,2})\s*[月\-/.]\s*(\d{1,2})\s*[日号]?")
_MONTH_RE = re.compile(r"((?:19|20)\d{2})\s*[年\-/.]\s*(\d{1,2})\s*月")
# 支持 “2026-10” 这种不带“月”的显式年月（完整日期已在前面优先匹配）
_MONTH_NUM_RE = re.compile(r"((?:19|20)\d{2})\s*[-/.]\s*(\d{1,2})(?!\d)")
_YEAR_RE = re.compile(r"((?:19|20)\d{2})\s*年")

_RELATIVE_RANGE_RE = re.compile(
    r"(今年|本年度|本年|去年|上一年度|上一年|"
    r"本月|这个月|当月|上月|上个月|"
    r"今天|今日|当天|昨天|昨日|前天|"
    r"本周|这周|这个星期|上周|上个星期|"
    r"最近\s*(\d+)\s*天|近\s*(\d+)\s*天|过去\s*(\d+)\s*天|"
    r"近\s*(\d+)\s*周|最近\s*(\d+)\s*周|"
    r"近\s*(\d+)\s*个月|最近\s*(\d+)\s*个月|"
    r"近一周|最近一周|过去一周|"
    r"近一个月|最近一个月|过去一个月|"
    r"近三个月|最近三个月|过去三个月|"
    r"最近|近期)"
)

_MINE_RE = re.compile(r"我(?:自己)?(?:上传|提交|创建|发布)的?|我传的|我的(?:文件|文档|资料)")
_ALL_SCOPE_WORDS = ("全站", "全平台", "全系统", "系统里", "平台里", "所有文件",
                    "全部文件", "所有文档", "全部文档", "所有资料", "全部资料")
_COLLEGE_SCOPE_WORDS = ("本院", "本学院", "我们学院", "我院")

_TIME_WORD_RE = re.compile(r"什么时候|何时|几点|几号|哪天|哪一天|时间|日期")
_UPLOAD_VERB_RE = re.compile(r"上传|提交|创建|发布|传的")
_INDEX_VERB_RE = re.compile(r"索引|入库|建库|解析完成|建索引")
_COUNT_RE = re.compile(r"多少|几个|几份|几条|数量|总数|共计|总共|一共|合计|统计")
_LIST_RE = re.compile(r"哪些|列出|显示|有什么|清单|列表|全部|所有|文件|文档|资料")
_EXPLICIT_LIST_RE = re.compile(r"哪些|列出|显示|有什么|清单|列表")


def _match_pattern(question: str, patterns: list) -> bool:
    """检查问题是否匹配任一正则模式。"""
    for pattern in patterns:
        if re.search(pattern, question, re.IGNORECASE):
            return True
    return False


def _range_payload(start, end, label: str) -> dict:
    """构造日期区间参数（左闭右开，ISO 8601 带业务时区偏移）。"""
    return {
        "start_at": to_iso(start),
        "end_at": to_iso(end),
        "range_label": label,
        "range_end_exclusive": True,
    }


def _extract_date_range(question: str, now: Optional[datetime] = None) -> Optional[dict]:
    """从自然语言问题中解析业务时区日期区间。

    支持：明确日期、明确年月、今天/昨天/前天、本周/上周、
    本月/上月、今年/去年、近 N 天/周/月，以及默认 30 天的“最近”。
    无法识别的日期返回 None，不猜测。
    """
    # naive datetime 按项目约定解释为业务时区，避免 localdate 直接抛 ValueError
    today = timezone.localdate(to_business_time(now) if now is not None else None)

    match = _FULL_DATE_RE.search(question)
    if match:
        year, month, day = (int(x) for x in match.groups())
        try:
            target = date(year, month, day)
        except ValueError:
            return None
        start, end = day_bounds(target)
        return _range_payload(start, end, f"{target.year}年{target.month}月{target.day}日")

    match = _MONTH_RE.search(question) or _MONTH_NUM_RE.search(question)
    if match:
        year, month = (int(x) for x in match.groups())
        if not 1 <= month <= 12:
            return None
        start, end = month_bounds(year, month)
        return _range_payload(start, end, f"{year}年{month}月")

    match = _RELATIVE_RANGE_RE.search(question)
    if match:
        minutes_ago_text = match.group(0)
        numbers = [int(g) for g in match.groups()[1:] if g]
        if "今年" in minutes_ago_text or "本年" in minutes_ago_text:
            start, end = year_bounds(today.year)
            return _range_payload(start, end, f"{today.year}年")
        if "去年" in minutes_ago_text or "上一年" in minutes_ago_text:
            start, end = year_bounds(today.year - 1)
            return _range_payload(start, end, f"{today.year - 1}年")
        if "本月" in minutes_ago_text or "这个月" in minutes_ago_text or "当月" in minutes_ago_text:
            start, end = month_bounds(today.year, today.month)
            return _range_payload(start, end, f"{today.year}年{today.month}月")
        if "上月" in minutes_ago_text or "上个月" in minutes_ago_text:
            year = today.year if today.month > 1 else today.year - 1
            month = today.month - 1 if today.month > 1 else 12
            start, end = month_bounds(year, month)
            return _range_payload(start, end, f"{year}年{month}月")
        if "今天" in minutes_ago_text or "今日" in minutes_ago_text or "当天" in minutes_ago_text:
            start, end = day_bounds(today)
            return _range_payload(start, end, f"今天（{today.isoformat()}）")
        if "昨天" in minutes_ago_text or "昨日" in minutes_ago_text:
            target = today - timedelta(days=1)
            start, end = day_bounds(target)
            return _range_payload(start, end, f"昨天（{target.isoformat()}）")
        if "前天" in minutes_ago_text:
            target = today - timedelta(days=2)
            start, end = day_bounds(target)
            return _range_payload(start, end, f"前天（{target.isoformat()}）")
        if "本周" in minutes_ago_text or "这周" in minutes_ago_text or "这个星期" in minutes_ago_text:
            monday = today - timedelta(days=today.weekday())
            start, end = day_bounds(monday)
            return _range_payload(start, end, f"本周（{monday.isoformat()} 起）")
        if "上周" in minutes_ago_text or "上个星期" in minutes_ago_text:
            monday = today - timedelta(days=today.weekday() + 7)
            start, _ = day_bounds(monday)
            end = day_bounds(monday + timedelta(days=6))[1]
            return _range_payload(start, end, f"上周（{monday.isoformat()} 起）")

        if "近一周" in minutes_ago_text or "最近一周" in minutes_ago_text or "过去一周" in minutes_ago_text:
            days = 7
        elif "近一个月" in minutes_ago_text or "最近一个月" in minutes_ago_text or "过去一个月" in minutes_ago_text:
            days = 30
        elif "近三个月" in minutes_ago_text or "最近三个月" in minutes_ago_text or "过去三个月" in minutes_ago_text:
            days = 90
        elif numbers:
            days = numbers[0]
            if "周" in minutes_ago_text:
                days *= 7
            elif "个月" in minutes_ago_text:
                days *= 30
        else:
            # “最近/近期”没有明确粒度 → 默认最近 30 天，避免无界查询
            days = 30

        days = max(1, min(days, 3660))
        start, end = recent_days_bounds(days, today)
        label = f"最近{days}天（{start.date().isoformat()} 至 {today.isoformat()}）"
        return _range_payload(start, end, label)

    match = _YEAR_RE.search(question)
    if match:
        year = int(match.group(1))
        start, end = year_bounds(year)
        return _range_payload(start, end, f"{year}年")

    return None


def _extract_scope(question: str) -> tuple:
    """解析“我上传的 / 本院 / 全站”语义。

    Returns:
        (mine_only, scope_label)
    """
    if _MINE_RE.search(question):
        return True, "本人上传"
    if any(word in question for word in _ALL_SCOPE_WORDS):
        return False, "当前账号可访问范围（全站查询受既有权限限制）"
    if any(word in question for word in _COLLEGE_SCOPE_WORDS):
        return False, "本院范围（既有权限）"
    if re.search(r"(所有|全部).*(文件|文档|资料)", question):
        return False, "当前账号可访问范围（全站查询受既有权限限制）"
    return False, "当前账号可访问范围"


def _extract_time_param(question: str) -> Optional[dict]:
    """兼容旧接口：把时间表达转换为路由参数。

    Returns:
        {"start_at": str, "end_at": str, "range_label": str} 或 None
    """
    return _extract_date_range(question)


def _extract_keywords(question: str) -> list:
    """从问题中提取搜索关键词（用于 find_docs 类型）。
    使用 jieba 分词，避免粗暴替换导致关键词被截断。
    """
    try:
        import jieba
        words = jieba.lcut(question)
    except ImportError:
        # 无 jieba 时用正则按中英文拆分
        words = re.findall(r'[a-zA-Z0-9]+|[一-鿿]+', question)

    stop_words = {
        "我", "你", "的", "了", "有", "没有", "吗", "啊", "呢",
        "找", "查", "搜索", "检索", "看看", "显示", "列出",
        "多少", "几个", "哪个", "哪些", "什么", "怎么", "如何",
        "帮", "请", "一下", "最近", "上传", "文件", "文档", "资料",
        "关于", "相关", "哪里", "可以", "查看", "详情", "信息",
    }
    keywords = [w for w in words if w.lower() not in stop_words and len(w) >= 2]
    return keywords[:5]


_FILE_KEYWORD_NOISE_RE = re.compile(
    r"请问|帮我|帮忙|查一下|查询|查看|告诉我|我想知道|我想查|"
    r"这个|那个|这份|那份|该|这篇|那篇|"
    r"文件|文档|资料|附件|材料|"
    r"什么时候|何时|几点|几号|哪天|哪一天|"
    r"上传|提交|创建|发布|索引|入库|建库|解析|完成|"
    r"时间|日期|什么|时候|"
    r"关于|相关|以及|还有|"
    r"是|在|于|的|了|呢|吗|啊|吧|请|给|我|你|"
    r"哪些|多少|几个|几份|列出|显示|系统|平台|全站|所有|全部|"
    r"今年|去年|本月|上月|上个月|这个月|今天|昨天|前天|最近|近期|"
    r"本周|上周|近一周|近一个月|近三个月",
    re.IGNORECASE,
)


def _extract_file_keywords(question: str) -> list:
    """提取指定文件名/标题关键词，用于精确回答某个文件的时间。"""
    quoted = re.findall(r"[《“\"]([^》”\"]{1,80})[》”\"]", question)
    if quoted:
        return [item.strip() for item in quoted if item.strip()][:3]

    text = question
    for pattern in (_FULL_DATE_RE, _MONTH_RE, _YEAR_RE):
        text = pattern.sub(" ", text)
    text = re.sub(r"\d+\s*[月日号]", " ", text)
    text = _FILE_KEYWORD_NOISE_RE.sub(" ", text)
    text = re.sub(r"[，。！？、；：,.!?;:（）()\[\]【】]", " ", text)

    tokens = []
    for token in re.split(r"\s+", text):
        token = token.strip("-_")
        if not token:
            continue
        if len(token) >= 2 or re.fullmatch(r"[A-Za-z0-9]+", token):
            tokens.append(token)
    return tokens[:5]


def _is_topic_search(question: str) -> bool:
    """判断是否为纯关键词搜索（非完整问句）。

    只有以下情况路由到 find_docs：
    - 纯英文关键词（如 "mysql"）
    - 中英混合关键词（如 "MySQL第二版"）
    - 含"文件/文档/资料"等搜索意图词
    - 纯中文短语（如 "通知公告"）走 RAG 知识搜索
    """
    q_words = ["什么", "怎么", "如何", "为什么", "吗", "呢", "吧", "嘛", "哪",
               "谁", "何时", "何处", "是否", "能不能", "会不会", "有没有"]
    for w in q_words:
        if w in question:
            return False

    # 纯中文短语 → 走 RAG（可能是知识点名称）
    if _has_chinese_only(question):
        return False

    # 非中文关键词：短且不是完整问句
    return len(question) <= 20 and not question.endswith("？") and not question.endswith("?")


def _has_chinese_only(s: str) -> bool:
    """判断是否全部为中文（无英文字母）。"""
    return bool(re.fullmatch(r'[一-鿿　-〿＀-￯\s]+', s))


def _is_doc_time_question(question: str) -> bool:
    return bool(_TIME_WORD_RE.search(question)) and bool(
        _UPLOAD_VERB_RE.search(question) or _INDEX_VERB_RE.search(question)
    )


def _has_doc_reference(question: str) -> bool:
    """是否明确指向某一个文件（书名号/引号/这个文件/文件名等）。"""
    return bool(re.search(
        r"[《“\"].{1,80}[》”\"]|"
        r"(这个|这份|该|那个|那份|这篇|那篇)(文件|文档|资料|附件|材料)|"
        r"(文件|文档)\s*[A-Za-z0-9]",
        question,
    ))


def _build_params(question: str, date_range: Optional[dict], scope: tuple) -> dict:
    mine_only, scope_label = scope
    params = {}
    if date_range:
        params.update(date_range)
    params["mine_only"] = mine_only
    params["scope_label"] = scope_label

    category_match = re.search(
        r"(教学课件|试题库|实验指导|规章制度|培养方案|"
        r"论文摘要|参考资料|官网公告|新闻动态|课件|教案|"
        r"试卷|试题|题目|实验|指导书|规则|制度|方案|论文)",
        question,
    )
    if category_match:
        params["category"] = category_match.group(1)

    type_match = re.search(
        r"(PDF|Word|PPT|Excel|图片|视频|音频|表格|演示)",
        question,
        re.IGNORECASE,
    )
    if type_match:
        params["file_type"] = type_match.group(1).lower()
    return params


def route_question(question: str, user=None) -> dict:
    """判断问题意图并返回路由信息。"""
    if not question:
        return {"type": "general", "query_type": "", "params": {}, "confidence": 0.0}

    question = question.strip()
    date_range = _extract_date_range(question)
    scope = _extract_scope(question)
    mine_only, _ = scope
    has_upload_verb = bool(_UPLOAD_VERB_RE.search(question))
    is_count = bool(_COUNT_RE.search(question))
    is_list = bool(_LIST_RE.search(question))
    is_time_question = _is_doc_time_question(question)

    # 1. 明确指向某个文件的时间问题：上传时间 / 索引完成时间
    if is_time_question and (_has_doc_reference(question) or not date_range):
        params = _build_params(question, date_range, scope)
        keywords = _extract_file_keywords(question)
        if keywords:
            params["keywords"] = keywords
        if _INDEX_VERB_RE.search(question):
            return {
                "type": "data_query",
                "query_type": "doc_index_time",
                "params": params,
                "confidence": 0.95,
            }
        return {
            "type": "data_query",
            "query_type": "doc_upload_time",
            "params": params,
            "confidence": 0.95,
        }

    # 2. 带日期范围的上传问句：确定性统计/列表
    if date_range and has_upload_verb:
        params = _build_params(question, date_range, scope)
        if is_count and not _EXPLICIT_LIST_RE.search(question):
            return {
                "type": "data_query",
                "query_type": "count_docs_by_range",
                "params": params,
                "confidence": 0.95,
            }
        params["limit"] = 20
        return {
            "type": "data_query",
            "query_type": "list_docs_by_range",
            "params": params,
            "confidence": 0.95,
        }

    # 3. 只有日期范围的文件列表（如“上个月的文件”）
    if date_range and not is_time_question and re.search(r"文件|文档|资料", question):
        params = _build_params(question, date_range, scope)
        params["limit"] = 20
        return {
            "type": "data_query",
            "query_type": "list_docs_by_range",
            "params": params,
            "confidence": 0.85,
        }

    # 4. 明确的系统/全部文件列表（无日期范围）
    if (not date_range and re.search(r"系统|平台|全站|全系统|所有|全部", question)
            and re.search(r"文件|文档|资料", question)
            and not is_count):
        params = _build_params(question, None, scope)
        params["limit"] = 20
        return {
            "type": "data_query",
            "query_type": "list_docs_by_range",
            "params": params,
            "confidence": 0.9,
        }

    # 5. 原有文件管理查询（补充日期区间、范围语义）
    for query_type, patterns in _DATA_QUERY_PATTERNS.items():
        if _match_pattern(question, patterns):
            params = _build_params(question, date_range, scope)

            if query_type == "find_docs":
                keywords = _extract_keywords(question)
                if keywords:
                    params["keywords"] = keywords

            return {
                "type": "data_query",
                "query_type": query_type,
                "params": params,
                "confidence": 0.9,
            }

    # 6. 知识文档查询（热门/排行/统计/最新）
    for query_type, patterns in _KNOWLEDGE_DOC_QUERY_PATTERNS.items():
        if _match_pattern(question, patterns):
            params = _build_params(question, date_range, scope)
            return {
                "type": "data_query",
                "query_type": query_type,
                "params": params,
                "confidence": 0.9,
            }

    # 7. 兜底：纯关键词搜索（如 "mysql"、"奖学金"）→ 走 find_docs
    if _is_topic_search(question):
        user_role = getattr(user, 'role', None) if user else None
        # 管理员才走 find_docs（普通用户无上传权限，搜不到结果）
        if user_role in ('super_admin', 'admin'):
            keywords = _extract_keywords(question)
            if not keywords:
                # 纯英文关键词（如 "mysql"）不会被中文停用词过滤掉
                keywords = [question]
            return {
                "type": "data_query",
                "query_type": "find_docs",
                "params": {
                    "keywords": keywords,
                    "mine_only": mine_only,
                    "scope_label": scope[1],
                },
                "confidence": 0.6,
            }

    # 没有匹配到文件管理查询，走 RAG
    return {
        "type": "rag",
        "query_type": "",
        "params": {},
        "confidence": 0.7,
    }


def is_data_query(question: str) -> bool:
    """快速判断是否为文件管理查询（简化接口）。"""
    return route_question(question)["type"] == "data_query"
