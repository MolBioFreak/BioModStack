"""Location migration does not authorize a different scientific runtime."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("location_preflight", ROOT / "scripts/dorado_p4_preflight.py")
assert spec is not None and spec.loader is not None
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


@pytest.fixture
def images(tmp_path, monkeypatch):
    monkeypatch.delenv("BMS_RUNTIME_IMAGE_STORE", raising=False)
    monkeypatch.delenv("BMS_CONTAINER_DIR", raising=False)
    source = tmp_path / "dorado-old.sif"
    source.write_bytes(b"historical scientific image")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    store = tmp_path / "store"
    canonical = store / "objects" / "sha256" / digest / "runtime.sif"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(source.read_bytes())
    canonical.chmod(0o400)
    canonical.parent.chmod(0o500)
    return source, digest, store, canonical


def test_explicit_canonical_without_original(images):
    source, digest, store, canonical = images
    source.unlink()
    path, identity = preflight._verified_runtime_location(canonical, digest)
    assert path == canonical
    assert identity == preflight.verify_image(canonical, digest)


@pytest.mark.parametrize("config", ["store", "container"])
def test_exact_compatibility_alias(images, monkeypatch, config):
    source, digest, store, canonical = images
    if config == "container":
        store.rename(store.parent / ".image-store")
        canonical = store.parent / ".image-store" / canonical.relative_to(store)
        monkeypatch.setenv("BMS_CONTAINER_DIR", str(store.parent))
    else:
        monkeypatch.setenv("BMS_RUNTIME_IMAGE_STORE", str(store))
    source.unlink()
    source.symlink_to(os.path.relpath(canonical, source.parent))
    assert preflight._verified_runtime_location(source, digest)[0] == canonical


@pytest.mark.parametrize("bad", ["wrong_alias", "chain", "missing", "corrupt", "canonical_link", "directory_link", "current_digest", "bad_store"])
def test_bad_selection_fails_closed(images, monkeypatch, bad):
    source, digest, store, canonical = images
    monkeypatch.setenv("BMS_RUNTIME_IMAGE_STORE", str(store))
    selected = source
    if bad in {"wrong_alias", "chain"}:
        other = source.parent / "other.sif"
        if bad == "chain":
            other.symlink_to(canonical)
        else:
            other.write_bytes(source.read_bytes())
        source.unlink()
        source.symlink_to(other)
    elif bad == "missing":
        canonical.parent.chmod(0o700)
        canonical.unlink()
    elif bad == "corrupt":
        canonical.chmod(0o600)
        canonical.write_bytes(b"corrupt")
        canonical.chmod(0o400)
    elif bad == "canonical_link":
        canonical.parent.chmod(0o700)
        canonical.unlink()
        canonical.symlink_to(source)
        canonical.parent.chmod(0o500)
        selected = canonical
    elif bad == "directory_link":
        moved = canonical.parent.with_name("moved")
        canonical.parent.rename(moved)
        canonical.parent.symlink_to(moved, target_is_directory=True)
        selected = canonical
    elif bad == "current_digest":
        source.write_bytes(b"current lane not retained history")
    elif bad == "bad_store":
        monkeypatch.setenv("BMS_RUNTIME_IMAGE_STORE", "relative/store")
    with pytest.raises((ValueError, OSError, RuntimeError)):
        preflight._verified_runtime_location(selected, digest)


def test_explicit_current_canonical_does_not_replace_historical_digest(images):
    _, digest, store, canonical = images
    current_digest = hashlib.sha256(b"current lane").hexdigest()
    current = store / "objects" / "sha256" / current_digest / "runtime.sif"
    current.parent.mkdir()
    current.write_bytes(b"current lane")
    current.chmod(0o400)
    current.parent.chmod(0o500)
    with pytest.raises(ValueError, match="canonical selector identity mismatch"):
        preflight._verified_runtime_location(current, digest)
    assert preflight._verified_runtime_location(canonical, digest)[0] == canonical


def test_missing_explicit_selector_does_not_fall_back(images, monkeypatch):
    source, digest, store, _ = images
    monkeypatch.setenv("BMS_RUNTIME_IMAGE_STORE", str(store))
    source.unlink()
    with pytest.raises(ValueError, match="SIF identity mismatch"):
        preflight._verified_runtime_location(source, digest)


def test_qualification_regular_input_and_no_unconfigured_alias(images):
    source, digest, _, canonical = images
    assert preflight._verified_runtime_location(source, digest) == (source, None)
    source.unlink()
    source.symlink_to(canonical)
    with pytest.raises(ValueError, match="identity mismatch"):
        preflight._verified_runtime_location(source, digest)


@pytest.fixture
def native_preflight(images, tmp_path, monkeypatch):
    source, digest, store, canonical = images
    monkeypatch.setenv("BMS_RUNTIME_IMAGE_STORE", str(store))
    lock = preflight.load_lock(ROOT / "config/ngs/dorado_v1.3.1.lock.json")
    lock["dorado"]["sif_sha256"] = digest
    lock["dorado"]["version"] = "historical-version"
    model_root = tmp_path / "models"
    model = lock["models"]["dna"]["hac"]
    model_dir = model_root / model["id"]
    model_dir.mkdir(parents=True)
    (model_dir / "weights").write_bytes(b"locked model weights")
    aggregate, count, size = preflight._model_aggregate(model_dir)
    model.update(aggregate_sha256=aggregate, files=count, bytes=size)
    lock_path = tmp_path / "retained.lock.json"
    lock_path.write_text(json.dumps(lock))
    pod5 = tmp_path / "pod5"
    pod5.mkdir()
    (pod5 / "reads.pod5").write_bytes(b"inventory test input")
    monkeypatch.setattr(preflight, "_read_pod5_inventory", lambda *_: ({}, set()))
    monkeypatch.setattr(preflight, "_validate_chemistry", lambda *_: None)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="historical-version", stderr="")
    monkeypatch.setattr(preflight.subprocess, "run", run)
    def capabilities(path, observed_lock, **kwargs):
        assert path == canonical
        assert observed_lock == lock
        return {"historical": True}
    monkeypatch.setattr(preflight, "_verify_runtime_capabilities", capabilities)
    args = dict(lock_path=lock_path, pod5_root=pod5, molecule="dna", quality="hac", mode="simplex", model_root=model_root, runtime_sif=canonical)
    return args, calls, model_dir


def test_native_preflight_retains_history_and_executes_canonical(native_preflight, images, tmp_path):
    args, calls, _ = native_preflight
    source, digest, _, canonical = images
    receipt = tmp_path / "historical-receipt.json"
    receipt.write_text(json.dumps({"runtime": str(source), "sha256": digest}))
    before = receipt.read_bytes(), args["lock_path"].read_bytes()
    source.unlink()
    result = preflight.build_preflight(**args)
    assert result["runtime"]["sif_sha256"] == digest
    assert result["runtime"]["version"] == "historical-version"
    assert result["runtime"]["assets"]["runtime_sif"]["path"] == str(canonical)
    assert calls == [["apptainer", "exec", str(canonical), "dorado", "--version"]]
    assert before == (receipt.read_bytes(), args["lock_path"].read_bytes())


@pytest.mark.parametrize("failure", ["version", "model", "inode"])
def test_native_validation_retained(native_preflight, images, monkeypatch, failure):
    args, _, model_dir = native_preflight
    _, _, _, canonical = images
    if failure == "version":
        monkeypatch.setattr(preflight.subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="current-version", stderr=""))
    elif failure == "model":
        (model_dir / "weights").write_bytes(b"not locked")
    else:
        def replaced(*_a, **_k):
            canonical.parent.chmod(0o700)
            old = canonical.with_name("old.sif")
            canonical.rename(old)
            canonical.write_bytes(old.read_bytes())
            canonical.chmod(0o400)
            canonical.parent.chmod(0o500)
            return {}
        monkeypatch.setattr(preflight, "_verify_runtime_capabilities", replaced)
    with pytest.raises(ValueError, match={"version": "version identity mismatch", "model": "model identity mismatch", "inode": "object identity changed"}[failure]):
        preflight.build_preflight(**args)
