"""P1-2D STEP 3：UploadIntent 基础设施测试。

覆盖 §十三 要求的 10 组用例：
    1 创建 / 2 key 命名空间 / 3 key 唯一性 / 4 用户隔离 / 5 过期 /
    6 consumed 不可重复 / 7 cancelled 不可用 / 8 并发 consume 只成功一个 /
    9 ChunkedUploadSession 关联 / 10 删除与级联策略

全部使用纯模型与 service 层调用，**不触网**：不连 OSS、不连 ClamAV、
不连 LLM/Vision。创建 intent 只用 `build_quarantine_key()` 生成字符串，
不产生任何 OSS 请求。

唯一例外是 §9 的分片入口用例：它必须驱动真实的 `ChunkUploadInitView` 才能证明
"Key 由服务端生成"，因此只把 `init_multipart_upload`（唯一的网络调用）mock 掉，
其余全部走生产代码。mock 的对象仍是 OSS，模块级"不触网"的约定不变。
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import College, User
from apps.classify.models import Department
from utils.oss import OFFICIAL_KEY_PREFIX, QUARANTINE_KEY_PREFIX

from .models import ChunkedUploadSession, KnowledgeDoc, UploadIntent
from .upload_intent import (
    REASON_EXPIRED,
    REASON_INVALID_ARGUMENT,
    REASON_INVALID_STATE,
    REASON_NOT_FOUND,
    REASON_SIZE_MISMATCH,
    UPLOAD_INTENT_CANCELLED,
    UPLOAD_INTENT_COMPLETED,
    UPLOAD_INTENT_CONSUMED,
    UPLOAD_INTENT_EXPIRED,
    UPLOAD_INTENT_ISSUED,
    UPLOAD_INTENT_STATUS_CHOICES,
    UploadIntentError,
    cancel_upload_intent,
    complete_upload_intent,
    consume_upload_intent,
    create_upload_intent,
    get_upload_intent,
    intent_ttl_seconds,
    is_quarantine_key,
    resolve_upload_scope,
    serialize_for_client,
    serialize_for_upload,
)


class UploadIntentTestBase(TestCase):
    """公共夹具：四种角色的用户 + 一个 intent 工厂。"""

    def setUp(self):
        self.college = College.objects.create(name='计算机与软件学院', code='CS')
        self.other_college = College.objects.create(name='外国语学院', code='FL')
        self.department = Department.objects.create(name='教学管理')

        self.super_admin = User.objects.create_user(
            username='uid_super', password='testpass123', role='super_admin',
        )
        self.dept_admin = User.objects.create_user(
            username='uid_dept', password='testpass123',
            role='dept_admin', department=self.department,
        )
        self.college_admin = User.objects.create_user(
            username='uid_college', password='testpass123',
            role='college_admin', college=self.college,
        )
        self.plain_user = User.objects.create_user(
            username='uid_user', password='testpass123',
            role='user', college=self.college,
        )
        self.other_user = User.objects.create_user(
            username='uid_other', password='testpass123',
            role='college_admin', college=self.other_college,
        )

    def _intent(self, user=None, file_name='报告.pdf', **kwargs):
        return create_upload_intent(user=user or self.college_admin,
                                    file_name=file_name, **kwargs)

    def _completed(self, user=None, **kwargs):
        intent = self._intent(user=user, **kwargs)
        return complete_upload_intent(user or self.college_admin, intent.pk)


# ---------------------------------------------------------------------------
# 1. 创建
# ---------------------------------------------------------------------------

class UploadIntentCreateTests(UploadIntentTestBase):
    """§十三-1：创建后各字段正确。"""

    def test_create_sets_user_scope_key_status_and_expiry(self):
        before = timezone.now()
        intent = self._intent(file_name='毕业论文.docx', content_type='application/msword',
                              expected_size=2048)

        self.assertEqual(intent.user_id, self.college_admin.pk)
        self.assertEqual(intent.status, UPLOAD_INTENT_ISSUED)
        self.assertEqual(intent.scope, 'college')
        self.assertEqual(intent.owner_id, self.college.id)
        self.assertEqual(intent.original_filename, '毕业论文.docx')
        self.assertEqual(intent.content_type, 'application/msword')
        self.assertEqual(intent.expected_size, 2048)
        self.assertIsNone(intent.actual_size)
        self.assertIsNone(intent.completed_at)
        self.assertIsNone(intent.consumed_at)
        self.assertGreaterEqual(intent.expires_at, before)
        self.assertTrue(intent.created_at)

    def test_expiry_uses_configured_ttl(self):
        intent = self._intent()
        delta = intent.expires_at - intent.created_at
        # 允许少量执行耗时带来的误差
        self.assertLessEqual(abs(delta.total_seconds() - intent_ttl_seconds()), 5)

    def test_scope_follows_role_exactly_like_existing_upload_entries(self):
        """scope 推导必须与现有 4 个上传入口一致，不能出现第二套归属概念。"""
        self.assertEqual(resolve_upload_scope(self.super_admin), ('school', 0))
        self.assertEqual(resolve_upload_scope(self.dept_admin),
                         ('department', self.department.id))
        self.assertEqual(resolve_upload_scope(self.college_admin),
                         ('college', self.college.id))
        self.assertEqual(resolve_upload_scope(self.plain_user), ('college', self.college.id))

    def test_created_intent_scope_and_owner_match_the_key_path(self):
        """落库的 scope/owner_id 必须与 key 里的路径段一致，否则后续 scope 校验会错位。"""
        for user in (self.super_admin, self.dept_admin, self.college_admin, self.plain_user):
            intent = self._intent(user=user)
            expected_prefix = f'{QUARANTINE_KEY_PREFIX}{intent.scope}/{intent.owner_id}/'
            self.assertTrue(
                intent.quarantine_key.startswith(expected_prefix),
                f'{intent.quarantine_key} 与 scope={intent.scope} owner={intent.owner_id} 不一致',
            )

    def test_dept_admin_without_department_falls_back_to_college(self):
        """与现有入口一致：没有 department_id 的 dept_admin 退回 college 分支。"""
        odd = User.objects.create_user(
            username='uid_dept_none', password='testpass123',
            role='dept_admin', college=self.college,
        )
        self.assertEqual(resolve_upload_scope(odd), ('college', self.college.id))

    def test_status_is_confined_to_declared_choices(self):
        valid = {value for value, _ in UPLOAD_INTENT_STATUS_CHOICES}
        self.assertEqual(
            valid,
            {UPLOAD_INTENT_ISSUED, UPLOAD_INTENT_COMPLETED, UPLOAD_INTENT_CONSUMED,
             UPLOAD_INTENT_EXPIRED, UPLOAD_INTENT_CANCELLED},
        )

    def test_filename_path_segments_are_stripped(self):
        """原始文件名只保留 basename，避免把路径写进展示字段。"""
        intent = self._intent(file_name='../../etc/passwd.txt')
        self.assertEqual(intent.original_filename, 'passwd.txt')

    def test_negative_expected_size_is_rejected(self):
        with self.assertRaises(UploadIntentError) as ctx:
            self._intent(expected_size=-1)
        self.assertEqual(ctx.exception.reason, REASON_INVALID_ARGUMENT)

    def test_intent_requires_a_persisted_user(self):
        with self.assertRaises(UploadIntentError) as ctx:
            create_upload_intent(user=None, file_name='a.txt')
        self.assertEqual(ctx.exception.reason, REASON_INVALID_ARGUMENT)

    def test_filename_without_extension_still_produces_a_key(self):
        intent = self._intent(file_name='无扩展名')
        self.assertTrue(intent.quarantine_key.endswith('.bin'))


# ---------------------------------------------------------------------------
# 2. Key 命名空间
# ---------------------------------------------------------------------------

class UploadIntentKeyNamespaceTests(UploadIntentTestBase):
    """§十三-2：key 必须落在 quarantine/，不得是 knowledge/。"""

    def test_key_is_in_quarantine_namespace(self):
        intent = self._intent()
        self.assertTrue(intent.quarantine_key.startswith(QUARANTINE_KEY_PREFIX))

    def test_key_is_never_in_official_namespace(self):
        intent = self._intent()
        self.assertFalse(intent.quarantine_key.startswith(OFFICIAL_KEY_PREFIX))

    def test_quarantine_and_official_prefixes_do_not_overlap(self):
        """两个命名空间必须是平级的顶层前缀（防止 quarantine/ 被写成 knowledge/ 的子路径）。"""
        self.assertFalse(QUARANTINE_KEY_PREFIX.startswith(OFFICIAL_KEY_PREFIX))
        self.assertFalse(OFFICIAL_KEY_PREFIX.startswith(QUARANTINE_KEY_PREFIX))

    def test_is_quarantine_key_helper(self):
        self.assertTrue(is_quarantine_key('quarantine/college/1/2026-09/a.pdf'))
        self.assertFalse(is_quarantine_key('knowledge/college/1/2026-09/a.pdf'))
        self.assertFalse(is_quarantine_key(''))
        self.assertFalse(is_quarantine_key(None))

    def test_key_reuses_p1_2a_generator(self):
        """同一 scope/owner/文件名下，key 的结构必须与 build_quarantine_key 完全一致。"""
        intent = self._intent(file_name='讲义.pptx')
        parts = intent.quarantine_key.split('/')
        self.assertEqual(parts[0], 'quarantine')
        self.assertEqual(parts[1], intent.scope)
        self.assertEqual(parts[2], str(intent.owner_id))
        self.assertEqual(len(parts), 5)
        self.assertTrue(parts[4].endswith('.pptx'))

    def test_client_supplied_key_cannot_be_recorded(self):
        """创建入口不存在任何可以传入 key 的参数——只能由服务端生成。"""
        intent = self._intent()
        key = intent.quarantine_key

        # 即便调用方试图在 kwargs 里塞 key，也不会生效（create_upload_intent 无该参数）
        with self.assertRaises(TypeError):
            create_upload_intent(user=self.college_admin, file_name='a.txt',
                                 quarantine_key='knowledge/college/1/2026-09/evil.pdf')

        intent.refresh_from_db()
        self.assertEqual(intent.quarantine_key, key)


# ---------------------------------------------------------------------------
# 3. Key 唯一性
# ---------------------------------------------------------------------------

class UploadIntentKeyUniquenessTests(UploadIntentTestBase):
    """§十三-3：同一个 key 不能对应两个 intent（数据库层约束）。"""

    def test_duplicate_key_is_rejected_by_the_database(self):
        existing = self._intent()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UploadIntent.objects.create(
                    user=self.other_user, scope='college',
                    owner_id=self.other_college.id,
                    quarantine_key=existing.quarantine_key,
                    original_filename='撞车.pdf',
                    expires_at=timezone.now() + timedelta(hours=1),
                )

    def test_two_intents_always_get_different_keys(self):
        keys = {self._intent().quarantine_key for _ in range(20)}
        self.assertEqual(len(keys), 20)


# ---------------------------------------------------------------------------
# 4. 用户隔离
# ---------------------------------------------------------------------------

class UploadIntentOwnershipTests(UploadIntentTestBase):
    """§十三-4 / §十：A 不能读取、完成、消费 B 的 intent。"""

    def setUp(self):
        super().setUp()
        self.intent = self._intent(user=self.college_admin)

    def test_owner_can_read_own_intent(self):
        self.assertEqual(
            get_upload_intent(self.college_admin, self.intent.pk).pk, self.intent.pk,
        )

    def test_other_user_cannot_read(self):
        with self.assertRaises(UploadIntentError) as ctx:
            get_upload_intent(self.other_user, self.intent.pk)
        self.assertEqual(ctx.exception.reason, REASON_NOT_FOUND)

    def test_other_user_cannot_complete(self):
        with self.assertRaises(UploadIntentError) as ctx:
            complete_upload_intent(self.other_user, self.intent.pk)
        self.assertEqual(ctx.exception.reason, REASON_NOT_FOUND)

        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, UPLOAD_INTENT_ISSUED)

    def test_other_user_cannot_consume(self):
        complete_upload_intent(self.college_admin, self.intent.pk)

        with self.assertRaises(UploadIntentError) as ctx:
            consume_upload_intent(self.other_user, self.intent.pk)
        self.assertEqual(ctx.exception.reason, REASON_NOT_FOUND)

        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, UPLOAD_INTENT_COMPLETED)

    def test_other_user_cannot_cancel(self):
        with self.assertRaises(UploadIntentError) as ctx:
            cancel_upload_intent(self.other_user, self.intent.pk)
        self.assertEqual(ctx.exception.reason, REASON_NOT_FOUND)
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, UPLOAD_INTENT_ISSUED)

    def test_nonexistent_intent_is_indistinguishable_from_foreign_one(self):
        """越权与不存在返回同一原因码：不通过错误信息泄露 intent 是否存在。"""
        with self.assertRaises(UploadIntentError) as foreign:
            get_upload_intent(self.other_user, self.intent.pk)
        with self.assertRaises(UploadIntentError) as missing:
            get_upload_intent(self.college_admin, 99999999)
        self.assertEqual(foreign.exception.reason, missing.exception.reason)

    def test_intent_without_owner_cannot_be_used_by_anyone(self):
        """用户注销后 user 置空（SET_NULL 保留审计记录）→ 任何请求方都不能消费。"""
        self.intent.user = None
        self.intent.save(update_fields=['user'])

        with self.assertRaises(UploadIntentError) as ctx:
            get_upload_intent(self.college_admin, self.intent.pk)
        self.assertEqual(ctx.exception.reason, REASON_NOT_FOUND)

    def test_invalid_intent_id_types_are_reported_as_not_found(self):
        for bad_id in ('abc', None, 0):
            with self.assertRaises(UploadIntentError) as ctx:
                get_upload_intent(self.college_admin, bad_id)
            self.assertEqual(ctx.exception.reason, REASON_NOT_FOUND)


# ---------------------------------------------------------------------------
# 5. 过期
# ---------------------------------------------------------------------------

class UploadIntentExpiryTests(UploadIntentTestBase):
    """§十三-5 / §十二：过期后不能完成、不能消费；过期判定是惰性的。"""

    def _expire(self, intent):
        UploadIntent.objects.filter(pk=intent.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        intent.refresh_from_db()
        return intent

    def test_expired_intent_cannot_complete(self):
        intent = self._expire(self._intent())

        with self.assertRaises(UploadIntentError) as ctx:
            complete_upload_intent(self.college_admin, intent.pk)
        self.assertEqual(ctx.exception.reason, REASON_EXPIRED)

    def test_expired_intent_cannot_consume(self):
        intent = self._completed()
        intent = self._expire(intent)

        with self.assertRaises(UploadIntentError) as ctx:
            consume_upload_intent(self.college_admin, intent.pk)
        self.assertEqual(ctx.exception.reason, REASON_EXPIRED)

    def test_expiry_is_persisted_lazily_on_use(self):
        """§十二：不引入定时任务，但每次使用时必须把过期落到状态上。"""
        intent = self._expire(self._intent())

        get_upload_intent(self.college_admin, intent.pk)
        intent.refresh_from_db()
        self.assertEqual(intent.status, UPLOAD_INTENT_EXPIRED)

    def test_expired_intent_cannot_be_cancelled(self):
        intent = self._expire(self._intent())
        with self.assertRaises(UploadIntentError) as ctx:
            cancel_upload_intent(self.college_admin, intent.pk)
        self.assertEqual(ctx.exception.reason, REASON_EXPIRED)

    def test_not_yet_expired_intent_is_usable(self):
        intent = self._intent()
        self.assertFalse(intent.expires_at <= timezone.now())
        self.assertEqual(
            complete_upload_intent(self.college_admin, intent.pk).status,
            UPLOAD_INTENT_COMPLETED,
        )

    def test_expired_is_a_terminal_state(self):
        intent = self._expire(self._intent())
        get_upload_intent(self.college_admin, intent.pk)
        intent.refresh_from_db()
        self.assertEqual(intent.status, UPLOAD_INTENT_EXPIRED)

        # 再次过期判定不会改变结果
        get_upload_intent(self.college_admin, intent.pk)
        intent.refresh_from_db()
        self.assertEqual(intent.status, UPLOAD_INTENT_EXPIRED)


# ---------------------------------------------------------------------------
# 6. 消费
# ---------------------------------------------------------------------------

class UploadIntentConsumeTests(UploadIntentTestBase):
    """§十三-6：consumed 是终态，不能再次消费。"""

    def test_consume_requires_completion_first(self):
        intent = self._intent()

        with self.assertRaises(UploadIntentError) as ctx:
            consume_upload_intent(self.college_admin, intent.pk)
        self.assertEqual(ctx.exception.reason, REASON_INVALID_STATE)

    def test_happy_path_issued_completed_consumed(self):
        intent = self._intent()

        completed = complete_upload_intent(self.college_admin, intent.pk)
        self.assertEqual(completed.status, UPLOAD_INTENT_COMPLETED)
        self.assertIsNotNone(completed.completed_at)
        self.assertIsNone(completed.consumed_at)

        consumed = consume_upload_intent(self.college_admin, intent.pk)
        self.assertEqual(consumed.status, UPLOAD_INTENT_CONSUMED)
        self.assertIsNotNone(consumed.consumed_at)

    def test_second_consume_is_rejected(self):
        intent = self._completed()
        consume_upload_intent(self.college_admin, intent.pk)

        with self.assertRaises(UploadIntentError) as ctx:
            consume_upload_intent(self.college_admin, intent.pk)
        self.assertEqual(ctx.exception.reason, REASON_INVALID_STATE)

    def test_consumed_intent_cannot_be_completed_again(self):
        intent = self._completed()
        consume_upload_intent(self.college_admin, intent.pk)

        with self.assertRaises(UploadIntentError) as ctx:
            complete_upload_intent(self.college_admin, intent.pk)
        self.assertEqual(ctx.exception.reason, REASON_INVALID_STATE)

    def test_consumed_intent_cannot_be_cancelled(self):
        intent = self._completed()
        consume_upload_intent(self.college_admin, intent.pk)

        with self.assertRaises(UploadIntentError) as ctx:
            cancel_upload_intent(self.college_admin, intent.pk)
        self.assertEqual(ctx.exception.reason, REASON_INVALID_STATE)

    def test_key_cannot_change_between_creation_and_consumption(self):
        """consume 前后 key 必须完全一致——不允许中途被改写。"""
        intent = self._intent()
        key = intent.quarantine_key
        complete_upload_intent(self.college_admin, intent.pk)
        consumed = consume_upload_intent(self.college_admin, intent.pk)
        self.assertEqual(consumed.quarantine_key, key)


# ---------------------------------------------------------------------------
# 7. cancelled
# ---------------------------------------------------------------------------

class UploadIntentCancelTests(UploadIntentTestBase):
    """§十三-7：cancelled 后不能完成、不能消费。"""

    def test_cancelled_intent_cannot_complete(self):
        intent = cancel_upload_intent(self.college_admin, self._intent().pk)
        self.assertEqual(intent.status, UPLOAD_INTENT_CANCELLED)

        with self.assertRaises(UploadIntentError) as ctx:
            complete_upload_intent(self.college_admin, intent.pk)
        self.assertEqual(ctx.exception.reason, REASON_INVALID_STATE)

    def test_cancelled_intent_cannot_consume(self):
        intent = self._completed()
        cancel_upload_intent(self.college_admin, intent.pk)

        with self.assertRaises(UploadIntentError) as ctx:
            consume_upload_intent(self.college_admin, intent.pk)
        self.assertEqual(ctx.exception.reason, REASON_INVALID_STATE)

    def test_completed_intent_can_still_be_cancelled(self):
        """上传完成但业务尚未消费 → 仍允许作废（放弃这次上传）。"""
        intent = self._completed()
        cancelled = cancel_upload_intent(self.college_admin, intent.pk)
        self.assertEqual(cancelled.status, UPLOAD_INTENT_CANCELLED)

    def test_double_cancel_is_rejected(self):
        intent = self._intent()
        cancel_upload_intent(self.college_admin, intent.pk)

        with self.assertRaises(UploadIntentError) as ctx:
            cancel_upload_intent(self.college_admin, intent.pk)
        self.assertEqual(ctx.exception.reason, REASON_INVALID_STATE)


# ---------------------------------------------------------------------------
# 8. 并发消费
# ---------------------------------------------------------------------------

class UploadIntentConcurrencyTests(UploadIntentTestBase):
    """§十三-8：两个消费者并发时只能有一个成功。

    实现说明（重要）
    ----------------
    消费的原子性由 `_cas_status()` 的单条 `UPDATE ... WHERE status IN (...)` 保证，
    而不是依赖 `select_for_update()`——后者在 SQLite 上是**静默空操作**
    （Django 的 `has_select_for_update` 为 False），不能作为唯一防线。

    因此这里不做多线程测试：测试库是 SQLite `:memory:`，`TestCase` 会把整个用例
    包在一个事务里，其它线程根本看不到未提交的数据，线程测试只会测出假象。
    下面改用**确定性的过期快照**（stale snapshot）复现同一个竞态：两个消费者各自
    读到 `completed`，先后提交 CAS —— 第二个必须失败。
    """

    def test_two_stale_snapshots_only_one_can_win(self):
        intent = self._completed()

        worker_a = UploadIntent.objects.get(pk=intent.pk)
        worker_b = UploadIntent.objects.get(pk=intent.pk)
        self.assertEqual(worker_a.status, UPLOAD_INTENT_COMPLETED)
        self.assertEqual(worker_b.status, UPLOAD_INTENT_COMPLETED)

        consume_upload_intent(self.college_admin, worker_a.pk)

        with self.assertRaises(UploadIntentError) as ctx:
            consume_upload_intent(self.college_admin, worker_b.pk)
        self.assertEqual(ctx.exception.reason, REASON_INVALID_STATE)

    def test_cas_on_a_stale_instance_never_overwrites_a_terminal_state(self):
        """直接用 CAS 原语验证：状态已被推进后，旧快照的写入必须影响 0 行。"""
        from .upload_intent import _cas_status

        intent = self._completed()
        stale = UploadIntent.objects.get(pk=intent.pk)
        consume_upload_intent(self.college_admin, intent.pk)

        won = _cas_status(stale, {UPLOAD_INTENT_COMPLETED}, UPLOAD_INTENT_CONSUMED,
                          consumed_at=timezone.now())
        self.assertFalse(won)

        intent.refresh_from_db()
        self.assertEqual(intent.status, UPLOAD_INTENT_CONSUMED)

    def test_repeated_consume_never_produces_two_consumptions(self):
        """重复消费请求不能把 consumed_at 刷新成第二个时间点。"""
        intent = self._completed()
        first = consume_upload_intent(self.college_admin, intent.pk)
        first_at = first.consumed_at

        for _ in range(3):
            with self.assertRaises(UploadIntentError):
                consume_upload_intent(self.college_admin, intent.pk)

        intent.refresh_from_db()
        self.assertEqual(intent.consumed_at, first_at)

    def test_complete_then_consume_is_never_reentrant(self):
        """完整生命周期跑完后，再次完整跑一遍必须全部失败（不产生第二个消费）。"""
        intent = self._completed()
        consume_upload_intent(self.college_admin, intent.pk)

        with self.assertRaises(UploadIntentError):
            complete_upload_intent(self.college_admin, intent.pk)
        with self.assertRaises(UploadIntentError):
            consume_upload_intent(self.college_admin, intent.pk)


# ---------------------------------------------------------------------------
# 9. ChunkedUploadSession 关联
# ---------------------------------------------------------------------------

CHUNK_INIT_URL = '/api/knowledge/upload/chunk/init/'


class UploadIntentChunkedSessionTests(UploadIntentTestBase):
    """§十三-9：分片会话与 UploadIntent 的关联。

    `_session()` 是**纯模型夹具**，只用于验证关联字段本身（可空、唯一、
    PROTECT）。它写死的 `oss_key` 不代表生产行为——生产 Key 由服务端生成，
    见 `test_real_chunk_init_binds_a_server_generated_quarantine_key`。
    """

    def _session(self, user=None, **overrides):
        defaults = dict(
            upload_id='up-intent-1', user=user or self.college_admin,
            file_name='大文件.zip', file_size=0, file_md5='b' * 32,
            oss_key=f'knowledge/college/{self.college.id}/2026-09/big.zip',
        )
        defaults.update(overrides)
        return ChunkedUploadSession.objects.create(**defaults)

    def test_session_can_reference_an_intent(self):
        intent = self._intent()
        session = self._session(intent=intent)

        self.assertEqual(session.intent_id, intent.pk)
        self.assertEqual(intent.chunked_session.pk, session.pk)

    def test_session_without_intent_still_works(self):
        """不传 intent 的会话照常创建（字段可空，STEP 4-A 之前的存量数据如此）。"""
        session = self._session()
        self.assertIsNone(session.intent)

    def test_second_session_for_the_same_intent_is_rejected(self):
        intent = self._intent()
        self._session(intent=intent, upload_id='up-intent-1')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._session(intent=intent, upload_id='up-intent-2')

    def test_many_sessions_may_have_no_intent(self):
        """可空唯一字段允许多个 NULL：不会因为第二个空 intent 的会话报错。"""
        self._session(upload_id='up-null-1')
        self._session(upload_id='up-null-2')
        self.assertEqual(ChunkedUploadSession.objects.filter(intent__isnull=True).count(), 2)

    def test_real_chunk_init_binds_a_server_generated_quarantine_key(self):
        """分片入口：会话 Key 由服务端生成、落在隔离区，客户端参数改不动它。

        取代原先的 `test_link_does_not_change_the_sessions_oss_key`。旧测试的
        `oss_key` 来自本测试自己写死的夹具（`knowledge/college/...`），断言
        "它仍以 knowledge/ 开头"只是在验证夹具自身，对生产行为零证明力；更糟的是
        它把 STEP 4-A 已经推翻的旧结论（"分片仍写正式区"）固化成一个"通过"的用例。

        这里改为驱动真实的 `ChunkUploadInitView`：Key 只能是视图内部
        `upload_service.begin_upload()` → `create_upload_intent()` →
        `build_quarantine_key()` 的产物，夹具不预先写入任何 Key。OSS 仅 mock
        `init_multipart_upload`（唯一的网络调用），其余全部走生产代码。
        """
        client = APIClient()
        client.force_authenticate(self.college_admin)

        with patch('apps.knowledge.chunked_views.oss_client') as mock_oss:
            mock_oss.check_duplicate.return_value = None
            mock_oss.bucket.init_multipart_upload.return_value = SimpleNamespace(
                upload_id='up-real-1',
            )
            response = client.post(CHUNK_INIT_URL, {
                'file_md5': 'd' * 32, 'filename': '大文件.zip', 'file_size': 2048,
                # 客户端试图指定最终路径：正式区 Key 与自选的隔离区 Key 各一，
                # 两者都必须被忽略——最终位置只认服务端签发的 intent。
                'oss_key': f'knowledge/college/{self.college.id}/2026-09/evil.zip',
                'quarantine_key': 'quarantine/college/9999/2026-09/evil.zip',
            }, format='json')

        self.assertEqual(response.status_code, 200)
        session = ChunkedUploadSession.objects.get(upload_id='up-real-1')

        # 1. Key 是服务端生成的隔离区 Key，不是夹具写死的正式区 Key
        self.assertTrue(is_quarantine_key(session.oss_key))
        self.assertFalse(session.oss_key.startswith(OFFICIAL_KEY_PREFIX))
        # 2. 客户端提交的两个 Key 都没有参与
        self.assertNotIn('evil', session.oss_key)
        self.assertNotEqual(
            session.oss_key, 'quarantine/college/9999/2026-09/evil.zip',
        )
        # 3. 会话与其 intent 绑定的是**同一个**服务端生成的 Key
        self.assertIsNotNone(session.intent_id)
        self.assertEqual(session.oss_key, session.intent.quarantine_key)

    def test_session_lifecycle_is_unaffected_by_intent_state(self):
        """intent 进入终态不会连带改写会话状态。"""
        intent = self._intent()
        session = self._session(intent=intent)

        cancel_upload_intent(self.college_admin, intent.pk)
        session.refresh_from_db()
        self.assertEqual(session.status, 'uploading')


# ---------------------------------------------------------------------------
# 10. 删除 / 级联
# ---------------------------------------------------------------------------

class UploadIntentDeletionTests(UploadIntentTestBase):
    """§十三-10：删除策略明确、无孤儿记录。"""

    def test_deleting_an_intent_referenced_by_a_session_is_protected(self):
        intent = self._intent()
        ChunkedUploadSession.objects.create(
            upload_id='up-del-1', user=self.college_admin, file_name='a.zip',
            file_size=0, file_md5='c' * 32,
            oss_key=f'knowledge/college/{self.college.id}/2026-09/a.zip',
            intent=intent,
        )

        with self.assertRaises(ProtectedError):
            intent.delete()

        self.assertTrue(UploadIntent.objects.filter(pk=intent.pk).exists())

    def test_deleting_an_unreferenced_intent_is_allowed(self):
        intent = self._intent()
        pk = intent.pk
        intent.delete()
        self.assertFalse(UploadIntent.objects.filter(pk=pk).exists())

    def test_deleting_a_user_keeps_the_intent_as_evidence(self):
        """user 是 SET_NULL：注销用户不会删掉隔离区凭证记录（对象还在隔离区）。"""
        intent = self._intent(user=self.other_user)
        self.other_user.delete()

        intent.refresh_from_db()
        self.assertIsNone(intent.user_id)
        self.assertTrue(intent.quarantine_key.startswith(QUARANTINE_KEY_PREFIX))

    def test_intent_deletion_never_touches_knowledge_docs(self):
        """intent 与 KnowledgeDoc 之间没有外键，删 intent 不会级联掉文档。

        STEP 4-A 起链路确实打通了（文档的 oss_key 就是 intent 的 quarantine
        key），但那是**值**上的对应关系，不是数据库外键，因此删除行为不受影响。
        """
        intent = self._intent()
        doc = KnowledgeDoc.objects.create(
            title='无关文档', source_type='upload',
            oss_key=f'knowledge/college/{self.college.id}/2026-09/x.pdf',
            uploader=self.college_admin,
        )

        intent.delete()
        self.assertTrue(KnowledgeDoc.objects.filter(pk=doc.pk).exists())


# ---------------------------------------------------------------------------
# 附加：完成阶段的安全校验
# ---------------------------------------------------------------------------

class UploadIntentCompletionGuardTests(UploadIntentTestBase):
    """完成阶段必须校验"服务端观察到的对象"与"intent 记录"一致。"""

    def test_mismatched_quarantine_key_is_rejected(self):
        """上传落到 intent 之外的路径 → 拒绝完成（防把对象写到别处再声称成功）。"""
        intent = self._intent()

        with self.assertRaises(UploadIntentError) as ctx:
            complete_upload_intent(
                self.college_admin, intent.pk,
                quarantine_key='knowledge/college/1/2026-09/injected.pdf',
            )
        self.assertEqual(ctx.exception.reason, REASON_INVALID_ARGUMENT)

        intent.refresh_from_db()
        self.assertEqual(intent.status, UPLOAD_INTENT_ISSUED)

    def test_matching_quarantine_key_completes(self):
        intent = self._intent()
        completed = complete_upload_intent(
            self.college_admin, intent.pk, quarantine_key=intent.quarantine_key,
        )
        self.assertEqual(completed.status, UPLOAD_INTENT_COMPLETED)

    def test_size_mismatch_is_rejected(self):
        intent = self._intent(expected_size=1024)

        with self.assertRaises(UploadIntentError) as ctx:
            complete_upload_intent(self.college_admin, intent.pk, actual_size=2048)
        self.assertEqual(ctx.exception.reason, REASON_SIZE_MISMATCH)

        intent.refresh_from_db()
        self.assertEqual(intent.status, UPLOAD_INTENT_ISSUED)

    def test_size_match_is_recorded(self):
        intent = self._intent(expected_size=1024)
        completed = complete_upload_intent(self.college_admin, intent.pk, actual_size=1024)
        self.assertEqual(completed.actual_size, 1024)

    def test_size_without_declaration_is_accepted_and_recorded(self):
        intent = self._intent()
        completed = complete_upload_intent(self.college_admin, intent.pk, actual_size=4096)
        self.assertEqual(completed.actual_size, 4096)

    def test_negative_actual_size_is_rejected(self):
        intent = self._intent()
        with self.assertRaises(UploadIntentError) as ctx:
            complete_upload_intent(self.college_admin, intent.pk, actual_size=-5)
        self.assertEqual(ctx.exception.reason, REASON_INVALID_ARGUMENT)


# ---------------------------------------------------------------------------
# 附加：对外序列化不泄露内部信息
# ---------------------------------------------------------------------------

class UploadIntentSerializationTests(UploadIntentTestBase):
    """§十一：默认不下发 OSS key，仅在直传场景按需下发。"""

    def test_client_payload_has_no_internal_key(self):
        payload = serialize_for_client(self._intent())

        self.assertEqual(set(payload), {'intent_id', 'status', 'expires_at'})
        self.assertNotIn('quarantine_key', payload)
        self.assertNotIn('owner_id', payload)
        self.assertNotIn('scope', payload)

    def test_upload_payload_includes_the_key(self):
        intent = self._intent()
        payload = serialize_for_upload(intent)

        self.assertEqual(payload['quarantine_key'], intent.quarantine_key)
        self.assertEqual(payload['intent_id'], intent.pk)

    def test_payloads_never_expose_moderation_internals(self):
        intent = self._intent()
        forbidden = {'moderation', 'verdict', 'detector', 'storage_state', 'promoted_key'}
        for payload in (serialize_for_client(intent), serialize_for_upload(intent)):
            self.assertFalse(forbidden & set(payload))
