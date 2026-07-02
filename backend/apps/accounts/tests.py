"""认证模块单元测试 — SSO / 登录 / 个人信息 / 修改密码"""
from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from .models import User, College


class SsoMockTests(TestCase):
    """SSO Mock 模式测试"""

    def setUp(self):
        self.client = APIClient()
        # 确保 SSO Mock 开启
        self._sso_mock_original = getattr(settings, 'SSO_MOCK_ENABLED', True)

    # ---------- GET /api/auth/sso/login/ ----------

    def test_sso_login_returns_mock_codes(self):
        """Mock 模式 — GET 返回可用测试码列表"""
        r = self.client.get('/api/auth/sso/login/')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['mode'], 'mock')
        self.assertIn('mock_codes', r.data)
        self.assertGreater(len(r.data['mock_codes']), 0)
        # 验证每个 code 的结构
        for item in r.data['mock_codes']:
            self.assertIn('code', item)
            self.assertIn('username', item)
            self.assertIn('role', item)

    def test_sso_login_returns_usage_hint(self):
        """Mock 模式 — 返回使用说明"""
        r = self.client.get('/api/auth/sso/login/')
        self.assertIn('usage', r.data)

    # ---------- POST /api/auth/sso/callback/ ----------

    def test_callback_creates_new_user(self):
        """Mock 模式 — 传入有效 code 创建新用户并返回 JWT"""
        code = 'mock_admin'
        self.assertFalse(User.objects.filter(sso_uid='sso_10001').exists())

        r = self.client.post('/api/auth/sso/callback/', {'code': code}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK)

        # JWT 字段
        self.assertIn('access', r.data)
        self.assertIn('refresh', r.data)
        self.assertIn('user', r.data)
        self.assertTrue(r.data['created'])

        # 用户已创建
        user = User.objects.get(sso_uid='sso_10001')
        self.assertEqual(user.username, 'sso_admin')
        self.assertEqual(user.role, 'admin')
        self.assertFalse(user.has_usable_password())

    def test_callback_returns_existing_user(self):
        """Mock 模式 — 重复回调返回已有用户，不重复创建"""
        code = 'mock_student'

        # 第一次：创建
        r1 = self.client.post('/api/auth/sso/callback/', {'code': code}, format='json')
        self.assertTrue(r1.data['created'])

        # 第二次：返回已有用户
        r2 = self.client.post('/api/auth/sso/callback/', {'code': code}, format='json')
        self.assertFalse(r2.data['created'])
        self.assertEqual(r2.data['user']['username'], 'sso_student')

        # 数据库仍只有一条
        self.assertEqual(User.objects.filter(sso_uid='sso_10003').count(), 1)

    def test_callback_updates_existing_user_info(self):
        """Mock 模式 — 重复回调更新已有用户的姓名和邮箱"""
        code = 'mock_teacher'
        self.client.post('/api/auth/sso/callback/', {'code': code}, format='json')

        # 手动改掉用户信息
        User.objects.filter(sso_uid='sso_10002').update(first_name='旧', last_name='名', email='old@test.com')

        # 再次回调：应更新
        r = self.client.post('/api/auth/sso/callback/', {'code': code}, format='json')
        self.assertFalse(r.data['created'])
        user = User.objects.get(sso_uid='sso_10002')
        self.assertEqual(user.first_name, 'SSO')
        self.assertEqual(user.last_name, '教师')

    def test_callback_all_three_codes_work(self):
        """Mock 模式 — 三个测试码都能登录"""
        for code in ['mock_admin', 'mock_teacher', 'mock_student']:
            r = self.client.post('/api/auth/sso/callback/', {'code': code}, format='json')
            self.assertEqual(r.status_code, status.HTTP_200_OK, f'code={code} failed')
            self.assertIn('access', r.data)

    # ---------- 错误场景 ----------

    def test_callback_missing_code(self):
        """缺少 code 参数 — 返回 400"""
        r = self.client.post('/api/auth/sso/callback/', {}, format='json')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_callback_invalid_code(self):
        """无效 code — 返回 401 并列出可用值"""
        r = self.client.post('/api/auth/sso/callback/', {'code': 'bad_code'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn('available_codes', r.data)
        self.assertIn('mock_admin', r.data['available_codes'])

    def test_callback_empty_string_code(self):
        """空字符串 code — 返回 400"""
        r = self.client.post('/api/auth/sso/callback/', {'code': ''}, format='json')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_callback_sso_uid_unique(self):
        """同一 sso_uid 不会创建重复用户"""
        self.client.post('/api/auth/sso/callback/', {'code': 'mock_admin'}, format='json')
        self.client.post('/api/auth/sso/callback/', {'code': 'mock_admin'}, format='json')
        self.assertEqual(User.objects.filter(sso_uid='sso_10001').count(), 1)

    # ---------- JWT 有效性 ----------

    def test_callback_jwt_can_access_protected_endpoint(self):
        """SSO 返回的 access token 可访问受保护接口"""
        r = self.client.post('/api/auth/sso/callback/', {'code': 'mock_admin'}, format='json')
        access = r.data['access']

        client2 = APIClient()
        client2.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        r2 = client2.get('/api/auth/me/')
        self.assertEqual(r2.status_code, status.HTTP_200_OK)
        self.assertEqual(r2.data['username'], 'sso_admin')


class SsoProductionModeTests(TestCase):
    """SSO 正式模式（关闭 Mock）"""

    def setUp(self):
        self.client = APIClient()

    @override_settings(SSO_MOCK_ENABLED=False)
    def test_sso_login_without_config(self):
        """Mock 关闭 + SSO URL 未配置 — 返回 501"""
        r = self.client.get('/api/auth/sso/login/')
        self.assertEqual(r.status_code, status.HTTP_501_NOT_IMPLEMENTED)

    @override_settings(SSO_MOCK_ENABLED=False, SSO_LOGIN_URL='https://sso.nsu.edu.cn/login')
    def test_sso_login_with_url(self):
        """Mock 关闭 + SSO URL 已配置 — 返回 SSO 地址"""
        r = self.client.get('/api/auth/sso/login/')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn('sso_url', r.data)

    @override_settings(SSO_MOCK_ENABLED=False)
    def test_callback_not_implemented(self):
        """Mock 关闭 — 回调返回 501（未对接）"""
        r = self.client.post('/api/auth/sso/callback/', {'code': 'x'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_501_NOT_IMPLEMENTED)


class LoginApiTests(TestCase):
    """JWT 登录 / 刷新 / 个人信息 / 修改密码"""

    @classmethod
    def setUpTestData(cls):
        cls.college = College.objects.create(name='测试学院', code='TEST')
        cls.user = User.objects.create_user(
            username='testuser',
            password='testpass123',
            role='user',
            college=cls.college,
        )

    def setUp(self):
        self.client = APIClient()

    # ---------- JWT 登录 ----------

    def test_login_success(self):
        """正常登录 — 返回 access + refresh"""
        r = self.client.post('/api/auth/login/',
                             {'username': 'testuser', 'password': 'testpass123'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn('access', r.data)
        self.assertIn('refresh', r.data)

    def test_login_wrong_password(self):
        """密码错误 — 返回 401"""
        r = self.client.post('/api/auth/login/',
                             {'username': 'testuser', 'password': 'wrong'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_nonexistent_user(self):
        """用户不存在 — 返回 401"""
        r = self.client.post('/api/auth/login/',
                             {'username': 'nobody', 'password': 'x'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_missing_fields(self):
        """缺少必填字段 — 返回 400"""
        r = self.client.post('/api/auth/login/', {}, format='json')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    # ---------- 刷新 Token ----------

    def test_refresh_success(self):
        """正常刷新 — 返回新 access"""
        login_r = self.client.post('/api/auth/login/',
                                   {'username': 'testuser', 'password': 'testpass123'}, format='json')
        refresh_token = login_r.data['refresh']

        r = self.client.post('/api/auth/refresh/', {'refresh': refresh_token}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn('access', r.data)

    def test_refresh_with_access_token(self):
        """用 access token 刷新 — 返回 401"""
        login_r = self.client.post('/api/auth/login/',
                                   {'username': 'testuser', 'password': 'testpass123'}, format='json')
        access_token = login_r.data['access']

        r = self.client.post('/api/auth/refresh/', {'refresh': access_token}, format='json')
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_refresh_invalid_token(self):
        """无效 token — 返回 401"""
        r = self.client.post('/api/auth/refresh/',
                             {'refresh': 'not_a_real_token'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)

    # ---------- 个人信息 ----------

    def _login(self):
        r = self.client.post('/api/auth/login/',
                             {'username': 'testuser', 'password': 'testpass123'}, format='json')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {r.data["access"]}')

    def test_me_get(self):
        """已登录 — GET 返回用户信息"""
        self._login()
        r = self.client.get('/api/auth/me/')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['username'], 'testuser')
        self.assertEqual(r.data['college_name'], '测试学院')

    def test_me_put(self):
        """已登录 — PUT 更新手机号"""
        self._login()
        r = self.client.put('/api/auth/me/', {'phone': '13800001111'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['phone'], '13800001111')

    def test_me_put_cannot_change_role(self):
        """PUT 不能修改 role 字段"""
        self._login()
        r = self.client.put('/api/auth/me/', {'role': 'super_admin'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        # role 不应被修改
        self.assertEqual(r.data['role'], 'user')

    def test_me_without_auth(self):
        """未登录 — 返回 401"""
        r = self.client.get('/api/auth/me/')
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)

    # ---------- 修改密码 ----------

    def test_change_password_success(self):
        """正确的旧密码 — 修改成功"""
        self._login()
        r = self.client.post('/api/auth/change-password/',
                             {'old_password': 'testpass123', 'new_password': 'newpass456'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK)

        # 用新密码重新登录
        r2 = self.client.post('/api/auth/login/',
                              {'username': 'testuser', 'password': 'newpass456'}, format='json')
        self.assertEqual(r2.status_code, status.HTTP_200_OK)

    def test_change_password_wrong_old(self):
        """旧密码错误 — 返回 400"""
        self._login()
        r = self.client.post('/api/auth/change-password/',
                             {'old_password': 'wrong_old', 'new_password': 'newpass456'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('old_password', r.data)

    def test_change_password_weak_new(self):
        """新密码太弱 — 返回 400"""
        self._login()
        r = self.client.post('/api/auth/change-password/',
                             {'old_password': 'testpass123', 'new_password': '123'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_change_password_without_auth(self):
        """未登录 — 返回 401"""
        r = self.client.post('/api/auth/change-password/',
                             {'old_password': 'x', 'new_password': 'y'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)


class EdgeCaseTests(TestCase):
    """边界场景"""

    @classmethod
    def setUpTestData(cls):
        cls.college = College.objects.create(name='测试学院', code='TEST')

    def setUp(self):
        self.client = APIClient()

    def test_disabled_user_cannot_login(self):
        """禁用用户不能登录"""
        user = User.objects.create_user(
            username='disabled_user', password='pass123', is_active=False,
        )
        r = self.client.post('/api/auth/login/',
                             {'username': 'disabled_user', 'password': 'pass123'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_sso_user_cannot_jwt_login(self):
        """SSO 用户无本地密码，不能 JWT 登录"""
        # 先通过 SSO 创建
        r = self.client.post('/api/auth/sso/callback/',
                             {'code': 'mock_student'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK)

        # 用 JWT 登录应失败（密码不可用）
        r2 = self.client.post('/api/auth/login/',
                              {'username': 'sso_student', 'password': 'anything'}, format='json')
        self.assertEqual(r2.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_sso_user_can_access_me(self):
        """SSO 用户的 token 可以正常使用"""
        r = self.client.post('/api/auth/sso/callback/',
                             {'code': 'mock_admin'}, format='json')
        access = r.data['access']

        client2 = APIClient()
        client2.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        r2 = client2.get('/api/auth/me/')
        self.assertEqual(r2.status_code, status.HTTP_200_OK)
        self.assertEqual(r2.data['username'], 'sso_admin')
