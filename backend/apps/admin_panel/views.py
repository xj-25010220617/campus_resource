from typing import Optional
"""Admin Panel - 视图

分两大块：
- 用户管理（既有）：UserAdminViewSet + BatchReset/BatchDelete + College/Dept + CurrentUser
- 超管日志管理（新增）：10 个 List/Detail + 4 个 stats + 1 个 dashboard

代码规范（CONVENTIONS §7）：
- 日志管理一律使用 generics.ListAPIView / RetrieveAPIView
- URL 注册用 .as_view()，不引入 Router
"""
import datetime as _datetime

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from rest_framework import generics, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import College
from apps.chat.models import QueryLog, SensitiveLog
from apps.classify.models import Department
from apps.classify.serializers import DepartmentSerializer
from apps.resources.models import UploadLog

from config.permissions import IsSuperAdmin

from .mixins import OperationLogMixin
from .models import LoginLog, OperationLog
from .permissions import (
    IsAnyAdmin,
    assert_can_manage_user,
    manage_scope_label,
    scope_users,
)
from .serializers import (
    AdminLoginLogSerializer,
    AdminOperationLogSerializer,
    AdminQueryLogSerializer,
    AdminSensitiveLogSerializer,
    AdminUploadLogSerializer,
    BatchResetPasswordSerializer,
    CollegeSerializer,
    DepartmentWriteSerializer,
    LoginLogStatsSerializer,
    OperationLogStatsSerializer,
    QueryLogStatsSerializer,
    ResetPasswordSerializer,
    UploadLogStatsSerializer,
    UserAdminSerializer,
    UserCreateSerializer,
    UserUpdateSerializer,
)
from .services import dashboard as dashboard_svc
from .utils import write_operation_log


User = get_user_model()


# ======================================================================
# 一、用户管理（既有，未改动 CRUD 行为）
# ======================================================================
class IsAdminOrSuperAdmin(permissions.BasePermission):
    """所有管理员角色权限：super_admin、college_admin、dept_admin、admin（兼容旧角色）"""

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role in ('super_admin', 'college_admin', 'dept_admin', 'admin'))


