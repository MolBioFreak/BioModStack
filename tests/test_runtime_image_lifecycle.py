"""Lifecycle tests touch only synthetic temporary images, never runtime services."""
import hashlib
import json
import multiprocessing
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from lib import runtime_image_lifecycle as lifecycle
from lib import shared_runtime_images as shared
import publish_runtime_images as publisher


@pytest.fixture
def setup(tmp_path):
    source = tmp_path / "source.sif"
    source.write_bytes(b"synthetic image one")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    store = tmp_path / "store"
    spec = {"BMS_NGS_RUNTIME_SIF": {"source": str(source), "sha256": digest}}
    yield source, store, digest, spec
    for parent, _, _ in os.walk(tmp_path):
        if not Path(parent).is_symlink():
            os.chmod(parent, 0o700)


def publish(setup):
    source, store, digest, spec = setup
    return shared.publish_image(source, store, digest)


def test_retained_rollback_release_is_root_until_explicitly_forgotten(setup):
    source, store, digest, spec = setup
    env = publisher.publish_references(store, "development", spec)
    first_text = env.read_text()
    first = lifecycle.load_state(store)["current"]["development"]
    source.write_bytes(b"synthetic image two")
    second_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    spec["BMS_NGS_RUNTIME_SIF"]["sha256"] = second_digest
    publisher.publish_references(store, "development", spec)
    second = lifecycle.load_state(store)["current"]["development"]
    plan = lifecycle.plan_retirement(store, digest)
    assert plan["aliases"] == []
    assert any(first in reason for reason in plan["reasons"])
    with pytest.raises(lifecycle.Error, match="retained"):
        lifecycle.apply_retirement(store, plan, maintenance_authorization="isolated fixture")
    lifecycle.select_release(store, "development", first)
    assert env.read_text() == first_text
    with pytest.raises(lifecycle.Error, match="current"):
        lifecycle.forget_release(store, first)
    lifecycle.select_release(store, "development", second)
    lifecycle.forget_release(store, first)
    assert lifecycle.plan_retirement(store, digest)["reasons"] == []


def test_upgrade_legacy_env_retains_old_release(setup):
    source, store, digest, spec = setup
    obj = publish(setup)
    refs = store / "references"
    refs.mkdir()
    release = {"images": {"BMS_NGS_RUNTIME_SIF": {"sha256": digest, "path": str(obj)}}}
    (refs / "development.env").write_text(lifecycle.environment_text(store, release))
    source.write_bytes(b"next image after legacy publisher")
    spec["BMS_NGS_RUNTIME_SIF"]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    publisher.publish_references(store, "development", spec)
    state = lifecycle.load_state(store)
    assert "legacy-development" in state["releases"]
    assert any("legacy-development" in reason for reason in lifecycle.plan_retirement(store, digest)["reasons"])


def test_lane_admission_lease_survives_release_switch_and_unretention(setup):
    source, store, digest, spec = setup
    publisher.publish_references(store, "development", spec)
    release, token, identities = lifecycle.acquire_lane_lease(store, "development", owner="admitted-job")
    assert set(identities) == {digest}
    source.write_bytes(b"replacement release")
    spec["BMS_NGS_RUNTIME_SIF"]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    publisher.publish_references(store, "development", spec)
    lifecycle.forget_release(store, release)
    assert lifecycle.plan_retirement(store, digest)["reasons"] == [f"lease:{token}:admitted-job"]


def test_lease_durable_across_reload_and_no_expiration(setup):
    _, store, digest, _ = setup
    obj = publish(setup)
    token, identities = lifecycle.acquire_lease(store, [digest], owner="resumable-job")
    assert identities[digest] == shared.verify_image(obj, digest)
    state = lifecycle.load_state(store)
    state["leases"][token]["created_ns"] = 1
    lifecycle.save_state(store, state)
    plan = lifecycle.plan_retirement(store, digest)
    assert plan["reasons"] == [f"lease:{token}:resumable-job"]
    with pytest.raises(lifecycle.Error, match="retained"):
        lifecycle.apply_retirement(store, plan, maintenance_authorization="fixture")
    with pytest.raises(lifecycle.Error, match="owner"):
        lifecycle.release_lease(store, token, owner="wrong-job")
    lifecycle.release_lease(store, token, owner="resumable-job")
    assert lifecycle.plan_retirement(store, digest)["reasons"] == []


