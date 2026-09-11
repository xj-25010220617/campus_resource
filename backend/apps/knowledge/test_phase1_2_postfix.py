"""P1-2 Phase 1-2：STEP 4-B Post-Fix / 运维安全与生产部署止血 —— 回归测试。

对应用户实施要求 §三十二的测试清单，一个文件覆盖七组改动：

    P-1 / P-9  运维脚本安全化（reindex_tool：dry-run 只读、预检 fail-closed、
               --force 不绕审核门、不把不可索引文档写成 failed）
    P-2        人工确认放行（唯一写入方 confirmation.py 的全部拒绝分支）
    P-3        RAG 读取侧审核门（白名单 × storage_state 矩阵）
    P-4        上传链与审核链解耦（dispatch_async 绝不降级同步执行 + 补扫恢复）
    P-5        部署编排（端口统一 / worker / beat 注册的任务名）
    P-6        settings 生产安全（DEBUG / ALLOWED_HOSTS / CORS fail-closed）
    P-7        向量后端不可用的可观测性（None ≠ []）

测试原则：不连接任何真实外部依赖。OSS / Milvus / Celery 全部替身，
不访问网络。任何一条安全逻辑都不被 mock 掉——被替身化的是**外部服务**，
不是**判定本身**。
"""
import importlib.util
import os
import re
import unittest
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import College, User
from utils.oss import QUARANTINE_KEY_PREFIX, oss_client

from .models import ContentModeration, DocChunk, KnowledgeDoc
from .moderation import (
    MODERATION_STATE_APPROVED,
    MODERATION_STATE_CONFIRMED_UNCHECKED,
    MODERATION_STATE_ERROR,
    MODERATION_STATE_PENDING,
    MODERATION_STATE_REJECTED,
    MODERATION_STATE_UNAVAILABLE,
    STORAGE_STATE_PROMOTED,
    STORAGE_STATE_PROMOTION_FAILED,
    STORAGE_STATE_QUARANTINE,
)

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SETTINGS_PATH = os.path.join(BACKEND_DIR, 'config', 'settings.py')


# ===========================================================================
# 公共夹具
# ===========================================================================

class _Base(TestCase):

    def setUp(self):
        self.college = College.objects.create(name='计算机与软件工程学院', code='CS')
        self.admin = User.objects.create_user(
            username='pf_admin', password='testpass123',
            role='super_admin', college=self.college,
        )
        self.plain = User.objects.create_user(
            username='pf_user', password='testpass123', role='user',
        )

    def _doc(self, *, oss_key='quarantine/college/1/2026-09/abc.txt',
             status='uploaded', **kw):
        return KnowledgeDoc.objects.create(
            title=kw.pop('title', '跳绳项目工作报告'),
            description='测试文档',
            source_type='upload',
            file_name=kw.pop('file_name', 'report.txt'),
            file_type='txt',
            uploader=kw.pop('uploader', self.admin),
            oss_key=oss_key,
            status=status,
            **kw,
        )

    def _moderation(self, doc, **kw):
        defaults = dict(
            state=MODERATION_STATE_UNAVAILABLE,
            storage_state=STORAGE_STATE_QUARANTINE,
            quarantine_key=doc.oss_key or '',
        )
        defaults.update(kw)
        return ContentModeration.objects.create(doc=doc, **defaults)


def _celery_installed() -> bool:
    """本地 venv 是否装了 celery（它是 requirements.txt 里的可选依赖）。"""
    try:
        import celery  # noqa: F401
    except ImportError:
        return False
    return True


#: "调用方没指定" 的哨兵——与"显式传入 None（OSS 不可验证）"区分开
_DEFAULT_OSS = object()


class _OssStub:
    """`head_object` 替身：按预设返回 {exists: bool} 或 None（不可验证）。"""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, key):
        self.calls.append(key)
        return self.result


# ===========================================================================
# P-4：dispatch_async 只入队、绝不降级同步执行
# ===========================================================================

