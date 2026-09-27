"""上传意图（UploadIntent）——服务端签发、绑定到具体用户与隔离区对象的短生命周期凭证。

本模块解决的问题
----------------
P1-2D STEP 1 审计发现：现有 5 个上传入口里，唯一接收**客户端提交 oss_key** 的
`UploadCallbackView` 无法证明"该对象由本次请求的发起者上传"——STS 阶段服务端签发的
key 与回调阶段客户端回传的 key 之间没有任何绑定记录。后果是同一 scope 目录下，
用户 A 只要拿到用户 B 的对象 key，就能用自己的身份把它变成自己的 KnowledgeDoc。

UploadIntent 就是那个缺失的绑定记录：
    服务端签发 intent → intent 上写死唯一的 quarantine key 与归属用户
    → 客户端只能携带 intent id，不能改写 key
    → 消费时校验 user 一致且状态为 completed

分层约定
--------
- 本模块：上传意图的生命周期编排（创建 / 完成 / 消费 / 过期）。
- `models.py` 的 `UploadIntent`：状态持久化 + 数据库约束。
- OSS Key 生成：复用 P1-2A 的 `utils.oss.build_quarantine_key()`，本模块不重写规则。
- 本模块**不写 OSS、不建 KnowledgeDoc、不调用审核**——那些属于 STEP 4 及之后。
"""

import logging
import os
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from utils.oss import QUARANTINE_KEY_PREFIX, build_quarantine_key

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 状态机（持久化在 models.UploadIntent.status）
#
#     issued ──complete──> completed ──consume──> consumed   （正常路径，单向）
#       │                      │
#       └──────expire──────────┴──────> expired             （超过 expires_at）
#       │
#       └──────cancel──────────────────> cancelled          （主动作废）
#
# consumed / expired / cancelled 均为终态，不能回到之前的任何状态。
# 状态只能由服务端在本模块内变更；客户端提交的状态字段一律忽略。
# ---------------------------------------------------------------------------

UPLOAD_INTENT_ISSUED = 'issued'
UPLOAD_INTENT_COMPLETED = 'completed'
UPLOAD_INTENT_CONSUMED = 'consumed'
UPLOAD_INTENT_EXPIRED = 'expired'
UPLOAD_INTENT_CANCELLED = 'cancelled'

UPLOAD_INTENT_STATUS_CHOICES = [
    (UPLOAD_INTENT_ISSUED, '已签发'),
    (UPLOAD_INTENT_COMPLETED, '已上传'),
    (UPLOAD_INTENT_CONSUMED, '已消费'),
    (UPLOAD_INTENT_EXPIRED, '已过期'),
    (UPLOAD_INTENT_CANCELLED, '已作废'),
]

# 可以继续被使用（complete / consume）的状态
UPLOAD_INTENT_ACTIVE_STATUSES = frozenset({
    UPLOAD_INTENT_ISSUED,
    UPLOAD_INTENT_COMPLETED,
})

DEFAULT_TTL_SECONDS = 3600
MAX_FILENAME_LENGTH = 256


# ---------------------------------------------------------------------------
# 失败原因码
#
# 调用方（视图层，STEP 4 接入）据此映射 HTTP 状态码；本模块不依赖 DRF，
# 保持"service 层不感知 HTTP"的分层。
# ---------------------------------------------------------------------------
REASON_NOT_FOUND = 'not_found'
REASON_EXPIRED = 'expired'
REASON_INVALID_STATE = 'invalid_state'
REASON_FORBIDDEN = 'forbidden'
REASON_SIZE_MISMATCH = 'size_mismatch'
REASON_INVALID_ARGUMENT = 'invalid_argument'


class UploadIntentError(Exception):
    """上传意图操作失败。

    与 `OSSOperationError` 同样的取舍：本模块位于上传安全链路的判定路径上，
    调用方必须能区分"确认成功"与"不确定/失败"，因此显式抛异常而不是返回 None。
    """

    def __init__(self, reason: str, message: str = ''):
        self.reason = reason
        self.message = message or reason
        super().__init__(self.message)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def intent_ttl_seconds() -> int:
    """上传意图有效期（秒）。"""
    value = getattr(settings, 'UPLOAD_INTENT_TTL_SECONDS', DEFAULT_TTL_SECONDS)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TTL_SECONDS
    return value if value > 0 else DEFAULT_TTL_SECONDS


# ---------------------------------------------------------------------------
# 归属（scope / owner_id）
# ---------------------------------------------------------------------------

