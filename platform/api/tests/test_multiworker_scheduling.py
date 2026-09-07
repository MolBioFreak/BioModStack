"""Actual scheduler/SQL claims, isolated SQLite; no provider or science calls."""
from datetime import datetime
import sqlite3

import pytest
import pytest_asyncio
from sqlalchemy import inspect, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, ExecutionTarget, Job
import services.gpu_orchestrator as scheduler
from migrations.enable_multiple_execution_targets import migrate


@pytest_asyncio.fixture
async def workers(tmp_path):
    path = tmp_path / 'workers.db'
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    # Reproduce the deployed old constraint; only the production migration
    # may remove it. Fleet cases must run, never skip or drop it in test SQL.
    with sqlite3.connect(path) as legacy:
        legacy.execute("CREATE UNIQUE INDEX uq_execution_targets_one_active ON execution_targets(active) WHERE active = 1")
    migrate(path)
    async with engine.begin() as connection:
        indexes = await connection.run_sync(lambda sync: inspect(sync).get_indexes("execution_targets"))
    assert not any(index["name"] == "uq_execution_targets_one_active" for index in indexes)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        for ordinal in (1, 2):
            target = f"vast:{ordinal}"
            session.add(ExecutionTarget(id=target, provider="vast", provider_instance_id=str(ordinal),
                active=True, state="ready", provider_metadata={"inventory": {
                    "status": "complete", "present": True, "running": True,
                    "checked_at": datetime.utcnow().isoformat()}}))
            session.add(Job(id=f"job-{ordinal}", name=f"job-{ordinal}", params={},
                status="queued", queue_status="queued", paused=False, priority=3-ordinal,
                model_id="cpu-only", mode="run", vram_estimate_mb=0,
                execution_target_id=target, output_dir=str(tmp_path / f"output-{ordinal}")))
        await session.commit()
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", ["lease", "job_changed"])
async def test_losing_claim_does_not_expire_other_workers_or_hold_target(workers, conflict):
    async with workers() as session:
        first = await session.get(Job, "job-1")
        second = await session.get(Job, "job-2")
        second.name = "pending-unrelated-edit"
        async with workers() as writer:
            if conflict == "lease":
                await writer.execute(update(ExecutionTarget).where(ExecutionTarget.id == "vast:1")
                                     .values(leased_job_id="predecessor"))
            else:
                await writer.execute(update(Job).where(Job.id == "job-1").values(paused=True))
            await writer.commit()
        assert await scheduler._claim_remote_job(session, first, gpu_id=None, vram_estimate_mb=0) is None
        assert not inspect(second).expired_attributes
        assert second.id == "job-2"

        assert await scheduler._claim_remote_job(session, second, gpu_id=None, vram_estimate_mb=0) == {}
    async with workers() as verify:
        first_target = await verify.get(ExecutionTarget, "vast:1")
        second_target = await verify.get(ExecutionTarget, "vast:2")
        assert first_target.leased_job_id == ("predecessor" if conflict == "lease" else None)
        assert second_target.leased_job_id == "job-2"
        assert (await verify.get(Job, "job-2")).name == "pending-unrelated-edit"


@pytest.mark.asyncio
async def test_idle_ready_worker_and_retained_results_acquire_no_lease(workers, monkeypatch):
    async with workers() as session:
        await session.execute(update(Job).values(paused=True))
        session.add(Job(id="retained-idle", name="retained-idle", model_id="cpu-only", mode="run", params={}, status="awaiting_input",
            queue_status="awaiting_input", execution_target_id="vast:1",
            awaiting_stage="remote_results", remote_state="results_available"))
        await session.commit()
    monkeypatch.setattr(scheduler, "read_scheduler_config", lambda: {"global": {"enabled": True}})
    async def launch(**kwargs):
        pytest.fail("idle worker or retained results must not launch science")
    await scheduler.GPUOrchestrator(workers, lambda: [], launch)._process_cycle()
    async with workers() as verify:
        for ordinal in (1, 2):
            target = await verify.get(ExecutionTarget, f"vast:{ordinal}")
            assert target.leased_job_id is None and target.lease_acquired_at is None
        retained = await verify.get(Job, "retained-idle")
        assert retained.remote_state == "results_available"


