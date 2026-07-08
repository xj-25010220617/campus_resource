"""
Celery 异步任务调度工具

- safe_delay: 安全地触发 Celery 任务
  - Celery + Redis 可用 → 正常异步分发
  - Celery 不可用 / Redis 未启动 → 自动降级为同步执行
- 用于在视图中触发异步任务而不写 try/except（遵循 CONVENTIONS.md 第 9 节）
"""
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def safe_delay(task, *args, **kwargs) -> Optional[Any]:
    """安全触发 Celery 异步任务（带同步降级）。

    Args:
        task: Celery 共享任务函数（@shared_task 装饰的）
        *args: 任务位置参数
        **kwargs: 任务关键字参数

    Returns:
        AsyncResult: Celery 入队成功时返回 Celery 任务句柄（含 task_id）
        None: 降级同步执行完毕时返回 None

    Note:
        - 触发失败不影响调用方主流程（上传、视图响应）
        - 优先尝试异步分发；连接失败时降级为同步执行
        - 降级时通过 logger.warning 输出，便于环境差异排查
    """
    try:
        return task.delay(*args, **kwargs)
    except Exception as e:
        logger.warning(
            'Celery 异步分发失败 (%s: %s)，降级为同步执行 task=%s',
            type(e).__name__, e, getattr(task, 'name', task),
        )
        try:
            # 降级同步执行：直接调用 task（兼容 bind=True 和普通任务）
            # 对 Celery 任务，task(*args) == task.run(*args) 且 bind=True 时
            # Python 方法绑定已自动处理 self，无需额外传 None
            return task(*args, **kwargs)
        except Exception as sync_e:
            logger.error(
                '同步降级执行失败 task=%s: %s',
                getattr(task, 'name', task), sync_e,
            )
            return None
