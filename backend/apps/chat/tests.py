"""文字提问上下文继承与语音入口隔离测试。

覆盖：
- 同一会话内的文件上下文继承（文件名重放 / RAG QueryLog）；
- 多候选澄清、当前问题优先、跨轮次不误继承；
- 权限重新检查（普通用户不得越过结构化查询范围）；
- QueryLog 轮次窗口与重复问题排序；
- 语音入口依赖的共享函数保持原行为。
"""
import inspect
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import College, User
from apps.chat import views as chat_views
from apps.chat.models import ChatMessage, Conversation, QueryLog
from apps.knowledge.models import KnowledgeDoc
from utils.data_query import build_data_query_prompt, execute_query
from utils.query_router import _extract_file_keywords, route_question

FILE_A = '成都东软学院学生培养成本专项审计报告'
FILE_B = '成都东软学院国家奖学金名额分配公示'
FILE_C = '成都东软学院实验室安全检查通报'


def beijing(year, month, day, hour=0, minute=0, second=0):
    """构造北京时间点（测试写入 created_at/indexed_at 用）。"""
    return datetime(
        year, month, day, hour, minute, second,
        tzinfo=dt_timezone(timedelta(hours=8)),
    )


class ChatContextTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.college_a = College.objects.create(name='计算机学院', code='CS')
        cls.college_b = College.objects.create(name='外语学院', code='FL')
        cls.super_admin = User.objects.create_user(
            username='super', password='x', role='super_admin',
        )
        cls.admin_a = User.objects.create_user(
            username='admin_a', password='x', role='admin', college=cls.college_a,
        )
        cls.admin_b = User.objects.create_user(
            username='admin_b', password='x', role='admin', college=cls.college_b,
        )
        cls.normal = User.objects.create_user(
            username='normal', password='x', role='user', college=cls.college_a,
        )

    def setUp(self):
        self.client = APIClient()

    # ---------------- fixtures ----------------
    def _doc(self, title, uploader, created_at, indexed_at=None,
             college=None, department=None, visibility='public'):
        doc = KnowledgeDoc.objects.create(
            title=title,
            description='',
            source_type='upload',
            file_name=f'{title}.pdf',
            file_type='pdf',
            visibility=visibility,
            college=college,
            department=department,
            uploader=uploader,
            status='indexed',
        )
        KnowledgeDoc.objects.filter(pk=doc.pk).update(
            created_at=created_at, indexed_at=indexed_at,
        )
        doc.refresh_from_db()
        return doc

    def _conv(self, user, title='会话'):
        return Conversation.objects.create(user=user, title=title)

    def _ask(self, user, conversation, question):
        """调用文字问答接口，返回 (response, 捕获的 messages 列表)。"""
        self.client.force_authenticate(user=user)
        captured = []

        def fake_stream(messages, **kwargs):
            captured.append(messages)

            def _gen():
                yield '已查询。'

            return _gen()

        with patch(
            'apps.chat.views.llm_client.chat_stream', side_effect=fake_stream,
        ), patch(
            'apps.chat.views.rag.generate_suggested_questions', return_value=[],
        ):
            response = self.client.post(
                '/api/chat/ask/',
                {'conversation_id': conversation.id, 'question': question},
                format='json',
            )
            if response.status_code == 200:
                # 消费 SSE 流，触发查询与提示词构造
                list(response.streaming_content)
        return response, captured

    @staticmethod
    def _system(captured):
        return captured[-1][0]['content']