class UserAdminViewSet(OperationLogMixin, viewsets.ModelViewSet):
    """
    管理后台用户管理接口（含操作日志埋点）

    GET    /api/admin/users/              - 用户列表
    POST   /api/admin/users/             - 新建用户
    GET    /api/admin/users/{id}/        - 用户详情
    PUT    /api/admin/users/{id}/        - 编辑用户
    DELETE /api/admin/users/{id}/        - 删除用户

    权限：
    - super_admin：管理所有用户（不含同级 super_admin）
    - admin / college_admin：管理本学院 role=user（学生）
    - dept_admin：管理本部门所有用户（员工）

    操作日志（OperationLog.target_type='user'）：
    - POST /api/admin/users/                  → action=create
    - PUT/PATCH /api/admin/users/{id}/        → action=update
    - DELETE /api/admin/users/{id}/           → action=delete
    - POST /api/admin/users/{id}/reset_password/  → action=reset_password
    - POST /api/admin/users/batch_reset_password/  → action=batch_reset_password
    - POST /api/admin/users/batch_delete/     → action=batch_delete
    """
    queryset = User.objects.select_related('college', 'department').all()
    permission_classes = [permissions.IsAuthenticated, IsAnyAdmin]
    log_target_type = 'user'
    log_serializer_class = UserAdminSerializer

    def get_serializer_class(self):
        if self.action == 'create':
            return UserCreateSerializer
        if self.action in ('update', 'partial_update'):
            return UserUpdateSerializer
        return UserAdminSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        request_user = self.request.user

        qs = scope_users(qs, request_user)

        if request_user.role == 'super_admin':
            qs = qs.exclude(role='super_admin')

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
            data['role'] = 'user'
            data['college_id'] = u.college_id
            data['department'] = None
        elif u.role == 'dept_admin':
            data['department_id'] = u.department_id
            data['college'] = None
            data['role'] = 'user'  # 强制锁定为普通用户，防止建出同级/上级角色
        serializer.save()

    def create(self, request, *args, **kwargs):
        """新建用户：用详情序列化器重新输出，保证响应字段完整。"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        output = UserAdminSerializer(serializer.instance)
        headers = self.get_success_headers(output.data)
        # 显式写日志（确保 after 快照基于完整输出）
        write_operation_log(
            request,
            action='create',
            target_type='user',
            instance=serializer.instance,
            after=output.data,
        )
        return Response(output.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_update(self, serializer):
        assert_can_manage_user(serializer.instance, self.request.user)
        serializer.save()

    def update(self, request, *args, **kwargs):
        """更新用户（整量）：用详情序列化器重新输出。"""
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        output = UserAdminSerializer(serializer.instance)
        write_operation_log(
            request,
            action='update',
            target_type='user',
            instance=serializer.instance,
            after=output.data,
        )
        return Response(output.data)

    def partial_update(self, request, *args, **kwargs):
        """更新用户（局部）：同上。"""
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    def perform_destroy(self, instance):
        request_user = self.request.user
        if instance.id == request_user.id:
            raise PermissionDenied('不能删除自己')
        assert_can_manage_user(instance, request_user)
        instance.delete()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        write_operation_log(
            request,
            action='delete',
            target_type='user',
            instance=instance,
        )
        return Response({'detail': '删除成功'}, status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'], url_path='reset_password')
    def reset_password(self, request, pk=None):
        """重置单个用户密码。

        POST /api/admin/users/{id}/reset_password/
        """
        user = self.get_object()
        request_user = request.user

        if user.id == request_user.id:
            return Response(
                {'detail': '不能重置自己的密码'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        assert_can_manage_user(user, request_user)

        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user.set_password(serializer.validated_data['new_password'])
        user.save(update_fields=['password'])

        write_operation_log(
            request,
            action='reset_password',
            target_type='user',
            instance=user,
        )
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
            if user.id == request_user.id:
                fail_count += 1
                fail_reasons.append({'id': user.id, 'username': user.username, 'reason': '不能重置自己'})
                continue

            try:
                assert_can_manage_user(user, request_user)
            except PermissionDenied as e:
                fail_count += 1
                fail_reasons.append({'id': user.id, 'username': user.username, 'reason': str(e)})
                continue

            user.set_password(new_password)
            user.save(update_fields=['password'])
            success_count += 1

        # 写操作日志
        write_operation_log(
            request,
            action='batch_reset_password',
            target_type='user',
            target_repr=f'批量重置 {success_count} 个用户',
        )

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
            if user.id == request_user.id:
                fail_count += 1
                fail_reasons.append({'id': user.id, 'username': user.username, 'reason': '不能删除自己'})
                continue

            try:
                assert_can_manage_user(user, request_user)
            except PermissionDenied as e:
                fail_count += 1
                fail_reasons.append({'id': user.id, 'username': user.username, 'reason': str(e)})
                continue

            user.delete()
            success_count += 1

        write_operation_log(
            request,
            action='batch_delete',
            target_type='user',
            target_repr=f'批量删除 {success_count} 个用户',
        )

        return Response({
            'detail': f'批量删除完成：成功 {success_count} 个，失败 {fail_count} 个',
            'success_count': success_count,
            'fail_count': fail_count,
            'fail_reasons': fail_reasons if fail_reasons else None,
        })


class CollegeListView(generics.ListAPIView):
    """学院列表查询接口

    GET /api/admin/colleges/
    """
    permission_classes = [permissions.IsAuthenticated, IsAnyAdmin]
    queryset = College.objects.all()
    serializer_class = CollegeSerializer


class DepartmentListView(generics.ListCreateAPIView):
    """部门列表查询 / 新建部门

    GET  /api/admin/departments/  - 部门列表（树形，所有管理员）
    POST /api/admin/departments/  - 新建部门（仅超级管理员）
    权限：
        - GET: 所有管理员（IsAnyAdmin）
        - POST: 仅超级管理员（IsSuperAdmin）
    POST 请求体：
        - name (str, required): 部门名称
        - parent (int, optional): 上级部门 ID，空表示一级部门
        - sort_order (int, optional): 排序值，默认 0
    """
    serializer_class = DepartmentSerializer

    def get_permissions(self):
        if self.request.method == 'POST':
            return [permissions.IsAuthenticated(), IsSuperAdmin()]
        return [permissions.IsAuthenticated(), IsAnyAdmin()]

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return DepartmentWriteSerializer
        return DepartmentSerializer

    def get_queryset(self):
        return Department.objects.all().order_by('sort_order', 'id')

    def perform_create(self, serializer):
        serializer.save()
        write_operation_log(
            self.request,
            action='create',
            target_type='department',
            instance=serializer.instance,
            after=serializer.data,
        )


class DepartmentDestroyView(generics.DestroyAPIView):
    """删除部门 - 超管

    DELETE /api/admin/departments/{id}/
    权限：仅超级管理员
    说明：删除部门会级联删除其下级部门（parent CASCADE）；
         归属该部门的用户 department 字段置空（SET_NULL）。
    """
    permission_classes = [permissions.IsAuthenticated, IsSuperAdmin]
    queryset = Department.objects.all()

    def perform_destroy(self, instance):
        target_id = instance.pk
        target_repr = str(instance)
        instance.delete()
        write_operation_log(
            self.request,
            action='delete',
            target_type='department',
            target_id=target_id,
            target_repr=target_repr,
        )


class CurrentUserView(APIView):
    """当前管理员信息

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