class DispatchAsyncTests(_Base):
    """`utils.celery_dispatcher.dispatch_async` 的语义（审计报告 P-4）。"""

    def test_returns_delay_result_on_success(self):
        from utils.celery_dispatcher import dispatch_async

        sentinel = object()

        class _Task:
            name = 'demo.task'

            def delay(self, *a, **kw):
                return sentinel

        self.assertIs(dispatch_async(_Task(), 7), sentinel)

    def test_inline_fallback_task_is_refused_not_executed(self):
        """未安装 Celery 时 `delay` 是就地同步执行的 runner——必须拒绝。"""
        from utils.celery_dispatcher import DispatchUnavailable, dispatch_async

        executed = []

        class _Task:
            name = 'demo.inline'

            def delay(self, *a, **kw):
                executed.append(a)      # 若被调用，就是"静默同步跑完"

        _Task.delay._inline_fallback = True

        with self.assertRaises(DispatchUnavailable):
            dispatch_async(_Task(), 1)

        self.assertEqual(executed, [], '降级形态的任务体绝不能被就地执行')

    def test_broker_failure_is_refused_not_executed(self):
        """broker 不可用 → 抛 DispatchUnavailable，任务体一次都不跑。"""
        from utils.celery_dispatcher import DispatchUnavailable, dispatch_async

        body_ran = []

        class _Task:
            name = 'demo.broker_down'

            def delay(self, *a, **kw):
                raise RuntimeError('broker unavailable')

            def run(self, *a, **kw):        # 任务体
                body_ran.append(a)

        with self.assertRaises(DispatchUnavailable):
            dispatch_async(_Task(), 1)

        self.assertEqual(body_ran, [], '派发失败时任务体绝不能被执行')

    def test_safe_delay_still_degrades(self):
        """`safe_delay` 的降级能力必须保留（其它调用方依赖它）。"""
        from utils.celery_dispatcher import safe_delay

        ran = []

        class _Task:
            name = 'demo.sync_ok'

            def delay(self, *a, **kw):
                raise RuntimeError('broker unavailable')

            def __call__(self, *a, **kw):
                ran.append(a)

        safe_delay(_Task(), 3)
        self.assertEqual(ran, [(3,)], 'safe_delay 必须仍然降级为同步执行')


class ScheduleModerationTests(_Base):
    """`upload_service.schedule_moderation()` 的两种结果。

    派发发生在 `transaction.on_commit()` 里，而 `TestCase` 全程包在事务中、
    回调默认不执行，因此必须用 `captureOnCommitCallbacks(execute=True)` 捕获
    并执行——否则观察到的永远是"没派发"，得到一个恒为假的空断言。
    """

    def _schedule(self, doc_id, dispatch_effect):
        """在"on_commit 立即执行"的语义下调用 schedule_moderation。

        `TestCase` 全程包在事务里，`transaction.on_commit()` 默认被推迟到
        （永不发生的）提交时执行，此时 `schedule_moderation` 的返回值只可能
        是"已安排"的 True，观察不到真实结果。这里把 `on_commit` 换成直接
        调用——正是"无外层事务"时 Django 的真实行为，也是上传入口的实际语义。
        """
        from apps.knowledge import upload_service

        with patch('utils.celery_dispatcher.dispatch_async',
                   side_effect=dispatch_effect) as spy, \
                patch('django.db.transaction.on_commit',
                      side_effect=lambda fn, **kw: fn()):
            ok = upload_service.schedule_moderation(doc_id)
        return ok, spy

    def test_returns_true_when_dispatched(self):
        doc = self._doc()
        ok, spy = self._schedule(doc.id, dispatch_effect=None)
        self.assertTrue(ok)
        spy.assert_called_once()
        self.assertEqual(spy.call_args.args[1], doc.id)

    def test_queue_down_leaves_document_untouched(self):
        """队列不可用：上传仍成功，文档保持未审核、对象仍在隔离区。"""
        from utils.celery_dispatcher import DispatchUnavailable

        doc = self._doc()
        ok, _ = self._schedule(
            doc.id, dispatch_effect=DispatchUnavailable('队列不可用'),
        )

        self.assertFalse(ok, '入队失败必须返回 False，不能假装成功')
        doc.refresh_from_db()
        self.assertEqual(doc.status, 'uploaded', '文档不得被就地推进状态')
        self.assertEqual(ContentModeration.objects.count(), 0,
                         '队列不可用期间绝不能产生审核记录')
        self.assertTrue(doc.oss_key.startswith(QUARANTINE_KEY_PREFIX))

    def test_queue_down_does_not_raise(self):
        """入队失败不得把上传请求打成 500。"""
        from utils.celery_dispatcher import DispatchUnavailable

        doc = self._doc()
        from apps.knowledge import upload_service

        with patch('utils.celery_dispatcher.dispatch_async',
                   side_effect=DispatchUnavailable('boom')), \
                patch('django.db.transaction.on_commit',
                      side_effect=lambda fn, **kw: fn()):
            upload_service.schedule_moderation(doc.id)   # 不抛异常即通过


