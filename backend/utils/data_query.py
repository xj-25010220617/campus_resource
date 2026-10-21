"""
文件管理数据查询 (Data Query)

执行文件管理相关的数据库查询，供意图路由分发后使用。
支持：文档数统计、按日期区间统计/列表、指定文件上传/索引时间、
最近上传、存储统计、分类统计、带条件查找。

时间字段约定：
- created_at：文件首次入库/上传到系统的时间；
- indexed_at：索引完成时间（可能为空，表示尚未完成）；
- freshness_date：内容截止日期，不是上传时间。
所有 *_display 字段均已转换为业务时区（Asia/Shanghai）。
"""
import logging
from typing import List, Optional

from django.db.models import Count, Sum, Q

from utils.timefmt import (
    format_business_time,
    parse_bound,
    recent_days_bounds,
    to_iso,
)

logger = logging.getLogger(__name__)


def _visible_docs_qs(user, mine_only: bool = False):
    """构造当前用户可访问的文档查询集（复用既有角色/学院/部门隔离）。

    - mine_only=True：只看当前登录用户自己上传的文件；
    - super_admin：全部；
    - admin/college_admin：校级文档 + 本学院文档 + 自己上传的文档；
    - dept_admin：校级文档 + 本部门文档 + 自己上传的文档；
    - 普通用户：自己上传的文档；
    - 未登录：空集。
    """
    from apps.knowledge.models import KnowledgeDoc

    user_role = getattr(user, 'role', None) if user else None
    if not user or not user_role:
        return KnowledgeDoc.objects.none()
    if mine_only:
        return KnowledgeDoc.objects.filter(uploader=user)
    if user_role == 'super_admin':
        return KnowledgeDoc.objects.all()
    if user_role in ('admin', 'college_admin'):
        return KnowledgeDoc.objects.filter(
            Q(college_id__isnull=True, department_id__isnull=True)
            | Q(college_id=user.college_id)
            | Q(uploader=user)
        )
    if user_role == 'dept_admin':
        return KnowledgeDoc.objects.filter(
            Q(college_id__isnull=True, department_id__isnull=True)
            | Q(department_id=user.department_id)
            | Q(uploader=user)
        )
    return KnowledgeDoc.objects.filter(uploader=user)


def _parse_bound_or_raise(value, name: str):
    """解析路由传入的区间端点；传了非法值就报错，不静默忽略。"""
    if not value:
        return None
    parsed = parse_bound(value)
    if parsed is None:
        raise ValueError(f"{name} 不是合法日期时间: {value!r}")
    return parsed


def _apply_range(qs, start_at=None, end_at=None, field: str = 'created_at'):
    """按左闭右开区间过滤；只处理真正传入的端点。"""
    start = _parse_bound_or_raise(start_at, 'start_at')
    end = _parse_bound_or_raise(end_at, 'end_at')
    if start is not None:
        qs = qs.filter(**{f'{field}__gte': start})
    if end is not None:
        qs = qs.filter(**{f'{field}__lt': end})
    return qs


def _time_fields(doc) -> dict:
    """统一的文件时间字段（ISO 8601 带 +08:00 + 业务时区展示值）。"""
    return {
        "created_at": to_iso(doc.created_at) or '',
        "created_at_display": format_business_time(doc.created_at),
        "indexed_at": to_iso(doc.indexed_at),
        "indexed_at_display": format_business_time(doc.indexed_at),
        "freshness_date": doc.freshness_date.isoformat() if doc.freshness_date else None,
    }


def _doc_summary(doc) -> dict:
    data = {
        "id": doc.id,
        "title": doc.title,
        "file_name": doc.file_name,
        "file_type": doc.file_type,
        "file_size": doc.file_size,
        "file_size_str": _format_size(doc.file_size or 0),
        "status": doc.get_status_display(),
        "status_code": doc.status,
        "uploader_name": doc.uploader.username if doc.uploader else "未知",
        "category_name": doc.category.name if doc.category else "未分类",
        "source_type": doc.get_source_type_display(),
    }
    data.update(_time_fields(doc))
    return data


