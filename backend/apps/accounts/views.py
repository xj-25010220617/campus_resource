import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.admin_panel.utils import write_login_log
from utils.error_codes import ErrorCode

from .serializers import UserSerializer, ChangePasswordSerializer


logger = logging.getLogger(__name__)
User = get_user_model()


class LoggedTokenObtainPairView(TokenObtainPairView):
    """
    JWT 登录接口（带审计日志埋点）

    POST /api/auth/login/
    功能：账号密码登录，签发 access / refresh；同步写入 admin_login_log
          （成功：login_type=jwt / success=true / user=当前用户；
           失败：login_type=jwt / success=false / fail_reason=异常描述）
    参数：
        - username (string, required): 登录用户名
        - password (string, required): 密码
    返回：{"access": "...", "refresh": "..."}
    """

    def post(self, request, *args, **kwargs):
        try:
            response = super().post(request, *args, **kwargs)
        except Exception as exc:
            write_login_log(
                request,
                login_type='jwt',
                success=False,
                fail_reason=str(exc) or '凭证错误或账号不存在',
            )
            raise

        if response.status_code == status.HTTP_200_OK:
            username = (request.data.get('username') or '').strip()
            user = User.objects.filter(username=username).first() if username else None
            write_login_log(
                request,
                login_type='jwt',
                success=True,
                user=user,
                username=username,
            )
        else:
            write_login_log(
                request,
                login_type='jwt',
                success=False,
                username=(request.data.get('username') or ''),
                fail_reason=_extract_jwt_fail_reason(response.data),
            )
        return response


def _extract_jwt_fail_reason(data) -> str:
    """从 SimpleJWT 错误响应中提取失败原因文本"""
    detail = (data or {}).get('detail') if isinstance(data, dict) else None
    if detail:
        return str(detail)
    return '凭证错误或账号不存在'


