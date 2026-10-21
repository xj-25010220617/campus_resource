"""AI 上传时间/精确日期查询专项测试。

覆盖：业务时区转换、自然语言日期区间、区间执行、权限范围、
上传时间与索引时间分离、截断提示、SSE/序列化字段。
"""
from datetime import date, datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.accounts.models import College, User
from apps.chat.models import ChatMessage, Conversation
from apps.chat.serializers import ChatMessageSerializer
from apps.classify.models import Category, Department
from apps.knowledge.models import KnowledgeDoc
from apps.knowledge.serializers import (
    KnowledgeDocListSerializer,
)
from utils.data_query import (
    annotate_doc_time_status,
    build_data_query_prompt,
    build_text_data_query_prompt,
    count_user_docs,
    execute_query,
    get_doc_times,
    list_docs_by_range,
)
from utils.query_router import (
    _extract_date_range,
    _extract_file_keywords,
    _extract_scope,
    route_question,
    route_text_question,
)
from utils.sse import build_references_events
from utils.timefmt import (
    day_bounds,
    format_business_time,
    month_bounds,
    recent_days_bounds,
    to_business_time,
    to_iso,
    year_bounds,
)

UTC = dt_timezone.utc
# 北京时间 2026-10-21 10:00（周三）
FIXED_NOW = datetime(2026, 10, 21, 2, 0, tzinfo=UTC)


def beijing_to_utc(year, month, day, hour=0, minute=0, second=0):
    """把北京时间墙钟转换为 UTC datetime（测试数据写入用）。"""
    return datetime(
        year, month, day, hour, minute, second, tzinfo=dt_timezone(timedelta(hours=8)),
    ).astimezone(UTC)


class TimeFormatTests(TestCase):
    def test_utc_aware_converted_to_beijing(self):
        value = datetime(2026, 10, 20, 16, 30, tzinfo=UTC)
        self.assertEqual(format_business_time(value), '2026-10-21 00:30:00')
        self.assertEqual(to_iso(value), '2026-10-21T00:30:00+08:00')

    def test_naive_interpreted_as_business_timezone(self):
        value = datetime(2026, 10, 20, 16, 30)
        converted = to_business_time(value)
        self.assertEqual(converted.utcoffset(), timedelta(hours=8))
        self.assertEqual(format_business_time(value), '2026-10-20 16:30:00')

    def test_none_and_invalid_are_safe(self):
        self.assertEqual(format_business_time(None), '')
        self.assertEqual(format_business_time('not-a-date'), '')
        self.assertIsNone(to_iso(None))

    def test_date_value_becomes_local_midnight(self):
        self.assertEqual(
            format_business_time(date(2026, 10, 21)), '2026-10-21 00:00:00',
        )

    def test_day_month_year_bounds_are_local_and_half_open(self):
        start, end = day_bounds(date(2026, 10, 21))
        self.assertEqual(to_iso(start), '2026-10-21T00:00:00+08:00')
        self.assertEqual(to_iso(end), '2026-10-22T00:00:00+08:00')

        start, end = month_bounds(2026, 12)
        self.assertEqual(to_iso(start), '2026-12-01T00:00:00+08:00')
        self.assertEqual(to_iso(end), '2027-01-01T00:00:00+08:00')

        start, end = year_bounds(2025)
        self.assertEqual(to_iso(start), '2025-01-01T00:00:00+08:00')
        self.assertEqual(to_iso(end), '2026-01-01T00:00:00+08:00')

    def test_recent_days_includes_today(self):
        start, end = recent_days_bounds(30, date(2026, 10, 21))
        self.assertEqual(to_iso(start), '2026-09-22T00:00:00+08:00')
        self.assertEqual(to_iso(end), '2026-10-22T00:00:00+08:00')


