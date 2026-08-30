"""Migration 45: provider-neutral remote execution targets and Job placement."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from paths import get_db_path


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info('{table}')")}


def migrate(db_path: str | Path | None = None) -> None:
    path = Path(db_path) if db_path is not None else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=30) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_targets (
                id VARCHAR(160) PRIMARY KEY,
                provider VARCHAR(32) NOT NULL,
                provider_instance_id VARCHAR(128) NOT NULL,
                name VARCHAR(255),
                state VARCHAR(32) NOT NULL DEFAULT 'discovered',
                active BOOLEAN NOT NULL DEFAULT 0,
                host VARCHAR(255),
                port INTEGER,
                username VARCHAR(64),
                remote_root VARCHAR(500) NOT NULL DEFAULT '/opt/biomodstack',
                host_key_sha256 VARCHAR(64),
                capabilities JSON NOT NULL DEFAULT '{}',
                pricing JSON NOT NULL DEFAULT '{}',
                provider_metadata JSON NOT NULL DEFAULT '{}',
                last_error TEXT,
                last_seen_at DATETIME,
                activated_at DATETIME,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_execution_target_provider_instance UNIQUE(provider, provider_instance_id),
                CONSTRAINT ck_execution_target_provider CHECK(provider IN ('vast')),
                CONSTRAINT ck_execution_target_active CHECK(active IN (0, 1))
            )
            """
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_execution_targets_one_active ON execution_targets(active) WHERE active = 1"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_execution_targets_provider_state ON execution_targets(provider, state)"
        )
        job_columns = _columns(connection, "jobs")
        additions = {
            "execution_target_id": "VARCHAR(160)",
            "execution_source_revision": "VARCHAR(64)",
            "execution_source_tree": "VARCHAR(64)",
            "execution_bundle_sha256": "VARCHAR(64)",
            "remote_attempt_id": "VARCHAR(64)",
            "remote_state": "VARCHAR(32)",
        }
        for name, sql_type in additions.items():
            if name not in job_columns:
                connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {sql_type}")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_jobs_execution_target_id ON jobs(execution_target_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_jobs_remote_attempt_id ON jobs(remote_attempt_id)"
        )
        connection.commit()


if __name__ == "__main__":
    migrate()
