"""Invocation-only deployment override; no job mutation or persistent bypass."""
from __future__ import annotations

import fcntl
import json
import sqlite3
import subprocess

import pytest

from test_biomodstack_dev_sync import MODULE_PATH, load_module


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    sync = load_module()
    root = tmp_path / "root"
    root.mkdir()
    (root / ".git").mkdir()
    source = root / "scripts" / "biomodstack_dev_sync.py"
    source.parent.mkdir()
    source.write_bytes(MODULE_PATH.read_bytes())
    state = tmp_path / "state"
    sync.set_deployment_paused(state, True)
    installed = tmp_path / "installed.py"
    installed.write_bytes(b"previous synchronizer\n")
    monkeypatch.setattr(sync, "DEFAULT_INSTALLED_SYNC", installed)
    monkeypatch.delenv(sync.DEPLOYMENT_ADMISSION_LOCK_ENV, raising=False)
    database = tmp_path / "jobs.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE jobs (id TEXT, status TEXT, queue_status TEXT, remote_attempt_id TEXT)")
        db.execute("INSERT INTO jobs VALUES ('job', 'running', 'running', 'original-attempt')")
    monkeypatch.setattr(sync, "_development_database", lambda _: database)
    current = {"head": "a" * 40, "live": "a" * 40, "remote": "b" * 40, "dirty": "", "restart_failures": 0}
    calls = []

    def fenced():
        for name in ("dev-sync.lock", sync.DEPLOYMENT_ADMISSION_LOCK_FILENAME):
            with (state / name).open("a+") as lock:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def git(_root, *args, **kwargs):
        calls.append(args)
        if args == ("status", "--porcelain"):
            return current["dirty"]
        if args == ("rev-parse", "HEAD"):
            return current["head"]
        if args == ("rev-parse", "refs/remotes/origin/test"):
            return current["remote"]
        if args[:2] == ("fetch", "--quiet"):
            return ""
        if args[:2] == ("merge", "--ff-only"):
            fenced()
            current["head"] = current["remote"]
            return ""
        if args[:2] == ("reset", "--hard"):
            fenced()
            current["head"] = args[2]
            return ""
        raise AssertionError(args)

    def run(_root, *args, **kwargs):
        if args[:2] == ("git", "merge-base"):
            return subprocess.CompletedProcess(args, 0)
        fenced()
        assert args[-3:] == ("restart", "--runtime", "dev")
        calls.append(("restart",))
        if current["restart_failures"]:
            current["restart_failures"] -= 1
            raise subprocess.CalledProcessError(1, args)
        current["live"] = current["head"]
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(sync, "_git", git)
    monkeypatch.setattr(sync, "_run", run)
    monkeypatch.setattr(sync, "_git_blob", lambda *_: source.read_bytes())
    monkeypatch.setattr(sync, "_deployed_revision", lambda _: current["live"])
    return sync, root, state, database, installed, current, calls


def rows(database):
    with sqlite3.connect(database) as db:
        return db.execute("SELECT * FROM jobs").fetchall()


@pytest.mark.parametrize("paused", [True, False])
def test_explicit_override_deploys_preserves_jobs_pause_and_default_policy(deployment, paused):
    sync, root, state, database, installed, current, calls = deployment
    sync.set_deployment_paused(state, paused)
    control = (state / sync.SYNC_CONTROL_FILENAME).read_bytes()
    before = rows(database)
    assert sync.sync_once(root, state, deploy_now=True, allow_active_work=True) == "fast-forward-deploy"
    receipt = json.loads((state / "dev-sync.json").read_text())
    assert receipt["status"] == "deployed"
    assert receipt["active_work_count"] == 1
    assert receipt["allow_active_work"] is True and receipt["manual_deploy"] is True
    assert receipt["deployment_paused"] is paused
    assert receipt["deployed_revision_after"] == "b" * 40
    assert current["live"] == "b" * 40
    assert installed.read_bytes() == MODULE_PATH.read_bytes()
    assert calls.count(("restart",)) == 1
    assert rows(database) == before
    assert (state / sync.SYNC_CONTROL_FILENAME).read_bytes() == control
    # The next timer poll cannot inherit this manual permission.
    current["remote"] = "c" * 40
    assert sync.sync_once(root, state) == ("paused" if paused else "deferred-active-work")
    assert calls.count(("restart",)) == 1
    assert rows(database) == before
    service = sync.render_sync_units(root)[sync.SYNC_SERVICE]
    assert "--once" in service and "--allow-active-work" not in service


