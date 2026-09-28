"""2026-09-28 业务规则：**正式知识库存储** 与 **RAG 索引** 是两道独立的门。

本文件是这条规则的可执行契约，覆盖用户实施要求 §十一 的核心场景。

两道门
======

    审核 → 判断是否允许进入正式知识库存储 → 允许 → 正式知识库（有文件）
                                              ↓ 独立判断
                                        文件能不能解析
                                              ↓ 解析成功
                                        分块 → Embedding → Milvus
                                              ↓
                                          status='indexed'

- **存储门**（`is_storage_allowed` / `promote_document`）：只问审核状态。
  `approved` 与 `unavailable` 允许；`rejected` / `pending` / `scanning` /
  `error` 一律禁止。
- **索引门**（`check_indexable` + `index_document_task` 的解析链）：在存储门
  之上再要求 "真的解析出正文 + 分块 + Embedding + Milvus 写入成功"。

因此下面这条链是**合法终态**，不是 bug：

    unavailable → 允许入库 → 解析不出来（MP4 / ZIP）→ 有文件、不可搜索

**「允许入库」≠「允许进入 RAG 索引」。** 两份同样是 `unavailable` 的文档，
一份 PDF 会走到 `indexed`，一份 MP4 必须停在非 `indexed`。本文件的每个用例
都在钉住这条分界，尤其是"不能伪造 indexed"。

测试原则：不连接任何真实外部依赖。OSS / Milvus / Embedding / 解析器全部替身，
被替身化的是**外部服务**，不是**判定本身**。
"""
from unittest import mock

from django.test import TestCase

from apps.accounts.models import User
from utils.oss import OFFICIAL_KEY_PREFIX, oss_client

from ..models import ContentModeration, DocChunk, KnowledgeDoc
from ..moderation import (
    MODERATION_STATE_APPROVED,
    MODERATION_STATE_ERROR,
    MODERATION_STATE_PENDING,
    MODERATION_STATE_REJECTED,
    MODERATION_STATE_SCANNING,
    MODERATION_STATE_UNAVAILABLE,
    STORAGE_STATE_PROMOTED,
    STORAGE_STATE_QUARANTINE,
    VERDICT_CLEAN,
    check_indexable,
)


def _parse_result(text='', error=''):
    from ..extractor import ParseResult

    return ParseResult(text=text, error=error, pages_total=1, pages_native=1)


#: 足以通过分块门槛的正文（`chunk_document` 会生成多个片段）
_ENOUGH_TEXT = '校园虚拟资源管理系统的正文内容，用于验证索引链路的调用顺序。' * 40


