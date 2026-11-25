"""Shared RCSB PDB cache paths and metadata helpers."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import time
from typing import Optional

from paths import get_analysis_cache_dir

logger = logging.getLogger(__name__)

RCSB_CACHE_DIR = get_analysis_cache_dir() / "rcsb"
RCSB_BASE_URL = "https://files.rcsb.org/download"
RCSB_SEARCH_URL = "https://search.rcsb.org/rcsbsearch/v2/query"


def isoformat_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def metadata_path(cache_path: Path) -> Path:
    return cache_path.with_suffix(f"{cache_path.suffix}.meta.json")


def get_cache_timestamps(cache_path: Path) -> tuple[str, Optional[str]]:
    stat = cache_path.stat()
    cached_at = isoformat_timestamp(stat.st_mtime)
    last_used_at: Optional[str] = None
    meta_path = metadata_path(cache_path)
    if meta_path.exists():
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            cached_at = payload.get("cached_at") or cached_at
            last_used_at = payload.get("last_used_at") or None
        except Exception as exc:
            logger.warning(f"[RCSB] Failed to read cache metadata for {cache_path}: {exc}")
    return cached_at, last_used_at


def write_cache_metadata(cache_path: Path, *, cached_at: str, last_used_at: Optional[str]) -> None:
    meta_path = metadata_path(cache_path)
    meta_path.write_text(
        json.dumps(
            {
                "cached_at": cached_at,
                "last_used_at": last_used_at,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def touch_last_used(cache_path: Path) -> None:
    try:
        cached_at, _ = get_cache_timestamps(cache_path)
        write_cache_metadata(
            cache_path,
            cached_at=cached_at,
            last_used_at=isoformat_timestamp(time.time()),
        )
    except Exception as exc:
        logger.warning(f"[RCSB] Failed to update last-used timestamp for {cache_path}: {exc}")
