"""
SSE 流式代理包装器
将 LLM stream 转发为 Server-Sent Events 格式。
客户端通过 EventSource 接收逐字输出。
"""
import json
import logging

from django.http import StreamingHttpResponse

logger = logging.getLogger(__name__)


def sse_response(generator, extra_events: dict = None):
    """
    创建 SSE StreamingHttpResponse。
    参数:
        generator: Python 生成器，逐 token yield 字符串
        extra_events: 额外的 SSE 事件（如 start/done/references）
    """

    def event_stream():
        # 1. 发送 start 事件
        if extra_events and extra_events.get('start'):
            yield _format_sse('start', extra_events['start'])

        # 2. 流式发送 token
        for token in generator:
            if token:
                yield _format_sse('token', {'content': token})

        # 3. 发送 references
        if extra_events and extra_events.get('references'):
            yield _format_sse('references', extra_events['references'])

        # 4. 发送 suggested_questions
        if extra_events and extra_events.get('suggested'):
            yield _format_sse('suggested', extra_events['suggested'])

        # 5. 发送 confidence
        if extra_events and extra_events.get('confidence'):
            yield _format_sse('confidence', extra_events['confidence'])

        # 6. 发送 done 事件
        done_data = {}
        if extra_events and extra_events.get('done'):
            done_data = extra_events['done']
        yield _format_sse('done', done_data)

    response = StreamingHttpResponse(
        event_stream(),
        content_type='text/event-stream',
    )
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'  # 禁用 Nginx 缓冲
    response['Access-Control-Allow-Origin'] = '*'
    return response


def _format_sse(event: str, data) -> str:
    """格式化一条 SSE 事件"""
    if isinstance(data, dict):
        data = json.dumps(data, ensure_ascii=False)
    elif not isinstance(data, str):
        data = str(data)
    return f'event: {event}\ndata: {data}\n\n'


def build_chat_events(
    conversation_id: int,
    message_id: int,
    is_authorized: bool = False,
) -> dict:
    """
    构造 SSE 事件的 extra payload。
    参数:
        conversation_id: 会话 ID
        message_id: 消息 ID
        is_authorized: 是否为授权用户（admin/super_admin）
    返回:
        extra_events dict
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


def build_error_sse(message: str, code: str = 'error'):
    """构造错误 SSE 响应"""
    def generator():
        yield _format_sse('error', {'message': message, 'code': code})

    response = StreamingHttpResponse(
        generator(),
        content_type='text/event-stream',
    )
    response['Cache-Control'] = 'no-cache'
    return response
