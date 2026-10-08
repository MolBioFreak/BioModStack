"""Create the terminal ONT move-source authority immutability trigger."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from migrations.ont_sqlite_schema_contract import (
    assert_ont_move_source_table_contract,
    ensure_ont_move_source_terminal_immutability,
)


def migrate(db_path: str | Path) -> None:
    connection = sqlite3.connect(str(db_path), timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        ensure_ont_move_source_terminal_immutability(connection)
        assert_ont_move_source_table_contract(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


__all__ = ["migrate"]
