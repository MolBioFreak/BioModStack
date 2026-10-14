from __future__ import annotations

import asyncio
from contextlib import contextmanager
import fcntl
import importlib
import inspect
import sys
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException, Request, Response


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import main as api_main
import runtime_policy
from routers import jobs
from schemas import JobCreate
from services import analysis_autorun, nextflow


class _ExplodingRegistry:
    def reload(self) -> None:
        raise AssertionError("model registry should not be touched when core-runtime mode blocks workflow launches")


class _ExplodingSession:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("database should not be touched when core-runtime mode blocks workflow launches")






























@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["cross-class-backlog", "preparation-failure"])
async def test_analysis_worker_real_loop_preserves_progress_and_cleanup(monkeypatch, tmp_path, scenario):
    from datetime import datetime, timedelta
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from database import AnalysisRun, Base
    from services import analysis_worker
    from services.analysis_runs import build_artifact_manifest_for_run
    from paths import resolve_allowed_path

    monkeypatch.setenv("BMS_ANALYSIS_CACHE", str(tmp_path / "analysis"))
    monkeypatch.setenv("BMS_ANALYSIS_MAX_CONCURRENT_HEAVY", "1")
    monkeypatch.setenv("BMS_ANALYSIS_MAX_CONCURRENT_LIGHT", "4")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'analysis.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    worker = analysis_worker.AnalysisWorker(factory, poll_interval=0.001)
    children = []

    class Child:
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

    def spawn(*_, **__):
        child = Child()
        children.append(child)
        return child

    def run(identity, resource_class, order):
        row = AnalysisRun(
            id=identity, subject_kind="job", subject_id="subject", analysis_type="job_aa_composition",
            resource_class=resource_class, status="queued", params_hash=identity,
            input_signature=identity, code_version="1", cache_key=identity,
            queued_at=datetime(2026, 1, 1) + timedelta(seconds=order),
        )
        row.artifact_manifest = build_artifact_manifest_for_run(row)
        return row

    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        if scenario == "cross-class-backlog":
            rows = [run(f"heavy-{i}", "cpu_heavy", i) for i in range(65)]
            rows.append(run("light", "cpu_light", 100))
        else:
            rows = [run("good", "cpu_light", 0), run("bad", "cpu_light", 1)]
            # Valid producer manifests; a real filesystem failure after first spawn.
            resolve_allowed_path(rows[1].artifact_manifest["stderr_log"]).mkdir(parents=True)
        async with factory() as session:
            session.add_all(rows)
            await session.commit()

        cycles = 0
        launch = worker._launch_available_runs

        async def bounded_launch():
            nonlocal cycles
            await launch()
            cycles += 1
            if cycles == 3:
                worker._stop_event.set()

        monkeypatch.setattr(worker, "_launch_available_runs", bounded_launch)
        monkeypatch.setattr(analysis_worker.subprocess, "Popen", spawn)
        await worker.start()
        # Exercise the task itself, not merely the start/stop calls. Current
        # preparation failure is intentionally an effective, un-xfailed regression.
        await asyncio.wait_for(asyncio.shield(worker._task), timeout=5)
        await worker.stop()
        async with factory() as session:
            if scenario == "cross-class-backlog":
                assert (await session.get(AnalysisRun, "light")).status == "running"
            else:
                assert (await session.get(AnalysisRun, "bad")).status == "failed"
        assert children and all(child.terminated for child in children)
        assert not worker._running
    finally:
        worker._stop_event.set()
        if worker._task is not None:
            if not worker._task.done():
                worker._task.cancel()
            await asyncio.gather(worker._task, return_exceptions=True)
        # Test cleanup must not conceal whether normal stop owned the children.
        await worker._terminate_running_processes()
        await engine.dispose()