class TextContextInheritanceTests(ChatContextTestBase):
    def test_followup_inherits_previous_filename(self):
        doc = self._doc(
            FILE_A, self.admin_a, beijing(2026, 10, 1, 9, 0),
            college=self.college_a,
        )
        conv = self._conv(self.admin_a)

        first, _ = self._ask(self.admin_a, conv, f'{FILE_A}是什么时候上传的')
        self.assertEqual(first.status_code, 200)

        second, captured = self._ask(
            self.admin_a, conv, '这个文件什么时候上传的',
        )
        self.assertEqual(second.status_code, 200)
        system = self._system(captured)
        self.assertIn('2026-10-01 09:00:00', system)
        self.assertIn(FILE_A, system)
        self.assertNotIn('当前状态为 need_filename', system)
        self.assertNotIn('context_ambiguous', system)
        self.assertTrue(KnowledgeDoc.objects.filter(pk=doc.pk).exists())

    def test_followup_from_rag_query_log(self):
        doc = self._doc(
            FILE_A, self.admin_a, beijing(2026, 10, 2, 10, 0),
            college=self.college_a,
        )
        conv = self._conv(self.admin_a)
        turn = ChatMessage.objects.create(
            conversation=conv, role='user', content='查一下最近的审计报告',
        )
        log = QueryLog.objects.create(
            user=self.admin_a, conversation=conv,
            question=turn.content, top_doc_ids=[doc.id],
        )
        ChatMessage.objects.create(
            conversation=conv, role='assistant', content='已找到。',
        )

        response, captured = self._ask(
            self.admin_a, conv, '这个文件什么时候上传的',
        )
        self.assertEqual(response.status_code, 200)
        system = self._system(captured)
        self.assertIn(FILE_A, system)
        self.assertNotIn('当前状态为 need_filename', system)

    def test_ordinary_user_cannot_read_rag_only_doc_metadata(self):
        # 校级公开文档：普通用户 RAG 可见，但结构化查询范围只有本人上传
        doc = self._doc(
            FILE_A, self.super_admin, beijing(2026, 10, 3, 11, 0),
        )
        conv = self._conv(self.normal)
        turn = ChatMessage.objects.create(
            conversation=conv, role='user', content='审计报告讲了什么',
        )
        log = QueryLog.objects.create(
            user=self.normal, conversation=conv,
            question=turn.content, top_doc_ids=[doc.id],
        )

        response, captured = self._ask(
            self.normal, conv, '这个文件什么时候上传的',
        )
        self.assertEqual(response.status_code, 200)
        system = self._system(captured)
        # 不得返回时间、标题，也不得暴露“存在但不可见”
        self.assertNotIn(FILE_A, system)
        self.assertNotIn('2026-10-03 11:00:00', system)
        self.assertNotIn('not_visible', system)
        self.assertIn('当前状态为 not_found', system)

    def test_multiple_candidates_require_clarification(self):
        doc_a = self._doc(
            FILE_A, self.admin_a, timezone.now() - timedelta(days=4),
            college=self.college_a,
        )
        doc_b = self._doc(
            FILE_B, self.admin_a, timezone.now() - timedelta(days=5),
            college=self.college_a,
        )
        conv = self._conv(self.admin_a)
        turn = ChatMessage.objects.create(
            conversation=conv, role='user', content='最近的审计和奖学金文件',
        )
        log = QueryLog.objects.create(
            user=self.admin_a, conversation=conv,
            question=turn.content, top_doc_ids=[doc_a.id, doc_b.id],
        )

        response, captured = self._ask(
            self.admin_a, conv, '这个文件什么时候上传的',
        )
        self.assertEqual(response.status_code, 200)
        system = self._system(captured)
        self.assertIn('当前状态为 ambiguous', system)
        # 不得自动选择其中一份，也不得把候选文件名塞进结果
        self.assertNotIn(FILE_A, system)
        self.assertNotIn(FILE_B, system)

    def test_current_filename_overrides_history(self):
        doc_a = self._doc(
            FILE_A, self.admin_a, timezone.now() - timedelta(days=6),
            college=self.college_a,
        )
        doc_b = self._doc(
            FILE_B, self.admin_a, timezone.now() - timedelta(days=7),
            college=self.college_a,
        )
        conv = self._conv(self.admin_a)
        self._ask(self.admin_a, conv, f'{FILE_A}是什么时候上传的')

        response, captured = self._ask(
            self.admin_a, conv, f'《{FILE_B}》是什么时候上传的',
        )
        self.assertEqual(response.status_code, 200)
        system = self._system(captured)
        self.assertIn(FILE_B, system)
        self.assertNotIn(FILE_A, system)
        self.assertNotEqual(doc_a.id, doc_b.id)

    def test_intermediate_turn_file_wins(self):
        self._doc(
            FILE_A, self.admin_a, timezone.now() - timedelta(days=8),
            college=self.college_a,
        )
        self._doc(
            FILE_B, self.admin_a, timezone.now() - timedelta(days=9),
            college=self.college_a,
        )
        conv = self._conv(self.admin_a)
        self._ask(self.admin_a, conv, f'{FILE_A}是什么时候上传的')
        self._ask(self.admin_a, conv, f'《{FILE_B}》是什么时候上传的')

        response, captured = self._ask(
            self.admin_a, conv, '这个文件什么时候上传的',
        )
        self.assertEqual(response.status_code, 200)
        system = self._system(captured)
        self.assertIn(FILE_B, system)
        self.assertNotIn(FILE_A, system)

    def test_new_conversation_does_not_inherit(self):
        self._doc(
            FILE_A, self.admin_a, timezone.now() - timedelta(days=10),
            college=self.college_a,
        )
        old_conv = self._conv(self.admin_a, '旧会话')
        self._ask(self.admin_a, old_conv, f'{FILE_A}是什么时候上传的')

        new_conv = self._conv(self.admin_a, '新会话')
        response, captured = self._ask(
            self.admin_a, new_conv, '这个文件什么时候上传的',
        )
        self.assertEqual(response.status_code, 200)
        system = self._system(captured)
        self.assertIn('当前状态为 need_filename', system)
        self.assertNotIn(FILE_A, system)

    def test_cross_user_conversation_is_rejected(self):
        conv = self._conv(self.admin_a)
        response, _ = self._ask(self.admin_b, conv, '这个文件什么时候上传的')
        self.assertEqual(response.status_code, 404)

    def test_revoked_permission_after_first_turn(self):
        doc = self._doc(
            FILE_A, self.admin_a, beijing(2026, 10, 5, 8, 0),
            college=self.college_a,
        )
        conv = self._conv(self.admin_a)
        self._ask(self.admin_a, conv, f'{FILE_A}是什么时候上传的')

        # 文件改属外院且更换上传人：当前 admin_a 不再有权限
        KnowledgeDoc.objects.filter(pk=doc.pk).update(
            college=self.college_b, uploader=self.admin_b,
        )

        response, captured = self._ask(
            self.admin_a, conv, '这个文件什么时候上传的',
        )
        self.assertEqual(response.status_code, 200)
        system = self._system(captured)
        self.assertNotIn(FILE_A, system)
        self.assertNotIn('2026-10-05 08:00:00', system)
        self.assertIn('当前状态为 need_filename', system)

    def test_deleted_doc_after_first_turn(self):
        doc = self._doc(
            FILE_A, self.admin_a, timezone.now() - timedelta(days=12),
            college=self.college_a,
        )
        conv = self._conv(self.admin_a)
        self._ask(self.admin_a, conv, f'{FILE_A}是什么时候上传的')
        doc.delete()

        response, captured = self._ask(
            self.admin_a, conv, '这个文件什么时候上传的',
        )
        self.assertEqual(response.status_code, 200)
        system = self._system(captured)
        self.assertNotIn(FILE_A, system)
        self.assertIn('当前状态为 need_filename', system)

    def test_anonymous_data_query_rejected(self):
        # 既有缺陷（不在本次修复范围）：AskView 的匿名分支引用了不存在的
        # ErrorCode.UNAUTHORIZED，实际返回 500 而非 401。此处只锁定安全属性：
        # 请求未落库、未进入文件查询。缺陷已在实施报告中单独说明。
        question = '这个文件什么时候上传的'
        with self.assertLogs('django.request', level='ERROR'):
            response = APIClient().post(
                '/api/chat/ask/', {'question': question}, format='json',
            )
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(Conversation.objects.count(), 0)


