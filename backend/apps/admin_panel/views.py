"""Admin Panel - 用户管理视图"""
from django.contrib.auth import get_user_model

from rest_framework import generics, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import College
from apps.classify.models import Department
from apps.classify.serializers import DepartmentSerializer

from .permissions import (
    IsAnyAdmin,
    assert_can_manage_user,
    manage_scope_label,
    scope_users,
)
from .serializers import (
    BatchResetPasswordSerializer,
    CollegeSerializer,
    ResetPasswordSerializer,
    UserAdminSerializer,
    UserCreateSerializer,
    UserUpdateSerializer,
)


User = get_user_model()


# 兼容旧调用方：admin_panel 内部和 config/permissions.py 都定义了同名类
# 这里保留一个别名给其他模块使用
class IsAdminOrSuperAdmin(permissions.BasePermission):
    """管理员或超级管理员权限（兼容旧角色 admin）"""

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role in ('admin', 'super_admin'))


class UserAdminViewSet(viewsets.ModelViewSet):
    """
    管理后台用户管理接口

    GET    /api/admin/users/              - 用户列表
    POST   /api/admin/users/             - 新建用户
    GET    /api/admin/users/{id}/        - 用户详情
    PUT    /api/admin/users/{id}/        - 编辑用户
    DELETE /api/admin/users/{id}/        - 删除用户

    权限：
    - super_admin：管理所有用户（不含同级 super_admin）
    - admin / college_admin：管理本学院 role=user（学生）
    - dept_admin：管理本部门所有用户（员工）
    """
    queryset = User.objects.select_related('college', 'department').all()
    permission_classes = [permissions.IsAuthenticated, IsAnyAdmin]

    def get_serializer_class(self):
        if self.action == 'create':
            return UserCreateSerializer
        if self.action in ('update', 'partial_update'):
            return UserUpdateSerializer
        return UserAdminSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        request_user = self.request.user

        # 范围过滤
        qs = scope_users(qs, request_user)

        # super_admin 不列同级
        if request_user.role == 'super_admin':
            qs = qs.exclude(role='super_admin')

        # 支持过滤参数（跳过空值，避免 Apifox/Postman 遗留空参数行干扰）
        role = self.request.query_params.get('role')
        if role:
            qs = qs.filter(role=role)

        college_id = self.request.query_params.get('college_id')
        if college_id:
            qs = qs.filter(college_id=college_id)

        department_id = self.request.query_params.get('department_id')
        if department_id:
            qs = qs.filter(department_id=department_id)

        is_active = self.request.query_params.get('is_active')
        if is_active and is_active.lower() in ('true', 'false'):
            qs = qs.filter(is_active=is_active.lower() == 'true')

        search = self.request.query_params.get('search')
        if search:
            qs = qs.filter(username__icontains=search)

        return qs.order_by('-date_joined')

    def perform_create(self, serializer):
        u = self.request.user
        data = serializer.validated_data
        if u.role in ('admin', 'college_admin'):
            # 学院管理员只能建本学院学生
            data['role'] = 'user'
            data['college_id'] = u.college_id
            data['department'] = None
        elif u.role == 'dept_admin':
            # 部门管理员只能建本部门员工
            data['department_id'] = u.department_id
            data['college'] = None
            data.setdefault('role', 'user')
        # super_admin 不做任何限制
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
        assert_can_manage_user(serializer.instance, self.request.user)
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
            raise PermissionDenied('不能删除自己')
        # 权限校验
        assert_can_manage_user(instance, request_user)
        instance.delete()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response({'detail': '删除成功'}, status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'], url_path='reset_password')
    def reset_password(self, request, pk=None):
        """重置单个用户密码。

        POST /api/admin/users/{id}/reset_password/
        """
        user = self.get_object()
        request_user = request.user

        # 不能重置自己的密码
        if user.id == request_user.id:
            return Response(
                {'detail': '不能重置自己的密码'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 权限校验（get_object 已用 scope_users 限制了 queryset，这里再断言一次）
        assert_can_manage_user(user, request_user)

        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user.set_password(serializer.validated_data['new_password'])
        user.save(update_fields=['password'])

        return Response({'detail': '密码重置成功'})


class BatchResetPasswordView(generics.GenericAPIView):
    """
    批量重置用户密码接口

    POST /api/admin/users/batch_reset_password/
    """
    permission_classes = [permissions.IsAuthenticated, IsAnyAdmin]
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

        users = User.objects.filter(id__in=user_ids).select_related('college', 'department')
        found_ids = set(users.values_list('id', flat=True))

        for uid in user_ids:
            if uid not in found_ids:
                fail_count += 1
                fail_reasons.append({'id': uid, 'reason': '用户不存在'})

        for user in users:
            # 不能重置自己
            if user.id == request_user.id:
                fail_count += 1
                fail_reasons.append({'id': user.id, 'username': user.username, 'reason': '不能重置自己'})
                continue

            # 权限校验
            try:
                assert_can_manage_user(user, request_user)
            except PermissionDenied as e:
                fail_count += 1
                fail_reasons.append({'id': user.id, 'username': user.username, 'reason': str(e)})
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
    """
    permission_classes = [permissions.IsAuthenticated, IsAnyAdmin]

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

        users = User.objects.filter(id__in=user_ids).select_related('college', 'department')
        found_ids = set(users.values_list('id', flat=True))

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

            # 权限校验
            try:
                assert_can_manage_user(user, request_user)
            except PermissionDenied as e:
                fail_count += 1
                fail_reasons.append({'id': user.id, 'username': user.username, 'reason': str(e)})
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
    """
    permission_classes = [permissions.IsAuthenticated, IsAnyAdmin]
    queryset = College.objects.all()
    serializer_class = CollegeSerializer


class DepartmentListView(generics.ListAPIView):
    """
    部门列表查询接口

    GET /api/admin/departments/
    权限：仅管理员（与 /api/admin/colleges/ 一致）
    请求参数：
        - college_id (int, optional): 按所属学院筛选
    """
    permission_classes = [permissions.IsAuthenticated, IsAnyAdmin]
    serializer_class = DepartmentSerializer

    def get_queryset(self):
        qs = Department.objects.all().order_by('sort_order', 'id')
        college_id = self.request.query_params.get('college_id')
        if college_id:
            qs = qs.filter(college_id=college_id)
        return qs


class CurrentUserView(APIView):
    """
    当前管理员信息

    GET /api/admin/me/
    返回当前登录用户的归属和权限范围
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        u = request.user
        return Response({
            'id': u.id,
            'username': u.username,
            'email': u.email,
            'role': u.role,
            'role_display': u.get_role_display(),
            'is_staff': u.is_staff,
            'is_superuser': u.is_superuser,
            'college_id': u.college_id,
            'college_name': u.college.name if u.college_id else None,
            'department_id': u.department_id,
            'department_name': u.department.name if u.department_id else None,
            'permissions': {
                'manage_users': u.role in {'super_admin', 'college_admin',
                                           'dept_admin', 'admin'},
                'manage_resources': u.role in {'super_admin', 'college_admin',
                                               'dept_admin', 'admin'},
                'manage_scope': manage_scope_label(u),
            },
        })