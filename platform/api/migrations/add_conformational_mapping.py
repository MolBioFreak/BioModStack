"""Additive canonical conformational-mapping persistence tables."""

from __future__ import annotations

import sqlite3

from paths import get_db_path


STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS conformational_mapping_requests (
      request_id VARCHAR(36) PRIMARY KEY, job_id VARCHAR(36) NOT NULL UNIQUE,
      principal_id VARCHAR(255) NOT NULL, backend VARCHAR(64) NOT NULL,
      status VARCHAR(32) NOT NULL, request_sha256 VARCHAR(64) NOT NULL UNIQUE,
      coordinate_plan_sha256 VARCHAR(64) NOT NULL, resume_key VARCHAR(64) NOT NULL,
      result_contract_id VARCHAR(64) NOT NULL, request_json JSON NOT NULL,
      coordinate_plan_json JSON NOT NULL, progress_json JSON NOT NULL,
      failure_receipt_json JSON, retry_of_request_id VARCHAR(36),
      created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, terminal_at DATETIME,
      FOREIGN KEY(job_id) REFERENCES jobs(id))""",
    """CREATE TABLE IF NOT EXISTS conformational_mapping_sources (
      source_id VARCHAR(80) PRIMARY KEY, principal_id VARCHAR(255) NOT NULL,
      source_kind VARCHAR(64) NOT NULL, storage_root VARCHAR(2000) NOT NULL,
      relative_path VARCHAR(1000) NOT NULL, content_sha256 VARCHAR(64) NOT NULL,
      size_bytes INTEGER NOT NULL, metadata_json JSON NOT NULL, immutable BOOLEAN NOT NULL,
      created_at DATETIME NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS conformational_mapping_records (
      id VARCHAR(36) PRIMARY KEY, request_id VARCHAR(36) NOT NULL,
      record_type VARCHAR(64) NOT NULL, record_key VARCHAR(255) NOT NULL,
      content_sha256 VARCHAR(64) NOT NULL, payload_json JSON NOT NULL,
      created_at DATETIME NOT NULL,
      CONSTRAINT uq_cm_record_identity UNIQUE(request_id,record_type,record_key),
      FOREIGN KEY(request_id) REFERENCES conformational_mapping_requests(request_id))""",
    """CREATE TABLE IF NOT EXISTS conformational_mapping_artifacts (
      artifact_id VARCHAR(96) PRIMARY KEY, request_id VARCHAR(36) NOT NULL,
      candidate_id VARCHAR(128), role VARCHAR(64) NOT NULL,
      relative_path VARCHAR(1000) NOT NULL, storage_path VARCHAR(2000) NOT NULL,
      content_sha256 VARCHAR(64) NOT NULL, size_bytes INTEGER NOT NULL,
      media_type VARCHAR(128) NOT NULL, metadata_json JSON NOT NULL,
      created_at DATETIME NOT NULL, CONSTRAINT uq_cm_artifact_path UNIQUE(request_id,relative_path),
      FOREIGN KEY(request_id) REFERENCES conformational_mapping_requests(request_id))""",
    """CREATE TABLE IF NOT EXISTS conformational_mapping_landscape_rows (
      id VARCHAR(36) PRIMARY KEY, request_id VARCHAR(36) NOT NULL,
      candidate_id VARCHAR(128) NOT NULL, entity_instance_id VARCHAR(128) NOT NULL,
      auth_asym_id VARCHAR(128) NOT NULL, auth_seq_id VARCHAR(64) NOT NULL,
      insertion_code VARCHAR(16) NOT NULL, sequence_index INTEGER NOT NULL,
      wt VARCHAR(1) NOT NULL, mutation_aa VARCHAR(1) NOT NULL, score FLOAT,
      score_class VARCHAR(32), scoreable BOOLEAN NOT NULL, status VARCHAR(32) NOT NULL,
      reason TEXT, provenance_json JSON NOT NULL,
      CONSTRAINT uq_cm_landscape_slot UNIQUE(request_id,candidate_id,entity_instance_id,auth_asym_id,auth_seq_id,insertion_code,sequence_index,mutation_aa),
      FOREIGN KEY(request_id) REFERENCES conformational_mapping_requests(request_id))""",
)


def migrate() -> None:
    connection = sqlite3.connect(str(get_db_path()), timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        for statement in STATEMENTS:
            connection.execute(statement)
        for table, columns in {
            "conformational_mapping_requests": ("principal_id", "backend", "status", "resume_key"),
            "conformational_mapping_sources": ("principal_id", "source_kind", "content_sha256"),
            "conformational_mapping_records": ("request_id", "record_type", "content_sha256"),
            "conformational_mapping_artifacts": ("request_id", "candidate_id", "content_sha256"),
            "conformational_mapping_landscape_rows": ("request_id", "candidate_id", "status"),
        }.items():
            for column in columns:
                connection.execute(
                    f"CREATE INDEX IF NOT EXISTS ix_{table}_{column} ON {table} ({column})"
                )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
