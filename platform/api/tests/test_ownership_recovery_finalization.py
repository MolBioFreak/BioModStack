"""Recovery uses a committed finalization; it does not write the Job's terminal state again."""
import subprocess
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Job
from services import analysis_autorun, nextflow, result_ingester, result_state_integrity
from services import gpu_orchestrator as orchestrator


@pytest.mark.asyncio
async def test_recovery_does_not_restamp_a_job_its_finalizer_committed(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'recovery.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    async with sessions() as session:
        session.add(Job(
            id="recovered", name="recovered", model_id="boltz2", mode="predict",
            params={"result_integrity_requires_designs": False}, provenance={},
            status="running", queue_status="running", awaiting_input=False, awaiting_payload={},
            output_dir=str(output_dir), started_at=datetime.utcnow() - timedelta(minutes=10),
        ))
        await session.commit()

    # No process, no scheduler-snapshot history; the run directory's history says OK.
    monkeypatch.setattr(orchestrator, "_read_nextflow_history_statuses", lambda ids: {})
    monkeypatch.setattr(orchestrator, "nextflow_history_status_for_run_dir", lambda *args: None)
    monkeypatch.setattr(orchestrator, "nextflow_history_status", lambda *args: "OK")
    monkeypatch.setattr(orchestrator, "workflow_adapter_enabled", lambda: False)
    monkeypatch.setattr(nextflow, "get_running_jobs", lambda: {})
    monkeypatch.setattr(analysis_autorun, "schedule_viewer_minimum_analyses_for_job", lambda *args: None)
    run = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: (
        SimpleNamespace(returncode=0, stdout="") if command == ["ps", "aux"] else run(command, **kwargs)))
    monkeypatch.setattr(result_ingester, "ingest_job_results", AsyncMock(return_value=0))

    committed = []
    finalize = result_state_integrity.finalize_successful_job

    async def finalize_and_record(job, *args, **kwargs):
        result = await finalize(job, *args, **kwargs)
        committed.append((result.completed, job.status, job.queue_status, job.completed_at))
        return result

    monkeypatch.setattr(result_state_integrity, "finalize_successful_job", finalize_and_record)
    later_job_writes = []

    def record(connection, cursor, statement, *args):
        if committed and statement.lstrip().upper().startswith("UPDATE JOBS"):
            later_job_writes.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        scheduler = orchestrator.GPUOrchestrator(sessions, lambda: [], AsyncMock())
        scheduler._next_binder_recovery_at = float("inf")
        await scheduler.check_job_completions()
        assert [entry[:3] for entry in committed] == [(True, "completed", "completed")]
        assert later_job_writes == []
        async with sessions() as session:
            job = await session.get(Job, "recovered")
            assert (job.status, job.queue_status, job.completed_at) == committed[0][1:]
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
        await engine.dispose()
