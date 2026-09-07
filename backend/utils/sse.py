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
    user_context: dict = None,
) -> dict:
    """构造来源事件，并在生成签名 URL 前完成文档授权校验。

    KnowledgeDoc 来源会返回文件元数据；网页、FAQ 等非文件来源只返回
    经过校验的来源 URL，不会把检索结果中的任意 OSS key 当作可信输入。
    """
    if not search_results:
        return {'references': {'count': 0, 'summary': '未找到相关参考资料'}}

    from .rag import RAG_REFERENCE_THRESHOLD, authorized_knowledge_docs

    # 引用阈值独立于向量融合分数，避免 0.7 权重把相关来源全部过滤掉。
    qualified = [
        result for result in search_results
        if result.get('score', 0) >= min(RAG_REFERENCE_THRESHOLD, 0.35)
    ]
    if not qualified:
        return {'references': {'count': 0, 'summary': '暂无高相关度参考资料'}}

    context = dict(user_context or {})
    context.setdefault('role', user_role or 'user')
    context.setdefault('user_id', user_id)
    doc_ids = {int(r['doc_id']) for r in qualified if r.get('doc_id')}
    docs_by_id = {}
    if doc_ids:
        docs_by_id = {
            doc.id: doc for doc in authorized_knowledge_docs(context).filter(
                id__in=doc_ids, status='indexed',
            )
        }

    # 普通用户只看到授权后的数量摘要。
    if user_role == 'user':
        count = sum(1 for result in qualified if result.get('doc_id') in docs_by_id)
        return {
            'references': {
                'count': count,
                'summary': f'参考了{count}份文档' if count else '暂无高相关度参考资料',
            }
        }

    from urllib.parse import urlparse
    from .oss import oss_client

    detail = []
    for result in qualified:
        doc_id = result.get('doc_id')
        doc = docs_by_id.get(int(doc_id)) if doc_id else None
        if doc is not None:
            entry = {
                'doc_id': doc.id,
                'doc_title': doc.title,
                'source_type': doc.source_type,
                'file_name': doc.file_name,
                'page': result.get('page', 0),
                'chunk_index': result.get('chunk_index'),
                'score': result.get('score', 0),
                'content_snippet': result.get('content', '')[:200],
                'source_url': doc.source_url or '',
            }
            if doc.oss_key:
                try:
                    entry['download_url'] = oss_client.get_download_url(
                        doc.oss_key, expires=300, file_name=doc.file_name,
                    ) or ''
                except Exception as exc:
                    logger.warning('生成文档下载链接失败 doc_id=%s: %s', doc.id, exc)
                    entry['download_url'] = ''
            detail.append(entry)
            continue

        # Non-file source: preserve it only as a safe web/labelled source.
        source_url = result.get('source_url') or ''
        parsed = urlparse(source_url)
        if source_url and parsed.scheme in ('http', 'https') and parsed.netloc:
            detail.append({
                'doc_id': None,
                'doc_title': result.get('doc_title', '外部网页'),
                'source_type': result.get('source_type') or 'web',
                'file_name': '',
                'page': result.get('page', 0),
                'score': result.get('score', 0),
                'content_snippet': result.get('content', '')[:200],
                'source_url': source_url,
                'download_url': '',
            })

    return {'references_detail': detail} if detail else {
        'references': {'count': 0, 'summary': '暂无可展示的授权参考资料'},
    }


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
            # 签名预览 URL（不带 attachment / content-type 覆盖，浏览器内联显示图片）。
            # 注意：阿里云 OSS 不支持 response-content-type 参数（400 InvalidRequest），
            # 图片 Content-Type 已在上传时写对（utils.oss.guess_content_type），<img> 亦会嗅探。
            entry['preview_url'] = oss_client.get_preview_url(
                oss_key, expires=3600,
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
