#!/usr/bin/env python3
"""Snapshot the exact Protenix checkpoint and wrapper bytes used by inference."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


class ProtenixExecutionSnapshotError(ValueError):
    """Execution inputs could not be snapshotted without a race or ambiguity."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _identity(metadata: os.stat_result, digest: str, size: int) -> dict[str, int | str]:
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mode": stat.S_IMODE(metadata.st_mode),
        "size": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
        "sha256": digest,
        "bytes": size,
    }


def _open_pinned_file(path: Path) -> tuple[int, int, str, os.stat_result, os.stat_result]:
    """Open a file through no-follow directory descriptors and pin its parent."""

    absolute = Path(os.path.abspath(path))
    parts = absolute.parts
    if len(parts) < 2 or parts[0] != os.sep:
        raise ProtenixExecutionSnapshotError(f"execution input path is not absolute: {path}")
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


def _read_stable_bytes(path: Path) -> bytes:
    """Read a staged JSON input without allowing a path swap during the read."""

    file_fd, parent_fd, leaf, before, path_before = _open_pinned_file(path)
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(file_fd)
        path_after = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        visible_after = os.lstat(path)
        if not _same_identity(before, after) or not _same_identity(path_before, path_after) or not _same_identity(before, visible_after):
            raise ProtenixExecutionSnapshotError(f"execution input path changed while reading: {path}")
        return b"".join(chunks)
    finally:
        os.close(file_fd)
        os.close(parent_fd)


def snapshot_opened_file(
    *,
    source: Path,
    destination: Path,
    expected_sha256: str | None,
) -> dict[str, Any]:
    """Copy one regular file from a pinned descriptor and verify path stability."""

    source = Path(os.path.abspath(source))
    try:
        source_fd, parent_fd, leaf, opened, path_before = _open_pinned_file(source)
    except OSError as exc:
        raise ProtenixExecutionSnapshotError(f"cannot open execution input {source}: {exc}") from exc
    temporary: Path | None = None
    try:
        if not stat.S_ISREG(opened.st_mode):
            raise ProtenixExecutionSnapshotError(f"execution input is not a regular file: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination_fd, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", dir=destination.parent
        )
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        size = 0
        try:
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_fd, view)
                    if written <= 0:
                        raise OSError("short write while snapshotting execution input")
                    view = view[written:]
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
        observed_sha256 = digest.hexdigest()
        if expected_sha256 is not None and observed_sha256 != expected_sha256:
            raise ProtenixExecutionSnapshotError(
                f"execution input digest mismatch for {source}: "
                f"expected {expected_sha256}, observed {observed_sha256}"
            )
        after_read = os.fstat(source_fd)
        try:
            path_after = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            visible_path_after = os.lstat(source)
        except OSError as exc:
            raise ProtenixExecutionSnapshotError(
                f"execution input path changed after open: {source}"
            ) from exc
        identity_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if not _same_identity(opened, after_read) or not _same_identity(path_before, path_after) or not _same_identity(opened, visible_path_after):
            raise ProtenixExecutionSnapshotError(f"execution input path changed after open: {source}")
        if size != opened.st_size:
            raise ProtenixExecutionSnapshotError(f"execution input size changed while copying: {source}")
        os.chmod(temporary, 0o444)
        try:
            os.link(temporary, destination)
        except FileExistsError as exc:
            raise ProtenixExecutionSnapshotError(f"execution snapshot already exists: {destination}") from exc
        temporary.unlink()
        temporary = None
        return {
            "source_path": str(source),
            "snapshot_path": str(destination),
            "expected_sha256": expected_sha256,
            "observed_source": _identity(opened, observed_sha256, size),
            "verified_snapshot": {
                "sha256": observed_sha256,
                "bytes": size,
                "mode": stat.S_IMODE(destination.stat().st_mode),
            },
        }
    finally:
        os.close(source_fd)
        os.close(parent_fd)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _checkpoint_source(weights_root: Path, relative_value: object) -> tuple[Path, PurePosixPath]:
    if not isinstance(relative_value, str):
        raise ProtenixExecutionSnapshotError("registry checkpoint_relative_path is missing")
    relative = PurePosixPath(relative_value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ProtenixExecutionSnapshotError("registry checkpoint_relative_path is unsafe")
    root = Path(os.path.abspath(weights_root))
    root_info = os.lstat(root)
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise ProtenixExecutionSnapshotError("configured weights root is not a real directory")
    source = root.joinpath(*relative.parts)
    return source, relative


def prepare_execution_snapshot(
    *,
    registry_path: Path,
    weights_root: Path,
    wrapper: Path,
    runtime_root: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    registry = json.loads(_read_stable_bytes(registry_path).decode("utf-8"))
    checkpoint_source, checkpoint_relative = _checkpoint_source(
        weights_root, registry.get("checkpoint_relative_path")
    )
    expected_checkpoint = registry.get("checkpoint_sha256")
    if not isinstance(expected_checkpoint, str) or len(expected_checkpoint) != 64:
        raise ProtenixExecutionSnapshotError("registry checkpoint_sha256 is malformed")
    root = runtime_root
    if root.exists() or root.is_symlink():
        raise ProtenixExecutionSnapshotError("runtime snapshot root already exists")
    try:
        root.mkdir(parents=True)
    except FileExistsError as exc:
        raise ProtenixExecutionSnapshotError("runtime snapshot root already exists") from exc

    checkpoint_snapshot = root / Path(*checkpoint_relative.parts)
    wrapper_snapshot = root / "bms-wrapper" / "run_protenix_inference.py"
    checkpoint_receipt = snapshot_opened_file(
        source=checkpoint_source,
        destination=checkpoint_snapshot,
        expected_sha256=expected_checkpoint,
    )
    wrapper_receipt = snapshot_opened_file(
        source=wrapper,
        destination=wrapper_snapshot,
        expected_sha256=None,
    )
    checkpoint_receipt["source_path"] = checkpoint_relative.as_posix()
    checkpoint_receipt["snapshot_path"] = checkpoint_relative.as_posix()
    wrapper_receipt["source_path"] = wrapper.name
    wrapper_receipt["snapshot_path"] = "bms-wrapper/run_protenix_inference.py"
    receipt = {
        "schema_name": "cm_protenix_execution_snapshot",
        "schema_version": 1,
        "status": "verified_before_execution",
        "checkpoint": checkpoint_receipt,
        "wrapper": wrapper_receipt,
    }
    payload = _canonical_bytes(receipt)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        receipt_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o444,
    )
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write while recording execution snapshot")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--weights-root", type=Path, required=True)
    parser.add_argument("--wrapper", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare_execution_snapshot(
            registry_path=args.registry,
            weights_root=args.weights_root,
            wrapper=args.wrapper,
            runtime_root=args.runtime_root,
            receipt_path=args.receipt,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(2, f"Protenix execution snapshot failed: {exc}\n")


if __name__ == "__main__":
    main()
