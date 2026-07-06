from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    LoggedTokenObtainPairView,
    MeView,
    ChangePasswordView,
    SsoLoginView,
    SsoCallbackView,
)


urlpatterns = [
    # JWT 认证（使用包装版，写 LoginLog）
    path('login/', LoggedTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    # 个人信息
    path('me/', MeView.as_view(), name='auth_me'),

    # 修改密码
    path('change-password/', ChangePasswordView.as_view(), name='auth_change_password'),

    # SSO（暂未对接，预留接口）
    path('sso/login/', SsoLoginView.as_view(), name='sso_login'),
    path('sso/callback/', SsoCallbackView.as_view(), name='sso_callback'),
] 