def resolve_upload_scope(user) -> tuple:
    """按上传者角色算出归属范围，返回 `(scope, owner_id)`。

    规则与全部上传入口一致（FileUploadView / BatchFileUploadView /
    TextUploadView / 分片上传）：
        super_admin                → ('school', 0)
        dept_admin 且有 department → ('department', department_id)
        其余（含 college_admin/user）→ ('college', college_id or 0)

    Note:
        STEP 4-A 起这是归属规则的**唯一权威实现**：5 个上传入口都经
        `upload_service.begin_upload()` 走到这里，各入口内的内联副本（含已删除的
        `chunked_views._build_oss_key()`）不复存在。
    """
    role = getattr(user, 'role', None)
    if role == 'super_admin':
        return 'school', 0
    if role == 'dept_admin' and getattr(user, 'department_id', None):
        return 'department', user.department_id
    return 'college', getattr(user, 'college_id', None) or 0


def _sanitize_filename(file_name: str) -> str:
    """只保留文件名部分，去掉路径分隔符（防 `../../` 之类以原文件名形式留存）。"""
    raw = (file_name or '').replace('\\', '/')
    base = os.path.basename(raw).strip()
    # 去掉控制字符与 NUL，避免污染日志与 Content-Disposition
    base = ''.join(c for c in base if ord(c) >= 32)
    if base in ('', '.', '..'):
        base = 'unnamed'
    return base[:MAX_FILENAME_LENGTH]


# ---------------------------------------------------------------------------
# 创建
# ---------------------------------------------------------------------------

def create_upload_intent(user, file_name: str, *, content_type: str = '',
                         expected_size=None, ttl_seconds=None):
    """服务端创建一个上传意图。

    quarantine key **由服务端生成并写死**，调用方（含视图层）不得传入或覆盖。

    Args:
        user: 归属用户（必填，不能为 None）
        file_name: 原始文件名（仅用于取扩展名与展示）
        content_type: 上传时声明的 MIME，可为空
        expected_size: 预期大小（字节），可为空
        ttl_seconds: 覆盖默认有效期，可为空

    Returns:
        UploadIntent 实例（status=issued）

    Raises:
        UploadIntentError: `invalid_argument`（user 为空 / 大小非法）
    """
    from .models import UploadIntent

    if user is None or getattr(user, 'pk', None) is None:
        raise UploadIntentError(REASON_INVALID_ARGUMENT, '上传意图必须绑定已登录用户')

    safe_name = _sanitize_filename(file_name)
    if expected_size is not None:
        try:
            expected_size = int(expected_size)
        except (TypeError, ValueError):
            raise UploadIntentError(REASON_INVALID_ARGUMENT, 'expected_size 必须是整数')
        if expected_size < 0:
            raise UploadIntentError(REASON_INVALID_ARGUMENT, 'expected_size 不能为负数')

    scope, owner_id = resolve_upload_scope(user)
    # Key 一律由 P1-2A 的 build_quarantine_key 生成：本模块不重写 key 规则。
    quarantine_key = build_quarantine_key(scope, owner_id, safe_name)
    ttl = intent_ttl_seconds() if ttl_seconds is None else int(ttl_seconds)

    return UploadIntent.objects.create(
        user=user,
        scope=scope,
        owner_id=owner_id,
        quarantine_key=quarantine_key,
        original_filename=safe_name,
        content_type=(content_type or '')[:128],
        expected_size=expected_size,
        status=UPLOAD_INTENT_ISSUED,
        expires_at=timezone.now() + timedelta(seconds=ttl),
    )


# ---------------------------------------------------------------------------
# 查询 / 归属校验 / 过期
# ---------------------------------------------------------------------------

def _load_owned(user, intent_id, *, lock: bool = False):
    """按 id 取 intent 并校验归属。

    `intent.user` 为空（用户已注销，SET_NULL 保留审计记录）时一律拒绝——
    fail-closed：没有归属人就没有合法的消费方。

    Raises:
        UploadIntentError: `not_found`（不存在 / 不属于该用户）
    """
    from .models import UploadIntent

    if user is None or getattr(user, 'pk', None) is None:
        raise UploadIntentError(REASON_NOT_FOUND, '上传意图不存在')

    queryset = UploadIntent.objects
    if lock:
        # MySQL/GaussDB 上行级锁；SQLite 上是静默空操作（见下方 CAS 说明）
        queryset = queryset.select_for_update()
    try:
        intent = queryset.get(pk=intent_id)
    except (UploadIntent.DoesNotExist, ValueError, TypeError):
        raise UploadIntentError(REASON_NOT_FOUND, '上传意图不存在')

    if intent.user_id is None or intent.user_id != user.pk:
        # 对外与"不存在"返回同一个原因码，避免通过错误信息枚举他人 intent id；
        # 差异只留在服务端日志里，便于排查越权尝试。
        logger.warning(
            '上传意图越权访问被拒: intent=%s owner=%s requester=%s',
            intent_id, intent.user_id, getattr(user, 'pk', None),
        )
        raise UploadIntentError(REASON_NOT_FOUND, '上传意图不存在')

    return intent


