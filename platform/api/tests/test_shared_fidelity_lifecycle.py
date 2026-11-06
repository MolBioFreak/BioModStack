"""Real scratch SQLite/API retry and historical readback; no app lifespan."""
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI

from database import Design, Job, get_session
from test_core_protein_candidates import setup
from test_esmfold2_native_confidence import app_for
from test_protenix_chemistry_transport import science


@pytest.mark.asyncio
@pytest.mark.parametrize('model_id', ['esmfold2', 'esmfold2_experimental', 'protenix'])
@pytest.mark.parametrize('independent_source', [False, True])
async def test_historical_psce_readback_keeps_upstream_metadata(tmp_path, model_id, independent_source):
    factory, engine = await setup(tmp_path)
    payload = {'sequence': 'ACD', 'mutation': ['A1C'], 'fampnn_avg_psce': 76.68}
    if independent_source:
        payload['producer_model_id'] = 'fampnn'
    try:
        async with factory() as session:
            session.add(Job(id='historical', name='historical', model_id=model_id, mode='predict', params={}))
            session.add(Design(id='old', job_id='historical', name='old', pdb_path=str(tmp_path / 'unretained.pdb'), confidence_metrics={'fampnn': payload}))
            await session.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=await app_for(factory)), base_url='http://test') as client:
            response = await client.get('/api/designs/old')
            assert response.status_code == 200, response.text
            readback = response.json()['confidence_metrics']['fampnn']
            assert readback['sequence'] == 'ACD' and readback['mutation'] == ['A1C']
            assert ('fampnn_avg_psce' in readback) == independent_source
        async with factory() as session:
            assert (await session.get(Design, 'old')).confidence_metrics['fampnn'] == payload
    finally:
        await engine.dispose()


@pytest.mark.parametrize('model_id', ['fampnn', 'proteinmpnn'])
def test_project_sequence_descriptors_derive_native_catalog(model_id):
    from model_registry import get_registry
    from services.protein_project_capabilities import _model_sequence_design_schema
    definition = get_registry().get_model(model_id)
    schema = _model_sequence_design_schema('protein.sequence_design.' + model_id, model_id)
    assert schema['x-bms-executable-authority'] == 'catalogued_model_contract_without_project_dispatch'
    for parameter in definition.params:
        if parameter.type in {'file', 'directory'}:
            continue
        actual = schema['properties'][parameter.name]
        assert actual['default'] == parameter.default
        for key in ('minimum', 'maximum', 'enum', 'pattern'):
            expected = getattr(parameter, key)
            assert actual.get(key) == expected


@pytest.mark.parametrize('capability', ['protein.de_novo.rfd3', 'protein.de_novo.local_redesign'])
def test_project_redesign_uses_native_request_not_superseded_count_descriptor(capability):
    from jsonschema import validate
    from services.protein_project_capabilities import protein_setup_parameter_schema

    schema = protein_setup_parameter_schema(capability)
    assert schema['x-bms-native-editor-draft'] is True
    assert 'num_designs' not in schema['properties']
    request = {'model_id': 'protein_local_redesign', 'mode': 'local_redesign', 'params': {'num_designs': 1001}}
    validate({'native_job_request': request}, schema)


@pytest.mark.parametrize('model_id', ['boltz2', 'esmfold2', 'af2'])
def test_unknown_nested_settings_diagnostics_are_not_validation(model_id):
    from model_registry import get_registry
    params = {'sequence': 'ACD', 'unexpected_nested_science': {'iterations': 999}}
    original = deepcopy(params)
    registry = get_registry()
    diagnostics = registry.parameter_diagnostics(model_id, params)
    assert 'unexpected_nested_science' in diagnostics['settings_outside_catalog']
    assert diagnostics['native_consumption'] == 'not_established_by_catalog_validation'
    assert not any('unexpected_nested_science' in error for error in registry.validate_job_params(model_id, 'predict', params))
    assert params == original


@pytest.mark.asyncio
@pytest.mark.parametrize('model_id', ['boltz2', 'protenix', 'esmfold2', 'fampnn', 'proteinmpnn', 'frustrampnn'])
@pytest.mark.parametrize('remote', [False, True])
async def test_actual_queue_retry_retains_nested_science_cold_sqlite(tmp_path, model_id, remote):
    from routers import queue
    factory, engine = await setup(tmp_path)
    params = {'native_sources': [], 'seed': 0, 'enabled': False,
              'unexpected_nested_science': {'iterations': 999}, 'complex_components': science()['complex_components']}
    try:
        async with factory() as session:
            session.add(Job(id='retry', name='retry', model_id=model_id, mode='predict', params=params,
                            status='failed', queue_status='failed', retry_count=0, max_retries=2,
                            execution_target_id='offline-target' if remote else None,
                            provenance={'remote_execution_receipt': {'attempt': 'old'}} if remote else {}))
            await session.commit()
        app = FastAPI()
        app.include_router(queue.router, prefix='/api')
        async def isolated():
            async with factory() as session:
                yield session
        app.dependency_overrides[get_session] = isolated
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/queue/retry/retry')
            assert response.status_code == 200, response.text
        await engine.dispose()
        async with factory() as session:
            row = await session.get(Job, 'retry')
            assert row.params == params and row.retry_count == 1
            assert row.status == row.queue_status == 'queued'
            if remote:
                assert row.provenance['remote_execution_attempt_history'] == [{'attempt': 'old'}]
                assert 'remote_execution_receipt' not in row.provenance
    finally:
        await engine.dispose()
