from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Any, Literal

import pyarrow as pa
import pyarrow.parquet as pq

RAW_RETENTION_SECONDS = 7 * 24 * 60 * 60
AGGREGATE_RETENTION_SECONDS = 90 * 24 * 60 * 60
MAX_HISTORY_POINTS = 10_000
TELEMETRY_FRESHNESS_STALE_AFTER_MS = 15_000
TELEMETRY_SCHEMA_VERSION = 2

_GPU_PROCESS_TYPE = pa.struct(
    [
        pa.field("pid", pa.int64()),
        pa.field("name", pa.string()),
        pa.field("memory_mb", pa.int64()),
    ]
)
_GPU_TYPE = pa.struct(
    [
        pa.field("index", pa.int64()),
        pa.field("name", pa.string()),
        pa.field("utilization", pa.float64()),
        pa.field("memory_utilization", pa.float64()),
        pa.field("memory_used_mb", pa.float64()),
        pa.field("memory_total_mb", pa.float64()),
        pa.field("reserved_memory_mb", pa.float64()),
        pa.field("power_draw_w", pa.float64()),
        pa.field("power_limit_w", pa.float64()),
        pa.field("min_power_watts", pa.float64()),
        pa.field("default_power_watts", pa.float64()),
        pa.field("max_power_watts", pa.float64()),
        pa.field("temperature", pa.float64()),
        pa.field("fan_speed", pa.float64()),
        pa.field("clock_graphics_mhz", pa.float64()),
        pa.field("clock_memory_mhz", pa.float64()),
        pa.field("clock_max_graphics_mhz", pa.float64()),
        pa.field("clock_max_memory_mhz", pa.float64()),
        pa.field("processes", pa.list_(_GPU_PROCESS_TYPE)),
    ]
)
_RAW_PARTITION_SCHEMA = pa.schema(
    [
        pa.field("timestamp_ms", pa.int64(), nullable=False),
        pa.field("timestamp", pa.string(), nullable=False),
        pa.field("gpu_error", pa.string()),
        pa.field("cpu_name", pa.string(), nullable=False),
        pa.field("cpu_cores_physical", pa.int64(), nullable=False),
        pa.field("cpu_cores_logical", pa.int64(), nullable=False),
        pa.field("cpu_utilization", pa.float64()),
        pa.field("cpu_per_core_utilization", pa.list_(pa.float64()), nullable=False),
        pa.field("cpu_frequency_current_mhz", pa.float64()),
        pa.field("cpu_frequency_max_mhz", pa.float64()),
        pa.field("cpu_temperature", pa.float64()),
        pa.field("cpu_power_watts", pa.float64()),
        pa.field("cpu_power_source", pa.string(), nullable=False),
        pa.field("cpu_power_available", pa.bool_(), nullable=False),
        pa.field("cpu_power_status", pa.string(), nullable=False),
        pa.field("cpu_power_message", pa.string(), nullable=False),
        pa.field("cpu_power_discovered_sources", pa.int64(), nullable=False),
        pa.field("cpu_power_readable_sources", pa.int64(), nullable=False),
        pa.field("cpu_power_setup_hint", pa.string()),
        pa.field("ram_total_gb", pa.float64()),
        pa.field("ram_used_gb", pa.float64()),
        pa.field("ram_available_gb", pa.float64()),
        pa.field("ram_utilization", pa.float64()),
        pa.field("ram_swap_total_gb", pa.float64()),
        pa.field("ram_swap_used_gb", pa.float64()),
        pa.field("ram_swap_percent", pa.float64()),
        pa.field("gpus", pa.list_(_GPU_TYPE), nullable=False),
    ]
)

_BASE_COLUMNS = """
    timestamp_ms INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    sample_count INTEGER NOT NULL CHECK(sample_count > 0),
    gpu_error TEXT,
    cpu_name TEXT NOT NULL,
    cpu_cores_physical INTEGER NOT NULL,
    cpu_cores_logical INTEGER NOT NULL,
    cpu_utilization REAL,
    cpu_frequency_current_mhz REAL,
    cpu_frequency_max_mhz REAL,
    cpu_temperature REAL,
    cpu_power_watts REAL,
    cpu_power_source TEXT NOT NULL,
    cpu_power_available INTEGER NOT NULL CHECK(cpu_power_available IN (0, 1)),
    cpu_power_status TEXT NOT NULL,
    cpu_power_message TEXT NOT NULL,
    cpu_power_discovered_sources INTEGER NOT NULL,
    cpu_power_readable_sources INTEGER NOT NULL,
    cpu_power_setup_hint TEXT,
    ram_total_gb REAL,
    ram_used_gb REAL,
    ram_available_gb REAL,
    ram_utilization REAL,
    ram_swap_total_gb REAL,
    ram_swap_used_gb REAL,
    ram_swap_percent REAL
"""

