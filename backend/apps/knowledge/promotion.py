"""P1-2D STEP 4-B：隔离区 → 正式区的**业务层** promotion workflow。

与 `utils.oss.OSSClient.promote_object()` 的分工
------------------------------------------------
    OSS primitive : promote_object()   —— 一次 copy + HEAD 校验，纯 OSS 操作，
                                          不碰数据库、不改任何业务状态
    业务 workflow : promote_document() —— 本模块。审核判定 → 生成正式区 Key →
                                          OSS 提升 → CAS 推进 storage_state →
                                          更新 KnowledgeDoc.oss_key → 安排索引

**二者不可互相替代**：primitive 成功不代表业务上已"提升完成"（DB 还没动），
业务 CAS 成功也不代表对象真的存在（必须先经过 primitive 的 verify）。

事务边界（本模块最重要的约束）
------------------------------
OSS 操作**不可回滚**，因此绝不能把它包进 `transaction.atomic()` 假装成一个原子
事务。执行顺序被刻意固定为：

    1. 审核白名单判定            （DB 读，fail-closed）
    2. CAS 抢占 promoting        （DB 写，排他，崩溃后可超时重认领）
    3. OSS copy + verify         （**事务之外**，幂等，可安全重试）
    4. CAS promoting → promoted  （DB 写，排他）
    5. 更新 KnowledgeDoc.oss_key （与第 4 步同事务）
    6. transaction.on_commit()   （提交后才派发索引）

这样任意一步失败/崩溃都不会产生"DB 说已提升、对象其实不存在"的顺序错误：
`storage_state=promoted` **只可能**在 OSS 校验通过之后被写入。

幂等性
------
- copy 幂等（同 src/dst 覆盖写），重复执行结果一致
- 抢占 CAS 排他，`promoted` 是吸收态：已是 promoted 时直接返回，不再 copy
- worker 在 `promoting` 崩溃 → 记录 `updated_at` 会变陈旧，超过
  `PROMOTION_CLAIM_TIMEOUT_SECONDS` 后可被重新认领（复用现有字段，不新增）
"""
import logging
import time
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from utils.oss import OSSOperationError, build_promoted_key, oss_client

from .moderation import (
    STORAGE_STATE_PROMOTED,
    STORAGE_STATE_PROMOTING,
    STORAGE_STATE_PROMOTION_FAILED,
    STORAGE_STATE_QUARANTINE,
    is_indexable_moderation_state,
)

logger = logging.getLogger(__name__)


# 抢占 `promoting` 的租约时长（秒）。超过此时长仍是 promoting，视为持有者已崩溃，
# 允许其他 worker 重新认领——否则一次崩溃会让该文档永久卡在"提升中"。
DEFAULT_PROMOTION_CLAIM_TIMEOUT_SECONDS = 900


def promotion_claim_timeout_seconds() -> int:
    """promoting 租约时长（秒），全部走配置，不在代码里散落字面量。"""
    value = getattr(
        settings, 'PROMOTION_CLAIM_TIMEOUT_SECONDS',
        DEFAULT_PROMOTION_CLAIM_TIMEOUT_SECONDS,
    )
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PROMOTION_CLAIM_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_PROMOTION_CLAIM_TIMEOUT_SECONDS


# 可被抢占为 promoting 的既有存储状态。
# `promotion_failed` 可重试；`promoting` 仅在租约过期（崩溃恢复）时可抢。
_CLAIMABLE_ACTIVE_STATES = (STORAGE_STATE_QUARANTINE, STORAGE_STATE_PROMOTION_FAILED)


class PromotionResult:
    """一次 `promote_document()` 调用的结果（**不是**数据库记录）。"""

    __slots__ = ('doc_id', 'status', 'promoted_key', 'reason', 'duration_ms')

    #: 状态取值（供调用方与测试引用，避免比较魔法字符串）
    STATUS_PROMOTED = 'promoted'          # 本次真的完成了提升
    STATUS_ALREADY = 'already_promoted'   # 早已提升，幂等短路
    STATUS_SKIPPED = 'skipped'            # 未通过审核门 / 无记录 / 无 Key
    STATUS_IN_PROGRESS = 'in_progress'    # 被其他 worker 抢占中
    STATUS_FAILED = 'failed'              # OSS 或校验失败（已落 promotion_failed）

    def __init__(self, doc_id=None, status='', promoted_key='', reason='', duration_ms=0):
        self.doc_id = doc_id
        self.status = status
        self.promoted_key = promoted_key
        self.reason = reason
        self.duration_ms = duration_ms

    @property
    def did_promote(self) -> bool:
        return self.status == self.STATUS_PROMOTED

    def __repr__(self):
        return (f'<PromotionResult doc={self.doc_id} {self.status} '
                f'key={self.promoted_key!r} reason={self.reason!r}>')


