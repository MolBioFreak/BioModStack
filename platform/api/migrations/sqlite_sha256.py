"""SQLite SHA-256 registration shared by persistence guards and application engines."""
from __future__ import annotations

import hashlib
from typing import Any


def sqlite_sha256(value: Any) -> str | None:
    """Return a lowercase SHA-256 digest for SQLite TEXT/BLOB input."""
    if value is None:
        return None
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = str(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def register_sqlite_sha256(connection: Any) -> None:
    """Install deterministic ``sha256(value)`` on one SQLite DB-API connection."""
    connection.create_function("sha256", 1, sqlite_sha256, deterministic=True)
