"""Immutable manifest-backed FrustraMPNN result persistence."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def migrate(db_path: str | Path) -> None:
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        design_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(designs)").fetchall()
        }
        required_design_columns = {
            "frustrampnn_contract_version": "VARCHAR(32)",
            "frustrampnn_status": "VARCHAR(32)",
            "frustrampnn_source_sha256": "VARCHAR(64)",
            "frustrampnn_manifest_relpath": "VARCHAR(1000)",
            "frustrampnn_landscape_relpath": "VARCHAR(1000)",
            "frustrampnn_summary_relpath": "VARCHAR(1000)",
            "frustrampnn_runtime_sha256": "VARCHAR(64)",
            "frustrampnn_failure_class": "VARCHAR(64)",
            "frustrampnn_failure_detail": "VARCHAR(1000)",
        }
        for name, sql_type in required_design_columns.items():
            if name not in design_columns:
                connection.execute(f"ALTER TABLE designs ADD COLUMN {name} {sql_type}")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS frustrampnn_results (
                parent_job_id VARCHAR(36) NOT NULL REFERENCES jobs(id),
                invocation_id VARCHAR(128) NOT NULL,
                parent_workflow_id VARCHAR(128) NOT NULL,
                candidate_id VARCHAR(128) NOT NULL,
                design_id VARCHAR(36) REFERENCES designs(id),
                requiredness VARCHAR(16) NOT NULL,
                request_sha256 VARCHAR(64) NOT NULL,
                source_artifact_id VARCHAR(128),
                source_artifact_sha256 VARCHAR(64) NOT NULL,
                manifest_sha256 VARCHAR(64) NOT NULL,
                manifest_json JSON NOT NULL,
                summary_sha256 VARCHAR(64) NOT NULL,
                summary_json JSON NOT NULL,
                runtime_identity_json JSON NOT NULL,
                assigned_gpu_json JSON NOT NULL,
                terminal_result_json JSON NOT NULL,
                parent_metadata_json JSON,
                created_at DATETIME NOT NULL,
                PRIMARY KEY (parent_job_id, invocation_id),
                CONSTRAINT uq_frustrampnn_results_job_invocation
                    UNIQUE (parent_job_id, invocation_id)
            );

            CREATE INDEX IF NOT EXISTS ix_frustrampnn_results_parent_job_id
                ON frustrampnn_results(parent_job_id);
            CREATE INDEX IF NOT EXISTS ix_frustrampnn_results_candidate_id
                ON frustrampnn_results(candidate_id);
            CREATE INDEX IF NOT EXISTS ix_frustrampnn_results_design_id
                ON frustrampnn_results(design_id);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_frustrampnn_results_job_invocation
                ON frustrampnn_results(parent_job_id, invocation_id);
            CREATE INDEX IF NOT EXISTS ix_frustrampnn_results_job_candidate
                ON frustrampnn_results(parent_job_id, candidate_id);

            CREATE TABLE IF NOT EXISTS frustrampnn_artifacts (
                artifact_id VARCHAR(96) PRIMARY KEY NOT NULL,
                parent_job_id VARCHAR(36) NOT NULL,
                invocation_id VARCHAR(128) NOT NULL,
                role VARCHAR(64) NOT NULL,
                relative_path VARCHAR(1000) NOT NULL,
                storage_path VARCHAR(2000) NOT NULL,
                content_sha256 VARCHAR(64) NOT NULL,
                size_bytes INTEGER NOT NULL,
                media_type VARCHAR(128) NOT NULL,
                metadata_json JSON NOT NULL,
                created_at DATETIME NOT NULL,
                CONSTRAINT fk_frustrampnn_artifacts_result
                    FOREIGN KEY (parent_job_id, invocation_id)
                    REFERENCES frustrampnn_results(parent_job_id, invocation_id),
                CONSTRAINT uq_frustrampnn_artifacts_invocation_path
                    UNIQUE (parent_job_id, invocation_id, relative_path)
            );

            CREATE INDEX IF NOT EXISTS ix_frustrampnn_artifacts_parent_job_id
                ON frustrampnn_artifacts(parent_job_id);
            CREATE INDEX IF NOT EXISTS ix_frustrampnn_artifacts_invocation_id
                ON frustrampnn_artifacts(invocation_id);
            CREATE INDEX IF NOT EXISTS ix_frustrampnn_artifacts_role
                ON frustrampnn_artifacts(role);
            CREATE INDEX IF NOT EXISTS ix_frustrampnn_artifacts_content_sha256
                ON frustrampnn_artifacts(content_sha256);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_frustrampnn_artifacts_invocation_path
                ON frustrampnn_artifacts(parent_job_id, invocation_id, relative_path);

            CREATE TABLE IF NOT EXISTS frustrampnn_landscape_rows (
                id VARCHAR(96) PRIMARY KEY NOT NULL,
                parent_job_id VARCHAR(36) NOT NULL,
                invocation_id VARCHAR(128) NOT NULL,
                target_id VARCHAR(128) NOT NULL,
                entity_instance_id VARCHAR(128) NOT NULL,
                auth_asym_id VARCHAR(128) NOT NULL,
                auth_seq_id VARCHAR(64) NOT NULL,
                insertion_code VARCHAR(16) NOT NULL DEFAULT '',
                sequence_index INTEGER NOT NULL,
                wt VARCHAR(1) NOT NULL,
                mutation_aa VARCHAR(1) NOT NULL,
                score FLOAT,
                score_class VARCHAR(32) NOT NULL,
                scoreable BOOLEAN NOT NULL,
                status VARCHAR(32) NOT NULL,
                reason TEXT,
                row_json JSON NOT NULL,
                provenance_json JSON NOT NULL,
                CONSTRAINT fk_frustrampnn_landscape_result
                    FOREIGN KEY (parent_job_id, invocation_id)
                    REFERENCES frustrampnn_results(parent_job_id, invocation_id),
                CONSTRAINT uq_frustrampnn_landscape_slot UNIQUE (
                    parent_job_id, invocation_id, target_id, entity_instance_id, auth_asym_id,
                    auth_seq_id, insertion_code, sequence_index, wt, mutation_aa
                )
            );

            CREATE INDEX IF NOT EXISTS ix_frustrampnn_landscape_rows_parent_job_id
                ON frustrampnn_landscape_rows(parent_job_id);
            CREATE INDEX IF NOT EXISTS ix_frustrampnn_landscape_rows_invocation_id
                ON frustrampnn_landscape_rows(invocation_id);
            CREATE INDEX IF NOT EXISTS ix_frustrampnn_landscape_rows_target_id
                ON frustrampnn_landscape_rows(target_id);
            CREATE INDEX IF NOT EXISTS ix_frustrampnn_landscape_rows_entity_instance_id
                ON frustrampnn_landscape_rows(entity_instance_id);
            CREATE INDEX IF NOT EXISTS ix_frustrampnn_landscape_rows_status
                ON frustrampnn_landscape_rows(status);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_frustrampnn_landscape_slot
                ON frustrampnn_landscape_rows(
                    parent_job_id, invocation_id, target_id, entity_instance_id, auth_asym_id,
                    auth_seq_id, insertion_code, sequence_index, wt, mutation_aa
                );
            CREATE INDEX IF NOT EXISTS ix_frustrampnn_landscape_page_order
                ON frustrampnn_landscape_rows(
                    parent_job_id, invocation_id, target_id, entity_instance_id, auth_asym_id,
                    auth_seq_id, insertion_code, sequence_index, mutation_aa, id
                );
            """
        )
        result_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(frustrampnn_results)"
            ).fetchall()
        }
        if "parent_metadata_json" not in result_columns:
            connection.execute(
                "ALTER TABLE frustrampnn_results ADD COLUMN parent_metadata_json JSON"
            )
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(
                f"FrustraMPNN persistence migration foreign-key violations: {violations!r}"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


__all__ = ["migrate"]