@pytest.mark.asyncio
@pytest.mark.parametrize("busy_first", [False, True])
async def test_cycle_routes_independent_workers_without_idle_reservations(workers, monkeypatch, busy_first):
    async with workers() as session:
        targets = [await session.get(ExecutionTarget, f"vast:{i}") for i in (1, 2)]
        assert all(target.leased_job_id is None for target in targets)
        if busy_first:
            targets[0].leased_job_id = "uncertain-predecessor"
        # Retained results do not reserve idle worker capacity or cause a pull.
        session.add(Job(id="retained", name="retained", model_id="cpu-only", mode="run", params={}, status="awaiting_input",
            queue_status="awaiting_input", execution_target_id="vast:2",
            awaiting_stage="remote_results", remote_state="results_available"))
        await session.commit()
    monkeypatch.setattr(scheduler, "read_scheduler_config", lambda: {"global": {"enabled": True}})
    launched = []
    async def launch(**kwargs):
        async with workers() as verify:
            job = await verify.get(Job, kwargs["job_id"])
            target = await verify.get(ExecutionTarget, job.execution_target_id)
            assert target.leased_job_id == job.id
            assert job.queue_status == "preparing" and job.started_at is None
            assert "gpu_id" not in kwargs["params"]
            launched.append((job.id, target.id))
    await scheduler.GPUOrchestrator(workers, lambda: [], launch)._process_cycle()
    assert launched == ([("job-2", "vast:2")] if busy_first else
                        [("job-1", "vast:1"), ("job-2", "vast:2")])
    async with workers() as verify:
        retained = await verify.get(Job, "retained")
        assert retained.remote_state == "results_available"
        if busy_first:
            first = await verify.get(Job, "job-1")
            target = await verify.get(ExecutionTarget, "vast:1")
            assert first.queue_status == "queued"
            assert target.leased_job_id == "uncertain-predecessor"


@pytest.mark.asyncio
async def test_same_target_concurrent_claims_have_one_winner_and_leave_other_worker_idle(workers):
    import asyncio
    async with workers() as session:
        await session.execute(update(Job).where(Job.id == "job-2").values(execution_target_id="vast:1"))
        await session.commit()
    async def claim(identifier):
        async with workers() as session:
            job = await session.get(Job, identifier)
            return await scheduler._claim_remote_job(session, job, gpu_id=None, vram_estimate_mb=0)
    results = await asyncio.gather(claim("job-1"), claim("job-2"))
    assert sum(result is not None for result in results) == 1
    async with workers() as session:
        owner = (await session.get(ExecutionTarget, "vast:1")).leased_job_id
        assert owner in {"job-1", "job-2"}
        assert (await session.get(Job, owner)).queue_status == "preparing"
        other = "job-2" if owner == "job-1" else "job-1"
        assert (await session.get(Job, other)).queue_status == "queued"
        assert (await session.get(ExecutionTarget, "vast:2")).leased_job_id is None


@pytest.mark.asyncio
async def test_attachment_controller_admits_distinct_workers_but_not_duplicate_setup(workers, monkeypatch):
    import asyncio
    from services.remote_execution import targets, vast
    from services.remote_execution.contracts import ExecutionTargetActivateRequest, ExecutionTargetInventoryResponse
    release = asyncio.Event()
    async def finish(*args):
        await release.wait()
    async def inventory(_session):
        return ExecutionTargetInventoryResponse(provider="vast", available=True, credential_configured=True, message="fixture",
            instances=[vast._normalize({"id": str(i), "actual_status": "running", "ssh_host": f"203.0.113.{i}", "ssh_port": 22}) for i in (1, 2)])
    monkeypatch.setattr(targets, "finish_activation", finish)
    monkeypatch.setattr(targets, "refresh_vast_targets", inventory)
    controller = targets.AttachmentController(workers)
    try:
        async with workers() as session:
            for i in (1, 2):
                target = await session.get(ExecutionTarget, f"vast:{i}")
                target.host, target.port = f"203.0.113.{i}", 22
            await session.commit()
            for i in (1, 2):
                result = await controller.attach(session, ExecutionTargetActivateRequest(provider_instance_id=str(i)))
                assert result.state == "probing"
            assert set(controller.tasks) == {"vast:1", "vast:2"}
            with pytest.raises(targets.ExecutionTargetError, match="already in progress"):
                await controller.attach(session, ExecutionTargetActivateRequest(provider_instance_id="1"))
            for i in (1, 2):
                assert (await session.get(ExecutionTarget, f"vast:{i}")).leased_job_id is None
    finally:
        release.set()
        await asyncio.gather(*list(controller.tasks.values()))
        await controller.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("other_activity", ["idle", "lease", "preload", "nonterminal"])