class RecoverUndispatchedUploadsTests(_Base):
    """`_recover_undispatched_uploads()`：队列恢复后的补扫（P-4 恢复路径）。"""

    def _run(self, dispatch=None):
        from .tasks import _recover_undispatched_uploads

        calls = []

        def _dispatch(task, doc_id):
            calls.append((task, doc_id))

        n = _recover_undispatched_uploads(
            50, timezone.now(), dispatch or _dispatch,
        )
        return n, calls

    def _age(self, doc, seconds):
        """把 created_at 拨老，越过补扫宽限期。"""
        KnowledgeDoc.objects.filter(pk=doc.pk).update(
            created_at=timezone.now() - timedelta(seconds=seconds),
        )

    def test_picks_quarantine_doc_without_moderation(self):
        from .tasks import moderate_and_promote_task

        doc = self._doc()
        self._age(doc, 10_000)

        n, calls = self._run()

        self.assertEqual(n, 1)
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][0], moderate_and_promote_task)
        self.assertEqual(calls[0][1], doc.id)

    def test_fresh_documents_are_left_alone(self):
        """宽限期内的文档不动——避免和正在入队的请求抢同一份。"""
        doc = self._doc()
        n, calls = self._run()
        self.assertEqual((n, calls), (0, []))
        self.assertIsNotNone(doc.pk)

    def test_document_with_moderation_is_skipped(self):
        doc = self._doc()
        self._age(doc, 10_000)
        self._moderation(doc, state=MODERATION_STATE_PENDING,
                         storage_state=STORAGE_STATE_QUARANTINE)

        n, calls = self._run()
        self.assertEqual((n, calls), (0, []),
                         '已有审核记录的文档不属于"入队丢失"')

    def test_official_key_document_is_skipped(self):
        """正式区（knowledge/）的历史文档绝不进补扫——本阶段不做历史回填。"""
        doc = self._doc(oss_key='knowledge/college/1/2026-09/old.txt')
        self._age(doc, 10_000)

        n, calls = self._run()
        self.assertEqual((n, calls), (0, []), '补扫只碰 quarantine/ 对象')

    def test_indexed_document_is_skipped(self):
        doc = self._doc(status='indexed')
        self._age(doc, 10_000)

        n, calls = self._run()
        self.assertEqual((n, calls), (0, []))

    def test_recovery_never_approves(self):
        """补扫只重新排队，不得写任何审核记录、不得改文档状态。"""
        doc = self._doc()
        self._age(doc, 10_000)
        before = KnowledgeDoc.objects.get(pk=doc.pk).status

        self._run()

        self.assertEqual(ContentModeration.objects.count(), 0)
        self.assertEqual(KnowledgeDoc.objects.get(pk=doc.pk).status, before)


# ===========================================================================
# P-1 / P-9：运维重索引工具
# ===========================================================================

class ReindexDryRunTests(_Base):
    """`--dry-run` 必须纯只读（审计报告 P-1、P-9）。"""

    def setUp(self):
        super().setUp()
        from .moderation import VERDICT_CLEAN

        self.ok_doc = self._doc(
            oss_key='knowledge/college/1/2026-09/ok.txt', status='indexed',
        )
        self._moderation(
            self.ok_doc, state=MODERATION_STATE_APPROVED,
            verdict=VERDICT_CLEAN, storage_state=STORAGE_STATE_PROMOTED,
        )
        DocChunk.objects.create(
            doc=self.ok_doc, chunk_index=0, content='既有分块',
            token_count=4, vector_id='doc_x_chunk_0',
        )
        self.legacy = self._doc(
            oss_key='knowledge/college/1/2026-09/legacy.txt', status='indexed',
        )
        self.quarantined = self._doc(status='uploaded')

    def test_dry_run_writes_nothing(self):
        from .reindex_tool import run_reindex

        lines = []
        with patch('utils.celery_dispatcher.safe_delay') as safe_delay, \
                patch('utils.celery_dispatcher.dispatch_async') as dispatch:
            result = run_reindex(dry_run=True, out=lines.append)

        safe_delay.assert_not_called()
        dispatch.assert_not_called()
        self.assertEqual(result['processed'], 0)
        # 分块没被删、文档状态没被改、没有新增审核记录
        self.assertEqual(DocChunk.objects.count(), 1)
        self.assertEqual(
            KnowledgeDoc.objects.get(pk=self.ok_doc.pk).status, 'indexed',
        )
        self.assertEqual(ContentModeration.objects.count(), 1)

    def test_stats_match_expected_buckets(self):
        from .reindex_tool import collect_stats

        stats = collect_stats()
        self.assertEqual(stats['total'], 3)
        self.assertEqual(stats['indexed'], 2)
        self.assertEqual(stats['uploaded'], 1)
        self.assertEqual(stats['has_moderation'], 1)
        self.assertEqual(stats['no_moderation'], 2)
        self.assertEqual(stats['approved'], 1)
        self.assertEqual(stats['promoted'], 1)
        self.assertEqual(stats['not_promoted'], 0)
        self.assertEqual(stats['indexable'], 1)
        self.assertEqual(stats['not_indexable'], 2)
        self.assertIn('approved', stats['indexable_states'])

    def test_dry_run_report_mentions_blocked_reasons(self):
        from .reindex_tool import collect_stats, format_stats

        text = format_stats(collect_stats())
        self.assertIn('无 ContentModeration 记录', text)
        self.assertIn('不可索引', text)


