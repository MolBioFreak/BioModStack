from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Job
from services import binder_round, execution_ownership, gpu_orchestrator, nextflow


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [
    "active", "active-history", "query-error", "missing-lane", "nonempty-cgroup",
    "failed-unit", "different-invocation", "missing-receipt", "malformed-receipt",
    "sparse-receipt", "wrong-lane", "wrong-job", "inactive",
])
async def test_completion_reconciler_waits_for_transient_owner_before_stale_failure(
    monkeypatch, tmp_path, scenario,
) -> None:
    job_id = "job-transient-owner-123"
    unit = execution_ownership.deterministic_unit_name("development", job_id, 1)
    receipt = execution_ownership.planned_execution_attempt(
        lane="development", job_id=job_id, generation=1, attempt=1, unit=unit,
        owner_nonce="owner-nonce-1", request_fingerprint_value="request-fingerprint-1",
    )
    receipt.update(state="started", invocation_id="invocation-1",
                   started_at="2026-08-09T23:04:24.446389Z")
    receipts: list[object] = [receipt]
    if scenario == "missing-receipt":
        receipts = []
    elif scenario == "malformed-receipt":
        receipts.append("malformed-newest-receipt")
    elif scenario == "sparse-receipt":
        receipts.append({"lane": "development", "unit": unit, "invocation_id": "invocation-1"})
    elif scenario in {"wrong-lane", "wrong-job"}:
        lane = "production" if scenario == "wrong-lane" else "development"
        owner_job = "other-job" if scenario == "wrong-job" else job_id
        other = execution_ownership.planned_execution_attempt(
            lane=lane, job_id=owner_job, generation=2, attempt=1,
            unit=execution_ownership.deterministic_unit_name(lane, owner_job, 1),
            owner_nonce="other-nonce", request_fingerprint_value="other-fingerprint",
        )
        other.update(state="started", invocation_id="other-invocation",
                     started_at="2026-08-09T23:04:24.446389Z")
        receipts.append(other)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'owner.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add(Job(
                id=job_id, name="adapter-owned-job", model_id="fampnn", mode="design",
                status="running", queue_status="running", assigned_gpu=0, pinned_gpu=0,
                started_at=datetime.utcnow() - timedelta(seconds=400), nextflow_run_id=unit,
                params={execution_ownership.EXECUTION_ATTEMPTS_PARAM: receipts},
            ))
            await session.commit()

        queries = []

        def show_unit_properties(unit_name, lane):
            queries.append((unit_name, lane))
            if scenario == "query-error":
                raise execution_ownership.ExecutionOwnershipError("systemd user bus unavailable")
            return SimpleNamespace(
                active_state="active" if scenario.startswith("active") else
                             "failed" if scenario == "failed-unit" else "inactive",
                invocation_id="different" if scenario == "different-invocation" else "invocation-1",
            )

        async def no_round_recovery(*_):
            pass

        monkeypatch.setattr(binder_round, "recover_rounds", no_round_recovery)
        monkeypatch.setattr(nextflow, "get_running_jobs", lambda: {})
        monkeypatch.setattr(gpu_orchestrator, "workflow_adapter_enabled", lambda: True)
        monkeypatch.setattr(gpu_orchestrator, "workflow_adapter_lane",
                            lambda: None if scenario == "missing-lane" else "development")
        monkeypatch.setattr(gpu_orchestrator, "_read_nextflow_history_statuses",
                            lambda _: {job_id: ("OK", "1s")} if scenario == "active-history" else {})
        monkeypatch.setattr(gpu_orchestrator, "show_unit_properties", show_unit_properties)
        monkeypatch.setattr(gpu_orchestrator, "unit_has_empty_cgroup",
                            lambda _: scenario != "nonempty-cgroup")
        orchestrator = gpu_orchestrator.GPUOrchestrator(factory, lambda: [], lambda **_: None)
        await orchestrator.check_job_completions()

        no_query = {"missing-lane", "missing-receipt", "malformed-receipt",
                    "sparse-receipt", "wrong-lane", "wrong-job"}
        assert queries == ([] if scenario in no_query else [(unit, "development")])
        async with factory() as session:
            job = await session.get(Job, job_id)
            if scenario == "inactive":
                assert job.status == job.queue_status == "failed"
                assert job.completed_at is not None
                assert "no active process" in job.error_message
                assert job.assigned_gpu is None
            else:
                assert job.status == job.queue_status == "running"
                assert job.completed_at is None
                assert job.error_message is None
                assert job.assigned_gpu == 0
    finally:
        await engine.dispose()
