from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import Base, Job, JobArtifact, MdReplicaRun, MdRun
from services.job_control import reject_generic_md_lifecycle_control
from services.md.read_model import md_run_snapshot
from services.md.state import (
    MdStateError, accept_checkpoint, append_event_cas, create_md_run,
    create_replica_attempt, finalize_cancel, finalize_pause, request_cancel,
    request_pause, resume_run, retry_replica_attempt,
)


def _request() -> dict:
    return {
        "schema": "bms.md.job.v2",
        "chemistry": {
            "profile_id": "amber_ff19sb_opc_protein_v1",
            "profile_sha256": "a" * 64,
            "assurance": "curated_profile",
        },
    }


@pytest_asyncio.fixture
async def session(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as value:
        yield value
    await engine.dispose()


@pytest.mark.asyncio
async def test_pause_requires_terminal_process_observation_and_accepted_checkpoint(session) -> None:
    job = Job(id="md-parent", name="MD", status="running", model_id="md", mode="molecular_dynamics", params={})
    child = Job(
        id="md-parent-replica-0", name="MD replica 0", status="running", model_id="md",
        mode="molecular_dynamics", params={}, parent_job_id=job.id, child_stage="md_replica",
    )
    session.add_all([job, child]); await session.flush()
    run = await create_md_run(session, job=job, normalized_request=_request())
    run.phase = "replicas_running"
    replica, segment = await create_replica_attempt(
        session, job_id=job.id, replica_index=0, attempt=0, engine="gromacs",
        execution_plan_sha256="b" * 64, compatibility_key="c" * 64,
        child_job_id=child.id,
    )
    replica.state = "running"; await session.flush()

    run = await request_pause(session, job_id=job.id, expected_version=0, idempotency_key="pause:1")
    assert run.phase == "checkpointing" and run.controls_blocked is True and run.state_version == 1
    with pytest.raises(MdStateError, match="targeted descendants") as blocked:
        await finalize_pause(session, job_id=job.id, expected_version=1, idempotency_key="pause-done:1")
    assert blocked.value.code == "MD_PAUSE_INCOMPLETE"

    replica.state = "paused"
    checkpoint = await accept_checkpoint(
        session, segment_id=segment.id, logical_role="continuation", relative_path="replica_0/state.cpt",
        sha256="d" * 64, bytes_=1024, step=5000, time_ps=10.0, compatibility_key="c" * 64,
    )
    session.add(JobArtifact(
        id="md-parent-checkpoint-artifact", owner_job_id=child.id, attempt=0,
        logical_path=checkpoint.relative_path, storage_path=checkpoint.relative_path,
        sha256=checkpoint.sha256, bytes=checkpoint.bytes, media_type="application/octet-stream",
        provenance={
            "schema": "bms.md.artifact-provenance.v1", "md_job_id": job.id,
            "replica_run_id": replica.id, "segment_id": segment.id,
            "checkpoint_id": checkpoint.id, "semantic_role": "checkpoint", "sources": [],
        },
    ))
    await session.flush()
    run = await finalize_pause(session, job_id=job.id, expected_version=1, idempotency_key="pause-done:1")
    assert run.phase == "paused" and run.controls_blocked is False and run.state_version == 2
    finalized_replay = await finalize_pause(
        session, job_id=job.id, expected_version=1, idempotency_key="pause-done:1",
    )
    assert finalized_replay.phase == "paused" and finalized_replay.state_version == 2

    continuations = await resume_run(
        session, job_id=job.id, expected_version=2, idempotency_key="resume:1",
    )
    continuation = continuations[0]
    assert continuation.segment_index == 1
    assert continuation.source_checkpoint_id == checkpoint.id
    assert continuation.start_step == 5000 and continuation.start_time_ps == 10.0
    assert run.phase == "replicas_queued" and run.state_version == 3
    assert child.status == "queued" and child.queue_status == "queued" and child.paused is False
    assert child.params["md_resume_checkpoint"] == checkpoint.relative_path
    assert child.params["md_resume_checkpoint_sha256"] == checkpoint.sha256
    assert child.params["md_resume_output_dir"] == "replica_0"
    assert child.params["md_resume_segment_id"] == continuation.id
    assert job.status == "running" and job.queue_status == "running" and job.paused is False
    replayed = await resume_run(
        session, job_id=job.id, expected_version=2, idempotency_key="resume:1",
    )
    assert [item.id for item in replayed] == [continuation.id]
    with pytest.raises(MdStateError) as wrong_version:
        await resume_run(
            session, job_id=job.id, expected_version=999, idempotency_key="resume:1",
        )
    assert wrong_version.value.code == "MD_IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_retry_dynamics_creates_new_attempt_without_mutating_failed_attempt(session) -> None:
    job = Job(id="md-retry", name="MD", status="failed", model_id="md", mode="molecular_dynamics", params={})
    session.add(job); await session.flush()
    run = await create_md_run(session, job=job, normalized_request=_request())
    failed_child = Job(
        id="md-retry-child-0", name="MD replica 1", status="failed",
        model_id="molecular_dynamics", mode="replica",
        params={"md_replica_index": 0, "md_attempt": 0, "lineage_root_job_id": job.id},
        parent_job_id=job.id, batch_id=job.id, child_stage="md_replica",
    )
    session.add(failed_child); await session.flush()
    failed, failed_segment = await create_replica_attempt(
        session, job_id=job.id, replica_index=0, attempt=0, engine="gromacs",
        execution_plan_sha256="b" * 64, compatibility_key="c" * 64,
        child_job_id=failed_child.id,
    )
    failed.state = "failed"; failed.active = False
    failed.failure = {"code": "worker_lost"}; run.phase = "failed"
    await session.flush()

    before_retry = await md_run_snapshot(session, job.id)
    assert before_retry is not None and "retry_dynamics" in before_retry["allowed_actions"]

    retried = await retry_replica_attempt(
        session, job_id=job.id, replica_index=0, expected_version=0,
        idempotency_key="retry:0:1",
    )
    await session.flush()

    assert retried.attempt == 1 and retried.active is True and retried.state == "queued"
    retry_child = await session.get(Job, retried.child_job_id)
    assert retry_child is not None and retry_child.status == "queued"
    assert retry_child.parent_job_id == job.id and retry_child.params["md_attempt"] == 1
    assert failed.state == "failed" and failed.failure == {"code": "worker_lost"}
    assert failed_segment.execution_plan_sha256 == "b" * 64
    replay = await retry_replica_attempt(
        session, job_id=job.id, replica_index=0, expected_version=0,
        idempotency_key="retry:0:1",
    )
    assert replay.id == retried.id
    attempts = list((await session.scalars(
        __import__("sqlalchemy").select(MdReplicaRun).where(MdReplicaRun.md_job_id == job.id)
    )).all())
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_retry_dynamics_rejects_scientific_failures(session) -> None:
    parent = Job(id="md-science-fail", name="MD", status="failed", model_id="md", mode="molecular_dynamics", params={})
    child = Job(id="md-science-child", name="replica", status="failed", model_id="md", mode="replica", params={"md_attempt": 0})
    session.add_all([parent, child]); await session.flush()
    run = await create_md_run(session, job=parent, normalized_request=_request())
    replica, _ = await create_replica_attempt(
        session, job_id=parent.id, replica_index=0, attempt=0, engine="gromacs",
        execution_plan_sha256="b" * 64, compatibility_key="c" * 64,
        child_job_id=child.id,
    )
    replica.active = False; replica.state = "failed"
    replica.failure = {"code": "nan_detected"}; run.phase = "failed"
    await session.flush()
    with pytest.raises(MdStateError) as rejected:
        await retry_replica_attempt(
            session, job_id=parent.id, replica_index=0, expected_version=0,
            idempotency_key="retry-scientific",
        )
    assert rejected.value.code == "MD_RETRY_REVIEW_REQUIRED"


@pytest.mark.asyncio
async def test_cas_idempotency_and_cancel_barrier(session) -> None:
    job = Job(id="md-cancel", name="MD", status="running", model_id="md", mode="molecular_dynamics", params={})
    session.add(job); await session.flush()
    run = await create_md_run(session, job=job, normalized_request=_request())
    run.phase = "replicas_running"
    replica, _ = await create_replica_attempt(
        session, job_id=job.id, replica_index=0, attempt=0, engine="gromacs",
        execution_plan_sha256="b" * 64, compatibility_key="c" * 64,
    )
    cancelled = await request_cancel(session, job_id=job.id, expected_version=0, idempotency_key="cancel:1")
    assert cancelled.phase == "cancelling" and cancelled.state_version == 1
    replay = await request_cancel(session, job_id=job.id, expected_version=0, idempotency_key="cancel:1")
    assert replay.state_version == 1
    with pytest.raises(MdStateError) as incomplete:
        await finalize_cancel(session, job_id=job.id, expected_version=1, idempotency_key="cancel-done:1")
    assert incomplete.value.code == "MD_CANCEL_INCOMPLETE"
    replica.state = "cancelled"; replica.active = False
    terminal = await finalize_cancel(session, job_id=job.id, expected_version=1, idempotency_key="cancel-done:1")
    assert terminal.phase == "cancelled" and job.status == "cancelled"


@pytest.mark.asyncio
async def test_stale_state_version_fails_closed(session) -> None:
    job = Job(id="md-cas", name="MD", status="running", model_id="md", mode="molecular_dynamics", params={})
    session.add(job); await session.flush(); await create_md_run(session, job=job, normalized_request=_request())
    await append_event_cas(session, job_id=job.id, idempotency_key="one", event_type="observed",
                           expected_version=0, next_phase="preparing")
    with pytest.raises(MdStateError) as stale:
        await append_event_cas(session, job_id=job.id, idempotency_key="two", event_type="observed",
                               expected_version=0, next_phase="replicas_queued")
    assert stale.value.code == "MD_STATE_VERSION_CONFLICT"


@pytest.mark.asyncio
async def test_resume_and_generic_controls_reject_unbacked_durable_checkpoint(session) -> None:
    parent = Job(id="md-unbacked", name="MD", status="paused", model_id="md", mode="molecular_dynamics", params={})
    child = Job(
        id="md-unbacked-child", name="replica", status="paused", model_id="md", mode="replica", params={},
        parent_job_id=parent.id, child_stage="md_replica",
    )
    session.add_all([parent, child]); await session.flush()
    run = await create_md_run(session, job=parent, normalized_request=_request())
    replica, segment = await create_replica_attempt(
        session, job_id=parent.id, replica_index=0, attempt=0, engine="gromacs",
        execution_plan_sha256="b" * 64, compatibility_key="c" * 64, child_job_id=child.id,
    )
    checkpoint = await accept_checkpoint(
        session, segment_id=segment.id, logical_role="continuation", relative_path="replica/state.cpt",
        sha256="d" * 64, bytes_=1024, step=5000, time_ps=10.0, compatibility_key="c" * 64,
    )
    run.phase = "paused"; run.controls_blocked = False; replica.state = "paused"
    await session.flush()

    with pytest.raises(MdStateError) as blocked:
        await resume_run(
            session, job_id=parent.id, expected_version=0, idempotency_key="resume-unbacked",
        )
    assert blocked.value.code == "MD_RESUME_CHECKPOINT_UNVERIFIED"
    assert checkpoint.accepted is True

    for target in (parent.id, child.id):
        with pytest.raises(HTTPException) as generic:
            await reject_generic_md_lifecycle_control(target, session)
        assert generic.value.status_code == 409
        assert generic.value.detail["code"] == "MD_LIFECYCLE_CONTROL_REQUIRED"


@pytest.mark.asyncio
async def test_finalize_pause_rejects_wrong_phase(session) -> None:
    job = Job(id="md-wrong-phase", name="MD", status="running", model_id="md", mode="molecular_dynamics", params={})
    session.add(job); await session.flush()
    run = await create_md_run(session, job=job, normalized_request=_request())
    run.phase = "replicas_running"
    with pytest.raises(MdStateError) as invalid:
        await finalize_pause(
            session, job_id=job.id, expected_version=0, idempotency_key="wrong-phase",
        )
    assert invalid.value.code == "MD_PAUSE_TRANSITION_INVALID"


@pytest.mark.asyncio
async def test_event_idempotency_key_cannot_cross_run_or_operation(session) -> None:
    jobs = [
        Job(id="md-event-a", name="A", status="running", model_id="md", mode="molecular_dynamics", params={}),
        Job(id="md-event-b", name="B", status="running", model_id="md", mode="molecular_dynamics", params={}),
    ]
    session.add_all(jobs); await session.flush()
    for job in jobs:
        await create_md_run(session, job=job, normalized_request=_request())
    await append_event_cas(
        session, job_id=jobs[0].id, idempotency_key="global-key", event_type="observed",
        expected_version=0, next_phase="preparing",
    )
    with pytest.raises(MdStateError) as collision:
        await append_event_cas(
            session, job_id=jobs[1].id, idempotency_key="global-key", event_type="observed",
            expected_version=0, next_phase="preparing",
        )
    assert collision.value.code == "MD_IDEMPOTENCY_CONFLICT"
    with pytest.raises(MdStateError) as operation:
        await append_event_cas(
            session, job_id=jobs[0].id, idempotency_key="global-key", event_type="different",
            expected_version=0, next_phase="preparing",
        )
    assert operation.value.code == "MD_IDEMPOTENCY_CONFLICT"
    run_b = await session.get(MdRun, jobs[1].id)
    assert run_b is not None
    run_b.phase = "paused"
    with pytest.raises(MdStateError) as paused_fast_path:
        await request_pause(
            session, job_id=jobs[1].id, expected_version=0, idempotency_key="global-key",
        )
    assert paused_fast_path.value.code == "MD_IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_mixed_active_replica_states_do_not_advertise_or_partially_resume(session) -> None:
    parent = Job(id="md-mixed", name="MD", status="paused", model_id="md", mode="molecular_dynamics", params={})
    children = [
        Job(id=f"md-mixed-child-{index}", name=f"replica {index}", status="paused", queue_status="paused",
            paused=True, model_id="md", mode="replica", params={}, parent_job_id=parent.id, child_stage="md_replica")
        for index in range(2)
    ]
    session.add_all([parent, *children]); await session.flush()
    run = await create_md_run(session, job=parent, normalized_request=_request())
    replicas = []
    segments = []
    for index, child in enumerate(children):
        replica, segment = await create_replica_attempt(
            session, job_id=parent.id, replica_index=index, attempt=0, engine="gromacs",
            execution_plan_sha256="b" * 64, compatibility_key="c" * 64, child_job_id=child.id,
        )
        replicas.append(replica); segments.append(segment)
    run.phase = "paused"; run.controls_blocked = False
    replicas[0].state = "paused"; replicas[1].state = "running"
    checkpoint = await accept_checkpoint(
        session, segment_id=segments[0].id, logical_role="continuation", relative_path="replica_0/state.cpt",
        sha256="d" * 64, bytes_=1024, step=5000, time_ps=10.0, compatibility_key="c" * 64,
    )
    session.add(JobArtifact(
        id="md-mixed-checkpoint", owner_job_id=children[0].id, attempt=0,
        logical_path=checkpoint.relative_path, storage_path=checkpoint.relative_path,
        sha256=checkpoint.sha256, bytes=checkpoint.bytes, media_type="application/octet-stream",
        provenance={"schema": "bms.md.artifact-provenance.v1", "md_job_id": parent.id,
                    "replica_run_id": replicas[0].id, "segment_id": segments[0].id,
                    "checkpoint_id": checkpoint.id, "semantic_role": "checkpoint", "sources": []},
    ))
    await session.flush()

    snapshot = await md_run_snapshot(session, parent.id)
    assert snapshot is not None and snapshot["checkpoint_available"] is False
    assert "resume_dynamics" not in snapshot["allowed_actions"]
    with pytest.raises(MdStateError) as blocked:
        await resume_run(session, job_id=parent.id, expected_version=0, idempotency_key="mixed-resume")
    assert blocked.value.code == "MD_RESUME_BARRIER_INCOMPLETE"
    assert replicas[0].state == "paused" and replicas[1].state == "running"
