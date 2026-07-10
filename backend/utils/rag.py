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
# 历史意图检测
# ------------------------------------------------------------------
def _detect_history_intent(question: str) -> bool:
    """检测用户是否有查看历史数据的意图（关键词匹配）"""
    history_keywords = ['历年', '所有', '历史', '全部', '以往', '过去', '各年']
    return any(keyword in question for keyword in history_keywords)


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
# 内建知识库：东软官网核心信息（手工维护，代替爬虫）
# 增删改后重启即生效，无需爬虫、无需向量库
# ------------------------------------------------------------------
NEUSOFT_KNOWLEDGE = """## 学校概况
- 学校名称：成都东软学院（Chengdu Neusoft University）
- 官网：https://www.nsu.edu.cn（原 www.neusoft.edu.cn）
- 成立时间：2003年
- 办学层次：全日制普通本科院校
- 所在地：四川省成都市都江堰市青城山镇东软大道1号
- 在校生：约2万余人
- 校训：精勤博学、学以致用

## 院系设置（13个二级学院）
1. 计算机与软件学院（cs.nsu.edu.cn）— 软件工程、计算机科学与技术、网络工程等
2. 智能科学与工程学院（ise.nsu.edu.cn）— 数据科学、人工智能、机器人工程等
3. 信息与商务管理学院（sibm.nsu.edu.cn）— 信息管理、电子商务、财务管理等
4. 数字艺术与设计学院（da.nsu.edu.cn）— 数字媒体、动画、虚拟现实等
5. 外国语学院（english.nsu.edu.cn）— 英语、商务英语、日语
6. 健康医疗科技学院（jk.nsu.edu.cn）— 智能医学工程、医学影像技术等

## 校园服务
- 图书馆（lib.nsu.edu.cn）：周一至周日8:00-22:00
- 校园网：全校Wi-Fi覆盖
- 教务系统：https://bkshpg.nsu.edu.cn

## 学期安排
- 开学：每年9月初
- 期末：1月 / 6-7月
- 毕业典礼：每年6月中下旬
- 暑假：7月中-8月底
- 寒假：1月中-2月底"""

SYSTEM_PROMPT = """你是成都东软学院的AI智能助手，负责回答学院教学、科研、管理相关问题以及常识性问题。

## 学校内建知识库（可直接使用）
{knowlege}

## 外部检索资料（补充信息）
---
{references}
---

指令：
1. 学院概况、院系专业、校园服务等问题，优先用内建知识库回答
2. 外部资料有更具体信息时可补充
3. 列出出处（内建知识库 / 参考文档名）
4. 都不足以回答时，请如实告知
5. 常识性问题可直接用自己的知识回答
6. 回答使用中文，结构清晰"""


# ------------------------------------------------------------------
# RAG 控制参数（可通过 settings 覆盖）
# ------------------------------------------------------------------
RAG_TOP_K = 10
RAG_SCORE_THRESHOLD = 0.35      # 低于此分数不放入 LLM 上下文
RAG_REFERENCE_THRESHOLD = 0.7   # 低于此分数不作为引用展示
CONTEXT_MAX_CHARS = 8000


# ------------------------------------------------------------------
# Prompt 构造
# ------------------------------------------------------------------
def build_rag_prompt(
    question: str,
    chunks: List[dict],
    history: Optional[List[dict]] = None,
) -> List[dict]:
    """
    构造 RAG Prompt（支持多轮对话历史）。

    Args:
        question: 用户问题
        chunks: 检索结果 [{'content': str, 'doc_title': str, 'page': int, 'score': float}, ...]
        history: 历史对话 [{'role': 'user'|'assistant', 'content': str}, ...]（可选）

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
    system_content = SYSTEM_PROMPT.format(knowlege=NEUSOFT_KNOWLEDGE, references=refs_text)

    messages = [{'role': 'system', 'content': system_content}]

    # 插入历史对话（多轮上下文）
    if history:
        # 截取最近 N 轮（防止超长），保留最后 6 条消息
        recent_history = history[-6:]
        for msg in recent_history:
            if msg['role'] in ('user', 'assistant'):
                messages.append({
                    'role': msg['role'],
                    'content': msg['content'][:2000],  # 每条历史截断
                })

    # 当前问题
    messages.append({'role': 'user', 'content': question})
    return messages


# ------------------------------------------------------------------
# 相关推荐
# ------------------------------------------------------------------
_SUGGEST_PROMPT = """你是一个智能问答系统的推荐助手。
根据用户的提问和 AI 的回答，生成 3 个"用户可能还想问"的相关问题。

要求：
- 每个问题简短、具体、覆盖不同角度
- 从回答中挖掘可延伸的方向
- 用 JSON 数组格式输出，不要额外文字

