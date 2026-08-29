"""Add governed Squigualiser move, mapping, view, and viewer-session ledgers."""
from __future__ import annotations

import sqlite3


def migrate(db_path: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS ont_move_table_sources (
                id VARCHAR(96) PRIMARY KEY NOT NULL,
                run_id VARCHAR(80) NOT NULL REFERENCES ont_instrument_runs(id) ON DELETE RESTRICT,
                observed_generation INTEGER NOT NULL,
                raw_representation_id VARCHAR(96) NOT NULL REFERENCES ont_raw_signal_representations(id) ON DELETE RESTRICT,
                input_file_id VARCHAR(36) NOT NULL REFERENCES input_files(id) ON DELETE RESTRICT,
                source_job_id VARCHAR(36) REFERENCES jobs(id) ON DELETE RESTRICT,
                external_registration_receipt_id VARCHAR(128),
                artifact_sha256 VARCHAR(64) NOT NULL,
                artifact_size_bytes INTEGER NOT NULL,
                bam_header_sha256 VARCHAR(64),
                record_count INTEGER,
                unique_read_count INTEGER,
                mv_tag_count INTEGER,
                ts_tag_count INTEGER,
                ns_tag_count INTEGER,
                basecall_model_id VARCHAR(255),
                molecule_type VARCHAR(16) NOT NULL CHECK (molecule_type IN ('dna','rna')),
                source_runtime_identity JSON NOT NULL,
                read_inventory_sha256 VARCHAR(64),
                validation_state VARCHAR(32) NOT NULL CHECK (validation_state IN ('requested','running','ready','failed')),
                reason_code VARCHAR(96) NOT NULL,
                validation_receipt JSON NOT NULL,
                claim_token VARCHAR(96) UNIQUE,
                lease_expires_at VARCHAR,
                created_at VARCHAR NOT NULL,
                validated_at VARCHAR,
                CONSTRAINT uq_ont_move_source_artifact UNIQUE (run_id, observed_generation, artifact_sha256)
            );
            CREATE INDEX IF NOT EXISTS ix_ont_move_sources_generation ON ont_move_table_sources(run_id, observed_generation);
            CREATE INDEX IF NOT EXISTS ix_ont_move_sources_state ON ont_move_table_sources(validation_state);

            CREATE TABLE IF NOT EXISTS ont_signal_calibration_artifacts (
                id VARCHAR(96) PRIMARY KEY NOT NULL,
                raw_representation_id VARCHAR(96) NOT NULL REFERENCES ont_raw_signal_representations(id) ON DELETE RESTRICT,
                move_source_id VARCHAR(96) NOT NULL REFERENCES ont_move_table_sources(id) ON DELETE RESTRICT,
                basecall_model_id VARCHAR(255) NOT NULL,
                sample_selection JSON NOT NULL,
                recommended_kmer_length INTEGER NOT NULL,
                recommended_signal_move_offset INTEGER NOT NULL,
                score_evidence JSON NOT NULL,
                runtime_identity JSON NOT NULL,
                parent_sha256s JSON NOT NULL,
                artifact_sha256 VARCHAR(64) NOT NULL UNIQUE,
                created_at VARCHAR NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ont_signal_calibration_jobs (
                id VARCHAR(96) PRIMARY KEY NOT NULL,
                run_id VARCHAR(80) NOT NULL REFERENCES ont_instrument_runs(id) ON DELETE RESTRICT,
                observed_generation INTEGER NOT NULL,
                raw_representation_id VARCHAR(96) NOT NULL REFERENCES ont_raw_signal_representations(id) ON DELETE RESTRICT,
                move_source_id VARCHAR(96) NOT NULL REFERENCES ont_move_table_sources(id) ON DELETE RESTRICT,
                sample_count INTEGER NOT NULL CHECK (sample_count >= 1 AND sample_count <= 100),
                request_fingerprint VARCHAR(64) NOT NULL UNIQUE,
                state VARCHAR(32) NOT NULL CHECK (state IN ('requested','running','ready','failed','cancelled')),
                reason_code VARCHAR(96) NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0,
                claim_token VARCHAR(96) UNIQUE,
                lease_expires_at VARCHAR,
                cancel_requested_at VARCHAR,
                resource_snapshot JSON NOT NULL,
                stage_receipts JSON NOT NULL,
                calibration_artifact_id VARCHAR(96) UNIQUE REFERENCES ont_signal_calibration_artifacts(id) ON DELETE RESTRICT,
                failure_code VARCHAR(96),
                failure_message TEXT CHECK (failure_message IS NULL OR length(failure_message) <= 4000),
                created_at VARCHAR NOT NULL,
                updated_at VARCHAR NOT NULL,
                completed_at VARCHAR
            );
            CREATE INDEX IF NOT EXISTS ix_ont_signal_calibration_jobs_generation ON ont_signal_calibration_jobs(run_id, observed_generation);
            CREATE INDEX IF NOT EXISTS ix_ont_signal_calibration_jobs_state ON ont_signal_calibration_jobs(state, lease_expires_at);

            CREATE TABLE IF NOT EXISTS ont_signal_mapping_profiles (
                id VARCHAR(96) PRIMARY KEY NOT NULL,
                name VARCHAR(255) NOT NULL,
                molecule_type VARCHAR(16) NOT NULL CHECK (molecule_type IN ('dna','rna')),
                basecall_model_id VARCHAR(255) NOT NULL,
                kmer_length INTEGER NOT NULL CHECK (kmer_length > 0),
                signal_move_offset INTEGER NOT NULL,
                parameter_source VARCHAR(32) NOT NULL CHECK (parameter_source = 'approved_calibration'),
                calibration_artifact_id VARCHAR(96) NOT NULL REFERENCES ont_signal_calibration_artifacts(id) ON DELETE RESTRICT,
                primary_alignment_policy VARCHAR(32) NOT NULL CHECK (primary_alignment_policy = 'primary_only'),
                minimum_mapq INTEGER NOT NULL CHECK (minimum_mapq = 0),
                include_supplementary INTEGER NOT NULL CHECK (include_supplementary = 0),
                read_set_selection VARCHAR(32) NOT NULL CHECK (read_set_selection = 'immutable_full_set'),
                approval_receipt JSON NOT NULL,
                approved_at VARCHAR NOT NULL,
                approved_by VARCHAR(255),
                created_at VARCHAR NOT NULL,
                CONSTRAINT uq_ont_signal_profile_calibration UNIQUE (basecall_model_id, molecule_type, kmer_length, signal_move_offset, calibration_artifact_id, minimum_mapq, read_set_selection)
            );

            CREATE TABLE IF NOT EXISTS ont_signal_mapping_jobs (
                id VARCHAR(96) PRIMARY KEY NOT NULL,
                mode VARCHAR(32) NOT NULL CHECK (mode IN ('signal_to_read','signal_to_reference')),
                run_id VARCHAR(80) NOT NULL REFERENCES ont_instrument_runs(id) ON DELETE RESTRICT,
                observed_generation INTEGER NOT NULL,
                raw_representation_id VARCHAR(96) NOT NULL REFERENCES ont_raw_signal_representations(id) ON DELETE RESTRICT,
                move_source_id VARCHAR(96) NOT NULL REFERENCES ont_move_table_sources(id) ON DELETE RESTRICT,
                mapping_profile_id VARCHAR(96) NOT NULL REFERENCES ont_signal_mapping_profiles(id) ON DELETE RESTRICT,
                reference_revision_id VARCHAR(128),
                alignment_job_id VARCHAR(36) REFERENCES jobs(id) ON DELETE RESTRICT,
                alignment_session_id VARCHAR(96),
                parent_mapping_job_id VARCHAR(96) REFERENCES ont_signal_mapping_jobs(id) ON DELETE RESTRICT,
                request_fingerprint VARCHAR(64) NOT NULL UNIQUE,
                state VARCHAR(32) NOT NULL CHECK (state IN ('requested','running','ready','failed','cancelled')),
                reason_code VARCHAR(96) NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0,
                claim_token VARCHAR(96) UNIQUE,
                lease_expires_at VARCHAR,
                cancel_requested_at VARCHAR,
                resource_snapshot JSON NOT NULL,
                stage_receipts JSON NOT NULL,
                failure_code VARCHAR(96),
                failure_message TEXT,
                created_at VARCHAR NOT NULL,
                updated_at VARCHAR NOT NULL,
                completed_at VARCHAR
            );
            CREATE INDEX IF NOT EXISTS ix_ont_signal_mapping_jobs_generation ON ont_signal_mapping_jobs(run_id, observed_generation);
            CREATE INDEX IF NOT EXISTS ix_ont_signal_mapping_jobs_state ON ont_signal_mapping_jobs(state, lease_expires_at);

            CREATE TABLE IF NOT EXISTS ont_signal_mapping_events (
                id VARCHAR(96) PRIMARY KEY NOT NULL,
                job_id VARCHAR(96) NOT NULL REFERENCES ont_signal_mapping_jobs(id) ON DELETE RESTRICT,
                state VARCHAR(32) NOT NULL,
                reason_code VARCHAR(96) NOT NULL,
                receipt JSON NOT NULL,
                created_at VARCHAR NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_ont_signal_mapping_events_job ON ont_signal_mapping_events(job_id, created_at);

            CREATE TABLE IF NOT EXISTS ont_signal_mapping_artifacts (
                id VARCHAR(96) PRIMARY KEY NOT NULL,
                mapping_job_id VARCHAR(96) NOT NULL REFERENCES ont_signal_mapping_jobs(id) ON DELETE RESTRICT,
                kind VARCHAR(64) NOT NULL,
                managed_relative_path TEXT NOT NULL UNIQUE,
                media_type VARCHAR(255) NOT NULL,
                sha256 VARCHAR(64) NOT NULL,
                size_bytes INTEGER NOT NULL,
                parent_identities JSON NOT NULL,
                runtime_identity JSON NOT NULL,
                validation_receipt JSON NOT NULL,
                created_at VARCHAR NOT NULL,
                CONSTRAINT uq_ont_signal_mapping_artifact_kind UNIQUE (mapping_job_id, kind)
            );

            CREATE TABLE IF NOT EXISTS ont_squigualiser_view_jobs (
                id VARCHAR(96) PRIMARY KEY NOT NULL,
                mapping_artifact_id VARCHAR(96) NOT NULL REFERENCES ont_signal_mapping_artifacts(id) ON DELETE RESTRICT,
                mode VARCHAR(32) NOT NULL CHECK (mode IN ('read','reference','pileup')),
                read_id VARCHAR(128),
                reference_contig VARCHAR(255),
                reference_start INTEGER,
                reference_end INTEGER,
                render_params JSON NOT NULL,
                request_fingerprint VARCHAR(64) NOT NULL UNIQUE,
                state VARCHAR(32) NOT NULL CHECK (state IN ('requested','running','ready','failed','cancelled')),
                reason_code VARCHAR(96) NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0,
                claim_token VARCHAR(96) UNIQUE,
                lease_expires_at VARCHAR,
                cancel_requested_at VARCHAR,
                output_manifest JSON NOT NULL,
                render_receipt JSON NOT NULL,
                failure_code VARCHAR(96),
                failure_message TEXT,
                created_at VARCHAR NOT NULL,
                updated_at VARCHAR NOT NULL,
                completed_at VARCHAR
            );
            CREATE INDEX IF NOT EXISTS ix_ont_squigualiser_views_state ON ont_squigualiser_view_jobs(state, lease_expires_at);

            CREATE TABLE IF NOT EXISTS ont_signal_viewer_sessions (
                id VARCHAR(96) PRIMARY KEY NOT NULL,
                dataset_id VARCHAR(128) NOT NULL,
                run_id VARCHAR(80) NOT NULL REFERENCES ont_instrument_runs(id) ON DELETE RESTRICT,
                observed_generation INTEGER NOT NULL,
                alignment_job_id VARCHAR(36) REFERENCES jobs(id) ON DELETE RESTRICT,
                alignment_session_id VARCHAR(96),
                reference_revision_id VARCHAR(128),
                raw_representation_id VARCHAR(96) REFERENCES ont_raw_signal_representations(id) ON DELETE RESTRICT,
                move_source_id VARCHAR(96) REFERENCES ont_move_table_sources(id) ON DELETE RESTRICT,
                mapping_profile_id VARCHAR(96) REFERENCES ont_signal_mapping_profiles(id) ON DELETE RESTRICT,
                contig VARCHAR(255),
                locus_start INTEGER,
                locus_end INTEGER,
                selected_read_id VARCHAR(128),
                igv_state JSON NOT NULL,
                signal_state JSON NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                created_at VARCHAR NOT NULL,
                updated_at VARCHAR NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_ont_signal_viewer_session_generation ON ont_signal_viewer_sessions(run_id, observed_generation);

            CREATE TRIGGER IF NOT EXISTS trg_ont_move_source_no_delete BEFORE DELETE ON ont_move_table_sources
            BEGIN SELECT RAISE(ABORT, 'ONT move-table sources are immutable retained evidence'); END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_move_source_identity_no_update BEFORE UPDATE ON ont_move_table_sources
            WHEN NEW.run_id IS NOT OLD.run_id OR NEW.observed_generation IS NOT OLD.observed_generation OR
                 NEW.raw_representation_id IS NOT OLD.raw_representation_id OR NEW.input_file_id IS NOT OLD.input_file_id OR
                 NEW.source_job_id IS NOT OLD.source_job_id OR
                 NEW.external_registration_receipt_id IS NOT OLD.external_registration_receipt_id OR
                 NEW.source_runtime_identity IS NOT OLD.source_runtime_identity OR
                 NEW.artifact_sha256 IS NOT OLD.artifact_sha256 OR NEW.artifact_size_bytes IS NOT OLD.artifact_size_bytes OR
                 NEW.molecule_type IS NOT OLD.molecule_type OR NEW.created_at IS NOT OLD.created_at
            BEGIN SELECT RAISE(ABORT, 'ONT move-table source identity is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_move_source_terminal_no_update BEFORE UPDATE ON ont_move_table_sources
            WHEN OLD.validation_state IN ('ready','failed') AND (
                 NEW.run_id IS NOT OLD.run_id OR
                 NEW.observed_generation IS NOT OLD.observed_generation OR
                 NEW.raw_representation_id IS NOT OLD.raw_representation_id OR
                 NEW.input_file_id IS NOT OLD.input_file_id OR
                 NEW.source_job_id IS NOT OLD.source_job_id OR
                 NEW.external_registration_receipt_id IS NOT OLD.external_registration_receipt_id OR
                 NEW.artifact_sha256 IS NOT OLD.artifact_sha256 OR
                 NEW.artifact_size_bytes IS NOT OLD.artifact_size_bytes OR
                 NEW.bam_header_sha256 IS NOT OLD.bam_header_sha256 OR
                 NEW.record_count IS NOT OLD.record_count OR
                 NEW.unique_read_count IS NOT OLD.unique_read_count OR
                 NEW.mv_tag_count IS NOT OLD.mv_tag_count OR
                 NEW.ts_tag_count IS NOT OLD.ts_tag_count OR
                 NEW.ns_tag_count IS NOT OLD.ns_tag_count OR
                 NEW.basecall_model_id IS NOT OLD.basecall_model_id OR
                 NEW.molecule_type IS NOT OLD.molecule_type OR
                 NEW.source_runtime_identity IS NOT OLD.source_runtime_identity OR
                 NEW.read_inventory_sha256 IS NOT OLD.read_inventory_sha256 OR
                 NEW.validation_state IS NOT OLD.validation_state OR
                 NEW.reason_code IS NOT OLD.reason_code OR
                 NEW.validation_receipt IS NOT OLD.validation_receipt OR
                 NEW.claim_token IS NOT OLD.claim_token OR
                 NEW.lease_expires_at IS NOT OLD.lease_expires_at OR
                 NEW.created_at IS NOT OLD.created_at OR
                 NEW.validated_at IS NOT OLD.validated_at)
            BEGIN SELECT RAISE(ABORT, 'ONT move-table source terminal evidence is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_signal_calibration_job_no_delete BEFORE DELETE ON ont_signal_calibration_jobs
            BEGIN SELECT RAISE(ABORT, 'ONT signal calibration jobs are retained receipts'); END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_signal_calibration_job_identity_no_update BEFORE UPDATE ON ont_signal_calibration_jobs
            WHEN NEW.run_id != OLD.run_id OR NEW.observed_generation != OLD.observed_generation OR
                 NEW.raw_representation_id != OLD.raw_representation_id OR NEW.move_source_id != OLD.move_source_id OR
                 NEW.sample_count != OLD.sample_count OR NEW.request_fingerprint != OLD.request_fingerprint OR
                 NEW.created_at != OLD.created_at
            BEGIN SELECT RAISE(ABORT, 'ONT signal calibration request identity is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_signal_calibration_job_terminal_no_update BEFORE UPDATE ON ont_signal_calibration_jobs
            WHEN OLD.state IN ('ready','failed','cancelled') AND (
                 NEW.state IS NOT OLD.state OR NEW.reason_code IS NOT OLD.reason_code OR
                 NEW.resource_snapshot IS NOT OLD.resource_snapshot OR NEW.stage_receipts IS NOT OLD.stage_receipts OR
                 NEW.calibration_artifact_id IS NOT OLD.calibration_artifact_id OR
                 NEW.failure_code IS NOT OLD.failure_code OR NEW.failure_message IS NOT OLD.failure_message OR
                 NEW.cancel_requested_at IS NOT OLD.cancel_requested_at OR NEW.completed_at IS NOT OLD.completed_at)
            BEGIN SELECT RAISE(ABORT, 'ONT signal calibration terminal evidence is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_signal_calibration_job_receipts_append_only BEFORE UPDATE ON ont_signal_calibration_jobs
            WHEN OLD.state NOT IN ('ready','failed','cancelled') AND NEW.state NOT IN ('ready','failed','cancelled') AND
                 EXISTS (
                    SELECT 1 FROM json_tree(OLD.stage_receipts) AS old_item
                    WHERE NOT EXISTS (
                        SELECT 1 FROM json_tree(NEW.stage_receipts) AS new_item
                        WHERE new_item.fullkey = old_item.fullkey AND new_item.type = old_item.type
                          AND new_item.atom IS old_item.atom
                    )
                 )
            BEGIN SELECT RAISE(ABORT, 'ONT signal calibration stage receipts are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_signal_calibration_artifact_no_update BEFORE UPDATE ON ont_signal_calibration_artifacts
            BEGIN SELECT RAISE(ABORT, 'ONT signal calibration artifacts are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_signal_calibration_artifact_no_delete BEFORE DELETE ON ont_signal_calibration_artifacts
            BEGIN SELECT RAISE(ABORT, 'ONT signal calibration artifacts are retained evidence'); END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_signal_mapping_profiles_no_update BEFORE UPDATE ON ont_signal_mapping_profiles
            BEGIN SELECT RAISE(ABORT, 'ONT signal mapping profiles are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_signal_mapping_profiles_no_delete BEFORE DELETE ON ont_signal_mapping_profiles
            BEGIN SELECT RAISE(ABORT, 'ONT signal mapping profiles are immutable retained authority'); END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_signal_mapping_jobs_identity_no_update BEFORE UPDATE ON ont_signal_mapping_jobs
            WHEN NEW.mode IS NOT OLD.mode OR NEW.run_id IS NOT OLD.run_id OR
                 NEW.observed_generation IS NOT OLD.observed_generation OR
                 NEW.raw_representation_id IS NOT OLD.raw_representation_id OR
                 NEW.move_source_id IS NOT OLD.move_source_id OR
                 NEW.mapping_profile_id IS NOT OLD.mapping_profile_id OR
                 NEW.reference_revision_id IS NOT OLD.reference_revision_id OR
                 NEW.alignment_job_id IS NOT OLD.alignment_job_id OR
                 NEW.alignment_session_id IS NOT OLD.alignment_session_id OR
                 NEW.parent_mapping_job_id IS NOT OLD.parent_mapping_job_id OR
                 NEW.request_fingerprint IS NOT OLD.request_fingerprint OR
                 NEW.created_at IS NOT OLD.created_at
            BEGIN SELECT RAISE(ABORT, 'ONT signal mapping request identity is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_signal_mapping_jobs_no_delete BEFORE DELETE ON ont_signal_mapping_jobs
            BEGIN SELECT RAISE(ABORT, 'ONT signal mapping jobs are retained evidence'); END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_signal_mapping_jobs_terminal_no_update BEFORE UPDATE ON ont_signal_mapping_jobs
            WHEN OLD.state IN ('ready','failed','cancelled') AND (
                 NEW.state IS NOT OLD.state OR NEW.reason_code IS NOT OLD.reason_code OR
                 NEW.resource_snapshot IS NOT OLD.resource_snapshot OR NEW.stage_receipts IS NOT OLD.stage_receipts OR
                 NEW.failure_code IS NOT OLD.failure_code OR NEW.failure_message IS NOT OLD.failure_message OR
                 NEW.cancel_requested_at IS NOT OLD.cancel_requested_at OR NEW.completed_at IS NOT OLD.completed_at)
            BEGIN SELECT RAISE(ABORT, 'ONT signal mapping terminal evidence is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_signal_mapping_jobs_receipts_append_only BEFORE UPDATE ON ont_signal_mapping_jobs
            WHEN OLD.state NOT IN ('ready','failed','cancelled') AND NEW.state NOT IN ('ready','failed','cancelled') AND
                 EXISTS (
                    SELECT 1 FROM json_tree(OLD.stage_receipts) AS old_item
                    WHERE NOT EXISTS (
                        SELECT 1 FROM json_tree(NEW.stage_receipts) AS new_item
                        WHERE new_item.fullkey = old_item.fullkey AND new_item.type = old_item.type
                          AND new_item.atom IS old_item.atom
                    )
                 )
            BEGIN SELECT RAISE(ABORT, 'ONT signal mapping stage receipts are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_signal_mapping_events_no_update BEFORE UPDATE ON ont_signal_mapping_events
            BEGIN SELECT RAISE(ABORT, 'ONT signal mapping events are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_signal_mapping_events_no_delete BEFORE DELETE ON ont_signal_mapping_events
            BEGIN SELECT RAISE(ABORT, 'ONT signal mapping events are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_signal_mapping_artifacts_no_update BEFORE UPDATE ON ont_signal_mapping_artifacts
            BEGIN SELECT RAISE(ABORT, 'ONT signal mapping artifacts are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_signal_mapping_artifacts_no_delete BEFORE DELETE ON ont_signal_mapping_artifacts
            BEGIN SELECT RAISE(ABORT, 'ONT signal mapping artifacts are retained'); END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_squigualiser_views_identity_no_update BEFORE UPDATE ON ont_squigualiser_view_jobs
            WHEN NEW.mapping_artifact_id IS NOT OLD.mapping_artifact_id OR NEW.mode IS NOT OLD.mode OR
                 NEW.read_id IS NOT OLD.read_id OR NEW.reference_contig IS NOT OLD.reference_contig OR
                 NEW.reference_start IS NOT OLD.reference_start OR NEW.reference_end IS NOT OLD.reference_end OR
                 NEW.render_params IS NOT OLD.render_params OR NEW.request_fingerprint IS NOT OLD.request_fingerprint OR
                 NEW.created_at IS NOT OLD.created_at
            BEGIN SELECT RAISE(ABORT, 'ONT Squigualiser view request identity is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_squigualiser_views_no_delete BEFORE DELETE ON ont_squigualiser_view_jobs
            BEGIN SELECT RAISE(ABORT, 'ONT Squigualiser view jobs are retained evidence'); END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_squigualiser_views_terminal_no_update BEFORE UPDATE ON ont_squigualiser_view_jobs
            WHEN OLD.state IN ('ready','failed','cancelled') AND (
                 NEW.state IS NOT OLD.state OR NEW.reason_code IS NOT OLD.reason_code OR
                 NEW.output_manifest IS NOT OLD.output_manifest OR NEW.render_receipt IS NOT OLD.render_receipt OR
                 NEW.failure_code IS NOT OLD.failure_code OR NEW.failure_message IS NOT OLD.failure_message OR
                 NEW.cancel_requested_at IS NOT OLD.cancel_requested_at OR NEW.completed_at IS NOT OLD.completed_at)
            BEGIN SELECT RAISE(ABORT, 'ONT Squigualiser view terminal output evidence is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS trg_ont_squigualiser_views_receipts_append_only BEFORE UPDATE ON ont_squigualiser_view_jobs
            WHEN OLD.state NOT IN ('ready','failed','cancelled') AND NEW.state NOT IN ('ready','failed','cancelled') AND
                 EXISTS (
                    SELECT 1 FROM json_tree(OLD.render_receipt) AS old_item
                    WHERE NOT EXISTS (
                        SELECT 1 FROM json_tree(NEW.render_receipt) AS new_item
                        WHERE new_item.fullkey = old_item.fullkey AND new_item.type = old_item.type
                          AND new_item.atom IS old_item.atom
                    )
                 )
            BEGIN SELECT RAISE(ABORT, 'ONT Squigualiser view stage receipts are append-only'); END;
            """
        )
        connection.commit()
    finally:
        connection.close()
