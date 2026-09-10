import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

import oss2

from apps.accounts.models import College, User
from utils.oss import (
    OFFICIAL_KEY_PREFIX,
    QUARANTINE_KEY_PREFIX,
    OSSOperationError,
    build_quarantine_key,
    oss_client,
)

from .models import (
    ChunkedUploadSession,
    ContentModeration,
    DocChunk,
    Keyword,
    KnowledgeDoc,
    ModerationAttempt,
)
from .moderation import (
    INDEXABLE_MODERATION_STATES,
    INDEXABLE_OSS_KEY_PREFIX,
    MAX_EXCERPT_LENGTH,
    MAX_FINDINGS,
    MAX_FINDINGS_JSON_BYTES,
    MODERATION_STATE_CHOICES,
    STORAGE_STATE_CHOICES,
    VERDICT_CHOICES,
    is_indexable_moderation_state,
    normalize_findings,
)


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


# ======================================================================
# Phase 1-1 安全止血回归测试
#
# 覆盖：上传回调的对象真实性校验、序列化器内部状态字段保护、
#       分片上传大小/归属/并发校验、管理员可见范围（R26）、
#       索引任务集中式前置校验。
# ======================================================================

CALLBACK_URL = '/api/knowledge/upload/callback/'
DOC_LIST_URL = '/api/knowledge/docs/'
CHUNK_INIT_URL = '/api/knowledge/upload/chunk/init/'
CHUNK_URL = '/api/knowledge/upload/chunk/'
CHUNK_MERGE_URL = '/api/knowledge/upload/chunk/merge/'

MB = 1024 * 1024


def _oss_meta(size, content_type='application/pdf', etag='', exists=True):
    """构造 OSSClient.head_object 的返回值（与 utils/oss.py 约定一致）。"""
    if not exists:
        return {'exists': False, 'size': 0, 'content_type': '', 'etag': ''}
    return {'exists': True, 'size': size, 'content_type': content_type, 'etag': etag}