class DateRangeParsingTests(TestCase):
    def test_yesterday_is_natural_day_not_24_hours(self):
        with patch('django.utils.timezone.now', return_value=FIXED_NOW):
            result = _extract_date_range('昨天上传了哪些文件')
        self.assertEqual(result['start_at'], '2026-10-20T00:00:00+08:00')
        self.assertEqual(result['end_at'], '2026-10-21T00:00:00+08:00')

    def test_this_year_uses_calendar_year(self):
        with patch('django.utils.timezone.now', return_value=FIXED_NOW):
            result = _extract_date_range('今年上传了多少文件')
        self.assertEqual(result['start_at'], '2026-01-01T00:00:00+08:00')
        self.assertEqual(result['end_at'], '2027-01-01T00:00:00+08:00')

    def test_last_year_uses_calendar_year(self):
        with patch('django.utils.timezone.now', return_value=FIXED_NOW):
            result = _extract_date_range('去年上传了多少文件')
        self.assertEqual(result['start_at'], '2025-01-01T00:00:00+08:00')
        self.assertEqual(result['end_at'], '2026-01-01T00:00:00+08:00')

    def test_last_month_is_calendar_month(self):
        with patch('django.utils.timezone.now', return_value=FIXED_NOW):
            result = _extract_date_range('上个月上传了哪些文件')
        self.assertEqual(result['start_at'], '2026-09-01T00:00:00+08:00')
        self.assertEqual(result['end_at'], '2026-10-01T00:00:00+08:00')

    def test_explicit_date_and_month(self):
        self.assertEqual(
            _extract_date_range('2026-10-20 上传了哪些文件')['start_at'],
            '2026-10-20T00:00:00+08:00',
        )
        self.assertEqual(
            _extract_date_range('2026年1月上传了多少文件')['end_at'],
            '2026-02-01T00:00:00+08:00',
        )

    def test_recent_defaults_to_30_days(self):
        with patch('django.utils.timezone.now', return_value=FIXED_NOW):
            result = _extract_date_range('最近上传了哪些文件')
        self.assertEqual(result['start_at'], '2026-09-22T00:00:00+08:00')
        self.assertEqual(result['end_at'], '2026-10-22T00:00:00+08:00')

    def test_recent_7_days(self):
        with patch('django.utils.timezone.now', return_value=FIXED_NOW):
            result = _extract_date_range('最近7天上传了哪些文件')
        self.assertEqual(result['start_at'], '2026-10-15T00:00:00+08:00')

    def test_explicit_numeric_month_without_marker(self):
        # “2026-10”不带“月”也是显式年月，不能被忽略成全量查询
        result = _extract_date_range('2026-10 上传了哪些文件')
        self.assertEqual(result['start_at'], '2026-10-01T00:00:00+08:00')
        self.assertEqual(result['end_at'], '2026-11-01T00:00:00+08:00')

    def test_naive_now_is_interpreted_as_business_timezone(self):
        # 传入 naive datetime 时不抛 ValueError，按业务时区解释
        result = _extract_date_range(
            '昨天上传了哪些文件', now=datetime(2026, 10, 21, 10, 0),
        )
        self.assertEqual(result['start_at'], '2026-10-20T00:00:00+08:00')

    def test_scope_does_not_treat_propagation_as_my_upload(self):
        # “我传播了…”不是“我上传的”，不能被误判成本人上传
        mine_only, _ = _extract_scope('我传播了校园新闻')
        self.assertFalse(mine_only)
        mine_only, _ = _extract_scope('我上传的课件')
        self.assertTrue(mine_only)

    def test_invalid_date_is_not_guessed(self):
        self.assertIsNone(_extract_date_range('2026-02-30 上传了哪些文件'))
        self.assertIsNone(_extract_date_range('2026年13月上传了哪些文件'))


class RouteTests(TestCase):
    def test_upload_time_question_routes_to_deterministic_query(self):
        route = route_question('《审计报告》是什么时候上传的')
        self.assertEqual(route['query_type'], 'doc_upload_time')
        self.assertEqual(route['params']['keywords'], ['审计报告'])

    def test_index_time_question_routes_to_index_query(self):
        route = route_question('这个文件的索引完成时间是什么时候')
        self.assertEqual(route['query_type'], 'doc_index_time')

    def test_yesterday_list_is_mine_only(self):
        with patch('django.utils.timezone.now', return_value=FIXED_NOW):
            route = route_question('昨天我上传了哪些文件')
        self.assertEqual(route['query_type'], 'list_docs_by_range')
        self.assertTrue(route['params']['mine_only'])
        self.assertEqual(route['params']['scope_label'], '本人上传')

    def test_last_year_count_keeps_full_year_range(self):
        with patch('django.utils.timezone.now', return_value=FIXED_NOW):
            route = route_question('去年我上传了多少文件')
        self.assertEqual(route['query_type'], 'count_docs_by_range')
        self.assertEqual(route['params']['start_at'], '2025-01-01T00:00:00+08:00')
        self.assertEqual(route['params']['end_at'], '2026-01-01T00:00:00+08:00')

    def test_system_files_list_uses_existing_scope(self):
        route = route_question('系统里有哪些文件')
        self.assertEqual(route['query_type'], 'list_docs_by_range')
        self.assertFalse(route['params']['mine_only'])
        self.assertIn('既有权限', route['params']['scope_label'])

    def test_my_count_scope(self):
        route = route_question('我上传了多少个文件')
        self.assertEqual(route['query_type'], 'count_docs')
        self.assertTrue(route['params']['mine_only'])

    def test_plain_time_question_without_filename_needs_filename(self):
        route = route_question('这个文件是什么时候上传的')
        self.assertEqual(route['query_type'], 'doc_upload_time')
        self.assertNotIn('keywords', route['params'])


class DataQueryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.college_a = College.objects.create(name='计算机学院', code='CS')
        cls.college_b = College.objects.create(name='外语学院', code='FL')
        cls.department = Department.objects.create(name='教学管理')
        cls.super_admin = User.objects.create_user(
            username='super', password='x', role='super_admin',
        )
        cls.admin_a = User.objects.create_user(
            username='admin_a', password='x', role='admin',
            college=cls.college_a,
        )
        cls.admin_b = User.objects.create_user(
            username='admin_b', password='x', role='admin',
            college=cls.college_b,
        )
        cls.normal = User.objects.create_user(
            username='normal', password='x', role='user',
            college=cls.college_a,
        )

    def _doc(
        self, title, uploader, created_at, indexed_at=None,
        college=None, department=None, status='indexed',
    ):
        doc = KnowledgeDoc.objects.create(
            title=title,
            description='',
            source_type='upload',
            file_name=f'{title}.pdf',
            file_type='pdf',
            visibility='public',
            college=college,
            department=department,
            uploader=uploader,
            status=status,
        )
        KnowledgeDoc.objects.filter(pk=doc.pk).update(
            created_at=created_at, indexed_at=indexed_at,
        )
        doc.refresh_from_db()
        return doc

    def test_yesterday_range_excludes_beijing_midnight_doc(self):
        # 北京时间 2026-10-20 23:59:59
        yesterday_doc = self._doc(
            '昨天报告', self.admin_a,
            beijing_to_utc(2026, 10, 20, 23, 59, 59),
            college=self.college_a,
        )
        # 北京时间 2026-10-21 00:00:00，属于今天
        today_doc = self._doc(
            '今天报告', self.admin_a,
            beijing_to_utc(2026, 10, 21, 0, 0, 0),
            college=self.college_a,
        )

        with patch('django.utils.timezone.now', return_value=FIXED_NOW):
            route = route_question('昨天我上传了哪些文件')
            result = execute_query(route, self.admin_a)

        self.assertTrue(result['success'])
        ids = [item['id'] for item in result['data']['items']]
        self.assertIn(yesterday_doc.id, ids)
        self.assertNotIn(today_doc.id, ids)

    def test_count_last_year_and_this_year(self):
        old_doc = self._doc(
            '旧年报告', self.admin_a,
            beijing_to_utc(2025, 6, 1, 12, 0), college=self.college_a,
        )
        new_doc = self._doc(
            '今年报告', self.admin_a,
            beijing_to_utc(2026, 3, 1, 12, 0), college=self.college_a,
        )

        with patch('django.utils.timezone.now', return_value=FIXED_NOW):
            last_year = execute_query(route_question('去年我上传了多少文件'), self.admin_a)
            this_year = execute_query(route_question('今年我上传了多少文件'), self.admin_a)

        self.assertEqual(last_year['data']['total'], 1)
        self.assertEqual(this_year['data']['total'], 1)
        self.assertNotEqual(old_doc.id, new_doc.id)

    def test_created_and_indexed_time_are_separate(self):
        created = beijing_to_utc(2026, 10, 21, 0, 30)
        indexed = beijing_to_utc(2026, 10, 21, 9, 15)
        doc = self._doc(
            '审计报告', self.admin_a, created,
            indexed_at=indexed, college=self.college_a,
        )
        result = get_doc_times(self.admin_a, keywords=['审计报告'])
        self.assertEqual(result['total'], 1)
        item = result['matches'][0]
        self.assertEqual(item['created_at_display'], '2026-10-21 00:30:00')
        self.assertEqual(item['indexed_at_display'], '2026-10-21 09:15:00')
        self.assertEqual(item['created_at'], '2026-10-21T00:30:00+08:00')

    def test_null_index_time_is_not_faked_from_created_at(self):
        doc = self._doc(
            '未索引完成', self.admin_a,
            beijing_to_utc(2026, 10, 20, 8, 0), college=self.college_a,
        )
        result = get_doc_times(self.admin_a, keywords=['未索引完成'])
        item = result['matches'][0]
        self.assertIsNone(item['indexed_at'])
        self.assertEqual(item['indexed_at_display'], '')
        self.assertEqual(item['created_at_display'], '2026-10-20 08:00:00')

    def test_not_found_and_need_filename(self):
        self.assertTrue(get_doc_times(self.admin_a, keywords=['不存在'])['not_found'])
        self.assertTrue(get_doc_times(self.admin_a)['need_filename'])

    def test_duplicate_titles_are_ambiguous(self):
        self._doc('同名文件', self.admin_a, beijing_to_utc(2026, 10, 1))
        self._doc('同名文件', self.admin_a, beijing_to_utc(2026, 10, 2))
        result = get_doc_times(self.admin_a, keywords=['同名文件'])
        self.assertTrue(result['ambiguous'])
        self.assertEqual(result['total'], 2)

    def test_admin_scope_does_not_widen(self):
        school_doc = self._doc(
            '校级文件', self.super_admin, beijing_to_utc(2026, 10, 1),
        )
        a_doc = self._doc(
            '本院文件', self.admin_b, beijing_to_utc(2026, 10, 1),
            college=self.college_a,
        )
        b_doc = self._doc(
            '外院文件', self.admin_b, beijing_to_utc(2026, 10, 1),
            college=self.college_b,
        )
        own_doc = self._doc(
            '我上传的外院文件', self.admin_a, beijing_to_utc(2026, 10, 1),
            college=self.college_b,
        )

        data = count_user_docs(self.admin_a)
        self.assertEqual(data['total'], 3)  # 校级 + 本院 + 自己上传
        self.assertEqual(count_user_docs(self.super_admin)['total'], 4)

        b_times = get_doc_times(self.admin_a, keywords=['外院文件'])
        # “外院文件”同时模糊命中“我上传的外院文件”，但绝不含 admin_b 的外院文件
        self.assertNotIn(b_doc.id, [item['id'] for item in b_times['matches']])
        self.assertIn(own_doc.id, [item['id'] for item in b_times['matches']])
        self.assertIn(a_doc.id, [item['id'] for item in get_doc_times(
            self.admin_a, keywords=['本院文件'],
        )['matches']])
        self.assertIn(school_doc.id, [item['id'] for item in get_doc_times(
            self.admin_a, keywords=['校级文件'],
        )['matches']])

    def test_normal_user_only_sees_own_docs(self):
        own = self._doc(
            '我的文件', self.normal, beijing_to_utc(2026, 10, 1),
            college=self.college_a,
        )
        self._doc(
            '同院他人文件', self.admin_a, beijing_to_utc(2026, 10, 1),
            college=self.college_a,
        )
        data = count_user_docs(self.normal)
        self.assertEqual(data['total'], 1)
        result = get_doc_times(self.normal, keywords=['同院他人文件'])
        self.assertTrue(result['not_found'])
        self.assertEqual(
            get_doc_times(self.normal, keywords=['我的文件'])['matches'][0]['id'],
            own.id,
        )

    def test_mine_only_for_admin(self):
        self._doc(
            '本院他人文件', self.admin_b, beijing_to_utc(2026, 10, 1),
            college=self.college_a,
        )
        own = self._doc(
            '我自己的文件', self.admin_a, beijing_to_utc(2026, 10, 1),
            college=self.college_a,
        )
        data = count_user_docs(self.admin_a, mine_only=True)
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['scope_label'], '本人上传')
        self.assertEqual(get_doc_times(
            self.admin_a, keywords=['本院他人文件'], mine_only=True,
        )['total'], 0)
        self.assertEqual(get_doc_times(
            self.admin_a, keywords=['我自己的文件'], mine_only=True,
        )['matches'][0]['id'], own.id)

    def test_list_truncation_reports_total(self):
        for i in range(25):
            self._doc(
                f'批量文件{i}', self.admin_a,
                beijing_to_utc(2026, 10, 20, 8, 0) + timedelta(minutes=i),
                college=self.college_a,
            )
        data = list_docs_by_range(
            self.admin_a,
            start_at='2026-10-20T00:00:00+08:00',
            end_at='2026-10-21T00:00:00+08:00',
            mine_only=True,
            limit=20,
        )
        self.assertEqual(data['total'], 25)
        self.assertEqual(data['shown'], 20)
        self.assertTrue(data['truncated'])
        self.assertIn('还有更多', data['notice'])

    def test_invalid_range_param_returns_error_not_silent(self):
        result = execute_query({
            'query_type': 'list_docs_by_range',
            'params': {'start_at': 'not-a-date', 'mine_only': True},
        }, self.admin_a)
        self.assertFalse(result['success'])
        self.assertIn('start_at', result['error'])

    def test_range_start_is_really_applied(self):
        self._doc(
            '区间外', self.admin_a, beijing_to_utc(2026, 9, 1),
            college=self.college_a,
        )
        inside = self._doc(
            '区间内', self.admin_a, beijing_to_utc(2026, 10, 10),
            college=self.college_a,
        )
        data = list_docs_by_range(
            self.admin_a,
            start_at='2026-10-01T00:00:00+08:00',
            end_at='2026-11-01T00:00:00+08:00',
            mine_only=True,
        )
        self.assertEqual([item['id'] for item in data['items']], [inside.id])

    def test_prompt_warns_against_fake_times(self):
        doc = self._doc(
            '提示测试', self.admin_a, beijing_to_utc(2026, 10, 21, 0, 30),
            college=self.college_a,
        )
        result = execute_query(
            route_question('提示测试是什么时候上传的'), self.admin_a,
        )
        messages = build_data_query_prompt('提示测试是什么时候上传的', result)
        system = messages[0]['content']
        self.assertIn('created_at_display', system)
        self.assertIn('indexed_at', system)
        self.assertIn('北京时间', system)
        self.assertIn('2026-10-21 00:30:00', system)
        self.assertIn(doc.title, system)

    def test_sse_references_include_upload_and_index_times(self):
        doc = self._doc(
            'SSE 引用文件', self.super_admin,
            beijing_to_utc(2026, 10, 21, 0, 30),
            indexed_at=beijing_to_utc(2026, 10, 21, 9, 0),
        )
        events = build_references_events(
            [{'doc_id': doc.id, 'score': 0.9, 'content': '片段', 'page': 1}],
            'super_admin', self.super_admin.id,
            user_context={'role': 'super_admin', 'user_id': self.super_admin.id},
        )
        detail = events['references_detail'][0]
        self.assertEqual(detail['created_at_display'], '2026-10-21 00:30:00')
        self.assertEqual(detail['indexed_at_display'], '2026-10-21 09:00:00')
        self.assertEqual(detail['created_at'], '2026-10-21T00:30:00+08:00')

    def test_serializer_exposes_display_fields(self):
        doc = self._doc(
            '序列化文件', self.admin_a,
            beijing_to_utc(2026, 10, 21, 0, 30),
            indexed_at=beijing_to_utc(2026, 10, 21, 9, 0),
            college=self.college_a,
        )
        data = KnowledgeDocListSerializer(doc).data
        self.assertEqual(data['created_at_display'], '2026-10-21 00:30:00')
        self.assertEqual(data['indexed_at_display'], '2026-10-21 09:00:00')

    def test_history_references_are_preserved(self):
        conv = Conversation.objects.create(user=self.admin_a, title='历史')
        refs = [{
            'doc_id': 1, 'doc_title': '旧文件',
            'created_at': '2026-10-20T16:30:00+08:00',
            'created_at_display': '2026-10-21 00:30:00',
            'indexed_at': None, 'indexed_at_display': '',
        }]
        msg = ChatMessage.objects.create(
            conversation=conv, role='assistant', content='回答', references=refs,
        )
        data = ChatMessageSerializer(msg).data
        self.assertEqual(data['references'][0]['created_at_display'], '2026-10-21 00:30:00')
        self.assertEqual(data['references'][0]['doc_title'], '旧文件')


