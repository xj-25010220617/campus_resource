"""人工确认放行（`confirmed_unchecked`）的**唯一**写入方。

背景
----
`ContentModeration.state = 'confirmed_unchecked'` 在 P1-2A 就存在于白名单与
`TERMINAL_MODERATION_STATES` 中，但直到 Phase 1-2 之前**没有任何写入方**：
没有 admin action、没有 API、没有 management command、没有脚本。后果是
ClamAV（或任一必需检测器）不可用时，全部新上传停在 `unavailable`，运维
**没有任何手段**人工放行 —— 知识库永久停止增长（见生产审计报告 P-2）。

本模块提供那条唯一、受控、可审计的写入路径。

安全不变量（与 ContentModeration 模型 docstring 第 3 条一致）
-----------------------------------------------------------
人工放行**不是**"绕过审核"，而是把一次显式的人工判断**变成可追溯的事实**：

1. 只有管理员可以执行（`apps.admin_panel.permissions.ADMIN_ROLES`）。
2. 必须显式指定 KnowledgeDoc，且该文档必须真实存在于本系统。
3. 只允许从 `unavailable` 进入 —— 这是模型 docstring 写死的前置条件：
   `rejected`（已判定有害）永远不能靠人工确认翻案。
4. 对象必须**当前存在于隔离区**（`quarantine/`）且能被 OSS 证实存在。
   正式区（`knowledge/`）的历史对象没有 ContentModeration，**不属于**本
   模块的处理范围（历史数据迁移是独立阶段，见审计报告 P-8）。
5. `confirmed_by` / `confirmed_at` / `confirmation_reason` 三者必须同时写入，
   `confirmation_reason` 非空。
6. **不绕过 promotion**：确认只把状态推进到 `confirmed_unchecked`，随后派发
   的仍是既有编排 `moderate_and_promote_task` —— 对象照样要经过
   `promote_document()` 的 OSS 复制与校验才会出现在 `knowledge/`。

为什么可以直接复用 `moderate_and_promote_task`
---------------------------------------------
`moderate_document()` 在 `state in TERMINAL_MODERATION_STATES` 且 `force=False`
时直接 skip，而 `confirmed_unchecked` **就在** `TERMINAL_MODERATION_STATES`
里。因此编排任务的第一步不会重跑检测链、不会把人工结论覆盖回 `unavailable`，
而是直接进入 promotion。（这一点是本模块成立的前提，`tests` 中有断言固定它，
一旦 `TERMINAL_MODERATION_STATES` 被改动导致该前提失效，测试会失败。）
"""

import logging

from django.db import transaction
from django.utils import timezone

from utils.oss import oss_client

logger = logging.getLogger(__name__)


# 人工确认只能在**这个**审核状态下发生。
# 只允许 unavailable：它是"没能得出结论"，人工兜底是合理的；
# rejected / error / approved 要么已有结论、要么已被判定有害，都不该被本入口覆盖。
CONFIRMABLE_MODERATION_STATES = frozenset({'unavailable'})

# 允许执行人工确认的角色。复用 admin_panel 既有定义，不另立一套。
# 延迟 import：避免 apps.knowledge 在 AppRegistry 就绪前反向依赖 admin_panel。
def _admin_roles():
    from apps.admin_panel.permissions import ADMIN_ROLES
    return ADMIN_ROLES


class ConfirmationError(Exception):
    """人工确认失败。`reason` 是稳定的机器可读标识，`detail` 面向操作者。"""

    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def can_confirm(actor) -> bool:
    """actor 是否有资格执行人工放行。"""
    if actor is None or not getattr(actor, 'is_authenticated', False):
        return False
    return getattr(actor, 'role', None) in _admin_roles()


def _assert_actor(actor) -> None:
    if actor is None or not getattr(actor, 'is_authenticated', False):
        raise ConfirmationError('not_authenticated', '未登录，不能执行人工确认')
    if not can_confirm(actor):
        raise ConfirmationError(
            'not_admin',
            '只有管理员可以执行人工确认放行（当前角色：%s）'
            % getattr(actor, 'role', 'unknown'),
        )


def _canonical_quarantine_key(doc, moderation) -> str:
    """取该文档当前应当存在的隔离区 Key（以 ContentModeration 记录为准）。"""
    return str(moderation.quarantine_key or doc.oss_key or '').strip()