_SCHEMA = f"""
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
BEGIN IMMEDIATE;
PRAGMA user_version={TELEMETRY_SCHEMA_VERSION};
CREATE TABLE telemetry_schema (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    version INTEGER NOT NULL
) WITHOUT ROWID;
INSERT INTO telemetry_schema(singleton, version) VALUES (1, {TELEMETRY_SCHEMA_VERSION});
CREATE TABLE raw_samples ({_BASE_COLUMNS}) WITHOUT ROWID;
CREATE TABLE raw_cpu_cores (
    timestamp_ms INTEGER NOT NULL REFERENCES raw_samples(timestamp_ms) ON DELETE CASCADE,
    core_index INTEGER NOT NULL,
    utilization REAL NOT NULL,
    PRIMARY KEY(timestamp_ms, core_index)
) WITHOUT ROWID;
CREATE TABLE raw_gpu_samples (
    timestamp_ms INTEGER NOT NULL REFERENCES raw_samples(timestamp_ms) ON DELETE CASCADE,
    gpu_index INTEGER NOT NULL,
    name TEXT NOT NULL,
    utilization REAL,
    memory_utilization REAL,
    memory_used_mb REAL,
    memory_total_mb REAL,
    reserved_memory_mb REAL,
    power_draw_w REAL,
    power_limit_w REAL,
    min_power_watts REAL,
    default_power_watts REAL,
    max_power_watts REAL,
    temperature REAL,
    fan_speed REAL,
    clock_graphics_mhz REAL,
    clock_memory_mhz REAL,
    clock_max_graphics_mhz REAL,
    clock_max_memory_mhz REAL,
    PRIMARY KEY(timestamp_ms, gpu_index)
) WITHOUT ROWID;
CREATE TABLE raw_gpu_processes (
    timestamp_ms INTEGER NOT NULL,
    gpu_index INTEGER NOT NULL,
    pid INTEGER NOT NULL,
    name TEXT NOT NULL,
    memory_mb INTEGER NOT NULL,
    PRIMARY KEY(timestamp_ms, gpu_index, pid),
    FOREIGN KEY(timestamp_ms, gpu_index) REFERENCES raw_gpu_samples(timestamp_ms, gpu_index) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE minute_aggregates ({_BASE_COLUMNS}) WITHOUT ROWID;
CREATE TABLE minute_cpu_cores (
    timestamp_ms INTEGER NOT NULL REFERENCES minute_aggregates(timestamp_ms) ON DELETE CASCADE,
    core_index INTEGER NOT NULL,
    utilization REAL NOT NULL,
    PRIMARY KEY(timestamp_ms, core_index)
) WITHOUT ROWID;
CREATE TABLE minute_gpu_samples (
    timestamp_ms INTEGER NOT NULL REFERENCES minute_aggregates(timestamp_ms) ON DELETE CASCADE,
    gpu_index INTEGER NOT NULL,
    name TEXT NOT NULL,
    utilization REAL,
    memory_utilization REAL,
    memory_used_mb REAL,
    memory_total_mb REAL,
    reserved_memory_mb REAL,
    power_draw_w REAL,
    power_limit_w REAL,
    min_power_watts REAL,
    default_power_watts REAL,
    max_power_watts REAL,
    temperature REAL,
    fan_speed REAL,
    clock_graphics_mhz REAL,
    clock_memory_mhz REAL,
    clock_max_graphics_mhz REAL,
    clock_max_memory_mhz REAL,
    PRIMARY KEY(timestamp_ms, gpu_index)
) WITHOUT ROWID;
CREATE TABLE minute_gpu_processes (
    timestamp_ms INTEGER NOT NULL,
    gpu_index INTEGER NOT NULL,
    pid INTEGER NOT NULL,
    name TEXT NOT NULL,
    memory_mb INTEGER NOT NULL,
    PRIMARY KEY(timestamp_ms, gpu_index, pid),
    FOREIGN KEY(timestamp_ms, gpu_index) REFERENCES minute_gpu_samples(timestamp_ms, gpu_index) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE telemetry_partitions (
    partition_start_ms INTEGER PRIMARY KEY,
    partition_end_ms INTEGER NOT NULL,
    relative_path TEXT NOT NULL UNIQUE,
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
    size_bytes INTEGER NOT NULL CHECK(size_bytes > 0),
    row_count INTEGER NOT NULL CHECK(row_count > 0),
    created_at TEXT NOT NULL
) WITHOUT ROWID;
CREATE TRIGGER raw_samples_guard_update BEFORE UPDATE ON raw_samples
BEGIN SELECT RAISE(ABORT, 'raw_samples is append-only'); END;
CREATE TRIGGER raw_samples_guard_delete BEFORE DELETE ON raw_samples
WHEN telemetry_retention_authorized() = 0
BEGIN SELECT RAISE(ABORT, 'raw_samples retention authorization required'); END;
CREATE TRIGGER minute_aggregates_guard_update BEFORE UPDATE ON minute_aggregates
BEGIN SELECT RAISE(ABORT, 'minute_aggregates is append-only'); END;
CREATE TRIGGER minute_aggregates_guard_delete BEFORE DELETE ON minute_aggregates
WHEN telemetry_retention_authorized() = 0
BEGIN SELECT RAISE(ABORT, 'minute_aggregates retention authorization required'); END;
CREATE TRIGGER raw_cpu_cores_guard_update BEFORE UPDATE ON raw_cpu_cores
BEGIN SELECT RAISE(ABORT, 'raw_cpu_cores is append-only'); END;
CREATE TRIGGER raw_cpu_cores_guard_delete BEFORE DELETE ON raw_cpu_cores
WHEN telemetry_retention_authorized() = 0
BEGIN SELECT RAISE(ABORT, 'raw_cpu_cores retention authorization required'); END;
CREATE TRIGGER raw_gpu_samples_guard_update BEFORE UPDATE ON raw_gpu_samples
BEGIN SELECT RAISE(ABORT, 'raw_gpu_samples is append-only'); END;
CREATE TRIGGER raw_gpu_samples_guard_delete BEFORE DELETE ON raw_gpu_samples
WHEN telemetry_retention_authorized() = 0
BEGIN SELECT RAISE(ABORT, 'raw_gpu_samples retention authorization required'); END;
CREATE TRIGGER raw_gpu_processes_guard_update BEFORE UPDATE ON raw_gpu_processes
BEGIN SELECT RAISE(ABORT, 'raw_gpu_processes is append-only'); END;
CREATE TRIGGER raw_gpu_processes_guard_delete BEFORE DELETE ON raw_gpu_processes
WHEN telemetry_retention_authorized() = 0
BEGIN SELECT RAISE(ABORT, 'raw_gpu_processes retention authorization required'); END;
CREATE TRIGGER minute_cpu_cores_guard_update BEFORE UPDATE ON minute_cpu_cores
BEGIN SELECT RAISE(ABORT, 'minute_cpu_cores is append-only'); END;
CREATE TRIGGER minute_cpu_cores_guard_delete BEFORE DELETE ON minute_cpu_cores
WHEN telemetry_retention_authorized() = 0
BEGIN SELECT RAISE(ABORT, 'minute_cpu_cores retention authorization required'); END;
CREATE TRIGGER minute_gpu_samples_guard_update BEFORE UPDATE ON minute_gpu_samples
BEGIN SELECT RAISE(ABORT, 'minute_gpu_samples is append-only'); END;
CREATE TRIGGER minute_gpu_samples_guard_delete BEFORE DELETE ON minute_gpu_samples
WHEN telemetry_retention_authorized() = 0
BEGIN SELECT RAISE(ABORT, 'minute_gpu_samples retention authorization required'); END;
CREATE TRIGGER minute_gpu_processes_guard_update BEFORE UPDATE ON minute_gpu_processes
BEGIN SELECT RAISE(ABORT, 'minute_gpu_processes is append-only'); END;
CREATE TRIGGER minute_gpu_processes_guard_delete BEFORE DELETE ON minute_gpu_processes
WHEN telemetry_retention_authorized() = 0
BEGIN SELECT RAISE(ABORT, 'minute_gpu_processes retention authorization required'); END;
CREATE TRIGGER telemetry_partitions_guard_update BEFORE UPDATE ON telemetry_partitions
BEGIN SELECT RAISE(ABORT, 'telemetry_partitions is append-only'); END;
CREATE TRIGGER telemetry_partitions_guard_delete BEFORE DELETE ON telemetry_partitions
WHEN telemetry_retention_authorized() = 0
BEGIN SELECT RAISE(ABORT, 'telemetry_partitions retention authorization required'); END;
CREATE TRIGGER raw_samples_guard_finalized_hour BEFORE INSERT ON raw_samples
WHEN EXISTS (
    SELECT 1 FROM telemetry_partitions
    WHERE NEW.timestamp_ms >= partition_start_ms AND NEW.timestamp_ms < partition_end_ms
) OR EXISTS (
    SELECT 1 FROM minute_aggregates
    WHERE timestamp_ms = (NEW.timestamp_ms / 60000) * 60000
)
BEGIN SELECT RAISE(ABORT, 'raw sample belongs to a finalized telemetry period'); END;
CREATE TRIGGER raw_samples_guard_insert_authority BEFORE INSERT ON raw_samples
WHEN telemetry_writer_authorized() = 0
BEGIN SELECT RAISE(ABORT, 'raw sample insert authority required'); END;
CREATE TRIGGER minute_aggregates_guard_insert_authority BEFORE INSERT ON minute_aggregates
WHEN telemetry_writer_authorized() = 0
BEGIN SELECT RAISE(ABORT, 'minute aggregate insert authority required'); END;
CREATE TRIGGER raw_cpu_cores_guard_insert_authority BEFORE INSERT ON raw_cpu_cores
WHEN telemetry_writer_authorized() = 0
BEGIN SELECT RAISE(ABORT, 'raw CPU core insert authority required'); END;
CREATE TRIGGER raw_gpu_samples_guard_insert_authority BEFORE INSERT ON raw_gpu_samples
WHEN telemetry_writer_authorized() = 0
BEGIN SELECT RAISE(ABORT, 'raw GPU sample insert authority required'); END;
CREATE TRIGGER raw_gpu_processes_guard_insert_authority BEFORE INSERT ON raw_gpu_processes
WHEN telemetry_writer_authorized() = 0
BEGIN SELECT RAISE(ABORT, 'raw GPU process insert authority required'); END;
CREATE TRIGGER minute_cpu_cores_guard_insert_authority BEFORE INSERT ON minute_cpu_cores
WHEN telemetry_writer_authorized() = 0
BEGIN SELECT RAISE(ABORT, 'minute CPU core insert authority required'); END;
CREATE TRIGGER minute_gpu_samples_guard_insert_authority BEFORE INSERT ON minute_gpu_samples
WHEN telemetry_writer_authorized() = 0
BEGIN SELECT RAISE(ABORT, 'minute GPU sample insert authority required'); END;
CREATE TRIGGER minute_gpu_processes_guard_insert_authority BEFORE INSERT ON minute_gpu_processes
WHEN telemetry_writer_authorized() = 0
BEGIN SELECT RAISE(ABORT, 'minute GPU process insert authority required'); END;
CREATE TRIGGER telemetry_partitions_guard_insert_authority BEFORE INSERT ON telemetry_partitions
WHEN telemetry_partition_authorized() = 0
BEGIN SELECT RAISE(ABORT, 'telemetry partition insert authority required'); END;
COMMIT;
"""

