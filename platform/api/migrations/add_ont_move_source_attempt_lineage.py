"""Add immutable attempt lineage to governed ONT move-table sources."""
from __future__ import annotations

import hashlib
import sqlite3

from migrations.add_ont_external_move_bam_receipts import (
    MIGRATION_33_TRIGGER_SQL,
    MIGRATION_33_TRIGGER_SQL_DIGESTS,
    migration_33_trigger_sql_digest,
)


_SOURCE_TABLE = "ont_move_table_sources"
_LEGACY_SOURCE_TABLE = "ont_move_table_sources_v33"

MIGRATION_34_TRIGGER_SQL = {
    "trg_ont_move_source_no_delete": """
        CREATE TRIGGER IF NOT EXISTS trg_ont_move_source_no_delete
        BEFORE DELETE ON ont_move_table_sources
        BEGIN SELECT RAISE(ABORT, 'ONT move-table sources are immutable retained evidence'); END
    """,
    "trg_ont_move_source_identity_no_update": """
        CREATE TRIGGER IF NOT EXISTS trg_ont_move_source_identity_no_update
        BEFORE UPDATE ON ont_move_table_sources
        WHEN NEW.run_id IS NOT OLD.run_id OR NEW.observed_generation IS NOT OLD.observed_generation OR
             NEW.raw_representation_id IS NOT OLD.raw_representation_id OR NEW.input_file_id IS NOT OLD.input_file_id OR
             NEW.source_job_id IS NOT OLD.source_job_id OR
             NEW.external_registration_receipt_id IS NOT OLD.external_registration_receipt_id OR
             NEW.source_runtime_identity IS NOT OLD.source_runtime_identity OR
             NEW.artifact_sha256 IS NOT OLD.artifact_sha256 OR NEW.artifact_size_bytes IS NOT OLD.artifact_size_bytes OR
             NEW.molecule_type IS NOT OLD.molecule_type OR NEW.created_at IS NOT OLD.created_at OR
             NEW.attempt_number IS NOT OLD.attempt_number OR
             NEW.predecessor_move_source_id IS NOT OLD.predecessor_move_source_id
        BEGIN SELECT RAISE(ABORT, 'ONT move-table source identity is immutable'); END
    """,
    "trg_ont_move_source_terminal_no_update": """
        CREATE TRIGGER IF NOT EXISTS trg_ont_move_source_terminal_no_update
        BEFORE UPDATE ON ont_move_table_sources
        WHEN OLD.validation_state IN ('ready','failed') AND (
             NEW.run_id IS NOT OLD.run_id OR
             NEW.observed_generation IS NOT OLD.observed_generation OR
             NEW.raw_representation_id IS NOT OLD.raw_representation_id OR
             NEW.input_file_id IS NOT OLD.input_file_id OR
             NEW.source_job_id IS NOT OLD.source_job_id OR
             NEW.external_registration_receipt_id IS NOT OLD.external_registration_receipt_id OR
             NEW.artifact_sha256 IS NOT OLD.artifact_sha256 OR
             NEW.artifact_size_bytes IS NOT OLD.artifact_size_bytes OR
             NEW.bam_header_sha256 IS NOT OLD.bam_header_sha256 OR
             NEW.record_count IS NOT OLD.record_count OR
             NEW.unique_read_count IS NOT OLD.unique_read_count OR
             NEW.mv_tag_count IS NOT OLD.mv_tag_count OR
             NEW.ts_tag_count IS NOT OLD.ts_tag_count OR
             NEW.ns_tag_count IS NOT OLD.ns_tag_count OR
             NEW.basecall_model_id IS NOT OLD.basecall_model_id OR
             NEW.molecule_type IS NOT OLD.molecule_type OR
             NEW.source_runtime_identity IS NOT OLD.source_runtime_identity OR
             NEW.read_inventory_sha256 IS NOT OLD.read_inventory_sha256 OR
             NEW.validation_state IS NOT OLD.validation_state OR
             NEW.reason_code IS NOT OLD.reason_code OR
             NEW.validation_receipt IS NOT OLD.validation_receipt OR
             NEW.claim_token IS NOT OLD.claim_token OR
             NEW.lease_expires_at IS NOT OLD.lease_expires_at OR
             NEW.created_at IS NOT OLD.created_at OR
             NEW.validated_at IS NOT OLD.validated_at OR
             NEW.attempt_number IS NOT OLD.attempt_number OR
             NEW.predecessor_move_source_id IS NOT OLD.predecessor_move_source_id)
        BEGIN SELECT RAISE(ABORT, 'ONT move-table source terminal evidence is immutable'); END
    """,
    "trg_ont_move_source_attempt_lineage_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_ont_move_source_attempt_lineage_insert
        BEFORE INSERT ON ont_move_table_sources
        WHEN NEW.predecessor_move_source_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM ont_move_table_sources AS predecessor
            WHERE predecessor.id = NEW.predecessor_move_source_id
              AND predecessor.validation_state = 'failed'
              AND predecessor.source_job_id IS NULL
              AND predecessor.external_registration_receipt_id IS NOT NULL
              AND predecessor.claim_token IS NULL
              AND predecessor.lease_expires_at IS NULL
              AND NEW.attempt_number = predecessor.attempt_number + 1
              AND NEW.run_id IS predecessor.run_id
              AND NEW.observed_generation IS predecessor.observed_generation
              AND NEW.raw_representation_id IS predecessor.raw_representation_id
              AND NEW.input_file_id IS predecessor.input_file_id
              AND NEW.source_job_id IS predecessor.source_job_id
              AND NEW.external_registration_receipt_id IS predecessor.external_registration_receipt_id
              AND NEW.artifact_sha256 IS predecessor.artifact_sha256
              AND NEW.artifact_size_bytes IS predecessor.artifact_size_bytes
              AND NEW.molecule_type IS predecessor.molecule_type
              AND NEW.source_runtime_identity IS predecessor.source_runtime_identity
        )
        BEGIN SELECT RAISE(ABORT, 'ONT move-source attempt lineage authority diverged'); END
    """,
}


def _trigger_sql_digest(sql: str) -> str:
    normalized = " ".join(sql.strip().rstrip(";").split())
    normalized = normalized.replace(
        "CREATE TRIGGER IF NOT EXISTS ", "CREATE TRIGGER ", 1
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


MIGRATION_34_TRIGGER_SQL_DIGESTS = {
    name: _trigger_sql_digest(sql) for name, sql in MIGRATION_34_TRIGGER_SQL.items()
}


_CREATE_SOURCE_TABLE_SQL = """
    CREATE TABLE ont_move_table_sources (
        id VARCHAR(96) PRIMARY KEY NOT NULL,
        run_id VARCHAR(80) NOT NULL REFERENCES ont_instrument_runs(id) ON DELETE RESTRICT,
        observed_generation INTEGER NOT NULL,
        raw_representation_id VARCHAR(96) NOT NULL REFERENCES ont_raw_signal_representations(id) ON DELETE RESTRICT,
        input_file_id VARCHAR(36) NOT NULL REFERENCES input_files(id) ON DELETE RESTRICT,
        source_job_id VARCHAR(36) REFERENCES jobs(id) ON DELETE RESTRICT,
        external_registration_receipt_id VARCHAR(128),
        artifact_sha256 VARCHAR(64) NOT NULL,
        artifact_size_bytes INTEGER NOT NULL,
        bam_header_sha256 VARCHAR(64),
        record_count INTEGER,
        unique_read_count INTEGER,
        mv_tag_count INTEGER,
        ts_tag_count INTEGER,
        ns_tag_count INTEGER,
        basecall_model_id VARCHAR(255),
        molecule_type VARCHAR(16) NOT NULL CHECK (molecule_type IN ('dna','rna')),
        source_runtime_identity JSON NOT NULL,
        read_inventory_sha256 VARCHAR(64),
        validation_state VARCHAR(32) NOT NULL CHECK (validation_state IN ('requested','running','ready','failed')),
        reason_code VARCHAR(96) NOT NULL,
        validation_receipt JSON NOT NULL,
        claim_token VARCHAR(96) UNIQUE,
        lease_expires_at VARCHAR,
        created_at VARCHAR NOT NULL,
        validated_at VARCHAR,
        attempt_number INTEGER NOT NULL DEFAULT 1 CHECK (attempt_number >= 1),
        predecessor_move_source_id VARCHAR(96) REFERENCES ont_move_table_sources(id) ON DELETE RESTRICT,
        CONSTRAINT ck_ont_move_source_attempt_lineage CHECK (
            (attempt_number = 1 AND predecessor_move_source_id IS NULL) OR
            (attempt_number > 1 AND predecessor_move_source_id IS NOT NULL)
        ),
        CONSTRAINT uq_ont_move_source_artifact_attempt
            UNIQUE (run_id, observed_generation, artifact_sha256, attempt_number),
        CONSTRAINT uq_ont_move_source_predecessor UNIQUE (predecessor_move_source_id)
    )
