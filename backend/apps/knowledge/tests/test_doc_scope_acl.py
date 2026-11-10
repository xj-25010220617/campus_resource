"""文档归属范围 ACL 回归测试（2026-11 部门间文件越权排查）。

覆盖：
- 未绑定学院/部门的管理员不得因 Q(college_id=None) 命中其他部门文档
- 未绑定部门的部门管理员同理
- 旧 scope=college/department 但归属字段未回填的文档不当作校级公开
- 浏览、删除、AI 检索三条链路使用同一判定
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import College, User
from apps.classify.models import Department
from apps.knowledge.models import KnowledgeDoc
from utils.rag import authorized_knowledge_docs

DOC_LIST_URL = '/api/knowledge/docs/'


class DocScopeAclTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.college_a = College.objects.create(name='计算机与软件学院', code='CS')
        self.dept_a1 = Department.objects.create(name='教学管理部')
        self.dept_a2 = Department.objects.create(name='学生工作部')

        self.super_admin = User.objects.create_user(
            username='root', password='pass12345', role='super_admin',
        )
        self.dept_admin_a1 = User.objects.create_user(
            username='da1', password='pass12345', role='dept_admin',
            department=self.dept_a1,
        )
        self.dept_admin_a2 = User.objects.create_user(
            username='da2', password='pass12345', role='dept_admin',
            department=self.dept_a2,
        )
        self.college_admin_a = User.objects.create_user(
            username='ca', password='pass12345', role='college_admin',
            college=self.college_a,
        )
        # 兼容旧角色：未绑定学院/部门的 admin（mock_admin 即此类账号）
        self.orphan_admin = User.objects.create_user(
            username='orphan', password='pass12345', role='admin',
        )
        self.orphan_dept_admin = User.objects.create_user(
            username='orphan_dept', password='pass12345', role='dept_admin',
        )

        self.school_doc = self._make_doc(
            self.super_admin, '校级文档', scope='',
        )
        self.school_doc_null_scope = self._make_doc(
            self.super_admin, '校级文档（旧数据为 NULL）', scope=None,
        )
        self.legacy_school_doc = self._make_doc(
            self.super_admin, '旧 scope=school 文档', scope='school',
        )
        self.dept_a1_doc = self._make_doc(
            self.dept_admin_a1, 'A1 部门文档', department=self.dept_a1,
        )
        self.dept_a2_doc = self._make_doc(
            self.dept_admin_a2, 'A2 部门文档', department=self.dept_a2,
        )
        self.college_a_doc = self._make_doc(
            self.college_admin_a, '学院级文档', college=self.college_a,
        )
        # 历史数据：旧 scope 标记为部门，但 department_id 未回填
        self.legacy_dept_doc = self._make_doc(
            self.dept_admin_a2, '旧 scope=department 但无部门', scope='department',
        )

    def _make_doc(self, uploader, title, college=None, department=None, scope=''):
        return KnowledgeDoc.objects.create(
            title=title,
            source_type='upload',
            file_name='a.pdf',
            file_type='pdf',
            uploader=uploader,
            college=college,
            department=department,
            scope=scope,
            visibility='public',
            review_status='approved',
        )

    def _list_ids(self, user):
        self.client.force_authenticate(user)
        resp = self.client.get(DOC_LIST_URL, {'page_size': 50})
        self.assertEqual(resp.status_code, 200, resp.data)
        return {row['id'] for row in resp.data['results']}

    def test_dept_admin_sees_own_department_and_school_only(self):
        ids = self._list_ids(self.dept_admin_a1)
        self.assertEqual(
            ids,
            {self.school_doc.id, self.school_doc_null_scope.id,
             self.legacy_school_doc.id, self.dept_a1_doc.id},
        )

    def test_orphan_admin_does_not_see_other_departments(self):
        """未绑定学院的 admin 不能把 Q(college_id=None) 变成全量可见。"""
        ids = self._list_ids(self.orphan_admin)
        self.assertEqual(
            ids,
            {self.school_doc.id, self.school_doc_null_scope.id,
             self.legacy_school_doc.id},
        )
        self.assertNotIn(self.dept_a1_doc.id, ids)
        self.assertNotIn(self.dept_a2_doc.id, ids)
        self.assertNotIn(self.legacy_dept_doc.id, ids)

    def test_orphan_dept_admin_does_not_see_unscoped_docs(self):
        ids = self._list_ids(self.orphan_dept_admin)
        self.assertEqual(
            ids,
            {self.school_doc.id, self.school_doc_null_scope.id,
             self.legacy_school_doc.id},
        )
        self.assertNotIn(self.legacy_dept_doc.id, ids)
        self.assertNotIn(self.college_a_doc.id, ids)

    def test_college_admin_sees_own_college_and_school_only(self):
        ids = self._list_ids(self.college_admin_a)
        self.assertEqual(
            ids,
            {self.school_doc.id, self.school_doc_null_scope.id,
             self.legacy_school_doc.id, self.college_a_doc.id},
        )

    def test_legacy_department_scope_is_fail_closed(self):
        """旧 scope=department 但无 department_id 的文档仅上传者可见。"""
        ids = self._list_ids(self.dept_admin_a1)
        self.assertNotIn(self.legacy_dept_doc.id, ids)
        own_ids = self._list_ids(self.dept_admin_a2)
        self.assertIn(self.legacy_dept_doc.id, own_ids)

    def test_orphan_admin_cannot_delete_other_department_doc(self):
        self.client.force_authenticate(self.orphan_admin)
        resp = self.client.delete(
            f'/api/knowledge/docs/{self.dept_a1_doc.id}/delete/',
        )
        self.assertEqual(resp.status_code, 404, resp.data)
        self.assertTrue(KnowledgeDoc.objects.filter(id=self.dept_a1_doc.id).exists())

    def test_rag_scope_matches_browse_scope(self):
        ctx = {
            'role': 'admin',
            'user_id': self.orphan_admin.id,
            'college_id': None,
            'department_id': None,
        }
        ids = set(authorized_knowledge_docs(ctx).values_list('id', flat=True))
        self.assertEqual(
            ids,
            {self.school_doc.id, self.school_doc_null_scope.id,
             self.legacy_school_doc.id},
        )