class QueryLogWindowTests(ChatContextTestBase):
    def test_repeated_question_uses_current_turn_log(self):
        doc_a = self._doc(
            FILE_A, self.admin_a, timezone.now() - timedelta(days=20),
            college=self.college_a,
        )
        doc_b = self._doc(
            FILE_B, self.admin_a, timezone.now() - timedelta(days=21),
            college=self.college_a,
        )
        conv = self._conv(self.admin_a)

        turn_1 = ChatMessage.objects.create(
            conversation=conv, role='user', content='最近的审计和奖学金文件',
        )
        log_1 = QueryLog.objects.create(
            user=self.admin_a, conversation=conv,
            question=turn_1.content, top_doc_ids=[doc_a.id],
        )

        turn_2 = ChatMessage.objects.create(
            conversation=conv, role='user', content='最近的审计和奖学金文件',
        )
        log_2 = QueryLog.objects.create(
            user=self.admin_a, conversation=conv,
            question=turn_2.content, top_doc_ids=[doc_b.id],
        )

        response, captured = self._ask(
            self.admin_a, conv, '这个文件什么时候上传的',
        )
        self.assertEqual(response.status_code, 200)
        system = self._system(captured)
        self.assertIn(FILE_B, system)
        self.assertNotIn(FILE_A, system)

    def test_latest_log_wins_within_same_turn(self):
        doc_a = self._doc(
            FILE_A, self.admin_a, timezone.now() - timedelta(days=22),
            college=self.college_a,
        )
        doc_b = self._doc(
            FILE_B, self.admin_a, timezone.now() - timedelta(days=23),
            college=self.college_a,
        )
        conv = self._conv(self.admin_a)
        turn = ChatMessage.objects.create(
            conversation=conv, role='user', content='最近的文件',
        )
        first = QueryLog.objects.create(
            user=self.admin_a, conversation=conv,
            question=turn.content, top_doc_ids=[doc_a.id],
        )
        second = QueryLog.objects.create(
            user=self.admin_a, conversation=conv,
            question=turn.content, top_doc_ids=[doc_b.id],
        )
        # 两条日志自然创建顺序由 -created_at/-id 保证；不再人为修改时间戳，
        # 否则可能落到“当前提问”之后而被轮次窗口排除。

        response, captured = self._ask(
            self.admin_a, conv, '这个文件什么时候上传的',
        )
        self.assertEqual(response.status_code, 200)
        system = self._system(captured)
        self.assertIn(FILE_B, system)
        self.assertNotIn(FILE_A, system)

    def test_context_is_conversation_scoped(self):
        doc = self._doc(
            FILE_C, self.admin_a, timezone.now() - timedelta(days=24),
            college=self.college_a,
        )
        other_conv = self._conv(self.admin_a, '另一个会话')
        turn = ChatMessage.objects.create(
            conversation=other_conv, role='user', content='实验室安全',
        )
        log = QueryLog.objects.create(
            user=self.admin_a, conversation=other_conv,
            question=turn.content, top_doc_ids=[doc.id],
        )

        target_conv = self._conv(self.admin_a, '当前会话')
        response, captured = self._ask(
            self.admin_a, target_conv, '这个文件什么时候上传的',
        )
        self.assertEqual(response.status_code, 200)
        system = self._system(captured)
        self.assertNotIn(FILE_C, system)
        self.assertIn('当前状态为 need_filename', system)


