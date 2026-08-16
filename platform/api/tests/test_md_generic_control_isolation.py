from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request
from starlette.responses import Response

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import Base, Job
from routers.gpu import ForceRunRequest, force_run_job
from routers.jobs import (
    delete_job_permanently,
    mark_children_aggregated,
    open_stage_gate,
    report_stage_complete,
    report_stage_start,
    resubmit_job,
    resume_job,
)
from routers.queue import (
    ForceLaunchRequest,
    PinGPURequest,
    PriorityRequest,
    cancel_all_queued,
    force_launch_job,
    get_queue_stats,
    list_cancelled_jobs,
    list_queue,
    pin_job_to_gpu,
    release_job_gpu,
    set_job_priority,
)
from services.md.state import create_md_run


def _request() -> dict:
    return {
        "schema": "bms.md.job.v2",
        "chemistry": {
            "profile_id": "amber_ff19sb_opc_protein_v1",
            "profile_sha256": "a" * 64,
            "assurance": "curated_profile",
        },
    }


@pytest_asyncio.fixture
async def session(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'generic-isolation.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as value:
        yield value
    await engine.dispose()


async def _assert_md_guard(call) -> None:
    with pytest.raises(HTTPException) as rejected:
        await call()
    assert rejected.value.status_code == 409
    assert rejected.value.detail["code"] == "MD_LIFECYCLE_CONTROL_REQUIRED"


@pytest.mark.asyncio
async def test_every_targeted_generic_mutation_rejects_md_owner(session) -> None:
    parent = Job(
        id="md-isolated", name="MD", status="paused", queue_status="paused", paused=True,
        model_id="molecular_dynamics", mode="molecular_dynamics", params={}, vram_estimate_mb=100,
    )
    child = Job(
        id="md-isolated-child", name="replica", status="paused", queue_status="paused", paused=True,
        model_id="molecular_dynamics", mode="replica", params={}, parent_job_id=parent.id,
        child_stage="md_replica", vram_estimate_mb=100,
    )
    session.add_all([parent, child]); await session.flush()
    await create_md_run(session, job=parent, normalized_request=_request())
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})

    calls = [
        lambda: release_job_gpu(parent.id, session),
        lambda: pin_job_to_gpu(parent.id, PinGPURequest(gpu_id=None), session),
        lambda: set_job_priority(parent.id, PriorityRequest(priority=1), session),
        lambda: force_launch_job(parent.id, ForceLaunchRequest(gpu_id=0), session),
        lambda: delete_job_permanently(parent.id, session),
        lambda: resubmit_job(parent.id, request, Response(), session),
        lambda: resume_job(parent.id, request, Response(), session=session),
        lambda: force_run_job(parent.id, ForceRunRequest(gpu_id=0), session),
        lambda: open_stage_gate(parent.id, "generic-review", None, session),
        lambda: report_stage_complete(parent.id, request, "generic-stage", [], session),
        lambda: report_stage_start(parent.id, request, "generic-stage", session),
        lambda: mark_children_aggregated(parent.id, None, None, session),
    ]
    for call in calls:
        await _assert_md_guard(call)

    for child_call in (
        lambda: release_job_gpu(child.id, session),
        lambda: delete_job_permanently(child.id, session),
    ):
        await _assert_md_guard(child_call)


@pytest.mark.asyncio
async def test_clear_all_excludes_md_parent_and_descendant(session) -> None:
    parent = Job(
        id="md-clear", name="MD", status="queued", queue_status="queued", paused=False,
        model_id="molecular_dynamics", mode="molecular_dynamics", params={},
    )
    child = Job(
        id="md-clear-child", name="replica", status="queued", queue_status="queued", paused=False,
        model_id="molecular_dynamics", mode="replica", params={}, parent_job_id=parent.id,
        child_stage="md_replica",
    )
    ordinary = Job(
        id="ordinary-clear", name="ordinary", status="queued", queue_status="queued", paused=False,
        model_id="boltz", mode="predict", params={},
    )
    session.add_all([parent, child, ordinary]); await session.flush()
    await create_md_run(session, job=parent, normalized_request=_request())

    receipt = await cancel_all_queued(session)
    assert receipt["cancelled_count"] == 1
    assert parent.status == "queued" and child.status == "queued"
    assert ordinary.status == "cancelled"


@pytest.mark.asyncio
async def test_global_queue_shows_md_without_repairing_md_projection(session) -> None:
    parent = Job(
        id="md-stale-projection", name="MD", status="completed", queue_status="running",
        paused=False, assigned_gpu=0, model_id="molecular_dynamics", mode="molecular_dynamics",
        params={}, vram_estimate_mb=100,
    )
    session.add(parent); await session.flush()
    await create_md_run(session, job=parent, normalized_request=_request())

    visible = await list_queue(session=session)

    assert [job.id for job in visible] == [parent.id]
    assert parent.queue_status == "running"
    assert parent.assigned_gpu == 0


@pytest.mark.asyncio
async def test_global_queue_stats_include_md_owned_jobs(session) -> None:
    parent = Job(
        id="md-global-count", name="MD", status="queued", queue_status="queued",
        paused=False, model_id="molecular_dynamics", mode="molecular_dynamics",
        params={}, vram_estimate_mb=100,
    )
    ordinary = Job(
        id="ordinary-global-count", name="ordinary", status="running", queue_status="running",
        paused=False, model_id="boltz", mode="predict", params={}, vram_estimate_mb=100,
    )
    session.add_all([parent, ordinary]); await session.flush()
    await create_md_run(session, job=parent, normalized_request=_request())

    stats = await get_queue_stats(session=session)

    assert stats.queued == 1
    assert stats.running == 1
    assert stats.total == 2


@pytest.mark.asyncio
async def test_cancelled_limit_is_applied_after_md_exclusion(session) -> None:
    now = datetime.utcnow()
    ordinary = Job(
        id="ordinary-cancelled", name="ordinary", status="cancelled", queue_status="completed",
        error_message="cancelled by user", model_id="boltz", mode="predict", params={},
        created_at=now - timedelta(minutes=2),
    )
    md_jobs = [
        Job(
            id=f"md-cancelled-{index}", name="MD", status="cancelled", queue_status="completed",
            error_message="cancelled by user", model_id="molecular_dynamics", mode="molecular_dynamics",
            params={}, created_at=now - timedelta(seconds=index),
        )
        for index in range(2)
    ]
    session.add_all([ordinary, *md_jobs]); await session.flush()
    for job in md_jobs:
        await create_md_run(session, job=job, normalized_request=_request())

    visible = await list_cancelled_jobs(limit=2, session=session)

    assert [job.id for job in visible] == [ordinary.id]
