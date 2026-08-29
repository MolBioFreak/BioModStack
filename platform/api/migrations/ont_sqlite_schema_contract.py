"""Exact SQLite schema-contract checks for governed migrations."""
from __future__ import annotations

import sqlite3

from migrations.add_ont_external_move_bam_receipts import MIGRATION_33_TRIGGER_SQL
from migrations.add_ont_move_source_attempt_lineage import MIGRATION_34_TRIGGER_SQL
from migrations.seal_ont_external_move_bam_receipt_binding import MIGRATION_39_TRIGGER_SQL


ContractColumn = tuple[str, str, int, str | None, int]
IndexMetadata = tuple[str, str, bool, bool, tuple[str, ...]]
IndexContract = frozenset[IndexMetadata]
ForeignKeyContract = frozenset[tuple[str, str, str, str, str, str]]
TriggerContract = dict[str, str]


def normalize_sql(sql: str) -> str:
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
    normalized = "".join(output)
    return normalized.replace("CREATE TRIGGER IF NOT EXISTS ", "CREATE TRIGGER ", 1)


def sqlite_index_contract(
    connection: sqlite3.Connection, table_name: str
) -> IndexContract:
    indexes: set[IndexMetadata] = set()
    for row in connection.execute(
        "SELECT seq, name, \"unique\", origin, partial "
        "FROM pragma_index_list(?)",
        (table_name,),
    ):
        index_name = str(row[1])
        columns = tuple(
            str(column[2])
            for column in connection.execute(
                "SELECT seqno, cid, name FROM pragma_index_info(?) ORDER BY seqno",
                (index_name,),
            )
        )
        indexes.add((index_name, str(row[3]), bool(row[4]), bool(row[2]), columns))
    return frozenset(indexes)


def sqlite_trigger_contract(
    connection: sqlite3.Connection, table_name: str
) -> TriggerContract:
    return {
        str(name): normalize_sql(str(sql or ""))
        for name, sql in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
            (table_name,),
        )
    }


def assert_sqlite_table_contract(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    columns: tuple[ContractColumn, ...],
    indexes: IndexContract,
    foreign_keys: ForeignKeyContract,
    sql_fragments: tuple[str, ...],
    label: str,
    triggers: TriggerContract | None = None,
) -> None:
    observed_columns = tuple(
        (str(row[1]), str(row[2]), int(row[3]), row[4], int(row[5]))
        for row in connection.execute(
            "SELECT cid, name, type, \"notnull\", dflt_value, pk "
            "FROM pragma_table_info(?) ORDER BY cid",
            (table_name,),
        )
    )
    if observed_columns != columns:
        raise RuntimeError(f"{label} columns diverged")

    table_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    normalized_table_sql = "" if table_sql_row is None else normalize_sql(str(table_sql_row[0] or ""))
    if any(normalize_sql(fragment) not in normalized_table_sql for fragment in sql_fragments):
        raise RuntimeError(f"{label} checks diverged")

    if sqlite_index_contract(connection, table_name) != indexes:
        raise RuntimeError(f"{label} indexes diverged")

    observed_foreign_keys = frozenset(
        (str(row[3]), str(row[2]), str(row[4]), str(row[5]), str(row[6]), str(row[7]))
        for row in connection.execute(
            "SELECT id, seq, \"table\", \"from\", \"to\", on_update, on_delete, match "
            "FROM pragma_foreign_key_list(?)",
            (table_name,),
        )
    )
    if observed_foreign_keys != foreign_keys:
        raise RuntimeError(f"{label} foreign keys diverged")

    if triggers is not None and sqlite_trigger_contract(connection, table_name) != triggers:
        raise RuntimeError(f"{label} triggers diverged")


