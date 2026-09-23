"""Fail-closed controller cancellation and mutation ownership regressions."""
import asyncio
import threading
from pathlib import Path


import pytest
from fastapi import BackgroundTasks

from database import ExecutionTarget, Job
from services import job_control
from services.remote_execution import executor as ex, result_generation as gen
from test_remote_lifecycle_gaps import receipt, store
from test_remote_manual_result_pull import ready


@pytest.mark.asyncio
async def test_cancelled_remote_owner_retries_stop_before_releasing_lease(store, monkeypatch):
    async def failed_stop(_):
        return False
    monkeypatch.setattr(job_control, "cancel_nextflow_job", failed_stop)
    async with store() as s:
        await job_control.cancel_job_lineage("job", s)
    async with store() as s:
        job = await s.get(Job, "job")
        assert job.status == "cancelled"
        assert job.params["cancellation_receipt"]["remote_stop_verified"] is False
        assert (await s.get(ExecutionTarget, "target")).leased_job_id == "job"
        await job_control.cancel_job_lineage("job", s)
        assert job.params["cancellation_receipt"]["remote_stop_verified"] is False

    stopped = False
    calls = []
    async def status(*_):
        return receipt("cancelled" if stopped else "running")
    async def stop(*_, **__):
        nonlocal stopped
        calls.append("stop")
        if len(calls) > 1:
            stopped = True
        return stopped
    async def local(*_, **__):
        calls.append("local")
        return True
    monkeypatch.setattr(ex, "remote_status", status)
    monkeypatch.setattr(ex, "cancel_remote_job", stop)
    monkeypatch.setattr(ex, "cancel_local_result_transfer", local)
    async with store() as s:
        assert not await ex.reconcile_remote_job(s, await s.get(Job, "job"))
    async with store() as s:
        assert (await s.get(ExecutionTarget, "target")).leased_job_id == "job"
        assert await ex.reconcile_remote_job(s, await s.get(Job, "job"))
    async with store() as s:
        job = await s.get(Job, "job")
        assert job.params["cancellation_receipt"]["remote_stop_verified"] is True
        assert (await s.get(ExecutionTarget, "target")).leased_job_id is None
        assert calls.count("stop") == 2


@pytest.mark.asyncio
async def test_cancelling_return_skips_pull_failure_and_result_shortcut(store, monkeypatch):
    async with store() as s:
        job = await s.get(Job, "job")
        job.queue_status = "cancelling"
        job.remote_state = "returning"
        job.awaiting_stage = "remote_results"
        await s.commit()
    async def status(*_):
        return receipt("cancelled")
    async def local(*_, **__):
        return True
    monkeypatch.setattr(ex, "remote_status", status)
    monkeypatch.setattr(ex, "cancel_local_result_transfer", local)
    async with store() as s:
        assert await ex.reconcile_remote_job(s, await s.get(Job, "job"))
    async with store() as s:
        job = await s.get(Job, "job")
        assert (job.status, job.queue_status, job.remote_state) == ("cancelled", "cancelled", "cancelled")
        assert not await ex._pull_failure(s, job, "late transfer error")
        assert (await s.get(ExecutionTarget, "target")).leased_job_id is None


@pytest.mark.asyncio
async def test_blocked_mutation_joins_after_repeated_cancellation():
    started = threading.Event()
    release = threading.Event()
    ended = threading.Event()
    def mutate():
        started.set()
        release.wait(10)
        ended.set()
    owner = asyncio.create_task(ex._joined_thread(mutate))
    assert await asyncio.to_thread(started.wait, 5)
    owner.cancel()
    await asyncio.sleep(0)
    owner.cancel()
    await asyncio.sleep(0)
    assert not owner.done() and not ended.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await owner
    assert ended.is_set()


@pytest.mark.asyncio
async def test_no_run_id_does_not_fabricate_remote_stop_verification(store):
    async with store() as s:
        job = await s.get(Job, "job")
        job.nextflow_run_id = None
        await s.commit()
        await job_control.cancel_job_lineage("job", s)
        assert job.params["cancellation_receipt"]["remote_stop_verified"] is False
        assert (await s.get(ExecutionTarget, "target")).leased_job_id == "job"


@pytest.mark.asyncio
async def test_cancelled_return_without_compute_lease_waits_for_local_writer(store, monkeypatch):
    async with store() as s:
        job = await s.get(Job, "job")
        job.status = job.queue_status = "cancelled"
        job.remote_state = "returning"
        (await s.get(ExecutionTarget, "target")).leased_job_id = "successor"
        await s.commit()
    joined = False
    async def local(*_, **__):
        return joined
    async def forbidden(*_):
        pytest.fail("A released compute lease cannot authorize remote stop")
    monkeypatch.setattr(ex, "cancel_local_result_transfer", local)
    monkeypatch.setattr(ex, "remote_status", forbidden)
    async with store() as s:
        assert not await ex.reconcile_remote_job(s, await s.get(Job, "job"))
        assert (await s.get(Job, "job")).remote_state == "returning"
    joined = True
    async with store() as s:
        assert await ex.reconcile_remote_job(s, await s.get(Job, "job"))
    async with store() as s:
        assert (await s.get(Job, "job")).remote_state == "cancelled"
        assert (await s.get(ExecutionTarget, "target")).leased_job_id == "successor"


@pytest.mark.asyncio
async def test_auto_resume_waits_for_transfer_marker_without_spending_retry(store, tmp_path, monkeypatch):
    # A handed-off supervisor, unlike a never-spawned launcher, may own a writer.
    monkeypatch.setattr(ex, "get_data_root", lambda: tmp_path)
    async with store() as s:
        job = await s.get(Job, "job")
        job.output_dir = str(tmp_path / "result")
        digest = "d" * 64
        job.status = job.queue_status = "running"
        job.remote_state = "returning"
        job.awaiting_input = True
        job.awaiting_stage = "remote_results"
        job.params = dict(job.params or {}, remote_result_policy="automatic")
        job.provenance = dict(job.provenance or {}, remote_execution_receipt={"result_manifest_sha256": digest})
        job.awaiting_payload = ex._pull_identity(job)
        await s.commit()
        incoming = gen.staging_path(job, digest)
    incoming.parent.mkdir(parents=True, exist_ok=True)
    gen.begin_transfer(incoming)
    gen.durable_json(gen.transfer_marker(incoming), {
        "schema": "bms.local-result-transport.v1", "phase": "supervising",
        "destination": str(incoming),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
    })
    tasks = BackgroundTasks()
    async with store() as s:
        job = await s.get(Job, "job")
        assert not await ex.request_remote_result_pull(s, job, tasks, automatic=True)
        assert "remote_result_resume_attempted" not in job.provenance
        assert job.remote_state == "returning" and not tasks.tasks
    assert gen.transfer_marker(incoming).exists()


def test_transfer_inspection_with_no_staging_directory_is_read_only(tmp_path):
    incoming = tmp_path / "uncreated" / "incoming"
    gen.prepare_transfer(incoming)
    assert not incoming.parent.exists()
