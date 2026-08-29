from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time

import pytest

import telemetry_store as telemetry_store_module
from telemetry_store import TelemetryStore, open_read_only

SAMPLE_COUNT = 3_600
BASE_MS = 1_700_000_000_000


def realistic_sample(timestamp_ms: int, value: float) -> dict[str, object]:
    return {
        "timestamp": "2023-11-14T22:13:20Z",
        "timestamp_ms": timestamp_ms,
        "cpu": {
            "name": "48-core test CPU",
            "cores_physical": 24,
            "cores_logical": 48,
            "utilization": value,
            "per_core_utilization": [float((index + int(value)) % 101) for index in range(48)],
            "frequency_current_mhz": 4200.0,
            "frequency_max_mhz": 5200.0,
            "temperature": 60.0,
            "power_watts": 165.0,
            "power_telemetry": {
                "source": "rapl",
                "available": True,
                "status": "available",
                "message": "",
                "discovered_sources": 1,
                "readable_sources": 1,
                "setup_hint": None,
            },
        },
        "ram": {
            "total_gb": 128.0,
            "used_gb": 32.0,
            "available_gb": 96.0,
            "utilization": 25.0,
            "swap_total_gb": 16.0,
            "swap_used_gb": 0.0,
            "swap_percent": 0.0,
        },
        "gpus": [
            {
                "index": index,
                "name": f"Test GPU {index}",
                "utilization": 50.0 + index,
                "memory_utilization": 25.0,
                "memory_used_mb": 4096.0,
                "memory_total_mb": 16384.0,
                "reserved_memory_mb": 256.0,
                "power_draw_w": 150.0,
                "power_limit_w": 300.0,
                "min_power_watts": 100.0,
                "default_power_watts": 300.0,
                "max_power_watts": 350.0,
                "temperature": 65.0,
                "fan_speed": 40.0,
                "clock_graphics_mhz": 2200.0,
                "clock_memory_mhz": 9000.0,
                "clock_max_graphics_mhz": 2600.0,
                "clock_max_memory_mhz": 10000.0,
                "processes": [
                {"pid": 10_000 + index * 10 + process_index, "name": f"worker-{index}-{process_index}", "memory_mb": 256 + process_index}
                for process_index in range(2)
            ],
            }
            for index in range(3)
        ],
        "gpu_error": None,
    }


@pytest.fixture(scope="module")
def realistic_store(tmp_path_factory: pytest.TempPathFactory) -> tuple[TelemetryStore, int]:
    path = tmp_path_factory.mktemp("telemetry-capacity") / "telemetry.sqlite3"
    store = TelemetryStore(path)
    store.initialize()
    with open_read_only(path) as connection:
        baseline_bytes = int(connection.execute("PRAGMA page_count").fetchone()[0]) * int(
            connection.execute("PRAGMA page_size").fetchone()[0]
        )
    with telemetry_store_module._connect(path, writer=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for index in range(SAMPLE_COUNT):
            store._insert_payload(
                connection,
                prefix="raw",
                payload=realistic_sample(BASE_MS + index * 1_000, float(index % 101)),
                sample_count=1,
            )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return store, baseline_bytes


def test_realistic_hot_store_has_four_dynamic_rows_per_timestamp_and_bounded_size(
    realistic_store: tuple[TelemetryStore, int],
) -> None:
    store, baseline_bytes = realistic_store
    with open_read_only(store.path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert "raw_cpu_cores" not in tables
        assert "raw_gpu_processes" not in tables
        assert "minute_gpu_processes" not in tables
        assert connection.execute("SELECT COUNT(*) FROM raw_samples").fetchone()[0] == SAMPLE_COUNT
        assert connection.execute("SELECT COUNT(*) FROM raw_gpu_samples").fetchone()[0] == SAMPLE_COUNT * 3
        assert connection.execute("SELECT COUNT(*) FROM telemetry_hardware_profiles").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM telemetry_hardware_gpus").fetchone()[0] == 3
        allocated_bytes = int(connection.execute("PRAGMA page_count").fetchone()[0]) * int(
            connection.execute("PRAGMA page_size").fetchone()[0]
        )
    projected_two_hour_hot_bytes = baseline_bytes + 2 * (allocated_bytes - baseline_bytes)
    assert allocated_bytes < 8 * 1024 * 1024
    assert projected_two_hour_hot_bytes < 16 * 1024 * 1024


def test_realistic_one_hour_chart_query_is_subsecond(
    realistic_store: tuple[TelemetryStore, int],
) -> None:
    store, _baseline_bytes = realistic_store
    durations: list[float] = []
    for _ in range(5):
        started = time.perf_counter()
        result = store.read_chart_history(
            start_ms=BASE_MS,
            end_ms=BASE_MS + SAMPLE_COUNT * 1_000,
            bucket_ms=30_000,
            since_ms=None,
            limit=500,
        )
        durations.append(time.perf_counter() - started)
        assert len(result["points"]) in (120, 121)
        assert sum(point["sample_count"] for point in result["points"]) == SAMPLE_COUNT
    assert sorted(durations)[len(durations) // 2] < 0.250
    assert max(durations) < 0.750


def test_incremental_chart_payload_keeps_polling_volume_bounded(
    realistic_store: tuple[TelemetryStore, int],
) -> None:
    store, _baseline_bytes = realistic_store
    end_ms = BASE_MS + SAMPLE_COUNT * 1_000
    initial = store.read_chart_history(
        start_ms=end_ms - 180_000,
        end_ms=end_ms,
        bucket_ms=2_000,
        since_ms=None,
        limit=500,
    )
    delta = store.read_chart_history(
        start_ms=end_ms - 180_000,
        end_ms=end_ms,
        bucket_ms=2_000,
        since_ms=end_ms - 1_000,
        limit=500,
    )
    initial_bytes = len(json.dumps(initial, separators=(",", ":")).encode())
    delta_bytes = len(json.dumps(delta, separators=(",", ":")).encode())
    projected_visible_hour_bytes = initial_bytes + delta_bytes * 3_600

    assert len(initial["points"]) == 90
    assert len(delta["points"]) == 1
    assert initial_bytes < 80_000
    assert delta_bytes < 2_000
    assert projected_visible_hour_bytes < 8 * 1024 * 1024
