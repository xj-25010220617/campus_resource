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
def _keyword_search(
    question: str,
    top_k: int = 10,
    doc_ids: Optional[List[int]] = None,
) -> List[dict]:
    """
    MySQL 关键词匹配搜索。
    搜索范围：resource.title + resource.description + resource.course_term
    支持限定 doc_ids 范围（硬过滤后的候选集）。

    Args:
        question: 用户问题
        top_k: 返回数量
        doc_ids: 限定检索的 doc_id 列表（来自硬过滤）
    """
    try:
        from apps.resources.models import Resource
    except ImportError:
        logger.debug('Resource 模型尚未就绪，跳过关键词搜索')
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
            query |= Q(course_term__icontains=kw)

    qs = Resource.objects.filter(query, status='published')
    if doc_ids is not None:
        qs = qs.filter(id__in=doc_ids)

    docs = qs.values_list('id', 'title')[:top_k]

    results = []
    for doc_id, doc_title in docs:
        results.append({
            'doc_id': doc_id,
            'doc_title': doc_title,
            'score': 0.55,
            'content': '',
            'page': 0,
        })
    return results


def _keywords_boost_from_filters(filters: dict) -> dict:
    """
    从 parsed_filters 中提取高权重词，用于 RAG 召回加成。

    Args:
        filters: parse() 输出的过滤条件

    Returns:
        关键词 → 权重映射
    """
    boost = {}
    if term := filters.get('course_term'):
        boost[term] = 10
    return boost


# ------------------------------------------------------------------
# 合并重排序
# ------------------------------------------------------------------
def _merge_and_rerank(
    vector_hits: List[dict],
    keyword_hits: List[dict],
    vector_weight: float = 0.7,
    keywords_boost: Optional[dict] = None,
) -> List[dict]:
    """
    合并两路结果并重排序。
    向量得分 × 0.7 + 关键词得分 × 0.3 + 关键词表权重加成。

    Args:
        vector_hits: Milvus 命中
        keyword_hits: MySQL 关键词命中
        vector_weight: 向量权重（默认 0.7）
        keywords_boost: 关键词→权重映射，例：{'操作系统': 10}
    """
    keywords_boost = keywords_boost or {}
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

    # 关键词权重加成
    if keywords_boost:
        for hit in merged.values():
            content = (hit.get('content') or '').lower()
            extra = 0
            for word, weight in keywords_boost.items():
                if word.lower() in content:
                    extra += weight / 50.0
            hit['score'] += extra

    return sorted(merged.values(), key=lambda x: x['score'], reverse=True)


