"""Admin Panel - 用户管理视图"""
from django.contrib.auth import get_user_model
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .serializers import (
    UserAdminSerializer,
    UserCreateSerializer,
    UserUpdateSerializer,
)


User = get_user_model()


class IsAdminOrSuperAdmin(permissions.BasePermission):
    """管理员或超级管理员权限"""

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in ('admin', 'super_admin')
        )


class UserAdminViewSet(viewsets.ModelViewSet):
    """
    管理后台用户管理接口

    GET    /api/admin/users/              - 用户列表
    POST   /api/admin/users/             - 新建用户
    GET    /api/admin/users/{id}/        - 用户详情
    PUT    /api/admin/users/{id}/        - 编辑用户
    DELETE /api/admin/users/{id}/        - 删除用户
    """
    queryset = User.objects.select_related('college').all()
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]

    def get_serializer_class(self):
        if self.action == 'create':
            return UserCreateSerializer
        if self.action in ('update', 'partial_update'):
            return UserUpdateSerializer
        return UserAdminSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        request_user = self.request.user

        # admin 只能看到普通用户，super_admin 可以看到所有
        if request_user.role == 'admin':
            queryset = queryset.filter(role='user')
        elif request_user.role == 'super_admin':
            # 排除其他 super_admin（不能管理同级的 super_admin）
            queryset = queryset.exclude(role='super_admin')

        # 支持过滤参数
        role = self.request.query_params.get('role')
        if role:
            queryset = queryset.filter(role=role)

        college_id = self.request.query_params.get('college_id')
        if college_id:
            queryset = queryset.filter(college_id=college_id)

        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')

        return queryset.order_by('-date_joined')

    def perform_create(self, serializer):
        serializer.save()

    def perform_update(self, serializer):
        # 防止编辑时越权
        instance = serializer.instance
        request_user = self.request.user

        # admin 不能修改其他 admin 或 super_admin
        if request_user.role == 'admin' and instance.role != 'user':
            raise PermissionError('无权修改此用户')
        serializer.save()

    def perform_destroy(self, instance):
        request_user = self.request.user

        # 不能删除自己
        if instance.id == request_user.id:
            raise PermissionError('不能删除自己')

        # admin 只能删除普通用户
        if request_user.role == 'admin' and instance.role != 'user':
            raise PermissionError('无权删除此用户')

        instance.delete()

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except PermissionError as e:
            return Response({'detail': str(e)}, status=status.HTTP_403_FORBIDDEN)
