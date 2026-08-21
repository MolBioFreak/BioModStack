from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Literal

import pyarrow as pa

from services.scientific_artifacts import (
    artifact_reference,
    artifact_row_reference,
    canonical_json_bytes,
    canonical_sha256,
    envelope_rows,
    install_parquet_rows,
    reconstruct_envelope,
    resolve_json_value,
)

RAW_RETENTION_SECONDS = 7 * 24 * 60 * 60
AGGREGATE_RETENTION_SECONDS = 90 * 24 * 60 * 60
MAX_HISTORY_POINTS = 10_000

_TELEMETRY_SCHEMA = pa.schema(
    [
        ("row_index", pa.int64()),
        ("timestamp_ms", pa.int64()),
        ("bucket_ms", pa.int64()),
        ("sample_count", pa.int64()),
        ("timestamp", pa.string()),
        ("payload_json", pa.string()),
        ("cpu_utilization", pa.float64()),
        ("ram_utilization", pa.float64()),
        ("gpu_utilization", pa.list_(pa.float64())),
        ("gpu_memory_used_mb", pa.list_(pa.float64())),
        ("gpu_names", pa.list_(pa.string())),
    ]
)
_ENVELOPE_SCHEMA = pa.schema(
    [("key", pa.string()), ("item_index", pa.int64()), ("payload_json", pa.string())]
)

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
CREATE TABLE IF NOT EXISTS raw_samples (
    timestamp_ms INTEGER PRIMARY KEY,
    payload_json TEXT NOT NULL,
    cpu_utilization REAL,
    ram_utilization REAL,
    gpu_utilization_json TEXT,
    gpu_memory_used_json TEXT,
    gpu_names_json TEXT,
    staging_relative_path TEXT,
    staging_row_locator INTEGER
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS minute_aggregates (
    bucket_ms INTEGER PRIMARY KEY,
    sample_count INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    cpu_utilization REAL,
    ram_utilization REAL,
    gpu_utilization_json TEXT,
    gpu_memory_used_json TEXT,
    gpu_names_json TEXT
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS scientific_artifact_receipts (
    artifact_id TEXT PRIMARY KEY,
    owner_kind TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    role TEXT NOT NULL,
    schema_id TEXT NOT NULL,
    artifact_schema_version INTEGER NOT NULL,
    storage_root TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    row_count INTEGER NOT NULL,
    column_schema_sha256 TEXT NOT NULL,
    media_type TEXT NOT NULL,
    availability TEXT NOT NULL,
    source_receipts_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(owner_kind, owner_id, role, content_sha256)
);
CREATE TABLE IF NOT EXISTS scientific_payload_migrations (
    migration_id TEXT PRIMARY KEY,
    source_store TEXT NOT NULL,
    source_table TEXT NOT NULL,
    source_column TEXT NOT NULL,
    source_key TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL,
    equivalence_sha256 TEXT NOT NULL,
    state TEXT NOT NULL,
    diagnostic TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS telemetry_sample_artifact_refs (
    timestamp_ms INTEGER PRIMARY KEY,
    artifact_ref TEXT NOT NULL
) WITHOUT ROWID;
"""


def telemetry_db_path() -> Path:
    explicit = os.getenv("BMS_TELEMETRY_DB_PATH")
    if explicit:
        path = Path(explicit).expanduser().resolve()
    else:
        path = Path("/mnt/BioModStack/telemetry/telemetry.sqlite3").resolve()
    jobs = os.getenv("BMS_DB_PATH")
    if jobs and path == Path(jobs).expanduser().resolve():
        raise ValueError("telemetry database must be separate from the jobs database")
    return path


def _connect(path: Path, *, maintenance: bool = False) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.create_function("telemetry_retention_authorized", 0, lambda: 1 if maintenance else 0)
    return connection


def open_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _average_values(values: list[Any]) -> Any:
    present = [value for value in values if value is not None]
    if not present:
        return None
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in present):
        return sum(present) / len(present)
    if all(isinstance(value, dict) for value in present):
        keys = sorted({key for value in present for key in value})
        return {key: _average_values([value[key] for value in present if key in value]) for key in keys}
    if all(isinstance(value, list) for value in present):
        by_index: dict[int, list[Any]] = {}
        for value in present:
            for index, item in enumerate(value):
                by_index.setdefault(index, []).append(item)
        return [_average_values(by_index[index]) for index in sorted(by_index)]
    return present[-1]


def _projection(payload: dict[str, Any]) -> tuple[float | None, float | None, list[float], list[float], list[str]]:
    cpu = payload.get("cpu") or {}
    ram = payload.get("ram") or {}
    gpus = payload.get("gpus") or []
    gpu_util = [float(item.get("utilization")) for item in gpus if isinstance(item, dict) and item.get("utilization") is not None]
    gpu_memory = [float(item.get("memory_used_mb")) for item in gpus if isinstance(item, dict) and item.get("memory_used_mb") is not None]
    gpu_names = [str(item.get("name", item.get("index", ""))) for item in gpus if isinstance(item, dict)]
    return (
        float(cpu["utilization"]) if cpu.get("utilization") is not None else None,
        float(ram["utilization"]) if ram.get("utilization") is not None else None,
        gpu_util,
        gpu_memory,
        gpu_names,
    )


def _encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _artifact_source_sha(rows: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(canonical_json_bytes(row))
        digest.update(b"\n")
    return digest.hexdigest()


def _safe_staging_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    path.relative_to(root.resolve())
    if path.exists() and path.is_symlink():
        raise ValueError("staging path must not be a symlink")
    return path


def _receipt_values(artifact: Any, source: dict[str, Any]) -> tuple[Any, ...]:
    ref = artifact.reference()
    return (
        artifact.artifact_id,
        artifact.owner_kind,
        artifact.owner_id,
        artifact.role,
        artifact.schema_id,
        artifact.schema_version,
        "scientific_artifact_root",
        artifact.relative_path,
        artifact.content_sha256,
        artifact.size_bytes,
        artifact.row_count,
        artifact.column_schema_sha256,
        artifact.media_type,
        "available",
        _encoded(source),
        datetime.now(timezone.utc).isoformat(),
    )


class TelemetryStore:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.artifact_root = Path(
            os.getenv("BMS_TELEMETRY_ARTIFACT_ROOT", str(self.path.parent / "scientific_artifacts"))
        ).expanduser().resolve()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        with _connect(self.path) as connection:
            connection.executescript(_SCHEMA)
            receipt_columns = {row[1] for row in connection.execute("PRAGMA table_info(scientific_artifact_receipts)")}
            if "artifact_schema_version" not in receipt_columns:
                connection.execute(
                    "ALTER TABLE scientific_artifact_receipts ADD COLUMN artifact_schema_version INTEGER NOT NULL DEFAULT 1"
                )
                if "schema_version" in receipt_columns:
                    connection.execute(
                        "UPDATE scientific_artifact_receipts SET artifact_schema_version = schema_version"
                    )
            if "storage_root" not in receipt_columns:
                connection.execute(
                    "ALTER TABLE scientific_artifact_receipts ADD COLUMN storage_root TEXT NOT NULL DEFAULT 'scientific_artifact_root'"
                )
            ledger_columns = {row[1] for row in connection.execute("PRAGMA table_info(scientific_payload_migrations)")}
            if "attempt_count" not in ledger_columns:
                connection.execute(
                    "ALTER TABLE scientific_payload_migrations ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0"
                )
            for table in ("raw_samples", "minute_aggregates"):
                existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                additions = {
                    "cpu_utilization": "REAL",
                    "ram_utilization": "REAL",
                    "gpu_utilization_json": "TEXT",
                    "gpu_memory_used_json": "TEXT",
                    "gpu_names_json": "TEXT",
                }
                if table == "raw_samples":
                    additions.update({"staging_relative_path": "TEXT", "staging_row_locator": "INTEGER"})
                for name, ddl in additions.items():
                    if name not in existing:
                        connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            connection.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS raw_samples_guard_update
                BEFORE UPDATE ON raw_samples
                BEGIN SELECT RAISE(ABORT, 'raw_samples is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS raw_samples_guard_delete
                BEFORE DELETE ON raw_samples
                WHEN telemetry_retention_authorized() = 0
                BEGIN SELECT RAISE(ABORT, 'raw_samples retention authorization required'); END;
                CREATE TRIGGER IF NOT EXISTS minute_aggregates_guard_update
                BEFORE UPDATE ON minute_aggregates
                BEGIN SELECT RAISE(ABORT, 'minute_aggregates is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS minute_aggregates_guard_delete
                BEFORE DELETE ON minute_aggregates
                WHEN telemetry_retention_authorized() = 0
                BEGIN SELECT RAISE(ABORT, 'minute_aggregates retention authorization required'); END;
                """
            )

    def _stage_sample(self, payload: dict[str, Any]) -> tuple[str, int]:
        bucket = (int(payload["timestamp_ms"]) // 60_000) * 60_000
        relative = f"staging/raw-{bucket}.jsonl"
        path = _safe_staging_path(self.artifact_root, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        locator = 0
        if path.exists():
            with path.open("rb") as handle:
                locator = sum(1 for _ in handle)
        with path.open("ab") as handle:
            handle.write((_encoded(payload) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        return relative, locator

    def append_sample(self, payload: dict[str, Any]) -> None:
        timestamp_ms = int(payload["timestamp_ms"])
        relative, locator = self._stage_sample(payload)
        cpu, ram, gpu_util, gpu_memory, gpu_names = _projection(payload)
        with _connect(self.path) as connection:
            connection.execute(
                """INSERT INTO raw_samples(
                    timestamp_ms, payload_json, cpu_utilization, ram_utilization,
                    gpu_utilization_json, gpu_memory_used_json, gpu_names_json,
                    staging_relative_path, staging_row_locator
                ) VALUES (?, '{}', ?, ?, ?, ?, ?, ?, ?)""",
                (timestamp_ms, cpu, ram, _encoded(gpu_util), _encoded(gpu_memory), _encoded(gpu_names), relative, locator),
            )

    def _sample_for_row(self, row: sqlite3.Row) -> dict[str, Any]:
        if row["staging_relative_path"]:
            path = _safe_staging_path(self.artifact_root, str(row["staging_relative_path"]))
            lines = path.read_text(encoding="utf-8").splitlines()
            return json.loads(lines[int(row["staging_row_locator"])])
        value = json.loads(row["payload_json"])
        resolved = resolve_json_value(value, root=self.artifact_root)
        if isinstance(resolved, dict):
            return resolved
        raise ValueError("telemetry payload is not an object")

    def _insert_receipt(self, connection: sqlite3.Connection, artifact: Any, source: dict[str, Any]) -> None:
        connection.execute(
            """INSERT OR IGNORE INTO scientific_artifact_receipts(
                artifact_id, owner_kind, owner_id, role, schema_id, artifact_schema_version,
                storage_root, relative_path, content_sha256, size_bytes, row_count,
                column_schema_sha256, media_type, availability, source_receipts_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            _receipt_values(artifact, source),
        )

    def finalize_completed_minutes(self, now_ms: int) -> int:
        open_bucket = (int(now_ms) // 60_000) * 60_000
        finalized = 0
        with _connect(self.path) as connection:
            last = connection.execute("SELECT MAX(bucket_ms) FROM minute_aggregates").fetchone()[0]
            lower = 0 if last is None else int(last) + 60_000
            buckets = connection.execute(
                "SELECT DISTINCT (timestamp_ms / 60000) * 60000 AS bucket_ms FROM raw_samples WHERE timestamp_ms >= ? AND timestamp_ms < ? ORDER BY bucket_ms",
                (lower, open_bucket),
            ).fetchall()
            for bucket_row in buckets:
                bucket = int(bucket_row["bucket_ms"])
                rows = connection.execute(
                    "SELECT * FROM raw_samples WHERE timestamp_ms >= ? AND timestamp_ms < ? ORDER BY timestamp_ms",
                    (bucket, bucket + 60_000),
                ).fetchall()
                samples = [self._sample_for_row(row) for row in rows]
                if not samples:
                    continue
                artifact_rows = []
                for index, sample_value in enumerate(samples):
                    cpu, ram, gpu_util, gpu_memory, gpu_names = _projection(sample_value)
                    artifact_rows.append(
                        {
                            "row_index": index,
                            "timestamp_ms": int(sample_value["timestamp_ms"]),
                            "bucket_ms": bucket,
                            "sample_count": 1,
                            "timestamp": str(sample_value.get("timestamp", "")),
                            "payload_json": _encoded(sample_value),
                            "cpu_utilization": cpu,
                            "ram_utilization": ram,
                            "gpu_utilization": gpu_util,
                            "gpu_memory_used_mb": gpu_memory,
                            "gpu_names": gpu_names,
                        }
                    )
                raw_sha = _artifact_source_sha(artifact_rows)
                raw_artifact = install_parquet_rows(
                    root=self.artifact_root,
                    owner_kind="telemetry_bucket",
                    owner_id=f"raw:{bucket}",
                    role="raw_history",
                    schema_id="bms.telemetry.raw.v1",
                    schema_version=1,
                    source_sha256=raw_sha,
                    rows=artifact_rows,
                    schema=_TELEMETRY_SCHEMA,
                )
                aggregate = _average_values(samples)
                aggregate["timestamp_ms"] = bucket
                aggregate["timestamp"] = datetime.fromtimestamp(bucket / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
                aggregate_rows = envelope_rows(aggregate)
                aggregate_artifact = install_parquet_rows(
                    root=self.artifact_root,
                    owner_kind="telemetry_bucket",
                    owner_id=f"minute:{bucket}",
                    role="minute_aggregate",
                    schema_id="bms.telemetry.aggregate.v1",
                    schema_version=1,
                    source_sha256=canonical_sha256(aggregate),
                    rows=aggregate_rows,
                    schema=_ENVELOPE_SCHEMA,
                )
                connection.execute("BEGIN IMMEDIATE")
                source = {"source_store": "telemetry", "source_table": "raw_samples", "source_key": str(bucket)}
                self._insert_receipt(connection, raw_artifact, source)
                self._insert_receipt(connection, aggregate_artifact, {**source, "source_table": "minute_aggregates"})
                for index, row in enumerate(rows):
                    ref = artifact_row_reference(raw_artifact.reference(), index, value_field="payload_json")
                    connection.execute(
                        "INSERT OR IGNORE INTO telemetry_sample_artifact_refs(timestamp_ms, artifact_ref) VALUES (?, ?)",
                        (int(row["timestamp_ms"]), _encoded(ref)),
                    )
                aggregate_ref = artifact_reference(**{
                    "artifact_id": aggregate_artifact.artifact_id,
                    "owner_kind": aggregate_artifact.owner_kind,
                    "owner_id": aggregate_artifact.owner_id,
                    "role": aggregate_artifact.role,
                    "schema_id": aggregate_artifact.schema_id,
                    "schema_version": aggregate_artifact.schema_version,
                    "content_sha256": aggregate_artifact.content_sha256,
                    "size_bytes": aggregate_artifact.size_bytes,
                    "row_count": aggregate_artifact.row_count,
                    "relative_path": aggregate_artifact.relative_path,
                })
                cpu, ram, gpu_util, gpu_memory, gpu_names = _projection(aggregate)
                connection.execute(
                    """INSERT OR IGNORE INTO minute_aggregates(
                        bucket_ms, sample_count, payload_json, cpu_utilization, ram_utilization,
                        gpu_utilization_json, gpu_memory_used_json, gpu_names_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (bucket, len(samples), _encoded(aggregate_ref), cpu, ram, _encoded(gpu_util), _encoded(gpu_memory), _encoded(gpu_names)),
                )
                connection.commit()
                stage_paths = {row["staging_relative_path"] for row in rows if row["staging_relative_path"]}
                for relative in stage_paths:
                    _safe_staging_path(self.artifact_root, str(relative)).unlink(missing_ok=True)
                finalized += 1
        return finalized

    def apply_retention(self, now_ms: int) -> dict[str, int]:
        raw_cutoff = int(now_ms) - RAW_RETENTION_SECONDS * 1000
        minute_cutoff = int(now_ms) - AGGREGATE_RETENTION_SECONDS * 1000
        with _connect(self.path, maintenance=True) as connection:
            old_refs = [row[0] for row in connection.execute("SELECT timestamp_ms FROM raw_samples WHERE timestamp_ms < ?", (raw_cutoff,))]
            raw_deleted = connection.execute("DELETE FROM raw_samples WHERE timestamp_ms < ?", (raw_cutoff,)).rowcount
            for timestamp_ms in old_refs:
                connection.execute("DELETE FROM telemetry_sample_artifact_refs WHERE timestamp_ms = ?", (timestamp_ms,))
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
        extra = ", staging_relative_path, staging_row_locator" if resolution == "raw" else ""
        with open_read_only(self.path) as connection:
            rows = connection.execute(
                f"SELECT {column} AS timestamp_ms, payload_json, {sample_count}{extra} FROM {table} WHERE {column} >= ? AND {column} < ? ORDER BY {column} DESC LIMIT ?",
                (int(start_ms), int(end_ms), int(limit)),
            ).fetchall()
        values = []
        for row in reversed(rows):
            if resolution == "raw" and row["staging_relative_path"]:
                payload = self._sample_for_row(row)
            else:
                payload = resolve_json_value(json.loads(row["payload_json"]), root=self.artifact_root)
            values.append({"timestamp_ms": int(row["timestamp_ms"]), "sample_count": int(row["sample_count"]), "payload": payload})
        return values

    def insert_minute_for_test(self, bucket_ms: int, payload: dict[str, Any], sample_count: int) -> None:
        encoded = _encoded(payload)
        cpu, ram, gpu_util, gpu_memory, gpu_names = _projection(payload)
        with _connect(self.path) as connection:
            connection.execute(
                """INSERT INTO minute_aggregates(
                    bucket_ms, sample_count, payload_json, cpu_utilization, ram_utilization,
                    gpu_utilization_json, gpu_memory_used_json, gpu_names_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (int(bucket_ms), int(sample_count), encoded, cpu, ram, _encoded(gpu_util), _encoded(gpu_memory), _encoded(gpu_names)),
            )