# ------------------------------------------------------------------
# 文件名内部的日期不得被当作查询日期条件
# ------------------------------------------------------------------

FILENAME = '成都东软学院2025年学生培养成本专项审计报告'


class FileNameDateParsingTests(TestCase):
    def test_filename_with_extension_year_is_ignored(self):
        self.assertIsNone(_extract_date_range(f'{FILENAME}.pdf是几点上传的'))

    def test_quoted_filename_year_is_ignored(self):
        self.assertIsNone(_extract_date_range(f'《{FILENAME}.pdf》是几点上传的'))

    def test_filename_without_extension_year_is_ignored(self):
        self.assertIsNone(_extract_date_range(f'{FILENAME}是几点上传的'))

    def test_filename_with_full_date_is_ignored(self):
        name = '成都东软学院2026年10月20日培训方案.pdf'
        self.assertIsNone(_extract_date_range(f'{name}是几点上传的'))

    def test_real_year_condition_still_works(self):
        result = _extract_date_range('2025年有哪些文件上传了')
        self.assertEqual(result['start_at'], '2025-01-01T00:00:00+08:00')
        self.assertEqual(result['end_at'], '2026-01-01T00:00:00+08:00')

    def test_real_month_condition_still_works(self):
        result = _extract_date_range('2025年10月有哪些文件上传了')
        self.assertEqual(result['start_at'], '2025-10-01T00:00:00+08:00')
        self.assertEqual(result['end_at'], '2025-11-01T00:00:00+08:00')

    def test_real_full_date_condition_still_works(self):
        result = _extract_date_range('2026年10月20日上传了哪些文件')
        self.assertEqual(result['start_at'], '2026-10-20T00:00:00+08:00')
        self.assertEqual(result['end_at'], '2026-10-21T00:00:00+08:00')

    def test_year_outside_filename_is_kept(self):
        # 只屏蔽文件名片段，不删除用户真正的年份条件
        result = _extract_date_range('2025年上传的《审计报告.pdf》都有哪些')
        self.assertEqual(result['start_at'], '2025-01-01T00:00:00+08:00')

    def test_leading_instruction_does_not_break_year(self):
        result = _extract_date_range('请问2025年上传了多少文件')
        self.assertEqual(result['start_at'], '2025-01-01T00:00:00+08:00')

    def test_yesterday_with_filename_still_keeps_range(self):
        # 用户明确表达的相对日期必须保留
        with patch('django.utils.timezone.now', return_value=FIXED_NOW):
            result = _extract_date_range('昨天上传的文件中，审计报告.pdf 是几点上传的')
        self.assertEqual(result['start_at'], '2026-10-20T00:00:00+08:00')
        self.assertEqual(result['end_at'], '2026-10-21T00:00:00+08:00')

    def test_extension_token_is_not_a_file_keyword(self):
        keywords = _extract_file_keywords(f'{FILENAME}.pdf是几点上传的')
        self.assertIn('学生培养成本专项审计报告', keywords)
        self.assertNotIn('pdf', [item.lower() for item in keywords])


