"""表格问答引擎（tabular_query）单元测试

覆盖：SQL 白名单校验、schema 提取、意图识别、回退路径、sqlite 执行链路。
不依赖 OSS/LLM：下载与 SQL 生成均用 mock 替换。
"""
from unittest import mock

from django.test import TestCase

from apps.knowledge.extractor import extract_schema_from_grid
from apps.knowledge.models import KnowledgeDoc
from utils import tabular_query
from utils.query_router import route_question
from utils.tabular_query import TabularError, validate_sql


# ------------------------------------------------------------------
# validate_sql 白名单
# ------------------------------------------------------------------
class ValidateSqlTests(TestCase):
    def test_plain_select_passes_and_gets_limit(self):
        sql = validate_sql('SELECT COUNT(*) FROM data')
        self.assertIn('LIMIT', sql.upper())

    def test_select_with_limit_unchanged(self):
        sql = validate_sql('SELECT * FROM data LIMIT 5')
        self.assertEqual(sql, 'SELECT * FROM data LIMIT 5')

    def test_multi_statement_rejected(self):
        with self.assertRaises(TabularError):
            validate_sql('SELECT 1; SELECT 2')

    def test_write_ops_rejected(self):
        for bad in (
            'DROP TABLE data',
            'INSERT INTO data VALUES (1)',
            'DELETE FROM data',
            'UPDATE data SET x=1',
            'PRAGMA table_info(data)',
            'ATTACH DATABASE x AS y',
            'SELECT * FROM data; DROP TABLE data',
        ):
            with self.assertRaises(TabularError, msg=bad):
                validate_sql(bad)

    def test_other_table_rejected(self):
        with self.assertRaises(TabularError):
            validate_sql('SELECT * FROM knowledge_doc')

    def test_chinese_column_select(self):
        sql = validate_sql('SELECT "班级", COUNT(*) FROM data GROUP BY "班级"')
        self.assertIn('LIMIT', sql.upper())


# ------------------------------------------------------------------
# extract_schema_from_grid
# ------------------------------------------------------------------
class ExtractSchemaTests(TestCase):
    def test_basic_grid(self):
        grid = [
            ['姓名', '班级', '数量'],
            ['张三', '软件工程25206', '3'],
            ['李四', '软件工程25201', '5'],
        ]
        schema = extract_schema_from_grid(grid, 'Sheet1')
        self.assertEqual(schema['row_count'], 2)
        self.assertEqual(schema['columns'][0], {'name': '姓名', 'dtype': 'VARCHAR'})
        self.assertEqual(schema['columns'][2]['dtype'], 'DOUBLE')
        self.assertEqual(schema['sample_rows'][0][0], '张三')

    def test_merged_title_row_skipped(self):
        grid = [
            ['教材订购意愿表', '', ''],
            ['姓名', '班级', '数量'],
            ['张三', '软件工程25206', '3'],
        ]
        schema = extract_schema_from_grid(grid, 'S')
        self.assertEqual(schema['columns'][0]['name'], '姓名')
        self.assertEqual(schema['row_count'], 1)

    def test_empty_and_single_column(self):
        self.assertIsNone(extract_schema_from_grid([], 'S'))
        self.assertIsNone(extract_schema_from_grid([['只有一列'], ['值']], 'S'))


# ------------------------------------------------------------------
# 意图识别
# ------------------------------------------------------------------
class IntentRouteTests(TestCase):
    def test_class_count_question_routes_to_tabular(self):
        route = route_question('软件工程25206有多少人')
        self.assertEqual(route['type'], 'data_query')
        self.assertEqual(route['query_type'], 'tabular_query')
        self.assertEqual(route['params']['question'], '软件工程25206有多少人')

    def test_file_count_question_not_hijacked(self):
        route = route_question('我上传了多少文件')
        self.assertEqual(route['query_type'], 'count_docs')

    def test_ranking_question(self):
        route = route_question('各班人数排名')
        self.assertEqual(route['query_type'], 'tabular_query')

    def test_student_id_queries_route_to_tabular(self):
        # 学号类精确记录查询必须走表格问答，而非 find_docs（标题检索必空）
        for q in ('25021220122', '学号25021220122的学生是谁',
                  '查一下学号25021220122', '25021220122订了哪些教材'):
            route = route_question(q)
            self.assertEqual(
                route['query_type'], 'tabular_query',
                msg=f'{q} 被路由到 {route.get("query_type")}',
            )


