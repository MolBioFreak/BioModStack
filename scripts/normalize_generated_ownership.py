#!/usr/bin/env python3
"""Check or repair ownership of repo-local generated build/test paths only."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATHS = (
    REPO_ROOT / ".cache",
    REPO_ROOT / ".pytest_cache",
    REPO_ROOT / "platform/api/.venv",
    REPO_ROOT / "platform/api/.pytest_cache",
    REPO_ROOT / "platform/frontend/dist",
    REPO_ROOT / "platform/frontend/node_modules/.vite",
    REPO_ROOT / "platform/desktop-electron/dist",
)


def _entries(root: Path) -> Iterable[Path]:
    if not root.exists() and not root.is_symlink():
        return
    yield root
    if root.is_dir() and not root.is_symlink():
        for directory, dirnames, filenames in os.walk(root, followlinks=False):
            base = Path(directory)
            for name in dirnames:
                yield base / name
            for name in filenames:
                yield base / name


def normalize_paths(paths: Iterable[Path], *, uid: int, gid: int, check_only: bool) -> list[Path]:
    mismatches: list[Path] = []
    for root in paths:
        for path in _entries(Path(root)):
            stat = path.lstat()
            if stat.st_uid == uid and stat.st_gid == gid:
                continue
            mismatches.append(path)
            if not check_only:
                os.lchown(path, uid, gid)
    if not check_only:
        return [path for path in mismatches if path.lstat().st_uid != uid or path.lstat().st_gid != gid]
    return mismatches


def _target_identity() -> tuple[int, int]:
    uid_text = os.getenv("BMS_HOST_UID") or os.getenv("SUDO_UID")
    gid_text = os.getenv("BMS_HOST_GID") or os.getenv("SUDO_GID")
    if uid_text is None and os.geteuid() == 0:
        raise RuntimeError("root must set BMS_HOST_UID/BMS_HOST_GID (or run through sudo) before ownership normalization")
    return int(uid_text or os.getuid()), int(gid_text or os.getgid())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report drift without changing ownership")
    parser.add_argument("paths", nargs="*", type=Path, help="optional generated paths (defaults to repo-scoped build/test outputs)")
    args = parser.parse_args()
    uid, gid = _target_identity()
    paths = args.paths or list(DEFAULT_PATHS)
    mismatches = normalize_paths(paths, uid=uid, gid=gid, check_only=args.check)
    for path in mismatches:
        print(path)
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
