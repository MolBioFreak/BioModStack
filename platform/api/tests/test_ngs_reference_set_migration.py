from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from migrations.add_ngs_reference_sets import migrate


def _bootstrap_prerequisites(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE jobs (id VARCHAR(36) PRIMARY KEY NOT NULL);
            CREATE TABLE molbio_ngs_receipts (id VARCHAR(36) PRIMARY KEY NOT NULL);
            INSERT INTO jobs(id) VALUES ('source-job'), ('child-job');
            INSERT INTO molbio_ngs_receipts(id) VALUES ('receipt-1');
            """
        )
        connection.commit()
    finally:
        connection.close()


def test_ngs_reference_set_migration_is_idempotent_and_immutable(tmp_path: Path) -> None:
    db_path = tmp_path / "biomodstack.db"
    _bootstrap_prerequisites(db_path)

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
        assert {"ngs_reference_set_manifests", "ngs_reference_set_mappings"} <= tables

        connection.execute(
            """
            INSERT INTO ngs_reference_set_manifests(
                id, manifest_schema, mode, source_job_id, target_workflow,
                idempotency_key, request_fingerprint, manifest_path,
                manifest_sha256, manifest_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "set-1",
                "bms.ngs.reference-set.v1",
                "barcoded",
                "source-job",
                "ont_plasmid_qc",
                "request-1",
                "a" * 64,
                "/inputs/reference-set.json",
                "b" * 64,
                "{}",
                "2026-08-08T00:00:00Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO ngs_reference_set_mappings(
                id, reference_set_id, child_job_id, unit_id, sample_alias,
                sequence_id, revision_id, revision_sha256, receipt_id,
                fasta_snapshot_sha256, source_bam_path, source_bam_sha256,
                source_calls_sha256, preflight_sha256, demux_manifest_sha256,
                unit_manifest_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "mapping-1",
                "set-1",
                "child-job",
                "barcode01",
                "sample-a",
                "sequence-1",
                "revision-1",
                "c" * 64,
                "receipt-1",
                "d" * 64,
                "/results/source/barcode01.bam",
                "e" * 64,
                "f" * 64,
                "0" * 64,
                "1" * 64,
                "2" * 64,
                "2026-08-08T00:00:00Z",
            ),
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE ngs_reference_set_manifests SET target_workflow='changed' WHERE id='set-1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "DELETE FROM ngs_reference_set_mappings WHERE id='mapping-1'"
            )
    finally:
        connection.close()
