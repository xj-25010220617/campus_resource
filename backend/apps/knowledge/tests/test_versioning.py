"""新旧版本识别、确认与问答隔离测试。

覆盖：
- 文本归一化 / 版本词剥离 / Jaccard 相似度
- 同名高相似 → 直接生成待确认候选，旧版未被替代
- 异名中相似 → 必须 LLM 复核通过才生成候选；LLM 不可用则不生成
- 无关文档不生成候选；新版受众缩水不生成候选
- 上传者确认后旧版被替代（superseded_by/superseded_at + replaces 关联）
- 拒绝、重复确认、越权确认
- 确认后 RAG 授权集合与表格问答可见文档集合均排除旧版
- 删除新版/中间版后的版本链自动恢复
"""
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from apps.accounts.models import College, User
from apps.classify.models import Department
from utils.data_query import _visible_docs_qs
from utils.rag import authorized_knowledge_docs

from ..models import DocChunk, DocRelation, DocVersionCandidate, KnowledgeDoc
from ..versioning import (
    confirm_candidate,
    detect_versions,
    jaccard,
    reject_candidate,
    split_version_marker,
)

BASE_TEXT = (
    '第一条 为规范校园虚拟资源管理，保障教学与科研资料的准确性和时效性，特制定本办法。'
    '第二条 校内各单位上传的资料应当经过审核，审核通过后方可进入知识库检索范围。'
    '第三条 资料更新时，应当及时上传最新版本，避免旧版本内容干扰知识问答结果。'
    '第四条 知识库管理员应当定期检查资料的有效性，发现过期文件及时标注或替换。'
    '第五条 本办法自发布之日起施行，由信息中心负责解释。'
) * 8


def _make_chunks(doc, text=BASE_TEXT, count=5):
    """把正文切成分块落库，供版本识别采样。"""
    size = max(1, len(text) // count)
    for i in range(count):
        DocChunk.objects.create(
            doc=doc,
            chunk_index=i,
            content=text[i * size:(i + 1) * size],
            vector_id=f'{doc.id}-{i}',
        )


class VersionTextUtilityTests(SimpleTestCase):
    """纯函数测试，不依赖数据库。"""

    def test_split_version_marker(self):
        base, marked = split_version_marker('校园网络管理办法（2024修订版）.docx')
        self.assertTrue(marked)
        self.assertEqual(base, '校园网络管理办法')

    def test_year_only_is_not_explicit_version(self):
        base, marked = split_version_marker('2015级优秀毕业生名单.xlsx')
        self.assertFalse(marked, '只有年份/届别不算显式版本词')
        self.assertIn('优秀毕业生名单', base)

    def test_jaccard(self):
        self.assertEqual(jaccard(BASE_TEXT, BASE_TEXT), 1.0)
        self.assertEqual(jaccard('完全不同的内容甲乙丙', '另一段毫不相干的文字丁戊己'), 0.0)


class VersionDetectionTests(TestCase):
    """识别逻辑：只生成候选，不直接修改旧版。"""

    def setUp(self):
        self.college = College.objects.create(name='计算机与软件学院', code='CS')
        self.dept = Department.objects.create(name='教学管理部')
        self.uploader = User.objects.create_user(
            username='uploader', password='pass12345', role='dept_admin',
            department=self.dept, college=self.college,
        )
        self.other_admin = User.objects.create_user(
            username='other', password='pass12345', role='dept_admin',
            department=self.dept, college=self.college,
        )

    def _make_doc(self, title, file_name, text=BASE_TEXT, uploader=None,
                  visibility='public', **overrides):
        doc = KnowledgeDoc.objects.create(
            title=title,
            source_type='upload',
            file_name=file_name,
            file_type='pdf',
            uploader=uploader or self.uploader,
            status='indexed',
            visibility=visibility,
            college_id=self.college.id,
            department_id=self.dept.id,
            review_status='approved',
            chunk_count=5,
            **overrides,
        )
        _make_chunks(doc, text)
        return doc

    def test_same_file_name_creates_pending_candidate_only(self):
        old = self._make_doc('校园网络管理办法', '校园网络管理办法.pdf')
        new = self._make_doc(
            '校园网络管理办法', '校园网络管理办法.pdf',
            text=BASE_TEXT + '第六条 新增条款，明确版本替代流程。' * 3,
        )

        candidate = detect_versions(new.id)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.new_doc_id, new.id)
        self.assertEqual(candidate.old_doc_id, old.id)
        self.assertEqual(candidate.status, 'pending')
        old.refresh_from_db()
        self.assertIsNone(old.superseded_by_id, '识别阶段不得直接替代旧版')

    def test_similar_content_with_different_name_requires_llm(self):
        old = self._make_doc('校园网络管理办法', '网络管理办法2023.pdf')
        new = self._make_doc('校园网络管理细则', '网络管理细则2024.pdf')

        with patch('apps.knowledge.versioning._llm_verdict', return_value=None):
            self.assertIsNone(detect_versions(new.id), 'LLM 不可用时不得生成候选')

        with patch(
            'apps.knowledge.versioning._llm_verdict',
            return_value={'same_document': True, 'newer': 'old', 'confidence': 'high'},
        ):
            candidate = detect_versions(new.id)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.new_doc_id, new.id)
        self.assertEqual(candidate.old_doc_id, old.id)

    def test_unrelated_document_creates_no_candidate(self):
        self._make_doc('校园网络管理办法', '校园网络管理办法.pdf')
        unrelated = self._make_doc(
            '学生宿舍用电安全须知', '宿舍用电安全须知.pdf',
            text='严禁私拉乱接电线，离开宿舍须关闭电源，发现隐患及时报修。' * 20,
        )
        self.assertIsNone(detect_versions(unrelated.id))

    def test_audience_shrinking_creates_no_candidate(self):
        self._make_doc('校园网络管理办法', '校园网络管理办法.pdf', visibility='public')
        new = self._make_doc(
            '校园网络管理办法', '校园网络管理办法.pdf',
            text=BASE_TEXT + '第六条 新增条款。' * 3, visibility='private',
        )
        self.assertIsNone(
            detect_versions(new.id), '新版受众缩小为私密时不得替代公开旧版',
        )


