"""Bounded read-only analytical access to verified Parquet artifacts."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import duckdb
import pyarrow.parquet as pq

from .writer import ScientificArtifactError, verify_artifact


class ScientificArtifactQueryError(ScientificArtifactError):
    """A closed analytical query contract was violated."""


def _validated_scalar(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ScientificArtifactQueryError("query filter value is outside the supported scalar types")


def _query_connection(path: object) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(database=":memory:")
    allowed_directory = str(path).rsplit("/", 1)[0].replace("'", "''")
    connection.execute(f"SET allowed_directories=['{allowed_directory}']")
    connection.execute("SET enable_external_access=false")
    connection.execute("SET threads=2")
    connection.execute("SET memory_limit='512MB'")
    connection.execute("SET max_temp_directory_size='1GB'")
    return connection


def query_rows(
    artifact: Mapping[str, Any],
    *,
    columns: Sequence[str],
    limit: int,
    offset: int = 0,
    root: str | None = None,
    max_limit: int = 10_000,
    filters: Mapping[str, object] | None = None,
    order_by: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    if not columns or len(columns) > 64:
        raise ScientificArtifactQueryError("column projection is outside the supported bounds")
    if not 1 <= int(limit) <= max_limit or int(offset) < 0:
        raise ScientificArtifactQueryError("query window is outside the supported bounds")
    path = verify_artifact(artifact, root=root)
    schema_names = {field.name for field in pq.read_schema(path)}
    if any(column not in schema_names for column in columns):
        raise ScientificArtifactQueryError("query column is not present in the artifact schema")
    filters = filters or {}
    if any(column not in schema_names for column in filters):
        raise ScientificArtifactQueryError("query filter column is not present in the artifact schema")
    order_by = tuple(order_by or ())
    if any(column not in schema_names for column in order_by):
        raise ScientificArtifactQueryError("query ordering column is not present in the artifact schema")
    quoted_columns = ", ".join('"' + column.replace('"', '""') + '"' for column in columns)
    predicates: list[str] = []
    parameters: list[object] = [str(path)]
    for column, value in filters.items():
        quoted = '"' + column.replace('"', '""') + '"'
        if value is None:
            predicates.append(f"{quoted} IS NULL")
        else:
            predicates.append(f"{quoted} = ?")
            parameters.append(_validated_scalar(value))
    where = f" WHERE {' AND '.join(predicates)}" if predicates else ""
    order = ""
    if order_by:
        order = " ORDER BY " + ", ".join('"' + column.replace('"', '""') + '"' for column in order_by)
    connection = _query_connection(path)
    try:
        rows = connection.execute(
            f"SELECT {quoted_columns} FROM read_parquet(?)" + where + order + " LIMIT ? OFFSET ?",
            parameters + [int(limit), int(offset)],
        ).fetchall()
        return [dict(zip(columns, row, strict=True)) for row in rows]
    finally:
        connection.close()


def count_rows(
    artifact: Mapping[str, Any],
    *,
    root: str | None = None,
    filters: Mapping[str, object] | None = None,
) -> int:
    path = verify_artifact(artifact, root=root)
    schema_names = {field.name for field in pq.read_schema(path)}
    filters = filters or {}
    if any(column not in schema_names for column in filters):
        raise ScientificArtifactQueryError("query filter column is not present in the artifact schema")
    predicates: list[str] = []
    parameters: list[object] = [str(path)]
    for column, value in filters.items():
        quoted = '"' + column.replace('"', '""') + '"'
        if value is None:
            predicates.append(f"{quoted} IS NULL")
        else:
            predicates.append(f"{quoted} = ?")
            parameters.append(_validated_scalar(value))
    where = f" WHERE {' AND '.join(predicates)}" if predicates else ""
    connection = _query_connection(path)
    try:
        return int(connection.execute(
            "SELECT COUNT(*) FROM read_parquet(?)" + where,
            parameters,
        ).fetchone()[0])
    finally:
        connection.close()
