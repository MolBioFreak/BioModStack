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


def test_runtime_policy_imports_without_services_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BMS_CORE_RUNTIME_MODE", raising=False)
    monkeypatch.delenv("BMS_WORKFLOW_ADAPTER_URL", raising=False)

    for module_name in (
        "runtime_policy",
        "services",
        "services.nextflow",
        "services.workflow_adapter",
    ):
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    imported = importlib.import_module("runtime_policy")

    assert imported.workflow_launches_allowed() is True


def test_invalid_core_runtime_mode_value_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "banana")

    assert runtime_policy.core_runtime_mode_enabled() is True
    assert runtime_policy.workflow_launches_allowed() is False


def test_blank_core_runtime_mode_value_disables_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "   ")

    assert runtime_policy.core_runtime_mode_enabled() is False
    assert runtime_policy.workflow_launches_allowed() is True


def test_core_runtime_mode_with_adapter_allows_workflow_launches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://host.docker.internal:8001")

    assert runtime_policy.core_runtime_mode_enabled() is True
    assert runtime_policy.workflow_launches_allowed() is True


def test_guard_message_mentions_adapter_requirement_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")
    monkeypatch.delenv("BMS_WORKFLOW_ADAPTER_URL", raising=False)

    detail = runtime_policy.workflow_launch_block_detail("launch workflows")

    assert "adapter" in detail.lower()
    assert "host-native" in detail.lower()


@pytest.mark.asyncio
async def test_deployment_fence_rejects_mutation_before_route_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @contextmanager
    def blocked_admission():
        raise runtime_policy.WorkflowAdmissionBlocked("deployment in progress")
        yield

    async def route_should_not_run(_request):
        raise AssertionError("mutation route executed while deployment fence was held")

    monkeypatch.setattr(api_main, "workflow_mutation_admission", blocked_admission)
    response = await api_main.deployment_admission_fence(
        Request({"type": "http", "method": "POST", "scheme": "http", "path": "/api/jobs"}),
        route_should_not_run,
    )

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_detached_mutation_lease_blocks_cutover_until_coroutine_finishes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "deployment-admission.lock"
    monkeypatch.setenv(runtime_policy.DEPLOYMENT_ADMISSION_LOCK_ENV, str(lock_path))
    release = asyncio.Event()

    async def detached_mutation() -> None:
        await release.wait()

    lease = runtime_policy.acquire_workflow_mutation_lease()
    task = asyncio.create_task(
        runtime_policy.run_with_workflow_mutation_lease(lease, detached_mutation())
    )
    await asyncio.sleep(0)

    with lock_path.open("a+") as contender:
        with pytest.raises(BlockingIOError):
            fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    release.set()
    await task
    with lock_path.open("a+") as contender:
        fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(contender.fileno(), fcntl.LOCK_UN)


@pytest.mark.asyncio
async def test_analysis_autorun_holds_mutation_lease_until_detached_work_finishes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "deployment-admission.lock"
    monkeypatch.setenv(runtime_policy.DEPLOYMENT_ADMISSION_LOCK_ENV, str(lock_path))
    analysis_autorun._RECENT_AUTORUN_REQUESTS.clear()
    release = asyncio.Event()
    completed = asyncio.Event()

    async def fake_ensure(job_id: str) -> None:
        assert job_id == "job-1"
        await release.wait()
        completed.set()

    monkeypatch.setattr(analysis_autorun, "ensure_viewer_minimum_analyses_for_job", fake_ensure)

    assert analysis_autorun.schedule_viewer_minimum_analyses_for_job("job-1") is True
    await asyncio.sleep(0)
    with lock_path.open("a+") as contender:
        with pytest.raises(BlockingIOError):
            fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    release.set()
    await asyncio.wait_for(completed.wait(), timeout=2)
    await asyncio.sleep(0)
    with lock_path.open("a+") as contender:
        fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(contender.fileno(), fcntl.LOCK_UN)


def test_cdr_annotation_transfers_a_mutation_lease_to_detached_work() -> None:
    source = inspect.getsource(jobs.annotate_cdr_regions)

    assert "acquire_workflow_mutation_lease()" in source
    assert "run_with_workflow_mutation_lease(" in source


@pytest.mark.asyncio
async def test_create_job_rejects_workflow_launches_in_core_runtime_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")
    monkeypatch.setattr(jobs, "get_registry", lambda: _ExplodingRegistry())

    with pytest.raises(HTTPException) as exc_info:
        await jobs.create_job(
            JobCreate(name="guarded-job", model_id="boltz2", mode="predict", params={}),
            BackgroundTasks(),
            object(),
        )

    assert exc_info.value.status_code == 409
    assert "host-native" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_resubmit_job_rejects_workflow_launches_in_core_runtime_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")

    with pytest.raises(HTTPException) as exc_info:
        await jobs.resubmit_job(
            "job-123",
            request=Request({"type": "http", "method": "POST", "scheme": "http", "path": "/"}),
            response=Response(),
            session=_ExplodingSession(),
        )

    assert exc_info.value.status_code == 409
    assert "core-runtime" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_resume_job_rejects_workflow_launches_in_core_runtime_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")

    with pytest.raises(HTTPException) as exc_info:
        await jobs.resume_job(
            "job-123",
            request_context=Request({"type": "http", "method": "POST", "scheme": "http", "path": "/"}),
            response=Response(),
            session=_ExplodingSession(),
        )

    assert exc_info.value.status_code == 409
    assert "resume" in str(exc_info.value.detail).lower()


def test_launch_nextflow_job_detached_rejects_core_runtime_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")
    monkeypatch.setattr(
        asyncio,
        "create_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("asyncio.create_task should not be reached in guarded core-runtime mode")
        ),
    )

    with pytest.raises(RuntimeError) as exc_info:
        nextflow.launch_nextflow_job_detached(
            job_id="job-123",
            model_id="boltz2",
            mode="predict",
            params={},
            output_dir="/tmp/guarded-job",
        )

    assert "core-runtime" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_lifespan_skips_gpu_orchestrator_start_in_core_runtime_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")

    events: list[str] = []

    async def fake_init_db() -> None:
        events.append("db-init")

    async def fake_init_molbio_db() -> None:
        events.append("molbio-db-init")

    class FakeGPUOrchestrator:
        def __init__(self, *args, **kwargs) -> None:
            events.append("orchestrator-init")

        async def start(self) -> None:
            raise AssertionError("GPU orchestrator should not start in guarded core-runtime mode")

        async def stop(self) -> None:
            events.append("orchestrator-stop")

    class FakeAnalysisWorker:
        def __init__(self, *args, **kwargs) -> None:
            events.append("analysis-init")

        async def start(self) -> None:
            events.append("analysis-start")

        async def stop(self) -> None:
            events.append("analysis-stop")

    monkeypatch.setattr(api_main, "init_db", fake_init_db)
    monkeypatch.setattr(api_main, "init_molbio_db", fake_init_molbio_db)
    monkeypatch.setattr(api_main, "GPUOrchestrator", FakeGPUOrchestrator)
    monkeypatch.setattr(api_main, "AnalysisWorker", FakeAnalysisWorker)
    monkeypatch.setattr(api_main, "_orchestrator", None)
    monkeypatch.setattr(api_main, "_analysis_worker", None)

    async with api_main.lifespan(api_main.app):
        assert "db-init" in events
        assert "molbio-db-init" in events
        assert "analysis-start" in events

    assert "orchestrator-init" not in events
    assert "analysis-stop" in events