def _keyword_q(keywords, fields=('title', 'file_name')):
    query = Q()
    for keyword in keywords or []:
        keyword = (keyword or '').strip()
        if not keyword:
            continue
        if len(keyword) < 2 and not keyword.isascii():
            continue
        for field in fields:
            query |= Q(**{f'{field}__icontains': keyword})
    return query


# 路由从问句里抽到的类型词 → 模型 file_type 取值（见 KnowledgeDoc.FILE_TYPE_CHOICES）
_FILE_TYPE_ALIASES = {
    "pdf": "pdf",
    "word": "doc", "doc": "doc", "docx": "doc",
    "ppt": "ppt", "pptx": "ppt", "演示": "ppt", "演示文稿": "ppt",
    "excel": "xls", "xls": "xls", "xlsx": "xls", "表格": "xls",
    "图片": "image", "image": "image", "jpg": "image", "jpeg": "image",
    "png": "image",
    "视频": "video", "video": "video",
    "音频": "audio", "audio": "audio",
    "txt": "txt", "md": "md", "html": "html",
}


def _normalize_file_type(value) -> Optional[str]:
    """把路由参数里的类型词归一成模型可过滤的取值；无法识别时返回 None。"""
    if not value:
        return None
    return _FILE_TYPE_ALIASES.get(str(value).strip().lower())


def _apply_type_filters(qs, file_type=None, category=None):
    """应用已归一化的文件类型/分类过滤；无法识别的取值不参与过滤。"""
    normalized = _normalize_file_type(file_type)
    if normalized:
        qs = qs.filter(file_type=normalized)
    if category:
        qs = qs.filter(category__name__icontains=category)
    return qs


def _filter_meta(file_type=None, category=None) -> dict:
    """记录真正生效的过滤条件，便于回答层与测试核对。"""
    applied = {}
    normalized = _normalize_file_type(file_type)
    if normalized:
        applied["file_type"] = normalized
    if category:
        applied["category"] = category
    return {"applied_filters": applied} if applied else {}


def _range_meta(range_label: Optional[str], start_at=None, end_at=None) -> dict:
    return {
        "range_label": range_label or "全部时间",
        "range_start": start_at,
        "range_end": end_at,
        "range_end_exclusive": True,
    }


def count_user_docs(
    user,
    mine_only: bool = False,
    start_at=None,
    end_at=None,
    range_label: Optional[str] = None,
    scope_label: Optional[str] = None,
    file_type=None,
    category=None,
) -> dict:
    """统计文档数（超管看全部，管理员看自己的+本学院，普通用户仅看自己的）。

    Returns:
        {"total": int, "by_status": {...}, "scope_label": str, "range_label": str}
    """
    qs = _visible_docs_qs(user, mine_only=mine_only)
    qs = _apply_range(qs, start_at, end_at)
    qs = _apply_type_filters(qs, file_type, category)

    total = qs.count()
    by_status = dict(qs.values_list("status").annotate(count=Count("id")))

    return {
        "total": total,
        "by_status": by_status,
        "scope_label": scope_label or ("本人上传" if mine_only else "当前账号可访问范围"),
        **_range_meta(range_label, start_at, end_at),
        **_filter_meta(file_type, category),
    }


def list_user_recent_docs(
    user,
    days: Optional[int] = None,
    limit: int = 10,
    file_type: Optional[str] = None,
    category_name: Optional[str] = None,
    mine_only: bool = False,
    start_at=None,
    end_at=None,
    range_label: Optional[str] = None,
    scope_label: Optional[str] = None,
) -> List[dict]:
    """列出当前用户最近上传/指定区间内的文档。"""
    qs = _visible_docs_qs(user, mine_only=mine_only)

    if start_at or end_at:
        qs = _apply_range(qs, start_at, end_at)
    elif days:
        since, until = recent_days_bounds(days)
        qs = qs.filter(created_at__gte=since, created_at__lt=until)

    if file_type:
        qs = qs.filter(file_type=file_type)

    if category_name:
        qs = qs.filter(
            Q(category__name__icontains=category_name)
            | Q(title__icontains=category_name)
            | Q(description__icontains=category_name)
        )

    qs = qs.select_related("category", "uploader").order_by("-created_at")[:limit]

    results = []
    for doc in qs:
        item = _doc_summary(doc)
        item["scope_label"] = scope_label or ("本人上传" if mine_only else "当前账号可访问范围")
        item["range_label"] = range_label or (f"最近{days}天" if days else "全部时间")
        results.append(item)
    return results


