"""Persist BMS-owned Mk1D/MinKNOW run observations and immutable event history."""
from __future__ import annotations

import sqlite3


def migrate(db_path: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ont_instrument_runs (
                id VARCHAR(80) PRIMARY KEY NOT NULL,
                position_id VARCHAR(255) NOT NULL,
                minknow_run_id VARCHAR(255) UNIQUE,
                state VARCHAR(32) NOT NULL,
                observed_at VARCHAR NOT NULL,
                observed_generation INTEGER NOT NULL,
                sample_id VARCHAR(255),
                experiment_group VARCHAR(255),
                kit VARCHAR(255),
                output_directories JSON NOT NULL,
                output_files JSON NOT NULL,
                handoff_ready BOOLEAN NOT NULL DEFAULT 0,
                last_minknow_payload JSON,
                created_at VARCHAR NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ont_instrument_run_events (
                id VARCHAR(96) PRIMARY KEY NOT NULL,
                run_id VARCHAR(80) NOT NULL REFERENCES ont_instrument_runs(id) ON DELETE RESTRICT,
                event_type VARCHAR(32) NOT NULL,
                state VARCHAR(32) NOT NULL,
                observed_at VARCHAR NOT NULL,
                observed_generation INTEGER NOT NULL,
                minknow_payload JSON,
                output_files JSON NOT NULL,
                CONSTRAINT uq_ont_run_event_generation UNIQUE (run_id, observed_generation)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS ix_ont_instrument_runs_position_id ON ont_instrument_runs(position_id)")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_ont_instrument_runs_minknow_run_id ON ont_instrument_runs(minknow_run_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS ix_ont_instrument_run_events_run_id ON ont_instrument_run_events(run_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS ix_ont_run_events_run_generation ON ont_instrument_run_events(run_id, observed_generation)")
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_ont_instrument_run_events_no_update
            BEFORE UPDATE ON ont_instrument_run_events
            BEGIN
                SELECT RAISE(ABORT, 'ONT instrument-run events are append-only');
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_ont_instrument_run_events_no_delete
            BEFORE DELETE ON ont_instrument_run_events
            BEGIN
                SELECT RAISE(ABORT, 'ONT instrument-run events are append-only');
            END
            """
        )
        connection.commit()
    finally:
        connection.close()