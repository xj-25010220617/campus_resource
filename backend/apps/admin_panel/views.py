"""Admin Panel - 用户管理视图"""
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status, viewsets
from rest_framework.response import Response

from apps.accounts.models import College

from .serializers import (
    CollegeSerializer,
    ResetPasswordSerializer,
    UserAdminSerializer,
    UserCreateSerializer,
    UserUpdateSerializer,
    BatchResetPasswordSerializer,
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


@extend_schema(
    summary='用户管理',
    description='管理后台用户管理接口，支持列表、创建、编辑、删除',
    tags=['管理后台'],
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

    def create(self, request, *args, **kwargs):
        """新建用户：用详情序列化器重新输出，保证响应字段完整。"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        output = UserAdminSerializer(serializer.instance)
        headers = self.get_success_headers(output.data)
        return Response(output.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_update(self, serializer):
        # 防止编辑时越权
        instance = serializer.instance
        request_user = self.request.user

        # admin 不能修改其他 admin 或 super_admin
        if request_user.role == 'admin' and instance.role != 'user':
            raise PermissionError('无权修改此用户')
        serializer.save()

    def update(self, request, *args, **kwargs):
        """更新用户（整量）：用详情序列化器重新输出，保证响应字段完整。"""
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        output = UserAdminSerializer(serializer.instance)
        return Response(output.data)

    def partial_update(self, request, *args, **kwargs):
        """更新用户（局部）：同上，调用 update 时传入 partial=True。"""
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

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


@extend_schema(
    summary='重置用户密码',
    description='管理员重置指定用户的密码',
    tags=['管理后台'],
    operation_id='重置用户密码',
)
class ResetPasswordView(generics.GenericAPIView):
    """
    重置单个用户密码接口

    POST /api/admin/users/{id}/reset_password/
    功能：管理员重置指定用户的密码
    参数：
        - new_password (str): 新密码
        - password_confirm (str): 确认新密码
    返回：操作结果
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]
    serializer_class = ResetPasswordSerializer

    def post(self, request, pk):
        user = generics.get_object_or_404(User.objects.select_related('college'), pk=pk)
        request_user = request.user

        # 权限校验：admin 只能重置普通用户密码
        if request_user.role == 'admin' and user.role != 'user':
            return Response(
                {'detail': '无权重置此用户的密码'},
                status=status.HTTP_403_FORBIDDEN
            )

        # 不能重置自己的密码
        if user.id == request_user.id:
            return Response(
                {'detail': '不能重置自己的密码'},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user.set_password(serializer.validated_data['new_password'])
        user.save(update_fields=['password'])

        return Response({'detail': '密码重置成功'})


class BatchResetPasswordView(generics.GenericAPIView):
    """
    批量重置用户密码接口

    POST /api/admin/users/batch_reset_password/
    功能：管理员批量重置多个用户的密码
    参数：
        - user_ids (list[int]): 用户ID列表（最多100个）
        - new_password (str): 新密码
    返回：操作结果（成功数量、失败数量）
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]
    serializer_class = BatchResetPasswordSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user_ids = serializer.validated_data['user_ids']
        new_password = serializer.validated_data['new_password']
        request_user = request.user

        success_count = 0
        fail_count = 0
        fail_reasons = []

        users = User.objects.filter(id__in=user_ids).select_related('college')
        found_ids = set(users.values_list('id', flat=True))

        # 处理未找到的用户
        for uid in user_ids:
            if uid not in found_ids:
                fail_count += 1
                fail_reasons.append({'id': uid, 'reason': '用户不存在'})

        for user in users:
            # 权限校验
            if request_user.role == 'admin' and user.role != 'user':
                fail_count += 1
                fail_reasons.append({'id': user.id, 'username': user.username, 'reason': '无权重置此用户'})
                continue

            if user.id == request_user.id:
                fail_count += 1
                fail_reasons.append({'id': user.id, 'username': user.username, 'reason': '不能重置自己'})
                continue

            user.set_password(new_password)
            user.save(update_fields=['password'])
            success_count += 1

        return Response({
            'detail': f'批量重置完成：成功 {success_count} 个，失败 {fail_count} 个',
            'success_count': success_count,
            'fail_count': fail_count,
            'fail_reasons': fail_reasons if fail_reasons else None,
        })


class BatchDeleteUsersView(generics.GenericAPIView):
    """
    批量删除用户账号接口

    POST /api/admin/users/batch_delete/
    功能：管理员批量删除多个用户账号
    参数：
        - user_ids (list[int]): 用户ID列表（最多100个）
    返回：操作结果（成功数量、失败数量）
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]

    def post(self, request):
        user_ids = request.data.get('user_ids', [])
        request_user = request.user

        if not user_ids:
            return Response({'detail': '请提供用户ID列表'}, status=status.HTTP_400_BAD_REQUEST)

        if len(user_ids) > 100:
            return Response({'detail': '一次最多删除100个用户'}, status=status.HTTP_400_BAD_REQUEST)

        success_count = 0
        fail_count = 0
        fail_reasons = []

        users = User.objects.filter(id__in=user_ids)
        found_ids = set(users.values_list('id', flat=True))

        # 处理未找到的用户
        for uid in user_ids:
            if uid not in found_ids:
                fail_count += 1
                fail_reasons.append({'id': uid, 'reason': '用户不存在'})

        for user in users:
            # 不能删除自己
            if user.id == request_user.id:
                fail_count += 1
                fail_reasons.append({'id': user.id, 'username': user.username, 'reason': '不能删除自己'})
                continue

            # 权限校验：admin 只能删除普通用户
            if request_user.role == 'admin' and user.role != 'user':
                fail_count += 1
                fail_reasons.append({'id': user.id, 'username': user.username, 'reason': '无权删除此用户'})
                continue

            user.delete()
            success_count += 1

        return Response({
            'detail': f'批量删除完成：成功 {success_count} 个，失败 {fail_count} 个',
            'success_count': success_count,
            'fail_count': fail_count,
            'fail_reasons': fail_reasons if fail_reasons else None,
        })


class CollegeListView(generics.ListAPIView):
    """
    学院列表查询接口

    GET /api/admin/colleges/
    返回：所有学院列表，按 sort_order 排序
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]
    queryset = College.objects.all()
    serializer_class = CollegeSerializer
