"""P1-2D STEP 4-B：审核 → Promotion → Index 全链路测试

覆盖面（对应实施要求的 §二十 ~ §二十五）：

    A. `build_promoted_key()` 纯函数（只换顶层前缀，拒绝一切可疑输入）
    B. `promote_document()` 的 fail-closed 判定（未通过审核绝不提升）
    C. `promote_document()` 成功路径（OSS copy + verify → CAS → 改 oss_key）
    D. `promote_document()` 失败路径（OSS 报错 / 校验不符 → promotion_failed）
    E. 幂等性（重复调用不重复 copy、不重复索引）
    F. 并发（抢占 CAS 与收尾 CAS 都只有一个赢家）
    G. `check_indexable()` 的完整门禁矩阵（命名空间 × 审核状态 × 存储状态）
    H. `moderate_and_promote_task()` 编排（通过才提升，拒绝即止步）
    I. `retry_failed_moderations_task()`（只重派 unavailable / error，绝不自动放行）
    J. SSE 出口纵深防御（隔离区对象一律不签名）

测试原则（§二十七）：**不连接任何真实外部依赖**。OSS 用内存替身、检测器全部
mock、任务派发全部 patch 掉 `safe_delay`。全部可重复、无网络访问。
"""
from unittest.mock import patch

import oss2
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from utils.oss import (
    OFFICIAL_KEY_PREFIX,
    QUARANTINE_KEY_PREFIX,
    OSSOperationError,
    build_promoted_key,
    build_quarantine_key,
    is_official_oss_key,
    oss_client,
)

from .upload_intent import is_quarantine_key

from .detectors import DetectorResult
from .models import ContentModeration, KnowledgeDoc, ModerationAttempt
from .moderation import (
    MODERATION_STATE_APPROVED,
    MODERATION_STATE_CONFIRMED_UNCHECKED,
    MODERATION_STATE_ERROR,
    MODERATION_STATE_PENDING,
    MODERATION_STATE_REJECTED,
    MODERATION_STATE_SCANNING,
    MODERATION_STATE_UNAVAILABLE,
    STORAGE_STATE_DELETED,
    STORAGE_STATE_PROMOTED,
    STORAGE_STATE_PROMOTING,
    STORAGE_STATE_PROMOTION_FAILED,
    STORAGE_STATE_QUARANTINE,
    VERDICT_CLEAN,
    VERDICT_VIRUS,
    check_indexable,
    is_document_indexable_by_moderation,
    is_indexable_moderation_state,
)
from .promotion import (
    PromotionResult,
    promote_document,
    promotion_claim_timeout_seconds,
)
from .tasks import (
    index_document_task,
    moderate_and_promote_task,
    retry_failed_moderations_task,
)

QUARANTINE_KEY = 'quarantine/college/1/2026-09/abc123.txt'
EXPECTED_PROMOTED_KEY = 'knowledge/college/1/2026-09/abc123.txt'


# ===========================================================================
# 测试替身
# ===========================================================================

class _FakeGetResult:
    def __init__(self, payload):
        self._payload = payload
        self._pos = 0
        self.closed = False

    def read(self, amt=None):
        if self._pos >= len(self._payload):
            return b''
        chunk = (self._payload[self._pos:] if amt is None
                 else self._payload[self._pos:self._pos + amt])
        self._pos += len(chunk)
        return chunk

    def close(self):
        self.closed = True


class _FakeHeadResult:
    def __init__(self, size, content_type='application/octet-stream',
                 etag='d41d8cd98f00b204e9800998ecf8427e'):
        self.content_length = size
        self.headers = {'Content-Type': content_type}
        self.etag = etag


class _PromotionBucket:
    """内存 OSS 替身：同时支持审核读取（get_object）与提升（head / copy）。

    `copy_transform` 用来模拟"复制调用没报错、但目标对象并未出现/内容被改写"
    这类静默故障——promotion 必须靠复制后的 HEAD 校验发现它。
    """

    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.copy_calls = []
        self.copy_side_effect = None
        self.copy_transform = None

    def get_object(self, key, **kwargs):
        if key not in self.objects:
            raise oss2.exceptions.OssError(404, {}, b'Not Found', {})
        return _FakeGetResult(self.objects[key])

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

    def delete_object(self, key):
        self.objects.pop(key, None)


def _call_task(task, *args):
    """同步调用任务体（兼容"装有 Celery"与"降级兼容层"两种形态）。

    降级时 `@shared_task(bind=True)` 退化为原函数，self 需显式补 None 占位；
    装有 Celery 时 `task.run` 已经绑定好 self，不能再多传一个占位参数。
    （`tests.py::IndexTaskSecurityTests._run_task` 用的是同一套调用约定。）
    """
    runner = getattr(task, 'run', None)
    if runner is None:
        return task(None, *args)
    return runner(*args)


def _detector_clean(name):
    return DetectorResult.ok(name, VERDICT_CLEAN)


def _detector_skipped(name):
    return DetectorResult.skipped(name)


