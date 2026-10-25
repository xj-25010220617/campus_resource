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
