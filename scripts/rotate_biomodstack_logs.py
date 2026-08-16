#!/usr/bin/env python3
"""Bound BioModStack service wrapper logs before runtime startup.

The systemd user units append stdout/stderr to files for operator access. This
small rotator prevents those append-only files from growing without bound. Docker
container logs are capped separately by compose logging options.
"""
from __future__ import annotations

import os
from pathlib import Path

STATE_HOME = Path(os.getenv("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))).expanduser().resolve()
LOG_DIR = STATE_HOME / "biomodstack" / "logs"
LOG_PATHS = [
    LOG_DIR / "api.log",
    LOG_DIR / "frontend.log",
    LOG_DIR / "workflow-adapter.log",
    LOG_DIR / "core-runtime.log",
]
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5


def _int_env(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def rotate(path: Path, *, max_bytes: int, backup_count: int) -> bool:
    try:
        if not path.exists() or path.stat().st_size <= max_bytes:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)
            return False
        oldest = path.with_name(f"{path.name}.{backup_count}")
        if oldest.exists():
            oldest.unlink()
        for index in range(backup_count - 1, 0, -1):
            current = path.with_name(f"{path.name}.{index}")
            if current.exists():
                current.replace(path.with_name(f"{path.name}.{index + 1}"))
        path.replace(path.with_name(f"{path.name}.1"))
        path.touch()
        return True
    except OSError:
        return False


def main() -> int:
    max_bytes = _int_env("BMS_RUNTIME_LOG_MAX_BYTES", DEFAULT_MAX_BYTES, 1024 * 1024)
    backup_count = _int_env("BMS_RUNTIME_LOG_BACKUP_COUNT", DEFAULT_BACKUP_COUNT, 1)
    for path in LOG_PATHS:
        rotate(path, max_bytes=max_bytes, backup_count=backup_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
