from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import Base, Job
from services.job_control import pause_job, release_job_gpu, resume_job, retry_job


@pytest_asyncio.fixture
async def maker(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'queue-controls.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _add(maker, job_id: str, **fields) -> None:
    async with maker() as session:
        session.add(Job(id=job_id, name=job_id, model_id="boltz2", mode="predict", params={}, **fields))
        await session.commit()


async def _stored(maker, job_id: str) -> Job:
    async with maker() as session:
        return await session.get(Job, job_id)


async def _refused(maker, call, job_id: str, status_code: int) -> HTTPException:
    async with maker() as session:
        with pytest.raises(HTTPException) as refused:
            await call(job_id, session)
    assert refused.value.status_code == status_code
    return refused.value


@pytest.mark.asyncio
async def test_pause_job(maker) -> None:
    await _add(maker, "queued", status="queued", queue_status="queued")
    await _add(maker, "remote", status="running", queue_status="running", execution_target_id="vast:1")
    async with maker() as session:
        await pause_job("queued", session)
    stored = await _stored(maker, "queued")
    assert (stored.paused, stored.queue_status, stored.status) == (True, "paused", "queued")

    refusal = await _refused(maker, pause_job, "remote", 409)
    assert refusal.detail == "Running remote Jobs cannot be paused; cancel them or wait for completion"
    stored = await _stored(maker, "remote")
    assert (stored.paused, stored.queue_status) == (False, "running")


@pytest.mark.asyncio
async def test_resume_job(maker) -> None:
    await _add(maker, "paused", status="queued", queue_status="paused", paused=True)
    await _add(maker, "queued", status="queued", queue_status="queued")
    async with maker() as session:
        await resume_job("paused", session)
    stored = await _stored(maker, "paused")
    assert (stored.paused, stored.queue_status) == (False, "queued")

    assert (await _refused(maker, resume_job, "queued", 400)).detail == "Job is not paused"
    assert (await _refused(maker, resume_job, "missing", 404)).detail == "Job not found"


@pytest.mark.asyncio
async def test_release_job_gpu(maker) -> None:
    await _add(maker, "local", status="running", queue_status="running", assigned_gpu=1)
    await _add(maker, "remote", status="running", queue_status="running", assigned_gpu=1, execution_target_id="vast:1")
    async with maker() as session:
        _, released_gpu = await release_job_gpu("local", session)
    assert released_gpu == 1
    stored = await _stored(maker, "local")
    assert (stored.assigned_gpu, stored.queue_status) == (None, "running")

    refusal = await _refused(maker, release_job_gpu, "remote", 409)
    assert refusal.detail == "Remote Jobs release their GPU only through remote terminal reconciliation"
    assert (await _stored(maker, "remote")).assigned_gpu == 1


@pytest.mark.asyncio
async def test_retry_job(maker) -> None:
    await _add(
        maker, "failed", status="failed", queue_status="failed", paused=True, error_message="boom",
        assigned_gpu=0, current_stage="fold", retry_count=0, max_retries=1,
    )
    await _add(maker, "running", status="running", queue_status="running")
    async with maker() as session:
        await retry_job("failed", session)
    stored = await _stored(maker, "failed")
    assert (stored.retry_count, stored.status, stored.queue_status, stored.paused) == (1, "queued", "queued", False)
    assert (stored.error_message, stored.assigned_gpu, stored.current_stage) == (None, None, None)

    assert (await _refused(maker, retry_job, "running", 400)).detail == "Can only retry failed jobs"
    async with maker() as session:
        job = await session.get(Job, "failed")
        job.status = job.queue_status = "failed"
        await session.commit()
    assert (await _refused(maker, retry_job, "failed", 400)).detail == "Max retries (1) exceeded"
    assert (await _stored(maker, "failed")).retry_count == 1
