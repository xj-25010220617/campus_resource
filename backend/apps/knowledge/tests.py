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
    is_official_oss_key,
    oss_client,
)

from .models import (
    ChunkedUploadSession,
    ContentModeration,
    DocChunk,
    Keyword,
    KnowledgeDoc,
    ModerationAttempt,
    UploadIntent,
)
from .upload_intent import (
    cancel_upload_intent,
    create_upload_intent,
    is_quarantine_key,
)
from .moderation import (
    INDEXABLE_MODERATION_STATES,
    INDEXABLE_OSS_KEY_PREFIX,
    MAX_EXCERPT_LENGTH,
    MAX_FINDINGS,
    MAX_FINDINGS_JSON_BYTES,
    MODERATION_STATE_APPROVED,
    MODERATION_STATE_CHOICES,
    STORAGE_STATE_CHOICES,
    STORAGE_STATE_PROMOTED,
    VERDICT_CHOICES,
    VERDICT_CLEAN,
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
        doc = KnowledgeDoc.objects.create(**defaults)
        # P1-2 Phase 1-2 / 审计报告 P-3：RAG 读取侧已加审核门，
        # `status='indexed'` 不再是"可被检索"的充分条件——还必须
        # `state ∈ {approved, confirmed_unchecked}` 且 `storage_state=promoted`。
        # 因此"已索引文档"的夹具必须同时给出审核记录，否则它代表的是一个
        # STEP 4-B 之后**不可能存在**的状态（未审核却已索引）。
        # 这是夹具修正，不是放宽安全判定：审核门本身未做任何改动。
        if doc.status == 'indexed':
            ContentModeration.objects.create(
                doc=doc,
                state=MODERATION_STATE_APPROVED,
                verdict=VERDICT_CLEAN,
                storage_state=STORAGE_STATE_PROMOTED,
                promoted_key=doc.oss_key or '',
            )
        return doc

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
FILE_UPLOAD_URL = '/api/knowledge/upload/file/'
BATCH_UPLOAD_URL = '/api/knowledge/upload/batch/'
TEXT_UPLOAD_URL = '/api/knowledge/upload/text/'
STS_URL = '/api/knowledge/upload/sts/'

MB = 1024 * 1024


def _oss_meta(size, content_type='application/pdf', etag='', exists=True):
    """构造 OSSClient.head_object 的返回值（与 utils/oss.py 约定一致）。"""
    if not exists:
        return {'exists': False, 'size': 0, 'content_type': '', 'etag': ''}
    return {'exists': True, 'size': size, 'content_type': content_type, 'etag': etag}


class UploadCallbackSecurityTests(TestCase):
    """上传回调：oss_key 必须**精确绑定**服务端签发的 UploadIntent。

    P1-2D STEP 4-A 之前，回调只校验"key 落在我的 scope 目录下"，同一目录下
    别人上传的对象因此可以被认领；现在服务端在 /upload/sts/ 阶段生成
    quarantine key 并记在 intent 上，回调用 intent_id 取回该值做**精确比对**。
    """

    def setUp(self):
        self.client = APIClient()
        self.college = College.objects.create(name='计算机与软件学院', code='CS')
        self.admin = User.objects.create_user(
            username='cb_admin', password='testpass123',
            role='college_admin', college=self.college,
        )
        self.client.force_authenticate(self.admin)
        self.intent = create_upload_intent(
            self.admin, '课程设计报告.pdf',
            content_type='application/pdf', expected_size=2048,
        )
        self.oss_key = self.intent.quarantine_key

        # P1-2D STEP 4-B：回调建文档后必须派发统一审核编排。
        # 派发发生在 on_commit 里，而 TestCase 全程包在事务中、回调默认不执行，
        # 因此 `_post()` 用 captureOnCommitCallbacks(execute=True) 包住请求。
        #
        # 派发函数是 `dispatch_async`（只入队、绝不降级同步执行，见 P-4）。
        # 同时 spy `safe_delay` 并断言未被调用，防止有人改回降级派发。
        patcher = patch('utils.celery_dispatcher.dispatch_async')
        self.dispatch_async = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch('utils.celery_dispatcher.safe_delay')
        self.safe_delay = patcher.start()
        self.addCleanup(patcher.stop)
        self.commit_callbacks = []

    def _payload(self, **overrides):
        data = {
            'intent_id': self.intent.pk,
            'oss_key': self.oss_key,
            'file_name': '课程设计报告.pdf',
            'file_size': 2048,
            'file_type': 'pdf',
            'md5': 'a' * 32,
        }
        data.update(overrides)
        return data

    def _post(self, data, meta):
        """回调：head_object 全 mock，测试不得触达真实 OSS。

        P1-2D STEP 4-B 起回调建文档后会派发统一审核编排（on_commit），
        因此必须把请求包进 `captureOnCommitCallbacks(execute=True)`。
        """
        # 每次请求前清空记录：断言只针对"这一次请求"派发了什么，
        # 不受同一测试中先前请求的影响（如重放测试里的第一次回调）。
        self.dispatch_async.reset_mock()
        self.safe_delay.reset_mock()
        with patch('apps.knowledge.views.oss_client.head_object', return_value=meta):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                response = self.client.post(CALLBACK_URL, data, format='json')
        self.commit_callbacks = callbacks
        return response

    def assertModerationScheduled(self):
        """新建文档必须且只能派发一次统一审核编排任务。"""
        from .tasks import index_document_task, moderate_and_promote_task

        orchestration = [
            call.args[1] for call in self.dispatch_async.call_args_list
            if call.args and call.args[0] is moderate_and_promote_task
        ]
        self.assertEqual(
            orchestration, [KnowledgeDoc.objects.get().id],
            f'应恰好派发一次审核编排，实际 {orchestration}',
        )
        self.assertNotIn(
            index_document_task,
            [call.args[0] for call in self.dispatch_async.call_args_list if call.args],
            '回调不得直接派发索引任务',
        )
        self.safe_delay.assert_not_called()

    def assertModerationNotScheduled(self):
        """未新建文档的返回路径（拒绝 / 去重命中）不得派发任何任务。"""
        self.dispatch_async.assert_not_called()
        self.assertEqual(self.commit_callbacks, [])

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
    # Key 绑定：必须与 intent 上记录的 quarantine key 精确相等
    # ------------------------------------------------------------------
    def _peer(self, username='cb_peer'):
        return User.objects.create_user(
            username=username, password='testpass123',
            role='college_admin', college=self.college,
        )

    def test_rejects_key_of_another_intent(self):
        """不得认领他人 intent 的 quarantine key（这正是旧 scope 校验漏掉的缺口）。"""
        other_intent = create_upload_intent(self._peer(), '别人的报告.pdf')

        response = self._post(
            self._payload(oss_key=other_intent.quarantine_key), _oss_meta(2048),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('oss_key', response.data)
        self.assertEqual(KnowledgeDoc.objects.count(), 0)

    def test_rejects_key_in_official_namespace(self):
        """正式区（knowledge/）Key 一律拒绝：直传只允许落到隔离区。"""
        response = self._post(
            self._payload(oss_key=f'knowledge/college/{self.college.id}/2026-09/old.pdf'),
            _oss_meta(2048),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(KnowledgeDoc.objects.count(), 0)

    def test_rejects_relative_path_key(self):
        """路径穿越仍然被拦。"""
        response = self._post(
            self._payload(oss_key='quarantine/college/1/../../etc/passwd'),
            _oss_meta(2048),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(KnowledgeDoc.objects.count(), 0)

    # ------------------------------------------------------------------
    # 凭证归属 / 有效期
    # ------------------------------------------------------------------
    def test_rejects_missing_intent_id(self):
        """没有 intent_id 就没有归属依据，序列化层直接拒绝。"""
        payload = self._payload()
        payload.pop('intent_id')

        response = self._post(payload, _oss_meta(2048))

        self.assertEqual(response.status_code, 400)
        self.assertIn('intent_id', response.data)
        self.assertEqual(KnowledgeDoc.objects.count(), 0)

    def test_rejects_foreign_intent(self):
        """不得凭他人的 intent_id 完成回调。

        对外与"凭证不存在"返回同样的 404，避免枚举他人 intent id。
        """
        other_intent = create_upload_intent(self._peer(), '别人的报告.pdf')

        response = self._post(
            self._payload(
                intent_id=other_intent.pk,
                oss_key=other_intent.quarantine_key,
            ),
            _oss_meta(2048),
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(KnowledgeDoc.objects.count(), 0)

    def test_rejects_expired_intent(self):
        """Key 完全正确但凭证已过期时同样拒绝。"""
        UploadIntent.objects.filter(pk=self.intent.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        response = self._post(self._payload(), _oss_meta(2048))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(KnowledgeDoc.objects.count(), 0)

    def test_rejects_cancelled_intent(self):
        cancel_upload_intent(self.admin, self.intent.pk)

        response = self._post(self._payload(), _oss_meta(2048))

        self.assertEqual(response.status_code, 409)
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
        self.assertModerationScheduled()

    def test_created_document_points_at_quarantine_key(self):
        """新建记录指向隔离区对象，不指向正式区。"""
        response = self._post(self._payload(), _oss_meta(2048))

        doc = KnowledgeDoc.objects.get(id=response.data['id'])
        self.assertTrue(is_quarantine_key(doc.oss_key))
        self.assertFalse(is_official_oss_key(doc.oss_key))

    def test_callback_completes_the_intent(self):
        """回调成功后 intent 推进到 completed（对象已确认落在隔离区）。"""
        self._post(self._payload(), _oss_meta(2048))

        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, 'completed')
        self.assertEqual(self.intent.actual_size, 2048)

    def test_replay_returns_existing_document_without_duplicating(self):
        """同一凭证重复回调（重放）只返回首次的文档，不产生第二条记录。

        防重放依赖 UploadIntent 的状态机（issued→completed 的 CAS），
        不需要另设一套并发控制。
        """
        first = self._post(self._payload(), _oss_meta(2048))
        second = self._post(self._payload(), _oss_meta(2048))

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.data.get('duplicate'))
        self.assertEqual(second.data['id'], first.data['id'])
        self.assertEqual(KnowledgeDoc.objects.count(), 1)
        # 重放不得重复派发审核编排（去重命中走的是"返回既有文档"分支）。
        self.assertModerationNotScheduled()

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
        self.assertModerationNotScheduled()

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
        self.assertModerationNotScheduled()

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


class _RecordingBucket:
    """最小 OSS Bucket 替身：只记录 put_object，不产生任何网络请求。"""

    def __init__(self):
        self.puts = []

    def put_object(self, key, payload, headers=None):
        self.puts.append({'key': key, 'size': len(payload), 'headers': headers or {}})
        return SimpleNamespace(etag='e' * 32)

    def keys(self):
        return [p['key'] for p in self.puts]


class _FailingBucket:
    """写入必定失败的 Bucket 替身（模拟 OSS 服务端错误）。"""

    def put_object(self, key, payload, headers=None):
        raise OSSOperationError('OSS 写入失败（测试注入）')


class _QuarantineEntryPointBase(TestCase):
    """5 个生产上传入口的公共夹具。

    P1-2D STEP 4-A 的硬性验收：入口写入的对象必须在 `quarantine/`，
    **不得**出现 `knowledge/`、不得创建审核记录、不得派发索引任务。

    P1-2D STEP 4-B 追加验收：入口**必须**把每个新建文档交给统一审核编排
    （`moderate_and_promote_task`），但审核 / 提升 / 索引全部异步发生——
    请求返回时对象仍在隔离区、仍无审核记录、仍不是 `indexed`。

    关于"入口是否派发了审核"的断言：`TestCase` 全程包在一个事务里，
    `transaction.on_commit()` 的回调**默认永不执行**。若不加处理直接断言
    派发函数的调用，会得到一个恒为真的空断言。因此所有入口请求都要
    经 `_with_commit()` 包一层 `captureOnCommitCallbacks(execute=True)`。

    派发函数是 `celery_dispatcher.dispatch_async` 而**不是** `safe_delay`
    （P1-2 Phase 1-2 / 审计报告 P-4）：`safe_delay` 在 Celery 不可用时会
    降级为**同步执行**，而上传链路必须只入队、绝不降级。本夹具据此 spy。
    """

    def setUp(self):
        self.college = College.objects.create(name='计算机与软件学院', code='CS')
        self.user = User.objects.create_user(
            username='entry_admin', password='testpass123',
            role='college_admin', college=self.college,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        # 记录 OSS 实际被要求写入的 Key（upload_service 与 views 共用同一个
        # oss_client 实例，因此 patch 一次即可覆盖两者）。
        # `bucket` 是只读 property（内部走 _bucket 缓存），必须 patch 私有字段。
        self.bucket = _RecordingBucket()
        bucket_patcher = patch.object(oss_client, '_bucket', self.bucket)
        bucket_patcher.start()
        self.addCleanup(bucket_patcher.stop)

        # AI 分类：入口会调用，测试中直接降级为"AI 不可用"
        ai_patcher = patch('apps.knowledge.views._ai_classify', return_value=None)
        ai_patcher.start()
        self.addCleanup(ai_patcher.stop)

        # 编辑编排任务走 spy：真实派发需要 broker，且"派发到了哪个任务"才是
        # 本组测试的关注点。审核函数本体同样只 spy——入口进程绝不直接调用它，
        # 审核必须由 `moderate_and_promote_task` 在异步进程里执行。
        #
        # 这里 spy `dispatch_async`（入口实际使用的派发函数）而同时 spy
        # `safe_delay` 并断言"未被调用"：如果哪天有人把入口改回 safe_delay，
        # 异步性会被静默破坏，这两条断言能立刻发现。
        self.dispatch_async = self._spy('utils.celery_dispatcher.dispatch_async')
        self.safe_delay = self._spy('utils.celery_dispatcher.safe_delay')
        self.moderate_document = self._spy('apps.knowledge.moderation.moderate_document')
        # 最近一次 `_with_commit()` 捕获到的 on_commit 回调
        self.commit_callbacks = []

    def _spy(self, target):
        patcher = patch(target)
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def _with_commit(self, fn):
        """在请求外层捕获并执行 on_commit 回调（TestCase 不会真正提交事务）。"""
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            result = fn()
        self.commit_callbacks = callbacks
        return result

    def assertQuarantineOnly(self):
        """公共断言：请求返回时所有 OSS 对象与文档记录都还在隔离区。

        这是"审核尚未发生"的证明——入口若把对象直接写进 `knowledge/`，
        或把文档标成 `indexed`，或就地写出审核结论，本断言立刻失败。
        """
        self.assertTrue(self.bucket.puts, '上传入口没有向 OSS 写入任何对象')
        for entry in self.bucket.puts:
            self.assertTrue(
                is_quarantine_key(entry['key']),
                f'对象落在了非隔离区路径: {entry["key"]}',
            )
        for doc in KnowledgeDoc.objects.all():
            self.assertTrue(
                is_quarantine_key(doc.oss_key),
                f'文档记录指向非隔离区对象: {doc.oss_key}',
            )
            self.assertNotEqual(doc.status, 'indexed')
        self.assertEqual(ContentModeration.objects.count(), 0)
        self.assertEqual(ModerationAttempt.objects.count(), 0)
        self.moderate_document.assert_not_called()

    def assertModerationScheduled(self, expected=1):
        """入口必须在事务提交后，把每个文档交给统一审核编排任务。

        `expected` 为期望派发次数（批量上传按文件数计）。
        """
        from .tasks import index_document_task, moderate_and_promote_task

        orchestration = [
            call.args[1] for call in self.dispatch_async.call_args_list
            if call.args and call.args[0] is moderate_and_promote_task
        ]
        self.assertEqual(
            len(orchestration), expected,
            f'审核编排派发次数应为 {expected}，实际 {orchestration}',
        )
        self.assertEqual(
            sorted(orchestration),
            sorted(KnowledgeDoc.objects.values_list('id', flat=True)),
            '派发的 doc_id 与本次新建的文档不一致',
        )
        # 入口**不得**直接派发索引任务：索引只能由 promotion 成功后的
        # on_commit 派发，否则等于绕过了"对象必须已在正式区"这一前置条件。
        self.assertNotIn(
            index_document_task,
            [call.args[0] for call in self.dispatch_async.call_args_list if call.args],
            '上传入口不得直接派发索引任务',
        )
        # 上传链路必须只入队、绝不降级同步执行（P-4）。safe_delay 一旦被
        # 入口调用，就有"Celery 不可用时就地跑完整条审核链"的风险。
        self.safe_delay.assert_not_called()
        # 入口进程不执行审核，审核发生在异步任务里。
        self.moderate_document.assert_not_called()

    def assertModerationNotScheduled(self):
        """失败请求的结果：没有留下任何待执行的审核回调。"""
        self.dispatch_async.assert_not_called()
        self.assertEqual(
            self.commit_callbacks, [],
            '请求失败回滚后不得留下待执行的审核回调',
        )

    def assertIntentCompleted(self, expected=1):
        intents = list(UploadIntent.objects.all())
        self.assertEqual(len(intents), expected)
        for intent in intents:
            self.assertEqual(intent.status, 'completed')
            self.assertTrue(is_quarantine_key(intent.quarantine_key))


class FileUploadEntryPointTests(_QuarantineEntryPointBase):
    """§十七-A：单文件上传（POST /api/knowledge/upload/file/）。"""

    def _post(self, file_name='课程设计报告.pdf', content=b'%PDF-1.4 hello'):
        return self._with_commit(lambda: self.client.post(FILE_UPLOAD_URL, {
            'file': SimpleUploadedFile(file_name, content, content_type='application/pdf'),
        }, format='multipart'))

    def test_file_upload_lands_in_quarantine(self):
        response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertIntentCompleted()
        self.assertQuarantineOnly()
        self.assertModerationScheduled()

    def test_file_upload_document_points_at_quarantine_key(self):
        response = self._post()

        doc = KnowledgeDoc.objects.get(id=response.data['id'])
        self.assertTrue(is_quarantine_key(doc.oss_key))
        self.assertEqual(doc.oss_key, self.bucket.keys()[0])
        self.assertEqual(doc.status, 'uploaded')

    def test_file_upload_never_writes_to_official_namespace(self):
        self._post()

        for key in self.bucket.keys():
            self.assertFalse(is_official_oss_key(key), f'写入正式区: {key}')

    def test_file_upload_failure_leaves_intent_issued(self):
        """OSS 写入失败时不得建文档记录，意图停在 issued 等待过期回收。"""
        with patch.object(oss_client, '_bucket', _FailingBucket()):
            response = self._post()

        self.assertEqual(response.status_code, 500)
        self.assertEqual(KnowledgeDoc.objects.count(), 0)
        self.assertEqual(UploadIntent.objects.get().status, 'issued')
        # 没有文档就没有任何可编排的东西：不得派发审核，也不得留下悬空回调。
        self.assertModerationNotScheduled()


class TextUploadEntryPointTests(_QuarantineEntryPointBase):
    """§十七-C：文本录入上传（POST /api/knowledge/upload/text/）。"""

    def _post(self, content='课程设计报告正文内容', **extra):
        data = {'title': '文本资料', 'content': content}
        data.update(extra)
        return self._with_commit(lambda: self.client.post(TEXT_UPLOAD_URL, data, format='json'))

    def test_text_upload_lands_in_quarantine(self):
        response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertIntentCompleted()
        self.assertQuarantineOnly()
        self.assertModerationScheduled()

    def test_text_object_key_uses_txt_extension(self):
        self._post()

        self.assertTrue(self.bucket.keys()[0].endswith('.txt'))

    def test_text_upload_content_is_written(self):
        body = '正文内容-' * 20
        self._post(content=body)

        self.assertEqual(self.bucket.puts[0]['size'], len(body.encode('utf-8')))


class BatchUploadEntryPointTests(_QuarantineEntryPointBase):
    """§十七-B：批量上传 —— 每个文件一个独立 UploadIntent。"""

    def _post(self, names=('a.pdf', 'b.pdf')):
        files = [
            SimpleUploadedFile(n, b'%PDF-1.4 ' + n.encode(), content_type='application/pdf')
            for n in names
        ]
        return self._with_commit(
            lambda: self.client.post(BATCH_UPLOAD_URL, {'files': files}, format='multipart')
        )

    def test_each_file_gets_its_own_intent(self):
        response = self._post(names=('a.pdf', 'b.pdf', 'c.pdf'))

        self.assertEqual(response.status_code, 200)
        self.assertIntentCompleted(expected=3)
        self.assertEqual(KnowledgeDoc.objects.count(), 3)
        self.assertQuarantineOnly()
        # 每个文件一条独立编排任务：某个文件被拒不影响同批其它文件。
        self.assertModerationScheduled(expected=3)

    def test_batch_keys_are_distinct(self):
        self._post(names=('a.pdf', 'b.pdf'))

        keys = self.bucket.keys()
        self.assertEqual(len(keys), len(set(keys)))

    def test_batch_never_writes_to_official_namespace(self):
        self._post()

        for key in self.bucket.keys():
            self.assertFalse(is_official_oss_key(key), f'写入正式区: {key}')


class STSRequestEntryPointTests(_QuarantineEntryPointBase):
    """§十七-D：STS 直传凭证签发（POST /api/knowledge/upload/sts/）。"""

    def setUp(self):
        super().setUp()
        self.credentials = {
            'oss_key': '', 'upload_url': 'https://example.invalid/upload',
            'sts_credentials': {'AccessKeyId': 'fake'}, 'dev_mode': True,
        }

        def _fake_get_sts_credentials(file_name, scope='college', owner_id=0, oss_key=None):
            self.credentials['oss_key'] = oss_key
            return dict(self.credentials)

        patcher = patch.object(oss_client, 'get_sts_credentials', _fake_get_sts_credentials)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _post(self, **overrides):
        data = {
            'file_name': '课程设计报告.pdf', 'file_size': 2048, 'md5': 'a' * 32,
        }
        data.update(overrides)
        return self._with_commit(lambda: self.client.post(STS_URL, data, format='json'))

    def test_sts_returns_intent_and_quarantine_key(self):
        response = self._post()

        self.assertEqual(response.status_code, 200)
        intent = UploadIntent.objects.get()
        self.assertEqual(response.data['intent_id'], intent.pk)
        self.assertEqual(response.data['oss_key'], intent.quarantine_key)
        self.assertTrue(is_quarantine_key(response.data['oss_key']))

    def test_sts_signs_credentials_for_the_intent_key_only(self):
        """签发的凭证必须绑定 intent 的隔离区 Key，而不是 knowledge/ 路径。"""
        self._post()

        intent = UploadIntent.objects.get()
        self.assertEqual(self.credentials['oss_key'], intent.quarantine_key)
        self.assertFalse(is_official_oss_key(self.credentials['oss_key']))

    def test_sts_does_not_write_anything(self):
        """签发凭证阶段不产生 OSS 写入，也不建文档记录。"""
        self._post()

        self.assertEqual(self.bucket.puts, [])
        self.assertEqual(KnowledgeDoc.objects.count(), 0)

    def test_sts_issues_no_moderation_or_index(self):
        """STS 签发凭证只是"允许上传"，对象还不存在，因此无可编排之物。

        P1-2D STEP 4-B 起其余 4 个入口都会派发 `moderate_and_promote_task`；
        STS 入口是唯一例外——它连 KnowledgeDoc 都还没建。
        """
        self._post()

        self.assertEqual(ContentModeration.objects.count(), 0)
        self.assertModerationNotScheduled()

    def test_sts_intent_stays_issued_until_callback(self):
        """凭证签发后意图是 issued —— 必须等回调确认对象存在才推进。"""
        self._post()

        self.assertEqual(UploadIntent.objects.get().status, 'issued')


class QuarantineAccessGateTests(TestCase):
    """§十六：隔离区对象不得通过下载 / 预览 / 数据查询泄露。"""

    def setUp(self):
        self.college = College.objects.create(name='计算机与软件学院', code='CS')
        self.user = User.objects.create_user(
            username='gate_admin', password='testpass123',
            role='college_admin', college=self.college,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _doc(self, oss_key, **overrides):
        defaults = dict(
            title='文档', source_type='upload', file_name='a.pdf',
            file_type='pdf', uploader=self.user, college=self.college,
            status='uploaded', visibility='public', oss_key=oss_key,
        )
        defaults.update(overrides)
        return KnowledgeDoc.objects.create(**defaults)

    def test_download_of_quarantine_doc_is_rejected(self):
        doc = self._doc('quarantine/college/1/2026-09/abc123.pdf')

        with patch.object(oss_client, 'get_download_url') as mock_url:
            response = self.client.get(f'/api/knowledge/docs/{doc.id}/download/')

        self.assertEqual(response.status_code, 404)
        mock_url.assert_not_called()

    def test_preview_of_quarantine_doc_is_rejected(self):
        doc = self._doc('quarantine/college/1/2026-09/abc123.txt', file_type='txt')

        response = self.client.get(f'/api/knowledge/docs/{doc.id}/preview/')

        self.assertEqual(response.status_code, 404)

    def test_download_of_promoted_doc_still_works(self):
        """正式区（knowledge/）对象不受影响 —— 门禁只针对隔离区。"""
        doc = self._doc('knowledge/college/1/2026-09/abc123.pdf', status='indexed')

        with patch.object(oss_client, 'get_download_url', return_value='https://signed'):
            response = self.client.get(f'/api/knowledge/docs/{doc.id}/download/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['download_url'], 'https://signed')

    def test_data_query_withholds_quarantine_download_url(self):
        """数据查询（find_docs）不得为隔离区对象签发下载链接。"""
        from utils.data_query import find_user_docs

        self._doc('quarantine/college/1/2026-09/abc123.pdf')

        with patch.object(oss_client, 'get_download_url') as mock_url:
            results = find_user_docs(self.user)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['download_url'], '')
        mock_url.assert_not_called()

    def test_data_query_still_signs_official_download_url(self):
        from utils.data_query import find_user_docs

        self._doc('knowledge/college/1/2026-09/abc123.pdf', status='indexed')

        with patch.object(oss_client, 'get_download_url', return_value='https://signed'):
            results = find_user_docs(self.user)

        self.assertEqual(results[0]['download_url'], 'https://signed')

    # ------------------------------------------------------------------
    # 详情序列化器（P1-2D STEP 4-A.2）
    #
    # 修复前：KnowledgeDocSerializer 的 download_url / oss_url 只判断 oss_key
    # 是否为空，对 quarantine/ 对象同样签发 7 天有效的签名 URL，使
    # /download/ 与 /preview/ 上的 is_official_oss_key() 门禁形同虚设。
    # ------------------------------------------------------------------
    def _serialize(self, doc):
        from .serializers import KnowledgeDocSerializer

        return KnowledgeDocSerializer(doc).data

    def test_serializer_signs_official_urls(self):
        """正式区对象仍然正常签发两种 URL（回归要求）。"""
        doc = self._doc('knowledge/college/1/2026-09/abc123.pdf', status='indexed')

        with patch.object(oss_client, 'get_download_url', return_value='https://dl'), \
                patch.object(oss_client, 'get_preview_url', return_value='https://pv'):
            data = self._serialize(doc)

        self.assertEqual(data['download_url'], 'https://dl')
        self.assertEqual(data['oss_url'], 'https://pv')

    def test_serializer_blanks_quarantine_urls_without_signing(self):
        """隔离区对象：两个字段都必须为空，且**不得触发任何签名调用**。"""
        doc = self._doc('quarantine/college/1/2026-09/abc123.pdf')

        with patch.object(oss_client, 'get_download_url') as mock_dl, \
                patch.object(oss_client, 'get_preview_url') as mock_pv:
            data = self._serialize(doc)

        self.assertEqual(data['download_url'], '')
        self.assertEqual(data['oss_url'], '')
        mock_dl.assert_not_called()
        mock_pv.assert_not_called()

    def test_serializer_blanks_keys_outside_official_namespace(self):
        """非 knowledge/ 的任意 Key 一律 fail-closed（不止 quarantine/）。

        用 is_official_oss_key() 而不是 startswith('knowledge')，
        因此 `knowledge-malicious/` 这类相似前缀不会被误判为正式区。
        """
        for oss_key in ('random/path/file.pdf',
                        'knowledge-malicious/file.pdf',
                        'knowledge',
                        'quarantine',
                        'official/x.pdf'):
            with self.subTest(oss_key=oss_key):
                doc = self._doc(oss_key)

                with patch.object(oss_client, 'get_download_url') as mock_dl, \
                        patch.object(oss_client, 'get_preview_url') as mock_pv:
                    data = self._serialize(doc)

                self.assertEqual(data['download_url'], '')
                self.assertEqual(data['oss_url'], '')
                mock_dl.assert_not_called()
                mock_pv.assert_not_called()

    def test_serializer_keeps_empty_key_behavior(self):
        """空 oss_key 仍返回 ''，原有行为不回归。"""
        doc = self._doc('')

        with patch.object(oss_client, 'get_download_url') as mock_dl, \
                patch.object(oss_client, 'get_preview_url') as mock_pv:
            data = self._serialize(doc)

        self.assertEqual(data['download_url'], '')
        self.assertEqual(data['oss_url'], '')
        mock_dl.assert_not_called()
        mock_pv.assert_not_called()

    # ------------------------------------------------------------------
    # 详情接口集成（GET /api/knowledge/docs/{id}/）
    # ------------------------------------------------------------------
    def test_detail_api_hides_signed_urls_for_quarantine_doc(self):
        """本接口此前会为 quarantine 文档返回 7 天签名 URL（审计发现的漏洞）。"""
        doc = self._doc('quarantine/college/1/2026-09/abc123.pdf')

        with patch.object(oss_client, 'get_download_url') as mock_dl, \
                patch.object(oss_client, 'get_preview_url') as mock_pv:
            response = self.client.get(f'/api/knowledge/docs/{doc.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['download_url'], '')
        self.assertEqual(response.data['oss_url'], '')
        mock_dl.assert_not_called()
        mock_pv.assert_not_called()

        # 整体断言：响应体内不存在任何可访问的签名 URL。
        # （唯一允许出现的 URL 形态是 source_url —— 爬虫来源的网页地址，
        # 不是 OSS 对象；本用例的 source_type='upload'，故不应出现。）
        body = json.dumps(response.data, ensure_ascii=False)
        self.assertNotIn('http://', body)
        self.assertNotIn('https://', body)

        # oss_key 仍然按原样返回（最小修复：只堵 URL，不动 schema / 前端契约）。
        # 单独的 Key 不构成访问凭证 —— 桶为 private，签名由服务端掌握。
        self.assertEqual(response.data['oss_key'], doc.oss_key)

    def test_detail_api_hides_urls_from_other_admin_in_scope(self):
        """同学院另一位管理员能看到元数据，但拿不到任何签名 URL。

        可见范围（_admin_scope_filter）与"可否取内容"是两件事：前者按
        学院/部门放行，后者必须由 is_official_oss_key() 把关。上传默认
        visibility='public'，因此这条路径正是审计指出的实际攻击面。
        """
        peer = User.objects.create_user(
            username='gate_peer', password='testpass123',
            role='college_admin', college=self.college,
        )
        doc = self._doc('quarantine/college/1/2026-09/abc123.pdf')
        peer_client = APIClient()
        peer_client.force_authenticate(peer)

        with patch.object(oss_client, 'get_download_url') as mock_dl, \
                patch.object(oss_client, 'get_preview_url') as mock_pv:
            response = peer_client.get(f'/api/knowledge/docs/{doc.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['download_url'], '')
        self.assertEqual(response.data['oss_url'], '')
        mock_dl.assert_not_called()
        mock_pv.assert_not_called()

    def test_detail_api_still_returns_signed_urls_for_official_doc(self):
        """正式区文档在详情接口上的行为不得回归。"""
        doc = self._doc('knowledge/college/1/2026-09/abc123.pdf', status='indexed')

        with patch.object(oss_client, 'get_download_url', return_value='https://dl'), \
                patch.object(oss_client, 'get_preview_url', return_value='https://pv'):
            response = self.client.get(f'/api/knowledge/docs/{doc.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['download_url'], 'https://dl')
        self.assertEqual(response.data['oss_url'], 'https://pv')


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

        # P1-2D STEP 4-B：合并建文档后必须派发统一审核编排（详见 `_merge`）。
        # 派发函数是 `dispatch_async`（只入队、绝不降级同步执行，见 P-4）；
        # 同时 spy `safe_delay` 并断言未被调用，防止有人改回降级派发。
        patcher = patch('utils.celery_dispatcher.dispatch_async')
        self.dispatch_async = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch('utils.celery_dispatcher.safe_delay')
        self.safe_delay = patcher.start()
        self.addCleanup(patcher.stop)
        self.commit_callbacks = []

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
        """合并分片：OSS 全 mock，返回响应。

        P1-2D STEP 4-B 起，合并成功后视图会把文档交给统一审核编排。派发发生在
        `transaction.on_commit()` 里，而 TestCase 从不真正提交事务，因此必须用
        `captureOnCommitCallbacks(execute=True)` 捕获并执行回调，否则
        "有没有派发"根本观察不到。合并视图仍**不直接触发索引**。
        """
        with patch('apps.knowledge.chunked_views.oss_client') as mock_oss:
            mock_oss.check_duplicate.return_value = None
            # 每次合并前清空记录：断言只针对本次请求（重复合并测试会调两次）。
            self.dispatch_async.reset_mock()
            self.safe_delay.reset_mock()
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                response = self.client.post(CHUNK_MERGE_URL, data, format='json')
        self.commit_callbacks = callbacks
        return response

    def assertModerationScheduled(self):
        """合并出的文档必须且只能派发一次统一审核编排任务。"""
        from .tasks import index_document_task, moderate_and_promote_task

        orchestration = [
            call.args[1] for call in self.dispatch_async.call_args_list
            if call.args and call.args[0] is moderate_and_promote_task
        ]
        self.assertEqual(
            orchestration, [KnowledgeDoc.objects.get().id],
            f'应恰好派发一次审核编排，实际 {orchestration}',
        )
        self.assertNotIn(
            index_document_task,
            [call.args[0] for call in self.dispatch_async.call_args_list if call.args],
            '合并视图不得直接派发索引任务（4-A 删掉的"合并即索引"不得恢复）',
        )
        self.safe_delay.assert_not_called()

    def assertModerationNotScheduled(self):
        """未建文档的返回路径（校验失败 / 重复合并）不得派发任何任务。"""
        self.dispatch_async.assert_not_called()
        self.assertEqual(self.commit_callbacks, [])

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
    # 初始化：隔离区 Key 与 UploadIntent 绑定（P1-2D STEP 4-A）
    # ------------------------------------------------------------------
    def test_init_puts_object_in_quarantine(self):
        """初始化即由服务端定下隔离区 Key，并把它挂到会话的 intent 上。"""
        response = self._init({
            'file_md5': 'a' * 32, 'filename': 'report.pdf', 'file_size': 2048,
        })

        self.assertEqual(response.status_code, 200)
        session = ChunkedUploadSession.objects.get(upload_id='up-new-1')
        self.assertTrue(is_quarantine_key(session.oss_key))
        self.assertIsNotNone(session.intent_id)
        self.assertEqual(session.intent.status, 'issued')

    def test_init_response_does_not_leak_quarantine_key(self):
        """响应体里不出现隔离区 Key（客户端无需知道往哪写）。"""
        response = self._init({
            'file_md5': 'a' * 32, 'filename': 'report.pdf', 'file_size': 2048,
        })

        self.assertNotIn('quarantine/', json.dumps(response.data, ensure_ascii=False))

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
        self.assertModerationNotScheduled()

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
        self.assertModerationScheduled()

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
        # 第二次合并被拒绝，因此**没有**派发任何东西（记录已被 _merge 清空）。
        self.assertModerationNotScheduled()

    def test_merge_rejects_other_users_session(self):
        """用户 A 不得合并用户 B 的会话。"""
        self._session(user=self.other, file_size=2048)

        with patch('apps.knowledge.chunked_views._list_uploaded_parts',
                   return_value=[(1, 'etag1', 2048)]):
            response = self._merge({'upload_id': 'up-test-1', 'file_md5': 'a' * 32})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(KnowledgeDoc.objects.count(), 0)

    # ------------------------------------------------------------------
    # 合并：与 UploadIntent 的联动（P1-2D STEP 4-A）
    # ------------------------------------------------------------------
    def _session_with_intent(self, size=2048, **overrides):
        """建一个带 UploadIntent 的会话（模拟 STEP 4-A 之后的新流程）。"""
        intent = create_upload_intent(
            overrides.pop('user', self.user), 'chunk.pdf',
            content_type='application/pdf', expected_size=size,
        )
        session = self._session(
            file_size=size, oss_key=intent.quarantine_key, intent=intent, **overrides,
        )
        return session, intent

    def test_merge_completes_linked_intent(self):
        """合并成功后 intent 推进到 completed，文档指向隔离区对象。"""
        session, intent = self._session_with_intent()

        with patch('apps.knowledge.chunked_views._list_uploaded_parts',
                   return_value=[(1, 'etag1', 2048)]):
            response = self._merge({'upload_id': session.upload_id, 'file_md5': 'a' * 32})

        self.assertEqual(response.status_code, 200)
        intent.refresh_from_db()
        self.assertEqual(intent.status, 'completed')
        self.assertEqual(intent.actual_size, 2048)
        doc = KnowledgeDoc.objects.get()
        self.assertTrue(is_quarantine_key(doc.oss_key))
        self.assertFalse(is_official_oss_key(doc.oss_key))
        self.assertModerationScheduled()

    def test_merge_rejects_expired_intent(self):
        """凭证已过期时不得为隔离区对象建文档记录（fail-closed）。"""
        session, intent = self._session_with_intent()
        UploadIntent.objects.filter(pk=intent.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        with patch('apps.knowledge.chunked_views._list_uploaded_parts',
                   return_value=[(1, 'etag1', 2048)]):
            response = self._merge({'upload_id': session.upload_id, 'file_md5': 'a' * 32})

        self.assertEqual(response.status_code, 409)
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

        这里刻意把两个字段设成"看起来已审核通过"的值，校验函数**仍然拒绝**：
        STEP 4-B 起真正的审核门是 ContentModeration，而这两个字段是全仓库
        无任何写入点的死字段，把它们当成放行依据只会制造虚假的安全感。
        """
        from .moderation import check_indexable

        doc = self._doc(
            oss_key='knowledge/college/0/2026-09/a.pdf',
            is_sensitive=True, sensitive_checked=True,
        )

        allowed, reason = check_indexable(doc)

        self.assertFalse(allowed)
        self.assertIn('审核记录', reason)

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


class OfficialKeyPredicateTests(TestCase):
    """`is_official_oss_key`：下载 / 预览门禁的唯一判据（P1-2D STEP 4-A）。"""

    def test_official_keys_are_accepted(self):
        for key in ('knowledge/a.pdf', 'knowledge/college/1/2026-09/a.pdf'):
            self.assertTrue(is_official_oss_key(key), key)

    def test_quarantine_keys_are_rejected(self):
        for key in ('quarantine/a.pdf', 'quarantine/college/1/2026-09/a.pdf'):
            self.assertFalse(is_official_oss_key(key), key)

    def test_quarantine_keys_are_not_official(self):
        """两个命名空间互斥：没有任何 Key 同时属于隔离区与正式区。"""
        key = build_quarantine_key('college', 1, 'a.pdf')

        self.assertTrue(is_quarantine_key(key))
        self.assertFalse(is_official_oss_key(key))

    def test_lookalike_prefixes_are_rejected(self):
        """`knowledgex/`、`quarantinex/` 这类前缀相似路径不得被误判。"""
        self.assertFalse(is_official_oss_key('knowledgex/a.pdf'))
        self.assertFalse(is_quarantine_key('quarantinex/a.pdf'))

    def test_empty_and_none_are_rejected(self):
        for value in ('', None):
            self.assertFalse(is_official_oss_key(value))
            self.assertFalse(is_quarantine_key(value))


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
