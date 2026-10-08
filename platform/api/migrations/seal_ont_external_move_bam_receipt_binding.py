"""Seal external move-BAM receipt tuple binding at the SQLite boundary."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path


MIGRATION_39_TRIGGER_SQL = {
    "trg_ont_move_source_external_receipt_binding_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_ont_move_source_external_receipt_binding_insert
            BEFORE INSERT ON ont_move_table_sources
            WHEN NEW.external_registration_receipt_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM ont_external_move_bam_registration_receipts AS receipt
                WHERE receipt.id = NEW.external_registration_receipt_id
                  AND receipt.run_id IS NEW.run_id
                  AND receipt.observed_generation IS NEW.observed_generation
                  AND receipt.raw_representation_id IS NEW.raw_representation_id
                  AND receipt.artifact_sha256 IS NEW.artifact_sha256
                  AND receipt.artifact_size_bytes IS NEW.artifact_size_bytes
                  AND receipt.molecule_type IS NEW.molecule_type
              )
            BEGIN SELECT RAISE(ABORT, 'ONT move-source external receipt tuple is not authoritative'); END
    """,
    "trg_ont_move_source_external_receipt_binding_update": """
        CREATE TRIGGER IF NOT EXISTS trg_ont_move_source_external_receipt_binding_update
            BEFORE UPDATE ON ont_move_table_sources
            WHEN NEW.external_registration_receipt_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM ont_external_move_bam_registration_receipts AS receipt
                WHERE receipt.id = NEW.external_registration_receipt_id
                  AND receipt.run_id IS NEW.run_id
                  AND receipt.observed_generation IS NEW.observed_generation
                  AND receipt.raw_representation_id IS NEW.raw_representation_id
                  AND receipt.artifact_sha256 IS NEW.artifact_sha256
                  AND receipt.artifact_size_bytes IS NEW.artifact_size_bytes
                  AND receipt.molecule_type IS NEW.molecule_type
              )
            BEGIN SELECT RAISE(ABORT, 'ONT move-source external receipt tuple is not authoritative'); END
    """,
}


def _normalize_sql(sql: str) -> str:
    source = sql.strip().rstrip(";").strip()
    output: list[str] = []
    quote: str | None = None
    pending_space = False
    index = 0
    while index < len(source):
        character = source[index]
        if quote is not None:
            output.append(character)
            if character == quote:
                if index + 1 < len(source) and source[index + 1] == quote:
                    output.append(source[index + 1])
                    index += 1
                else:
                    quote = None
            index += 1
            continue
        if character in {"'", '"'}:
            if pending_space and output:
                output.append(" ")
            pending_space = False
            output.append(character)
            quote = character
        elif character.isspace():
            pending_space = True
        else:
            if pending_space and output:
                output.append(" ")
            pending_space = False
            output.append(character)
        index += 1
    return "".join(output).replace("CREATE TRIGGER IF NOT EXISTS ", "CREATE TRIGGER ", 1)


MIGRATION_39_TRIGGER_SQL_DIGESTS = {
    name: hashlib.sha256(_normalize_sql(sql).encode("utf-8")).hexdigest()
    for name, sql in MIGRATION_39_TRIGGER_SQL.items()
}


def _preflight_existing_rows(connection: sqlite3.Connection) -> None:
    mismatch = connection.execute(
        """
        SELECT source.id
        FROM ont_move_table_sources AS source
        JOIN ont_external_move_bam_registration_receipts AS receipt
          ON receipt.id = source.external_registration_receipt_id
        WHERE source.external_registration_receipt_id IS NOT NULL
          AND NOT (
              receipt.run_id IS source.run_id
              AND receipt.observed_generation IS source.observed_generation
              AND receipt.raw_representation_id IS source.raw_representation_id
              AND receipt.artifact_sha256 IS source.artifact_sha256
              AND receipt.artifact_size_bytes IS source.artifact_size_bytes
              AND receipt.molecule_type IS source.molecule_type
          )
        LIMIT 1
        """
    ).fetchone()
    if mismatch is not None:
        raise RuntimeError("existing ONT move-source external receipt tuple is not authoritative")


def migrate(db_path: str | Path) -> None:
    connection = sqlite3.connect(str(db_path), timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        _preflight_existing_rows(connection)
        for trigger_sql in MIGRATION_39_TRIGGER_SQL.values():
            connection.execute(trigger_sql)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def assert_attested(connection: sqlite3.Connection) -> None:
    observed = {
        str(row[0]): hashlib.sha256(_normalize_sql(str(row[1] or "")).encode("utf-8")).hexdigest()
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger'"
        )
        if str(row[0]) in MIGRATION_39_TRIGGER_SQL_DIGESTS
    }
    if observed != MIGRATION_39_TRIGGER_SQL_DIGESTS:
        raise RuntimeError("migration 39 trigger authority diverged")


__all__ = [
    "MIGRATION_39_TRIGGER_SQL",
    "MIGRATION_39_TRIGGER_SQL_DIGESTS",
    "assert_attested",
    "migrate",
]