class FileNameRouteTests(TestCase):
    def test_full_filename_routes_to_upload_time_without_date_filter(self):
        route = route_question(f'{FILENAME}.pdf是几点上传的')
        self.assertEqual(route['query_type'], 'doc_upload_time')
        self.assertNotIn('start_at', route['params'])
        self.assertNotIn('end_at', route['params'])
        self.assertIn('学生培养成本专项审计报告', route['params']['keywords'])
        self.assertNotIn('pdf', [k.lower() for k in route['params']['keywords']])
        # 精确文件时间查询不应生成下游不支持的假过滤
        self.assertNotIn('file_type', route['params'])

    def test_quoted_full_filename_routes_the_same_way(self):
        route = route_question(f'《{FILENAME}.pdf》是几点上传的')
        self.assertEqual(route['query_type'], 'doc_upload_time')
        self.assertNotIn('start_at', route['params'])
        self.assertEqual(route['params']['keywords'], [f'{FILENAME}.pdf'])

    def test_filename_without_extension_routes_to_upload_time(self):
        route = route_question(f'{FILENAME}是几点上传的')
        self.assertEqual(route['query_type'], 'doc_upload_time')
        self.assertNotIn('start_at', route['params'])

    def test_index_time_question_routes_to_index_query(self):
        route = route_question(f'{FILENAME}.pdf是什么时候完成索引的')
        self.assertEqual(route['query_type'], 'doc_index_time')
        self.assertNotIn('start_at', route['params'])

    def test_year_list_question_is_still_a_range_list(self):
        route = route_question('2025年有哪些文件上传了')
        self.assertEqual(route['query_type'], 'list_docs_by_range')
        self.assertEqual(route['params']['start_at'], '2025-01-01T00:00:00+08:00')

    def test_month_list_question_is_still_a_range_list(self):
        route = route_question('2025年10月有哪些文件上传了')
        self.assertEqual(route['query_type'], 'list_docs_by_range')
        self.assertEqual(route['params']['start_at'], '2025-10-01T00:00:00+08:00')

    def test_explicit_range_with_filename_is_kept(self):
        with patch('django.utils.timezone.now', return_value=FIXED_NOW):
            route = route_question('昨天上传的文件中，审计报告.pdf 是几点上传的')
        self.assertEqual(route['query_type'], 'doc_upload_time')
        self.assertEqual(route['params']['start_at'], '2026-10-20T00:00:00+08:00')
        self.assertIn('审计报告', route['params']['keywords'])

    def test_bare_file_reference_keeps_need_filename_path(self):
        route = route_question('这个文件是几点上传的')
        self.assertEqual(route['query_type'], 'doc_upload_time')
        self.assertNotIn('keywords', route['params'])


class FileNameQueryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.college = College.objects.create(name='计算机学院', code='CS')
        cls.category = Category.objects.create(name='培养方案')
        cls.admin = User.objects.create_user(
            username='admin_fn', password='x', role='admin', college=cls.college,
        )

    def _doc(
        self, title, created_at, file_type='pdf', category=None,
        file_name=None, uploader=None,
    ):
        doc = KnowledgeDoc.objects.create(
            title=title,
            description='',
            source_type='upload',
            file_name=file_name or f'{title}.pdf',
            file_type=file_type,
            visibility='public',
            college=self.college,
            category=category,
            uploader=uploader or self.admin,
            status='indexed',
        )
        KnowledgeDoc.objects.filter(pk=doc.pk).update(created_at=created_at)
        doc.refresh_from_db()
        return doc

    def test_file_with_year_in_name_is_found(self):
        target = self._doc(
            FILENAME, beijing_to_utc(2026, 10, 20, 11, 35, 20),
            file_name=f'{FILENAME}.pdf',
        )
        result = execute_query(
            route_question(f'{FILENAME}.pdf是几点上传的'), self.admin,
        )
        data = result['data']
        self.assertTrue(result['success'])
        self.assertFalse(data['not_found'])
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['matches'][0]['id'], target.id)
        self.assertEqual(data['matches'][0]['created_at_display'], '2026-10-20 11:35:20')

    def test_quoted_filename_also_finds_the_file(self):
        target = self._doc(
            FILENAME, beijing_to_utc(2026, 10, 20, 11, 35, 20),
            file_name=f'{FILENAME}.pdf',
        )
        data = execute_query(
            route_question(f'《{FILENAME}.pdf》是几点上传的'), self.admin,
        )['data']
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['matches'][0]['id'], target.id)

    def test_extension_token_does_not_widen_candidates(self):
        target = self._doc(
            FILENAME, beijing_to_utc(2026, 10, 20, 11, 0),
            file_name=f'{FILENAME}.pdf',
        )
        # 只有扩展名相同、文件名主体无关的文件不能被当作候选
        self._doc('无关文档', beijing_to_utc(2026, 10, 20, 12, 0),
                  file_name='无关文档.pdf')
        data = execute_query(
            route_question(f'{FILENAME}.pdf是几点上传的'), self.admin,
        )['data']
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['matches'][0]['id'], target.id)

    def test_missing_filename_returns_need_filename_not_a_list(self):
        self._doc('昨天报告', beijing_to_utc(2026, 10, 20, 9, 0))
        result = execute_query(
            route_question('这个文件是几点上传的'), self.admin,
        )
        data = result['data']
        self.assertTrue(result['success'])
        self.assertTrue(data['need_filename'])
        self.assertNotIn('items', data)  # 不得静默退化成文件列表
        self.assertNotIn('not_found', data)

    def test_not_found_is_distinct_from_query_error(self):
        missing = execute_query(
            route_question('完全不存在的东西.pdf是几点上传的'), self.admin,
        )
        self.assertTrue(missing['success'])
        self.assertTrue(missing['data']['not_found'])

        broken = execute_query({
            'query_type': 'doc_upload_time',
            'params': {'keywords': ['x'], 'start_at': 'not-a-date'},
        }, self.admin)
        self.assertFalse(broken['success'])
        self.assertIn('start_at', broken['error'])

    def test_explicit_date_still_constrains_the_file_query(self):
        # 若用户明确说“昨天上传的”，今天上传的文件不应被当成答案
        self._doc(
            '审计报告', beijing_to_utc(2026, 10, 21, 8, 0),
            file_name='审计报告.pdf',
        )
        with patch('django.utils.timezone.now', return_value=FIXED_NOW):
            data = execute_query(
                route_question('昨天上传的文件中，审计报告.pdf 是几点上传的'),
                self.admin,
            )['data']
        self.assertTrue(data['not_found'])
        self.assertEqual(data['range_label'], '昨天（2026-10-20）')

    def test_file_type_filter_is_applied_in_range_list(self):
        pdf_doc = self._doc('昨天课件', beijing_to_utc(2026, 10, 20, 9, 0))
        word_doc = self._doc(
            '昨天教案', beijing_to_utc(2026, 10, 20, 10, 0),
            file_type='doc', file_name='昨天教案.docx',
        )
        with patch('django.utils.timezone.now', return_value=FIXED_NOW):
            route = route_question('昨天上传了哪些Word文件')
            self.assertEqual(route['query_type'], 'list_docs_by_range')
            self.assertEqual(route['params']['file_type'], 'word')
            data = execute_query(route, self.admin)['data']
        self.assertEqual([item['id'] for item in data['items']], [word_doc.id])
        self.assertEqual(data['applied_filters'], {'file_type': 'doc'})
        self.assertNotIn(pdf_doc.id, [item['id'] for item in data['items']])

    def test_category_filter_is_applied_in_range_count(self):
        self._doc(
            '昨天的培养方案', beijing_to_utc(2026, 10, 20, 9, 0),
            category=self.category,
        )
        self._doc('昨天的其他文件', beijing_to_utc(2026, 10, 20, 10, 0))
        with patch('django.utils.timezone.now', return_value=FIXED_NOW):
            route = route_question('昨天上传了多少个培养方案文件')
            self.assertEqual(route['query_type'], 'count_docs_by_range')
            data = execute_query(route, self.admin)['data']
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['applied_filters'], {'category': '培养方案'})

    def test_unknown_file_type_does_not_filter_everything_out(self):
        # 无法归一的类型词不能被当成“过滤到 0 条”
        self._doc('昨天的报告', beijing_to_utc(2026, 10, 20, 9, 0))
        data = list_docs_by_range(
            self.admin,
            start_at='2026-10-20T00:00:00+08:00',
            end_at='2026-10-21T00:00:00+08:00',
            mine_only=True,
            file_type='不存在的类型',
        )
        self.assertEqual(data['total'], 1)
        self.assertNotIn('applied_filters', data)


class DataAnswerPromptTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.college = College.objects.create(name='计算机学院', code='CS')
        cls.admin = User.objects.create_user(
            username='admin_prompt', password='x', role='admin', college=cls.college,
        )

    def _doc(self, title, created_at):
        doc = KnowledgeDoc.objects.create(
            title=title, description='', source_type='upload',
            file_name=f'{title}.pdf', file_type='pdf', visibility='public',
            college=self.college, uploader=self.admin, status='indexed',
        )
        KnowledgeDoc.objects.filter(pk=doc.pk).update(created_at=created_at)
        return doc

    def test_list_result_has_no_download_url_field(self):
        self._doc('昨天报告', beijing_to_utc(2026, 10, 20, 9, 0))
        with patch('django.utils.timezone.now', return_value=FIXED_NOW):
            data = execute_query(
                route_question('昨天上传了哪些文件'), self.admin,
            )['data']
        self.assertTrue(data['items'])
        for item in data['items']:
            self.assertNotIn('download_url', item)

    def test_prompt_does_not_claim_download_url_always_exists(self):
        self._doc('昨天报告', beijing_to_utc(2026, 10, 20, 9, 0))
        with patch('django.utils.timezone.now', return_value=FIXED_NOW):
            result = execute_query(route_question('昨天上传了哪些文件'), self.admin)
        system = build_data_query_prompt('昨天上传了哪些文件', result)[0]['content']
        self.assertIn('download_url', system)
        self.assertIn('禁止自行拼接', system)
        self.assertNotIn('每条数据包含 download_url 字段', system)

    def test_prompt_explains_shown_vs_total(self):
        for i in range(25):
            self._doc(
                f'批量文件{i}',
                beijing_to_utc(2026, 10, 20, 8, 0) + timedelta(minutes=i),
            )
        with patch('django.utils.timezone.now', return_value=FIXED_NOW):
            result = execute_query(route_question('昨天上传了哪些文件'), self.admin)
        self.assertEqual(result['data']['total'], 25)
        self.assertEqual(result['data']['shown'], 20)
        system = build_data_query_prompt('昨天上传了哪些文件', result)[0]['content']
        self.assertIn('绝不能把 shown 当成总数', system)

    def test_prompt_forbids_claiming_db_wide_absence(self):
        result = execute_query(
            route_question('完全不存在的东西.pdf是几点上传的'), self.admin,
        )
        self.assertTrue(result['data']['not_found'])
        system = build_data_query_prompt(
            '完全不存在的东西.pdf是几点上传的', result,
        )[0]['content']
        self.assertIn('不能据此断言文件在整个数据库中不存在', system)


class TextRouteContextTests(TestCase):
    """文字入口专用路由：上下文继承与当前问题优先。"""

    def test_unique_context_injects_doc_id(self):
        route = route_text_question(
            '这个文件什么时候上传的',
            context={'doc_ids': [42], 'ambiguous': False},
        )
        self.assertEqual(route['type'], 'data_query')
        self.assertEqual(route['query_type'], 'doc_upload_time')
        self.assertEqual(route['params']['doc_id'], 42)
        self.assertNotIn('keywords', route['params'])

    def test_current_filename_overrides_context(self):
        route = route_text_question(
            '《审计报告》是什么时候上传的',
            context={'doc_ids': [42], 'ambiguous': False},
        )
        self.assertNotIn('doc_id', route['params'])
        self.assertIn('审计报告', route['params']['keywords'])

    def test_ambiguous_context_never_injects_doc_id(self):
        route = route_text_question(
            '这个文件什么时候上传的',
            context={'doc_ids': [1, 2], 'ambiguous': True},
        )
        self.assertNotIn('doc_id', route['params'])
        self.assertNotIn('keywords', route['params'])

    def test_single_id_marked_ambiguous_is_not_used(self):
        # 列表被截断：只看到 1 个但仍有更多候选
        route = route_text_question(
            '这个文件什么时候上传的',
            context={'doc_ids': [7], 'ambiguous': True},
        )
        self.assertNotIn('doc_id', route['params'])

    def test_pronoun_noise_is_removed(self):
        route = route_text_question('刚才那个文件什么时候上传的')
        self.assertEqual(route['query_type'], 'doc_upload_time')
        self.assertNotIn('keywords', route['params'])
        self.assertNotIn('doc_id', route['params'])

    def test_attachment_number_token_is_removed(self):
        route = route_text_question('附件6.关于国家奖学金的公示是什么时候上传的')
        keywords = route['params'].get('keywords') or []
        self.assertTrue(keywords)
        for keyword in keywords:
            self.assertNotEqual(
                keyword.strip('.-_、,，.。:：;；()（）[]【】'), '6',
            )

    def test_current_keywords_not_replaced_by_context(self):
        route = route_text_question(
            '审计报告是什么时候上传的',
            context={'doc_ids': [42], 'ambiguous': False},
        )
        self.assertNotIn('doc_id', route['params'])
        self.assertIn('审计报告', route['params']['keywords'])

    def test_non_time_query_ignores_context(self):
        route = route_text_question(
            '昨天上传了哪些文件', context={'doc_ids': [42]},
        )
        self.assertEqual(route['query_type'], 'list_docs_by_range')
        self.assertNotIn('doc_id', route['params'])

    def test_index_time_question_also_inherits(self):
        route = route_text_question(
            '那它的索引完成时间呢',
            context={'doc_ids': [9], 'ambiguous': False},
        )
        self.assertEqual(route['query_type'], 'doc_index_time')
        self.assertEqual(route['params']['doc_id'], 9)

    def test_shared_route_question_stays_legacy(self):
        route = route_question('刚才那个文件什么时候上传的')
        self.assertEqual(route['params'].get('keywords'), ['刚才'])
        self.assertNotIn('doc_id', route['params'])


