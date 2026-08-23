"""Add shared immutable scientific Parquet receipt and migration ledger tables."""
from __future__ import annotations

import sqlite3
from pathlib import Path


STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS scientific_artifact_receipts (
        artifact_id VARCHAR(128) PRIMARY KEY NOT NULL,
        owner_kind VARCHAR(96) NOT NULL,
        owner_id VARCHAR(255) NOT NULL,
        role VARCHAR(128) NOT NULL,
        schema_id VARCHAR(160) NOT NULL,
        artifact_schema_version INTEGER NOT NULL,
        content_sha256 VARCHAR(64) NOT NULL,
        size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
        row_count INTEGER NOT NULL CHECK (row_count >= 0),
        column_schema_sha256 VARCHAR(64) NOT NULL,
        storage_root VARCHAR(96) NOT NULL,
        relative_path VARCHAR(2000) NOT NULL,
        media_type VARCHAR(160) NOT NULL,
        availability VARCHAR(32) NOT NULL CHECK (availability IN ('staged','available','unavailable','integrity_failed')),
        source_receipts_json JSON NOT NULL,
        created_at DATETIME NOT NULL,
        CONSTRAINT uq_scientific_artifact_path UNIQUE(storage_root, relative_path),
        CONSTRAINT uq_scientific_artifact_content UNIQUE(owner_kind, owner_id, role, content_sha256)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scientific_payload_migrations (
        migration_id VARCHAR(160) PRIMARY KEY NOT NULL,
        source_store VARCHAR(96) NOT NULL,
        source_table VARCHAR(160) NOT NULL,
        source_column VARCHAR(160) NOT NULL,
        source_key VARCHAR(512) NOT NULL,
        source_sha256 VARCHAR(64) NOT NULL,
        artifact_id VARCHAR(128),
        artifact_sha256 VARCHAR(64),
        equivalence_sha256 VARCHAR(64),
        state VARCHAR(32) NOT NULL CHECK (state IN ('planned','running','completed','failed','quarantined')),
        diagnostic TEXT,
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        CONSTRAINT uq_scientific_payload_migration_source UNIQUE(source_store, source_table, source_column, source_key, source_sha256),
        FOREIGN KEY(artifact_id) REFERENCES scientific_artifact_receipts(artifact_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_scientific_artifact_owner ON scientific_artifact_receipts(owner_kind, owner_id, role)",
    "CREATE INDEX IF NOT EXISTS ix_scientific_artifact_hash ON scientific_artifact_receipts(content_sha256)",
    "CREATE INDEX IF NOT EXISTS ix_scientific_payload_migration_state ON scientific_payload_migrations(state, updated_at)",
)


def migrate(db_path: str | Path) -> None:
    connection = sqlite3.connect(str(db_path), timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        for statement in STATEMENTS:
            connection.execute(statement)
        connection.execute("PRAGMA foreign_key_check")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(f"scientific artifact migration foreign-key violations: {violations!r}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


__all__ = ["migrate"]
