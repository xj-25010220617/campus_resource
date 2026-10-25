"""请求级链路追踪。

每次问答在入口分配一个 trace_id，用 contextvar 透传，贯穿
路由 → 解析 → 检索 → 兜底 → 生成 各阶段。所有阶段日志都带同一个
trace_id，排查单次问题时可直接 grep 该 ID 拿到完整链路，无需在
多份散日志里拼时间线。

用法：
    tid = tracing.new_trace_id()        # 入口分配
    tracing.trace_log('route', 'type=%s', route['type'])
    tracing.get_trace_id()              # SSE / 响应头中回传
"""
import logging
import time
import uuid
from contextvars import ContextVar

logger = logging.getLogger('tracing')

_trace_id: ContextVar[str] = ContextVar('trace_id', default='')
_t0: ContextVar[float] = ContextVar('trace_t0', default=0.0)


def new_trace_id() -> str:
    """分配新的 trace_id 并重置计时起点。"""
    tid = uuid.uuid4().hex[:12]
    _trace_id.set(tid)
    _t0.set(time.time())
    return tid


def get_trace_id() -> str:
    """取当前链路的 trace_id；未初始化时返回空串。"""
    return _trace_id.get()


def elapsed_ms() -> int:
    """从 trace 起点到现在的毫秒数。"""
    t0 = _t0.get()
    return int((time.time() - t0) * 1000) if t0 else 0


def trace_log(stage: str, msg: str, *args, level: int = logging.INFO) -> None:
    """输出带 trace_id 与相对耗时的单行日志。

    未分配 trace_id（如脚本直接调用检索）时输出 `trace=-`，
    保证同一套代码在后台任务/评测脚本中也能正常记录。
    """
    text = msg % args if args else msg
    logger.log(level, 'trace=%s stage=%s +%dms %s',
               get_trace_id() or '-', stage, elapsed_ms(), text)
