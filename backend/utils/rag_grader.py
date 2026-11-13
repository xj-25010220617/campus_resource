"""检索证据充分性判定（CRAG 最小闭环）。

作用：第一轮检索结束后，让轻量模型判断「现有资料是否足以回答这个问题」，
据此决定是否扩大检索一次，并给生成侧一个明确的「证据不足」信号，
避免在资料确实不够时凭空编造。

设计原则：
- 默认关闭（RAG_GRADER_ENABLED=False），开启后才有额外 LLM 调用；
- 任何异常/超时/解析失败一律返回 None（fail-open），绝不阻断正常问答；
- 结果按「问题 + 命中文档集合」缓存，避免同一问题重复计费。
"""
import hashlib
import json
import logging
import re
from typing import List, Optional

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_GRADE_PROMPT = """你是检索质量审核器。只根据下面提供的资料判断：这些资料是否足以完整、准确地回答用户问题。

判定标准：
- 若资料直接包含回答问题所需的关键事实（数字、名单、条款、时间等），判 sufficient=true；
- 若资料只有相关背景但缺少关键事实，判 sufficient=false，并在 missing 里写清缺什么；
- 不要用你自己的知识判断"答案对不对"，只判断"资料够不够"。

只输出 JSON，不要任何解释：
{"sufficient": true 或 false, "missing": "缺少的信息（充分时为空字符串）"}

用户问题：{question}

资料：
{evidence}
"""


def _evidence_text(hits: List[dict], max_hits: int, snippet_chars: int) -> str:
    lines = []
    for i, hit in enumerate(hits[:max_hits], 1):
        title = hit.get('doc_title') or '未知文档'
        content = (hit.get('content') or '').strip().replace('\n', ' ')
        if len(content) > snippet_chars:
            content = content[:snippet_chars] + '…'
        lines.append(f'[{i}] 《{title}》 {content}')
    return '\n'.join(lines)


def _parse_verdict(raw: Optional[str]) -> Optional[dict]:
    """从模型输出里抠出 JSON；容忍 ```json 包裹与前后废话。"""
    if not raw:
        return None
    match = re.search(r'\{.*\}', raw, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except (ValueError, TypeError):
        return None
    if 'sufficient' not in data:
        return None
    return {
        'sufficient': bool(data.get('sufficient')),
        'missing': str(data.get('missing') or '')[:200],
    }


def grade_sufficiency(
    question: str,
    hits: List[dict],
    user_context: Optional[dict] = None,
) -> Optional[dict]:
    """返回 {'sufficient': bool, 'missing': str}；关闭或失败时返回 None。"""
    if not getattr(settings, 'RAG_GRADER_ENABLED', False):
        return None
    if not hits:
        return {'sufficient': False, 'missing': '未检索到任何资料'}

    max_hits = int(getattr(settings, 'RAG_GRADER_MAX_HITS', 5) or 5)
    snippet_chars = int(getattr(settings, 'RAG_GRADER_SNIPPET_CHARS', 400) or 400)
    timeout = float(getattr(settings, 'RAG_GRADER_TIMEOUT', 8) or 8)
    cache_ttl = int(getattr(settings, 'RAG_GRADER_CACHE_TTL', 600) or 0)

    doc_ids = sorted({str(h.get('doc_id')) for h in hits[:max_hits] if h.get('doc_id')})
    cache_key = 'rag_grade:' + hashlib.md5(
        f'{question}|{",".join(doc_ids)}|{max_hits}'.encode('utf-8'),
    ).hexdigest()
    if cache_ttl:
        cached = cache.get(cache_key)
        if cached:
            return cached

    evidence = _evidence_text(hits, max_hits, snippet_chars)
    # 模板里含 JSON 花括号，不能用 str.format（会把 {"sufficient"} 当占位符）
    prompt = _GRADE_PROMPT.replace('{question}', question).replace('{evidence}', evidence)

    try:
        from utils.llm import llm_client
        raw = llm_client.chat(
            [{'role': 'user', 'content': prompt}],
            temperature=0.0,
            max_tokens=120,
            timeout=timeout,
        )
    except Exception as e:
        # fail-open：判定失败不能影响正常问答
        logger.warning('检索充分性判定失败，跳过：%s', e)
        return None

    verdict = _parse_verdict(raw)
    if verdict is None:
        logger.warning('检索充分性判定输出无法解析：%s', (raw or '')[:120])
        return None
    if cache_ttl:
        cache.set(cache_key, verdict, cache_ttl)
    return verdict