def get_user_storage_stats(user) -> dict:
    """统计用户存储使用情况。"""
    from apps.knowledge.models import KnowledgeDoc

    qs = KnowledgeDoc.objects.filter(uploader=user)

    total_files = qs.count()
    total_size = qs.aggregate(total=Sum("file_size"))["total"] or 0

    by_type = (
        qs.values("file_type")
        .annotate(count=Count("id"), size=Sum("file_size"))
        .order_by("-size")
    )

    return {
        "total_size": total_size,
        "total_size_str": _format_size(total_size),
        "total_files": total_files,
        "by_file_type": list(by_type),
    }


def get_doc_categories(user) -> List[dict]:
    """按分类统计用户上传的文档分布。"""
    from apps.knowledge.models import KnowledgeDoc

    qs = (
        KnowledgeDoc.objects
        .filter(uploader=user)
        .values("category__name")
        .annotate(count=Count("id"), total_size=Sum("file_size"))
        .order_by("-count")
    )

    return [
        {
            "category_name": item["category__name"] or "未分类",
            "count": item["count"],
            "total_size": _format_size(item["total_size"] or 0),
        }
        for item in qs
    ]


def find_user_docs(
    user,
    keywords: Optional[List[str]] = None,
    days: Optional[int] = None,
    category: Optional[str] = None,
    file_type: Optional[str] = None,
    limit: int = 10,
    mine_only: bool = False,
    start_at=None,
    end_at=None,
    range_label: Optional[str] = None,
    scope_label: Optional[str] = None,
) -> List[dict]:
    """按条件查找用户可访问的文档（组合过滤）。"""
    qs = _visible_docs_qs(user, mine_only=mine_only)

    if keywords:
        kw_query = _keyword_q(keywords, fields=('title', 'description', 'file_name'))
        if kw_query:
            qs = qs.filter(kw_query)

    if start_at or end_at:
        qs = _apply_range(qs, start_at, end_at)
    elif days:
        since, until = recent_days_bounds(days)
        qs = qs.filter(created_at__gte=since, created_at__lt=until)

    if category:
        qs = qs.filter(
            Q(category__name__icontains=category) | Q(title__icontains=category)
        )

    if file_type:
        qs = qs.filter(file_type=file_type)

    qs = qs.select_related("category", "uploader").order_by("-created_at")[:limit]

    from utils.oss import oss_client

    def _get_dl_url(doc) -> str:
        if doc.oss_key:
            url = oss_client.get_download_url(
                doc.oss_key, expires=3600, file_name=doc.file_name,
            )
            if url:
                return url
        return ''

    results = []
    for doc in qs:
        item = _doc_summary(doc)
        item["description"] = doc.description[:100] if doc.description else ""
        item["download_url"] = _get_dl_url(doc)
        item["scope_label"] = scope_label or ("本人上传" if mine_only else "当前账号可访问范围")
        item["range_label"] = range_label or (f"最近{days}天" if days else "全部时间")
        results.append(item)
    return results


# ------------------------------------------------------------------
# 精确时间查询：指定文件的上传时间 / 索引完成时间
# ------------------------------------------------------------------