def _verify_quarantine_object(key: str) -> None:
    """确认隔离区对象当前真实存在。无法验证时一律拒绝（fail-closed）。"""
    if not key.startswith('quarantine/'):
        raise ConfirmationError(
            'not_quarantine_key',
            '人工确认只接受位于隔离区（quarantine/）的对象，当前 Key：%s' % key,
        )

    meta = oss_client.head_object(key)
    if meta is None:
        # head_object 返回 None 表示"OSS 不可用或查询失败"——无法证实对象存在，
        # 与"对象不存在"同样处理：拒绝。绝不能因为查不到就当作存在。
        raise ConfirmationError(
            'object_unverifiable',
            '无法向 OSS 证实隔离区对象存在，已拒绝（请检查 OSS 连通性后重试）',
        )
    if not meta.get('exists'):
        raise ConfirmationError(
            'object_missing', '隔离区对象不存在：%s' % key,
        )


def confirm_unchecked_document(doc, *, actor, reason, worker_id: str = ''):
    """把一份 `unavailable` 的文档人工确认放行。

    Args:
        doc: KnowledgeDoc 实例（必须已存在；调用方负责传入真实对象）
        actor: 执行确认的管理员用户
        reason: 确认理由，**必须非空**（会被原样写入 confirmation_reason）
        worker_id: 可选，记录到日志便于追踪

    Returns:
        ContentModeration（已更新为 confirmed_unchecked）

    Raises:
        ConfirmationError: 任一安全条件不满足。**不产生任何副作用。**
    """
    from .models import ContentModeration
    from .moderation import (
        MODERATION_STATE_CONFIRMED_UNCHECKED,
        MODERATION_STATE_UNAVAILABLE,
    )

    # ---- 1. 权限 ----
    _assert_actor(actor)

    # ---- 2. 理由必须非空 ----
    reason = (reason or '').strip()
    if not reason:
        raise ConfirmationError('reason_required', '必须填写确认理由')

    # ---- 3. 文档必须存在且属于本系统 ----
    if doc is None or getattr(doc, 'pk', None) is None:
        raise ConfirmationError('doc_not_found', '目标文档不存在')
    if not ContentModeration.objects.filter(doc=doc).exists() and not doc.oss_key:
        # 既无审核记录又无 OSS 关联对象 —— 不是本系统的上传产物
        raise ConfirmationError('doc_not_ours', '目标文档没有关联的隔离区对象')

    moderation = ContentModeration.objects.filter(doc=doc).first()
    if moderation is None:
        # 历史文档（knowledge/ 正式区、无审核记录）走这里。
        # 本阶段**不做**历史回填（审计报告 P-8 属独立阶段）。
        raise ConfirmationError(
            'no_moderation',
            '该文档没有审核记录；历史文档的人工放行属于独立的历史迁移阶段，'
            '本入口不处理',
        )

    # ---- 4. 只允许从 unavailable 放行 ----
    if moderation.state not in CONFIRMABLE_MODERATION_STATES:
        raise ConfirmationError(
            'not_confirmable_state',
            '当前审核状态为 %s，不允许人工确认放行（仅允许 %s）'
            % (moderation.state, '/'.join(sorted(CONFIRMABLE_MODERATION_STATES))),
        )

    # ---- 5. 对象必须在隔离区且真实存在 ----
    key = _canonical_quarantine_key(doc, moderation)
    if not key:
        raise ConfirmationError('no_oss_key', '该文档没有关联的隔离区对象 Key')
    _verify_quarantine_object(key)

    # ---- 6. CAS 写入 + 审计三件套 ----
    # 用 CAS（WHERE state='unavailable'）而不是 save()：并发确认 / 与
    # retry_failed_moderations_task 的重新审核竞争时，只有一个能生效。
    now = timezone.now()
    with transaction.atomic():
        updated = ContentModeration.objects.filter(
            pk=moderation.pk, state=MODERATION_STATE_UNAVAILABLE,
        ).update(
            state=MODERATION_STATE_CONFIRMED_UNCHECKED,
            confirmed_by=actor,
            confirmed_at=now,
            confirmation_reason=reason,
            updated_at=now,
        )
        if not updated:
            raise ConfirmationError(
                'cas_lost', '审核状态已被其他操作改变，请刷新后重试',
            )

        # ---- 7. 仍然走既有编排（promotion → knowledge/ → index） ----
        # 绝不在这里直接改 KnowledgeDoc.oss_key 或直接索引：能否放行、走不走
        # promotion 由 promote_document() 的白名单判定决定，不在本层重复实现。
        from utils.celery_dispatcher import safe_delay

        from .tasks import moderate_and_promote_task

        doc_id = doc.pk
        transaction.on_commit(lambda: safe_delay(moderate_and_promote_task, doc_id))

    moderation.refresh_from_db()
    logger.warning(
        '人工确认放行 moderation=%s doc=%s key=%s actor=%s worker=%s reason=%s',
        moderation.pk, doc_id, key, getattr(actor, 'pk', None), worker_id, reason,
    )
    return moderation