def test_new_lease_invalidates_approved_retirement_plan(setup):
    _, store, digest, _ = setup
    obj = publish(setup)
    plan = lifecycle.plan_retirement(store, digest)
    lifecycle.acquire_lease(store, [digest], owner="queued-job")
    with pytest.raises(lifecycle.Error, match="stale"):
        lifecycle.apply_retirement(store, plan, maintenance_authorization="fixture")
    assert obj.exists()


def test_explicit_quiescence_always_required_and_quarantine_preserves_inode(setup):
    _, store, digest, _ = setup
    obj = publish(setup)
    plan = lifecycle.plan_retirement(store, digest)
    assert plan["job_reference_coverage"] == "unproven"
    assert plan["identity"]["inode"] == obj.stat().st_ino
    assert plan["allocated_bytes"] == obj.stat().st_blocks * 512
    for authorization in (None, "", " "):
        with pytest.raises(lifecycle.Error, match="quiescence"):
            lifecycle.apply_retirement(store, plan, maintenance_authorization=authorization)
    assert obj.exists()
    quarantine = lifecycle.apply_retirement(store, plan, maintenance_authorization="test-maintenance-42")
    assert not obj.exists()
    assert shared.verify_image(quarantine / "runtime.sif", digest) == plan["identity"]
    assert json.loads((store / "quarantine" / (quarantine.name + ".json")).read_text())["authorization"] == "test-maintenance-42"
    with pytest.raises(FileNotFoundError):
        lifecycle.acquire_lease(store, [digest], owner="too-late")
    with pytest.raises(shared.SharedRuntimeImageError, match="quarantined"):
        publish(setup)


@pytest.mark.parametrize("kind", ["unknown-file", "legacy-env", "schema", "corrupt-state", "symlink", "object-extra"])
def test_unknown_references_fail_closed(setup, kind):
    _, store, digest, _ = setup
    obj = publish(setup)
    refs = store / "references"
    refs.mkdir()
    if kind in {"unknown-file", "legacy-env"}:
        (refs / ("unknown.json" if kind == "unknown-file" else "development.env")).write_text("untracked")
    elif kind == "schema":
        (refs / "state.json").write_text('{"schema_version": 999}')
    elif kind == "corrupt-state":
        (refs / "state.json").write_text("not json")
    elif kind == "symlink":
        (refs / "state.json").symlink_to(setup[0])
    else:
        obj.parent.chmod(0o700)
        (obj.parent / "unknown-ref").write_text("keep")
        obj.parent.chmod(0o500)
    with pytest.raises((lifecycle.Error, ValueError, OSError)):
        lifecycle.plan_retirement(store, digest)
    assert obj.exists()


def test_same_byte_inode_replacement_invalidates_plan(setup):
    source, store, digest, _ = setup
    obj = publish(setup)
    plan = lifecycle.plan_retirement(store, digest)
    replacement = source.with_name("replacement")
    replacement.write_bytes(source.read_bytes())
    replacement.chmod(0o400)
    obj.parent.chmod(0o700)
    replacement.replace(obj)
    obj.parent.chmod(0o500)
    with pytest.raises(lifecycle.Error, match="stale"):
        lifecycle.apply_retirement(store, plan, maintenance_authorization="fixture")


def test_crash_between_state_and_env_retains_both_and_blocks_retirement(setup, monkeypatch):
    source, store, digest, spec = setup
    env = publisher.publish_references(store, "development", spec)
    before = env.read_text()
    source.write_bytes(b"next release")
    spec["BMS_NGS_RUNTIME_SIF"]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    write = lifecycle.atomic_write
    def fail_env(path, text):
        if path.suffix == ".env":
            raise OSError("injected projection crash")
        return write(path, text)
    monkeypatch.setattr(lifecycle, "atomic_write", fail_env)
    with pytest.raises(OSError, match="projection crash"):
        publisher.publish_references(store, "development", spec)
    assert env.read_text() == before
    state = lifecycle.load_state(store)
    assert len(state["releases"]) == 2
    with pytest.raises(lifecycle.Error, match="projection"):
        lifecycle.plan_retirement(store, digest)
    monkeypatch.setattr(lifecycle, "atomic_write", write)
    lifecycle.select_release(store, "development", state["current"]["development"])
    assert lifecycle.plan_retirement(store, digest)["reasons"]


