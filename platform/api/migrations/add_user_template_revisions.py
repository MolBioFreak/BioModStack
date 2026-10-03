"""Add immutable UserTemplate history without rewriting any existing draft."""
import sqlite3

from paths import get_db_path


def migrate(db_path=None):
    with sqlite3.connect(str(db_path or get_db_path()), timeout=30) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("""CREATE TABLE IF NOT EXISTS user_template_revisions (
            template_id VARCHAR(36) NOT NULL,
            revision INTEGER NOT NULL,
            snapshot JSON NOT NULL,
            created_at DATETIME,
            PRIMARY KEY (template_id, revision)
        )""")
        # Historical snapshots survive head deletion and cannot be rewritten.
        for operation in ("UPDATE", "DELETE"):
            connection.execute(f"""CREATE TRIGGER IF NOT EXISTS user_template_revisions_no_{operation.lower()}
                BEFORE {operation} ON user_template_revisions BEGIN
                SELECT RAISE(ABORT, 'UserTemplate revisions are immutable'); END""")
