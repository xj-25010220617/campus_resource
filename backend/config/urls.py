"""URL configuration for campus resource management system."""
from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path('admin/', admin.site.urls),
    # API docs
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    # App routes
    path('api/auth/', include('apps.accounts.urls')),
    path('api/', include('apps.classify.urls')),
    path('api/knowledge/', include('apps.knowledge.urls')),
    path('api/chat/', include('apps.chat.urls')),
    path('api/feedback/', include('apps.feedback.urls')),
    path('api/admin/', include('apps.admin_panel.urls')),
    path('api/resources/', include('apps.resources.urls')),
    path('api/', include('apps.faq.urls')),
]
