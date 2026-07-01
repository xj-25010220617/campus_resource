"""
RAG 检索管道
- 问题向量化（含 LRU 缓存）→ Milvus 搜索 → 关键词匹配 → 重排序 → Top-K 结果
- 上下文组装：将检索结果拼接为 LLM Prompt
"""
import logging
import threading
import time
from typing import Dict, List, Optional

from .llm import llm_client
from .milvus import milvus_client

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Embedding 缓存（LRU + TTL，避免相同问题重复调 Embedding API）
# ------------------------------------------------------------------
_EMBEDDING_CACHE_TTL = 300  # 5 分钟
_EMBEDDING_CACHE_MAX_SIZE = 500

_embedding_cache: Dict[str, tuple] = {}
_embedding_cache_lock = threading.Lock()


def _cached_embed(question: str) -> Optional[List[float]]:
    """带 LRU + TTL 缓存的问题向量化"""
    now = time.time()
    with _embedding_cache_lock:
        # 命中且未过期
        if question in _embedding_cache:
            vec, ts = _embedding_cache[question]
            if now - ts < _EMBEDDING_CACHE_TTL:
                return vec
            del _embedding_cache[question]

    # 未命中，调用真实 API
    vec = llm_client.embed_one(question)
    if vec:
        with _embedding_cache_lock:
            # 超过容量限制时淘汰最旧的条目
            if len(_embedding_cache) >= _EMBEDDING_CACHE_MAX_SIZE:
                oldest = min(_embedding_cache.keys(), key=lambda k: _embedding_cache[k][1])
                del _embedding_cache[oldest]
            _embedding_cache[question] = (vec, now)
    return vec


# ------------------------------------------------------------------
# RAG System Prompt（与设计文档第 14.2 节⑦对齐）
# ------------------------------------------------------------------
SYSTEM_PROMPT = """你是成都东软学院的AI知识助手，专门回答与学院教学、科研、管理相关的问题。\
请基于以下参考资料回答问题。如果参考资料无法回答，请如实告知，不要编造。

参考资料：
---
{references}
---

指令：
1. 优先使用参考资料中的内容回答
2. 如果资料足够，请列出具体的出处（文档名+页码）
3. 如果资料不足，说明"根据现有知识库，暂时无法完整回答这个问题"
4. 回答使用中文，专业术语可附英文原文
5. 回答应结构清晰，适当使用列表或分点"""


# ------------------------------------------------------------------
# RAG 控制参数（可通过 settings 覆盖）
# ------------------------------------------------------------------
RAG_TOP_K = 10
RAG_SCORE_THRESHOLD = 0.6       # 低于此分数不放入 LLM 上下文
RAG_REFERENCE_THRESHOLD = 0.7   # 低于此分数不作为引用展示
CONTEXT_MAX_CHARS = 8000


# ------------------------------------------------------------------
# Prompt 构造
# ------------------------------------------------------------------
def build_rag_prompt(question: str, chunks: List[dict]) -> List[dict]:
    """
    构造 RAG Prompt。

    Args:
        question: 用户问题
        chunks: 检索结果 [{'content': str, 'doc_title': str, 'page': int, 'score': float}, ...]

    Returns:
        messages 列表，可直接传给 LLM chat
    """
    ref_lines = []
    total_chars = 0
    for i, chunk in enumerate(chunks, 1):
        title = chunk.get('doc_title', '未知文档')
        page = chunk.get('page', 0)
        content = chunk.get('content', '')
        ref_block = f'### 参考资料{i}（来源：{title}'
        if page:
            ref_block += f' 第{page}页'
        ref_block += f'）\n{content}'
        total_chars += len(ref_block)
        if total_chars > CONTEXT_MAX_CHARS:
            break
        ref_lines.append(ref_block)

    refs_text = '\n\n'.join(ref_lines) if ref_lines else '暂无相关参考资料。'
    system_content = SYSTEM_PROMPT.format(references=refs_text)

    return [
        {'role': 'system', 'content': system_content},
        {'role': 'user', 'content': question},
    ]


# ------------------------------------------------------------------
# 检索主入口
# ------------------------------------------------------------------
def retrieve(
    question: str,
    top_k: int = RAG_TOP_K,
    min_score: float = RAG_SCORE_THRESHOLD,
    college_id: int = None,
) -> List[dict]:
    """
    检索管道：向量检索 + 关键词匹配 → 重排序 → 加载 doc 元信息。

    Args:
        question: 用户问题
        top_k: Milvus 检索返回数量
        min_score: 最低分阈值（低于此分数不返回）
        college_id: 限制检索范围到指定学院（None 表示全量检索）

    Returns:
        [{'chunk_id': int, 'doc_id': int, 'doc_title': str,
          'content': str, 'score': float, 'page': int}, ...]
    """
    # 1. 向量检索（带缓存）
    question_embedding = _cached_embed(question)
    vector_hits = []
    if question_embedding:
        vector_hits = milvus_client.search(question_embedding, top_k=top_k)

    # 2. 关键词匹配（MySQL LIKE，作为向量检索补充）
    keyword_hits = _keyword_search(question, top_k=top_k, college_id=college_id)

    # 3. 合并 + 重排序
    merged = _merge_and_rerank(vector_hits, keyword_hits)

    # 4. 加载 doc 元信息（title、college 等）
    return _enrich_with_doc_meta(merged, min_score)


