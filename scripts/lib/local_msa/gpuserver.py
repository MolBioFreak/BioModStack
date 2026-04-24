from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

SCRIPT_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from local_msa_runtime import (
    DEFAULT_GPUSERVER_DB_LOAD_MODE,
    DEFAULT_GPUSERVER_STARTUP_WAIT_SECONDS,
    DEFAULT_GPUSERVER_WAIT_TIMEOUT,
    DEFAULT_MSA_SERVER_STATUS_URL,
    is_isolated_task_runtime,
    is_matching_gpuserver_process,
    normalize_gpuserver_db_load_mode,
    normalize_gpuserver_startup_wait,
    normalize_gpuserver_wait_timeout,
    query_host_gpuserver_status,
)


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    """Atomically write JSON payload to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, path)


__all__ = [
    "DEFAULT_GPUSERVER_DB_LOAD_MODE",
    "DEFAULT_GPUSERVER_STARTUP_WAIT_SECONDS",
    "DEFAULT_GPUSERVER_WAIT_TIMEOUT",
    "DEFAULT_MSA_SERVER_STATUS_URL",
    "_atomic_write_json",
    "is_isolated_task_runtime",
    "is_matching_gpuserver_process",
    "normalize_gpuserver_db_load_mode",
    "normalize_gpuserver_startup_wait",
    "normalize_gpuserver_wait_timeout",
    "query_host_gpuserver_status",
]
