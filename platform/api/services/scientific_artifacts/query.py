"""Bounded read-only analytical access to verified Parquet artifacts."""
from __future__ import annotations

from typing import Any, Mapping, Sequence
from contextlib import contextmanager
import shutil
import uuid

import duckdb
import pyarrow.parquet as pq

from .writer import ScientificArtifactError, verified_artifact_snapshot


class ScientificArtifactQueryError(ScientificArtifactError):
    """A closed analytical query contract was violated."""


def _validated_scalar(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ScientificArtifactQueryError("query filter value is outside the supported scalar types")


@contextmanager
def _query_connection(path: object):
    """One current-target allocation through query close and scratch removal.

    DuckDB's buffer/spill limits consume the global owner's effective allocation;
    they are not a claim of process-wide isolation for Arrow/Python allocations.
    """
    from services import global_resource_admission as resources
    from services.ngs_alignment_sessions import _snapshot_cache_directory
    root = _snapshot_cache_directory()
    scratch = root / ("query-" + uuid.uuid4().hex)
    allocation = None
    connection = None
    try:
        allocation = resources.reserve(owner="scientific-artifact-query", storage_root=root,
            owned_path=scratch, cpu_threads=1)
        scratch.mkdir(mode=0o700)
        connection = duckdb.connect(database=":memory:")
        scratch_sql = str(scratch).replace("'", "''")
        directories = [scratch_sql]
        if path is not None:
            directories.append(str(path).rsplit("/", 1)[0].replace("'", "''"))
        connection.execute("SET allowed_directories=[" + ", ".join("'" + item + "'" for item in directories) + "]")
        connection.execute(f"SET threads={allocation.cpu_threads}")
        connection.execute(f"SET memory_limit='{allocation.dram_bytes}B'")
        connection.execute(f"SET temp_directory='{scratch_sql}'")
        connection.execute(f"SET max_temp_directory_size='{allocation.disk_bytes}B'")
        yield connection
    except resources.ResourceCapacityUnavailable as exc:
        raise ScientificArtifactQueryError("query capacity unavailable") from exc
    finally:
        try:
            if connection is not None:
                connection.close()
        finally:
            try:
                if scratch.exists():
                    shutil.rmtree(scratch)
            finally:
                if allocation is not None:
                    allocation.release(storage_removed=not scratch.exists())


@contextmanager
def _verified_dataset(artifact, root):
    from services.ngs_alignment_sessions import verified_parquet_dataset
    with verified_artifact_snapshot(artifact, root=root, file_like=True) as handle:
        with verified_parquet_dataset(handle) as dataset:
            yield dataset


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
    with _verified_dataset(artifact, root) as dataset:
        schema_names = set(dataset.schema.names)
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
        with _query_connection(None) as connection:
            connection.register("verified_input", dataset)
            rows = connection.execute(
                f"SELECT {quoted_columns} FROM verified_input" + where + order + " LIMIT ? OFFSET ?",
                [*filter_parameters, int(limit), int(offset)],
            ).fetchall()
            return [dict(zip(columns, row, strict=True)) for row in rows]


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
    with _verified_dataset(artifact, root) as dataset:
        schema_names = set(dataset.schema.names)
        if key_column not in schema_names or any(column not in schema_names for column in columns):
            raise ScientificArtifactQueryError("query column is not present in the artifact schema")
        quoted_columns = ", ".join('p."' + column.replace('"', '""') + '"' for column in columns)
        quoted_key = '"' + key_column.replace('"', '""') + '"'
        with _query_connection(None) as connection:
            connection.register("verified_input", dataset)
            connection.execute("CREATE TEMP TABLE requested_values (value VARCHAR PRIMARY KEY)")
            connection.executemany(
                "INSERT INTO requested_values (value) VALUES (?)",
                [(value,) for value in values],
            )
            rows = connection.execute(
                f"SELECT {quoted_columns} FROM verified_input AS p "
                f"JOIN requested_values AS r ON p.{quoted_key} = r.value "
                f"ORDER BY p.{quoted_key}",
            ).fetchall()
            return [dict(zip(columns, row, strict=True)) for row in rows]


def count_rows(
    artifact: Mapping[str, Any],
    *,
    root: str | None = None,
    filters: Mapping[str, object] | None = None,
    range_filters: Mapping[str, tuple[object | None, object | None]] | None = None,
) -> int:
    with _verified_dataset(artifact, root) as dataset:
        schema_names = set(dataset.schema.names)
        where, filter_parameters = _predicates(schema_names, filters, range_filters)
        with _query_connection(None) as connection:
            connection.register("verified_input", dataset)
            return int(connection.execute(
                "SELECT COUNT(*) FROM verified_input" + where,
                filter_parameters,
            ).fetchone()[0])
