"""Create immutable server-owned NGS reference-set launch records."""
from __future__ import annotations

import sqlite3


def migrate(db_path: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS ngs_reference_set_manifests (
                id VARCHAR(36) PRIMARY KEY NOT NULL,
                manifest_schema VARCHAR(80) NOT NULL,
                mode VARCHAR(32) NOT NULL,
                source_job_id VARCHAR(36) NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT,
                target_workflow VARCHAR(64) NOT NULL,
                idempotency_key VARCHAR(255) NOT NULL UNIQUE,
                request_fingerprint VARCHAR(64) NOT NULL,
                manifest_path VARCHAR(1000) NOT NULL,
                manifest_sha256 VARCHAR(64) NOT NULL,
                manifest_json JSON NOT NULL,
                created_at VARCHAR NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_ngs_reference_set_manifests_source_job_id
                ON ngs_reference_set_manifests(source_job_id);
            CREATE INDEX IF NOT EXISTS ix_ngs_reference_set_manifests_manifest_sha256
                ON ngs_reference_set_manifests(manifest_sha256);

            CREATE TABLE IF NOT EXISTS ngs_reference_set_mappings (
                id VARCHAR(36) PRIMARY KEY NOT NULL,
                reference_set_id VARCHAR(36) NOT NULL REFERENCES ngs_reference_set_manifests(id) ON DELETE RESTRICT,
                child_job_id VARCHAR(36) NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE RESTRICT,
                unit_id VARCHAR(32) NOT NULL,
                sample_alias VARCHAR(255),
                sequence_id VARCHAR(36) NOT NULL,
                revision_id VARCHAR(36) NOT NULL,
                revision_sha256 VARCHAR(64) NOT NULL,
                receipt_id VARCHAR(36) NOT NULL UNIQUE REFERENCES molbio_ngs_receipts(id) ON DELETE RESTRICT,
                fasta_snapshot_sha256 VARCHAR(64) NOT NULL,
                source_bam_path VARCHAR(1000) NOT NULL,
                source_bam_sha256 VARCHAR(64) NOT NULL,
                source_calls_sha256 VARCHAR(64) NOT NULL,
                preflight_sha256 VARCHAR(64) NOT NULL,
                demux_manifest_sha256 VARCHAR(64) NOT NULL,
                unit_manifest_sha256 VARCHAR(64) NOT NULL,
                created_at VARCHAR NOT NULL,
                UNIQUE(reference_set_id, unit_id)
            );
            CREATE INDEX IF NOT EXISTS ix_ngs_reference_set_mappings_reference_set_id
                ON ngs_reference_set_mappings(reference_set_id);
            CREATE INDEX IF NOT EXISTS ix_ngs_reference_set_mappings_unit_id
                ON ngs_reference_set_mappings(unit_id);

            CREATE TRIGGER IF NOT EXISTS trg_ngs_reference_set_manifests_no_update
            BEFORE UPDATE ON ngs_reference_set_manifests
            BEGIN
                SELECT RAISE(ABORT, 'NGS reference-set manifests are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS trg_ngs_reference_set_manifests_no_delete
            BEFORE DELETE ON ngs_reference_set_manifests
            BEGIN
                SELECT RAISE(ABORT, 'NGS reference-set manifests are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS trg_ngs_reference_set_mappings_no_update
            BEFORE UPDATE ON ngs_reference_set_mappings
            BEGIN
                SELECT RAISE(ABORT, 'NGS reference-set mappings are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS trg_ngs_reference_set_mappings_no_delete
            BEFORE DELETE ON ngs_reference_set_mappings
            BEGIN
                SELECT RAISE(ABORT, 'NGS reference-set mappings are immutable');
            END;
            """
        )
        connection.commit()
    finally:
        connection.close()
