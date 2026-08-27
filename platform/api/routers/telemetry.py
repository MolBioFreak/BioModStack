from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from telemetry_store import (
    AGGREGATE_RETENTION_SECONDS,
    MAX_HISTORY_POINTS,
    RAW_RETENTION_SECONDS,
    TelemetryStore,
    telemetry_db_path,
)

router = APIRouter()

MAX_CHART_SPAN_MS = 60 * 60 * 1000
MIN_CHART_BUCKET_MS = 1_000
MAX_CHART_BUCKET_MS = 60_000
MAX_CHART_POINTS = 500


@router.get("/telemetry/chart-history")
def telemetry_chart_history(
    start_ms: int = Query(..., ge=0),
    end_ms: int = Query(..., ge=1),
    bucket_ms: int = Query(..., ge=MIN_CHART_BUCKET_MS, le=MAX_CHART_BUCKET_MS),
    since_ms: int | None = Query(None, ge=0),
):
    if start_ms >= end_ms:
        raise HTTPException(status_code=422, detail="start_ms must be earlier than end_ms")
    if end_ms - start_ms > MAX_CHART_SPAN_MS:
        raise HTTPException(status_code=422, detail="chart history range is too large")
    if since_ms is not None and not start_ms <= since_ms < end_ms:
        raise HTTPException(status_code=422, detail="since_ms must be inside the requested range")
    aligned_bucket_count = ((end_ms - 1) // bucket_ms) - (start_ms // bucket_ms) + 1
    if aligned_bucket_count > MAX_CHART_POINTS:
        raise HTTPException(status_code=422, detail="chart history would return too many buckets")
    path: Path = telemetry_db_path()
    if not path.is_file():
        raise HTTPException(status_code=503, detail="Telemetry history is unavailable")
    try:
        result = TelemetryStore(path).read_chart_history(
            start_ms=start_ms,
            end_ms=end_ms,
            bucket_ms=bucket_ms,
            since_ms=since_ms,
            limit=MAX_CHART_POINTS,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except (sqlite3.Error, OSError, RuntimeError):
        raise HTTPException(status_code=503, detail="Telemetry history is unavailable") from None
    return {
        "source": "immutable_server_telemetry",
        "database": "dedicated_telemetry_store",
        "resolution": "server_bucketed_raw",
        "start_ms": start_ms,
        "end_ms": end_ms,
        "effective_start_ms": result["effective_start_ms"],
        "bucket_ms": bucket_ms,
        "generated_at_ms": int(time.time() * 1000),
        "next_cursor_ms": result["next_cursor_ms"],
        "points": result["points"],
    }


@router.get("/telemetry/history")
def telemetry_history(
    start_ms: int = Query(..., ge=0),
    end_ms: int = Query(..., ge=1),
    resolution: Literal["raw", "minute"] = "raw",
    limit: int = Query(4000, ge=1, le=MAX_HISTORY_POINTS),
):
    max_span_ms = (RAW_RETENTION_SECONDS if resolution == "raw" else AGGREGATE_RETENTION_SECONDS) * 1000
    if start_ms >= end_ms:
        raise HTTPException(status_code=422, detail="start_ms must be earlier than end_ms")
    if end_ms - start_ms > max_span_ms:
        raise HTTPException(status_code=422, detail=f"{resolution} history range is too large")
    path: Path = telemetry_db_path()
    if not path.is_file():
        raise HTTPException(status_code=503, detail="Telemetry history is unavailable")
    try:
        points = TelemetryStore(path).read_history(
            start_ms=start_ms,
            end_ms=end_ms,
            resolution=resolution,
            limit=limit,
        )
    except (sqlite3.Error, OSError, RuntimeError):
        raise HTTPException(status_code=503, detail="Telemetry history is unavailable") from None
    return {
        "source": "immutable_server_telemetry",
        "database": "dedicated_telemetry_store",
        "resolution": resolution,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "generated_at_ms": int(time.time() * 1000),
        "points": points,
    }
