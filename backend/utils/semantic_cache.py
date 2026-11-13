"""检索级语义缓存（阶段 5.2）。

只缓存"检索证据"，不缓存"最终答案"：
- 命中后仍走正常 Prompt 组装与生成，引用天然与当次一致，不存在"缓存答案引用过期"；
- key 由 role + 可见范围 + 过滤条件 + 知识库版本共同决定，越权/串号风险为零；
- 统计/名单/时间类问题一律不缓存（这类答案随时效变化）；
- 相似度必须 ≥ 阈值（默认 0.95），低相似不走缓存。
"""
import hashlib
import logging
import math
from typing import List, Optional

from django.conf import settings

from utils.cache_safe import safe_get, safe_set

logger = logging.getLogger(__name__)

# 时效/统计/名单类问题禁止命中缓存
_BLOCK_HINTS = (
    '名单', '排名', '多少人', '人数', '多少', '统计', '总数', '合计',
    '最新', '今年', '历年', '截止', '几号', '日期', '时间', '现在',
)


def is_cacheable(question: str) -> bool:
    if not getattr(settings, 'SEMANTIC_CACHE_ENABLED', False):
        return False
    q = (question or '').strip()
    if len(q) < 4:
        return False
    return not any(hint in q for hint in _BLOCK_HINTS)


def _knowledge_version() -> int:
    """用文档表最近更新时间做版本号；60s 内复用，避免每次检索都查库。"""
    cached = safe_get('semcache:kv', None)
    if cached is not None:
        return int(cached)
    try:
        from apps.knowledge.models import KnowledgeDoc
        latest = KnowledgeDoc.objects.order_by('-updated_at').values_list('updated_at', flat=True).first()
        version = int(latest.timestamp()) if latest else 0
    except Exception:
        version = 0
    safe_set('semcache:kv', version, 60)
    return version


def _scope_key(user_context: dict, parsed_filters: dict, doc_scope) -> str:
    raw = '|'.join([
        str((user_context or {}).get('user_id')),
        str((user_context or {}).get('role')),
        str((user_context or {}).get('college_id')),
        str((user_context or {}).get('department_id')),
        ','.join(map(str, sorted(doc_scope or []))),
        repr(sorted((parsed_filters or {}).items())),
        str(getattr(settings, 'RETRIEVAL_FUSION', 'weighted')),
        str(_knowledge_version()),
    ])
    return hashlib.md5(raw.encode('utf-8')).hexdigest()


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    na = math.sqrt(math.fsum(x * x for x in a))
    nb = math.sqrt(math.fsum(y * y for y in b))
    if not na or not nb:
        return 0.0
    return math.fsum(x * y for x, y in zip(a, b)) / (na * nb)


def lookup(question: str, user_context: dict, parsed_filters: dict, doc_scope) -> Optional[List[dict]]:
    """命中返回证据列表副本；未命中/不满足条件返回 None。"""
    if not is_cacheable(question):
        return None

    from utils.llm import llm_client
    vec = llm_client.embed_one(question, is_query=True)
    if not vec:
        return None

    entries = safe_get('semcache:' + _scope_key(user_context, parsed_filters, doc_scope), None)
    if not entries:
        return None

    threshold = float(getattr(settings, 'SEMANTIC_CACHE_THRESHOLD', 0.95) or 0.95)
    best_score, best_hits = 0.0, None
    for entry in entries:
        score = _cosine(vec, entry.get('vec') or [])
        if score > best_score:
            best_score, best_hits = score, entry.get('hits')
    if best_hits is None or best_score < threshold:
        return None

    logger.info('[语义缓存] 命中 score=%.4f q=%s', best_score, question[:30])
    return [dict(h) for h in best_hits]


def store(question: str, user_context: dict, parsed_filters: dict, doc_scope, hits: List[dict]) -> None:
    """只缓存干净证据（不缓存被判"证据不足"的结果）。"""
    if not is_cacheable(question) or not hits:
        return
    if any(h.get('evidence_insufficient') for h in hits):
        return

    from utils.llm import llm_client
    vec = llm_client.embed_one(question, is_query=True)
    if not vec:
        return

    key = 'semcache:' + _scope_key(user_context, parsed_filters, doc_scope)
    entries = safe_get(key, None) or []
    entries.insert(0, {
        'q': question[:100],
        'vec': vec,
        'hits': [{k: v for k, v in h.items() if k != 'evidence_insufficient'} for h in hits],
    })
    max_entries = int(getattr(settings, 'SEMANTIC_CACHE_MAX_ENTRIES', 50) or 50)
    safe_set(key, entries[:max_entries], getattr(settings, 'SEMANTIC_CACHE_TTL', 600))
