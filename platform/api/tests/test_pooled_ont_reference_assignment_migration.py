from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from migrations.add_ngs_reference_sets import migrate as migrate_reference_sets
from migrations.add_pooled_ont_reference_assignment import migrate


def _bootstrap(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE jobs (id VARCHAR(36) PRIMARY KEY NOT NULL);
            CREATE TABLE molbio_ngs_receipts (id VARCHAR(36) PRIMARY KEY NOT NULL);
            INSERT INTO jobs(id) VALUES ('assignment-job'), ('child-job');
            INSERT INTO molbio_ngs_receipts(id) VALUES ('receipt-1');
            """
        )
        connection.commit()
    finally:
        connection.close()


def test_pooled_reference_assignment_migration_is_idempotent_and_append_only(tmp_path: Path) -> None:
    db_path = tmp_path / "main.db"
    _bootstrap(db_path)
    migrate_reference_sets(str(db_path))
    migrate(str(db_path))
    migrate(str(db_path))

    connection = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {
            "ngs_pooled_reference_targets",
            "ngs_pooled_assignment_releases",
            "ngs_pooled_assignment_release_targets",
        } <= tables

        connection.execute(
            """
            INSERT INTO ngs_reference_set_manifests(
                id, manifest_schema, mode, source_job_id, target_workflow,
                idempotency_key, request_fingerprint, manifest_path,
                manifest_sha256, manifest_json, created_at
            ) VALUES ('set-1', 'bms.ngs.reference-set.v1', 'pooled', 'assignment-job',
                      'ont_pooled_reference_assignment', 'submit-1', ?, '/inputs/set/reference_set.json', ?, '{}', ?)
            """,
            ("a" * 64, "b" * 64, "2026-08-08T00:00:00Z"),
        )
        connection.execute(
            """
            INSERT INTO ngs_pooled_reference_targets(
                id, reference_set_id, target_id, label, indistinguishable_group,
                sequence_id, revision_id, revision_sha256, receipt_id,
                fasta_path, fasta_sha256, created_at
            ) VALUES ('target-row-1', 'set-1', 'target-a', 'Target A', NULL,
                      'sequence-1', 'revision-1', ?, 'receipt-1', 'refs/target-a.fasta', ?, ?)
            """,
            ("c" * 64, "d" * 64, "2026-08-08T00:00:00Z"),
        )
        connection.execute(
            """
            INSERT INTO ngs_pooled_assignment_releases(
                id, assignment_job_id, reference_set_id, idempotency_key,
                request_fingerprint, target_workflow, assignment_summary_path,
                assignment_summary_sha256, created_at
            ) VALUES ('release-1', 'assignment-job', 'set-1', 'release-key', ?,
                      'ont_plasmid_qc', '/results/assignment/assignment_summary.json', ?, ?)
            """,
            ("e" * 64, "f" * 64, "2026-08-08T00:00:00Z"),
        )
        connection.execute(
            """
            INSERT INTO ngs_pooled_assignment_release_targets(
                id, release_id, assignment_job_id, reference_set_id, target_id,
                child_job_id, sequence_id, revision_id, revision_sha256, receipt_id,
                fasta_path, fasta_sha256, assigned_fastq_path,
                assigned_fastq_sha256, assigned_read_count, created_at
            ) VALUES ('release-target-1', 'release-1', 'assignment-job', 'set-1',
                      'target-a', 'child-job', 'sequence-1', 'revision-1', ?,
                      'receipt-1', '/inputs/set/refs/target-a.fasta', ?,
                      '/results/assignment/target-a.fastq', ?, 1, ?)
            """,
            ("c" * 64, "d" * 64, "1" * 64, "2026-08-08T00:00:00Z"),
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE ngs_pooled_reference_targets SET label='changed' WHERE id='target-row-1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE ngs_pooled_assignment_releases SET target_workflow='changed' WHERE id='release-1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "DELETE FROM ngs_pooled_assignment_release_targets WHERE id='release-target-1'"
            )
    finally:
        connection.close()
