"""Admin Panel - 用户管理路由"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    BatchDeleteUsersView,
    BatchResetPasswordView,
    CollegeListView,
    CurrentUserView,
    DepartmentListView,
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
    path('me/', CurrentUserView.as_view(), name='current-user'),
    # Router 内的 @action(detail=True, url_path='reset_password') 自动生成 users/{pk}/reset_password/
    # Router 必须放在最后
    path('', include(router.urls)),
]