"""Real SQLite/native receiver checks; file bytes are explicit inference stubs."""
import copy
import hashlib
import json
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from database import Design, Job, JobArtifact
from services.result_ingester import ingest_job_results
from services.rfd4_proteina_results import read_published_generation_page
from test_core_protein_scientific_admission import admission


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['rfd4_proteina', 'complexa'])
async def test_native_artifact_only_page_and_replay(admission, tmp_path, model):
    output = tmp_path / model; root = output / 'collected' / model; root.mkdir(parents=True)
    (root / 'one.cif').write_text('data_explicit_fixture\n')
    common = {'model_id': model, 'contract': model + '_v1', 'artifacts': [{'path': 'one.cif'}],
              'population_completeness': 'partial', 'observation_errors': ['native score missing']}
    if model == 'rfd4_proteina':
        name = 'manifest.json'
        manifest = {**common, 'records': [{'example_id': 'one', 'document': 'one.cif', 'metrics': {}}],
                    'events': [{'event': 'diagnostic', 'message': 'partial native output'}]}
    else:
        name = 'complexa-results.json'
        manifest = {**common, 'documents': [{'path': 'one.cif', 'role': 'lookahead'}],
                    'observations': [{'values': {'loss': None}}]}
    _write(root / name, manifest)
    job = Job(id=model, name='fixture', model_id=model, mode='generate', params={}, status='failed', output_dir=str(output))
    admission.add(job); await admission.commit()
    assert await ingest_job_results(model, output, admission) == 0
    assert await ingest_job_results(model, output, admission) == 0
    app = FastAPI()
    @app.get('/result')
    async def read(offset: int = 0):
        return await read_published_generation_page(await admission.get(Job, model), admission, offset=offset, limit=1)
    async with AsyncClient(transport=ASGITransport(app), base_url='http://test') as client:
        first = (await client.get('/result')).json(); second = (await client.get('/result?offset=1')).json()
        assert first['total'] == 2 and len(first['records']) == 1
        assert first['execution_status'] == 'failed' and first['population_completeness'] == 'partial'
        assert first['records'][0]['candidate_ref']['kind'] == 'native_document'
        assert second['records'][0]['candidate_ref']['kind'] == 'native_observation'
        assert 'native_record_id' in second['records'][0]['candidate_ref']
        assert (await client.get('/result?offset=2')).json()['records'] == []
    assert list(await admission.scalars(select(Design))) == []
    before = [(a.id, a.sha256) for a in await admission.scalars(select(JobArtifact))]
    (root / 'one.cif').write_text('changed')
    with pytest.raises(ValueError, match='replay changed'):
        await ingest_job_results(model, output, admission)
    assert [(a.id, a.sha256) for a in await admission.scalars(select(JobArtifact))] == before
