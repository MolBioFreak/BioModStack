"""Add provenance fields to nucleotide_sequences table."""
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

    columns = [
        ("parent_id", "TEXT"),
        ("operation", "TEXT"),
        ("operation_params", "TEXT"),  # JSON stored as TEXT
        ("version", "INTEGER DEFAULT 1"),
    ]

    for col_name, col_type in columns:
        try:
            cursor.execute(f"ALTER TABLE nucleotide_sequences ADD COLUMN {col_name} {col_type}")
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else str(get_db_path())
    migrate(db)
