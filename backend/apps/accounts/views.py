import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .serializers import UserSerializer, ChangePasswordSerializer


logger = logging.getLogger(__name__)
User = get_user_model()

# ------------------------------------------------------------------
# Mock SSO 用户映射（仅开发测试用，正式对接后删除）
# code → 自动创建/查找的用户信息
# ------------------------------------------------------------------
MOCK_SSO_USERS = {
    'mock_admin': {
        'sso_uid': 'sso_10001',
        'username': 'sso_admin',
        'role': 'admin',
        'first_name': 'SSO',
        'last_name': '管理员',
        'email': 'sso_admin@nsu.edu.cn',
    },
    'mock_teacher': {
        'sso_uid': 'sso_10002',
        'username': 'sso_teacher',
        'role': 'user',
        'first_name': 'SSO',
        'last_name': '教师',
        'email': 'sso_teacher@nsu.edu.cn',
    },
    'mock_student': {
        'sso_uid': 'sso_10003',
        'username': 'sso_student',
        'role': 'user',
        'first_name': 'SSO',
        'last_name': '学生',
        'email': 'sso_student@nsu.edu.cn',
    },
}


@extend_schema(
    summary='当前用户信息',
    description='获取当前登录用户的个人信息，或更新个人资料',
    tags=['用户管理'],
    operation_id='当前用户信息',
)
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
    功能：
        - Mock 模式（默认）：返回可用测试码列表，前端可跳转到回调接口完成 SSO 模拟
        - 正式模式：HTTP 302 重定向至学校统一认证页面
    返回：
        - Mock: {"mock_codes": [...], "redirect_url_template": "..."}
        - 正式: HTTP 302
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        is_mock = getattr(settings, 'SSO_MOCK_ENABLED', True)

        if is_mock:
            # Mock 模式：返回可用测试码，前端选择后构造回调请求
            mock_codes = []
            for code, info in MOCK_SSO_USERS.items():
                mock_codes.append({
                    'code': code,
                    'username': info['username'],
                    'role': info['role'],
                    'description': f'{info["last_name"]}{info["first_name"]}（{info["role"]}）',
                })
            return Response({
                'mode': 'mock',
                'mock_codes': mock_codes,
                'usage': '使用任一 code 调用 POST /api/auth/sso/callback/ 即可登录',
            })

        # 正式模式：重定向到学校 SSO
        sso_url = getattr(settings, 'SSO_LOGIN_URL', '')
        if sso_url:
            return Response(
                {'detail': f'请访问学校统一认证: {sso_url}', 'sso_url': sso_url},
                status=status.HTTP_200_OK,
            )
        return Response(
            {'detail': 'SSO 登录暂未配置，请联系管理员'},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )


class SsoCallbackView(APIView):
    """
    SSO 登录回调

    POST /api/auth/sso/callback/
    功能：
        - Mock 模式：接受 code 参数，自动创建/查找用户，签发 JWT
        - 正式模式：验证 SAML/OAuth 票据，签发 JWT
    参数：
        - code (string, required, Mock 模式): 测试用户码（见 GET /api/auth/sso/login/）
        - saml_response / code (string, required, 正式模式): SSO 认证票据
    返回：{"access": "...", "refresh": "...", "user": {...}}
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        is_mock = getattr(settings, 'SSO_MOCK_ENABLED', True)

        if is_mock:
            return self._handle_mock_callback(request)

        # TODO: 对接学校 SAML/OAuth SSO 后实现实际校验逻辑
        return Response(
            {'detail': 'SSO 回调暂未对接'},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )

    def _handle_mock_callback(self, request):
        """Mock 模式回调：根据 code 查找/创建用户并签发 JWT"""
        code = request.data.get('code') or request.query_params.get('code')

        if not code:
            return Response(
                {'detail': '缺少 code 参数', 'code': 'VALIDATION_ERROR'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_info = MOCK_SSO_USERS.get(code)
        if not user_info:
            return Response(
                {
                    'detail': f'无效的 SSO 测试码: {code}',
                    'available_codes': list(MOCK_SSO_USERS.keys()),
                    'code': 'INVALID_SSO_CODE',
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # 查找已有用户或创建新用户
        user, created = self._get_or_create_sso_user(user_info)

        # 签发 JWT
        refresh = RefreshToken.for_user(user)

        logger.info(
            'SSO Mock 登录: code=%s user=%s created=%s',
            code, user.username, created,
        )

        return Response({
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': UserSerializer(user).data,
            'created': created,
        }, status=status.HTTP_200_OK)

    @staticmethod
    def _get_or_create_sso_user(user_info):
        """根据 sso_uid 查找或创建用户"""
        with transaction.atomic():
            user = User.objects.filter(sso_uid=user_info['sso_uid']).first()
            if user:
                # 已有用户：更新基本信息
                user.email = user_info.get('email', user.email)
                user.first_name = user_info.get('first_name', user.first_name)
                user.last_name = user_info.get('last_name', user.last_name)
                user.save(update_fields=['email', 'first_name', 'last_name'])
                return user, False

            # 创建新用户（SSO 用户无本地密码，用 unusable password）
            user = User.objects.create_user(
                username=user_info['username'],
                email=user_info.get('email', ''),
                first_name=user_info.get('first_name', ''),
                last_name=user_info.get('last_name', ''),
                role=user_info.get('role', 'user'),
                sso_uid=user_info['sso_uid'],
            )
            user.set_unusable_password()
            user.save(update_fields=['password'])
            return user, True
