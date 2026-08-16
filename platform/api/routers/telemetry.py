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
    except (sqlite3.Error, OSError):
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
