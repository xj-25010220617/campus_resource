"""
SSE 流式代理包装器
将 LLM stream 转发为 Server-Sent Events 格式。
客户端通过 EventSource 接收逐字输出。

支持同步和异步两种模式：
- sse_response: 同步，配合 llm_client.chat_stream()
- sse_response_async: 异步，配合 llm_client.chat_stream_async()（生产推荐）
"""
import json
import logging

from django.http import StreamingHttpResponse

from utils.error_codes import ErrorCode

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 同步 SSE 响应（当前 Django 4.2 兼容）
# ------------------------------------------------------------------
def sse_response(generator, extra_events: dict = None):
    """
    创建 SSE StreamingHttpResponse（同步）。

    Args:
        generator: 同步生成器，逐 token yield 字符串
        extra_events: 额外 SSE 事件数据，可包含:
            - start: 会话/消息 ID
            - references: 普通用户的引用摘要 {count, summary}
            - references_detail: 授权用户引用详情 [{doc_title, page, score, download_url}, ...]
            - suggested: 推荐问题列表
            - confidence: 置信度 {score}
            - done: 结束标记
    """

    def event_stream():
        # 1. start 事件
        if extra_events and extra_events.get('start'):
            yield _format_sse('start', extra_events['start'])

        # 2. 逐 token 转发
        for token in generator:
            if token:
                yield _format_sse('token', {'content': token})

        # 3. references（普通用户）
        if extra_events and extra_events.get('references'):
            yield _format_sse('references', extra_events['references'])

        # 4. references_detail（admin/super_admin 可看完整引用）
        if extra_events and extra_events.get('references_detail'):
            yield _format_sse('references_detail', extra_events['references_detail'])

        # 5. suggested_questions
        if extra_events and extra_events.get('suggested'):
            yield _format_sse('suggested', extra_events['suggested'])

        # 6. confidence
        if extra_events and extra_events.get('confidence'):
            yield _format_sse('confidence', extra_events['confidence'])

        # 7. done 事件
        done_data = extra_events.get('done', {}) if extra_events else {}
        yield _format_sse('done', done_data)

    response = StreamingHttpResponse(
        event_stream(),
        content_type='text/event-stream',
    )
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'  # 禁用 Nginx 缓冲
    response['Access-Control-Allow-Origin'] = '*'
    return response


# ------------------------------------------------------------------
# 异步 SSE 响应（生产环境推荐）
# ------------------------------------------------------------------
async def sse_response_async(async_generator, extra_events: dict = None):
    """
    创建 SSE 异步 StreamingHttpResponse。

    配合 llm_client.chat_stream_async() 使用，避免同步阻塞。

    Args:
        async_generator: 异步生成器，逐 token yield 字符串
        extra_events: 同 sse_response
    """
    async def event_stream():
        # 1. start 事件
        if extra_events and extra_events.get('start'):
            yield _format_sse('start', extra_events['start'])

        # 2. 逐 token 转发
        async for token in async_generator:
            if token:
                yield _format_sse('token', {'content': token})

        # 3. references
        if extra_events and extra_events.get('references'):
            yield _format_sse('references', extra_events['references'])

        # 4. references_detail
        if extra_events and extra_events.get('references_detail'):
            yield _format_sse('references_detail', extra_events['references_detail'])

        # 5. suggested
        if extra_events and extra_events.get('suggested'):
            yield _format_sse('suggested', extra_events['suggested'])

        # 6. confidence
        if extra_events and extra_events.get('confidence'):
            yield _format_sse('confidence', extra_events['confidence'])

        # 7. done
        done_data = extra_events.get('done', {}) if extra_events else {}
        yield _format_sse('done', done_data)

    response = StreamingHttpResponse(
        event_stream(),
        content_type='text/event-stream',
    )
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    response['Access-Control-Allow-Origin'] = '*'
    return response


