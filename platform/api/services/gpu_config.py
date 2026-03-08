"""
Centralized GPU scheduler configuration I/O.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict
import json
import logging
import os
import tempfile

from paths import get_code_root

logger = logging.getLogger(__name__)

PROJECT_ROOT = get_code_root()
GPU_CONFIG_PATH = PROJECT_ROOT / ".gpu_config.json"

DEFAULT_SCHEDULER_CONFIG: Dict[str, Any] = {
    "global": {
        "busy_threshold": 0.5,
        "cooldown_ms": 10000,
        "enabled": True,
        "target_vram_fill": 0.85,
        "capacity_weight": 3.0,
        "emptiness_weight": 5.0,
        "max_launches_per_cycle": 3,
        "msa_concurrency_limit": 1,
        "msa_preferred_gpu_ids": [],
        "msa_avoid_heavy_gpus": False,
    },
    "overrides": {},
    "workflow_pins": {},
    "gpu_locks": {},
    "concurrency_limits": {},
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def read_scheduler_config() -> Dict[str, Any]:
    """Read scheduler config from file, merged with defaults."""
    if not GPU_CONFIG_PATH.exists():
        return deepcopy(DEFAULT_SCHEDULER_CONFIG)
    try:
        with open(GPU_CONFIG_PATH, "r") as f:
            config = json.load(f)
        return _deep_merge(DEFAULT_SCHEDULER_CONFIG, config)
    except Exception as exc:
        logger.warning(f"Failed to read scheduler config: {exc}")
        return deepcopy(DEFAULT_SCHEDULER_CONFIG)


def write_scheduler_config(config: Dict[str, Any]) -> bool:
    """Write scheduler config to file."""
    try:
        GPU_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = None
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=str(GPU_CONFIG_PATH.parent),
            prefix=".gpu_config.",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        ) as f:
            tmp_path = Path(f.name)
            json.dump(config, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, GPU_CONFIG_PATH)
        return True
    except Exception as exc:
        logger.error(f"Failed to write scheduler config: {exc}")
        try:
            if 'tmp_path' in locals() and tmp_path and tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        return False
