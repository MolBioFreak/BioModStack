"""Create immutable pooled ONT targets and append-only release records."""
from __future__ import annotations

import sqlite3


def migrate(db_path: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS ngs_pooled_reference_targets (
                id VARCHAR(36) PRIMARY KEY NOT NULL,
                reference_set_id VARCHAR(36) NOT NULL
                    REFERENCES ngs_reference_set_manifests(id) ON DELETE RESTRICT,
                target_id VARCHAR(128) NOT NULL,
                label VARCHAR(255) NOT NULL,
                indistinguishable_group VARCHAR(128),
                sequence_id VARCHAR(128) NOT NULL,
                revision_id VARCHAR(128) NOT NULL,
                revision_sha256 VARCHAR(64) NOT NULL,
                receipt_id VARCHAR(36) NOT NULL UNIQUE
                    REFERENCES molbio_ngs_receipts(id) ON DELETE RESTRICT,
                fasta_path VARCHAR(512) NOT NULL,
                fasta_sha256 VARCHAR(64) NOT NULL,
                created_at VARCHAR NOT NULL,
                UNIQUE(reference_set_id, target_id)
            );
            CREATE INDEX IF NOT EXISTS ix_ngs_pooled_reference_targets_reference_set_id
                ON ngs_pooled_reference_targets(reference_set_id);
            CREATE INDEX IF NOT EXISTS ix_ngs_pooled_reference_targets_target_id
                ON ngs_pooled_reference_targets(target_id);

            CREATE TABLE IF NOT EXISTS ngs_pooled_assignment_releases (
                id VARCHAR(36) PRIMARY KEY NOT NULL,
                assignment_job_id VARCHAR(36) NOT NULL
                    REFERENCES jobs(id) ON DELETE RESTRICT,
                reference_set_id VARCHAR(36) NOT NULL
                    REFERENCES ngs_reference_set_manifests(id) ON DELETE RESTRICT,
                idempotency_key VARCHAR(255) NOT NULL UNIQUE,
                request_fingerprint VARCHAR(64) NOT NULL,
                target_workflow VARCHAR(64) NOT NULL,
                assignment_summary_path VARCHAR(1000) NOT NULL,
                assignment_summary_sha256 VARCHAR(64) NOT NULL,
                created_at VARCHAR NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_ngs_pooled_assignment_releases_assignment_job_id
                ON ngs_pooled_assignment_releases(assignment_job_id);
            CREATE INDEX IF NOT EXISTS ix_ngs_pooled_assignment_releases_reference_set_id
                ON ngs_pooled_assignment_releases(reference_set_id);

            CREATE TABLE IF NOT EXISTS ngs_pooled_assignment_release_targets (
                id VARCHAR(36) PRIMARY KEY NOT NULL,
                release_id VARCHAR(36) NOT NULL
                    REFERENCES ngs_pooled_assignment_releases(id) ON DELETE RESTRICT,
                assignment_job_id VARCHAR(36) NOT NULL
                    REFERENCES jobs(id) ON DELETE RESTRICT,
                reference_set_id VARCHAR(36) NOT NULL
                    REFERENCES ngs_reference_set_manifests(id) ON DELETE RESTRICT,
                target_id VARCHAR(128) NOT NULL,
                child_job_id VARCHAR(36) NOT NULL UNIQUE
                    REFERENCES jobs(id) ON DELETE RESTRICT,
                sequence_id VARCHAR(128) NOT NULL,
                revision_id VARCHAR(128) NOT NULL,
                revision_sha256 VARCHAR(64) NOT NULL,
                receipt_id VARCHAR(36) NOT NULL
                    REFERENCES molbio_ngs_receipts(id) ON DELETE RESTRICT,
                fasta_path VARCHAR(1000) NOT NULL,
                fasta_sha256 VARCHAR(64) NOT NULL,
                assigned_fastq_path VARCHAR(1000) NOT NULL,
                assigned_fastq_sha256 VARCHAR(64) NOT NULL,
                assigned_read_count INTEGER NOT NULL,
                created_at VARCHAR NOT NULL,
                UNIQUE(release_id, target_id)
            );
            CREATE INDEX IF NOT EXISTS ix_ngs_pooled_assignment_release_targets_release_id
                ON ngs_pooled_assignment_release_targets(release_id);
            CREATE INDEX IF NOT EXISTS ix_ngs_pooled_assignment_release_targets_assignment_job_id
                ON ngs_pooled_assignment_release_targets(assignment_job_id);

            CREATE TRIGGER IF NOT EXISTS trg_ngs_pooled_reference_targets_no_update
            BEFORE UPDATE ON ngs_pooled_reference_targets
            BEGIN
                SELECT RAISE(ABORT, 'NGS pooled reference targets are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS trg_ngs_pooled_reference_targets_no_delete
            BEFORE DELETE ON ngs_pooled_reference_targets
            BEGIN
                SELECT RAISE(ABORT, 'NGS pooled reference targets are immutable');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_ngs_pooled_assignment_releases_no_update
            BEFORE UPDATE ON ngs_pooled_assignment_releases
            BEGIN
                SELECT RAISE(ABORT, 'NGS pooled assignment releases are append-only');
            END;
            CREATE TRIGGER IF NOT EXISTS trg_ngs_pooled_assignment_releases_no_delete
            BEFORE DELETE ON ngs_pooled_assignment_releases
            BEGIN
                SELECT RAISE(ABORT, 'NGS pooled assignment releases are append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_ngs_pooled_assignment_release_targets_no_update
            BEFORE UPDATE ON ngs_pooled_assignment_release_targets
            BEGIN
                SELECT RAISE(ABORT, 'NGS pooled assignment release targets are append-only');
            END;
            CREATE TRIGGER IF NOT EXISTS trg_ngs_pooled_assignment_release_targets_no_delete
            BEFORE DELETE ON ngs_pooled_assignment_release_targets
            BEGIN
                SELECT RAISE(ABORT, 'NGS pooled assignment release targets are append-only');
            END;
            """
        )
        connection.commit()
    finally:
        connection.close()
