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
from services.rfd3_generation import request_sha256, GenerationContractError
from services.rfd3_generation_results import read_generation_results
from services.rfd4_proteina_results import read_published_generation_page
from test_core_protein_scientific_admission import admission


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


@pytest.mark.asyncio
async def test_rfd3_v2_incremental_empty_partial_and_replacement(admission, tmp_path):
    output = tmp_path / 'rfd3'; output.mkdir()
    request = {'schema': 'bms.rfd3.generation.request.v1', 'job_id': 'rfd3', 'request_id': 'request',
               'generation': {'min_length': 1, 'max_length': 20, 'num_designs': 2}, 'execution': {}}
    manifest = {'schema': 'bms.rfd3.generation.result-manifest.v2', 'job_id': 'rfd3',
        'request_id': 'request', 'request_sha256': request_sha256(request), 'execution_status': 'failed',
        'population': {'requested': 2, 'generated': 0, 'missing': 2, 'unexpected': 0, 'failed': None, 'unpaired_artifacts': []},
        'aggregate': {'requested': 2, 'generated': 0, 'accepted': 0, 'length': None, 'radius_of_gyration': None}, 'candidates': []}
    path = output / 'collected/rfd3_generation/rfd3_generation_result_manifest.json'
    orphan = output / 'unpaired.json'; orphan.write_text('{}')
    manifest['population']['unpaired_artifacts'] = [{'role': 'candidate_metadata', 'relative_path': 'unpaired.json',
        'sha256': hashlib.sha256(b'{}').hexdigest(), 'bytes': 2, 'media_type': 'application/json'}]
    _write(path, manifest)
    # The old receiver rejected any manifest over 8 MiB, even valid JSON.
    with path.open('a') as handle: handle.write(' ' * (8 * 1024 * 1024))
    job = Job(id='rfd3', name='fixture', model_id='protein_modification_experimental', mode='de_novo_design',
              params={'generator': 'rfd3', 'rfd3_generation_request': request}, status='failed', output_dir=str(output))
    admission.add(job); await admission.commit()
    assert await ingest_job_results('rfd3', output, admission) == 0
    app = FastAPI()
    @app.get('/result')
    async def read():
        return await read_generation_results(await admission.get(Job, 'rfd3'), admission, offset=0, limit=1)
    async with AsyncClient(transport=ASGITransport(app), base_url='http://test') as client:
        empty = (await client.get('/result')).json()
        assert empty['counts']['generated'] == 0 and empty['population']['missing'] == 2
        assert empty['execution_status'] == 'failed' and empty['candidates'] == []
        descriptors = []
        for role, name, data in [('candidate_structure', 'one.cif', 'data_fixture\n'), ('candidate_metadata', 'one.json', '{}')]:
            artifact = output / name; artifact.write_text(data)
            descriptors.append({'role': role, 'relative_path': name, 'sha256': hashlib.sha256(data.encode()).hexdigest(),
                                'bytes': len(data), 'media_type': 'application/octet-stream'})
        candidate = {'candidate_id': 'one', 'accepted': False, 'metrics': {'residue_count': 10, 'chain_count': 1,
            'radius_of_gyration': 2.0, 'helix_count': None, 'strand_count': None},
            'artifacts': descriptors, 'artifact_manifest_sha256': hashlib.sha256(json.dumps(descriptors, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
        manifest['candidates'] = [candidate]
        manifest['population'].update(generated=1, missing=1)
        manifest['aggregate'].update(generated=1, length={'min': 10, 'mean': 10.0, 'max': 10}, radius_of_gyration={'min': 2.0, 'mean': 2.0, 'max': 2.0})
        _write(path, manifest)
        assert await ingest_job_results('rfd3', output, admission) == 1
        assert await ingest_job_results('rfd3', output, admission) == 1
        partial = (await client.get('/result')).json()
        assert partial['counts']['generated'] == 1 and partial['population']['missing'] == 1
        original = (await admission.scalars(select(Design))).one()
        original_id = original.id
        candidate['metrics']['radius_of_gyration'] = 3
        manifest['aggregate']['radius_of_gyration'] = {'min': 3, 'mean': 3, 'max': 3}
        _write(path, manifest)
        with pytest.raises(GenerationContractError, match='conflict'):
            await ingest_job_results('rfd3', output, admission)
        restored = (await admission.scalars(select(Design))).one()
        assert restored.id == original_id and restored.provenance['metrics']['radius_of_gyration'] == 2


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
