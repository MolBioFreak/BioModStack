"""Add durable leased NGS alignment-presentation requests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from paths import get_db_path


_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ngs_alignment_presentation_jobs (
    id VARCHAR(96) PRIMARY KEY NOT NULL,
    job_id VARCHAR(36) NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT,
    session_id VARCHAR(64) NOT NULL,
    mode VARCHAR(32) NOT NULL CHECK (mode IN ('primary','dimer_candidates')),
    source_authority_sha256 VARCHAR(64) NOT NULL
        CHECK (length(source_authority_sha256)=64 AND source_authority_sha256 NOT GLOB '*[^0-9a-f]*'),
    source_manifest_sha256 VARCHAR(64) NOT NULL
        CHECK (length(source_manifest_sha256)=64 AND source_manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
    source_artifact_set_sha256 VARCHAR(64) NOT NULL
        CHECK (length(source_artifact_set_sha256)=64 AND source_artifact_set_sha256 NOT GLOB '*[^0-9a-f]*'),
    policy_version INTEGER NOT NULL CHECK (policy_version >= 1),
    state VARCHAR(16) NOT NULL DEFAULT 'requested'
        CHECK (state IN ('requested','running','ready','failed')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    manual_retry_count INTEGER NOT NULL DEFAULT 0 CHECK (manual_retry_count >= 0),
    claim_token VARCHAR(96) UNIQUE,
    lease_expires_at DATETIME,

    authority_sha256 VARCHAR(64),
    manifest_sha256 VARCHAR(64),
    error_code VARCHAR(32)
        CHECK (error_code IS NULL OR error_code IN (
            'source_invalid','resource_limit','cancelled',
            'infrastructure_failed','publication_failed','integrity_mismatch'
        )),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_ngs_alignment_presentation_claim
        CHECK (
            (state='running' AND claim_token IS NOT NULL AND lease_expires_at IS NOT NULL)
            OR (state<>'running' AND claim_token IS NULL AND lease_expires_at IS NULL)
        ),

    CONSTRAINT ck_ngs_alignment_presentation_ready
        CHECK (
            (state='ready' AND authority_sha256 IS NOT NULL AND manifest_sha256 IS NOT NULL
                AND length(authority_sha256)=64 AND authority_sha256 NOT GLOB '*[^0-9a-f]*'
                AND length(manifest_sha256)=64 AND manifest_sha256 NOT GLOB '*[^0-9a-f]*')
            OR (state<>'ready' AND authority_sha256 IS NULL AND manifest_sha256 IS NULL)
        )
)
"""


def migrate(db_path: str | Path | None = None) -> None:
    path = Path(db_path) if db_path is not None else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute("PRAGMA foreign_keys").fetchone() != (1,):
            raise sqlite3.IntegrityError("SQLite foreign keys could not be enabled")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(_TABLE_SQL)
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_ngs_alignment_presentation_source
            ON ngs_alignment_presentation_jobs(
                job_id, session_id, source_authority_sha256, policy_version
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_ngs_alignment_presentation_claimable
            ON ngs_alignment_presentation_jobs(state, created_at, id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_ngs_alignment_presentation_lease
            ON ngs_alignment_presentation_jobs(state, lease_expires_at)
            """
        )
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(
                f"NGS alignment-presentation migration foreign-key violations: {violations!r}"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


__all__ = ["migrate"]
