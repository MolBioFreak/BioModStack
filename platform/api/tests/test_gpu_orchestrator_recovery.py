from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import Base, ExecutionTarget, Job, MdRun
import services.gpu_orchestrator as gpu_module
from services.gpu_orchestrator import (
    GPUOrchestrator,
    _claim_job_for_gpu,
    _claim_remote_job,
    _commit_reconciled_job_mutations,
    _has_terminal_nextflow_history,
    _job_requires_result_output_for_terminal_history,
    _recover_rfantibody_parent_after_child_wait,
    _reconcile_terminal_history_without_process,
)


@pytest.mark.asyncio
async def test_remote_vram_budget_allows_exactly_one_concurrent_claim(tmp_path: Path, monkeypatch) -> None:
    from services.remote_execution import targets
    async def telemetry(target):
        return dict(available=True, gpus=[dict(index=0, uuid="GPU-remote", memory_total_mb=7000,
                                              memory_used_mb=0, utilization=0)])
    monkeypatch.setattr(targets, "remote_target_telemetry", telemetry)
    monkeypatch.setattr(gpu_module, "read_scheduler_config", lambda: {"global": {
        "target_vram_fill": 0.75, "vram_safety_margin_mb": 0}})
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'remote-claim.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    target_id = "vast:123"
    async with factory() as seed:
        seed.add(ExecutionTarget(
            capabilities={"gpu_count": 1},
            id=target_id,
            provider="vast",
            provider_instance_id="123",
            state="ready",
            active=True,
            provider_metadata={"inventory": {"status": "complete", "present": True,
                                             "running": True, "checked_at": datetime.utcnow().isoformat()}},
        ))
        for job_id in ("remote-a", "remote-b"):
            seed.add(Job(
                id=job_id,
                name=job_id,
                status="queued",
                queue_status="queued",
                paused=False,
                model_id="boltz2",
                mode="predict",
                params={},
                output_dir=str(tmp_path / job_id),
                execution_target_id=target_id,
            ))
        await seed.commit()

    async def claim(job_id: str) -> dict[str, Any] | None:
        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            return await _claim_remote_job(
                session,
                job,
                gpu_id=0,
                gpu_ids=[0],
                vram_estimate_mb=4096,
            )

    claims = await asyncio.gather(claim("remote-a"), claim("remote-b"))
    assert sum(result is not None for result in claims) == 1
    async with factory() as verify:
        target = await verify.get(ExecutionTarget, target_id)
        jobs = [await verify.get(Job, job_id) for job_id in ("remote-a", "remote-b")]
        assert target is not None and target.leased_job_id is None
        assert sum(job is not None and job.queue_status == "preparing" for job in jobs) == 1
        assert all(job.started_at is None and job.status == "queued" for job in jobs)
    await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_vram_is_estimated_and_pinned_while_explicit_zero_stays_cpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scheduler.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add_all(
            [
                Job(
                    id="gpu-job",
                    name="gpu-job",
                    status="queued",
                    queue_status="queued",
                    model_id="protein_local_redesign",
                    mode="local_redesign",
                    params={},
                    output_dir=str(tmp_path / "gpu-output"),
                    vram_estimate_mb=None,
                    pinned_gpu=2,
                ),
                Job(
                    id="cpu-job",
                    name="cpu-job",
                    status="queued",
                    queue_status="queued",
                    model_id="cpu-only",
                    mode="run",
                    params={},
                    output_dir=str(tmp_path / "cpu-output"),
                    vram_estimate_mb=0,
                ),
            ]
        )
        await session.commit()

    monkeypatch.setattr(
        gpu_module,
        "read_scheduler_config",
        lambda: {"global": {"enabled": True, "target_vram_fill": 0.9}, "concurrency_limits": {}},
    )
    launched: list[dict[str, Any]] = []
    durable_assignments: list[tuple[int | None, object]] = []

    async def launch(**kwargs: Any) -> None:
        launched.append(kwargs)
        if kwargs["job_id"] == "gpu-job":
            async with factory() as handoff_session:
                persisted_job = await handoff_session.get(Job, "gpu-job")
                durable_assignments.append(
                    (
                        persisted_job.assigned_gpu if persisted_job is not None else None,
                        persisted_job.params.get("gpu_id") if persisted_job is not None else None,
                    )
                )

    gpu = SimpleNamespace(
        index=2,
        name="GPU 2",
        memory_used_mb=0,
        memory_total_mb=32607,
        memory_free_mb=32607,
        utilization=0,
        temperature=30,
    )
    orchestrator = GPUOrchestrator(factory, lambda: [gpu], launch)
    await orchestrator._process_cycle()

    by_id = {str(item["job_id"]): item for item in launched}
    assert "gpu_id" not in by_id["cpu-job"]["params"]
    assert by_id["gpu-job"]["params"]["gpu_id"] == 2
    assert durable_assignments == [(2, 2)]
    async with factory() as session:
        gpu_job = await session.get(Job, "gpu-job")
        cpu_job = await session.get(Job, "cpu-job")
        assert gpu_job is not None and gpu_job.assigned_gpu == 2
        assert gpu_job.params["gpu_id"] == 2
        assert gpu_job.vram_estimate_mb is not None and gpu_job.vram_estimate_mb > 0
        assert cpu_job is not None and cpu_job.assigned_gpu is None
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mb,devices,model,expected", [
    (20000, [2], 'example', 1),
    (4096, [2], 'example', 2),
    (20000, [2, 3], 'example', 2),
    (0, [], 'example', 2),
])
async def test_overlapping_local_cycles(tmp_path, monkeypatch, mb, devices, model, expected):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'overlap.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(gpu_module, 'read_scheduler_config', lambda: {'global': {
        'max_launches_per_cycle': 1, 'target_vram_fill': .9}})
    gpus = [SimpleNamespace(index=i, name='gpu', memory_used_mb=0, memory_total_mb=32607,
                            utilization=0, temperature=30) for i in devices]
    async def seed(key, priority):
        async with factory() as s:
            s.add(Job(id=key, name=key, model_id=model, mode='run', params={},
                      output_dir=str(tmp_path / key), status='queued', queue_status='queued',
                      priority=priority, vram_estimate_mb=mb))
            await s.commit()

    launches = []
    async def launch(job_id, **kwargs):
        async with factory() as reader:
            owner = await reader.get(Job, job_id)
            launches.append((job_id, owner.status, owner.queue_status, owner.assigned_gpu))

    # Pause A after reading its queue. B then sees a different priority winner.
    # Neither the packer nor the actual claim or reservation query is stubbed.
    selected, resume = asyncio.Event(), asyncio.Event()
    lock_held, contender = asyncio.Event(), asyncio.Event()
    class DelayedSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            result = await super().execute(statement, *args, **kwargs)
            if not selected.is_set() and 'ORDER BY jobs.priority DESC' in str(statement):
                selected.set()
                await asyncio.wait_for(resume.wait(), 5)
            if str(statement) == 'BEGIN IMMEDIATE' and not lock_held.is_set():
                lock_held.set()
                await asyncio.wait_for(contender.wait(), 5)
            return result

    class ContendingSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            if str(statement) == 'BEGIN IMMEDIATE':
                contender.set()
            return await super().execute(statement, *args, **kwargs)

    delayed = sessionmaker(engine, class_=DelayedSession, expire_on_commit=False)
    await seed('first', 1)
    task = asyncio.create_task(GPUOrchestrator(delayed, lambda: gpus, launch)._process_cycle())
    try:
        await asyncio.wait_for(selected.wait(), 5)
        await seed('second', 2)
        peer = factory
        if mb:
            resume.set()
            await asyncio.wait_for(lock_held.wait(), 5)
            peer = sessionmaker(engine, class_=ContendingSession, expire_on_commit=False)
        await GPUOrchestrator(peer, lambda: gpus, launch)._process_cycle()
    finally:
        resume.set()
        await task
    assert len(launches) == expected
    assert {row[0] for row in launches} == (
        {'first', 'second'} if expected == 2 else {'first' if mb else 'second'})
    assert all(row[1:3] == ('running', 'running') for row in launches)
    if len(devices) == expected == 2:
        assert {row[3] for row in launches} == set(devices)
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['success', 'failure', 'cancel_then_fail', 'successor_then_fail'])
async def test_ordinary_cpu_handoff_has_one_owner(tmp_path, monkeypatch, outcome):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cpu.db'}")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as seed:
        seed.add(Job(id='cpu', name='cpu', model_id='example', mode='run', params={},
                     output_dir=str(tmp_path), vram_estimate_mb=0, status='queued', queue_status='queued'))
        await seed.commit()
    calls = []
    async def launch(job_id, **kwargs):
        async with factory() as peer:
            job = await peer.get(Job, job_id)
            calls.append((job.status, job.queue_status, job.started_at is not None))
            if outcome == 'cancel_then_fail':
                job.queue_status = job.status = 'cancelled'
            elif outcome == 'successor_then_fail':
                job.started_at = datetime(2030, 1, 1)
            await peer.commit()
        if outcome != 'success':
            raise RuntimeError('inert failure')

    monkeypatch.setattr(gpu_module, 'read_scheduler_config', lambda: {})
    await asyncio.gather(*(GPUOrchestrator(factory, lambda: [], launch)._process_cycle() for _ in range(2)))
    assert calls == [('running', 'running', True)]
    async with factory() as reader:
        job = await reader.get(Job, 'cpu')
        status = {'cancel_then_fail': 'cancelled', 'failure': 'failed'}.get(outcome, 'running')
        assert job.status == job.queue_status == status
        assert job.error_message == ('inert failure' if outcome == 'failure' else None)
    await engine.dispose()


