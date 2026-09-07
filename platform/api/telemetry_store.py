from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
import time
from typing import Any, Literal

import pyarrow as pa
import pyarrow.parquet as pq

RAW_RETENTION_SECONDS = 7 * 24 * 60 * 60
HOT_RAW_RETENTION_SECONDS = 60 * 60
AGGREGATE_RETENTION_SECONDS = 90 * 24 * 60 * 60
MAX_HISTORY_POINTS = 10_000
TELEMETRY_FRESHNESS_STALE_AFTER_MS = 15_000
TELEMETRY_SCHEMA_VERSION = 3
CORE_VECTOR_FORMAT_F64_LE = 1
PROCESS_BLOB_FORMAT_V1 = 1

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

_V2_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
BEGIN IMMEDIATE;
PRAGMA user_version=2;
CREATE TABLE telemetry_schema (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    version INTEGER NOT NULL
) WITHOUT ROWID;
INSERT INTO telemetry_schema(singleton, version) VALUES (1, 2);
CREATE TABLE raw_samples (
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
) WITHOUT ROWID;
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
CREATE TABLE minute_aggregates (
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
) WITHOUT ROWID;
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

_BASE_COLUMNS = """
    timestamp_ms INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    sample_count INTEGER NOT NULL CHECK(sample_count > 0),
    hardware_profile_id INTEGER NOT NULL REFERENCES telemetry_hardware_profiles(profile_id),
    gpu_error TEXT,
    cpu_per_core_format INTEGER NOT NULL CHECK(cpu_per_core_format = 1),
    cpu_per_core_count INTEGER NOT NULL CHECK(cpu_per_core_count >= 0),
    cpu_per_core_f64 BLOB NOT NULL CHECK(
        typeof(cpu_per_core_f64) = 'blob'
        AND length(cpu_per_core_f64) = cpu_per_core_count * 8
    ),
    cpu_utilization REAL,
    cpu_frequency_current_mhz REAL,
    cpu_temperature REAL,
    cpu_power_watts REAL,
    cpu_power_source TEXT NOT NULL,
    cpu_power_available INTEGER NOT NULL CHECK(cpu_power_available IN (0, 1)),
    cpu_power_status TEXT NOT NULL,
    cpu_power_message TEXT NOT NULL,
    cpu_power_discovered_sources INTEGER NOT NULL,
    cpu_power_readable_sources INTEGER NOT NULL,
    cpu_power_setup_hint TEXT,
    ram_used_gb REAL,
    ram_available_gb REAL,
    ram_utilization REAL,
    ram_swap_used_gb REAL,
    ram_swap_percent REAL
"""

_HARDWARE_GPU_STATIC_COLUMNS = """
    profile_id INTEGER NOT NULL REFERENCES telemetry_hardware_profiles(profile_id) ON DELETE CASCADE,
    gpu_index INTEGER NOT NULL,
    name TEXT NOT NULL,
    memory_total_mb REAL,
    min_power_watts REAL,
    default_power_watts REAL,
    max_power_watts REAL,
    PRIMARY KEY(profile_id, gpu_index)
"""

_GPU_DYNAMIC_COLUMNS = """
    timestamp_ms INTEGER NOT NULL,
    gpu_index INTEGER NOT NULL,
    utilization REAL,
    memory_utilization REAL,
    memory_used_mb REAL,
    reserved_memory_mb REAL,
    power_draw_w REAL,
    power_limit_w REAL,
    temperature REAL,
    fan_speed REAL,
    clock_graphics_mhz REAL,
    clock_memory_mhz REAL,
    clock_max_graphics_mhz REAL,
    clock_max_memory_mhz REAL,
    process_format INTEGER NOT NULL CHECK(process_format = 1),
    process_count INTEGER NOT NULL CHECK(process_count >= 0),
    process_blob BLOB NOT NULL CHECK(
        typeof(process_blob) = 'blob'
        AND length(process_blob) = process_count * 24
    ),
    PRIMARY KEY(timestamp_ms, gpu_index)
"""


def _guard_sql(table: str, insert_authority: str) -> str:
    return f"""
CREATE TRIGGER {table}_guard_update BEFORE UPDATE ON {table}
BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;
CREATE TRIGGER {table}_guard_delete BEFORE DELETE ON {table}
WHEN telemetry_retention_authorized() = 0
BEGIN SELECT RAISE(ABORT, '{table} retention authorization required'); END;
CREATE TRIGGER {table}_guard_insert_authority BEFORE INSERT ON {table}
WHEN {insert_authority}() = 0
BEGIN SELECT RAISE(ABORT, '{table} insert authority required'); END;
"""


