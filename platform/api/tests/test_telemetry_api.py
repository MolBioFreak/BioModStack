from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

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