# ------------------------------------------------------------------
# 关键词搜索（降级方案 / 补充检索）
# ------------------------------------------------------------------
def _keyword_search(question: str, top_k: int = 10, college_id: int = None) -> List[dict]:
    """
    MySQL 关键词匹配搜索。
    搜索范围：knowledge_doc.title + knowledge_doc.description
    当 knowledge app 模型未就绪时降级返回空列表。
    """
    try:
        from apps.knowledge.models import KnowledgeDoc
    except ImportError:
        logger.debug('KnowledgeDoc 模型尚未就绪，跳过关键词搜索')
        return []

    try:
        import jieba
        keywords = list(jieba.cut(question))[:10]
    except ImportError:
        keywords = question.split()[:10]

    from django.db.models import Q

    query = Q()
    for kw in keywords:
        if len(kw) >= 2:
            query |= Q(title__icontains=kw)
            query |= Q(description__icontains=kw)

    qs = KnowledgeDoc.objects.filter(query, status='indexed')
    if college_id is not None:
        qs = qs.filter(college_id=college_id)

    docs = qs.values_list('id', 'title')[:top_k]

    results = []
    for doc_id, doc_title in docs:
        results.append({
            'doc_id': doc_id,
            'doc_title': doc_title,
            'score': 0.55,             # 关键词匹配基础分（略低于向量阈值）
            'content': '',
            'page': 0,
        })
    return results


# ------------------------------------------------------------------
# 合并重排序
# ------------------------------------------------------------------
def _merge_and_rerank(
    vector_hits: List[dict],
    keyword_hits: List[dict],
    vector_weight: float = 0.7,
) -> List[dict]:
    """
    合并两路结果并重排序。
    向量得分 × 0.7 + 关键词得分 × 0.3
    向量结果自带 chunk_text，直接使用。
    """
    merged = {}

    for hit in vector_hits:
        key = hit.get('vector_id') or f"v_{hit.get('doc_id')}_{hit.get('chunk_index')}"
        merged[key] = {
            'doc_id': hit.get('doc_id'),
            'chunk_index': hit.get('chunk_index'),
            'vector_id': hit.get('vector_id'),
            'content': hit.get('chunk_text', ''),
            'page': hit.get('page_number', 0),
            'score': hit['score'] * vector_weight,
        }

    for hit in keyword_hits:
        key = f"kw_{hit.get('doc_id')}"
        existing = merged.get(key)
        if existing:
            existing['score'] += hit['score'] * (1 - vector_weight)
        else:
            merged[key] = {
                'doc_id': hit.get('doc_id'),
                'content': hit.get('content', ''),
                'page': hit.get('page', 0),
                'vector_id': '',
                'score': hit['score'] * (1 - vector_weight),
            }

    # 按得分降序
    return sorted(merged.values(), key=lambda x: x['score'], reverse=True)


# ------------------------------------------------------------------
# 加载 doc 元信息
# ------------------------------------------------------------------
def _enrich_with_doc_meta(hits: List[dict], min_score: float) -> List[dict]:
    """
    查询 MySQL knowledge_doc 表，补全文档标题等元信息。
    过滤低于 min_score 的结果，返回 Top-5。
    """
    doc_ids = list({h['doc_id'] for h in hits if h.get('doc_id')})

    doc_title_map = {}
    if doc_ids:
        try:
            from apps.knowledge.models import KnowledgeDoc
            docs_qs = KnowledgeDoc.objects.filter(id__in=doc_ids).values('id', 'title')
            for d in docs_qs:
                doc_title_map[d['id']] = d['title']
        except ImportError:
            logger.debug('KnowledgeDoc 模型尚未就绪，使用占位标题')

    results = []
    for hit in hits:
        if hit['score'] < min_score:
            continue
        if not hit.get('content'):
            continue
        results.append({
            'doc_id': hit['doc_id'],
            'doc_title': doc_title_map.get(hit['doc_id'], '未知文档'),
            'content': hit['content'],
            'score': round(hit['score'], 4),
            'page': hit.get('page', 0),
        })
        if len(results) >= 5:  # Top-5
            break

    return results
