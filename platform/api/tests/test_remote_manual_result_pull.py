"""Offline manual-transfer boundaries. Written for parent execution after code freeze."""
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException
from database import Job, ExecutionTarget
from services.remote_execution import executor as ex
from services.remote_execution.contracts import RemoteAttemptStatus
from test_remote_lifecycle_gaps import store


async def ready(store):
    async with store() as session:
        job = await session.get(Job, "job")
        job.status, job.queue_status = "awaiting_input", "completed"
        job.remote_state = "results_available"
        job.awaiting_input, job.awaiting_stage = True, "remote_results"
        job.awaiting_payload = ex._pull_identity(job)
        job.provenance = {"remote_execution_receipt": {"result_manifest_sha256": "a" * 64}}
        (await session.get(ExecutionTarget, "target")).leased_job_id = "other-job"
        await session.commit()


def success():
    return RemoteAttemptStatus(
        job_id="job", attempt_id="attempt", state="succeeded", exit_code=0,
        started_at=datetime.utcnow(), completed_at=datetime.utcnow(),
        result_manifest_sha256="a" * 64,
    )


@pytest.mark.asyncio
async def test_remote_success_only_persists_prompt_and_releases_own_lease(store, monkeypatch):
    async def status(*_):
        return success()
    async def forbidden(*_, **__):
        pytest.fail("automatic scientific transfer")
    monkeypatch.setattr(ex, "remote_status", status)
    monkeypatch.setattr(ex, "collect_remote_results", forbidden)
    async with store() as session:
        assert await ex.reconcile_remote_job(session, await session.get(Job, "job"))
    async with store() as session:
        job = await session.get(Job, "job")
        assert (job.status, job.queue_status, job.remote_state) == ("awaiting_input", "completed", "results_available")
        assert job.awaiting_input and job.awaiting_stage == "remote_results"
        assert job.awaiting_payload == ex._pull_identity(job)
        assert job.completed_at is None
        assert (await session.get(ExecutionTarget, "target")).leased_job_id is None
        monkeypatch.setattr(ex, "remote_status", forbidden)
        assert not await ex.reconcile_remote_job(session, job)


@pytest.mark.asyncio
async def test_duplicate_click_one_background_transfer_other_gpu_owner_untouched(store, monkeypatch):
    await ready(store)
    events = []
    async def proof(*_):
        events.append("proof")
    async def status(*_):
        events.append("status")
        return success()
    async def collect(*_):
        events.append("collect")
        return SimpleNamespace(artifacts=[]), "/unused"
    async def finalize(session, job, *_):
        events.append("finalize")
        assert not job.awaiting_input
        assert await ex._publish_remote_transition(session, job, {
            "status": "completed", "queue_status": "completed", "remote_state": "ingested",
        }, require_lease=False)
    monkeypatch.setattr(ex, "_prove_pull_endpoint", proof)
    monkeypatch.setattr(ex, "remote_status", status)
    monkeypatch.setattr(ex, "collect_remote_results", collect)
    monkeypatch.setattr(ex, "_finalize_pulled_results", finalize)
    first, duplicate = BackgroundTasks(), BackgroundTasks()
    async with store() as session:
        await ex.request_remote_result_pull(session, await session.get(Job, "job"), first)
    try:
        assert not events  # The HTTP admission itself performs no transfer.
        async with store() as session:
            await ex.request_remote_result_pull(session, await session.get(Job, "job"), duplicate)
            assert not await ex.reconcile_remote_job(session, await session.get(Job, "job"))
        assert len(first.tasks) == 1 and not duplicate.tasks
    finally:
        await first()
    assert events == ["proof", "status", "collect", "finalize"]
    async with store() as session:
        assert (await session.get(Job, "job")).remote_state == "ingested"
        assert (await session.get(ExecutionTarget, "target")).leased_job_id == "other-job"


@pytest.mark.asyncio
async def test_failed_pull_requires_manual_retry_and_restart_never_downloads(store, monkeypatch):
    await ready(store)
    async def unavailable(*_):
        raise RuntimeError("provider unavailable")
    async def forbidden(*_):
        pytest.fail("unrequested transfer")
    monkeypatch.setattr(ex, "_prove_pull_endpoint", unavailable)
    monkeypatch.setattr(ex, "collect_remote_results", forbidden)
    background = BackgroundTasks()
    async with store() as session:
        await ex.request_remote_result_pull(session, await session.get(Job, "job"), background)
    await background()
    async with store() as session:
        job = await session.get(Job, "job")
        assert (job.status, job.remote_state, job.awaiting_stage) == ("awaiting_input", "result_pull_failed", "remote_results")
        assert "provider unavailable" in job.error_message
        assert not await ex.reconcile_remote_job(session, job)
        job.status, job.queue_status, job.remote_state = "running", "running", "returning"
        await session.commit()
        assert await ex.reconcile_remote_job(session, job)
        assert job.remote_state == "result_pull_failed" and job.awaiting_input
        assert (await session.get(ExecutionTarget, "target")).leased_job_id == "other-job"


