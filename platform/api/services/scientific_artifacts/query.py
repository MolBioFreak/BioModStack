"""Bounded read-only analytical access to verified Parquet artifacts."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
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


class ArtifactQuery:
    """One verified receipt, schema and bounded connection for one request."""

    def __init__(self, path, connection):
        self.path = path
        self.connection = connection
        self.schema_names = {field.name for field in pq.read_schema(path)}

    def query_rows(
        self,
        *,
        columns: Sequence[str],
        limit: int,
        offset: int = 0,
        max_limit: int = 10_000,
        filters: Mapping[str, object] | None = None,
        range_filters: Mapping[str, tuple[object | None, object | None]] | None = None,
        order_by: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not columns or len(columns) > 64:
            raise ScientificArtifactQueryError("column projection is outside the supported bounds")
        if not 1 <= int(limit) <= max_limit or int(offset) < 0:
            raise ScientificArtifactQueryError("query window is outside the supported bounds")
        path = self.path
        schema_names = self.schema_names
        connection = self.connection
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
        rows = connection.execute(
            f"SELECT {quoted_columns} FROM read_parquet(?)" + where + order + " LIMIT ? OFFSET ?",
            [str(path), *filter_parameters, int(limit), int(offset)],
        ).fetchall()
        return [dict(zip(columns, row, strict=True)) for row in rows]

    def query_rows_by_values(
        self,
        *,
        key_column: str,
        values: Sequence[str],
        columns: Sequence[str],
        max_values: int = 10_000,
    ) -> list[dict[str, Any]]:
        """Return rows for one bounded exact string-key set."""
        if not columns or len(columns) > 64:
            raise ScientificArtifactQueryError("column projection is outside the supported bounds")
        if not values or len(values) > max_values or len(set(values)) != len(values):
            raise ScientificArtifactQueryError("query key set is outside the supported bounds")
        if any(not isinstance(value, str) or not value or len(value) > 255 for value in values):
            raise ScientificArtifactQueryError("query key value is outside the supported bounds")
        path = self.path
        schema_names = self.schema_names
        connection = self.connection
        if key_column not in schema_names or any(column not in schema_names for column in columns):
            raise ScientificArtifactQueryError("query column is not present in the artifact schema")
        quoted_columns = ", ".join('p."' + column.replace('"', '""') + '"' for column in columns)
        quoted_key = '"' + key_column.replace('"', '""') + '"'
        connection.execute("CREATE OR REPLACE TEMP TABLE requested_values (value VARCHAR PRIMARY KEY)")
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

    def count_rows(
        self,
        *,
        filters: Mapping[str, object] | None = None,
        range_filters: Mapping[str, tuple[object | None, object | None]] | None = None,
    ) -> int:
        path = self.path
        schema_names = self.schema_names
        connection = self.connection
        where, filter_parameters = _predicates(schema_names, filters, range_filters)
        return int(connection.execute(
            "SELECT COUNT(*) FROM read_parquet(?)" + where,
            [str(path), *filter_parameters],
        ).fetchone()[0])


@contextmanager
def artifact_query(artifact: Mapping[str, Any], *, root: Path | str | None = None):
    with verified_artifact_snapshot(artifact, root=root) as path:
        connection = _query_connection(path)
        try:
            yield ArtifactQuery(path, connection)
        finally:
            connection.close()


def query_rows(
    artifact: Mapping[str, Any],
    *,
    columns: Sequence[str],
    limit: int,
    offset: int = 0,
    root: Path | str | None = None,
    max_limit: int = 10_000,
    filters: Mapping[str, object] | None = None,
    range_filters: Mapping[str, tuple[object | None, object | None]] | None = None,
    order_by: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    with artifact_query(artifact, root=root) as query:
        return query.query_rows(columns=columns, limit=limit, offset=offset, max_limit=max_limit, filters=filters, range_filters=range_filters, order_by=order_by)


def query_rows_by_values(
    artifact: Mapping[str, Any],
    *,
    key_column: str,
    values: Sequence[str],
    columns: Sequence[str],
    root: Path | str | None = None,
    max_values: int = 10_000,
) -> list[dict[str, Any]]:
    with artifact_query(artifact, root=root) as query:
        return query.query_rows_by_values(key_column=key_column, values=values, columns=columns, max_values=max_values)


def count_rows(
    artifact: Mapping[str, Any],
    *,
    root: Path | str | None = None,
    filters: Mapping[str, object] | None = None,
    range_filters: Mapping[str, tuple[object | None, object | None]] | None = None,
) -> int:
    with artifact_query(artifact, root=root) as query:
        return query.count_rows(filters=filters, range_filters=range_filters)
