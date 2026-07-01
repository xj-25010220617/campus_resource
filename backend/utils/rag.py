"""
RAG 检索管道
- 问题向量化 → Milvus 搜索 → 关键词匹配 → 重排序 → Top-K 结果
- 上下文组装：将检索结果拼接为 LLM Prompt
"""
import logging
from typing import List, Tuple

from django.db.models import Q

from .llm import llm_client
from .milvus import milvus_client

logger = logging.getLogger(__name__)


# RAG System Prompt
SYSTEM_PROMPT = """你是成都东软学院的 AI 知识助手。你的职责是基于提供的参考资料，准确回答师生关于学校的问题。

请遵循以下规则：
1. 只基于下方【参考资料】的内容回答，不要编造信息
2. 如果参考资料不足以回答问题，请明确告知用户"我目前的知识库中还没有相关信息"
3. 回答要简洁清晰，使用中文，适当使用 Markdown 排版
4. 如果参考了多份文档，可以分点说明
5. 引用具体数据时注明参考来源（如"根据《XXX》..."）"""


def build_rag_prompt(question: str, chunks: List[dict]) -> List[dict]:
    """
    构造 RAG Prompt。
    参数:
        question: 用户问题
        chunks: 检索结果 [{'content': str, 'doc_title': str, ...}, ...]
    返回:
        messages 列表，可直接传给 LLM chat
    """
    # 组装参考资料
    refs = []
    for i, chunk in enumerate(chunks, 1):
        title = chunk.get('doc_title', '未知文档')
        content = chunk.get('content', '')
        refs.append(f'### 参考资料{i}（来源：{title}）\n{content}')

    refs_text = '\n\n'.join(refs) if refs else '暂无相关参考资料。'

    return [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': f'【参考资料】\n{refs_text}\n\n【用户问题】\n{question}'},
    ]


def retrieve(
    question: str,
    top_k: int = 20,
    min_score: float = 0.5,
) -> List[dict]:
    """
    检索管道：向量检索 + 关键词匹配 → 重排序。
    返回: [{'chunk_id': int, 'doc_id': int, 'doc_title': str,
            'content': str, 'score': float, 'page': int}, ...]
    """
    # 1. 向量检索
    question_embedding = llm_client.embed_one(question)
    vector_hits = []
    if question_embedding:
        vector_hits = milvus_client.search(question_embedding, top_k=top_k)

    # 2. 关键词匹配（MySQL FULLTEXT）
    keyword_hits = _keyword_search(question, top_k=top_k)

    # 3. 合并 + 重排序
    merged = _merge_and_rerank(vector_hits, keyword_hits)

    # 4. 加载 chunk 完整文本
    return _load_chunks(merged, min_score)


def _keyword_search(question: str, top_k: int = 20) -> List[dict]:
    """
    MySQL 关键词匹配搜索。
    搜索范围：knowledge_doc.title + knowledge_doc.description + keyword.phrase
    """
    from apps.knowledge.models import KnowledgeDoc
    from apps.knowledge.models import DocChunk

    # 用 jieba 分词提取关键短语
    try:
        import jieba
        keywords = list(jieba.cut(question))[:10]
    except ImportError:
        keywords = question.split()[:10]

    # 搜索 knowledge_doc
    query = Q()
    for kw in keywords:
        if len(kw) >= 2:
            query |= Q(title__icontains=kw)
            query |= Q(description__icontains=kw)

    docs = KnowledgeDoc.objects.filter(
        query, status='indexed'
    ).values_list('id', 'title')[:top_k]

    results = []
    for doc_id, doc_title in docs:
        chunks = DocChunk.objects.filter(doc_id=doc_id)[:2]
        for chunk in chunks:
            results.append({
                'chunk_id': chunk.id,
                'doc_id': doc_id,
                'doc_title': doc_title,
                'score': 0.6,  # 关键词匹配基础分
                'page': chunk.page_start,
            })
    return results


def _merge_and_rerank(
    vector_hits: List[dict],
    keyword_hits: List[dict],
    vector_weight: float = 0.7,
) -> List[dict]:
    """
    合并两路结果并重排序。
    向量得分 × 0.7 + 关键词得分 × 0.3
    """
    merged = {}
    for hit in vector_hits:
        key = hit.get('vector_id') or f"v_{hit.get('chunk_id')}"
        merged[key] = {
            'chunk_id': hit.get('chunk_id'),
            'doc_id': hit.get('doc_id'),
            'vector_id': hit.get('vector_id'),
            'score': hit['score'] * vector_weight,
        }

    for hit in keyword_hits:
        key = f"kw_{hit.get('chunk_id')}"
        existing = merged.get(key)
        if existing:
            existing['score'] += hit['score'] * (1 - vector_weight)
        else:
            merged[key] = {
                'chunk_id': hit.get('chunk_id'),
                'doc_id': hit.get('doc_id'),
                'vector_id': hit.get('vector_id', ''),
                'score': hit['score'] * (1 - vector_weight),
            }

    # 按得分降序
    sorted_hits = sorted(merged.values(), key=lambda x: x['score'], reverse=True)
    return sorted_hits


def _load_chunks(hits: List[dict], min_score: float = 0.5) -> List[dict]:
    """
    从 MySQL 加载 chunk 完整文本，组装返回。
    过滤低于 min_score 的结果。
    """
    from apps.knowledge.models import DocChunk

    chunk_ids = [h['chunk_id'] for h in hits if h.get('chunk_id')]
    chunks_map = {}
    if chunk_ids:
        chunks_qs = DocChunk.objects.filter(
            id__in=chunk_ids
        ).select_related('doc')
        for c in chunks_qs:
            chunks_map[c.id] = c

    results = []
    for hit in hits[:5]:  # Top-5
        if hit['score'] < min_score:
            continue
        chunk = chunks_map.get(hit.get('chunk_id'))
        if not chunk:
            continue
        results.append({
            'chunk_id': chunk.id,
            'doc_id': chunk.doc_id,
            'doc_title': chunk.doc.title if chunk.doc else '未知文档',
            'content': chunk.content,
            'score': round(hit['score'], 4),
            'page': chunk.page_start,
        })

    return results
