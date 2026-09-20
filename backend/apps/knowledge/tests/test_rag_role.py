from django.test import SimpleTestCase

from utils.rag import ADMIN_MIN_SCORE, ADMIN_ROLES, is_strict_role


class TestRoleStrict(SimpleTestCase):
    def test_admin_roles_are_strict(self):
        for r in ADMIN_ROLES:
            assert is_strict_role(r) is True, f'{r} 应为严格角色'

    def test_user_not_strict(self):
        assert is_strict_role('user') is False

    def test_unknown_role_not_strict(self):
        assert is_strict_role('') is False
        assert is_strict_role('guest') is False

    def test_admin_min_score_positive(self):
        assert ADMIN_MIN_SCORE > 0