# ======================================================================
# 二、超管日志管理 — 通用时间区间工具
# ======================================================================
def _parse_date_param(value: Optional[str]) -> Optional[_datetime.datetime]:
    """把查询参数 date_from / date_to 解析为带时区的 datetime。"""
    if not value:
        return None
    if 'T' in value:
        dt = parse_datetime(value)
        if dt is None:
            return None
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        return dt
    try:
        dt = _datetime.datetime.strptime(value, '%Y-%m-%d')
    except ValueError:
        return None
    return timezone.make_aware(dt)


def _apply_date_range(qs, date_from: Optional[str], date_to: Optional[str]):
    """对 queryset 应用 date_from / date_to 区间。"""
    start = _parse_date_param(date_from)
    end = _parse_date_param(date_to)
    if start:
        qs = qs.filter(created_at__gte=start)
    if end:
        if 'T' not in (date_to or ''):
            end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
        qs = qs.filter(created_at__lte=end)
    return qs


# ======================================================================
# 三、超管日志管理 — 5 类日志的 List / Detail
# 全部使用 ListAPIView / RetrieveAPIView（规范 §7：不用 ViewSet/Router）
# 权限：IsAuthenticated + IsSuperAdmin
# ======================================================================
# ----- 1. UploadLog -----
class AdminUploadLogListView(generics.ListAPIView):
    """上传日志列表 - 超管

    GET /api/admin/logs/upload/
    过滤：user_id / status / college_id / department_id / file_type /
          date_from (YYYY-MM-DD or ISO) / date_to / keyword (file_name)
    返回：分页的上传日志列表
    """
    permission_classes = [permissions.IsAuthenticated, IsSuperAdmin]
    serializer_class = AdminUploadLogSerializer

    def get_queryset(self):
        qs = UploadLog.objects.select_related(
            'user', 'college', 'department', 'knowledge_doc',
        )
        for key in ('user_id', 'status', 'college_id', 'department_id', 'file_type'):
            value = self.request.query_params.get(key)
            if value:
                qs = qs.filter(**{key: value})
        qs = _apply_date_range(
            qs,
            self.request.query_params.get('date_from'),
            self.request.query_params.get('date_to'),
        )
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(file_name__icontains=keyword)
        return qs.order_by('-created_at')


class AdminUploadLogDetailView(generics.RetrieveAPIView):
    """上传日志详情 - 超管

    GET /api/admin/logs/upload/{id}/
    """
    permission_classes = [permissions.IsAuthenticated, IsSuperAdmin]
    serializer_class = AdminUploadLogSerializer
    queryset = UploadLog.objects.select_related(
        'user', 'college', 'department', 'knowledge_doc',
    )


# ----- 2. QueryLog -----
class AdminQueryLogListView(generics.ListAPIView):
    """查询日志列表 - 超管

    GET /api/admin/logs/query/
    过滤：user_id / user_feedback (-1/0/1) / hit_count_min /
          date_from / date_to / keyword (question)
    返回：分页的查询日志列表
    """
    permission_classes = [permissions.IsAuthenticated, IsSuperAdmin]
    serializer_class = AdminQueryLogSerializer

    def get_queryset(self):
        qs = QueryLog.objects.select_related('user', 'conversation')
        user_id = self.request.query_params.get('user_id')
        if user_id:
            qs = qs.filter(user_id=user_id)
        feedback = self.request.query_params.get('user_feedback')
        if feedback is not None and feedback != '':
            try:
                qs = qs.filter(user_feedback=int(feedback))
            except (TypeError, ValueError):
                pass
        hit_min = self.request.query_params.get('hit_count_min')
        if hit_min is not None and hit_min != '':
            try:
                qs = qs.filter(hit_count__gte=int(hit_min))
            except (TypeError, ValueError):
                pass
        qs = _apply_date_range(
            qs,
            self.request.query_params.get('date_from'),
            self.request.query_params.get('date_to'),
        )
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(question__icontains=keyword)
        return qs.order_by('-created_at')