async def test_attachment_is_target_scoped_and_preserves_other_active_worker(workers, monkeypatch, tmp_path, other_activity):
    from types import SimpleNamespace
    from sqlalchemy import select
    from services.remote_execution import targets, vast
    from services.remote_execution.contracts import ExecutionTargetActivateRequest, ExecutionTargetInventoryResponse
    from services import nextflow
    async with workers() as session:
        first = await session.get(ExecutionTarget, "vast:1")
        if other_activity == "lease":
            first.leased_job_id = "uncertain-predecessor"
        if other_activity == "preload":
            first.provider_metadata = {**first.provider_metadata, "preload": {"phase": "transferring"}}
        await session.execute(update(Job).values(status="completed", queue_status="completed"))
        if other_activity == "nonterminal":
            await session.execute(update(Job).where(Job.id == "job-1").values(status="running", queue_status="running"))
        second = await session.get(ExecutionTarget, "vast:2")
        second.host, second.port, second.username = "203.0.113.2", 22, "root"
        second.active, second.state = False, "discovered"
        await session.commit()
        before = (await session.execute(select(ExecutionTarget.__table__).where(ExecutionTarget.id == "vast:1"))).one()
        jobs_before = (await session.execute(select(Job.__table__).order_by(Job.id))).all()
        instance = vast._normalize({"id": "2", "actual_status": "running", "ssh_host": second.host, "ssh_port": 22})
        async def inventory(_session):
            return ExecutionTargetInventoryResponse(provider="vast", available=True, credential_configured=True, message="fixture", instances=[instance])
        async def capture(*args): return ("fixture host key", "a" * 64)
        async def noop(*args, **kwargs): pass
        async def probe(*args): return {"ok": True}
        async def run(connection, command, **kwargs):
            if command[0] == "env": return SimpleNamespace(stdout="nextflow version 25.10.1\n")
            if command[0] == "apptainer": return SimpleNamespace(stdout="BMS_CUDA_OK\n")
            return SimpleNamespace(stdout="fixturehash worker\nfixturehash nextflow\n")
        launcher = tmp_path / "nextflow"
        launcher.write_text("fixture")
        monkeypatch.setattr(nextflow, "resolve_nextflow_executable", lambda: str(launcher))
        monkeypatch.setattr(targets, "refresh_vast_targets", inventory)
        monkeypatch.setattr(targets, "capture_host_key", capture)
        monkeypatch.setattr(targets, "persist_host_key", noop)
        monkeypatch.setattr(targets, "probe_readiness", probe)
        monkeypatch.setattr(targets, "rsync_to_remote", noop)
        monkeypatch.setattr(targets, "run_remote", run)
        monkeypatch.setattr(targets, "_sha256_file", lambda *args: "fixturehash")
        response = await targets.activate_target(session, ExecutionTargetActivateRequest(provider_instance_id="2"))
        assert response.active and response.state == "ready"
        assert (await session.execute(select(ExecutionTarget.__table__).where(ExecutionTarget.id == "vast:1"))).one() == before
        assert (await session.execute(select(Job.__table__).order_by(Job.id))).all() == jobs_before
        assert (await session.get(ExecutionTarget, "vast:2")).leased_job_id is None
