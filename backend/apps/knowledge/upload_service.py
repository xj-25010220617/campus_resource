"""P1-2D STEP 4-A：生产上传链路统一进入隔离区的编排层。

本模块是 5 个生产上传入口（`FileUploadView` / `BatchFileUploadView` /
`TextUploadView` / `UploadSTSView`+`UploadCallbackView` / 分片上传）**共用**的
上传编排。职责只有三段：

    创建 UploadIntent（key 由服务端生成，落在 quarantine/）
        ↓
    把对象写进 intent.quarantine_key
        ↓
    把 intent 推进到 completed

**本模块刻意不做的事**：

- 不直接调用 `moderate_document()`：内容审核由 `moderate_and_promote_task`
  在**事务提交之后**执行（见 `schedule_moderation()`），本模块只负责"安排"。
- 不做 promotion（`promote_object()`）：入口阶段对象**始终留在 quarantine/**，
  提升由 `promotion.promote_document()` 在审核通过后执行。
- 不触发 `index_document_task` / `extract_freshness_task`
- 不创建 `KnowledgeDoc`（那是各入口自己的业务，含各自的元数据与关键词）

P1-2D STEP 4-B 的接入点只有一个：各入口在**建完 KnowledgeDoc 之后**调用
`schedule_moderation(doc.id)`。它用 `transaction.on_commit()` 派发，因此在
`ChunkMergeView` 这类把建文档包在事务里的入口中也不会提前触发。

分层的理由：视图层只负责"解析请求 + 组装业务字段 + 返回响应"，安全关键的
"key 从哪来、怎么校验、状态怎么变"全部收在这里，避免 5 个入口各写一份
if/elif 而出现某一处漏改。
"""

import logging

from utils.oss import OSSOperationError, guess_content_type, oss_client

from .upload_intent import (
    REASON_EXPIRED,
    REASON_INVALID_ARGUMENT,
    REASON_INVALID_STATE,
    REASON_NOT_FOUND,
    REASON_SIZE_MISMATCH,
    UploadIntentError,
    complete_upload_intent,
    create_upload_intent,
    get_upload_intent,
    is_quarantine_key,
)

logger = logging.getLogger(__name__)


# 与 upload_intent.UploadIntentError.reason 一一对应的 HTTP 语义。
# 视图层用它把 service 异常翻译成响应，自身不做判断逻辑。
_ERROR_STATUS = {
    REASON_NOT_FOUND: 404,
    REASON_EXPIRED: 409,
    REASON_INVALID_STATE: 409,
    REASON_SIZE_MISMATCH: 400,
    REASON_INVALID_ARGUMENT: 400,
}

# 默认给客户端看的原因说明（不泄露 intent 是否存在）
_ERROR_DETAIL = {
    REASON_NOT_FOUND: '上传凭证不存在或不属于当前用户',
    REASON_EXPIRED: '上传凭证已过期，请重新发起上传',
    REASON_INVALID_STATE: '上传凭证状态不允许该操作',
    REASON_SIZE_MISMATCH: '文件大小与上传凭证记录不一致',
    REASON_INVALID_ARGUMENT: '上传参数与上传凭证不一致',
}


def intent_error_status(exc: UploadIntentError) -> int:
    """UploadIntentError → HTTP 状态码。"""
    return _ERROR_STATUS.get(exc.reason, 400)


def intent_error_detail(exc: UploadIntentError) -> str:
    """UploadIntentError → 可直接返回给客户端的说明。"""
    return _ERROR_DETAIL.get(exc.reason, '上传凭证校验失败')


# ---------------------------------------------------------------------------
# 1. 开始上传：创建意图
# ---------------------------------------------------------------------------

def begin_upload(user, file_name: str, *, content_type: str = '',
                 expected_size=None):
    """为一次上传创建隔离区上传凭证。

    quarantine key 由 `create_upload_intent()` 内部调用 P1-2A 的
    `build_quarantine_key()` 生成，**调用方无法指定**。
    """
    return create_upload_intent(
        user, file_name,
        content_type=content_type,
        expected_size=expected_size,
    )


# ---------------------------------------------------------------------------
# 2. 落盘：把字节写进隔离区
# ---------------------------------------------------------------------------

def quarantine_oss_url(oss_key: str) -> str:
    """构造对象的展示用 URL（不带签名）。

    与既有实现保持一致：桶 ACL 为 private，未签名 URL 无法匿名访问，
    这里只是把对象的最终位置记录下来，不构成访问授权。
    """
    return f'https://{oss_client.bucket_name}.{oss_client.endpoint}/{oss_key}'


def put_quarantine_object(intent, payload: bytes, *, content_type: str = None):
    """把字节写入该 intent 的隔离区 Key，成功后把意图推进到 `completed`。

    Args:
        intent: UploadIntent 实例
        payload: 文件字节
        content_type: 写入对象时使用的 Content-Type；为 None 时按扩展名推断

    Returns:
        dict(oss_key, oss_url, size)

    Raises:
        OSSOperationError: 桶不可用 / 写入失败（意图保持 issued，等待过期）
        UploadIntentError: 意图状态或大小校验失败
    """
    if not is_quarantine_key(intent.quarantine_key):
        # 防御性检查：intent 的 key 必须是隔离区的。正常情况下由
        # create_upload_intent 保证，这里再拦一道，避免将来有人改错生成逻辑
        # 之后，新上传又悄悄写回正式区。
        raise UploadIntentError(
            REASON_INVALID_ARGUMENT, '上传意图的 Key 不在隔离区命名空间内',
        )

    bucket = oss_client.bucket
    if not bucket:
        raise OSSOperationError('OSS 桶未初始化，请检查 OSS 配置')

    headers = {'Content-Type': content_type or guess_content_type(intent.original_filename)}
    bucket.put_object(intent.quarantine_key, payload, headers=headers)

    size = len(payload)
    # 写入成功后才有资格推进意图状态；失败则意图停在 issued，等过期回收
    intent = complete_upload_intent(
        intent.user, intent.pk,
        actual_size=size,
        quarantine_key=intent.quarantine_key,
    )
    logger.info(
        '新上传已进入隔离区 intent=%s key=%s size=%s（等待审核，未索引）',
        intent.pk, intent.quarantine_key, size,
    )
    return {
        'oss_key': intent.quarantine_key,
        'oss_url': quarantine_oss_url(intent.quarantine_key),
        'size': size,
        'intent': intent,
    }


