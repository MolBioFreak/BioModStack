"""Remove redundant single-column indexes covered by FrustraMPNN page order."""
from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Union

from paths import get_db_path


_INDEXES = (
    "ix_frustrampnn_landscape_rows_invocation_id",
    "ix_frustrampnn_landscape_rows_parent_job_id",
    "ix_frustrampnn_landscape_rows_target_id",
    "ix_frustrampnn_landscape_rows_entity_instance_id",
)


def _apply(connection: sqlite3.Connection) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        for index_name in _INDEXES:
            connection.execute(f'DROP INDEX IF EXISTS "{index_name}"')
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
