"""Add canonical antibody pipeline artifact-contract columns."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Optional

from paths import get_db_path


DB_PATH = Path(get_db_path())

JOB_COLUMNS = [
    ("selected_input_artifact_class", "VARCHAR(64)", None),
    ("selected_input_schema_version", "INTEGER", None),
]

DESIGN_COLUMNS = [
    ("artifact_class", "VARCHAR(64)", None),
    ("artifact_schema_version", "INTEGER", None),
]


def _get_existing_columns(cursor: sqlite3.Cursor, table_name: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cursor.fetchall()}


def _add_columns(
    cursor: sqlite3.Cursor,
    table_name: str,
    columns: list[tuple[str, str, Optional[str]]],
) -> None:
    existing = _get_existing_columns(cursor, table_name)
    for col_name, col_type, default in columns:
        if col_name in existing:
            continue
        if default is not None:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type} DEFAULT {default}")
        else:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}")


def migrate(db_path: Optional[str] = None) -> None:
    db_path = Path(db_path or DB_PATH)
    if not db_path.exists():
        print(f"Database not found at {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    cursor = conn.cursor()

    try:
        _add_columns(cursor, "jobs", JOB_COLUMNS)
        _add_columns(cursor, "designs", DESIGN_COLUMNS)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS ix_jobs_selected_input_artifact_class ON jobs(selected_input_artifact_class)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS ix_designs_artifact_class ON designs(artifact_class)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else str(DB_PATH)
    migrate(db)
