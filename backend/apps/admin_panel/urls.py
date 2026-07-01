"""Admin Panel - 用户管理路由"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import UserAdminViewSet


router = DefaultRouter()
router.register(r'users', UserAdminViewSet, basename='admin-users')

urlpatterns = [
    path('', include(router.urls)),
]
