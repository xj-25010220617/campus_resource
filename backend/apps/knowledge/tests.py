from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import DocChunk, Keyword, KnowledgeDoc


class KnowledgeDocSearchTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='super_admin',
            password='testpass123',
            role='super_admin',
        )
        self.client.force_authenticate(self.user)

    def test_search_matches_original_file_name(self):
        KnowledgeDoc.objects.create(
            title='校园传统文化实践案例',
            description='剪纸艺术主题活动资料',
            source_type='upload',
            file_name='剪纸福娃的校园奇遇.docx',
            file_type='doc',
            uploader=self.user,
            status='uploaded',
        )

        response = self.client.get(
            '/api/knowledge/docs/',
            {'search': '剪纸福娃的校园奇遇', 'days_back': 0},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
    def test_rag_search_matches_original_file_name(self):
        KnowledgeDoc.objects.create(
            title='校园传统文化实践案例',
            description='剪纸艺术主题活动资料',
            source_type='upload',
            file_name='剪纸福娃的校园奇遇.docx',
            file_type='doc',
            uploader=self.user,
            status='indexed',
        )

        from utils.rag import _keyword_search

        results = _keyword_search(
            '剪纸福娃的校园奇遇',
            user_context={'role': 'super_admin', 'user_id': self.user.id},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['file_name'], '剪纸福娃的校园奇遇.docx')


class RagRetrievalTests(TestCase):
    """RAG 多路检索回归测试。

    覆盖：Keyword 表通道、文件名匹配、正文分块通道、相关分块选择、
    权限一致性、未索引文档不可检索、retrieve() 多通道合并。
    测试设计不依赖 jieba 是否安装（查询词用空格分隔）。
    """

    def setUp(self):
        self.admin = User.objects.create_user(
            username='rag_admin', password='testpass123', role='super_admin',
        )
        self.plain = User.objects.create_user(
            username='rag_user', password='testpass123', role='user',
        )
        self.admin_ctx = {'role': 'super_admin', 'user_id': self.admin.id}
        self.user_ctx = {'role': 'user', 'user_id': self.plain.id}

    def _create_doc(self, **overrides):
        defaults = dict(
            title='2025年春季学期体育工作总结',
            description='本学期各项体育活动开展情况汇总',
            source_type='upload',
            file_name='贡嘎杯跳绳项目开发工作报告.docx',
            file_type='doc',
            uploader=self.admin,
            status='indexed',
        )
        defaults.update(overrides)
        return KnowledgeDoc.objects.create(**defaults)

    def _create_chunks(self, doc, contents):
        for i, text in enumerate(contents):
            DocChunk.objects.create(
                doc=doc, chunk_index=i, content=text,
                token_count=len(text), vector_id=f'doc_{doc.id}_chunk_{i}',
            )

    # ------------------------------------------------------------------
    # Keyword 表通道
    # ------------------------------------------------------------------
    def test_keyword_table_matches_document(self):
        """标题不含查询词、Keyword.phrase 含查询词的文档可以被召回。"""
        doc = self._create_doc()
        Keyword.objects.create(
            doc=doc, phrase='贡嘎杯', weight=5, created_by=self.admin,
        )

        from utils.rag import _keyword_table_search

        results = _keyword_table_search(['贡嘎杯'], self.admin_ctx)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['doc_id'], doc.id)
        self.assertEqual(results[0]['file_name'], '贡嘎杯跳绳项目开发工作报告.docx')
        self.assertIsNotNone(results[0]['content'])

    def test_keyword_table_respects_permissions(self):
        """普通用户不能通过 Keyword 表检索到私有文档。"""
        doc = self._create_doc(visibility='private')
        Keyword.objects.create(
            doc=doc, phrase='贡嘎杯', weight=5, created_by=self.admin,
        )

        from utils.rag import _keyword_table_search

        self.assertEqual(_keyword_table_search(['贡嘎杯'], self.user_ctx), [])

    def test_unindexed_document_not_searchable(self):
        """未完成索引的文档不参与 Keyword 表和分块检索。"""
        doc = self._create_doc(status='uploaded')
        Keyword.objects.create(
            doc=doc, phrase='贡嘎杯', weight=5, created_by=self.admin,
        )
        self._create_chunks(doc, ['贡嘎杯跳绳比赛于四月举行。'])

        from utils.rag import _chunk_content_search, _keyword_table_search

        self.assertEqual(_keyword_table_search(['贡嘎杯'], self.admin_ctx), [])
        self.assertEqual(_chunk_content_search(['贡嘎杯'], self.admin_ctx), [])

    # ------------------------------------------------------------------
    # 文件名匹配
    # ------------------------------------------------------------------
    def test_keyword_search_matches_file_name(self):
        """文件名包含查询词但标题/描述不包含时，元数据检索可以召回。"""
        self._create_doc()

        from utils.rag import _keyword_search

        results = _keyword_search(
            '贡嘎杯跳绳项目开发工作报告', user_context=self.admin_ctx,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['file_name'], '贡嘎杯跳绳项目开发工作报告.docx')

    # ------------------------------------------------------------------
    # 正文分块通道
    # ------------------------------------------------------------------
    def test_chunk_content_search_matches_second_chunk(self):
        """查询词只出现在第二个分块时，分块检索返回第二个分块。"""
        doc = self._create_doc()
        self._create_chunks(doc, [
            '本报告介绍学期体育课程安排与教学目标。',
            '贡嘎杯跳绳比赛于四月举行，包含耐力与速度两项测试。',
        ])

        from utils.rag import _chunk_content_search

        results = _chunk_content_search(['贡嘎杯'], self.admin_ctx)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['chunk_index'], 1)
        self.assertIn('贡嘎杯', results[0]['content'])

    def test_chunk_content_search_respects_permissions(self):
        """普通用户不能通过 DocChunk.content 检索到私有文档。"""
        doc = self._create_doc(visibility='private')
        self._create_chunks(doc, ['贡嘎杯跳绳比赛于四月举行。'])

        from utils.rag import _chunk_content_search

        self.assertEqual(_chunk_content_search(['贡嘎杯'], self.user_ctx), [])

    # ------------------------------------------------------------------
    # 兜底检索：相关分块优先
    # ------------------------------------------------------------------
    def test_fallback_selects_relevant_chunk_not_first(self):
        """兜底检索不再固定取第一个分块，而是返回包含查询词的分块。"""
        doc = self._create_doc(title='跳绳项目工作报告')
        self._create_chunks(doc, [
            '本报告介绍项目背景与组织分工。',
            '贡嘎杯比赛要求全体队员完成跳绳耐力测试。',
        ])

        from utils.rag import _fallback_title_search

        results = _fallback_title_search('贡嘎杯 跳绳', self.admin_ctx)

        self.assertEqual(len(results), 1)
        self.assertIn('贡嘎杯', results[0]['content'])
        self.assertEqual(results[0]['chunk_index'], 1)

    # ------------------------------------------------------------------
    # 未索引文档诊断提示
    # ------------------------------------------------------------------
    def test_find_pending_index_docs(self):
        """检索无结果时能识别出匹配但未完成索引的文档（含状态）。"""
        self._create_doc(status='embedding')

        from utils.rag import find_pending_index_docs

        pending = find_pending_index_docs('贡嘎杯 跳绳', self.admin_ctx)

        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['status'], 'embedding')

    def test_find_pending_excludes_indexed(self):
        """已索引文档不进入 pending 提示。"""
        self._create_doc(status='indexed')

        from utils.rag import find_pending_index_docs

        self.assertEqual(find_pending_index_docs('贡嘎杯 跳绳', self.admin_ctx), [])

    def test_find_pending_respects_permissions(self):
        """普通用户不能通过 pending 提示感知私有文档存在。"""
        self._create_doc(visibility='private', status='embedding')

        from utils.rag import find_pending_index_docs

        self.assertEqual(find_pending_index_docs('贡嘎杯 跳绳', self.user_ctx), [])

    # ------------------------------------------------------------------
    # retrieve() 集成：多通道合并 + 来源元数据
    # ------------------------------------------------------------------
    def test_retrieve_integrates_new_channels(self):
        """向量检索为空时，文件名/Keyword/分块通道合并召回并带来源元数据。"""
        doc = self._create_doc()
        self._create_chunks(doc, [
            '本报告介绍项目背景与组织分工。',
            '贡嘎杯比赛要求全体队员完成跳绳耐力测试。',
        ])
        Keyword.objects.create(
            doc=doc, phrase='贡嘎杯', weight=5, created_by=self.admin,
        )

        from utils import rag

        question = '贡嘎杯 跳绳 项目的目标是什么'
        with patch('utils.query_rewriter.rewrite_query', return_value=[question]), \
                patch.object(rag, '_cached_embed', return_value=None):
            results = rag.retrieve(question, user_context=self.admin_ctx)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['doc_id'], doc.id)
        self.assertEqual(results[0]['file_name'], '贡嘎杯跳绳项目开发工作报告.docx')
        self.assertIn('贡嘎杯', results[0]['content'])
        self.assertGreaterEqual(results[0]['score'], 0.35)

    def test_retrieve_respects_permissions(self):
        """retrieve() 整体不向普通用户泄露私有文档。"""
        doc = self._create_doc(visibility='private')
        self._create_chunks(doc, ['贡嘎杯跳绳比赛于四月举行。'])
        Keyword.objects.create(
            doc=doc, phrase='贡嘎杯', weight=5, created_by=self.admin,
        )

        from utils import rag

        question = '贡嘎杯 跳绳 项目的目标是什么'
        with patch('utils.query_rewriter.rewrite_query', return_value=[question]), \
                patch.object(rag, '_cached_embed', return_value=None):
            results = rag.retrieve(question, user_context=self.user_ctx)

        self.assertEqual(results, [])


