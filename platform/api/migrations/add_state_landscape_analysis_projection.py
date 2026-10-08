"""Add bounded normalized read-model tables for canonical state analyses."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from paths import get_db_path


_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS conformational_mapping_state_landscape_analysis_headers (
        request_id VARCHAR(36) NOT NULL,
        analysis_id VARCHAR(80) NOT NULL,
        content_sha256 VARCHAR(64) NOT NULL,
        source_ensemble_sha256 VARCHAR(64) NOT NULL,
        source_landscape_sha256 VARCHAR(64) NOT NULL,
        source_structure_map_sha256 VARCHAR(64) NOT NULL,
        comparison_sha256 VARCHAR(64) NOT NULL,
        formula_version VARCHAR(80) NOT NULL,
        formula_sha256 VARCHAR(64) NOT NULL,
        policy_sha256 VARCHAR(64) NOT NULL,
        comparison_mode VARCHAR(32) NOT NULL,
        comparison_target_id VARCHAR(128) NOT NULL,
        comparison_scope VARCHAR(64) NOT NULL,
        reference_backend_coordinates_json JSON,
        reference_candidate_id VARCHAR(128),
        pair_count INTEGER NOT NULL,
        row_count INTEGER NOT NULL,
        exclusion_count INTEGER NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (request_id, analysis_id),
        FOREIGN KEY(request_id) REFERENCES conformational_mapping_requests(request_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conformational_mapping_state_landscape_analysis_pairs (
        request_id VARCHAR(36) NOT NULL,
        analysis_id VARCHAR(80) NOT NULL,
        pair_id VARCHAR(300) NOT NULL,
        candidate_a_id VARCHAR(128) NOT NULL,
        candidate_b_id VARCHAR(128) NOT NULL,
        PRIMARY KEY (request_id, analysis_id, pair_id),
        FOREIGN KEY(request_id, analysis_id)
          REFERENCES conformational_mapping_state_landscape_analysis_headers(request_id, analysis_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conformational_mapping_state_landscape_analysis_rows (
        id VARCHAR(96) PRIMARY KEY NOT NULL,
        request_id VARCHAR(36) NOT NULL,
        analysis_id VARCHAR(80) NOT NULL,
        pair_id VARCHAR(300) NOT NULL,
        candidate_a_id VARCHAR(128) NOT NULL,
        candidate_b_id VARCHAR(128) NOT NULL,
        target_id VARCHAR(128) NOT NULL,
        entity_instance_id VARCHAR(128) NOT NULL,
        auth_asym_id VARCHAR(128) NOT NULL,
        auth_seq_id INTEGER NOT NULL,
        insertion_code VARCHAR(16) NOT NULL,
        sequence_index INTEGER NOT NULL,
        validated_wt VARCHAR(1) NOT NULL,
        metrics_json JSON NOT NULL,
        availability_json JSON NOT NULL,
        CONSTRAINT uq_cm_state_analysis_row_identity UNIQUE(
          request_id, analysis_id, pair_id, entity_instance_id, auth_asym_id,
          auth_seq_id, insertion_code, sequence_index, validated_wt
        ),
        FOREIGN KEY(request_id, analysis_id)
          REFERENCES conformational_mapping_state_landscape_analysis_headers(request_id, analysis_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_cm_state_analysis_header_content ON conformational_mapping_state_landscape_analysis_headers (content_sha256)",
    "CREATE INDEX IF NOT EXISTS ix_cm_state_analysis_rows_request ON conformational_mapping_state_landscape_analysis_rows (request_id)",
    "CREATE INDEX IF NOT EXISTS ix_cm_state_analysis_rows_analysis ON conformational_mapping_state_landscape_analysis_rows (analysis_id)",
    "CREATE INDEX IF NOT EXISTS ix_cm_state_analysis_rows_pair ON conformational_mapping_state_landscape_analysis_rows (pair_id)",
)


def migrate(db_path: str | None = None) -> None:
    """Create additive projection tables without modifying canonical authority."""

    database = Path(db_path) if db_path is not None else Path(get_db_path())
    connection = sqlite3.connect(database, timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        for statement in _STATEMENTS:
            connection.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()