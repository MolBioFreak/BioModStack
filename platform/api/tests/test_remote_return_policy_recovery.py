"""Persisted return authorization and crash recovery; no provider/science calls."""
import asyncio
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks

from database import Job, ExecutionTarget
from services.remote_execution import executor as ex
from test_remote_lifecycle_gaps import store
from test_remote_manual_result_pull import ready, success
from test_remote_diagnostics_backend import terminal
from test_remote_result_generation import package


async def policy(store, value):
    async with store() as session:
        job = await session.get(Job, "job")
        job.params = dict(job.params or {}, remote_result_policy=value)
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "manual", True, "true", "auto", {}, "automatic"])
async def test_only_persisted_explicit_opt_in_reserves_success_return(store, monkeypatch, value):
    await policy(store, value)
    async def status(*_):
        return success()
    async def unavailable(*_):
        raise ex.RemoteExecutionError("offline endpoint")
    async def forbidden(*_, **__):
        pytest.fail("science must never be submitted by return policy")
    monkeypatch.setattr(ex, "remote_status", status)
    monkeypatch.setattr(ex, "_prove_pull_endpoint", unavailable)
    monkeypatch.setattr(ex, "run_remote", forbidden)
    tasks = BackgroundTasks()
    async with store() as session:
        assert await ex.reconcile_remote_job(session, await session.get(Job, "job"), background_tasks=tasks)
    try:
        async with store() as session:
            job = await session.get(Job, "job")
            assert job.params["remote_result_policy"] == value
            assert job.remote_state == ("returning" if value == "automatic" else "results_available")
            assert (await session.get(ExecutionTarget, "target")).leased_job_id is None
            assert len(tasks.tasks) == int(value == "automatic")
    finally:
        await tasks()
    # Fresh sessions/controller reconciliation cannot turn failure into retries.
    async with store() as session:
        assert not await ex.reconcile_remote_job(session, await session.get(Job, "job"), background_tasks=BackgroundTasks())


@pytest.mark.asyncio
async def test_restarted_controller_schedules_without_browser_lifetime(store, monkeypatch):
    await ready(store)
    await policy(store, "automatic")
    seen = []
    async def unavailable(*_):
        seen.append("proof")
        raise ex.RemoteExecutionError("offline endpoint")
    monkeypatch.setattr(ex, "_prove_pull_endpoint", unavailable)
    async with store() as session:
        assert await ex.reconcile_remote_job(session, await session.get(Job, "job"))
    await asyncio.gather(*list(ex._result_return_tasks))
    assert seen == ["proof"]
    async with store() as session:
        job = await session.get(Job, "job")
        assert job.remote_state == "result_pull_failed"
        assert not await ex.reconcile_remote_job(session, job)


@pytest.mark.asyncio
@pytest.mark.parametrize("race", ["revoke", "cancel", "attempt"])
async def test_stale_opt_in_snapshot_cannot_reserve_after_operator_change(store, race):
    await ready(store)
    await policy(store, "automatic")
    tasks = BackgroundTasks()
    async with store() as stale:
        job = await stale.get(Job, "job")
        async with store() as other:
            current = await other.get(Job, "job")
            if race == "revoke":
                current.params = dict(current.params, remote_result_policy="manual")
            elif race == "cancel":
                current.status = current.queue_status = "cancelled"
            else:
                current.remote_attempt_id = "new-attempt"
                current.nextflow_run_id = "remote:new-attempt"
                current.awaiting_payload = ex._pull_identity(current)
            await other.commit()
        assert not await ex.request_remote_result_pull(stale, job, tasks, automatic=True)
    assert not tasks.tasks


@pytest.mark.asyncio
async def test_interrupted_auto_return_requires_explicit_retry(store):
    await ready(store)
    await policy(store, "automatic")
    async with store() as session:
        job = await session.get(Job, "job")
        job.status = job.queue_status = "running"
        job.remote_state = "returning"
        await session.commit()
    tasks = BackgroundTasks()
    async with store() as session:
        assert await ex.reconcile_remote_job(session, await session.get(Job, "job"), background_tasks=tasks)
        assert not tasks.tasks
    async with store() as session:
        job = await session.get(Job, "job")
        assert job.remote_state == "result_pull_failed"
        assert not await ex.reconcile_remote_job(session, job, background_tasks=tasks)
        assert not tasks.tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["failed", "lost", "cancelled"])
async def test_success_policy_never_retrieves_terminal_diagnostics(store, monkeypatch, state):
    await policy(store, "automatic")
    async def status(*_):
        return success().model_copy(update={"state": state, "exit_code": 1})
    async def forbidden(*_, **__):
        pytest.fail("diagnostics require separate explicit authorization")
    monkeypatch.setattr(ex, "remote_status", status)
    monkeypatch.setattr(ex, "collect_remote_results", forbidden)
    tasks = BackgroundTasks()
    async with store() as session:
        assert await ex.reconcile_remote_job(session, await session.get(Job, "job"), background_tasks=tasks)
    assert not tasks.tasks
    async with store() as session:
        job = await session.get(Job, "job")
        assert job.status == "failed"
        assert job.provenance["remote_execution_receipt"]["state"] == state
        assert not await ex.reconcile_remote_job(session, job, background_tasks=tasks)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["failed", "lost", "cancelled"])
