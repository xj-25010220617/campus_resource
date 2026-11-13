"""多问题查询分解（阶段 3.3）。

只对"对比/分别/各学院/历年"这类天然含多个子目标的问句生效：
先拆成最多 N 个可独立检索的子问题，各自召回后合并，避免只答到其中一半。
默认关闭；任何异常都返回空列表（fail-open，退回原始单查询检索）。
"""
import json
import logging
import re
from typing import List

from django.conf import settings

logger = logging.getLogger(__name__)

_HINTS = (
    '对比', '比较', '分别', '各自', '各学院', '各专业', '历年', '每年',
    '相比', '区别', '差异', '以及', '同时',
)

_DECOMPOSE_PROMPT = """把用户问题拆成最多 {max_n} 个可以独立检索的子问题。

要求：
- 只在问题确实包含多个子目标时才拆（例如"计算机学院和智能学院分别有哪些专业"→2个）；
- 简单单一问题不要拆，返回只含原问题的数组；
- 每个子问题必须能独立表达完整语义，不要出现"它/该学院"这类指代；
- 只输出 JSON 字符串数组，不要编号、不要解释。

用户问题：{question}
"""


def should_decompose(question: str) -> bool:
    if not getattr(settings, 'RAG_DECOMPOSE_ENABLED', False):
        return False
    q = (question or '').strip()
    if len(q) < 10:
        return False
    return any(hint in q for hint in _HINTS)


def decompose_question(question: str) -> List[str]:
    """返回子问题列表；不需要拆或失败时返回 []。"""
    if not should_decompose(question):
        return []

    max_n = int(getattr(settings, 'RAG_DECOMPOSE_MAX_SUBQUERIES', 3) or 3)
    timeout = float(getattr(settings, 'RAG_DECOMPOSE_TIMEOUT', 8) or 8)
    prompt = _DECOMPOSE_PROMPT.replace('{max_n}', str(max_n)).replace('{question}', question)

    try:
        from utils.llm import llm_client
        raw = llm_client.chat(
            [{'role': 'user', 'content': prompt}],
            temperature=0.0,
            max_tokens=200,
            timeout=timeout,
        )
    except Exception as e:
        logger.warning('查询分解失败，退回单查询：%s', e)
        return []

    match = re.search(r'\[.*?\]', raw or '', re.S)
    if not match:
        return []
    try:
        items = json.loads(match.group(0))
    except (ValueError, TypeError):
        return []

    subs, seen = [], set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, str):
            continue
        text = item.strip().strip('"').strip()
        if len(text) < 3 or text in seen:
            continue
        seen.add(text)
        subs.append(text)
        if len(subs) >= max_n:
            break

    # 拆不出两个有效子问题就没有意义，直接走原链路
    if len(subs) < 2:
        return []
    return subs
