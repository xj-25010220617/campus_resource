"""
Celery 装饰器兼容层

- 已安装 celery：等价于 @shared_task，行为完全一致
- 未安装 celery（本地开发环境）：装饰后的函数保留 .delay() / .apply_async()，
  调用时直接同步执行并打 warning 日志。视图层 / 上传链路不再因为缺 celery 而 500。

注意：
- Celery 的 @shared_task(bind=True) 会把 task 实例作为 self 注入；
  降级路径里没有 self，按函数签名自动补 None 占位，保证源码无需改动。
- 任务体里若调用了 self.retry(...)，降级路径会捕获 AttributeError 并仅记日志（不抛），
  避免在本地同步执行时出现无关的二次异常；其他异常透传给调用方。
- 业务异常（ValueError / RuntimeError 等）不会被吞，调用方能正常感知。

用法：
    from utils.shared_task_compat import shared_task

    @shared_task(bind=True, max_retries=2, default_retry_delay=30)
    def my_task(self, doc_id):
        ...
"""
# 1. 标准库
import inspect
import logging
from functools import wraps

logger = logging.getLogger(__name__)

# 2. Django 框架
# (本模块不依赖 Django 框架)

# 3. 第三方包
try:
    from celery import shared_task as _celery_shared_task  # noqa: F401
except ImportError:
    _celery_shared_task = None  # type: ignore[assignment]

# 4. 本地模块
# (本模块为基础设施，不依赖其他本地模块)


if _celery_shared_task is not None:
    shared_task = _celery_shared_task
else:

    def shared_task(*dargs, **dkwargs):
        """无 celery 时的 @shared_task 兼容实现。"""

        def _decorator(fn):
            # 检测函数第一个参数是不是 self/cls（bound task 的特征）
            try:
                params = list(inspect.signature(fn).parameters.values())
                needs_self = bool(params) and params[0].name in ('self', 'cls')
            except (TypeError, ValueError):
                needs_self = False

            @wraps(fn)
            def _inline_runner(*args, **kwargs):
                logger.warning(
                    '[fallback-sync] Celery 未安装，%s 改为同步执行 args=%s kwargs=%s',
                    getattr(fn, '__name__', fn), args, kwargs,
                )
                try:
                    if needs_self:
                        return fn(None, *args, **kwargs)
                    return fn(*args, **kwargs)
                except AttributeError as e:
                    # 降级模式下 self 是 None，self.retry / self.update_state
                    # 等 celery 专属调用会抛 AttributeError；这里统一降为日志
                    if 'NoneType' in str(e):
                        logger.error(
                            '[fallback-sync] 任务 %s 内调用了 celery 专属 API (%s)，'
                            '降级模式下忽略',
                            getattr(fn, '__name__', fn), e,
                        )
                        return None
                    raise

            # 标记：`delay` 不是真正的队列投递，而是**就地同步执行**。
            # `celery_dispatcher.dispatch_async()` 靠这个标记判断"任务是否
            # 真的被异步派发了"——否则在没有 Celery 的环境里，`task.delay()`
            # 会静默地同步跑完整条审核链，P-4 的解耦就形同虚设。
            _inline_runner._inline_fallback = True  # type: ignore[attr-defined]

            fn.delay = _inline_runner       # type: ignore[attr-defined]
            fn.apply_async = _inline_runner  # type: ignore[attr-defined]
            return fn

        # 兼容 @shared_task  和  @shared_task(bind=True, ...)  两种用法
        if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
            return _decorator(dargs[0])
        return _decorator
