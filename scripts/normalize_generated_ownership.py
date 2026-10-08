#!/usr/bin/env python3
"""Check or repair ownership of repo-local generated build/test paths only."""
from __future__ import annotations

import argparse
import os
import stat as stat_module
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATHS = (
    REPO_ROOT / ".cache",
    REPO_ROOT / ".nextflow",
    REPO_ROOT / ".nextflow-test-artifacts",
    REPO_ROOT / ".pytest_cache",
    REPO_ROOT / "work",
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


def _open_parent_without_symlinks(path: Path) -> tuple[int, str]:
    absolute = Path(os.path.abspath(path))
    parts = absolute.parts[1:]
    if not parts:
        raise ValueError("ownership normalization cannot target the filesystem root")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in parts[:-1]:
            next_descriptor = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor, parts[-1]
    except BaseException:
        os.close(descriptor)
        raise


def normalize_paths(paths: Iterable[Path], *, uid: int, gid: int, check_only: bool) -> list[Path]:
    """Normalize through stable directory descriptors without following symlinks."""
    mismatches: list[Path] = []
    for root in paths:
        display_root = Path(os.path.abspath(root))
        try:
            parent_fd, leaf = _open_parent_without_symlinks(display_root)
        except FileNotFoundError:
            continue
        try:
            try:
                root_stat = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if (root_stat.st_uid, root_stat.st_gid) != (uid, gid):
                mismatches.append(display_root)
                if not check_only:
                    os.chown(leaf, uid, gid, dir_fd=parent_fd, follow_symlinks=False)
            if not stat_module.S_ISDIR(root_stat.st_mode):
                continue
            root_fd = os.open(
                leaf,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
            try:
                for relative_dir, dirnames, filenames, dir_fd in os.fwalk(
                    ".", topdown=True, follow_symlinks=False, dir_fd=root_fd
                ):
                    relative = Path() if relative_dir == "." else Path(relative_dir)
                    for name in [*dirnames, *filenames]:
                        entry_stat = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                        display_path = display_root / relative / name
                        if (entry_stat.st_uid, entry_stat.st_gid) == (uid, gid):
                            continue
                        mismatches.append(display_path)
                        if not check_only:
                            os.chown(name, uid, gid, dir_fd=dir_fd, follow_symlinks=False)
            finally:
                os.close(root_fd)
        finally:
            os.close(parent_fd)
    return mismatches if check_only else []


def _target_identity() -> tuple[int, int]:
    uid_text = os.getenv("BMS_HOST_UID") or os.getenv("SUDO_UID")
    gid_text = os.getenv("BMS_HOST_GID") or os.getenv("SUDO_GID")
    if uid_text is None and os.geteuid() == 0:
        raise RuntimeError("root must set BMS_HOST_UID/BMS_HOST_GID (or run through sudo) before ownership normalization")
    return int(uid_text or os.getuid()), int(gid_text or os.getgid())


def validate_requested_paths(paths: Iterable[Path]) -> list[Path]:
    """Fail closed unless every requested path is inside an allowed generated root."""
    repo_root = REPO_ROOT.resolve()
    allowed_roots: list[tuple[Path, Path]] = []
    for path in DEFAULT_PATHS:
        lexical_root = Path(os.path.abspath(path))
        resolved_root = lexical_root.resolve(strict=False)
        if resolved_root != repo_root and repo_root not in resolved_root.parents:
            continue
        allowed_roots.append((lexical_root, resolved_root))
    validated: list[Path] = []
    for requested in paths:
        candidate = Path(os.path.abspath(requested))
        resolved_candidate = candidate.resolve(strict=False)
        allowed = any(
            (candidate == lexical_root or lexical_root in candidate.parents)
            and (resolved_candidate == resolved_root or resolved_root in resolved_candidate.parents)
            and (resolved_candidate == repo_root or repo_root in resolved_candidate.parents)
            for lexical_root, resolved_root in allowed_roots
        )
        if not allowed:
            raise ValueError(f"ownership path is outside the generated-output allowlist: {requested}")
        validated.append(candidate)
    return validated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report drift without changing ownership")
    parser.add_argument("paths", nargs="*", type=Path, help="optional generated paths (defaults to repo-scoped build/test outputs)")
    args = parser.parse_args()
    uid, gid = _target_identity()
    paths = validate_requested_paths(args.paths or list(DEFAULT_PATHS))
    mismatches = normalize_paths(paths, uid=uid, gid=gid, check_only=args.check)
    for path in mismatches:
        print(path)
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
