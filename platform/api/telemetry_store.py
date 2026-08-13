from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Literal

RAW_RETENTION_SECONDS = 7 * 24 * 60 * 60
AGGREGATE_RETENTION_SECONDS = 90 * 24 * 60 * 60
MAX_HISTORY_POINTS = 10_000

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS raw_samples (
    timestamp_ms INTEGER PRIMARY KEY,
    payload_json TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS minute_aggregates (
    bucket_ms INTEGER PRIMARY KEY,
    sample_count INTEGER NOT NULL CHECK (sample_count > 0),
    payload_json TEXT NOT NULL
) WITHOUT ROWID;
CREATE TRIGGER IF NOT EXISTS raw_samples_no_update
BEFORE UPDATE ON raw_samples BEGIN SELECT RAISE(ABORT, 'raw telemetry is immutable'); END;
CREATE TRIGGER IF NOT EXISTS raw_samples_guard_delete
BEFORE DELETE ON raw_samples WHEN telemetry_retention_authorized() != 1
BEGIN SELECT RAISE(ABORT, 'raw telemetry is immutable'); END;
CREATE TRIGGER IF NOT EXISTS minute_aggregates_no_update
BEFORE UPDATE ON minute_aggregates BEGIN SELECT RAISE(ABORT, 'minute telemetry is immutable'); END;
CREATE TRIGGER IF NOT EXISTS minute_aggregates_guard_delete
BEFORE DELETE ON minute_aggregates WHEN telemetry_retention_authorized() != 1
BEGIN SELECT RAISE(ABORT, 'minute telemetry is immutable'); END;
"""


def telemetry_db_path() -> Path:
    configured = os.getenv("BMS_TELEMETRY_DB_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    state_home = Path(os.getenv("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
    return (state_home / "biomodstack" / "telemetry.sqlite3").resolve()


def _connect(path: Path, *, maintenance: bool = False) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.create_function("telemetry_retention_authorized", 0, lambda: 1 if maintenance else 0)
    return connection


def open_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _average_values(values: list[Any]) -> Any:
    present = [value for value in values if value is not None]
    if not present:
        return None
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in present):
        return sum(float(value) for value in present) / len(present)
    if all(isinstance(value, dict) for value in present):
        keys = sorted({key for value in present for key in value})
        return {key: _average_values([value.get(key) for value in present]) for key in keys}
    if all(isinstance(value, list) for value in present):
        by_index: dict[int, list[dict[str, Any]]] = {}
        for value in present:
            for item in value:
                if isinstance(item, dict) and isinstance(item.get("index"), int):
                    by_index.setdefault(item["index"], []).append(item)
        return [_average_values(by_index[index]) for index in sorted(by_index)]
    return present[-1]


class TelemetryStore:
    def __init__(self, path: Path):
        self.path = path.resolve()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self.path) as connection:
            connection.executescript(_SCHEMA)

    def append_sample(self, payload: dict[str, Any]) -> None:
        timestamp_ms = int(payload["timestamp_ms"])
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True, allow_nan=False)
        with _connect(self.path) as connection:
            connection.execute(
                "INSERT INTO raw_samples(timestamp_ms, payload_json) VALUES (?, ?)",
                (timestamp_ms, encoded),
            )

    def finalize_completed_minutes(self, now_ms: int) -> int:
        open_bucket = (int(now_ms) // 60_000) * 60_000
        with _connect(self.path) as connection:
            last_bucket_row = connection.execute("SELECT MAX(bucket_ms) FROM minute_aggregates").fetchone()
            lower_bound = 0 if last_bucket_row[0] is None else int(last_bucket_row[0]) + 60_000
            bucket_rows = connection.execute(
                "SELECT DISTINCT (timestamp_ms / 60000) * 60000 AS bucket_ms "
                "FROM raw_samples WHERE timestamp_ms >= ? AND timestamp_ms < ? ORDER BY bucket_ms",
                (lower_bound, open_bucket),
            ).fetchall()
            finalized = 0
            for bucket_row in bucket_rows:
                bucket = int(bucket_row["bucket_ms"])
                rows = connection.execute(
                    "SELECT payload_json FROM raw_samples WHERE timestamp_ms >= ? AND timestamp_ms < ? ORDER BY timestamp_ms",
                    (bucket, bucket + 60_000),
                ).fetchall()
                samples = [json.loads(row["payload_json"]) for row in rows]
                if not samples:
                    continue
                aggregate = _average_values(samples)
                aggregate["timestamp_ms"] = bucket
                connection.execute(
                    "INSERT INTO minute_aggregates(bucket_ms, sample_count, payload_json) VALUES (?, ?, ?)",
                    (bucket, len(samples), json.dumps(aggregate, separators=(",", ":"), sort_keys=True, allow_nan=False)),
                )
                finalized += 1
            return finalized

    def apply_retention(self, now_ms: int) -> dict[str, int]:
        raw_cutoff = int(now_ms) - RAW_RETENTION_SECONDS * 1000
        minute_cutoff = int(now_ms) - AGGREGATE_RETENTION_SECONDS * 1000
        with _connect(self.path, maintenance=True) as connection:
            raw_deleted = connection.execute("DELETE FROM raw_samples WHERE timestamp_ms < ?", (raw_cutoff,)).rowcount
            minute_deleted = connection.execute("DELETE FROM minute_aggregates WHERE bucket_ms < ?", (minute_cutoff,)).rowcount
        return {"raw_deleted": raw_deleted, "minute_deleted": minute_deleted}

    def read_history(
        self,
        *,
        start_ms: int,
        end_ms: int,
        resolution: Literal["raw", "minute"],
        limit: int,
    ) -> list[dict[str, Any]]:
        if resolution not in {"raw", "minute"}:
            raise ValueError("unsupported resolution")
        if not 1 <= int(limit) <= MAX_HISTORY_POINTS:
            raise ValueError("limit is outside the supported range")
        table, column = ("raw_samples", "timestamp_ms") if resolution == "raw" else ("minute_aggregates", "bucket_ms")
        sample_count = "1 AS sample_count" if resolution == "raw" else "sample_count"
        with open_read_only(self.path) as connection:
            rows = connection.execute(
                f"SELECT {column} AS timestamp_ms, payload_json, {sample_count} FROM {table} "
                f"WHERE {column} >= ? AND {column} < ? ORDER BY {column} DESC LIMIT ?",
                (int(start_ms), int(end_ms), int(limit)),
            ).fetchall()
        return [
            {"timestamp_ms": int(row["timestamp_ms"]), "sample_count": int(row["sample_count"]), "payload": json.loads(row["payload_json"])}
            for row in reversed(rows)
        ]

    def insert_minute_for_test(self, bucket_ms: int, payload: dict[str, Any], sample_count: int) -> None:
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True, allow_nan=False)
        with _connect(self.path) as connection:
            connection.execute(
                "INSERT INTO minute_aggregates(bucket_ms, sample_count, payload_json) VALUES (?, ?, ?)",
                (int(bucket_ms), int(sample_count), encoded),
            )
