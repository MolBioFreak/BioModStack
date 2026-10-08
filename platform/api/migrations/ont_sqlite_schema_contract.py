"""Exact SQLite schema-contract checks for governed migrations."""
from __future__ import annotations

import sqlite3


ContractColumn = tuple[str, str, int, str | None, int]
IndexContract = frozenset[tuple[bool, tuple[str, ...]]]
ForeignKeyContract = frozenset[tuple[str, str, str, str, str, str]]


def sqlite_index_contract(
    connection: sqlite3.Connection, table_name: str
) -> IndexContract:
    indexes: set[tuple[bool, tuple[str, ...]]] = set()
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
        indexes.add((bool(row[2]), columns))
    return frozenset(indexes)


def assert_sqlite_table_contract(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    columns: tuple[ContractColumn, ...],
    indexes: IndexContract,
    foreign_keys: ForeignKeyContract,
    sql_fragments: tuple[str, ...],
    label: str,
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
    normalized_table_sql = "" if table_sql_row is None else " ".join(str(table_sql_row[0] or "").split())
    if any(fragment not in normalized_table_sql for fragment in sql_fragments):
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
