"""文档审核链路回归测试。

覆盖：
- 上传初始审核状态（超管免审 / 其余管理员 pending）
- 先审后索引（pending 不派任务，通过后才派）
- 检索可见性（pending / rejected 不进 RAG 授权集合）
- 审核权限与范围（跨部门 403、超管 403、学生 403）
- 驳回理由必填、驳回后文件保留
- 重复审核 409、驳回后重新提交
- 无归属管理员禁止上传、审核员无上传权限
"""
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import College, User
from apps.classify.models import Department
from utils.rag import authorized_knowledge_docs

from ..models import KnowledgeDoc, KnowledgeDocReview

TEXT_UPLOAD_URL = '/api/knowledge/upload/text/'


def _oss_stub():
    """OSS 打桩：上传成功、签名 URL 固定，测试不触网。"""
    client = MagicMock()
    client.bucket = MagicMock()
    client.bucket_name = 'test-bucket'
    client.endpoint = 'oss-cn-test.aliyuncs.com'
    client.upload_text.return_value = True
    client.get_preview_url.return_value = 'https://example.com/preview'
    client.get_download_url.return_value = 'https://example.com/download'
    return client


class ReviewTestBase(TestCase):
    """公共夹具：两个学院、两个部门、各角色账号。"""

    def setUp(self):
        self.client = APIClient()
        self.college_a = College.objects.create(name='计算机与软件学院', code='CS')
        self.college_b = College.objects.create(name='外国语学院', code='FL')
        self.dept_a = Department.objects.create(name='教学管理部')
        self.dept_b = Department.objects.create(name='学生工作部')

        self.super_admin = User.objects.create_user(
            username='root', password='pass12345', role='super_admin',
        )
        self.college_admin = User.objects.create_user(
            username='ca', password='pass12345', role='college_admin',
            college=self.college_a,
        )
        self.dept_admin = User.objects.create_user(
            username='da', password='pass12345', role='dept_admin',
            department=self.dept_a,
        )
        # 审核员：部门级 / 学院级 各两个
        self.reviewer_dept_a = User.objects.create_user(
            username='rv_dept_a', password='pass12345', role='reviewer',
            department=self.dept_a,
        )
        self.reviewer_dept_b = User.objects.create_user(
            username='rv_dept_b', password='pass12345', role='reviewer',
            department=self.dept_b,
        )
        self.reviewer_college_a = User.objects.create_user(
            username='rv_college_a', password='pass12345', role='reviewer',
            college=self.college_a,
        )
        self.reviewer_college_b = User.objects.create_user(
            username='rv_college_b', password='pass12345', role='reviewer',
            college=self.college_b,
        )
        self.student = User.objects.create_user(
            username='stu', password='pass12345', role='user',
            college=self.college_a,
        )

    def _make_doc(self, uploader, **overrides):
        """造一条文档，默认按上传者归属 + pending。"""
        fields = {
            'title': '测试文档',
            'source_type': 'upload',
            'file_name': 'a.pdf',
            'file_type': 'pdf',
            'uploader': uploader,
            'status': 'uploaded',
            'visibility': 'public',
            'college_id': uploader.college_id,
            'department_id': uploader.department_id,
            'review_status': 'pending',
        }
        fields.update(overrides)
        return KnowledgeDoc.objects.create(**fields)

    def _auth(self, user):
        self.client.force_authenticate(user)

    def _text_upload(self, user, **payload):
        self._auth(user)
        body = {
            'content': '这是一段测试正文内容，用于触发上传链路。',
            'title': '测试标题',
            'description': '测试描述',
            'keywords': ['测试'],
            'visibility': 'public',
        }
        body.update(payload)
        return self.client.post(TEXT_UPLOAD_URL, body, format='json')


