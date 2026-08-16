from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, text
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
            model_id="antibody_child",
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
            pinned_gpu=1,
        )
        session.add(job)
        session.add_all(
            [
                Design(
                    id=f"design-{index}",
                    job_id="job-summary-1",
                    name=f"design {index}",
                    pdb_path=f"design_{index}.pdb",
                )
                for index in range(250)
            ]
        )
        await session.commit()

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app = FastAPI()
    app.dependency_overrides[jobs_router.get_session] = override_get_session
    app.include_router(jobs_router.router, prefix="/api/jobs")
    client = TestClient(app)

    selected_statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def capture_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selected_statements.append(statement.lower())

    summary_response = client.get("/api/jobs", params={"summary": "true", "limit": 10})
    assert summary_response.status_code == 200
    summary_job = summary_response.json()["jobs"][0]
    assert summary_job["id"] == "job-summary-1"
    assert summary_job["name"] == "heavy lineage job"
    assert summary_job["design_count"] == 250
    assert summary_job["selection_dataset_name"] == "dataset-a"
    assert summary_job["stage_family"] == "fampnn"
    assert summary_job["stage_mode"] == "sequence_design"
    assert summary_job["pinned_gpu"] == 1
    assert summary_job["params"] == {}
    assert summary_job["provenance"] is None
    assert summary_job["saved_selection_sets"] is None
    assert summary_job["stage_outputs"] == {}
    assert summary_job["awaiting_payload"] == {}
    assert summary_job["decision_history"] == []

    summary_job_query = next(
        statement
        for statement in selected_statements
        if "from jobs" in statement and "design_count" in statement
    )
    assert "group by designs.job_id" in summary_job_query
    assert "group by jobs.id" not in summary_job_query
    for forbidden_column in (
        "params",
        "provenance",
        "saved_selection_sets",
        "stage_outputs",
        "awaiting_payload",
        "decision_history",
    ):
        assert f"jobs.{forbidden_column}" not in summary_job_query

    full_response = client.get("/api/jobs", params={"limit": 10})
    assert full_response.status_code == 200
    full_job = full_response.json()["jobs"][0]
    assert full_job["params"]["antibody_chains"] == "H"
    assert len(full_job["provenance"]["selected_design_ids"]) == 1000
    assert len(full_job["stage_outputs"]["fampnn"]) == 250

    await engine.dispose()


@pytest.mark.asyncio
async def test_jobs_list_tolerates_rfc3339_z_timestamp_rows(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs-z-timestamps.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                """
                INSERT INTO jobs (
                    id, name, status, model_id, mode, params,
                    created_at, completed_at, queue_status
                ) VALUES (
                    'job-z-timestamp', 'imported z timestamp job', 'completed',
                    'external_import', 'structure_import', '{}',
                    '2026-07-05T03:55:04.487348Z',
                    '2026-07-05T03:55:04.487348Z',
                    'completed'
                )
                """
            )
        )

    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app = FastAPI()
    app.dependency_overrides[jobs_router.get_session] = override_get_session
    app.include_router(jobs_router.router, prefix="/api/jobs")
    client = TestClient(app)

    response = client.get("/api/jobs", params={"limit": 10})

    assert response.status_code == 200
    payload = response.json()
    assert payload["jobs"][0]["id"] == "job-z-timestamp"
    assert payload["jobs"][0]["created_at"].startswith("2026-07-05T03:55:04.487348")

    await engine.dispose()
