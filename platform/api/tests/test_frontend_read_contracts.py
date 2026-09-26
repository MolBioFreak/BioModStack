"""Frontend list contracts; isolated SQLite and metadata GETs only."""
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, Job
from routers import jobs, models


def test_compact_choices_preserve_metadata_and_selected_detail():
    app = FastAPI()
    app.include_router(models.router, prefix="/api/models")
    client = TestClient(app)
    for include_experimental in (False, True):
        full = client.get("/api/models", params={"include_experimental": include_experimental})
        compact = client.get("/api/models", params={"include_experimental": include_experimental, "compact": True})
        assert full.status_code == compact.status_code == 200
        assert len(full.json()) > 0
        expected = []
        for model in full.json():
            choice = {key: value for key, value in model.items() if key != "params"}
            choice["modes"] = [{key: value for key, value in mode.items() if key != "params"} for mode in model["modes"]]
            expected.append(choice)
            detail = client.get(f"/api/models/{model['id']}")
            assert detail.status_code == 200
            assert detail.json()["params"] == model["params"]
            assert detail.json()["modes"] == model["modes"]
        assert compact.json() == expected
        assert len(compact.content) < len(full.content)
        category = expected[0]["category"]
        filtered = client.get("/api/models", params={"category": category, "compact": True, "include_experimental": include_experimental})
        assert filtered.json() == [choice for choice in expected if choice["category"] == category]


@pytest.mark.asyncio
async def test_recent_jobs_server_scope_and_count_before_paging(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'frontend-list.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    def row(**values):
        values.setdefault('params', {})
        return Job(**values)

    async with factory() as session:
        session.add_all([row(id=f"job-{index:03}", name=f"Run {index}", status="completed", model_id="fixture", mode="read",
                            created_at=datetime(2026, 1, 1) + timedelta(minutes=index)) for index in range(205)])
        session.add(row(id="UniqueIdCase", name="Literal 100%_name", status="running", model_id="fixture", mode="read", awaiting_input=True))
        session.add(row(id="status-awaiting", name="Another input", status="awaiting_input", model_id="fixture", mode="read"))
        session.add(row(id="ngs-model", name="NGS", status="completed", model_id=" ONT_FASTQ_QC ", mode="read"))
        session.add(row(id="ngs-legacy", name="Legacy NGS", status="completed", model_id="", mode="read", params={"workflow_id": "fastq_qc"}))
        session.add(row(id="ngs-mode", name="Mode NGS", status="completed", model_id="", mode="nanopore_methylation"))
        session.add(row(id="not-ngs", name="Named non-NGS owner", status="completed", model_id="fixture", mode="fastq_qc", params={"workflow_id": "fastq_qc"}))
        session.add(row(id="ngs-whitespace", name="Whitespace NGS", status="completed", model_id="\tONT_FASTQ_QC\n", mode="read"))
        session.add(row(id="ngs-false-fallback", name="False alias fallback", status="completed", model_id="", mode="read", params={"ont_workflow_id": False, "workflow_id": "fastq_qc"}))
        session.add(row(id="ngs-zero-fallback", name="Zero alias fallback", status="completed", model_id="", mode="read", params={"ont_workflow_id": 0, "workflow_id": "fastq_qc"}))
        session.add(row(id="not-ngs-whitespace", name="Truthy blank alias", status="completed", model_id="", mode="read", params={"ont_workflow_id": " ", "workflow_id": "fastq_qc"}))
        session.add(row(id="not-ngs-alias", name="Truthy unknown alias", status="completed", model_id="", mode="read", params={"ont_workflow_id": "other", "workflow_id": "fastq_qc"}))
        session.add(row(id="child", name="Run child", status="completed", model_id="fixture", mode="read", parent_job_id="job-000"))
        await session.commit()

    async def session_override():
        async with factory() as session:
            yield session

    app = FastAPI()
    app.dependency_overrides[jobs.get_session] = session_override
    app.include_router(jobs.router, prefix="/api/jobs")
    client = TestClient(app)

    def read(**params):
        result = client.get("/api/jobs", params={"summary": True, **params})
        assert result.status_code == 200, result.text
        return result.json()

    first = read(q="Run", limit=100)
    second = read(q="Run", limit=100, offset=100)
    last = read(q="Run", limit=100, offset=200)
    assert first["total"] == second["total"] == last["total"] == 205
    assert len(first["jobs"]) == len(second["jobs"]) == 100
    assert len(last["jobs"]) == 5
    assert len({row["id"] for page in (first, second, last) for row in page["jobs"]}) == 205
    assert read(q="job-000")["jobs"][0]["id"] == "job-000"
    assert read(q="UniqueId")["total"] == 1
    assert read(q="uniqueid")["total"] == 0  # ID search is case-sensitive, as in the UI.
    assert read(q="100%_")["total"] == 1  # SQL wildcard characters remain literal.
    assert read(q="run 0")["jobs"][0]["id"] == "job-000"
    waiting = read(status="awaiting_input")
    assert waiting["total"] == 2
    assert {row["id"] for row in waiting["jobs"]} == {"UniqueIdCase", "status-awaiting"}
    without_ngs = read(exclude_ngs=True, limit=500)
    assert without_ngs["total"] == 210
    assert {"ngs-model", "ngs-legacy", "ngs-mode", "ngs-whitespace", "ngs-false-fallback", "ngs-zero-fallback"}.isdisjoint(row["id"] for row in without_ngs["jobs"])
    assert {"not-ngs", "not-ngs-whitespace", "not-ngs-alias"} <= {row["id"] for row in without_ngs["jobs"]}
    assert read(include_children=True, q="Run")["total"] == 206
    await engine.dispose()
