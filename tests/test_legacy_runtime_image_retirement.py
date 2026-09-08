"""Real temporary-file retirement only; no live images or services."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from lib import runtime_image_lifecycle as life
from lib import shared_runtime_images as shared

AUTH = {"maintenance_authorization": "test-only external maintenance fence"}
FAIL = (life.Error, OSError)


@pytest.fixture
def files(tmp_path):
    source = tmp_path / ".bms-ngs-runtime-old" / "runtime.sif"
    source.parent.mkdir()
    source.write_bytes(b"temporary image bytes" * 1000)
    survivor = tmp_path / "historical.sif"
    survivor.write_bytes(source.read_bytes())
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    store = tmp_path / "store"
    yield source, survivor, store, digest
    for parent, _, _ in os.walk(tmp_path):
        os.chmod(parent, 0o700)


def plan(files, **kwargs):
    source, survivor, store, digest = files
    return life.plan_legacy_retirement(store, source, [survivor], digest, **kwargs)


def replace_equal(path):
    other = path.with_name("replacement")
    other.write_bytes(path.read_bytes())
    os.replace(other, path)


@pytest.mark.parametrize("cas", [False, True])
@pytest.mark.parametrize("remove_directory", [False, True])
def test_full_retirement_preserves_exact_survivor(files, cas, remove_directory):
    source, survivor, store, digest = files
    if cas:
        survivor = shared.publish_image(survivor, store, digest)
    else:
        os.link(survivor, survivor.with_suffix(".retained-hardlink"))
    old = survivor.stat()
    parent = survivor.parent.stat()
    if remove_directory:
        source.chmod(0o400)
        source.parent.chmod(0o500)
    p = life.plan_legacy_retirement(store, source, [survivor], digest,
                                    remove_directory=remove_directory)
    source_inode = source.stat().st_ino
    receipt = life.apply_legacy_retirement(store, p, **AUTH)
    quarantined = Path(receipt["quarantine"])
    assert not source.exists() and quarantined.stat().st_ino == source_inode
    assert receipt["reclaimed_bytes"] == 0
    purged = life.purge_legacy_retirement(store, receipt, **AUTH)
    assert purged["reclaimed_bytes"] == p["allocated_bytes"] > 0
    assert not quarantined.exists()
    assert source.parent.exists() is not remove_directory
    for attr in ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns"):
        assert getattr(survivor.stat(), attr) == getattr(old, attr)
    if cas:
        assert survivor.parent.stat() == parent
    with pytest.raises(FAIL):
        life.purge_legacy_retirement(store, receipt, **AUTH)


@pytest.mark.parametrize('stage', ['rename', 'unlink'])
def test_readonly_snapshot_mode_restored_on_mutation_failure(files, monkeypatch, stage):
    source, survivor, store, digest = files
    source.chmod(0o400)
    source.parent.chmod(0o500)
    p = plan(files, remove_directory=True)
    receipt = life.apply_legacy_retirement(store, p, **AUTH) if stage == 'unlink' else None
    original = getattr(os, stage)
    def fail_selected(path, *args, **kwargs):
        if str(path) == 'runtime.sif' or str(path).startswith('.quarantine-legacy-'):
            fd = kwargs.get('src_dir_fd', kwargs.get('dir_fd'))
            assert isinstance(fd, int)
            assert os.fstat(fd).st_mode & 0o777 == 0o700
            raise OSError('injected mutation failure')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, stage, fail_selected)
    with pytest.raises(OSError, match='injected mutation failure'):
        if stage == 'rename':
            life.apply_legacy_retirement(store, p, **AUTH)
        else:
            life.purge_legacy_retirement(store, receipt, **AUTH)
    assert source.parent.stat().st_mode & 0o777 == 0o500
    assert survivor.read_bytes() == b'temporary image bytes' * 1000
    assert (source if receipt is None else Path(receipt['quarantine'])).exists()


@pytest.mark.parametrize("target", ["source", "survivor"])
@pytest.mark.parametrize("mutation", ["replace", "corrupt", "symlink", "remove", "chmod"])
def test_stale_plan_no_mutation(files, target, mutation):
    source, survivor, store, digest = files
    p = plan(files)
    path = source if target == "source" else survivor
    if mutation == "replace":
        replace_equal(path)
    elif mutation == "corrupt":
        path.write_bytes(b"corrupt")
    elif mutation == "chmod":
        path.chmod(0o400)
    else:
        path.unlink()
        if mutation == "symlink":
            path.symlink_to(survivor if target == "source" else source)
    with pytest.raises(FAIL):
        life.apply_legacy_retirement(store, p, **AUTH)
    assert not list(source.parent.glob(".quarantine-*"))


@pytest.mark.parametrize("target", ["quarantine", "survivor"])
@pytest.mark.parametrize("mutation", ["replace", "corrupt", "symlink", "remove", "hardlink"])
def test_purge_revalidates_every_identity(files, target, mutation):
    source, survivor, store, digest = files
    receipt = life.apply_legacy_retirement(store, plan(files), **AUTH)
    q = Path(receipt["quarantine"])
    path = q if target == "quarantine" else survivor
    if mutation == "replace":
        replace_equal(path)
    elif mutation == "corrupt":
        path.write_bytes(b"bad")
    elif mutation == "hardlink":
        os.link(path, path.with_name("new-alias"))
    else:
        path.unlink()
        if mutation == "symlink":
            path.symlink_to(survivor if target == "quarantine" else q)
    with pytest.raises(FAIL):
        life.purge_legacy_retirement(store, receipt, **AUTH)
    if target != "quarantine" or mutation != "remove":
        assert q.exists() or q.is_symlink()


@pytest.mark.parametrize("stage", ["plan", "apply", "purge"])
def test_unknown_snapshot_entries_never_deleted(files, stage):
    source, survivor, store, digest = files
    if stage != "plan":
        p = plan(files, remove_directory=True)
    if stage == "purge":
        receipt = life.apply_legacy_retirement(store, p, **AUTH)
    unknown = source.parent / "unknown"
    unknown.mkdir()
    (unknown / "keep").write_bytes(b"operator data")
    with pytest.raises(FAIL):
        if stage == "plan":
            plan(files, remove_directory=True)
        elif stage == "apply":
            life.apply_legacy_retirement(store, p, **AUTH)
        else:
            life.purge_legacy_retirement(store, receipt, **AUTH)
    assert (unknown / "keep").read_bytes() == b"operator data"


def test_same_inode_last_copy_and_store_source_rejected(files):
    source, survivor, store, digest = files
    with pytest.raises(FAIL):
        life.plan_legacy_retirement(store, source, [], digest)
    with pytest.raises(FAIL):
        life.plan_legacy_retirement(store, source, [source], digest)
    survivor.unlink()
    os.link(source, survivor)
    with pytest.raises(FAIL):
        plan(files)
    obj = shared.publish_image(source, store, digest)
    with pytest.raises(FAIL):
        life.plan_legacy_retirement(store, obj, [source], digest)


@pytest.mark.parametrize("stage", ["apply", "purge"])
def test_authorization_and_generation_required(files, stage):
    source, survivor, store, digest = files
    evidence = plan(files)
    action = life.apply_legacy_retirement
    if stage == "purge":
        evidence = action(store, evidence, **AUTH)
        action = life.purge_legacy_retirement
    with pytest.raises(life.Error, match="authorization"):
        action(store, evidence, maintenance_authorization=" ")
    with life.transaction(store) as root:
        life.save_state(root, life.load_state(root))
    with pytest.raises(life.Error, match="stale"):
        action(store, evidence, **AUTH)


def test_unknown_reference_and_altered_plan_receipt(files):
    source, survivor, store, digest = files
    p = plan(files)
    bad = copy.deepcopy(p)
    bad["allocated_bytes"] += 1
    with pytest.raises(life.Error, match="stale"):
        life.apply_legacy_retirement(store, bad, **AUTH)
    (store / "references").mkdir()
    unknown = store / "references" / "unknown.env"
    unknown.write_text("unknown")
    with pytest.raises(life.Error, match="unknown"):
        life.apply_legacy_retirement(store, p, **AUTH)
    unknown.unlink()
    receipt = life.apply_legacy_retirement(store, p, **AUTH)
    bad = copy.deepcopy(receipt)
    bad["quarantine"] = str(survivor)
    with pytest.raises(life.Error, match="changed"):
        life.purge_legacy_retirement(store, bad, **AUTH)
    source.write_bytes(b"reappeared")
    with pytest.raises(life.Error, match="reappeared"):
        life.purge_legacy_retirement(store, receipt, **AUTH)


def test_parent_symlink_and_same_file_new_directory_rejected(files):
    source, survivor, store, digest = files
    p = plan(files)
    old = source.parent.with_name("moved")
    source.parent.rename(old)
    source.parent.symlink_to(old, target_is_directory=True)
    with pytest.raises(FAIL):
        life.apply_legacy_retirement(store, p, **AUTH)
    source.parent.unlink()
    source.parent.mkdir()
    (old / source.name).rename(source)
    with pytest.raises(FAIL):
        life.apply_legacy_retirement(store, p, **AUTH)


def test_all_survivors_required(files):
    source, survivor, store, digest = files
    second = survivor.with_name("second.sif")
    second.write_bytes(source.read_bytes())
    p = life.plan_legacy_retirement(store, source, [survivor, second], digest)
    receipt = life.apply_legacy_retirement(store, p, **AUTH)
    second.unlink()
    with pytest.raises(FAIL):
        life.purge_legacy_retirement(store, receipt, **AUTH)
    assert Path(receipt["quarantine"]).exists()


def test_cli_plan_apply_purge(files, tmp_path):
    source, survivor, store, digest = files
    script = Path(__file__).resolve().parents[1] / "scripts" / "retire_runtime_images.py"
    def run(*args):
        return json.loads(subprocess.check_output(
            [sys.executable, str(script), "--store-root", str(store), *map(str, args)], text=True))
    p = run("legacy-plan", "--source", source, "--survivor", survivor, "--digest", digest,
            "--remove-empty-snapshot-directory")
    reviewed = tmp_path / "plan.json"
    reviewed.write_text(json.dumps(p))
    receipt = run("legacy-apply", "--plan", reviewed, "--maintenance-authorization", "fixture")
    reviewed.write_text(json.dumps(receipt))
    result = run("legacy-purge", "--receipt", reviewed, "--maintenance-authorization", "fixture")
    assert result["status"] == "purged" and not source.parent.exists()


def test_frustrampnn_legacy_projection_already_supported(files):
    source, survivor, store, digest = files
    obj = shared.publish_image(source, store, digest)
    with life.transaction(store) as root:
        life.atomic_write(root / "references" / "development.env", life.environment_text(root, {
            "images": {"BMS_FRUSTRAMPNN_SIF": {"path": str(obj), "sha256": digest}}}))
    state = life.load_state(store)
    assert state["releases"]["legacy-development"]["images"]["BMS_FRUSTRAMPNN_SIF"]["sha256"] == digest
    p = plan(files)
    assert p["generation"] == 0


def test_existing_lifecycle_lock_covers_hashes_rename_and_unlink(files, monkeypatch):
    import fcntl
    source, survivor, store, digest = files
    seen = []
    def guarded(name, original):
        def call(*args, **kwargs):
            with open(store / ".locks" / "lifecycle.lock", "r") as probe:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            seen.append(name)
            return original(*args, **kwargs)
        return call
    monkeypatch.setattr(life, "_hash", guarded("hash", life._hash))
    monkeypatch.setattr(os, "rename", guarded("rename", os.rename))
    monkeypatch.setattr(os, "unlink", guarded("unlink", os.unlink))
    p = plan(files)
    receipt = life.apply_legacy_retirement(store, p, **AUTH)
    life.purge_legacy_retirement(store, receipt, **AUTH)
    assert {"hash", "rename", "unlink"} <= set(seen)


def test_interrupted_apply_retains_recoverable_evidence(files, monkeypatch):
    source, survivor, store, digest = files
    p = plan(files)
    real_save = life._save_legacy_receipt
    def fail_completion(root, receipt):
        if receipt["status"] == "quarantined":
            raise OSError("simulated crash writing completed receipt")
        real_save(root, receipt)
    monkeypatch.setattr(life, "_save_legacy_receipt", fail_completion)
    with pytest.raises(OSError, match="simulated crash"):
        life.apply_legacy_retirement(store, p, **AUTH)
    saved = json.loads(next((store / "quarantine").glob("legacy-*.json")).read_text())
    assert saved["status"] == "prepared"
    assert Path(saved["quarantine"]).read_bytes() == survivor.read_bytes()
    assert saved["plan"] == p
    with pytest.raises(life.Error):
        life.purge_legacy_retirement(store, saved, **AUTH)


def test_survivor_changed_after_hash_before_rename_is_rejected(files, monkeypatch):
    source, survivor, store, digest = files
    p = plan(files)
    real_save = life._save_legacy_receipt
    def change_after_verification(root, receipt):
        real_save(root, receipt)
        survivor.unlink()
    monkeypatch.setattr(life, "_save_legacy_receipt", change_after_verification)
    with pytest.raises(FAIL):
        life.apply_legacy_retirement(store, p, **AUTH)
    assert source.exists()


def test_unknown_store_survivor_and_unknown_plan_fields_rejected(files):
    source, survivor, store, digest = files
    p = plan(files)
    p["unreviewed_action"] = "delete another path"
    with pytest.raises(life.Error, match="stale"):
        life.apply_legacy_retirement(store, p, **AUTH)
    unknown = store / "quarantine" / "not-an-object.sif"
    unknown.parent.mkdir()
    unknown.write_bytes(source.read_bytes())
    with pytest.raises(life.Error, match="unknown survivor"):
        life.plan_legacy_retirement(store, source, [unknown], digest)
