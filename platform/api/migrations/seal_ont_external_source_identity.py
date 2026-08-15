"""Seal external ONT source inode and run-generation authority."""
from __future__ import annotations

import sqlite3


_IDENTITY_COLUMNS = (
    ("external_source_device", "INTEGER"),
    ("external_source_inode", "INTEGER"),
    ("external_source_bytes", "INTEGER"),
    ("external_source_mtime_ns", "INTEGER"),
    ("external_source_ctime_ns", "INTEGER"),
    ("external_source_root_device", "INTEGER"),
    ("external_source_root_inode", "INTEGER"),
    ("external_source_relative_path", "TEXT"),
)


def migrate(db_path: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        existing = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(ont_instrument_runs)")
        }
        for column, sql_type in _IDENTITY_COLUMNS:
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE ont_instrument_runs ADD COLUMN {column} {sql_type}"
                )
        connection.executescript(
            """
            DROP TRIGGER IF EXISTS trg_ont_external_registration_identity_immutable;
            DROP TRIGGER IF EXISTS trg_ont_external_registration_delete_restrict;
            DROP TRIGGER IF EXISTS trg_ont_external_source_identity_immutable;

            CREATE TRIGGER trg_ont_external_registration_identity_immutable
            BEFORE UPDATE OF external_registration_key, experiment_group, sample_id,
                             last_minknow_payload, observed_generation
            ON ont_instrument_runs
            WHEN OLD.external_registration_key IS NOT NULL OR OLD.external_source_device IS NOT NULL
            BEGIN
                SELECT RAISE(ABORT, 'external ONT registration identity is immutable');
            END;

            CREATE TRIGGER trg_ont_external_registration_delete_restrict
            BEFORE DELETE ON ont_instrument_runs
            WHEN OLD.external_registration_key IS NOT NULL OR OLD.external_source_device IS NOT NULL
            BEGIN
                SELECT RAISE(ABORT, 'external ONT registration is immutable');
            END;

            CREATE TRIGGER trg_ont_external_source_identity_immutable
            BEFORE UPDATE OF external_source_device, external_source_inode,
                             external_source_bytes, external_source_mtime_ns,
                             external_source_ctime_ns, external_source_root_device,
                             external_source_root_inode, external_source_relative_path
            ON ont_instrument_runs
            WHEN (
                (OLD.external_source_device IS NOT NULL AND NEW.external_source_device IS NOT OLD.external_source_device) OR
                (OLD.external_source_inode IS NOT NULL AND NEW.external_source_inode IS NOT OLD.external_source_inode) OR
                (OLD.external_source_bytes IS NOT NULL AND NEW.external_source_bytes IS NOT OLD.external_source_bytes) OR
                (OLD.external_source_mtime_ns IS NOT NULL AND NEW.external_source_mtime_ns IS NOT OLD.external_source_mtime_ns) OR
                (OLD.external_source_ctime_ns IS NOT NULL AND NEW.external_source_ctime_ns IS NOT OLD.external_source_ctime_ns) OR
                (OLD.external_source_root_device IS NOT NULL AND NEW.external_source_root_device IS NOT OLD.external_source_root_device) OR
                (OLD.external_source_root_inode IS NOT NULL AND NEW.external_source_root_inode IS NOT OLD.external_source_root_inode) OR
                (OLD.external_source_relative_path IS NOT NULL AND NEW.external_source_relative_path IS NOT OLD.external_source_relative_path) OR
                NEW.external_source_device IS NULL OR
                NEW.external_source_inode IS NULL OR
                NEW.external_source_bytes IS NULL OR
                NEW.external_source_mtime_ns IS NULL OR
                NEW.external_source_ctime_ns IS NULL OR
                NEW.external_source_root_device IS NULL OR
                NEW.external_source_root_inode IS NULL OR
                NEW.external_source_relative_path IS NULL
            )
            BEGIN
                SELECT RAISE(ABORT, 'external ONT source identity is immutable');
            END;
            """
        )
        connection.commit()
    finally:
        connection.close()
