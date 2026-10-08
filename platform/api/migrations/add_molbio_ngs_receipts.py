"""Durable one-time MolBio-to-NGS handoff receipts."""
from __future__ import annotations

import sqlite3


def migrate(db_path: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS molbio_ngs_receipts (
                id VARCHAR(36) PRIMARY KEY NOT NULL,
                sequence_id VARCHAR(36) NOT NULL,
                revision_id VARCHAR(36) NOT NULL,
                revision_sha256 VARCHAR(64) NOT NULL,
                reference_snapshot_path VARCHAR(1000) NOT NULL,
                reference_snapshot_sha256 VARCHAR(64) NOT NULL,
                expires_at VARCHAR NOT NULL,
                consumed_at VARCHAR,
                consumed_job_id VARCHAR(36) UNIQUE REFERENCES jobs(id),
                created_at VARCHAR NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS ix_molbio_ngs_receipts_sequence_id ON molbio_ngs_receipts(sequence_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS ix_molbio_ngs_receipts_revision_id ON molbio_ngs_receipts(revision_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS ix_molbio_ngs_receipts_expires_at ON molbio_ngs_receipts(expires_at)")
        connection.commit()
    finally:
        connection.close()