示例输出：["什么是进程调度？", "操作系统的分类有哪些？", "如何查看 Linux 进程？"]

用户问题：{question}
AI回答：{answer}
"""


def generate_suggested_questions(
    question: str,
    answer: str,
    max_questions: int = 3,
) -> List[str]:
    """根据问答内容生成相关推荐问题。

    Args:
        question: 用户原始问题
        answer: AI 完整回答
        max_questions: 推荐问题数量（默认 3）

    Returns:
        推荐问题列表，生成失败返回空列表
    """
    if not answer or not answer.strip():
        return []

    import json
    import re as _re

    prompt = _SUGGEST_PROMPT.format(
        question=question[:300],
        answer=answer[:1000],
    )
    messages = [{'role': 'user', 'content': prompt}]

    result = llm_client.chat(messages, temperature=0.7, max_tokens=300)
    if not result:
        return []

    # 解析 JSON 数组
    try:
        return json.loads(result.strip())[:max_questions]
    except (json.JSONDecodeError, TypeError):
        pass

    # 尝试从 markdown 代码块中提取
    match = _re.search(r'```(?:json)?\s*(\[.*?\])\s*```', result, _re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))[:max_questions]
        except (json.JSONDecodeError, TypeError):
            pass

    return []


# ------------------------------------------------------------------
# 关键词搜索（双表支持）
# ------------------------------------------------------------------
def _keyword_search(
    question: str,
    top_k: int = 10,
    doc_ids: Optional[List[int]] = None,
) -> List[dict]:
    """
    MySQL 关键词匹配搜索（支持 Resource 和 KnowledgeDoc 双表）。

    - Resource 命中的 doc_id 为正数，KnowledgeDoc 命中的 doc_id 为负数。
    - 关键词路命中时返回 content 字段（title + description 拼接），
      解决旧版 content='' 被 _enrich_with_doc_meta 过滤掉的问题。

    Args:
        question: 用户问题
        top_k: 返回数量
        doc_ids: 限定检索的 doc_id 列表（来自硬过滤，正负混排）
    """
    try:
        import jieba
        keywords = list(jieba.cut(question))[:10]
    except ImportError:
        keywords = question.split()[:10]

    if not keywords:
        return []

    from django.db.models import Q
    from apps.resources.models import Resource

    # 拆分 doc_ids（正数=Resource，负数=KnowledgeDoc 的真实 ID）
    resource_candidate_ids = None
    kdoc_candidate_ids = None
    if doc_ids is not None:
        resource_candidate_ids = [d for d in doc_ids if d > 0]
        kdoc_candidate_ids = [abs(d) for d in doc_ids if d < 0]

    # ---------- Resource 表关键词匹配 ----------
    resource_results = []
    if resource_candidate_ids is not None and not resource_candidate_ids:
        # 硬过滤后无 Resource 候选，直接跳过 Resource 表
        pass
    else:
        kw_query = Q()
        for kw in keywords:
            if len(kw) >= 2:
                kw_query |= Q(title__icontains=kw)
                kw_query |= Q(description__icontains=kw)
                kw_query |= Q(course_term__icontains=kw)

        resource_qs = Resource.objects.filter(
            kw_query, status='published'
        )
        if resource_candidate_ids is not None:
            resource_qs = resource_qs.filter(id__in=resource_candidate_ids)

        # 取 title + description 前 300 字作为 content
        for d in resource_qs.values('id', 'title', 'description')[:top_k]:
            excerpt = (d['description'] or '')[:300]
            resource_results.append({
                'doc_id': d['id'],
                'doc_title': d['title'],
                'content': f"{d['title']} {excerpt}".strip(),
                'score': 0.55,
                'page': 0,
            })

    # ---------- KnowledgeDoc 表关键词匹配 ----------
    kdoc_results = []
    if kdoc_candidate_ids is not None and not kdoc_candidate_ids:
        pass
    else:
        try:
            from apps.knowledge.models import KnowledgeDoc

            kw_query = Q()
            for kw in keywords:
                if len(kw) >= 2:
                    kw_query |= Q(title__icontains=kw)
                    kw_query |= Q(description__icontains=kw)

            kdoc_qs = KnowledgeDoc.objects.filter(
                kw_query, status='indexed'
            )
            if kdoc_candidate_ids is not None:
                kdoc_qs = kdoc_qs.filter(id__in=kdoc_candidate_ids)

            for d in kdoc_qs.values('id', 'title', 'description')[:top_k]:
                excerpt = (d['description'] or '')[:300]
                kdoc_results.append({
                    'doc_id': -d['id'],           # 负数区分来源
                    'doc_title': d['title'],
                    'content': f"{d['title']} {excerpt}".strip(),
                    'score': 0.55,
                    'page': 0,
                })
        except ImportError:
            logger.debug('KnowledgeDoc 模型尚未就绪，跳过知识库关键词搜索')

    # 合并（Resource 在前，KnowledgeDoc 在后，截取 top_k）
    combined = resource_results + kdoc_results
    return combined[:top_k]


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
    from datetime import timedelta
    from django.utils import timezone

    # --- 默认时间范围：近一年（用户明确要求历史时跳过）---
    time_range = filters.get('time_range')
    need_history = filters.get('need_history', False)
    one_year_ago = None
    if not time_range and not need_history:
        one_year_ago = timezone.now() - timedelta(days=365)

    # --- Resource 表硬过滤 ---
    qs = Resource.objects.filter(status='published')

    # 默认一年时间过滤
    if one_year_ago:
        qs = qs.filter(published_at__gte=one_year_ago)

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

    # 默认一年时间过滤
    if one_year_ago:
        kd_qs = kd_qs.filter(created_at__gte=one_year_ago)

    # AI 检索权限：private 绝不检索；public 按 college_id/department_id 限缩
    kd_qs = kd_qs.filter(visibility='public')
    # 校级文档：college_id 和 department_id 均为 null
    SCHOOL_LEVEL = Q(college_id__isnull=True, department_id__isnull=True)
    is_admin_query = role in ('admin', 'college_admin', 'dept_admin', 'super_admin')
    if not is_admin_query:
        # 普通用户 AI 检索：校级全部 + 本院 + 本部门 + 自己上传
        conditions = SCHOOL_LEVEL
        if user_college_id := user_context.get('college_id'):
            conditions |= Q(college_id=user_college_id)
        if user_dept_id := user_context.get('department_id'):
            conditions |= Q(department_id=user_dept_id)
        if user_id:
            conditions |= Q(uploader_id=user_id)
        kd_qs = kd_qs.filter(conditions)
    elif role in ('college_admin', 'admin'):
        kd_qs = kd_qs.filter(
            SCHOOL_LEVEL
            | Q(college_id=user_context.get('college_id'))
            | Q(uploader_id=user_id),
        )
    elif role == 'dept_admin':
        kd_qs = kd_qs.filter(
            SCHOOL_LEVEL
            | Q(department_id=user_context.get('department_id'))
            | Q(uploader_id=user_id),
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
    """
    if not hits:
        return []

    from apps.resources.models import Resource
    from apps.knowledge.models import KnowledgeDoc
    from django.db.models import Q

    role = user_context.get('role', 'user')
    user_id = user_context.get('user_id')
    user_college_id = user_context.get('college_id')

    doc_ids = list(set(h['doc_id'] for h in hits))
    allowed_ids = set()

    # Resource 权限：public/internal 可见
    for d in Resource.objects.filter(id__in=doc_ids, visibility__in=['public', 'internal']).values('id'):
        allowed_ids.add(d['id'])

    # KnowledgeDoc 权限
    remaining = [i for i in doc_ids if i not in allowed_ids]
    if remaining:
        SCHOOL_LEVEL = Q(college_id__isnull=True, department_id__isnull=True)
        kd_qs = KnowledgeDoc.objects.filter(id__in=remaining, visibility='public')
        if role == 'super_admin':
            allowed_ids.update(kd_qs.values_list('id', flat=True))
        elif role in ('college_admin', 'admin'):
            allowed_ids.update(kd_qs.filter(
                SCHOOL_LEVEL | Q(college_id=user_college_id) | Q(uploader_id=user_id),
            ).values_list('id', flat=True))
        elif role == 'dept_admin':
            allowed_ids.update(kd_qs.filter(
                SCHOOL_LEVEL | Q(department_id=user_context.get('department_id')) | Q(uploader_id=user_id),
            ).values_list('id', flat=True))
        else:
            conditions = SCHOOL_LEVEL
            if user_college_id:
                conditions |= Q(college_id=user_college_id)
            if user_context.get('department_id'):
                conditions |= Q(department_id=user_context.get('department_id'))
            if user_id:
                conditions |= Q(uploader_id=user_id)
            allowed_ids.update(kd_qs.filter(conditions).values_list('id', flat=True))

    filtered = [h for h in hits if h.get('doc_id') in allowed_ids]
    return filtered[:5]