class DocTimeStatusTests(TestCase):
    """文字入口状态标注与提示词包装（语音不调用这些函数）。"""

    def _route(self, doc_id=None):
        params = {'doc_id': doc_id} if doc_id else {}
        return {
            'type': 'data_query',
            'query_type': 'doc_upload_time',
            'params': params,
        }

    def _data(self, **overrides):
        base = {
            'need_filename': False,
            'not_found': True,
            'ambiguous': False,
            'total': 0,
            'shown': 0,
            'truncated': False,
            'matches': [],
            'scope_label': '当前账号可访问范围',
        }
        base.update(overrides)
        return base

    def test_ok_status_is_not_written_into_data(self):
        data = self._data(not_found=False, total=1, shown=1, matches=[{'id': 1}])
        annotated = annotate_doc_time_status({'success': True, 'data': data}, self._route())
        self.assertEqual(annotated['status'], 'ok')
        self.assertNotIn('status', annotated['data'])

    def test_need_filename_status(self):
        data = self._data(need_filename=True, not_found=False)
        annotated = annotate_doc_time_status({'success': True, 'data': data}, self._route())
        self.assertEqual(annotated['status'], 'need_filename')

    def test_context_ambiguous_wins_over_need_filename(self):
        data = self._data(need_filename=True, not_found=False, context_ambiguous=True)
        annotated = annotate_doc_time_status({'success': True, 'data': data}, self._route())
        self.assertEqual(annotated['status'], 'ambiguous')

    def test_ambiguous_status(self):
        data = self._data(not_found=False, total=2, shown=2, ambiguous=True)
        annotated = annotate_doc_time_status({'success': True, 'data': data}, self._route())
        self.assertEqual(annotated['status'], 'ambiguous')

    def test_doc_id_miss_is_not_visible(self):
        annotated = annotate_doc_time_status(
            {'success': True, 'data': self._data()}, self._route(doc_id=5),
        )
        self.assertEqual(annotated['status'], 'not_visible')
        self.assertNotIn('status', annotated['data'])

    def test_keyword_miss_is_not_found(self):
        annotated = annotate_doc_time_status(
            {'success': True, 'data': self._data()}, self._route(),
        )
        self.assertEqual(annotated['status'], 'not_found')

    def test_query_error_status(self):
        annotated = annotate_doc_time_status(
            {'success': False, 'data': None, 'error': 'boom'}, self._route(),
        )
        self.assertEqual(annotated['status'], 'query_error')

    def test_non_time_route_is_unchanged(self):
        result = {'success': True, 'data': {'total': 1}}
        route = {'query_type': 'list_docs_by_range', 'params': {}}
        self.assertEqual(annotate_doc_time_status(result, route), result)
        self.assertNotIn('status', annotate_doc_time_status(result, route))

    def test_ok_prompt_is_identical_to_legacy(self):
        data = self._data(not_found=False, total=1, shown=1, matches=[{'id': 1}])
        result = {'success': True, 'data': data}
        annotated = dict(result, status='ok')
        self.assertEqual(
            build_text_data_query_prompt('这个文件什么时候上传的', annotated),
            build_data_query_prompt('这个文件什么时候上传的', result),
        )

    def test_not_visible_prompt_matches_not_found(self):
        result = {'success': True, 'data': self._data()}
        not_found = annotate_doc_time_status(result, self._route())
        not_visible = annotate_doc_time_status(result, self._route(doc_id=5))
        question = '这个文件什么时候上传的'
        content_not_found = build_text_data_query_prompt(question, not_found)[0]['content']
        content_not_visible = build_text_data_query_prompt(question, not_visible)[0]['content']
        self.assertEqual(content_not_found, content_not_visible)
        self.assertNotIn('not_visible', content_not_visible)

    def test_need_filename_prompt_asks_for_clarification(self):
        result = {'success': True, 'data': self._data(need_filename=True, not_found=False)}
        annotated = annotate_doc_time_status(result, self._route())
        content = build_text_data_query_prompt('这个文件什么时候上传的', annotated)[0]['content']
        self.assertIn('当前状态为 need_filename', content)
        self.assertIn('请用户补充完整文件名', content)
        self.assertIn('不得声称', content)

    def test_query_error_prompt_never_claims_not_found(self):
        result = {'success': False, 'data': None, 'error': 'boom'}
        annotated = annotate_doc_time_status(result, self._route())
        content = build_text_data_query_prompt('这个文件什么时候上传的', annotated)[0]['content']
        self.assertIn('暂时无法查询', content)
        self.assertIn('当前状态为 query_error', content)
        self.assertNotIn('没有匹配', content)
