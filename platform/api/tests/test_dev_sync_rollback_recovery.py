from __future__ import annotations

import fcntl
import json
import subprocess
from pathlib import Path

import pytest

from test_biomodstack_dev_sync import load_module


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    sync = load_module()
    root = tmp_path / "root"
    root.mkdir()
    (root / ".git").mkdir()
    state = tmp_path / "state"
    installed = tmp_path / "installed.py"
    installed.write_bytes(b"candidate")
    monkeypatch.setattr(sync, "DEFAULT_INSTALLED_SYNC", installed)
    monkeypatch.delenv(sync.DEPLOYMENT_ADMISSION_LOCK_ENV, raising=False)
    sync.set_deployment_paused(state, True)
    marker = sync._new_sync_refresh_marker("b" * 40, b"candidate", "a" * 40, b"previous")
    marker["phase"] = "rollback-failed"
    sync._write_sync_refresh_marker(state, marker)
    sync._write_queued_revision(state, "b" * 40)
    calls = []
    current = {"head": "b" * 40}

    def fenced():
        for name in ("dev-sync.lock", sync.DEPLOYMENT_ADMISSION_LOCK_FILENAME):
            with (state / name).open("a+") as lock:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def git(_root, *args):
        fenced()
        calls.append(args)
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return current["head"]
        if args == ("reset", "--hard", "a" * 40):
            current["head"] = "a" * 40
            return ""
        raise AssertionError(args)

    def restart(_root, *args):
        fenced()
        assert args[-3:] == ("restart", "--runtime", "dev")
        assert sync._read_sync_refresh_required(state) == marker
        calls.append(("restart",))

    monkeypatch.setattr(sync, "_git", git)
    monkeypatch.setattr(sync, "_run", restart)
    monkeypatch.setattr(sync, "_git_blob", lambda *args: b"candidate")
    monkeypatch.setattr(sync, "_active_development_work", lambda root: (False, 0))
    monkeypatch.setattr(sync, "_deployed_revision", lambda root: "a" * 40)
    monkeypatch.setattr(sync, "validate_candidate_runtime_authority", lambda *args: fenced() or {})
    return sync, root, state, installed, marker, calls, current


def test_recover_failed_rollback_verifies_then_clears_under_both_fences(recovery):
    sync, root, state, installed, marker, calls, _ = recovery
    receipt = sync.recover_failed_rollback(root, state)
    assert receipt["decision"] == "recovered-failed-rollback"
    assert installed.read_bytes() == b"previous"
    assert installed.stat().st_mode & 0o777 == 0o755
    assert sync._read_sync_refresh_required(state) is None
    assert sync._read_deployment_paused(state)
    assert sync._read_queued_revision(state) == marker["target_revision"]
    assert ("reset", "--hard", marker["rollback_revision"]) in calls


@pytest.mark.parametrize("failure", ["pause", "missing", "phase", "malformed", "backup", "dirty", "head", "candidate", "active", "database", "authority"])
def test_recovery_preconditions_preserve_evidence_without_restart(recovery, monkeypatch, failure):
    sync, root, state, installed, marker, calls, current = recovery
    path = state / sync.SYNC_REFRESH_FILENAME
    if failure == "pause":
        sync.set_deployment_paused(state, False)
    elif failure == "missing":
        path.unlink()
    elif failure == "phase":
        marker["phase"] = "prepared"
        sync._write_sync_refresh_marker(state, marker)
    elif failure == "malformed":
        path.write_text('{"schema":')
    elif failure == "backup":
        marker["installed_before_sha256"] = "0" * 64
        sync._write_sync_refresh_marker(state, marker)
    elif failure == "dirty":
        monkeypatch.setattr(sync, "_git", lambda *args: "dirty")
    elif failure == "head":
        current["head"] = "c" * 40
    elif failure == "candidate":
        monkeypatch.setattr(sync, "_git_blob", lambda *args: b"wrong")
    elif failure == "active":
        monkeypatch.setattr(sync, "_active_development_work", lambda *args: (True, 1))
    else:
        def fail(*args):
            raise RuntimeError(failure)
        monkeypatch.setattr(sync, "_active_development_work" if failure == "database" else "validate_candidate_runtime_authority", fail)
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(RuntimeError):
        sync.recover_failed_rollback(root, state)
    assert (path.read_bytes() if path.exists() else None) == before
    assert installed.read_bytes() == b"candidate"
    assert ("restart",) not in calls
    assert not any(call[0] == "reset" for call in calls)


