"""The real RFD3 HTTP reader honors its existing service pagination contract."""
from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job, get_session
from routers.jobs import router


@pytest_asyncio.fixture
async def result_client(tmp_path, monkeypatch):
    monkeypatch.setenv("BMS_DATA", str(tmp_path))
    monkeypatch.setenv("BMS_RESULTS_DIR", str(tmp_path / "results"))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pages.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    lengths = [2, 3, 4]
    radii = [1.0, 2.0, 3.0]
    aggregate = {
        "requested": len(lengths), "generated": len(lengths), "accepted": len(lengths),
        "length": {"min": min(lengths), "mean": sum(lengths) / len(lengths), "max": max(lengths)},
        "radius_of_gyration": {"min": min(radii), "mean": sum(radii) / len(radii), "max": max(radii)},
    }
    async with sessions() as session:
        session.add(Job(
            id="paged-rfd3", name="paged-rfd3", model_id="protein_modification_experimental",
            mode="de_novo_design", status="completed", output_dir=str(tmp_path / "results"),
            params={
                "rfd3_generation_request": {"schema": "bms.rfd3.generation.request.v1"},
                "rfd3_generation_result_manifest_sha256": "a" * 64,
                "rfd3_generation_aggregate": aggregate,
            },
        ))
        for index, (length, radius) in enumerate(zip(lengths, radii)):
            session.add(Design(
                id=f"design-{index}", job_id="paged-rfd3", name=f"sample_{index}",
                pdb_path=str(tmp_path / f"sample_{index}.cif"),
                provenance={"accepted": True, "metrics": {
                    "residue_count": length, "radius_of_gyration": radius,
                    "helix_count": 0, "strand_count": 0,
                }},
            ))
        await session.commit()
    app = FastAPI()
    app.include_router(router, prefix="/api/jobs")

    async def private_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = private_session
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            yield client
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rfd3_http_nonzero_page_and_tail_keep_full_population_totals(result_client):
    first = await result_client.get("/api/jobs/paged-rfd3/rfd3-generation?offset=1&limit=1")
    assert first.status_code == 200, first.text
    body = first.json()
    assert [row["candidate_id"] for row in body["candidates"]] == ["sample_1"]
    assert (body["offset"], body["limit"], body["total"]) == (1, 1, 3)
    assert body["counts"] == {"requested": 3, "generated": 3, "accepted": 3}
    tail = await result_client.get("/api/jobs/paged-rfd3/rfd3-generation?offset=2&limit=2")
    assert tail.status_code == 200, tail.text
    assert [row["candidate_id"] for row in tail.json()["candidates"]] == ["sample_2"]
    assert tail.json()["total"] == 3


@pytest.mark.asyncio
async def test_rfd3_http_omitted_page_preserves_existing_unbounded_read(result_client):
    response = await result_client.get("/api/jobs/paged-rfd3/rfd3-generation")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["candidate_id"] for row in body["candidates"]] == ["sample_0", "sample_1", "sample_2"]
    assert body["offset"] == 0 and body["limit"] is None and body["total"] == 3


@pytest.mark.asyncio
async def test_rfd3_http_uses_existing_service_page_validation(result_client):
    response = await result_client.get("/api/jobs/paged-rfd3/rfd3-generation?offset=-1&limit=1")
    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid native page"
