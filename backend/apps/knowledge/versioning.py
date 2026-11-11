"""文档新旧版本识别与问答隔离。

流程：
1. 索引完成 → detect_version_task 调用 detect_versions(doc_id)
2. 自动比对同范围内已索引文档，命中"疑似新旧版本"时只写 DocVersionCandidate(pending)
3. 上传者确认后 confirm_candidate() 才把旧版标记为 superseded（问答检索随之中止召回旧版）
4. 删除文档时由 post_delete 信号把版本链恢复

设计原则：识别阶段绝不直接修改旧版文档，宁可漏判也不误判。
"""
import difflib
import json
import logging
import re

from django.db import transaction
from django.db.models import Q
from django.db.models.signals import post_delete, pre_delete

logger = logging.getLogger(__name__)

# 采样参数：首2 + 中1 + 尾2 块，最多 2500 字
SAMPLE_CHUNK_COUNT = 200
SAMPLE_MAX_CHARS = 2500
MIN_CONTENT_CHARS = 300
CANDIDATE_POOL_LIMIT = 200
CANDIDATE_PRELOAD_LIMIT = 12
SHINGLE_SIZE = 3

# 判定阈值
JACCARD_DIRECT = 0.85
JACCARD_EXPLICIT = 0.6
JACCARD_LLM = 0.65
TITLE_SIM_LLM = 0.55
TITLE_SIM_EXPLICIT = 0.5
TITLE_SIM_MIN = 0.45

# 显式版本词：只有这些词出现才允许"不调 LLM 直接判版"
_EXPLICIT_VERSION_RE = re.compile(
    r'(?:v|ver|version)\s*\d+(?:\.\d+)*'
    r'|第\s*[0-9一二三四五六七八九十百]+\s*版'
    r'|修订版|修订稿|终版|最终版|定稿|新版|旧版|历史版|副本'
    r'|\(\s*\d+\s*\)|（\s*\d+\s*）',
    re.IGNORECASE,
)
# 基名归一化时额外剥离的年份/日期噪声（不算显式版本词）
_DATE_NOISE_RE = re.compile(
    r'\d{4}\s*[-_.年]\s*\d{1,2}\s*[-_.月]\s*\d{1,2}\s*[日号]?'
    r'|\d{4}\s*年?\s*(?:版|年度|修订)?'
    r'|\d{1,2}\s*月\s*\d{1,2}\s*[日号]?',
    re.IGNORECASE,
)


# ------------------------------------------------------------------
# 文本归一化与相似度
# ------------------------------------------------------------------
def normalize_text(value: str) -> str:
    """小写、去空白与标点，仅保留文字/数字，用于相似度计算。"""
    if not value:
        return ''
    return re.sub(r'[\s\W_]+', '', str(value)).lower()


def split_version_marker(value: str) -> tuple[str, bool]:
    """把文件名/标题拆成 (基名, 是否含显式版本词)。

    例：'XX管理办法(2024修订版).docx' → ('xx管理办法', True)
        '2015级优秀毕业生名单.xlsx' → ('级优秀毕业生名单', False)
    """
    raw = str(value or '')
    # 去扩展名
    raw = re.sub(r'\.[a-zA-Z0-9]{1,6}$', '', raw.strip())
    base, marker = raw, False
    if _EXPLICIT_VERSION_RE.search(raw):
        marker = True
        base = _EXPLICIT_VERSION_RE.sub('', base)
    base = _DATE_NOISE_RE.sub('', base)
    return normalize_text(base), marker


def title_similarity(a: str, b: str) -> float:
    """标题相似度（0-1），基于归一化后的 SequenceMatcher。"""
    left, right = normalize_text(a), normalize_text(b)
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def char_shingles(text: str, n: int = SHINGLE_SIZE) -> set:
    """字符 n-gram 集合（先做归一化）。"""
    normalized = normalize_text(text)
    if not normalized:
        return set()
    if len(normalized) <= n:
        return {normalized}
    return {normalized[i:i + n] for i in range(len(normalized) - n + 1)}