class AdminQueryLogDetailView(generics.RetrieveAPIView):
    """查询日志详情 - 超管

    GET /api/admin/logs/query/{id}/
    """
    permission_classes = [permissions.IsAuthenticated, IsSuperAdmin]
    serializer_class = AdminQueryLogSerializer
    queryset = QueryLog.objects.select_related('user', 'conversation')


# ----- 3. SensitiveLog -----
class AdminSensitiveLogListView(generics.ListAPIView):
    """敏感内容日志列表 - 超管

    GET /api/admin/logs/sensitive/
    过滤：user_id / date_from / date_to / keyword (question)
    """
    permission_classes = [permissions.IsAuthenticated, IsSuperAdmin]
    serializer_class = AdminSensitiveLogSerializer

    def get_queryset(self):
        qs = SensitiveLog.objects.select_related('user')
        user_id = self.request.query_params.get('user_id')
        if user_id:
            qs = qs.filter(user_id=user_id)
        qs = _apply_date_range(
            qs,
            self.request.query_params.get('date_from'),
            self.request.query_params.get('date_to'),
        )
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(question__icontains=keyword)
        return qs.order_by('-created_at')


class AdminSensitiveLogDetailView(generics.RetrieveAPIView):
    """敏感内容日志详情 - 超管

    GET /api/admin/logs/sensitive/{id}/
    """
    permission_classes = [permissions.IsAuthenticated, IsSuperAdmin]
    serializer_class = AdminSensitiveLogSerializer
    queryset = SensitiveLog.objects.select_related('user')


# ----- 4. LoginLog -----
class AdminLoginLogListView(generics.ListAPIView):
    """登录日志列表 - 超管

    GET /api/admin/logs/login/
    过滤：user_id / username / login_type / success (true/false) /
          date_from / date_to
    """
    permission_classes = [permissions.IsAuthenticated, IsSuperAdmin]
    serializer_class = AdminLoginLogSerializer

    def get_queryset(self):
        qs = LoginLog.objects.select_related('user')
        user_id = self.request.query_params.get('user_id')
        if user_id:
            qs = qs.filter(user_id=user_id)
        username = self.request.query_params.get('username')
        if username:
            qs = qs.filter(username__icontains=username)
        login_type = self.request.query_params.get('login_type')
        if login_type:
            qs = qs.filter(login_type=login_type)
        success = self.request.query_params.get('success')
        if success is not None and success != '':
            qs = qs.filter(success=success.lower() == 'true')
        qs = _apply_date_range(
            qs,
            self.request.query_params.get('date_from'),
            self.request.query_params.get('date_to'),
        )
        return qs.order_by('-created_at')


class AdminLoginLogDetailView(generics.RetrieveAPIView):
    """登录日志详情 - 超管

    GET /api/admin/logs/login/{id}/
    """
    permission_classes = [permissions.IsAuthenticated, IsSuperAdmin]
    serializer_class = AdminLoginLogSerializer
    queryset = LoginLog.objects.select_related('user')


# ----- 5. OperationLog -----
class AdminOperationLogListView(generics.ListAPIView):
    """操作日志列表 - 超管

    GET /api/admin/logs/operation/
    过滤：operator_id / action / target_type / target_id /
          date_from / date_to
    """
    permission_classes = [permissions.IsAuthenticated, IsSuperAdmin]
    serializer_class = AdminOperationLogSerializer

    def get_queryset(self):
        qs = OperationLog.objects.select_related('operator')
        operator_id = self.request.query_params.get('operator_id')
        if operator_id:
            qs = qs.filter(operator_id=operator_id)
        action = self.request.query_params.get('action')
        if action:
            qs = qs.filter(action=action)
        target_type = self.request.query_params.get('target_type')
        if target_type:
            qs = qs.filter(target_type=target_type)
        target_id = self.request.query_params.get('target_id')
        if target_id:
            qs = qs.filter(target_id=target_id)
        qs = _apply_date_range(
            qs,
            self.request.query_params.get('date_from'),
            self.request.query_params.get('date_to'),
        )
        return qs.order_by('-created_at')


