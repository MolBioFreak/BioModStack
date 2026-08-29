"""Add independently retryable FrustraMPNN statistics-analysis children."""

from __future__ import annotations

import sqlite3
from pathlib import Path


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS frustrampnn_statistics_analyses (
    analysis_id VARCHAR(36) PRIMARY KEY NOT NULL,
    parent_job_id VARCHAR(36) NOT NULL,
    invocation_id VARCHAR(128) NOT NULL,
    core_artifact_id VARCHAR(384) NOT NULL,
    core_bundle_relative_path TEXT NOT NULL,
    core_landscape_sha256 VARCHAR(64) NOT NULL,
    core_manifest_sha256 VARCHAR(64) NOT NULL,
    state VARCHAR(16) NOT NULL DEFAULT 'queued'
        CHECK (state IN ('queued','running','completed','failed')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    formula_version VARCHAR(64) NOT NULL,
    policy_version VARCHAR(64) NOT NULL,
    package_version VARCHAR(64) NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    artifact_relative_path TEXT,
    artifact_sha256 VARCHAR(64),
    statistics_sha256 VARCHAR(64),
    diagnostic TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT fk_frustrampnn_statistics_analysis_result
        FOREIGN KEY (parent_job_id, invocation_id)
        REFERENCES frustrampnn_results(parent_job_id, invocation_id)
        ON DELETE CASCADE,
    CONSTRAINT uq_frustrampnn_statistics_analysis_core
        UNIQUE (parent_job_id, invocation_id, core_landscape_sha256)
);
CREATE INDEX IF NOT EXISTS ix_frustrampnn_statistics_analysis_parent_job_id
    ON frustrampnn_statistics_analyses(parent_job_id);
CREATE INDEX IF NOT EXISTS ix_frustrampnn_statistics_analysis_invocation_id
    ON frustrampnn_statistics_analyses(invocation_id);
CREATE INDEX IF NOT EXISTS ix_frustrampnn_statistics_analysis_state
    ON frustrampnn_statistics_analyses(state);
CREATE INDEX IF NOT EXISTS ix_frustrampnn_statistics_analysis_state_created
    ON frustrampnn_statistics_analyses(state, created_at);
"""


def migrate(db_path: str | Path) -> None:
    connection = sqlite3.connect(str(db_path), timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        connection.executescript(_CREATE_SQL)
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(
                "FrustraMPNN statistics-analysis migration foreign-key violations: "
                f"{violations!r}"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


__all__ = ["migrate"]
