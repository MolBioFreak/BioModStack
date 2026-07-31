from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import Base, Job, MdAttemptSegment
from services.md.reconcile import acquire_reconciler_lease, reconcile_md_state
from services.md.state import create_md_run, create_replica_attempt


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
async def test_reconciliation_dry_run_then_apply_is_lease_owned_and_idempotent(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'reconcile.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        parent = Job(id="md-parent", name="MD", status="running", model_id="md", mode="molecular_dynamics", params={})
        child = Job(id="md-child", name="replica", status="completed", model_id="md", mode="replica", params={})
        session.add_all([parent, child]); await session.flush()
        run = await create_md_run(session, job=parent, normalized_request=_request())
        run.phase = "replicas_running"
        replica, segment = await create_replica_attempt(
            session, job_id=parent.id, replica_index=0, attempt=0, engine="gromacs",
            execution_plan_sha256="b" * 64, compatibility_key="c" * 64,
            child_job_id=child.id,
        )
        segment_id = segment.id
        replica.state = "running"; await session.commit()

    async with maker() as session:
        dry = await reconcile_md_state(session, owner_id="owner-a", apply=False)
        assert dry["dry_run"] is True and dry["change_count"] == 4
        assert replica.state == "running"
    async with maker() as session:
        applied = await reconcile_md_state(session, owner_id="owner-a", apply=True)
        await session.commit()
        assert applied["applied"] is True
    async with maker() as session:
        settled = await reconcile_md_state(session, owner_id="owner-a", apply=False)
        assert settled["change_count"] == 0
        stored_segment = await session.get(MdAttemptSegment, segment_id)
        assert stored_segment is not None and stored_segment.state == "completed"

    await engine.dispose()


@pytest.mark.asyncio
async def test_reconciliation_repairs_terminal_segment_from_validated_manifest(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'terminal.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    output = tmp_path / "results"
    manifest = output / "replicas" / "replica_0" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "schema": "bms.md.run.v1",
        "job_id": "md-terminal",
        "replica_index": 0,
        "config": {"stages": {"production": {
            "enabled": True, "steps": 5_000, "timestep_fs": 2.0,
        }}},
        "stages": {"production": {"status": "completed"}},
    }))
    async with maker() as session:
        parent = Job(id="md-terminal", name="MD", status="completed", model_id="md",
                     mode="molecular_dynamics", params={}, output_dir=str(output))
        child = Job(id="md-terminal-child", name="replica", status="completed", model_id="md",
                    mode="replica", params={}, parent_job_id=parent.id)
        session.add_all([parent, child]); await session.flush()
        run = await create_md_run(session, job=parent, normalized_request=_request())
        replica, segment = await create_replica_attempt(
            session, job_id=parent.id, replica_index=0, attempt=0, engine="gromacs",
            execution_plan_sha256="b" * 64, compatibility_key="c" * 64,
            child_job_id=child.id,
        )
        run.phase = "completed"
        replica.state = "completed"; replica.active = False
        segment_id = segment.id
        await session.commit()

    async with maker() as session:
        receipt = await reconcile_md_state(session, owner_id="repair-owner", apply=True)
        await session.commit()
        assert any(change["kind"] == "segment_state" for change in receipt["changes"])
        stored = await session.get(MdAttemptSegment, segment_id)
        assert stored is not None and stored.state == "completed"
        assert stored.end_step == 5_000
        assert stored.end_time_ps == 10.0

    await engine.dispose()


@pytest.mark.asyncio
async def test_reconciler_lease_has_one_winner_under_concurrent_acquisition(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'lease.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def acquire(owner: str) -> bool:
        async with maker() as session:
            won = await acquire_reconciler_lease(session, owner_id=owner)
            await session.commit()
            return won

    results = await asyncio.gather(acquire("owner-a"), acquire("owner-b"), return_exceptions=True)
    assert not any(isinstance(value, Exception) for value in results)
    assert sorted(results) == [False, True]
    await engine.dispose()


@pytest.mark.asyncio
async def test_reconciliation_classifies_launch_rejection_without_retrying_execution_failure(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'failure-classification.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        parent = Job(id="md-failure", name="MD", status="running", model_id="md", mode="molecular_dynamics", params={})
        launch_child = Job(
            id="md-launch-rejected", name="launch", status="failed", queue_status="failed",
            model_id="md", mode="replica", params={}, parent_job_id=parent.id,
            error_message="workflow adapter unavailable during launch",
            provenance={"failure_receipt": {
                "code": "spawn_rejected",
                "message": "workflow adapter unavailable during launch",
                "source": "scheduler_launch",
            }},
        )
        execution_child = Job(
            id="md-execution-failed", name="execution", status="failed", queue_status="failed",
            model_id="md", mode="replica", params={}, parent_job_id=parent.id,
            error_message="GROMACS chemistry validation failed",
        )
        session.add_all([parent, launch_child, execution_child]); await session.flush()
        run = await create_md_run(session, job=parent, normalized_request=_request())
        run.phase = "replicas_running"
        launch_replica, _ = await create_replica_attempt(
            session, job_id=parent.id, replica_index=0, attempt=0, engine="gromacs",
            execution_plan_sha256="b" * 64, compatibility_key="c" * 64,
            child_job_id=launch_child.id,
        )
        execution_replica, _ = await create_replica_attempt(
            session, job_id=parent.id, replica_index=1, attempt=0, engine="gromacs",
            execution_plan_sha256="d" * 64, compatibility_key="e" * 64,
            child_job_id=execution_child.id,
        )
        launch_id, execution_id = launch_replica.id, execution_replica.id
        await session.commit()

    async with maker() as session:
        await reconcile_md_state(session, owner_id="failure-owner", apply=True)
        await session.commit()
        launch = await session.get(type(launch_replica), launch_id)
        execution = await session.get(type(execution_replica), execution_id)
        assert launch is not None and launch.state == "failed" and launch.active is False
        assert launch.failure == {
            "code": "spawn_rejected",
            "message": "workflow adapter unavailable during launch",
            "source": "scheduler_launch",
        }
        assert execution is not None and execution.state == "failed" and execution.active is False
        assert execution.failure == {
            "code": "execution_failed",
            "message": "GROMACS chemistry validation failed",
            "source": "worker_terminal",
        }

    await engine.dispose()


@pytest.mark.asyncio
async def test_reconciliation_self_heals_active_md_parent_scheduler_projection(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'parent-projection.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        parent = Job(
            id="md-active-parent", name="MD", status="failed", queue_status="failed",
            error_message="stale coordinator failure", model_id="md",
            mode="molecular_dynamics", params={},
        )
        child = Job(
            id="md-active-child", name="replica", status="running", queue_status="running",
            model_id="md", mode="replica", params={}, parent_job_id=parent.id,
        )
        session.add_all([parent, child]); await session.flush()
        run = await create_md_run(session, job=parent, normalized_request=_request())
        run.phase = "replicas_running"
        replica, _ = await create_replica_attempt(
            session, job_id=parent.id, replica_index=0, attempt=1, engine="gromacs",
            execution_plan_sha256="b" * 64, compatibility_key="c" * 64,
            child_job_id=child.id,
        )
        replica.state = "running"
        await session.commit()

    async with maker() as session:
        receipt = await reconcile_md_state(session, owner_id="projection-owner", apply=True)
        await session.commit()
        assert any(change["kind"] == "parent_job_projection" for change in receipt["changes"])
        parent = await session.get(Job, "md-active-parent")
        assert parent is not None
        assert (parent.status, parent.queue_status, parent.error_message) == ("running", "running", None)

    await engine.dispose()