class UploadReviewStateTests(ReviewTestBase):
    """上传时的初始审核状态与索引派发时机。"""

    def test_super_admin_upload_is_approved_and_dispatches_tasks(self):
        with patch('apps.knowledge.views.oss_client', _oss_stub()), \
                patch('utils.celery_dispatcher.safe_delay') as mock_delay:
            resp = self._text_upload(self.super_admin)

        self.assertEqual(resp.status_code, 200, resp.data)
        doc = KnowledgeDoc.objects.get(id=resp.data['id'])
        self.assertEqual(doc.review_status, 'approved')
        self.assertIsNone(doc.college_id)
        self.assertIsNone(doc.department_id)
        self.assertEqual(mock_delay.call_count, 3, '超管上传应派发 3 个异步任务')

    def test_dept_admin_upload_is_pending_and_does_not_dispatch(self):
        with patch('apps.knowledge.views.oss_client', _oss_stub()), \
                patch('utils.celery_dispatcher.safe_delay') as mock_delay:
            resp = self._text_upload(self.dept_admin)

        self.assertEqual(resp.status_code, 200, resp.data)
        doc = KnowledgeDoc.objects.get(id=resp.data['id'])
        self.assertEqual(doc.review_status, 'pending')
        self.assertEqual(doc.department_id, self.dept_a.id)
        mock_delay.assert_not_called()

    def test_college_admin_upload_is_college_level_pending(self):
        with patch('apps.knowledge.views.oss_client', _oss_stub()), \
                patch('utils.celery_dispatcher.safe_delay') as mock_delay:
            resp = self._text_upload(self.college_admin)

        self.assertEqual(resp.status_code, 200, resp.data)
        doc = KnowledgeDoc.objects.get(id=resp.data['id'])
        self.assertEqual(doc.review_status, 'pending')
        self.assertEqual(doc.college_id, self.college_a.id)
        self.assertIsNone(doc.department_id, '学院管理员上传应为学院级（无部门）')
        mock_delay.assert_not_called()

    def test_admin_without_college_and_department_cannot_upload(self):
        orphan_admin = User.objects.create_user(
            username='orphan_admin', password='pass12345', role='admin',
        )
        with patch('apps.knowledge.views.oss_client', _oss_stub()):
            resp = self._text_upload(orphan_admin)

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(KnowledgeDoc.objects.count(), 0)

    def test_reviewer_cannot_upload(self):
        with patch('apps.knowledge.views.oss_client', _oss_stub()):
            resp = self._text_upload(self.reviewer_dept_a)

        self.assertEqual(resp.status_code, 403, resp.data)
        self.assertEqual(KnowledgeDoc.objects.count(), 0)


