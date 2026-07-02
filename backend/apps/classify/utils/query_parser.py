"""
问题解析器（Query Parser）

将用户提问解析为结构化过滤条件（LLM 优先，规则回退兜底）。
输入：question 字符串 + user_context 字典
输出：parsed_filters 字典（如 {"college_id": 1, "discipline_id": 2, "file_type": "pdf"}）

依赖：
    - utils.llm.llm_client：LLM 调用
    - apps.accounts.models.College：学院名→ID 归一化
    - apps.classify.models.Discipline / Category：学科/分类归一化
"""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM 抽取 Prompt
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# 规则回退：关键词→字段映射
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# 公共接口
# ---------------------------------------------------------------------------
def parse(question: str, user_context: Optional[dict] = None) -> dict:
    """
    解析用户提问为结构化过滤条件。

    Args:
        question: 用户原始问题
        user_context: 用户上下文（含 user_id / role / college_id），可选

    Returns:
        过滤条件字典，例：
        {
            'college_id': 1,
            'discipline_id': 2,
            'category_ids': [3, 4, 5],
            'target_audience': 'undergrad',
            'course_term': '操作系统2025春',
            'file_type': 'pdf',
            'time_range': '2025',
        }
        所有字段均可能不存在（表示该条件未解析出）
    """
    # 1. LLM 抽取（首选）
    try:
        raw = _parse_by_llm(question)
        if raw:
            return _normalize(raw)
    except Exception as e:
        logger.warning(f'LLM 解析失败，回退到规则匹配: {e}')

    # 2. 规则匹配回退
    return _parse_by_rules(question)


def _parse_by_llm(question: str) -> Optional[dict]:
    """LLM 抽取（首选），解析失败返回 None"""
    from utils.llm import llm_client

    prompt = _PARSE_PROMPT.format(question=question)
    raw = llm_client.chat(
        messages=[{'role': 'user', 'content': prompt}],
        temperature=0.0,
        max_tokens=200,
    )
    if not raw:
        return None

    # 从 LLM 返回中提取 JSON
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        logger.warning(f'LLM 返回 JSON 解析失败: {raw[:100]}')
        return None


def _parse_by_rules(question: str) -> dict:
    """规则匹配（兜底），从问题中按关键词匹配字段"""
    result = {}

    for field, mapping in _RULE_KEYWORDS.items():
        for kw, value in mapping.items():
            if kw in question:
                result[field] = value
                break

    # 提取课程+学期（格式：中文 2-8 字 + 20XX + 春夏秋冬）
    course_match = re.search(r'([\u4e00-\u9fa5]{2,8}20\d{2}[春夏秋冬])', question)
    if course_match:
        result['course_term'] = course_match.group(0)

    return result


def _normalize(raw: dict) -> dict:
    """
    归一化：将 LLM 返回的学院/学科/分类名称转换为数据库 ID。

    Args:
        raw: LLM 解析结果（可能含 college/discipline/category 名称字符串）

    Returns:
        归一化后的过滤条件（名称字符串转为 *_id 整数字段）
    """
    from apps.accounts.models import College
    from apps.classify.models import Discipline, Category

    result = {}

    # 学院名 → college_id
    if 'college' in raw and raw['college']:
        try:
            college_obj = College.objects.get(name=raw['college'])
            result['college_id'] = college_obj.id
        except College.DoesNotExist:
            logger.debug(f'学院名未匹配到数据库记录: {raw["college"]}')
        except Exception as e:
            logger.warning(f'查询学院失败: {e}')

    # 学科名 → discipline_id
    if 'discipline' in raw and raw['discipline']:
        try:
            disc_obj = Discipline.objects.get(name=raw['discipline'])
            result['discipline_id'] = disc_obj.id
        except Discipline.DoesNotExist:
            logger.debug(f'学科名未匹配到数据库记录: {raw["discipline"]}')
        except Exception as e:
            logger.warning(f'查询学科失败: {e}')

    # 分类名 → category_ids（含一级 + 所有二级子分类 ID）
    if 'category' in raw and raw['category']:
        try:
            cat = Category.objects.get(name=raw['category'], parent__isnull=True)
            result['category_ids'] = [cat.id] + [
                c.id for c in cat.children.all()
            ]
        except Category.DoesNotExist:
            logger.debug(f'分类名未匹配到数据库记录: {raw["category"]}')
        except Exception as e:
            logger.warning(f'查询分类失败: {e}')

    # 透传简单字段（名称直接可用，无需归一化）
    for key in ('target_audience', 'course_term', 'file_type', 'time_range'):
        if key in raw and raw[key]:
            result[key] = raw[key]

    return result
