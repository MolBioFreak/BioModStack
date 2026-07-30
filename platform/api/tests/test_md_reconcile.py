from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import Base, Job
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
        replica, _ = await create_replica_attempt(
            session, job_id=parent.id, replica_index=0, attempt=0, engine="gromacs",
            execution_plan_sha256="b" * 64, compatibility_key="c" * 64,
            child_job_id=child.id,
        )
        replica.state = "running"; await session.commit()

    async with maker() as session:
        dry = await reconcile_md_state(session, owner_id="owner-a", apply=False)
        assert dry["dry_run"] is True and dry["change_count"] == 2
        assert replica.state == "running"
    async with maker() as session:
        applied = await reconcile_md_state(session, owner_id="owner-a", apply=True)
        await session.commit()
        assert applied["applied"] is True
    async with maker() as session:
        settled = await reconcile_md_state(session, owner_id="owner-a", apply=False)
        assert settled["change_count"] == 0

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