class ReviewActionTests(ReviewTestBase):
    """审核动作：通过 / 驳回 / 重新提交 / 权限范围。"""

    def test_dept_reviewer_approve_dispatches_tasks(self):
        doc = self._make_doc(self.dept_admin)
        self._auth(self.reviewer_dept_a)

        with patch('utils.celery_dispatcher.safe_delay') as mock_delay:
            resp = self.client.post(f'/api/knowledge/docs/{doc.id}/review/approve/')

        self.assertEqual(resp.status_code, 200, resp.data)
        doc.refresh_from_db()
        self.assertEqual(doc.review_status, 'approved')
        self.assertEqual(doc.reviewer_id, self.reviewer_dept_a.id)
        self.assertIsNotNone(doc.reviewed_at)
        self.assertEqual(mock_delay.call_count, 3)
        self.assertEqual(
            KnowledgeDocReview.objects.filter(doc=doc, action='approve').count(), 1,
        )

    def test_cross_department_reviewer_forbidden(self):
        doc = self._make_doc(self.dept_admin)
        self._auth(self.reviewer_dept_b)

        resp = self.client.post(f'/api/knowledge/docs/{doc.id}/review/approve/')

        self.assertEqual(resp.status_code, 403, resp.data)
        doc.refresh_from_db()
        self.assertEqual(doc.review_status, 'pending')

    def test_college_reviewer_can_review_college_level_doc(self):
        doc = self._make_doc(self.college_admin)  # 学院级，部门为空
        self._auth(self.reviewer_college_a)

        with patch('utils.celery_dispatcher.safe_delay'):
            resp = self.client.post(f'/api/knowledge/docs/{doc.id}/review/approve/')

        self.assertEqual(resp.status_code, 200, resp.data)
        doc.refresh_from_db()
        self.assertEqual(doc.review_status, 'approved')

    def test_college_reviewer_cannot_review_other_college(self):
        doc = self._make_doc(self.college_admin)
        self._auth(self.reviewer_college_b)

        resp = self.client.post(f'/api/knowledge/docs/{doc.id}/review/approve/')

        self.assertEqual(resp.status_code, 403, resp.data)

    def test_college_reviewer_cannot_review_department_level_doc(self):
        """学院审核员只审学院级文档，不抢部门审核员的活。"""
        doc = self._make_doc(self.dept_admin)  # 部门级
        self._auth(self.reviewer_college_a)

        resp = self.client.post(f'/api/knowledge/docs/{doc.id}/review/approve/')

        self.assertEqual(resp.status_code, 403, resp.data)

    def test_super_admin_has_no_review_entry(self):
        doc = self._make_doc(self.dept_admin)
        self._auth(self.super_admin)

        resp = self.client.post(f'/api/knowledge/docs/{doc.id}/review/approve/')

        self.assertEqual(resp.status_code, 403, resp.data)

    def test_student_cannot_review(self):
        doc = self._make_doc(self.dept_admin)
        self._auth(self.student)

        resp = self.client.post(f'/api/knowledge/docs/{doc.id}/review/approve/')

        self.assertEqual(resp.status_code, 403, resp.data)

    def test_reject_requires_comment(self):
        doc = self._make_doc(self.dept_admin)
        self._auth(self.reviewer_dept_a)

        resp = self.client.post(
            f'/api/knowledge/docs/{doc.id}/review/reject/', {}, format='json',
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        doc.refresh_from_db()
        self.assertEqual(doc.review_status, 'pending')

    def test_reject_blank_comment_returns_400(self):
        doc = self._make_doc(self.dept_admin)
        self._auth(self.reviewer_dept_a)

        resp = self.client.post(
            f'/api/knowledge/docs/{doc.id}/review/reject/',
            {'comment': '   '}, format='json',
        )

        self.assertEqual(resp.status_code, 400, resp.data)

    def test_reject_keeps_file_and_writes_reason(self):
        doc = self._make_doc(self.dept_admin)
        self._auth(self.reviewer_dept_a)

        resp = self.client.post(
            f'/api/knowledge/docs/{doc.id}/review/reject/',
            {'comment': '内容与部门职责不符'}, format='json',
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        doc.refresh_from_db()
        self.assertEqual(doc.review_status, 'rejected')
        self.assertEqual(doc.review_comment, '内容与部门职责不符')
        self.assertTrue(KnowledgeDoc.objects.filter(id=doc.id).exists(), '驳回后文件保留')
        record = KnowledgeDocReview.objects.get(doc=doc, action='reject')
        self.assertEqual(record.comment, '内容与部门职责不符')

    def test_double_review_returns_409(self):
        doc = self._make_doc(self.dept_admin)
        self._auth(self.reviewer_dept_a)

        with patch('utils.celery_dispatcher.safe_delay'):
            first = self.client.post(f'/api/knowledge/docs/{doc.id}/review/approve/')
            second = self.client.post(f'/api/knowledge/docs/{doc.id}/review/approve/')

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409, second.data)

    def test_resubmit_moves_rejected_back_to_pending(self):
        doc = self._make_doc(
            self.dept_admin, review_status='rejected', review_comment='请补充附件',
            reviewer=self.reviewer_dept_a,
        )
        self._auth(self.dept_admin)

        resp = self.client.post(
            f'/api/knowledge/docs/{doc.id}/review/resubmit/',
            {'comment': '已补充附件'}, format='json',
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        doc.refresh_from_db()
        self.assertEqual(doc.review_status, 'pending')
        self.assertEqual(doc.review_comment, '')
        self.assertIsNone(doc.reviewer_id)
        self.assertTrue(
            KnowledgeDocReview.objects.filter(doc=doc, action='resubmit').exists(),
        )

    def test_resubmit_only_by_uploader(self):
        doc = self._make_doc(self.dept_admin, review_status='rejected')
        self._auth(self.student)

        resp = self.client.post(f'/api/knowledge/docs/{doc.id}/review/resubmit/')

        self.assertEqual(resp.status_code, 403, resp.data)

    def test_resubmit_rejected_only_when_rejected(self):
        doc = self._make_doc(self.dept_admin, review_status='pending')
        self._auth(self.dept_admin)

        resp = self.client.post(f'/api/knowledge/docs/{doc.id}/review/resubmit/')

        self.assertEqual(resp.status_code, 409, resp.data)


class PendingListViewTests(ReviewTestBase):
    """待审列表按审核员归属过滤。"""

    def test_dept_reviewer_sees_only_own_department(self):
        mine = self._make_doc(self.dept_admin)
        self._make_doc(self.dept_admin, department_id=self.dept_b.id)
        self._make_doc(self.college_admin)
        self._make_doc(self.dept_admin, review_status='approved')

        self._auth(self.reviewer_dept_a)
        resp = self.client.get('/api/knowledge/reviews/pending/')

        self.assertEqual(resp.status_code, 200, resp.data)
        ids = [item['id'] for item in resp.data['results']]
        self.assertEqual(ids, [mine.id])

    def test_college_reviewer_sees_only_college_level(self):
        college_level = self._make_doc(self.college_admin)
        self._make_doc(self.dept_admin)  # 部门级，不归学院审核员
        self._make_doc(
            self.college_admin, college_id=self.college_b.id,  # 他院
        )

        self._auth(self.reviewer_college_a)
        resp = self.client.get('/api/knowledge/reviews/pending/')

        self.assertEqual(resp.status_code, 200, resp.data)
        ids = [item['id'] for item in resp.data['results']]
        self.assertEqual(ids, [college_level.id])

    def test_super_admin_cannot_list_pending(self):
        self._auth(self.super_admin)
        resp = self.client.get('/api/knowledge/reviews/pending/')
        self.assertEqual(resp.status_code, 403)


class StsCallbackReviewTests(ReviewTestBase):
    """STS 直传回调入口（前端直传 OSS 后回调建记录）同样受审核约束。"""

    CALLBACK_URL = '/api/knowledge/upload/callback/'

    def _callback(self, user):
        self._auth(user)
        return self.client.post(self.CALLBACK_URL, {
            'oss_key': 'knowledge/college/1/2026-10/abcdef123456.pdf',
            'title': '直传文档',
            'file_name': '直传.pdf',
            'file_size': 1024,
            'file_type': 'pdf',
            'md5': 'd41d8cd98f00b204e9800998ecf8427e',
            'visibility': 'public',
        }, format='json')

    def test_dept_admin_callback_is_pending_and_does_not_dispatch(self):
        with patch('apps.knowledge.views.oss_client') as mock_oss, \
                patch('utils.celery_dispatcher.safe_delay') as mock_delay:
            mock_oss.check_duplicate.return_value = None
            resp = self._callback(self.dept_admin)

        self.assertEqual(resp.status_code, 200, resp.data)
        doc = KnowledgeDoc.objects.get(id=resp.data['id'])
        self.assertEqual(doc.review_status, 'pending')
        mock_delay.assert_not_called()

    def test_super_admin_callback_is_approved_and_dispatches(self):
        with patch('apps.knowledge.views.oss_client') as mock_oss, \
                patch('utils.celery_dispatcher.safe_delay') as mock_delay:
            mock_oss.check_duplicate.return_value = None
            resp = self._callback(self.super_admin)

        self.assertEqual(resp.status_code, 200, resp.data)
        doc = KnowledgeDoc.objects.get(id=resp.data['id'])
        self.assertEqual(doc.review_status, 'approved')
        self.assertEqual(mock_delay.call_count, 3)


class RetrievalVisibilityTests(ReviewTestBase):
    """审核状态对 RAG 检索授权集合的影响。"""

    def _visible_ids(self, user):
        context = {
            'role': user.role,
            'user_id': user.id,
            'college_id': user.college_id,
            'department_id': user.department_id,
        }
        return set(
            authorized_knowledge_docs(context).values_list('id', flat=True),
        )

    def test_pending_and_rejected_are_not_retrievable(self):
        # 用学院级文档：本学院学生可见性由 college_id 命中，便于验证审核状态过滤
        pending = self._make_doc(self.college_admin, review_status='pending')
        rejected = self._make_doc(self.college_admin, review_status='rejected')
        approved = self._make_doc(self.college_admin, review_status='approved')

        visible = self._visible_ids(self.student)

        self.assertIn(approved.id, visible)
        self.assertNotIn(pending.id, visible)
        self.assertNotIn(rejected.id, visible)

    def test_super_admin_cannot_retrieve_pending_either(self):
        pending = self._make_doc(self.college_admin, review_status='pending')
        visible = self._visible_ids(self.super_admin)
        self.assertNotIn(pending.id, visible)

    def test_reviewer_query_scope_equals_normal_user(self):
        """审核员的问答范围与普通用户一致：本部门文档 + 校级文档。"""
        own_dept = self._make_doc(
            self.dept_admin, review_status='approved', college_id=None,
        )
        other_dept = self._make_doc(
            self.dept_admin, review_status='approved',
            department_id=self.dept_b.id, college_id=None,
        )

        visible = self._visible_ids(self.reviewer_dept_a)

        self.assertIn(own_dept.id, visible)
        self.assertNotIn(other_dept.id, visible)