# ------------------------------------------------------------------
# 加载 doc 元信息


# ------------------------------------------------------------------
# 加载 doc 元信息
# ------------------------------------------------------------------
def _enrich_with_doc_meta(hits: List[dict], min_score: float) -> List[dict]:
    """
    查询 MySQL resource / knowledge_doc 表，补全文档标题等元信息。
    过滤低于 min_score 的结果，返回 Top-5。
    """
    doc_ids = list({int(h['doc_id']) for h in hits if h.get('doc_id')})

    doc_title_map = {}

    if doc_ids:
        # 查 Resource 表
        try:
            from apps.resources.models import Resource
            for d in Resource.objects.filter(id__in=doc_ids).values('id', 'title'):
                doc_title_map[d['id']] = d['title']
        except ImportError:
            pass
        # 查 KnowledgeDoc 表
        try:
            from apps.knowledge.models import KnowledgeDoc
            for d in KnowledgeDoc.objects.filter(id__in=doc_ids).values('id', 'title'):
                doc_title_map[d['id']] = d['title']
        except ImportError:
            pass

    results = []
    for hit in hits:
        if hit['score'] < min_score:
            continue
        if not hit.get('content'):
            continue
        did = int(hit['doc_id'])
        results.append({
            'doc_id': did,
            'doc_title': doc_title_map.get(did, '未知文档'),
            'content': hit['content'],
            'score': round(hit['score'], 4),
            'page': hit.get('page', 0),
        })
        if len(results) >= 5:
            break

    return results


