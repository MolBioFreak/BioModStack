"""Shared native routes/compiler; no inference, providers or live stores."""
import json
from pathlib import Path
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from routers import models, jobs
from schemas import JobCreate
from services.nextflow import compile_nextflow_invocation, resolve_nextflow_entrypoint
from component_runtime import SourceIdentity
from model_registry import workflow_pack_dependencies, get_registry
from test_project_workflow_setups import setup_store
from test_project_generation_bound_jobs import isolated_core
from test_project_setup_job_preparation import isolate_applied_local_policy


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [2, 2.7])
async def test_actual_job_http_insertion(isolated_core, setup_store, isolate_applied_local_policy, count):
    from database import get_session, Job
    from experiment_database import get_experiment_session
    app = FastAPI(); app.include_router(jobs.router, prefix='/jobs')
    async def core_session():
        async with isolated_core() as session: yield session
    async def project_session():
        async with setup_store() as session: yield session
    app.dependency_overrides[get_session] = core_session
    app.dependency_overrides[get_experiment_session] = project_session
    async with AsyncClient(transport=ASGITransport(app), base_url='http://fixture') as client:
        payload = dict(name='native-ingress', model_id='protein_modification_experimental', mode='de_novo_design',
                       execution_target_id=None, params={'generator': 'rfd3', 'num_designs': count})
        response = await client.post('/jobs', json=payload)
        assert response.status_code == 201, response.text
        async with isolated_core() as session:
            job = await session.get(Job, response.json()['id'])
            assert job.params['num_designs'] == 2
            assert job.parent_job_id is None and job.execution_target_id is None
        native = await client.post('/jobs', json=dict(name='RFD4-ingress', model_id='rfd4_proteina',
            mode='generate', execution_target_id=None, params={'rfd4_request': rfd4()}))
        assert native.status_code == 201, native.text
        async with isolated_core() as session:
            job = await session.get(Job, native.json()['id'])
            request = json.loads(Path(job.params['rfd4_prepared_request']).read_text())
            assert request['request'] == job.params['rfd4_request']
            assert job.parent_job_id is None and job.execution_target_id is None



def rfd4():
    return {'input': {'kind': 'unconditional', 'name': 'sample',
        'chains': [{'chain_id': 'A', 'min_length': 100, 'max_length': 100}]},
        'settings': {'seed': 0, 'metrics_tags': []}}


@pytest.mark.asyncio
async def test_actual_discovery_routes():
    app = FastAPI(); app.include_router(models.router, prefix='/models')
    async with AsyncClient(transport=ASGITransport(app), base_url='http://fixture') as client:
        for model, field in [('rfd4_proteina', 'rfd4_request'), ('complexa', 'complexa_request')]:
            detail = await client.get('/models/' + model)
            assert detail.status_code == 200, detail.text
            assert all(m['parameter_schema'] for m in detail.json()['modes'])
            inventory = await client.get('/models/' + model + '/generation-settings?mode=generate')
            assert inventory.status_code == 200, inventory.text
            assert inventory.json().get('request_field', inventory.json().get('request_key')) == field
        parent = await client.get('/models/protein_modification_experimental/generation-settings?mode=de_novo_design&generator=rfd4_proteina&task=evaluate')
        assert parent.status_code == 200, parent.text
        assert parent.json()['mode'] == 'evaluate'
        for generator in ('rfd3', 'laproteina', 'disco'):
            response = await client.get('/models/protein_modification_experimental/generation-settings',
                params={'mode': 'de_novo_design', 'generator': generator})
            assert response.status_code == 200, response.text
            assert response.json()['model_id'] == generator


@pytest.mark.parametrize('parent', [False, True])
def test_rfd4_real_normalization_pure_compile_and_materialization(tmp_path, parent):
    model = 'protein_modification_experimental' if parent else 'rfd4_proteina'
    mode = 'de_novo_design' if parent else 'generate'
    params = {'rfd4_request': rfd4(), **({'generator': 'rfd4_proteina'} if parent else {})}
    request = JobCreate(name='native', model_id=model, mode=mode, params=params, execution_target_id=None)
    normalized = jobs.normalize_job_request(request)
    output = tmp_path / 'not-allocated'
    invocation = compile_nextflow_invocation(model, mode, normalized.params, str(output), job_id='fixture',
        source_identity=SourceIdentity('a' * 40, 'b' * 40), _preview_only=True)
    assert not output.exists()
    assert invocation.entrypoint == 'workflows/rfd4_proteina.nf'
    assert invocation.native_parameters['rfd4_request'] == normalized.params['rfd4_request']
    assert len(invocation.generated_inputs) == 2
    for item in invocation.generated_inputs: item.materialize(output)
    payload = json.loads((output / invocation.generated_inputs[0].relative_path).read_text())
    assert payload['request'] == normalized.params['rfd4_request']
    assert invocation.execution_plan.complete, invocation.execution_plan.metadata.blockers
    stages = invocation.execution_plan.metadata.static_components
    assert [stage.authority for stage in stages] == ['modules/rfd4_proteina.nf:RunRFD4Proteina']


def test_named_pack_separate_from_exact_request():
    refs = workflow_pack_dependencies('de_novo_design')
    assert len(refs) == len(set(refs))
    paths = {ref.relative_path for ref in refs}
    assert {'rfd4_proteina.sif', 'complexa.sif', 'rfd4-proteina', 'gromacs-md-2025.3.sif',
            'disco.sif', 'laproteina.sif', 'foundry.sif', 'shape_rfd3.sif'} <= paths
    assert 'protenix/mmcif' not in paths


@pytest.mark.parametrize('model', ['rfd4_proteina', 'complexa'])
def test_unknown_native_mode_never_falls_through(model):
    with pytest.raises(ValueError):
        resolve_nextflow_entrypoint(effective_profile='rfd3', model_id=model, mode='unknown')


def test_registry_uses_native_owner_errors():
    assert get_registry().validate_job_params('rfd4_proteina', 'generate', {'rfd4_request': rfd4()}) == []
    request = rfd4(); request['settings']['invented_science'] = 1
    assert get_registry().validate_job_params('rfd4_proteina', 'generate', {'rfd4_request': request})
