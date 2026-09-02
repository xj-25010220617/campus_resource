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
            - images: 内联展示的图片预览 [{doc_id, doc_title, description, preview_url}, ...]
            - suggested: 推荐问题列表
            - confidence: 置信度 {score}
            - done: 结束标记
    """

    def event_stream():
        try:
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

            # 4.5 images（内联展示的图片预览）
            if extra_events and extra_events.get('images'):
                yield _format_sse('images', extra_events['images'])

            # 5. suggested_questions
            if extra_events and extra_events.get('suggested'):
                yield _format_sse('suggested', extra_events['suggested'])

            # 6. confidence
            if extra_events and extra_events.get('confidence'):
                yield _format_sse('confidence', extra_events['confidence'])

            # 7. done 事件
            done_data = extra_events.get('done', {}) if extra_events else {}
            yield _format_sse('done', done_data)
        except GeneratorExit:
            raise
        except Exception as e:
            logger.error(f'SSE 流异常: {e}')
            yield _format_sse('error', {'message': '回答生成失败，请重试', 'code': ErrorCode.SSE_ERROR.value})

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
        try:
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

            # 4.5 images（内联展示的图片预览）
            if extra_events and extra_events.get('images'):
                yield _format_sse('images', extra_events['images'])

            # 5. suggested
            if extra_events and extra_events.get('suggested'):
                yield _format_sse('suggested', extra_events['suggested'])

            # 6. confidence
            if extra_events and extra_events.get('confidence'):
                yield _format_sse('confidence', extra_events['confidence'])

            # 7. done
            done_data = extra_events.get('done', {}) if extra_events else {}
            yield _format_sse('done', done_data)
        except GeneratorExit:
            raise
        except Exception as e:
            logger.error(f'SSE 异步流异常: {e}')
            yield _format_sse('error', {'message': '回答生成失败，请重试', 'code': ErrorCode.SSE_ERROR.value})

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
    """格式化一条 SSE 事件。

    非字符串数据（dict / list / bool / int 等）统一 json.dumps 序列化，
    避免 list 走 str() 产生单引号的非法 JSON（前端 JSON.parse 会失败）。
    """
    if not isinstance(data, str):
        data = json.dumps(data, ensure_ascii=False)
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
                entry['download_url'] = oss_client.get_download_url(
                    doc.oss_key, expires=300, file_name=doc.file_name,
                )
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


# 图片 MIME 类型映射（供 AI 对话图片内联展示）
_IMAGE_MIME_MAP = {
    'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
    'gif': 'image/gif', 'webp': 'image/webp', 'bmp': 'image/bmp',
    'svg': 'image/svg+xml',
}


def _guess_image_content_type(file_name: str) -> str:
    """根据文件扩展名推断图片 MIME 类型"""
    if not file_name or '.' not in file_name:
        return ''
    ext = file_name.rsplit('.', 1)[-1].lower()
    return _IMAGE_MIME_MAP.get(ext, '')


def build_images_events(image_results: list) -> dict:
    """构造 images 事件数据（图片预览 URL 列表，供前端内联展示）。

    Args:
        image_results: retrieve_images() 返回的图片文档列表

    Returns:
        {'images': [{'doc_id','doc_title','description','preview_url'}, ...]}
    """
    from .oss import oss_client

    images = []
    for r in image_results:
        entry = {
            'doc_id': r.get('doc_id'),
            'doc_title': r.get('doc_title', ''),
            'description': (r.get('description') or '')[:200],
            'preview_url': '',
        }
        oss_key = r.get('oss_key', '')
        if oss_key:
            # 签名预览 URL（不带 attachment，浏览器内联显示图片）
            # 传入 content_type 修正 OSS 上传时缺失的 MIME 类型
            file_name = r.get('file_name', '')
            content_type = _guess_image_content_type(file_name)
            entry['preview_url'] = oss_client.get_preview_url(
                oss_key, expires=3600, content_type=content_type,
            ) or ''
        images.append(entry)
    return {'images': images}


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
