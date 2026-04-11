from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
from typing import Dict, Optional

from sqlalchemy import select

from database import AnalysisRun
from services.analysis_runs import build_artifact_manifest_for_run


@dataclass
class _RunningProcess:
    process: subprocess.Popen
    stdout_handle: object
    stderr_handle: object
    resource_class: str


def _max_runs_for_class(resource_class: str) -> int:
    normalized = str(resource_class or "").strip().lower()
    if normalized == "cpu_light":
        env_name = "BMS_ANALYSIS_MAX_CONCURRENT_LIGHT"
        default = "4"
    else:
        env_name = "BMS_ANALYSIS_MAX_CONCURRENT_HEAVY"
        default = "1"
    try:
        return max(1, int(os.getenv(env_name, default)))
    except (TypeError, ValueError):
        return int(default)


def _threads_for_class(resource_class: str) -> int:
    normalized = str(resource_class or "").strip().lower()
    if normalized == "cpu_light":
        override = os.getenv("BMS_ANALYSIS_LIGHT_CPUS")
        default = "1"
    else:
        override = os.getenv("BMS_ANALYSIS_HEAVY_CPUS")
        default = None

    cpu_count = max(1, os.cpu_count() or 1)
    if override:
        value = str(override).strip().lower()
        if value == "all":
            return cpu_count
        if value == "all_minus_2":
            return max(1, cpu_count - 2)
        try:
            return max(1, min(cpu_count, int(value)))
        except (TypeError, ValueError):
            pass
    if default is not None:
        try:
            return max(1, min(cpu_count, int(default)))
        except (TypeError, ValueError):
            return 1
    return max(1, cpu_count - 2)


class AnalysisWorker:
    """Background worker that launches persisted analysis runs in subprocesses."""

    def __init__(self, db_session_factory, poll_interval: float = 2.0):
        self._db_session_factory = db_session_factory
        self._poll_interval = poll_interval
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._running: Dict[str, _RunningProcess] = {}

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            await self._task
            self._task = None

    async def _run_loop(self) -> None:
        await self._recover_orphaned_runs()
        while not self._stop_event.is_set():
            await self._reconcile_processes()
            await self._launch_available_runs()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval)
            except asyncio.TimeoutError:
                continue

        await self._terminate_running_processes()

    async def _recover_orphaned_runs(self) -> None:
        async with self._db_session_factory() as session:
            result = await session.execute(select(AnalysisRun).where(AnalysisRun.status == "running"))
            runs = list(result.scalars().all())
            if not runs:
                return
            now = datetime.utcnow()
            for run in runs:
                run.status = "failed"
                run.error_message = "Analysis worker restarted before completion"
                run.completed_at = now
            await session.commit()

    async def _reconcile_processes(self) -> None:
        completed: list[tuple[str, int]] = []
        for run_id, entry in list(self._running.items()):
            return_code = entry.process.poll()
            if return_code is None:
                continue
            completed.append((run_id, return_code))
            try:
                entry.stdout_handle.close()
            except Exception:
                pass
            try:
                entry.stderr_handle.close()
            except Exception:
                pass
            self._running.pop(run_id, None)

        if not completed:
            return

        async with self._db_session_factory() as session:
            now = datetime.utcnow()
            for run_id, return_code in completed:
                result = await session.execute(select(AnalysisRun).where(AnalysisRun.id == run_id))
                run = result.scalar_one_or_none()
                if run is None:
                    continue
                if run.status == "running":
                    run.status = "failed"
                    run.error_message = f"Analysis subprocess exited before marking completion (exit={return_code})"
                    run.completed_at = now
            await session.commit()

    async def _launch_available_runs(self) -> None:
        running_by_class: Dict[str, int] = {}
        for entry in self._running.values():
            key = str(entry.resource_class or "cpu_heavy")
            running_by_class[key] = running_by_class.get(key, 0) + 1

        async with self._db_session_factory() as session:
            result = await session.execute(
                select(AnalysisRun)
                .where(AnalysisRun.status == "queued")
                .order_by(AnalysisRun.queued_at.asc())
                .limit(64)
            )
            queued_runs = list(result.scalars().all())
            if not queued_runs:
                return

            for run in queued_runs:
                resource_class = str(run.resource_class or "cpu_heavy")
                max_runs = _max_runs_for_class(resource_class)
                if running_by_class.get(resource_class, 0) >= max_runs:
                    continue
                manifest = run.artifact_manifest if isinstance(run.artifact_manifest, dict) else None
                if not manifest:
                    manifest = build_artifact_manifest_for_run(run)
                    run.artifact_manifest = manifest

                stdout_path = Path(str(manifest["stdout_log"]))
                stderr_path = Path(str(manifest["stderr_log"]))
                from paths import resolve_allowed_path

                stdout_file = resolve_allowed_path(str(stdout_path))
                stderr_file = resolve_allowed_path(str(stderr_path))
                stdout_file.parent.mkdir(parents=True, exist_ok=True)
                stderr_file.parent.mkdir(parents=True, exist_ok=True)

                run.status = "running"
                run.started_at = datetime.utcnow()
                run.error_message = None
                await session.flush()

                stdout_handle = open(stdout_file, "ab")
                stderr_handle = open(stderr_file, "ab")
                api_root = Path(__file__).resolve().parents[1]
                env = dict(os.environ)
                cpu_threads = str(_threads_for_class(resource_class))
                env["BMS_ANALYSIS_CPUS"] = cpu_threads
                env["OMP_NUM_THREADS"] = cpu_threads
                env["OPENBLAS_NUM_THREADS"] = cpu_threads
                env["MKL_NUM_THREADS"] = cpu_threads
                env["NUMEXPR_NUM_THREADS"] = cpu_threads
                try:
                    process = subprocess.Popen(
                        [sys.executable, "-m", "services.analysis_subprocess", run.id],
                        cwd=str(api_root),
                        stdout=stdout_handle,
                        stderr=stderr_handle,
                        env=env,
                    )
                except Exception as exc:
                    try:
                        stdout_handle.close()
                    except Exception:
                        pass
                    try:
                        stderr_handle.close()
                    except Exception:
                        pass
                    run.status = "failed"
                    run.error_message = f"Failed to launch analysis subprocess: {exc}"
                    run.completed_at = datetime.utcnow()
                    continue

                self._running[run.id] = _RunningProcess(
                    process=process,
                    stdout_handle=stdout_handle,
                    stderr_handle=stderr_handle,
                    resource_class=resource_class,
                )
                running_by_class[resource_class] = running_by_class.get(resource_class, 0) + 1

            await session.commit()

    async def _terminate_running_processes(self) -> None:
        for run_id, entry in list(self._running.items()):
            try:
                entry.process.terminate()
            except Exception:
                pass
            try:
                entry.stdout_handle.close()
            except Exception:
                pass
            try:
                entry.stderr_handle.close()
            except Exception:
                pass
            self._running.pop(run_id, None)
