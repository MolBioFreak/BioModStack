from __future__ import annotations

import hashlib
import json
import asyncio
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import Base, Job, JobArtifact, MdAttemptSegment, MdCheckpoint, MdReplicaRun, MdRun
from services.md.pause_actuator import (
    _checkpoint_receipt,
    _checkpoint_roots,
    _pause_boundary,
    _snapshot_checkpoint,
    _wait_for_receipt,
    pause_running_md_run,
)
from services.md.state import create_md_run, create_replica_attempt, resume_run


@pytest_asyncio.fixture
async def session(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pause.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as value:
        yield value
    await engine.dispose()


def _request() -> dict:
    return {
        "schema": "bms.md.job.v2",
        "chemistry": {
            "profile_id": "amber_ff19sb_opc_protein_v1",
            "profile_sha256": "a" * 64,
            "assurance": "curated_profile",
        },
    }


@pytest.mark.asyncio
async def test_pause_running_md_run_stops_worker_and_persists_exact_checkpoint(session, tmp_path) -> None:
    parent = Job(
        id="pause-parent", name="MD", status="running", queue_status="running",
        model_id="molecular_dynamics", mode="molecular_dynamics", params={}, assigned_gpu=0,
    )
    work_dir = tmp_path / "work"
    checkpoint = work_dir / "replica_0" / "production" / "production.cpt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"gromacs-checkpoint")
    digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    child = Job(
        id="pause-child", name="replica", status="running", queue_status="running",
        model_id="molecular_dynamics", mode="replica", params={}, parent_job_id=parent.id,
        child_stage="md_replica", assigned_gpu=1, nextflow_run_id="4242",
        stage_work_dir=str(work_dir),
    )
    session.add_all([parent, child])
    await session.flush()
    run = await create_md_run(session, job=parent, normalized_request=_request())
    run.phase = "replicas_running"
    replica, segment = await create_replica_attempt(
        session, job_id=parent.id, child_job_id=child.id, replica_index=0, attempt=0,
        engine="gromacs", execution_plan_sha256="b" * 64, compatibility_key="c" * 64,
    )
    replica.state = "running"
    segment.state = "running"
    receipt = {
        "schema": "bms.md.checkpoint-receipt.v1",
        "checkpoint_path": "production/production.cpt",
        "sha256": digest,
        "bytes": checkpoint.stat().st_size,
        "step": 2500,
        "time_ps": 5.0,
        "execution_plan_sha256": "b" * 64,
        "compatibility_key": "c" * 64,
    }
    receipt_path = work_dir / "replica_0" / "md-checkpoint-receipt.json"
    stale_checkpoint = checkpoint.with_name("old.cpt")
    stale_checkpoint.write_bytes(b"stale-checkpoint")
    stale_receipt = {
        **receipt,
        "checkpoint_path": "production/old.cpt",
        "sha256": hashlib.sha256(stale_checkpoint.read_bytes()).hexdigest(),
        "bytes": stale_checkpoint.stat().st_size,
        "step": 1000,
        "time_ps": 2.0,
    }
    receipt_path.write_text(json.dumps(stale_receipt), encoding="utf-8")
    await session.commit()

    cancelled: list[str] = []

    async def cancel(run_id: str) -> bool:
        cancelled.append(run_id)

        async def publish_current_receipt() -> None:
            await asyncio.sleep(0.05)
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

        asyncio.create_task(publish_current_receipt())
        return True

    paused = await pause_running_md_run(
        session, job_id=parent.id, expected_version=0,
        idempotency_key="pause:one", cancel_worker=cancel,
    )
    await session.commit()

    assert cancelled == ["4242"]
    assert paused.phase == "paused" and paused.controls_blocked is False
    assert replica.state == "paused" and segment.state == "paused"
    assert child.status == "paused" and child.queue_status == "paused"
    assert child.paused is True and child.assigned_gpu is None
    assert parent.status == "paused" and parent.queue_status == "paused"
    assert parent.paused is True and parent.assigned_gpu is None

    checkpoint_row = await session.scalar(select(MdCheckpoint))
    artifact = await session.scalar(select(JobArtifact))
    assert checkpoint_row is not None and artifact is not None
    assert checkpoint_row.segment_id == segment.id
    assert checkpoint_row.step == 2500 and checkpoint_row.time_ps == 5.0
    assert artifact.owner_job_id == child.id
    assert artifact.sha256 == digest and artifact.bytes == checkpoint.stat().st_size
    durable_checkpoint = Path(artifact.storage_path)
    assert durable_checkpoint != checkpoint
    assert durable_checkpoint.read_bytes() == b"gromacs-checkpoint"
    assert checkpoint_row.relative_path.startswith(f".bms-checkpoints/{segment.id}/")
    checkpoint.write_bytes(b"continued-run-overwrite")
    assert durable_checkpoint.read_bytes() == b"gromacs-checkpoint"
    assert artifact.provenance == {
        "schema": "bms.md.artifact-provenance.v1",
        "md_job_id": parent.id,
        "replica_run_id": replica.id,
        "segment_id": segment.id,
        "checkpoint_id": checkpoint_row.id,
        "semantic_role": "checkpoint",
        "replica_output_dir": str(work_dir / "replica_0"),
        "sources": [],
    }

    continuations = await resume_run(
        session,
        job_id=parent.id,
        expected_version=paused.state_version,
        idempotency_key="resume:one",
    )
    assert len(continuations) == 1
    assert child.params["md_resume_checkpoint"] == str(durable_checkpoint)
    assert child.params["md_resume_checkpoint_sha256"] == digest
    assert child.params["md_resume_output_dir"] == str(work_dir / "replica_0")


@pytest.mark.asyncio
async def test_pause_running_md_run_keeps_checkpointing_when_worker_does_not_stop(session, tmp_path) -> None:
    parent = Job(
        id="pause-failed-parent", name="MD", status="running", queue_status="running",
        model_id="molecular_dynamics", mode="molecular_dynamics", params={},
    )
    child = Job(
        id="pause-failed-child", name="replica", status="running", queue_status="running",
        model_id="molecular_dynamics", mode="replica", params={}, parent_job_id=parent.id,
        child_stage="md_replica", nextflow_run_id="5252", stage_work_dir=str(tmp_path),
    )
    session.add_all([parent, child])
    await session.flush()
    run = await create_md_run(session, job=parent, normalized_request=_request())
    run.phase = "replicas_running"
    replica, segment = await create_replica_attempt(
        session, job_id=parent.id, child_job_id=child.id, replica_index=0, attempt=0,
        engine="gromacs", execution_plan_sha256="b" * 64, compatibility_key="c" * 64,
    )
    replica.state = "running"
    segment.state = "running"
    await session.commit()

    async def cancel(_run_id: str) -> bool:
        return False

    with pytest.raises(Exception, match="MD_PAUSE_ACTUATION_FAILED"):
        await pause_running_md_run(
            session, job_id=parent.id, expected_version=0,
            idempotency_key="pause:failed", cancel_worker=cancel,
        )
    await session.commit()
    await session.refresh(run)
    assert run.phase == "checkpointing" and run.controls_blocked is True
    assert replica.state == "running"
    assert child.status == "running"


def test_pause_after_resume_reads_receipt_from_original_replica_output(tmp_path: Path) -> None:
    original_output = tmp_path / "original" / "replica_0"
    new_nextflow_work = tmp_path / "resumed-nextflow-work"
    original_output.mkdir(parents=True)
    new_nextflow_work.mkdir()
    checkpoint = original_output / "production" / "production.cpt"
    checkpoint.parent.mkdir()
    checkpoint.write_bytes(b"second-pause-checkpoint")
    receipt = {
        "schema": "bms.md.checkpoint-receipt.v1",
        "checkpoint_path": "production/production.cpt",
        "sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        "bytes": checkpoint.stat().st_size,
        "step": 5000,
        "time_ps": 10.0,
        "execution_plan_sha256": "b" * 64,
        "compatibility_key": "c" * 64,
    }
    (original_output / "md-checkpoint-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    child = Job(
        id="resumed-child",
        name="replica",
        status="running",
        queue_status="running",
        model_id="molecular_dynamics",
        mode="replica",
        params={"md_resume_output_dir": str(original_output)},
        stage_work_dir=str(new_nextflow_work),
    )

    roots = _checkpoint_roots(child)
    payload, resolved_checkpoint, relative_path = _checkpoint_receipt(roots)

    assert roots == [original_output.resolve(), new_nextflow_work.resolve()]
    assert payload == receipt
    assert resolved_checkpoint == checkpoint.resolve()
    assert relative_path == "production/production.cpt"


@pytest.mark.asyncio
async def test_pause_waits_for_atomic_receipt_after_nextflow_pid_exits(tmp_path: Path) -> None:
    root = tmp_path / "worker"
    root.mkdir()

    async def publish_later() -> None:
        await asyncio.sleep(0.05)
        (root / "md-checkpoint-receipt.json").write_text("{}", encoding="utf-8")

    publisher = asyncio.create_task(publish_later())
    assert await _wait_for_receipt([root], timeout_seconds=1.0) is True
    await publisher


def test_repeated_pauses_create_distinct_immutable_checkpoint_snapshots(tmp_path: Path) -> None:
    output = tmp_path / "replica_0"
    checkpoint = output / "production" / "production.cpt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"first-pause")
    first_digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    first, first_logical, first_root = _snapshot_checkpoint(
        checkpoint_path=checkpoint,
        source_relative_path="production/production.cpt",
        segment_id="segment-1",
        sha256=first_digest,
    )

    checkpoint.write_bytes(b"second-pause")
    second_digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    second, second_logical, second_root = _snapshot_checkpoint(
        checkpoint_path=checkpoint,
        source_relative_path="production/production.cpt",
        segment_id="segment-2",
        sha256=second_digest,
    )

    assert first != second and first_logical != second_logical
    assert first_root == output and second_root == output
    assert first.read_bytes() == b"first-pause"
    assert second.read_bytes() == b"second-pause"


def test_pause_boundary_survives_api_restart_and_rotates_for_next_pause(tmp_path: Path) -> None:
    root = tmp_path / "replica_0"
    root.mkdir()
    first = _pause_boundary(root, idempotency_key="pause:first", reuse_existing=False)
    receipt = root / "md-checkpoint-receipt.json"
    receipt.write_text("{}", encoding="utf-8")

    recovered = _pause_boundary(root, idempotency_key="pause:first", reuse_existing=False)
    assert recovered == first
    assert receipt.stat().st_mtime_ns >= recovered

    second = _pause_boundary(root, idempotency_key="pause:second", reuse_existing=False)
    assert second >= first
    assert json.loads((root / ".bms-pause-boundary.json").read_text(encoding="utf-8")) == {
        "idempotency_key": "pause:second"
    }
