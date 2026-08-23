"""Seal terminal ONT raw-waveform lookup rows at SQLite."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path


MIGRATION_40_TRIGGER_SQL = {
    "trg_ont_raw_signal_lookup_terminal_no_update": """
        CREATE TRIGGER IF NOT EXISTS trg_ont_raw_signal_lookup_terminal_no_update
            BEFORE UPDATE ON ont_raw_signal_lookups
            WHEN OLD.state IN ('ready', 'failed')
            BEGIN SELECT RAISE(ABORT, 'terminal ONT raw-signal lookup is immutable'); END
    """,
    "trg_ont_raw_signal_lookup_terminal_no_delete": """
        CREATE TRIGGER IF NOT EXISTS trg_ont_raw_signal_lookup_terminal_no_delete
            BEFORE DELETE ON ont_raw_signal_lookups
            WHEN OLD.state IN ('ready', 'failed')
            BEGIN SELECT RAISE(ABORT, 'terminal ONT raw-signal lookup is immutable'); END
    """,
}


def _normalize_sql(sql: str) -> str:
    return " ".join(sql.split()).replace(
        "CREATE TRIGGER IF NOT EXISTS ", "CREATE TRIGGER ", 1
    )


MIGRATION_40_TRIGGER_SQL_DIGESTS = {
    name: hashlib.sha256(_normalize_sql(sql).encode("utf-8")).hexdigest()
    for name, sql in MIGRATION_40_TRIGGER_SQL.items()
}


def migrate(db_path: str | Path) -> None:
    connection = sqlite3.connect(str(db_path), timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ont_raw_signal_lookups'"
        ).fetchone() is None:
            connection.commit()
            return
        for trigger_sql in MIGRATION_40_TRIGGER_SQL.values():
            connection.execute(trigger_sql)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def assert_attested(connection: sqlite3.Connection) -> None:
    observed = {
        str(row[0]): hashlib.sha256(
            _normalize_sql(str(row[1] or "")).encode("utf-8")
        ).hexdigest()
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger'"
        )
        if str(row[0]) in MIGRATION_40_TRIGGER_SQL_DIGESTS
    }
    if observed != MIGRATION_40_TRIGGER_SQL_DIGESTS:
        raise RuntimeError("migration 40 trigger authority diverged")


__all__ = [
    "MIGRATION_40_TRIGGER_SQL",
    "MIGRATION_40_TRIGGER_SQL_DIGESTS",
    "assert_attested",
    "migrate",
]