class VoiceIsolationTests(ChatContextTestBase):
    """语音入口仍调用共享旧函数，行为必须保持原样。"""

    def test_legacy_route_keeps_pronoun_keywords(self):
        route = route_question('刚才那个文件什么时候上传的')
        self.assertEqual(route['query_type'], 'doc_upload_time')
        self.assertEqual(route['params'].get('keywords'), ['刚才'])
        self.assertNotIn('doc_id', route['params'])

    def test_legacy_route_has_no_doc_id(self):
        route = route_question('这个文件是什么时候上传的')
        self.assertEqual(route['query_type'], 'doc_upload_time')
        self.assertNotIn('doc_id', route['params'])
        self.assertNotIn('keywords', route['params'])

    def test_legacy_keyword_extraction_unchanged(self):
        # 旧提取器按原样保留：标点被替换为空格后得到裸数字 '6'，由文字入口的
        # 新清理函数丢弃；此处锁定旧行为，证明共享函数未被改动。
        self.assertIn('6', _extract_file_keywords('附件6.关于国家奖学金的公示是什么时候上传的'))
        self.assertEqual(
            _extract_file_keywords('刚才那个文件什么时候上传的'), ['刚才'],
        )

    def test_legacy_execute_query_has_no_status_key(self):
        doc = self._doc(
            FILE_A, self.admin_a, timezone.now() - timedelta(days=25),
            college=self.college_a,
        )
        route = route_question(f'{FILE_A}是什么时候上传的')
        result = execute_query(route, self.admin_a)
        self.assertTrue(result['success'])
        self.assertNotIn('status', result)
        self.assertNotIn('context_ambiguous', result['data'])
        self.assertEqual(result['data']['matches'][0]['id'], doc.id)

    def test_legacy_prompt_has_no_text_only_rules(self):
        route = route_question('这个文件是什么时候上传的')
        result = execute_query(route, self.admin_a)
        system = build_data_query_prompt('这个文件是什么时候上传的', result)[0]['content']
        self.assertNotIn('当前状态为', system)
        self.assertNotIn('query_error', system)
        self.assertNotIn('context_ambiguous', system)

    def test_voice_source_does_not_use_text_helpers(self):
        source = inspect.getsource(chat_views.VoiceAskView._stream_answer)
        self.assertIn('route_question(question, user)', source)
        self.assertIn('build_data_query_prompt(question, query_result)', source)
        for helper in (
            'route_text_question', 'build_text_data_query_prompt',
            'annotate_doc_time_status', '_resolve_doc_context',
        ):
            self.assertNotIn(helper, source)

    def test_shared_function_signatures_unchanged(self):
        self.assertEqual(
            list(inspect.signature(route_question).parameters),
            ['question', 'user'],
        )
        self.assertEqual(
            list(inspect.signature(execute_query).parameters),
            ['route', 'user'],
        )
        self.assertEqual(
            list(inspect.signature(build_data_query_prompt).parameters),
            ['question', 'result'],
        )
