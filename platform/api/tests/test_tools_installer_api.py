"""Tools screen API over the installer engine, with a stand-in container runtime."""
from __future__ import annotations

import json
import shutil
import stat
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
for root in (API_ROOT, REPO_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from routers import tools_installer  # noqa: E402

FAKE_RUNTIME = """#!/bin/bash
case "$1" in
  build)
    [ -n "${FAKE_FAIL_BUILD:-}" ] && { echo "simulated build error"; exit 1; }
    for a in "$@"; do case "$a" in *.sif) out="$a";; esac; done
    echo image > "$out" ;;
  *) exit 0 ;;
esac
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / "tools" / "recipes").mkdir(parents=True)
    (project / "apptainer").mkdir()
    for name in ("biomodstack_tools.py", "biomodstack_runtime_profile.py"):
        shutil.copy2(REPO_ROOT / name, project / name)
    (project / "apptainer" / "demo.def").write_text("Bootstrap: docker\nFrom: example\n")
    (project / "tools" / "recipes" / "demo.json").write_text(json.dumps({
        "schema": "bms.tool-recipe.v1", "id": "demo", "name": "Demo tool", "label": "starting-point",
        "modules": ["ont"], "upstream": {"homepage": "https://example.org/demo"},
        "license": {"id": "DEMO-1.0", "url": "https://example.org/l"},
        "images": [{"filename": "demo.sif", "def": "apptainer/demo.def", "approx_size_gb": 0}],
    }))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    runtime = bin_dir / "apptainer"
    runtime.write_text(FAKE_RUNTIME)
    runtime.chmod(runtime.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("BMS_DATA", str(tmp_path / "data"))
    for name in ("BMS_CONTAINER_DIR", "BMS_WEIGHTS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(tools_installer, "REPO_ROOT", project)
    app = FastAPI()
    app.include_router(tools_installer.router)
    test_client = TestClient(app)
    test_client.data = tmp_path / "data"
    return test_client


def _wait_for(client, tool, wanted, seconds=20):
    deadline = time.time() + seconds
    while time.time() < deadline:
        status = client.get(f"/api/tools/{tool}/status").json()
        if status["status"] in wanted:
            return status
        time.sleep(0.2)
    raise AssertionError(f"{tool} stayed {status['status']}")


def test_overview_lists_modules_and_tools(client):
    body = client.get("/api/tools").json()
    assert body["selection_made"] is False and body["container_runtime_present"] is True
    assert [(t["tool"], t["status"], t["label"]) for t in body["tools"]] == [("demo", "not-installed", "starting-point")]
    assert body["tools"][0]["upstream"] == {"homepage": "https://example.org/demo"}
    modules = {m["module"]: m for m in body["modules"]}
    assert modules["robot"]["state"] == "included" and modules["ont"]["state"] == "needs-tools"


def test_selection_is_saved(client):
    body = client.put("/api/tools/modules", json={"modules": ["ont"]}).json()
    assert body["selection_made"] is True
    assert {m["module"] for m in body["modules"] if m["selected"]} == {"molecular_tools", "robot", "ont"}
    refused = client.put("/api/tools/modules", json={"modules": ["nonsense"]})
    assert refused.status_code == 409 and refused.json()["detail"]["reason_code"] == "unknown_module"


def test_build_runs_in_background_and_becomes_ready(client):
    plan = client.get("/api/tools/demo/plan").json()
    assert plan["status"] == "planned" and [s["step"] for s in plan["steps"]] == ["build:demo.sif"]
    started = client.post("/api/tools/demo/build", json={})
    assert started.status_code == 200 and started.json()["started"] is True
    status = _wait_for(client, "demo", {"ready", "failed"})
    assert status["status"] == "ready" and status["origin"] == "built"
    assert (client.data / "apptainer" / "demo.sif").is_file()
    log = client.get("/api/tools/demo/log", params={"tail": 50}).json()
    assert any("apptainer build" in line for line in log["lines"])
    removed = client.post("/api/tools/demo/remove").json()
    assert removed["status"] == "not-installed" and not (client.data / "apptainer" / "demo.sif").exists()


def test_failed_build_reports_reason(client, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL_BUILD", "1")
    assert client.post("/api/tools/demo/build", json={}).status_code == 200
    status = _wait_for(client, "demo", {"ready", "failed"})
    assert status["status"] == "failed" and status["reason_code"] == "image_build_failed"
    assert not (client.data / "apptainer" / "demo.sif").exists()


def test_adopt_and_unknown_tool(client, tmp_path):
    mine = tmp_path / "mine.sif"
    mine.write_text("image")
    adopted = client.post("/api/tools/demo/adopt", json={"path": str(mine)}).json()
    assert adopted["status"] == "ready" and adopted["origin"] == "adopted"
    client.post("/api/tools/demo/remove")
    assert mine.is_file(), "the adopted original is never deleted"
    missing = client.get("/api/tools/nope/status")
    assert missing.status_code == 404 and missing.json()["detail"]["reason_code"] == "unknown_tool"


def test_changes_are_refused_for_remote_clients(client, monkeypatch):
    monkeypatch.setattr(tools_installer, "LOCAL_ADMIN_HOSTS", {"127.0.0.1"})
    assert client.get("/api/tools").status_code == 200
    for method, path, body in (("put", "/api/tools/modules", {"modules": []}),
                               ("post", "/api/tools/demo/build", {}),
                               ("post", "/api/tools/demo/adopt", {"path": "/x"}),
                               ("post", "/api/tools/demo/verify", None),
                               ("post", "/api/tools/demo/remove", None)):
        assert getattr(client, method)(path, json=body).status_code == 403, path
