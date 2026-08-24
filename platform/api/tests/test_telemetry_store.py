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
        "UPDATE raw_cpu_cores SET utilization = 99",
        "DELETE FROM raw_cpu_cores",
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
        "INSERT INTO raw_cpu_cores VALUES (1700000000000, 99, 1)",
        "INSERT INTO raw_gpu_samples(timestamp_ms, gpu_index, name) VALUES (1700000000000, 99, 'x')",
        "INSERT INTO raw_gpu_processes VALUES (1700000000000, 0, 999, 'x', 1)",
        "INSERT INTO minute_cpu_cores VALUES (1700000000000, 99, 1)",
        "INSERT INTO minute_gpu_samples(timestamp_ms, gpu_index, name) VALUES (1700000000000, 99, 'x')",
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
            "SELECT cpu_utilization, ram_utilization FROM raw_samples"
        ).fetchone()
        assert tuple(row) == (10.0, 25.0)
        assert [tuple(item) for item in connection.execute("SELECT utilization FROM raw_cpu_cores ORDER BY core_index")] == [(10.0,), (20.0,)]
        assert tuple(connection.execute("SELECT name, utilization FROM raw_gpu_samples").fetchone()) == ("Test GPU", 40.0)
        assert tuple(connection.execute("SELECT pid, name, memory_mb FROM raw_gpu_processes").fetchone()) == (123, "worker", 512)
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM raw_samples")


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
        connection.execute("DROP TRIGGER raw_cpu_cores_guard_update")
        connection.execute(
            "CREATE TRIGGER raw_cpu_cores_guard_update BEFORE UPDATE ON raw_cpu_cores "
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


def test_retention_deletes_only_expired_typed_rows(tmp_path: Path) -> None:
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
        assert connection.execute("SELECT COUNT(*) FROM raw_samples").fetchone()[0] == 1
    assert partition.exists()
    assert not Path(f"{partition}.retiring").exists()


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