class UploadCallbackSecurityTests(TestCase):
    """上传回调：客户端提交的 oss_key / 大小 / 类型不再被无条件信任。"""

    def setUp(self):
        self.client = APIClient()
        self.college = College.objects.create(name='计算机与软件学院', code='CS')
        self.admin = User.objects.create_user(
            username='cb_admin', password='testpass123',
            role='college_admin', college=self.college,
        )
        self.client.force_authenticate(self.admin)
        self.oss_key = f'knowledge/college/{self.college.id}/2026-09/abcdef123456.pdf'

    def _payload(self, **overrides):
        data = {
            'oss_key': self.oss_key,
            'file_name': '课程设计报告.pdf',
            'file_size': 2048,
            'file_type': 'pdf',
            'md5': 'a' * 32,
        }
        data.update(overrides)
        return data

    def _post(self, data, meta):
        """回调：head_object 与异步任务全 mock。

        UploadCallbackView 在 post() 内部才 import safe_delay，因此这里 patch
        `utils.celery_dispatcher.safe_delay` 是生效的；测试中不得让任务同步
        执行并真实访问 OSS。
        """
        with patch('apps.knowledge.views.oss_client.head_object', return_value=meta), \
                patch('utils.celery_dispatcher.safe_delay'):
            return self.client.post(CALLBACK_URL, data, format='json')

    # ------------------------------------------------------------------
    # 对象真实性
    # ------------------------------------------------------------------
    def test_rejects_missing_object(self):
        """OSS 上不存在该对象时不得创建文档记录。"""
        response = self._post(self._payload(), _oss_meta(0, exists=False))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(KnowledgeDoc.objects.count(), 0)

    def test_rejects_size_mismatch(self):
        """声明的 file_size 与 OSS 实际对象大小不一致时拒绝。"""
        response = self._post(self._payload(file_size=2048), _oss_meta(9999))

        self.assertEqual(response.status_code, 400)
        self.assertIn('file_size', response.data)
        self.assertEqual(KnowledgeDoc.objects.count(), 0)

    def test_rejects_content_type_mismatch(self):
        """文件名后缀推断的类型与 OSS 记录的实际类型冲突时拒绝。"""
        response = self._post(
            self._payload(), _oss_meta(2048, content_type='image/png'),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('file_name', response.data)
        self.assertEqual(KnowledgeDoc.objects.count(), 0)

    def test_accepts_generic_content_type(self):
        """OSS 对象为通用类型时不误伤（前端 PUT 未指定 Content-Type 的场景）。"""
        response = self._post(
            self._payload(), _oss_meta(2048, content_type='application/octet-stream'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(KnowledgeDoc.objects.count(), 1)

    def test_rejects_when_object_cannot_be_verified(self):
        """OSS 不可用（无法验证）时拒绝，而非放行。"""
        response = self._post(self._payload(), None)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(KnowledgeDoc.objects.count(), 0)

    def test_rejects_etag_md5_mismatch(self):
        """OSS ETag（简单 PUT 下即对象 MD5）与声明的 md5 不一致时拒绝。"""
        response = self._post(
            self._payload(md5='a' * 32), _oss_meta(2048, etag='b' * 32),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('md5', response.data)
        self.assertEqual(KnowledgeDoc.objects.count(), 0)

    # ------------------------------------------------------------------
    # Key 归属范围
    # ------------------------------------------------------------------
    def test_rejects_key_of_other_college(self):
        """不得认领其他学院前缀下的对象。"""
        response = self._post(
            self._payload(oss_key='knowledge/college/999/2026-09/other.pdf'),
            _oss_meta(2048),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('oss_key', response.data)
        self.assertEqual(KnowledgeDoc.objects.count(), 0)

    def test_rejects_key_outside_upload_scopes(self):
        """knowledge/ 下非上传范围的前缀（如爬虫的 website/）同样拒绝。"""
        response = self._post(
            self._payload(oss_key='knowledge/website/0/2026-09/crawled.pdf'),
            _oss_meta(2048),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(KnowledgeDoc.objects.count(), 0)

    def test_rejects_relative_path_key(self):
        """路径穿越仍然被拦。"""
        response = self._post(
            self._payload(oss_key='knowledge/college/1/../../etc/passwd'),
            _oss_meta(2048),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(KnowledgeDoc.objects.count(), 0)

    # ------------------------------------------------------------------
    # 正常路径 / 重复回调
    # ------------------------------------------------------------------
    def test_accepts_valid_upload_and_uses_actual_size(self):
        """合法上传通过，且入库大小取 OSS 实测值。"""
        response = self._post(self._payload(file_size=2048), _oss_meta(2048))

        self.assertEqual(response.status_code, 200)
        doc = KnowledgeDoc.objects.get()
        self.assertEqual(doc.file_size, 2048)
        self.assertEqual(doc.uploader_id, self.admin.id)

    def test_duplicate_md5_returns_existing_document(self):
        """同 MD5 重复回调返回既有文档，不新建。"""
        existing = KnowledgeDoc.objects.create(
            title='已存在', source_type='upload', file_name='old.pdf',
            file_type='pdf', uploader=self.admin, status='indexed',
            md5_hash='a' * 32, visibility='public',
        )

        response = self._post(self._payload(), _oss_meta(2048))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.get('duplicate'))
        self.assertEqual(response.data['id'], existing.id)
        self.assertEqual(KnowledgeDoc.objects.count(), 1)

    def test_same_key_does_not_create_second_document(self):
        """同一 oss_key 再次回调只返回既有文档，不新建第二条指向同一对象的记录。

        否则两条记录共享一个 OSS 对象，删除任意一条都会 delete_object 掉
        另一条仍在使用的文件。
        """
        existing = KnowledgeDoc.objects.create(
            title='已登记', source_type='upload', file_name='课程设计报告.pdf',
            file_type='pdf', uploader=self.admin, status='indexed',
            oss_key=self.oss_key, md5_hash='c' * 32, visibility='public',
        )

        response = self._post(self._payload(md5='a' * 32), _oss_meta(2048))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.get('duplicate'))
        self.assertEqual(response.data['id'], existing.id)
        self.assertEqual(KnowledgeDoc.objects.count(), 1)

    def test_same_key_invisible_document_is_rejected(self):
        """对象已被他人私有登记时既不能新建记录，也不得泄露该文档详情。"""
        other = User.objects.create_user(
            username='cb_other', password='testpass123',
            role='college_admin', college=self.college,
        )
        KnowledgeDoc.objects.create(
            title='他人私有文档', source_type='upload', file_name='课程设计报告.pdf',
            file_type='pdf', uploader=other, status='indexed',
            oss_key=self.oss_key, md5_hash='c' * 32, visibility='private',
        )

        response = self._post(self._payload(md5='a' * 32), _oss_meta(2048))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(KnowledgeDoc.objects.count(), 1)


class KnowledgeDocSerializerSecurityTests(TestCase):
    """序列化器：服务端内部状态字段不得由客户端写入。"""

    def setUp(self):
        self.college = College.objects.create(name='计算机与软件学院', code='CS')
        self.super_admin = User.objects.create_user(
            username='ser_super', password='testpass123', role='super_admin',
        )
        self.college_admin = User.objects.create_user(
            username='ser_ca', password='testpass123',
            role='college_admin', college=self.college,
        )

    def _client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def _create_doc(self, uploader, **overrides):
        defaults = dict(
            title='测试文档', source_type='upload', file_name='a.pdf',
            file_type='pdf', uploader=uploader, status='uploaded',
        )
        defaults.update(overrides)
        return KnowledgeDoc.objects.create(**defaults)

    def test_client_cannot_forge_indexed_status(self):
        """客户端不能把 status 改成 indexed 从而穿过 RAG 检索门。

        read_only_fields 对 PUT / PATCH 同时生效。
        """
        doc = self._create_doc(self.super_admin, status='uploaded')

        response = self._client_for(self.super_admin).patch(
            f'/api/knowledge/docs/{doc.id}/', {'status': 'indexed'}, format='json',
        )

        self.assertEqual(response.status_code, 200)
        doc.refresh_from_db()
        self.assertEqual(doc.status, 'uploaded')

    def test_client_cannot_forge_moderation_flags(self):
        """客户端不能伪造 is_sensitive / sensitive_checked 冒充"已安全审核"。"""
        doc = self._create_doc(self.super_admin)

        response = self._client_for(self.super_admin).patch(
            f'/api/knowledge/docs/{doc.id}/',
            {'is_sensitive': False, 'sensitive_checked': True},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        doc.refresh_from_db()
        self.assertFalse(doc.is_sensitive)
        self.assertFalse(doc.sensitive_checked)

    def test_non_super_admin_cannot_rescope_college(self):
        """非超管不能改写文档归属学院（否则可把院级文档改成校级扩大可见范围）。"""
        doc = self._create_doc(self.college_admin, college=self.college)

        response = self._client_for(self.college_admin).patch(
            f'/api/knowledge/docs/{doc.id}/', {'college': None}, format='json',
        )

        self.assertEqual(response.status_code, 403)
        doc.refresh_from_db()
        self.assertEqual(doc.college_id, self.college.id)

    def test_super_admin_can_rescope_college(self):
        """超管仍然可以调整归属（保留既有管理能力）。"""
        doc = self._create_doc(self.college_admin, college=self.college)

        response = self._client_for(self.super_admin).patch(
            f'/api/knowledge/docs/{doc.id}/', {'college': None}, format='json',
        )

        self.assertEqual(response.status_code, 200)
        doc.refresh_from_db()
        self.assertIsNone(doc.college_id)


class AdminScopeVisibilityTests(TestCase):
    """R26：private 文档仅上传者本人与 super_admin 可见。"""

    def setUp(self):
        # 删除接口在删除记录后会调用 oss_client.delete 清理 OSS 对象。本类只验证
        # 可见性 / 权限，不验证 OSS 清理，必须把该调用挡掉——否则一旦 .env 里配置了
        # 真实 AK，测试就会向生产 OSS 发出真实的 DELETE 请求。
        delete_patcher = patch.object(oss_client, 'delete', return_value=True)
        delete_patcher.start()
        self.addCleanup(delete_patcher.stop)

        self.college_a = College.objects.create(name='计算机与软件学院', code='CS')
        self.owner = User.objects.create_user(
            username='scope_owner', password='testpass123',
            role='college_admin', college=self.college_a,
        )
        self.peer = User.objects.create_user(
            username='scope_peer', password='testpass123',
            role='college_admin', college=self.college_a,
        )
        self.super_admin = User.objects.create_user(
            username='scope_super', password='testpass123', role='super_admin',
        )
        self.private_doc = KnowledgeDoc.objects.create(
            title='私密文档', source_type='upload', file_name='private.pdf',
            file_type='pdf', uploader=self.owner, college=self.college_a,
            visibility='private', status='uploaded',
            oss_key='knowledge/college/1/2026-09/private.pdf',
        )
        self.public_doc = KnowledgeDoc.objects.create(
            title='公开文档', source_type='upload', file_name='public.pdf',
            file_type='pdf', uploader=self.owner, college=self.college_a,
            visibility='public', status='uploaded',
        )

    def _client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_peer_admin_does_not_see_private_doc_in_list(self):
        response = self._client_for(self.peer).get(DOC_LIST_URL, {'days_back': 0})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], self.public_doc.id)

    def test_private_metadata_and_signed_url_not_leaked(self):
        """响应体里不得出现 private 文档的 oss_key（自然也不含其签名 URL）。"""
        response = self._client_for(self.peer).get(DOC_LIST_URL, {'days_back': 0})

        body = json.dumps(response.data, ensure_ascii=False)
        self.assertNotIn(self.private_doc.oss_key, body)
        self.assertNotIn('private.pdf', body)

    def test_peer_admin_cannot_open_private_doc_detail(self):
        response = self._client_for(self.peer).get(
            f'/api/knowledge/docs/{self.private_doc.id}/',
        )

        self.assertEqual(response.status_code, 404)

    def test_owner_still_sees_own_private_doc(self):
        response = self._client_for(self.owner).get(DOC_LIST_URL, {'days_back': 0})

        self.assertEqual(response.data['count'], 2)

    def test_super_admin_still_sees_private_doc(self):
        response = self._client_for(self.super_admin).get(DOC_LIST_URL, {'days_back': 0})

        self.assertEqual(response.data['count'], 2)

    def test_public_doc_visibility_not_broken(self):
        response = self._client_for(self.peer).get(
            f'/api/knowledge/docs/{self.public_doc.id}/',
        )

        self.assertEqual(response.status_code, 200)

    def test_peer_admin_cannot_delete_private_doc(self):
        """看不见的文档也不应删得掉，避免"可见范围收窄、破坏性操作没收窄"。"""
        response = self._client_for(self.peer).delete(
            f'/api/knowledge/docs/{self.private_doc.id}/delete/',
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(KnowledgeDoc.objects.filter(id=self.private_doc.id).exists())

    def test_owner_can_delete_own_private_doc(self):
        response = self._client_for(self.owner).delete(
            f'/api/knowledge/docs/{self.private_doc.id}/delete/',
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(KnowledgeDoc.objects.filter(id=self.private_doc.id).exists())


class ChunkedUploadSecurityTests(TestCase):
    """分片上传：大小上限、会话归属、重复合并。"""

    def setUp(self):
        self.college = College.objects.create(name='计算机与软件学院', code='CS')
        self.user = User.objects.create_user(
            username='chunk_admin', password='testpass123',
            role='college_admin', college=self.college,
        )
        self.other = User.objects.create_user(
            username='chunk_other', password='testpass123',
            role='college_admin', college=self.college,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.oss_key = f'knowledge/college/{self.college.id}/2026-09/chunk.pdf'

    def _session(self, user=None, **overrides):
        defaults = dict(
            upload_id='up-test-1', user=user or self.user, file_name='chunk.pdf',
            file_size=0, file_md5='a' * 32, oss_key=self.oss_key,
        )
        defaults.update(overrides)
        return ChunkedUploadSession.objects.create(**defaults)

    def _init(self, data):
        """初始化分片上传：OSS 全 mock，返回响应。"""
        with patch('apps.knowledge.chunked_views.oss_client') as mock_oss:
            mock_oss.check_duplicate.return_value = None
            mock_oss.bucket.init_multipart_upload.return_value = SimpleNamespace(
                upload_id='up-new-1',
            )
            return self.client.post(CHUNK_INIT_URL, data, format='json')

    def _merge(self, data):
        """合并分片：OSS 与异步任务全 mock，返回响应。

        注意 patch 目标是 `apps.knowledge.chunked_views.safe_delay` 而不是
        `utils.celery_dispatcher.safe_delay`：前者在模块导入时已绑定，改后者
        不会生效，Celery 缺失时索引任务会同步执行并真的去访问 OSS。
        """
        with patch('apps.knowledge.chunked_views.oss_client') as mock_oss, \
                patch('apps.knowledge.chunked_views.safe_delay'):
            mock_oss.check_duplicate.return_value = None
            return self.client.post(CHUNK_MERGE_URL, data, format='json')

    # ------------------------------------------------------------------
    # 初始化：大小上限
    # ------------------------------------------------------------------
    def test_init_rejects_declared_size_over_type_limit(self):
        """pdf 声明 60MB 超过 50MB 分档上限，初始化即拒绝。"""
        response = self._init({
            'file_md5': 'a' * 32, 'filename': 'big.pdf', 'file_size': 60 * MB,
        })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(ChunkedUploadSession.objects.count(), 0)

    def test_init_rejects_declared_size_over_absolute_limit(self):
        response = self._init({
            'file_md5': 'a' * 32, 'filename': 'huge.mp4', 'file_size': 900 * MB,
        })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(ChunkedUploadSession.objects.count(), 0)

    def test_init_rejects_negative_declared_size(self):
        response = self._init({
            'file_md5': 'a' * 32, 'filename': 'a.pdf', 'file_size': -1,
        })

        self.assertEqual(response.status_code, 400)

    def test_init_allows_zero_declared_size(self):
        """EMPTY_FILE_POLICY = ALLOWED：零字节文件仍可上传（沿用 c402ed75 行为）。"""
        response = self._init({
            'file_md5': 'a' * 32, 'filename': 'empty.pdf', 'file_size': 0,
        })

        self.assertEqual(response.status_code, 200)
        session = ChunkedUploadSession.objects.get(upload_id='up-new-1')
        self.assertEqual(session.file_size, 0)

    def test_init_allows_missing_declared_size(self):
        """未提供 file_size 时同样接受，落库为 0。"""
        response = self._init({'file_md5': 'a' * 32, 'filename': 'empty.pdf'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ChunkedUploadSession.objects.get().file_size, 0)

    # ------------------------------------------------------------------
    # 分片：单分片上限与序号范围
    # ------------------------------------------------------------------
    def test_chunk_rejects_oversize_part(self):
        self._session()

        with patch('apps.knowledge.chunked_views.MAX_CHUNK_SIZE', 1024), \
                patch('apps.knowledge.chunked_views.oss_client'):
            response = self.client.post(CHUNK_URL, {
                'upload_id': 'up-test-1',
                'chunk_index': 0,
                'chunk_file': SimpleUploadedFile('part.bin', b'x' * 4096),
            }, format='multipart')

        self.assertEqual(response.status_code, 400)
        self.assertIn('chunk_file', response.data)

    def test_chunk_rejects_out_of_range_index(self):
        self._session()

        response = self.client.post(CHUNK_URL, {
            'upload_id': 'up-test-1',
            'chunk_index': 10000,
            'chunk_file': SimpleUploadedFile('part.bin', b'x'),
        }, format='multipart')

        self.assertEqual(response.status_code, 400)

    def test_chunk_rejects_negative_index(self):
        self._session()

        response = self.client.post(CHUNK_URL, {
            'upload_id': 'up-test-1',
            'chunk_index': -1,
            'chunk_file': SimpleUploadedFile('part.bin', b'x'),
        }, format='multipart')

        self.assertEqual(response.status_code, 400)

    def test_chunk_rejects_other_users_session(self):
        """用户 A 不得向用户 B 的会话上传分片。"""
        self._session(user=self.other)

        response = self.client.post(CHUNK_URL, {
            'upload_id': 'up-test-1',
            'chunk_index': 0,
            'chunk_file': SimpleUploadedFile('part.bin', b'x'),
        }, format='multipart')

        self.assertEqual(response.status_code, 404)

    # ------------------------------------------------------------------
    # 合并：实测大小、归属、重复合并
    # ------------------------------------------------------------------
    def test_merge_rejects_total_over_absolute_limit(self):
        """file_size=0（未声明）时，实测总大小超过绝对上限仍必须拒绝。"""
        self._session(file_size=0)

        with patch('apps.knowledge.chunked_views._list_uploaded_parts',
                   return_value=[(1, 'etag1', 600 * MB)]):
            response = self._merge({'upload_id': 'up-test-1', 'file_md5': 'a' * 32})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(KnowledgeDoc.objects.count(), 0)

    def test_merge_rejects_total_over_type_limit(self):
        """实测总大小未超绝对上限但超过该类型分档上限时拒绝。"""
        self._session(file_size=0)

        with patch('apps.knowledge.chunked_views._list_uploaded_parts',
                   return_value=[(1, 'etag1', 60 * MB)]):
            response = self._merge({'upload_id': 'up-test-1', 'file_md5': 'a' * 32})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(KnowledgeDoc.objects.count(), 0)

    def test_merge_rejects_size_mismatch_with_declared(self):
        """声明大小与 OSS 实测总大小不一致（分片缺失）时拒绝。"""
        self._session(file_size=2048)

        with patch('apps.knowledge.chunked_views._list_uploaded_parts',
                   return_value=[(1, 'etag1', 1024)]):
            response = self._merge({'upload_id': 'up-test-1', 'file_md5': 'a' * 32})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(KnowledgeDoc.objects.count(), 0)

    def test_merge_accepts_undeclared_size_and_uses_actual(self):
        """未声明大小时以 OSS 实测总大小入库。"""
        self._session(file_size=0)

        with patch('apps.knowledge.chunked_views._list_uploaded_parts',
                   return_value=[(1, 'etag1', 1024), (2, 'etag2', 1024)]):
            response = self._merge({'upload_id': 'up-test-1', 'file_md5': 'a' * 32})

        self.assertEqual(response.status_code, 200)
        doc = KnowledgeDoc.objects.get()
        self.assertEqual(doc.file_size, 2048)

    def test_merge_rejects_duplicate_merge(self):
        """同一会话不得被合并两次（第二次必须被安全拒绝）。"""
        self._session(file_size=2048)

        parts = [(1, 'etag1', 2048)]
        with patch('apps.knowledge.chunked_views._list_uploaded_parts', return_value=parts):
            first = self._merge({'upload_id': 'up-test-1', 'file_md5': 'a' * 32})
            second = self._merge({'upload_id': 'up-test-1', 'file_md5': 'a' * 32})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 404)
        self.assertEqual(KnowledgeDoc.objects.count(), 1)
        self.assertEqual(
            ChunkedUploadSession.objects.get(upload_id='up-test-1').status,
            'completed',
        )

    def test_merge_rejects_other_users_session(self):
        """用户 A 不得合并用户 B 的会话。"""
        self._session(user=self.other, file_size=2048)

        with patch('apps.knowledge.chunked_views._list_uploaded_parts',
                   return_value=[(1, 'etag1', 2048)]):
            response = self._merge({'upload_id': 'up-test-1', 'file_md5': 'a' * 32})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(KnowledgeDoc.objects.count(), 0)


class IndexTaskSecurityTests(TestCase):
    """索引任务：集中式前置校验必须落在任务体内，且不得依赖死字段。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username='idx_admin', password='testpass123', role='super_admin',
        )

    @staticmethod
    def _run_task(doc_id):
        """兼容 Celery 已安装 / 未安装两种方式调用任务函数本体。"""
        from .tasks import index_document_task

        return getattr(index_document_task, 'run', index_document_task)(None, doc_id)

    def _doc(self, **overrides):
        defaults = dict(
            title='索引测试', source_type='upload', file_name='a.pdf',
            file_type='pdf', uploader=self.user, status='uploaded',
        )
        defaults.update(overrides)
        return KnowledgeDoc.objects.create(**defaults)

    def test_task_rejects_document_without_oss_key(self):
        """没有 OSS 对象的文档在任务体内即被拒绝，不会走到下载。"""
        doc = self._doc(oss_key='')

        with patch('utils.oss.oss_client.get_download_url') as mock_url:
            self._run_task(doc.id)
            mock_url.assert_not_called()

        doc.refresh_from_db()
        self.assertEqual(doc.status, 'failed')
        self.assertIn('索引前置校验未通过', doc.index_error)

    def test_task_rejects_document_outside_knowledge_prefix(self):
        doc = self._doc(oss_key='elsewhere/a.pdf')

        self._run_task(doc.id)

        doc.refresh_from_db()
        self.assertEqual(doc.status, 'failed')

    def test_task_consults_central_check(self):
        """任务体确实调用了集中式校验函数。"""
        doc = self._doc(oss_key='knowledge/college/0/2026-09/a.pdf')

        with patch('apps.knowledge.moderation.check_indexable',
                   return_value=(False, '测试拒绝原因')):
            self._run_task(doc.id)

        doc.refresh_from_db()
        self.assertEqual(doc.status, 'failed')
        self.assertIn('测试拒绝原因', doc.index_error)

    def test_central_check_does_not_use_dead_moderation_fields(self):
        """is_sensitive / sensitive_checked 是死字段，绝不能被当作审核结论。

        这里刻意把两个字段设成"看起来已审核通过"的值，校验函数仍应放行
        （而不是把它们当成审核门）——真正的内容审核门属于 P1-2。
        """
        from .moderation import check_indexable

        doc = self._doc(
            oss_key='knowledge/college/0/2026-09/a.pdf',
            is_sensitive=True, sensitive_checked=True,
        )

        allowed, reason = check_indexable(doc)

        self.assertTrue(allowed, reason)

    def test_dispatch_path_lands_on_guarded_task(self):
        """上传链路使用的 safe_delay 分发路径同样落在受校验的任务体内。

        这是上传视图触发索引的唯一入口：无论 Celery 可用（异步入队）还是
        不可用（同步降级），最终执行的都是带 check_indexable 前置校验的
        同一个任务函数，不存在绕过校验的第二条分发路径。
        """
        from utils.celery_dispatcher import safe_delay

        from .tasks import index_document_task

        doc = self._doc(oss_key='')

        self.assertEqual(safe_delay(index_document_task, doc.id), None)

        doc.refresh_from_db()
        self.assertEqual(doc.status, 'failed')
        self.assertIn('索引前置校验未通过', doc.index_error)

    def test_safe_delay_fallback_also_lands_on_guarded_task(self):
        """模拟 broker 不可用：safe_delay 的降级分支执行同一个受校验函数。

        本环境未安装 celery，任务对象是兼容层包装后的普通函数，调用时需要
        补 None 占位 self（与 utils/shared_task_compat 的降级路径一致）。
        """
        from utils.celery_dispatcher import safe_delay

        from .tasks import index_document_task

        class _BrokerDownTask:
            """只保留"异步分发失败"特征的替身，调用即等价于任务函数本体。"""

            name = 'apps.knowledge.tasks.index_document_task'

            def delay(self, *args, **kwargs):
                raise RuntimeError('broker unavailable')

            def __call__(self, doc_id):
                return index_document_task(None, doc_id)

        doc = self._doc(oss_key='')

        safe_delay(_BrokerDownTask(), doc.id)

        doc.refresh_from_db()
        self.assertEqual(doc.status, 'failed')
        self.assertIn('索引前置校验未通过', doc.index_error)


# ===========================================================================
# P1-2A：Moderation 数据模型 + Quarantine OSS 基础设施
#
# OSS 相关测试全部使用替身 Bucket（patch oss_client._bucket），
# 不产生任何真实 OSS 请求：不 GET、不 PUT、不 COPY、不 DELETE。
# ===========================================================================


def _make_doc(user, title='审核测试文档'):
    """构造一份最小可用的 KnowledgeDoc。"""
    return KnowledgeDoc.objects.create(
        title=title,
        source_type='upload',
        file_name='测试资料.pdf',
        file_type='pdf',
        uploader=user,
        status='uploaded',
    )


class _FakeHeadResult:
    """模拟 oss2 HEAD 返回值（只需 head_object 包装层用到的属性）。"""

    def __init__(self, size, content_type='application/octet-stream',
                 etag='d41d8cd98f00b204e9800998ecf8427e'):
        self.content_length = size
        self.headers = {'Content-Type': content_type}
        self.etag = etag


class _FakeGetResult:
    """模拟 oss2 的 GetObjectResult：支持 read(n) 与 close()。"""

    def __init__(self, payload):
        self._payload = payload
        self._pos = 0
        self.closed = False

    def read(self, amt=None):
        if self._pos >= len(self._payload):
            return b''
        if amt is None:
            chunk = self._payload[self._pos:]
        else:
            chunk = self._payload[self._pos:self._pos + amt]
        self._pos += len(chunk)
        return chunk

    def close(self):
        self.closed = True


class _FakeOSSBucket:
    """测试用 OSS Bucket 替身——不产生任何网络请求。

    用 copy_transform 模拟两种真实故障：
    - 返回 None：复制静默未生效（源对象一直都在，但目标对象没出现）
    - 返回被改写的字节：复制后内容与源不一致
    """

    def __init__(self):
        self.objects = {}
        self.copy_calls = []
        self.deleted_keys = []
        self.get_results = []
        self.copy_side_effect = None
        self.get_side_effect = None
        self.copy_transform = None

    def head_object(self, key):
        if key not in self.objects:
            raise oss2.exceptions.OssError(404, {}, b'Not Found', {})
        return _FakeHeadResult(len(self.objects[key]))

    def copy_object(self, source_bucket_name, source_key, target_key, headers=None):
        self.copy_calls.append((source_bucket_name, source_key, target_key, headers))
        if self.copy_side_effect is not None:
            raise self.copy_side_effect
        if source_key not in self.objects:
            raise oss2.exceptions.OssError(404, {}, b'Not Found', {})
        payload = self.objects[source_key]
        if self.copy_transform is not None:
            payload = self.copy_transform(payload)
            if payload is None:
                return
        self.objects[target_key] = payload

    def get_object(self, key, **kwargs):
        if self.get_side_effect is not None:
            raise self.get_side_effect
        if key not in self.objects:
            raise oss2.exceptions.OssError(404, {}, b'Not Found', {})
        result = _FakeGetResult(self.objects[key])
        self.get_results.append(result)
        return result

    def delete_object(self, key):
        self.deleted_keys.append(key)
        self.objects.pop(key, None)


class ContentModerationModelTests(TestCase):
    """ContentModeration 模型：1:1 关系、默认值、白名单判定。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username='moderator_admin', password='testpass123', role='super_admin',
        )
        self.doc = _make_doc(self.user)

    def test_one_to_one_defaults(self):
        moderation = ContentModeration.objects.create(doc=self.doc)

        self.assertEqual(moderation.state, 'pending')
        self.assertEqual(moderation.verdict, '')
        self.assertEqual(moderation.storage_state, 'quarantine')
        self.assertEqual(moderation.applied_seq, 0)
        self.assertEqual(moderation.attempt_count, 0)
        self.assertEqual(moderation.promotion_retry, 0)
        self.assertEqual(moderation.categories, [])
        self.assertEqual(moderation.findings, [])
        self.assertIsNone(moderation.checked_at)
        self.assertEqual(moderation.quarantine_key, '')
        self.assertEqual(moderation.promoted_key, '')

    def test_second_moderation_for_same_doc_is_rejected(self):
        ContentModeration.objects.create(doc=self.doc)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ContentModeration.objects.create(doc=self.doc)

    def test_reverse_accessor_returns_the_same_record(self):
        moderation = ContentModeration.objects.create(doc=self.doc)
        self.assertEqual(self.doc.moderation.pk, moderation.pk)

    def test_oss_key_semantics_are_not_implicitly_approved(self):
        """KnowledgeDoc.oss_key 存在 ≠ 已通过审核（P1-2A 核心安全不变量）。"""
        self.doc.oss_key = 'knowledge/college/1/2026-09/test.pdf'
        self.doc.status = 'indexed'
        self.doc.save(update_fields=['oss_key', 'status'])

        # 文档"看起来正常"，但根本不存在审核记录，绝不能推导出放行
        self.assertFalse(ContentModeration.objects.filter(doc=self.doc).exists())

    def test_confirmed_fields_default_to_unconfirmed(self):
        moderation = ContentModeration.objects.create(doc=self.doc)

        self.assertIsNone(moderation.confirmed_by)
        self.assertIsNone(moderation.confirmed_at)
        self.assertEqual(moderation.confirmation_reason, '')

    def test_is_approved_follows_the_whitelist_only(self):
        moderation = ContentModeration.objects.create(doc=self.doc)

        for state in ('pending', 'scanning', 'rejected', 'unavailable', 'error', ''):
            moderation.state = state
            self.assertFalse(moderation.is_approved, f'state={state!r} 不应放行')

        for state in ('approved', 'confirmed_unchecked'):
            moderation.state = state
            self.assertTrue(moderation.is_approved, f'state={state!r} 应放行')

    def test_is_promoted_follows_storage_state_only(self):
        moderation = ContentModeration.objects.create(doc=self.doc)

        self.assertFalse(moderation.is_promoted)
        for state in ('promoting', 'promotion_failed', 'deleted'):
            moderation.storage_state = state
            self.assertFalse(moderation.is_promoted, f'storage_state={state!r} 不算已提升')
        moderation.storage_state = 'promoted'
        self.assertTrue(moderation.is_promoted)

    def test_json_fields_round_trip(self):
        moderation = ContentModeration.objects.create(
            doc=self.doc,
            categories=['virus'],
            findings=[{'category': 'virus', 'reason': '检出测试样本', 'source': 'clamav'}],
        )
        moderation.refresh_from_db()

        self.assertEqual(moderation.categories, ['virus'])
        self.assertEqual(moderation.findings[0]['source'], 'clamav')


class ModerationAttemptModelTests(TestCase):
    """ModerationAttempt 模型：1:N 关系与 (moderation, attempt_seq) 唯一约束。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username='attempt_admin', password='testpass123', role='super_admin',
        )
        self.doc = _make_doc(self.user)
        self.moderation = ContentModeration.objects.create(doc=self.doc)

    def test_attempts_are_many_to_one(self):
        ModerationAttempt.objects.create(moderation=self.moderation, attempt_seq=1)
        ModerationAttempt.objects.create(moderation=self.moderation, attempt_seq=2)

        self.assertEqual(self.moderation.attempts.count(), 2)

    def test_attempt_seq_is_unique_within_one_moderation(self):
        ModerationAttempt.objects.create(moderation=self.moderation, attempt_seq=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ModerationAttempt.objects.create(moderation=self.moderation, attempt_seq=1)

    def test_same_attempt_seq_is_allowed_across_moderations(self):
        other_doc = _make_doc(self.user, title='另一份测试文档')
        other = ContentModeration.objects.create(doc=other_doc)

        ModerationAttempt.objects.create(moderation=self.moderation, attempt_seq=1)
        ModerationAttempt.objects.create(moderation=other, attempt_seq=1)

        self.assertEqual(ModerationAttempt.objects.filter(attempt_seq=1).count(), 2)

    def test_defaults_and_json_round_trip(self):
        attempt = ModerationAttempt.objects.create(
            moderation=self.moderation,
            attempt_seq=1,
            detector_results=[{'detector': 'rule', 'verdict': 'clean', 'confidence': 1.0}],
            policy_version='p1-2a-dev',
            worker_id='worker-1',
        )
        attempt.refresh_from_db()

        self.assertEqual(attempt.state_at_start, 'pending')
        self.assertEqual(attempt.verdict, '')
        self.assertEqual(attempt.error, '')
        self.assertEqual(attempt.detector_results[0]['detector'], 'rule')
        self.assertEqual(attempt.policy_version, 'p1-2a-dev')
        self.assertEqual(attempt.worker_id, 'worker-1')
        self.assertIsNotNone(attempt.created_at)

    def test_attempts_are_ordered_by_creation_desc(self):
        first = ModerationAttempt.objects.create(moderation=self.moderation, attempt_seq=1)
        second = ModerationAttempt.objects.create(moderation=self.moderation, attempt_seq=2)
        # auto_now_add 让同一事务内的两条记录时间可能完全相同，而并列时顺序未定义；
        # 这里显式错开时间，验证 ordering = ['-created_at'] 确实生效。
        ModerationAttempt.objects.filter(pk=first.pk).update(
            created_at=timezone.now() - timedelta(minutes=5),
        )

        self.assertEqual(list(self.moderation.attempts.all()), [second, first])


class ModerationSchemaMigrationTests(TestCase):
    """migration 只建 schema：不得为历史文档自动制造审核结论。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username='migration_admin', password='testpass123', role='super_admin',
        )

    def test_existing_docs_get_no_moderation_record(self):
        doc = _make_doc(self.user)

        self.assertFalse(ContentModeration.objects.filter(doc=doc).exists())
        self.assertEqual(ContentModeration.objects.count(), 0)

    def test_no_document_is_automatically_approved_or_clean(self):
        for index, status in enumerate(['uploaded', 'indexed', 'failed']):
            KnowledgeDoc.objects.create(
                title=f'历史文档{index}', source_type='upload', uploader=self.user, status=status,
            )

        self.assertFalse(
            ContentModeration.objects.filter(
                state__in=('approved', 'confirmed_unchecked'),
            ).exists()
        )
        self.assertFalse(ContentModeration.objects.filter(verdict='clean').exists())

    def test_new_docs_do_not_gain_moderation_records_either(self):
        """P1-2A 未改上传链路，因此新建文档同样不会自动产生审核记录。"""
        _make_doc(self.user)

        self.assertEqual(ContentModeration.objects.count(), 0)

    def test_deleting_doc_cascades_to_moderation_and_attempts(self):
        doc = _make_doc(self.user)
        moderation = ContentModeration.objects.create(doc=doc)
        ModerationAttempt.objects.create(moderation=moderation, attempt_seq=1)

        doc.delete()

        self.assertFalse(ContentModeration.objects.exists())
        self.assertFalse(ModerationAttempt.objects.exists())


class ModerationStateInvariantTests(TestCase):
    """状态常量与白名单不变量。"""

    def test_indexable_states_is_an_exact_whitelist(self):
        self.assertEqual(
            INDEXABLE_MODERATION_STATES, frozenset({'approved', 'confirmed_unchecked'}),
        )

    def test_unknown_and_empty_states_are_not_indexable(self):
        for state in (None, '', 'PENDING', 'Approved', 'pending', 'scanning',
                      'rejected', 'unavailable', 'error'):
            self.assertFalse(is_indexable_moderation_state(state), f'state={state!r}')

    def test_state_choices_are_exactly_the_agreed_set(self):
        self.assertEqual(
            [choice[0] for choice in MODERATION_STATE_CHOICES],
            ['pending', 'scanning', 'approved', 'rejected',
             'unavailable', 'confirmed_unchecked', 'error'],
        )

    def test_verdict_choices_are_exactly_the_agreed_set(self):
        self.assertEqual(
            [choice[0] for choice in VERDICT_CHOICES],
            ['clean', 'virus', 'sensitive', 'harmful',
             'negative_guidance', 'mixed', 'unavailable', 'error'],
        )

    def test_storage_state_choices_are_exactly_the_agreed_set(self):
        self.assertEqual(
            [choice[0] for choice in STORAGE_STATE_CHOICES],
            ['quarantine', 'promoting', 'promoted', 'promotion_failed', 'deleted'],
        )

    def test_models_share_the_single_source_of_truth(self):
        self.assertIs(ContentModeration.STATE_CHOICES, MODERATION_STATE_CHOICES)
        self.assertIs(ContentModeration.VERDICT_CHOICES, VERDICT_CHOICES)
        self.assertIs(ContentModeration.STORAGE_STATE_CHOICES, STORAGE_STATE_CHOICES)
        self.assertIs(ModerationAttempt.VERDICT_CHOICES, VERDICT_CHOICES)

    def test_official_prefix_matches_the_index_gate_prefix(self):
        """knowledge/ 这个命名空间必须与索引门的前缀常量保持一致。"""
        self.assertEqual(OFFICIAL_KEY_PREFIX, INDEXABLE_OSS_KEY_PREFIX)

    def test_quarantine_is_parallel_not_nested(self):
        self.assertEqual(QUARANTINE_KEY_PREFIX, 'quarantine/')
        self.assertFalse(QUARANTINE_KEY_PREFIX.startswith(OFFICIAL_KEY_PREFIX))


class QuarantineKeyTests(TestCase):
    """隔离区 Key 形态：与 knowledge/ 平级，且结构与正式 Key 一致。"""

    def test_key_uses_the_quarantine_namespace(self):
        key = build_quarantine_key('college', 12, '课件.pdf')

        self.assertTrue(key.startswith('quarantine/'))
        self.assertFalse(key.startswith('knowledge/'))
        self.assertFalse(key.startswith('knowledge/quarantine/'))

    def test_key_layout_is_scope_owner_month_uuid_ext(self):
        key = build_quarantine_key('college', 12, '课件.pdf')

        parts = key.split('/')
        self.assertEqual(parts[0], 'quarantine')
        self.assertEqual(parts[1], 'college')
        self.assertEqual(parts[2], '12')
        self.assertRegex(parts[3], r'^\d{4}-\d{2}$')
        self.assertRegex(parts[4], r'^[0-9a-f]{12}\.pdf$')

    def test_key_preserves_normalized_extension(self):
        self.assertTrue(build_quarantine_key('school', 0, 'A.PDF').endswith('.pdf'))
        self.assertTrue(build_quarantine_key('school', 0, 'noext').endswith('.bin'))

    def test_quarantine_keys_are_unique(self):
        keys = {build_quarantine_key('school', 0, 'a.txt') for _ in range(50)}

        self.assertEqual(len(keys), 50)

    def test_school_scope_uses_owner_zero(self):
        key = build_quarantine_key('school', 0, 'a.txt')

        self.assertEqual(key.split('/')[2], '0')


class FindingsConstraintTests(TestCase):
    """findings 结构约束：条数、片段长度、总字节数。"""

    def test_none_returns_empty_list(self):
        self.assertEqual(normalize_findings(None), [])
        self.assertEqual(normalize_findings([]), [])

    def test_non_dict_entries_are_dropped(self):
        result = normalize_findings(['not-a-dict', 42, None, {'reason': 'ok'}])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['reason'], 'ok')

    def test_findings_count_is_capped(self):
        raw = [{'reason': f'命中{index}'} for index in range(MAX_FINDINGS + 20)]

        self.assertEqual(len(normalize_findings(raw)), MAX_FINDINGS)

    def test_long_excerpt_is_truncated(self):
        result = normalize_findings([{'excerpt': 'x' * 500}])

        self.assertEqual(len(result[0]['excerpt']), MAX_EXCERPT_LENGTH + 1)   # 含省略号
        self.assertTrue(result[0]['excerpt'].endswith('…'))

    def test_short_excerpt_is_left_untouched(self):
        result = normalize_findings([{'excerpt': '短片段'}])

        self.assertEqual(result[0]['excerpt'], '短片段')

    def test_total_bytes_stay_bounded(self):
        raw = [{'reason': 'y' * 5000} for _ in range(MAX_FINDINGS)]

        result = normalize_findings(raw)

        self.assertLessEqual(
            len(json.dumps(result, ensure_ascii=False).encode('utf-8')),
            MAX_FINDINGS_JSON_BYTES,
        )

    def test_input_is_not_mutated(self):
        raw = [{'excerpt': 'x' * 500}]
        normalize_findings(raw)

        self.assertEqual(len(raw[0]['excerpt']), 500)


class OSSPrimitiveBaseTests(TestCase):
    """OSS 原语测试基类：把 oss_client 的 bucket 换成内存替身。"""

    def setUp(self):
        self.bucket = _FakeOSSBucket()
        patcher = patch.object(oss_client, '_bucket', self.bucket)
        patcher.start()
        self.addCleanup(patcher.stop)


class OSSCopyObjectTests(OSSPrimitiveBaseTests):
    """copy_object：服务端复制 + 复制后 HEAD 校验。"""

    def test_copy_returns_destination_metadata(self):
        self.bucket.objects['quarantine/college/1/a.pdf'] = b'payload'

        result = oss_client.copy_object(
            'quarantine/college/1/a.pdf', 'knowledge/college/1/a.pdf',
        )

        self.assertEqual(result['dst_key'], 'knowledge/college/1/a.pdf')
        self.assertEqual(result['size'], len(b'payload'))
        self.assertEqual(self.bucket.objects['knowledge/college/1/a.pdf'], b'payload')

    def test_copy_uses_server_side_copy_on_the_configured_bucket(self):
        self.bucket.objects['quarantine/a.bin'] = b'x'

        oss_client.copy_object('quarantine/a.bin', 'knowledge/a.bin')

        bucket_name, src, dst, _ = self.bucket.copy_calls[-1]
        self.assertEqual(bucket_name, oss_client.bucket_name)
        self.assertEqual(src, 'quarantine/a.bin')
        self.assertEqual(dst, 'knowledge/a.bin')

    def test_content_type_sets_replace_directive(self):
        self.bucket.objects['quarantine/a.bin'] = b'x'

        oss_client.copy_object('quarantine/a.bin', 'knowledge/a.pdf',
                               content_type='application/pdf')

        headers = self.bucket.copy_calls[-1][3]
        self.assertEqual(headers['Content-Type'], 'application/pdf')
        self.assertEqual(headers['x-oss-metadata-directive'], 'REPLACE')

    def test_no_content_type_sends_no_headers(self):
        self.bucket.objects['quarantine/a.bin'] = b'x'

        oss_client.copy_object('quarantine/a.bin', 'knowledge/a.bin')

        self.assertIsNone(self.bucket.copy_calls[-1][3])

    def test_copy_raises_when_oss_call_fails(self):
        self.bucket.objects['quarantine/a.bin'] = b'x'
        self.bucket.copy_side_effect = oss2.exceptions.OssError(500, {}, b'boom', {})

        with self.assertRaises(OSSOperationError):
            oss_client.copy_object('quarantine/a.bin', 'knowledge/a.bin')

    def test_copy_raises_when_destination_missing_after_copy(self):
        """复制调用没报错、但目标对象没出现——必须报错，不能当作成功。"""
        self.bucket.objects['quarantine/a.bin'] = b'x'
        self.bucket.copy_transform = lambda payload: None

        with self.assertRaises(OSSOperationError):
            oss_client.copy_object('quarantine/a.bin', 'knowledge/a.bin')

    def test_copy_rejects_traversal_keys(self):
        self.bucket.objects['quarantine/a.bin'] = b'x'

        for src, dst in (('quarantine/../etc/passwd', 'knowledge/x'),
                         ('quarantine/a.bin', '/knowledge/x'),
                         ('', 'knowledge/x')):
            with self.assertRaises(OSSOperationError):
                oss_client.copy_object(src, dst)

        self.assertEqual(self.bucket.copy_calls, [])


class OSSStreamReadTests(OSSPrimitiveBaseTests):
    """iter_object_chunks：流式读取，不整文件进内存，且有硬性字节上限。"""

    def test_streams_in_chunks(self):
        self.bucket.objects['quarantine/big.bin'] = b'abcdefghij'

        chunks = list(oss_client.iter_object_chunks('quarantine/big.bin', chunk_size=4))

        self.assertEqual(chunks, [b'abcd', b'efgh', b'ij'])
        self.assertEqual(b''.join(chunks), b'abcdefghij')

    def test_raises_when_exceeding_max_bytes(self):
        self.bucket.objects['quarantine/big.bin'] = b'x' * 100

        with self.assertRaises(OSSOperationError):
            list(oss_client.iter_object_chunks('quarantine/big.bin', chunk_size=10, max_bytes=25))

    def test_reads_object_exactly_at_max_bytes(self):
        self.bucket.objects['quarantine/big.bin'] = b'x' * 30

        chunks = list(oss_client.iter_object_chunks('quarantine/big.bin', chunk_size=10, max_bytes=30))

        self.assertEqual(sum(len(chunk) for chunk in chunks), 30)

    def test_rejects_non_positive_max_bytes(self):
        for max_bytes in (0, -1, None):
            with self.assertRaises(OSSOperationError):
                list(oss_client.iter_object_chunks('quarantine/a.bin', max_bytes=max_bytes))

        self.assertEqual(self.bucket.get_results, [])

    def test_rejects_non_positive_chunk_size(self):
        for chunk_size in (0, -5):
            with self.assertRaises(OSSOperationError):
                list(oss_client.iter_object_chunks('quarantine/a.bin', chunk_size=chunk_size))

    def test_raises_when_object_cannot_be_opened(self):
        self.bucket.get_side_effect = oss2.exceptions.OssError(403, {}, b'denied', {})

        with self.assertRaises(OSSOperationError):
            list(oss_client.iter_object_chunks('quarantine/a.bin'))

    def test_closes_the_read_stream(self):
        self.bucket.objects['quarantine/a.bin'] = b'abc'

        list(oss_client.iter_object_chunks('quarantine/a.bin'))

        self.assertTrue(self.bucket.get_results[0].closed)


class OSSPromotionTests(OSSPrimitiveBaseTests):
    """promote_object：quarantine → knowledge，带前缀限制与大小校验，且不删源。"""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username='promote_admin', password='testpass123', role='super_admin',
        )
        self.src = 'quarantine/college/1/2026-09/abc123.pdf'
        self.dst = 'knowledge/college/1/2026-09/abc123.pdf'
        self.bucket.objects[self.src] = b'quarantine-payload'

    def test_promote_success_returns_verified_metadata(self):
        result = oss_client.promote_object(self.src, self.dst)

        self.assertTrue(result['verified'])
        self.assertEqual(result['size'], result['src_size'])
        self.assertEqual(result['dst_key'], self.dst)
        self.assertEqual(self.bucket.objects[self.dst], b'quarantine-payload')

    def test_promote_rejects_source_outside_quarantine(self):
        self.bucket.objects['knowledge/x.pdf'] = b'x'

        for src in ('knowledge/x.pdf', 'other/x.pdf', 'quarantine', '', None):
            with self.assertRaises(OSSOperationError):
                oss_client.promote_object(src, self.dst)

    def test_promote_rejects_destination_outside_official_prefix(self):
        for dst in ('quarantine/x.pdf', 'other/x.pdf', 'knowledge', 'knowledgex/a.pdf', '', None):
            with self.assertRaises(OSSOperationError):
                oss_client.promote_object(self.src, dst)

    def test_promote_raises_on_size_mismatch(self):
        self.bucket.copy_transform = lambda payload: payload[:3]

        with self.assertRaises(OSSOperationError):
            oss_client.promote_object(self.src, self.dst)

    def test_promote_raises_when_source_is_missing(self):
        with self.assertRaises(OSSOperationError):
            oss_client.promote_object('quarantine/not-there.pdf', self.dst)

    def test_promote_raises_when_copy_silently_fails(self):
        self.bucket.copy_transform = lambda payload: None

        with self.assertRaises(OSSOperationError):
            oss_client.promote_object(self.src, self.dst)

    def test_promote_never_deletes_the_source(self):
        oss_client.promote_object(self.src, self.dst)

        self.assertIn(self.src, self.bucket.objects)
        self.assertEqual(self.bucket.deleted_keys, [])

    def test_promote_does_not_touch_the_database(self):
        """promotion 是纯 OSS 原语：不建文档、不改审核状态、不触发索引。"""
        _make_doc(self.user)
        docs_before = KnowledgeDoc.objects.count()

        oss_client.promote_object(self.src, self.dst)

        self.assertEqual(ContentModeration.objects.count(), 0)
        self.assertEqual(ModerationAttempt.objects.count(), 0)
        self.assertEqual(KnowledgeDoc.objects.count(), docs_before)