def _ContentModerationModel():
    from .models import ContentModeration
    return ContentModeration


def _KnowledgeDocModel():
    from .models import KnowledgeDoc
    return KnowledgeDoc


def _schedule_index(doc_id: int) -> None:
    """派发索引任务（**只允许**在事务提交后调用，见模块 docstring 第 6 步）。"""
    from utils.celery_dispatcher import safe_delay

    from .tasks import index_document_task

    safe_delay(index_document_task, doc_id)


def promote_document(doc_id: int, *, worker_id: str = '',
                     schedule_index: bool = True) -> PromotionResult:
    """把已通过审核的隔离区对象提升到正式区，并在成功后安排索引。

    Args:
        doc_id: KnowledgeDoc 主键
        worker_id: 执行者标识，仅用于日志
        schedule_index: 是否在成功后用 `transaction.on_commit` 派发索引任务。
            测试与"只补索引"场景可传 False。

    Returns:
        PromotionResult。`did_promote` 为 True 表示**本次**完成了
        `quarantine → knowledge` 的完整流程。

    Note:
        调用方**必须**先跑 `moderate_document()`；本函数只读审核结论，从不修改
        `ContentModeration.state`（审核结论与存储状态是两件事，见 models.py
        ContentModeration 的类 docstring）。
    """
    started = time.time()
    ContentModeration = _ContentModerationModel()
    KnowledgeDoc = _KnowledgeDocModel()

    def _result(status, promoted_key='', reason=''):
        return PromotionResult(
            doc_id=doc_id, status=status, promoted_key=promoted_key,
            reason=reason, duration_ms=int((time.time() - started) * 1000),
        )

    try:
        doc = KnowledgeDoc.objects.get(id=doc_id)
    except KnowledgeDoc.DoesNotExist:
        logger.warning('promotion：doc_id=%s 不存在', doc_id)
        return _result(PromotionResult.STATUS_SKIPPED, reason='doc_not_found')

    moderation = ContentModeration.objects.filter(doc=doc).first()
    if moderation is None:
        return _result(PromotionResult.STATUS_SKIPPED, reason='no_moderation')

    # ---- 1. 审核白名单（fail-closed：未知状态一律拒绝） ----
    if not is_indexable_moderation_state(moderation.state):
        return _result(
            PromotionResult.STATUS_SKIPPED,
            reason=f'not_approved:{moderation.state}',
        )

    # ---- 2. 已是 promoted：幂等短路，绝不重复 copy ----
    if moderation.storage_state == STORAGE_STATE_PROMOTED:
        return _result(
            PromotionResult.STATUS_ALREADY,
            promoted_key=moderation.promoted_key or doc.oss_key,
            reason='already_promoted',
        )

    # ---- 3. 源 Key 必须来自隔离区 ----
    # 新上传的文档 oss_key 就是隔离区 Key（moderation 从不改写它）；
    # quarantine_key 字段在首次提升时补齐，供审计与重试使用。
    src_key = (moderation.quarantine_key or doc.oss_key or '').strip()
    if not src_key:
        return _result(PromotionResult.STATUS_SKIPPED, reason='no_quarantine_key')
    try:
        dst_key = build_promoted_key(src_key)
    except OSSOperationError as e:
        logger.error('promotion：源 Key 非法 doc_id=%s key=%r: %s', doc_id, src_key, e)
        return _result(PromotionResult.STATUS_SKIPPED, reason=f'invalid_source_key: {e}')

    # ---- 4. CAS 抢占 promoting（排他；崩溃后超时可重认领） ----
    now = timezone.now()
    stale_before = now - timedelta(seconds=promotion_claim_timeout_seconds())

    claimed = ContentModeration.objects.filter(pk=moderation.pk).filter(
        Q(storage_state__in=_CLAIMABLE_ACTIVE_STATES)
        | Q(storage_state=STORAGE_STATE_PROMOTING, updated_at__lt=stale_before)
    ).update(
        storage_state=STORAGE_STATE_PROMOTING,
        promotion_error='',
        quarantine_key=src_key,
        updated_at=now,
    )
    if not claimed:
        logger.info(
            'promotion：doc_id=%s 已被其他 worker 抢占中（storage_state=%s）',
            doc_id, moderation.storage_state,
        )
        return _result(PromotionResult.STATUS_IN_PROGRESS, reason='claim_lost')

    # ---- 5. OSS copy + verify（事务之外：OSS 不可回滚） ----
    try:
        oss_result = oss_client.promote_object(src_key, dst_key)
    except OSSOperationError as e:
        logger.error(
            'promotion：OSS 提升失败 doc_id=%s %s -> %s: %s',
            doc_id, src_key, dst_key, e,
        )
        ContentModeration.objects.filter(
            pk=moderation.pk, storage_state=STORAGE_STATE_PROMOTING,
        ).update(
            storage_state=STORAGE_STATE_PROMOTION_FAILED,
            promotion_error=str(e)[:2000],
            promotion_retry=F('promotion_retry') + 1,
            updated_at=timezone.now(),
        )
        return _result(PromotionResult.STATUS_FAILED, reason=str(e))
    except Exception as e:  # 兜底：任何非预期异常同样不得放行索引
        logger.exception('promotion：OSS 提升异常 doc_id=%s: %s', doc_id, e)
        ContentModeration.objects.filter(
            pk=moderation.pk, storage_state=STORAGE_STATE_PROMOTING,
        ).update(
            storage_state=STORAGE_STATE_PROMOTION_FAILED,
            promotion_error=f'{type(e).__name__}: {e}'[:2000],
            promotion_retry=F('promotion_retry') + 1,
            updated_at=timezone.now(),
        )
        return _result(PromotionResult.STATUS_FAILED, reason=str(e))

    # 到这里 OSS 侧已确认：目标对象存在，且大小与源一致。
    verified_key = oss_result.get('dst_key') or dst_key
    if not oss_result.get('verified') or verified_key != dst_key:
        # primitive 的返回与预期不一致：不得进入 promoted
        ContentModeration.objects.filter(
            pk=moderation.pk, storage_state=STORAGE_STATE_PROMOTING,
        ).update(
            storage_state=STORAGE_STATE_PROMOTION_FAILED,
            promotion_error=f'提升校验结果异常: {oss_result!r}'[:2000],
            promotion_retry=F('promotion_retry') + 1,
            updated_at=timezone.now(),
        )
        return _result(PromotionResult.STATUS_FAILED, reason='verify_mismatch')

    # ---- 6. CAS promoting → promoted，并同步 KnowledgeDoc ----
    with transaction.atomic():
        updated = ContentModeration.objects.filter(
            pk=moderation.pk, storage_state=STORAGE_STATE_PROMOTING,
        ).update(
            storage_state=STORAGE_STATE_PROMOTED,
            promoted_key=verified_key,
            promotion_error='',
            promotion_retry=F('promotion_retry') + 1,
            updated_at=timezone.now(),
        )
        if not updated:
            # 并发下被抢先或被判失败：本次不推进，绝不派发索引
            logger.info('promotion：doc_id=%s CAS 未命中，放弃本次推进', doc_id)
            return _result(PromotionResult.STATUS_IN_PROGRESS, reason='cas_lost')

        # KnowledgeDoc.oss_key 只在**对象已成功 copy + verify 之后**才改。
        # 它必须代表"已存在并验证过的正式区对象"，不能作为 promotion 的预期值。
        KnowledgeDoc.objects.filter(pk=doc.pk).update(oss_key=verified_key)

        if schedule_index:
            # 提交后才派发：safe_delay 在 Celery 不可用时是**同步执行**，
            # 若在提交前跑，索引任务会读到旧的 oss_key 而误判不可索引。
            transaction.on_commit(lambda: _schedule_index(doc.pk))

    logger.info(
        'promotion 完成 doc_id=%s worker=%s %s -> %s',
        doc_id, worker_id or '-', src_key, verified_key,
    )
    return _result(PromotionResult.STATUS_PROMOTED, promoted_key=verified_key)
