"""
问题意图路由 (Query Router)

判断用户问题的意图类型：
- data_query: 文件管理查询类（"我上传了多少个文件？"）
- rag: 知识检索类（需要搜索知识库）
- general: 常识/闲聊类（直接回答）
"""
import logging
import re
from typing import Optional

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
    ],
}

# 时间范围关键词
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


def _match_pattern(question: str, patterns: list) -> bool:
    """检查问题是否匹配任一正则模式。"""
    for pattern in patterns:
        if re.search(pattern, question, re.IGNORECASE):
            return True
    return False


def _extract_time_param(question: str) -> Optional[dict]:
    """从问题中提取时间范围参数。

    Returns:
        {"days": int} 或 None
    """
    # 优先匹配自定义天数
    for pattern, patterns_list in _TIME_PATTERNS.items():
        if pattern == "custom_days":
            for p in patterns_list:
                match = re.search(p, question)
                if match:
                    days = int(match.group(1))
                    return {"days": days}
        else:
            if _match_pattern(question, patterns_list):
                mapping = {
                    "this_year": {"days": 365},
                    "last_year": {"days": 365, "offset": 365},
                    "this_month": {"days": 30},
                    "last_month": {"days": 30, "offset": 30},
                    "this_week": {"days": 7},
                    "last_7_days": {"days": 7},
                    "last_30_days": {"days": 30},
                    "last_90_days": {"days": 90},
                }
                return mapping.get(pattern)

    return None


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



def _has_chinese_only(s: str) -> bool:
    """判断是否全部为中文（无英文字母）。"""
    import re
    return bool(re.fullmatch(r'[一-鿿　-〿＀-￯\s]+', s))


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


def route_question(question: str, user=None) -> dict:
    """判断问题意图并返回路由信息。"""
    if not question:
        return {"type": "general", "query_type": "", "params": {}, "confidence": 0.0}

    # 检查是否为文件管理查询
    for query_type, patterns in _DATA_QUERY_PATTERNS.items():
        if _match_pattern(question, patterns):
            params = {}

            # 提取时间参数
            time_param = _extract_time_param(question)
            if time_param:
                params.update(time_param)

            # 提取搜索关键词
            if query_type == "find_docs":
                keywords = _extract_keywords(question)
                if keywords:
                    params["keywords"] = keywords

            # 检查是否有分类限定词
            category_match = re.search(
                r"(教学课件|试题库|实验指导|规章制度|培养方案|"
                r"论文摘要|参考资料|官网公告|新闻动态|课件|教案|"
                r"试卷|试题|题目|实验|指导书|规则|制度|方案|论文)",
                question,
            )
            if category_match:
                params["category"] = category_match.group(1)

            # 检查文件类型
            type_match = re.search(
                r"(PDF|Word|PPT|Excel|图片|视频|音频|文档|表格|演示)",
                question,
                re.IGNORECASE,
            )
            if type_match:
                params["file_type"] = type_match.group(1).lower()

            return {
                "type": "data_query",
                "query_type": query_type,
                "params": params,
                "confidence": 0.9,
            }

    # 兜底：纯关键词搜索（如 "mysql"、"奖学金"）→ 走 find_docs
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
                "params": {"keywords": keywords},
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
