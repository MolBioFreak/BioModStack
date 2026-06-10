from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import Base, Design, Job  # noqa: E402
from routers import jobs as jobs_router  # noqa: E402


@pytest.mark.asyncio
async def test_jobs_list_summary_omits_heavy_fields_but_keeps_rows_selectable(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs-summary.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        job = Job(
            id="job-summary-1",
            name="heavy lineage job",
            status="completed",
            model_id="template_antibody_denovo",
            mode="antibody_refinement_pipeline",
            params={"very_large": "x" * 10000, "antibody_chains": "H"},
            created_at=datetime(2026, 6, 10, 12, 0, 0),
            completed_at=datetime(2026, 6, 10, 12, 5, 0),
            output_dir="/mnt/BioModStack/bms_results/heavy",
            provenance={"selected_design_ids": [str(i) for i in range(1000)]},
            saved_selection_sets=[{"id": "filter-a", "filters": {"huge": "y" * 1000}}],
            completed_stages=["rfantibody", "fampnn"],
            stage_outputs={"fampnn": [f"design_{i}.pdb" for i in range(250)]},
            awaiting_payload={"resume_direct": True, "large": "z" * 1000},
            decision_history=[{"decision": "continue", "payload": "q" * 1000}],
            selection_dataset_name="dataset-a",
            stage_family="fampnn",
            stage_mode="sequence_design",
        )
        session.add(job)
        session.add(Design(id="design-1", job_id="job-summary-1", name="design 1", pdb_path="design_1.pdb"))
        await session.commit()

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app = FastAPI()
    app.dependency_overrides[jobs_router.get_session] = override_get_session
    app.include_router(jobs_router.router, prefix="/api/jobs")
    client = TestClient(app)

    summary_response = client.get("/api/jobs", params={"summary": "true", "limit": 10})
    assert summary_response.status_code == 200
    summary_job = summary_response.json()["jobs"][0]
    assert summary_job["id"] == "job-summary-1"
    assert summary_job["name"] == "heavy lineage job"
    assert summary_job["design_count"] == 1
    assert summary_job["selection_dataset_name"] == "dataset-a"
    assert summary_job["stage_family"] == "fampnn"
    assert summary_job["stage_mode"] == "sequence_design"
    assert summary_job["params"] == {}
    assert summary_job["provenance"] is None
    assert summary_job["saved_selection_sets"] is None
    assert summary_job["stage_outputs"] == {}
    assert summary_job["awaiting_payload"] == {}
    assert summary_job["decision_history"] == []

    full_response = client.get("/api/jobs", params={"limit": 10})
    assert full_response.status_code == 200
    full_job = full_response.json()["jobs"][0]
    assert full_job["params"]["antibody_chains"] == "H"
    assert len(full_job["provenance"]["selected_design_ids"]) == 1000
    assert len(full_job["stage_outputs"]["fampnn"]) == 250

    await engine.dispose()