class _PromotionTestCase(TestCase):
    """公共夹具：内存 OSS + 一份隔离区文档 + 一个可控的审核记录。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username='p4b_user', password='testpass123', role='super_admin',
        )
        self.bucket = _PromotionBucket({QUARANTINE_KEY: '文档正文内容'.encode('utf-8')})
        patcher = patch.object(oss_client, '_bucket', self.bucket)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.doc = self._make_doc()

    def _make_doc(self, oss_key=QUARANTINE_KEY, **overrides):
        defaults = dict(
            title='P1-2D 4-B 测试文档',
            source_type='upload',
            # 用 txt 而非 pdf：本地环境没有 PDF 解析库，pdf 会因为"解析依赖缺失"
            # 被判 unavailable，让审核编排测试测不出"通过"这条路径。
            file_name='测试资料.txt',
            file_type='txt',
            uploader=self.user,
            status='uploaded',
            oss_key=oss_key,
        )
        defaults.update(overrides)
        return KnowledgeDoc.objects.create(**defaults)

    def _moderation(self, doc=None, state=MODERATION_STATE_APPROVED,
                    storage_state=STORAGE_STATE_QUARANTINE, **overrides):
        defaults = dict(doc=doc or self.doc, state=state, storage_state=storage_state)
        defaults.update(overrides)
        return ContentModeration.objects.create(**defaults)


# ===========================================================================
# A. build_promoted_key（§三：正式区 Key 生成）
# ===========================================================================

class BuildPromotedKeyTests(TestCase):
    """正式区 Key 只能由隔离区 Key 推出，且只换顶层前缀。"""

    def test_only_the_top_level_prefix_changes(self):
        self.assertEqual(build_promoted_key(QUARANTINE_KEY), EXPECTED_PROMOTED_KEY)

    def test_result_is_an_official_key(self):
        self.assertTrue(is_official_oss_key(build_promoted_key(QUARANTINE_KEY)))

    def test_round_trip_with_build_quarantine_key(self):
        """与 `build_quarantine_key()` 互为逆操作：分片结构逐段保持。"""
        created = build_quarantine_key('college', 7, '课程设计报告.docx')

        promoted = build_promoted_key(created)

        self.assertTrue(created.startswith(QUARANTINE_KEY_PREFIX))
        self.assertTrue(promoted.startswith(OFFICIAL_KEY_PREFIX))
        self.assertEqual(
            promoted[len(OFFICIAL_KEY_PREFIX):],
            created[len(QUARANTINE_KEY_PREFIX):],
        )

    def test_surrounding_whitespace_is_tolerated(self):
        self.assertEqual(
            build_promoted_key(f'  {QUARANTINE_KEY}  '), EXPECTED_PROMOTED_KEY,
        )

    def test_rejects_empty_and_non_string(self):
        for value in ('', '   ', None, 42, [], b'quarantine/a/b/c/d.pdf'):
            with self.subTest(value=value):
                with self.assertRaises(OSSOperationError):
                    build_promoted_key(value)

    def test_rejects_keys_outside_the_quarantine_namespace(self):
        for value in (
            'random/path/file.pdf',
            'knowledge/college/1/2026-09/a.pdf',       # 已是正式区
            'knowledge-malicious/college/1/2026-09/a.pdf',
            'quarantinex/college/1/2026-09/a.pdf',
            '/quarantine/college/1/2026-09/a.pdf',
        ):
            with self.subTest(value=value):
                with self.assertRaises(OSSOperationError):
                    build_promoted_key(value)

    def test_rejects_traversal_and_control_characters(self):
        for value in (
            'quarantine/../knowledge/x.pdf',
            'quarantine/college/1/2026-09/../../etc/passwd',
            'quarantine/college/1/2026-09/a\r\n.pdf',
            'quarantine/college/1/2026-09/a\\b.pdf',
            'quarantine/college/1/2026-09/a\x00.pdf',
        ):
            with self.subTest(value=value):
                with self.assertRaises(OSSOperationError):
                    build_promoted_key(value)

    def test_rejects_wrong_segment_shape(self):
        """前缀之后必须恰好四段非空路径，与 build_quarantine_key 结构一致。"""
        for value in (
            'quarantine/',
            'quarantine/college',
            'quarantine/college/1',
            'quarantine/college/1/2026-09',
            'quarantine/college/1/2026-09/',
            'quarantine/college//2026-09/a.pdf',
            'quarantine/college/1/2026-09/a/b.pdf',
            'quarantine/./1/2026-09/a.pdf',
        ):
            with self.subTest(value=value):
                with self.assertRaises(OSSOperationError):
                    build_promoted_key(value)

    def test_never_returns_a_quarantine_key(self):
        """输出必须始终跨出隔离区命名空间。"""
        promoted = build_promoted_key(build_quarantine_key('school', 0, 'a.txt'))

        self.assertFalse(is_quarantine_key(promoted))
        self.assertTrue(is_official_oss_key(promoted))


# ===========================================================================
# B. promote_document：fail-closed 判定（§六、§十九）
# ===========================================================================

class PromotionGateTests(_PromotionTestCase):
    """未通过审核的文档**绝不**被提升，且不产生任何副作用。"""

    def _assert_untouched(self, result):
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.oss_key, QUARANTINE_KEY)
        self.assertTrue(is_quarantine_key(self.doc.oss_key))
        self.assertNotEqual(self.doc.status, 'indexed')
        self.assertEqual(self.bucket.copy_calls, [])
        self.assertNotIn(EXPECTED_PROMOTED_KEY, self.bucket.objects)
        self.assertIn(QUARANTINE_KEY, self.bucket.objects)
        self.assertFalse(result.did_promote)

    def test_document_without_moderation_record_is_skipped(self):
        """没有审核记录 ≡ 未审核，绝不能因为"查不到"而放行。"""
        result = promote_document(self.doc.id)

        self.assertEqual(result.status, PromotionResult.STATUS_SKIPPED)
        self.assertEqual(result.reason, 'no_moderation')
        self._assert_untouched(result)

    def test_legacy_document_in_official_namespace_is_skipped(self):
        """历史文档 osc_key 已在 knowledge/：不能就地"提升"（无源可提升）。"""
        doc = self._make_doc(oss_key='knowledge/college/1/2026-09/legacy.pdf')
        self._moderation(doc=doc)

        result = promote_document(doc.id)

        self.assertEqual(result.status, PromotionResult.STATUS_SKIPPED)
        self.assertIn('invalid_source_key', result.reason)
        self.assertEqual(self.bucket.copy_calls, [])

    def test_document_without_oss_key_is_skipped(self):
        doc = self._make_doc(oss_key='')
        self._moderation(doc=doc)

        result = promote_document(doc.id)

        self.assertEqual(result.status, PromotionResult.STATUS_SKIPPED)
        self.assertEqual(result.reason, 'no_quarantine_key')
        self.assertEqual(self.bucket.copy_calls, [])

    def test_missing_document_is_skipped(self):
        result = promote_document(999999)

        self.assertEqual(result.status, PromotionResult.STATUS_SKIPPED)
        self.assertEqual(result.reason, 'doc_not_found')

    def test_every_non_indexable_state_blocks_promotion(self):
        """白名单之外的状态一个都不能提升（含 pending / scanning 中间态）。"""
        for state in (
            MODERATION_STATE_PENDING,
            MODERATION_STATE_SCANNING,
            MODERATION_STATE_REJECTED,
            MODERATION_STATE_UNAVAILABLE,
            MODERATION_STATE_ERROR,
        ):
            with self.subTest(state=state):
                ContentModeration.objects.all().delete()
                doc = self._make_doc(oss_key=(
                    'quarantine/college/1/2026-09/'
                    f'{state}.pdf'
                ))
                self.bucket.objects[doc.oss_key] = b'content'
                self._moderation(doc=doc, state=state)

                result = promote_document(doc.id, schedule_index=False)

                self.assertEqual(result.status, PromotionResult.STATUS_SKIPPED)
                self.assertEqual(result.reason, f'not_approved:{state}')
                self.assertEqual(self.bucket.copy_calls, [])
                doc.refresh_from_db()
                self.assertTrue(is_quarantine_key(doc.oss_key))

    def test_unknown_state_is_denied(self):
        """未知状态走白名单判定 → 拒绝（黑名单写法会在这里静默放行）。"""
        self._moderation(state='future_state_from_a_newer_version')

        result = promote_document(self.doc.id, schedule_index=False)

        self.assertEqual(result.status, PromotionResult.STATUS_SKIPPED)
        self.assertEqual(self.bucket.copy_calls, [])

    def test_gate_matches_the_shared_whitelist(self):
        """promotion 的判定必须复用 `is_indexable_moderation_state()`，
        不得另写一套 approved 比较。"""
        for state in (
            MODERATION_STATE_APPROVED,
            MODERATION_STATE_CONFIRMED_UNCHECKED,
            MODERATION_STATE_REJECTED,
            MODERATION_STATE_UNAVAILABLE,
            MODERATION_STATE_ERROR,
            MODERATION_STATE_PENDING,
            MODERATION_STATE_SCANNING,
            '',
            None,
        ):
            with self.subTest(state=state):
                self.assertEqual(is_indexable_moderation_state(state), state in (
                    MODERATION_STATE_APPROVED, MODERATION_STATE_CONFIRMED_UNCHECKED,
                ))

    def test_rejected_document_already_in_official_namespace_is_not_re_promoted(self):
        """storage_state 已是 promoted 但审核结论是 rejected：不得短路放行。"""
        doc = self._make_doc(oss_key=EXPECTED_PROMOTED_KEY)
        self._moderation(
            doc=doc, state=MODERATION_STATE_REJECTED,
            storage_state=STORAGE_STATE_PROMOTED, promoted_key=EXPECTED_PROMOTED_KEY,
        )

        result = promote_document(doc.id, schedule_index=False)

        self.assertEqual(result.status, PromotionResult.STATUS_SKIPPED)
        self.assertFalse(result.did_promote)

    def test_deleted_storage_state_is_not_eligible_for_promotion(self):
        self._moderation(storage_state=STORAGE_STATE_DELETED)

        result = promote_document(self.doc.id, schedule_index=False)

        self.assertFalse(result.did_promote)
        self.assertEqual(self.bucket.copy_calls, [])


# ===========================================================================
# C. promote_document：成功路径（§五、§八）
# ===========================================================================

class PromotionSuccessTests(_PromotionTestCase):

    def test_approved_document_is_promoted(self):
        self._moderation()

        result = promote_document(self.doc.id, schedule_index=False)

        self.assertEqual(result.status, PromotionResult.STATUS_PROMOTED)
        self.assertTrue(result.did_promote)
        self.assertEqual(result.promoted_key, EXPECTED_PROMOTED_KEY)
        self.assertEqual(len(self.bucket.copy_calls), 1)
        self.assertIn(EXPECTED_PROMOTED_KEY, self.bucket.objects)

    def test_confirmed_unchecked_is_also_eligible(self):
        """人工确认放行的文档与 approved 同等对待（同属白名单）。"""
        self._moderation(state=MODERATION_STATE_CONFIRMED_UNCHECKED)

        result = promote_document(self.doc.id, schedule_index=False)

        self.assertEqual(result.status, PromotionResult.STATUS_PROMOTED)

    def test_document_key_is_updated_only_after_a_verified_copy(self):
        """`KnowledgeDoc.oss_key` 代表"已存在的正式区对象"，不是预期值。"""
        self._moderation()

        promote_document(self.doc.id, schedule_index=False)

        self.doc.refresh_from_db()
        self.assertEqual(self.doc.oss_key, EXPECTED_PROMOTED_KEY)
        self.assertIn(self.doc.oss_key, self.bucket.objects)
        self.assertTrue(is_official_oss_key(self.doc.oss_key))

    def test_storage_state_becomes_promoted_with_audit_fields(self):
        self._moderation()

        promote_document(self.doc.id, schedule_index=False)

        moderation = ContentModeration.objects.get(doc=self.doc)
        self.assertEqual(moderation.storage_state, STORAGE_STATE_PROMOTED)
        self.assertEqual(moderation.promoted_key, EXPECTED_PROMOTED_KEY)
        self.assertEqual(moderation.quarantine_key, QUARANTINE_KEY)
        self.assertEqual(moderation.promotion_error, '')
        self.assertEqual(moderation.promotion_retry, 1)

    def test_moderation_verdict_is_never_touched_by_promotion(self):
        """审核结论与存储状态是两件事：promotion 只碰后者。"""
        self._moderation()

        promote_document(self.doc.id, schedule_index=False)

        moderation = ContentModeration.objects.get(doc=self.doc)
        self.assertEqual(moderation.state, MODERATION_STATE_APPROVED)
        self.assertEqual(moderation.applied_seq, 0)

    def test_source_object_is_not_deleted(self):
        """copy-verify 孤儿：源对象在本阶段刻意保留，不在原语里做 copy+delete。"""
        self._moderation()

        promote_document(self.doc.id, schedule_index=False)

        self.assertIn(QUARANTINE_KEY, self.bucket.objects)

    def test_index_is_scheduled_only_after_commit(self):
        """§十二：派发必须走 on_commit，否则同步降级时会读到旧的 oss_key。"""
        self._moderation()

        with patch('apps.knowledge.tasks.index_document_task.delay') as delay:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                promote_document(self.doc.id)

        self.assertEqual(len(callbacks), 1)
        delay.assert_called_once_with(self.doc.id)

    def test_no_index_dispatch_when_schedule_index_is_false(self):
        self._moderation()

        with patch('utils.celery_dispatcher.safe_delay') as safe_delay:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                promote_document(self.doc.id, schedule_index=False)

        self.assertEqual(callbacks, [])
        safe_delay.assert_not_called()

    def test_index_is_not_dispatched_before_commit(self):
        """回调未执行（事务未提交）时，索引绝不能已经跑起来。"""
        self._moderation()

        with patch('utils.celery_dispatcher.safe_delay') as safe_delay:
            with self.captureOnCommitCallbacks(execute=False):
                promote_document(self.doc.id)

            safe_delay.assert_not_called()


# ===========================================================================
# D. promote_document：失败路径（§六、§七）
# ===========================================================================

class PromotionFailureTests(_PromotionTestCase):

    def test_oss_error_marks_promotion_failed_and_never_promotes(self):
        self._moderation()
        self.bucket.copy_side_effect = oss2.exceptions.OssError(500, {}, b'boom', {})

        with patch('utils.celery_dispatcher.safe_delay') as safe_delay:
            with self.captureOnCommitCallbacks(execute=True):
                result = promote_document(self.doc.id)

        self.assertEqual(result.status, PromotionResult.STATUS_FAILED)
        moderation = ContentModeration.objects.get(doc=self.doc)
        self.assertEqual(moderation.storage_state, STORAGE_STATE_PROMOTION_FAILED)
        self.assertNotEqual(moderation.promotion_error, '')
        self.assertEqual(moderation.promotion_retry, 1)
        self.assertEqual(moderation.promoted_key, '')

        self.doc.refresh_from_db()
        self.assertEqual(self.doc.oss_key, QUARANTINE_KEY)
        self.assertNotEqual(self.doc.status, 'indexed')
        safe_delay.assert_not_called()

    def test_silent_copy_failure_is_caught_by_head_verification(self):
        """复制调用没报错但目标对象没出现——必须被判失败，不能当作成功。"""
        self._moderation()
        self.bucket.copy_transform = lambda payload: None

        result = promote_document(self.doc.id, schedule_index=False)

        self.assertEqual(result.status, PromotionResult.STATUS_FAILED)
        moderation = ContentModeration.objects.get(doc=self.doc)
        self.assertEqual(moderation.storage_state, STORAGE_STATE_PROMOTION_FAILED)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.oss_key, QUARANTINE_KEY)

    def test_source_missing_marks_promotion_failed(self):
        self._moderation()
        self.bucket.objects.pop(QUARANTINE_KEY)

        result = promote_document(self.doc.id, schedule_index=False)

        self.assertEqual(result.status, PromotionResult.STATUS_FAILED)
        moderation = ContentModeration.objects.get(doc=self.doc)
        self.assertEqual(moderation.storage_state, STORAGE_STATE_PROMOTION_FAILED)

    def test_unexpected_exception_still_fails_closed(self):
        """非 OSSOperationError 的意外异常同样不得放行。"""
        self._moderation()

        with patch.object(
            oss_client, 'promote_object', side_effect=RuntimeError('unexpected'),
        ):
            result = promote_document(self.doc.id, schedule_index=False)

        self.assertEqual(result.status, PromotionResult.STATUS_FAILED)
        moderation = ContentModeration.objects.get(doc=self.doc)
        self.assertEqual(moderation.storage_state, STORAGE_STATE_PROMOTION_FAILED)
        self.assertIn('RuntimeError', moderation.promotion_error)

    def test_promotion_never_writes_promoted_on_failure(self):
        """`storage_state=promoted` 只可能在 OSS 校验通过之后被写入。"""
        self._moderation()

        with patch.object(
            oss_client, 'promote_object',
            side_effect=OSSOperationError('注入的校验失败'),
        ):
            promote_document(self.doc.id, schedule_index=False)

        self.assertFalse(
            ContentModeration.objects.filter(
                doc=self.doc, storage_state=STORAGE_STATE_PROMOTED,
            ).exists()
        )


# ===========================================================================
# E. 幂等性（§十五）
# ===========================================================================

class PromotionIdempotencyTests(_PromotionTestCase):

    def test_second_call_short_circuits_without_copying_again(self):
        self._moderation()

        first = promote_document(self.doc.id, schedule_index=False)
        second = promote_document(self.doc.id, schedule_index=False)

        self.assertTrue(first.did_promote)
        self.assertEqual(second.status, PromotionResult.STATUS_ALREADY)
        self.assertFalse(second.did_promote)
        self.assertEqual(len(self.bucket.copy_calls), 1)
        # promoted 是吸收态：重复调用不得把 promotion_retry 继续推高
        self.assertEqual(
            ContentModeration.objects.get(doc=self.doc).promotion_retry, 1,
        )

    def test_retry_after_failure_succeeds_once(self):
        self._moderation()
        self.bucket.copy_side_effect = oss2.exceptions.OssError(500, {}, b'boom', {})

        failed = promote_document(self.doc.id, schedule_index=False)
        self.bucket.copy_side_effect = None
        recovered = promote_document(self.doc.id, schedule_index=False)

        self.assertEqual(failed.status, PromotionResult.STATUS_FAILED)
        self.assertEqual(recovered.status, PromotionResult.STATUS_PROMOTED)
        moderation = ContentModeration.objects.get(doc=self.doc)
        self.assertEqual(moderation.storage_state, STORAGE_STATE_PROMOTED)
        self.assertEqual(moderation.promotion_retry, 2)

    def test_retry_uses_the_recorded_quarantine_key(self):
        """quarantine_key 一旦记录，就以它为准（不重新从 doc.oss_key 猜）。"""
        self._moderation()
        self.bucket.copy_side_effect = oss2.exceptions.OssError(500, {}, b'boom', {})
        promote_document(self.doc.id, schedule_index=False)

        # 模拟"文档记录已被别处改写"，但审核记录里的源 Key 仍然指向原对象
        KnowledgeDoc.objects.filter(pk=self.doc.pk).update(oss_key='quarantine/x/y/z/w.pdf')
        self.bucket.copy_side_effect = None

        result = promote_document(self.doc.id, schedule_index=False)

        self.assertEqual(result.status, PromotionResult.STATUS_PROMOTED)
        _, src, _, _ = self.bucket.copy_calls[-1]
        self.assertEqual(src, QUARANTINE_KEY)

    def test_oss_copy_target_is_stable_across_retries(self):
        """同一文档的目标 Key 是确定性的，重试不会产生第二份正式区对象。"""
        self._moderation()
        self.bucket.copy_side_effect = oss2.exceptions.OssError(500, {}, b'boom', {})
        promote_document(self.doc.id, schedule_index=False)
        self.bucket.copy_side_effect = None

        promote_document(self.doc.id, schedule_index=False)

        targets = {call[2] for call in self.bucket.copy_calls}
        self.assertEqual(targets, {EXPECTED_PROMOTED_KEY})


# ===========================================================================
# F. 并发（§二十一）
# ===========================================================================

class PromotionConcurrencyTests(_PromotionTestCase):

    def test_fresh_promoting_claim_blocks_a_second_worker(self):
        """另一个 worker 正在提升（租约未过期）时，本次不得重复 copy。"""
        self._moderation(storage_state=STORAGE_STATE_PROMOTING)

        result = promote_document(self.doc.id, schedule_index=False)

        self.assertEqual(result.status, PromotionResult.STATUS_IN_PROGRESS)
        self.assertEqual(result.reason, 'claim_lost')
        self.assertEqual(self.bucket.copy_calls, [])

    def test_stale_promoting_claim_can_be_reclaimed(self):
        """持有者在 promoting 崩溃：租约过期后允许其他 worker 接手。"""
        moderation = self._moderation(storage_state=STORAGE_STATE_PROMOTING)
        stale = timezone.now() - timezone.timedelta(
            seconds=promotion_claim_timeout_seconds() + 60,
        )
        ContentModeration.objects.filter(pk=moderation.pk).update(updated_at=stale)

        result = promote_document(self.doc.id, schedule_index=False)

        self.assertEqual(result.status, PromotionResult.STATUS_PROMOTED)
        self.assertEqual(len(self.bucket.copy_calls), 1)

    def test_finalize_cas_lets_exactly_one_worker_win(self):
        """两个 worker 都通过了抢占，收尾 CAS 仍只能有一个推进到 promoted。"""
        self._moderation()
        moderation = ContentModeration.objects.get(doc=self.doc)

        def _steal_during_copy(src_key, dst_key, content_type=None):
            # 模拟另一个 worker 在本次 copy 期间抢先把状态推到 promoted
            ContentModeration.objects.filter(pk=moderation.pk).update(
                storage_state=STORAGE_STATE_PROMOTED,
                promoted_key=EXPECTED_PROMOTED_KEY,
            )
            return {
                'src_key': src_key, 'dst_key': dst_key, 'size': 4, 'etag': 'e',
                'content_type': 'text/plain', 'src_size': 4, 'verified': True,
            }

        with patch.object(
            oss_client, 'promote_object', side_effect=_steal_during_copy,
        ):
            result = promote_document(self.doc.id, schedule_index=False)

        self.assertEqual(result.status, PromotionResult.STATUS_IN_PROGRESS)
        self.assertEqual(result.reason, 'cas_lost')
        # 收尾 CAS 落空 → 绝不改 oss_key，绝不派发索引
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.oss_key, QUARANTINE_KEY)

    def test_cas_losing_worker_never_dispatches_index(self):
        self._moderation()
        moderation = ContentModeration.objects.get(doc=self.doc)

        def _steal(src_key, dst_key, content_type=None):
            ContentModeration.objects.filter(pk=moderation.pk).update(
                storage_state=STORAGE_STATE_PROMOTION_FAILED,
            )
            return {'dst_key': dst_key, 'verified': True, 'size': 1, 'src_size': 1}

        with patch.object(oss_client, 'promote_object', side_effect=_steal):
            with patch('utils.celery_dispatcher.safe_delay') as safe_delay:
                with self.captureOnCommitCallbacks(execute=True):
                    promote_document(self.doc.id)

        safe_delay.assert_not_called()


# ===========================================================================
# G. check_indexable 门禁矩阵（§九、§二十三）
# ===========================================================================

class CheckIndexableGateTests(_PromotionTestCase):
    """索引准入 = 正式区命名空间 ∧ 已提升 ∧ 审核白名单，三者缺一不可。"""

    def test_quarantine_object_is_denied_regardless_of_moderation(self):
        self._moderation(storage_state=STORAGE_STATE_PROMOTED)

        allowed, reason = check_indexable(self.doc)

        self.assertFalse(allowed)
        self.assertIn('知识库路径', reason)

    def test_official_object_without_moderation_record_is_denied(self):
        """历史文档 / 爬虫文档：无审核记录一律拒绝（有意选择 fail-closed）。"""
        doc = self._make_doc(oss_key=EXPECTED_PROMOTED_KEY)

        allowed, reason = check_indexable(doc)

        self.assertFalse(allowed)
        self.assertIn('审核记录', reason)

    def test_official_object_approved_but_not_promoted_is_denied(self):
        doc = self._make_doc(oss_key=EXPECTED_PROMOTED_KEY)
        self._moderation(doc=doc, storage_state=STORAGE_STATE_QUARANTINE)

        allowed, reason = check_indexable(doc)

        self.assertFalse(allowed)
        self.assertIn('尚未提升', reason)

    def test_official_object_promoted_but_rejected_is_denied(self):
        """storage_state 是存储事实，不能覆盖审核结论。"""
        doc = self._make_doc(oss_key=EXPECTED_PROMOTED_KEY)
        self._moderation(
            doc=doc, state=MODERATION_STATE_REJECTED,
            storage_state=STORAGE_STATE_PROMOTED,
        )

        allowed, reason = check_indexable(doc)

        self.assertFalse(allowed)
        self.assertIn(MODERATION_STATE_REJECTED, reason)

    def test_official_object_promoted_and_approved_is_allowed(self):
        doc = self._make_doc(oss_key=EXPECTED_PROMOTED_KEY)
        self._moderation(doc=doc, storage_state=STORAGE_STATE_PROMOTED)

        allowed, reason = check_indexable(doc)

        self.assertTrue(allowed, reason)

    def test_promotion_failed_storage_state_is_denied(self):
        doc = self._make_doc(oss_key=EXPECTED_PROMOTED_KEY)
        self._moderation(doc=doc, storage_state=STORAGE_STATE_PROMOTION_FAILED)

        allowed, _ = check_indexable(doc)

        self.assertFalse(allowed)

    def test_end_to_end_promotion_unlocks_indexing(self):
        """走完 promotion 之后同一个文档从"拒绝"翻转为"允许"。"""
        self._moderation()
        before, _ = check_indexable(self.doc)

        promote_document(self.doc.id, schedule_index=False)

        self.doc.refresh_from_db()
        after, reason = check_indexable(self.doc)
        self.assertFalse(before)
        self.assertTrue(after, reason)

    def test_index_gate_and_promotion_gate_are_the_same_predicate(self):
        doc = self._make_doc(oss_key=EXPECTED_PROMOTED_KEY)
        self._moderation(doc=doc, storage_state=STORAGE_STATE_PROMOTED)

        self.assertEqual(check_indexable(doc), is_document_indexable_by_moderation(doc))


class IndexTaskModerationGateTests(_PromotionTestCase):
    """索引任务体内也必须挂审核门（视图层之外还有多条调用路径）。"""

    def _run_index(self, doc_id):
        with patch('utils.celery_dispatcher.safe_delay'):
            return _call_task(index_document_task, doc_id)

    def test_document_without_moderation_is_marked_failed_not_indexed(self):
        self._make_doc(oss_key=EXPECTED_PROMOTED_KEY)  # 无审核记录的历史文档
        legacy = KnowledgeDoc.objects.get(oss_key=EXPECTED_PROMOTED_KEY)

        result = self._run_index(legacy.id)

        self.assertIsNone(result)
        legacy.refresh_from_db()
        self.assertEqual(legacy.status, 'failed')
        self.assertIn('审核记录', legacy.index_error)

    def test_rejected_document_never_indexes(self):
        self._moderation(state=MODERATION_STATE_REJECTED,
                         storage_state=STORAGE_STATE_PROMOTED)

        self._run_index(self.doc.id)

        self.doc.refresh_from_db()
        self.assertEqual(self.doc.status, 'failed')
        self.assertEqual(self.doc.oss_key, QUARANTINE_KEY)

    def test_approved_and_promoted_document_passes_the_gate(self):
        """门禁本身放行后，任务才会继续往下走（这里让它停在解析阶段即可）。"""
        doc = self._make_doc(oss_key=EXPECTED_PROMOTED_KEY)
        self._moderation(doc=doc, storage_state=STORAGE_STATE_PROMOTED)

        with patch('apps.knowledge.extractor.extract_text_from_oss', return_value='') as extract:
            with patch('utils.celery_dispatcher.safe_delay'):
                _call_task(index_document_task, doc.id)

        extract.assert_called_once()
        doc.refresh_from_db()
        self.assertEqual(doc.status, 'failed')          # 空文本 → failed
        self.assertIn('解析结果为空', doc.index_error)   # 但**不是**被审核门拦下


# ===========================================================================
# H. moderate_and_promote_task 编排（§十四）
# ===========================================================================

class ModerateAndPromoteTaskTests(_PromotionTestCase):

    def _patch_detectors(self, clamav=None, rule=None, llm=None, vision=None):
        for target, value in (
            ('apps.knowledge.detectors.ClamAVAdapter.scan_oss_key',
             clamav or _detector_clean('clamav')),
            ('apps.knowledge.detectors.RuleAdapter.check',
             rule or _detector_clean('rule')),
            ('apps.knowledge.detectors.LLMAdapter.check',
             llm or _detector_clean('llm')),
            ('apps.knowledge.detectors.VisionAdapter.check_image',
             vision or _detector_skipped('vision')),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run_task(self):
        with patch('utils.celery_dispatcher.safe_delay') as safe_delay:
            with self.captureOnCommitCallbacks(execute=True):
                _call_task(moderate_and_promote_task, self.doc.id)
        return safe_delay

    def test_clean_document_is_approved_promoted_and_indexed(self):
        self._patch_detectors()

        safe_delay = self._run_task()

        moderation = ContentModeration.objects.get(doc=self.doc)
        self.assertEqual(moderation.state, MODERATION_STATE_APPROVED)
        self.assertEqual(moderation.storage_state, STORAGE_STATE_PROMOTED)
        self.assertIn(EXPECTED_PROMOTED_KEY, self.bucket.objects)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.oss_key, EXPECTED_PROMOTED_KEY)
        safe_delay.assert_called_once()
        self.assertIs(safe_delay.call_args.args[0], index_document_task)
        self.assertEqual(safe_delay.call_args.args[1], self.doc.id)

    def test_virus_document_is_rejected_and_left_in_quarantine(self):
        self._patch_detectors(clamav=DetectorResult.ok('clamav', VERDICT_VIRUS))

        safe_delay = self._run_task()

        moderation = ContentModeration.objects.get(doc=self.doc)
        self.assertEqual(moderation.state, MODERATION_STATE_REJECTED)
        self.assertEqual(moderation.storage_state, STORAGE_STATE_QUARANTINE)
        self.assertEqual(self.bucket.copy_calls, [])
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.oss_key, QUARANTINE_KEY)
        self.assertNotEqual(self.doc.status, 'indexed')
        safe_delay.assert_not_called()

    def test_unavailable_detector_never_promotes(self):
        self._patch_detectors(
            llm=DetectorResult.unavailable('llm', reason='测试', error='不可用'),
        )

        safe_delay = self._run_task()

        moderation = ContentModeration.objects.get(doc=self.doc)
        self.assertEqual(moderation.state, MODERATION_STATE_UNAVAILABLE)
        self.assertEqual(self.bucket.copy_calls, [])
        safe_delay.assert_not_called()

    def test_document_without_oss_key_aborts_without_a_moderation_record(self):
        doc = self._make_doc(oss_key='')
        self.doc = doc
        self._patch_detectors()

        safe_delay = self._run_task()

        self.assertEqual(ContentModeration.objects.count(), 0)
        self.assertEqual(ModerationAttempt.objects.count(), 0)
        self.assertEqual(self.bucket.copy_calls, [])
        safe_delay.assert_not_called()

    def test_second_run_does_not_copy_again(self):
        """promoted 是吸收态：重复进入绝不重复 copy、绝不产生第二份正式区对象。"""
        self._patch_detectors()
        self._run_task()

        with patch('utils.celery_dispatcher.safe_delay'):
            with self.captureOnCommitCallbacks(execute=True):
                _call_task(moderate_and_promote_task, self.doc.id)

        self.assertEqual(len(self.bucket.copy_calls), 1)
        self.assertEqual(
            sorted(k for k in self.bucket.objects if k.startswith(OFFICIAL_KEY_PREFIX)),
            [EXPECTED_PROMOTED_KEY],
        )

    def test_already_indexed_document_is_not_re_dispatched(self):
        """已 indexed 的文档即使再次进入编排，也不得重复派发索引。"""
        self._patch_detectors()
        self._run_task()
        KnowledgeDoc.objects.filter(pk=self.doc.pk).update(status='indexed')

        with patch('utils.celery_dispatcher.safe_delay') as safe_delay:
            with self.captureOnCommitCallbacks(execute=True):
                _call_task(moderate_and_promote_task, self.doc.id)

        safe_delay.assert_not_called()

    def test_already_promoted_but_unindexed_is_re_dispatched(self):
        """恢复路径：提升成功但派发丢失时，再次进入可补齐索引。"""
        self._patch_detectors()
        self._run_task()
        # 抹掉"已经派发过"的痕迹：把文档退回 uploaded
        KnowledgeDoc.objects.filter(pk=self.doc.pk).update(status='uploaded')

        with patch('utils.celery_dispatcher.safe_delay') as safe_delay:
            with self.captureOnCommitCallbacks(execute=True):
                _call_task(moderate_and_promote_task, self.doc.id)

        safe_delay.assert_called_once()
        self.assertIs(safe_delay.call_args.args[0], index_document_task)

    def test_missing_document_is_a_no_op(self):
        with patch('utils.celery_dispatcher.safe_delay') as safe_delay:
            with self.captureOnCommitCallbacks(execute=True):
                _call_task(moderate_and_promote_task, 999999)

        safe_delay.assert_not_called()
        self.assertEqual(ContentModeration.objects.count(), 0)


# ===========================================================================
# I. retry_failed_moderations_task（§十六）
# ===========================================================================

class RetryFailedModerationsTaskTests(_PromotionTestCase):

    def _due(self, state=MODERATION_STATE_UNAVAILABLE, **overrides):
        defaults = dict(
            doc=self.doc, state=state, storage_state=STORAGE_STATE_QUARANTINE,
            attempt_count=1,
            next_retry_at=timezone.now() - timezone.timedelta(seconds=1),
        )
        defaults.update(overrides)
        return ContentModeration.objects.create(**defaults)

    def test_due_unavailable_document_is_re_dispatched(self):
        self._due()

        with patch('utils.celery_dispatcher.safe_delay') as safe_delay:
            _call_task(retry_failed_moderations_task)

        safe_delay.assert_called_once()
        self.assertIs(safe_delay.call_args.args[0], moderate_and_promote_task)
        self.assertEqual(safe_delay.call_args.args[1], self.doc.id)

    def test_due_error_document_is_re_dispatched(self):
        self._due(state=MODERATION_STATE_ERROR)

        with patch('utils.celery_dispatcher.safe_delay') as safe_delay:
            _call_task(retry_failed_moderations_task)

        safe_delay.assert_called_once()

    def test_terminal_states_are_never_re_run(self):
        """approved / rejected 是终态，重试器不得触碰（更不得"重审后放行"）。"""
        for state in (MODERATION_STATE_APPROVED, MODERATION_STATE_REJECTED,
                      MODERATION_STATE_PENDING, MODERATION_STATE_SCANNING):
            with self.subTest(state=state):
                ContentModeration.objects.all().delete()
                self._due(state=state)

                with patch('utils.celery_dispatcher.safe_delay') as safe_delay:
                    _call_task(retry_failed_moderations_task)

                safe_delay.assert_not_called()

    def test_future_next_retry_at_is_not_due_yet(self):
        self._due(next_retry_at=timezone.now() + timezone.timedelta(hours=1))

        with patch('utils.celery_dispatcher.safe_delay') as safe_delay:
            _call_task(retry_failed_moderations_task)

        safe_delay.assert_not_called()

    def test_attempts_at_the_cap_are_left_alone(self):
        """达到上限的文档保持 unavailable / error，绝不自动 approved。"""
        from .moderation import max_attempts

        self._due(attempt_count=max_attempts())

        with patch('utils.celery_dispatcher.safe_delay') as safe_delay:
            _call_task(retry_failed_moderations_task)

        safe_delay.assert_not_called()
        self.assertEqual(
            ContentModeration.objects.get(doc=self.doc).state,
            MODERATION_STATE_UNAVAILABLE,
        )

    def test_claim_pushes_next_retry_at_to_avoid_double_dispatch(self):
        """CAS 抢占：重复运行的重试器不会对同一文档重复派发。"""
        moderation = self._due()

        with patch('utils.celery_dispatcher.safe_delay') as safe_delay:
            _call_task(retry_failed_moderations_task)
            _call_task(retry_failed_moderations_task)

        safe_delay.assert_called_once()
        moderation.refresh_from_db()
        self.assertGreater(moderation.next_retry_at, timezone.now())

    def test_no_due_documents_is_a_no_op(self):
        with patch('utils.celery_dispatcher.safe_delay') as safe_delay:
            self.assertIsNone(_call_task(retry_failed_moderations_task))

        safe_delay.assert_not_called()

    def test_retry_never_promotes_on_its_own(self):
        """重试器只有"重新派发审核"这一条路径，没有任何放行分支。"""
        self._due()

        with patch('utils.celery_dispatcher.safe_delay'):
            _call_task(retry_failed_moderations_task)

        moderation = ContentModeration.objects.get(doc=self.doc)
        self.assertEqual(moderation.storage_state, STORAGE_STATE_QUARANTINE)
        self.assertEqual(self.bucket.copy_calls, [])
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.oss_key, QUARANTINE_KEY)


# ===========================================================================
# J. SSE 出口纵深防御（§十八）
# ===========================================================================

class SSEQuarantineGateTests(_PromotionTestCase):

    def test_references_detail_does_not_sign_a_quarantine_key(self):
        from utils.sse import build_references_events

        doc = self._make_doc(
            oss_key=QUARANTINE_KEY, status='indexed', visibility='public',
        )

        with patch.object(oss_client, 'get_download_url') as signer:
            events = build_references_events(
                [{'doc_id': doc.id, 'score': 0.9, 'content': '片段'}],
                'super_admin', user_id=self.user.id,
            )

        signer.assert_not_called()
        entry = events['references_detail'][0]
        self.assertEqual(entry.get('download_url', ''), '')

    def test_references_detail_signs_an_official_key(self):
        from utils.sse import build_references_events

        doc = self._make_doc(
            oss_key=EXPECTED_PROMOTED_KEY, status='indexed', visibility='public',
        )

        with patch.object(
            oss_client, 'get_download_url', return_value='https://signed',
        ) as signer:
            events = build_references_events(
                [{'doc_id': doc.id, 'score': 0.9, 'content': '片段'}],
                'super_admin', user_id=self.user.id,
            )

        signer.assert_called_once()
        self.assertEqual(events['references_detail'][0]['download_url'], 'https://signed')

    def test_images_event_does_not_sign_a_quarantine_key(self):
        from utils.sse import build_images_events

        with patch.object(oss_client, 'get_preview_url') as signer:
            events = build_images_events([
                {'doc_id': 1, 'doc_title': '图', 'oss_key': QUARANTINE_KEY},
            ])

        signer.assert_not_called()
        self.assertEqual(events['images'][0]['preview_url'], '')

    def test_images_event_signs_an_official_key(self):
        from utils.sse import build_images_events

        with patch.object(
            oss_client, 'get_preview_url', return_value='https://preview',
        ) as signer:
            events = build_images_events([
                {'doc_id': 1, 'doc_title': '图', 'oss_key': EXPECTED_PROMOTED_KEY},
            ])

        signer.assert_called_once()
        self.assertEqual(events['images'][0]['preview_url'], 'https://preview')

    def test_empty_key_is_never_signed(self):
        from utils.sse import build_images_events

        with patch.object(oss_client, 'get_preview_url') as signer:
            events = build_images_events([{'doc_id': 1, 'oss_key': ''}])

        signer.assert_not_called()
        self.assertEqual(events['images'][0]['preview_url'], '')
