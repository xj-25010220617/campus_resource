from django.db.backends.sqlite3.schema import (
    DatabaseSchemaEditor as SQLiteDatabaseSchemaEditor,
)

class DatabaseSchemaEditor(SQLiteDatabaseSchemaEditor):
    """Skip MySQL-only table comments when running tests on SQLite."""

    def alter_db_table_comment(
        self, model, old_db_table_comment, new_db_table_comment
    ):
        return