# ------------------------------------------------------------------
# Mock SSO 用户映射（仅开发测试用，正式对接后删除）
# code → 自动创建/查找的用户信息
# ------------------------------------------------------------------
MOCK_SSO_USERS = {
    'mock_super_admin': {
        'sso_uid': 'sso_10000',
        'username': 'sso_super_admin',
        'role': 'super_admin',
        'first_name': 'SSO',
        'last_name': '超级管理员',
        'email': 'sso_super_admin@nsu.edu.cn',
    },
    'mock_admin': {
        'sso_uid': 'sso_10001',
        'username': 'sso_admin',
        'role': 'admin',
        'first_name': 'SSO',
        'last_name': '管理员',
        'email': 'sso_admin@nsu.edu.cn',
    },
    'mock_college_admin': {
        'sso_uid': 'sso_10004',
        'username': 'sso_college_admin',
        'role': 'college_admin',
        'first_name': '学院',
        'last_name': '管理员',
        'email': 'sso_college_admin@nsu.edu.cn',
        'college_code': 'CS',
    },
    'mock_dept_admin': {
        'sso_uid': 'sso_10005',
        'username': 'sso_dept_admin',
        'role': 'dept_admin',
        'first_name': '部门',
        'last_name': '管理员',
        'email': 'sso_dept_admin@nsu.edu.cn',
        'department_id': 13,
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
            write_login_log(
                request,
                login_type='sso_mock',
                success=False,
                fail_reason='缺少 code 参数',
            )
            return Response(
                {'detail': '缺少 code 参数', 'code': ErrorCode.VALIDATION_ERROR.value},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_info = MOCK_SSO_USERS.get(code)
        if not user_info:
            write_login_log(
                request,
                login_type='sso_mock',
                success=False,
                username=code,
                fail_reason=f'无效的 SSO 测试码: {code}',
            )
            return Response(
                {
                    'detail': f'无效的 SSO 测试码: {code}',
                    'available_codes': list(MOCK_SSO_USERS.keys()),
                    'code': ErrorCode.INVALID_SSO_CODE.value,
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

        write_login_log(
            request,
            login_type='sso_mock',
            success=True,
            user=user,
            username=user.username,
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
        from apps.accounts.models import College
        from apps.classify.models import Department

        with transaction.atomic():
            user = User.objects.filter(sso_uid=user_info['sso_uid']).first()
            if user:
                # 已有用户：更新基本信息
                user.email = user_info.get('email', user.email)
                user.first_name = user_info.get('first_name', user.first_name)
                user.last_name = user_info.get('last_name', user.last_name)
                update_fields = ['email', 'first_name', 'last_name']
                user.save(update_fields=update_fields)
                return user, False

            # 解析 college（college_admin / user 需要）
            college = None
            college_code = user_info.get('college_code')
            if college_code:
                college = College.objects.filter(code=college_code).first()

            # 解析 department（dept_admin 需要）
            department = None
            department_id = user_info.get('department_id')
            if department_id:
                department = Department.objects.filter(id=department_id).first()

            role = user_info.get('role', 'user')

            # 创建新用户（SSO 用户无本地密码）
            user = User.objects.create_user(
                username=user_info['username'],
                email=user_info.get('email', ''),
                first_name=user_info.get('first_name', ''),
                last_name=user_info.get('last_name', ''),
                role=role,
                sso_uid=user_info['sso_uid'],
                college=college,
                department=department,
            )
            user.set_unusable_password()
            user.save(update_fields=['password'])
            return user, True


class LogoutView(APIView):
    """
    登出

    POST /api/auth/logout/
    功能：将 refresh token 加入黑名单，使 refresh 失效
    参数：
        - refresh (string, required): 登录时获取的 refresh token
    返回：{"detail": "已登出"}
    注意：客户端需同时丢弃本地 access token
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        refresh_token = request.data.get('refresh')
        if refresh_token:
            try:
                token = RefreshToken(refresh_token)
                token.blacklist()
                logger.info('Token 已加入黑名单')
            except Exception:
                logger.warning('Token 黑名单失败（可能未启用 blacklist app）')
        return Response({'detail': '已登出'}, status=status.HTTP_200_OK)


class DingtalkQrView(APIView):
    """
    钉钉扫码登录 — 获取扫码地址

    GET /api/auth/dingtalk/qr/
    功能：返回钉钉 OAuth 授权 URL，前端生成二维码供用户扫码
    返回：{"auth_url": "https://login.dingtalk.com/oauth2/auth?...", "qr_url": "..."}
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        app_key = getattr(settings, 'DINGTALK_APP_KEY', '')
        # 优先用前端传的回调地址（手机扫码后跳回前端页面），否则用后端地址
        redirect_uri = (
            request.query_params.get('redirect_uri')
            or request.build_absolute_uri('/api/auth/dingtalk/callback/')
        )

        if not app_key:
            return Response(
                {
                    'detail': '钉钉登录未配置，请在 .env 中设置 DINGTALK_APP_KEY 和 DINGTALK_APP_SECRET',
                    'config_hint': '打开钉钉开放平台 → 创建应用 → 获取 AppKey 和 AppSecret',
                },
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )

        import uuid
        from urllib.parse import quote

        state = uuid.uuid4().hex
        auth_url = (
            'https://login.dingtalk.com/oauth2/auth'
            f'?redirect_uri={quote(redirect_uri, safe="")}'
            '&response_type=code'
            f'&client_id={app_key}'
            '&scope=openid'
            f'&state={state}'
            '&prompt=consent'
        )
        return Response({
            'auth_url': auth_url,
            'usage': '前端将此 URL 生成二维码，用户用钉钉扫码后在钉钉内确认登录',
        })


class DingtalkCallbackView(APIView):
    """
    钉钉扫码登录 — 回调

    POST /api/auth/dingtalk/callback/
    功能：接收前端传来的 authCode，调用钉钉 API 换取用户信息，签发 JWT
    参数：
        - authCode (string, required): 钉钉扫码后回调的授权码
    返回：{"access": "...", "refresh": "...", "user": {...}}
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        auth_code = request.data.get('authCode') or request.data.get('code')

        if not auth_code:
            write_login_log(
                request,
                login_type='sso',
                success=False,
                fail_reason='缺少 authCode 参数',
            )
            return Response(
                {'detail': '缺少 authCode 参数', 'code': ErrorCode.VALIDATION_ERROR.value},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 调用钉钉 API 换取用户信息
        user_info = self._get_dingtalk_user(auth_code)
        if not user_info:
            write_login_log(
                request,
                login_type='sso',
                success=False,
                fail_reason='钉钉授权失败，请重新扫码',
            )
            return Response(
                {'detail': '钉钉授权失败，请重新扫码', 'code': ErrorCode.DINGTALK_AUTH_FAILED.value},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # 查找或创建本地用户
        user, created = self._get_or_create_user(user_info)

        refresh = RefreshToken.for_user(user)
        logger.info(
            '钉钉扫码登录: union_id=%s user=%s created=%s',
            user_info.get('union_id'), user.username, created,
        )

        write_login_log(
            request,
            login_type='sso',
            success=True,
            user=user,
            username=user.username,
        )

        return Response({
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': UserSerializer(user).data,
            'created': created,
        }, status=status.HTTP_200_OK)

    def _get_dingtalk_user(self, auth_code):
        """调用钉钉 OpenAPI 换取用户信息"""
        from utils.http_client import ExternalClient

        app_key = getattr(settings, 'DINGTALK_APP_KEY', '')
        app_secret = getattr(settings, 'DINGTALK_APP_SECRET', '')

        try:
            # 步骤1：authCode → accessToken
            token_resp = ExternalClient.post(
                'dingtalk',
                'https://api.dingtalk.com/v1.0/oauth2/userAccessToken',
                json={
                    'clientId': app_key,
                    'clientSecret': app_secret,
                    'code': auth_code,
                    'grantType': 'authorization_code',
                },
            )
            token_data = token_resp.json()
            access_token = token_data.get('accessToken')
            if not access_token:
                logger.error('钉钉获取 accessToken 失败: %s', token_data)
                return None

            # 步骤2：accessToken → 用户信息
            user_resp = ExternalClient.get(
                'dingtalk',
                'https://api.dingtalk.com/v1.0/contact/users/me',
                headers={'x-acs-dingtalk-access-token': access_token},
            )
            user_data = user_resp.json()
            logger.info('钉钉用户信息: nick=%s unionId=%s', user_data.get('nick'), user_data.get('unionId'))
            return user_data

        except Exception as e:
            logger.error('钉钉 API 调用失败: %s', e)
            return None

    @staticmethod
    def _get_or_create_user(user_info):
        """根据钉钉 unionId 查找或创建本地用户"""
        union_id = user_info.get('unionId', '')
        nick = user_info.get('nick', '')
        email = user_info.get('email', '')
        mobile = user_info.get('mobile', '')

        with transaction.atomic():
            user = User.objects.filter(sso_uid=union_id).first()
            if user:
                user.email = email or user.email
                user.phone = mobile or user.phone
                user.save(update_fields=['email', 'phone'])
                return user, False

            user = User.objects.create_user(
                username=f'dd_{nick}' if nick else f'dd_{union_id[:12]}',
                email=email,
                first_name=nick,
                last_name='',
                phone=mobile,
                role='user',
                sso_uid=union_id,
            )
            user.set_unusable_password()
            user.save(update_fields=['password'])
            return user, True


class DingtalkRedirectView(APIView):
    """
    钉钉扫码回调 — 直接返回 Token 页面

    GET /api/auth/dingtalk/redirect/
    功能：钉钉扫码后跳转到此接口，后端换 token 并返回 HTML 页面，
          页面自动存 token 到 localStorage 并跳转前端首页。
    参数：
        - code (string, required): 钉钉授权码
        - frontend_url (string, optional): 登录成功后跳转的前端地址
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        import requests

        auth_code = request.query_params.get('code')
        if not auth_code:
            return Response(
                {'detail': '缺少 code 参数'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 调用钉钉 API 换取用户信息
        app_key = getattr(settings, 'DINGTALK_APP_KEY', '')
        app_secret = getattr(settings, 'DINGTALK_APP_SECRET', '')
        try:
            token_resp = requests.post(
                'https://api.dingtalk.com/v1.0/oauth2/userAccessToken',
                json={
                    'clientId': app_key, 'clientSecret': app_secret,
                    'code': auth_code, 'grantType': 'authorization_code',
                },
                timeout=10,
            )
            token_data = token_resp.json()
            access_token = token_data.get('accessToken')
            if not access_token:
                logger.error('钉钉获取 accessToken 失败: %s', token_data)
                return Response(
                    {'detail': '钉钉授权失败，请重新扫码'},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            user_resp = requests.get(
                'https://api.dingtalk.com/v1.0/contact/users/me',
                headers={'x-acs-dingtalk-access-token': access_token},
                timeout=10,
            )
            user_data = user_resp.json()
        except Exception as e:
            logger.error('钉钉 API 调用失败: %s', e)
            return Response(
                {'detail': '钉钉服务异常，请稍后重试'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # 查找或创建本地用户
        user, created = self._find_or_create(user_data)
        refresh = RefreshToken.for_user(user)

        logger.info(
            '钉钉扫码登录: union_id=%s user=%s created=%s',
            user_data.get('unionId'), user.username, created,
        )

        frontend_url = request.query_params.get(
            'frontend_url',
            getattr(settings, 'FRONTEND_URL', 'http://localhost:5173'),
        )

        return self._html_page(
            access=str(refresh.access_token),
            refresh=str(refresh),
            user=UserSerializer(user).data,
            frontend_url=frontend_url,
        )

    @staticmethod
    def _find_or_create(user_data):
        union_id = user_data.get('unionId', '')
        nick = user_data.get('nick', '')
        email = user_data.get('email', '')
        mobile = user_data.get('mobile', '')
        with transaction.atomic():
            user = User.objects.filter(sso_uid=union_id).first()
            if user:
                user.email = email or user.email
                user.phone = mobile or user.phone
                user.save(update_fields=['email', 'phone'])
                return user, False
            user = User.objects.create_user(
                username=f'dd_{nick}' if nick else f'dd_{union_id[:12]}',
                email=email, first_name=nick, last_name='',
                phone=mobile, role='user', sso_uid=union_id,
            )
            user.set_unusable_password()
            user.save(update_fields=['password'])
            return user, True

    @staticmethod
    def _html_page(access, refresh, user, frontend_url):
        import json

        user_json = json.dumps(user, ensure_ascii=False)
        # 防止 JSON 中的 </ 提前关闭 <script> 标签
        user_json_safe = user_json.replace('</', '<\\/')

        template = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>钉钉登录</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;min-height:-webkit-fill-available;background:#f0f2f5;padding:20px}
  .card{background:#fff;border-radius:16px;padding:36px 28px 28px;text-align:center;box-shadow:0 4px 24px rgba(0,0,0,0.08);max-width:320px;width:100%}
  .check{width:64px;height:64px;border-radius:50%;background:#f6ffed;display:flex;align-items:center;justify-content:center;margin:0 auto 20px}
  .check svg{width:36px;height:36px}
  h2{font-size:20px;color:#1a1a1a;margin-bottom:6px;font-weight:600}
  .uname{font-size:14px;color:#8c8c8c;margin-bottom:20px}
  .btn{display:block;width:100%;padding:13px 0;background:#1890ff;color:#fff;border:none;border-radius:8px;font-size:16px;letter-spacing:1px;cursor:pointer;-webkit-appearance:none}
  .btn:active{background:#096dd9}
  .tip{font-size:12px;color:#bfbfbf;margin-top:14px;display:none}
  .err{color:#ff4d4f;font-size:14px;margin-top:12px}
</style>
</head>
<body>
<div class="card">
  <div class="check">
    <svg viewBox="0 0 24 24" fill="none" stroke="#52c41a" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
  </div>
  <h2>登录成功</h2>
  <p class="uname" id="uname"></p>
  <button class="btn" onclick="goBack()">返回应用</button>
  <p class="tip" id="tip">请手动切换回应用</p>
  <p class="err" id="err"></p>
</div>

<script type="application/json" id="user-json">__USER_JSON__</script>
<script>
// 先把 token 存好 — 这一步独立于 JSON 解析，确保不出错
localStorage.setItem("access_token", "__ACCESS__");
localStorage.setItem("refresh_token", "__REFRESH__");

(function(){
  var el = document.getElementById("user-json");
  var jsonStr = el.textContent || el.innerText || "";
  try {
    var user = JSON.parse(jsonStr);
    localStorage.setItem("user", jsonStr);
    var name = (user.last_name || "") + (user.first_name || user.username || "");
    document.getElementById("uname").innerText = name;
    document.title = name + " - 登录成功";
  } catch(e) {
    document.getElementById("err").innerText = "登录成功，请返回应用";
  }
})();

function goBack() {
  try { window.close(); } catch(_) {}
  var tip = document.getElementById("tip");
  tip.style.display = "block";
  setTimeout(function(){ tip.style.display = "none"; }, 3000);
}
</script>
</body>
</html>'''
        html = template.replace('__USER_JSON__', user_json_safe)
        html = html.replace('__ACCESS__', access)
        html = html.replace('__REFRESH__', refresh)
        return Response(html, content_type='text/html; charset=utf-8')