# ------------------------------------------------------------------
# 兜底：按标题/描述关键词搜索（向量检索无结果时降级）
# ------------------------------------------------------------------
def _fallback_title_search(
    question: str,
    user_context: dict,
    top_k: int = 5,
) -> List[dict]:
    """当向量检索无结果时，从 KnowledgeDoc 标题/描述中按关键词匹配。

    Returns:
        [{'doc_id': int, 'doc_title': str, 'content': str, 'score': float, 'page': int}, ...]
    """
    import re
    from django.db.models import Q
    from apps.knowledge.models import KnowledgeDoc, DocChunk

    # 提取关键词：去疑问词，取有意义的词
    stop_words = {"什么", "怎么", "如何", "为什么", "吗", "呢", "吧", "嘛",
                  "哪个", "哪些", "谁", "何时", "哪里", "是否", "有", "没有",
                  "的", "了", "我", "你", "他", "她", "它", "们", "这个", "那个"}
    words = re.findall(r'[一-鿿\w]+', question)
    keywords = []
    for w in words:
        w = w.lower()
        if w in stop_words or len(w) < 2:
            continue
        # 拆解混合词：如 "MySQL第二版" → "MySQL" + "第二版"
        parts = re.findall(r'[a-z]+|[一-鿿]+', w)
        for p in parts:
            if len(p) >= 2 and p not in stop_words and p not in keywords:
                keywords.append(p)
    keywords = keywords[:5]
    if not keywords:
        return []

    # 按关键词匹配标题/描述
    kw_query = Q()
    for kw in keywords:
        kw_query |= Q(title__icontains=kw)
        kw_query |= Q(description__icontains=kw)

    # 权限过滤（同 retrieve 逻辑：超管全量，管理员本学院，用户自己的）
    role = user_context.get('role', 'user')
    college_id = user_context.get('college_id')
    user_id = user_context.get('user_id')

    if role == 'super_admin':
        docs = KnowledgeDoc.objects.filter(kw_query)
    elif role == 'admin' and college_id:
        docs = KnowledgeDoc.objects.filter(
            kw_query, Q(uploader_id=user_id) | Q(college_id=college_id)
        )
    elif user_id:
        docs = KnowledgeDoc.objects.filter(kw_query, uploader_id=user_id)
    else:
        return []

    docs = docs.filter(status='indexed').order_by('-created_at')[:top_k]

    # 取每篇文档的第一个 chunk 作为内容预览
    results = []
    for doc in docs:
        first_chunk = DocChunk.objects.filter(doc=doc).order_by('chunk_index').first()
        content = first_chunk.content[:500] if first_chunk else (doc.description or doc.title)
        results.append({
            'doc_id': doc.id,
            'doc_title': doc.title,
            'content': content,
            'score': 0.5,  # 固定中等分，低于向量检索的分数
            'page': 0,
        })
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

    # 检测用户是否需要历史数据（含"历年/历史/全部"等关键词则不限时间）
    parsed_filters['need_history'] = _detect_history_intent(question)

    # 1. SQL 硬过滤 → 候选 ID 列表
    candidate_ids = _apply_hard_filters(parsed_filters, user_context, top_k=500)

    if not candidate_ids:
        logger.info(f'硬过滤后无候选，尝试标题降级搜索: question={question[:50]}')
        title_matches = _fallback_title_search(question, user_context, top_k=5)
        if title_matches:
            logger.info(f'标题降级搜索命中 {len(title_matches)} 条')
            return title_matches
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
    results = _filter_by_permission(enriched, user_context)

    # 6. 兜底：向量检索无结果时，按标题/描述关键词搜索
    if not results:
        title_matches = _fallback_title_search(question, user_context, top_k=5)
        if title_matches:
            logger.info(f'向量检索无结果，标题降级搜索命中 {len(title_matches)} 条')
            return title_matches

    return results