def get_doc_times(
    user,
    keywords: Optional[List[str]] = None,
    doc_id: Optional[int] = None,
    mine_only: bool = False,
    start_at=None,
    end_at=None,
    range_label: Optional[str] = None,
    scope_label: Optional[str] = None,
    limit: int = 10,
) -> dict:
    """确定性查询指定文件的上传时间与索引完成时间。

    不依赖 RAG 或模型推断；未找到时明确返回 not_found。
    """
    qs = _visible_docs_qs(user, mine_only=mine_only)
    qs = _apply_range(qs, start_at, end_at)

    if doc_id:
        qs = qs.filter(pk=doc_id)
    elif keywords:
        kw_query = _keyword_q(keywords)
        if not kw_query:
            return {
                "need_filename": True,
                "matches": [],
                "total": 0,
                "scope_label": scope_label or ("本人上传" if mine_only else "当前账号可访问范围"),
                **_range_meta(range_label, start_at, end_at),
            }
        qs = qs.filter(kw_query)
    else:
        return {
            "need_filename": True,
            "matches": [],
            "total": 0,
            "scope_label": scope_label or ("本人上传" if mine_only else "当前账号可访问范围"),
            **_range_meta(range_label, start_at, end_at),
        }

    total = qs.count()
    docs = list(
        qs.select_related("uploader", "category").order_by("-created_at")[:limit]
    )
    matches = []
    for doc in docs:
        item = {
            "id": doc.id,
            "title": doc.title,
            "file_name": doc.file_name,
            "file_type": doc.file_type,
            "status": doc.get_status_display(),
            "status_code": doc.status,
            "uploader_name": doc.uploader.username if doc.uploader else "未知",
            **_time_fields(doc),
        }
        matches.append(item)

    result = {
        "need_filename": False,
        "not_found": total == 0,
        "ambiguous": total > 1,
        "total": total,
        "shown": len(matches),
        "truncated": total > len(matches),
        "matches": matches,
        "scope_label": scope_label or ("本人上传" if mine_only else "当前账号可访问范围"),
        **_range_meta(range_label, start_at, end_at),
    }
    if total > len(matches):
        result["notice"] = (
            f"共匹配到 {total} 个文件，仅展示前 {len(matches)} 个；"
            "请提供更精确的文件名或补充日期范围。"
        )
    return result


# ------------------------------------------------------------------
# 按日期区间统计 / 列表
# ------------------------------------------------------------------

def list_docs_by_range(
    user,
    start_at=None,
    end_at=None,
    range_label: Optional[str] = None,
    mine_only: bool = False,
    scope_label: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    file_type=None,
    category=None,
) -> dict:
    """按上传时间区间列出文档（左闭右开），并返回截断信息。"""
    limit = max(1, min(int(limit or 20), 100))
    offset = max(0, int(offset or 0))

    qs = _visible_docs_qs(user, mine_only=mine_only)
    qs = _apply_range(qs, start_at, end_at)
    qs = _apply_type_filters(qs, file_type, category)
    qs = qs.select_related("uploader", "category")
    total = qs.count()
    docs = list(qs.order_by("-created_at")[offset:offset + limit])

    items = [_doc_summary(doc) for doc in docs]
    shown = len(items)
    has_more = offset + shown < total
    result = {
        "total": total,
        "shown": shown,
        "offset": offset,
        "limit": limit,
        "truncated": has_more,
        "has_more": has_more,
        "items": items,
        "scope_label": scope_label or ("本人上传" if mine_only else "当前账号可访问范围"),
        **_range_meta(range_label, start_at, end_at),
        **_filter_meta(file_type, category),
    }
    if has_more:
        result["notice"] = (
            f"共 {total} 个文件，当前展示第 {offset + 1}-{offset + shown} 个，"
            "还有更多结果；可缩小日期范围或要求继续列出。"
        )
    elif total == 0:
        result["notice"] = "该时间区间内没有查询到文件。"
    return result


def count_docs_by_range(
    user,
    start_at=None,
    end_at=None,
    range_label: Optional[str] = None,
    mine_only: bool = False,
    scope_label: Optional[str] = None,
    file_type=None,
    category=None,
) -> dict:
    """按上传时间区间统计文档数量（左闭右开）。"""
    qs = _visible_docs_qs(user, mine_only=mine_only)
    qs = _apply_range(qs, start_at, end_at)
    qs = _apply_type_filters(qs, file_type, category)
    return {
        "total": qs.count(),
        "by_status": dict(qs.values_list("status").annotate(count=Count("id"))),
        "scope_label": scope_label or ("本人上传" if mine_only else "当前账号可访问范围"),
        **_range_meta(range_label, start_at, end_at),
        **_filter_meta(file_type, category),
    }