class SuperAdminPrivateDocTests(TestCase):
    """超管上传的 private 文档仅该超管本人可见（浏览 + AI 检索两条链路一致）。"""

    def setUp(self):
        self.owner = User.objects.create_user(
            username='sa_owner', password='testpass123', role='super_admin',
        )
        self.other_sa = User.objects.create_user(
            username='sa_other', password='testpass123', role='super_admin',
        )
        self.college_admin = User.objects.create_user(
            username='sa_college_admin', password='testpass123', role='college_admin',
        )
        self.dept_admin = User.objects.create_user(
            username='sa_dept_admin', password='testpass123', role='dept_admin',
        )
        # 超管上传的文档：college_id / department_id 均为 null（校级文档），
        # 修复前会命中 SCHOOL_LEVEL 向所有管理角色泄露。
        self.doc = KnowledgeDoc.objects.create(
            title='超管私密文档',
            source_type='upload',
            file_name='secret.pdf',
            file_type='pdf',
            uploader=self.owner,
            status='indexed',
            visibility='private',
        )

    def _browsable_ids(self, user):
        from apps.knowledge.views import KnowledgeDocListView
        return set(
            KnowledgeDocListView._admin_scope_filter(
                KnowledgeDoc.objects.all(), user,
            ).values_list('id', flat=True)
        )

    def _retrievable_ids(self, user):
        from utils.rag import authorized_knowledge_docs
        return set(
            authorized_knowledge_docs({'role': user.role, 'user_id': user.id})
            .values_list('id', flat=True)
        )

    def test_owner_super_admin_can_browse(self):
        self.assertIn(self.doc.id, self._browsable_ids(self.owner))

    def test_other_super_admin_cannot_browse(self):
        self.assertNotIn(self.doc.id, self._browsable_ids(self.other_sa))

    def test_college_admin_cannot_browse(self):
        self.assertNotIn(self.doc.id, self._browsable_ids(self.college_admin))

    def test_dept_admin_cannot_browse(self):
        self.assertNotIn(self.doc.id, self._browsable_ids(self.dept_admin))

    def test_owner_super_admin_can_retrieve(self):
        self.assertIn(self.doc.id, self._retrievable_ids(self.owner))

    def test_other_super_admin_cannot_retrieve(self):
        self.assertNotIn(self.doc.id, self._retrievable_ids(self.other_sa))
