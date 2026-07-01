"""
问题解析器（Query Parser）

功能：将用户自然语言提问解析为结构化硬过滤条件。
策略：LLM 抽取（首选）→ 规则匹配回退（兜底）
"""
import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# LLM 抽取 Prompt
_PARSE_PROMPT = """你是校园知识库的查询解析器。请把用户提问解析为 JSON 格式的过滤条件。

可选字段（仅以下 7 个，全部可选）：
- college: 学院名（仅以下选项之一：['计算机与软件学院', '信息与商务管理学院', '外国语学院', '数字艺术与设计学院', '健康医养技术学院', '马克思主义学院', '通识教学部', '体育教学部', '创新创业学院', '国际合作学院', '继续教育学院', '基础部', '现代管理研究院']）
- discipline: 学科方向（仅以下选项之一：['计算机科学与技术', '软件工程', '数字媒体技术', '电子商务', '英语', '视觉传达设计', '数据科学与大数据技术', '物联网工程', '人工智能']）
- category: 资源一级类型（仅以下选项之一：['课件', '习题', '试卷', '论文', '教案', '实验报告', '毕业设计', '教学大纲', '其他']）
- target_audience: 适用对象（仅以下选项之一：'undergrad'/'postgrad'/'teacher'/'staff'/'all'）
- course_term: 课程+学期（字符串，例："操作系统2025春"）
- file_type: 文件格式（仅以下选项之一：'pdf'/'doc'/'ppt'/'xls'/'image'/'video'/'audio'）
- time_range: 时间范围（格式 "YYYY" 或 "YYYY-MM"）

只返回 JSON，不要解释。如果无法判断，留空或省略。

示例：
用户："计算机系大三下学期的操作系统复习资料"
输出：{{"college": "计算机与软件学院", "discipline": "计算机科学与技术", "target_audience": "undergrad", "course_term": "操作系统2025春", "category": "习题"}}

用户："{question}"
输出：
"""


# 规则回退：常见关键词 → 字段映射
_RULE_KEYWORDS = {
    'college': {
        '计算机': '计算机与软件学院',
        '信管': '信息与商务管理学院',
        '外语': '外国语学院',
        '艺设': '数字艺术与设计学院',
        '医养': '健康医养技术学院',
    },
    'target_audience': {
        '本科': 'undergrad',
        '研究生': 'postgrad',
        '教师': 'teacher',
        '老师': 'teacher',
        '大一': 'undergrad',
        '大二': 'undergrad',
        '大三': 'undergrad',
        '大四': 'undergrad',
    },
    'file_type': {
        'PDF': 'pdf',
        'pdf': 'pdf',
        'PPT': 'ppt',
        'ppt': 'ppt',
        'Word': 'doc',
        'doc': 'doc',
        'Excel': 'xls',
        'xls': 'xls',
    },
}


def parse(question: str, user_context: Optional[dict] = None) -> dict:
    """
    解析用户提问为结构化过滤条件。

    Args:
        question: 用户原始问题
        user_context: 用户上下文（含 user_id / role / college_id）

    Returns:
        过滤条件字典，例：
        {'college_id': 1, 'target_audience': 'undergrad', 'course_term': '操作系统'}
    """
    user_context = user_context or {}

    # 1. LLM 抽取（首选）
    try:
        raw = _parse_by_llm(question)
        if raw:
            return _normalize(raw, user_context)
    except Exception as e:
        logger.warning(f'LLM 解析失败，回退到规则匹配: {e}')

    # 2. 规则匹配兜底
    return _parse_by_rules(question, user_context)


def _parse_by_llm(question: str) -> Optional[dict]:
    """LLM 抽取（首选）"""
    from utils.llm import llm_client

    prompt = _PARSE_PROMPT.format(question=question)
    raw = llm_client.chat(
        messages=[{'role': 'user', 'content': prompt}],
        temperature=0.0,
        max_tokens=200,
    )
    if not raw:
        return None

    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _parse_by_rules(question: str, user_context: dict) -> dict:
    """规则匹配（兜底）"""
    result = {}
    for field, mapping in _RULE_KEYWORDS.items():
        for kw, value in mapping.items():
            if kw in question:
                result[field] = value
                break

    course_match = re.search(r'([\u4e00-\u9fa5]{2,8}20\d{2}[春夏秋冬])', question)
    if course_match:
        result['course_term'] = course_match.group(1)

    return _normalize(result, user_context)


def _normalize(raw: dict, user_context: dict) -> dict:
    """将学院/学科名称转为 ID，并注入用户上下文"""
    from apps.accounts.models import College
    from apps.classify.models import Discipline, Category

    result = {}

    if 'college' in raw:
        try:
            college = College.objects.get(name=raw['college'])
            result['college_id'] = college.id
        except College.DoesNotExist:
            logger.debug(f'未找到学院: {raw["college"]}')

    if 'discipline' in raw:
        try:
            discipline = Discipline.objects.get(name=raw['discipline'])
            result['discipline_id'] = discipline.id
        except Discipline.DoesNotExist:
            logger.debug(f'未找到学科: {raw["discipline"]}')

    if 'category' in raw:
        try:
            cat = Category.objects.get(name=raw['category'], parent__isnull=True)
            result['category_ids'] = [cat.id] + [c.id for c in cat.children.all()]
        except Category.DoesNotExist:
            logger.debug(f'未找到分类: {raw["category"]}')

    # 透传简单字段
    for key in ('target_audience', 'course_term', 'file_type', 'time_range'):
        if key in raw:
            result[key] = raw[key]

    return result
