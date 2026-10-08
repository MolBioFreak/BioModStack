"""Migration 42: immutable ONT Squigulator comparison ledgers."""
from __future__ import annotations

import sqlite3

from migrations.ont_signal_comparison_schema_contract import COMPARISON_SCHEMA_SQL


def migrate(db_path: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(COMPARISON_SCHEMA_SQL)
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError("ONT signal comparison migration violates foreign-key authority")
        connection.commit()
    finally:
        connection.close()