"""

_OLD_COLUMNS = (
    "id",
    "run_id",
    "observed_generation",
    "raw_representation_id",
    "input_file_id",
    "source_job_id",
    "external_registration_receipt_id",
    "artifact_sha256",
    "artifact_size_bytes",
    "bam_header_sha256",
    "record_count",
    "unique_read_count",
    "mv_tag_count",
    "ts_tag_count",
    "ns_tag_count",
    "basecall_model_id",
    "molecule_type",
    "source_runtime_identity",
    "read_inventory_sha256",
    "validation_state",
    "reason_code",
    "validation_receipt",
    "claim_token",
    "lease_expires_at",
    "created_at",
    "validated_at",
)


def _table_columns(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info('{_SOURCE_TABLE}')")
    )


def _create_indexes_and_triggers(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_ont_move_sources_generation "
        "ON ont_move_table_sources(run_id, observed_generation)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_ont_move_sources_state "
        "ON ont_move_table_sources(validation_state)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_ont_move_sources_predecessor "
        "ON ont_move_table_sources(predecessor_move_source_id)"
    )
    for trigger_sql in MIGRATION_34_TRIGGER_SQL.values():
        connection.execute(trigger_sql)
    for trigger_sql in MIGRATION_33_TRIGGER_SQL.values():
        connection.execute(trigger_sql)


def attest(connection: sqlite3.Connection) -> None:
    expected_columns = _OLD_COLUMNS + (
        "attempt_number",
        "predecessor_move_source_id",
    )
    if _table_columns(connection) != expected_columns:
        raise RuntimeError("ONT move-source attempt-lineage columns diverged")
    table_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (_SOURCE_TABLE,),
    ).fetchone()
    table_sql = "" if table_sql_row is None else str(table_sql_row[0] or "")
    normalized_table_sql = " ".join(table_sql.split())
    required_fragments = (
        "CHECK (attempt_number >= 1)",
        "UNIQUE (run_id, observed_generation, artifact_sha256, attempt_number)",
        "UNIQUE (predecessor_move_source_id)",
    )
    if any(fragment not in normalized_table_sql for fragment in required_fragments):
        raise RuntimeError("ONT move-source attempt-lineage constraints diverged")
    source_fks = {
        (str(row[3]), str(row[2]), str(row[4]), str(row[6]))
        for row in connection.execute(
            "PRAGMA foreign_key_list('ont_move_table_sources')"
        )
    }
    if (
        "predecessor_move_source_id",
        "ont_move_table_sources",
        "id",
        "RESTRICT",
    ) not in source_fks:
        raise RuntimeError("ONT move-source predecessor foreign key diverged")
    for child_table in (
        "ont_signal_calibration_artifacts",
        "ont_signal_calibration_jobs",
        "ont_signal_mapping_jobs",
        "ont_signal_viewer_sessions",
    ):
        child_fks = {
            (str(row[3]), str(row[2]), str(row[4]), str(row[6]))
            for row in connection.execute(f"PRAGMA foreign_key_list('{child_table}')")
        }
        if (
            "move_source_id",
            "ont_move_table_sources",
            "id",
            "RESTRICT",
        ) not in child_fks:
            raise RuntimeError(
                "ONT move-source dependent foreign-key authority diverged"
            )
    index_columns = {
        tuple(
            str(column[2])
            for column in connection.execute(
                f"PRAGMA index_info('{str(index[1])}')"
            )
        )
        for index in connection.execute(
            "PRAGMA index_list('ont_move_table_sources')"
        )
    }
    if not {
        ("run_id", "observed_generation"),
        ("validation_state",),
        ("predecessor_move_source_id",),
        ("run_id", "observed_generation", "artifact_sha256", "attempt_number"),
    }.issubset(index_columns):
        raise RuntimeError("ONT move-source attempt-lineage indexes diverged")
    trigger_sql = {
        str(row[0]): str(row[1] or "")
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger'"
        )
    }
    observed_v33 = {
        name: migration_33_trigger_sql_digest(trigger_sql.get(name, ""))
        for name in MIGRATION_33_TRIGGER_SQL_DIGESTS
    }
    if observed_v33 != MIGRATION_33_TRIGGER_SQL_DIGESTS:
        raise RuntimeError("migration 33 move-source trigger authority diverged")
    observed_v34 = {
        name: _trigger_sql_digest(trigger_sql.get(name, ""))
        for name in MIGRATION_34_TRIGGER_SQL_DIGESTS
    }
    if observed_v34 != MIGRATION_34_TRIGGER_SQL_DIGESTS:
        raise RuntimeError("migration 34 move-source trigger authority diverged")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("ONT move-source attempt-lineage foreign keys diverged")


def _preflight_v33(connection: sqlite3.Connection) -> None:
    if _table_columns(connection) != _OLD_COLUMNS:
        raise RuntimeError("ONT move-source v33 schema is not the exact migration predecessor")
    invalid = connection.execute(
        """
        SELECT id FROM ont_move_table_sources
        WHERE (source_job_id IS NULL) = (external_registration_receipt_id IS NULL)
           OR claim_token IS NOT NULL AND validation_state IN ('ready','failed')
           OR lease_expires_at IS NOT NULL AND validation_state IN ('ready','failed')
        LIMIT 1
        """
    ).fetchone()
    if invalid is not None:
        raise RuntimeError("existing ONT move-source authority cannot enter attempt lineage")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("existing ONT move-source foreign keys are invalid")


def migrate(db_path: str) -> None:
    connection = sqlite3.connect(db_path, timeout=30)
    foreign_keys = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
    legacy_alter_table = int(
        connection.execute("PRAGMA legacy_alter_table").fetchone()[0]
    )
    try:
        columns = _table_columns(connection)
        if columns == _OLD_COLUMNS + (
            "attempt_number",
            "predecessor_move_source_id",
        ):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            _create_indexes_and_triggers(connection)
            attest(connection)
            connection.commit()
            return

        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA legacy_alter_table=ON")
        connection.execute("BEGIN IMMEDIATE")
        _preflight_v33(connection)
        for trigger_name in (
            *MIGRATION_34_TRIGGER_SQL,
            *MIGRATION_33_TRIGGER_SQL,
        ):
            connection.execute(f'DROP TRIGGER IF EXISTS "{trigger_name}"')
        connection.execute(
            f"ALTER TABLE {_SOURCE_TABLE} RENAME TO {_LEGACY_SOURCE_TABLE}"
        )
        connection.execute("PRAGMA legacy_alter_table=OFF")
        connection.execute(_CREATE_SOURCE_TABLE_SQL)
        old_columns_sql = ", ".join(_OLD_COLUMNS)
        connection.execute(
            f"""
            INSERT INTO {_SOURCE_TABLE} (
                {old_columns_sql}, attempt_number, predecessor_move_source_id
            )
            SELECT {old_columns_sql}, 1, NULL FROM {_LEGACY_SOURCE_TABLE}
            """
        )
        connection.execute(f"DROP TABLE {_LEGACY_SOURCE_TABLE}")
        _create_indexes_and_triggers(connection)
        attest(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.execute(f"PRAGMA legacy_alter_table={legacy_alter_table}")
        connection.execute(f"PRAGMA foreign_keys={foreign_keys}")
        connection.close()
