"""文档归属范围 ACL 回归测试（2026-11 部门间文件越权排查）。

覆盖：
- 未绑定学院/部门的管理员不得因 Q(college_id=None) 命中其他部门文档
- 未绑定部门的部门管理员同理
- 旧 scope=college/department 但归属字段未回填的文档不当作校级公开
- 浏览、删除、AI 检索三条链路使用同一判定
"""
from unittest.mock import patch

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


class DocVisibilityDownloadTests(TestCase):
    """公开/私密语义（2026-11 调整）。

    public：问答可检索 + 可下载；
    private：问答同样可检索，但仅上传者本人与超管可下载。
    """

    def setUp(self):
        self.client = APIClient()
        self.dept = Department.objects.create(name='教学管理部')
        self.uploader = User.objects.create_user(
            username='up', password='pass12345', role='dept_admin',
            department=self.dept,
        )
        self.colleague = User.objects.create_user(
            username='col', password='pass12345', role='dept_admin',
            department=self.dept,
        )
        self.super_admin = User.objects.create_user(
            username='root', password='pass12345', role='super_admin',
        )
        self.private_doc = self._make_doc('私密资料', 'private')
        self.public_doc = self._make_doc('公开资料', 'public')

    def _make_doc(self, title, visibility):
        return KnowledgeDoc.objects.create(
            title=title,
            source_type='upload',
            file_name=f'{title}.pdf',
            file_type='pdf',
            oss_key=f'docs/{title}.pdf',
            uploader=self.uploader,
            department=self.dept,
            visibility=visibility,
            review_status='approved',
            status='indexed',
        )

    def _download_status(self, user, doc):
        self.client.force_authenticate(user)
        with patch('apps.knowledge.views.oss_client') as oss:
            oss.get_download_url.return_value = 'https://example.com/download'
            resp = self.client.get(f'/api/knowledge/docs/{doc.id}/download/')
        return resp.status_code

    def test_public_doc_downloadable_by_colleague(self):
        self.assertEqual(
            self._download_status(self.colleague, self.public_doc), 200,
        )

    def test_private_doc_not_downloadable_by_colleague(self):
        self.assertEqual(
            self._download_status(self.colleague, self.private_doc), 404,
        )

    def test_private_doc_downloadable_by_uploader(self):
        self.assertEqual(
            self._download_status(self.uploader, self.private_doc), 200,
        )

    def test_private_doc_downloadable_by_super_admin(self):
        self.assertEqual(
            self._download_status(self.super_admin, self.private_doc), 200,
        )

    def test_private_doc_in_scope_still_retrievable(self):
        ctx = {
            'role': 'dept_admin',
            'user_id': self.colleague.id,
            'college_id': None,
            'department_id': self.dept.id,
        }
        ids = set(authorized_knowledge_docs(ctx).values_list('id', flat=True))
        self.assertIn(self.private_doc.id, ids)

    def test_private_doc_reference_has_no_download_url(self):
        from utils.sse import build_references_events

        events = build_references_events(
            [{'doc_id': self.private_doc.id, 'score': 0.9, 'content': '片段'}],
            'dept_admin', self.colleague.id,
            user_context={
                'role': 'dept_admin', 'user_id': self.colleague.id,
                'department_id': self.dept.id,
            },
        )
        self.assertEqual(events['references_detail'][0]['download_url'], '')

    def _detail_download_url(self, user, doc):
        self.client.force_authenticate(user)
        with patch('apps.knowledge.serializers.oss_client') as oss:
            oss.get_download_url.return_value = 'https://example.com/download'
            oss.get_preview_url.return_value = 'https://example.com/preview'
            resp = self.client.get(f'/api/knowledge/docs/{doc.id}/')
        self.assertEqual(resp.status_code, 200, resp.data)
        return resp.data['download_url']

    def test_private_doc_detail_hides_download_url(self):
        self.assertEqual(
            self._detail_download_url(self.colleague, self.private_doc), '',
        )

    def test_public_doc_detail_exposes_download_url(self):
        self.assertEqual(
            self._detail_download_url(self.colleague, self.public_doc),
            'https://example.com/download',
        )