@pytest.mark.parametrize("boundary", ["before_rename", "after_rename", "after_commit", "corrupt"])
async def test_diagnostic_process_death_recovery_matrix(store, tmp_path, monkeypatch, state, boundary):
    await terminal(store, tmp_path)
    async with store() as session:
        job = await session.get(Job, "job")
        job.status = job.queue_status = job.remote_state = "cancelled" if state == "cancelled" else "failed"
        _, incoming, status = package(job)
        digest = status.result_manifest_sha256
        identity = ex._pull_identity(job)
        receipt = dict(state=state, exit_code=1, result_manifest_sha256=digest)
        record = dict(state="returning", identity=identity, result_manifest_sha256=digest,
                      output_dir=None, error=None)
        job.provenance = dict(remote_execution_receipt=receipt, remote_diagnostics=record)
        destination = ex._diagnostic_destination("job", identity, digest)
        await session.commit()
        provenance = dict(job.provenance)
    # Real abrupt child exit exercises the filesystem/SQLite commit gap, not a
    # Python exception whose cleanup could hide the abandoned durable claim.
    pid = os.fork()
    if pid == 0:
        if boundary != "before_rename":
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(incoming, destination)
        if boundary == "after_commit":
            conn = sqlite3.connect(tmp_path / "state.sqlite")
            provenance["remote_diagnostics"] = dict(record, state="returned", output_dir=str(destination))
            conn.execute("UPDATE jobs SET provenance=? WHERE id='job'", (json.dumps(provenance),))
            conn.commit()
        if boundary == "corrupt":
            (destination / "first.txt").write_text("corrupt")
        os._exit(73)
    _, code = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(code) == 73
    async def forbidden(*_, **__):
        pytest.fail("restart recovery must not contact worker or run science")
    monkeypatch.setattr(ex, "remote_status", forbidden)
    monkeypatch.setattr(ex, "collect_remote_results", forbidden)
    async with store() as session:
        job = await session.get(Job, "job")
        assert await ex.reconcile_remote_job(session, job) == (boundary != "after_commit")
        await session.refresh(job)
        record = job.provenance["remote_diagnostics"]
        assert record["state"] == ("returned" if boundary in {"after_rename", "after_commit"} else "failed")
        assert job.completed_at == datetime(2025, 1, 1)
        assert job.error_message == "science failed"
        assert job.status == ("cancelled" if state == "cancelled" else "failed")
        assert (Path(job.output_dir) / "untouched").read_text() == "science"
        assert (await session.get(ExecutionTarget, "target")).leased_job_id == "successor"
        assert not await ex.reconcile_remote_job(session, job)
        if boundary == "before_rename":
            assert (incoming / "first.txt").read_text() == "first"
        elif boundary == "corrupt":
            assert (destination / "first.txt").read_text() == "corrupt"
        else:
            assert (Path(record["output_dir"]) / "first.txt").read_text() == "first"


@pytest.mark.asyncio
@pytest.mark.parametrize("race", ["receipt", "cancel", "attempt"])
async def test_return_publication_cannot_use_changed_authority(store, monkeypatch, race):
    await ready(store)
    async def proof(*_):
        pass
    async def status(*_):
        return success()
    async def collect(*_):
        async with store() as other:
            job = await other.get(Job, "job")
            if race == "receipt":
                job.provenance = dict(job.provenance, remote_execution_receipt=dict(
                    job.provenance["remote_execution_receipt"], result_manifest_sha256="b" * 64))
            elif race == "cancel":
                job.queue_status = "cancelling"
            else:
                job.remote_attempt_id = "successor"
            await other.commit()
        return SimpleNamespace(artifacts=[]), Path("/must-not-publish")
    async def forbidden(*_, **__):
        pytest.fail("superseded return must not publish or import")
    monkeypatch.setattr(ex, "_prove_pull_endpoint", proof)
    monkeypatch.setattr(ex, "remote_status", status)
    monkeypatch.setattr(ex, "collect_remote_results", collect)
    monkeypatch.setattr(ex, "_finalize_pulled_results", forbidden)
    tasks = BackgroundTasks()
    async with store() as session:
        await ex.request_remote_result_pull(session, await session.get(Job, "job"), tasks)
    await tasks()
    async with store() as session:
        job = await session.get(Job, "job")
        assert job.remote_state == "returning"
        assert (await session.get(ExecutionTarget, "target")).leased_job_id == "other-job"