# ------------------------------------------------------------------
# KnowledgeDoc 表数据查询（热门/排行/统计/最新）
# ------------------------------------------------------------------
# Keep the historical function names and response keys for callers that still
# use the resource-query API.  KnowledgeDoc no longer stores download/view
# counters or a published_at timestamp, so indexed_at (falling back to
# created_at) is used as the publication time and those counters are exposed
# as zero-valued compatibility fields.
def _published_docs_queryset():
    """Return documents that are available to knowledge-base queries."""
    from apps.knowledge.models import KnowledgeDoc

    return KnowledgeDoc.objects.filter(status='indexed')


def _published_range_filter(qs, since=None, until=None):
    """按索引时间过滤，未索引完成的文档回退到创建时间。"""
    if since is not None:
        qs = qs.filter(Q(indexed_at__gte=since) | Q(indexed_at__isnull=True, created_at__gte=since))
    if until is not None:
        qs = qs.filter(Q(indexed_at__lt=until) | Q(indexed_at__isnull=True, created_at__lt=until))
    return qs


def get_popular_knowledge_docs(
    limit: int = 10,
    days: Optional[int] = None,
    college_id: Optional[int] = None,
    discipline_id: Optional[int] = None,
    start_at=None,
    end_at=None,
) -> List[dict]:
    """查询热门知识文档（按最近索引时间排序，保留旧 API 字段）。"""
    qs = _published_docs_queryset()

    start = _parse_bound_or_raise(start_at, 'start_at')
    end = _parse_bound_or_raise(end_at, 'end_at')
    if start is None and end is None and days:
        start, end = recent_days_bounds(days)
    qs = _published_range_filter(qs, start, end)

    if college_id:
        qs = qs.filter(college_id=college_id)
    if discipline_id:
        qs = qs.filter(discipline_id=discipline_id)

    qs = qs.select_related('college', 'uploader').order_by('-indexed_at', '-created_at')[:limit]
    return [
        {
            'id': doc.id,
            'title': doc.title,
            'description': (doc.description or '')[:100],
            'file_type': doc.file_type,
            'download_count': 0,
            'view_count': 0,
            'college_name': doc.college.name if doc.college else '未知',
            'uploader_name': doc.uploader.username if doc.uploader else '未知',
            'published_at': format_business_time(doc.indexed_at or doc.created_at, '%Y-%m-%d'),
        }
        for doc in qs
    ]


def get_recent_knowledge_docs(
    limit: int = 10,
    days: int = 7,
    college_id: Optional[int] = None,
    discipline_id: Optional[int] = None,
    start_at=None,
    end_at=None,
) -> List[dict]:
    """查询最近索引的知识文档（保留旧 API 字段）。"""
    start = _parse_bound_or_raise(start_at, 'start_at')
    end = _parse_bound_or_raise(end_at, 'end_at')
    if start is None and end is None:
        start, end = recent_days_bounds(days)
    qs = _published_range_filter(_published_docs_queryset(), start, end)

    if college_id:
        qs = qs.filter(college_id=college_id)
    if discipline_id:
        qs = qs.filter(discipline_id=discipline_id)

    qs = qs.select_related('college', 'uploader', 'category').order_by('-indexed_at', '-created_at')[:limit]
    return [
        {
            'id': doc.id,
            'title': doc.title,
            'description': (doc.description or '')[:100],
            'file_type': doc.file_type,
            'download_count': 0,
            'view_count': 0,
            'college_name': doc.college.name if doc.college else '未知',
            'category_name': doc.category.name if doc.category else '未分类',
            'uploader_name': doc.uploader.username if doc.uploader else '未知',
            'published_at': format_business_time(doc.indexed_at or doc.created_at, '%Y-%m-%d'),
        }
        for doc in qs
    ]