# ------------------------------------------------------------------
# 硬过滤（SQL WHERE 缩范围）
# ------------------------------------------------------------------
def _apply_hard_filters(
    filters: dict,
    user_context: dict,
    top_k: int = 500,
) -> List[int]:
    """
    对 resource 表执行 SQL 硬过滤，返回候选 doc_id 列表。

    Args:
        filters: parse() 输出的过滤条件
        user_context: {'role': 'user', 'college_id': 1, 'user_id': 123}
        top_k: 最多返回多少候选（默认 500）

    Returns:
        doc_id 列表
    """
    from apps.resources.models import Resource
    from apps.knowledge.models import KnowledgeDoc
    from django.db.models import Q

    # --- Resource 表硬过滤 ---
    qs = Resource.objects.filter(status='published')

    # 1. 可见层级权限
    role = user_context.get('role', 'user')
    user_id = user_context.get('user_id')
    if role == 'super_admin':
        visibility_q = Q()
    elif role == 'admin':
        visibility_q = Q(visibility__in=['public', 'internal'])
    else:
        visibility_q = Q(visibility__in=['public', 'internal'])
        visibility_q |= Q(visibility='restricted', uploader_id=user_id)
    qs = qs.filter(visibility_q)

    # 2. 适用对象
    audience = filters.get('target_audience')
    if audience and audience != 'all':
        qs = qs.filter(Q(target_audience=audience) | Q(target_audience='all'))

    # 3. 学科 / 学院 / 分类 / 格式 / 学期
    if (cid := filters.get('college_id')):
        qs = qs.filter(college_id=cid)
    if (did := filters.get('discipline_id')):
        qs = qs.filter(discipline_id=did)
    if (cat_ids := filters.get('category_ids')):
        qs = qs.filter(category_id__in=cat_ids)
    if (ft := filters.get('file_type')):
        qs = qs.filter(file_type=ft)
    if (term := filters.get('course_term')):
        qs = qs.filter(course_term__icontains=term)

    # 4. 时间范围
    if (tr := filters.get('time_range')):
        import re as _re
        if _re.match(r'^\d{4}$', tr):
            year = int(tr)
            qs = qs.filter(published_at__year=year)
        elif _re.match(r'^\d{4}-\d{2}$', tr):
            year, month = tr.split('-')
            qs = qs.filter(
                published_at__year=int(year),
                published_at__month=int(month),
            )

    resource_ids = list(qs.values_list('id', flat=True)[:top_k])

    # --- KnowledgeDoc 表硬过滤（AI 只能检索公开文件）---
    kd_qs = KnowledgeDoc.objects.filter(status='indexed')

    # AI 检索时强制只看公开文件；后台管理查询时按角色过滤
    is_admin_query = role in ('admin', 'super_admin')
    if not is_admin_query:
        kd_qs = kd_qs.filter(visibility='public')
    elif role == 'admin':
        user_college_id = user_context.get('college_id')
        kd_qs = kd_qs.filter(
            Q(visibility='public')
            | Q(visibility='private', college_id=user_college_id)
            | Q(visibility='private', uploader_id=user_id),
        )
    # super_admin: 不过滤

    # 应用相同的学科/学院/分类筛选
    if (cid := filters.get('college_id')):
        kd_qs = kd_qs.filter(college_id=cid)
    if (did := filters.get('discipline_id')):
        kd_qs = kd_qs.filter(discipline_id=did)
    if (cat_ids := filters.get('category_ids')):
        kd_qs = kd_qs.filter(category_id__in=cat_ids)
    if (ft := filters.get('file_type')):
        kd_qs = kd_qs.filter(file_type=ft)

    knowledge_doc_ids = list(kd_qs.values_list('id', flat=True)[:top_k])

    # 合并两表 ID（Resource 用正数，KnowledgeDoc 用负数区分来源）
    return resource_ids + [-kid for kid in knowledge_doc_ids]


# ------------------------------------------------------------------
# 权限校验（过滤 Top-K 后再过滤 visibility）
# ------------------------------------------------------------------
def _filter_by_permission(
    hits: List[dict],
    user_context: dict,
) -> List[dict]:
    """
    在 Top-K 命中里剥离用户不可见的 doc。

    Args:
        hits: retrieve() 输出的命中列表
        user_context: 用户上下文

    Returns:
        过滤后的 hits
    """
    if not hits:
        return []

    from apps.resources.models import Resource
    from apps.knowledge.models import KnowledgeDoc

    resource_ids = [h['doc_id'] for h in hits if h.get('doc_id', 0) > 0]
    knowledge_ids = [abs(h['doc_id']) for h in hits if h.get('doc_id', 0) < 0]

    role = user_context.get('role', 'user')
    user_id = user_context.get('user_id')
    user_college_id = user_context.get('college_id')

    # 构建权限缓存
    allowed_ids = set()

    # Resource 权限
    if resource_ids:
        res_docs = Resource.objects.filter(id__in=resource_ids).values('id', 'visibility', 'uploader_id')
        for d in res_docs:
            if d['visibility'] in ('public', 'internal'):
                allowed_ids.add(d['id'])
            elif d['visibility'] == 'restricted' and (role in ('super_admin', 'admin') or d['uploader_id'] == user_id):
                allowed_ids.add(d['id'])

    # KnowledgeDoc 权限（已在 _apply_hard_filters 中过滤，此处二次兜底）
    if knowledge_ids:
        from django.db.models import Q
        kd_qs = KnowledgeDoc.objects.filter(id__in=knowledge_ids)
        if role == 'super_admin':
            allowed_ids.update(-kid for kid in kd_qs.values_list('id', flat=True))
        elif role == 'admin':
            kd_qs = kd_qs.filter(
                Q(visibility='public')
                | Q(visibility='private', college_id=user_college_id)
                | Q(visibility='private', uploader_id=user_id),
            )
            allowed_ids.update(-kid for kid in kd_qs.values_list('id', flat=True))
        else:
            kd_qs = kd_qs.filter(
                Q(visibility='public')
                | Q(visibility='private', uploader_id=user_id),
            )
            allowed_ids.update(-kid for kid in kd_qs.values_list('id', flat=True))

    filtered = [h for h in hits if h.get('doc_id') in allowed_ids]
    return filtered[:5]