class ReindexPreflightTests(_Base):
    """默认模式 fail-closed；`--force` 也不绕过审核门（审计报告 P-1）。"""

    def setUp(self):
        super().setUp()
        self.legacy = self._doc(
            oss_key='knowledge/college/1/2026-09/legacy.txt', status='indexed',
        )

    def test_default_mode_refuses_when_any_doc_blocked(self):
        from .reindex_tool import ReindexBlocked, run_reindex

        with self.assertRaises(ReindexBlocked):
            run_reindex(dry_run=False, out=lambda *_: None)

    def test_refusal_leaves_data_untouched(self):
        from .reindex_tool import ReindexBlocked, run_reindex

        with patch('utils.celery_dispatcher.safe_delay') as safe_delay:
            with self.assertRaises(ReindexBlocked):
                run_reindex(dry_run=False, out=lambda *_: None)

        safe_delay.assert_not_called()
        self.assertEqual(self.legacy.status,
                         KnowledgeDoc.objects.get(pk=self.legacy.pk).status)

    def test_blocked_document_is_never_marked_failed(self):
        """P-9 的核心：不可索引 ≠ 索引失败，不得写 failed。"""
        from .reindex_tool import ReindexBlocked, run_reindex

        try:
            run_reindex(dry_run=False, out=lambda *_: None)
        except ReindexBlocked:
            pass

        doc = KnowledgeDoc.objects.get(pk=self.legacy.pk)
        self.assertNotEqual(doc.status, 'failed')
        self.assertEqual(doc.index_error, '')

    def test_force_still_skips_non_indexable_documents(self):
        """`--force` 只影响"要不要重做已 indexed 的"，不解除审核门。"""
        from .reindex_tool import run_reindex

        dispatched = []
        with patch('utils.celery_dispatcher.safe_delay') as safe_delay:
            safe_delay.side_effect = lambda task, doc_id: dispatched.append(doc_id)
            run_reindex(
                dry_run=False, force=True,
                dispatch=lambda doc: safe_delay('t', doc.id),
                out=lambda *_: None,
            )

        self.assertNotIn(
            self.legacy.id, dispatched,
            '未审核的历史文档在 --force 下同样不得被处理',
        )

    def test_force_reindexes_gate_passing_document(self):
        from .moderation import VERDICT_CLEAN
        from .reindex_tool import run_reindex

        doc = self._doc(
            oss_key='knowledge/college/1/2026-09/ok.txt', status='indexed',
        )
        self._moderation(
            doc, state=MODERATION_STATE_APPROVED, verdict=VERDICT_CLEAN,
            storage_state=STORAGE_STATE_PROMOTED,
        )

        processed = []
        run_reindex(
            dry_run=False, force=True, out=lambda *_: None,
            dispatch=lambda d: processed.append(d.id),
        )
        self.assertEqual(processed, [doc.id])

    def test_default_mode_skips_already_indexed(self):
        """不带 --force 时，已 indexed 的文档不必重做，也不算失败。"""
        from .moderation import VERDICT_CLEAN
        from .reindex_tool import collect_preflight

        doc = self._doc(
            oss_key='knowledge/college/1/2026-09/ok.txt', status='indexed',
        )
        self._moderation(
            doc, state=MODERATION_STATE_APPROVED, verdict=VERDICT_CLEAN,
            storage_state=STORAGE_STATE_PROMOTED,
        )

        eligible, blocked = collect_preflight(include_indexed=False)
        self.assertEqual([d.id for d, _ in eligible], [])
        self.assertEqual({d.id for d, _ in blocked}, {self.legacy.id})

    def test_tool_has_no_delete_or_update_calls(self):
        """静态不变量：工具模块里不得出现任何 `.delete()` / `.update()` 调用。

        用 AST 而不是文本搜索：docstring 里**刻意**引用了旧脚本的破坏性命令
        作为对照说明，文本搜索会把注释也当成代码。
        """
        import ast

        from . import reindex_tool

        tree = ast.parse(open(reindex_tool.__file__, encoding='utf-8').read())
        offenders = sorted({
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {'delete', 'update', 'bulk_create'}
        })
        self.assertEqual(
            offenders, [],
            f'重索引工具不得包含批量破坏性调用，发现：{offenders}',
        )


# ===========================================================================
# P-2：人工确认放行
# ===========================================================================

