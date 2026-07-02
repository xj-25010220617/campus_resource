"""
Celery 异步任务调度工具

- safe_delay: 安全地触发 Celery 任务，Celery 不可用时记录日志并降级返回 None
- 用于在视图中触发异步任务而不写 try/except（遵循 CONVENTIONS.md 第 9 节）
"""
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def safe_delay(task, *args, **kwargs) -> Optional[Any]:
    """安全触发 Celery 异步任务。

    Args:
        task: Celery 共享任务函数（@shared_task 装饰的）
        *args: 任务位置参数
        **kwargs: 任务关键字参数

    Returns:
        AsyncResult: Celery 入队成功时返回 Celery 任务句柄（含 task_id）
        None: Celery 不可用或触发失败时返回 None（已记录日志）

    Note:
        - 触发失败不影响调用方主流程（上传、视图响应）
        - 日志通过 logger.error 输出，运维可通过 'Celery 任务触发失败' tag 检索
    """
    try:
        return task.delay(*args, **kwargs)
    except Exception as e:
        logger.error(
            f'Celery 任务触发失败 task={getattr(task, "name", task)}: {e}'
        )
        return None