def test_lane_writers_serialize_and_preserve_all_releases(setup):
    _, store, digest, spec = setup
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: publisher.publish_references(store, "development", spec), range(8)))
    state = lifecycle.load_state(store)
    assert state["generation"] == 8 and len(state["releases"]) == 8
    assert lifecycle.plan_retirement(store, digest)["aliases"] == ["development.env:BMS_NGS_RUNTIME_SIF"]
    assert len(list((store / "objects/sha256").iterdir())) == 1


def test_reference_publication_fences_retirement_until_manifest_is_pinned(setup, monkeypatch):
    _, store, digest, spec = setup
    obj = publish(setup)
    plan = lifecycle.plan_retirement(store, digest)
    reached, proceed, attempting = Event(), Event(), Event()
    commit = publisher.commit_release
    def paused(*args):
        reached.set()
        assert proceed.wait(10)
        return commit(*args)
    monkeypatch.setattr(publisher, "commit_release", paused)
    def retire():
        attempting.set()
        return lifecycle.apply_retirement(store, plan, maintenance_authorization="fixture")
    with ThreadPoolExecutor(max_workers=2) as pool:
        publication = pool.submit(publisher.publish_references, store, "development", spec)
        assert reached.wait(10)
        retirement = pool.submit(retire)
        assert attempting.wait(10)
        assert not retirement.done()
        proceed.set()
        publication.result(timeout=10)
        with pytest.raises(lifecycle.Error, match="stale"):
            retirement.result(timeout=10)
    assert obj.exists()


@pytest.mark.parametrize("point", ["copy", "rename"])
def test_real_process_crash_stage_recovered_on_different_digest_publication(setup, point):
    source, store, digest, _ = setup
    def crash():
        if point == "copy":
            def failed_copy(src, dst):
                os.write(dst, b"partial")
                os._exit(73)
            shared._copy = failed_copy
        else:
            shared.os.rename = lambda *a, **kw: os._exit(73)
        shared.publish_image(source, store, digest)
    child = multiprocessing.get_context("fork").Process(target=crash)
    child.start()
    child.join(10)
    assert child.exitcode == 73
    assert len(list((store / "objects/sha256").glob(".publish-*"))) == 1
    source.write_bytes(b"different digest after restart")
    new_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    obj = shared.publish_image(source, store, new_digest)
    assert list((store / "objects/sha256").iterdir()) == [obj.parent]


def test_recovery_does_not_remove_unknown_stage_contents(setup):
    _, store, digest, _ = setup
    stage = store / "objects/sha256" / (".publish-" + digest + "-" + "a" * 32)
    stage.mkdir(parents=True)
    unexpected = stage / "operator-data"
    unexpected.write_bytes(b"keep")
    with pytest.raises(shared.SharedRuntimeImageError, match="unknown"):
        shared.recover_publications(store)
    assert unexpected.read_bytes() == b"keep"


def test_recovery_waits_for_active_publication(setup, monkeypatch):
    source, store, digest, _ = setup
    reached, proceed, attempting = Event(), Event(), Event()
    copy = shared._copy
    def paused(*args):
        reached.set()
        assert proceed.wait(10)
        return copy(*args)
    def recover():
        attempting.set()
        shared.recover_publications(store)
    monkeypatch.setattr(shared, "_copy", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        publication = pool.submit(shared.publish_image, source, store, digest)
        assert reached.wait(10)
        recovery = pool.submit(recover)
        assert attempting.wait(10)
        assert not recovery.done()
        proceed.set()
        obj = publication.result(timeout=10)
        recovery.result(timeout=10)
    assert obj.exists()


def test_cli_plan_is_dry_run_and_apply_is_explicit(setup, tmp_path):
    _, store, digest, _ = setup
    obj = publish(setup)
    script = Path(__file__).resolve().parents[1] / "scripts/retire_runtime_images.py"
    command = [sys.executable, str(script), "--store-root", str(store)]
    result = subprocess.run(command + ["plan", "--digest", digest], capture_output=True, text=True, check=True)
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(result.stdout)
    assert obj.exists()
    missing = subprocess.run(command + ["apply", "--plan", str(plan_file)], capture_output=True)
    assert missing.returncode != 0 and obj.exists()
    result = subprocess.run(command + ["apply", "--plan", str(plan_file),
                            "--maintenance-authorization", "synthetic-fixture-only"],
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["reclaimed_bytes"] == 0
    assert not obj.exists()