# ------------------------------------------------------------------
# 执行链路（sqlite 内存表）
# ------------------------------------------------------------------
class ExecutionTests(TestCase):
    def _make_doc(self):
        return KnowledgeDoc.objects.create(
            title='软件工程25201-25206教材订购意愿',
            source_type='upload',
            file_name='软件工程25201-25206教材订购意愿.xlsx',
            file_type='xls',
            status='indexed',
            visibility='public',
            table_schema={
                'sheets': [{
                    'name': 'Sheet1',
                    'columns': [
                        {'name': '姓名', 'dtype': 'VARCHAR'},
                        {'name': '班级', 'dtype': 'VARCHAR'},
                        {'name': '数量', 'dtype': 'DOUBLE'},
                    ],
                    'row_count': 3,
                    'sample_rows': [['张三', '软件工程25206', '3']],
                }],
            },
        )

    def test_sqlite_count_query(self):
        import sqlite3
        conn = sqlite3.connect(':memory:')
        conn.execute('CREATE TABLE data("姓名" TEXT, "班级" TEXT, "数量" REAL)')
        conn.executemany('INSERT INTO data VALUES (?,?,?)', [
            ('张三', '软件工程25206', 3.0),
            ('李四', '软件工程25206', 5.0),
            ('王五', '软件工程25201', 2.0),
        ])
        sql = validate_sql('SELECT COUNT(*) FROM data WHERE "班级" = \'软件工程25206\'')
        cols, rows, truncated = tabular_query._execute(conn, __import__('threading').Lock(), sql)
        self.assertEqual(rows[0][0], 2)
        self.assertFalse(truncated)

    def test_fallback_when_doc_not_found(self):
        result = tabular_query.run_tabular_query(
            user=None, question='软件工程25206有多少人', doc_id=999999,
        )
        self.assertFalse(result['success'])
        self.assertEqual(result['fallback'], 'rag')

    def test_fallback_when_no_schema(self):
        doc = KnowledgeDoc.objects.create(
            title='普通文档', source_type='upload', file_name='a.pdf',
            file_type='pdf', status='indexed', visibility='public',
        )
        result = tabular_query.run_tabular_query(
            user=None, question='有多少人', doc_id=doc.id,
        )
        # pdf 不在表格类型白名单 → 定位失败回退
        self.assertFalse(result['success'])
        self.assertEqual(result['fallback'], 'rag')


# ------------------------------------------------------------------
# AST 校验（sqlglot）与自愈/自检
# ------------------------------------------------------------------
class SchemaLinkingTests(TestCase):
    _SCHEMA = {
        'sheets': [
            {
                'name': '教材订购',
                'columns': [{'name': '姓名', 'dtype': 'VARCHAR'}, {'name': '数量', 'dtype': 'DOUBLE'}],
                'row_count': 3,
                'sample_rows': [['张三', '3'], ['李四', '5']],
            },
            {
                'name': '期末成绩表',
                'columns': [
                    {'name': '学号', 'dtype': 'VARCHAR'},
                    {'name': '课程', 'dtype': 'VARCHAR'},
                    {'name': '成绩', 'dtype': 'DOUBLE'},
                ],
                'row_count': 2,
                'sample_rows': [['2025001', '高数', '88'], ['2025002', '英语', '92']],
            },
        ],
    }

    def test_pick_sheet_by_question(self):
        sheet = tabular_query._pick_sheet(self._SCHEMA, '期末成绩表里高数成绩是多少')
        self.assertEqual(sheet['name'], '期末成绩表')
        sheet = tabular_query._pick_sheet(self._SCHEMA, '教材订购数量')
        self.assertEqual(sheet['name'], '教材订购')

    def test_columns_limited_but_keep_matched(self):
        cols = [{'name': f'列{i}', 'dtype': 'VARCHAR'} for i in range(30)]
        cols.append({'name': '目标列', 'dtype': 'VARCHAR'})
        schema = {'sheets': [{'name': 'S', 'columns': cols, 'row_count': 1,
                              'sample_rows': [['x'] * len(cols)]}]}
        with self.settings(TABULAR_SCHEMA_MAX_COLUMNS=5):
            prompt = tabular_query._schema_prompt(schema, '目标列是多少')
        self.assertIn('目标列', prompt)
        self.assertIn('未展示', prompt)

    def test_enum_values_included(self):
        prompt = tabular_query._schema_prompt(self._SCHEMA, '教材订购数量')
        self.assertIn('候选值', prompt)
        self.assertIn('张三', prompt)