_EXPECTED_SCHEMA_OBJECTS: dict[tuple[str, str], str] | None = None


def _schema_objects(connection: sqlite3.Connection) -> dict[tuple[str, str], str]:
    return {
        (str(row[0]), str(row[1])): " ".join(str(row[2]).split())
        for row in connection.execute(
            """SELECT type, name, sql FROM sqlite_master
            WHERE type IN ('table', 'trigger')
              AND name NOT LIKE 'sqlite_%'
            ORDER BY type, name"""
        )
    }


def _expected_schema_objects() -> dict[tuple[str, str], str]:
    global _EXPECTED_SCHEMA_OBJECTS
    if _EXPECTED_SCHEMA_OBJECTS is None:
        connection = sqlite3.connect(":memory:", isolation_level=None)
        connection.create_function("telemetry_retention_authorized", 0, lambda: 0)
        try:
            connection.executescript(_SCHEMA)
            _EXPECTED_SCHEMA_OBJECTS = _schema_objects(connection)
        finally:
            connection.close()
    return dict(_EXPECTED_SCHEMA_OBJECTS)


def _validate_schema(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT version FROM telemetry_schema WHERE singleton = 1"
    ).fetchone()
    schema_rows = connection.execute("SELECT COUNT(*) FROM telemetry_schema").fetchone()
    if (
        row is None
        or schema_rows is None
        or int(schema_rows[0]) != 1
        or int(row[0]) != TELEMETRY_SCHEMA_VERSION
        or int(connection.execute("PRAGMA user_version").fetchone()[0]) != TELEMETRY_SCHEMA_VERSION
        or _schema_objects(connection) != _expected_schema_objects()
        or connection.execute("PRAGMA foreign_key_check").fetchone() is not None
    ):
        raise RuntimeError("unsupported or incomplete typed telemetry store schema")