class AdminOperationLogDetailView(generics.RetrieveAPIView):
    """操作日志详情 - 超管

    GET /api/admin/logs/operation/{id}/
    """
    permission_classes = [permissions.IsAuthenticated, IsSuperAdmin]
    serializer_class = AdminOperationLogSerializer
    queryset = OperationLog.objects.select_related('operator')


# ======================================================================
# 四、统计接口（每类日志 1 个 stats，已复用 4 类；sensitive 不另设 stats）
# ======================================================================
class _PeriodCounterHelper:
    """公共：统计 today / week / month 计数"""

    @staticmethod
    def get_counters(model, now: _datetime.datetime):
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - _datetime.timedelta(days=now.weekday())
        month_start = today_start.replace(day=1)
        return {
            'total': model.objects.count(),
            'today': model.objects.filter(created_at__gte=today_start).count(),
            'this_week': model.objects.filter(created_at__gte=week_start).count(),
            'this_month': model.objects.filter(created_at__gte=month_start).count(),
        }


class UploadLogStatsView(APIView):
    """上传日志统计 - 超管

    GET /api/admin/logs/upload/stats/
    返回：total/today/this_week/this_month + 按 status/file_type/college 分组 + total_size
    """
    permission_classes = [permissions.IsAuthenticated, IsSuperAdmin]

    def get(self, request):
        now = timezone.now()
        base = _PeriodCounterHelper.get_counters(UploadLog, now)
        qs = UploadLog.objects.all()

        from django.db.models import Count, Sum
        by_status = [
            {
                'key': (row['status'] or 'unknown'),
                'label': row['status'] or '未知',
                'count': row['count'],
            }
            for row in qs.values('status').annotate(count=Count('id'))
        ]
        by_file_type = [
            {
                'key': (row['file_type'] or 'unknown'),
                'label': row['file_type'] or '未知',
                'count': row['count'],
            }
            for row in qs.values('file_type').annotate(count=Count('id'))
        ]
        by_college_rows = (
            qs.values('college__id', 'college__name')
            .annotate(count=Count('id'))
            .order_by('-count')[:10]
        )
        by_college = [
            {
                'key': str(row['college__id']) if row['college__id'] else 'none',
                'label': row['college__name'] or '未分类',
                'count': row['count'],
            }
            for row in by_college_rows
        ]
        total_size = qs.aggregate(s=Sum('file_size'))['s'] or 0

        data = {**base, 'by_status': by_status,
                'by_file_type': by_file_type, 'by_college': by_college,
                'total_size': total_size}
        return Response(UploadLogStatsSerializer(data).data)


class QueryLogStatsView(APIView):
    """查询日志统计 - 超管

    GET /api/admin/logs/query/stats/
    返回：total/today/this_week/this_month + avg_hit_count / avg_response_ms / like_rate / by_feedback
    """
    permission_classes = [permissions.IsAuthenticated, IsSuperAdmin]

    def get(self, request):
        now = timezone.now()
        base = _PeriodCounterHelper.get_counters(QueryLog, now)
        qs = QueryLog.objects.all()

        from django.db.models import Avg, Count, Q
        agg = qs.aggregate(
            avg_hit=Avg('hit_count'),
            avg_ms=Avg('response_ms'),
            likes=Count('id', filter=Q(user_feedback=1)),
        )
        total = base['total']
        like_rate = (agg['likes'] / total) if total else 0

        feedback_label_map = {1: '赞', 0: '踩', -1: '无反馈'}
        by_feedback = [
            {
                'key': str(row['user_feedback']),
                'label': feedback_label_map.get(row['user_feedback'], '未知'),
                'count': row['count'],
            }
            for row in qs.values('user_feedback').annotate(count=Count('id')).order_by('user_feedback')
        ]

        data = {**base,
                'avg_hit_count': round(agg['avg_hit'] or 0, 2),
                'avg_response_ms': int(agg['avg_ms'] or 0),
                'like_rate': round(like_rate, 4),
                'by_feedback': by_feedback}
        return Response(QueryLogStatsSerializer(data).data)


