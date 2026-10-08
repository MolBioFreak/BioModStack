"""Add durable Molecular Dynamics lifecycle and artifact lineage tables."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from paths import get_db_path


_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS md_runs (
        job_id VARCHAR(36) PRIMARY KEY NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        normalized_request JSON NOT NULL,
        request_sha256 VARCHAR(64) NOT NULL,
        phase VARCHAR(32) NOT NULL DEFAULT 'validating',
        state_version INTEGER NOT NULL DEFAULT 0,
        chemistry_profile_id VARCHAR(128) NOT NULL,
        chemistry_profile_sha256 VARCHAR(64) NOT NULL,
        chemistry_assurance VARCHAR(32) NOT NULL,
        verification_status VARCHAR(32) NOT NULL DEFAULT 'not_run',
        controls_blocked BOOLEAN NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_md_runs_phase ON md_runs (phase)",
    "CREATE INDEX IF NOT EXISTS ix_md_runs_request_sha256 ON md_runs (request_sha256)",
    "CREATE INDEX IF NOT EXISTS ix_md_runs_chemistry_profile_id ON md_runs (chemistry_profile_id)",
    """
    CREATE TABLE IF NOT EXISTS md_replica_runs (
        id VARCHAR(36) PRIMARY KEY NOT NULL,
        child_job_id VARCHAR(36) UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
        md_job_id VARCHAR(36) NOT NULL REFERENCES md_runs(job_id) ON DELETE CASCADE,
        replica_index INTEGER NOT NULL,
        attempt INTEGER NOT NULL,
        state VARCHAR(32) NOT NULL DEFAULT 'queued',
        active BOOLEAN NOT NULL DEFAULT 1,
        engine VARCHAR(32) NOT NULL,
        failure JSON,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        completed_at DATETIME,
        CONSTRAINT uq_md_replica_attempt UNIQUE (md_job_id, replica_index, attempt)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_md_replica_runs_md_job_id ON md_replica_runs (md_job_id)",
    "CREATE INDEX IF NOT EXISTS ix_md_replica_runs_state ON md_replica_runs (state)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_md_replica_active ON md_replica_runs (md_job_id, replica_index) WHERE active = 1",
    """
    CREATE TABLE IF NOT EXISTS md_attempt_segments (
        id VARCHAR(36) PRIMARY KEY NOT NULL,
        replica_run_id VARCHAR(36) NOT NULL REFERENCES md_replica_runs(id) ON DELETE CASCADE,
        segment_index INTEGER NOT NULL,
        state VARCHAR(32) NOT NULL DEFAULT 'queued',
        source_segment_id VARCHAR(36) REFERENCES md_attempt_segments(id),
        source_checkpoint_id VARCHAR(36) REFERENCES md_checkpoints(id),
        execution_plan_sha256 VARCHAR(64) NOT NULL,
        compatibility_key VARCHAR(64) NOT NULL,
        launch_identity JSON,
        reservation_token VARCHAR(128) UNIQUE,
        start_step INTEGER,
        end_step INTEGER,
        start_time_ps FLOAT,
        end_time_ps FLOAT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        completed_at DATETIME,
        CONSTRAINT uq_md_attempt_segment UNIQUE (replica_run_id, segment_index)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_md_attempt_segments_replica_run_id ON md_attempt_segments (replica_run_id)",
    "CREATE INDEX IF NOT EXISTS ix_md_attempt_segments_state ON md_attempt_segments (state)",
    "CREATE INDEX IF NOT EXISTS ix_md_attempt_segments_compatibility_key ON md_attempt_segments (compatibility_key)",
    """
    CREATE TABLE IF NOT EXISTS md_checkpoints (
        id VARCHAR(36) PRIMARY KEY NOT NULL,
        segment_id VARCHAR(36) NOT NULL REFERENCES md_attempt_segments(id) ON DELETE CASCADE,
        logical_role VARCHAR(32) NOT NULL,
        relative_path VARCHAR(1000) NOT NULL,
        sha256 VARCHAR(64) NOT NULL,
        bytes INTEGER NOT NULL,
        step INTEGER NOT NULL,
        time_ps FLOAT NOT NULL,
        compatibility_key VARCHAR(64) NOT NULL,
        accepted BOOLEAN NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT uq_md_checkpoint_path UNIQUE (segment_id, logical_role, relative_path)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_md_checkpoints_segment_id ON md_checkpoints (segment_id)",
    "CREATE INDEX IF NOT EXISTS ix_md_checkpoints_compatibility_key ON md_checkpoints (compatibility_key)",
    """
    CREATE TABLE IF NOT EXISTS job_artifacts (
        id VARCHAR(36) PRIMARY KEY NOT NULL,
        owner_job_id VARCHAR(36) NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        attempt INTEGER NOT NULL DEFAULT 0,
        logical_path VARCHAR(1000) NOT NULL,
        storage_path VARCHAR(1000) NOT NULL,
        sha256 VARCHAR(64) NOT NULL,
        bytes INTEGER NOT NULL,
        media_type VARCHAR(128) NOT NULL,
        provenance JSON NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT uq_job_artifact_logical UNIQUE (owner_job_id, attempt, logical_path)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_job_artifacts_owner_job_id ON job_artifacts (owner_job_id)",
    """
    CREATE TABLE IF NOT EXISTS md_events (
        id VARCHAR(36) PRIMARY KEY NOT NULL,
        md_job_id VARCHAR(36) NOT NULL REFERENCES md_runs(job_id) ON DELETE CASCADE,
        idempotency_key VARCHAR(128) NOT NULL,
        event_type VARCHAR(64) NOT NULL,
        expected_state_version INTEGER NOT NULL,
        resulting_state_version INTEGER NOT NULL,
        payload JSON NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT uq_md_event_idempotency UNIQUE (idempotency_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_md_events_md_job_id ON md_events (md_job_id)",
    "CREATE INDEX IF NOT EXISTS ix_md_events_event_type ON md_events (event_type)",
    """
    CREATE TABLE IF NOT EXISTS md_reconciler_leases (
        name VARCHAR(64) PRIMARY KEY NOT NULL,
        owner_id VARCHAR(128) NOT NULL,
        expires_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
)


def migrate(db_path: str | None = None) -> None:
    """Apply the additive MD schema atomically without rewriting existing rows."""
    database = Path(db_path) if db_path is not None else Path(get_db_path())
    connection = sqlite3.connect(database, timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        for statement in _STATEMENTS:
            connection.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