def test_deploy_now_alone_does_not_override_active_work(deployment):
    sync, root, state, database, _, _, calls = deployment
    assert sync.sync_once(root, state, deploy_now=True) == "deferred-active-work"
    assert ("restart",) not in calls
    assert rows(database) == [("job", "running", "running", "original-attempt")]
    assert sync._read_deployment_paused(state)


def test_active_work_rechecked_under_fence_without_override(deployment, monkeypatch):
    sync, root, state, _, _, _, calls = deployment
    results = iter([(False, 0), (True, 1)])
    monkeypatch.setattr(sync, "_active_development_work", lambda _: next(results))
    assert sync.sync_once(root, state, deploy_now=True) == "deferred-active-work"
    assert ("restart",) not in calls


def test_override_applies_to_fenced_recheck_without_falsifying_count(deployment, monkeypatch):
    sync, root, state, _, _, _, calls = deployment
    results = iter([(False, 0), (True, 2)])
    monkeypatch.setattr(sync, "_active_development_work", lambda _: next(results))
    assert sync.sync_once(root, state, deploy_now=True, allow_active_work=True) == "fast-forward-deploy"
    assert json.loads((state / "dev-sync.json").read_text())["active_work_count"] == 2
    assert calls.count(("restart",)) == 1


def test_override_keeps_rollback_and_pause_without_touching_job(deployment):
    sync, root, state, database, installed, current, calls = deployment
    current["restart_failures"] = 1
    before = rows(database)
    with pytest.raises(sync.DeploymentRolledBackError):
        sync.sync_once(root, state, deploy_now=True, allow_active_work=True)
    assert current["head"] == current["live"] == "a" * 40
    assert installed.read_bytes() == b"previous synchronizer\n"
    assert calls.count(("restart",)) == 2
    assert rows(database) == before and sync._read_deployment_paused(state)
    receipt = json.loads((state / "dev-sync.json").read_text())
    assert receipt["decision"] == "blocked-deployment-rolled-back"
    assert receipt["allow_active_work"] is True


@pytest.mark.parametrize("change, expected", [
    ({"dirty": True}, "blocked-dirty"),
    ({"remote_descends_from_local": False}, "blocked-diverged"),
    ({"deployed_revision": None}, "blocked-health-unavailable"),
])
def test_override_only_bypasses_active_work(change, expected):
    sync = load_module()
    options = dict(dirty=False, local_revision="a" * 40, remote_revision="b" * 40,
                   deployed_revision="a" * 40, remote_descends_from_local=True,
                   active_work=True, allow_active_work=True)
    assert sync.plan_sync(**(options | change)) == expected


@pytest.mark.parametrize("action", ["--once", "--resume-deploy", "--install"])
def test_override_cannot_be_attached_to_automatic_controls(action, monkeypatch):
    sync = load_module()
    monkeypatch.setattr(sync.sys, "argv", ["dev-sync", action, "--allow-active-work"])
    with pytest.raises(SystemExit) as error:
        sync.main()
    assert error.value.code == 2


def test_cli_runs_real_transaction_without_resuming_timer(deployment, monkeypatch, capsys):
    sync, root, state, database, _, current, calls = deployment
    before = rows(database)
    monkeypatch.setattr(sync.sys, "argv", ["dev-sync", "--deploy-now", "--allow-active-work",
                                          "--root", str(root), "--state-dir", str(state)])
    assert sync.main() == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "fast-forward-deploy"
    assert current["live"] == "b" * 40
    assert rows(database) == before and sync._read_deployment_paused(state)
    assert not any(call[0] == "systemctl" for call in calls)