_ONT_SOURCE_COLUMN_CONTRACT = (
    ("id", "VARCHAR(96)", 1, None, 1),
    ("run_id", "VARCHAR(80)", 1, None, 0),
    ("observed_generation", "INTEGER", 1, None, 0),
    ("raw_representation_id", "VARCHAR(96)", 1, None, 0),
    ("input_file_id", "VARCHAR(36)", 1, None, 0),
    ("source_job_id", "VARCHAR(36)", 0, None, 0),
    ("external_registration_receipt_id", "VARCHAR(128)", 0, None, 0),
    ("artifact_sha256", "VARCHAR(64)", 1, None, 0),
    ("artifact_size_bytes", "INTEGER", 1, None, 0),
    ("bam_header_sha256", "VARCHAR(64)", 0, None, 0),
    ("record_count", "INTEGER", 0, None, 0),
    ("unique_read_count", "INTEGER", 0, None, 0),
    ("mv_tag_count", "INTEGER", 0, None, 0),
    ("ts_tag_count", "INTEGER", 0, None, 0),
    ("ns_tag_count", "INTEGER", 0, None, 0),
    ("basecall_model_id", "VARCHAR(255)", 0, None, 0),
    ("molecule_type", "VARCHAR(16)", 1, None, 0),
    ("source_runtime_identity", "JSON", 1, None, 0),
    ("read_inventory_sha256", "VARCHAR(64)", 0, None, 0),
    ("validation_state", "VARCHAR(32)", 1, None, 0),
    ("reason_code", "VARCHAR(96)", 1, None, 0),
    ("validation_receipt", "JSON", 1, None, 0),
    ("claim_token", "VARCHAR(96)", 0, None, 0),
    ("lease_expires_at", "VARCHAR", 0, None, 0),
    ("created_at", "VARCHAR", 1, None, 0),
    ("validated_at", "VARCHAR", 0, None, 0),
    ("attempt_number", "INTEGER", 1, "1", 0),
    ("predecessor_move_source_id", "VARCHAR(96)", 0, None, 0),
)
_ONT_SOURCE_INDEX_CONTRACT = frozenset(
    {
        ("sqlite_autoindex_ont_move_table_sources_1", "pk", False, True, ("id",)),
        ("sqlite_autoindex_ont_move_table_sources_2", "u", False, True, ("claim_token",)),
        ("sqlite_autoindex_ont_move_table_sources_3", "u", False, True, ("run_id", "observed_generation", "artifact_sha256", "attempt_number")),
        ("sqlite_autoindex_ont_move_table_sources_4", "u", False, True, ("predecessor_move_source_id",)),
        ("ix_ont_move_sources_generation", "c", False, False, ("run_id", "observed_generation")),
        ("ix_ont_move_sources_state", "c", False, False, ("validation_state",)),
        ("ix_ont_move_sources_predecessor", "c", False, False, ("predecessor_move_source_id",)),
    }
)
_ONT_SOURCE_FOREIGN_KEY_CONTRACT = frozenset(
    {
        ("predecessor_move_source_id", "ont_move_table_sources", "id", "NO ACTION", "RESTRICT", "NONE"),
        ("source_job_id", "jobs", "id", "NO ACTION", "RESTRICT", "NONE"),
        ("input_file_id", "input_files", "id", "NO ACTION", "RESTRICT", "NONE"),
        ("raw_representation_id", "ont_raw_signal_representations", "id", "NO ACTION", "RESTRICT", "NONE"),
        ("run_id", "ont_instrument_runs", "id", "NO ACTION", "RESTRICT", "NONE"),
    }
)
_ONT_SOURCE_SQL_FRAGMENTS = (
    "CHECK (molecule_type IN ('dna','rna'))",
    "CHECK (validation_state IN ('requested','running','ready','failed'))",
    "CHECK (attempt_number >= 1)",
    "CHECK ( (attempt_number = 1 AND predecessor_move_source_id IS NULL) OR (attempt_number > 1 AND predecessor_move_source_id IS NOT NULL) )",
)
_ONT_SOURCE_TRIGGER_CONTRACT = {
    name: normalize_sql(sql)
    for name, sql in {**MIGRATION_33_TRIGGER_SQL, **MIGRATION_34_TRIGGER_SQL}.items()
    if "move_source" in name or "move-source" in name or "ont_move_source" in name
}

ONT_TERMINAL_IMMUTABILITY_TRIGGER_NAME = "trg_ont_move_source_terminal_authority_immutable"
ONT_TERMINAL_IMMUTABILITY_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_ont_move_source_terminal_authority_immutable
BEFORE UPDATE ON ont_move_table_sources
WHEN OLD.validation_state IN ('ready','failed') AND (
    NEW.id IS NOT OLD.id OR
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
    NEW.predecessor_move_source_id IS NOT OLD.predecessor_move_source_id
)
BEGIN
    SELECT RAISE(ABORT, 'terminal move-source authority immutable');
END;
"""
_ONT_SOURCE_TRIGGER_CONTRACT[ONT_TERMINAL_IMMUTABILITY_TRIGGER_NAME] = normalize_sql(
    ONT_TERMINAL_IMMUTABILITY_TRIGGER_SQL
)


def ensure_ont_move_source_terminal_immutability(connection: sqlite3.Connection) -> None:
    connection.execute(ONT_TERMINAL_IMMUTABILITY_TRIGGER_SQL)

def assert_ont_move_source_table_contract(
    connection: sqlite3.Connection,
    *,
    include_external_receipt_binding: bool = False,
) -> None:
    triggers = dict(_ONT_SOURCE_TRIGGER_CONTRACT)
    if include_external_receipt_binding:
        triggers.update(
            {
                name: normalize_sql(sql)
                for name, sql in MIGRATION_39_TRIGGER_SQL.items()
            }
        )
    assert_sqlite_table_contract(
        connection,
        table_name="ont_move_table_sources",
        columns=_ONT_SOURCE_COLUMN_CONTRACT,
        indexes=_ONT_SOURCE_INDEX_CONTRACT,
        foreign_keys=_ONT_SOURCE_FOREIGN_KEY_CONTRACT,
        sql_fragments=_ONT_SOURCE_SQL_FRAGMENTS,
        label="ONT move-source table",
        triggers=triggers,
    )