# ------------------------------------------------------------------
# SSE 事件格式化
# ------------------------------------------------------------------
def _format_sse(event: str, data) -> str:
    """格式化一条 SSE 事件"""
    if isinstance(data, dict):
        data = json.dumps(data, ensure_ascii=False)
    elif not isinstance(data, str):
        data = str(data)
    return f'event: {event}\ndata: {data}\n\n'


# ------------------------------------------------------------------
# 便捷构造器
# ------------------------------------------------------------------
def build_chat_events(
    conversation_id: int,
    message_id: int,
    is_authorized: bool = False,
) -> dict:
    """
    构造 SSE 事件的 extra payload（start + done 部分）。

    references / references_detail / suggested / confidence
    由调用方在 LLM 流结束后填充。

    Args:
        conversation_id: 会话 ID
        message_id: 消息 ID
        is_authorized: 是否为授权用户（admin/super_admin）

    Returns:
        extra_events dict（只含 start/done，其他事件由调用方补充）
    """
    return {
        'start': {
            'conversation_id': conversation_id,
            'message_id': message_id,
        },
        'done': {
            'message_id': message_id,
        },
        '_is_authorized': is_authorized,
    }


def build_references_events(
    search_results: list,
    user_role: str,
    user_id: int = None,
) -> dict:
    """
    根据用户角色构造 references / references_detail 事件数据。

    设计文档第 14.2 节⑬⑭:
    - user: 仅返回 count + summary
    - admin: 看自己上传文档的引用详情
    - super_admin: 看全部引用详情

    Args:
        search_results: retrieve() 返回的检索结果列表
        user_role: 用户角色 ('user' | 'admin' | 'super_admin')
        user_id: 当前用户 ID（admin 需据此过滤）

    Returns:
        {'references': {...}} 或 {'references_detail': [...]}
    """
    if not search_results:
        return {'references': {'count': 0, 'summary': '未找到相关参考资料'}}

    # 过滤 score >= 0.7 的作为引用
    from .rag import RAG_REFERENCE_THRESHOLD
    qualified = [r for r in search_results if r.get('score', 0) >= RAG_REFERENCE_THRESHOLD]

    if user_role == 'user':
        return {
            'references': {
                'count': len(qualified),
                'summary': f'参考了{len(qualified)}份文档' if qualified else '暂无高相关度参考资料',
            }
        }

    # admin / super_admin: 构建引用详情
    detail = []
    for r in qualified:
        entry = {
            'doc_id': r['doc_id'],
            'doc_title': r['doc_title'],
            'page': r.get('page', 0),
            'score': r['score'],
            'content_snippet': r.get('content', '')[:200],
        }
        # 生成 OSS 签名下载 URL
        try:
            from .oss import oss_client
            from apps.knowledge.models import KnowledgeDoc
            doc = KnowledgeDoc.objects.filter(id=r['doc_id']).first()
            if doc and doc.oss_key:
                entry['download_url'] = oss_client.get_download_url(doc.oss_key, expires=300)
        except (ImportError, Exception):
            pass
        detail.append(entry)

    # admin 进一步过滤：只能看自己上传的
    if user_role == 'admin' and user_id:
        try:
            from apps.knowledge.models import KnowledgeDoc
            own_doc_ids = set(
                KnowledgeDoc.objects.filter(
                    uploader_id=user_id, id__in=[d['doc_id'] for d in detail]
                ).values_list('id', flat=True)
            )
            detail = [d for d in detail if d['doc_id'] in own_doc_ids]
        except ImportError:
            pass

    return {'references_detail': detail}


def build_error_sse(message: str, code: ErrorCode = ErrorCode.SSE_ERROR):
    """构造错误 SSE 响应"""
    def generator():
        yield _format_sse('error', {'message': message, 'code': code.value if isinstance(code, ErrorCode) else code})

    response = StreamingHttpResponse(
        generator(),
        content_type='text/event-stream',
    )
    response['Cache-Control'] = 'no-cache'
    return response
