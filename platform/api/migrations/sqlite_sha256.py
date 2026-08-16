"""SQLite SHA-256 and canonical-JSON functions for persistence guards."""
from __future__ import annotations

import hashlib
import json
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


def sqlite_canonical_object_json(value: Any) -> str | None:
    """Return the exact application canonical form for one JSON object."""

    if not isinstance(value, str):
        return None

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        document: dict[str, Any] = {}
        for key, child in pairs:
            if key in document:
                raise ValueError("duplicate JSON key")
            document[key] = child
        return document

    try:
        document = json.loads(value, object_pairs_hook=unique_object)
        if type(document) is not dict:
            return None
        return json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        return None


def register_sqlite_sha256(connection: Any) -> None:
    """Install deterministic digest and canonical-JSON functions."""
    connection.create_function("sha256", 1, sqlite_sha256, deterministic=True)
    connection.create_function(
        "canonical_object_json", 1, sqlite_canonical_object_json, deterministic=True
    )
