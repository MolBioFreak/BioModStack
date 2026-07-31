from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import Base, Job, MdAttemptSegment, MdReplicaRun  # noqa: E402
from routers import molecular_dynamics  # noqa: E402
from services.md import read_model  # noqa: E402
from services.md.state import accept_checkpoint, create_md_run, create_replica_attempt  # noqa: E402


def _request(job_id: str, *, replicas: int = 2) -> dict:
    return {
        "schema": "bms.md.job.v2",
        "job_id": job_id,
        "engine": "gromacs",
        "replicas": replicas,
        "chemistry": {
            "profile_id": "amber_ff19sb_opc_protein_v1",
            "profile_sha256": "a" * 64,
            "assurance": "curated_profile",
        },
        "stages": {
            "minimization": {"enabled": True, "steps": 500},
            "nvt": {"enabled": True, "steps": 5_000},
            "npt": {"enabled": False, "steps": 1},
            "production": {"enabled": True, "steps": 45_000, "timestep_fs": 2.0},
        },
    }


async def _seed_md_run(
    session: AsyncSession,
    job_id: str,
    *,
    phase: str,
    created_at: datetime,
) -> None:
    job = Job(
        id=job_id,
        name=f"MD run {job_id}",
        status="running",
        queue_status="running",
        model_id="molecular_dynamics",
        mode="molecular_dynamics",
        params={},
        created_at=created_at,
    )
    session.add(job)
    await session.flush()
    run = await create_md_run(session, job=job, normalized_request=_request(job_id))
    run.phase = phase
    run.updated_at = created_at
    replica, segment = await create_replica_attempt(
        session,
        job_id=job_id,
        replica_index=0,
        attempt=0,
        engine="gromacs",
        execution_plan_sha256="b" * 64,
        compatibility_key="c" * 64,
    )
    replica.state = "running"
    segment.state = "running"
    segment.end_time_ps = 25.0
    await session.flush()


@pytest.mark.asyncio
async def test_md_queue_projection_is_newest_first_summary_only_and_hard_limited(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'md-queue.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as session:
            now = datetime(2026, 7, 29, 12, 0, 0)
            await _seed_md_run(session, "md-old", phase="preparing", created_at=now - timedelta(minutes=2))
            await _seed_md_run(session, "md-middle", phase="replicas_queued", created_at=now - timedelta(minutes=1))
            await _seed_md_run(session, "md-new", phase="replicas_running", created_at=now)
            await session.commit()

            payload = await read_model.md_queue_snapshot(session, limit=2)

        assert payload["schema"] == "bms.md.queue.v1"
        assert payload["bounded"] is True
        assert payload["limit"] == 2
        assert payload["count"] == 2
        assert [item["job_id"] for item in payload["runs"]] == ["md-new", "md-middle"]
        newest = payload["runs"][0]
        assert newest["name"] == "MD run md-new"
        assert newest["phase"] == "replicas_running"
        assert newest["engine"] == "gromacs"
        assert newest["replica_count"] == 2
        assert newest["replica_summary"] == {"running": 1}
        assert newest["simulated_time_ps"] == 25.0
        assert newest["requested_time_ps"] == 100.0
        assert newest["chemistry"]["profile_id"] == "amber_ff19sb_opc_protein_v1"
        for forbidden in ("replicas", "segments", "checkpoints", "events", "normalized_request"):
            assert forbidden not in newest
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_md_queue_route_enforces_server_limit_and_excludes_non_md_jobs(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'md-queue-route.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        now = datetime(2026, 7, 29, 12, 0, 0)
        await _seed_md_run(session, "md-route", phase="replicas_running", created_at=now)
        session.add(Job(
            id="ordinary-job", name="ordinary", status="running", queue_status="running",
            model_id="boltz2", mode="structure_prediction", params={}, created_at=now + timedelta(minutes=1),
        ))
        await session.commit()

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app = FastAPI()
    app.dependency_overrides[molecular_dynamics.get_session] = override_get_session
    app.include_router(molecular_dynamics.router)
    with TestClient(app) as client:
        response = client.get("/api/molecular-dynamics/runs", params={"limit": 1})
        oversized = client.get("/api/molecular-dynamics/runs", params={"limit": 101})

    assert response.status_code == 200
    assert response.json()["limit"] == 1
    assert [item["job_id"] for item in response.json()["runs"]] == ["md-route"]
    assert oversized.status_code == 422
    await engine.dispose()


@pytest.mark.asyncio
async def test_pause_is_advertised_only_after_running_worker_identity_is_durable(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'md-pause-ready.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as session:
            await _seed_md_run(
                session, "md-pause-ready", phase="replicas_running",
                created_at=datetime(2026, 7, 29, 12, 0, 0),
            )
            replica = await session.scalar(select(MdReplicaRun).where(
                MdReplicaRun.md_job_id == "md-pause-ready"
            ))
            assert replica is not None
            before = await read_model.md_run_snapshot(session, "md-pause-ready")
            assert before is not None and "pause" not in before["allowed_actions"]

            child = Job(
                id="md-pause-child", name="replica", status="running", queue_status="running",
                model_id="molecular_dynamics", mode="replica", params={},
                nextflow_run_id="adapter-run-1", stage_work_dir=str(tmp_path / "work"),
                parent_job_id="md-pause-ready",
            )
            session.add(child)
            replica.child_job_id = child.id
            await session.flush()

            detail = await read_model.md_run_snapshot(session, "md-pause-ready")
            queue = await read_model.md_queue_snapshot(session, limit=25)
            assert detail is not None and "pause" in detail["allowed_actions"]
            assert "pause" in queue["runs"][0]["allowed_actions"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_md_queue_does_not_advertise_unbound_accepted_checkpoint(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'md-queue-checkpoint.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as session:
            await _seed_md_run(
                session, "md-unbound-checkpoint", phase="paused",
                created_at=datetime(2026, 7, 29, 12, 0, 0),
            )
            segment = await session.scalar(
                select(MdAttemptSegment)
                .join(MdReplicaRun, MdReplicaRun.id == MdAttemptSegment.replica_run_id)
                .where(MdReplicaRun.md_job_id == "md-unbound-checkpoint")
            )
            assert segment is not None
            await accept_checkpoint(
                session, segment_id=segment.id, logical_role="continuation",
                relative_path="replicas/replica_0/state.cpt", sha256="d" * 64,
                bytes_=4096, step=100, time_ps=0.2, compatibility_key="c" * 64,
            )
            await session.commit()
            payload = await read_model.md_queue_snapshot(session, limit=25)
        row = payload["runs"][0]
        assert row["checkpoint_available"] is False
        assert "resume_dynamics" not in row["allowed_actions"]
    finally:
        await engine.dispose()
