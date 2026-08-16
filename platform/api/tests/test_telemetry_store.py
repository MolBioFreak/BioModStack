from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from telemetry_store import (
    AGGREGATE_RETENTION_SECONDS,
    RAW_RETENTION_SECONDS,
    TelemetryStore,
    open_read_only,
)


def sample(timestamp_ms: int, cpu: float, gpu_util: float = 40.0) -> dict[str, object]:
    return {
        "timestamp_ms": timestamp_ms,
        "timestamp": datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z"),
        "cpu": {"utilization": cpu, "per_core_utilization": [cpu, cpu + 10.0], "frequency_current_mhz": 2000.0, "power_watts": 80.0, "temperature": 55.0},
        "ram": {"used_gb": 16.0, "available_gb": 48.0, "utilization": 25.0, "swap_used_gb": 0.0},
        "gpus": [{"index": 0, "utilization": gpu_util, "memory_used_mb": 1024.0, "memory_free_mb": 23000.0, "temperature": 60.0, "power_draw_watts": 120.0, "fan_speed_percent": 30.0}],
        "gpu_error": None,
    }


def test_store_is_separate_append_only_and_readers_are_query_only(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.sqlite3"
    store = TelemetryStore(path)
    store.initialize()
    store.append_sample(sample(1_700_000_000_000, 10.0))

    with pytest.raises(sqlite3.IntegrityError):
        store.append_sample(sample(1_700_000_000_000, 99.0))

    with sqlite3.connect(path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE raw_samples SET payload_json = '{}' WHERE timestamp_ms = ?", (1_700_000_000_000,))

    with open_read_only(path) as connection:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        assert json.loads(connection.execute("SELECT payload_json FROM raw_samples").fetchone()[0])["cpu"]["utilization"] == 10.0
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM raw_samples")


def test_completed_minute_aggregate_is_finalized_once(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    minute = 1_700_000_040_000
    store.append_sample(sample(minute + 1_000, 10.0, 20.0))
    store.append_sample(sample(minute + 31_000, 30.0, 60.0))

    assert store.finalize_completed_minutes(minute + 60_000) == 1
    assert store.finalize_completed_minutes(minute + 120_000) == 0

    points = store.read_history(start_ms=minute, end_ms=minute + 120_000, resolution="minute", limit=10)
    assert len(points) == 1
    assert points[0]["timestamp_ms"] == minute
    assert points[0]["sample_count"] == 2
    assert points[0]["payload"]["cpu"]["utilization"] == 20.0
    assert points[0]["payload"]["gpus"][0]["utilization"] == 40.0
    assert points[0]["payload"]["cpu"]["per_core_utilization"] == [20.0, 30.0]
    assert points[0]["payload"]["timestamp_ms"] == minute
    assert points[0]["payload"]["timestamp"] == datetime.fromtimestamp(minute / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def test_telemetry_path_rejects_jobs_database(monkeypatch, tmp_path: Path) -> None:
    jobs_path = tmp_path / "biomodstack.db"
    monkeypatch.setenv("BMS_DB_PATH", str(jobs_path))
    monkeypatch.setenv("BMS_TELEMETRY_DB_PATH", str(jobs_path))
    from telemetry_store import telemetry_db_path

    with pytest.raises(ValueError, match="separate"):
        telemetry_db_path()


def test_retention_deletes_only_expired_rows_and_preserves_boundary(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    store.initialize()
    now_ms = 2_000_000_000_000
    raw_cutoff = now_ms - RAW_RETENTION_SECONDS * 1000
    aggregate_cutoff = now_ms - AGGREGATE_RETENTION_SECONDS * 1000

    for timestamp_ms in (raw_cutoff - 1, raw_cutoff, now_ms):
        store.append_sample(sample(timestamp_ms, 10.0))
    store.insert_minute_for_test(aggregate_cutoff - 60_000, sample(aggregate_cutoff - 60_000, 10.0), 1)
    store.insert_minute_for_test(aggregate_cutoff, sample(aggregate_cutoff, 10.0), 1)

    assert store.apply_retention(now_ms) == {"raw_deleted": 1, "minute_deleted": 1}
    assert [point["timestamp_ms"] for point in store.read_history(start_ms=0, end_ms=now_ms + 1, resolution="raw", limit=10)] == [raw_cutoff, now_ms]
    assert [point["timestamp_ms"] for point in store.read_history(start_ms=0, end_ms=now_ms + 1, resolution="minute", limit=10)] == [aggregate_cutoff]


def test_store_reopens_with_persisted_history(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.sqlite3"
    first = TelemetryStore(path)
    first.initialize()
    first.append_sample(sample(1_700_000_000_000, 17.0))

    reopened = TelemetryStore(path)
    reopened.initialize()
    points = reopened.read_history(
        start_ms=1_699_999_999_000,
        end_ms=1_700_000_001_000,
        resolution="raw",
        limit=10,
    )
    assert len(points) == 1
    assert points[0]["payload"]["cpu"]["utilization"] == 17.0


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
        except BaseException as error:  # pragma: no cover - asserted below
            errors.append(error)

    thread = threading.Thread(target=write_samples)
    thread.start()
    while thread.is_alive():
        reader.read_history(
            start_ms=base,
            end_ms=base + 60_000,
            resolution="raw",
            limit=100,
        )
    thread.join()

    assert errors == []
    points = reader.read_history(start_ms=base, end_ms=base + 60_000, resolution="raw", limit=100)
    assert len(points) == 40
