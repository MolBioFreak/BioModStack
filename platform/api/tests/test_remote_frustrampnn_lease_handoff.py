"""Offline SQLite lease interleavings; no worker/network/GPU execution.

Run only after the integration owner opens the coordinated test gate.
"""
import asyncio
import copy
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, ExecutionTarget, Job
from services.gpu_orchestrator import _claim_remote_job
from services.remote_execution import executor as ex
from services.remote_execution.fanout_lease import (
    HANDOFF_KEY, authorized_parent, recover_terminal_leases, release_lease,
)
from services.structure_dataset_fanout import (
    FANOUT_CAPABILITY_CONSUMED_KEY, FANOUT_PROVENANCE_KEY, FANOUT_SCHEMA,
    StructureDatasetMember, _canonical_bytes, _child_id, _fanout_plan,
)
import hashlib


@pytest_asyncio.fixture
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'handoff.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    members = [StructureDatasetMember(f"candidate-{i}", {"source_job_id": "parent"}, None)
               for i in range(5)]
    plan = _fanout_plan(workflow_id="structure_prediction.frustrampnn.v1", parent_job_id="parent",
                        members=members, batching_enabled=False, structures_per_job=25,
                        request_identity={"trigger": "parent_workflow_terminal_dataset",
                                          "requested_settings": {"batching_enabled": False,
                                                                 "structures_per_job": 25}})
    fanout_id = hashlib.sha256(_canonical_bytes(plan)).hexdigest()
    ids = [_child_id(fanout_id, i) for i in range(5)]
    common = dict(execution_target_id="target", execution_source_revision="revision",
                  execution_source_tree="tree", paused=False)
    async with factory() as session:
        session.add(ExecutionTarget(
            id="target", provider="vast", provider_instance_id="1", active=True, state="ready",
            leased_job_id="parent", lease_acquired_at=datetime.utcnow(),
            provider_metadata={"inventory": {"status": "complete", "present": True,
                "running": True, "checked_at": datetime.utcnow().isoformat()}},
        ))
        session.add(Job(id="parent", name="parent", model_id="protenix", mode="predict", params={},
            status="running", queue_status="running", remote_state="running", assigned_gpu=0,
            remote_attempt_id="parent-attempt", nextflow_run_id="remote:parent-attempt",
            provenance={"workflow_stage_report_token_sha256": "a" * 64,
                FANOUT_CAPABILITY_CONSUMED_KEY: "a" * 64,
                FANOUT_PROVENANCE_KEY: {fanout_id: {"schema_name": FANOUT_SCHEMA,
                    "schema_version": 1, "plan": plan, "child_job_ids": ids}}}, **common))
        for i, child_id in enumerate(ids):
            session.add(Job(id=child_id, name=child_id, model_id="frustrampnn", mode="analyze",
                child_stage="frustrampnn", parent_job_id="parent", status="queued", queue_status="queued",
                params={"_frustrampnn_child_v1": {"schema_name": "bms.frustrampnn.scheduler-child.v1",
                    "execution_owner_job_id": child_id, "source_parent_job_id": "parent",
                    "trigger": "parent_workflow_terminal_dataset"}},
                provenance={FANOUT_PROVENANCE_KEY: {"schema_name": FANOUT_SCHEMA, "schema_version": 1,
                    "fanout_id": fanout_id, "parent_job_id": "parent", "batch_ordinal": i,
                    "structure_ids": [members[i].structure_id], "member_lineage": [dict(members[i].lineage)]}},
                **common))
        session.add(Job(id="unrelated", name="unrelated", model_id="boltz2", mode="predict",
                        status="queued", queue_status="queued", params={}, **common))
        await session.commit()
    yield factory, ids
    await engine.dispose()


async def claim(factory, job_id):
    async with factory() as session:
        job = await session.get(Job, job_id)
        return await _claim_remote_job(session, job, gpu_id=0, gpu_ids=[0], vram_estimate_mb=4096)


async def owner(factory):
    async with factory() as session:
        return (await session.get(ExecutionTarget, "target")).leased_job_id


async def finish(factory, child_id, state="completed", release=True):
    async with factory() as session:
        child = await session.get(Job, child_id)
        assert await ex._publish_remote_transition(session, child, {
            "status": state, "queue_status": state, "assigned_gpu": None,
        }, release_lease=release)


