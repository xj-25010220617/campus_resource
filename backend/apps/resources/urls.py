from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import UploadLogViewSet

router = DefaultRouter()
router.register(r'upload-log', UploadLogViewSet, basename='upload-log')

# 资源相关接口（W2 实现）
urlpatterns = [
    path('', include(router.urls)),
]
