from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import UploadLogViewSet

router = DefaultRouter()
router.register(r'upload-log', UploadLogViewSet, basename='upload-log')

urlpatterns = [
    path('', include(router.urls)),
]
