"""Persistent GPU scheduler configuration with atomic cross-process updates."""
from __future__ import annotations

import copy
import fcntl
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator

from paths import get_code_root, get_data_root

logger = logging.getLogger(__name__)

PROJECT_ROOT = get_code_root()
LEGACY_GPU_CONFIG_PATH = PROJECT_ROOT / ".gpu_config.json"
SCHEDULER_STATE_DIR = Path(
    os.environ.get("BMS_SCHEDULER_STATE_DIR") or (get_data_root() / "scheduler")
).expanduser().resolve()
GPU_CONFIG_PATH = SCHEDULER_STATE_DIR / "gpu_config.json"
GPU_CONFIG_LOCK_PATH = SCHEDULER_STATE_DIR / "gpu_config.lock"

DEFAULT_SCHEDULER_CONFIG: Dict[str, Any] = {
    "global": {
        "busy_threshold": 0.95,
        "cooldown_ms": 3000,
        "cpu_threads_per_job": 24,
        "auto_cpu_threads": True,
        "auto_cpu_thread_job_threshold": 2,
        "enabled": True,
        "target_vram_fill": 0.90,
        "capacity_weight": 9.0,
        "emptiness_weight": 1.0,
        "max_launches_per_cycle": 3,
        "msa_concurrency_limit": 1,
        "msa_preferred_gpu_ids": [],
        "msa_avoid_heavy_gpus": False,
        "force_run_excluded_gpu_ids": [],
    },
    "overrides": {},
    "workflow_pins": {},
    "gpu_locks": {},
    "concurrency_limits": {},
}


def get_default_config() -> Dict[str, Any]:
    return copy.deepcopy(DEFAULT_SCHEDULER_CONFIG)


def _merge_with_defaults(config: Dict[str, Any]) -> Dict[str, Any]:
    merged = get_default_config()
    global_config = config.get("global")
    if isinstance(global_config, dict):
        merged["global"].update(global_config)
    for key in ("overrides", "workflow_pins", "gpu_locks", "concurrency_limits"):
        value = config.get(key)
        if isinstance(value, dict):
            merged[key] = value
    return merged


@contextmanager
def _config_lock() -> Iterator[None]:
    GPU_CONFIG_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with GPU_CONFIG_LOCK_PATH.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_json(path: Path) -> Dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read scheduler config %s: %s", path, exc)
        return None
    if not isinstance(payload, dict):
        logger.warning("Ignoring non-object scheduler config at %s", path)
        return None
    return _merge_with_defaults(payload)


def _atomic_write_unlocked(config: Dict[str, Any]) -> None:
    GPU_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_merge_with_defaults(config), indent=2, sort_keys=True) + "\n"
    fd, temporary_name = tempfile.mkstemp(
        dir=str(GPU_CONFIG_PATH.parent),
        prefix=f".{GPU_CONFIG_PATH.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, GPU_CONFIG_PATH)
        directory_fd = os.open(GPU_CONFIG_PATH.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary_path.unlink(missing_ok=True)


def _read_or_migrate_unlocked() -> Dict[str, Any]:
    current = _read_json(GPU_CONFIG_PATH)
    if current is not None:
        return current

    legacy = _read_json(LEGACY_GPU_CONFIG_PATH)
    if legacy is not None and LEGACY_GPU_CONFIG_PATH.resolve() != GPU_CONFIG_PATH.resolve():
        _atomic_write_unlocked(legacy)
        logger.info(
            "Migrated GPU scheduler config from %s to persistent state %s",
            LEGACY_GPU_CONFIG_PATH,
            GPU_CONFIG_PATH,
        )
        return legacy

    return get_default_config()


def read_scheduler_config() -> Dict[str, Any]:
    """Read the persisted scheduler config, migrating the checkout-local legacy file once."""
    with _config_lock():
        return _read_or_migrate_unlocked()


def write_scheduler_config(config: Dict[str, Any]) -> bool:
    """Atomically replace the scheduler config under a cross-process file lock."""
    try:
        with _config_lock():
            _atomic_write_unlocked(config)
        return True
    except (OSError, TypeError, ValueError) as exc:
        logger.error("Failed to write GPU scheduler config: %s", exc)
        return False


def mutate_scheduler_config(
    mutator: Callable[[Dict[str, Any]], Dict[str, Any] | None],
) -> Dict[str, Any]:
    """Apply one read/modify/write transaction without losing concurrent updates."""
    with _config_lock():
        config = _read_or_migrate_unlocked()
        replacement = mutator(config)
        if replacement is not None:
            if not isinstance(replacement, dict):
                raise TypeError("scheduler config mutator must return a dict or None")
            config = replacement
        config = _merge_with_defaults(config)
        _atomic_write_unlocked(config)
        return config
