"""Bounded read-only analytical access to verified Parquet artifacts."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import duckdb
import pyarrow.parquet as pq

from .writer import ScientificArtifactError, verify_artifact


class ScientificArtifactQueryError(ScientificArtifactError):
    """A closed analytical query contract was violated."""


def query_rows(
    artifact: Mapping[str, Any],
    *,
    columns: Sequence[str],
    limit: int,
    offset: int = 0,
    root: str | None = None,
    max_limit: int = 10_000,
) -> list[dict[str, Any]]:
    if not columns or len(columns) > 64:
        raise ScientificArtifactQueryError("column projection is outside the supported bounds")
    if not 1 <= int(limit) <= max_limit or int(offset) < 0:
        raise ScientificArtifactQueryError("query window is outside the supported bounds")
    path = verify_artifact(artifact, root=root)
    schema_names = {field.name for field in pq.read_schema(path)}
    if any(column not in schema_names for column in columns):
        raise ScientificArtifactQueryError("query column is not present in the artifact schema")
    quoted_columns = ", ".join('"' + column.replace('"', '""') + '"' for column in columns)
    connection = duckdb.connect(database=":memory:")
    try:
        allowed_directory = path.parent.as_posix().replace("'", "''")
        connection.execute(f"SET allowed_directories=['{allowed_directory}']")
        connection.execute("SET enable_external_access=false")
        connection.execute("SET threads=2")
        connection.execute("SET memory_limit='512MB'")
        connection.execute("SET max_temp_directory_size='1GB'")
        rows = connection.execute(
            f"SELECT {quoted_columns} FROM read_parquet(?) LIMIT ? OFFSET ?",
            [str(path), int(limit), int(offset)],
        ).fetchall()
        return [dict(zip(columns, row, strict=True)) for row in rows]
    finally:
        connection.close()
