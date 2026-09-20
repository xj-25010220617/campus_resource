from django.db import migrations


def _convert_table(table):
    return (
        f"ALTER TABLE `{table}` CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;",
        f"ALTER TABLE `{table}` CONVERT TO CHARACTER SET utf8 COLLATE utf8_unicode_ci;",
    )


class Migration(migrations.Migration):
    dependencies = [
        ('knowledge', '0007_knowledgedoc_index_diagnostics'),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER DATABASE `campus_db` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;",
            reverse_sql="ALTER DATABASE `campus_db` CHARACTER SET utf8 COLLATE utf8_unicode_ci;",
        ),
        *[
            migrations.RunSQL(sql=sql, reverse_sql=reverse_sql)
            for sql, reverse_sql in (
                _convert_table('knowledge_doc'),
                _convert_table('keyword'),
                _convert_table('doc_chunk'),
                _convert_table('chunked_upload_session'),
            )
        ],
    ]