class FewShotTests(TestCase):
    def test_disabled_records_nothing(self):
        from apps.knowledge.models import TabularFewShot
        from utils import tabular_fewshot

        with mock.patch('utils.llm.llm_client.embed_one', return_value=[1.0, 0.0]):
            tabular_fewshot.record_example('有多少条记录', 'SELECT COUNT(*) FROM data')
        self.assertEqual(TabularFewShot.objects.count(), 0)
        self.assertEqual(tabular_fewshot.format_examples('有多少条记录'), '')

    def test_unverified_not_injected(self):
        from utils import tabular_fewshot

        with self.settings(TABULAR_FEWSHOT_ENABLED=True), \
             mock.patch('utils.llm.llm_client.embed_one', return_value=[1.0, 0.0]):
            tabular_fewshot.record_example('有多少条记录', 'SELECT COUNT(*) FROM data')
            self.assertEqual(tabular_fewshot.retrieve_examples('有多少条记录'), [])

    def test_verified_example_retrieved(self):
        from apps.knowledge.models import TabularFewShot
        from utils import tabular_fewshot

        with self.settings(TABULAR_FEWSHOT_ENABLED=True), \
             mock.patch('utils.llm.llm_client.embed_one', return_value=[1.0, 0.0]):
            tabular_fewshot.record_example('有多少条记录', 'SELECT COUNT(*) FROM data')
            TabularFewShot.objects.update(verified=True)
            examples = tabular_fewshot.retrieve_examples('一共有多少条记录')
            self.assertEqual(len(examples), 1)
            self.assertIn('COUNT(*)', examples[0]['sql'])

    def test_dissimilar_example_filtered(self):
        from apps.knowledge.models import TabularFewShot
        from utils import tabular_fewshot

        with self.settings(TABULAR_FEWSHOT_ENABLED=True), \
             mock.patch('utils.llm.llm_client.embed_one', side_effect=[[1.0, 0.0], [0.0, 1.0]]):
            tabular_fewshot.record_example('有多少条记录', 'SELECT COUNT(*) FROM data')
            TabularFewShot.objects.update(verified=True)
            self.assertEqual(tabular_fewshot.retrieve_examples('重修怎么申请'), [])


class AstValidateTests(TestCase):
    def test_cte_select_allowed(self):
        sql = validate_sql('WITH t AS (SELECT 姓名 FROM data) SELECT * FROM t')
        self.assertIn('LIMIT', sql.upper())

    def test_select_into_rejected(self):
        with self.assertRaises(TabularError):
            validate_sql('SELECT * INTO backup FROM data')

    def test_attach_rejected(self):
        with self.assertRaises(TabularError):
            validate_sql('ATTACH DATABASE \'x.db\' AS x')

    def test_comment_bypass_rejected(self):
        with self.assertRaises(TabularError):
            validate_sql('SELECT * FROM data; /* hi */ DROP TABLE data')

    def test_nested_other_table_rejected(self):
        with self.assertRaises(TabularError):
            validate_sql('SELECT * FROM (SELECT * FROM secret) AS s')

    def test_regex_backend_still_available(self):
        with self.settings(TABULAR_SQL_VALIDATOR='regex'):
            sql = validate_sql('SELECT * FROM data')
        self.assertIn('LIMIT', sql.upper())


class ResultSuspicionTests(TestCase):
    def test_group_question_single_row(self):
        reason = tabular_query._result_suspicion('各班级的学生名单', ['班级'], [['软件1班']])
        self.assertIn('GROUP BY', reason)

    def test_count_question_multi_rows(self):
        reason = tabular_query._result_suspicion('共有多少条记录', ['姓名'], [['张三'], ['李四']])
        self.assertIn('聚合', reason)

    def test_consistent_result_no_warning(self):
        self.assertIsNone(
            tabular_query._result_suspicion('共有多少条记录', ['COUNT(*)'], [[3]]),
        )
        self.assertIsNone(
            tabular_query._result_suspicion('各班级人数', ['班级', '人数'], [['1班', 2], ['2班', 3]]),
        )


