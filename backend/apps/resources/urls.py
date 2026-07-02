from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import ResourceListView, UploadLogViewSet

router = DefaultRouter()
router.register(r'upload-log', UploadLogViewSet, basename='upload-log')

urlpatterns = [
    path('', ResourceListView.as_view(), name='resource_list'),
    path('', include(router.urls)),
]