def get_upload_intent(user, intent_id):
    """读取 intent（校验归属，不做状态变更）。过期则顺带标记为 expired。"""
    intent = _load_owned(user, intent_id)
    _mark_expired_if_due(intent)
    return intent


def is_expired(intent, now=None) -> bool:
    """当前时间是否已达到/超过 expires_at。"""
    if intent.expires_at is None:
        return False
    return (now or timezone.now()) >= intent.expires_at


def _mark_expired_if_due(intent) -> bool:
    """过期则把 intent 落为 expired（CAS，仅在 issued/completed 时可落）。

    §十二：本 STEP 不引入定时清理任务，过期只在"每次使用 intent 时"惰性判定。

    Returns:
        True 表示该 intent 现在处于 expired 状态
    """
    if intent.expires_at is None:
        return False
    if not is_expired(intent):
        return False
    if intent.status == UPLOAD_INTENT_EXPIRED:
        return True
    expired = _cas_status(
        intent, UPLOAD_INTENT_ACTIVE_STATUSES, UPLOAD_INTENT_EXPIRED,
    )
    if expired:
        intent.status = UPLOAD_INTENT_EXPIRED
    else:
        # 并发下已被别的状态变更抢先（例如刚好被消费掉）：以库里的实际值为准
        intent.refresh_from_db(fields=['status'])
    return intent.status == UPLOAD_INTENT_EXPIRED


# ---------------------------------------------------------------------------
# 状态原子变更
# ---------------------------------------------------------------------------

def _cas_status(intent, from_statuses, to_status, **extra) -> bool:
    """把 intent 从 `from_statuses` 之一原子地改为 `to_status`。

    用 `UPDATE ... WHERE pk=? AND status IN (...)` 的单条语句 + 影响行数判定，
    而不是"先读后写"——两个并发请求只有一个能让 rowcount 为 1。

    Note:
        这是本模块消费/完成操作的**主要**并发保护。`select_for_update()` 在
        SQLite 上是静默空操作（Django 的 `has_select_for_update` 为 False），
        因此不能把它当作唯一保证；CAS 在 SQLite / MySQL / GaussDB 上语义一致。
    """
    updated = type(intent).objects.filter(
        pk=intent.pk, status__in=list(from_statuses),
    ).update(status=to_status, **extra)
    return updated == 1


def _require_usable(intent):
    """状态与有效期校验（不改状态）。"""
    if _mark_expired_if_due(intent):
        raise UploadIntentError(REASON_EXPIRED, '上传意图已过期')
    if intent.status == UPLOAD_INTENT_CANCELLED:
        raise UploadIntentError(REASON_INVALID_STATE, '上传意图已作废')
    if intent.status == UPLOAD_INTENT_CONSUMED:
        raise UploadIntentError(REASON_INVALID_STATE, '上传意图已被消费')


# ---------------------------------------------------------------------------
# 完成（对象已上传到隔离区）
# ---------------------------------------------------------------------------

def complete_upload_intent(user, intent_id, *, actual_size=None,
                           quarantine_key: str = None):
    """把 intent 从 `issued` 推进到 `completed`。

    只能由服务端在上传确认后调用（STEP 4 的视图层负责先向 OSS 核实对象）。

    Args:
        user: 请求方，必须与 intent.user 一致
        intent_id: 意图 id
        actual_size: 服务端核实到的对象大小（可选）。若 intent 记录过
            expected_size 且两者不一致，拒绝完成——上传的字节和声明的不是同一份。
        quarantine_key: 服务端实际写入的 key（可选）。若与 intent 上的 key 不一致，
            拒绝完成——防止把对象写到 intent 之外的路径上。

    Raises:
        UploadIntentError: not_found / expired / invalid_state / size_mismatch /
            invalid_argument(key 不匹配)
    """
    intent = _load_owned(user, intent_id, lock=True)
    _require_usable(intent)

    if quarantine_key is not None and quarantine_key != intent.quarantine_key:
        raise UploadIntentError(
            REASON_INVALID_ARGUMENT, '上传对象与意图记录的隔离区 Key 不一致',
        )

    if actual_size is not None:
        try:
            actual_size = int(actual_size)
        except (TypeError, ValueError):
            raise UploadIntentError(REASON_INVALID_ARGUMENT, 'actual_size 必须是整数')
        if actual_size < 0:
            raise UploadIntentError(REASON_INVALID_ARGUMENT, 'actual_size 不能为负数')
        if intent.expected_size is not None and actual_size != intent.expected_size:
            raise UploadIntentError(
                REASON_SIZE_MISMATCH,
                f'对象大小与声明不一致：声明 {intent.expected_size}，实际 {actual_size}',
            )

    fields = {'completed_at': timezone.now()}
    if actual_size is not None:
        fields['actual_size'] = actual_size

    if not _cas_status(intent, {UPLOAD_INTENT_ISSUED}, UPLOAD_INTENT_COMPLETED, **fields):
        # 并发下已被消费/作废：以库里的实际值为准再抛一次语义正确的错误
        intent.refresh_from_db()
        _require_usable(intent)
        raise UploadIntentError(REASON_INVALID_STATE, '上传意图当前状态不允许完成')

    intent.refresh_from_db()
    return intent


