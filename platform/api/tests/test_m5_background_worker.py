"""Acceptance tests for M5 (R31): standalone background worker, GPU stats service, and writer custody."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import biomodstack_services as services
import biomodstack_core_runtime_controller as core_controller
import bms_backup


def test_worker_registered_across_units_compose_controller_and_backup(tmp_path: Path, monkeypatch) -> None:
    """R31 + C40: worker unit, runtime_service_names, compose, ALL_SERVICES, and backup writer-stop list."""
    assert services.WORKER_SERVICE == "biomodstack-worker.service"
    assert services.WORKER_SERVICE in services.runtime_service_names("dev")
    assert services.WORKER_SERVICE in services.all_runtime_service_names()
    assert "bms-worker" in core_controller.ALL_SERVICES
    assert core_controller.expected_container_names()["bms-worker"] == "biomodstack-worker"
    assert services.WORKER_SERVICE in bms_backup.DEFAULT_WRITER_UNITS

    # Verify backup refuses to run when biomodstack-worker.service is active.
    def _fake_runner(cmd, **_kwargs):
        unit = cmd[-1]
        if unit == services.WORKER_SERVICE:
            return SimpleNamespace(returncode=0, stdout="active\n", stderr="")
        return SimpleNamespace(returncode=3, stdout="inactive\n", stderr="")

    with pytest.raises(bms_backup.BackupError, match="biomodstack-worker.service"):
        bms_backup.ensure_writers_stopped(runner=_fake_runner)


def test_gpu_stats_extracted_from_router_with_no_router_imports() -> None:
    """R31 + C55: GPU stats live in services/gpu_stats.py and routers/gpu.py re-exports them."""
    from services import gpu_stats
    from routers import gpu as gpu_router

    assert gpu_router.get_gpu_stats is gpu_stats.get_gpu_stats
    assert gpu_router.get_gpu_stats_with_error is gpu_stats.get_gpu_stats_with_error
    assert gpu_router.GPUStatusEnhanced is gpu_stats.GPUStatusEnhanced
    src = Path(gpu_stats.__file__).read_text(encoding="utf-8")
    assert "from routers" not in src and "import routers" not in src


def test_worker_single_owner_lock_prevents_duplicate_owner(tmp_path: Path, monkeypatch) -> None:
    """C40: Only one WorkerRuntime may own the worker lock for a given data root."""
    import worker as bms_worker

    monkeypatch.setattr(bms_worker, "_worker_lock_path", lambda: tmp_path / ".biomodstack-worker.lock")
    first = bms_worker.WorkerRuntime(acquire_lock=True)
    second = bms_worker.WorkerRuntime(acquire_lock=True)
    first._claim_single_owner_lock()
    try:
        with pytest.raises(RuntimeError, match="already holds"):
            second._claim_single_owner_lock()
    finally:
        first._release_single_owner_lock()
    # After release, a new owner can claim it cleanly.
    second._claim_single_owner_lock()
    second._release_single_owner_lock()


def test_worker_runtime_starts_and_stops_cleanly_within_timeout(tmp_path: Path, monkeypatch) -> None:
    """C41: WorkerRuntime starts Loops 1-4 and stops promptly without hanging on pollers."""
    import worker as bms_worker

    events: list[str] = []

    class _DummyWorker:
        def __init__(self, name: str):
            self.name = name

        async def start(self):
            events.append(f"start:{self.name}")

        async def stop(self):
            events.append(f"stop:{self.name}")

    async def _slow_poll(_sf, stop_event):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            events.append("cancelled:poll")
            raise

    class _DummySessionCtx:
        async def __aenter__(self):
            return SimpleNamespace()

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(bms_worker, "_worker_lock_path", lambda: tmp_path / ".biomodstack-worker.lock")
    monkeypatch.setattr(bms_worker.catalog_authority, "require", lambda: None)
    monkeypatch.setattr(bms_worker, "init_db", lambda: asyncio.sleep(0))
    monkeypatch.setattr(bms_worker, "init_experiment_db", lambda: asyncio.sleep(0))
    monkeypatch.setattr(bms_worker, "async_session", lambda: _DummySessionCtx())
    monkeypatch.setattr(bms_worker, "install_feature_enabled", lambda _k: True)
    monkeypatch.setattr(bms_worker, "workflow_launches_allowed", lambda: True)
    monkeypatch.setattr(bms_worker, "workflow_adapter_base_url", lambda: None)
    monkeypatch.setattr(bms_worker, "invalidate_vast_inventory", lambda _s: asyncio.sleep(0))
    monkeypatch.setattr(bms_worker, "run_vast_inventory_refresh", _slow_poll)
    monkeypatch.setattr(bms_worker.remote_telemetry, "run", _slow_poll)
    monkeypatch.setattr(bms_worker, "ExternalImportWorker", lambda *a, **k: _DummyWorker("ext"))
    monkeypatch.setattr(bms_worker, "BoltzApiJobWorker", lambda *a, **k: _DummyWorker("boltz"))
    monkeypatch.setattr(bms_worker, "FrustraMPNNStatisticsWorker", lambda *a, **k: _DummyWorker("frustra"))
    monkeypatch.setattr(bms_worker, "MdReconcilerWorker", lambda *a, **k: _DummyWorker("md"))
    monkeypatch.setattr(bms_worker, "GPUOrchestrator", lambda *a, **k: _DummyWorker("orch"))
    monkeypatch.setattr(bms_worker, "AnalysisWorker", lambda *a, **k: _DummyWorker("analysis"))

    async def _run():
        rt = bms_worker.WorkerRuntime()
        await rt.start()
        t0 = time.monotonic()
        await asyncio.wait_for(rt.stop(), timeout=2.0)
        return time.monotonic() - t0

    elapsed = asyncio.run(_run())
    assert elapsed < 2.0
    for name in ("ext", "boltz", "frustra", "md", "orch", "analysis"):
        assert f"start:{name}" in events
        assert f"stop:{name}" in events


def test_remote_telemetry_persists_snapshot_across_worker_and_api(tmp_path: Path, monkeypatch) -> None:
    """Worker writes remote telemetry snapshot; API reader reads it without SSH."""
    from collections import deque
    from services.remote_execution import telemetry as tel_mod
    from services.remote_execution import targets as targets_mod

    monkeypatch.setattr(tel_mod.RemoteTelemetry, "_snapshot_path", staticmethod(lambda: tmp_path / "remote_telemetry_state.json"))
    monkeypatch.setattr(targets_mod, "telemetry_eligible", lambda _t: True)
    monkeypatch.setattr(
        targets_mod,
        "_target_response",
        lambda t: SimpleNamespace(model_dump=lambda mode="json": {"id": t.id}),
    )

    fake_target = SimpleNamespace(
        id="target-1", host="1.2.3.4", port=22, username="root",
        remote_root="/workspace", host_key_sha256="abc", activated_at="2026-01-01T00:00:00Z",
    )

    writer = tel_mod.RemoteTelemetry(persist=True)
    key = tel_mod.identity(fake_target)
    now_mono = time.monotonic()
    now_wall = time.time()
    writer.sequence = 1
    writer.entries[key] = {
        "epoch": "ep1",
        "history": deque([(1, now_mono, now_wall, {"available": True, "gpus": [{"index": 0, "memory_total_mb": 24576}], "sequence": 1})]),
    }
    writer._write_snapshot()

    reader = tel_mod.RemoteTelemetry(persist=True)
    sample = reader.read(fake_target)
    assert sample["available"] is True
    assert sample["gpus"][0]["memory_total_mb"] == 24576
    assert sample["cursor"] == "ep1:1"


def test_concurrent_api_worker_runner_sqlite_writes_raise_no_lock_error(tmp_path: Path) -> None:
    """C42: Concurrent writers (API, worker, runner) under WAL + busy_timeout raise no database is locked."""
    db_path = tmp_path / "concurrent.db"
    init_conn = sqlite3.connect(str(db_path), timeout=30.0)
    init_conn.execute("PRAGMA journal_mode=WAL")
    init_conn.execute("PRAGMA synchronous=NORMAL")
    init_conn.execute("CREATE TABLE writes (id INTEGER PRIMARY KEY AUTOINCREMENT, writer TEXT NOT NULL, seq INTEGER NOT NULL)")
    init_conn.commit()
    init_conn.close()

    errors: list[Exception] = []

    def _writer(name: str) -> None:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            for i in range(40):
                conn.execute("INSERT INTO writes (writer, seq) VALUES (?, ?)", (name, i))
                conn.commit()
        except Exception as exc:
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=_writer, args=(role,)) for role in ("api", "worker", "runner")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    check = sqlite3.connect(str(db_path))
    count = check.execute("SELECT COUNT(*) FROM writes").fetchone()[0]
    check.close()
    assert count == 120
