"""
Query 改写工具。

将用户口语化提问改写为更利于文档检索的版本。
与同义词库（utils/synonym.py）协同：先做规则改写，再做 LLM 改写。

典型场景：
    "那个有空调的考研自习室在哪" → "图书馆 教学楼 自习室 开放时间 空调 考研"
    "学费多少钱" → "成都东软学院 学费 标准 收费"
    "软工大三上什么课" → "软件工程 专业 培养方案 大三 课程"
"""
import logging
import re
from typing import List

from django.core.cache import cache

logger = logging.getLogger(__name__)

_CACHE_TTL = 600  # 10 分钟缓存

# 离线规则：在调用 LLM 之前做廉价改写
_RULE_PATTERNS = [
    (r'^那?个', ''),
    (r'请问[一下]?', ''),
    (r'我想?知道[一下]?', ''),
    (r'在哪[里儿]?(\?|？)?$', '位置 地址'),
    (r'多少[钱元](\?|？)?$', '费用 收费标准 学费'),
    (r'什么时候(\?|？)?$', '时间 日期'),
    (r'怎么[办申请做]?(\?|？)?$', '流程 申请方法 步骤'),
    (r'几[点点钟](\?|？)?$', '时间 开放时间'),
    (r'什么是', '介绍 定义'),
    (r'有哪些(\?|？)?$', '列表 类型'),
    (r'^软工$', '软件工程'),
    (r'^计算机$', '计算机科学与技术'),
    (r'^大[一二三四五](\?|？)?$', '培养方案 课程'),
    (r'^考研', '研究生 报考'),
    (r'^四六级$', '英语四六级 CET 报名'),
    (r'^体测$', '体育 体能测试'),
    (r'^挂科$', '重修 补考 学分'),
    (r'^毕业$', '毕业条件 学分 要求'),
]

_LLM_REWRITE_PROMPT = """你是校园问答系统的查询改写助手。目标：把用户的口语化提问改写为更利于文档检索的查询词。

要求：
1. 保留核心意图
2. 补全缺失的主语（学校、学院、专业等）
3. 删除语气词（那个、这个、请问）
4. 拆解口语化表达为多个关键词
5. 输出 1-3 个改写版本，按"最可能命中"排序

示例：
原问题：那个有空调的考研自习室在哪
改写：
1. 成都东软学院 考研 自习室 开放时间 空调
2. 图书馆 教学楼 自习室 位置 空调
3. 成都东软学院 自习室 位置

原问题：{question}

仅输出改写后的查询词列表，每行一个，不要编号或说明："""


def rewrite_query(question: str, use_cache: bool = True) -> List[str]:
    """
    将用户提问改写为多个检索词。

    Args:
        question: 用户原始问题
        use_cache: 是否使用 Django 缓存（避免重复 LLM 调用）

    Returns:
        改写后的查询词列表（含原文），第一个为首选
    """
    if not question or not question.strip():
        return [question]

    question = question.strip()
    cache_key = f'qr:{question}'

    if use_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    # 1. 规则改写（零成本，立即返回）
    rule_result = _rule_based_rewrite(question)
    if rule_result != question:
        result = [rule_result, question]
        if use_cache:
            cache.set(cache_key, result, _CACHE_TTL)
        return result

    # 2. LLM 改写（仅在规则未命中时）
    try:
        from utils.llm import llm_client
        prompt = _LLM_REWRITE_PROMPT.format(question=question)
        response = llm_client.chat(
            [{'role': 'user', 'content': prompt}],
            temperature=0.1, max_tokens=120,
        )
        if not response:
            return [question]

        lines = [
            line.strip().lstrip('0123456789.、) ').strip()
            for line in response.split('\n') if line.strip()
        ]
        lines = [l for l in lines if l and len(l) > 2][:3]
        if not lines:
            return [question]

        result = lines + [question]
        if use_cache:
            cache.set(cache_key, result, _CACHE_TTL)
        return result
    except Exception as e:
        logger.warning('Query 改写 LLM 调用失败: %s', e)
        return [question]


def _rule_based_rewrite(question: str) -> str:
    """基于规则的快速改写。命中任意规则即返回改写后文本。"""
    q = question
    for pattern, replacement in _RULE_PATTERNS:
        new_q = re.sub(pattern, replacement, q)
        if new_q != q:
            q = new_q
    q = re.sub(r'\s+', ' ', q).strip()
    return q if q else question