_BASE_INSERT_COLUMNS = (
    "timestamp_ms", "timestamp", "sample_count", "gpu_error", "cpu_name",
    "cpu_cores_physical", "cpu_cores_logical", "cpu_utilization",
    "cpu_frequency_current_mhz", "cpu_frequency_max_mhz", "cpu_temperature",
    "cpu_power_watts", "cpu_power_source", "cpu_power_available",
    "cpu_power_status", "cpu_power_message", "cpu_power_discovered_sources",
    "cpu_power_readable_sources", "cpu_power_setup_hint", "ram_total_gb",
    "ram_used_gb", "ram_available_gb", "ram_utilization", "ram_swap_total_gb",
    "ram_swap_used_gb", "ram_swap_percent",
)
_GPU_COLUMNS = (
    "timestamp_ms", "gpu_index", "name", "utilization", "memory_utilization",
    "memory_used_mb", "memory_total_mb", "reserved_memory_mb", "power_draw_w",
    "power_limit_w", "min_power_watts", "default_power_watts", "max_power_watts",
    "temperature", "fan_speed", "clock_graphics_mhz", "clock_memory_mhz",
    "clock_max_graphics_mhz", "clock_max_memory_mhz",
)


def telemetry_db_path() -> Path:
    explicit = os.getenv("BMS_TELEMETRY_DB_PATH")
    path = Path(explicit).expanduser().resolve() if explicit else Path("/mnt/BioModStack/telemetry/telemetry.sqlite3").resolve()
    jobs = os.getenv("BMS_DB_PATH")
    if jobs and path == Path(jobs).expanduser().resolve():
        raise ValueError("telemetry database must be separate from the jobs database")
    return path


def _connect(
    path: Path,
    *,
    maintenance: bool = False,
    writer: bool = False,
    publisher: bool = False,
) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.create_function("telemetry_retention_authorized", 0, lambda: 1 if maintenance else 0)
    connection.create_function("telemetry_writer_authorized", 0, lambda: 1 if writer else 0)
    connection.create_function("telemetry_partition_authorized", 0, lambda: 1 if publisher else 0)
    return connection


def open_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    return float(value)


def _integer(value: Any, default: int = 0) -> int:
    if value is None or isinstance(value, bool):
        return default
    return int(value)


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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


