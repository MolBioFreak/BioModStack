"""Add the governed ONT raw-signal representation and derivation ledgers."""
from __future__ import annotations

import sqlite3


def migrate(db_path: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS ont_raw_signal_representations (
                id VARCHAR(96) PRIMARY KEY NOT NULL,
                run_id VARCHAR(80) NOT NULL REFERENCES ont_instrument_runs(id) ON DELETE RESTRICT,
                observed_generation INTEGER NOT NULL,
                role VARCHAR(16) NOT NULL CHECK (role IN ('source','derived')),
                source_kind VARCHAR(32) NOT NULL,
                format VARCHAR(16) NOT NULL CHECK (format IN ('pod5','slow5','blow5')),
                source_fidelity VARCHAR(64) NOT NULL DEFAULT 'unknown',
                state VARCHAR(32) NOT NULL,
                reason_code VARCHAR(96) NOT NULL,
                artifact_manifest JSON NOT NULL,
                manifest_sha256 VARCHAR(64) NOT NULL,
                parent_representation_ids JSON NOT NULL,
                parent_manifest_sha256s JSON NOT NULL,
                compression JSON NOT NULL,
                runtime_identity JSON NOT NULL,
                validation_receipts JSON NOT NULL,
                profile_id VARCHAR(128),
                acquisition_id VARCHAR(255),
                read_count INTEGER,
                published_at VARCHAR,
                retention_pinned_at VARCHAR,
                created_at VARCHAR NOT NULL,
                CONSTRAINT uq_ont_raw_signal_rep_manifest UNIQUE (run_id, observed_generation, manifest_sha256)
            );
            CREATE INDEX IF NOT EXISTS ix_ont_raw_signal_representations_run_id ON ont_raw_signal_representations(run_id);
            CREATE INDEX IF NOT EXISTS ix_ont_raw_signal_representations_generation ON ont_raw_signal_representations(observed_generation);
            CREATE INDEX IF NOT EXISTS ix_ont_raw_signal_representations_format ON ont_raw_signal_representations(format);
            CREATE INDEX IF NOT EXISTS ix_ont_raw_signal_representations_state ON ont_raw_signal_representations(state);
            CREATE INDEX IF NOT EXISTS ix_ont_raw_signal_representations_manifest ON ont_raw_signal_representations(manifest_sha256);

            CREATE TABLE IF NOT EXISTS ont_raw_signal_derivation_jobs (
                id VARCHAR(96) PRIMARY KEY NOT NULL,
                run_id VARCHAR(80) NOT NULL REFERENCES ont_instrument_runs(id) ON DELETE RESTRICT,
                observed_generation INTEGER NOT NULL,
                source_representation_id VARCHAR(96) NOT NULL REFERENCES ont_raw_signal_representations(id) ON DELETE RESTRICT,
                output_representation_id VARCHAR(96) REFERENCES ont_raw_signal_representations(id) ON DELETE RESTRICT,
                requested_preference VARCHAR(16) NOT NULL,
                consumer_id VARCHAR(128) NOT NULL,
                profile_id VARCHAR(128) NOT NULL,
                state VARCHAR(32) NOT NULL,
                reason_code VARCHAR(96) NOT NULL,
                resource_snapshot JSON NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0,
                claim_token VARCHAR(96) UNIQUE,
                lease_expires_at VARCHAR,
                cancel_requested_at VARCHAR,
                stage_receipts JSON NOT NULL,
                failure_code VARCHAR(96),
                failure_message TEXT,
                created_at VARCHAR NOT NULL,
                updated_at VARCHAR NOT NULL,
                completed_at VARCHAR,
                CONSTRAINT uq_ont_raw_signal_derivation UNIQUE (run_id, observed_generation, source_representation_id, profile_id)
            );
            CREATE INDEX IF NOT EXISTS ix_ont_raw_signal_derivation_jobs_run_id ON ont_raw_signal_derivation_jobs(run_id);
            CREATE INDEX IF NOT EXISTS ix_ont_raw_signal_derivation_jobs_generation ON ont_raw_signal_derivation_jobs(observed_generation);
            CREATE INDEX IF NOT EXISTS ix_ont_raw_signal_derivation_jobs_state ON ont_raw_signal_derivation_jobs(state);
            CREATE INDEX IF NOT EXISTS ix_ont_raw_signal_derivation_jobs_lease ON ont_raw_signal_derivation_jobs(lease_expires_at);

            CREATE TABLE IF NOT EXISTS ont_raw_signal_derivation_events (
                id VARCHAR(96) PRIMARY KEY NOT NULL,
                job_id VARCHAR(96) NOT NULL REFERENCES ont_raw_signal_derivation_jobs(id) ON DELETE RESTRICT,
                state VARCHAR(32) NOT NULL,
                reason_code VARCHAR(96) NOT NULL,
                receipt JSON NOT NULL,
                created_at VARCHAR NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_ont_raw_signal_derivation_events_job_id ON ont_raw_signal_derivation_events(job_id);

            CREATE TABLE IF NOT EXISTS ont_raw_signal_lookups (
                id VARCHAR(96) PRIMARY KEY NOT NULL,
                run_id VARCHAR(80) NOT NULL REFERENCES ont_instrument_runs(id) ON DELETE RESTRICT,
                observed_generation INTEGER NOT NULL,
                representation_id VARCHAR(96) NOT NULL REFERENCES ont_raw_signal_representations(id) ON DELETE RESTRICT,
                read_id VARCHAR(128) NOT NULL,
                state VARCHAR(32) NOT NULL DEFAULT 'requested',
                reason_code VARCHAR(96) NOT NULL DEFAULT 'requested',
                claim_token VARCHAR(96) UNIQUE,
                lease_expires_at VARCHAR,
                sample_count INTEGER,
                samples JSON,
                receipt JSON NOT NULL,
                created_at VARCHAR NOT NULL,
                updated_at VARCHAR NOT NULL,
                completed_at VARCHAR,
                CONSTRAINT uq_ont_raw_signal_lookup_read UNIQUE (representation_id, read_id)
            );
            CREATE INDEX IF NOT EXISTS ix_ont_raw_signal_lookups_run_id ON ont_raw_signal_lookups(run_id);
            CREATE INDEX IF NOT EXISTS ix_ont_raw_signal_lookups_generation ON ont_raw_signal_lookups(observed_generation);
            CREATE INDEX IF NOT EXISTS ix_ont_raw_signal_lookups_state ON ont_raw_signal_lookups(state);

            CREATE TRIGGER IF NOT EXISTS trg_ont_raw_signal_source_no_update
            BEFORE UPDATE ON ont_raw_signal_representations
            WHEN OLD.role = 'source' AND (
                NEW.run_id != OLD.run_id OR
                NEW.observed_generation != OLD.observed_generation OR
                NEW.role != OLD.role OR
                NEW.source_kind != OLD.source_kind OR
                NEW.format != OLD.format OR
                NEW.artifact_manifest != OLD.artifact_manifest OR
                NEW.manifest_sha256 != OLD.manifest_sha256 OR
                COALESCE(NEW.acquisition_id, '') != COALESCE(OLD.acquisition_id, '') OR
                NEW.created_at != OLD.created_at
            )
            BEGIN
                SELECT RAISE(ABORT, 'ONT raw-signal source identity and artifact authority are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_raw_signal_source_no_delete
            BEFORE DELETE ON ont_raw_signal_representations
            WHEN OLD.role = 'source'
            BEGIN
                SELECT RAISE(ABORT, 'ONT raw-signal source representations require operator retention action');
            END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_raw_signal_derivation_events_no_update
            BEFORE UPDATE ON ont_raw_signal_derivation_events
            BEGIN
                SELECT RAISE(ABORT, 'ONT raw-signal derivation events are append-only');
            END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_raw_signal_derivation_events_no_delete
            BEFORE DELETE ON ont_raw_signal_derivation_events
            BEGIN
                SELECT RAISE(ABORT, 'ONT raw-signal derivation events are append-only');
            END;
            """
        )
        connection.commit()
    finally:
        connection.close()
