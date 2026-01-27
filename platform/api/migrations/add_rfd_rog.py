"""Add rfd_rog column to designs table."""
import sqlite3
import sys
from typing import Optional

from paths import get_db_path


def migrate(db_path: Optional[str] = None) -> None:
    db_path = db_path or str(get_db_path())
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    cursor = conn.cursor()

    try:
        cursor.execute("ALTER TABLE designs ADD COLUMN rfd_rog REAL")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else str(get_db_path())
    migrate(db)
