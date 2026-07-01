"""
DRF 权限类 — 三级权限体系

IsSuperAdmin    : 超管，所有权限
IsAdmin         : 管理员，可管理自己的资源 + 管理 user 角色用户
IsOwnerOrReadOnly : 资源所有者可写，其余只读
"""
from rest_framework import permissions


class IsSuperAdmin(permissions.BasePermission):
    """超管专属权限 — 只有 super_admin 角色可访问"""

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role == 'super_admin'
        )


class IsAdmin(permissions.BasePermission):
    """管理员权限 — admin 或 super_admin 可访问"""

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in ('admin', 'super_admin')
        )


class IsOwnerOrReadOnly(permissions.BasePermission):
    """
    资源所有者可编辑/删除，其余只读。
    适用于 knowledge_doc 的 PUT/DELETE 操作。
    admin 只能操作自己上传的文档，super_admin 可操作所有。
    """

    def has_object_permission(self, request, view, obj):
        # 只读操作（GET, HEAD, OPTIONS）放行
        if request.method in permissions.SAFE_METHODS:
            return True

        # 超管可操作所有
        if request.user.role == 'super_admin':
            return True

        # admin 只能操作自己上传的
        if hasattr(obj, 'uploader_id'):
            return obj.uploader_id == request.user.id

        return False


class IsLoginUser(permissions.BasePermission):
    """登录用户权限 — 仅校验是否已认证，所有角色均可访问"""

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
        )


class IsAdminOrSuperAdmin(permissions.BasePermission):
    """admin 和 super_admin 可访问，user 不可"""

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in ('admin', 'super_admin')
        )


class CanViewReferences(permissions.BasePermission):
    """
    引用来源查看权限。
    admin 只能看自己上传文档的引用，super_admin 可看全部。
    用于 /api/chat/message/{id}/references/
    """

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in ('admin', 'super_admin')
        )


class CanManageUsers(permissions.BasePermission):
    """
    用户管理权限。
    admin: 只能管理 role=user 的用户
    super_admin: 管理所有（除自己和同级 super_admin）

    使用方式（在视图中显式声明即可）:
        permission_classes = [IsAuthenticated, CanManageUsers]
    """

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in ('admin', 'super_admin')
        )

    def has_object_permission(self, request, view, obj):
        if request.user.role == 'super_admin':
            # DELETE 操作：不能删除自己和其他 super_admin
            if request.method == 'DELETE':
                return obj.id != request.user.id and obj.role != 'super_admin'
            return True

        # admin 只能管理 role=user 的用户
        if request.user.role == 'admin':
            return obj.role == 'user'

        return False