@pytest.mark.asyncio
async def test_gpu_claim_loses_to_second_session_cancellation_without_launch(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'claim.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        job = Job(
            id="claim-job",
            name="claim-job",
            status="queued",
            queue_status="queued",
            model_id="protein_local_redesign",
            mode="local_redesign",
            params={"gpu_id": 7},
            output_dir=str(tmp_path / "output"),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        async with factory() as operator_session:
            operator_job = await operator_session.get(Job, "claim-job")
            assert operator_job is not None
            operator_job.status = "cancelled"
            operator_job.queue_status = "cancelled"
            operator_job.completed_at = datetime.utcnow()
            await operator_session.commit()
        claimed = await _claim_job_for_gpu(session, job, 2, 4096)
        assert claimed is None
    async with factory() as session:
        persisted = await session.get(Job, "claim-job")
        assert persisted is not None
        assert persisted.status == "cancelled"
        assert persisted.queue_status == "cancelled"
        assert persisted.assigned_gpu is None
        assert persisted.params == {"gpu_id": 7}
    await engine.dispose()


@pytest.mark.asyncio
async def test_launch_failure_after_operator_cancellation_preserves_terminal_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'launch-cancel.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add(
            Job(
                id="launch-cancel-job",
                name="launch-cancel-job",
                status="queued",
                queue_status="queued",
                model_id="protein_local_redesign",
                mode="local_redesign",
                params={"gpu_id": 7},
                output_dir=str(tmp_path / "output"),
                vram_estimate_mb=4096,
                pinned_gpu=2,
            )
        )
        await session.commit()

    monkeypatch.setattr(
        gpu_module,
        "read_scheduler_config",
        lambda: {"global": {"enabled": True, "target_vram_fill": 0.9}, "concurrency_limits": {}},
    )

    async def cancel_then_fail(**_kwargs: Any) -> None:
        async with factory() as operator_session:
            operator_job = await operator_session.get(Job, "launch-cancel-job")
            assert operator_job is not None
            operator_job.status = "cancelled"
            operator_job.queue_status = "cancelled"
            operator_job.error_message = "cancelled by operator"
            operator_job.completed_at = datetime.utcnow()
            await operator_session.commit()
        raise RuntimeError("stale launcher failure")

    gpu = SimpleNamespace(
        index=2,
        name="GPU 2",
        memory_used_mb=0,
        memory_total_mb=32607,
        memory_free_mb=32607,
        utilization=0,
        temperature=30,
    )
    orchestrator = GPUOrchestrator(factory, lambda: [gpu], cancel_then_fail)
    await orchestrator._process_cycle()

    async with factory() as session:
        persisted = await session.get(Job, "launch-cancel-job")
        assert persisted is not None
        assert persisted.status == "cancelled"
        assert persisted.queue_status == "cancelled"
        assert persisted.error_message == "cancelled by operator"
        assert persisted.assigned_gpu is None
        assert persisted.params == {"gpu_id": 7}
    await engine.dispose()