@pytest.mark.asyncio
async def test_provider_endpoint_drift_rejected_before_ssh(store, monkeypatch):
    from services.remote_execution import vast
    async def changed(*_):
        return SimpleNamespace(provider_state="running", host="changed", port=123)
    async def forbidden(*_):
        pytest.fail("SSH before endpoint proof")
    monkeypatch.setattr(vast, "get_owned_instance", changed)
    monkeypatch.setattr(ex, "_verify_remote_runner", forbidden)
    async with store() as session:
        job = await session.get(Job, "job")
        job.remote_state = "returning"
        await session.commit()
        with pytest.raises(ex.RemoteExecutionError, match="endpoint"):
            await ex._prove_pull_endpoint(session, job)


@pytest.mark.asyncio
async def test_stale_attempt_payload_and_terminal_history_cannot_pull(store):
    await ready(store)
    async with store() as session:
        job = await session.get(Job, "job")
        job.remote_attempt_id = "new-attempt"
        job.nextflow_run_id = "remote:new-attempt"
        await session.commit()
        with pytest.raises(ex.RemoteExecutionError):
            await ex.request_remote_result_pull(session, job, BackgroundTasks())
        job.status, job.queue_status, job.remote_state = "completed", "completed", "ingested"
        await session.commit()
        with pytest.raises(ex.RemoteExecutionError):
            await ex.request_remote_result_pull(session, job, BackgroundTasks())
        assert not await ex.reconcile_remote_job(session, job)


@pytest.mark.asyncio
async def test_ordinary_manual_finalizer_does_not_need_or_release_other_gpu_lease(store):
    from services.result_state_integrity import finalize_successful_job
    await ready(store)
    async def ingest(*_, **__):
        return 0
    async with store() as session:
        job = await session.get(Job, "job")
        job.model_id = "custom_file_workflow"
        job.status, job.queue_status, job.remote_state = "running", "running", "returning"
        job.awaiting_input, job.awaiting_stage, job.awaiting_payload = False, None, {}
        await session.commit()
        result = await finalize_successful_job(job, "/unused", session, ingest_fn=ingest)
        assert result.completed
        assert (await session.get(ExecutionTarget, "target")).leased_job_id == "other-job"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["execution_source_revision", "execution_source_tree", "execution_bundle_sha256"])
@pytest.mark.parametrize("invalid", [None, "", "None"])
async def test_incomplete_source_identity_fails_before_transfer(store, field, invalid):
    await ready(store)
    tasks = BackgroundTasks()
    async with store() as session:
        job = await session.get(Job, "job")
        setattr(job, field, invalid)
        # Even a matching but invalid legacy prompt cannot authorize transfer.
        job.awaiting_payload = ex._pull_identity(job)
        await session.commit()
        with pytest.raises(ex.RemoteExecutionError, match="complete source identity"):
            await ex.request_remote_result_pull(session, job, tasks)
        assert not tasks.tasks
        await session.refresh(job)
        assert job.remote_state == "results_available"
        assert (await session.get(ExecutionTarget, "target")).leased_job_id == "other-job"


@pytest.mark.asyncio
async def test_remote_failure_preserves_diagnostics_without_transfer(store, monkeypatch):
    failed = success().model_copy(update={"state": "failed", "exit_code": 17, "error": "model stage failed"})
    async def status(*_):
        return failed
    async def forbidden(*_, **__):
        pytest.fail("automatic failed-run transfer or science finalization")
    monkeypatch.setattr(ex, "remote_status", status)
    monkeypatch.setattr(ex, "collect_remote_results", forbidden)
    monkeypatch.setattr(ex, "_finalize_pulled_results", forbidden)
    async with store() as session:
        assert await ex.reconcile_remote_job(session, await session.get(Job, "job"))
    async with store() as session:
        job = await session.get(Job, "job")
        assert (job.status, job.queue_status, job.remote_state) == ("failed", "failed", "failed")
        receipt = job.provenance["remote_execution_receipt"]
        assert receipt["result_manifest_sha256"] == failed.result_manifest_sha256
        assert receipt["state"] == "failed" and receipt["exit_code"] == 17
        assert receipt["error"] == job.error_message == "model stage failed"
        assert (await session.get(ExecutionTarget, "target")).leased_job_id is None
        with pytest.raises(ex.RemoteExecutionError):
            await ex.request_remote_result_pull(session, job, BackgroundTasks())


@pytest.mark.asyncio
async def test_force_launch_rejects_remote_results_gate(store):
    from services.job_control import _force_launch_with_session
    await ready(store)
    async with store() as session:
        with pytest.raises(HTTPException) as caught:
            await _force_launch_with_session(session, "job", 0, ["completed"])
        assert caught.value.status_code == 409