# ---------------------------------------------------------------------------
# 消费（业务已基于该对象建过记录）
# ---------------------------------------------------------------------------

def consume_upload_intent(user, intent_id, *, lock: bool = True):
    """把 intent 从 `completed` 推进到 `consumed`，返回消费后的实例。

    语义：该 intent 已经被成功处理，**不能再次用于创建另一个独立 KnowledgeDoc**。
    必须原子化——两个并发请求只能有一个拿到成功结果。

    Raises:
        UploadIntentError: not_found / expired / invalid_state
    """
    intent = _load_owned(user, intent_id, lock=lock)
    _require_usable(intent)
    if intent.status != UPLOAD_INTENT_COMPLETED:
        raise UploadIntentError(
            REASON_INVALID_STATE, '上传意图尚未完成上传，不能消费',
        )

    if not _cas_status(intent, {UPLOAD_INTENT_COMPLETED}, UPLOAD_INTENT_CONSUMED,
                       consumed_at=timezone.now()):
        intent.refresh_from_db()
        if _mark_expired_if_due(intent):
            raise UploadIntentError(REASON_EXPIRED, '上传意图已过期')
        raise UploadIntentError(REASON_INVALID_STATE, '上传意图已经被消费')

    intent.refresh_from_db()
    return intent


# ---------------------------------------------------------------------------
# 作废
# ---------------------------------------------------------------------------

def cancel_upload_intent(user, intent_id):
    """主动作废一个尚未消费的 intent（issued / completed 均可）。

    Returns:
        作废后的 UploadIntent

    Raises:
        UploadIntentError: not_found / invalid_state（已过期或已消费/已作废）
    """
    intent = _load_owned(user, intent_id, lock=True)
    if _mark_expired_if_due(intent):
        raise UploadIntentError(REASON_EXPIRED, '上传意图已过期')
    if intent.status not in UPLOAD_INTENT_ACTIVE_STATUSES:
        raise UploadIntentError(REASON_INVALID_STATE, '上传意图当前状态不允许作废')

    if not _cas_status(intent, UPLOAD_INTENT_ACTIVE_STATUSES, UPLOAD_INTENT_CANCELLED):
        intent.refresh_from_db()
        raise UploadIntentError(REASON_INVALID_STATE, '上传意图当前状态不允许作废')

    intent.refresh_from_db()
    return intent


# ---------------------------------------------------------------------------
# 对外序列化（§十一：只给必要信息）
# ---------------------------------------------------------------------------

def serialize_for_client(intent) -> dict:
    """常规返回：**不含任何 OSS key**。

    客户端拿到 intent id 就够用；quarantine key 是服务端内部概念，
    默认不下发，减少"客户端拿着 key 去拼别的路径"的机会。
    """
    return {
        'intent_id': intent.pk,
        'status': intent.status,
        'expires_at': intent.expires_at,
    }


def serialize_for_upload(intent) -> dict:
    """直传场景返回：额外带上 quarantine_key。

    走 OSS 直传时客户端**必须**知道要往哪个 key PUT，因此这里必须给出。
    该 key 由服务端在创建 intent 时生成并持久化，后续所有校验都以库里的值为准，
    客户端回传的值仅用于比对（不一致即拒绝），**不能覆盖**它。
    """
    data = serialize_for_client(intent)
    data['quarantine_key'] = intent.quarantine_key
    return data


def is_quarantine_key(oss_key) -> bool:
    """key 是否位于隔离区命名空间。"""
    return bool(oss_key) and str(oss_key).startswith(QUARANTINE_KEY_PREFIX)