class VersionConfirmTests(TestCase):
    """确认/拒绝、权限与恢复链路。"""

    def setUp(self):
        self.college = College.objects.create(name='计算机与软件学院', code='CS')
        self.dept = Department.objects.create(name='教学管理部')
        self.uploader = User.objects.create_user(
            username='uploader', password='pass12345', role='dept_admin',
            department=self.dept, college=self.college,
        )
        self.other = User.objects.create_user(
            username='other', password='pass12345', role='dept_admin',
            department=self.dept, college=self.college,
        )
        self.super_admin = User.objects.create_user(
            username='root', password='pass12345', role='super_admin',
        )
        self.old = self._make_doc('校园网络管理办法', 'v1.pdf')
        self.new = self._make_doc('校园网络管理办法', 'v2.pdf')
        self.candidate = DocVersionCandidate.objects.create(
            new_doc=self.new, old_doc=self.old, confidence=0.9,
            signals={'jaccard': 0.9}, reason='文件名相同',
        )

    def _make_doc(self, title, file_name):
        doc = KnowledgeDoc.objects.create(
            title=title, source_type='upload', file_name=file_name,
            file_type='pdf', uploader=self.uploader, status='indexed',
            visibility='public', college_id=self.college.id,
            department_id=self.dept.id, review_status='approved', chunk_count=5,
        )
        _make_chunks(doc)
        return doc

    def test_confirm_marks_old_as_superseded(self):
        candidate = confirm_candidate(self.candidate.id, self.uploader)

        self.assertEqual(candidate.status, 'confirmed')
        self.assertIsNotNone(candidate.decided_at)
        self.old.refresh_from_db()
        self.assertEqual(self.old.superseded_by_id, self.new.id)
        self.assertIsNotNone(self.old.superseded_at)
        self.assertTrue(
            DocRelation.objects.filter(
                source_doc=self.new, target_doc=self.old, relation_type='replaces',
            ).exists()
        )

    def test_confirm_is_idempotent(self):
        confirm_candidate(self.candidate.id, self.uploader)
        candidate = confirm_candidate(self.candidate.id, self.uploader)
        self.assertEqual(candidate.status, 'confirmed')

    def test_reject_keeps_old_active(self):
        candidate = reject_candidate(self.candidate.id, self.uploader)
        self.assertEqual(candidate.status, 'rejected')
        self.old.refresh_from_db()
        self.assertIsNone(self.old.superseded_by_id)

    def test_other_user_cannot_confirm(self):
        with self.assertRaises(PermissionDenied):
            confirm_candidate(self.candidate.id, self.other)

    def test_super_admin_can_confirm(self):
        candidate = confirm_candidate(self.candidate.id, self.super_admin)
        self.assertEqual(candidate.status, 'confirmed')

    def test_retrieval_excludes_superseded_after_confirm(self):
        context = {'role': 'super_admin', 'user_id': self.super_admin.id}
        before = set(authorized_knowledge_docs(context).values_list('id', flat=True))
        self.assertIn(self.old.id, before, '确认前旧版仍在检索范围')

        confirm_candidate(self.candidate.id, self.uploader)

        after = set(authorized_knowledge_docs(context).values_list('id', flat=True))
        self.assertNotIn(self.old.id, after, '确认后旧版必须从 RAG 检索中排除')
        self.assertIn(self.new.id, after)
        self.assertNotIn(
            self.old.id, set(_visible_docs_qs(self.super_admin).values_list('id', flat=True)),
        )

    def test_delete_new_doc_restores_old(self):
        confirm_candidate(self.candidate.id, self.uploader)
        self.new.delete()
        self.old.refresh_from_db()
        self.assertIsNone(self.old.superseded_by_id, '删除新版后旧版应恢复为最新')

    def test_delete_middle_version_relinks_chain(self):
        middle = self.old
        newest = self.new
        oldest = self._make_doc('校园网络管理办法', 'v0.pdf')
        # oldest ← middle ← newest
        oldest.superseded_by = middle
        oldest.superseded_at = middle.created_at
        oldest.save(update_fields=['superseded_by', 'superseded_at'])
        middle.superseded_by = newest
        middle.superseded_at = newest.created_at
        middle.save(update_fields=['superseded_by', 'superseded_at'])

        middle.delete()

        oldest.refresh_from_db()
        self.assertEqual(oldest.superseded_by_id, newest.id, '删中间版后旧版应指向最新版')


