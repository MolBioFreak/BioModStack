"""Opt-in private producer/HTTP capture. Deterministic publisher fixtures, NOT INFERENCE.

The existing inert runtime leaf supplies CSV scores; real normalizer, component
writers, statistics, ingestion, SQLite and GET routers are unmodified. Five
238-residue poly-G fixtures are NOT GFP structures or scientific predictions.
"""
import importlib
import hashlib
import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job, get_session
from routers.frustrampnn import router
from services.frustrampnn.contracts import AA_ORDER, canonical_json_bytes
from services.frustrampnn.persistence import ingest_result_bundle
from services.frustrampnn.statistics_jobs import FrustraMPNNStatisticsWorker
from test_frustrampnn_component_phase3 import _v2_inputs, _mock_v2_runtime


@pytest.mark.asyncio
async def test_capture_real_producer_http_wire(tmp_path, monkeypatch):
    destination = os.environ.get('BMS_TEST_FRUSTRA_WIRE')
    if not destination:
        pytest.skip('explicit private capture destination required')
    assert destination is not None
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    component = importlib.import_module('scripts.run_frustrampnn_component')
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "capture.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    candidates = [f'NOT-INFERENCE-polyG-{i}' for i in range(5)]
    async with sessions() as session:
        session.add(Job(id='job-v2', name='NOT INFERENCE publisher capture', status='completed',
                        queue_status='completed', model_id='boltz2', mode='predict',
                        params={}, output_dir=str(tmp_path)))
        await session.commit()
        for i, candidate in enumerate(candidates):
            root = tmp_path / candidate
            root.mkdir()
            request, normalized, map_path, _ = _v2_inputs(root,
                residues=[('A', j + 1) for j in range(238)],
                selected=[('A', j) for j in range(238)], request_generation=3,
                candidate_id=candidate, invocation_id=f'inert-invocation-{i}')
            source_artifact = request['source_artifact']
            assert isinstance(source_artifact, dict)
            source_artifact['artifact_id'] = candidate
            session.add(Design(id=candidate, job_id='job-v2', name=candidate,
                               pdb_path=str(root / 'source.pdb')))
            await session.commit()
            with monkeypatch.context() as runtime_patch:
                _mock_v2_runtime(component, runtime_patch, root,
                    scores={aa: (j - 10) / 5 for j, aa in enumerate(AA_ORDER)})
                output = root / 'bundle'
                component.run_component(request=request, request_payload=canonical_json_bytes(request),
                    source_structure=normalized, structure_map=map_path, output_dir=output,
                    container=root / 'mock.sif', physical_gpu_id=3)
            terminal = json.loads((output / 'workflow_component_result_v3.json').read_bytes())
            await ingest_result_bundle(session, output, parent_job_id='job-v2', terminal_envelope=terminal)
        await session.commit()
    worker = FrustraMPNNStatisticsWorker(sessions)
    for _ in candidates:
        assert await worker.run_pending_once() is not None
    await engine.dispose()  # cold database readback, not an identity-map replay
    app = FastAPI()
    app.include_router(router)
    async def private_session():
        async with sessions() as session:
            async def forbidden_commit():
                raise AssertionError('GET capture attempted database commit')
            session.commit = forbidden_commit
            yield session
    app.dependency_overrides[get_session] = private_session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        async def get(url, **params):
            response = await client.get(url, params=params or None)
            assert response.status_code == 200, response.text
            return response.json()
        listing = await get('/api/frustrampnn/jobs/job-v2/results')
        assert listing['total'] == len(listing['items']) == 5
        for i, candidate in enumerate(candidates):
            base = f'/api/frustrampnn/results/inert-invocation-{i}'
            wire = {'list': listing}
            for key, suffix in [('detail', ''), ('statistics', '/statistics'),
                                ('artifacts', '/artifacts')]:
                wire[key] = await get(base + suffix, job_id='job-v2')
            wire['analysis'] = await get(f'/api/frustrampnn/results/job-v2/inert-invocation-{i}/statistics/analysis')
            assert wire['analysis']['state'] == 'completed'
            assert wire['statistics']['availability'] is True
            wire['pages'] = []
            offset = 0
            while offset is not None:
                page = await get(base + '/landscape', job_id='job-v2', offset=offset, limit=500)
                wire['pages'].append(page)
                offset = page['next_offset']
            assert sum(len(p['items']) for p in wire['pages']) == 238 * 20
            artifacts = {item['role']: item for item in wire['artifacts']['items']}
            wire['mapUrl'] = artifacts['structure_map']['download_url']
            wire['normalizedUrl'] = artifacts['normalized_input']['download_url']
            wire['map'] = await get(wire['mapUrl'])
            assert len(wire['map']['rows']) == 238
            normalized_response = await client.get(wire['normalizedUrl'])
            assert normalized_response.status_code == 200
            assert hashlib.sha256(normalized_response.content).hexdigest() == artifacts['normalized_input']['content_sha256']
            wire['evidence_kind'] = 'inert-publisher-fixture-NOT-INFERENCE'
            (destination / f'{candidate}.json').write_text(json.dumps(wire))
    (destination / 'manifest.json').write_text(json.dumps({
        'candidates': candidates, 'evidence_kind': 'inert-publisher-fixture-NOT-INFERENCE',
        'description': 'Five deterministic 238-residue poly-G publisher fixtures; not GFP or predictions.',
        'producer': 'scripts.run_frustrampnn_component.run_component',
        'inert_leaf': 'test_frustrampnn_component_phase3._mock_v2_runtime',
    }, indent=2))
    await engine.dispose()
