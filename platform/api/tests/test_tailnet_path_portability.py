from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path
import sys

import pytest

import biomodstack_runtime_profile as profile
import biomodstack_tailnet as tailnet

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def isolated_installation(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "alternate user"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "selected config"))
    for key in tuple(__import__("os").environ):
        if key.startswith("BMS_"):
            monkeypatch.delenv(key)
    assert Path(tailnet.__file__).resolve() == ROOT / "biomodstack_tailnet.py"


def persist(values):
    path = profile.get_install_profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values))


@pytest.mark.parametrize("lane,leaf", [("development", "dev-test-canonical"), ("production", "prod-main-canonical")])
def test_default_lane_roots_follow_installing_user(monkeypatch, lane, leaf):
    # Import after selecting HOME, like a fresh managed launcher process.
    spec = importlib.util.spec_from_file_location("portable_tailnet", ROOT / "biomodstack_tailnet.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setenv("BMS_HOME", "/unrelated/development/process")
    assert module._canonical_environment_root(lane) == Path.home() / "biomodstack" / leaf


@pytest.mark.parametrize("lane", ["development", "production"])
def test_persistent_lane_root_beats_other_lane_ambient_home(monkeypatch, tmp_path, lane):
    expected = tmp_path / f"{lane} checkout with spaces"
    persist({f"{lane}_project_root": str(expected)})
    monkeypatch.setenv("BMS_HOME", str(tmp_path / "wrong lane"))
    assert tailnet._canonical_environment_root(lane) == expected


@pytest.fixture
def production_report(monkeypatch, tmp_path):
    state = tmp_path / "production storage"
    persist({"data_root": str(state)})
    revision, container_id = "a" * 40, "b" * 64
    cgroup = f"0::/system.slice/docker-{container_id}.scope\n"
    configured = {"data_root": str(state), "inputs_dir": str(tmp_path / "split inputs"),
                  "db_path": str(tmp_path / "split db" / "custom.db")}
    persist(configured)
    mounts = sorted([
        {"type": "bind", "source": m["source"], "destination": m["target"],
         "mode": "ro" if m["read_only"] else "rw", "rw": not m["read_only"], "propagation": "rprivate"}
        for m in profile.core_runtime_storage_mounts(profile.resolve_runtime_paths(profile=configured, environ={}))
    ], key=lambda m: (m["destination"], m["source"]))
    item = {"name": "biomodstack-api", "container_id": container_id, "image_id": tailnet.MANAGED_API_IMAGE_ID, "revision": revision, "compose_working_dir": str(tmp_path), "pid": 123, "cgroup": cgroup, "cmdline": "/bin/sh -ec /app/platform/api/.venv/bin/python run_migrations.py && exec /app/platform/api/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 18000", "cwd": "/app/platform/api", "readonly_rootfs": False, "mounts": mounts}
    process = {"pid": 123, "cgroup": cgroup, "container_pid": 1, "parent_container_pid": 0, "executable": "/usr/local/bin/python3.10", "argv": ["/app/platform/api/.venv/bin/python", "/app/platform/api/.venv/bin/uvicorn", "main:app", "--host", "127.0.0.1", "--port", "18000"], "cwd": "/app/platform/api", "uid": 1000}
    monkeypatch.setattr(tailnet, "_docker_runtime_report", lambda names: {"containers": [item]})
    monkeypatch.setattr(tailnet, "_accepted_release_image_ids", lambda *a: None)
    monkeypatch.setattr(tailnet, "_git_revision", lambda root: revision)
    monkeypatch.setattr(tailnet, "_run", lambda *a: None)
    monkeypatch.setattr(tailnet, "_container_host_pids", lambda name: [123])
    monkeypatch.setattr(tailnet, "_container_process_reports", lambda *a: [process])
    monkeypatch.setattr(tailnet, "_process_cgroup", lambda pid: cgroup)
    return item


def test_production_mounts_follow_persisted_profile_not_dev_environment(monkeypatch, tmp_path, production_report):
    for key in ("BMS_DATA", "BMS_STATE_DIR", "BMS_HOME", "BMS_CONTAINER_STATE_PATH", "BMS_MK1D_RECOVERY_SOCKET_DIR"):
        monkeypatch.setenv(key, str(tmp_path / "wrong development value"))
    report = tailnet._validated_container_runtime(tmp_path, require_web=False)
    assert report["containers"][0]["mounts"] == production_report["mounts"]


@pytest.mark.parametrize("change", ["source", "destination", "mode", "extra", "missing_socket", "image", "cgroup"])
def test_production_portability_keeps_exact_provenance(monkeypatch, tmp_path, production_report, change):
    item = production_report
    if change == "source":
        item["mounts"][1]["source"] = str(tmp_path / "other lane")
        monkeypatch.setenv("BMS_DATA", item["mounts"][1]["source"])
    elif change == "destination":
        item["mounts"][1]["destination"] = "/other"
    elif change == "mode":
        item["mounts"][0].update(mode="rw", rw=True)
    elif change == "extra":
        item["mounts"].append(dict(item["mounts"][1], destination="/app"))
    elif change == "missing_socket":
        item["mounts"].pop(0)
    elif change == "image":
        item["image_id"] = "sha256:" + "c" * 64
    else:
        item["cgroup"] = "0::/foreign.scope"
    with pytest.raises(tailnet.TailnetEnvironmentError):
        tailnet._validated_container_runtime(tmp_path, require_web=False)


def test_sync_restart_retains_installation_selection_without_runtime_leak(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("portable_sync", ROOT / "scripts" / "biomodstack_dev_sync.py")
    assert spec and spec.loader
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
    captured = {}

    def fake_run(args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(sync.subprocess, "run", fake_run)
    for key in ("BMS_HOME", "BMS_DATA", "BMS_DB_PATH", "BMS_RUNTIME_MODE", "GIT_INDEX_FILE"):
        monkeypatch.setenv(key, "unrelated caller value")
    sync._run(tmp_path, sys.executable, str(tmp_path / "scripts" / "manage_desktop_services.py"), "restart", "--runtime", "dev")
    assert captured["env"]["XDG_CONFIG_HOME"] == os.environ["XDG_CONFIG_HOME"]
    assert captured["env"]["HOME"] == str(Path.home())
    assert not any(key.startswith("BMS_") for key in captured["env"])
    assert "GIT_INDEX_FILE" not in captured["env"]
