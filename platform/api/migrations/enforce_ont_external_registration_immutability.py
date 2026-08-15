"""Make durable external ONT registration authority immutable."""
from __future__ import annotations

import sqlite3


def migrate(db_path: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS trg_ont_external_registration_identity_immutable
            BEFORE UPDATE OF external_registration_key, experiment_group, sample_id, last_minknow_payload
            ON ont_instrument_runs
            WHEN OLD.external_registration_key IS NOT NULL
            BEGIN
                SELECT RAISE(ABORT, 'external ONT registration identity is immutable');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_ont_external_registration_delete_restrict
            BEFORE DELETE ON ont_instrument_runs
            WHEN OLD.external_registration_key IS NOT NULL
            BEGIN
                SELECT RAISE(ABORT, 'external ONT registration authority cannot be deleted');
            END;
            """
        )
        connection.commit()
    finally:
        connection.close()