class ManualConfirmationTests(_Base):

    def setUp(self):
        super().setUp()
        self.doc = self._doc()
        self.moderation = self._moderation(self.doc)

    def _confirm(self, head_result=_DEFAULT_OSS, **kw):
        from .confirmation import confirm_unchecked_document

        stub = _OssStub(
            {'exists': True} if head_result is _DEFAULT_OSS else head_result,
        )
        with patch.object(oss_client, 'head_object', stub), \
                patch('utils.celery_dispatcher.safe_delay') as safe_delay:
            with self.captureOnCommitCallbacks(execute=True):
                result = confirm_unchecked_document(
                    kw.pop('doc', self.doc),
                    actor=kw.pop('actor', self.admin),
                    reason=kw.pop('reason',
                                  'ClamAV 故障期间人工复核，已下载检查无毒'),
                    **kw,
                )
        return result, stub, safe_delay

    # ---- 拒绝分支 ---------------------------------------------------

    def test_anonymous_is_refused(self):
        from .confirmation import ConfirmationError

        with self.assertRaises(ConfirmationError) as ctx:
            self._confirm(actor=None)
        self.assertEqual(ctx.exception.reason, 'not_authenticated')

    def test_non_admin_is_refused(self):
        from .confirmation import ConfirmationError

        with self.assertRaises(ConfirmationError) as ctx:
            self._confirm(actor=self.plain)
        self.assertEqual(ctx.exception.reason, 'not_admin')
        self.moderation.refresh_from_db()
        self.assertEqual(self.moderation.state, MODERATION_STATE_UNAVAILABLE)

    def test_empty_reason_is_refused(self):
        from .confirmation import ConfirmationError

        with self.assertRaises(ConfirmationError) as ctx:
            self._confirm(reason='   ')
        self.assertEqual(ctx.exception.reason, 'reason_required')

    def test_document_without_moderation_is_refused(self):
        """历史文档（无审核记录）不属于本入口——P-8 是独立阶段。"""
        from .confirmation import ConfirmationError

        legacy = self._doc(oss_key='knowledge/college/1/2026-09/legacy.txt')
        with self.assertRaises(ConfirmationError) as ctx:
            self._confirm(doc=legacy)
        self.assertEqual(ctx.exception.reason, 'no_moderation')

    def test_rejected_document_can_never_be_confirmed(self):
        from .confirmation import ConfirmationError

        self.moderation.state = MODERATION_STATE_REJECTED
        self.moderation.save(update_fields=['state'])

        with self.assertRaises(ConfirmationError) as ctx:
            self._confirm()
        self.assertEqual(ctx.exception.reason, 'not_confirmable_state')

        self.moderation.refresh_from_db()
        self.assertEqual(self.moderation.state, MODERATION_STATE_REJECTED)

    def test_error_state_is_not_confirmable(self):
        """error 是"程序异常"，应先由重试器处理，人工不直接覆盖。"""
        from .confirmation import ConfirmationError

        self.moderation.state = MODERATION_STATE_ERROR
        self.moderation.save(update_fields=['state'])

        with self.assertRaises(ConfirmationError) as ctx:
            self._confirm()
        self.assertEqual(ctx.exception.reason, 'not_confirmable_state')

    def test_non_quarantine_key_is_refused(self):
        from .confirmation import ConfirmationError

        self.moderation.quarantine_key = 'knowledge/college/1/2026-09/x.txt'
        self.moderation.save(update_fields=['quarantine_key'])

        with self.assertRaises(ConfirmationError) as ctx:
            self._confirm()
        self.assertEqual(ctx.exception.reason, 'not_quarantine_key')

    def test_missing_object_is_refused(self):
        from .confirmation import ConfirmationError

        with self.assertRaises(ConfirmationError) as ctx:
            self._confirm(head_result={'exists': False})
        self.assertEqual(ctx.exception.reason, 'object_missing')

        self.moderation.refresh_from_db()
        self.assertEqual(self.moderation.state, MODERATION_STATE_UNAVAILABLE)

    def test_unverifiable_object_is_refused(self):
        """OSS 查不到 ≠ 对象存在（fail-closed）。"""
        from .confirmation import ConfirmationError

        with self.assertRaises(ConfirmationError) as ctx:
            self._confirm(head_result=None)
        self.assertEqual(ctx.exception.reason, 'object_unverifiable')

    # ---- 成功路径 ---------------------------------------------------

    def test_success_writes_audit_triple(self):
        result, stub, safe_delay = self._confirm()

        self.assertEqual(result.state, MODERATION_STATE_CONFIRMED_UNCHECKED)
        self.assertEqual(result.confirmed_by, self.admin)
        self.assertIsNotNone(result.confirmed_at)
        self.assertIn('人工复核', result.confirmation_reason)
        self.assertEqual(stub.calls, [self.doc.oss_key],
                         '必须真的去 OSS 核实过对象存在')

    def test_success_dispatches_orchestration_not_index(self):
        """§9：确认后必须走 promotion，绝不能直接索引。"""
        from .tasks import index_document_task, moderate_and_promote_task

        _, _, safe_delay = self._confirm()

        safe_delay.assert_called_once()
        self.assertIs(safe_delay.call_args.args[0], moderate_and_promote_task)
        self.assertEqual(safe_delay.call_args.args[1], self.doc.id)
        self.assertIsNot(
            safe_delay.call_args.args[0], index_document_task,
            '人工确认不得绕过 promotion 直接派发索引',
        )

    def test_confirmed_state_is_terminal_for_orchestrator(self):
        """前提固定：confirmed_unchecked 属于终态，编排任务不会重跑检测链。"""
        from .moderation import TERMINAL_MODERATION_STATES

        self.assertIn(
            MODERATION_STATE_CONFIRMED_UNCHECKED, TERMINAL_MODERATION_STATES,
            'confirmation.py 依赖该前提，一旦被改动必须同步重新设计确认流程',
        )

    def test_confirmed_state_is_indexable(self):
        from .moderation import is_indexable_moderation_state

        self.assertTrue(
            is_indexable_moderation_state(MODERATION_STATE_CONFIRMED_UNCHECKED),
        )

    def test_second_confirmation_loses_cas(self):
        from .confirmation import ConfirmationError

        self._confirm()
        with self.assertRaises(ConfirmationError) as ctx:
            self._confirm()
        self.assertEqual(ctx.exception.reason, 'not_confirmable_state')


