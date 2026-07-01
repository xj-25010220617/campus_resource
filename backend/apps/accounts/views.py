import logging

from django.contrib.auth import get_user_model
from django.http import HttpResponseRedirect
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import UserSerializer, ChangePasswordSerializer


logger = logging.getLogger(__name__)
User = get_user_model()


class MeView(generics.RetrieveUpdateAPIView):
    """
    当前登录用户信息

    GET /api/auth/me/
    功能：获取当前登录用户的个人信息，含角色、学院等
    返回：用户信息对象，包含 id/username/role/college/phone/avatar 等字段

    PUT /api/auth/me/
    功能：更新个人资料
    参数：
        - phone (string, optional): 手机号
        - avatar (string, optional): 头像 URL
        - first_name (string, optional): 名
        - last_name (string, optional): 姓
        - email (string, optional): 邮箱
    返回：更新后的完整用户信息对象
    """
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


class ChangePasswordView(APIView):
    """
    修改密码

    POST /api/auth/change-password/
    功能：用户自己修改密码，需提供旧密码验证
    参数：
        - old_password (string, required): 旧密码
        - new_password (string, required): 新密码
    返回：{"detail": "密码修改成功"}
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data,
            context={'request': request},
        )
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data['new_password'])
        request.user.save()
        logger.info(f'用户 {request.user.username} 修改了密码')
        return Response({'detail': '密码修改成功'}, status=status.HTTP_200_OK)


class SsoLoginView(APIView):
    """
    SSO 登录跳转

    GET /api/auth/sso/login/
    功能：重定向至学校统一认证页面
    返回：HTTP 302 重定向
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        # TODO: 对接学校 SAML/OAuth SSO 后，替换为实际重定向逻辑
        return Response(
            {'detail': 'SSO 登录暂未对接，请使用 /api/auth/login/ 进行 JWT 登录'},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )


class SsoCallbackView(APIView):
    """
    SSO 登录回调

    POST /api/auth/sso/callback/
    功能：SSO 回调，验证 SAML/OAuth 票据，签发 JWT
    参数：
        - saml_response / code (string, required): SSO 认证票据
    返回：{"access": "...", "refresh": "...", "user": {...}}
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        # TODO: 对接学校 SAML/OAuth SSO 后实现实际校验逻辑
        # 1. 验证 SAML Response 或 OAuth code
        # 2. 根据 sso_uid 查找或创建 User
        # 3. 签发 JWT
        return Response(
            {'detail': 'SSO 回调暂未对接'},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )
