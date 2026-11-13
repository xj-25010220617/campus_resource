"""给 doc_chunk.content 加 ngram 全文索引（MySQL 专用）。

背景：关键词/正文分块通道此前用 `content LIKE '%kw%'` 扫全表，
数据量涨上来后必然变慢。MySQL 8 的 ngram 解析器支持中文分词，
建 FULLTEXT 后可用 MATCH ... AGAINST 走倒排索引。

只对 MySQL 生效；SQLite 等其它后端（本地/测试）直接跳过，
utils/rag.py 侧捕获异常后仍回退 icontains，功能不受影响。
"""
from django.db import migrations

INDEX_NAME = 'ft_chunk_content'
TABLE = 'doc_chunk'


def _is_mysql(schema_editor) -> bool:
    return schema_editor.connection.vendor == 'mysql'


def _index_exists(schema_editor, table: str, index: str) -> bool:
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = %s AND index_name = %s",
            [table, index],
        )
        return cursor.fetchone()[0] > 0


def create_fulltext_index(apps, schema_editor):
    if not _is_mysql(schema_editor):
        return
    if _index_exists(schema_editor, TABLE, INDEX_NAME):
        return
    schema_editor.execute(
        f"ALTER TABLE `{TABLE}` ADD FULLTEXT INDEX `{INDEX_NAME}` (`content`) "
        "WITH PARSER ngram"
    )


def drop_fulltext_index(apps, schema_editor):
    if not _is_mysql(schema_editor):
        return
    if not _index_exists(schema_editor, TABLE, INDEX_NAME):
        return
    schema_editor.execute(f"ALTER TABLE `{TABLE}` DROP INDEX `{INDEX_NAME}`")


class Migration(migrations.Migration):

    # MySQL 的 DDL 不支持事务回滚，Django 默认的原子迁移会直接报
    # TransactionManagementError，必须显式关闭
    atomic = False

    dependencies = [
        ('knowledge', '0016_crawlrun'),
    ]

    operations = [
        migrations.RunPython(create_fulltext_index, drop_fulltext_index, atomic=False),
    ]
