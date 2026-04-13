"""Add persistent saved-selection storage for jobs."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Optional

from paths import get_db_path


DB_PATH = Path(get_db_path())


def _get_existing_columns(cursor: sqlite3.Cursor, table_name: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cursor.fetchall()}


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
        existing = _get_existing_columns(cursor, "jobs")
        if "saved_selection_sets" not in existing:
            cursor.execute("ALTER TABLE jobs ADD COLUMN saved_selection_sets JSON")
            cursor.execute("UPDATE jobs SET saved_selection_sets = '[]' WHERE saved_selection_sets IS NULL")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else str(DB_PATH)
    migrate(db)