# ------------------------------------------------------------------
# 加载 doc 元信息
# ------------------------------------------------------------------
def _enrich_with_doc_meta(hits: List[dict], min_score: float) -> List[dict]:
    """
    查询 MySQL resource / knowledge_doc 表，补全文档标题等元信息。
    过滤低于 min_score 的结果，返回 Top-5。
    KnowledgeDoc 的 doc_id 为负数，解码时取绝对值。
    """
    resource_ids = list({h['doc_id'] for h in hits if h.get('doc_id') and h['doc_id'] > 0})
    knowledge_ids = list({abs(h['doc_id']) for h in hits if h.get('doc_id') and h['doc_id'] < 0})

    doc_title_map = {}

    # Resource 表
    if resource_ids:
        try:
            from apps.resources.models import Resource
            for d in Resource.objects.filter(id__in=resource_ids).values('id', 'title'):
                doc_title_map[d['id']] = d['title']
        except ImportError:
            logger.debug('Resource 模型尚未就绪，使用占位标题')

    # KnowledgeDoc 表
    if knowledge_ids:
        try:
            from apps.knowledge.models import KnowledgeDoc
            for d in KnowledgeDoc.objects.filter(id__in=knowledge_ids).values('id', 'title'):
                doc_title_map[-d['id']] = d['title']  # 负数 key 匹配
        except ImportError:
            logger.debug('KnowledgeDoc 模型尚未就绪，使用占位标题')

    results = []
    for hit in hits:
        if hit['score'] < min_score:
            continue
        if not hit.get('content'):
            continue
        results.append({
            'doc_id': abs(hit['doc_id']),
            'doc_title': doc_title_map.get(hit['doc_id'], '未知文档'),
            'content': hit['content'],
            'score': round(hit['score'], 4),
            'page': hit.get('page', 0),
        })
        if len(results) >= 5:
            break

    return results


# ------------------------------------------------------------------
# 检索主入口（升级版）
# ------------------------------------------------------------------
def retrieve(
    question: str,
    user_context: Optional[dict] = None,
    parsed_filters: Optional[dict] = None,
    top_k: int = RAG_TOP_K,
    min_score: float = RAG_SCORE_THRESHOLD,
) -> List[dict]:
    """
    检索管道（三层检索升级版）：硬过滤 → 双路召回 → 关键词权重 → 权限校验。

    Args:
        question: 用户问题
        user_context: 用户上下文 {'user_id': int, 'role': str, 'college_id': int}
        parsed_filters: parse() 输出的过滤条件
        top_k: Milvus 召回数量
        min_score: 最低分阈值（低于此分数不返回）

    Returns:
        [{'doc_id': int, 'doc_title': str, 'content': str, 'score': float, 'page': int}, ...]
    """
    user_context = user_context or {'role': 'user'}
    parsed_filters = parsed_filters or {}

    # 1. SQL 硬过滤 → 候选 ID 列表
    candidate_ids = _apply_hard_filters(parsed_filters, user_context, top_k=500)

    if not candidate_ids:
        logger.info(f'硬过滤后无候选: question={question[:50]}')
        return []

    logger.debug(f'硬过滤候选数: {len(candidate_ids)}, filters={parsed_filters}')

    # 2. 双路召回（仅在候选范围内检索）
    question_embedding = _cached_embed(question)
    vector_hits = []
    if question_embedding:
        vector_hits = milvus_client.search(
            question_embedding, top_k=top_k, filter_ids=candidate_ids,
        )

    keyword_hits = _keyword_search(
        question, top_k=top_k, doc_ids=candidate_ids,
    )

    # 3. 合并 + 关键词权重加成
    merged = _merge_and_rerank(
        vector_hits,
        keyword_hits,
        keywords_boost=_keywords_boost_from_filters(parsed_filters),
    )

    # 4. 加载 doc 元信息
    enriched = _enrich_with_doc_meta(merged, min_score)

    # 5. 权限校验
    return _filter_by_permission(enriched, user_context)
