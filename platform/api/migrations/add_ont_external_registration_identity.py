"""Bind external ONT registrations to one database-enforced identity."""
from __future__ import annotations

import sqlite3


def migrate(db_path: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(ont_instrument_runs)")}
        if "external_registration_key" not in columns:
            connection.execute(
                "ALTER TABLE ont_instrument_runs ADD COLUMN external_registration_key VARCHAR(64)"
            )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_ont_run_external_registration_key "
            "ON ont_instrument_runs(external_registration_key)"
        )
        connection.commit()
    finally:
        connection.close()
