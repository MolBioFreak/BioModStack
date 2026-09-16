"""Exercise real SQLite handles; retain references so GC cannot hide leaks."""
from contextlib import closing
import sqlite3

import pytest

import telemetry_store as module
from test_telemetry_store import sample


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("BMS_TELEMETRY_PARTITION_ROOT", str(tmp_path / "partitions"))
    value = module.TelemetryStore(tmp_path / "telemetry.sqlite3")
    value.initialize()
    value.append_sample(sample(120_000, 20))
    return value


@pytest.fixture
def handles(monkeypatch):
    opened = []
    original = sqlite3.connect

    class Tracked(sqlite3.Connection):
        fail_sql = None
        fail_udf = False
        closed = False

        def execute(self, sql, *args, **kwargs):
            if self.fail_sql and self.fail_sql in sql:
                raise sqlite3.OperationalError("injected SQL failure")
            return super().execute(sql, *args, **kwargs)

        def create_function(self, *args, **kwargs):
            if self.fail_udf:
                raise RuntimeError("injected UDF failure")
            return super().create_function(*args, **kwargs)

        def close(self):
            self.closed = True
            return super().close()

    def connect(*args, **kwargs):
        connection = original(*args, **kwargs, factory=Tracked)
        opened.append(connection)
        return connection

    monkeypatch.setattr(module.sqlite3, "connect", connect)
    yield opened, Tracked
    for connection in opened:
        connection.close()


def assert_closed(opened):
    assert opened
    for connection in opened:
        assert connection.closed
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def operation(store, name):
    return {
        "initialize": lambda: store.initialize(),
        "append": lambda: store.append_sample(sample(121_000, 30)),
        "minute_insert": lambda: store.insert_minute_for_test(60_000, sample(60_000, 10), 1),
        "finalize": lambda: store.finalize_completed_minutes(7_200_000),
        "retention": lambda: store.apply_retention(120_000),
        "history": lambda: store.read_history(start_ms=0, end_ms=180_000, resolution="raw", limit=100),
        "chart": lambda: store.read_chart_history(start_ms=0, end_ms=180_000, bucket_ms=1000, since_ms=None, limit=100),
        "freshness": lambda: store.read_freshness(now_ms=120_000, stale_after_ms=15_000),
        "integrity": lambda: store.verify_integrity(),
    }[name]()


@pytest.mark.parametrize("name", ["initialize", "append", "minute_insert", "finalize", "retention", "history", "chart", "freshness", "integrity"])
@pytest.mark.parametrize("fail", [False, True])
def test_owned_operations_close(store, handles, name, fail):
    opened, tracked = handles
    if fail:
        # After acquisition/setup: exercise transaction/query failure cleanup.
        tracked.fail_sql = {
            "initialize": "SELECT name FROM sqlite_master",
            "append": "INSERT INTO raw_samples",
            "minute_insert": "INSERT INTO minute_aggregates",
            "finalize": "INSERT INTO telemetry_partitions",
            "retention": "DELETE FROM minute_aggregates",
            "history": "SELECT * FROM raw_samples",
            "chart": "AVG(cpu_utilization)",
            "freshness": "(SELECT MAX(timestamp_ms)",
            "integrity": "PRAGMA integrity_check",
        }[name]
        with pytest.raises((sqlite3.Error, RuntimeError)):
            operation(store, name)
    else:
        operation(store, name)
    tracked.fail_sql = None
    assert_closed(opened)
    if fail and name == "append":
        assert len(store.read_history(start_ms=0, end_ms=180_000, resolution="raw", limit=100)) == 1


@pytest.mark.parametrize("helper", ["writer", "reader", "v2_schema", "v3_schema"])
@pytest.mark.parametrize("fail", [False, True])
def test_acquisition_and_schema_helpers_close(store, handles, monkeypatch, helper, fail):
    opened, tracked = handles
    if helper in {"v2_schema", "v3_schema"}:
        attr = "_EXPECTED_V2_SCHEMA_OBJECTS" if helper == "v2_schema" else "_EXPECTED_SCHEMA_OBJECTS"
        monkeypatch.setattr(module, attr, None)
        call = module._expected_v2_schema_objects if helper == "v2_schema" else module._expected_schema_objects
        if fail:
            if helper == "v2_schema":
                tracked.fail_udf = True
            else:
                monkeypatch.setattr(module, "_SCHEMA", "INVALID SQL")
    else:
        call = (lambda: module._connect(store.path)) if helper == "writer" else (lambda: module.open_read_only(store.path))
        if fail:
            tracked.fail_sql = "PRAGMA"
    if fail:
        with pytest.raises((sqlite3.Error, RuntimeError)):
            call()
    elif helper in {"writer", "reader"}:
        connection = call()
        assert isinstance(connection, sqlite3.Connection)
        with closing(connection):
            assert connection.execute("SELECT 1").fetchone()[0] == 1
    else:
        assert call()
    tracked.fail_sql = None
    assert_closed(opened)


@pytest.mark.parametrize("error_type", [sqlite3.OperationalError, RuntimeError, ValueError, KeyboardInterrupt])
def test_validation_failure_closes_before_transfer(store, handles, monkeypatch, error_type):
    def fail(*args, **kwargs):
        raise error_type("injected validation failure")

    monkeypatch.setattr(module, "_validate_schema", fail)
    expected = RuntimeError if error_type is sqlite3.OperationalError else error_type
    with pytest.raises(expected):
        store._validated_read_connection()
    assert_closed(handles[0])


def test_borrowed_connection_remains_open_and_snapshot_active(store, handles):
    with closing(store._validated_read_connection()) as connection:
        assert connection.in_transaction
        rows, payloads = store._read_payloads(connection, prefix="raw", start_ms=0, end_ms=180_000)
        assert len(rows) == len(payloads) == 1
        assert connection.in_transaction
        assert not connection.closed
        assert connection.execute("SELECT 1").fetchone()[0] == 1
    assert_closed(handles[0])