def jaccard(a: str, b: str) -> float:
    """两段文本的字符 n-gram Jaccard 相似度（0-1）。"""
    left, right = char_shingles(a), char_shingles(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def sample_text(chunks: list, table_schema: dict | None = None,
                max_chars: int = SAMPLE_MAX_CHARS) -> str:
    """从分块文本中分散采样：首2 + 中1 + 尾2，最多 max_chars 字。

    表格类文档额外拼入列名，避免只采到表头/表尾而漏掉结构信息。
    """
    contents = [c.strip() for c in (chunks or []) if c and c.strip()]
    if not contents:
        return ''
    if len(contents) >= 5:
        indexes = sorted({0, 1, len(contents) // 2, len(contents) - 2, len(contents) - 1})
    else:
        indexes = list(range(len(contents)))
    text = ''.join(contents[i] for i in indexes)
    if table_schema:
        columns = []
        for sheet in (table_schema.get('sheets') or []):
            columns.extend(str(c) for c in (sheet.get('columns') or []))
        if columns:
            text = ''.join(columns) + text
    return text[:max_chars]


def _load_sample(doc, max_chars: int = SAMPLE_MAX_CHARS) -> str:
    """读取文档分块并采样；表格文档附带列名。"""
    chunks = list(
        doc.chunks.order_by('chunk_index').values_list('content', flat=True)[:SAMPLE_CHUNK_COUNT]
    )
    return sample_text(chunks, doc.table_schema, max_chars=max_chars)


# ------------------------------------------------------------------
# 候选发现
# ------------------------------------------------------------------
def _scope_queryset(doc):
    """同归属（学院+部门完全一致）或同上传者的已索引文档。"""
    from .models import KnowledgeDoc

    qs = KnowledgeDoc.objects.filter(
        status='indexed',
        review_status='approved',
        superseded_by__isnull=True,
    ).exclude(id=doc.id)
    scope_q = Q(college_id=doc.college_id, department_id=doc.department_id)
    if doc.uploader_id:
        scope_q |= Q(uploader_id=doc.uploader_id)
    return qs.filter(scope_q)


def find_candidates(doc) -> list:
    """返回按标题相似度排序的候选文档（最多 CANDIDATE_PRELOAD_LIMIT 篇）。"""
    pool = list(
        _scope_queryset(doc).order_by('-created_at')[:CANDIDATE_POOL_LIMIT]
    )
    scored = []
    for cand in pool:
        sim = title_similarity(doc.title, cand.title)
        base_new, _ = split_version_marker(doc.file_name)
        base_old, _ = split_version_marker(cand.file_name)
        same_base = bool(base_new) and base_new == base_old
        if sim >= TITLE_SIM_MIN or same_base:
            scored.append((same_base, sim, cand))
    # 基名相同优先，其次标题相似度
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in scored[:CANDIDATE_PRELOAD_LIMIT]]


def evaluate_pair(doc, cand, new_sample: str | None = None) -> dict:
    """计算一对文档的相似度信号（纯计算，不落库）。"""
    new_sample = new_sample if new_sample is not None else _load_sample(doc)
    cand_sample = _load_sample(cand)
    base_new, marker_new = split_version_marker(doc.file_name)
    base_old, marker_old = split_version_marker(cand.file_name)
    left_name = normalize_text(re.sub(r'\.[a-zA-Z0-9]{1,6}$', '', doc.file_name or ''))
    right_name = normalize_text(re.sub(r'\.[a-zA-Z0-9]{1,6}$', '', cand.file_name or ''))
    file_name_equal = bool(left_name) and left_name == right_name
    return {
        'file_name_equal': file_name_equal,
        'base_name_equal': bool(base_new) and base_new == base_old,
        'explicit_version_word': marker_new or marker_old,
        'title_similarity': round(title_similarity(doc.title, cand.title), 4),
        'jaccard': round(jaccard(new_sample, cand_sample), 4),
        'new_sample_chars': len(new_sample),
        'old_sample_chars': len(cand_sample),
    }


def should_check_by_llm(signals: dict) -> bool:
    """矩阵中需要 LLM 复核的分支。"""
    jac = signals['jaccard']
    sim = signals['title_similarity']
    if signals['file_name_equal'] and jac >= 0.5:
        return True
    return jac >= JACCARD_LLM and sim >= TITLE_SIM_LLM


def is_direct_match(signals: dict) -> bool:
    """高置信、无需 LLM 即可生成候选的分支。"""
    if signals['jaccard'] < JACCARD_EXPLICIT:
        return False
    if signals['explicit_version_word'] and (
        signals['base_name_equal'] or signals['title_similarity'] >= TITLE_SIM_EXPLICIT
    ):
        return True
    return signals['file_name_equal'] and signals['jaccard'] >= JACCARD_DIRECT


def _llm_verdict(doc, cand, signals: dict) -> dict | None:
    """LLM 复核是否同一份资料的新旧版本，返回 {'same_document','newer','confidence'}。"""
    prompt = (
        "你是校园知识库的版本判断助手。判断下面两份资料是否为「同一份材料的新旧版本」，"
        "例如同一份管理办法修订后重新上传。注意：不同年份/不同届别的名单、同一模板套用的不同班级表格，"
        "即使内容高度相似也不算版本。\n"
        "只返回 JSON（不要 markdown 标记），格式："
        '{"same_document": true/false, "newer": "new"/"old", "confidence": "high"/"low"}\n'
        '其中 newer="new" 表示第二份（文档B）更新，newer="old" 表示第一份（文档A）更新。\n\n'
        f"文档A：标题《{doc.title}》，文件名 {doc.file_name}，"
        f"上传时间 {doc.created_at:%Y-%m-%d}，内容日期 {doc.freshness_date or '未知'}\n"
        f"文档A片段：{_load_sample(doc, 800)}\n\n"
        f"文档B：标题《{cand.title}》，文件名 {cand.file_name}，"
        f"上传时间 {cand.created_at:%Y-%m-%d}，内容日期 {cand.freshness_date or '未知'}\n"
        f"文档B片段：{_load_sample(cand, 800)}\n\n"
        f"参考信号：文件名相同={signals['file_name_equal']}，"
        f"标题相似度={signals['title_similarity']:.2f}，正文相似度={signals['jaccard']:.2f}"
    )
    try:
        from utils.llm import llm_client
        raw = llm_client.chat(
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.0, max_tokens=200,
        )
    except Exception as e:
        logger.warning(f'版本识别 LLM 调用失败 doc={doc.id} cand={cand.id}: {e}')
        return None
    if not raw:
        return None
    try:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        parsed = json.loads(match.group(0)) if match else json.loads(raw)
    except (json.JSONDecodeError, AttributeError):
        logger.warning(f'版本识别 LLM 返回非 JSON raw={raw[:200]!r}')
        return None
    if not isinstance(parsed, dict):
        return None
    return {
        'same_document': bool(parsed.get('same_document')),
        'newer': 'old' if parsed.get('newer') == 'old' else 'new',
        'confidence': parsed.get('confidence') or 'low',
    }


def _pick_newer(doc, cand) -> tuple:
    """按 freshness_date（都有值时）否则按 created_at 判断新旧，返回 (new, old)。"""
    if doc.freshness_date and cand.freshness_date:
        newer_is_doc = doc.freshness_date > cand.freshness_date
    else:
        newer_is_doc = doc.created_at >= cand.created_at
    return (doc, cand) if newer_is_doc else (cand, doc)


def _audience_superset(new_doc, old_doc) -> bool:
    """新版受众必须是旧版超集，否则不允许替代（避免资料被"改小范围"后消失）。"""
    if new_doc.visibility == 'private' and old_doc.visibility == 'public':
        return False
    new_school_level = new_doc.college_id is None and new_doc.department_id is None
    if new_school_level:
        return True
    if (new_doc.college_id, new_doc.department_id) == (
        old_doc.college_id, old_doc.department_id
    ):
        return True
    return new_doc.uploader_id == old_doc.uploader_id


def _reason_text(signals: dict, llm: dict | None) -> str:
    parts = []
    if signals['file_name_equal']:
        parts.append('文件名相同')
    if signals['base_name_equal']:
        parts.append('文件名基名相同')
    if signals['explicit_version_word']:
        parts.append('含版本标识')
    parts.append(f"标题相似度 {signals['title_similarity']:.0%}")
    parts.append(f"正文相似度 {signals['jaccard']:.0%}")
    if llm:
        parts.append(f"AI 判定：{'同一份资料' if llm['same_document'] else '不同资料'}")
    return '，'.join(parts)[:512]


def create_candidate(new_doc, old_doc, confidence: float, signals: dict,
                     reason: str) -> object | None:
    """写入待确认候选；已存在同一对时跳过。"""
    from .models import DocVersionCandidate

    candidate, created = DocVersionCandidate.objects.get_or_create(
        new_doc=new_doc, old_doc=old_doc,
        defaults={
            'confidence': confidence,
            'signals': signals,
            'reason': reason,
        },
    )
    if created:
        logger.info(
            f'发现疑似版本 doc={new_doc.id} 替代 doc={old_doc.id} '
            f'confidence={confidence:.2f} reason={reason}'
        )
    return candidate if created else None


def detect_versions(doc_id: int) -> object | None:
    """识别 doc_id 的疑似新旧版本，命中时生成一条 pending 候选。

    Returns: 新建的 DocVersionCandidate 或 None
    """
    from .models import DocVersionCandidate, KnowledgeDoc

    try:
        doc = KnowledgeDoc.objects.get(id=doc_id)
    except KnowledgeDoc.DoesNotExist:
        logger.warning(f'detect_versions: doc_id={doc_id} 不存在')
        return None

    if doc.status != 'indexed' or doc.review_status != 'approved':
        return None
    if doc.superseded_by_id is not None:
        return None
    if doc.file_type in ('image', 'video', 'audio'):
        return None
    if DocVersionCandidate.objects.filter(new_doc=doc, status='pending').exists():
        return None

    new_sample = _load_sample(doc)
    if len(new_sample) < MIN_CONTENT_CHARS:
        logger.debug(f'doc_id={doc_id} 正文过短（{len(new_sample)} 字），跳过版本识别')
        return None

    best = None  # (score, cand, signals, llm)
    for cand in find_candidates(doc):
        signals = evaluate_pair(doc, cand, new_sample=new_sample)
        if not (is_direct_match(signals) or should_check_by_llm(signals)):
            continue
        llm = None
        if not is_direct_match(signals):
            llm = _llm_verdict(doc, cand, signals)
            if not llm or not llm['same_document'] or llm['confidence'] != 'high':
                continue
        score = signals['jaccard'] + signals['title_similarity'] * 0.3
        if best is None or score > best[0]:
            best = (score, cand, signals, llm)

    if best is None:
        return None

    _, cand, signals, llm = best
    if llm:
        # LLM 复核时以它给出的方向为准（newer="old" 指 doc 更新）
        new_doc, old_doc = (doc, cand) if llm.get('newer') == 'old' else (cand, doc)
    else:
        new_doc, old_doc = _pick_newer(doc, cand)
    if new_doc.id == old_doc.id:
        return None
    if not _audience_superset(new_doc, old_doc):
        logger.info(
            f'疑似版本但新版受众不覆盖旧版，跳过候选 '
            f'new={new_doc.id} old={old_doc.id}'
        )
        return None

    confidence = 0.95 if is_direct_match(signals) else 0.75
    reason = _reason_text(signals, llm)
    return create_candidate(
        new_doc, old_doc, confidence,
        {'signals': signals, 'llm': llm}, reason,
    )


# ------------------------------------------------------------------
# 确认 / 拒绝
# ------------------------------------------------------------------
def _ensure_decision_permission(candidate, user) -> None:
    from rest_framework.exceptions import PermissionDenied

    if getattr(user, 'role', None) == 'super_admin':
        return
    if candidate.new_doc.uploader_id == getattr(user, 'id', None):
        return
    raise PermissionDenied('只有上传者或超级管理员可以确认版本替代')


def confirm_candidate(candidate_id: int, user):
    """确认候选：旧版标记为 superseded，并建立 replaces 关联。"""
    from django.utils import timezone

    from .models import DocRelation, DocVersionCandidate, KnowledgeDoc

    try:
        candidate = DocVersionCandidate.objects.select_related(
            'new_doc', 'old_doc',
        ).get(id=candidate_id)
    except DocVersionCandidate.DoesNotExist:
        raise VersionCandidateNotFound('版本候选不存在')
    _ensure_decision_permission(candidate, user)
    if candidate.status != 'pending':
        return candidate

    ids = sorted({candidate.new_doc_id, candidate.old_doc_id})
    with transaction.atomic():
        docs = {
            d.id: d
            for d in KnowledgeDoc.objects.select_for_update().filter(id__in=ids)
        }
        new_doc = docs[candidate.new_doc_id]
        old_doc = docs[candidate.old_doc_id]
        old_doc.superseded_by = new_doc
        old_doc.superseded_at = timezone.now()
        old_doc.save(update_fields=['superseded_by', 'superseded_at', 'updated_at'])
        DocRelation.objects.get_or_create(
            source_doc=new_doc, target_doc=old_doc, relation_type='replaces',
            defaults={
                'created_by': user if getattr(user, 'is_authenticated', False) else None,
            },
        )
        candidate.status = 'confirmed'
        candidate.decided_by = user if getattr(user, 'is_authenticated', False) else None
        candidate.decided_at = timezone.now()
        candidate.save(update_fields=['status', 'decided_by', 'decided_at'])
    logger.info(f'版本候选已确认 id={candidate_id} old={old_doc.id} new={new_doc.id}')
    return candidate


def reject_candidate(candidate_id: int, user):
    """拒绝候选：保留两篇独立文档，仅记录决策。"""
    from django.utils import timezone

    from .models import DocVersionCandidate

    try:
        candidate = DocVersionCandidate.objects.select_related(
            'new_doc', 'old_doc',
        ).get(id=candidate_id)
    except DocVersionCandidate.DoesNotExist:
        raise VersionCandidateNotFound('版本候选不存在')
    _ensure_decision_permission(candidate, user)
    if candidate.status != 'pending':
        return candidate
    candidate.status = 'rejected'
    candidate.decided_by = user if getattr(user, 'is_authenticated', False) else None
    candidate.decided_at = timezone.now()
    candidate.save(update_fields=['status', 'decided_by', 'decided_at'])
    return candidate


class VersionCandidateNotFound(Exception):
    """候选不存在（由视图转换为 404）。"""


def latest_doc_of(doc):
    """沿 superseded_by 链找到当前最新版（带环路保护）。"""
    visited = set()
    current = doc
    while current.superseded_by_id and current.id not in visited:
        visited.add(current.id)
        current = current.superseded_by
    return current


def latest_doc_id_of(doc) -> int:
    return latest_doc_of(doc).id


# ------------------------------------------------------------------
# 删除恢复：删新版或缺中间版时，保证版本链不断
# ------------------------------------------------------------------
def _capture_superseded_children(sender, instance, **kwargs):
    """删除前记录"被本文档替代"的旧版 ID。

    Django 删除时会先把 SET_NULL 外键置空，post_delete 里已无法按
    superseded_by 反查，因此必须在 pre_delete 阶段把子文档 ID 快照下来。
    """
    from .models import KnowledgeDoc

    instance._superseded_children_ids = list(
        KnowledgeDoc.objects.filter(superseded_by_id=instance.id)
        .values_list('id', flat=True)
    )


def restore_chain_after_delete(sender, instance, **kwargs):
    """删除文档后把被它替代的文档重新指向它的后继。

    例：A ← B ← C（C 最新），删 B 后 A 应指向 C；删 C 后 B 恢复为最新。
    """
    from django.utils import timezone

    from .models import KnowledgeDoc

    children = getattr(instance, '_superseded_children_ids', None) or []
    if not children:
        return
    successor_id = instance.superseded_by_id
    KnowledgeDoc.objects.filter(id__in=children).update(
        superseded_by_id=successor_id,
        superseded_at=timezone.now() if successor_id else None,
    )


pre_delete.connect(
    _capture_superseded_children,
    sender='knowledge.KnowledgeDoc',
    dispatch_uid='knowledge_capture_version_children',
)
post_delete.connect(
    restore_chain_after_delete,
    sender='knowledge.KnowledgeDoc',
    dispatch_uid='knowledge_restore_version_chain',
)