class VersionCandidateApiTests(TestCase):
    """候选接口：上传者只看自己的，确认接口可用。"""

    def setUp(self):
        self.client = APIClient()
        self.college = College.objects.create(name='计算机与软件学院', code='CS')
        self.dept = Department.objects.create(name='教学管理部')
        self.uploader = User.objects.create_user(
            username='uploader', password='pass12345', role='dept_admin',
            department=self.dept, college=self.college,
        )
        self.other = User.objects.create_user(
            username='other', password='pass12345', role='dept_admin',
            department=self.dept, college=self.college,
        )
        self.old = KnowledgeDoc.objects.create(
            title='旧版', source_type='upload', file_name='v1.pdf', file_type='pdf',
            uploader=self.uploader, status='indexed', visibility='public',
            college_id=self.college.id, department_id=self.dept.id,
            review_status='approved',
        )
        self.new = KnowledgeDoc.objects.create(
            title='新版', source_type='upload', file_name='v2.pdf', file_type='pdf',
            uploader=self.uploader, status='indexed', visibility='public',
            college_id=self.college.id, department_id=self.dept.id,
            review_status='approved',
        )
        self.candidate = DocVersionCandidate.objects.create(
            new_doc=self.new, old_doc=self.old, confidence=0.9, reason='文件名相同',
        )

    def test_uploader_sees_own_pending_candidates(self):
        self.client.force_authenticate(self.uploader)
        resp = self.client.get('/api/knowledge/version-candidates/')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(len(resp.data['results'] if isinstance(resp.data, dict) else resp.data), 1)

        self.client.force_authenticate(self.other)
        resp = self.client.get('/api/knowledge/version-candidates/')
        body = resp.data['results'] if isinstance(resp.data, dict) else resp.data
        self.assertEqual(len(body), 0, '他人不应看到别人的版本候选')

    def test_confirm_api(self):
        self.client.force_authenticate(self.uploader)
        resp = self.client.post(f'/api/knowledge/version-candidates/{self.candidate.id}/confirm/')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.old.refresh_from_db()
        self.assertEqual(self.old.superseded_by_id, self.new.id)

    def test_confirm_api_forbidden_for_other_user(self):
        self.client.force_authenticate(self.other)
        resp = self.client.post(f'/api/knowledge/version-candidates/{self.candidate.id}/confirm/')
        self.assertEqual(resp.status_code, 403, resp.data)


class DocumentListVersionVisibilityTests(TestCase):
    """列表默认隐藏旧版；include_superseded=1 可查看历史版本。"""

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username='root', password='pass12345', role='super_admin',
        )
        self.new = KnowledgeDoc.objects.create(
            title='新版', source_type='upload', file_name='v2.pdf', file_type='pdf',
            uploader=self.admin, status='indexed', visibility='public',
            review_status='approved',
        )
        self.old = KnowledgeDoc.objects.create(
            title='旧版', source_type='upload', file_name='v1.pdf', file_type='pdf',
            uploader=self.admin, status='indexed', visibility='public',
            review_status='approved', superseded_by=self.new,
        )
        self.client.force_authenticate(self.admin)

    @staticmethod
    def _items(resp):
        return resp.data['results'] if isinstance(resp.data, dict) else resp.data

    def test_list_hides_superseded_by_default(self):
        resp = self.client.get('/api/knowledge/docs/')
        self.assertEqual(resp.status_code, 200, resp.data)
        ids = [item['id'] for item in self._items(resp)]
        self.assertIn(self.new.id, ids)
        self.assertNotIn(self.old.id, ids)

    def test_include_superseded_shows_history(self):
        resp = self.client.get('/api/knowledge/docs/?include_superseded=1')
        ids = [item['id'] for item in self._items(resp)]
        self.assertIn(self.old.id, ids)
        row = next(item for item in self._items(resp) if item['id'] == self.old.id)
        self.assertFalse(row['is_latest'])
        self.assertEqual(row['superseded_by'], self.new.id)
        self.assertEqual(row['superseded_by_title'], '新版')
