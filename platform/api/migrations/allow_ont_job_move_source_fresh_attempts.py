"""Let a failed job-sourced ONT move source receive a fresh validation attempt.

Migration 34 limited attempt lineage to external move-BAM registrations. A basecall
job's own calls.bam that failed validation (for example under a since-corrected
validator) could therefore never be re-validated. This replaces only the lineage
insert trigger: the predecessor must still be a terminal, unclaimed failure with
exactly one producer authority, and the new row must repeat its exact identity.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from migrations import add_ont_move_source_attempt_lineage as migration_34

LINEAGE_TRIGGER_NAME = "trg_ont_move_source_attempt_lineage_insert"
LINEAGE_TRIGGER_SQL = """
        CREATE TRIGGER IF NOT EXISTS trg_ont_move_source_attempt_lineage_insert
        BEFORE INSERT ON ont_move_table_sources
        WHEN NEW.predecessor_move_source_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM ont_move_table_sources AS predecessor
            WHERE predecessor.id = NEW.predecessor_move_source_id
              AND predecessor.validation_state = 'failed'
              AND (predecessor.source_job_id IS NULL) != (predecessor.external_registration_receipt_id IS NULL)
              AND predecessor.claim_token IS NULL
              AND predecessor.lease_expires_at IS NULL
              AND NEW.attempt_number = predecessor.attempt_number + 1
              AND NEW.run_id IS predecessor.run_id
              AND NEW.observed_generation IS predecessor.observed_generation
              AND NEW.raw_representation_id IS predecessor.raw_representation_id
              AND NEW.input_file_id IS predecessor.input_file_id
              AND NEW.source_job_id IS predecessor.source_job_id
              AND NEW.external_registration_receipt_id IS predecessor.external_registration_receipt_id
              AND NEW.artifact_sha256 IS predecessor.artifact_sha256
              AND NEW.artifact_size_bytes IS predecessor.artifact_size_bytes
              AND NEW.molecule_type IS predecessor.molecule_type
              AND NEW.source_runtime_identity IS predecessor.source_runtime_identity
        )
        BEGIN SELECT RAISE(ABORT, 'ONT move-source attempt lineage authority diverged'); END
"""
LINEAGE_TRIGGER_DIGEST = migration_34._trigger_sql_digest(LINEAGE_TRIGGER_SQL)

_ATTEST_LOCK = threading.Lock()


def migrate(db_path: str | Path) -> None:
    connection = sqlite3.connect(str(db_path), timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(f'DROP TRIGGER IF EXISTS "{LINEAGE_TRIGGER_NAME}"')
        connection.execute(LINEAGE_TRIGGER_SQL)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def attest(connection: sqlite3.Connection) -> None:
    """Migration 34's full schema attestation with this migration's lineage trigger."""
    with _ATTEST_LOCK:
        original = migration_34.MIGRATION_34_TRIGGER_SQL_DIGESTS
        migration_34.MIGRATION_34_TRIGGER_SQL_DIGESTS = {
            **original, LINEAGE_TRIGGER_NAME: LINEAGE_TRIGGER_DIGEST,
        }
        try:
            migration_34.attest(connection)
        finally:
            migration_34.MIGRATION_34_TRIGGER_SQL_DIGESTS = original


__all__ = ["LINEAGE_TRIGGER_NAME", "LINEAGE_TRIGGER_SQL", "attest", "migrate"]
