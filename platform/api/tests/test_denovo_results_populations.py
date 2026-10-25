"""Real publisher/registration/cold reader; inert specimens, no inference."""
import json
import os
from pathlib import Path
import pytest
from database import Job
from test_remote_lifecycle_gaps import store
from test_denovo_canonical_return import cif_ensemble
from scripts.bms_rfd4.publication import NativeObserver, publish
from scripts.lib.rfd4_proteina_contract import normalize_request
from scripts.lib.rfd4_proteina_contract import parameter_schema
from services.rfd4_proteina_results import publish_native_generation, read_published_generation_page

@pytest.mark.asyncio
async def test_population_counts_and_sequence_custody(store, tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_DATA', str(tmp_path))
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path))
    output = tmp_path / 'output'
    root = output / 'collected/rfd4_proteina'
    root.mkdir(parents=True)
    (root / 'native/sampling_states').mkdir(parents=True)
    (root / 'native/sampling_states/inert.pt').write_bytes(b'explicit inert transport specimen, not a loadable state')
    observer = NativeObserver()
    sequence = {'chain_id': 'B', 'chain_type': 'PROTEIN', 'sequence': 'A[MSE]',
                'positions': [{'chain_id': 'B', 'res_id': 12, 'ins_code': 'A', 'component_id': 'MSE'}]}
    for name, population in [('final', 'final_beam'), ('rollout', 'completed_rollout')]:
        path = root / f'{name}.cif'; path.write_bytes(cif_ensemble())
        observer.events.append({'event': 'native_writer', 'example_id': name, 'path': str(path),
                                'population': population, 'sequences': [sequence], 'reward': 0})
    observer.events.extend([{'event': 'search_scoring', 'population': 'ranking_intermediate', 'reward': 0},
                            {'event': 'final_population', 'count': 1, 'observed': True}])
    observer.errors.append('Explicit observation-error test specimen')
    manifest = publish(root, normalize_request({'input': {'kind': 'unconditional', 'name': 'specimen',
        'chains': [{'chain_id': 'B', 'min_length': 2, 'max_length': 2}]}}), {'execution_status': 'failed'}, observer)
    async with store() as session:
        job = await session.get(Job, 'job')
        job.model_id, job.mode, job.params, job.status = 'rfd4_proteina', 'generate', {}, 'failed'
        job.output_dir = str(output)
        await publish_native_generation(session, job, output)
        await session.commit()
    async with store() as session:
        page = await read_published_generation_page(await session.get(Job, 'job'), session)
        assert page['record_counts'] == {'total': 4, 'molecular_records': 2, 'observations': 2,
            'populations': {'final_beam': 1, 'completed_rollout': 1, 'ranking_intermediate': 1, 'event': 1}}
        assert page['records'][0]['sequences'] == [sequence]
        assert page['records'][2]['record_kind'] == 'observation'
        assert page['native_request'] == manifest['request']
        assert page['execution_status'] == 'failed'
        if os.environ.get('BMS_RESULTS_POPULATION_WIRE'):
            Path(os.environ['BMS_RESULTS_POPULATION_WIRE']).write_text(json.dumps(page))
            Path(os.environ['BMS_RESULTS_POPULATION_WIRE'] + '.schemas.json').write_text(json.dumps({mode: parameter_schema(mode) for mode in ['generate', 'evaluate']}))

def test_continuation_sparse_native_requests():
    annotated = {'input': {'kind': 'annotated', 'slots': ['selected']}}
    for request, mode in [(annotated, 'generate'), (annotated, 'evaluate'),
                          ({**annotated, 'groups': {'generation': 'inverse_folding'}}, 'generate'),
                          ({**annotated, 'resume_slot': 'resume', 'settings': {'seed': 0}}, 'generate')]:
        normalized = normalize_request(request, mode)
        assert normalized['input'] == annotated['input']
        if 'settings' in request:
            assert normalized['settings']['seed'] == 0