def get_doc_ranking_by_college(limit: int = 20) -> List[dict]:
    """按学院统计已索引知识文档数量（保留旧 API 字段）。"""
    qs = (
        _published_docs_queryset()
        .filter(college__isnull=False)
        .values('college__name')
        .annotate(total_count=Count('id'))
        .order_by('-total_count')[:limit]
    )
    return [
        {
            'college_name': item['college__name'],
            'resource_count': item['total_count'],
            'total_downloads': 0,
            'total_views': 0,
        }
        for item in qs
    ]


def get_doc_stats() -> dict:
    """统计知识文档总量（保留旧资源统计 API 的返回结构）。"""
    from apps.knowledge.models import KnowledgeDoc

    published_qs = _published_docs_queryset()
    by_type = list(
        published_qs.values('file_type')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    by_status = list(
        KnowledgeDoc.objects.values('status')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    return {
        'total': published_qs.count(),
        'total_downloads': 0,
        'total_views': 0,
        'by_file_type': by_type,
        'by_status': by_status,
    }


def execute_query(route: dict, user) -> dict:
    """根据路由信息执行对应的数据查询。

    Args:
        route: route_question() 返回的路由信息
        user: 用户对象

    Returns:
        {"success": bool, "data": any, "error": str}
    """
    query_type = route.get("query_type", "")
    params = route.get("params", {})

    def _common_kwargs():
        return {
            "mine_only": bool(params.get("mine_only")),
            "start_at": params.get("start_at"),
            "end_at": params.get("end_at"),
            "range_label": params.get("range_label"),
            "scope_label": params.get("scope_label"),
        }

    def _filter_kwargs():
        # 只有真正支持类型/分类过滤的查询才传，避免上游参数被静默丢弃
        return {
            "file_type": params.get("file_type"),
            "category": params.get("category"),
        }

    try:
        if query_type == "count_docs":
            return {"success": True, "data": count_user_docs(
                user, **_common_kwargs(), **_filter_kwargs(),
            )}

        elif query_type == "count_docs_by_range":
            return {"success": True, "data": count_docs_by_range(
                user, **_common_kwargs(), **_filter_kwargs(),
            )}

        elif query_type == "list_docs_by_range":
            return {"success": True, "data": list_docs_by_range(
                user,
                limit=params.get("limit", 20),
                offset=params.get("offset", 0),
                **_common_kwargs(),
                **_filter_kwargs(),
            )}

        elif query_type == "recent_docs":
            data = list_user_recent_docs(
                user,
                days=params.get("days"),
                limit=params.get("limit", 10),
                file_type=params.get("file_type"),
                category_name=params.get("category"),
                **_common_kwargs(),
            )
            return {"success": True, "data": data}

        elif query_type == "storage_stats":
            return {"success": True, "data": get_user_storage_stats(user)}

        elif query_type == "doc_categories":
            return {"success": True, "data": get_doc_categories(user)}

        elif query_type in ("doc_upload_time", "doc_index_time"):
            # 无法确定目标文件时返回 need_filename，由回答层要求补充文件名；
            # 不能静默换成“按区间列出文件”的无关查询。
            data = get_doc_times(
                user,
                keywords=params.get("keywords"),
                doc_id=params.get("doc_id"),
                limit=params.get("limit", 10),
                **_common_kwargs(),
            )
            return {"success": True, "data": data}

        elif query_type == "find_docs":
            data = find_user_docs(
                user,
                keywords=params.get("keywords"),
                days=params.get("days"),
                category=params.get("category"),
                file_type=params.get("file_type"),
                limit=params.get("limit", 10),
                **_common_kwargs(),
            )
            return {"success": True, "data": data}

        elif query_type == "popular_docs":
            data = get_popular_knowledge_docs(
                limit=params.get("limit", 10),
                days=params.get("days"),
                college_id=params.get("college_id"),
                discipline_id=params.get("discipline_id"),
                start_at=params.get("start_at"),
                end_at=params.get("end_at"),
            )
            return {"success": True, "data": data}

        elif query_type == "recent_docs_platform":
            data = get_recent_knowledge_docs(
                limit=params.get("limit", 10),
                days=params.get("days", 7),
                college_id=params.get("college_id"),
                discipline_id=params.get("discipline_id"),
                start_at=params.get("start_at"),
                end_at=params.get("end_at"),
            )
            return {"success": True, "data": data}

        elif query_type == "doc_ranking":
            data = get_doc_ranking_by_college(
                limit=params.get("limit", 20),
            )
            return {"success": True, "data": data}

        elif query_type == "doc_stats":
            return {"success": True, "data": get_doc_stats()}

        # Compatibility for routes persisted by older clients.
        elif query_type == "resource_popular":
            return {"success": True, "data": get_popular_knowledge_docs(
                limit=params.get("limit", 10), days=params.get("days"),
                college_id=params.get("college_id"), discipline_id=params.get("discipline_id"),
                start_at=params.get("start_at"), end_at=params.get("end_at"),
            )}
        elif query_type == "resource_recent":
            return {"success": True, "data": get_recent_knowledge_docs(
                limit=params.get("limit", 10), days=params.get("days", 7),
                college_id=params.get("college_id"), discipline_id=params.get("discipline_id"),
                start_at=params.get("start_at"), end_at=params.get("end_at"),
            )}
        elif query_type == "resource_ranking":
            return {"success": True, "data": get_doc_ranking_by_college(limit=params.get("limit", 20))}
        elif query_type == "resource_stats":
            return {"success": True, "data": get_doc_stats()}

        else:
            return {"success": False, "data": None, "error": f"未知查询类型: {query_type}"}

    except Exception as e:
        logger.error(f"数据查询失败: {e}", exc_info=True)
        return {"success": False, "data": None, "error": str(e)}


# ------------------------------------------------------------------
# Prompt 构造（将查询结果格式化为 LLM 上下文）
# ------------------------------------------------------------------
_SYSTEM_PROMPT_TEMPLATE = """你是成都东软学院的 AI 智能助手，负责回答用户关于文件管理和知识库的问题。

{context}

用户查询了文件管理数据，以下是数据库确定性查询结果。请只根据这些结果用自然语言回答，不要编造。

查询结果：
{result}

时间字段说明（全部为 Asia/Shanghai 北京时间）：
- created_at / created_at_display：文件首次上传（入库）时间；回答“上传时间”只能用它。
- indexed_at / indexed_at_display：索引完成时间；为 null 时说明“尚未完成索引/暂无索引完成时间”，
  绝不能拿 created_at 或 freshness_date 冒充。
- freshness_date：文件内容截止日期，不是上传时间，不能当作上传时间回答。
- 所有 *_display 字段已经是北京时间，直接原样使用，禁止再加减时区或换算成 UTC。
- range_start / range_end 是左闭右开区间（含开始，不含结束）；range_label 可直接引用。
- scope_label 说明查询范围（如“本人上传”“当前账号可访问范围”）；不要扩大或缩小这个范围。
- shown 是本次实际返回的条数，total 是符合条件的总条数；total 可能远大于 shown，
  绝不能把 shown 当成总数（例如 shown=20、total=170 时必须说明“共 170 个，这里列出 20 个”）。
- truncated=true 时要告知用户“可能还有更多”，不要假装已列出全部。
- need_filename=true：说明还没确定是哪个文件，请用户补充具体文件名；不要猜测文件，也不要改列文件清单。
- not_found=true：只代表“在当前查询条件（range_label、applied_filters、scope_label）下没有匹配”，
  不能据此断言文件在整个数据库中不存在。
- ambiguous=true：存在多个同名/相近文件时，逐一列出文件名、上传人、上传时间让用户确认，不要替用户选一个。
- applied_filters：本次真正生效的类型/分类过滤；没有该字段表示未按类型或分类过滤。

请根据以上数据直接回答用户的问题。数据为空时如实说明“在本次查询条件下没有找到”，不要编造文件或时间。
回答要简洁、友好，使用中文。

下载链接规则：只有当某条数据的 download_url 是非空字符串时，才可以用它给文件名加链接；
数据中没有 download_url 或为空时，只写文件名，禁止自行拼接、猜测或引用示例链接。"""


def build_data_query_prompt(question: str, result: dict) -> list:
    """将数据查询结果转换为 LLM 消息。"""
    import json
    from utils.context import build_system_context
    ctx = build_system_context()

    if not result["success"]:
        system_content = (
            f"{ctx}\n\n"
            "你是成都东软学院的 AI 智能助手。用户查询文件管理数据时出错了，"
            "请礼貌地告知用户暂时无法查询，建议稍后重试或联系管理员。"
        )
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": question},
        ]
        return messages

    result_str = json.dumps(result["data"], ensure_ascii=False, indent=2)
    system_content = _SYSTEM_PROMPT_TEMPLATE.format(result=result_str, context=ctx)

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": question},
    ]
    return messages


