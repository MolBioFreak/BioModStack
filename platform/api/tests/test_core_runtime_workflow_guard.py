from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import main as api_main
import runtime_policy
from routers import jobs
from schemas import JobCreate
from services import nextflow


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
        await jobs.resubmit_job("job-123", session=_ExplodingSession())

    assert exc_info.value.status_code == 409
    assert "core-runtime" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_resume_job_rejects_workflow_launches_in_core_runtime_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")

    with pytest.raises(HTTPException) as exc_info:
        await jobs.resume_job("job-123", session=_ExplodingSession())

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
    monkeypatch.setattr(api_main, "GPUOrchestrator", FakeGPUOrchestrator)
    monkeypatch.setattr(api_main, "AnalysisWorker", FakeAnalysisWorker)
    monkeypatch.setattr(api_main, "_orchestrator", None)
    monkeypatch.setattr(api_main, "_analysis_worker", None)

    async with api_main.lifespan(api_main.app):
        assert "db-init" in events
        assert "analysis-start" in events

    assert "orchestrator-init" not in events
    assert "analysis-stop" in events
