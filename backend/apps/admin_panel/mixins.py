"""Admin Panel - 操作日志 Mixin

`OperationLogMixin`：给 DRF ViewSet 自动写 OperationLog。

当前实际只用在 UserAdminViewSet（超管用户 CRUD）。
Resource / Category 等 CRUD 入口尚未在本项目落地，待未来扩展使用。

注意：
- `log_target_type` 子类必须设置
- `log_serializer_class` 用于生成 after 快照（dict）；不设置则只存 target_repr
- Mixin 包装的是 `perform_create / perform_update / perform_destroy`，
  对 ModelViewSet 派生类开箱即用
"""
import logging

from rest_framework.exceptions import PermissionDenied

from .utils import write_operation_log


logger = logging.getLogger(__name__)


class OperationLogMixin:
    """给 ViewSet 加自动写 OperationLog 的能力。

    用法::

        class UserAdminViewSet(OperationLogMixin, viewsets.ModelViewSet):
            log_target_type = 'user'
            log_serializer_class = UserAdminSerializer

    子类必须设置：
        - log_target_type: 'user' / 'resource' / 'category'

    可选：
        - log_serializer_class: 用于生成 after 快照
        - log_skip_actions: set[str], 不写日志的动作集合
    """

    log_target_type = None
    log_serializer_class = None
    log_skip_actions = set()

    def _capture_after(self, instance):
        """生成 after 快照（dict）。失败返回 None。"""
        serializer_cls = getattr(self, 'log_serializer_class', None)
        if not serializer_cls or instance is None:
            return None
        try:
            return serializer_cls(instance).data
        except Exception as e:
            logger.warning('生成 after 快照失败: %s', e)
            return None

    def _log_if_enabled(self, action: str, instance=None, after=None):
        """满足条件时写一条 OperationLog（失败仅记录日志，不抛）"""
        if action in getattr(self, 'log_skip_actions', set()):
            return
        target_type = getattr(self, 'log_target_type', None)
        if not target_type:
            return
        write_operation_log(
            self.request,
            action=action,
            target_type=target_type,
            instance=instance,
            after=after,
        )

    # ---- 钩子 ----
    def perform_create(self, serializer):
        super().perform_create(serializer)
        self._log_if_enabled(
            action='create',
            instance=serializer.instance,
            after=self._capture_after(serializer.instance),
        )

    def perform_update(self, serializer):
        super().perform_update(serializer)
        self._log_if_enabled(
            action='update',
            instance=serializer.instance,
            after=self._capture_after(serializer.instance),
        )

    def perform_destroy(self, instance):
        super().perform_destroy(instance)
        self._log_if_enabled(action='delete', instance=instance)

    # ---- 自定义动作（reset_password / batch_delete 等）由各 ViewSet 自行调 _log_if_enabled ----