# ------------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------------
def _format_size(size_bytes: int) -> str:
    """将字节数格式化为可读字符串。"""
    size_bytes = size_bytes or 0
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


# ------------------------------------------------------------------
# 文字提问专用：结果状态标注与提示词包装
# ------------------------------------------------------------------
# 语音入口继续使用 execute_query / get_doc_times / build_data_query_prompt，
# 以下函数只由文字入口（AskView._handle_data_query）调用。

_DOC_TIME_QUERY_TYPES = ("doc_upload_time", "doc_index_time")

# not_visible 与 not_found 必须使用完全相同的措辞，避免泄露文件是否存在。
_NOT_FOUND_RULE = (
    "当前状态为 not_found：只能说明“在当前查询条件下没有匹配到”，"
    "不得断言文件在系统中不存在，不得提及权限、可见性或是否存在。"
)

_TEXT_STATUS_RULES = {
    "need_filename": (
        "当前状态为 need_filename：尚未确定用户指的是哪份文件。"
        "请用户补充完整文件名或说明具体是哪一份；"
        "不得声称“没有找到相关文件”，不得列出文件清单，也不得猜测文件。"
    ),
    "ambiguous": (
        "当前状态为 ambiguous：存在多份候选文件（可能来自上一轮检索结果）。"
        "请用户指明是哪一份（可提供文件名）；"
        "普通用户场景不要罗列候选文件名，不得替用户选择其中一份。"
    ),
    "not_found": _NOT_FOUND_RULE,
    # 与 not_found 完全一致：对外不得暴露“文件存在但不可见”的信息
    "not_visible": _NOT_FOUND_RULE,
    "query_error": (
        "当前状态为 query_error：查询过程出现异常。"
        "只能告知用户“查询暂时不可用，请稍后重试”，"
        "禁止说“没有找到”，也不得给出任何文件或时间信息。"
    ),
}


