"""Real filesystem tests for the shared, immutable local runtime image store."""
from __future__ import annotations

import hashlib
import multiprocessing
import os
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))
import shared_runtime_images as images


@pytest.fixture
def workspace(tmp_path):
    yield tmp_path
    # Restore only test-owned directory permissions for pytest's cleanup.
    for parent, directories, _ in os.walk(tmp_path):
        os.chmod(parent, 0o700)
        for name in directories:
            child = Path(parent) / name
            if not child.is_symlink():
                os.chmod(child, 0o700)


@pytest.fixture
def image(workspace):
    source = workspace / "source.sif"
    payload = b"real temporary SIF-like bytes\x00\xff" * 8192
    source.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    return source, workspace / "store", digest


def _publish_worker(args):
    result = images.publish_image(*args)
    return str(result), result.stat().st_ino


def test_public_api_publication_and_receipt(image):
    source, store, digest = image
    result = images.publish_image(source, store, "sha256:" + digest.upper())
    assert result == store / "objects" / "sha256" / digest / "runtime.sif"
    assert result.read_bytes() == source.read_bytes()
    assert result.stat().st_ino != source.stat().st_ino
    assert stat.S_IMODE(result.stat().st_mode) == 0o400
    assert stat.S_IMODE(result.parent.stat().st_mode) == 0o500
    info = result.stat()
    assert images.verify_image(result, digest) == {
        "sha256": digest, "size": info.st_size, "device": info.st_dev,
        "inode": info.st_ino, "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
    }


def test_reuse_does_not_copy_write_chmod_or_require_source(image, monkeypatch):
    source, store, digest = image
    result = images.publish_image(source, store, digest)
    before = images.verify_image(result, digest)
    parent_before = result.parent.stat()
    source.unlink()

    def forbidden(*args, **kwargs):
        pytest.fail("reuse must not copy, write or chmod")

    monkeypatch.setattr(images, "_copy", forbidden)
    monkeypatch.setattr(images.os, "write", forbidden)
    monkeypatch.setattr(images.os, "fchmod", forbidden)
    assert images.publish_image(source, store, digest) == result
    assert images.verify_image(result, digest) == before
    parent_after = result.parent.stat()
    assert parent_after.st_mtime_ns == parent_before.st_mtime_ns
    assert parent_after.st_ctime_ns == parent_before.st_ctime_ns


def test_mutable_source_isolation(image):
    source, store, digest = image
    result = images.publish_image(source, store, digest)
    before = images.verify_image(result, digest)
    source.write_bytes(b"changed mutable source")
    assert images.publish_image(source, store, digest) == result
    assert images.verify_image(result, digest) == before


def test_concurrent_threads_copy_once_same_inode(image, monkeypatch):
    copy = images._copy
    copies = []
    barrier = Barrier(8)

    def counted(*args):
        copies.append(1)
        return copy(*args)

    def publish(_):
        barrier.wait(timeout=10)
        return _publish_worker(image)

    monkeypatch.setattr(images, "_copy", counted)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(publish, range(8)))
    assert len(set(results)) == 1
    assert copies == [1]
    assert list((image[1] / "objects" / "sha256").iterdir()) == [Path(results[0][0]).parent]


def test_concurrent_processes_copy_once(image, workspace, monkeypatch):
    copy = images._copy
    count_file = workspace / "copies"

    def counted(*args):
        fd = os.open(count_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, b"copy\n")
        finally:
            os.close(fd)
        return copy(*args)

    monkeypatch.setattr(images, "_copy", counted)
    with multiprocessing.get_context("fork").Pool(4) as pool:
        results = pool.map(_publish_worker, [image] * 8)
    assert len(set(results)) == 1
    assert count_file.read_bytes() == b"copy\n"


