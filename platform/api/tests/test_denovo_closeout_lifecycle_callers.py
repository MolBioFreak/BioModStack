"""Exercise public/local lifecycle callers against the repaired shared owners."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from database import Job, get_session
from routers import jobs
from services import job_control, nextflow
from test_local_gate_review_rows import store, _job, _isolate  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelling", [False, True])
async def test_local_checkpoint_http_keeps_committed_cancel_intent(store, tmp_path, monkeypatch, cancelling):
    async with store() as session:
        job = _job(tmp_path, ["one"])
        job.provenance = {}
        if cancelling:
            job.queue_status = "cancelling"
            job.params = {"cancellation_receipt": {"state": "requested"}}
        session.add(job)
        await session.commit()
        checkpoint = job.awaiting_payload["component_checkpoint"]

    # Native checkpoint validation is not the boundary under repair. The HTTP
    # schema, loaded core row and real conditional UPDATE/commit remain real.
    runtime = SimpleNamespace(root_state=lambda: {
        "state": "paused", "quiescent": True,
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
    })
    monkeypatch.setattr(nextflow, "_local_checkpoint_resume", lambda job: (
        None, {"resources": {}}, runtime, checkpoint, None))
    monkeypatch.setattr(nextflow, "compile_component_checkpoint_continuation", lambda *args: None)
    monkeypatch.setattr(jobs, "_raise_if_workflow_launches_disabled", lambda *args: None)
    app = FastAPI()
    app.include_router(jobs.router, prefix="/api/jobs")

    async def private_session():
        async with store() as session:
            yield session

    app.dependency_overrides[get_session] = private_session
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.post("/api/jobs/gate-job/resume", json={
            "checkpoint_id": checkpoint["checkpoint_id"],
            "checkpoint_sha256": checkpoint["checkpoint_sha256"],
            "checkpoint_decision": {"selected_artifacts": [checkpoint["artifacts"][0]["relative_path"]]},
        })
    assert response.status_code == (409 if cancelling else 200), response.text
    async with store() as session:
        current = await session.get(Job, "gate-job")
        if cancelling:
            assert current.queue_status == "cancelling"
            assert current.awaiting_input is True
            assert current.params["cancellation_receipt"]["state"] == "requested"
        else:
            assert current.status == current.queue_status == "queued"
            assert current.awaiting_input is False


@pytest.mark.asyncio
async def test_local_completion_hook_cannot_restore_payload_after_cancel(store, tmp_path, monkeypatch):
    _isolate(monkeypatch)
    async with store() as session:
        session.add(_job(tmp_path, ["one"]))
        await session.commit()
    async with store() as publisher:
        original_get = publisher.get
        interleaved = False

        async def get_then_cancel(*args, **kwargs):
            nonlocal interleaved
            row = await original_get(*args, **kwargs)
            if not interleaved and isinstance(row, Job):
                interleaved = True
                # A separate physical connection commits before the publisher's
                # first DML; this is not a second writer inside its write lock.
                async with store() as cancellation:
                    await job_control.cancel_job_lineage("gate-job", cancellation)
            return row

        monkeypatch.setattr(publisher, "get", get_then_cancel)
        assert await nextflow._publish_local_review_rows(publisher, "gate-job") == 0
        assert interleaved
    async with store() as cold:
        current = await cold.get(Job, "gate-job")
        assert current.status == "cancelled" and current.awaiting_input is False
        assert current.awaiting_payload == {}
