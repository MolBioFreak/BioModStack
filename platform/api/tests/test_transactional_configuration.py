"""Real first-install CLI and crash/recovery tests; all writes in disposable homes."""
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import biomodstack_configuration as tx
import biomodstack_runtime_profile as profiles


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for key in list(os.environ):
        if key.startswith("BMS_"):
            monkeypatch.delenv(key)
    document = tmp_path / "install.json"
    document.write_text(json.dumps({"schema_version": "bms.install.v1", "profile": {},
                                   "ingress": {"mode": "tailnet", "target": "development"}}))
    return document


def apply(document, action="configure", **kwargs):
    return tx.configuration_report(action, project_root=ROOT, document=document, **kwargs)


def cli(*args):
    result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/manage_desktop_services.py"),
                             *args, "--json"], env=os.environ.copy(), text=True, capture_output=True)
    return result.returncode, json.loads(result.stdout)


def test_real_cli_runtime_exports_idempotence(fixture):
    code, result = cli("configure", "--document", str(fixture))
    assert code == 0, result
    assert result["status"] == "configured" and not result["ready"]
    assert result["ingress"] == {"mode": "tailnet", "target": "development", "applied": False}
    installed = profiles.load_install_profile()
    resolved = profiles.resolve_runtime_paths(ROOT, environ={})
    assert resolved["data_root"] == installed["data_root"]
    assert profiles.resolve_installed_core_runtime_paths(ROOT)["data_root"] == installed["data_root"]
    policy = __import__("biomodstack_local_resources").configured_local_policy(installed)
    resolved.update(local_cpu_threads=policy.cpu_threads, local_memory_bytes=policy.memory_bytes)
    assert profiles.get_core_runtime_env_path().read_text() == "\n".join(profiles._core_runtime_env_lines(resolved))
    assert profiles.get_compat_env_path().read_text() == "\n".join(profiles._compat_env_lines(resolved))
    shell = subprocess.run(["bash", "-c", 'source "$1"; printf "%s" "$BMS_DATA"', "bash",
                            str(profiles.get_compat_env_path())], capture_output=True, text=True)
    assert shell.returncode == 0 and shell.stdout == installed["data_root"]
    original = {p: p.lstat().st_mtime_ns for p in map(Path, result["destinations"].values())}
    for action in ("configure", "recover", "resume"):
        code, again = cli(action, "--document", str(fixture), "--operation-id", result["operation_id"])
        assert code == 0 and again["status"] == "already_configured", again
        assert again["operation_id"] == result["operation_id"]
    assert original == {p: p.lstat().st_mtime_ns for p in original}
    with pytest.raises(tx.ConfigurationBlocked, match="managed_configuration_read_only"):
        profiles.save_install_profile({"web_host_port": 18090}, ROOT)


POINTS = ["pending_journal", "journal", "stage:profile", "stage:core_runtime_env", "stage:compat_env", "validated",
          "publish:profile", "publish:core_runtime_env", "publish:compat_env", "before_activation",
          "activated", "committed"]


@pytest.mark.parametrize("point", POINTS)
def test_failure_resume_each_boundary(fixture, monkeypatch, point):
    def fail(name):
        if name == point:
            raise OSError("injected failure " + point)
    monkeypatch.setattr(tx, "_checkpoint", fail)
    result = apply(fixture)
    assert not result["configured"] and result["recovery_available"], result
    identity = result["operation_id"]
    assert result["configuration_active"] == (point in {"activated", "committed"})
    if point not in {"pending_journal", "activated", "committed"}:
        with pytest.raises(tx.ConfigurationBlocked):
            profiles.load_install_profile()
        # Launch guard must stop before touching services/tools/browser.
        shell = subprocess.run(["bash", str(ROOT / "scripts/run_biomodstack_frontend.sh")],
                               capture_output=True, text=True)
        assert shell.returncode == 78
    monkeypatch.setattr(tx, "_checkpoint", lambda name: None)
    code, recovered = cli("resume", "--operation-id", identity)
    assert code == 0 and recovered["configured"], recovered
    profiles.load_install_profile()
    assert apply(None, "recover")["status"] == "already_configured"


def _killed_apply(document, point):
    tx._checkpoint = lambda name: os._exit(91) if name == point else None
    apply(document)


@pytest.mark.parametrize("point", ["journal", "publish:core_runtime_env", "activated"])
def test_process_death_releases_durable_lock(fixture, point):
    process = multiprocessing.get_context("spawn").Process(target=_killed_apply, args=(fixture, point))
    process.start()
    process.join(10)
    assert process.exitcode == 91
    code, result = cli("recover")
    assert code == 0 and result["configured"], result


def test_recovery_failure_and_conflicting_file_preserved(fixture, monkeypatch):
    monkeypatch.setattr(tx, "_checkpoint", lambda name: (_ for _ in ()).throw(OSError("stop")) if name == "journal" else None)
    assert not apply(fixture)["configured"]
    monkeypatch.setattr(tx, "_checkpoint", lambda name: (_ for _ in ()).throw(OSError("recovery stop")) if name == "recover" else None)
    assert not apply(None, "recover")["configured"]
    monkeypatch.setattr(tx, "_checkpoint", lambda name: None)
    path = profiles.get_compat_env_path()
    path.parent.mkdir()
    path.write_text("existing operator config")
    result = apply(None, "recover")
    assert not result["configured"]
    assert path.read_text() == "existing operator config"
    with pytest.raises(tx.ConfigurationBlocked):
        profiles.load_install_profile()


