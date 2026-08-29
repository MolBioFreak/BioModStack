from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pyarrow.parquet as pq
import pytest

import telemetry_store as telemetry_store_module
import scripts.migrate_json_payloads_to_artifacts as legacy_payload_migration
from telemetry_store import (
    AGGREGATE_RETENTION_SECONDS,
    RAW_RETENTION_SECONDS,
    TELEMETRY_SCHEMA_VERSION,
    TelemetryStore,
    open_read_only,
)


def sample(timestamp_ms: int, cpu: float, gpu_util: float = 40.0) -> dict[str, object]:
    return {
        "timestamp_ms": timestamp_ms,
        "timestamp": datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z"),
        "cpu": {
            "name": "Test CPU",
            "cores_physical": 1,
            "cores_logical": 2,
            "utilization": cpu,
            "per_core_utilization": [cpu, cpu + 10.0],
            "frequency_current_mhz": 2000.0,
            "frequency_max_mhz": 4000.0,
            "temperature": 55.0,
            "power_watts": 80.0,
            "power_telemetry": {
                "source": "rapl",
                "available": True,
                "status": "ok",
                "message": "sampled",
                "discovered_sources": 1,
                "readable_sources": 1,
                "setup_hint": None,
            },
        },
        "ram": {
            "total_gb": 64.0,
            "used_gb": 16.0,
            "available_gb": 48.0,
            "utilization": 25.0,
            "swap_total_gb": 8.0,
            "swap_used_gb": 0.0,
            "swap_percent": 0.0,
        },
        "gpus": [
            {
                "index": 0,
                "name": "Test GPU",
                "utilization": gpu_util,
                "memory_utilization": 5.0,
                "memory_used_mb": 1024.0,
                "memory_total_mb": 24000.0,
                "reserved_memory_mb": 256.0,
                "power_draw_w": 120.0,
                "power_limit_w": 300.0,
                "min_power_watts": 100.0,
                "default_power_watts": 250.0,
                "max_power_watts": 350.0,
                "temperature": 60.0,
                "fan_speed": 30.0,
                "clock_graphics_mhz": 1800.0,
                "clock_memory_mhz": 9000.0,
                "clock_max_graphics_mhz": 2500.0,
                "clock_max_memory_mhz": 10000.0,
                "processes": [{"pid": 123, "name": "worker", "memory_mb": 512}],
            }
        ],
        "gpu_error": None,
    }


def _create_noncanonical_v2_store(path: Path, payload: dict[str, object]) -> None:
    base_columns = (
        ("timestamp_ms", "INTEGER PRIMARY KEY"), ("timestamp", "TEXT NOT NULL"),
        ("sample_count", "INTEGER NOT NULL"), ("gpu_error", "TEXT"),
        ("cpu_name", "TEXT NOT NULL"), ("cpu_cores_physical", "INTEGER NOT NULL"),
        ("cpu_cores_logical", "INTEGER NOT NULL"), ("cpu_utilization", "REAL"),
        ("cpu_frequency_current_mhz", "REAL"), ("cpu_frequency_max_mhz", "REAL"),
        ("cpu_temperature", "REAL"), ("cpu_power_watts", "REAL"),
        ("cpu_power_source", "TEXT NOT NULL"), ("cpu_power_available", "INTEGER NOT NULL"),
        ("cpu_power_status", "TEXT NOT NULL"), ("cpu_power_message", "TEXT NOT NULL"),
        ("cpu_power_discovered_sources", "INTEGER NOT NULL"),
        ("cpu_power_readable_sources", "INTEGER NOT NULL"),
        ("cpu_power_setup_hint", "TEXT"), ("ram_total_gb", "REAL"),
        ("ram_used_gb", "REAL"), ("ram_available_gb", "REAL"),
        ("ram_utilization", "REAL"), ("ram_swap_total_gb", "REAL"),
        ("ram_swap_used_gb", "REAL"), ("ram_swap_percent", "REAL"),
    )
    gpu_columns = (
        ("timestamp_ms", "INTEGER NOT NULL"), ("gpu_index", "INTEGER NOT NULL"),
        ("name", "TEXT NOT NULL"), ("utilization", "REAL"),
        ("memory_utilization", "REAL"), ("memory_used_mb", "REAL"),
        ("memory_total_mb", "REAL"), ("reserved_memory_mb", "REAL"),
        ("power_draw_w", "REAL"), ("power_limit_w", "REAL"),
        ("min_power_watts", "REAL"), ("default_power_watts", "REAL"),
        ("max_power_watts", "REAL"), ("temperature", "REAL"),
        ("fan_speed", "REAL"), ("clock_graphics_mhz", "REAL"),
        ("clock_memory_mhz", "REAL"), ("clock_max_graphics_mhz", "REAL"),
        ("clock_max_memory_mhz", "REAL"),
    )
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=2")
        connection.execute(
            "CREATE TABLE telemetry_schema(singleton INTEGER PRIMARY KEY CHECK(singleton = 1), version INTEGER NOT NULL) WITHOUT ROWID"
        )
        connection.execute("INSERT INTO telemetry_schema VALUES (1, 2)")
        for parent in ("raw_samples", "minute_aggregates"):
            connection.execute(
                f"CREATE TABLE {parent}({','.join(f'{name} {kind}' for name, kind in base_columns)})"
            )
        for prefix in ("raw", "minute"):
            connection.execute(
                f"CREATE TABLE {prefix}_cpu_cores(timestamp_ms INTEGER NOT NULL, core_index INTEGER NOT NULL, utilization REAL NOT NULL, PRIMARY KEY(timestamp_ms, core_index))"
            )
            connection.execute(
                f"CREATE TABLE {prefix}_gpu_samples({','.join(f'{name} {kind}' for name, kind in gpu_columns)}, PRIMARY KEY(timestamp_ms, gpu_index))"
            )
            connection.execute(
                f"CREATE TABLE {prefix}_gpu_processes(timestamp_ms INTEGER NOT NULL, gpu_index INTEGER NOT NULL, pid INTEGER NOT NULL, name TEXT NOT NULL, memory_mb INTEGER NOT NULL, PRIMARY KEY(timestamp_ms, gpu_index, pid))"
            )
        connection.execute(
            "CREATE TABLE telemetry_partitions(partition_start_ms INTEGER PRIMARY KEY, partition_end_ms INTEGER NOT NULL, relative_path TEXT NOT NULL UNIQUE, content_sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL, row_count INTEGER NOT NULL, created_at TEXT NOT NULL)"
        )
        cpu = payload["cpu"]  # type: ignore[index]
        ram = payload["ram"]  # type: ignore[index]
        power = cpu["power_telemetry"]  # type: ignore[index]
        base_values = (
            payload["timestamp_ms"], payload["timestamp"], 1, payload["gpu_error"],
            cpu["name"], cpu["cores_physical"], cpu["cores_logical"], cpu["utilization"],
            cpu["frequency_current_mhz"], cpu["frequency_max_mhz"], cpu["temperature"],
            cpu["power_watts"], power["source"], 1, power["status"], power["message"],
            power["discovered_sources"], power["readable_sources"], power["setup_hint"],
            ram["total_gb"], ram["used_gb"], ram["available_gb"], ram["utilization"],
            ram["swap_total_gb"], ram["swap_used_gb"], ram["swap_percent"],
        )
        connection.execute(
            f"INSERT INTO raw_samples({','.join(name for name, _kind in base_columns)}) VALUES ({','.join('?' for _ in base_columns)})",
            base_values,
        )
        for core_index, utilization in enumerate(cpu["per_core_utilization"]):  # type: ignore[index]
            connection.execute(
                "INSERT INTO raw_cpu_cores VALUES (?, ?, ?)",
                (payload["timestamp_ms"], core_index, utilization),
            )
        gpu = payload["gpus"][0]  # type: ignore[index]
        connection.execute(
            f"INSERT INTO raw_gpu_samples({','.join(name for name, _kind in gpu_columns)}) VALUES ({','.join('?' for _ in gpu_columns)})",
            (
                payload["timestamp_ms"],
                *(
                    gpu["index"] if name == "gpu_index" else gpu[name]
                    for name, _kind in gpu_columns[1:]
                ),
            ),
        )
        process = gpu["processes"][0]
        connection.execute(
            "INSERT INTO raw_gpu_processes VALUES (?, ?, ?, ?, ?)",
            (payload["timestamp_ms"], gpu["index"], process["pid"], process["name"], process["memory_mb"]),
        )