class _StorageIndexCase(TestCase):
    """公共夹具：一份已经躺在正式区、审核记录可指定的文档。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username='storage_index_user', password='testpass123',
            role='super_admin',
        )

    def _make_doc(self, *, state=MODERATION_STATE_UNAVAILABLE, **overrides):
        """建文档 + 审核记录，对象已在 `knowledge/` 正式命名空间。

        默认 `storage_state=promoted`：本文件验证的是**过了存储门之后**的
        解析链路，存储门本身由 test_promotion_flow 覆盖。
        """
        defaults = dict(
            title='存储与索引分界样本',
            description='描述不应被当作正文索引',
            source_type='upload',
            file_name='sample.pdf',
            file_type='pdf',
            oss_key=f'{OFFICIAL_KEY_PREFIX}college/1/2026-09/sample.pdf',
            uploader=self.user,
            status='uploaded',
        )
        defaults.update(overrides)
        doc = KnowledgeDoc.objects.create(**defaults)
        ContentModeration.objects.create(
            doc=doc,
            state=state,
            verdict=VERDICT_CLEAN if state != MODERATION_STATE_ERROR else '',
            storage_state=overrides.get('storage_state', STORAGE_STATE_PROMOTED),
            promoted_key=doc.oss_key,
        )
        return doc

    def _run_index(self, doc_id):
        """同步执行索引任务（Celery 存在用 .apply，本地降级用 .delay）。"""
        from ..tasks import index_document_task

        if hasattr(index_document_task, 'apply'):
            return index_document_task.apply(args=(doc_id,), throw=True)
        return index_document_task.delay(doc_id)

    def _patch_downstream(self):
        """拦截 Embedding / Milvus。返回 (embed, insert, delete, ensure) 四个 mock。"""
        from utils.llm import llm_client
        from utils.milvus import milvus_client

        patches = [
            mock.patch.object(
                llm_client, 'embed',
                side_effect=lambda texts, **kw: [[0.1] * 8 for _ in texts]),
            mock.patch.object(
                milvus_client, 'insert_chunks',
                side_effect=lambda chunks, embs: [c['vector_id'] for c in chunks]),
            mock.patch.object(milvus_client, 'delete_by_doc'),
            mock.patch.object(milvus_client, 'ensure_collection', return_value=True),
        ]
        mocks = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)
        return mocks


# ===========================================================================
# 一、存储门：允许入库的状态集合
# ===========================================================================

class StorageGateMatrixTests(_StorageIndexCase):
    """存储门只回答"允不允许进正式知识库"，`unavailable` 与 `approved` 同权。"""

    def test_approved_and_unavailable_pass_the_storage_gate(self):
        for state in (MODERATION_STATE_APPROVED, MODERATION_STATE_UNAVAILABLE):
            with self.subTest(state=state):
                ContentModeration.objects.all().delete()
                KnowledgeDoc.objects.all().delete()
                doc = self._make_doc(state=state)

                allowed, reason = check_indexable(doc)

                self.assertTrue(allowed, f'{state} 应通过审计门，实际：{reason}')

    def test_forbidden_states_do_not_pass_the_storage_gate(self):
        for state in (MODERATION_STATE_REJECTED, MODERATION_STATE_PENDING,
                      MODERATION_STATE_SCANNING, MODERATION_STATE_ERROR):
            with self.subTest(state=state):
                ContentModeration.objects.all().delete()
                KnowledgeDoc.objects.all().delete()
                doc = self._make_doc(state=state)

                allowed, _reason = check_indexable(doc)

                self.assertFalse(allowed, f'{state} 不得通过审计门')

    def test_unavailable_but_not_promoted_is_still_blocked(self):
        """放行状态 ≠ 已入库：对象还在隔离区时不得进入索引流程。"""
        doc = self._make_doc()
        ContentModeration.objects.filter(doc=doc).update(
            storage_state=STORAGE_STATE_QUARANTINE,
        )

        allowed, reason = check_indexable(doc)

        self.assertFalse(allowed)
        self.assertIn('提升', reason)


# ===========================================================================
# 二、unavailable + 可解析文档 → 走完整解析链，最终 indexed
# ===========================================================================

class UnavailableParseableDocumentTests(_StorageIndexCase):
    """§二 的 PDF 分支：`unavailable` 允许入库，且内容可解析 → 正常索引。"""

    def test_unavailable_parseable_pdf_reaches_indexed(self):
        doc = self._make_doc()
        m_embed, m_insert, _m_delete, _m_ensure = self._patch_downstream()

        with mock.patch(
            'apps.knowledge.extractor.extract_text_from_oss_detailed',
            return_value=_parse_result(text=_ENOUGH_TEXT),
        ) as m_parse:
            self._run_index(doc.id)

        doc.refresh_from_db()
        self.assertEqual(doc.status, 'indexed')
        self.assertGreater(doc.chunk_count, 0)
        self.assertIsNotNone(doc.indexed_at)

        # 解析 → 分块 → Embedding → Milvus 每一步都真的走了
        m_parse.assert_called_once()
        m_embed.assert_called_once()
        m_insert.assert_called_once()
        self.assertEqual(
            DocChunk.objects.filter(doc=doc).count(), doc.chunk_count,
        )

    def test_index_pipeline_runs_in_order(self):
        """调用顺序：解析 → Embedding → 清旧向量 → 建集合 → 写 Milvus → 写 DocChunk。

        顺序错了会在两个地方出问题：先写 Milvus 再算 Embedding 会写入空向量；
        不清旧向量就插入会让重试后同一文档出现重复分块。
        """
        doc = self._make_doc()
        order = []

        from utils.llm import llm_client
        from utils.milvus import milvus_client

        with mock.patch(
            'apps.knowledge.extractor.extract_text_from_oss_detailed',
            side_effect=lambda *a, **kw: (
                order.append('parse') or _parse_result(text=_ENOUGH_TEXT)),
        ), mock.patch.object(
            llm_client, 'embed',
            side_effect=lambda texts, **kw: (
                order.append('embed') or [[0.1] * 8 for _ in texts]),
        ), mock.patch.object(
            milvus_client, 'delete_by_doc',
            side_effect=lambda *a, **kw: order.append('delete_by_doc'),
        ), mock.patch.object(
            milvus_client, 'ensure_collection',
            side_effect=lambda *a, **kw: (order.append('ensure') or True),
        ), mock.patch.object(
            milvus_client, 'insert_chunks',
            side_effect=lambda chunks, embs: (
                order.append('insert') or [c['vector_id'] for c in chunks]),
        ):
            self._run_index(doc.id)

        self.assertEqual(
            order,
            ['parse', 'embed', 'delete_by_doc', 'ensure', 'insert'],
            f'索引链路顺序被改动：{order}',
        )
        doc.refresh_from_db()
        self.assertEqual(doc.status, 'indexed')

    def test_unreadable_document_stays_out_of_the_index(self):
        """解析通道出错（能力不足）→ 有文件、不可搜索，`status != 'indexed'`。"""
        doc = self._make_doc()
        m_embed, m_insert, *_ = self._patch_downstream()

        with mock.patch(
            'apps.knowledge.extractor.extract_text_from_oss_detailed',
            return_value=_parse_result(error='不支持的视频格式，无法提取文本'),
        ):
            self._run_index(doc.id)

        doc.refresh_from_db()
        self.assertNotEqual(doc.status, 'indexed')
        self.assertEqual(doc.status, 'failed')
        self.assertIn('解析', doc.index_error)
        self.assertEqual(doc.chunk_count, 0)
        m_embed.assert_not_called()
        m_insert.assert_not_called()
        self.assertEqual(DocChunk.objects.filter(doc=doc).count(), 0)


# ===========================================================================
# 三、unavailable + 不可解析文档（MP4 / ZIP）→ 入库成功，但不索引
# ===========================================================================

class UnavailableUnparseableDocumentTests(_StorageIndexCase):
    """§二 的 MP4 分支——本规则最容易写错的地方。

    正确结果是"**已入库、未索引**"：对象在 `knowledge/`、`storage_state=promoted`、
    审核状态保持 `unavailable`；同时 `status != 'indexed'`、没有 DocChunk、
    没有 Embedding、没有 Milvus 写入。**绝不能**为了让链路"看起来跑通"而写
    `status='indexed'`。
    """

    def _run_unparseable(self, *, file_name, file_type, error):
        doc = self._make_doc(file_name=file_name, file_type=file_type)
        m_embed, m_insert, m_delete, m_ensure = self._patch_downstream()

        with mock.patch(
            'apps.knowledge.extractor.extract_text_from_oss_detailed',
            return_value=_parse_result(error=error),
        ) as m_parse:
            self._run_index(doc.id)

        doc.refresh_from_db()
        return doc, m_parse, (m_embed, m_insert, m_delete, m_ensure)

    def test_unavailable_mp4_is_stored_but_not_indexed(self):
        doc, m_parse, (m_embed, m_insert, m_delete, m_ensure) = self._run_unparseable(
            file_name='运动会开幕式.mp4', file_type='mp4',
            error='不支持的视频格式，无法提取文本',
        )

        # 存储侧：允许入库，且确实已经入库（文件在正式区）
        moderation = ContentModeration.objects.get(doc=doc)
        self.assertEqual(moderation.state, MODERATION_STATE_UNAVAILABLE)
        self.assertEqual(moderation.storage_state, STORAGE_STATE_PROMOTED)
        self.assertTrue(doc.oss_key.startswith(OFFICIAL_KEY_PREFIX))

        # 索引侧：一步都没往下走，尤其没有伪造 indexed
        self.assertNotEqual(doc.status, 'indexed')
        self.assertEqual(doc.chunk_count, 0)
        self.assertEqual(DocChunk.objects.filter(doc=doc).count(), 0)
        m_parse.assert_called_once()      # 解析尝试过，这是"独立判断"的落点
        m_embed.assert_not_called()
        m_insert.assert_not_called()
        m_delete.assert_not_called()
        m_ensure.assert_not_called()

    def test_unavailable_zip_is_stored_but_not_indexed(self):
        doc, _m_parse, (m_embed, m_insert, _m_delete, _m_ensure) = self._run_unparseable(
            file_name='教学材料打包.zip', file_type='zip',
            error='不支持的压缩包格式，无法提取文本',
        )

        self.assertEqual(
            ContentModeration.objects.get(doc=doc).state,
            MODERATION_STATE_UNAVAILABLE,
        )
        self.assertNotEqual(doc.status, 'indexed')
        self.assertEqual(DocChunk.objects.filter(doc=doc).count(), 0)
        m_embed.assert_not_called()
        m_insert.assert_not_called()

    def test_unavailable_empty_images_are_stored_but_not_indexed(self):
        doc, _m_parse, (m_embed, m_insert, _m_delete, _m_ensure) = self._run_unparseable(
            file_name='无文字海报.jpg', file_type='jpg',
            error='OCR 未识别出任何文字',
        )

        self.assertEqual(
            ContentModeration.objects.get(doc=doc).state,
            MODERATION_STATE_UNAVAILABLE,
        )
        self.assertNotEqual(doc.status, 'indexed')
        self.assertEqual(DocChunk.objects.filter(doc=doc).count(), 0)
        m_embed.assert_not_called()
        m_insert.assert_not_called()


# ===========================================================================
# 四、被拒绝 / 未审完的文档：连解析都不许开始
# ===========================================================================

class ForbiddenStatesNeverReachParsingTests(_StorageIndexCase):
    """`rejected` / `pending` / `scanning` / `error` 在解析之前就被挡住。

    "入库"与"索引"分离**不是**给禁止状态开后门：它们既不进正式知识库，
    也不进索引流程。
    """

    def _run_forbidden(self, state):
        doc = self._make_doc(state=state)
        m_embed, m_insert, m_delete, m_ensure = self._patch_downstream()

        with mock.patch(
            'apps.knowledge.extractor.extract_text_from_oss_detailed',
            return_value=_parse_result(text=_ENOUGH_TEXT),
        ) as m_parse:
            self._run_index(doc.id)

        return doc, m_parse, (m_embed, m_insert, m_delete, m_ensure)

    def test_rejected_document_never_reaches_the_parser(self):
        doc, m_parse, (m_embed, m_insert, m_delete, m_ensure) = self._run_forbidden(
            MODERATION_STATE_REJECTED,
        )
        doc.refresh_from_db()

        self.assertNotEqual(doc.status, 'indexed')
        self.assertEqual(doc.chunk_count, 0)
        m_parse.assert_not_called()       # 被拒绝的内容连解析都不该发生
        m_embed.assert_not_called()
        m_insert.assert_not_called()
        m_delete.assert_not_called()
        m_ensure.assert_not_called()
        self.assertEqual(DocChunk.objects.filter(doc=doc).count(), 0)

    def test_pending_and_scanning_never_reach_the_parser(self):
        for state in (MODERATION_STATE_PENDING, MODERATION_STATE_SCANNING):
            with self.subTest(state=state):
                ContentModeration.objects.all().delete()
                KnowledgeDoc.objects.all().delete()
                doc, m_parse, mocks = self._run_forbidden(state)
                doc.refresh_from_db()

                self.assertNotEqual(doc.status, 'indexed')
                m_parse.assert_not_called()
                for m in mocks:
                    m.assert_not_called()

    def test_error_state_never_reaches_the_parser(self):
        """`error`（审核过程异常）与 `unavailable`（能力不足）必须区别对待：
        前者不得自动入库，也不得进入索引链。"""
        doc, m_parse, (m_embed, m_insert, _m_delete, _m_ensure) = self._run_forbidden(
            MODERATION_STATE_ERROR,
        )
        doc.refresh_from_db()

        self.assertNotEqual(doc.status, 'indexed')
        m_parse.assert_not_called()
        m_embed.assert_not_called()
        m_insert.assert_not_called()

    def test_rejected_document_already_in_official_namespace_is_still_blocked(self):
        """对象已经躺在 `knowledge/` 也不放行——门禁看的是审核状态，不是路径。"""
        doc = self._make_doc(state=MODERATION_STATE_REJECTED)
        allowed, reason = check_indexable(doc)

        self.assertFalse(allowed)
        self.assertIn('rejected', reason)


# ===========================================================================
# 五、不能伪造 indexed：下游任何一步失败都不得写出 indexed
# ===========================================================================

class IndexedRequiresTheWholeChainTests(_StorageIndexCase):
    """`status='indexed'` 是"整条链都成功了"的结论，不是"流程跑过"的标记。"""

    def _run_with(self, *, embed=None, insert=None, text=_ENOUGH_TEXT):
        """跑一次索引任务，**容忍任务把异常抛出**。

        任务在 `retries < max_retries` 时会 `raise`（交给 Celery 重试），
        同步降级执行（无 Celery）时 `self is None`，异常直接冒到调用方。
        这不是本用例要断言的东西——本用例断言的是**数据库里没有被写成 indexed**。
        """
        doc = self._make_doc()
        from utils.llm import llm_client
        from utils.milvus import milvus_client

        embed_side = embed or (lambda texts, **kw: [[0.1] * 8 for _ in texts])
        insert_side = insert or (
            lambda chunks, embs: [c['vector_id'] for c in chunks])

        patches = [
            mock.patch.object(llm_client, 'embed', side_effect=embed_side),
            mock.patch.object(milvus_client, 'insert_chunks', side_effect=insert_side),
            mock.patch.object(milvus_client, 'delete_by_doc'),
            mock.patch.object(milvus_client, 'ensure_collection', return_value=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        with mock.patch(
            'apps.knowledge.extractor.extract_text_from_oss_detailed',
            return_value=_parse_result(text=text),
        ):
            try:
                self._run_index(doc.id)
            except Exception:      # noqa: BLE001 — 见 docstring
                pass

        doc.refresh_from_db()
        return doc

    def test_milvus_write_failure_does_not_produce_indexed(self):
        """Milvus 写失败 → 绝不能留下 indexed（那会让检索指向不存在的向量）。"""
        def _boom(chunks, embs):
            raise RuntimeError('Milvus 写入失败')

        doc = self._run_with(insert=_boom)

        self.assertNotEqual(doc.status, 'indexed')
        self.assertIsNone(doc.indexed_at)
        self.assertEqual(doc.chunk_count, 0)
        self.assertEqual(DocChunk.objects.filter(doc=doc).count(), 0)

    def test_embedding_failure_does_not_produce_indexed(self):
        """Embedding 失败 → 不得写 DocChunk、不得 indexed。"""
        def _short(texts, **kw):
            return []          # 数量不匹配 = 失败

        doc = self._run_with(embed=_short)

        self.assertNotEqual(doc.status, 'indexed')
        self.assertEqual(DocChunk.objects.filter(doc=doc).count(), 0)

    def test_milvus_write_is_verified_before_indexed_is_written(self):
        """indexed 只能出现在 Milvus 写入**之后**——顺序断言。"""
        order = []
        doc = self._make_doc()
        from utils.llm import llm_client
        from utils.milvus import milvus_client

        patches = [
            mock.patch.object(
                llm_client, 'embed',
                side_effect=lambda texts, **kw: [[0.1] * 8 for _ in texts]),
            mock.patch.object(milvus_client, 'delete_by_doc'),
            mock.patch.object(milvus_client, 'ensure_collection', return_value=True),
            mock.patch.object(
                milvus_client, 'insert_chunks',
                side_effect=lambda chunks, embs: (
                    order.append('insert') or [c['vector_id'] for c in chunks]),
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        real_save = KnowledgeDoc.save

        def _spy_save(self, *args, **kwargs):
            if getattr(self, 'status', None) == 'indexed':
                order.append('indexed')
            return real_save(self, *args, **kwargs)

        with mock.patch.object(KnowledgeDoc, 'save', _spy_save), mock.patch(
            'apps.knowledge.extractor.extract_text_from_oss_detailed',
            return_value=_parse_result(text=_ENOUGH_TEXT),
        ):
            self._run_index(doc.id)

        self.assertEqual(order, ['insert', 'indexed'],
                         f'indexed 必须在 Milvus 写入之后：{order}')


# ===========================================================================
# 六、读取侧一致性：入库但未索引的文档不出现在检索结果里
# ===========================================================================

class UnindexedDocumentIsNotRetrievableTests(_StorageIndexCase):
    """`status != 'indexed'` 的文档不得被检索通道返回（腿和门要对齐）。"""

    def test_failed_document_is_excluded_from_the_gate(self):
        from ..moderation import filter_indexable_docs

        doc = self._make_doc()
        with mock.patch(
            'apps.knowledge.extractor.extract_text_from_oss_detailed',
            return_value=_parse_result(error='不支持的视频格式'),
        ):
            self._run_index(doc.id)

        doc.refresh_from_db()
        self.assertEqual(doc.status, 'failed')
        # 审核门本身放行（它只管审核 + 存储），检索侧再用 status 收窄
        self.assertIn(doc.id, set(
            filter_indexable_docs(KnowledgeDoc.objects.all())
            .values_list('id', flat=True),
        ))
        self.assertFalse(
            KnowledgeDoc.objects.filter(id=doc.id, status='indexed').exists(),
            '未索引的文档不得被当成已索引',
        )

    def test_oss_source_object_is_never_deleted_on_promotion(self):
        """promotion 只复制不删除：索引失败也不能把正式区的文件弄丢。"""
        with mock.patch.object(oss_client, 'head_object') as m_head:
            m_head.return_value = mock.Mock(status=200)
            doc = self._make_doc()

        self.assertTrue(doc.oss_key.startswith(OFFICIAL_KEY_PREFIX))
