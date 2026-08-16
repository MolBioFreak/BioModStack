"""Persist expiring server-owned Mk1D protocol option receipts and armed intents."""
from __future__ import annotations

import sqlite3


def migrate(db_path: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ont_protocol_option_receipts (
                id VARCHAR(80) PRIMARY KEY NOT NULL,
                option_id VARCHAR(80) NOT NULL UNIQUE,
                position_id VARCHAR(255) NOT NULL,
                flow_cell_identity_sha256 VARCHAR(64) NOT NULL,
                source_digest VARCHAR(64) NOT NULL,
                capability_digest VARCHAR(64) NOT NULL,
                source_snapshot JSON NOT NULL,
                expires_at VARCHAR NOT NULL,
                consumed_at VARCHAR,
                created_at VARCHAR NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ont_instrument_run_preflights (
                id VARCHAR(80) PRIMARY KEY NOT NULL,
                run_id VARCHAR(80) NOT NULL UNIQUE REFERENCES ont_instrument_runs(id) ON DELETE RESTRICT,
                option_receipt_id VARCHAR(80) NOT NULL UNIQUE REFERENCES ont_protocol_option_receipts(id) ON DELETE RESTRICT,
                selected_option_id VARCHAR(80) NOT NULL,
                flow_cell_identity_sha256 VARCHAR(64) NOT NULL,
                source_digest VARCHAR(64) NOT NULL,
                capability_digest VARCHAR(64) NOT NULL,
                source_snapshot JSON NOT NULL,
                expires_at VARCHAR NOT NULL,
                invalidated_at VARCHAR,
                invalidation_reason VARCHAR(255),
                created_at VARCHAR NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_ont_protocol_option_receipts_position_expires "
            "ON ont_protocol_option_receipts(position_id, expires_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_ont_protocol_option_receipts_expires_at "
            "ON ont_protocol_option_receipts(expires_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_ont_instrument_run_preflights_run_id "
            "ON ont_instrument_run_preflights(run_id)"
        )
        connection.commit()
    finally:
        connection.close()
