"""
文件管理数据查询 (Data Query)

执行文件管理相关的数据库查询，供意图路由分发后使用。
支持：文档数统计、最近上传、存储统计、分类统计、带条件查找。
"""
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from django.db.models import Count, Sum, Q
from django.utils import timezone

logger = logging.getLogger(__name__)


def count_user_docs(user) -> dict:
    """统计当前用户上传的文档数。

    Returns:
        {"total": int, "by_status": {"indexed": int, "uploaded": int, ...}}
    """
    from apps.knowledge.models import KnowledgeDoc

    qs = KnowledgeDoc.objects.filter(uploader=user)

    total = qs.count()
    by_status = dict(qs.values_list("status").annotate(count=Count("id")))

    return {
        "total": total,
        "by_status": by_status,
    }


def list_user_recent_docs(
    user,
    days: Optional[int] = None,
    limit: int = 10,
    file_type: Optional[str] = None,
    category_name: Optional[str] = None,
) -> List[dict]:
    """列出当前用户最近上传的文档。

    Args:
        user: 用户对象
        days: 最近 N 天（None 表示不限时间）
        limit: 最多返回条数
        file_type: 按文件类型过滤（可选）
        category_name: 按分类名称过滤（可选）

    Returns:
        [{"id": int, "title": str, "file_type": str, "file_size": int,
          "status": str, "created_at": str, "category_name": str}, ...]
    """
    from apps.knowledge.models import KnowledgeDoc

    qs = KnowledgeDoc.objects.filter(uploader=user)

    if days:
        since = timezone.now() - timedelta(days=days)
        qs = qs.filter(created_at__gte=since)

    if file_type:
        qs = qs.filter(file_type=file_type)

    if category_name:
        from apps.classify.models import Category
        qs = qs.filter(
            Q(category__name__icontains=category_name)
            | Q(title__icontains=category_name)
            | Q(description__icontains=category_name)
        )

    qs = qs.select_related("category").order_by("-created_at")[:limit]

    results = []
    for doc in qs:
        results.append({
            "id": doc.id,
            "title": doc.title,
            "file_type": doc.file_type,
            "file_size": doc.file_size,
            "status": doc.get_status_display(),
            "created_at": doc.created_at.strftime("%Y-%m-%d %H:%M"),
            "category_name": doc.category.name if doc.category else "未分类",
            "source_type": doc.get_source_type_display(),
        })
    return results


def get_user_storage_stats(user) -> dict:
    """统计用户存储使用情况。

    Returns:
        {
            "total_size": int,          # 总字节数
            "total_size_str": str,      # 格式化大小
            "total_files": int,
            "by_file_type": [{"file_type": str, "count": int, "size": int}, ...]
        }
    """
    from apps.knowledge.models import KnowledgeDoc

    qs = KnowledgeDoc.objects.filter(uploader=user)

    total_files = qs.count()
    total_size = qs.aggregate(total=Sum("file_size"))["total"] or 0

    # 按文件类型分组
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
    """按分类统计用户上传的文档分布。

    Returns:
        [{"category_name": str, "count": int, "total_size": int}, ...]
    """
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
) -> List[dict]:
    """按条件查找用户上传的文档（组合过滤）。

    Args:
        user: 用户对象
        keywords: 搜索关键词列表
        days: 最近 N 天
        category: 分类名称关键词
        file_type: 文件类型
        limit: 最多返回条数

    Returns:
        [{"id": int, "title": str, "description": str, ...}, ...]
    """
    from apps.knowledge.models import KnowledgeDoc

    qs = KnowledgeDoc.objects.filter(uploader=user)

    if keywords:
        kw_query = Q()
        for kw in keywords:
            if len(kw) >= 2:
                kw_query |= Q(title__icontains=kw)
                kw_query |= Q(description__icontains=kw)
        if kw_query:
            qs = qs.filter(kw_query)

    if days:
        since = timezone.now() - timedelta(days=days)
        qs = qs.filter(created_at__gte=since)

    if category:
        qs = qs.filter(
            Q(category__name__icontains=category) | Q(title__icontains=category)
        )

    if file_type:
        qs = qs.filter(file_type=file_type)

    qs = qs.select_related("category").order_by("-created_at")[:limit]

    return [
        {
            "id": doc.id,
            "title": doc.title,
            "description": doc.description[:100] if doc.description else "",
            "file_type": doc.file_type,
            "file_size": doc.file_size,
            "file_size_str": _format_size(doc.file_size),
            "status": doc.get_status_display(),
            "created_at": doc.created_at.strftime("%Y-%m-%d %H:%M"),
            "category_name": doc.category.name if doc.category else "未分类",
        }
        for doc in qs
    ]


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

    try:
        if query_type == "count_docs":
            return {"success": True, "data": count_user_docs(user)}

        elif query_type == "recent_docs":
            data = list_user_recent_docs(
                user,
                days=params.get("days"),
                limit=params.get("limit", 10),
                file_type=params.get("file_type"),
                category_name=params.get("category"),
            )
            return {"success": True, "data": data}

        elif query_type == "storage_stats":
            return {"success": True, "data": get_user_storage_stats(user)}

        elif query_type == "doc_categories":
            return {"success": True, "data": get_doc_categories(user)}

        elif query_type == "find_docs":
            data = find_user_docs(
                user,
                keywords=params.get("keywords"),
                days=params.get("days"),
                category=params.get("category"),
                file_type=params.get("file_type"),
                limit=params.get("limit", 10),
            )
            return {"success": True, "data": data}

        else:
            return {"success": False, "data": None, "error": f"未知查询类型: {query_type}"}

    except Exception as e:
        logger.error(f"数据查询失败: {e}", exc_info=True)
        return {"success": False, "data": None, "error": str(e)}


# ------------------------------------------------------------------
# Prompt 构造（将查询结果格式化为 LLM 上下文）
# ------------------------------------------------------------------
_SYSTEM_PROMPT_TEMPLATE = """你是成都东东学院的 AI 智能助手，负责回答用户关于文件管理和知识库的问题。

用户查询了文件管理数据，以下是查询结果。请用自然语言回答用户的问题。

查询结果：
{result}

请根据以上数据直接回答用户的问题。如果数据为空或没有找到，如实告知用户。
回答要简洁、友好，使用中文。"""


def build_data_query_prompt(question: str, result: dict) -> list:
    """将数据查询结果转换为 LLM 消息。

    Args:
        question: 用户原始问题
        result: execute_query() 的返回结果

    Returns:
        messages 列表，可直接传给 llm_client.chat_stream()
    """
    import json

    if not result["success"]:
        system_content = (
            "你是成都东东学院的 AI 智能助手。用户查询文件管理数据时出错了，"
            "请礼貌地告知用户暂时无法查询，建议稍后重试或联系管理员。"
        )
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": question},
        ]
        return messages

    result_str = json.dumps(result["data"], ensure_ascii=False, indent=2)
    system_content = _SYSTEM_PROMPT_TEMPLATE.format(result=result_str)

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
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