def _create_v2_store(path: Path, payload: dict[str, object]) -> None:
    cpu = telemetry_store_module._object(payload["cpu"])
    ram = telemetry_store_module._object(payload["ram"])
    power = telemetry_store_module._object(cpu["power_telemetry"])
    base_values = (
        payload["timestamp_ms"], payload["timestamp"], 1, payload["gpu_error"],
        cpu["name"], cpu["cores_physical"], cpu["cores_logical"], cpu["utilization"],
        cpu["frequency_current_mhz"], cpu["frequency_max_mhz"], cpu["temperature"],
        cpu["power_watts"], power["source"], 1, power["status"], power["message"],
        power["discovered_sources"], power["readable_sources"], power["setup_hint"],
        ram["total_gb"], ram["used_gb"], ram["available_gb"], ram["utilization"],
        ram["swap_total_gb"], ram["swap_used_gb"], ram["swap_percent"],
    )
    gpu = telemetry_store_module._object(payload["gpus"][0])  # type: ignore[index]
    with telemetry_store_module._connect(path, writer=True, publisher=True, maintenance=True) as connection:
        connection.executescript(telemetry_store_module._V2_SCHEMA)
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                f"INSERT INTO raw_samples({','.join(telemetry_store_module._V2_BASE_COLUMN_NAMES)}) "
                f"VALUES ({','.join('?' for _ in telemetry_store_module._V2_BASE_COLUMN_NAMES)})",
                base_values,
            )
            for core_index, utilization in enumerate(cpu["per_core_utilization"]):  # type: ignore[index]
                connection.execute(
                    "INSERT INTO raw_cpu_cores VALUES (?, ?, ?)",
                    (payload["timestamp_ms"], core_index, utilization),
                )
            connection.execute(
                f"INSERT INTO raw_gpu_samples({','.join(telemetry_store_module._V2_GPU_COLUMN_NAMES)}) "
                f"VALUES ({','.join('?' for _ in telemetry_store_module._V2_GPU_COLUMN_NAMES)})",
                (
                    payload["timestamp_ms"],
                    *(
                        gpu["index"] if name == "gpu_index" else gpu[name]
                        for name in telemetry_store_module._V2_GPU_COLUMN_NAMES[1:]
                    ),
                ),
            )
            process = telemetry_store_module._object(gpu["processes"][0])  # type: ignore[index]
            connection.execute(
                "INSERT INTO raw_gpu_processes VALUES (?, ?, ?, ?, ?)",
                (payload["timestamp_ms"], gpu["index"], process["pid"], process["name"], process["memory_mb"]),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def test_store_is_typed_append_only_and_readers_are_query_only(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.sqlite3"
    store = TelemetryStore(path)
    store.initialize()
    store.append_sample(sample(1_700_000_000_000, 10.0))

    with pytest.raises(sqlite3.IntegrityError):
        store.append_sample(sample(1_700_000_000_000, 99.0))
    with sqlite3.connect(path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE raw_samples SET cpu_utilization = 99 WHERE timestamp_ms = ?",
            (1_700_000_000_000,),
        )
    for statement in (
        "UPDATE telemetry_hardware_profiles SET cpu_name = 'x'",
        "DELETE FROM telemetry_hardware_profiles",
        "UPDATE telemetry_hardware_gpus SET name = 'x'",
        "DELETE FROM telemetry_hardware_gpus",
        "UPDATE raw_gpu_samples SET utilization = 99",
        "DELETE FROM raw_gpu_samples",
        "UPDATE raw_gpu_processes SET memory_mb = 99",
        "DELETE FROM raw_gpu_processes",
    ):
        with sqlite3.connect(path) as connection, pytest.raises(sqlite3.DatabaseError):
            connection.execute(statement)
    store.insert_minute_for_test(
        1_700_000_000_000,
        sample(1_700_000_000_000, 10.0),
        1,
    )
    unauthorized_inserts = (
        "INSERT INTO telemetry_hardware_profiles(profile_sha256, created_at, cpu_name, cpu_cores_physical, cpu_cores_logical) "
        "VALUES ('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'now', 'x', 1, 1)",
        "INSERT INTO telemetry_hardware_gpus(profile_id, gpu_index, name) VALUES (1, 99, 'x')",
        "INSERT INTO raw_gpu_samples(timestamp_ms, gpu_index) VALUES (1700000000000, 99)",
        "INSERT INTO raw_gpu_processes VALUES (1700000000000, 0, 999, 'x', 1)",
        "INSERT INTO minute_gpu_samples(timestamp_ms, gpu_index) VALUES (1700000000000, 99)",
        "INSERT INTO minute_gpu_processes VALUES (1700000000000, 0, 999, 'x', 1)",
        "INSERT INTO telemetry_partitions VALUES (1, 2, 'raw/x.parquet', "
        "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 1, 1, 'now')",
    )
    for statement in unauthorized_inserts:
        with sqlite3.connect(path) as connection, pytest.raises(sqlite3.DatabaseError):
            connection.execute(statement)
    with open_read_only(path) as connection:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        assert connection.execute("SELECT version FROM telemetry_schema").fetchone()[0] == TELEMETRY_SCHEMA_VERSION
        row = connection.execute(
            "SELECT cpu_utilization, ram_utilization, typeof(cpu_per_core_f64), "
            "length(cpu_per_core_f64) FROM raw_samples"
        ).fetchone()
        assert tuple(row) == (10.0, 25.0, "blob", 16)
        gpu = connection.execute(
            """SELECT profile_gpu.name, sample.utilization
            FROM raw_gpu_samples AS sample
            JOIN raw_samples AS parent ON parent.timestamp_ms = sample.timestamp_ms
            JOIN telemetry_hardware_gpus AS profile_gpu
              ON profile_gpu.profile_id = parent.hardware_profile_id
             AND profile_gpu.gpu_index = sample.gpu_index"""
        ).fetchone()
        assert tuple(gpu) == ("Test GPU", 40.0)
        assert store.read_history(
            start_ms=1_700_000_000_000,
            end_ms=1_700_000_000_001,
            resolution="raw",
            limit=1,
        )[0]["payload"]["gpus"][0]["processes"] == [
            {"pid": 123, "name": "worker", "memory_mb": 512}
        ]
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM raw_samples")


def test_compact_store_uses_one_parent_and_one_row_per_gpu(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.sqlite3"
    store = TelemetryStore(path)
    store.initialize()
    first = sample(1_700_000_000_000, 10.125)
    base_gpu = dict(first["gpus"][0])  # type: ignore[index]
    first["gpus"] = [
        {
            **base_gpu,
            "index": index,
            "name": f"Test GPU {index}",
            "processes": [
                {"pid": 1000 + index, "name": f"worker-{index}", "memory_mb": 256 + index}
            ],
        }
        for index in range(3)
    ]
    second = sample(1_700_000_001_000, 20.375)
    second["gpus"] = [
        {
            **base_gpu,
            "index": index,
            "name": f"Test GPU {index}",
            "processes": [
                {"pid": 2000 + index, "name": f"worker-{index}", "memory_mb": 512 + index}
            ],
        }
        for index in range(3)
    ]

    store.append_sample(first)
    store.append_sample(second)

    with open_read_only(path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        vector = connection.execute(
            "SELECT typeof(cpu_per_core_f64), length(cpu_per_core_f64) FROM raw_samples ORDER BY timestamp_ms LIMIT 1"
        ).fetchone()
        counts = {
            "raw_samples": connection.execute("SELECT COUNT(*) FROM raw_samples").fetchone()[0],
            "raw_gpu_samples": connection.execute("SELECT COUNT(*) FROM raw_gpu_samples").fetchone()[0],
            "profiles": connection.execute("SELECT COUNT(*) FROM telemetry_hardware_profiles").fetchone()[0],
            "profile_gpus": connection.execute("SELECT COUNT(*) FROM telemetry_hardware_gpus").fetchone()[0],
        }
    assert "raw_cpu_cores" not in tables
    assert "minute_cpu_cores" not in tables
    assert "raw_gpu_processes" not in tables
    assert "minute_gpu_processes" not in tables
    assert tuple(vector) == ("blob", 16)
    assert counts == {
        "raw_samples": 2,
        "raw_gpu_samples": 6,
        "profiles": 1,
        "profile_gpus": 3,
    }
    restored = store.read_history(
        start_ms=1_700_000_000_000,
        end_ms=1_700_000_000_001,
        resolution="raw",
        limit=1,
    )[0]["payload"]
    assert restored == first


def test_compact_store_supports_standard_sqlite_vacuum(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.sqlite3"
    store = TelemetryStore(path)
    store.initialize()
    store.append_sample(sample(1_700_000_000_000, 10.0))

    with sqlite3.connect(path) as connection:
        connection.execute("VACUUM")
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_mutable_gpu_limits_and_observed_clocks_do_not_create_hardware_profiles(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.sqlite3"
    store = TelemetryStore(path)
    store.initialize()
    first = sample(1_700_000_000_000, 10.0)
    second = sample(1_700_000_001_000, 11.0)
    second_gpu = second["gpus"][0]  # type: ignore[index]
    second_gpu["power_limit_w"] = 180
    second_gpu["clock_max_graphics_mhz"] = 2_500
    second_gpu["clock_max_memory_mhz"] = 10_500

    store.append_sample(first)
    store.append_sample(second)

    with open_read_only(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM telemetry_hardware_profiles").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM telemetry_hardware_gpus").fetchone()[0] == 1
    restored = store.read_history(
        start_ms=1_700_000_001_000,
        end_ms=1_700_000_001_001,
        resolution="raw",
        limit=1,
    )[0]["payload"]["gpus"][0]
    assert restored["power_limit_w"] == 180
    assert restored["clock_max_graphics_mhz"] == 2_500
    assert restored["clock_max_memory_mhz"] == 10_500


def test_cpu_vector_count_must_match_hardware_profile(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.sqlite3"
    store = TelemetryStore(path)
    store.initialize()
    malformed = sample(1_700_000_000_000, 10.0)
    malformed["cpu"]["per_core_utilization"] = [10.0]  # type: ignore[index]

    with pytest.raises(sqlite3.IntegrityError, match="CPU-core vector"):
        store.append_sample(malformed)


def test_initialize_rejects_noncanonical_v2_schema_without_mutation(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.sqlite3"
    expected = sample(1_700_000_000_000, 17.125)
    _create_noncanonical_v2_store(path, expected)

    with pytest.raises(RuntimeError, match="v2 schema"):
        TelemetryStore(path).initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT version FROM telemetry_schema").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM raw_samples").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM raw_gpu_processes").fetchone()[0] == 1


def test_initialize_migrates_v2_rows_without_history_loss(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.sqlite3"
    expected = sample(1_700_000_000_000, 17.125)
    _create_v2_store(path, expected)

    store = TelemetryStore(path)
    store.initialize()

    with open_read_only(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == TELEMETRY_SCHEMA_VERSION
        assert connection.execute("SELECT version FROM telemetry_schema").fetchone()[0] == TELEMETRY_SCHEMA_VERSION
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert "raw_cpu_cores" not in tables
        assert connection.execute("PRAGMA foreign_key_check").fetchone() is None
    restored = store.read_history(
        start_ms=1_700_000_000_000,
        end_ms=1_700_000_000_001,
        resolution="raw",
        limit=1,
    )[0]["payload"]
    assert restored == expected


def test_schema_has_no_json_or_generic_artifact_persistence(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.sqlite3"
    store = TelemetryStore(path)
    store.initialize()
    store.append_sample(sample(1_700_000_000_000, 10.0))

    with open_read_only(path) as connection:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        columns = {
            table: [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
            for table in tables
        }
    forbidden_tables = {
        "scientific_artifact_receipts",
        "scientific_payload_migrations",
        "telemetry_sample_artifact_refs",
    }
    assert forbidden_tables.isdisjoint(tables)
    assert all("json" not in column.lower() for names in columns.values() for column in names)
    assert list(tmp_path.rglob("*.jsonl")) == []


def test_legacy_json_store_is_rejected_instead_of_mutated(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE raw_samples(timestamp_ms INTEGER PRIMARY KEY, payload_json TEXT NOT NULL)")
    with pytest.raises(RuntimeError, match="legacy JSON telemetry store"):
        TelemetryStore(path).initialize()


def test_legacy_telemetry_json_migration_entrypoint_is_retired(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.sqlite3"
    target = tmp_path / "target.sqlite3"
    artifacts = tmp_path / "artifacts"
    monkeypatch.setattr(
        legacy_payload_migration,
        "parse_args",
        lambda: SimpleNamespace(
            source=source,
            target=target,
            artifact_root=artifacts,
            source_artifact_root=None,
            store="telemetry",
            retire_landscapes=False,
            apply=True,
        ),
    )
    with pytest.raises(
        SystemExit,
        match="telemetry JSON/scientific-artifact migration is retired",
    ):
        legacy_payload_migration.main()
    assert not source.exists()
    assert not target.exists()
    assert not artifacts.exists()


def test_incomplete_v2_schema_marker_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE telemetry_schema(singleton INTEGER PRIMARY KEY, version INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO telemetry_schema(singleton, version) VALUES (1, ?)",
            (TELEMETRY_SCHEMA_VERSION,),
        )
    with pytest.raises(RuntimeError, match="incomplete typed telemetry"):
        TelemetryStore(path).initialize()


def test_exact_schema_validation_rejects_extra_tables_and_noop_guards(tmp_path: Path) -> None:
    extra_path = tmp_path / "extra.sqlite3"
    extra_store = TelemetryStore(extra_path)
    extra_store.initialize()
    with sqlite3.connect(extra_path) as connection:
        connection.execute("CREATE TABLE scientific_artifact_receipts(id TEXT PRIMARY KEY)")
    with pytest.raises(RuntimeError, match="incomplete typed telemetry"):
        extra_store.initialize()
    with pytest.raises(RuntimeError, match="schema is unavailable or invalid"):
        extra_store.read_freshness(now_ms=1_700_000_000_000, stale_after_ms=15_000)

    trigger_path = tmp_path / "noop-trigger.sqlite3"
    trigger_store = TelemetryStore(trigger_path)
    trigger_store.initialize()
    with sqlite3.connect(trigger_path) as connection:
        connection.execute("DROP TRIGGER raw_samples_guard_update")
        connection.execute(
            "CREATE TRIGGER raw_samples_guard_update BEFORE UPDATE ON raw_samples "
            "BEGIN SELECT 1; END"
        )
    with pytest.raises(RuntimeError, match="incomplete typed telemetry"):
        trigger_store.initialize()


def test_legacy_store_cannot_report_freshness(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE raw_samples(timestamp_ms INTEGER PRIMARY KEY, payload_json TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO raw_samples(timestamp_ms, payload_json) VALUES (?, '{}')",
            (1_700_000_000_000,),
        )
    with pytest.raises(RuntimeError, match="schema is unavailable or invalid"):
        TelemetryStore(path).read_freshness(
            now_ms=1_700_000_000_001,
            stale_after_ms=15_000,
        )


def test_raw_history_reconstructs_complete_http_payload_from_typed_rows(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    expected = sample(1_700_000_000_000, 17.0)
    store.append_sample(expected)

    point = store.read_history(
        start_ms=1_699_999_999_000,
        end_ms=1_700_000_001_000,
        resolution="raw",
        limit=10,
    )[0]
    assert point["sample_count"] == 1
    assert point["payload"] == expected


def test_completed_minute_is_typed_and_averaged_once(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    minute = 1_700_000_040_000
    store.append_sample(sample(minute + 1_000, 10.0, 20.0))
    store.append_sample(sample(minute + 31_000, 30.0, 60.0))

    assert store.finalize_completed_minutes(minute + 60_000) == 1
    assert store.finalize_completed_minutes(minute + 120_000) == 0
    points = store.read_history(
        start_ms=minute,
        end_ms=minute + 120_000,
        resolution="minute",
        limit=10,
    )
    assert len(points) == 1
    assert points[0]["sample_count"] == 2
    payload = points[0]["payload"]
    assert payload["cpu"]["utilization"] == 20.0
    assert payload["cpu"]["per_core_utilization"] == [20.0, 30.0]
    assert payload["gpus"][0]["utilization"] == 40.0
    assert payload["gpus"][0]["processes"] == [{"pid": 123, "name": "worker", "memory_mb": 512}]


def test_late_unfinalized_minute_is_not_skipped_after_a_newer_minute(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    hour = 1_699_999_200_000
    store.append_sample(sample(hour + 1_000, 10.0))
    store.append_sample(sample(hour + 121_000, 30.0))
    assert store.finalize_completed_minutes(hour + 180_000) == 2
    store.append_sample(sample(hour + 61_000, 20.0))
    assert store.finalize_completed_minutes(hour + 180_000) == 1
    points = store.read_history(
        start_ms=hour,
        end_ms=hour + 180_000,
        resolution="minute",
        limit=10,
    )
    assert [point["timestamp_ms"] for point in points] == [
        hour,
        hour + 60_000,
        hour + 120_000,
    ]


def test_completed_hour_creates_one_typed_parquet_partition(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    hour = 1_699_999_200_000
    for offset in (1_000, 61_000, 121_000):
        store.append_sample(sample(hour + offset, float(offset)))

    assert store.finalize_completed_minutes(hour + 3_600_000) == 3
    assert store.finalize_completed_minutes(hour + 3_660_000) == 0
    partitions = list(store.partition_root.rglob("*.parquet"))
    assert len(partitions) == 1
    schema = pq.read_schema(partitions[0])
    assert "payload_json" not in schema.names
    assert "gpus" in schema.names
    assert pq.read_table(partitions[0]).num_rows == 3
    with open_read_only(store.path) as connection:
        metadata = connection.execute(
            "SELECT partition_start_ms, partition_end_ms, row_count, length(content_sha256) FROM telemetry_partitions"
        ).fetchone()
    assert tuple(metadata) == (hour, hour + 3_600_000, 3, 64)


def test_verified_parquet_owns_raw_history_after_one_hour_hot_tail(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    hour = 1_699_999_200_000
    expected = sample(hour + 1_000, 10.125)
    store.append_sample(expected)

    store.finalize_completed_minutes(hour + 3_600_000)
    with open_read_only(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM raw_samples").fetchone()[0] == 1

    store.finalize_completed_minutes(hour + 7_200_000)
    with open_read_only(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM raw_samples").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM telemetry_partitions").fetchone()[0] == 1

    points = store.read_history(
        start_ms=hour,
        end_ms=hour + 3_600_000,
        resolution="raw",
        limit=10,
    )
    assert points == [{"timestamp_ms": hour + 1_000, "sample_count": 1, "payload": expected}]


def test_hot_tail_retires_only_verified_rows_older_than_one_hour(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    hour = 1_699_999_200_000
    early = hour + 1_000
    late = hour + 3_599_000
    store.append_sample(sample(early, 10.0))
    store.append_sample(sample(late, 20.0))
    store.finalize_completed_minutes(hour + 3_600_000)

    store.finalize_completed_minutes(hour + 3_601_500)

    with open_read_only(store.path) as connection:
        remaining = [
            int(row[0])
            for row in connection.execute("SELECT timestamp_ms FROM raw_samples ORDER BY timestamp_ms")
        ]
    assert remaining == [late]
    points = store.read_history(
        start_ms=hour,
        end_ms=hour + 3_600_000,
        resolution="raw",
        limit=10,
    )
    assert [point["timestamp_ms"] for point in points] == [early, late]


def test_corrupt_parquet_cannot_authorize_raw_retirement(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    hour = 1_699_999_200_000
    store.append_sample(sample(hour + 1_000, 10.0))
    store.finalize_completed_minutes(hour + 3_600_000)
    partition = next(store.partition_root.rglob("*.parquet"))
    with partition.open("ab") as handle:
        handle.write(b"corrupt")

    with pytest.raises(RuntimeError, match="partition is missing or invalid"):
        store.finalize_completed_minutes(hour + 7_200_000)
    with open_read_only(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM raw_samples").fetchone()[0] == 1


def test_concurrent_hour_finalizers_publish_one_hash_bound_partition(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    hour = 1_699_999_200_000
    store.append_sample(sample(hour + 1_000, 10.0))
    start = threading.Barrier(2)
    results: list[int] = []
    errors: list[BaseException] = []

    def finalize() -> None:
        try:
            start.wait(timeout=10)
            connection = telemetry_store_module._connect(store.path, publisher=True)
            connection.row_factory = sqlite3.Row
            try:
                results.append(store._finalize_completed_hours(connection, hour + 3_600_000))
            finally:
                connection.close()
        except BaseException as error:  # pragma: no cover
            errors.append(error)

    threads = [threading.Thread(target=finalize) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    assert sorted(results) == [0, 1]
    partitions = list(store.partition_root.rglob("*.parquet"))
    assert len(partitions) == 1
    with open_read_only(store.path) as connection:
        row = connection.execute(
            "SELECT content_sha256, size_bytes, row_count FROM telemetry_partitions"
        ).fetchone()
    assert tuple(row) == (
        telemetry_store_module._file_sha256(partitions[0]),
        partitions[0].stat().st_size,
        1,
    )
    assert list(store.partition_root.rglob("*.tmp")) == []


def test_finalization_rejects_a_late_concurrent_sample(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    hour = 1_699_999_200_000
    store.append_sample(sample(hour + 1_000, 10.0))
    write_started = threading.Event()
    append_attempted = threading.Event()
    original_write = telemetry_store_module.pq.write_table

    def synchronized_write(*args: object, **kwargs: object) -> None:
        write_started.set()
        assert append_attempted.wait(timeout=10)
        original_write(*args, **kwargs)

    monkeypatch.setattr(telemetry_store_module.pq, "write_table", synchronized_write)
    finalizer_errors: list[BaseException] = []
    writer_errors: list[BaseException] = []

    def finalize() -> None:
        try:
            connection = telemetry_store_module._connect(store.path, publisher=True)
            connection.row_factory = sqlite3.Row
            try:
                assert store._finalize_completed_hours(connection, hour + 3_600_000) == 1
            finally:
                connection.close()
        except BaseException as error:  # pragma: no cover
            finalizer_errors.append(error)

    def append_late() -> None:
        append_attempted.set()
        try:
            store.append_sample(sample(hour + 2_000, 20.0))
        except BaseException as error:
            writer_errors.append(error)

    finalizer = threading.Thread(target=finalize)
    finalizer.start()
    assert write_started.wait(timeout=10)
    writer = threading.Thread(target=append_late)
    writer.start()
    finalizer.join(timeout=30)
    writer.join(timeout=30)
    assert finalizer_errors == []
    assert len(writer_errors) == 1
    assert isinstance(writer_errors[0], sqlite3.IntegrityError)
    with open_read_only(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM raw_samples").fetchone()[0] == 1
        assert connection.execute("SELECT row_count FROM telemetry_partitions").fetchone()[0] == 1


def test_failed_partition_metadata_insert_removes_published_file(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    hour = 1_699_999_200_000
    store.append_sample(sample(hour + 1_000, 10.0))
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_partition_insert BEFORE INSERT ON telemetry_partitions "
            "BEGIN SELECT RAISE(ABORT, 'simulated metadata failure'); END"
        )
    connection = telemetry_store_module._connect(store.path, publisher=True)
    connection.row_factory = sqlite3.Row
    try:
        with pytest.raises(sqlite3.IntegrityError, match="simulated metadata failure"):
            store._finalize_completed_hours(connection, hour + 3_600_000)
    finally:
        connection.close()
    assert list(store.partition_root.rglob("*.parquet")) == []
    assert list(store.partition_root.rglob("*.tmp")) == []
    with open_read_only(store.path) as reader:
        assert reader.execute("SELECT COUNT(*) FROM telemetry_partitions").fetchone()[0] == 0


def test_failed_publisher_cleanup_cannot_delete_a_concurrent_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    hour = 1_699_999_200_000
    store.append_sample(sample(hour + 1_000, 10.0))
    destination = store.partition_root / "raw" / f"hour-{hour}.parquet"
    first_failed = threading.Event()
    second_replaced = threading.Event()
    original_open = telemetry_store_module.os.open
    original_replace = telemetry_store_module.os.replace
    original_unlink = Path.unlink
    directory_failure_injected = False

    def fail_first_directory_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal directory_failure_injected
        if (
            threading.current_thread().name == "failing-finalizer"
            and Path(path) == destination.parent
            and not directory_failure_injected
        ):
            directory_failure_injected = True
            first_failed.set()
            raise OSError("simulated pre-metadata directory fsync failure")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    def observe_second_replace(
        source: str | os.PathLike[str],
        target: str | os.PathLike[str],
    ) -> None:
        original_replace(source, target)
        if threading.current_thread().name == "successful-finalizer" and Path(target) == destination:
            second_replaced.set()

    def delay_first_cleanup(path: Path, missing_ok: bool = False) -> None:
        if threading.current_thread().name == "failing-finalizer" and path == destination:
            second_replaced.wait(timeout=1)
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(telemetry_store_module.os, "open", fail_first_directory_open)
    monkeypatch.setattr(telemetry_store_module.os, "replace", observe_second_replace)
    monkeypatch.setattr(Path, "unlink", delay_first_cleanup)
    results: list[int] = []
    errors: list[BaseException] = []

    def finalize() -> None:
        connection = telemetry_store_module._connect(store.path, publisher=True)
        try:
            results.append(store._finalize_completed_hours(connection, hour + 3_600_000))
        except BaseException as error:
            errors.append(error)
        finally:
            connection.close()

    failing = threading.Thread(target=finalize, name="failing-finalizer")
    failing.start()
    assert first_failed.wait(timeout=10)
    successful = threading.Thread(target=finalize, name="successful-finalizer")
    successful.start()
    failing.join(timeout=30)
    successful.join(timeout=30)
    assert len(errors) == 1
    assert isinstance(errors[0], OSError)
    assert results == [1]
    assert destination.is_file()
    with open_read_only(store.path) as connection:
        metadata = connection.execute(
            "SELECT content_sha256, size_bytes FROM telemetry_partitions"
        ).fetchone()
    assert tuple(metadata) == (
        telemetry_store_module._file_sha256(destination),
        destination.stat().st_size,
    )


def test_initialize_removes_unregistered_partition_and_temporary_files(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    orphan = store.partition_root / "raw" / "hour-orphan.parquet"
    temporary = store.partition_root / "raw" / ".hour-orphan.parquet.crash.tmp"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"orphan")
    temporary.write_bytes(b"temporary")
    store.initialize()
    assert not orphan.exists()
    assert not temporary.exists()


def test_initialize_cannot_delete_a_partition_during_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    hour = 1_699_999_200_000
    store.append_sample(sample(hour + 1_000, 10.0))
    published = threading.Event()
    initialize_started = threading.Event()
    original_replace = telemetry_store_module.os.replace

    def blocked_replace(
        source: str | bytes | os.PathLike[str],
        destination: str | bytes | os.PathLike[str],
    ) -> None:
        original_replace(source, destination)
        if str(destination).endswith(".parquet") and str(source).endswith(".tmp"):
            published.set()
            assert initialize_started.wait(timeout=10)

    monkeypatch.setattr(telemetry_store_module.os, "replace", blocked_replace)
    errors: list[BaseException] = []

    def finalize() -> None:
        try:
            connection = telemetry_store_module._connect(store.path, publisher=True)
            try:
                assert store._finalize_completed_hours(connection, hour + 3_600_000) == 1
            finally:
                connection.close()
        except BaseException as error:  # pragma: no cover
            errors.append(error)

    def initialize() -> None:
        initialize_started.set()
        try:
            store.initialize()
        except BaseException as error:  # pragma: no cover
            errors.append(error)

    finalizer = threading.Thread(target=finalize)
    finalizer.start()
    assert published.wait(timeout=10)
    initializer = threading.Thread(target=initialize)
    initializer.start()
    finalizer.join(timeout=30)
    initializer.join(timeout=30)
    assert errors == []
    partition = next(store.partition_root.rglob("*.parquet"))
    with open_read_only(store.path) as connection:
        metadata = connection.execute(
            "SELECT content_sha256, size_bytes FROM telemetry_partitions"
        ).fetchone()
    assert tuple(metadata) == (
        telemetry_store_module._file_sha256(partition),
        partition.stat().st_size,
    )


def test_telemetry_path_rejects_jobs_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    jobs_path = tmp_path / "biomodstack.db"
    monkeypatch.setenv("BMS_DB_PATH", str(jobs_path))
    monkeypatch.setenv("BMS_TELEMETRY_DB_PATH", str(jobs_path))
    from telemetry_store import telemetry_db_path

    with pytest.raises(ValueError, match="separate"):
        telemetry_db_path()


def test_retention_preserves_expired_raw_without_verified_parquet(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    now_ms = 2_000_000_000_000
    raw_cutoff = now_ms - RAW_RETENTION_SECONDS * 1000
    aggregate_cutoff = now_ms - AGGREGATE_RETENTION_SECONDS * 1000
    for timestamp_ms in (raw_cutoff - 1, raw_cutoff, now_ms):
        store.append_sample(sample(timestamp_ms, 10.0))
    store.insert_minute_for_test(aggregate_cutoff - 60_000, sample(aggregate_cutoff - 60_000, 10.0), 1)
    store.insert_minute_for_test(aggregate_cutoff, sample(aggregate_cutoff, 10.0), 1)

    with pytest.raises(RuntimeError, match="verified Parquet"):
        store.apply_retention(now_ms)
    assert [point["timestamp_ms"] for point in store.read_history(start_ms=0, end_ms=now_ms + 1, resolution="raw", limit=10)] == [
        raw_cutoff - 1,
        raw_cutoff,
        now_ms,
    ]
    assert [point["timestamp_ms"] for point in store.read_history(start_ms=0, end_ms=now_ms + 1, resolution="minute", limit=10)] == [
        aggregate_cutoff - 60_000,
        aggregate_cutoff,
    ]


def test_retention_restores_partition_when_metadata_delete_fails(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    hour = 1_699_999_200_000
    store.append_sample(sample(hour + 1_000, 10.0))
    store.finalize_completed_minutes(hour + 3_600_000)
    partition = next(store.partition_root.rglob("*.parquet"))
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_partition_retention BEFORE DELETE ON telemetry_partitions "
            "BEGIN SELECT RAISE(ABORT, 'simulated metadata delete failure'); END"
        )
    now_ms = hour + AGGREGATE_RETENTION_SECONDS * 1000 + 3_600_001
    with pytest.raises(sqlite3.DatabaseError, match="simulated metadata delete failure"):
        store.apply_retention(now_ms)
    with open_read_only(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM telemetry_partitions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM raw_samples").fetchone()[0] == 0
    assert partition.exists()
    assert not Path(f"{partition}.retiring").exists()
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER reject_partition_retention")
    points = store.read_history(
        start_ms=hour,
        end_ms=hour + 3_600_000,
        resolution="raw",
        limit=10,
    )
    assert [point["timestamp_ms"] for point in points] == [hour + 1_000]


def test_initialize_restores_registered_partition_after_retention_crash(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    hour = 1_699_999_200_000
    store.append_sample(sample(hour + 1_000, 10.0))
    store.finalize_completed_minutes(hour + 3_600_000)
    partition = next(store.partition_root.rglob("*.parquet"))
    retiring = Path(f"{partition}.retiring")
    os.replace(partition, retiring)
    store.initialize()
    assert partition.is_file()
    assert not retiring.exists()


def test_initialize_rejects_registered_partition_with_no_recoverable_file(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    hour = 1_699_999_200_000
    store.append_sample(sample(hour + 1_000, 10.0))
    store.finalize_completed_minutes(hour + 3_600_000)
    partition = next(store.partition_root.rglob("*.parquet"))
    partition.unlink()
    with pytest.raises(RuntimeError, match="missing or invalid"):
        store.initialize()


def test_collection_freshness_is_bound_to_latest_typed_sample(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    base = 1_700_000_000_000
    assert store.read_freshness(now_ms=base, stale_after_ms=15_000)["status"] == "empty"
    store.append_sample(sample(base, 10.0))
    assert store.read_freshness(now_ms=base + 15_000, stale_after_ms=15_000)["ready"] is True
    stale = store.read_freshness(now_ms=base + 15_001, stale_after_ms=15_000)
    assert stale["status"] == "stale"
    assert stale["age_ms"] == 15_001
    future = store.read_freshness(now_ms=base - 1, stale_after_ms=15_000)
    assert future["status"] == "future"


def test_request_reads_skip_global_foreign_key_scan_but_explicit_integrity_runs_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    store.append_sample(sample(1_700_000_000_000, 10.0))
    statements: list[str] = []
    original_open = telemetry_store_module.open_read_only

    def observed_open(path: Path) -> sqlite3.Connection:
        connection = original_open(path)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(telemetry_store_module, "open_read_only", observed_open)
    store.read_history(
        start_ms=1_700_000_000_000,
        end_ms=1_700_000_000_001,
        resolution="raw",
        limit=1,
    )
    store.read_freshness(now_ms=1_700_000_000_001, stale_after_ms=15_000)
    assert all("foreign_key_check" not in statement.lower() for statement in statements)

    statements.clear()
    store.verify_integrity()
    assert any("integrity_check" in statement.lower() for statement in statements)
    assert any("foreign_key_check" in statement.lower() for statement in statements)


def test_chart_history_initial_read_includes_complete_aligned_leading_bucket(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    base = 1_700_000_000_000
    store.append_sample(sample(base + 1_000, 10.0))
    store.append_sample(sample(base + 2_000, 20.0))

    result = store.read_chart_history(
        start_ms=base + 1_500,
        end_ms=base + 4_000,
        bucket_ms=2_000,
        since_ms=None,
        limit=10,
    )

    assert result["effective_start_ms"] == base
    assert [point["timestamp_ms"] for point in result["points"]] == [base, base + 2_000]
    assert result["points"][0]["sample_count"] == 1


def test_chart_history_returns_aligned_buckets_and_recomputes_cursor_bucket(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    base = 1_700_000_000_000
    store.append_sample(sample(base, 10.0))
    store.append_sample(sample(base + 1_000, 20.0))
    store.append_sample(sample(base + 2_000, 30.0))

    initial = store.read_chart_history(
        start_ms=base,
        end_ms=base + 4_000,
        bucket_ms=2_000,
        since_ms=None,
        limit=10,
    )
    assert initial["next_cursor_ms"] == base + 2_000
    assert [point["timestamp_ms"] for point in initial["points"]] == [base, base + 2_000]
    assert initial["points"][0]["sample_count"] == 2
    assert initial["points"][0]["cpu_utilization"] == 15.0
    assert initial["points"][0]["gpus"][0]["index"] == 0
    assert initial["points"][0]["gpus"][0]["utilization"] == 40.0

    store.append_sample(sample(base + 3_000, 40.0))
    delta = store.read_chart_history(
        start_ms=base,
        end_ms=base + 6_000,
        bucket_ms=2_000,
        since_ms=base + 2_000,
        limit=10,
    )
    assert delta["next_cursor_ms"] == base + 3_000
    assert [point["timestamp_ms"] for point in delta["points"]] == [base + 2_000]
    assert delta["points"][0]["sample_count"] == 2
    assert delta["points"][0]["cpu_utilization"] == 35.0

    store.append_sample(sample(base + 6_000, 50.0))
    with_gap = store.read_chart_history(
        start_ms=base,
        end_ms=base + 8_000,
        bucket_ms=2_000,
        since_ms=None,
        limit=10,
    )
    assert [point["timestamp_ms"] for point in with_gap["points"]] == [
        base,
        base + 2_000,
        base + 6_000,
    ]


def test_raw_history_limit_selects_newest_points_across_parquet_and_sqlite(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    hour = 1_699_999_200_000
    old_timestamps = [hour + 1_000, hour + 2_000, hour + 3_000]
    hot_timestamps = [hour + 3_601_000, hour + 3_602_000]
    for index, timestamp_ms in enumerate(old_timestamps + hot_timestamps):
        store.append_sample(sample(timestamp_ms, float(index)))
    store.finalize_completed_minutes(hour + 7_200_000)

    with open_read_only(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM raw_samples WHERE timestamp_ms < ?",
            (hour + 3_600_000,),
        ).fetchone()[0] == 0
    points = store.read_history(
        start_ms=hour,
        end_ms=hour + 7_200_000,
        resolution="raw",
        limit=3,
    )
    assert [point["timestamp_ms"] for point in points] == [
        old_timestamps[-1],
        hot_timestamps[0],
        hot_timestamps[1],
    ]


def test_history_is_range_bounded_ordered_and_resolution_limited(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    base = 1_700_000_000_000
    for offset in range(5):
        store.append_sample(sample(base + offset * 1000, float(offset)))
    points = store.read_history(start_ms=base + 1000, end_ms=base + 5000, resolution="raw", limit=2)
    assert [point["timestamp_ms"] for point in points] == [base + 3000, base + 4000]
    with pytest.raises(ValueError, match="limit"):
        store.read_history(start_ms=base, end_ms=base + 5000, resolution="raw", limit=0)
    with pytest.raises(ValueError, match="resolution"):
        store.read_history(start_ms=base, end_ms=base + 5000, resolution="hour", limit=10)


def test_history_parent_and_children_share_one_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    timestamp_ms = 1_700_000_000_000
    store.append_sample(sample(timestamp_ms, 10.0))
    hour_start_ms = (timestamp_ms // 3_600_000) * 3_600_000
    store.finalize_completed_minutes(hour_start_ms + 3_600_000)
    parent_selected = threading.Event()
    continue_children = threading.Event()
    original_payloads = store._payloads_for_rows

    def blocked_payloads(
        connection: sqlite3.Connection,
        rows: list[sqlite3.Row],
        *,
        prefix: str,
    ) -> dict[int, dict[str, object]]:
        parent_selected.set()
        assert continue_children.wait(timeout=10)
        return original_payloads(connection, rows, prefix=prefix)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "_payloads_for_rows", blocked_payloads)
    result: list[dict[str, object]] = []
    errors: list[BaseException] = []

    def read() -> None:
        try:
            result.extend(
                store.read_history(
                    start_ms=timestamp_ms,
                    end_ms=timestamp_ms + 1,
                    resolution="raw",
                    limit=10,
                )
            )
        except BaseException as error:  # pragma: no cover
            errors.append(error)

    reader = threading.Thread(target=read)
    reader.start()
    assert parent_selected.wait(timeout=10)
    store.apply_retention(timestamp_ms + RAW_RETENTION_SECONDS * 1000 + 1)
    continue_children.set()
    reader.join(timeout=30)
    assert errors == []
    payload = result[0]["payload"]
    assert isinstance(payload, dict)
    assert payload["cpu"]["per_core_utilization"] == [10.0, 20.0]
    assert payload["gpus"][0]["processes"] == [
        {"pid": 123, "name": "worker", "memory_mb": 512}
    ]


def test_wal_reader_writer_and_retention_can_run_concurrently(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.sqlite3"
    writer = TelemetryStore(path)
    reader = TelemetryStore(path)
    writer.initialize()
    base = 2_000_000_000_000
    errors: list[BaseException] = []

    def write_samples() -> None:
        try:
            for offset in range(40):
                writer.append_sample(sample(base + offset * 1000, float(offset)))
                if offset % 10 == 0:
                    writer.apply_retention(base + offset * 1000)
        except BaseException as error:  # pragma: no cover
            errors.append(error)

    thread = threading.Thread(target=write_samples)
    thread.start()
    while thread.is_alive():
        reader.read_history(start_ms=base, end_ms=base + 60_000, resolution="raw", limit=100)
    thread.join()
    assert errors == []
    assert len(reader.read_history(start_ms=base, end_ms=base + 60_000, resolution="raw", limit=100)) == 40
