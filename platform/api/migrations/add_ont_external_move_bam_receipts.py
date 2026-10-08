"""Add immutable external move-BAM registration receipts."""
from __future__ import annotations

import hashlib
import sqlite3


_RECEIPT_TABLE = "ont_external_move_bam_registration_receipts"

MIGRATION_33_TRIGGER_SQL = {
    "trg_ont_external_move_bam_receipt_no_update": """
        CREATE TRIGGER IF NOT EXISTS trg_ont_external_move_bam_receipt_no_update
            BEFORE UPDATE ON ont_external_move_bam_registration_receipts
            BEGIN SELECT RAISE(ABORT, 'ONT external move-BAM registration receipts are immutable'); END
    """,
    "trg_ont_external_move_bam_receipt_no_delete": """
        CREATE TRIGGER IF NOT EXISTS trg_ont_external_move_bam_receipt_no_delete
            BEFORE DELETE ON ont_external_move_bam_registration_receipts
            BEGIN SELECT RAISE(ABORT, 'ONT external move-BAM registration receipts are retained authority'); END
    """,
    "trg_ont_move_source_exact_producer_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_ont_move_source_exact_producer_insert
            BEFORE INSERT ON ont_move_table_sources
            WHEN (NEW.source_job_id IS NULL) = (NEW.external_registration_receipt_id IS NULL)
            BEGIN SELECT RAISE(ABORT, 'ONT move-table source requires exactly one producer authority'); END
    """,
    "trg_ont_move_source_exact_producer_update": """
        CREATE TRIGGER IF NOT EXISTS trg_ont_move_source_exact_producer_update
            BEFORE UPDATE ON ont_move_table_sources
            WHEN (NEW.source_job_id IS NULL) = (NEW.external_registration_receipt_id IS NULL)
            BEGIN SELECT RAISE(ABORT, 'ONT move-table source requires exactly one producer authority'); END
    """,
    "trg_ont_move_source_external_receipt_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_ont_move_source_external_receipt_insert
            BEFORE INSERT ON ont_move_table_sources
            WHEN NEW.external_registration_receipt_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM ont_external_move_bam_registration_receipts AS receipt
                WHERE receipt.id = NEW.external_registration_receipt_id
              )
            BEGIN SELECT RAISE(ABORT, 'ONT move-table source external receipt authority does not exist'); END
    """,
    "trg_ont_move_source_external_receipt_update": """
        CREATE TRIGGER IF NOT EXISTS trg_ont_move_source_external_receipt_update
            BEFORE UPDATE ON ont_move_table_sources
            WHEN NEW.external_registration_receipt_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM ont_external_move_bam_registration_receipts AS receipt
                WHERE receipt.id = NEW.external_registration_receipt_id
              )
            BEGIN SELECT RAISE(ABORT, 'ONT move-table source external receipt authority does not exist'); END
    """,
}


def migration_33_trigger_sql_digest(sql: str) -> str:
    normalized = " ".join(sql.strip().rstrip(";").split())
    normalized = normalized.replace(
        "CREATE TRIGGER IF NOT EXISTS ", "CREATE TRIGGER ", 1
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


MIGRATION_33_TRIGGER_SQL_DIGESTS = {
    name: migration_33_trigger_sql_digest(sql)
    for name, sql in MIGRATION_33_TRIGGER_SQL.items()
}


def _preflight_existing_move_sources(connection: sqlite3.Connection) -> None:
    invalid_authority = connection.execute(
        """
        SELECT id FROM ont_move_table_sources
        WHERE (source_job_id IS NULL) = (external_registration_receipt_id IS NULL)
        LIMIT 1
        """
    ).fetchone()
    if invalid_authority is not None:
        raise RuntimeError(
            "existing ONT move-table source does not have exactly one producer authority"
        )

    receipt_table_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (_RECEIPT_TABLE,),
    ).fetchone() is not None
    if receipt_table_exists:
        dangling_receipt = connection.execute(
            """
            SELECT source.id
            FROM ont_move_table_sources AS source
            LEFT JOIN ont_external_move_bam_registration_receipts AS receipt
              ON receipt.id = source.external_registration_receipt_id
            WHERE source.external_registration_receipt_id IS NOT NULL AND receipt.id IS NULL
            LIMIT 1
            """
        ).fetchone()
    else:
        dangling_receipt = connection.execute(
            """
            SELECT id FROM ont_move_table_sources
            WHERE external_registration_receipt_id IS NOT NULL
            LIMIT 1
            """
        ).fetchone()
    if dangling_receipt is not None:
        raise RuntimeError(
            "existing ONT move-table source external receipt authority does not exist"
        )


def migrate(db_path: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        _preflight_existing_move_sources(connection)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ont_external_move_bam_registration_receipts (
                id VARCHAR(128) PRIMARY KEY NOT NULL,
                candidate_id VARCHAR(64) NOT NULL,
                run_id VARCHAR(80) NOT NULL REFERENCES ont_instrument_runs(id) ON DELETE RESTRICT,
                observed_generation INTEGER NOT NULL,
                raw_representation_id VARCHAR(96) NOT NULL REFERENCES ont_raw_signal_representations(id) ON DELETE RESTRICT,
                server_relative_path TEXT NOT NULL,
                root_device INTEGER NOT NULL,
                root_inode INTEGER NOT NULL,
                file_device INTEGER NOT NULL,
                file_inode INTEGER NOT NULL,
                file_mtime_ns INTEGER NOT NULL,
                file_ctime_ns INTEGER NOT NULL,
                artifact_sha256 VARCHAR(64) NOT NULL,
                artifact_size_bytes INTEGER NOT NULL CHECK (artifact_size_bytes > 0),
                molecule_type VARCHAR(16) NOT NULL CHECK (molecule_type IN ('dna','rna')),
                created_at VARCHAR NOT NULL,
                CONSTRAINT uq_ont_external_move_bam_registration
                    UNIQUE (run_id, observed_generation, raw_representation_id, candidate_id, molecule_type)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_ont_external_move_bam_registration_generation
                ON ont_external_move_bam_registration_receipts(run_id, observed_generation)
            """
        )
        for trigger_sql in MIGRATION_33_TRIGGER_SQL.values():
            connection.execute(trigger_sql)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