@pytest.mark.parametrize("mutation", ["corrupt", "writable_file", "writable_directory", "missing", "symlink", "hardlink"])
def test_existing_invalid_object_rejected_not_repaired(image, workspace, mutation):
    source, store, digest = image
    result = images.publish_image(source, store, digest)
    if mutation == "corrupt":
        os.chmod(result, 0o600)
        result.write_bytes(b"corruption")
        os.chmod(result, 0o400)
    elif mutation == "writable_file":
        os.chmod(result, 0o600)
    elif mutation == "writable_directory":
        os.chmod(result.parent, 0o700)
    elif mutation in {"missing", "symlink"}:
        os.chmod(result.parent, 0o700)
        result.unlink()
        if mutation == "symlink":
            result.symlink_to(source)
        os.chmod(result.parent, 0o500)
    else:
        os.link(result, workspace / "alias.sif")
    with pytest.raises((images.SharedRuntimeImageError, OSError)):
        images.verify_image(result, digest)
    with pytest.raises((images.SharedRuntimeImageError, OSError)):
        images.publish_image(source, store, digest)
    if mutation == "corrupt":
        assert result.read_bytes() == b"corruption"
    if mutation == "missing":
        assert not result.exists()


def test_same_byte_replacement_changes_receipt_identity(image, workspace):
    source, store, digest = image
    result = images.publish_image(source, store, digest)
    before = images.verify_image(result, digest)
    replacement = workspace / "replacement.sif"
    replacement.write_bytes(source.read_bytes())
    os.chmod(replacement, 0o400)
    os.chmod(result.parent, 0o700)
    os.replace(replacement, result)
    os.chmod(result.parent, 0o500)
    after = images.verify_image(result, digest)
    assert before["sha256"] == after["sha256"]
    assert before["inode"] != after["inode"]
    assert before != after


@pytest.mark.parametrize("mutation", ["bytes", "replace", "parent_replace"])
def test_source_changed_during_copy_rejected_and_cleaned(image, monkeypatch, mutation):
    source, store, digest = image
    copy = images._copy

    def racing_copy(*args):
        result = copy(*args)
        if mutation == "bytes":
            source.write_bytes(b"modified during copy")
        elif mutation == "replace":
            replacement = source.with_name("replacement.sif")
            replacement.write_bytes(source.read_bytes())
            os.replace(replacement, source)
        else:
            # Move just the source's containing directory, not the store.
            source.parent.rename(source.parent.with_name("moved"))
            source.parent.mkdir()
            source.write_bytes(b"replacement path")
        return result

    if mutation == "parent_replace":
        parent = source.parent / "input"
        parent.mkdir()
        source = source.rename(parent / source.name)
    monkeypatch.setattr(images, "_copy", racing_copy)
    with pytest.raises(images.SharedRuntimeImageError, match="changed|replaced"):
        images.publish_image(source, store, digest)
    assert list((store / "objects" / "sha256").iterdir()) == []


def test_replacement_during_verification_rejected(image, workspace, monkeypatch):
    source, store, digest = image
    result = images.publish_image(source, store, digest)
    hash_file = images._hash

    def racing_hash(fd):
        value = hash_file(fd)
        replacement = workspace / "replacement"
        replacement.write_bytes(source.read_bytes())
        replacement.chmod(0o400)
        result.parent.chmod(0o700)
        os.replace(replacement, result)
        result.parent.chmod(0o500)
        return value

    monkeypatch.setattr(images, "_hash", racing_hash)
    with pytest.raises(images.SharedRuntimeImageError, match="changed"):
        images.verify_image(result, digest)


@pytest.mark.parametrize("value", ["../escape", "g" * 64, "f" * 63, "", None])
def test_invalid_digest_has_no_store_side_effect(image, value):
    source, store, _ = image
    with pytest.raises(images.SharedRuntimeImageError, match="SHA-256"):
        images.publish_image(source, store, value)
    assert not store.exists()


def test_wrong_source_digest_cleans_staging(image):
    source, store, _ = image
    with pytest.raises(images.SharedRuntimeImageError, match="SHA-256"):
        images.publish_image(source, store, "0" * 64)
    assert list((store / "objects" / "sha256").iterdir()) == []


