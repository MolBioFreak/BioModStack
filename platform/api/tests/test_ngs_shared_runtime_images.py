"""Real-byte image admission/pinning tests; no Apptainer or models are run."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import ngs_alignment_sessions as service
from lib import shared_runtime_images as images


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "lane-a" / "dorado.sif"
    source.parent.mkdir()
    source.write_bytes(b"approved runtime bytes")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    lock = tmp_path / "dorado.lock.json"
    lock.write_text(json.dumps({
        "dorado": {"sif_sha256": digest},
        "scientific_tools": {"samtools": {"version": "1.24"}},
    }))
    monkeypatch.setattr(service, "DORADO_LOCK_PATH", lock)
    monkeypatch.setenv("BMS_NGS_RUNTIME_SIF", str(source))
    monkeypatch.delenv("BMS_RUNTIME_IMAGE_STORE", raising=False)
    monkeypatch.setenv("BMS_CONTAINER_DIR", str(tmp_path / "containers"))
    monkeypatch.setattr(service.shutil, "which", lambda _name: "/test/apptainer")
    monkeypatch.setattr(service.subprocess, "run", lambda *_a, **_kw: SimpleNamespace(stdout="samtools 1.24\n"))
    service._clear_samtools_runtime_cache()
    try:
        yield source, digest, lock
    finally:
        service._clear_samtools_runtime_cache()


def test_samtools_shared_object_reuses_inode_across_source_parents(runtime, tmp_path, monkeypatch):
    source, digest, _lock = runtime
    first = service._samtools_command()
    object_path = tmp_path / "containers" / ".image-store" / "objects" / "sha256" / digest / "runtime.sif"
    assert first.runtime_path == object_path
    assert first.runtime_identity != service._runtime_stat_identity(source.stat())
    second_source = tmp_path / "lane-b" / "dorado.sif"
    second_source.parent.mkdir()
    second_source.write_bytes(source.read_bytes())

    def no_copy(*_args, **_kwargs):
        pytest.fail("an admitted shared object must never be recopied")

    monkeypatch.setattr(images, "_copy", no_copy)
    monkeypatch.setenv("BMS_NGS_RUNTIME_SIF", str(second_source))
    second = service._samtools_command()
    assert second is not first  # Source keys are not confused with object authority.
    assert second.runtime_path == first.runtime_path
    assert second.runtime_identity == first.runtime_identity
    assert second.runtime_directory_identity == first.runtime_directory_identity
    first.verify_runtime()
    assert not list(tmp_path.glob("lane-*/.bms-ngs-runtime-*"))
    assert not list(tmp_path.glob("lane-*/.image-store"))

    # Cold command reuse also preserves the object when the mutable source changes.
    identity = second.runtime_identity
    service._clear_samtools_runtime_cache()
    second_source.write_bytes(b"untrusted replacement source")
    third = service._samtools_command()
    assert third.runtime_identity == identity
    assert third.runtime_size is not None
    assert os.pread(third.pass_fds[0], third.runtime_size, 0) == b"approved runtime bytes"

    # A deployment may point directly to the immutable object without copying it.
    monkeypatch.setenv("BMS_NGS_RUNTIME_SIF", str(object_path))
    direct = service._samtools_command()
    assert direct.runtime_identity == identity


@pytest.mark.parametrize("setting", ["explicit", "container", "fallback"])
def test_samtools_image_store_precedence(runtime, tmp_path, monkeypatch, setting):
    source, digest, _lock = runtime
    expected_root = tmp_path / "containers" / ".image-store"
    if setting == "explicit":
        expected_root = tmp_path / "shared-store"
        monkeypatch.setenv("BMS_RUNTIME_IMAGE_STORE", str(expected_root))
    elif setting == "fallback":
        monkeypatch.delenv("BMS_CONTAINER_DIR")
        expected_root = source.parent / ".image-store"
    command = service._samtools_command()
    assert command.runtime_path == expected_root / "objects" / "sha256" / digest / "runtime.sif"


@pytest.mark.parametrize("mutation", ["same_bytes", "directory", "writable", "symlink", "ancestor_symlink", "missing"])
def test_samtools_cached_shared_object_replacement_fails_closed(runtime, tmp_path, monkeypatch, mutation):
    command = service._samtools_command()
    path = command.runtime_path
    assert path is not None
    original_bytes = path.read_bytes()
    if mutation == "directory":
        displaced = path.parent.with_name("displaced")
        path.parent.rename(displaced)
        path.parent.mkdir(mode=0o700)
        path.write_bytes(original_bytes)
        path.chmod(0o400)
        path.parent.chmod(0o500)
    elif mutation == "ancestor_symlink":
        ancestor = path.parents[2]
        displaced = ancestor.with_name("displaced-objects")
        ancestor.rename(displaced)
        ancestor.symlink_to(displaced, target_is_directory=True)
    elif mutation == "writable":
        path.chmod(0o600)
    else:
        path.parent.chmod(0o700)
        if mutation == "same_bytes":
            replacement = tmp_path / "replacement.sif"
            replacement.write_bytes(original_bytes)
            replacement.chmod(0o400)
            os.replace(replacement, path)
        else:
            path.unlink()
            if mutation == "symlink":
                path.symlink_to(runtime[0])
        path.parent.chmod(0o500)
    with pytest.raises(service.AlignmentSessionError):
        command.verify_runtime()
    with pytest.raises(service.AlignmentSessionError):
        service._samtools_command()


@pytest.mark.parametrize("change", ["digest", "version", "missing", "invalid"])
def test_samtools_cached_command_rereads_canonical_lock(runtime, monkeypatch, change):
    _source, _digest, lock = runtime
    service._samtools_command()
    if change == "missing":
        lock.unlink()
    elif change == "invalid":
        lock.write_text("not json")
    else:
        authority = json.loads(lock.read_text())
        if change == "digest":
            authority["dorado"]["sif_sha256"] = "0" * 64
        else:
            authority["scientific_tools"]["samtools"]["version"] = "1.23"
        lock.write_text(json.dumps(authority))

    def no_image_work(*_args, **_kwargs):
        pytest.fail("lock rejection must precede image work")

    monkeypatch.setattr(service, "publish_image", no_image_work)
    monkeypatch.setattr(service, "verify_image", no_image_work)
    with pytest.raises(service.AlignmentSessionError, match="lock|version"):
        service._samtools_command()


def test_samtools_replacement_between_verification_and_fd_pin_is_rejected(runtime, tmp_path, monkeypatch):
    real_verify = service.verify_image

    def replace_after_verification(path, digest):
        record = real_verify(path, digest)
        replacement = tmp_path / "replacement.sif"
        replacement.write_bytes(path.read_bytes())
        replacement.chmod(0o400)
        path.parent.chmod(0o700)
        os.replace(replacement, path)
        path.parent.chmod(0o500)
        return record

    monkeypatch.setattr(service, "verify_image", replace_after_verification)
    with pytest.raises(service.AlignmentSessionError, match="changed during validation"):
        service._samtools_command()
    assert not service._samtools_runtime_cache
