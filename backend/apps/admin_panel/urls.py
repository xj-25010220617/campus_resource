"""Admin Panel - 用户管理路由"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    BatchDeleteUsersView,
    BatchResetPasswordView,
    CollegeListView,
    ResetPasswordView,
    UserAdminViewSet,
)


router = DefaultRouter()
router.register(r'users', UserAdminViewSet, basename='admin-users')

urlpatterns = [
    # 具体路由必须放在 Router 之前，避免被 {pk} 匹配
    path('users/batch_reset_password/', BatchResetPasswordView.as_view(), name='batch-reset-password'),
    path('users/batch_delete/', BatchDeleteUsersView.as_view(), name='batch-delete-users'),
    path('colleges/', CollegeListView.as_view(), name='college-list'),
    path('users/<int:pk>/reset_password/', ResetPasswordView.as_view(), name='reset-password'),
    # Router 必须放在最后
    path('', include(router.urls)),
]