def annotate_doc_time_status(result: dict, route: dict) -> dict:
    """文字入口专用：在 execute_query 结果顶层附加 status。

    只读、无副作用；status 放在 result 顶层，不进入 result["data"]，
    因此不会随 json.dumps 进入提示词。非文件时间查询原样返回。
    """
    if not isinstance(result, dict):
        return result
    if (route or {}).get("query_type") not in _DOC_TIME_QUERY_TYPES:
        return result

    if not result.get("success"):
        status = "query_error"
    else:
        data = result.get("data")
        if not isinstance(data, dict):
            status = "query_error"
        elif data.get("context_ambiguous"):
            status = "ambiguous"
        elif data.get("need_filename"):
            status = "need_filename"
        elif data.get("ambiguous"):
            status = "ambiguous"
        elif data.get("not_found"):
            status = "not_visible" if (route.get("params") or {}).get("doc_id") else "not_found"
        else:
            status = "ok"

    annotated = dict(result)
    annotated["status"] = status
    return annotated


def build_text_data_query_prompt(question: str, result: dict) -> list:
    """文字入口专用提示词。

    status 为 ok 时与既有提示词逐字节一致；只有异常/不完整状态才追加规则，
    避免影响正常的查询回答。
    """
    status = (result or {}).get("status")
    messages = build_data_query_prompt(question, result)
    if status in (None, "ok"):
        return messages

    rule = _TEXT_STATUS_RULES.get(status, _NOT_FOUND_RULE)
    messages[0]["content"] = messages[0]["content"] + "\n\n" + rule
    return messages
