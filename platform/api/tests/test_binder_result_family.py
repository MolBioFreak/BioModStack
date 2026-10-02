"""Scientific-family readback through the existing paged Jobs owner."""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from database import Base, Job, Design
from routers import jobs


@pytest.mark.asyncio
async def test_family_reopens_every_round_without_scheduler_or_design_count_gate(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'family.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    def row(id, **kw):
        return Job(id=id, name=id, status="completed", model_id=kw.pop("model_id", "bindcraft2"),
                   mode="default", params=kw.pop("params", {}), **kw)
    async with factory() as session:
        session.add_all([
            row("root"), row("scheduler"), row("unrelated"),
            row("accepted", lineage_root_job_id="root", selection_source_job_id="root"),
            row("rejected-zero", lineage_root_job_id="root", source_stage_job_id="root"),
            row("native-zero", model_id="ppiflow", lineage_root_job_id="root"),
            row("round2", model_id="boltzgen", lineage_root_job_id="root", selection_source_job_id="accepted", parent_job_id="scheduler"),
            row("round3", params={"iteration_source_root_job_id": "root", "iteration_source_job_id": "round2"}),
            row("legacy", parent_job_id="root"),
            row("source-only", params={"source_stage_job_id": "round3"}),
        ])
        session.add(Design(id="design", job_id="accepted", name="accepted", pdb_path="test-only.pdb"))
        await session.commit()
    async def scoped():
        async with factory() as session:
            yield session
    app = FastAPI()
    app.include_router(jobs.router, prefix="/api/jobs")
    app.dependency_overrides[jobs.get_session] = scoped
    expected = {"root", "accepted", "rejected-zero", "native-zero", "round2", "round3", "legacy", "source-only"}
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for reopened in ("root", "round3", "native-zero", "source-only"):
                found = {}
                for offset in range(0, len(expected), 3):
                    response = await client.get("/api/jobs", params={"scientific_family_job_id": reopened,
                        "include_children": True, "summary": False, "limit": 3, "offset": offset})
                    assert response.status_code == 200, response.text
                    data = response.json()
                    assert data["total"] == len(expected)
                    found.update({job["id"]: job for job in data["jobs"]})
                assert set(found) == expected
                assert found["rejected-zero"]["design_count"] == found["native-zero"]["design_count"] == 0
                assert found["accepted"]["design_count"] == 1
                assert found["round2"]["parent_job_id"] == "scheduler"
                assert found["round2"]["selection_source_job_id"] == "accepted"
            response = await client.get("/api/jobs", params={"scientific_family_job_id": "unrelated", "summary": True})
            assert [job["id"] for job in response.json()["jobs"]] == ["unrelated"]
    finally:
        await engine.dispose()