def test_stale_input_operation_and_corrupt_stage(fixture, monkeypatch):
    assert apply(fixture, expect_document_sha256="0" * 64)["blockers"][0]["code"] == "stale_input"
    assert not tx.transaction_dir().exists()
    monkeypatch.setattr(tx, "_checkpoint", lambda name: (_ for _ in ()).throw(OSError("stop")) if name == "validated" else None)
    result = apply(fixture)
    assert apply(None, "recover", operation_id="wrong")["blockers"][0]["code"] == "operation_mismatch"
    raw = json.loads(fixture.read_text())
    raw["profile"]["web_host_port"] = 18090
    fixture.write_text(json.dumps(raw))
    assert apply(fixture, "resume")["blockers"][0]["code"] == "stale_input"
    assert apply(fixture)["blockers"][0]["code"] == "stale_input"
    monkeypatch.setattr(tx, "_checkpoint", lambda name: None)
    stage = tx.transaction_dir() / "generation/profile"
    stage.write_text("{}")
    assert not apply(None, "recover")["configured"]
    assert stage.read_text() == "{}"
    assert result["operation_id"]


def test_existing_configuration_preserved(fixture):
    path = profiles.get_install_profile_path()
    path.parent.mkdir(parents=True)
    path.write_text('{"unknown_existing_setting": "keep"}')
    before = path.read_bytes()
    result = apply(fixture)
    assert not result["configured"]
    assert result["blockers"][0]["code"] == "existing_install_migration_unsupported"
    assert path.read_bytes() == before
    assert not profiles.get_compat_env_path().exists()


def test_stale_state_and_configuration_storage_overlap(fixture, monkeypatch):
    raw = json.loads(fixture.read_text())
    raw["profile"] = {"data_root": str(Path.home() / ".biomodstack")}
    fixture.write_text(json.dumps(raw))
    assert apply(fixture)["blockers"][0]["code"] == "configuration_storage_overlap"
    raw["profile"] = {}
    fixture.write_text(json.dumps(raw))
    monkeypatch.setattr(tx, "_checkpoint", lambda name: (_ for _ in ()).throw(OSError("stop")) if name == "journal" else None)
    assert not apply(fixture)["configured"]
    profile = json.loads(tx._load()["files"]["profile"])
    state = Path(profile["data_root"])
    state.mkdir(parents=True)
    (state / "biomodstack.db").write_bytes(b"preserve existing user data")
    monkeypatch.setattr(tx, "_checkpoint", lambda name: None)
    assert apply(None, "recover")["blockers"][0]["code"] == "stale_storage"
    assert (state / "biomodstack.db").read_bytes() == b"preserve existing user data"


def test_fsync_error_does_not_activate_partial_stage(fixture, monkeypatch):
    original = tx._sync
    def fail_generation(path):
        if path.name == "generation":
            raise OSError("injected fsync error")
        original(path)
    monkeypatch.setattr(tx, "_sync", fail_generation)
    result = apply(fixture)
    assert not result["configured"] and not result["configuration_active"]
    monkeypatch.setattr(tx, "_sync", original)
    assert apply(None, "recover")["configured"]


def test_concurrent_apply_lock(fixture):
    with tx.configuration_lock():
        code, result = cli("configure", "--document", str(fixture))
        assert code == 3 and result["blockers"][0]["code"] == "configuration_busy"
    assert apply(fixture)["configured"]


def test_external_storage_and_changed_runtime_authority(fixture, monkeypatch):
    raw = json.loads(fixture.read_text())
    external = fixture.parent / "external-disk"
    raw["profile"] = {"data_root": str(external / "production"),
                      "dev_data_root": str(external / "development"),
                      "weights_root": str(external / "shared-weights")}
    fixture.write_text(json.dumps(raw))
    monkeypatch.setattr(tx, "_checkpoint", lambda name: (_ for _ in ()).throw(OSError("stop")) if name == "validated" else None)
    assert not apply(fixture)["configured"]
    monkeypatch.setattr(tx, "_checkpoint", lambda name: None)
    original = profiles._core_runtime_env_lines
    monkeypatch.setattr(profiles, "_core_runtime_env_lines", lambda resolved: original(resolved) + ["CHANGED=1"])
    assert apply(None, "recover")["blockers"][0]["code"] == "stale_generation"
    monkeypatch.setattr(profiles, "_core_runtime_env_lines", original)
    result = apply(None, "resume")
    assert result["configured"], result
    assert profiles.resolve_installed_core_runtime_paths(ROOT)["data_root"] == str(external / "production")
    assert not external.exists()  # recording storage is not provisioning it


def test_cross_filesystem_destinations(fixture, monkeypatch):
    with tempfile.TemporaryDirectory(prefix="bms-config-", dir="/dev/shm") as other:
        if Path(other).stat().st_dev == fixture.parent.stat().st_dev:
            pytest.skip("requires distinct filesystems")
        monkeypatch.setenv("XDG_CONFIG_HOME", other)
        result = apply(fixture)
        assert result["configured"], result
        assert profiles.get_compat_env_path().lstat().st_dev != profiles.get_install_profile_path().lstat().st_dev
        assert profiles.resolve_installed_core_runtime_paths(ROOT)["data_root"]


@pytest.mark.parametrize("profile", [{"data_root": "/tmp/$(touch BAD)"}, {"data_root": "/tmp/shared", "dev_data_root": "/tmp/shared"}])
def test_unsafe_or_cross_lane_document_rejected(fixture, profile):
    raw = json.loads(fixture.read_text())
    raw["profile"] = profile
    fixture.write_text(json.dumps(raw))
    assert not apply(fixture)["configured"]
    assert not tx.transaction_dir().exists()
