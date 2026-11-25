"""Acceptance tests for R37 (C58-C62): robot controls bypass deploy fence and deploys defer on active BioXP runs."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import urllib.error

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import main as api_main

REPO_ROOT = Path(__file__).resolve().parents[3]
DEV_SYNC_PATH = REPO_ROOT / "scripts" / "biomodstack_dev_sync.py"


def _load_dev_sync():
    spec = importlib.util.spec_from_file_location("biomodstack_dev_sync_r37", DEV_SYNC_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_fenced_probe_app() -> FastAPI:
    probe = FastAPI()
    probe.middleware("http")(api_main.deployment_admission_fence)

    @probe.post("/api/bioxp/operator-controls/actions/{action_id}")
    async def operator_action(action_id: str):
        return {"reached": True, "action_id": action_id}

    @probe.post("/api/bioxp/methods/runs/{job_id}/control")
    async def method_run_control(job_id: str):
        return {"reached": True, "job_id": job_id, "control": "safe_stop"}

    @probe.post("/api/bioxp/methods/quick-runs")
    async def method_quick_run():
        return {"reached": True}

    @probe.post("/api/bioxp/methods/library/{template_id}/runs")
    async def method_library_run(template_id: str):
        return {"reached": True, "template_id": template_id}

    @probe.post("/api/bioxp/protocols/submit")
    async def protocol_submit():
        return {"reached": True}

    @probe.post("/api/jobs")
    async def create_job():
        return {"reached": True}

    return probe


@contextmanager
def _hold_deploy_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sync = _load_dev_sync()
    lock_path = tmp_path / "dev-sync-admission.lock"
    monkeypatch.setenv(sync.DEPLOYMENT_ADMISSION_LOCK_ENV, str(lock_path))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield lock_path
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def test_c58_c59_c60_deploy_fence_carveout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(_build_fenced_probe_app())
    with _hold_deploy_lock(tmp_path, monkeypatch):
        # C58: abort_all reaches handler while deploy lock is held
        abort_resp = client.post("/api/bioxp/operator-controls/actions/oem.abort_all")
        assert abort_resp.status_code == 200
        assert abort_resp.json() == {"reached": True, "action_id": "oem.abort_all"}

        # C59: method-run safe_stop control reaches handler while deploy lock is held
        stop_resp = client.post("/api/bioxp/methods/runs/run-123/control", json={"action": "safe_stop"})
        assert stop_resp.status_code == 200
        assert stop_resp.json()["reached"] is True

        # C60: new run-start routes stay fenced with 503
        assert client.post("/api/bioxp/methods/quick-runs").status_code == 503
        assert client.post("/api/bioxp/methods/library/tpl-1/runs").status_code == 503
        assert client.post("/api/bioxp/protocols/submit").status_code == 503
        assert client.post("/api/jobs").status_code == 503


def _create_idle_dev_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT, queue_status TEXT)")
    conn.execute("INSERT INTO jobs VALUES ('j1', 'completed', 'completed')")
    conn.commit()
    conn.close()


class _FakeHTTPResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._buf = io.BytesIO(body)

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_c61_dev_sync_defers_when_bioxp_run_active(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sync = _load_dev_sync()
    db_path = tmp_path / "biomodstack.db"
    _create_idle_dev_db(db_path)
    monkeypatch.setattr(sync, "_development_database", lambda _root: db_path)
    # Serialize the actual endpoint response type, not an ad hoc rows dictionary.
    from services.bioxp.protocol_models import ProtocolJobs, ProtocolJobSummary

    page = ProtocolJobs(rows=[ProtocolJobSummary(
        job_id="bx-1", status="running", dry_run=True, protocol_id="fixture",
        source_type="native", created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
        pending_review=None,
    )])
    monkeypatch.setattr(sync.urllib.request, "urlopen", lambda _url, timeout=3:
                        _FakeHTTPResponse(200, page.model_dump_json(exclude_unset=True).encode()))
    active, count = sync._active_development_work(tmp_path)
    assert active is True
    assert count == 1


def test_c62_dev_sync_proceeds_when_bioxp_off_or_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sync = _load_dev_sync()
    db_path = tmp_path / "biomodstack.db"
    _create_idle_dev_db(db_path)
    monkeypatch.setattr(sync, "_development_database", lambda _root: db_path)

    # BioXP feature disabled (404) or robot powered off (502) or API unreachable
    for exc in (
        urllib.error.HTTPError("http://127.0.0.1:18002/api/bioxp/protocols/jobs", 502, "Bad Gateway", {}, None),
        urllib.error.HTTPError("http://127.0.0.1:18002/api/bioxp/protocols/jobs", 404, "Not Found", {}, None),
        urllib.error.URLError("connection refused"),
    ):
        monkeypatch.setattr(sync.urllib.request, "urlopen", lambda _url, timeout=3, _e=exc: (_ for _ in ()).throw(_e))
        active, count = sync._active_development_work(tmp_path)
        assert active is False
        assert count == 0

    # Completed BioXP runs do not block deploy
    monkeypatch.setattr(
        sync.urllib.request,
        "urlopen",
        lambda _url, timeout=3: _FakeHTTPResponse(
            200,
            json.dumps({"rows": [{"job_id": "bx-done", "status": "completed"}]}).encode("utf-8"),
        ),
    )
    active, count = sync._active_development_work(tmp_path)
    assert active is False
    assert count == 0


@pytest.mark.parametrize("body", [b"not-json", b'{"rows":null}'])
def test_status_read_failure_is_not_positive_idle_evidence(tmp_path, monkeypatch, body):
    # Characterize the approved fail-open policy. No robot or listening API.
    sync = _load_dev_sync()
    monkeypatch.setattr(sync.urllib.request, "urlopen", lambda _url, timeout=3: _FakeHTTPResponse(200, body))
    assert sync._active_bioxp_runs(tmp_path) == 0
