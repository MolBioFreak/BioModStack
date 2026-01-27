"""
Centralized GPU scheduler configuration I/O.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict
import json
import logging

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
        with open(GPU_CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as exc:
        logger.error(f"Failed to write scheduler config: {exc}")
        return False
