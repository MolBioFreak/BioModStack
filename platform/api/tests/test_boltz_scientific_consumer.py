"""Native publisher -> ingester -> committed/reloaded SQLite -> production consumer."""

# Every mutation test starts from a healthy real publication; no strict loader or
# native-verifier replacement is used for positive evidence.
import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from database import Design
from services.result_ingester import ingest_job_results
from test_boltz_scientific_persistence import publication, job, setup


@pytest.mark.asyncio
async def test_persisted_native_pae_and_document(tmp_path):
    publication(tmp_path, sequence=True, count=2)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            session.add(job(tmp_path))
            await session.commit()
            assert await ingest_job_results('job', str(tmp_path), session) == 2
        async with factory() as session:
            from routers.designs import get_design, get_pae_data
            from services.analysis_registry import build_analysis_input_signature, get_analysis_definition
            row = (await session.execute(select(Design).order_by(Design.name))).scalars().first()
            detail = await get_design(row.id, session)
            assert detail.scientific_structure_document is not None
            pae = await get_pae_data(row.id, 200, session)
            wire = json.loads(pae.model_dump_json())
            assert wire['status'] == 'ok'
            assert wire['document'] == detail.scientific_structure_document.model_dump()
            assert wire['producer_binding']['candidate_id'] == 'sample'
            assert wire['producer_binding']['document_id'] == row.name
            assert wire['document']['candidateId'] == row.id
            assert wire['pae_matrix'][0][1] != wire['pae_matrix'][1][0]
            definition = get_analysis_definition('pae_matrix')
            signature = await build_analysis_input_signature(definition, row, {'max_size':200}, session)
            assert len(signature) == 64
            from fastapi import FastAPI
            from httpx import ASGITransport, AsyncClient
            from routers.designs import router
            from database import get_session
            app = FastAPI()
            app.include_router(router, prefix='/designs')
            async def dependency():
                yield session
            app.dependency_overrides[get_session] = dependency
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                for url in ['/designs?job_id=job', '/designs/by-job/job']:
                    response = await client.get(url)
                    assert response.status_code == 200, response.text
                    listed = response.json()['designs']
                    assert len(listed) == 2
                    assert all(d['scientific_structure_document']['candidateId'] == d['id'] for d in listed)
                    assert all('row_axis' not in d and 'pae_matrix' not in d for d in listed)
                response = await client.get(f'/designs/{row.id}/pae')
                assert response.json() == wire
            block = copy.deepcopy(row.confidence_metrics)
            block['core_protein_scientific']['candidate_id'] = 'other'
            row.confidence_metrics = block
            # Copied/edited compact metadata cannot mint an authoritative read.
            rejected = await get_pae_data(row.id, 200, session)
            assert rejected.status == 'unavailable'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['bytes', 'candidate', 'ledger', 'key', 'receipt'])
async def test_persisted_consumer_rejects_changed_authority(tmp_path, damage):
    root, manifest = publication(tmp_path, count=2)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            session.add(job(tmp_path))
            await session.commit()
            await ingest_job_results('job', str(tmp_path), session)
        async with factory() as session:
            from routers.designs import get_pae_data
            from database import Job
            row = (await session.execute(select(Design).order_by(Design.name))).scalars().first()
            assert (await get_pae_data(row.id, 200, session)).status == 'ok'
            if damage == 'bytes':
                path = Path(row.confidence_metrics['core_protein_scientific']['artifacts']['pae']['path'])
                path.write_bytes(path.read_bytes() + b'changed')
            elif damage == 'candidate':
                block = copy.deepcopy(row.confidence_metrics)
                block['core_protein_scientific']['document_id'] = manifest['candidates'][1]['producer_output_key']
                row.confidence_metrics = block
            elif damage in ('ledger', 'key'):
                native = manifest['candidates'][0]['boltz_native_identity']['aligned_error']
                if damage == 'key':
                    native['identity_evidence']['matrix_key'] = 'other'
                else:
                    axis = native['identity_evidence']['row_axis']['residues']
                    axis[0], axis[3] = axis[3], axis[0]
                (root / 'producer_candidates.json').write_text(json.dumps(manifest))
            else:
                current = await session.get(Job, 'job')
                provenance = copy.deepcopy(current.provenance)
                del provenance['core_protein_candidate_publication']
                current.provenance = provenance
            result = await get_pae_data(row.id, 200, session)
            assert result.status == 'unavailable'
            assert result.reason
            assert result.pae_matrix is None
    finally:
        await engine.dispose()