# ===========================================================================
# P-3：RAG 读取侧审核门
# ===========================================================================

class RagModerationGateTests(_Base):
    """`filter_indexable_docs()` 的完整门禁矩阵（白名单 × storage_state）。"""

    def _gate_ids(self):
        from .moderation import filter_indexable_docs

        return set(
            filter_indexable_docs(KnowledgeDoc.objects.all())
            .values_list('id', flat=True)
        )

    def test_no_moderation_record_is_not_retrievable(self):
        """没有审核记录 = 未审核 = 不进 RAG（fail-closed，无 status 兜底）。"""
        self._doc(status='indexed')
        self.assertEqual(self._gate_ids(), set())

    def test_approved_but_not_promoted_is_excluded(self):
        doc = self._doc(status='indexed')
        self._moderation(doc, state=MODERATION_STATE_APPROVED,
                         storage_state=STORAGE_STATE_QUARANTINE)
        self.assertEqual(self._gate_ids(), set())

    def test_promoted_but_rejected_is_excluded(self):
        doc = self._doc(status='indexed')
        self._moderation(doc, state=MODERATION_STATE_REJECTED,
                         storage_state=STORAGE_STATE_PROMOTED)
        self.assertEqual(self._gate_ids(), set())

    def test_promoted_but_unavailable_is_excluded(self):
        doc = self._doc(status='indexed')
        self._moderation(doc, state=MODERATION_STATE_UNAVAILABLE,
                         storage_state=STORAGE_STATE_PROMOTED)
        self.assertEqual(self._gate_ids(), set())

    def test_promotion_failed_is_excluded(self):
        doc = self._doc(status='indexed')
        self._moderation(doc, state=MODERATION_STATE_APPROVED,
                         storage_state=STORAGE_STATE_PROMOTION_FAILED)
        self.assertEqual(self._gate_ids(), set())

    def test_approved_and_promoted_is_included(self):
        doc = self._doc(status='indexed')
        self._moderation(doc, state=MODERATION_STATE_APPROVED,
                         storage_state=STORAGE_STATE_PROMOTED)
        self.assertEqual(self._gate_ids(), {doc.id})

    def test_confirmed_unchecked_and_promoted_is_included(self):
        doc = self._doc(status='indexed')
        self._moderation(doc, state=MODERATION_STATE_CONFIRMED_UNCHECKED,
                         storage_state=STORAGE_STATE_PROMOTED)
        self.assertEqual(self._gate_ids(), {doc.id})

    def test_no_status_indexed_bypass(self):
        """`OR status='indexed'` 这类兜底不得存在：已索引但未审核 → 不可检索。"""
        doc = self._doc(status='indexed')
        self._moderation(doc, state=MODERATION_STATE_REJECTED,
                         storage_state=STORAGE_STATE_QUARANTINE)
        self.assertNotIn(doc.id, self._gate_ids())


class RagChannelGateTests(RagModerationGateTests):
    """门禁必须作用在真实检索通道上，而不只是那个 helper。"""

    def _ctx(self):
        return {'role': 'super_admin', 'user_id': self.admin.id}

    def test_fallback_title_search_excludes_ungated_docs(self):
        from utils.rag import _fallback_title_search

        doc = self._doc(status='indexed', title='贡嘎杯跳绳报告')
        DocChunk.objects.create(
            doc=doc, chunk_index=0, content='贡嘎杯跳绳比赛于四月举行。',
            token_count=14, vector_id='doc_x_chunk_0',
        )
        self.assertEqual(_fallback_title_search('贡嘎杯 跳绳', self._ctx()), [])

    def test_fallback_title_search_includes_gated_docs(self):
        from utils.rag import _fallback_title_search

        doc = self._doc(status='indexed', title='贡嘎杯跳绳报告')
        DocChunk.objects.create(
            doc=doc, chunk_index=0, content='贡嘎杯跳绳比赛于四月举行。',
            token_count=14, vector_id='doc_x_chunk_0',
        )
        self._moderation(doc, state=MODERATION_STATE_APPROVED,
                         storage_state=STORAGE_STATE_PROMOTED)

        results = _fallback_title_search('贡嘎杯 跳绳', self._ctx())
        self.assertEqual([r['doc_id'] for r in results], [doc.id])

    def test_gate_reuses_single_whitelist_implementation(self):
        """禁止在 rag.py 里复制第二份白名单判定。"""
        from utils import rag

        src = open(rag.__file__, encoding='utf-8').read()
        self.assertNotIn("state='approved'", src)
        self.assertNotIn('MODERATION_STATE_APPROVED', src)
        self.assertIn('filter_indexable_docs', src)


