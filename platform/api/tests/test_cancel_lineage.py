from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import Base, ExecutionTarget, Job
import services.job_control as job_control
from services.job_control import _job_is_cancelable, _lineage_has_cancelable_jobs, _sort_jobs_for_cancellation, cancel_job_lineage


@dataclass
class DummyJob:
    id: str
    status: str
    queue_status: str
    created_at: datetime
    awaiting_input: bool = False
    nextflow_run_id: str | None = None
    completed_at: datetime | None = None


def test_lineage_is_cancelable_when_root_is_cancelled_but_child_is_running() -> None:
    base = datetime(2026, 3, 15, 9, 0, 0)
    root = DummyJob(
        id="root",
        status="cancelled",
        queue_status="cancelled",
        created_at=base,
        completed_at=base + timedelta(minutes=1),
    )
    child = DummyJob(
        id="child",
        status="running",
        queue_status="running",
        created_at=base + timedelta(seconds=5),
        nextflow_run_id="12345",
    )

    assert not _job_is_cancelable(root)
    assert _job_is_cancelable(child)
    assert _lineage_has_cancelable_jobs([root, child])


def test_terminal_completed_job_is_not_cancelable_without_live_process() -> None:
    job = DummyJob(
        id="done",
        status="completed",
        queue_status="completed",
        created_at=datetime(2026, 3, 15, 9, 0, 0),
        completed_at=datetime(2026, 3, 15, 9, 30, 0),
    )

    assert not _job_is_cancelable(job)
    assert not _lineage_has_cancelable_jobs([job])


def test_sort_jobs_for_cancellation_orders_descendants_before_parent() -> None:
    base = datetime(2026, 3, 15, 9, 0, 0)
    parent = DummyJob(
        id="parent",
        status="running",
        queue_status="running",
        created_at=base,
    )
    child = DummyJob(
        id="child",
        status="running",
        queue_status="running",
        created_at=base + timedelta(seconds=1),
    )
    grandchild = DummyJob(
        id="grandchild",
        status="running",
        queue_status="running",
        created_at=base + timedelta(seconds=2),
    )

    ordered = _sort_jobs_for_cancellation(
        [parent, child, grandchild],
        {"parent": 0, "child": 1, "grandchild": 2},
    )

    assert [job.id for job in ordered] == ["grandchild", "child", "parent"]


@pytest.mark.asyncio
async def test_repeat_cancellation_is_idempotent_and_clears_stale_runtime_state(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cancel.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    cancelled_at = datetime(2026, 7, 13, 12, 0, 0)
    async with factory() as session:
        session.add(
            Job(
                id="already-cancelled",
                name="already-cancelled",
                model_id="boltz2",
                mode="predict",
                params={},
                status="cancelled",
                queue_status="queued",
                created_at=cancelled_at,
                completed_at=cancelled_at,
                paused=True,
                assigned_gpu=2,
                awaiting_input=True,
                awaiting_stage="post_fampnn",
                awaiting_payload={"stage": "post_fampnn"},
                retry_count=2,
                current_stage="run_boltz",
                stage_progress="1/3",
            )
        )
        await session.commit()

        root, lineage = await cancel_job_lineage("already-cancelled", session)
        assert root.id == "already-cancelled"
        assert [job.id for job in lineage] == ["already-cancelled"]

        job = await session.get(Job, "already-cancelled")
        assert job is not None
        assert job.status == "cancelled"
        assert job.queue_status == "cancelled"
        assert job.paused is False
        assert job.assigned_gpu is None
        assert job.awaiting_input is False
        assert job.awaiting_stage is None
        assert job.awaiting_payload == {}
        assert job.retry_count == 0
        assert job.current_stage is None
        assert job.stage_progress is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_stop_remains_cancelling_with_requested_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cancel-pending.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(job_control, "cancel_nextflow_job", lambda _run_id: _async_bool(False))

    async with factory() as session:
        session.add(
            Job(
                id="pending-cancel",
                name="pending-cancel",
                model_id="nanopore",
                mode="plasmid_qc",
                params={},
                status="running",
                queue_status="running",
                created_at=datetime.utcnow(),
                nextflow_run_id="biomodstack-development-job-pending-cancel-attempt-1.service",
                assigned_gpu=0,
            )
        )
        await session.commit()

        with pytest.raises(HTTPException) as raised:
            await cancel_job_lineage("pending-cancel", session)
        assert raised.value.status_code == 409
        job = await session.get(Job, "pending-cancel")
        assert job is not None
        assert job.status == "running"
        assert job.queue_status == "cancelling"
        assert job.assigned_gpu == 0
        assert job.completed_at is None
        assert job.params["cancellation_receipt"]["state"] == "requested"

    await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_cancel_requires_owned_unit_stop_and_empty_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cancel-complete.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(job_control, "cancel_nextflow_job", lambda _run_id: _async_bool(True))

    async with factory() as session:
        session.add(
            Job(
                id="completed-cancel",
                name="completed-cancel",
                model_id="nanopore",
                mode="plasmid_qc",
                params={},
                status="running",
                queue_status="running",
                created_at=datetime.utcnow(),
                nextflow_run_id="biomodstack-development-job-completed-cancel-attempt-1.service",
                assigned_gpu=0,
            )
        )
        await session.commit()
        await cancel_job_lineage("completed-cancel", session)
        job = await session.get(Job, "completed-cancel")
        assert job is not None
        assert job.status == "cancelled"
        assert job.queue_status == "cancelled"
        assert job.assigned_gpu is None
        assert job.completed_at is not None
        assert job.params["cancellation_receipt"]["state"] == "completed"

    await engine.dispose()


@pytest.mark.asyncio
async def test_remote_terminal_cancel_releases_exclusive_target_lease_idempotently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'remote-cancel.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(job_control, "cancel_nextflow_job", lambda _run_id: _async_bool(True))

    async with factory() as session:
        session.add(
            ExecutionTarget(
                id="vast:123",
                provider="vast",
                provider_instance_id="123",
                name="vast",
                state="ready",
                active=True,
                leased_job_id="remote-cancel",
                lease_acquired_at=datetime.utcnow(),
            )
        )
        session.add(
            Job(
                id="remote-cancel",
                name="remote-cancel",
                model_id="boltz2",
                mode="predict",
                params={},
                status="running",
                queue_status="running",
                created_at=datetime.utcnow(),
                nextflow_run_id="remote:remote-cancel:11111111-1111-4111-8111-111111111111",
                remote_attempt_id="11111111-1111-4111-8111-111111111111",
                execution_target_id="vast:123",
                assigned_gpu=0,
            )
        )
        await session.commit()

        await cancel_job_lineage("remote-cancel", session)
        await cancel_job_lineage("remote-cancel", session)
        target = await session.get(ExecutionTarget, "vast:123")
        assert target is not None
        assert target.leased_job_id is None
        assert target.lease_acquired_at is None

    await engine.dispose()


async def _async_bool(value: bool) -> bool:
    return value
