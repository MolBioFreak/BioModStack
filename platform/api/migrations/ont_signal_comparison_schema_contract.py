"""Exact SQLite schema contract for immutable ONT ideal comparisons."""
from __future__ import annotations

COMPARISON_SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS ont_signal_comparison_jobs (
 id VARCHAR(96) PRIMARY KEY NOT NULL,
 viewer_session_id VARCHAR(96) NOT NULL REFERENCES ont_signal_viewer_sessions(id) ON DELETE RESTRICT,
 viewer_session_revision INTEGER NOT NULL CHECK(viewer_session_revision >= 1),
 run_id VARCHAR(80) NOT NULL REFERENCES ont_instrument_runs(id) ON DELETE RESTRICT,
 observed_generation INTEGER NOT NULL CHECK(observed_generation >= 1),
 raw_representation_id VARCHAR(96) NOT NULL REFERENCES ont_raw_signal_representations(id) ON DELETE RESTRICT,
 mapping_artifact_id VARCHAR(96) NOT NULL REFERENCES ont_signal_mapping_artifacts(id) ON DELETE RESTRICT,
 reference_revision_id VARCHAR(128) NOT NULL,
 selected_read_id VARCHAR(128) NOT NULL,
 reference_contig VARCHAR(255) NOT NULL,
 reference_start INTEGER NOT NULL CHECK(reference_start >= 1),
 reference_end INTEGER NOT NULL CHECK(reference_end >= reference_start AND reference_end-reference_start+1 <= 1000),
 simulation_orientation VARCHAR(16) NOT NULL CHECK(simulation_orientation IN ('forward','reverse')),
 simulation_settings JSON NOT NULL,
 sequence_basis VARCHAR(32) NOT NULL CHECK(sequence_basis='managed_reference'),
 generated_read_id VARCHAR(128),
 render_params JSON NOT NULL,
 preview_digest VARCHAR(64) NOT NULL,
 request_fingerprint VARCHAR(64) NOT NULL,
 attempt_number INTEGER NOT NULL CHECK(attempt_number BETWEEN 1 AND 3),
 predecessor_job_id VARCHAR(96) UNIQUE REFERENCES ont_signal_comparison_jobs(id) ON DELETE RESTRICT,
 state VARCHAR(32) NOT NULL CHECK(state IN ('requested','running','ready','failed','cancelled')),
 reason_code VARCHAR(96) NOT NULL,
 claim_token VARCHAR(96) UNIQUE,
 lease_expires_at VARCHAR,
 cancel_requested_at VARCHAR,
 resource_snapshot JSON NOT NULL,
 stage_receipts JSON NOT NULL,
 output_manifest JSON NOT NULL,
 failure_code VARCHAR(96),
 failure_message TEXT CHECK(failure_message IS NULL OR length(failure_message)<=4000),
 created_at VARCHAR NOT NULL,
 updated_at VARCHAR NOT NULL,
 completed_at VARCHAR,
 CONSTRAINT uq_ont_signal_comparison_attempt UNIQUE(request_fingerprint, attempt_number)
);
CREATE INDEX IF NOT EXISTS ix_ont_signal_comparison_state ON ont_signal_comparison_jobs(state, lease_expires_at);
CREATE INDEX IF NOT EXISTS ix_ont_signal_comparison_viewer ON ont_signal_comparison_jobs(viewer_session_id, viewer_session_revision);
CREATE TABLE IF NOT EXISTS ont_signal_comparison_events (
 id VARCHAR(96) PRIMARY KEY NOT NULL,
 comparison_job_id VARCHAR(96) NOT NULL REFERENCES ont_signal_comparison_jobs(id) ON DELETE RESTRICT,
 state VARCHAR(32) NOT NULL,
 reason_code VARCHAR(96) NOT NULL,
 receipt JSON NOT NULL,
 created_at VARCHAR NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ont_signal_comparison_events_job ON ont_signal_comparison_events(comparison_job_id, created_at);
CREATE TABLE IF NOT EXISTS ont_signal_comparison_artifacts (
 id VARCHAR(96) PRIMARY KEY NOT NULL,
 comparison_job_id VARCHAR(96) NOT NULL REFERENCES ont_signal_comparison_jobs(id) ON DELETE RESTRICT,
 kind VARCHAR(64) NOT NULL,
 authority_class VARCHAR(32) NOT NULL CHECK(authority_class IN ('simulated_derived','comparison_derived')),
 managed_relative_path TEXT NOT NULL UNIQUE,
 media_type VARCHAR(255) NOT NULL,
 sha256 VARCHAR(64) NOT NULL,
 size_bytes INTEGER NOT NULL CHECK(size_bytes > 0),
 parent_identities JSON NOT NULL,
 squigulator_runtime_identity JSON,
 squigualiser_runtime_identity JSON,
 validation_receipt JSON NOT NULL,
 created_at VARCHAR NOT NULL,
 CONSTRAINT uq_ont_signal_comparison_artifact_kind UNIQUE(comparison_job_id, kind)
);
CREATE TABLE IF NOT EXISTS ont_signal_manual_reviews (
 id VARCHAR(96) PRIMARY KEY NOT NULL,
 comparison_job_id VARCHAR(96) NOT NULL REFERENCES ont_signal_comparison_jobs(id) ON DELETE RESTRICT,
 predecessor_review_id VARCHAR(96) UNIQUE REFERENCES ont_signal_manual_reviews(id) ON DELETE RESTRICT,
 review_question TEXT NOT NULL CHECK(length(review_question) BETWEEN 1 AND 1000),
 required_outcome VARCHAR(16) NOT NULL CHECK(required_outcome IN ('approve','reject','record_only')),
 note TEXT NOT NULL CHECK(length(note) BETWEEN 1 AND 4000),
 reviewed_start INTEGER NOT NULL CHECK(reviewed_start >= 1),
 reviewed_end INTEGER NOT NULL CHECK(reviewed_end >= reviewed_start),
 comparison_html_artifact_id VARCHAR(96) NOT NULL REFERENCES ont_signal_comparison_artifacts(id) ON DELETE RESTRICT,
 comparison_html_sha256 VARCHAR(64) NOT NULL,
 comparison_request_fingerprint VARCHAR(64) NOT NULL,
 reviewer_identity VARCHAR(255) NOT NULL,
 created_at VARCHAR NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ont_signal_manual_reviews_job ON ont_signal_manual_reviews(comparison_job_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ont_signal_manual_review_root ON ont_signal_manual_reviews(comparison_job_id) WHERE predecessor_review_id IS NULL;
CREATE TRIGGER IF NOT EXISTS trg_ont_signal_comparison_job_no_delete BEFORE DELETE ON ont_signal_comparison_jobs BEGIN SELECT RAISE(ABORT,'ONT signal comparison jobs are retained evidence'); END;
CREATE TRIGGER IF NOT EXISTS trg_ont_signal_comparison_job_identity_no_update BEFORE UPDATE ON ont_signal_comparison_jobs WHEN
 NEW.viewer_session_id IS NOT OLD.viewer_session_id OR NEW.viewer_session_revision IS NOT OLD.viewer_session_revision OR
 NEW.run_id IS NOT OLD.run_id OR NEW.observed_generation IS NOT OLD.observed_generation OR NEW.raw_representation_id IS NOT OLD.raw_representation_id OR
 NEW.mapping_artifact_id IS NOT OLD.mapping_artifact_id OR NEW.reference_revision_id IS NOT OLD.reference_revision_id OR NEW.selected_read_id IS NOT OLD.selected_read_id OR
 NEW.reference_contig IS NOT OLD.reference_contig OR NEW.reference_start IS NOT OLD.reference_start OR NEW.reference_end IS NOT OLD.reference_end OR
 NEW.simulation_orientation IS NOT OLD.simulation_orientation OR NEW.simulation_settings IS NOT OLD.simulation_settings OR NEW.sequence_basis IS NOT OLD.sequence_basis OR
 NEW.render_params IS NOT OLD.render_params OR NEW.preview_digest IS NOT OLD.preview_digest OR NEW.request_fingerprint IS NOT OLD.request_fingerprint OR
 NEW.attempt_number IS NOT OLD.attempt_number OR NEW.predecessor_job_id IS NOT OLD.predecessor_job_id OR NEW.created_at IS NOT OLD.created_at
 BEGIN SELECT RAISE(ABORT,'ONT signal comparison request identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_ont_signal_comparison_job_terminal_no_update BEFORE UPDATE ON ont_signal_comparison_jobs WHEN OLD.state IN ('ready','failed','cancelled') BEGIN SELECT RAISE(ABORT,'ONT signal comparison terminal evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_ont_signal_comparison_events_no_update BEFORE UPDATE ON ont_signal_comparison_events BEGIN SELECT RAISE(ABORT,'ONT signal comparison events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_ont_signal_comparison_events_no_delete BEFORE DELETE ON ont_signal_comparison_events BEGIN SELECT RAISE(ABORT,'ONT signal comparison events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_ont_signal_comparison_artifact_no_update BEFORE UPDATE ON ont_signal_comparison_artifacts BEGIN SELECT RAISE(ABORT,'ONT signal comparison artifacts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_ont_signal_comparison_artifact_no_delete BEFORE DELETE ON ont_signal_comparison_artifacts BEGIN SELECT RAISE(ABORT,'ONT signal comparison artifacts are retained evidence'); END;
CREATE TRIGGER IF NOT EXISTS trg_ont_signal_manual_reviews_no_update BEFORE UPDATE ON ont_signal_manual_reviews BEGIN SELECT RAISE(ABORT,'ONT signal manual reviews are immutable revisions'); END;
CREATE TRIGGER IF NOT EXISTS trg_ont_signal_manual_reviews_no_delete BEFORE DELETE ON ont_signal_manual_reviews BEGIN SELECT RAISE(ABORT,'ONT signal manual reviews are retained evidence'); END;
"""