# ===========================================================================
# P-7：向量后端不可用必须可区分
# ===========================================================================

class VectorBackendObservabilityTests(_Base):

    def test_search_returns_none_without_backend(self):
        """两个后端都不可用 → None（不是空列表）。"""
        from utils.milvus import milvus_client

        client = type(milvus_client)()
        client._connected = False
        with patch.dict('sys.modules', {'chromadb': None}):
            self.assertIsNone(client.search([0.1] * 4, top_k=3))

    def test_backend_name_reports_unavailable(self):
        from utils.milvus import milvus_client

        client = type(milvus_client)()
        client._connected = False
        with patch.dict('sys.modules', {'chromadb': None}):
            self.assertEqual(client.backend_name(), 'unavailable')

    def test_backend_name_reports_milvus(self):
        from utils.milvus import milvus_client

        client = type(milvus_client)()
        client._connected = True
        self.assertEqual(client.backend_name(), 'milvus')

    def test_vector_backend_available_helper(self):
        from utils import rag

        with patch.object(rag, '_vector_backend_state',
                          return_value='unavailable'):
            self.assertFalse(rag.vector_backend_available())
        with patch.object(rag, '_vector_backend_state', return_value='milvus'):
            self.assertTrue(rag.vector_backend_available())

    def test_empty_result_is_not_unavailable(self):
        """后端可用但确实没有匹配 → 空列表，这是合法结果。"""
        from utils.milvus import milvus_client

        client = type(milvus_client)()
        client._connected = True
        with patch.object(client, '_search_milvus', return_value=[]):
            self.assertEqual(client.search([0.1] * 4, top_k=3), [])


# ===========================================================================
# P-6：settings 生产安全（fail-closed）
# ===========================================================================

