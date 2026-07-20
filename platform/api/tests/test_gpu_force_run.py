from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers import gpu
from services import job_control


def _gpu(index: int, memory_used_mb: int) -> SimpleNamespace:
    return SimpleNamespace(index=index, memory_used_mb=memory_used_mb)


def test_force_run_auto_gpu_selection_does_not_hardcode_gpu_one_as_display(monkeypatch) -> None:
    captured: dict[str, int] = {}

    async def _fake_force_launch_job_service(*, job_id: str, gpu_id: int, allowed_queue_statuses: list[str]):
        captured["gpu_id"] = gpu_id
        captured["allowed_count"] = len(allowed_queue_statuses)
        return SimpleNamespace(name=f"job-{job_id}")

    monkeypatch.setattr(
        gpu,
        "get_gpu_stats",
        lambda: [_gpu(0, 4000), _gpu(1, 100), _gpu(2, 2500)],
    )
    monkeypatch.setattr(gpu, "read_scheduler_config", lambda: {"global": {}, "overrides": {}})
    monkeypatch.setattr(gpu, "force_launch_job_service", _fake_force_launch_job_service)

    response = asyncio.run(gpu.force_run_job("queued-1", gpu.ForceRunRequest()))

    assert captured["gpu_id"] == 1
    assert response["gpu_id"] == 1


def test_force_run_auto_gpu_selection_respects_scheduler_disabled_overrides(monkeypatch) -> None:
    captured: dict[str, int] = {}

    async def _fake_force_launch_job_service(*, job_id: str, gpu_id: int, allowed_queue_statuses: list[str]):
        captured["gpu_id"] = gpu_id
        return SimpleNamespace(name=f"job-{job_id}")

    monkeypatch.setattr(
        gpu,
        "get_gpu_stats",
        lambda: [_gpu(0, 4000), _gpu(2, 100)],
    )
    monkeypatch.setattr(
        gpu,
        "read_scheduler_config",
        lambda: {"global": {}, "overrides": {"2": {"disabled": True}}},
    )
    monkeypatch.setattr(gpu, "force_launch_job_service", _fake_force_launch_job_service)

    response = asyncio.run(gpu.force_run_job("queued-2", gpu.ForceRunRequest()))

    assert captured["gpu_id"] == 0
    assert response["gpu_id"] == 0


def test_force_run_auto_gpu_selection_respects_explicit_force_run_exclusions(monkeypatch) -> None:
    captured: dict[str, int] = {}

    async def _fake_force_launch_job_service(*, job_id: str, gpu_id: int, allowed_queue_statuses: list[str]):
        captured["gpu_id"] = gpu_id
        return SimpleNamespace(name=f"job-{job_id}")

    monkeypatch.setattr(
        gpu,
        "get_gpu_stats",
        lambda: [_gpu(0, 4000), _gpu(1, 100), _gpu(2, 2500)],
    )
    monkeypatch.setattr(
        gpu,
        "read_scheduler_config",
        lambda: {"global": {"force_run_excluded_gpu_ids": [1]}, "overrides": {}},
    )
    monkeypatch.setattr(gpu, "force_launch_job_service", _fake_force_launch_job_service)

    response = asyncio.run(gpu.force_run_job("queued-3", gpu.ForceRunRequest()))

    assert captured["gpu_id"] == 2
    assert response["gpu_id"] == 2


@pytest.mark.asyncio
async def test_force_run_is_pinned_and_requeued_for_orchestrator_ownership() -> None:
    job = SimpleNamespace(
        id="queued-4",
        name="queued-4",
        model_id="boltz2",
        mode="predict",
        params={"sequence": "AAAA"},
        child_output_dir=None,
        output_dir="/tmp/queued-4",
        queue_status="queued",
        status="queued",
        pinned_gpu=None,
        assigned_gpu=None,
        started_at=None,
        paused=False,
    )

    class _Result:
        def scalar_one_or_none(self):
            return job

    class _Session:
        commits = 0

        async def execute(self, _statement):
            return _Result()

        async def commit(self):
            self.commits += 1

    session = _Session()

    queued = await job_control._force_launch_with_session(
        session,
        job_id=job.id,
        gpu_id=2,
        allowed_queue_statuses=["queued"],
    )

    assert queued is job
    assert (job.status, job.queue_status) == ("queued", "queued")
    assert job.pinned_gpu == 2
    assert job.assigned_gpu is None
    assert job.started_at is None
    assert job.params["gpu_id"] == 2
    assert job.params["operator_force_run"] is True
    assert session.commits == 1
