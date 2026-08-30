"""Bounded read-only analytical access to verified Parquet artifacts."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import duckdb
import pyarrow.parquet as pq

from .writer import ScientificArtifactError, verified_artifact_snapshot


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
    connection.execute("SET threads=2")
    connection.execute("SET memory_limit='512MB'")
    connection.execute("SET max_temp_directory_size='1GB'")
    return connection


def _predicates(
    schema_names: set[str],
    filters: Mapping[str, object] | None,
    range_filters: Mapping[str, tuple[object | None, object | None]] | None,
) -> tuple[str, list[object]]:
    exact = filters or {}
    ranges = range_filters or {}
    if any(column not in schema_names for column in exact):
        raise ScientificArtifactQueryError("query filter column is not present in the artifact schema")
    if any(column not in schema_names for column in ranges):
        raise ScientificArtifactQueryError("query range column is not present in the artifact schema")
    predicates: list[str] = []
    parameters: list[object] = []
    for column, value in exact.items():
        quoted = '"' + column.replace('"', '""') + '"'
        if value is None:
            predicates.append(f"{quoted} IS NULL")
        else:
            predicates.append(f"{quoted} = ?")
            parameters.append(_validated_scalar(value))
    for column, bounds in ranges.items():
        if not isinstance(bounds, tuple) or len(bounds) != 2:
            raise ScientificArtifactQueryError("query range must contain lower and upper bounds")
        quoted = '"' + column.replace('"', '""') + '"'
        lower, upper = bounds
        if lower is not None:
            predicates.append(f"{quoted} >= ?")
            parameters.append(_validated_scalar(lower))
        if upper is not None:
            predicates.append(f"{quoted} <= ?")
            parameters.append(_validated_scalar(upper))
    where = f" WHERE {' AND '.join(predicates)}" if predicates else ""
    return where, parameters


def query_rows(
    artifact: Mapping[str, Any],
    *,
    columns: Sequence[str],
    limit: int,
    offset: int = 0,
    root: str | None = None,
    max_limit: int = 10_000,
    filters: Mapping[str, object] | None = None,
    range_filters: Mapping[str, tuple[object | None, object | None]] | None = None,
    order_by: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    if not columns or len(columns) > 64:
        raise ScientificArtifactQueryError("column projection is outside the supported bounds")
    if not 1 <= int(limit) <= max_limit or int(offset) < 0:
        raise ScientificArtifactQueryError("query window is outside the supported bounds")
    with verified_artifact_snapshot(artifact, root=root) as path:
        schema_names = {field.name for field in pq.read_schema(path)}
        if any(column not in schema_names for column in columns):
            raise ScientificArtifactQueryError("query column is not present in the artifact schema")
        order_by = tuple(order_by or ())
        if any(column not in schema_names for column in order_by):
            raise ScientificArtifactQueryError("query ordering column is not present in the artifact schema")
        quoted_columns = ", ".join('"' + column.replace('"', '""') + '"' for column in columns)
        where, filter_parameters = _predicates(schema_names, filters, range_filters)
        order = ""
        if order_by:
            order = " ORDER BY " + ", ".join('"' + column.replace('"', '""') + '"' for column in order_by)
        connection = _query_connection(path)
        try:
            rows = connection.execute(
                f"SELECT {quoted_columns} FROM read_parquet(?)" + where + order + " LIMIT ? OFFSET ?",
                [str(path), *filter_parameters, int(limit), int(offset)],
            ).fetchall()
            return [dict(zip(columns, row, strict=True)) for row in rows]
        finally:
            connection.close()


def query_rows_by_values(
    artifact: Mapping[str, Any],
    *,
    key_column: str,
    values: Sequence[str],
    columns: Sequence[str],
    root: str | None = None,
    max_values: int = 10_000,
) -> list[dict[str, Any]]:
    """Return rows for one bounded exact string-key set."""
    if not columns or len(columns) > 64:
        raise ScientificArtifactQueryError("column projection is outside the supported bounds")
    if not values or len(values) > max_values or len(set(values)) != len(values):
        raise ScientificArtifactQueryError("query key set is outside the supported bounds")
    if any(not isinstance(value, str) or not value or len(value) > 255 for value in values):
        raise ScientificArtifactQueryError("query key value is outside the supported bounds")
    with verified_artifact_snapshot(artifact, root=root) as path:
        schema_names = {field.name for field in pq.read_schema(path)}
        if key_column not in schema_names or any(column not in schema_names for column in columns):
            raise ScientificArtifactQueryError("query column is not present in the artifact schema")
        quoted_columns = ", ".join('p."' + column.replace('"', '""') + '"' for column in columns)
        quoted_key = '"' + key_column.replace('"', '""') + '"'
        connection = _query_connection(path)
        try:
            connection.execute("CREATE TEMP TABLE requested_values (value VARCHAR PRIMARY KEY)")
            connection.executemany(
                "INSERT INTO requested_values (value) VALUES (?)",
                [(value,) for value in values],
            )
            rows = connection.execute(
                f"SELECT {quoted_columns} FROM read_parquet(?) AS p "
                f"JOIN requested_values AS r ON p.{quoted_key} = r.value "
                f"ORDER BY p.{quoted_key}",
                [str(path)],
            ).fetchall()
            return [dict(zip(columns, row, strict=True)) for row in rows]
        finally:
            connection.close()


def count_rows(
    artifact: Mapping[str, Any],
    *,
    root: str | None = None,
    filters: Mapping[str, object] | None = None,
    range_filters: Mapping[str, tuple[object | None, object | None]] | None = None,
) -> int:
    with verified_artifact_snapshot(artifact, root=root) as path:
        schema_names = {field.name for field in pq.read_schema(path)}
        where, filter_parameters = _predicates(schema_names, filters, range_filters)
        connection = _query_connection(path)
        try:
            return int(connection.execute(
                "SELECT COUNT(*) FROM read_parquet(?)" + where,
                [str(path), *filter_parameters],
            ).fetchone()[0])
        finally:
            connection.close()