@pytest.mark.asyncio
async def test_five_siblings_progress_serially_without_local_or_unrelated_work(store):
    factory, ids = store
    for child_id in ids:
        assert await claim(factory, "unrelated") is None
        assert await claim(factory, child_id) is not None
        assert await owner(factory) == child_id
        for sibling in ids:
            if sibling != child_id:
                assert await claim(factory, sibling) is None
        assert await claim(factory, "unrelated") is None
        async with factory() as session:
            parent = await session.get(Job, "parent")
            child = await session.get(Job, child_id)
            assert parent.status == parent.queue_status == "running"
            assert child.execution_target_id == "target"
            assert child.provenance["remote_execution_assignment"][HANDOFF_KEY]["parent_job_id"] == "parent"
            assert await ex._publish_remote_transition(session, parent, {"remote_state": "running"})
        await finish(factory, child_id)
        assert await owner(factory) == "parent"
    async with factory() as session:
        parent = await session.get(Job, "parent")
        assert await ex._publish_remote_transition(session, parent, {
            "status": "completed", "queue_status": "completed"}, release_lease=True)
    assert await owner(factory) is None
    assert await claim(factory, "unrelated") is not None


@pytest.mark.asyncio
async def test_scheduler_inherits_remote_target_and_launches_one_child_per_cycle(store, monkeypatch):
    factory, ids = store
    import services.gpu_orchestrator as gpu
    from services.remote_execution import targets

    monkeypatch.setattr(gpu, "read_scheduler_config", lambda: {
        "global": {"enabled": True, "target_vram_fill": 0.9}, "concurrency_limits": {},
    })

    async def telemetry(_target):
        return {"available": True, "gpus": [{"index": 0, "memory_total_mb": 16384,
                                             "memory_used_mb": 0, "uuid": "remote-gpu"}]}

    monkeypatch.setattr(targets, "remote_target_telemetry", telemetry)
    async with factory() as session:
        target = await session.get(ExecutionTarget, "target")
        target.capabilities = {"gpu_count": 1, "gpu_vram_mb": 16384}
        await session.execute(update(Job).where(Job.id.in_(ids)).values(
            execution_target_id=None, execution_source_revision=None, execution_source_tree=None,
            vram_estimate_mb=4096,
        ))
        await session.commit()
    launched = []

    async def launch(**kwargs):
        async with factory() as session:
            job = await session.get(Job, kwargs["job_id"])
            assert job.execution_target_id == "target"
            assert job.execution_source_revision == "revision" and job.execution_source_tree == "tree"
            assert job.queue_status == "preparing" and job.id in ids
        launched.append(kwargs["job_id"])

    def no_local_gpu():
        raise AssertionError("remote descendants must never enter local GPU scheduling")

    orchestrator = gpu.GPUOrchestrator(factory, no_local_gpu, launch)
    for index in range(5):
        await orchestrator._process_cycle()
        assert len(launched) == index + 1 and len(set(launched)) == index + 1
        assert await owner(factory) == launched[-1]
        await finish(factory, launched[-1])
        assert await owner(factory) == "parent"
    assert set(launched) == set(ids)


@pytest.mark.asyncio
async def test_independent_concurrent_sibling_claims_have_one_winner(store):
    factory, ids = store
    claims = await asyncio.gather(*(claim(factory, child_id) for child_id in ids),
                                  claim(factory, "unrelated"))
    assert sum(value is not None for value in claims) == 1
    assert claims[-1] is None
    async with factory() as session:
        jobs = (await session.execute(select(Job).where(Job.queue_status == "preparing"))).scalars().all()
        assert len(jobs) == 1 and jobs[0].id == await owner(factory)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["completed", "failed", "cancelled"])
async def test_terminal_child_handback_and_crash_recovery(store, state):
    factory, ids = store
    assert await claim(factory, ids[0]) is not None
    await finish(factory, ids[0], state, release=False)
    assert await owner(factory) == ids[0]
    async with factory() as session:
        await recover_terminal_leases(session)
        await session.commit()
    assert await owner(factory) == "parent"
    assert await claim(factory, ids[1]) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["failed", "cancelled"])
async def test_parent_terminal_while_child_active_preserves_child_owner(store, state):
    factory, ids = store
    assert await claim(factory, ids[0]) is not None
    async with factory() as session:
        parent = await session.get(Job, "parent")
        assert await ex._publish_remote_transition(session, parent, {
            "status": state, "queue_status": state}, release_lease=True)
        await recover_terminal_leases(session)
        await session.commit()
    assert await owner(factory) == ids[0]
    assert await claim(factory, ids[1]) is None
    assert await claim(factory, "unrelated") is None
    await finish(factory, ids[0], "cancelled")
    assert await owner(factory) is None
    assert await claim(factory, ids[1]) is None  # cannot escape a dead parent onto an idle target
    assert await claim(factory, "unrelated") is not None


@pytest.mark.asyncio
async def test_lineage_cancel_keeps_lease_until_all_remote_units_confirm_stop(store, monkeypatch):
    factory, ids = store
    from fastapi import HTTPException
    from services import job_control

    assert await claim(factory, ids[0]) is not None
    async with factory() as session:
        child = await session.get(Job, ids[0])
        child.status = child.queue_status = "running"
        child.remote_attempt_id = "child-attempt"
        child.nextflow_run_id = "remote:child-attempt"
        await session.commit()
    stopped = False
    calls = []

    async def stop(run_id):
        calls.append(run_id)
        return stopped or run_id == "remote:parent-attempt"

    monkeypatch.setattr(job_control, "cancel_nextflow_job", stop)
    async with factory() as session:
        with pytest.raises(HTTPException) as error:
            await job_control.cancel_job_lineage("parent", session)
        assert error.value.status_code == 409
    assert set(calls) == {"remote:parent-attempt", "remote:child-attempt"}
    assert await owner(factory) == ids[0]
    assert await claim(factory, "unrelated") is None
    stopped = True
    async with factory() as session:
        await job_control.cancel_job_lineage("parent", session)
    assert await owner(factory) is None
    async with factory() as session:
        jobs = (await session.execute(select(Job).where(Job.id.in_(["parent", *ids])))).scalars().all()
        assert all(job.status == job.queue_status == "cancelled" for job in jobs)


@pytest.mark.asyncio
async def test_cancelling_parent_gets_lease_back_until_its_process_is_stopped(store):
    factory, ids = store
    assert await claim(factory, ids[0]) is not None
    async with factory() as session:
        await session.execute(update(Job).where(Job.id == "parent").values(queue_status="cancelling"))
        await session.commit()
    await finish(factory, ids[0], "cancelled")
    assert await owner(factory) == "parent"
    assert await claim(factory, "unrelated") is None
    async with factory() as session:
        parent = await session.get(Job, "parent")
        assert await ex._finish_remote_cancellation(session, parent)
    assert await owner(factory) is None


@pytest.mark.asyncio
async def test_parent_cancellation_during_child_staging_fences_start(store):
    factory, ids = store
    assert await claim(factory, ids[0]) is not None
    async with factory() as session:
        await session.execute(update(Job).where(Job.id == "parent").values(queue_status="cancelling"))
        await session.commit()
    async with factory() as session:
        child = await session.get(Job, ids[0])
        assert not await ex._publish_remote_transition(session, child, {"remote_state": "launch_requested"})
    assert await owner(factory) == ids[0]
    async with factory() as session:
        child = await session.get(Job, ids[0])
        assert await ex.fail_remote_prestart(session, child, "parent cancelled before launch")
    assert await owner(factory) == "parent"


@pytest.mark.asyncio
async def test_parent_cannot_resume_while_loaned_and_successor_lease_cannot_be_released(store):
    factory, ids = store
    assert await claim(factory, ids[0]) is not None
    async with factory() as session:
        parent = await session.get(Job, "parent")
        assert not await ex._publish_remote_transition(session, parent, {"remote_state": "launch_requested"})
    async with factory() as session:
        child = await session.get(Job, ids[0])
        await session.execute(update(ExecutionTarget).where(ExecutionTarget.id == "target").values(
            leased_job_id="successor"))
        await release_lease(session, child)
        await session.commit()
        assert not await ex._publish_remote_transition(session, child, {"remote_state": "running"})
    assert await owner(factory) == "successor"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["bare_parent", "trigger", "capability", "source", "target", "fanout", "cancel"])
async def test_forged_or_drifted_lineage_is_not_lease_authority(store, mutation):
    factory, ids = store
    async with factory() as session:
        child = await session.get(Job, ids[0])
        parent = await session.get(Job, "parent")
        assert authorized_parent(child, parent)
        if mutation == "bare_parent":
            child.provenance = {}
        elif mutation == "trigger":
            params = copy.deepcopy(child.params)
            params["_frustrampnn_child_v1"]["trigger"] = "design_analyze"
            child.params = params
        elif mutation == "capability":
            parent.provenance = {k: v for k, v in parent.provenance.items() if k != FANOUT_CAPABILITY_CONSUMED_KEY}
        elif mutation == "source":
            child.execution_source_tree = "drift"
        elif mutation == "target":
            child.execution_target_id = "other-target"
        elif mutation == "fanout":
            provenance = copy.deepcopy(child.provenance)
            provenance[FANOUT_PROVENANCE_KEY]["batch_ordinal"] = 3
            child.provenance = provenance
        else:
            parent.queue_status = "cancelling"
        assert not authorized_parent(child, parent)
        await session.commit()
    assert await claim(factory, ids[0]) is None
    assert await owner(factory) == "parent"


@pytest.mark.asyncio
async def test_parent_cancellation_after_admission_read_wins_atomic_claim(store, monkeypatch):
    factory, ids = store
    from services.remote_execution import fanout_lease
    original = fanout_lease.lending_parent

    async def cancel_after_read(session, child):
        parent = await original(session, child)
        # Independent committed writer between Python validation and lease CAS.
        async with factory() as other:
            await other.execute(update(Job).where(Job.id == "parent").values(queue_status="cancelling"))
            await other.commit()
        return parent

    monkeypatch.setattr(fanout_lease, "lending_parent", cancel_after_read)
    assert await claim(factory, ids[0]) is None
    assert await owner(factory) == "parent"