@pytest.mark.parametrize("failure", ["restart", "health", "source", "installed", "mode", "interrupt"])
def test_recovery_failure_remains_explicitly_blocked(recovery, monkeypatch, failure):
    sync, root, state, installed, marker, calls, current = recovery
    if failure in {"restart", "interrupt"}:
        def fail(*args):
            raise KeyboardInterrupt() if failure == "interrupt" else subprocess.CalledProcessError(1, "restart")
        monkeypatch.setattr(sync, "_run", fail)
    elif failure == "health":
        monkeypatch.setattr(sync, "_deployed_revision", lambda *args: None)
    elif failure == "source":
        def health(*args):
            current["head"] = "c" * 40
            return "a" * 40
        monkeypatch.setattr(sync, "_deployed_revision", health)
    else:
        def bad_write(raw, target, *, mode):
            target.write_bytes(raw if failure == "mode" else b"wrong")
            target.chmod(0o600)
        monkeypatch.setattr(sync, "_atomic_write_bytes", bad_write)
    with pytest.raises(KeyboardInterrupt if failure == "interrupt" else sync.DeploymentRollbackFailedError):
        sync.recover_failed_rollback(root, state)
    assert sync._read_sync_refresh_required(state) == marker
    with pytest.raises(RuntimeError, match="failed rollback evidence"):
        sync._complete_sync_refresh(root, state, marker["target_revision"])
    assert sync._read_queued_revision(state) == marker["target_revision"]


def test_cli_recovery_and_conflicting_operation(recovery, monkeypatch, capsys):
    sync, root, state, *_ = recovery
    argv = ["dev-sync", "--recover-failed-rollback", "--root", str(root), "--state-dir", str(state)]
    monkeypatch.setattr(sync.sys, "argv", argv + ["--resume-deploy"])
    with pytest.raises(SystemExit) as error:
        sync.main()
    assert error.value.code == 2
    monkeypatch.setattr(sync.sys, "argv", argv)
    assert sync.main() == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "recovered-failed-rollback"
    assert sync.main() == 1  # No marker is not another successful recovery.


def test_recovery_restores_absent_installed_baseline(recovery):
    sync, root, state, installed, marker, *_ = recovery
    marker["installed_before_sha256"] = None
    marker["installed_before_base64"] = None
    sync._write_sync_refresh_marker(state, marker)
    sync.recover_failed_rollback(root, state)
    assert not installed.exists()
    assert sync._read_sync_refresh_required(state) is None


def test_failed_marker_still_blocks_normal_deploy(recovery):
    sync, root, state, installed, marker, calls, _ = recovery
    # The normal candidate path is not a recovery authorization.
    sync.validate_candidate_runtime_authority = lambda *args: {}
    with pytest.raises(RuntimeError, match="failed rollback evidence"):
        sync._deploy_candidate(root, state, "deploy-current", "b" * 40, "b" * 40, "a" * 40)
    assert calls == []
    assert sync._read_sync_refresh_required(state) == marker


def test_run_binds_root_despite_inherited_bms_home(tmp_path, monkeypatch):
    sync = load_module()
    monkeypatch.setenv("BMS_HOME", "/unrelated/worktree")
    monkeypatch.setenv("BMS_RUNTIME_MODE", "prod")
    captured = {}
    def run(args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(sync.subprocess, "run", run)
    sync._run(tmp_path, "manager", "restart", "--runtime", "dev")
    assert captured["env"]["BMS_HOME"] == str(tmp_path.resolve())
    assert "BMS_RUNTIME_MODE" not in captured["env"]