class LoginLogStatsView(APIView):
    """登录日志统计 - 超管

    GET /api/admin/logs/login/stats/
    返回：total/today/this_week/this_month + success/fail + by_success/by_type/by_hour
    """
    permission_classes = [permissions.IsAuthenticated, IsSuperAdmin]

    def get(self, request):
        now = timezone.now()
        base = _PeriodCounterHelper.get_counters(LoginLog, now)
        qs = LoginLog.objects.all()

        from django.db.models import Count
        from django.db.models.functions import ExtractHour

        success_count = qs.filter(success=True).count()
        fail_count = qs.filter(success=False).count()
        by_success = [
            {'key': 'true' if row['success'] else 'false',
             'label': '成功' if row['success'] else '失败',
             'count': row['count']}
            for row in qs.values('success').annotate(count=Count('id'))
        ]
        type_label_map = {'jwt': '账号密码', 'sso': 'SSO', 'sso_mock': 'SSO Mock'}
        by_type = [
            {'key': row['login_type'] or 'unknown',
             'label': type_label_map.get(row['login_type'], row['login_type'] or '未知'),
             'count': row['count']}
            for row in qs.values('login_type').annotate(count=Count('id'))
        ]
        by_hour = [
            {'key': str(row['hour'] if row['hour'] is not None else -1),
             'label': f'{row["hour"]:02d}:00' if row['hour'] is not None else '未知',
             'count': row['count']}
            for row in qs.annotate(hour=ExtractHour('created_at'))
            .values('hour').annotate(count=Count('id'))
            .order_by('hour')
        ]

        data = {**base,
                'success_count': success_count,
                'fail_count': fail_count,
                'by_success': by_success,
                'by_type': by_type,
                'by_hour': by_hour}
        return Response(LoginLogStatsSerializer(data).data)


class OperationLogStatsView(APIView):
    """操作日志统计 - 超管

    GET /api/admin/logs/operation/stats/
    返回：total/today/this_week/this_month + by_action / by_target_type / top_operators
    """
    permission_classes = [permissions.IsAuthenticated, IsSuperAdmin]

    def get(self, request):
        now = timezone.now()
        base = _PeriodCounterHelper.get_counters(OperationLog, now)
        qs = OperationLog.objects.all()

        from django.db.models import Count
        action_label_map = {
            'create': '新建', 'update': '编辑', 'delete': '删除',
            'reset_password': '重置密码',
            'batch_delete': '批量删除', 'batch_reset_password': '批量重置密码',
        }
        by_action = [
            {'key': row['action'],
             'label': action_label_map.get(row['action'], row['action']),
             'count': row['count']}
            for row in qs.values('action').annotate(count=Count('id'))
        ]
        target_label_map = {'user': '用户', 'resource': '资源', 'category': '分类', 'department': '部门'}
        by_target_type = [
            {'key': row['target_type'],
             'label': target_label_map.get(row['target_type'], row['target_type']),
             'count': row['count']}
            for row in qs.values('target_type').annotate(count=Count('id'))
        ]
        top_operators_rows = (
            qs.values('operator_id', 'operator_username')
            .annotate(count=Count('id'))
            .order_by('-count')[:10]
        )
        top_operators = [
            {'key': str(row['operator_id']) if row['operator_id'] else 'anonymous',
             'label': row['operator_username'] or '匿名',
             'count': row['count']}
            for row in top_operators_rows
        ]

        data = {**base,
                'by_action': by_action,
                'by_target_type': by_target_type,
                'top_operators': top_operators}
        return Response(OperationLogStatsSerializer(data).data)


