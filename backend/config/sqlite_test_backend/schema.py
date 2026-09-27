from django.db.backends.sqlite3.schema import (
    DatabaseSchemaEditor as SQLiteDatabaseSchemaEditor,
)

class DatabaseSchemaEditor(SQLiteDatabaseSchemaEditor):
    """Skip MySQL-only table comments when running tests on SQLite."""

    #: 只有 MySQL 有意义的字符集 DDL。SQLite 没有"库/表字符集"概念，
    #: `knowledge.0008_utf8mb4_charset` 的
    #:     ALTER DATABASE `campus_db` CHARACTER SET utf8mb4 ...
    #:     ALTER TABLE `knowledge_doc` CONVERT TO CHARACTER SET utf8mb4 ...
    #: 在 SQLite 上会直接语法报错（near "DATABASE": syntax error），
    #: 使整个测试库在建库阶段就建不起来。这里按语句特征跳过——**只在测试
    #: 后端生效**，生产（MySQL）执行的是原样的迁移，字符集语义不受影响。
    _MYSQL_ONLY_DDL_MARKERS = (
        'ALTER DATABASE',
        'CONVERT TO CHARACTER SET',
    )

    def execute(self, sql, params=()):
        normalized = ' '.join(str(sql).split()).upper()
        if any(m in normalized for m in self._MYSQL_ONLY_DDL_MARKERS):
            return
        return super().execute(sql, params)

    def alter_db_table_comment(
        self, model, old_db_table_comment, new_db_table_comment
    ):
        return
