"""Host-local, durable receipts for acquisition starts (never an RPC retry queue)."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3


def _request_id(value: str) -> str:
    if not re.fullmatch(r"ont-run-[a-f0-9]{32}", value):
        raise ValueError("a BMS start_request_id is required")
    return value


@contextmanager
def _store():
    root = Path(os.getenv("XDG_STATE_HOME") or Path.home() / ".local/state") / "biomodstack"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / "ont-start-receipts.sqlite3"
    # Receipts contain a digest of settings and private run IDs, not raw settings.
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(descriptor)
    connection = sqlite3.connect(path, timeout=10)
    try:
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("""CREATE TABLE IF NOT EXISTS start_receipts (
            request_id TEXT PRIMARY KEY, position TEXT NOT NULL, request_sha256 TEXT NOT NULL,
            response_code INTEGER NOT NULL, response_json TEXT NOT NULL)""")
        yield connection
    finally:
        connection.close()


def read_start_receipt(position: str, request_id: str) -> dict:
    _request_id(request_id)
    with _store() as connection:
        row = connection.execute(
            "SELECT response_json FROM start_receipts WHERE request_id=? AND position=?",
            (request_id, position),
        ).fetchone()
    return json.loads(row[0]) if row else {
        "start_request_id": request_id, "position": position, "start_outcome": "unknown",
    }


def claim_start(position: str, payload: dict) -> tuple[int, dict] | None:
    request_id = _request_id(str(payload.get("start_request_id") or ""))
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    pending = {
        "start_request_id": request_id, "position": position, "start_outcome": "unknown",
        "detail": "The start outcome is not yet known. This request will not be sent again.",
    }
    with _store() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT position, request_sha256, response_code, response_json FROM start_receipts WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if row:
            if row[0] != position or row[1] != digest:
                return 409, {"detail": "The start request identity was already used with different settings"}
            return row[2], json.loads(row[3])
        connection.execute("INSERT INTO start_receipts VALUES (?, ?, ?, ?, ?)",
                           (request_id, position, digest, 202, json.dumps(pending)))
        connection.commit()
    return None


def finish_start(position: str, request_id: str, code: int, response: dict) -> tuple[int, dict]:
    response = {**response, "position": position, "start_request_id": _request_id(request_id)}
    with _store() as connection:
        connection.execute("UPDATE start_receipts SET response_code=?, response_json=? WHERE request_id=? AND position=?",
                           (code, json.dumps(response), request_id, position))
        connection.commit()
    return code, response