def _aggregate_samples(samples: list[dict[str, Any]], bucket: int) -> dict[str, Any]:
    aggregate = _average_values(samples)
    aggregate["timestamp_ms"] = bucket
    aggregate["timestamp"] = datetime.fromtimestamp(bucket / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
    last_gpus = (samples[-1].get("gpus") or []) if samples else []
    for index, gpu in enumerate(aggregate.get("gpus") or []):
        if index < len(last_gpus):
            gpu["processes"] = list(last_gpus[index].get("processes") or [])
    return aggregate


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class TelemetryStore:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.partition_root = Path(
            os.getenv("BMS_TELEMETRY_PARTITION_ROOT", str(self.path.parent / "partitions"))
        ).expanduser().resolve()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.partition_root.mkdir(parents=True, exist_ok=True)
        with _connect(self.path) as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            if not tables:
                connection.executescript(_SCHEMA)
                tables = {
                    str(item[0])
                    for item in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                    )
                }
            if "telemetry_schema" not in tables:
                raise RuntimeError("legacy JSON telemetry store must be discarded before typed-store startup")
            _validate_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            changed_directories: set[Path] = set()
            try:
                registered = {
                    str(item["relative_path"]): (
                        str(item["content_sha256"]),
                        int(item["size_bytes"]),
                    )
                    for item in connection.execute(
                        "SELECT relative_path, content_sha256, size_bytes FROM telemetry_partitions"
                    )
                }
                for relative, (content_sha256, size_bytes) in registered.items():
                    destination = (self.partition_root / relative).resolve()
                    destination.relative_to(self.partition_root)
                    retiring = Path(f"{destination}.retiring")
                    if not destination.exists() and retiring.exists():
                        os.replace(retiring, destination)
                        changed_directories.add(destination.parent)
                    if (
                        not destination.is_file()
                        or destination.stat().st_size != size_bytes
                        or _file_sha256(destination) != content_sha256
                    ):
                        raise RuntimeError("registered telemetry partition is missing or invalid")
                for candidate in self.partition_root.rglob("*.parquet"):
                    relative = candidate.relative_to(self.partition_root).as_posix()
                    if relative not in registered:
                        candidate.unlink(missing_ok=True)
                        changed_directories.add(candidate.parent)
                for candidate in self.partition_root.rglob("*.retiring"):
                    candidate.unlink(missing_ok=True)
                    changed_directories.add(candidate.parent)
                for candidate in self.partition_root.rglob("*.tmp"):
                    candidate.unlink(missing_ok=True)
                    changed_directories.add(candidate.parent)
                for directory in changed_directories:
                    directory_fd = os.open(directory, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _validated_read_connection(self) -> sqlite3.Connection:
        connection = open_read_only(self.path)
        try:
            connection.execute("BEGIN")
            _validate_schema(connection)
        except (sqlite3.Error, RuntimeError) as error:
            connection.close()
            raise RuntimeError("telemetry store schema is unavailable or invalid") from error
        return connection

    def _base_values(self, payload: dict[str, Any], sample_count: int) -> tuple[Any, ...]:
        cpu = _object(payload.get("cpu"))
        power = _object(cpu.get("power_telemetry"))
        ram = _object(payload.get("ram"))
        timestamp_ms = int(payload["timestamp_ms"])
        timestamp = str(payload.get("timestamp") or datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z"))
        gpu_error = payload.get("gpu_error")
        return (
            timestamp_ms, timestamp, int(sample_count), None if gpu_error is None else str(gpu_error),
            str(cpu.get("name") or ""), _integer(cpu.get("cores_physical")), _integer(cpu.get("cores_logical")),
            _number(cpu.get("utilization")), _number(cpu.get("frequency_current_mhz")),
            _number(cpu.get("frequency_max_mhz")), _number(cpu.get("temperature")),
            _number(cpu.get("power_watts")), str(power.get("source") or ""),
            1 if bool(power.get("available")) else 0, str(power.get("status") or ""),
            str(power.get("message") or ""), _integer(power.get("discovered_sources")),
            _integer(power.get("readable_sources")), None if power.get("setup_hint") is None else str(power.get("setup_hint")),
            _number(ram.get("total_gb")), _number(ram.get("used_gb")), _number(ram.get("available_gb")),
            _number(ram.get("utilization")), _number(ram.get("swap_total_gb")),
            _number(ram.get("swap_used_gb")), _number(ram.get("swap_percent")),
        )

    def _insert_payload(
        self,
        connection: sqlite3.Connection,
        *,
        prefix: Literal["raw", "minute"],
        payload: dict[str, Any],
        sample_count: int,
    ) -> None:
        timestamp_ms = int(payload["timestamp_ms"])
        parent = "raw_samples" if prefix == "raw" else "minute_aggregates"
        core_table = f"{prefix}_cpu_cores"
        gpu_table = f"{prefix}_gpu_samples"
        process_table = f"{prefix}_gpu_processes"
        placeholders = ",".join("?" for _ in _BASE_INSERT_COLUMNS)
        connection.execute(
            f"INSERT INTO {parent}({','.join(_BASE_INSERT_COLUMNS)}) VALUES ({placeholders})",
            self._base_values(payload, sample_count),
        )
        cpu = _object(payload.get("cpu"))
        for core_index, utilization in enumerate(cpu.get("per_core_utilization") or []):
            connection.execute(
                f"INSERT INTO {core_table}(timestamp_ms, core_index, utilization) VALUES (?, ?, ?)",
                (timestamp_ms, core_index, float(utilization)),
            )
        for gpu_value in payload.get("gpus") or []:
            gpu = _object(gpu_value)
            gpu_index = int(gpu.get("index", 0))
            values = (
                timestamp_ms, gpu_index, str(gpu.get("name") or ""), _number(gpu.get("utilization")),
                _number(gpu.get("memory_utilization")), _number(gpu.get("memory_used_mb")),
                _number(gpu.get("memory_total_mb")), _number(gpu.get("reserved_memory_mb")),
                _number(gpu.get("power_draw_w")), _number(gpu.get("power_limit_w")),
                _number(gpu.get("min_power_watts")), _number(gpu.get("default_power_watts")),
                _number(gpu.get("max_power_watts")), _number(gpu.get("temperature")),
                _number(gpu.get("fan_speed")), _number(gpu.get("clock_graphics_mhz")),
                _number(gpu.get("clock_memory_mhz")), _number(gpu.get("clock_max_graphics_mhz")),
                _number(gpu.get("clock_max_memory_mhz")),
            )
            connection.execute(
                f"INSERT INTO {gpu_table}({','.join(_GPU_COLUMNS)}) VALUES ({','.join('?' for _ in _GPU_COLUMNS)})",
                values,
            )
            for process_value in gpu.get("processes") or []:
                process = _object(process_value)
                connection.execute(
                    f"INSERT INTO {process_table}(timestamp_ms, gpu_index, pid, name, memory_mb) VALUES (?, ?, ?, ?, ?)",
                    (timestamp_ms, gpu_index, int(process["pid"]), str(process.get("name") or ""), int(process.get("memory_mb") or 0)),
                )

    def append_sample(self, payload: dict[str, Any]) -> None:
        with _connect(self.path, writer=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._insert_payload(connection, prefix="raw", payload=payload, sample_count=1)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _payloads_for_rows(
        self,
        connection: sqlite3.Connection,
        rows: list[sqlite3.Row],
        *,
        prefix: Literal["raw", "minute"],
    ) -> dict[int, dict[str, Any]]:
        if not rows:
            return {}
        timestamps = [int(row["timestamp_ms"]) for row in rows]
        lower, upper = min(timestamps), max(timestamps)
        wanted = set(timestamps)
        cores: dict[int, list[float]] = {timestamp: [] for timestamp in timestamps}
        for row in connection.execute(
            f"SELECT timestamp_ms, core_index, utilization FROM {prefix}_cpu_cores WHERE timestamp_ms BETWEEN ? AND ? ORDER BY timestamp_ms, core_index",
            (lower, upper),
        ):
            timestamp = int(row["timestamp_ms"])
            if timestamp in wanted:
                cores[timestamp].append(float(row["utilization"]))
        processes: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for row in connection.execute(
            f"SELECT * FROM {prefix}_gpu_processes WHERE timestamp_ms BETWEEN ? AND ? ORDER BY timestamp_ms, gpu_index, pid",
            (lower, upper),
        ):
            timestamp = int(row["timestamp_ms"])
            if timestamp in wanted:
                processes.setdefault((timestamp, int(row["gpu_index"])), []).append(
                    {"pid": int(row["pid"]), "name": str(row["name"]), "memory_mb": int(row["memory_mb"])}
                )
        gpus: dict[int, list[dict[str, Any]]] = {timestamp: [] for timestamp in timestamps}
        for row in connection.execute(
            f"SELECT * FROM {prefix}_gpu_samples WHERE timestamp_ms BETWEEN ? AND ? ORDER BY timestamp_ms, gpu_index",
            (lower, upper),
        ):
            timestamp = int(row["timestamp_ms"])
            if timestamp not in wanted:
                continue
            gpu_index = int(row["gpu_index"])
            gpus[timestamp].append(
                {
                    "index": gpu_index,
                    "name": str(row["name"]),
                    "utilization": row["utilization"],
                    "memory_utilization": row["memory_utilization"],
                    "memory_used_mb": row["memory_used_mb"],
                    "memory_total_mb": row["memory_total_mb"],
                    "reserved_memory_mb": row["reserved_memory_mb"],
                    "power_draw_w": row["power_draw_w"],
                    "power_limit_w": row["power_limit_w"],
                    "min_power_watts": row["min_power_watts"],
                    "default_power_watts": row["default_power_watts"],
                    "max_power_watts": row["max_power_watts"],
                    "temperature": row["temperature"],
                    "fan_speed": row["fan_speed"],
                    "clock_graphics_mhz": row["clock_graphics_mhz"],
                    "clock_memory_mhz": row["clock_memory_mhz"],
                    "clock_max_graphics_mhz": row["clock_max_graphics_mhz"],
                    "clock_max_memory_mhz": row["clock_max_memory_mhz"],
                    "processes": processes.get((timestamp, gpu_index), []),
                }
            )
        payloads: dict[int, dict[str, Any]] = {}
        for row in rows:
            timestamp = int(row["timestamp_ms"])
            payloads[timestamp] = {
                "timestamp_ms": timestamp,
                "timestamp": str(row["timestamp"]),
                "cpu": {
                    "name": str(row["cpu_name"]),
                    "cores_physical": int(row["cpu_cores_physical"]),
                    "cores_logical": int(row["cpu_cores_logical"]),
                    "utilization": row["cpu_utilization"],
                    "per_core_utilization": cores[timestamp],
                    "frequency_current_mhz": row["cpu_frequency_current_mhz"],
                    "frequency_max_mhz": row["cpu_frequency_max_mhz"],
                    "temperature": row["cpu_temperature"],
                    "power_watts": row["cpu_power_watts"],
                    "power_telemetry": {
                        "source": str(row["cpu_power_source"]),
                        "available": bool(row["cpu_power_available"]),
                        "status": str(row["cpu_power_status"]),
                        "message": str(row["cpu_power_message"]),
                        "discovered_sources": int(row["cpu_power_discovered_sources"]),
                        "readable_sources": int(row["cpu_power_readable_sources"]),
                        "setup_hint": row["cpu_power_setup_hint"],
                    },
                },
                "ram": {
                    "total_gb": row["ram_total_gb"],
                    "used_gb": row["ram_used_gb"],
                    "available_gb": row["ram_available_gb"],
                    "utilization": row["ram_utilization"],
                    "swap_total_gb": row["ram_swap_total_gb"],
                    "swap_used_gb": row["ram_swap_used_gb"],
                    "swap_percent": row["ram_swap_percent"],
                },
                "gpus": gpus[timestamp],
                "gpu_error": row["gpu_error"],
            }
        return payloads

    def _read_payloads(
        self,
        connection: sqlite3.Connection,
        *,
        prefix: Literal["raw", "minute"],
        start_ms: int,
        end_ms: int,
        limit: int | None = None,
    ) -> tuple[list[sqlite3.Row], dict[int, dict[str, Any]]]:
        parent = "raw_samples" if prefix == "raw" else "minute_aggregates"
        sql = f"SELECT * FROM {parent} WHERE timestamp_ms >= ? AND timestamp_ms < ? ORDER BY timestamp_ms"
        parameters: tuple[Any, ...] = (int(start_ms), int(end_ms))
        if limit is not None:
            sql = f"SELECT * FROM ({sql}) ORDER BY timestamp_ms DESC LIMIT ?"
            parameters = (*parameters, int(limit))
        rows = connection.execute(sql, parameters).fetchall()
        if limit is not None:
            rows = list(reversed(rows))
        return rows, self._payloads_for_rows(connection, rows, prefix=prefix)

    def _partition_row(self, payload: dict[str, Any]) -> dict[str, Any]:
        cpu = payload["cpu"]
        power = cpu["power_telemetry"]
        ram = payload["ram"]
        return {
            "timestamp_ms": int(payload["timestamp_ms"]),
            "timestamp": str(payload["timestamp"]),
            "gpu_error": payload.get("gpu_error"),
            "cpu_name": str(cpu.get("name") or ""),
            "cpu_cores_physical": int(cpu.get("cores_physical") or 0),
            "cpu_cores_logical": int(cpu.get("cores_logical") or 0),
            "cpu_utilization": cpu.get("utilization"),
            "cpu_per_core_utilization": [float(value) for value in cpu.get("per_core_utilization") or []],
            "cpu_frequency_current_mhz": cpu.get("frequency_current_mhz"),
            "cpu_frequency_max_mhz": cpu.get("frequency_max_mhz"),
            "cpu_temperature": cpu.get("temperature"),
            "cpu_power_watts": cpu.get("power_watts"),
            "cpu_power_source": str(power.get("source") or ""),
            "cpu_power_available": bool(power.get("available")),
            "cpu_power_status": str(power.get("status") or ""),
            "cpu_power_message": str(power.get("message") or ""),
            "cpu_power_discovered_sources": int(power.get("discovered_sources") or 0),
            "cpu_power_readable_sources": int(power.get("readable_sources") or 0),
            "cpu_power_setup_hint": power.get("setup_hint"),
            "ram_total_gb": ram.get("total_gb"),
            "ram_used_gb": ram.get("used_gb"),
            "ram_available_gb": ram.get("available_gb"),
            "ram_utilization": ram.get("utilization"),
            "ram_swap_total_gb": ram.get("swap_total_gb"),
            "ram_swap_used_gb": ram.get("swap_used_gb"),
            "ram_swap_percent": ram.get("swap_percent"),
            "gpus": payload.get("gpus") or [],
        }

    def _finalize_completed_hours(self, connection: sqlite3.Connection, now_ms: int) -> int:
        open_hour = (int(now_ms) // 3_600_000) * 3_600_000
        buckets = connection.execute(
            """SELECT DISTINCT (timestamp_ms / 3600000) * 3600000 AS hour_ms
            FROM raw_samples
            WHERE timestamp_ms < ?
              AND (timestamp_ms / 3600000) * 3600000 NOT IN
                  (SELECT partition_start_ms FROM telemetry_partitions)
            ORDER BY hour_ms""",
            (open_hour,),
        ).fetchall()
        finalized = 0
        for bucket_row in buckets:
            hour = int(bucket_row["hour_ms"])
            relative = f"raw/hour-{hour}.parquet"
            destination = (self.partition_root / relative).resolve()
            destination.relative_to(self.partition_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary: Path | None = None
            content_sha256: str | None = None
            published = False
            try:
                connection.execute("BEGIN IMMEDIATE")
                if connection.execute(
                    "SELECT 1 FROM telemetry_partitions WHERE partition_start_ms = ?",
                    (hour,),
                ).fetchone() is not None:
                    connection.rollback()
                    continue
                rows, payloads = self._read_payloads(
                    connection, prefix="raw", start_ms=hour, end_ms=hour + 3_600_000
                )
                if not rows:
                    connection.rollback()
                    continue
                table = pa.Table.from_pylist(
                    [self._partition_row(payloads[int(row["timestamp_ms"])]) for row in rows],
                    schema=_RAW_PARTITION_SCHEMA,
                )
                with tempfile.NamedTemporaryFile(
                    prefix=f".{destination.name}.",
                    suffix=".tmp",
                    dir=destination.parent,
                    delete=False,
                ) as temporary_handle:
                    temporary = Path(temporary_handle.name)
                pq.write_table(table, temporary, compression="zstd")
                with temporary.open("rb") as handle:
                    os.fsync(handle.fileno())
                content_sha256 = _file_sha256(temporary)
                size_bytes = temporary.stat().st_size
                os.replace(temporary, destination)
                published = True
                directory_fd = os.open(destination.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                connection.execute(
                    """INSERT INTO telemetry_partitions(
                        partition_start_ms, partition_end_ms, relative_path, content_sha256,
                        size_bytes, row_count, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        hour, hour + 3_600_000, relative, content_sha256,
                        size_bytes, len(rows), datetime.now(timezone.utc).isoformat(),
                    ),
                )
                connection.commit()
                finalized += 1
            except BaseException:
                try:
                    if published and connection.in_transaction:
                        destination.unlink(missing_ok=True)
                        directory_fd = os.open(destination.parent, os.O_RDONLY)
                        try:
                            os.fsync(directory_fd)
                        finally:
                            os.close(directory_fd)
                finally:
                    connection.rollback()
                raise
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return finalized

    def finalize_completed_minutes(self, now_ms: int) -> int:
        open_bucket = (int(now_ms) // 60_000) * 60_000
        finalized = 0
        with _connect(self.path, writer=True, publisher=True) as connection:
            while True:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    bucket_row = connection.execute(
                        """SELECT MIN((raw.timestamp_ms / 60000) * 60000) AS bucket_ms
                        FROM raw_samples AS raw
                        WHERE raw.timestamp_ms < ?
                          AND NOT EXISTS (
                              SELECT 1 FROM minute_aggregates AS minute
                              WHERE minute.timestamp_ms = (raw.timestamp_ms / 60000) * 60000
                          )""",
                        (open_bucket,),
                    ).fetchone()
                    if bucket_row is None or bucket_row["bucket_ms"] is None:
                        connection.rollback()
                        break
                    bucket = int(bucket_row["bucket_ms"])
                    rows, payloads = self._read_payloads(
                        connection, prefix="raw", start_ms=bucket, end_ms=bucket + 60_000
                    )
                    samples = [payloads[int(row["timestamp_ms"])] for row in rows]
                    if not samples:
                        connection.rollback()
                        continue
                    aggregate = _aggregate_samples(samples, bucket)
                    self._insert_payload(
                        connection, prefix="minute", payload=aggregate, sample_count=len(samples)
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                finalized += 1
            self._finalize_completed_hours(connection, int(now_ms))
        return finalized

    def apply_retention(self, now_ms: int) -> dict[str, int]:
        raw_cutoff = int(now_ms) - RAW_RETENTION_SECONDS * 1000
        minute_cutoff = int(now_ms) - AGGREGATE_RETENTION_SECONDS * 1000
        retiring_paths: list[tuple[Path, Path]] = []
        with _connect(self.path, maintenance=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                raw_deleted = connection.execute(
                    "DELETE FROM raw_samples WHERE timestamp_ms < ?", (raw_cutoff,)
                ).rowcount
                minute_deleted = connection.execute(
                    "DELETE FROM minute_aggregates WHERE timestamp_ms < ?", (minute_cutoff,)
                ).rowcount
                rows = connection.execute(
                    "SELECT relative_path FROM telemetry_partitions WHERE partition_end_ms < ?",
                    (minute_cutoff,),
                ).fetchall()
                changed_directories: set[Path] = set()
                for row in rows:
                    path = (self.partition_root / str(row["relative_path"])).resolve()
                    path.relative_to(self.partition_root)
                    retiring = Path(f"{path}.retiring")
                    if not path.is_file() or retiring.exists():
                        raise RuntimeError("telemetry partition retention state is invalid")
                    os.replace(path, retiring)
                    retiring_paths.append((path, retiring))
                    changed_directories.add(path.parent)
                for directory in changed_directories:
                    directory_fd = os.open(directory, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                connection.execute(
                    "DELETE FROM telemetry_partitions WHERE partition_end_ms < ?", (minute_cutoff,)
                )
                connection.commit()
            except Exception:
                connection.rollback()
                for path, retiring in retiring_paths:
                    if retiring.exists():
                        os.replace(retiring, path)
                for directory in {path.parent for path, _retiring in retiring_paths}:
                    directory_fd = os.open(directory, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                raise
        for _path, retiring in retiring_paths:
            retiring.unlink(missing_ok=True)
        for directory in {path.parent for path, _retiring in retiring_paths}:
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
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
        prefix: Literal["raw", "minute"] = resolution
        with self._validated_read_connection() as connection:
            rows, payloads = self._read_payloads(
                connection,
                prefix=prefix,
                start_ms=int(start_ms),
                end_ms=int(end_ms),
                limit=int(limit),
            )
        return [
            {
                "timestamp_ms": int(row["timestamp_ms"]),
                "sample_count": int(row["sample_count"]),
                "payload": payloads[int(row["timestamp_ms"])],
            }
            for row in rows
        ]

    def read_freshness(self, *, now_ms: int | None = None, stale_after_ms: int) -> dict[str, Any]:
        if int(stale_after_ms) <= 0:
            raise ValueError("telemetry freshness threshold must be positive")
        with self._validated_read_connection() as connection:
            latest = connection.execute("SELECT MAX(timestamp_ms) FROM raw_samples").fetchone()[0]
        if latest is None:
            return {
                "ready": False,
                "status": "empty",
                "latest_timestamp_ms": None,
                "age_ms": None,
                "stale_after_ms": int(stale_after_ms),
            }
        latest_timestamp_ms = int(latest)
        observed_now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        age_ms = observed_now_ms - latest_timestamp_ms
        if age_ms < 0:
            return {
                "ready": False,
                "status": "future",
                "latest_timestamp_ms": latest_timestamp_ms,
                "age_ms": age_ms,
                "stale_after_ms": int(stale_after_ms),
            }
        ready = age_ms <= int(stale_after_ms)
        return {
            "ready": ready,
            "status": "fresh" if ready else "stale",
            "latest_timestamp_ms": latest_timestamp_ms,
            "age_ms": age_ms,
            "stale_after_ms": int(stale_after_ms),
        }

    def insert_minute_for_test(self, bucket_ms: int, payload: dict[str, Any], sample_count: int) -> None:
        value = dict(payload)
        value["timestamp_ms"] = int(bucket_ms)
        value["timestamp"] = str(
            value.get("timestamp")
            or datetime.fromtimestamp(int(bucket_ms) / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
        )
        with _connect(self.path, writer=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._insert_payload(
                    connection, prefix="minute", payload=value, sample_count=int(sample_count)
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
