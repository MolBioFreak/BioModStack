"""Exact SQLite-owned schema SQL identity normalization."""
from __future__ import annotations


def sqlite_master_sql_identity(sql: object) -> str:
    """Preserve every stored SQL byte except outer space and one trailing semicolon."""

    normalized = str(sql or "").strip()
    if normalized.endswith(";"):
        normalized = normalized[:-1].rstrip()
    return normalized