# ======================================================================
# 五、Dashboard 聚合接口
# ======================================================================
class LogsDashboardView(APIView):
    """超管日志仪表盘聚合接口

    GET /api/admin/logs/dashboard/?period=day|week|month
    功能：聚合 5 类日志（upload/query/sensitive/login/operation）+ 1 个平台累计块
         （totals = 全平台用户数 + 全平台上传文件总大小），单次请求拿全所有顶部卡片数据。
    参数：
        - period (str, optional, default='all'): day / week / month / all
          决定 5 个统计块的时间区间；totals 不受 period 影响。
          - day:   今日 0:00 ~ now
          - week:  本周一 0:00 ~ now
          - month: 本月 1 日 0:00 ~ now
          - all:   全量数据（blocks 与 start_at/end_at 范围一致）
    返回：
        {
            "period": str,
            "start_at": ISO 8601,  # 全平台数据起止时间（不受 period 影响）
            "end_at":   ISO 8601,  # 全平台数据起止时间（不受 period 影响）
            "totals":   {"users": int, "upload_total_size": int} 或 {"error": str},
            "blocks":   {upload, query, sensitive, login, operation} 各块
        }
    实现细节：
    - start_at / end_at：全平台日志起止时间，由 compute_data_range() 查 5 张日志表得出，不受 period 控制
    - blocks.* 区间：由 parse_period(period) 决定
    - 时间区间基于 Asia/Shanghai（settings.TIME_ZONE）
    - 6 个块各自 try/except 隔离失败（块计算放 services/dashboard.py）
    - view 不接 try/except（遵循规范 §9）
    - totals 与 blocks 平级，与 period 无关
    - 权限：IsSuperAdmin（超管专属，与其余 /admin/logs/* 接口一致）。
      首页「平台数据概览」的公开展示已拆出到 /api/platform/stats/
      （见 PublicStatsView），本接口 2026-09-02 收回为超管日志看板专用，
      全字段（by_*/top_operators 等）仅超管可见。
    """
    permission_classes = [permissions.IsAuthenticated, IsSuperAdmin]

    def get(self, request):
        period = request.query_params.get('period') or 'all'
        block_start, block_end = dashboard_svc.parse_period(period)
        data_range = dashboard_svc.compute_data_range()

        blocks = {
            'upload': dashboard_svc.compute_upload_block(block_start, block_end),
            'query': dashboard_svc.compute_query_block(block_start, block_end),
            'sensitive': dashboard_svc.compute_sensitive_block(block_start, block_end),
            'login': dashboard_svc.compute_login_block(block_start, block_end),
            'operation': dashboard_svc.compute_operation_block(block_start, block_end),
        }
        totals = dashboard_svc.compute_totals()

        start_at = data_range.get('start_at') or block_start
        end_at = data_range.get('end_at') or block_end

        return Response({
            'period': period,
            'start_at': start_at.isoformat(),
            'end_at': end_at.isoformat(),
            'totals': totals,
            'blocks': blocks,
        })


# ======================================================================
# 六、首页公开数据概览（游客可见，无需 Token）
# ======================================================================
class PublicStatsView(APIView):
    """首页「平台数据概览」公开接口

    GET /api/platform/stats/
    功能：首页公开展示 7 项全量累计指标（AllowAny，含游客，无需 Token），
         （产品决策 2026-09-02）替代此前误用 /admin/logs/dashboard/ 的公开展示。
    参数：不接收参数；固定全量口径，与 period 无关。
    返回：
        {
            "users": int | null,             # 用户总数（role=user，不含管理员）
            "storage_bytes": int | null,     # 存储总量（已收录资料占用字节）
            "docs": int | null,              # 资料总数（全部已上传资料）
            "queries": int | null,           # 查询总数（AI 问答累计）
            "avg_response_sec": float | null, # 平均响应时长（秒，response_ms>0 的均值）
            "sensitive_blocks": int | null,   # 敏感拦截（命中敏感词累计）
            "logins": int | null,            # 登录次数（平台累计登录）
        }
    安全约定：
    - 只返回以上聚合数字，无学院/操作人/error 原文等任何明细（口径见 compute_public_stats）
    - 单项计算失败 → 该项 null + 服务端日志，响应结构保持稳定
    - 服务端缓存 120s，避免游客每次刷新重复扫表
    """
    # 完全公开：不做任何认证，即使请求头携带过期/伪造 Token 也不影响访问
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    CACHE_KEY = 'public_platform_stats_v2'
    CACHE_TTL = 120

    def get(self, request):
        data = cache.get(self.CACHE_KEY)
        if data is None:
            data = dashboard_svc.compute_public_stats()

            # 整库不可用（七项全 null）→ 不缓存、503，前端走错误态而不是静默显示 0；
            # 仅局部失败（某项 null）仍缓存返回，其余真实数字照常展示。
            if all(value is None for value in data.values()):
                return Response(status=status.HTTP_503_SERVICE_UNAVAILABLE)
            cache.set(self.CACHE_KEY, data, self.CACHE_TTL)

        return Response(data)
