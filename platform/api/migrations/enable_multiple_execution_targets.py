"""Migration 46: enable independently active workers without rewriting state."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from paths import get_db_path


def migrate(db_path: str | Path | None = None) -> None:
    path = Path(db_path) if db_path is not None else get_db_path()
    with sqlite3.connect(path, timeout=30) as connection:
        connection.execute("PRAGMA busy_timeout=30000")
        # DDL only: preserve active flags, target identities, leases and Jobs.
        # Explicit transaction makes interruption safe and reruns idempotent.
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DROP INDEX IF EXISTS uq_execution_targets_one_active")


if __name__ == "__main__":
    migrate()
