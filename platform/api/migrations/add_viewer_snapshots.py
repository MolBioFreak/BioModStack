"""Add immutable job-owned viewer snapshot records."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from paths import get_db_path


_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS viewer_snapshots (
        id VARCHAR(36) PRIMARY KEY NOT NULL,
        job_id VARCHAR(36) NOT NULL REFERENCES jobs(id),
        label VARCHAR(120) NOT NULL,
        created_by VARCHAR(128) NOT NULL,
        schema_version INTEGER NOT NULL DEFAULT 2,
        snapshot_sha256 VARCHAR(64) NOT NULL,
        snapshot_json JSON NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_viewer_snapshots_job_id ON viewer_snapshots (job_id)",
    "CREATE INDEX IF NOT EXISTS ix_viewer_snapshots_snapshot_sha256 ON viewer_snapshots (snapshot_sha256)",
)


def migrate(db_path: str | None = None) -> None:
    """Create the additive immutable snapshot table without touching existing job rows."""
    database = Path(db_path) if db_path is not None else Path(get_db_path())
    connection = sqlite3.connect(database, timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        for statement in _STATEMENTS:
            connection.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
