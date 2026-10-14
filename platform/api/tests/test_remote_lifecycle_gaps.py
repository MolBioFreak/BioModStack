"""Offline durable remote lifecycle interleavings (independent DB sessions)."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import asyncio
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database import Base, Job, ExecutionTarget
from component_runtime import NativeInvocation, SourceIdentity
from services.remote_execution import executor as ex
from services.remote_execution.contracts import RemoteAttemptStatus


@pytest_asyncio.fixture
async def store(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'state.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(ex, "async_session", factory)
    monkeypatch.setattr(ex, "get_data_root", lambda: tmp_path)
    async with factory() as s:
        s.add(Job(id="job", name="job", model_id="boltz2", mode="predict", params={},
                  status="running", queue_status="running", execution_target_id="target",
                  execution_source_revision="a" * 40, execution_source_tree="b" * 40,
                  execution_bundle_sha256="c" * 64,
                  nextflow_run_id="remote:attempt", remote_attempt_id="attempt", remote_state="running"))
        s.add(ExecutionTarget(id="target", provider="vast", provider_instance_id="1",
                             leased_job_id="job", lease_acquired_at=datetime.utcnow()))
        await s.commit()
    yield factory
    await engine.dispose()


def lifecycle_invocation(command):
    """Typed handoff for mocked lifecycle commands, not a science compiler probe."""
    return NativeInvocation(
        model_id="boltz2", mode="predict", command=tuple(command),
        requested_json=b"{}", effective_json=b"{}", native_parameters_json=b"{}",
        source_identity=SourceIdentity("a" * 40, "b" * 40),
    )


async def preparing(store):
    async with store() as s:
        job = await s.get(Job, "job")
        job.status, job.queue_status = "queued", "preparing"
        job.remote_attempt_id = job.nextflow_run_id = None
        job.remote_state = "preparing"
        job.provenance = {"remote_execution_assignment": {"claimed_at": datetime.utcnow().isoformat() + "Z"}}
        await s.commit()








































def receipt(state="succeeded", job_id="job", attempt_id="attempt"):
    return RemoteAttemptStatus(job_id=job_id, attempt_id=attempt_id, state=state,
                               boot_id="test-boot", quiescent=True,
                               exit_code=0, started_at=datetime.utcnow(), completed_at=datetime.utcnow())
