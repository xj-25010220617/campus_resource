"""知识库审核相关权限类。

说明：super_admin 明确不参与审核——超管上传的文档直接 approved，
后端不为超管提供审核入口，因此审核接口只认 reviewer 角色。
"""
from rest_framework import permissions


class IsReviewer(permissions.BasePermission):
    """审核接口权限：仅 reviewer（部门审核员 / 学院审核员）。"""

    message = '仅审核员可执行审核操作'

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == 'reviewer')
