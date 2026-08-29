from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import telemetry
from telemetry_store import RAW_RETENTION_SECONDS, TelemetryStore


def _sample(timestamp_ms: int) -> dict[str, object]:
    return {"timestamp_ms": timestamp_ms, "cpu": {"utilization": 12.0}, "ram": {}, "gpus": []}


def test_history_endpoint_returns_bounded_persisted_rows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "telemetry.sqlite3"
    store = TelemetryStore(path)
    store.initialize()
    store.append_sample(_sample(1_700_000_000_000))
    monkeypatch.setenv("BMS_TELEMETRY_DB_PATH", str(path))

    result = telemetry.telemetry_history(
        start_ms=1_699_999_999_000,
        end_ms=1_700_000_001_000,
        resolution="raw",
        limit=10,
    )
    assert result["resolution"] == "raw"
    assert len(result["points"]) == 1
    assert result["points"][0]["timestamp_ms"] == 1_700_000_000_000


def test_history_endpoint_rejects_unbounded_raw_range(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "telemetry.sqlite3"
    TelemetryStore(path).initialize()
    monkeypatch.setenv("BMS_TELEMETRY_DB_PATH", str(path))

    with pytest.raises(HTTPException) as error:
        telemetry.telemetry_history(
            start_ms=0,
            end_ms=RAW_RETENTION_SECONDS * 1000 + 1,
            resolution="raw",
            limit=10,
        )
    assert error.value.status_code == 422


def test_history_endpoint_reports_unavailable_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BMS_TELEMETRY_DB_PATH", str(tmp_path / "missing.sqlite3"))
    with pytest.raises(HTTPException) as error:
        telemetry.telemetry_history(start_ms=1, end_ms=2, resolution="raw", limit=10)
    assert error.value.status_code == 503


def test_history_endpoint_reports_invalid_schema_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "invalid.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE raw_samples(timestamp_ms INTEGER PRIMARY KEY, payload_json TEXT NOT NULL)"
        )
    monkeypatch.setenv("BMS_TELEMETRY_DB_PATH", str(path))
    with pytest.raises(HTTPException) as error:
        telemetry.telemetry_history(start_ms=1, end_ms=2, resolution="raw", limit=10)
    assert error.value.status_code == 503


def test_chart_history_router_returns_compact_incremental_buckets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "telemetry.sqlite3"
    store = TelemetryStore(path)
    store.initialize()
    base = 1_700_000_000_000
    store.append_sample(_sample(base))
    store.append_sample(_sample(base + 1_000))
    store.append_sample(_sample(base + 2_000))
    monkeypatch.setenv("BMS_TELEMETRY_DB_PATH", str(path))
    app = FastAPI()
    app.include_router(telemetry.router, prefix="/api")

    with TestClient(app) as client:
        initial = client.get(
            "/api/telemetry/chart-history",
            params={"start_ms": base, "end_ms": base + 4_000, "bucket_ms": 2_000},
        )
        delta = client.get(
            "/api/telemetry/chart-history",
            params={
                "start_ms": base,
                "end_ms": base + 4_000,
                "bucket_ms": 2_000,
                "since_ms": base + 2_000,
            },
        )
        invalid_bucket = client.get(
            "/api/telemetry/chart-history",
            params={"start_ms": base, "end_ms": base + 4_000, "bucket_ms": 999},
        )
        invalid_span = client.get(
            "/api/telemetry/chart-history",
            params={"start_ms": base, "end_ms": base + 3_600_001, "bucket_ms": 30_000},
        )

    assert initial.status_code == 200
    body = initial.json()
    assert body["bucket_ms"] == 2_000
    assert body["next_cursor_ms"] == base + 2_000
    assert [point["timestamp_ms"] for point in body["points"]] == [base, base + 2_000]
    assert "payload" not in body["points"][0]
    assert delta.status_code == 200
    assert [point["timestamp_ms"] for point in delta.json()["points"]] == [base + 2_000]
    assert invalid_bucket.status_code == 422
    assert invalid_span.status_code == 422


def test_history_router_registration_and_query_validation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "telemetry.sqlite3"
    store = TelemetryStore(path)
    store.initialize()
    store.append_sample(_sample(1_700_000_000_000))
    monkeypatch.setenv("BMS_TELEMETRY_DB_PATH", str(path))
    app = FastAPI()
    app.include_router(telemetry.router, prefix="/api")

    with TestClient(app) as client:
        response = client.get(
            "/api/telemetry/history",
            params={
                "start_ms": 1_699_999_999_000,
                "end_ms": 1_700_000_001_000,
                "resolution": "raw",
                "limit": 10,
            },
        )
        invalid = client.get(
            "/api/telemetry/history",
            params={"start_ms": 1, "end_ms": 2, "resolution": "hour", "limit": 10},
        )

    assert response.status_code == 200
    assert response.json()["points"][0]["timestamp_ms"] == 1_700_000_000_000
    assert invalid.status_code == 422
