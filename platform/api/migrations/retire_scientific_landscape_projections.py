"""Retire relational scientific landscape projections after artifact cutover."""
from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Union

from paths import get_db_path

_TABLES = (
    "conformational_mapping_landscape_rows",
    "frustrampnn_landscape_rows",
)
_TRIGGER_BY_TABLE = {
    "conformational_mapping_landscape_rows": "trg_cm_landscape_projection_retired_insert",
    "frustrampnn_landscape_rows": "trg_frustrampnn_landscape_projection_retired_insert",
}


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone() is not None


def _apply(connection: sqlite3.Connection) -> None:
    populated = {
        table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        for table in _TABLES
        if _table_exists(connection, table)
    }
    populated = {table: count for table, count in populated.items() if count}
    if populated:
        details = ", ".join(f"{table}={count}" for table, count in sorted(populated.items()))
        raise RuntimeError(f"legacy scientific landscape projections still contain rows: {details}")

    connection.execute("BEGIN IMMEDIATE")
    try:
        for table in _TABLES:
            if not _table_exists(connection, table):
                continue
            for index_row in connection.execute(f'PRAGMA index_list("{table}")').fetchall():
                index_name = str(index_row[1])
                if not index_name.startswith("sqlite_autoindex_"):
                    connection.execute(f'DROP INDEX IF EXISTS "{index_name}"')
            trigger = _TRIGGER_BY_TABLE[table]
            connection.execute(f'DROP TRIGGER IF EXISTS "{trigger}"')
            connection.execute(
                f'''CREATE TRIGGER "{trigger}"
                    BEFORE INSERT ON "{table}"
                    BEGIN
                        SELECT RAISE(ABORT, 'scientific landscape SQL projection is retired');
                    END'''
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def migrate(db_path: Union[str, Path, sqlite3.Connection, None] = None) -> None:
    if isinstance(db_path, sqlite3.Connection):
        _apply(db_path)
        return
    path = str(db_path or get_db_path())
    connection = sqlite3.connect(path, timeout=30)
    try:
        _apply(connection)
    finally:
        connection.close()
