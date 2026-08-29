from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import database  # noqa: E402
from database import Base, Design, Job  # noqa: E402
from routers import designs as designs_router  # noqa: E402
from routers import jobs as jobs_router  # noqa: E402
from services import analysis_autorun  # noqa: E402


async def _client_for(tmp_path: Path, *routers):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'phase1.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_session():
        async with factory() as session:
            yield session

    app = FastAPI()
    for router, prefix in routers:
        app.dependency_overrides[router.get_session] = override_get_session
        app.include_router(router.router, prefix=prefix)
    return engine, factory, TestClient(app)


@pytest.mark.asyncio
async def test_job_list_defaults_to_100_and_rejects_more_than_500(tmp_path: Path) -> None:
    engine, factory, client = await _client_for(tmp_path, (jobs_router, "/api/jobs"))
    async with factory() as session:
        session.add_all([
            Job(
                id=f"job-{index:03d}",
                name=f"job {index}",
                status="queued",
                model_id="test",
                mode="test",
                params={},
                created_at=datetime(2026, 1, 1, 0, 0, index % 60),
            )
            for index in range(101)
        ])
        await session.commit()

    default_response = client.get("/api/jobs", params={"summary": "true"})
    assert default_response.status_code == 200
    assert len(default_response.json()["jobs"]) == 100
    assert default_response.json()["total"] == 101
    assert client.get("/api/jobs", params={"limit": 500}).status_code == 200
    assert client.get("/api/jobs", params={"limit": 501}).status_code == 422
    assert client.get("/api/jobs", params={"limit": 0}).status_code == 422
    await engine.dispose()


@pytest.mark.asyncio
async def test_design_list_routes_reject_limits_above_500(tmp_path: Path) -> None:
    engine, _factory, client = await _client_for(tmp_path, (designs_router, "/api/designs"))
    assert client.get("/api/designs", params={"limit": 501}).status_code == 422
    assert client.get("/api/designs/by-job/missing", params={"limit": 501}).status_code == 422
    await engine.dispose()


@pytest.mark.asyncio
async def test_reusable_structure_list_is_bounded_to_existing_completed_designs(tmp_path: Path) -> None:
    engine, factory, client = await _client_for(tmp_path, (designs_router, "/api/designs"))
    usable = tmp_path / "usable.pdb"
    usable.write_text(
        "ATOM      1  CA  GLY A   1       0.000   0.000   0.000  1.00 10.00           C\nEND\n",
        encoding="utf-8",
    )
    missing = tmp_path / "missing.pdb"
    async with factory() as session:
        session.add_all([
            Job(id="completed-job", name="completed", status="completed", model_id="boltz2", mode="predict", params={}),
            Job(id="running-job", name="running", status="running", model_id="boltz2", mode="predict", params={}),
            Design(id="usable-design", job_id="completed-job", name="usable design", pdb_path=str(usable)),
            Design(id="missing-design", job_id="completed-job", name="missing design", pdb_path=str(missing)),
            Design(id="running-design", job_id="running-job", name="running design", pdb_path=str(usable)),
        ])
        await session.commit()

    response = client.get("/api/designs/reusable-structures", params={"limit": 2})

    assert response.status_code == 200
    assert response.json() == {
        "structures": [{
            "design_id": "usable-design",
            "design_name": "usable design",
            "job_id": "completed-job",
            "job_name": "completed",
            "model_id": "boltz2",
            "completed_at": None,
            "structure_url": "/api/designs/usable-design/pdb",
        }],
        "limit": 2,
    }
    assert client.get("/api/designs/reusable-structures", params={"limit": 51}).status_code == 422
    await engine.dispose()


@pytest.mark.asyncio
async def test_schema_helper_adds_design_job_id_index_idempotently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
    monkeypatch.setattr(database, "engine", engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("DROP INDEX ix_designs_job_id"))
        await database._ensure_schema(conn)
        await database._ensure_schema(conn)
        indexes = (await conn.execute(text("PRAGMA index_list(designs)"))).fetchall()

    assert "ix_designs_job_id" in {row[1] for row in indexes}
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_job_is_read_only_and_does_not_schedule_analysis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, factory, client = await _client_for(tmp_path, (jobs_router, "/api/jobs"))
    async with factory() as session:
        session.add(Job(
            id="read-only-job",
            name="read only",
            status="completed",
            model_id="test",
            mode="test",
            params={},
            completed_stages=[],
            stage_outputs={},
        ))
        await session.commit()

    repair_calls: list[str] = []
    analysis_calls: list[str] = []
    monkeypatch.setattr(jobs_router, "_repair_job_for_response", lambda job: repair_calls.append(job.id))
    monkeypatch.setattr(
        analysis_autorun,
        "schedule_viewer_minimum_analyses_for_job",
        lambda job_id: analysis_calls.append(job_id),
    )

    response = client.get("/api/jobs/read-only-job")
    assert response.status_code == 200
    assert repair_calls == []
    assert analysis_calls == []
    await engine.dispose()
