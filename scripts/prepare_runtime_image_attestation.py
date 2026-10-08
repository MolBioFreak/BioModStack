#!/usr/bin/env python3
"""Create a race-checked immutable snapshot of a registered runtime image."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any


class RuntimeImageAttestationError(RuntimeError):
    """The registered image could not be bound to one observed byte snapshot."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _identity(info: os.stat_result) -> dict[str, int]:
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "bytes": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
    }


def _open_pinned_file(path: Path) -> tuple[int, int, str, os.stat_result, os.stat_result]:
    """Open a file through no-follow directory descriptors and pin its parent."""

    absolute = Path(os.path.abspath(path))
    parts = absolute.parts
    if len(parts) < 2 or parts[0] != os.sep:
        raise RuntimeImageAttestationError(f"runtime image path is not absolute: {path}")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    parent_fd = os.open(os.sep, directory_flags)
    file_fd: int | None = None
    try:
        for component in parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = next_fd
        leaf = parts[-1]
        file_fd = os.open(
            leaf,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        opened = os.fstat(file_fd)
        path_before = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        return file_fd, parent_fd, leaf, opened, path_before
    except Exception:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)
        raise


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return all(
        getattr(left, field) == getattr(right, field)
        for field in ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
    )


def _copy_descriptor(source_fd: int, destination: Path) -> tuple[str, int]:
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o400,
    )
    digest = hashlib.sha256()
    size = 0
    try:
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short write while snapshotting runtime image")
                view = view[written:]
            digest.update(chunk)
            size += len(chunk)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return digest.hexdigest(), size


def _write_receipt(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o400,
    )
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write while writing runtime image receipt")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(path, 0o444)


def create_verified_image_snapshot(
    *,
    image: Path,
    expected_sha256: str,
    snapshot: Path,
    receipt: Path,
) -> dict[str, Any]:
    expected = expected_sha256.removeprefix("sha256:").lower()
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise RuntimeImageAttestationError("expected runtime image digest is not SHA-256")
    image = Path(os.path.abspath(image))
    snapshot = snapshot.absolute()
    receipt = receipt.absolute()
    if snapshot == receipt or snapshot.exists() or receipt.exists():
        raise RuntimeImageAttestationError("runtime snapshot outputs must be new distinct paths")
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    receipt.parent.mkdir(parents=True, exist_ok=True)

    source_fd: int | None = None
    parent_fd: int | None = None
    try:
        source_fd, parent_fd, leaf, before, path_before = _open_pinned_file(image)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeImageAttestationError("runtime image is not a regular file")
        source_digest, copied_bytes = _copy_descriptor(source_fd, snapshot)
        after = os.fstat(source_fd)
        try:
            path_after = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            visible_path_after = os.lstat(image)
        except OSError as exc:
            raise RuntimeImageAttestationError("runtime image path changed during snapshot") from exc
        if not _same_identity(before, after) or not _same_identity(path_before, path_after) or not _same_identity(before, visible_path_after):
            raise RuntimeImageAttestationError("runtime image path or inode changed during snapshot")
        if copied_bytes != before.st_size or source_digest != expected:
            raise RuntimeImageAttestationError(
                "runtime image observed digest or byte count differs from registry"
            )
        snapshot_info = os.lstat(snapshot)
        if not stat.S_ISREG(snapshot_info.st_mode) or snapshot_info.st_size != copied_bytes:
            raise RuntimeImageAttestationError("verified runtime image snapshot is incomplete")
        os.chmod(snapshot, 0o444)
        payload = {
            "schema_name": "cm_runtime_image_receipt",
            "schema_version": 1,
            "status": "verified_immutable_snapshot",
            "measurement_method": "open_no_follow+fstat_before_after+path_inode_recheck+sha256_copy",
            "expected_sha256": expected,
            "observed_source": {
                "path": image.name,
                **_identity(before),
                "sha256": source_digest,
            },
            "verified_snapshot": {
                "name": snapshot.name,
                "bytes": copied_bytes,
                "sha256": source_digest,
            },
        }
        _write_receipt(receipt, payload)
        return payload
    except Exception:
        snapshot.unlink(missing_ok=True)
        receipt.unlink(missing_ok=True)
        raise
    finally:
        if source_fd is not None:
            os.close(source_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    try:
        registry = json.loads(args.registry.read_text(encoding="utf-8"))
        expected = registry["container_digest"]
        if not isinstance(expected, str):
            raise RuntimeImageAttestationError("registry container digest is not a string")
        create_verified_image_snapshot(
            image=args.image,
            expected_sha256=expected,
            snapshot=args.snapshot,
            receipt=args.receipt,
        )
    except (OSError, KeyError, TypeError, json.JSONDecodeError, RuntimeImageAttestationError) as exc:
        parser.exit(2, f"Protenix runtime image preflight failed: {exc}\n")


if __name__ == "__main__":
    main()
