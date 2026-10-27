from django.db import migrations


def _convert_charset(apps, schema_editor):
    """把库与相关表转成 utf8mb4。

    MySQL 专用 SQL：`ALTER DATABASE` / `CONVERT TO CHARACTER SET` 在 sqlite 上无法解析，
    故按 connection.vendor 跳过，保证 `settings_test`（内存 sqlite）能跑完整迁移链。
    生产为 MySQL，行为与改造前完全一致。
    """
    if schema_editor.connection.vendor != 'mysql':
        return

    tables = ('knowledge_doc', 'keyword', 'doc_chunk', 'chunked_upload_session')
    statements = [
        "ALTER DATABASE `campus_db` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;",
        *[
            f"ALTER TABLE `{table}` CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
            for table in tables
        ],
    ]
    with schema_editor.connection.cursor() as cursor:
        for sql in statements:
            cursor.execute(sql)


class Migration(migrations.Migration):

    dependencies = [
        ('knowledge', '0007_knowledgedoc_index_diagnostics'),
    ]

    operations = [
        # 改造前为 5 条 RunSQL（无 vendor 判断）；改成一个带判断的 RunPython，
        # 使 sqlite 测试后端可执行。该迁移在生产早已 apply，不会再执行。
        migrations.RunPython(_convert_charset, migrations.RunPython.noop),
    ]