def test_recover_rfantibody_parent_after_child_wait_opens_post_rf_gate(tmp_path: Path) -> None:
    for mode in ("antibody_denovo_pipeline", "antibody_refinement_pipeline"):
        parent_output = tmp_path / mode / "parent"
        child_output = tmp_path / mode / "child"
        child_rfa_dir = child_output / "run" / "rfantibody" / "output"
        child_rfa_dir.mkdir(parents=True)
        (child_rfa_dir / "001_backbone.pdb").write_text("MODEL\nEND\n", encoding="utf-8")
        (child_rfa_dir / "001_backbone.trb").write_text("{}", encoding="utf-8")

        job = SimpleNamespace(
            mode=mode,
            output_dir=str(parent_output),
            params={
                "interactive_gating": True,
                "interactive_gate_stage": "post_rfantibody",
                "framework_type": "custom",
                "antibody_chains": "H",
                "structure_validator": "boltz2",
            },
            completed_stages=[],
            stage_outputs={},
            awaiting_input=False,
            awaiting_stage=None,
            awaiting_payload={},
            status="running",
            queue_status="running",
            current_stage="waitforchildren",
            stage_progress="1/4",
            error_message="stale launcher",
            completed_at=None,
            assigned_gpu=0,
        )

        recovered = _recover_rfantibody_parent_after_child_wait(
            job,
            {
                "output_dirs": [str(child_output)],
                "completed": 1,
                "total": 1,
            },
        )

        assert recovered is not None
        assert recovered["opened_gate"] is True
        raw_dir = parent_output / "collected" / "rfantibody_raw"
        assert (raw_dir / "job0_001_backbone.pdb").exists()
        assert (raw_dir / "job0_001_backbone.trb").exists()

        manifest = json.loads((parent_output / "collected" / "rfantibody" / "collection_manifest.json").read_text())
        assert manifest["count"] == 1
        assert manifest["recovered_after_child_wait"] is True

        assert job.completed_stages == ["rfantibody"]
        assert job.stage_outputs["rfantibody"] == [str(raw_dir)]
        assert job.awaiting_input is True
        assert job.awaiting_stage == "post_rfantibody"
        assert job.status == "awaiting_input"
        assert job.queue_status == "completed"
        assert job.current_stage == "post_rfantibody"
        assert job.awaiting_payload["candidate_count"] == 1
        assert Path(parent_output / "gates" / "gate_post_rfantibody.json").exists()


