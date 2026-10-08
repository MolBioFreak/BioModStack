"""Add split readiness without rewriting migration 46 or adopting old bytes."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from paths import get_db_path

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ngs_alignment_derived_products (
    id VARCHAR(96) PRIMARY KEY NOT NULL,
    job_id VARCHAR(36) NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT,
    session_id VARCHAR(64) NOT NULL,
    mode VARCHAR(32) NOT NULL CHECK (mode IN ('primary','dimer_candidates')),
    product VARCHAR(16) NOT NULL CHECK (product IN ('catalog','preview')),
    source_authority_sha256 VARCHAR(64) NOT NULL,
    source_identity JSON NOT NULL,
    intent_sha256 VARCHAR(64) NOT NULL,
    request_contract JSON NOT NULL,
    request_sha256 VARCHAR(64),
    catalog_request_id VARCHAR(96) REFERENCES ngs_alignment_derived_products(id) ON DELETE RESTRICT,
    catalog_authority_sha256 VARCHAR(64),
    state VARCHAR(16) NOT NULL DEFAULT 'requested' CHECK (state IN ('requested','running','ready','failed')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    manual_retry_count INTEGER NOT NULL DEFAULT 0 CHECK (manual_retry_count >= 0),
    claim_token VARCHAR(96) UNIQUE,
    lease_expires_at DATETIME,
    authority_sha256 VARCHAR(64),
    manifest_sha256 VARCHAR(64),
    error_code VARCHAR(32) CHECK (error_code IS NULL OR error_code IN (
        'source_invalid','resource_limit','cancelled','infrastructure_failed','publication_failed','integrity_mismatch')),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((product='catalog' AND catalog_request_id IS NULL AND catalog_authority_sha256 IS NULL
            AND request_sha256 IS NOT NULL) OR (product='preview' AND catalog_request_id IS NOT NULL)),
    CHECK (product<>'preview' OR state NOT IN ('running','ready') OR
           (catalog_authority_sha256 IS NOT NULL AND request_sha256 IS NOT NULL)),
    CHECK ((state='running' AND claim_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR
           (state<>'running' AND claim_token IS NULL AND lease_expires_at IS NULL)),
    CHECK ((state='ready' AND authority_sha256 IS NOT NULL AND manifest_sha256 IS NOT NULL) OR
           (state<>'ready' AND authority_sha256 IS NULL AND manifest_sha256 IS NULL)),
    CHECK ((state='failed' AND error_code IS NOT NULL) OR (state<>'failed' AND error_code IS NULL)),
    UNIQUE(job_id, session_id, product, intent_sha256)
    {hash_checks}
)
""".format(hash_checks="".join(
    f", CHECK ({field} IS NULL OR (length({field})=64 AND {field} NOT GLOB '*[^0-9a-f]*'))"
    for field in ("source_authority_sha256", "intent_sha256", "request_sha256",
                  "catalog_authority_sha256", "authority_sha256", "manifest_sha256")
))


def migrate(db_path: str | Path | None = None) -> None:
    path = Path(db_path) if db_path is not None else get_db_path()
    connection = sqlite3.connect(str(path), timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute("PRAGMA foreign_keys").fetchone() != (1,):
            raise sqlite3.IntegrityError("SQLite foreign keys could not be enabled")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(_TABLE_SQL)
        connection.execute("CREATE INDEX IF NOT EXISTS ix_ngs_derived_claimable ON ngs_alignment_derived_products(state, product, created_at, id)")
        connection.execute("CREATE INDEX IF NOT EXISTS ix_ngs_derived_lease ON ngs_alignment_derived_products(state, lease_expires_at)")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise sqlite3.IntegrityError("NGS split-readiness foreign-key violations")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
