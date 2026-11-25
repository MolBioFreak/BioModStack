"""BioModStack standalone background worker entry point (R31).

Owns the background loops separated from the HTTP API process:
1. ExternalImportWorker, BoltzApiJobWorker, FrustraMPNNStatisticsWorker
2. Vast inventory refresh and remote telemetry sampling
3. GPUOrchestrator (with workflow-adapter readiness wait) and MdReconcilerWorker
4. AnalysisWorker
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import signal
from pathlib import Path
from typing import Optional

from biomodstack_runtime_profile import install_feature_enabled
from database import async_session, init_db
from paths import get_data_root
from readiness import http_readiness
from runtime_policy import workflow_launch_block_detail, workflow_launches_allowed
from services.analysis_worker import AnalysisWorker
from services.boltz_api_jobs import BoltzApiJobWorker
from services.external_imports.worker import ExternalImportWorker
from services.frustrampnn.statistics_jobs import FrustraMPNNStatisticsWorker
from services.gpu_orchestrator import GPUOrchestrator
from services.gpu_stats import get_gpu_stats
from services.md.reconcile import MdReconcilerWorker
from services.remote_execution.targets import (
    invalidate_vast_inventory,
    run_vast_inventory_refresh,
)
from services.remote_execution.telemetry import remote_telemetry
from services.restriction_catalog import catalog_authority
from services.workflow_adapter import workflow_adapter_base_url

logging.basicConfig(level=logging.INFO)
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

_orchestrator: GPUOrchestrator | None = None
_analysis_worker: AnalysisWorker | None = None
_external_import_worker: ExternalImportWorker | None = None
_boltz_api_job_worker: BoltzApiJobWorker | None = None
_md_reconciler: MdReconcilerWorker | None = None
_frustrampnn_statistics_worker: FrustraMPNNStatisticsWorker | None = None
_adapter_wait_task: asyncio.Task[None] | None = None


async def _orchestrator_launch_job(job_id, model_id, mode, params, output_dir):
    """Delegate a scheduler-owned job whose durable state is already running."""
    from services.nextflow import launch_nextflow_job_detached

    logger.info("[ORCHESTRATOR] Launching job %s on GPU %s", job_id, params.get("gpu_id", 0))
    launch_nextflow_job_detached(
        job_id=job_id,
        model_id=model_id,
        mode=mode,
        params=params,
        output_dir=output_dir,
        allow_running_job=True,
    )


async def _start_workflow_orchestrator() -> None:
    global _orchestrator
    if _orchestrator is not None:
        return
    _orchestrator = GPUOrchestrator(
        db_session_factory=async_session,
        get_gpu_stats_fn=get_gpu_stats,
        launch_nextflow_job_fn=_orchestrator_launch_job,
        poll_interval=3.0,
    )
    await _orchestrator.start()
    logger.info("[WORKER] GPU Orchestrator started")


async def _wait_and_start_workflow_orchestrator(
    *,
    poll_interval_seconds: float = 0.5,
) -> None:
    """Wait until the workflow adapter answers, then start the scheduler once."""
    base_url = workflow_adapter_base_url()
    if not base_url:
        await _start_workflow_orchestrator()
        return
    health_url = f"{base_url}/api/workflow-adapter/health"
    while True:
        ready, _ = await http_readiness(health_url)
        if ready:
            await _start_workflow_orchestrator()
            return
        await asyncio.sleep(max(0.05, float(poll_interval_seconds)))


async def wait_for_workflow_adapter_admission(
    *,
    timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 0.25,
) -> None:
    """Block scheduler admission until the configured adapter answers health probes."""
    base_url = workflow_adapter_base_url()
    if not base_url:
        return
    health_url = f"{base_url}/api/workflow-adapter/health"
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, float(timeout_seconds))
    last_status = "unavailable"
    while True:
        ready, last_status = await http_readiness(health_url)
        if ready:
            return
        if loop.time() >= deadline:
            raise RuntimeError(
                f"Configured workflow adapter is not ready for scheduler admission: {last_status}"
            )
        await asyncio.sleep(max(0.0, float(poll_interval_seconds)))


def _worker_lock_path() -> Path:
    return get_data_root() / ".biomodstack-worker.lock"


class WorkerRuntime:
    """Lifecycle owner for the standalone BioModStack background worker."""

    def __init__(self, *, acquire_lock: bool = True) -> None:
        self._acquire_lock = acquire_lock
        self._lock_fd: Optional[int] = None
        self._vast_inventory_stop: asyncio.Event | None = None
        self._vast_inventory_task: asyncio.Task[None] | None = None
        self._remote_telemetry_stop: asyncio.Event | None = None
        self._remote_telemetry_task: asyncio.Task[None] | None = None
        self._started = False

    def _claim_single_owner_lock(self) -> None:
        if not self._acquire_lock:
            return
        lock_path = _worker_lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise RuntimeError(
                f"Another biomodstack-worker instance already holds {lock_path}"
            ) from exc
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        self._lock_fd = fd

    def _release_single_owner_lock(self) -> None:
        fd = self._lock_fd
        self._lock_fd = None
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass

    async def start(self) -> None:
        global _orchestrator
        global _analysis_worker
        global _external_import_worker
        global _boltz_api_job_worker
        global _md_reconciler
        global _frustrampnn_statistics_worker
        global _adapter_wait_task

        if self._started:
            return

        self._claim_single_owner_lock()
        try:
            catalog_authority.require()
            await init_db()

            protein_compute_enabled = install_feature_enabled("protein_compute")

            # Loop group 1: external-import, Boltz API, and FrustraMPNN statistics.
            _external_import_worker = ExternalImportWorker(async_session, poll_interval=2.0)
            await _external_import_worker.start()
            logger.info("[WORKER] External result import worker started")

            if protein_compute_enabled:
                _boltz_api_job_worker = BoltzApiJobWorker(async_session)
                await _boltz_api_job_worker.start()
                logger.info("[WORKER] Boltz API submission worker started")

                _frustrampnn_statistics_worker = FrustraMPNNStatisticsWorker(async_session)
                await _frustrampnn_statistics_worker.start()
                logger.info("[WORKER] FrustraMPNN statistics worker started")
            else:
                _boltz_api_job_worker = None
                _frustrampnn_statistics_worker = None

            # Loop group 2: Vast inventory refresh and remote telemetry sampling.
            async with async_session() as inventory_session:
                await invalidate_vast_inventory(inventory_session)
            self._vast_inventory_stop = asyncio.Event()
            self._vast_inventory_task = asyncio.create_task(
                run_vast_inventory_refresh(async_session, self._vast_inventory_stop),
                name="vast-inventory-refresh",
            )
            self._remote_telemetry_stop = asyncio.Event()
            self._remote_telemetry_task = asyncio.create_task(
                remote_telemetry.run(async_session, self._remote_telemetry_stop),
                name="remote-telemetry",
            )

            # Loop group 3: GPU scheduler and MD reconciler together.
            _orchestrator = None
            _adapter_wait_task = None
            if workflow_launches_allowed():
                _md_reconciler = MdReconcilerWorker(async_session, poll_interval=5.0)
                await _md_reconciler.start()
                logger.info("[WORKER] MD lifecycle reconciler started")

                adapter_url = workflow_adapter_base_url()
                if not adapter_url:
                    await _start_workflow_orchestrator()
                else:
                    adapter_ready, adapter_status = await http_readiness(
                        f"{adapter_url}/api/workflow-adapter/health"
                    )
                    if adapter_ready:
                        await _start_workflow_orchestrator()
                    else:
                        logger.warning(
                            "[WORKER] Workflow adapter not ready (%s); deferring GPU Orchestrator until adapter answers",
                            adapter_status,
                        )
                        _adapter_wait_task = asyncio.create_task(
                            _wait_and_start_workflow_orchestrator(),
                            name="workflow-adapter-admission-wait",
                        )
            else:
                _md_reconciler = None
                logger.warning("[WORKER] %s", workflow_launch_block_detail("start the GPU workflow scheduler"))

            # Loop group 4: Analysis worker.
            _analysis_worker = AnalysisWorker(
                db_session_factory=async_session,
                poll_interval=2.0,
            )
            await _analysis_worker.start()
            logger.info("[WORKER] Analysis worker started")
            self._started = True
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        global _orchestrator
        global _analysis_worker
        global _external_import_worker
        global _boltz_api_job_worker
        global _md_reconciler
        global _frustrampnn_statistics_worker
        global _adapter_wait_task

        try:
            if _adapter_wait_task is not None:
                _adapter_wait_task.cancel()
                await asyncio.gather(_adapter_wait_task, return_exceptions=True)
                _adapter_wait_task = None
            if self._remote_telemetry_stop is not None:
                self._remote_telemetry_stop.set()
            if self._vast_inventory_stop is not None:
                self._vast_inventory_stop.set()
            poll_tasks = [
                task
                for task in (self._remote_telemetry_task, self._vast_inventory_task)
                if task is not None
            ]
            for task in poll_tasks:
                task.cancel()
            if poll_tasks:
                await asyncio.gather(*poll_tasks, return_exceptions=True)
            self._remote_telemetry_task = None
            self._vast_inventory_task = None
            self._remote_telemetry_stop = None
            self._vast_inventory_stop = None

            if _orchestrator is not None:
                await _orchestrator.stop()
                _orchestrator = None
                logger.info("[WORKER] GPU Orchestrator stopped")
            if _md_reconciler is not None:
                await _md_reconciler.stop()
                _md_reconciler = None
                logger.info("[WORKER] MD lifecycle reconciler stopped")
            if _analysis_worker is not None:
                await _analysis_worker.stop()
                _analysis_worker = None
                logger.info("[WORKER] Analysis worker stopped")
            if _frustrampnn_statistics_worker is not None:
                await _frustrampnn_statistics_worker.stop()
                _frustrampnn_statistics_worker = None
                logger.info("[WORKER] FrustraMPNN statistics worker stopped")
            if _boltz_api_job_worker is not None:
                await _boltz_api_job_worker.stop()
                _boltz_api_job_worker = None
                logger.info("[WORKER] Boltz API submission worker stopped")
            if _external_import_worker is not None:
                await _external_import_worker.stop()
                _external_import_worker = None
                logger.info("[WORKER] External result import worker stopped")
        finally:
            self._started = False
            self._release_single_owner_lock()


async def run_worker() -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop() -> None:
        if not stop_event.is_set():
            logger.info("[WORKER] Shutdown signal received")
            stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, RuntimeError):
            pass

    runtime = WorkerRuntime()
    await runtime.start()
    logger.info("[WORKER] BioModStack background worker ready")
    try:
        await stop_event.wait()
    finally:
        await runtime.stop()


def main() -> int:
    asyncio.run(run_worker())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
