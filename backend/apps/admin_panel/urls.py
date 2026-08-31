"""Admin Panel - 路由

按规范 §7：用 .as_view() 而非 ViewSet + Router。
但既有 UserAdminViewSet 通过 Router 注册（既有行为，不破坏）；
日志管理全部用 path() + .as_view()。
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    AdminLoginLogDetailView,
    AdminLoginLogListView,
    AdminOperationLogDetailView,
    AdminOperationLogListView,
    AdminQueryLogDetailView,
    AdminQueryLogListView,
    AdminSensitiveLogDetailView,
    AdminSensitiveLogListView,
    AdminUploadLogDetailView,
    AdminUploadLogListView,
    BatchDeleteUsersView,
    BatchResetPasswordView,
    CollegeListView,
    CurrentUserView,
    DepartmentDestroyView,
    DepartmentListView,
    LoginLogStatsView,
    LogsDashboardView,
    OperationLogStatsView,
    QueryLogStatsView,
    UploadLogStatsView,
    UserAdminViewSet,
)


router = DefaultRouter()
router.register(r'users', UserAdminViewSet, basename='admin-users')


urlpatterns = [
    # 具体路由（不被 Router 吞）放在 Router 之前
    path('users/batch_reset_password/', BatchResetPasswordView.as_view(), name='batch-reset-password'),
    path('users/batch_delete/', BatchDeleteUsersView.as_view(), name='batch-delete-users'),
    path('colleges/', CollegeListView.as_view(), name='college-list'),
    path('departments/', DepartmentListView.as_view(), name='department-list'),
    path('departments/<int:pk>/', DepartmentDestroyView.as_view(), name='department-delete'),
    path('me/', CurrentUserView.as_view(), name='current-user'),

    # ---- 超管日志管理（ListAPIView + RetrieveAPIView）----
    # 列表
    path('logs/upload/', AdminUploadLogListView.as_view(), name='admin-log-upload-list'),
    path('logs/query/', AdminQueryLogListView.as_view(), name='admin-log-query-list'),
    path('logs/sensitive/', AdminSensitiveLogListView.as_view(), name='admin-log-sensitive-list'),
    path('logs/login/', AdminLoginLogListView.as_view(), name='admin-log-login-list'),
    path('logs/operation/', AdminOperationLogListView.as_view(), name='admin-log-operation-list'),

    # 详情
    path('logs/upload/<int:pk>/', AdminUploadLogDetailView.as_view(), name='admin-log-upload-detail'),
    path('logs/query/<int:pk>/', AdminQueryLogDetailView.as_view(), name='admin-log-query-detail'),
    path('logs/sensitive/<int:pk>/', AdminSensitiveLogDetailView.as_view(), name='admin-log-sensitive-detail'),
    path('logs/login/<int:pk>/', AdminLoginLogDetailView.as_view(), name='admin-log-login-detail'),
    path('logs/operation/<int:pk>/', AdminOperationLogDetailView.as_view(), name='admin-log-operation-detail'),

    # 统计（4 类）
    path('logs/upload/stats/', UploadLogStatsView.as_view(), name='admin-log-upload-stats'),
    path('logs/query/stats/', QueryLogStatsView.as_view(), name='admin-log-query-stats'),
    path('logs/login/stats/', LoginLogStatsView.as_view(), name='admin-log-login-stats'),
    path('logs/operation/stats/', OperationLogStatsView.as_view(), name='admin-log-operation-stats'),

    # Dashboard 聚合
    path('logs/dashboard/', LogsDashboardView.as_view(), name='admin-log-dashboard'),

    # Router 必须放在最后
    path('', include(router.urls)),
]
