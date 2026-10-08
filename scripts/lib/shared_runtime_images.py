"""A local, content-addressed SIF store shared by runtime consumers.

Publication copies bytes once (never hardlinks a mutable source), serializes
cooperating publishers with flock, and atomically exposes a durable read-only
object directory. Reuse hashes the existing object without repairing or copying
it. The store must be service-owned: POSIX modes do not protect against its owner
or root deliberately chmod-ing/replacing files. Consumers should retain and
compare verification identities at their execution/receipt boundaries.

This module is standard-library-only and does not import the API or launchers.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class SharedRuntimeImageError(RuntimeError):
    """An image or its filesystem identity failed closed validation."""


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_CHUNK_SIZE = 1024 * 1024


def _digest(value: str) -> str:
    if not isinstance(value, str):
        raise SharedRuntimeImageError("expected runtime image digest is not SHA-256")
    value = value.removeprefix("sha256:").lower()
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise SharedRuntimeImageError("expected runtime image digest is not SHA-256")
    return value


def _absolute(path: Path) -> Path:
    path = Path(path)
    if ".." in path.parts:
        raise SharedRuntimeImageError("parent traversal is not allowed in runtime image paths")
    return Path(os.path.abspath(path))


def _same(left: os.stat_result, right: os.stat_result) -> bool:
    return all(getattr(left, key) == getattr(right, key) for key in (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns"
    ))


@contextmanager
def _directory(path: Path, *, create: bool = False) -> Iterator[int]:
    """Walk every component using pinned no-follow directory descriptors."""
    path = _absolute(path)
    fd = os.open(os.sep, _DIRECTORY_FLAGS)
    try:
        for component in path.parts[1:]:
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


def _check_directory(path: Path, fd: int) -> None:
    with _directory(path) as visible_fd:
        left, right = os.fstat(fd), os.fstat(visible_fd)
        if (left.st_dev, left.st_ino) != (right.st_dev, right.st_ino):
            raise SharedRuntimeImageError("runtime image directory was replaced")


@contextmanager
def _file(path: Path) -> Iterator[tuple[int, int, os.stat_result]]:
    with _directory(path.parent) as parent_fd:
        fd = os.open(path.name, _FILE_FLAGS, dir_fd=parent_fd)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise SharedRuntimeImageError("runtime image is not a regular file")
            _check_file(path, fd, parent_fd, before)
            yield fd, parent_fd, before
        finally:
            os.close(fd)


def _check_file(path: Path, fd: int, parent_fd: int, before: os.stat_result) -> None:
    _check_directory(path.parent, parent_fd)
    visible = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
    if not _same(before, os.fstat(fd)) or not _same(before, visible):
        raise SharedRuntimeImageError("runtime image path or inode changed")


def _hash(fd: int) -> str:
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while chunk := os.read(fd, _CHUNK_SIZE):
        digest.update(chunk)
    return digest.hexdigest()


def verify_image(path: Path, expected_sha256: str) -> dict:
    """Hash a stable no-follow regular object with file0400/parent0500 modes.

    Returns sha256, size, device, inode, mtime_ns and ctime_ns. A caller comparing
    two receipts can detect same-byte inode replacement as well as corruption.
    Symlinks anywhere, hardlinked objects, writable objects, and mutation during
    hashing fail closed. Filesystem failures propagate as OSError.
    """
    expected = _digest(expected_sha256)
    path = _absolute(path)
    with _file(path) as (fd, parent_fd, before):
        parent_before = os.fstat(parent_fd)
        if stat.S_IMODE(before.st_mode) != 0o400 or before.st_nlink != 1:
            raise SharedRuntimeImageError("runtime image must be a single-link readonly file (0400)")
        if stat.S_IMODE(parent_before.st_mode) != 0o500:
            raise SharedRuntimeImageError("runtime image object directory must be readonly (0500)")
        observed = _hash(fd)
        _check_file(path, fd, parent_fd, before)
        if not _same(parent_before, os.fstat(parent_fd)):
            raise SharedRuntimeImageError("runtime image object directory changed")
        if observed != expected:
            raise SharedRuntimeImageError("runtime image SHA-256 differs from expected digest")
        return {
            "sha256": observed,
            "size": before.st_size,
            "device": before.st_dev,
            "inode": before.st_ino,
            "mtime_ns": before.st_mtime_ns,
            "ctime_ns": before.st_ctime_ns,
        }


def _copy(source_fd: int, destination_fd: int) -> tuple[str, int]:
    os.lseek(source_fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    size = 0
    while chunk := os.read(source_fd, _CHUNK_SIZE):
        digest.update(chunk)
        size += len(chunk)
        view = memoryview(chunk)
        while view:
            written = os.write(destination_fd, view)
            if written <= 0:
                raise OSError("short write while publishing runtime image")
            view = view[written:]
    return digest.hexdigest(), size


@contextmanager
def _lock(root: Path, digest: str) -> Iterator[None]:
    locks = root / ".locks"
    with _directory(locks, create=True) as parent_fd:
        name = digest + ".lock"
        fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                     0o600, dir_fd=parent_fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise SharedRuntimeImageError("runtime image lock must be a single-link regular file")
            fcntl.flock(fd, fcntl.LOCK_EX)
            _check_file(locks / name, fd, parent_fd, info)
            yield
        finally:
            os.close(fd)  # Releases flock, including on exceptions; lock files are never unlinked.


def publish_image(source: Path, store_root: Path, expected_sha256: str) -> Path:
    """Publish under the same admission fence used by references and retirement."""
    _digest(expected_sha256)  # Invalid input must not create a store.
    with _lock(_absolute(store_root), "lifecycle"):
        return _publish_image_locked(source, store_root, expected_sha256)


def _recover_stages(objects_fd: int, digest: str) -> None:
    """Recover only our exact private stage format, while holding its lock.

    No age heuristic: flock proves the cooperating writer is gone. Unknown
    contents/types fail closed, never recursively remove operator data.
    """
    prefix = ".publish-" + digest + "-"
    for name in os.listdir(objects_fd):
        if not name.startswith(prefix):
            continue
        suffix = name[len(prefix):]
        if len(suffix) != 32 or any(c not in "0123456789abcdef" for c in suffix):
            raise SharedRuntimeImageError("unknown publication staging name")
        fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=objects_fd)
        try:
            entries = os.listdir(fd)
            if set(entries) - {"runtime.sif"}:
                raise SharedRuntimeImageError("unknown publication staging contents")
            if entries:
                info = os.stat("runtime.sif", dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise SharedRuntimeImageError("unknown publication staging object")
            os.fchmod(fd, 0o700)
            if entries:
                os.unlink("runtime.sif", dir_fd=fd)
            os.rmdir(name, dir_fd=objects_fd)
            os.fsync(objects_fd)
        finally:
            os.close(fd)


def recover_publications(store_root: Path) -> None:
    """Explicit restart recovery; excludes every cooperating publisher."""
    with _lock(_absolute(store_root), "lifecycle"):
        _recover_publications_locked(_absolute(store_root))


def _recover_publications_locked(root: Path) -> None:
    try:
        with _directory(root / "objects" / "sha256") as fd:
            for name in os.listdir(fd):
                if name.startswith(".publish-"):
                    digest = _digest(name[len(".publish-"):len(".publish-") + 64])
                    if not name.startswith(".publish-" + digest + "-"):
                        raise SharedRuntimeImageError("unknown publication staging name")
                    with _lock(root, digest):
                        _recover_stages(fd, digest)
    except FileNotFoundError:
        # An unused store has no object directory yet.
        if (root / "objects" / "sha256").exists():
            raise


def _publish_image_locked(source: Path, store_root: Path, expected_sha256: str, *, _receipts=None) -> Path:
    """Publish once, or verify/reuse objects/sha256/<digest>/runtime.sif.

    Source identity and SHA-256 are checked before and after the sole byte copy.
    A valid existing object is authoritative; reuse does not read the source or
    write/chmod the object. A corrupt/incomplete existing object is never healed.
    The lock serializes publishers on this local filesystem, not remote hosts.
    """
    expected = _digest(expected_sha256)
    root = _absolute(store_root)
    source = _absolute(source)
    _recover_publications_locked(root)
    objects = root / "objects" / "sha256"
    result = objects / expected / "runtime.sif"
    with _lock(root, expected), _directory(objects, create=True) as objects_fd:
        _recover_stages(objects_fd, expected)
        if any(name.startswith(".quarantine-" + expected + "-") for name in os.listdir(objects_fd)):
            raise SharedRuntimeImageError("runtime image is quarantined; explicit maintenance recovery required")
        try:
            os.stat(expected, dir_fd=objects_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            receipt = verify_image(result, expected)
            if _receipts is not None:
                _receipts[expected] = receipt
            _check_directory(objects, objects_fd)
            return result

        stage = ".publish-" + expected + "-" + uuid.uuid4().hex
        os.mkdir(stage, 0o700, dir_fd=objects_fd)
        stage_fd = os.open(stage, _DIRECTORY_FLAGS, dir_fd=objects_fd)
        published = False
        try:
            with _file(source) as (source_fd, source_parent, before):
                if _hash(source_fd) != expected:
                    raise SharedRuntimeImageError("source runtime image SHA-256 differs from expected digest")
                _check_file(source, source_fd, source_parent, before)
                destination_fd = os.open(
                    "runtime.sif", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o400, dir_fd=stage_fd,
                )
                try:
                    copied_digest, size = _copy(source_fd, destination_fd)
                    os.fchmod(destination_fd, 0o400)
                    os.fsync(destination_fd)
                finally:
                    os.close(destination_fd)
                after_digest = _hash(source_fd)
                _check_file(source, source_fd, source_parent, before)
                if copied_digest != expected or after_digest != expected or size != before.st_size:
                    raise SharedRuntimeImageError("source runtime image changed during publication")
            os.fchmod(stage_fd, 0o500)
            os.fsync(stage_fd)
            verify_image(objects / stage / "runtime.sif", expected)
            _check_directory(objects, objects_fd)
            # All cooperating writers hold this digest's lock. Never overwrite
            # even an incomplete preexisting object discovered at commit time.
            try:
                os.stat(expected, dir_fd=objects_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise SharedRuntimeImageError("runtime image object appeared during publication")
            os.rename(stage, expected, src_dir_fd=objects_fd, dst_dir_fd=objects_fd)
            published = True
            os.fsync(objects_fd)
            receipt = verify_image(result, expected)
            if _receipts is not None:
                _receipts[expected] = receipt
            _check_directory(objects, objects_fd)
            return result
        finally:
            if not published:
                os.fchmod(stage_fd, 0o700)
                try:
                    os.unlink("runtime.sif", dir_fd=stage_fd)
                except FileNotFoundError:
                    pass
                os.rmdir(stage, dir_fd=objects_fd)
                os.fsync(objects_fd)
            os.close(stage_fd)