_GUARDED_WRITER_TABLES = (
    "telemetry_hardware_profiles",
    "telemetry_hardware_gpus",
    "telemetry_process_names",
    "raw_samples",
    "raw_gpu_samples",
    "minute_aggregates",
    "minute_gpu_samples",
)
_GUARD_SCHEMA = "".join(
    _guard_sql(table, "telemetry_writer_authorized")
    for table in _GUARDED_WRITER_TABLES
) + _guard_sql("telemetry_partitions", "telemetry_partition_authorized")

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
CREATE TABLE telemetry_hardware_profiles (
    profile_id INTEGER PRIMARY KEY,
    profile_sha256 TEXT NOT NULL UNIQUE CHECK(length(profile_sha256) = 64),
    created_at TEXT NOT NULL,
    cpu_name TEXT NOT NULL,
    cpu_cores_physical INTEGER NOT NULL,
    cpu_cores_logical INTEGER NOT NULL,
    cpu_frequency_max_mhz REAL,
    ram_total_gb REAL,
    ram_swap_total_gb REAL
);
CREATE TABLE telemetry_hardware_gpus ({_HARDWARE_GPU_STATIC_COLUMNS}) WITHOUT ROWID;
CREATE TABLE telemetry_process_names (
    process_name_id INTEGER PRIMARY KEY,
    name_sha256 TEXT NOT NULL UNIQUE CHECK(length(name_sha256) = 64),
    name TEXT NOT NULL
);
CREATE TABLE raw_samples ({_BASE_COLUMNS}) WITHOUT ROWID;
CREATE TABLE raw_gpu_samples (
    {_GPU_DYNAMIC_COLUMNS},
    FOREIGN KEY(timestamp_ms) REFERENCES raw_samples(timestamp_ms) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE minute_aggregates ({_BASE_COLUMNS}) WITHOUT ROWID;
CREATE TABLE minute_gpu_samples (
    {_GPU_DYNAMIC_COLUMNS},
    FOREIGN KEY(timestamp_ms) REFERENCES minute_aggregates(timestamp_ms) ON DELETE CASCADE
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
{_GUARD_SCHEMA}
CREATE TRIGGER raw_gpu_samples_guard_profile BEFORE INSERT ON raw_gpu_samples
WHEN NOT EXISTS (
    SELECT 1
    FROM raw_samples AS sample
    JOIN telemetry_hardware_gpus AS gpu
      ON gpu.profile_id = sample.hardware_profile_id
     AND gpu.gpu_index = NEW.gpu_index
    WHERE sample.timestamp_ms = NEW.timestamp_ms
)
BEGIN SELECT RAISE(ABORT, 'raw GPU sample does not match its hardware profile'); END;
CREATE TRIGGER minute_gpu_samples_guard_profile BEFORE INSERT ON minute_gpu_samples
WHEN NOT EXISTS (
    SELECT 1
    FROM minute_aggregates AS sample
    JOIN telemetry_hardware_gpus AS gpu
      ON gpu.profile_id = sample.hardware_profile_id
     AND gpu.gpu_index = NEW.gpu_index
    WHERE sample.timestamp_ms = NEW.timestamp_ms
)
BEGIN SELECT RAISE(ABORT, 'minute GPU sample does not match its hardware profile'); END;
CREATE TRIGGER raw_samples_guard_core_vector BEFORE INSERT ON raw_samples
WHEN NEW.cpu_per_core_count != (
    SELECT cpu_cores_logical FROM telemetry_hardware_profiles
    WHERE profile_id = NEW.hardware_profile_id
)
BEGIN SELECT RAISE(ABORT, 'raw CPU-core vector does not match its hardware profile'); END;
CREATE TRIGGER minute_aggregates_guard_core_vector BEFORE INSERT ON minute_aggregates
WHEN NEW.cpu_per_core_count != (
    SELECT cpu_cores_logical FROM telemetry_hardware_profiles
    WHERE profile_id = NEW.hardware_profile_id
)
BEGIN SELECT RAISE(ABORT, 'minute CPU-core vector does not match its hardware profile'); END;
CREATE TRIGGER raw_samples_guard_finalized_hour BEFORE INSERT ON raw_samples
WHEN EXISTS (
    SELECT 1 FROM telemetry_partitions
    WHERE NEW.timestamp_ms >= partition_start_ms AND NEW.timestamp_ms < partition_end_ms
) OR EXISTS (
    SELECT 1 FROM minute_aggregates
    WHERE timestamp_ms = (NEW.timestamp_ms / 60000) * 60000
)
BEGIN SELECT RAISE(ABORT, 'raw sample belongs to a finalized telemetry period'); END;
COMMIT;
"""

_EXPECTED_SCHEMA_OBJECTS: dict[tuple[str, str], str] | None = None
_EXPECTED_V2_SCHEMA_OBJECTS: dict[tuple[str, str], str] | None = None


def _normalize_schema_sql(value: Any) -> str:
    normalized = " ".join(str(value).split())
    for token in ("(", ")", ","):
        normalized = normalized.replace(f" {token}", token).replace(f"{token} ", token)
    return normalized


def _schema_objects(connection: sqlite3.Connection) -> dict[tuple[str, str], str]:
    return {
        (str(row[0]), str(row[1])): _normalize_schema_sql(row[2])
        for row in connection.execute(
            """SELECT type, name, sql FROM sqlite_master
            WHERE type IN ('table', 'trigger')
              AND name NOT LIKE 'sqlite_%'
            ORDER BY type, name"""
        )
    }


def _expected_v2_schema_objects() -> dict[tuple[str, str], str]:
    global _EXPECTED_V2_SCHEMA_OBJECTS
    if _EXPECTED_V2_SCHEMA_OBJECTS is None:
        connection = sqlite3.connect(":memory:", isolation_level=None)
        connection.create_function("telemetry_retention_authorized", 0, lambda: 0)
        connection.create_function("telemetry_writer_authorized", 0, lambda: 0)
        connection.create_function("telemetry_partition_authorized", 0, lambda: 0)
        try:
            connection.executescript(_V2_SCHEMA)
            _EXPECTED_V2_SCHEMA_OBJECTS = _schema_objects(connection)
        finally:
            connection.close()
    return dict(_EXPECTED_V2_SCHEMA_OBJECTS)


def _expected_schema_objects() -> dict[tuple[str, str], str]:
    global _EXPECTED_SCHEMA_OBJECTS
    if _EXPECTED_SCHEMA_OBJECTS is None:
        connection = sqlite3.connect(":memory:", isolation_level=None)
        try:
            connection.executescript(_SCHEMA)
            _EXPECTED_SCHEMA_OBJECTS = _schema_objects(connection)
        finally:
            connection.close()
    return dict(_EXPECTED_SCHEMA_OBJECTS)


def _validate_schema(
    connection: sqlite3.Connection,
    *,
    full_integrity: bool,
) -> None:
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
    ):
        raise RuntimeError("unsupported or incomplete typed telemetry store schema")
    if full_integrity:
        integrity_rows = [tuple(item) for item in connection.execute("PRAGMA integrity_check")]
        if (
            integrity_rows != [("ok",)]
            or connection.execute("PRAGMA foreign_key_check").fetchone() is not None
        ):
            raise RuntimeError("unsupported or incomplete typed telemetry store schema")
        _validate_process_name_bindings(connection)

_BASE_INSERT_COLUMNS = (
    "timestamp_ms", "timestamp", "sample_count", "hardware_profile_id", "gpu_error",
    "cpu_per_core_format", "cpu_per_core_count", "cpu_per_core_f64",
    "cpu_utilization", "cpu_frequency_current_mhz", "cpu_temperature",
    "cpu_power_watts", "cpu_power_source", "cpu_power_available",
    "cpu_power_status", "cpu_power_message", "cpu_power_discovered_sources",
    "cpu_power_readable_sources", "cpu_power_setup_hint", "ram_used_gb",
    "ram_available_gb", "ram_utilization", "ram_swap_used_gb", "ram_swap_percent",
)
_PROFILE_COLUMNS = (
    "cpu_name", "cpu_cores_physical", "cpu_cores_logical", "cpu_frequency_max_mhz",
    "ram_total_gb", "ram_swap_total_gb",
)
_PROFILE_GPU_COLUMNS = (
    "profile_id", "gpu_index", "name", "memory_total_mb",
    "min_power_watts", "default_power_watts", "max_power_watts",
)
_GPU_COLUMNS = (
    "timestamp_ms", "gpu_index", "utilization", "memory_utilization",
    "memory_used_mb", "reserved_memory_mb", "power_draw_w", "power_limit_w",
    "temperature", "fan_speed", "clock_graphics_mhz", "clock_memory_mhz",
    "clock_max_graphics_mhz", "clock_max_memory_mhz", "process_format",
    "process_count", "process_blob",
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


def _pack_core_vector(values: list[Any]) -> bytes:
    numeric = [float(value) for value in values]
    return struct.pack(f"<{len(numeric)}d", *numeric)


def _unpack_core_vector(value: bytes, count: int, vector_format: int) -> list[float]:
    if int(vector_format) != CORE_VECTOR_FORMAT_F64_LE:
        raise RuntimeError("unsupported telemetry CPU-core vector format")
    expected_size = int(count) * 8
    if len(value) != expected_size:
        raise RuntimeError("invalid telemetry CPU-core vector length")
    if count == 0:
        return []
    return list(struct.unpack(f"<{int(count)}d", value))


_PROCESS_RECORD = struct.Struct("<qqq")


def _pack_process_records(values: list[tuple[int, int, int]]) -> tuple[int, int, bytes]:
    records = sorted(values)
    if len({pid for pid, _memory_mb, _name_id in records}) != len(records):
        raise ValueError("telemetry GPU process PIDs must be unique per device sample")
    return (
        PROCESS_BLOB_FORMAT_V1,
        len(records),
        b"".join(_PROCESS_RECORD.pack(pid, memory_mb, process_name_id) for pid, memory_mb, process_name_id in records),
    )


def _unpack_process_records(
    value: bytes,
    count: int,
    process_format: int,
) -> list[tuple[int, int, int]]:
    if int(process_format) != PROCESS_BLOB_FORMAT_V1:
        raise RuntimeError("unsupported telemetry GPU-process format")
    payload = bytes(value)
    expected_size = int(count) * _PROCESS_RECORD.size
    if len(payload) != expected_size:
        raise RuntimeError("invalid telemetry GPU-process blob length")
    return [
        _PROCESS_RECORD.unpack_from(payload, offset)
        for offset in range(0, expected_size, _PROCESS_RECORD.size)
    ]


def _process_name_digest(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


def _validate_process_name_bindings(connection: sqlite3.Connection) -> None:
    process_name_ids = {
        int(row[0])
        for row in connection.execute("SELECT process_name_id FROM telemetry_process_names")
    }
    for table in ("raw_gpu_samples", "minute_gpu_samples"):
        for row in connection.execute(
            f"SELECT process_format, process_count, process_blob FROM {table}"
        ):
            records = _unpack_process_records(bytes(row[2]), int(row[1]), int(row[0]))
            if any(process_name_id not in process_name_ids for _pid, _memory_mb, process_name_id in records):
                raise RuntimeError("telemetry GPU process name is missing")


def _canonical_profile_part(value: Any) -> bytes:
    if value is None:
        return b"n"
    if isinstance(value, bool):
        return b"b1" if value else b"b0"
    if isinstance(value, int):
        return b"i" + str(value).encode("ascii") + b";"
    if isinstance(value, float):
        return b"f" + struct.pack("<d", value)
    encoded = str(value).encode("utf-8")
    return b"s" + len(encoded).to_bytes(4, "little") + encoded


def _hardware_profile(payload: dict[str, Any]) -> tuple[tuple[Any, ...], list[tuple[Any, ...]], str]:
    cpu = _object(payload.get("cpu"))
    ram = _object(payload.get("ram"))
    profile = (
        str(cpu.get("name") or ""),
        _integer(cpu.get("cores_physical")),
        _integer(cpu.get("cores_logical")),
        _number(cpu.get("frequency_max_mhz")),
        _number(ram.get("total_gb")),
        _number(ram.get("swap_total_gb")),
    )
    gpus = sorted(
        (
            int(gpu.get("index", 0)),
            str(gpu.get("name") or ""),
            _number(gpu.get("memory_total_mb")),
            _number(gpu.get("min_power_watts")),
            _number(gpu.get("default_power_watts")),
            _number(gpu.get("max_power_watts")),
        )
        for gpu in (_object(value) for value in (payload.get("gpus") or []))
    )
    digest = hashlib.sha256()
    for value in (*profile, len(gpus)):
        digest.update(_canonical_profile_part(value))
    for gpu in gpus:
        for value in gpu:
            digest.update(_canonical_profile_part(value))
    return profile, gpus, digest.hexdigest()


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


_V2_BASE_COLUMN_NAMES = (
    "timestamp_ms", "timestamp", "sample_count", "gpu_error", "cpu_name",
    "cpu_cores_physical", "cpu_cores_logical", "cpu_utilization",
    "cpu_frequency_current_mhz", "cpu_frequency_max_mhz", "cpu_temperature",
    "cpu_power_watts", "cpu_power_source", "cpu_power_available",
    "cpu_power_status", "cpu_power_message", "cpu_power_discovered_sources",
    "cpu_power_readable_sources", "cpu_power_setup_hint", "ram_total_gb",
    "ram_used_gb", "ram_available_gb", "ram_utilization", "ram_swap_total_gb",
    "ram_swap_used_gb", "ram_swap_percent",
)
_V2_GPU_COLUMN_NAMES = (
    "timestamp_ms", "gpu_index", "name", "utilization", "memory_utilization",
    "memory_used_mb", "memory_total_mb", "reserved_memory_mb", "power_draw_w",
    "power_limit_w", "min_power_watts", "default_power_watts", "max_power_watts",
    "temperature", "fan_speed", "clock_graphics_mhz", "clock_memory_mhz",
    "clock_max_graphics_mhz", "clock_max_memory_mhz",
)
_V2_TABLE_COLUMNS = {
    "telemetry_schema": ("singleton", "version"),
    "raw_samples": _V2_BASE_COLUMN_NAMES,
    "raw_cpu_cores": ("timestamp_ms", "core_index", "utilization"),
    "raw_gpu_samples": _V2_GPU_COLUMN_NAMES,
    "raw_gpu_processes": ("timestamp_ms", "gpu_index", "pid", "name", "memory_mb"),
    "minute_aggregates": _V2_BASE_COLUMN_NAMES,
    "minute_cpu_cores": ("timestamp_ms", "core_index", "utilization"),
    "minute_gpu_samples": _V2_GPU_COLUMN_NAMES,
    "minute_gpu_processes": ("timestamp_ms", "gpu_index", "pid", "name", "memory_mb"),
    "telemetry_partitions": (
        "partition_start_ms", "partition_end_ms", "relative_path", "content_sha256",
        "size_bytes", "row_count", "created_at",
    ),
}


def _validate_v2_schema(connection: sqlite3.Connection) -> None:
    marker = [
        tuple(row)
        for row in connection.execute(
            "SELECT version FROM telemetry_schema WHERE singleton = 1"
        )
    ]
    if (
        marker != [(2,)]
        or int(connection.execute("PRAGMA user_version").fetchone()[0]) != 2
        or _schema_objects(connection) != _expected_v2_schema_objects()
        or connection.execute("PRAGMA foreign_key_check").fetchone() is not None
    ):
        raise RuntimeError("unsupported or incomplete typed telemetry v2 schema")
    for parent, cores in (
        ("raw_samples", "raw_cpu_cores"),
        ("minute_aggregates", "minute_cpu_cores"),
    ):
        mismatch = connection.execute(
            f"""SELECT parent.timestamp_ms
                FROM {parent} AS parent
                LEFT JOIN {cores} AS core ON core.timestamp_ms = parent.timestamp_ms
                GROUP BY parent.timestamp_ms, parent.cpu_cores_logical
                HAVING COUNT(core.core_index) != parent.cpu_cores_logical
                LIMIT 1"""
        ).fetchone()
        if mismatch is not None:
            raise RuntimeError("unsupported or incomplete typed telemetry v2 schema cardinality")


def _read_v2_batch(
    connection: sqlite3.Connection,
    *,
    prefix: Literal["raw", "minute"],
    after_timestamp_ms: int | None,
    limit: int = 500,
    namespace: str = "",
) -> list[tuple[dict[str, Any], int]]:
    parent = f"{namespace}{'raw_samples' if prefix == 'raw' else 'minute_aggregates'}"
    if after_timestamp_ms is None:
        rows = connection.execute(
            f"SELECT * FROM {parent} ORDER BY timestamp_ms LIMIT ?",
            (int(limit),),
        ).fetchall()
    else:
        rows = connection.execute(
            f"SELECT * FROM {parent} WHERE timestamp_ms > ? ORDER BY timestamp_ms LIMIT ?",
            (int(after_timestamp_ms), int(limit)),
        ).fetchall()
    if not rows:
        return []
    lower, upper = int(rows[0]["timestamp_ms"]), int(rows[-1]["timestamp_ms"])
    wanted = {int(row["timestamp_ms"]) for row in rows}
    cores: dict[int, list[float]] = {timestamp: [] for timestamp in wanted}
    for row in connection.execute(
        f"SELECT timestamp_ms, core_index, utilization FROM {namespace}{prefix}_cpu_cores "
        "WHERE timestamp_ms BETWEEN ? AND ? ORDER BY timestamp_ms, core_index",
        (lower, upper),
    ):
        timestamp = int(row["timestamp_ms"])
        if timestamp in wanted:
            cores[timestamp].append(float(row["utilization"]))
    processes: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for row in connection.execute(
        f"SELECT * FROM {namespace}{prefix}_gpu_processes WHERE timestamp_ms BETWEEN ? AND ? "
        "ORDER BY timestamp_ms, gpu_index, pid",
        (lower, upper),
    ):
        timestamp = int(row["timestamp_ms"])
        if timestamp in wanted:
            processes.setdefault((timestamp, int(row["gpu_index"])), []).append(
                {"pid": int(row["pid"]), "name": str(row["name"]), "memory_mb": int(row["memory_mb"])}
            )
    gpus: dict[int, list[dict[str, Any]]] = {timestamp: [] for timestamp in wanted}
    for row in connection.execute(
        f"SELECT * FROM {namespace}{prefix}_gpu_samples WHERE timestamp_ms BETWEEN ? AND ? "
        "ORDER BY timestamp_ms, gpu_index",
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
    values: list[tuple[dict[str, Any], int]] = []
    for row in rows:
        timestamp = int(row["timestamp_ms"])
        values.append(
            (
                {
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
                },
                int(row["sample_count"]),
            )
        )
    return values


def _create_v3_migration_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE telemetry_hardware_profiles ("
        "profile_id INTEGER PRIMARY KEY, profile_sha256 TEXT NOT NULL UNIQUE CHECK(length(profile_sha256) = 64), "
        "created_at TEXT NOT NULL, cpu_name TEXT NOT NULL, cpu_cores_physical INTEGER NOT NULL, "
        "cpu_cores_logical INTEGER NOT NULL, cpu_frequency_max_mhz REAL, ram_total_gb REAL, ram_swap_total_gb REAL)"
    )
    connection.execute(
        f"CREATE TABLE telemetry_hardware_gpus ({_HARDWARE_GPU_STATIC_COLUMNS}) WITHOUT ROWID"
    )
    connection.execute(
        "CREATE TABLE telemetry_process_names (process_name_id INTEGER PRIMARY KEY, "
        "name_sha256 TEXT NOT NULL UNIQUE CHECK(length(name_sha256) = 64), name TEXT NOT NULL)"
    )
    connection.execute(f"CREATE TABLE raw_samples ({_BASE_COLUMNS}) WITHOUT ROWID")
    connection.execute(
        f"CREATE TABLE raw_gpu_samples ({_GPU_DYNAMIC_COLUMNS}, "
        "FOREIGN KEY(timestamp_ms) REFERENCES raw_samples(timestamp_ms) ON DELETE CASCADE) WITHOUT ROWID"
    )
    connection.execute(f"CREATE TABLE minute_aggregates ({_BASE_COLUMNS}) WITHOUT ROWID")
    connection.execute(
        f"CREATE TABLE minute_gpu_samples ({_GPU_DYNAMIC_COLUMNS}, "
        "FOREIGN KEY(timestamp_ms) REFERENCES minute_aggregates(timestamp_ms) ON DELETE CASCADE) WITHOUT ROWID"
    )
    connection.execute(
        "CREATE TABLE telemetry_partitions (partition_start_ms INTEGER PRIMARY KEY, "
        "partition_end_ms INTEGER NOT NULL, relative_path TEXT NOT NULL UNIQUE, "
        "content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64), "
        "size_bytes INTEGER NOT NULL CHECK(size_bytes > 0), row_count INTEGER NOT NULL CHECK(row_count > 0), "
        "created_at TEXT NOT NULL) WITHOUT ROWID"
    )


def _create_v3_triggers(connection: sqlite3.Connection) -> None:
    for table in _GUARDED_WRITER_TABLES:
        connection.execute(
            f"CREATE TRIGGER {table}_guard_update BEFORE UPDATE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
        )
        connection.execute(
            f"CREATE TRIGGER {table}_guard_delete BEFORE DELETE ON {table} "
            "WHEN telemetry_retention_authorized() = 0 "
            f"BEGIN SELECT RAISE(ABORT, '{table} retention authorization required'); END"
        )
        connection.execute(
            f"CREATE TRIGGER {table}_guard_insert_authority BEFORE INSERT ON {table} "
            "WHEN telemetry_writer_authorized() = 0 "
            f"BEGIN SELECT RAISE(ABORT, '{table} insert authority required'); END"
        )
    table = "telemetry_partitions"
    connection.execute(
        f"CREATE TRIGGER {table}_guard_update BEFORE UPDATE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
    )
    connection.execute(
        f"CREATE TRIGGER {table}_guard_delete BEFORE DELETE ON {table} "
        "WHEN telemetry_retention_authorized() = 0 "
        f"BEGIN SELECT RAISE(ABORT, '{table} retention authorization required'); END"
    )
    connection.execute(
        f"CREATE TRIGGER {table}_guard_insert_authority BEFORE INSERT ON {table} "
        "WHEN telemetry_partition_authorized() = 0 "
        f"BEGIN SELECT RAISE(ABORT, '{table} insert authority required'); END"
    )
    connection.execute(
        """CREATE TRIGGER raw_gpu_samples_guard_profile BEFORE INSERT ON raw_gpu_samples
WHEN NOT EXISTS (
    SELECT 1
    FROM raw_samples AS sample
    JOIN telemetry_hardware_gpus AS gpu
      ON gpu.profile_id = sample.hardware_profile_id
     AND gpu.gpu_index = NEW.gpu_index
    WHERE sample.timestamp_ms = NEW.timestamp_ms
)
BEGIN SELECT RAISE(ABORT, 'raw GPU sample does not match its hardware profile'); END"""
    )
    connection.execute(
        """CREATE TRIGGER minute_gpu_samples_guard_profile BEFORE INSERT ON minute_gpu_samples
WHEN NOT EXISTS (
    SELECT 1
    FROM minute_aggregates AS sample
    JOIN telemetry_hardware_gpus AS gpu
      ON gpu.profile_id = sample.hardware_profile_id
     AND gpu.gpu_index = NEW.gpu_index
    WHERE sample.timestamp_ms = NEW.timestamp_ms
)
BEGIN SELECT RAISE(ABORT, 'minute GPU sample does not match its hardware profile'); END"""
    )
    connection.execute(
        """CREATE TRIGGER raw_samples_guard_core_vector BEFORE INSERT ON raw_samples
WHEN NEW.cpu_per_core_count != (
    SELECT cpu_cores_logical FROM telemetry_hardware_profiles
    WHERE profile_id = NEW.hardware_profile_id
)
BEGIN SELECT RAISE(ABORT, 'raw CPU-core vector does not match its hardware profile'); END"""
    )
    connection.execute(
        """CREATE TRIGGER minute_aggregates_guard_core_vector BEFORE INSERT ON minute_aggregates
WHEN NEW.cpu_per_core_count != (
    SELECT cpu_cores_logical FROM telemetry_hardware_profiles
    WHERE profile_id = NEW.hardware_profile_id
)
BEGIN SELECT RAISE(ABORT, 'minute CPU-core vector does not match its hardware profile'); END"""
    )
    connection.execute(
        """CREATE TRIGGER raw_samples_guard_finalized_hour BEFORE INSERT ON raw_samples
WHEN EXISTS (
    SELECT 1 FROM telemetry_partitions
    WHERE NEW.timestamp_ms >= partition_start_ms AND NEW.timestamp_ms < partition_end_ms
) OR EXISTS (
    SELECT 1 FROM minute_aggregates
    WHERE timestamp_ms = (NEW.timestamp_ms / 60000) * 60000
)
BEGIN SELECT RAISE(ABORT, 'raw sample belongs to a finalized telemetry period'); END"""
    )


class TelemetryStore:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.partition_root = Path(
            os.getenv("BMS_TELEMETRY_PARTITION_ROOT", str(self.path.parent / "partitions"))
        ).expanduser().resolve()

    def _migrate_v2(self, connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA legacy_alter_table=OFF")
        connection.execute("BEGIN EXCLUSIVE")
        try:
            current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current_version == TELEMETRY_SCHEMA_VERSION:
                connection.rollback()
                return
            _validate_v2_schema(connection)
            trigger_names = [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
                )
            ]
            for trigger_name in trigger_names:
                quoted = trigger_name.replace('"', '""')
                connection.execute(f'DROP TRIGGER "{quoted}"')
            for table in (
                "raw_samples", "raw_cpu_cores", "raw_gpu_samples", "raw_gpu_processes",
                "minute_aggregates", "minute_cpu_cores", "minute_gpu_samples",
                "minute_gpu_processes", "telemetry_partitions",
            ):
                connection.execute(f"ALTER TABLE {table} RENAME TO v2_{table}")
            _create_v3_migration_tables(connection)
            for prefix in ("raw", "minute"):
                after_timestamp_ms: int | None = None
                while True:
                    batch = _read_v2_batch(
                        connection,
                        prefix=prefix,
                        after_timestamp_ms=after_timestamp_ms,
                        namespace="v2_",
                    )
                    if not batch:
                        break
                    for payload, sample_count in batch:
                        self._insert_payload(
                            connection,
                            prefix=prefix,
                            payload=payload,
                            sample_count=sample_count,
                        )
                    after_timestamp_ms = int(batch[-1][0]["timestamp_ms"])
            connection.execute(
                "INSERT INTO telemetry_partitions "
                "SELECT partition_start_ms, partition_end_ms, relative_path, content_sha256, "
                "size_bytes, row_count, created_at FROM v2_telemetry_partitions"
            )
            for table in (
                "v2_raw_gpu_processes", "v2_raw_cpu_cores", "v2_raw_gpu_samples",
                "v2_raw_samples", "v2_minute_gpu_processes", "v2_minute_cpu_cores",
                "v2_minute_gpu_samples", "v2_minute_aggregates", "v2_telemetry_partitions",
            ):
                connection.execute(f"DROP TABLE {table}")
            connection.execute(
                "UPDATE telemetry_schema SET version = ? WHERE singleton = 1",
                (TELEMETRY_SCHEMA_VERSION,),
            )
            connection.execute(f"PRAGMA user_version={TELEMETRY_SCHEMA_VERSION}")
            _create_v3_triggers(connection)
            violation = connection.execute("PRAGMA foreign_key_check").fetchone()
            if violation is not None:
                raise RuntimeError("telemetry v2 migration produced invalid foreign keys")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys=ON")

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
            if int(connection.execute("PRAGMA user_version").fetchone()[0]) == 2:
                self._migrate_v2(connection)
            _validate_schema(connection, full_integrity=True)
            connection.execute("BEGIN IMMEDIATE")
            changed_directories: set[Path] = set()
            try:
                registered = {
                    str(item["relative_path"]): (
                        str(item["content_sha256"]),
                        int(item["size_bytes"]),
                        int(item["row_count"]),
                    )
                    for item in connection.execute(
                        "SELECT relative_path, content_sha256, size_bytes, row_count FROM telemetry_partitions"
                    )
                }
                for relative, (content_sha256, size_bytes, row_count) in registered.items():
                    destination = (self.partition_root / relative).resolve()
                    destination.relative_to(self.partition_root)
                    retiring = Path(f"{destination}.retiring")
                    if not destination.exists() and retiring.exists():
                        os.replace(retiring, destination)
                        changed_directories.add(destination.parent)
                    self._validated_partition_path(
                        relative_path=relative,
                        content_sha256=content_sha256,
                        size_bytes=size_bytes,
                        row_count=row_count,
                    )
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
            _validate_schema(connection, full_integrity=False)
        except (sqlite3.Error, RuntimeError) as error:
            connection.close()
            raise RuntimeError("telemetry store schema is unavailable or invalid") from error
        return connection

    def verify_integrity(self) -> None:
        connection = open_read_only(self.path)
        try:
            connection.execute("BEGIN")
            _validate_schema(connection, full_integrity=True)
        except (sqlite3.Error, RuntimeError) as error:
            raise RuntimeError("telemetry store integrity verification failed") from error
        finally:
            connection.close()

    def _validated_partition_path(
        self,
        *,
        relative_path: str,
        content_sha256: str,
        size_bytes: int,
        row_count: int,
    ) -> Path:
        destination = (self.partition_root / relative_path).resolve()
        destination.relative_to(self.partition_root)
        if (
            not destination.is_file()
            or destination.stat().st_size != size_bytes
            or _file_sha256(destination) != content_sha256
            or pq.read_metadata(destination).num_rows != row_count
        ):
            raise RuntimeError("registered telemetry partition is missing or invalid")
        return destination

    def _ensure_process_name(
        self,
        connection: sqlite3.Connection,
        name: str,
        *,
        namespace: str = "",
    ) -> int:
        table = f"{namespace}telemetry_process_names"
        digest = _process_name_digest(name)
        row = connection.execute(
            f"SELECT process_name_id, name FROM {table} WHERE name_sha256 = ?",
            (digest,),
        ).fetchone()
        if row is not None:
            if str(row["name"]) != name:
                raise RuntimeError("telemetry process-name digest collision")
            return int(row["process_name_id"])
        cursor = connection.execute(
            f"INSERT INTO {table}(name_sha256, name) VALUES (?, ?)",
            (digest, name),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("telemetry process name did not receive an identity")
        return int(cursor.lastrowid)

    def _ensure_hardware_profile(
        self,
        connection: sqlite3.Connection,
        payload: dict[str, Any],
        *,
        namespace: str = "",
    ) -> int:
        profile_table = f"{namespace}telemetry_hardware_profiles"
        profile_gpu_table = f"{namespace}telemetry_hardware_gpus"
        profile, gpus, profile_sha256 = _hardware_profile(payload)
        row = connection.execute(
            f"SELECT * FROM {profile_table} WHERE profile_sha256 = ?",
            (profile_sha256,),
        ).fetchone()
        if row is not None:
            stored_profile = tuple(row[column] for column in _PROFILE_COLUMNS)
            stored_gpus = [
                tuple(item[column] for column in _PROFILE_GPU_COLUMNS[1:])
                for item in connection.execute(
                    f"SELECT * FROM {profile_gpu_table} WHERE profile_id = ? ORDER BY gpu_index",
                    (int(row["profile_id"]),),
                )
            ]
            if stored_profile != profile or stored_gpus != gpus:
                raise RuntimeError("telemetry hardware profile digest collision")
            return int(row["profile_id"])
        cursor = connection.execute(
            f"INSERT INTO {profile_table}(profile_sha256, created_at, {','.join(_PROFILE_COLUMNS)}) "
            f"VALUES ({','.join('?' for _ in range(len(_PROFILE_COLUMNS) + 2))})",
            (
                profile_sha256,
                str(payload.get("timestamp") or datetime.now(timezone.utc).isoformat()),
                *profile,
            ),
        )
        profile_id = int(cursor.lastrowid)
        for gpu in gpus:
            connection.execute(
                f"INSERT INTO {profile_gpu_table}({','.join(_PROFILE_GPU_COLUMNS)}) "
                f"VALUES ({','.join('?' for _ in _PROFILE_GPU_COLUMNS)})",
                (profile_id, *gpu),
            )
        return profile_id

    def _base_values(
        self,
        payload: dict[str, Any],
        sample_count: int,
        hardware_profile_id: int,
    ) -> tuple[Any, ...]:
        cpu = _object(payload.get("cpu"))
        power = _object(cpu.get("power_telemetry"))
        ram = _object(payload.get("ram"))
        per_core = list(cpu.get("per_core_utilization") or [])
        timestamp_ms = int(payload["timestamp_ms"])
        timestamp = str(payload.get("timestamp") or datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z"))
        gpu_error = payload.get("gpu_error")
        return (
            timestamp_ms, timestamp, int(sample_count), int(hardware_profile_id),
            None if gpu_error is None else str(gpu_error),
            CORE_VECTOR_FORMAT_F64_LE, len(per_core), _pack_core_vector(per_core),
            _number(cpu.get("utilization")), _number(cpu.get("frequency_current_mhz")),
            _number(cpu.get("temperature")), _number(cpu.get("power_watts")),
            str(power.get("source") or ""), 1 if bool(power.get("available")) else 0,
            str(power.get("status") or ""), str(power.get("message") or ""),
            _integer(power.get("discovered_sources")), _integer(power.get("readable_sources")),
            None if power.get("setup_hint") is None else str(power.get("setup_hint")),
            _number(ram.get("used_gb")), _number(ram.get("available_gb")),
            _number(ram.get("utilization")), _number(ram.get("swap_used_gb")),
            _number(ram.get("swap_percent")),
        )

    def _insert_payload(
        self,
        connection: sqlite3.Connection,
        *,
        prefix: Literal["raw", "minute"],
        payload: dict[str, Any],
        sample_count: int,
        namespace: str = "",
    ) -> None:
        timestamp_ms = int(payload["timestamp_ms"])
        parent = f"{namespace}{'raw_samples' if prefix == 'raw' else 'minute_aggregates'}"
        gpu_table = f"{namespace}{prefix}_gpu_samples"
        hardware_profile_id = self._ensure_hardware_profile(
            connection,
            payload,
            namespace=namespace,
        )
        placeholders = ",".join("?" for _ in _BASE_INSERT_COLUMNS)
        connection.execute(
            f"INSERT INTO {parent}({','.join(_BASE_INSERT_COLUMNS)}) VALUES ({placeholders})",
            self._base_values(payload, sample_count, hardware_profile_id),
        )
        for gpu_value in payload.get("gpus") or []:
            gpu = _object(gpu_value)
            gpu_index = int(gpu.get("index", 0))
            process_records: list[tuple[int, int, int]] = []
            for process_value in list(gpu.get("processes") or []):
                process = _object(process_value)
                process_name_id = self._ensure_process_name(
                    connection,
                    str(process.get("name") or ""),
                    namespace=namespace,
                )
                process_records.append((
                    _integer(process.get("pid")),
                    _integer(process.get("memory_mb")),
                    process_name_id,
                ))
            process_format, process_count, process_blob = _pack_process_records(process_records)
            values = (
                timestamp_ms, gpu_index, _number(gpu.get("utilization")),
                _number(gpu.get("memory_utilization")), _number(gpu.get("memory_used_mb")),
                _number(gpu.get("reserved_memory_mb")), _number(gpu.get("power_draw_w")),
                _number(gpu.get("power_limit_w")), _number(gpu.get("temperature")),
                _number(gpu.get("fan_speed")), _number(gpu.get("clock_graphics_mhz")),
                _number(gpu.get("clock_memory_mhz")), _number(gpu.get("clock_max_graphics_mhz")),
                _number(gpu.get("clock_max_memory_mhz")), process_format, process_count, process_blob,
            )
            connection.execute(
                f"INSERT INTO {gpu_table}({','.join(_GPU_COLUMNS)}) VALUES ({','.join('?' for _ in _GPU_COLUMNS)})",
                values,
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
        row_by_timestamp = {int(row["timestamp_ms"]): row for row in rows}
        profile_ids = {int(row["hardware_profile_id"]) for row in rows}
        profile_lower, profile_upper = min(profile_ids), max(profile_ids)
        profiles = {
            int(row["profile_id"]): row
            for row in connection.execute(
                "SELECT * FROM telemetry_hardware_profiles WHERE profile_id BETWEEN ? AND ?",
                (profile_lower, profile_upper),
            )
            if int(row["profile_id"]) in profile_ids
        }
        profile_gpus = {
            (int(row["profile_id"]), int(row["gpu_index"])): row
            for row in connection.execute(
                "SELECT * FROM telemetry_hardware_gpus WHERE profile_id BETWEEN ? AND ? ORDER BY profile_id, gpu_index",
                (profile_lower, profile_upper),
            )
            if int(row["profile_id"]) in profile_ids
        }
        if set(profiles) != profile_ids:
            raise RuntimeError("telemetry hardware profile is missing")
        process_names = {
            int(row["process_name_id"]): str(row["name"])
            for row in connection.execute("SELECT process_name_id, name FROM telemetry_process_names")
        }
        gpus: dict[int, list[dict[str, Any]]] = {timestamp: [] for timestamp in timestamps}
        for row in connection.execute(
            f"SELECT * FROM {prefix}_gpu_samples WHERE timestamp_ms BETWEEN ? AND ? ORDER BY timestamp_ms, gpu_index",
            (lower, upper),
        ):
            timestamp = int(row["timestamp_ms"])
            if timestamp not in wanted:
                continue
            gpu_index = int(row["gpu_index"])
            profile_id = int(row_by_timestamp[timestamp]["hardware_profile_id"])
            static = profile_gpus.get((profile_id, gpu_index))
            if static is None:
                raise RuntimeError("telemetry GPU hardware profile is missing")
            processes: list[dict[str, Any]] = []
            for pid, memory_mb, process_name_id in _unpack_process_records(
                bytes(row["process_blob"]),
                int(row["process_count"]),
                int(row["process_format"]),
            ):
                name = process_names.get(process_name_id)
                if name is None:
                    raise RuntimeError("telemetry GPU process name is missing")
                processes.append({"pid": pid, "name": name, "memory_mb": memory_mb})
            gpus[timestamp].append(
                {
                    "index": gpu_index,
                    "name": str(static["name"]),
                    "utilization": row["utilization"],
                    "memory_utilization": row["memory_utilization"],
                    "memory_used_mb": row["memory_used_mb"],
                    "memory_total_mb": static["memory_total_mb"],
                    "reserved_memory_mb": row["reserved_memory_mb"],
                    "power_draw_w": row["power_draw_w"],
                    "power_limit_w": row["power_limit_w"],
                    "min_power_watts": static["min_power_watts"],
                    "default_power_watts": static["default_power_watts"],
                    "max_power_watts": static["max_power_watts"],
                    "temperature": row["temperature"],
                    "fan_speed": row["fan_speed"],
                    "clock_graphics_mhz": row["clock_graphics_mhz"],
                    "clock_memory_mhz": row["clock_memory_mhz"],
                    "clock_max_graphics_mhz": row["clock_max_graphics_mhz"],
                    "clock_max_memory_mhz": row["clock_max_memory_mhz"],
                    "processes": processes,
                }
            )
        payloads: dict[int, dict[str, Any]] = {}
        for row in rows:
            timestamp = int(row["timestamp_ms"])
            profile = profiles[int(row["hardware_profile_id"])]
            payloads[timestamp] = {
                "timestamp_ms": timestamp,
                "timestamp": str(row["timestamp"]),
                "cpu": {
                    "name": str(profile["cpu_name"]),
                    "cores_physical": int(profile["cpu_cores_physical"]),
                    "cores_logical": int(profile["cpu_cores_logical"]),
                    "utilization": row["cpu_utilization"],
                    "per_core_utilization": _unpack_core_vector(
                        bytes(row["cpu_per_core_f64"]),
                        int(row["cpu_per_core_count"]),
                        int(row["cpu_per_core_format"]),
                    ),
                    "frequency_current_mhz": row["cpu_frequency_current_mhz"],
                    "frequency_max_mhz": profile["cpu_frequency_max_mhz"],
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
                    "total_gb": profile["ram_total_gb"],
                    "used_gb": row["ram_used_gb"],
                    "available_gb": row["ram_available_gb"],
                    "utilization": row["ram_utilization"],
                    "swap_total_gb": profile["ram_swap_total_gb"],
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

    def _payload_from_partition_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "timestamp": str(row["timestamp"]),
            "timestamp_ms": int(row["timestamp_ms"]),
            "cpu": {
                "name": str(row["cpu_name"]),
                "cores_physical": int(row["cpu_cores_physical"]),
                "cores_logical": int(row["cpu_cores_logical"]),
                "utilization": row["cpu_utilization"],
                "per_core_utilization": [
                    float(value) for value in row["cpu_per_core_utilization"]
                ],
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
            "gpus": row["gpus"] or [],
            "gpu_error": row["gpu_error"],
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

    def _retire_published_raw(self, connection: sqlite3.Connection, now_ms: int) -> int:
        cutoff_ms = int(now_ms) - HOT_RAW_RETENTION_SECONDS * 1000
        connection.execute("BEGIN IMMEDIATE")
        try:
            partitions = connection.execute(
                """SELECT partition_start_ms, partition_end_ms, relative_path,
                          content_sha256, size_bytes, row_count
                   FROM telemetry_partitions AS partition
                   WHERE partition_start_ms < ?
                     AND EXISTS (
                         SELECT 1 FROM raw_samples AS raw
                         WHERE raw.timestamp_ms >= partition.partition_start_ms
                           AND raw.timestamp_ms < MIN(partition.partition_end_ms, ?)
                     )
                   ORDER BY partition_start_ms""",
                (cutoff_ms, cutoff_ms),
            ).fetchall()
            retired = 0
            for partition in partitions:
                start_ms = int(partition["partition_start_ms"])
                end_ms = int(partition["partition_end_ms"])
                retire_end_ms = min(end_ms, cutoff_ms)
                expected_rows = int(partition["row_count"])
                current_rows = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM raw_samples WHERE timestamp_ms >= ? AND timestamp_ms < ?",
                        (start_ms, retire_end_ms),
                    ).fetchone()[0]
                )
                self._validated_partition_path(
                    relative_path=str(partition["relative_path"]),
                    content_sha256=str(partition["content_sha256"]),
                    size_bytes=int(partition["size_bytes"]),
                    row_count=expected_rows,
                )
                deleted = connection.execute(
                    "DELETE FROM raw_samples WHERE timestamp_ms >= ? AND timestamp_ms < ?",
                    (start_ms, retire_end_ms),
                ).rowcount
                if deleted != current_rows:
                    raise RuntimeError("telemetry raw retirement did not delete its verified range")
                retired += deleted
            connection.commit()
            return retired
        except BaseException:
            connection.rollback()
            raise

    def finalize_completed_minutes(self, now_ms: int) -> int:
        open_bucket = (int(now_ms) // 60_000) * 60_000
        finalized = 0
        with _connect(self.path, writer=True, publisher=True, maintenance=True) as connection:
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
            self._retire_published_raw(connection, int(now_ms))
        return finalized

    def apply_retention(self, now_ms: int) -> dict[str, int]:
        raw_cutoff = int(now_ms) - RAW_RETENTION_SECONDS * 1000
        minute_cutoff = int(now_ms) - AGGREGATE_RETENTION_SECONDS * 1000
        retiring_paths: list[tuple[Path, Path]] = []
        with _connect(self.path, maintenance=True) as connection:
            raw_deleted = self._retire_published_raw(connection, int(now_ms))
            connection.execute("BEGIN IMMEDIATE")
            try:
                expired_unpartitioned_raw = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM raw_samples WHERE timestamp_ms < ?",
                        (raw_cutoff,),
                    ).fetchone()[0]
                )
                if expired_unpartitioned_raw:
                    raise RuntimeError("expired raw telemetry lacks verified Parquet history")
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

    def _read_partition_history(
        self,
        connection: sqlite3.Connection,
        *,
        start_ms: int,
        end_ms: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        partitions = connection.execute(
            """SELECT partition_start_ms, partition_end_ms, relative_path,
                      content_sha256, size_bytes, row_count
               FROM telemetry_partitions
               WHERE partition_end_ms > ? AND partition_start_ms < ?
               ORDER BY partition_start_ms DESC""",
            (int(start_ms), int(end_ms)),
        ).fetchall()
        points: list[dict[str, Any]] = []
        for partition in partitions:
            path = self._validated_partition_path(
                relative_path=str(partition["relative_path"]),
                content_sha256=str(partition["content_sha256"]),
                size_bytes=int(partition["size_bytes"]),
                row_count=int(partition["row_count"]),
            )
            table = pq.read_table(
                path,
                filters=[
                    ("timestamp_ms", ">=", int(start_ms)),
                    ("timestamp_ms", "<", int(end_ms)),
                ],
            )
            if not table.schema.equals(_RAW_PARTITION_SCHEMA):
                raise RuntimeError("telemetry partition schema is invalid")
            for row in reversed(table.to_pylist()):
                timestamp_ms = int(row["timestamp_ms"])
                points.append(
                    {
                        "timestamp_ms": timestamp_ms,
                        "sample_count": 1,
                        "payload": self._payload_from_partition_row(row),
                    }
                )
                if len(points) >= int(limit):
                    return points
        return points

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
            partition_points = (
                self._read_partition_history(
                    connection,
                    start_ms=int(start_ms),
                    end_ms=int(end_ms),
                    limit=int(limit),
                )
                if resolution == "raw"
                else []
            )
        sqlite_points = [
            {
                "timestamp_ms": int(row["timestamp_ms"]),
                "sample_count": int(row["sample_count"]),
                "payload": payloads[int(row["timestamp_ms"])],
            }
            for row in rows
        ]
        if resolution == "minute":
            return sqlite_points
        points_by_timestamp = {
            int(point["timestamp_ms"]): point
            for point in partition_points
        }
        points_by_timestamp.update(
            {int(point["timestamp_ms"]): point for point in sqlite_points}
        )
        selected_timestamps = sorted(points_by_timestamp, reverse=True)[: int(limit)]
        return [
            points_by_timestamp[timestamp_ms]
            for timestamp_ms in sorted(selected_timestamps)
        ]

    def read_chart_history(
        self,
        *,
        start_ms: int,
        end_ms: int,
        bucket_ms: int,
        since_ms: int | None,
        limit: int,
    ) -> dict[str, Any]:
        start = int(start_ms)
        end = int(end_ms)
        bucket = int(bucket_ms)
        maximum = int(limit)
        if start >= end:
            raise ValueError("chart start must be earlier than end")
        if bucket <= 0:
            raise ValueError("chart bucket must be positive")
        if not 1 <= maximum <= MAX_HISTORY_POINTS:
            raise ValueError("chart point limit is outside the supported range")
        cursor = None if since_ms is None else int(since_ms)
        if cursor is not None and (cursor < start or cursor >= end):
            raise ValueError("chart cursor is outside the requested range")
        aligned_start = start - (start % bucket)
        effective_start = aligned_start if cursor is None else max(aligned_start, cursor - (cursor % bucket))
        with self._validated_read_connection() as connection:
            rows = connection.execute(
                """SELECT timestamp_ms - (timestamp_ms % ?) AS bucket_start_ms,
                          COUNT(*) AS sample_count,
                          MAX(timestamp_ms) AS latest_sample_ms,
                          AVG(cpu_utilization) AS cpu_utilization,
                          AVG(cpu_frequency_current_mhz) AS cpu_frequency_current_mhz,
                          AVG(cpu_power_watts) AS cpu_power_watts,
                          AVG(cpu_temperature) AS cpu_temperature,
                          AVG(ram_used_gb) AS ram_used_gb,
                          AVG(ram_available_gb) AS ram_available_gb,
                          AVG(ram_utilization) AS ram_utilization,
                          AVG(ram_swap_percent) AS ram_swap_percent
                   FROM raw_samples
                   WHERE timestamp_ms >= ? AND timestamp_ms < ?
                   GROUP BY bucket_start_ms
                   ORDER BY bucket_start_ms
                   LIMIT ?""",
                (bucket, effective_start, end, maximum + 1),
            ).fetchall()
            if len(rows) > maximum:
                raise ValueError("chart point limit exceeded")
            gpu_rows = connection.execute(
                """SELECT timestamp_ms - (timestamp_ms % ?) AS bucket_start_ms,
                          gpu_index,
                          COUNT(*) AS sample_count,
                          AVG(utilization) AS utilization,
                          AVG((memory_used_mb + reserved_memory_mb) / 1024.0) AS vram_gb,
                          AVG(power_draw_w) AS power_draw_w,
                          AVG(temperature) AS temperature
                   FROM raw_gpu_samples
                   WHERE timestamp_ms >= ? AND timestamp_ms < ?
                   GROUP BY bucket_start_ms, gpu_index
                   ORDER BY bucket_start_ms, gpu_index""",
                (bucket, effective_start, end),
            ).fetchall()

        gpus_by_bucket: dict[int, list[dict[str, Any]]] = {}
        parent_counts = {
            int(row["bucket_start_ms"]): int(row["sample_count"])
            for row in rows
        }
        for row in gpu_rows:
            bucket_start_ms = int(row["bucket_start_ms"])
            if int(row["sample_count"]) > parent_counts.get(bucket_start_ms, 0):
                raise RuntimeError("telemetry chart GPU cardinality is invalid")
            gpus_by_bucket.setdefault(bucket_start_ms, []).append(
                {
                    "index": int(row["gpu_index"]),
                    "utilization": row["utilization"],
                    "vram_gb": row["vram_gb"],
                    "power_draw_w": row["power_draw_w"],
                    "temperature": row["temperature"],
                }
            )
        points = [
            {
                "timestamp_ms": int(row["bucket_start_ms"]),
                "sample_count": int(row["sample_count"]),
                "cpu_utilization": row["cpu_utilization"],
                "cpu_frequency_current_mhz": row["cpu_frequency_current_mhz"],
                "cpu_power_watts": row["cpu_power_watts"],
                "cpu_temperature": row["cpu_temperature"],
                "ram_used_gb": row["ram_used_gb"],
                "ram_available_gb": row["ram_available_gb"],
                "ram_utilization": row["ram_utilization"],
                "ram_swap_percent": row["ram_swap_percent"],
                "gpus": gpus_by_bucket.get(int(row["bucket_start_ms"]), []),
            }
            for row in rows
        ]
        next_cursor_ms = (
            max(int(row["latest_sample_ms"]) for row in rows)
            if rows
            else cursor
        )
        return {
            "effective_start_ms": effective_start,
            "next_cursor_ms": next_cursor_ms,
            "points": points,
        }

    def read_freshness(self, *, now_ms: int | None = None, stale_after_ms: int) -> dict[str, Any]:
        if int(stale_after_ms) <= 0:
            raise ValueError("telemetry freshness threshold must be positive")
        observed_now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        with self._validated_read_connection() as connection:
            # A wall-clock rollback must not let old future-dated samples mask
            # a collector that has resumed writing at the corrected time.
            # Read both bounds in one SQLite snapshot; preserve all history.
            latest, future_count, latest_future = connection.execute(
                "SELECT "
                "(SELECT MAX(timestamp_ms) FROM raw_samples WHERE timestamp_ms <= ?), "
                "(SELECT COUNT(*) FROM raw_samples WHERE timestamp_ms > ?), "
                "(SELECT MAX(timestamp_ms) FROM raw_samples WHERE timestamp_ms > ?)",
                (observed_now_ms, observed_now_ms, observed_now_ms),
            ).fetchone()
        metadata = {
            "stale_after_ms": int(stale_after_ms),
            "future_sample_count": int(future_count),
            "latest_future_timestamp_ms": latest_future,
        }
        if latest is None:
            return {
                "ready": False,
                "status": "future" if latest_future is not None else "empty",
                "latest_timestamp_ms": latest_future,
                "age_ms": observed_now_ms - latest_future if latest_future is not None else None,
                **metadata,
            }
        latest_timestamp_ms = int(latest)
        age_ms = observed_now_ms - latest_timestamp_ms
        ready = age_ms <= int(stale_after_ms)
        return {
            "ready": ready,
            "status": "fresh" if ready else "stale",
            "latest_timestamp_ms": latest_timestamp_ms,
            "age_ms": age_ms,
            **metadata,
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