class ProductionSettingsGuardTests(TestCase):
    """直接加载 config/settings.py 源码，验证"配错就拒绝启动"。"""

    def _load(self, **env):
        spec = importlib.util.spec_from_file_location(
            'config._settings_probe', SETTINGS_PATH,
        )
        module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, env):
            spec.loader.exec_module(module)
        return module

    def test_production_with_wildcard_hosts_refuses_to_boot(self):
        with self.assertRaises(ImproperlyConfigured):
            self._load(
                DJANGO_DEBUG='false',
                DJANGO_ALLOWED_HOSTS='*',
                SECRET_KEY='a-real-production-secret',
            )

    def test_production_without_hosts_refuses_to_boot(self):
        with self.assertRaises(ImproperlyConfigured):
            self._load(
                DJANGO_DEBUG='false',
                DJANGO_ALLOWED_HOSTS='',
                ALLOWED_HOSTS='',
                SECRET_KEY='a-real-production-secret',
            )

    def test_production_with_wildcard_cors_refuses_to_boot(self):
        with self.assertRaises(ImproperlyConfigured):
            self._load(
                DJANGO_DEBUG='false',
                DJANGO_ALLOWED_HOSTS='example.edu.cn',
                CORS_ALLOW_ALL_ORIGINS='true',
                SECRET_KEY='a-real-production-secret',
            )

    def test_production_with_explicit_values_boots(self):
        module = self._load(
            DJANGO_DEBUG='false',
            DJANGO_ALLOWED_HOSTS='example.edu.cn,www.example.edu.cn',
            CORS_ALLOW_ALL_ORIGINS='false',
            CORS_ALLOWED_ORIGINS='https://example.edu.cn',
            SECRET_KEY='a-real-production-secret',
        )
        self.assertFalse(module.DEBUG)
        self.assertEqual(
            module.ALLOWED_HOSTS, ['example.edu.cn', 'www.example.edu.cn'],
        )
        self.assertFalse(module.CORS_ALLOW_ALL_ORIGINS)
        self.assertEqual(module.CORS_ALLOWED_ORIGINS, ['https://example.edu.cn'])

    def test_production_never_defaults_cors_to_localhost(self):
        module = self._load(
            DJANGO_DEBUG='false',
            DJANGO_ALLOWED_HOSTS='example.edu.cn',
            CORS_ALLOWED_ORIGINS='',
            CORS_ORIGINS='',
            SECRET_KEY='a-real-production-secret',
        )
        self.assertEqual(module.CORS_ALLOWED_ORIGINS, [])

    def test_development_still_works(self):
        """本地开发不受影响：DEBUG=True 时 '*' 与 localhost 兜底照旧。"""
        module = self._load(
            DJANGO_DEBUG='true',
            DJANGO_ALLOWED_HOSTS='*',
            CORS_ORIGINS='http://localhost:5173',
            SECRET_KEY='a-real-production-secret',
        )
        self.assertTrue(module.DEBUG)
        self.assertEqual(module.ALLOWED_HOSTS, ['*'])
        self.assertTrue(module.CORS_ALLOW_ALL_ORIGINS)
        self.assertEqual(module.CORS_ALLOWED_ORIGINS, ['http://localhost:5173'])

    def test_legacy_variable_names_still_honoured(self):
        """旧 .env 用 DEBUG / ALLOWED_HOSTS / CORS_ORIGINS，不能直接失效。"""
        module = self._load(
            DJANGO_DEBUG='true',
            DEBUG='true',
            ALLOWED_HOSTS='dev.local',
            CORS_ORIGINS='http://dev.local:5173',
            SECRET_KEY='a-real-production-secret',
        )
        self.assertEqual(module.ALLOWED_HOSTS, ['dev.local'])
        self.assertEqual(module.CORS_ALLOWED_ORIGINS, ['http://dev.local:5173'])

    def test_recovery_grace_setting_exists(self):
        from django.conf import settings

        self.assertGreaterEqual(
            settings.MODERATION_DISPATCH_RECOVERY_GRACE_SECONDS, 0,
        )

    # ---- P-25 / P-26：Celery worker 与 beat 的配置 ---------------------
    #
    # 下面两条**静态断言**在本地也能跑（本机 venv 未安装 celery：它是
    # requirements.txt 里声明的可选依赖）。它们检查的是"配置声明得对不对"，
    # 不依赖 celery 运行时。
    #
    # 真正的"任务是否被注册进 app"需要导入 celery，用 skipUnless 标注——
    # 这是**依赖缺失**导致的无法验证，不是为了让测试变绿而跳过安全检查。
    # 交付报告中该运行时验证标为 NOT VERIFIED。

    _BEAT_KEYS = (
        'generate-faq-weekly',
        'crawl-neusoft-website-daily',
        'crawl-neusoft-website-weekly-full',
        'refresh-daily-context',
        'cleanup-expired-upload-sessions',
        'retry-failed-moderations',
    )

    def test_celery_config_declares_all_beat_entries(self):
        """P-26：beat 配置不得删除任何既有条目，且必须含审核重试。"""
        src = open(
            os.path.join(BACKEND_DIR, 'config', 'celery.py'), encoding='utf-8',
        ).read()
        for key in self._BEAT_KEYS:
            self.assertIn(f"'{key}'", src, f'beat 条目丢失：{key}')

    def test_celery_config_autodiscovers_tasks(self):
        """P-25：worker 必须靠 autodiscover 找到各 app 的 tasks.py。"""
        src = open(
            os.path.join(BACKEND_DIR, 'config', 'celery.py'), encoding='utf-8',
        ).read()
        self.assertIn('app.autodiscover_tasks()', src)

    def test_beat_entry_points_at_retry_task(self):
        src = open(
            os.path.join(BACKEND_DIR, 'config', 'celery.py'), encoding='utf-8',
        ).read()
        self.assertIn(
            "'task': 'apps.knowledge.tasks.retry_failed_moderations_task'", src,
        )

    def test_moderation_tasks_are_registered_as_shared_tasks(self):
        """P-25：三个关键任务必须都在 `@shared_task` 之下，否则 autodiscover
        找不到它们，worker 起来了也消费不了。"""
        src = open(
            os.path.join(BACKEND_DIR, 'apps', 'knowledge', 'tasks.py'),
            encoding='utf-8',
        ).read()
        for name in ('moderate_and_promote_task',
                     'retry_failed_moderations_task',
                     'index_document_task'):
            self.assertIsNotNone(
                re.search(
                    r'@shared_task\([^)]*\)\s*\ndef %s\(' % re.escape(name),
                    src,
                ),
                f'{name} 必须紧跟 @shared_task(...) 装饰器，否则 autodiscover'
                f'找不到它，worker 起来了也消费不了',
            )

    @unittest.skipUnless(
        _celery_installed(),
        'NOT VERIFIED：本地 venv 未安装 celery'
        '（requirements.txt 已声明；生产部署必须装上后重跑本条）',
    )
    def test_celery_registers_moderation_tasks(self):
        """P-25 的运行时验证：worker 加载 config 后必须能发现这三个任务。"""
        from config.celery import app

        task_names = set(app.tasks.keys())
        for name in (
            'apps.knowledge.tasks.moderate_and_promote_task',
            'apps.knowledge.tasks.retry_failed_moderations_task',
            'apps.knowledge.tasks.index_document_task',
        ):
            self.assertIn(name, task_names)
