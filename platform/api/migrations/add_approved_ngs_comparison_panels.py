"""Server-owned immutable approved NGS comparison-panel records."""
from __future__ import annotations

import sqlite3


def migrate(db_path: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS approved_ngs_comparison_panels (
                id VARCHAR(36) PRIMARY KEY NOT NULL,
                version INTEGER NOT NULL,
                status VARCHAR(16) NOT NULL,
                label VARCHAR(255) NOT NULL,
                manifest_path VARCHAR(1000) NOT NULL,
                snapshot_sha256 VARCHAR(64) NOT NULL,
                provenance JSON NOT NULL,
                created_at VARCHAR NOT NULL,
                created_by VARCHAR(255) NOT NULL,
                UNIQUE(id, version)
            );
            CREATE TABLE IF NOT EXISTS ngs_comparison_panel_receipts (
                id VARCHAR(36) PRIMARY KEY NOT NULL,
                panel_id VARCHAR(36) NOT NULL REFERENCES approved_ngs_comparison_panels(id),
                panel_version INTEGER NOT NULL,
                panel_snapshot_path VARCHAR(1000) NOT NULL,
                panel_snapshot_sha256 VARCHAR(64) NOT NULL,
                expected_receipt_id VARCHAR(36) NOT NULL REFERENCES molbio_ngs_receipts(id),
                expires_at VARCHAR NOT NULL,
                consumed_at VARCHAR,
                consumed_job_id VARCHAR(36) UNIQUE REFERENCES jobs(id),
                created_at VARCHAR NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_ngs_comparison_panel_receipts_panel_id ON ngs_comparison_panel_receipts(panel_id);
            CREATE INDEX IF NOT EXISTS ix_ngs_comparison_panel_receipts_expires_at ON ngs_comparison_panel_receipts(expires_at);
            """
        )
        connection.commit()
    finally:
        connection.close()