class SelfHealTests(TestCase):
    def _doc(self):
        return KnowledgeDoc.objects.create(
            title='软件工程25201-25206教材订购意愿',
            source_type='upload',
            file_name='软件工程25201-25206教材订购意愿.xlsx',
            file_type='xls',
            status='indexed',
            visibility='public',
            table_schema={
                'sheets': [{
                    'name': 'Sheet1',
                    'columns': [
                        {'name': '姓名', 'dtype': 'VARCHAR'},
                        {'name': '班级', 'dtype': 'VARCHAR'},
                        {'name': '数量', 'dtype': 'DOUBLE'},
                    ],
                    'row_count': 3,
                    'sample_rows': [['张三', '软件工程25206', '3']],
                }],
            },
        )

    def _conn(self):
        import sqlite3
        conn = sqlite3.connect(':memory:')
        conn.execute('CREATE TABLE data("姓名" TEXT, "班级" TEXT, "数量" REAL)')
        conn.executemany('INSERT INTO data VALUES (?,?,?)', [
            ('张三', '软件工程25206', 3.0),
            ('李四', '软件工程25206', 5.0),
            ('王五', '软件工程25201', 2.0),
        ])
        return conn

    def test_repair_after_execution_error(self):
        import threading
        doc = self._doc()
        with mock.patch.object(
                tabular_query, '_visible_tabular_docs',
                return_value=KnowledgeDoc.objects.filter(pk=doc.id)), \
             mock.patch.object(tabular_query, '_get_conn', return_value=(self._conn(), threading.Lock())), \
             mock.patch.object(tabular_query, 'generate_sql', return_value='SELECT 不存在的列 FROM data'), \
             mock.patch.object(tabular_query, '_repair_sql', return_value='SELECT COUNT(*) FROM data'):
            result = tabular_query.run_tabular_query(user=None, question='有多少条记录', doc_id=doc.id)

        self.assertTrue(result['success'])
        self.assertIn('COUNT', result['data']['sql'].upper())
        self.assertEqual(result['data']['rows'][0][0], 3)

    def test_repair_when_result_suspicious(self):
        import threading
        doc = self._doc()
        with mock.patch.object(
                tabular_query, '_visible_tabular_docs',
                return_value=KnowledgeDoc.objects.filter(pk=doc.id)), \
             mock.patch.object(tabular_query, '_get_conn', return_value=(self._conn(), threading.Lock())), \
             mock.patch.object(tabular_query, 'generate_sql', return_value='SELECT "班级" FROM data LIMIT 1'), \
             mock.patch.object(
                 tabular_query, '_repair_sql',
                 return_value='SELECT "班级", COUNT(*) AS 人数 FROM data GROUP BY "班级"',
             ):
            result = tabular_query.run_tabular_query(user=None, question='各班级的学生名单', doc_id=doc.id)

        self.assertTrue(result['success'])
        self.assertIn('GROUP BY', result['data']['sql'].upper())
        self.assertEqual(result['data']['row_count'], 2)

    def test_keep_original_when_repair_no_better(self):
        import threading
        doc = self._doc()
        with mock.patch.object(
                tabular_query, '_visible_tabular_docs',
                return_value=KnowledgeDoc.objects.filter(pk=doc.id)), \
             mock.patch.object(tabular_query, '_get_conn', return_value=(self._conn(), threading.Lock())), \
             mock.patch.object(tabular_query, 'generate_sql', return_value='SELECT "班级" FROM data LIMIT 1'), \
             mock.patch.object(tabular_query, '_repair_sql', return_value='SELECT "班级" FROM data LIMIT 1'):
            result = tabular_query.run_tabular_query(user=None, question='各班级的学生名单', doc_id=doc.id)

        self.assertTrue(result['success'])
        self.assertEqual(result['data']['row_count'], 1)

    def test_heal_disabled_falls_back(self):
        import threading
        doc = self._doc()
        with self.settings(TABULAR_SQL_SELF_HEAL=False):
            with mock.patch.object(
                    tabular_query, '_visible_tabular_docs',
                    return_value=KnowledgeDoc.objects.filter(pk=doc.id)), \
                 mock.patch.object(tabular_query, '_get_conn', return_value=(self._conn(), threading.Lock())), \
                 mock.patch.object(tabular_query, 'generate_sql', return_value='SELECT 不存在的列 FROM data'):
                result = tabular_query.run_tabular_query(user=None, question='有多少条记录', doc_id=doc.id)

        self.assertFalse(result['success'])
        self.assertEqual(result['fallback'], 'rag')