# ---------------------------------------------------------------------------
# 3. 直传回调：校验并完成意图
# ---------------------------------------------------------------------------

def finalize_callback_intent(user, intent_id, *, oss_key: str,
                             actual_size: int):
    """STS 直传回调专用：把客户端回传的对象与 intent 精确比对后完成意图。

    这是 STEP 4-A 最关键的归属校验——它取代了"只能证明对象存在"的旧校验：

    - intent 必须是**当前用户**的（`get_upload_intent` 内部强制）
    - `oss_key` 必须与 intent 上记录的 quarantine key **精确相等**
      （不是前缀匹配、不是 scope 匹配）
    - 对象大小必须与声明一致（`actual_size` 来自 OSS `head_object`）

    Returns:
        UploadIntent（status=completed）

    Raises:
        UploadIntentError: 归属 / 状态 / 过期 / key 或大小不匹配
    """
    # 先取一次（校验归属 + 过期 + 拿到权威 key），再交给 complete 做原子 CAS
    intent = get_upload_intent(user, intent_id)
    if oss_key != intent.quarantine_key:
        logger.warning(
            '上传回调 Key 与意图不匹配: intent=%s expected=%s got=%s user=%s',
            intent_id, intent.quarantine_key, oss_key, getattr(user, 'pk', None),
        )
        raise UploadIntentError(
            REASON_INVALID_ARGUMENT,
            'OSS Key 与上传凭证记录的目标对象不一致',
        )

    return complete_upload_intent(
        user, intent.pk,
        actual_size=actual_size,
        quarantine_key=oss_key,
    )


# ---------------------------------------------------------------------------
# 4. 审核编排：5 个生产上传入口**唯一**的接入点
# ---------------------------------------------------------------------------

def schedule_moderation(doc_id: int) -> bool:
    """安排统一审核编排任务（`moderate_and_promote_task`）。

    调用时机：各入口**创建完 KnowledgeDoc 之后**。这是 STEP 4-B 唯一的接入点——
    5 个入口都调这一个函数，安全判定（能不能提升、能不能索引）全部在任务侧完成，
    入口不需要、也不允许自己判断审核结论。

    为什么必须用 `transaction.on_commit()`
    ------------------------------------
    `ChunkMergeView` 把建文档包在 `transaction.atomic()` 里。若在提交前派发，
    异步 worker 可能读到一个尚未提交（甚至可能被回滚）的 KnowledgeDoc，从而
    出现"审核了不存在的文档"或"文档回滚了但审核记录/提升结果留下了"。

    `on_commit` 在无外层事务时立即执行，因此对不使用事务的入口同样生效。

    为什么用 `dispatch_async` 而不是 `safe_delay`（审计报告 P-4）
    ---------------------------------------------------------
    `safe_delay` 在 Celery 不可用时会**降级为同步执行**。放在上传链路里，
    这意味着 HTTP 请求线程要跑完整条 moderation → promotion → index
    （数百秒量级），必然撞穿 nginx 的 `proxy_read_timeout`，并在客户端重试时
    产出重复文档与重复 Embedding 计费。

    因此改走 `dispatch_async`：**只入队，绝不降级**。队列不可用时抛
    `DispatchUnavailable`，这里捕获后**只记 ERROR 日志、不重抛**——

    - 上传本身是成功的（对象已落 quarantine/，KnowledgeDoc 已建），
      不能因为排队失败就把整个请求判为 500；
    - 绝不就地跑一遍任务体（那正是 P-4 要消除的行为）；
    - KnowledgeDoc 停在未审核状态、对象留在 quarantine/，由
      `retry_failed_moderations_task` 的补扫分支（见 tasks.py）在队列恢复后
      重新派发。**队列不可用期间上传的内容不会被自动放行**（fail-closed）。

    Returns:
        True  —— 已成功投递到异步队列
        False —— 队列不可用，本次派发未发生（文档保持未审核，等待补扫）
    """
    from django.db import transaction

    from utils.celery_dispatcher import DispatchUnavailable, dispatch_async

    from .tasks import moderate_and_promote_task

    # on_commit 在无外层事务时**立即执行**，因此这个列表通常能拿到真实结果；
    # 在事务内则等提交后才执行，此时返回 True 表示"已安排待提交"。
    outcome: list = []

    def _dispatch() -> None:
        try:
            dispatch_async(moderate_and_promote_task, doc_id)
        except DispatchUnavailable as e:
            # 故意不重抛：见 docstring。对象仍在 quarantine/、文档仍未审核，
            # 安全后果是"晚一点审核"，而不是"绕开审核"。
            logger.error(
                '审核任务入队失败，doc_id=%s 保持未审核（对象仍在 quarantine/，'
                '等待补扫重派）：%s',
                doc_id, e,
            )
            outcome.append(False)
            return
        logger.info('审核任务已入队 doc_id=%s', doc_id)
        outcome.append(True)

    transaction.on_commit(_dispatch)
    return outcome[0] if outcome else True