def test_reconcile_terminal_history_without_process_marks_err_history_as_failed() -> None:
    job = SimpleNamespace(
        name="CP resume run",
        status="running",
        queue_status="running",
        error_message=(
            "Reconciled as failed: no active process and no terminal .nextflow/history status "
            "(expected OK/ERR)"
        ),
        completed_at=None,
        awaiting_input=False,
        awaiting_stage=None,
        current_stage="boltz2",
        stage_progress="1/1",
        nextflow_run_id="3153244",
        stage_work_dir="/tmp/workdir",
        output_dir="/tmp/output",
    )

    reconciled = _reconcile_terminal_history_without_process(
        job,
        history_status="ERR",
        gate_present=False,
        age_seconds=301,
        stale_fail_after_seconds=300,
    )

    assert reconciled is True
    assert job.status == "failed"
    assert job.queue_status == "failed"
    assert job.error_message == "Reconciled as failed: terminal .nextflow/history status ERR"
    assert job.completed_at is not None


def test_terminal_nextflow_history_overrides_gpu_activity_liveness_hint() -> None:
    assert _has_terminal_nextflow_history(("OK", "36.6s")) is True
    assert _has_terminal_nextflow_history(("ERR", "4.2s")) is True
    assert _has_terminal_nextflow_history(None) is False


@pytest.mark.parametrize(("model_id", "mode", "requires_designs"), [
    ("protein_local_redesign", "partial_diffusion", False),
    ("boltz2", "predict", True),
])
def test_terminal_history_respects_native_publication_owner(model_id, mode, requires_designs) -> None:
    job = Job(model_id=model_id, mode=mode, params={}, provenance={})

    assert _job_requires_result_output_for_terminal_history(job) is requires_designs