@pytest.mark.parametrize("target", ["source", "source_parent", "store", "store_parent", "objects", "digest", "lock", "locks"])
def test_symlinks_rejected_at_every_path_boundary(image, workspace, target):
    source, store, digest = image
    external = workspace / "external"
    external.mkdir()
    if target == "source":
        alias = workspace / "alias.sif"
        alias.symlink_to(source)
        source = alias
    elif target == "source_parent":
        alias = workspace / "alias"
        alias.symlink_to(workspace, target_is_directory=True)
        source = alias / source.name
    elif target == "store":
        store.symlink_to(external, target_is_directory=True)
    elif target == "store_parent":
        alias = workspace / "alias"
        alias.symlink_to(external, target_is_directory=True)
        store = alias / "store"
    elif target == "objects":
        store.mkdir()
        (store / "objects").symlink_to(external, target_is_directory=True)
    elif target == "digest":
        objects = store / "objects" / "sha256"
        objects.mkdir(parents=True)
        (objects / digest).symlink_to(external, target_is_directory=True)
    elif target == "locks":
        store.mkdir()
        (store / ".locks").symlink_to(external, target_is_directory=True)
    else:
        locks = store / ".locks"
        locks.mkdir(parents=True)
        (locks / (digest + ".lock")).symlink_to(source)
    with pytest.raises((OSError, images.SharedRuntimeImageError)):
        images.publish_image(source, store, digest)
    assert list(external.iterdir()) == []


def test_verify_rejects_symlink_ancestor(image, workspace):
    result = images.publish_image(*image)
    alias = workspace / "alias"
    alias.symlink_to(result.parent, target_is_directory=True)
    with pytest.raises(OSError):
        images.verify_image(alias / "runtime.sif", image[2])


@pytest.mark.parametrize("target", ["source", "store"])
def test_parent_traversal_rejected(image, target):
    source, store, digest = image
    if target == "source":
        source = source.parent / "ignored" / ".." / source.name
    else:
        store = store / ".." / "escape"
    with pytest.raises(images.SharedRuntimeImageError, match="traversal"):
        images.publish_image(source, store, digest)


@pytest.mark.parametrize("kind", ["fifo", "directory"])
def test_nonregular_source_rejected_without_blocking(image, workspace, kind):
    source, store, digest = image
    source.unlink()
    if kind == "fifo":
        os.mkfifo(source)
    else:
        source.mkdir()
    with pytest.raises(images.SharedRuntimeImageError, match="regular"):
        images.publish_image(source, store, digest)


def test_copy_failure_cleans_stage_and_retry_succeeds(image, monkeypatch):
    copy = images._copy

    def failed_copy(source_fd, destination_fd):
        os.write(destination_fd, b"partial")
        raise OSError("injected copy failure")

    monkeypatch.setattr(images, "_copy", failed_copy)
    with pytest.raises(OSError, match="injected"):
        images.publish_image(*image)
    assert list((image[1] / "objects" / "sha256").iterdir()) == []
    monkeypatch.setattr(images, "_copy", copy)
    assert images.verify_image(images.publish_image(*image), image[2])["sha256"] == image[2]


def test_atomic_visibility_and_fsync_before_and_after_rename(image, monkeypatch):
    fsync, rename = images.os.fsync, images.os.rename
    events = []
    result = image[1] / "objects" / "sha256" / image[2] / "runtime.sif"

    def recording_fsync(fd):
        events.append(("fsync", stat.S_ISREG(os.fstat(fd).st_mode)))
        return fsync(fd)

    def recording_rename(*args, **kwargs):
        assert not result.exists()
        events.append(("rename", None))
        value = rename(*args, **kwargs)
        assert result.read_bytes() == image[0].read_bytes()
        assert stat.S_IMODE(result.stat().st_mode) == 0o400
        assert stat.S_IMODE(result.parent.stat().st_mode) == 0o500
        return value

    monkeypatch.setattr(images.os, "fsync", recording_fsync)
    monkeypatch.setattr(images.os, "rename", recording_rename)
    images.publish_image(*image)
    index = events.index(("rename", None))
    assert ("fsync", True) in events[:index]
    assert events[index - 1] == ("fsync", False)
    assert events[index + 1] == ("fsync", False)
