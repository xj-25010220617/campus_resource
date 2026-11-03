"""部门资料目录范围与权限测试。"""
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import College, User
from apps.classify.models import Department
from apps.knowledge.folders import resolve_folder_scope
from apps.knowledge.models import KnowledgeDoc, KnowledgeFolder


class FolderScopeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.college = College.objects.create(name='计算机与软件学院', code='CS')
        cls.other_college = College.objects.create(name='外国语学院', code='FL')
        cls.department = Department.objects.create(name='教学管理')
        cls.other_department = Department.objects.create(name='学生工作')

        cls.super_admin = User.objects.create_user(
            username='super', password='pass-1234', role='super_admin',
        )
        cls.dept_admin = User.objects.create_user(
            username='dept', password='pass-1234', role='dept_admin',
            college=cls.college, department=cls.department,
        )
        cls.other_dept_admin = User.objects.create_user(
            username='dept-other', password='pass-1234', role='dept_admin',
            college=cls.college, department=cls.other_department,
        )
        cls.reviewer = User.objects.create_user(
            username='reviewer', password='pass-1234', role='reviewer',
            college=cls.college, department=cls.department,
        )

        cls.own_folder = KnowledgeFolder.objects.create(
            name='教学资料', department=cls.department, created_by=cls.dept_admin,
        )
        cls.other_folder = KnowledgeFolder.objects.create(
            name='学生材料', department=cls.other_department,
        )

    def setUp(self):
        self.client = APIClient()

    def _login(self, user):
        self.client.force_authenticate(user=user)

    # ── 范围解析 ──

    def test_super_admin_defaults_to_all_departments(self):
        scope = resolve_folder_scope(self.super_admin)
        self.assertTrue(scope.all_departments)
        self.assertTrue(scope.can_manage)
        self.assertEqual(scope.label, 'school')

    def test_super_admin_can_narrow_to_department(self):
        scope = resolve_folder_scope(self.super_admin, self.department.id)
        self.assertFalse(scope.all_departments)
        self.assertEqual(scope.department_id, self.department.id)
        self.assertEqual(scope.label, 'department')

    def test_non_super_admin_ignores_department_param(self):
        scope = resolve_folder_scope(self.dept_admin, self.other_department.id)
        self.assertEqual(scope.department_id, self.department.id)

    def test_super_admin_can_narrow_to_college(self):
        scope = resolve_folder_scope(self.super_admin, None, self.college.id)
        self.assertFalse(scope.all_departments)
        self.assertEqual(scope.college_id, self.college.id)
        self.assertIsNone(scope.department_id)
        self.assertEqual(scope.label, 'college')

    def test_non_super_admin_ignores_college_param(self):
        scope = resolve_folder_scope(self.dept_admin, None, self.other_college.id)
        self.assertEqual(scope.college_id, self.college.id)
        self.assertEqual(scope.department_id, self.department.id)

    def test_invalid_college_id_raises(self):
        with self.assertRaises(ValueError):
            resolve_folder_scope(self.super_admin, None, 'abc')

    # ── 目录列表 ──

    def test_super_admin_sees_all_departments_by_default(self):
        self._login(self.super_admin)
        response = self.client.get('/api/knowledge/folders/')
        self.assertEqual(response.status_code, 200)
        names = {item['name'] for item in response.data['folders']}
        self.assertEqual(names, {'教学资料', '学生材料'})
        self.assertTrue(response.data['scope']['all_departments'])

    def test_super_admin_department_scope_filters_folders(self):
        self._login(self.super_admin)
        response = self.client.get(
            '/api/knowledge/folders/', {'department_id': self.department.id},
        )
        self.assertEqual(response.status_code, 200)
        names = {item['name'] for item in response.data['folders']}
        self.assertEqual(names, {'教学资料'})
        self.assertFalse(response.data['scope']['all_departments'])

    def test_super_admin_college_scope_filters_folders(self):
        KnowledgeFolder.objects.create(name='学院资料', college=self.college)
        KnowledgeFolder.objects.create(name='其他学院资料', college=self.other_college)
        self._login(self.super_admin)
        response = self.client.get(
            '/api/knowledge/folders/', {'college_id': self.college.id},
        )
        self.assertEqual(response.status_code, 200)
        names = {item['name'] for item in response.data['folders']}
        self.assertEqual(names, {'学院资料'})
        self.assertEqual(response.data['scope']['kind'], 'college')
        self.assertEqual(response.data['scope']['college_id'], self.college.id)

    def test_dept_admin_only_sees_own_department_folders(self):
        self._login(self.dept_admin)
        response = self.client.get('/api/knowledge/folders/')
        self.assertEqual(response.status_code, 200)
        names = {item['name'] for item in response.data['folders']}
        self.assertEqual(names, {'教学资料'})
        self.assertTrue(response.data['scope']['can_manage'])

    def test_reviewer_is_read_only(self):
        self._login(self.reviewer)
        response = self.client.get('/api/knowledge/folders/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['scope']['can_manage'])

        create = self.client.post(
            '/api/knowledge/folders/', {'name': '审核员目录'}, format='json',
        )
        self.assertEqual(create.status_code, 403)

        rename = self.client.patch(
            f'/api/knowledge/folders/{self.own_folder.id}/',
            {'name': '改名'}, format='json',
        )
        self.assertEqual(rename.status_code, 403)

    # ── 新建目录 ──

    def test_super_admin_must_pick_department_before_creating_folder(self):
        self._login(self.super_admin)
        response = self.client.post(
            '/api/knowledge/folders/', {'name': '校级目录'}, format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('department_id', response.data)

    def test_super_admin_creates_folder_in_selected_department(self):
        self._login(self.super_admin)
        response = self.client.post(
            '/api/knowledge/folders/',
            {'name': '课程大纲', 'department_id': self.department.id},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        folder = KnowledgeFolder.objects.get(id=response.data['id'])
        self.assertEqual(folder.department_id, self.department.id)
        self.assertFalse(folder.is_system)

    def test_super_admin_creates_folder_in_selected_college(self):
        self._login(self.super_admin)
        response = self.client.post(
            '/api/knowledge/folders/',
            {'name': '学院课程大纲', 'college_id': self.college.id},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        folder = KnowledgeFolder.objects.get(id=response.data['id'])
        self.assertEqual(folder.college_id, self.college.id)
        self.assertIsNone(folder.department_id)

    def test_super_admin_cannot_create_folder_in_missing_college(self):
        self._login(self.super_admin)
        response = self.client.post(
            '/api/knowledge/folders/',
            {'name': '不存在的学院目录', 'college_id': 999999},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('college_id', response.data)

    def test_dept_admin_cannot_create_folder_in_other_department(self):
        self._login(self.dept_admin)
        response = self.client.post(
            '/api/knowledge/folders/',
            {'name': '跨部门目录', 'department_id': self.other_department.id},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        folder = KnowledgeFolder.objects.get(id=response.data['id'])
        self.assertEqual(folder.department_id, self.department.id)

    # ── 文档计数 ──

    def test_department_scope_counts_only_that_departments_docs(self):
        KnowledgeDoc.objects.create(
            title='本部门文档', source_type='upload',
            department=self.department, folder=self.own_folder,
            uploader=self.dept_admin,
        )
        KnowledgeDoc.objects.create(
            title='其他部门文档', source_type='upload',
            department=self.other_department, folder=self.other_folder,
        )
        self._login(self.super_admin)
        response = self.client.get(
            '/api/knowledge/folders/', {'department_id': self.department.id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['folders'][0]['doc_count'], 1)
        self.assertEqual(response.data['unfiled_count'], 0)

    def test_system_folders_are_hidden_and_their_docs_count_as_unfiled(self):
        system_folder = KnowledgeFolder.objects.create(
            name='AI 自动分类', department=self.department,
            is_system=True, system_key='ai-default',
        )
        KnowledgeDoc.objects.create(
            title='历史文档', source_type='upload',
            department=self.department, folder=system_folder,
            uploader=self.dept_admin,
        )
        self._login(self.dept_admin)
        response = self.client.get('/api/knowledge/folders/')
        self.assertEqual(response.status_code, 200)
        names = {item['name'] for item in response.data['folders']}
        self.assertNotIn('AI 自动分类', names)
        self.assertEqual(response.data['unfiled_count'], 1)

    def test_college_scope_counts_only_that_college_docs(self):
        college_folder = KnowledgeFolder.objects.create(
            name='学院资料', college=self.college,
        )
        KnowledgeDoc.objects.create(
            title='学院归档文档', source_type='upload',
            college=self.college, folder=college_folder,
            uploader=self.super_admin,
        )
        KnowledgeDoc.objects.create(
            title='学院待分类文档', source_type='upload',
            college=self.college, uploader=self.super_admin,
        )
        KnowledgeDoc.objects.create(
            title='其他学院文档', source_type='upload',
            college=self.other_college, uploader=self.super_admin,
        )
        self._login(self.super_admin)
        response = self.client.get(
            '/api/knowledge/folders/', {'college_id': self.college.id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['folders'][0]['doc_count'], 1)
        self.assertEqual(response.data['unfiled_count'], 1)
        self.assertEqual(response.data['total_docs'], 2)


def _oss_stub():
    """OSS 打桩：上传成功，测试不触网。"""
    client = MagicMock()
    client.bucket_name = 'test-bucket'
    client.endpoint = 'oss-cn-test.aliyuncs.com'
    client.upload_text.return_value = True
    return client


class PersonalFolderTests(TestCase):
    """我的专属空间：目录仅属于超管本人，与其他超管、校级目录隔离。"""

    @classmethod
    def setUpTestData(cls):
        cls.college = College.objects.create(name='计算机与软件学院', code='CS')
        cls.department = Department.objects.create(name='教学管理')
        cls.super_admin = User.objects.create_user(
            username='super-a', password='pass-1234', role='super_admin',
        )
        cls.other_super = User.objects.create_user(
            username='super-b', password='pass-1234', role='super_admin',
        )
        cls.dept_admin = User.objects.create_user(
            username='dept', password='pass-1234', role='dept_admin',
            college=cls.college, department=cls.department,
        )
        cls.shared_folder = KnowledgeFolder.objects.create(name='校级共享目录')

    def setUp(self):
        self.client = APIClient()

    def _login(self, user):
        self.client.force_authenticate(user=user)

    # ── 范围解析 ──

    def test_resolve_personal_scope(self):
        scope = resolve_folder_scope(self.super_admin, personal=True)
        self.assertTrue(scope.personal)
        self.assertFalse(scope.all_departments)
        self.assertEqual(scope.owner_id, self.super_admin.id)
        self.assertEqual(scope.label, 'personal')
        self.assertTrue(scope.can_manage)

    def test_personal_takes_precedence_over_department(self):
        scope = resolve_folder_scope(
            self.super_admin, self.department.id, personal=True,
        )
        self.assertTrue(scope.personal)
        self.assertIsNone(scope.department_id)

    def test_non_super_admin_ignores_personal_flag(self):
        scope = resolve_folder_scope(self.dept_admin, personal=True)
        self.assertFalse(scope.personal)
        self.assertEqual(scope.department_id, self.department.id)

    # ── 新建 / 列表隔离 ──

    def test_super_admin_creates_personal_folder(self):
        self._login(self.super_admin)
        response = self.client.post(
            '/api/knowledge/folders/',
            {'name': '我的资料', 'personal': True},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        folder = KnowledgeFolder.objects.get(id=response.data['id'])
        self.assertEqual(folder.owner_id, self.super_admin.id)
        self.assertIsNone(folder.college_id)
        self.assertIsNone(folder.department_id)
        self.assertFalse(folder.is_system)

    def test_personal_folder_list_isolated_between_super_admins(self):
        mine = KnowledgeFolder.objects.create(
            name='A 的目录', owner=self.super_admin,
        )
        KnowledgeFolder.objects.create(name='B 的目录', owner=self.other_super)

        self._login(self.super_admin)
        response = self.client.get('/api/knowledge/folders/', {'personal': 1})
        self.assertEqual(response.status_code, 200)
        names = {item['name'] for item in response.data['folders']}
        self.assertEqual(names, {'A 的目录'})
        self.assertEqual(response.data['scope']['kind'], 'personal')
        self.assertTrue(response.data['scope']['personal'])
        self.assertEqual(response.data['folders'][0]['id'], mine.id)

    def test_personal_space_hides_shared_school_folders(self):
        self._login(self.super_admin)
        response = self.client.get('/api/knowledge/folders/', {'personal': 1})
        self.assertEqual(response.status_code, 200)
        names = {item['name'] for item in response.data['folders']}
        self.assertNotIn(self.shared_folder.name, names)

    def test_school_scope_excludes_personal_folders(self):
        KnowledgeFolder.objects.create(name='A 的目录', owner=self.super_admin)
        self._login(self.super_admin)
        response = self.client.get('/api/knowledge/folders/')
        self.assertEqual(response.status_code, 200)
        names = {item['name'] for item in response.data['folders']}
        self.assertIn(self.shared_folder.name, names)
        self.assertNotIn('A 的目录', names)

    # ── 重命名 / 删除 ──

    def test_rename_personal_folder(self):
        folder = KnowledgeFolder.objects.create(
            name='旧名称', owner=self.super_admin,
        )
        self._login(self.super_admin)
        response = self.client.patch(
            f'/api/knowledge/folders/{folder.id}/',
            {'name': '新名称'}, format='json', QUERY_STRING='personal=1',
        )
        self.assertEqual(response.status_code, 200, response.data)
        folder.refresh_from_db()
        self.assertEqual(folder.name, '新名称')

    def test_cannot_access_other_super_admin_personal_folder(self):
        folder = KnowledgeFolder.objects.create(
            name='B 的目录', owner=self.other_super,
        )
        self._login(self.super_admin)

        personalized = self.client.patch(
            f'/api/knowledge/folders/{folder.id}/',
            {'name': '越权改名'}, format='json',
        )
        self.assertEqual(personalized.status_code, 400, personalized.data)

        default_scope = self.client.patch(
            f'/api/knowledge/folders/{folder.id}/',
            {'name': '越权改名'}, format='json',
        )
        self.assertEqual(default_scope.status_code, 400, default_scope.data)
        folder.refresh_from_db()
        self.assertEqual(folder.name, 'B 的目录')

    def test_delete_personal_folder_keeps_docs(self):
        folder = KnowledgeFolder.objects.create(
            name='待删除', owner=self.super_admin,
        )
        doc = KnowledgeDoc.objects.create(
            title='私密文件', source_type='upload',
            uploader=self.super_admin, visibility='private', folder=folder,
        )
        self._login(self.super_admin)
        response = self.client.delete(
            f'/api/knowledge/folders/{folder.id}/', QUERY_STRING='personal=1',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(KnowledgeFolder.objects.filter(id=folder.id).exists())
        doc.refresh_from_db()
        self.assertIsNone(doc.folder_id)

    # ── 文档计数 / 归档 ──

    def test_personal_folder_counts_only_own_private_docs(self):
        folder = KnowledgeFolder.objects.create(
            name='私密目录', owner=self.super_admin,
        )
        KnowledgeDoc.objects.create(
            title='我的私密', source_type='upload', uploader=self.super_admin,
            visibility='private', folder=folder,
        )
        KnowledgeDoc.objects.create(
            title='我的公开', source_type='upload', uploader=self.super_admin,
            visibility='public', folder=folder,
        )
        KnowledgeDoc.objects.create(
            title='他人私密', source_type='upload', uploader=self.other_super,
            visibility='private',
        )
        KnowledgeDoc.objects.create(
            title='待归档私密', source_type='upload', uploader=self.super_admin,
            visibility='private',
        )
        self._login(self.super_admin)
        response = self.client.get('/api/knowledge/folders/', {'personal': 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['folders'][0]['doc_count'], 1)
        self.assertEqual(response.data['unfiled_count'], 1)
        self.assertEqual(response.data['total_docs'], 2)

    def test_move_private_doc_into_personal_folder(self):
        folder = KnowledgeFolder.objects.create(
            name='私密目录', owner=self.super_admin,
        )
        doc = KnowledgeDoc.objects.create(
            title='私密文件', source_type='upload',
            uploader=self.super_admin, visibility='private',
        )
        self._login(self.super_admin)
        response = self.client.post(
            f'/api/knowledge/docs/{doc.id}/move/',
            {'folder_id': folder.id, 'personal': True}, format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        doc.refresh_from_db()
        self.assertEqual(doc.folder_id, folder.id)

    def test_cannot_move_public_doc_into_personal_folder(self):
        folder = KnowledgeFolder.objects.create(
            name='私密目录', owner=self.super_admin,
        )
        doc = KnowledgeDoc.objects.create(
            title='公开文件', source_type='upload',
            uploader=self.super_admin, visibility='public',
        )
        self._login(self.super_admin)
        response = self.client.post(
            f'/api/knowledge/docs/{doc.id}/move/',
            {'folder_id': folder.id, 'personal': True}, format='json',
        )
        self.assertEqual(response.status_code, 404, response.data)
        doc.refresh_from_db()
        self.assertIsNone(doc.folder_id)

    # ── 上传到个人目录 ──

    def test_text_upload_into_personal_folder(self):
        folder = KnowledgeFolder.objects.create(
            name='私密目录', owner=self.super_admin,
        )
        self._login(self.super_admin)
        with patch('apps.knowledge.views.oss_client', _oss_stub()), \
                patch('utils.celery_dispatcher.safe_delay'):
            response = self.client.post(
                '/api/knowledge/upload/text/',
                {
                    'content': '专属空间的私密正文内容。',
                    'title': '私密文档',
                    'description': '私密描述',
                    'keywords': ['私密'],
                    'visibility': 'public',
                    'folder_id': folder.id,
                    'personal': True,
                },
                format='json',
            )
        self.assertEqual(response.status_code, 200, response.data)
        doc = KnowledgeDoc.objects.get(id=response.data['id'])
        self.assertEqual(doc.folder_id, folder.id)
        self.assertEqual(doc.visibility, 'private')
        self.assertIsNone(doc.college_id)
        self.assertIsNone(doc.department_id)
        self.assertEqual(doc.uploader_id, self.super_admin.id)

    def test_shared_folder_cannot_hold_personal_upload(self):
        """非 personal 请求不能把文件写进别人的专属目录。"""
        folder = KnowledgeFolder.objects.create(
            name='B 的目录', owner=self.other_super,
        )
        self._login(self.super_admin)
        with patch('apps.knowledge.views.oss_client', _oss_stub()):
            response = self.client.post(
                '/api/knowledge/upload/text/',
                {
                    'content': '越权归档正文。',
                    'folder_id': folder.id,
                },
                format='json',
            )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('folder_id', response.data)