@pytest.mark.asyncio
@pytest.mark.parametrize("new_values", [
    {"status": "cancelled", "queue_status": "cancelled", "awaiting_input": False},
    {"params": {"gpu_id": 2, "new_input": True}},
    {"assigned_gpu": 2},
    {"retry_count": 1, "started_at": datetime(2026, 1, 2)},
    {"status": "running"},
])
async def test_worker_recovery_publish_does_not_overwrite_concurrent_update(tmp_path: Path, new_values) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as seed:
        seed.add(Job(
            id="recovery-race", name="recovery-race", model_id="boltz2", mode="predict",
            params={"gpu_id": 0}, assigned_gpu=0,
            status="running", queue_status="running", awaiting_input=False,
            awaiting_payload={}, retry_count=0, max_retries=2,
        ))
        await seed.commit()

    async with factory() as worker:
        stale_job = (await worker.execute(select(Job).where(Job.id == "recovery-race"))).scalar_one()
        _reconcile_terminal_history_without_process(
            stale_job, history_status="ERR", gate_present=False,
            age_seconds=301, stale_fail_after_seconds=300,
        )
        async with factory() as cancellation:
            await cancellation.execute(
                update(Job).where(Job.id == "recovery-race").values(
                    **new_values,
                )
            )
            await cancellation.commit()

        unchanged = new_values == {"status": "running"}
        assert await _commit_reconciled_job_mutations(worker) == int(unchanged)

    async with factory() as verify:
        final = (await verify.execute(select(Job).where(Job.id == "recovery-race"))).scalar_one()
        if unchanged:
            assert (final.status, final.queue_status) == ("failed", "failed")
        else:
            for key, value in new_values.items():
                assert getattr(final, key) == value
            if "status" not in new_values:
                assert (final.status, final.queue_status) == ("running", "running")
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operator_values", "expected"),
    [
        ({"status": "cancelled", "queue_status": "cancelled", "awaiting_input": False}, ("cancelled", "cancelled", False)),
        ({"status": "awaiting_input", "queue_status": "completed", "awaiting_input": True}, ("awaiting_input", "completed", True)),
    ],
)
async def test_worker_loop_boundary_publishes_recovery_before_next_job_db_work(
    tmp_path: Path, operator_values: dict[str, object], expected: tuple[str, str, bool]
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'loop-boundary.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as seed:
        for job_id in ("stale-a", "next-b"):
            seed.add(Job(
                id=job_id, name=job_id, model_id="boltz2", mode="predict", params={},
                status="running", queue_status="running", awaiting_input=False,
                awaiting_payload={}, retry_count=0, max_retries=2,
            ))
        await seed.commit()

    async with factory() as worker:
        stale = (await worker.execute(select(Job).where(Job.id == "stale-a"))).scalar_one()
        _reconcile_terminal_history_without_process(
            stale, history_status="ERR", gate_present=False,
            age_seconds=301, stale_fail_after_seconds=300,
        )
        async with factory() as operator:
            await operator.execute(update(Job).where(Job.id == "stale-a").values(**operator_values))
            await operator.commit()

        # This is the completion loop's next-job boundary.  A subsequent finalizer
        # starts with DB work (refresh/select); it must not autoflush stale-a first.
        assert await _commit_reconciled_job_mutations(worker) == 0
        assert (await worker.execute(select(Job).where(Job.id == "next-b"))).scalar_one().id == "next-b"

    async with factory() as verify:
        final = (await verify.execute(select(Job).where(Job.id == "stale-a"))).scalar_one()
        assert (final.status, final.queue_status, final.awaiting_input) == expected
    await engine.dispose()


@pytest.mark.asyncio
async def test_generic_completion_does_not_publish_over_active_md_parent(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'md-parent.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as seed:
        seed.add(Job(
            id="md-parent", name="md-parent", model_id="molecular_dynamics",
            mode="simulate", params={}, status="running", queue_status="running",
            awaiting_input=False, awaiting_payload={}, retry_count=0, max_retries=0,
        ))
        seed.add(MdRun(
            job_id="md-parent", normalized_request={}, request_sha256="a" * 64,
            phase="replicas_running", state_version=1, chemistry_profile_id="test",
            chemistry_profile_sha256="b" * 64, chemistry_assurance="test",
        ))
        await seed.commit()

    async with factory() as worker:
        parent = (await worker.execute(select(Job).where(Job.id == "md-parent"))).scalar_one()
        _reconcile_terminal_history_without_process(
            parent, history_status="ERR", gate_present=False,
            age_seconds=301, stale_fail_after_seconds=300,
        )
        assert await _commit_reconciled_job_mutations(worker) == 0

    async with factory() as verify:
        parent = (await verify.execute(select(Job).where(Job.id == "md-parent"))).scalar_one()
        assert (parent.status, parent.queue_status) == ("running", "running")
    await engine.dispose()
