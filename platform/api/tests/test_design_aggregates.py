from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, Design, Job, get_session
from routers import designs


@pytest.mark.asyncio
async def test_design_list_summary_aggregates_the_complete_filtered_result_set(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'design-summary.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        session.add(Job(id="job-1", name="Large new result", status="completed", model_id="boltz2", mode="structure_prediction", params={}))
        session.add_all(
            [
                Design(
                    id="design-1", job_id="job-1", name="one", pdb_path="one.pdb",
                    plddt_overall=60.0, pae_overall=15.0, ptm=0.5, iptm=0.4,
                    ipsae=0.3, affinity_score=1.0, binder_probability=0.2,
                    epitope_contact_count=2, target_contact_count=3,
                    epitope_min_distance=8.0, target_min_distance=9.0,
                    rfa_hotspot_covered_count=1, fampnn_psce=0.1,
                    is_favorite=False, passed_screen=False,
                ),
                Design(
                    id="design-2", job_id="job-1", name="two", pdb_path="two.pdb",
                    plddt_overall=80.0, pae_overall=10.0, ptm=0.7, iptm=0.6,
                    ipsae=0.5, affinity_score=2.0, binder_probability=0.4,
                    epitope_contact_count=6, target_contact_count=7,
                    epitope_min_distance=4.0, target_min_distance=5.0,
                    rfa_hotspot_covered_count=3, fampnn_psce=0.3,
                    is_favorite=True, passed_screen=True,
                ),
                Design(
                    id="design-3", job_id="job-1", name="three", pdb_path="three.pdb",
                    plddt_overall=100.0, pae_overall=5.0, ptm=0.9, iptm=0.8,
                    ipsae=0.7, affinity_score=3.0, binder_probability=0.6,
                    epitope_contact_count=10, target_contact_count=11,
                    epitope_min_distance=2.0, target_min_distance=1.0,
                    rfa_hotspot_covered_count=5, fampnn_psce=0.5,
                    is_favorite=True,
                ),
            ]
        )
        await session.commit()

    app = FastAPI()
    app.include_router(designs.router, prefix="/api/designs")

    async def _session_override():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _session_override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/designs",
            params={
                "job_id": "job-1",
                "include_children": "false",
                "limit": 2,
                "offset": 0,
                "include_summary": "true",
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["designs"]) == 2
    assert payload["total"] == 3
    summary = payload["summary"]
    assert summary["total"] == 3
    assert summary["favorites"] == 2
    assert summary["avg_plddt"] == pytest.approx(80.0)
    assert summary["avg_pae"] == pytest.approx(10.0)
    assert summary["avg_ptm"] == pytest.approx(0.7)
    assert summary["avg_iptm"] == pytest.approx(0.6)
    assert summary["avg_ipsae"] == pytest.approx(0.5)
    assert summary["avg_affinity"] == pytest.approx(2.0)
    assert summary["avg_binder_probability"] == pytest.approx(0.4)
    assert summary["avg_epitope_contacts"] == pytest.approx(6.0)
    assert summary["avg_target_contacts"] == pytest.approx(7.0)
    assert summary["avg_epitope_distance"] == pytest.approx(14.0 / 3.0)
    assert summary["avg_target_distance"] == pytest.approx(5.0)
    assert summary["avg_hotspot_coverage"] == pytest.approx(3.0)
    assert summary["avg_psce"] == pytest.approx(0.3)
    assert summary["high_confidence"] == 2
    assert summary["low_error"] == 1
    assert summary["high_contacts"] == 2
    assert summary["screen_passed"] == 1
    assert summary["screen_failed"] == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_design_list_summary_uses_the_same_server_filters_as_rows(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'filtered-summary.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        session.add(Job(id="job-1", name="Filtered result", status="completed", model_id="boltz2", mode="structure_prediction", params={}))
        session.add_all(
            [
                Design(id="design-1", job_id="job-1", name="one", pdb_path="one.pdb", plddt_overall=50.0),
                Design(id="design-2", job_id="job-1", name="two", pdb_path="two.pdb", plddt_overall=90.0),
            ]
        )
        await session.commit()

    app = FastAPI()
    app.include_router(designs.router, prefix="/api/designs")

    async def _session_override():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _session_override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/designs",
            params={
                "job_id": "job-1",
                "include_children": "false",
                "plddt_min": 80,
                "include_summary": "true",
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["avg_plddt"] == pytest.approx(90.0)

    await engine.dispose()
