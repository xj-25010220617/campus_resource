"""Admin Panel - 审计日志写入工具

供其他 app 调用：
- write_login_log: 登录行为落库（accounts.views）
- OperationLogMixin（mixins.py）：CRUD 操作落库

约定：
- 不抛异常（外部调用方不应被审计日志影响）
- 失败时 logger.error，但不阻断主流程
"""
import logging
from typing import Optional

from .models import LoginLog, OperationLog


logger = logging.getLogger(__name__)


def get_client_ip(request) -> Optional[str]:
    """从 request 提取客户端 IP（优先取 X-Forwarded-For 第一项）"""
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') or None


def get_user_agent(request, max_len: int = 512) -> str:
    """取 User-Agent，截断到 max_len"""
    return (request.META.get('HTTP_USER_AGENT') or '')[:max_len]


def write_login_log(
    request,
    *,
    login_type: str,
    success: bool,
    user=None,
    username: str = '',
    fail_reason: str = '',
) -> Optional[LoginLog]:
    """写入一条登录日志；失败仅记录，不抛异常。

    Args:
        request: DRF/Django Request
        login_type: 'jwt' | 'sso' | 'sso_mock'
        success: 是否成功
        user: 登录用户对象（成功时必填；失败可为 None）
        username: 失败时的用户名兜底（从请求体取）
        fail_reason: 失败原因（成功时留空）

    Returns:
        LoginLog 实例；失败返回 None
    """
    try:
        resolved_user = user if (user and getattr(user, 'pk', None)) else None
        snap_username = (
            getattr(user, 'username', '') if resolved_user
            else (username or (request.data.get('username', '') if hasattr(request, 'data') else ''))
        )
        return LoginLog.objects.create(
            user_id=resolved_user.pk if resolved_user else None,
            username=snap_username or '',
            login_type=login_type,
            success=success,
            fail_reason=fail_reason or '',
            ip_address=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
    except Exception as e:
        logger.error('write_login_log 失败: %s', e)
        return None


def write_operation_log(
    request,
    *,
    action: str,
    target_type: str,
    instance=None,
    target_id: Optional[int] = None,
    target_repr: str = '',
    after=None,
) -> Optional[OperationLog]:
    """写入一条操作日志；失败仅记录，不抛异常。

    Args:
        request: DRF/Django Request
        action: 见 OperationLog.ACTION_CHOICES
        target_type: 见 OperationLog.TARGET_CHOICES
        instance: 操作的目标对象（用来取 id 和 __str__）
        target_id: 显式给的 ID（instance 拿不到时用）
        target_repr: 对象摘要（instance 拿不到时用）
        after: 操作后快照（dict 或 ModelSerializer.data）

    Returns:
        OperationLog 实例；失败返回 None
    """
    try:
        request_user = getattr(request, 'user', None)
        operator_pk = (
            request_user.pk if (request_user and request_user.is_authenticated) else None
        )
        operator_username = (
            getattr(request_user, 'username', '') or 'anonymous'
        ) if request_user and request_user.is_authenticated else 'anonymous'

        resolved_target_id = (
            getattr(instance, 'pk', None) if instance is not None else None
        ) or target_id
        resolved_repr = (
            str(instance)[:256] if instance is not None else (target_repr or '')
        )

        return OperationLog.objects.create(
            operator_id=operator_pk,
            operator_username=operator_username,
            action=action,
            target_type=target_type,
            target_id=resolved_target_id,
            target_repr=resolved_repr,
            after=after,
            ip_address=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
    except Exception as e:
        logger.error('write_operation_log 失败: %s', e)
        return None
