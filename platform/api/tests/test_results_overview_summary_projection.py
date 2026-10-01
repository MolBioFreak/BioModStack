"""Actual HTTP projection and persisted owner, isolated SQLite/filesystem only."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database import Base, Design, Job, get_session
from routers.analyses import router
from services import analysis_runs


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['missing', 'queued', 'running', 'completed', 'failed'])
async def test_overview_projection_keeps_exact_run_and_full_demand(monkeypatch, tmp_path, state, record_property):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "overview.sqlite"}')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setenv('BMS_DATA', str(tmp_path))
    monkeypatch.setattr(analysis_runs, 'resolve_allowed_path', lambda value: tmp_path / value)
    source = tmp_path / 'exact.pdb'
    source.write_text('ATOM      1  CA  ALA A  12B      0.000   0.000   0.000  1.00 90.00           C  \nEND\n')
    full_result = {'A': {'type': 'protein', 'length': 1000, 'metric': 0.375,
        'native_positions': [{'document_id': 'exact-document', 'author_chain_id': 'A',
            'author_residue_number': i, 'insertion_code': 'B', 'native_index': i - 1} for i in range(1, 1001)]}}
    async with sessions() as session:
        session.add(Job(id='source-job', name='TEST projection', model_id='boltz2', mode='predict', params={}))
        design = Design(id='exact-design', job_id='source-job', name='TEST exact', pdb_path=str(source),
            review_profile_id='structure_prediction_v1', review_contract_version=1, review_contract_source='job_identity')
        session.add(design)
        await session.commit()
        run, _ = await analysis_runs.request_design_analysis(session, design, 'chain_metrics')
        run_id = run.id
        if state == 'missing':
            await session.delete(run)
        else:
            run.status = state
            run.summary_json = {'chain_count': 1, 'polymer_chain_count': 1, 'residue_count': 1000}
            run.error_message = 'TEST failure' if state == 'failed' else None
            run.result_inline_json = None
            result_path = tmp_path / run.artifact_manifest['result_json']
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps(full_result))
        await session.commit()
    app = FastAPI()
    app.include_router(router, prefix='/api')
    async def dependency():
        async with sessions() as session:
            yield session
    app.dependency_overrides[get_session] = dependency
    loads = []
    original = analysis_runs.load_analysis_result
    def traced(run):
        loads.append(run.id)
        return original(run)
    monkeypatch.setattr(analysis_runs, 'load_analysis_result', traced)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
        url = '/api/designs/exact-design/analyses/chain_metrics'
        summary_response = await client.get(url, params={'include_result': 'false'})
        assert summary_response.status_code == 200, summary_response.text
        compact = summary_response.json()
        assert compact['status'] == state
        assert compact['result'] is None
        assert loads == []
        full_response = await client.get(url)  # Backward-compatible full default.
        assert full_response.status_code == 200, full_response.text
        full = full_response.json()
        assert {k: v for k, v in full.items() if k != 'result'} == {k: v for k, v in compact.items() if k != 'result'}
        assert full['subject_id'] == 'exact-design'
        assert full['params'] == {}
        if state == 'completed':
            assert full['result'] == full_result
            assert loads == [run_id]
            assert full['run_id'] == run_id
            assert full['artifacts'] == compact['artifacts']
            assert len(full_response.content) > 50 * len(summary_response.content)
            record_property('full_bytes', len(full_response.content))
            record_property('summary_bytes', len(summary_response.content))
            record_property('summary_result_loads', 0)
            record_property('full_result_loads', len(loads))
            # Failed file read remains the same truthful null contract; summary survives.
            result_path.write_text('not JSON')
            failed_read = await client.get(url)
            assert failed_read.json()['result'] is None
            assert failed_read.json()['status'] == 'completed'
            assert (await client.get(url, params={'include_result': 'false'})).json() == compact
        else:
            assert full['result'] is None
            assert loads == []
    await engine.dispose()
