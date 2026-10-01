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
    for detail_field in ("params", "provenance", "saved_selection_sets", "stage_outputs",
                         "awaiting_payload", "decision_history", "result_summary"):
        assert detail_field not in summary_job
    # frontendDashboardSummary.test.ts exercises equivalent decoded defaults.

    summary_job_query = next(
        statement
        for statement in selected_statements
        if "from jobs" in statement and "design_count" in statement
    )
    assert "group by designs.job_id" not in summary_job_query
    assert "where designs.job_id = anon_1.id" in summary_job_query
    assert "group by jobs.id" not in summary_job_query
    # This one bounded scalar projection is required by remote result-policy
    # presentation; fetching the complete params JSON is still forbidden.
    policy_projection = "json_extract(jobs.params, ?) as remote_result_policy"
    assert summary_job_query.count(policy_projection) == 1
    summary_without_policy = summary_job_query.replace(policy_projection, "remote_result_policy")
    # Only the retained stage inventories/receipts are projected, never the
    # complete provenance or heavyweight scientific params.
    for alias in ('stage_plan_components', 'stage_assigned_components', 'stage_terminal_states'):
        projection = f"json_quote(json_extract(jobs.provenance, ?)) as {alias}"
        assert summary_without_policy.count(projection) == 1
        summary_without_policy = summary_without_policy.replace(projection, alias)
    for forbidden_column in (
        "params",
        "provenance",
        "saved_selection_sets",
        "stage_outputs",
        "awaiting_payload",
        "decision_history",
    ):
        assert f"jobs.{forbidden_column}" not in summary_without_policy

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


@pytest.mark.asyncio
async def test_jobs_model_union_bounded_pages_discovery_and_validators(tmp_path: Path) -> None:
    from datetime import timedelta
    models = ['nanopore', 'ont_fastq_qc', 'ont_plasmid_qc', 'ont_construct_screening', 'wf_clone_validation']
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'union.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add_all([Job(id=f'Case-{i:04d}', name=f'run {i}', model_id=models[i % 5],
            mode='ont_fastq_qc', status='running' if i % 2 else 'queued',
            parent_job_id='Case-0000' if i % 3 == 1 else None,
            params={'explicit_false': False, 'zero': 0, 'empty': '', 'null': None},
            created_at=datetime(2026, 1, 1) + timedelta(seconds=i)) for i in range(625)])
        session.add(Job(id='excluded', name='unrelated', model_id='ont_basecall_dna', mode='basecall_dna', status='queued', params={}))
        session.add(Job(id='Literal_%', name='100%_\\literal Épreuve', model_id='nanopore', mode='ont_fastq_qc', status='failed', params={}, created_at=datetime(2025, 1, 1)))
        await session.commit()
    async def override():
        async with factory() as session:
            yield session
    app = FastAPI()
    app.dependency_overrides[jobs_router.get_session] = override
    app.include_router(jobs_router.router, prefix='/api/jobs')
    statements = []
    @event.listens_for(engine.sync_engine, 'before_cursor_execute')
    def record_select(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith('SELECT'):
            statements.append(statement)
    with TestClient(app) as client:
        params = [('model_ids', m) for m in models] + [('summary', 'true'), ('include_children', 'true'), ('limit', '100')]
        collected = []
        for offset in range(0, 626, 100):
            response = client.get('/api/jobs', params=params + [('offset', str(offset))])
            assert response.status_code == 200
            assert response.json()['total'] == 626
            assert len(response.json()['jobs']) <= 100
            collected.extend(j['id'] for j in response.json()['jobs'])
        assert collected == [f'Case-{i:04d}' for i in reversed(range(625))] + ['Literal_%']
        assert len(statements) == 28  # four SELECTs; queued/running rows need no child fallback
        print(f'union fixture: 626 rows, seven bounded pages, {len(statements)} SELECTs; last page JSON {len(response.content)} bytes')
        first = client.get('/api/jobs', params=params)
        etag = first.headers['etag']
        assert client.get('/api/jobs', params=params, headers={'If-None-Match': etag}).status_code == 304
        assert client.get('/api/jobs', params=params + [('offset', '100')], headers={'If-None-Match': etag}).status_code == 200
        # Existing single-model and case-sensitive ID search stay unchanged.
        assert client.get('/api/jobs', params={'model_id': 'ont_fastq_qc', 'include_children': True}).json()['total'] == 125
        assert client.get('/api/jobs', params=params + [('q', 'case-0001')]).json()['total'] == 0
        assert client.get('/api/jobs', params=params + [('q', 'case-0001'), ('q_ignore_case_id', 'true')]).json()['total'] == 1
        assert client.get('/api/jobs', params=params + [('q', 'éPREUVE'), ('q_ignore_case_id', 'true')]).json()['total'] == 1
        for literal in ['%', '_', '\\']:
            assert client.get('/api/jobs', params=params + [('q', literal)]).json()['total'] == 1
        running = client.get('/api/jobs', params=params + [('status', 'running')]).json()
        assert running['total'] == 312
        assert all(j['status'] == 'running' for j in running['jobs'])
        roots = client.get('/api/jobs', params=[(k, v) for k, v in params if k != 'include_children']).json()
        assert roots['total'] == 418
        detail = client.get('/api/jobs/Case-0001')
        assert detail.status_code == 200
        assert detail.json()['params'] == {'explicit_false': False, 'zero': 0, 'empty': '', 'null': None}
        async with factory() as session:
            child = Job(id='late-child', name='externally discovered child', model_id='wf_clone_validation', mode='ont_fastq_qc', status='queued', params={}, parent_job_id='Case-0000', created_at=datetime(2027, 1, 1))
            session.add(child)
            await session.commit()
        late = client.get('/api/jobs', params=params, headers={'If-None-Match': etag})
        assert late.status_code == 200
        assert late.json()['total'] == 627
        assert late.json()['jobs'][0]['id'] == 'late-child'
        etag = late.headers['etag']
        async with factory() as session:
            child = await session.get(Job, 'late-child')
            child.error_message = 'visible operational error'
            await session.commit()
        changed = client.get('/api/jobs', params=params, headers={'If-None-Match': etag})
        assert changed.status_code == 200
        assert changed.json()['jobs'][0]['error_message'] == 'visible operational error'
    await engine.dispose()
