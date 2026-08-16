from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from fastapi import HTTPException

from routers import queue
from routers.queue import _resolve_display_gpu_ids


class _ScalarResult:
    def __init__(self, job):
        self.job = job

    def scalar_one_or_none(self):
        return self.job


class _Session:
    def __init__(self, job):
        self.job = job
        self.commits = 0

    async def execute(self, _query):
        return _ScalarResult(self.job)

    async def commit(self):
        self.commits += 1


def _job(*, params=None, pinned_gpu=None, assigned_gpu=None, mode="design", current_stage="run_boltz"):
    return SimpleNamespace(
        params=params or {},
        pinned_gpu=pinned_gpu,
        assigned_gpu=assigned_gpu,
        mode=mode,
        current_stage=current_stage,
    )


def test_resolve_display_gpu_ids_prefers_boltz_cp_multi_gpu_launch_ids() -> None:
    job = _job(
        params={
            "bcp_gpu_ids": "0,1,2,3",
            "pinned_gpus": [0, 1, 2, 3],
        },
        pinned_gpu=None,
        assigned_gpu=0,
    )

    assert _resolve_display_gpu_ids(job) == [0, 1, 2, 3]


def test_resolve_display_gpu_ids_falls_back_to_explicit_pinned_gpu_list() -> None:
    job = _job(
        params={
            "pinned_gpus": [2, "3", 3, "bad"],
        },
        pinned_gpu=None,
        assigned_gpu=None,
    )

    assert _resolve_display_gpu_ids(job) == [2, 3]


def test_resolve_display_gpu_ids_uses_anchor_gpu_only_when_no_multi_gpu_config_exists() -> None:
    job = _job(params={}, pinned_gpu=2, assigned_gpu=2)

    assert _resolve_display_gpu_ids(job) == [2]


def test_resolve_display_gpu_ids_skips_non_gpu_tail_stage_anchor_assignments() -> None:
    job = _job(
        params={},
        pinned_gpu=None,
        assigned_gpu=2,
        mode="maturation_child",
        current_stage="filterbymaturation",
    )

    assert _resolve_display_gpu_ids(job) is None


def test_valid_queue_gpu_indices_uses_local_hardware_limits_first(monkeypatch) -> None:
    monkeypatch.setattr(queue, "HARDWARE_LIMITS", {2: {}, 0: {}})
    monkeypatch.setattr(queue, "workflow_adapter_enabled", lambda: True)
    monkeypatch.setattr(
        queue,
        "request_via_workflow_adapter",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("adapter should not be called")),
    )

    assert queue._valid_queue_gpu_indices() == [0, 2]


def test_valid_queue_gpu_indices_falls_back_to_workflow_adapter_when_local_empty(monkeypatch) -> None:
    monkeypatch.setattr(queue, "HARDWARE_LIMITS", {})
    monkeypatch.setattr(queue, "workflow_adapter_enabled", lambda: True)
    monkeypatch.setattr(
        queue,
        "request_via_workflow_adapter",
        lambda method, path: {
            "gpus": [
                {"index": 3, "name": "RTX 3090"},
                {"index": "0", "name": "RTX 5090"},
                {"index": 3, "name": "duplicate"},
                {"index": "bad"},
            ]
        },
    )

    assert queue._valid_queue_gpu_indices() == [0, 3]


def test_validate_queue_gpu_index_reports_adapter_valid_indices(monkeypatch) -> None:
    monkeypatch.setattr(queue, "HARDWARE_LIMITS", {})
    monkeypatch.setattr(queue, "workflow_adapter_enabled", lambda: True)
    monkeypatch.setattr(queue, "request_via_workflow_adapter", lambda method, path: {"gpus": [{"index": 0}, {"index": 1}]})

    queue._validate_queue_gpu_index(1)

    try:
        queue._validate_queue_gpu_index(4)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "Invalid GPU index (valid: 0,1)"
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected HTTPException")


@pytest.mark.asyncio
async def test_retry_restores_full_queued_state() -> None:
    job = SimpleNamespace(
        id="retry-1", name="retry", queue_status="failed", status="failed",
        retry_count=0, max_retries=2, paused=True, error_message="boom",
        assigned_gpu=0, started_at=datetime.utcnow(), completed_at=datetime.utcnow(),
        current_stage="failed stage", stage_progress="1/2",
    )
    session = _Session(job)

    response = await queue.retry_job(job.id, session)

    assert response["success"] is True
    assert session.commits == 1
    assert job.retry_count == 1
    assert job.status == "queued"
    assert job.queue_status == "queued"
    assert job.paused is False
    assert job.error_message is None
    assert job.assigned_gpu is None
    assert job.started_at is None
    assert job.completed_at is None
    assert job.current_stage is None
    assert job.stage_progress is None
