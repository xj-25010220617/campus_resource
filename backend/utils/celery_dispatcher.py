"""
Celery 异步任务调度工具

两种派发语义，**按调用方的安全需求选择**，不要混用：

- `safe_delay`：安全地触发 Celery 任务
  - Celery + Redis 可用 → 正常异步分发
  - Celery 不可用 / Redis 未启动 → 自动降级为**同步执行**
  - 适用于"同步执行也正确、只是慢一点"的任务（新鲜度提取、缓存刷新等）
- `dispatch_async`：**只入队，绝不降级**
  - Celery + Redis 可用 → 正常异步分发，返回 AsyncResult
  - Celery / broker 不可用 → 抛 `DispatchUnavailable`，**不执行任务体**
  - 适用于"同步执行会造成安全事故或阻塞请求"的任务（上传链路的
    审核 → 提升 → 索引编排，见 apps/knowledge/upload_service.py）

- 用于在视图中触发异步任务而不写 try/except（遵循 CONVENTIONS.md 第 9 节）
"""
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class DispatchUnavailable(RuntimeError):
    """异步队列不可用，且该调用方**不允许**降级为同步执行。

    调用方应把它当作"任务没有发生"处理：不要假装成功，也不要在本地
    把任务体跑一遍（那正是 P1-2 Phase 1-2 P-4 要消除的行为）。
    """


def dispatch_async(task, *args, **kwargs) -> Any:
    """把任务**只**投递到异步队列；队列不可用时抛 `DispatchUnavailable`。

    与 `safe_delay()` 的关键区别：本函数**没有同步降级分支**。上传链路里
    整条 moderation → promotion → index 是一次几百秒量级的操作，一旦在
    HTTP 请求线程内同步执行，就会撞穿 nginx 的 `proxy_read_timeout`，
    并在客户端重试时产生重复文档与重复 Embedding 计费（见审计报告 P-4）。

    因此调用方拿到 `DispatchUnavailable` 时应保持"未审核"状态
    （对象留在 quarantine/，KnowledgeDoc 保持未索引），等待下一轮调度，
    而不是就地执行。

    Args:
        task: Celery 共享任务函数（@shared_task 装饰的）
        *args: 任务位置参数
        **kwargs: 任务关键字参数

    Returns:
        AsyncResult: Celery 入队成功时的任务句柄（含 task_id）

    Raises:
        DispatchUnavailable: Celery 未安装 / broker 不可达 / 入队失败
    """
    # `utils.shared_task_compat` 在未安装 Celery 时会把 `delay` 指向一个
    # **就地同步执行**的 runner。那种情况下 `task.delay()` 不会抛异常，却把
    # 任务体在本进程跑完了 —— 正是本函数要杜绝的行为。因此先识别该标记，
    # 直接判定为"队列不可用"，绝不让它落到下面的 try 里被当作成功。
    if getattr(getattr(task, 'delay', None), '_inline_fallback', False):
        logger.error(
            '任务 %s 处于 Celery 降级形态（delay 会就地同步执行），'
            '按 fail-closed 处理：不派发、不执行',
            getattr(task, 'name', task),
        )
        raise DispatchUnavailable(
            f'未安装 Celery，异步队列不可用：{getattr(task, "name", task)}'
        )

    try:
        return task.delay(*args, **kwargs)
    except Exception as e:
        logger.error(
            '异步派发失败，按 fail-closed 处理（不降级同步执行）'
            ' task=%s: %s: %s',
            getattr(task, 'name', task), type(e).__name__, e,
        )
        raise DispatchUnavailable(
            f'异步队列不可用，任务未派发：{getattr(task, "name", task)}'
        ) from e
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
