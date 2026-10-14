"""Native batch semantics through schemas, HTTP, SQL and the actual compiler."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from database import Job
from services.ont_ngs_contract import normalize_ont_launch_params
from services.nextflow import compile_job_nextflow_invocation
from tests.test_ont_prepared_launch import launch, prepare

ROOT = Path(__file__).resolve().parents[3]
SURFACES = ('basecall_dna', 'basecall_rna', 'plasmid_qc', 'construct_screening',
            'methylation_analysis', 'clone_validation')


@pytest.mark.parametrize('surface', SURFACES)
def test_typed_batch_native_range(surface):
    schema = json.loads((ROOT / f'schemas/ngs_molbio/ngs-ont-{surface}-v1.schema.json').read_text())
    field = schema['properties']['batch_size']
    assert field['minimum'] == 0 and 'maximum' not in field
    validator = Draft202012Validator(field)
    for value in (0, 1, 256, 512, 1000000):
        validator.validate(value)
    for value in (-1, 1.5, True, '0'):
        assert list(validator.iter_errors(value)), value
    if surface == 'basecall_dna':
        assert field['x-bms-default-policy'] == 'simplex=64;duplex=32'
    else:
        assert field['default'] == 64


@pytest.mark.parametrize('value', [-1, 0.5, 1.5, True, '1.5', '-1'])
def test_normalizer_rejects_non_native_batch(value):
    with pytest.raises(ValueError, match='integer'):
        normalize_ont_launch_params('ont_basecall_dna', {'dorado_batch_size': value})


def test_defaults_and_legacy_numeric_string_preserve_native_semantics():
    for mode, default in [('simplex', 64), ('duplex', 32)]:
        params = {'dorado_basecall_mode': mode, 'duplex_pairs': '/inputs/pairs.txt'} if mode == 'duplex' else {}
        assert normalize_ont_launch_params('ont_basecall_dna', params)['dorado_batch_size'] == default
        for value in (0, '0', 512):
            assert normalize_ont_launch_params('ont_basecall_dna', {**params, 'dorado_batch_size': value})['dorado_batch_size'] == int(value)


@pytest.mark.asyncio
@pytest.mark.parametrize('surface', SURFACES)
@pytest.mark.parametrize('value', [0, 512])
async def test_batch_prepare_effective_sql_replay_and_compiler(launch, surface, value):
    workflow = 'wf_clone_validation' if surface == 'clone_validation' else f'ont_{surface}'
    body = {'execution_target_id': None, 'params': {
        'pod5_dir': str(launch.pod5), 'dorado_batch_size': value,
        'molbio_ngs_receipt_id': launch.receipt.id,
    }}
    first = await prepare(launch, body, workflow)
    assert first['request']['params']['dorado_batch_size'] == value
    assert await prepare(launch, first['request'], workflow) == first
    response = await launch.client.post(f'/api/ont/ngs/{workflow}/submit', json=first['request'])
    assert response.status_code == 201, response.text
    async with launch.factory() as session:
        job = await session.get(Job, response.json()['id'])
        assert job.params['dorado_batch_size'] == value
        normalized = normalize_ont_launch_params(workflow, job.params)
        assert normalized['dorado_batch_size'] == value
        invocation = compile_job_nextflow_invocation(job, normalized, job.output_dir)
        assert invocation.native_parameters['dorado_batch_size'] == value
        assert json.loads(invocation.requested_json)['dorado_batch_size'] == value
        command = list(invocation.command)
        assert command[command.index('--dorado_batch_size') + 1] == str(value)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,value,expected', [('simplex', None, 64), ('duplex', None, 32), ('duplex', 0, 0)])
async def test_mode_defaults_and_duplex_auto_at_request_boundary(launch, mode, value, expected):
    pairs = launch.pod5 / 'pairs.txt'
    pairs.write_text('template complement\n')
    params = {'pod5_dir': str(launch.pod5), 'dorado_basecall_mode': mode}
    if mode == 'duplex':
        params['duplex_pairs'] = str(pairs)
    if value is not None:
        params['dorado_batch_size'] = value
    first = await prepare(launch, {'params': params}, 'ont_basecall_dna')
    if value is None:
        assert 'dorado_batch_size' not in first['request']['params']
    else:
        assert first['request']['params']['dorado_batch_size'] == expected
    response = await launch.client.post('/api/ont/ngs/ont_basecall_dna/submit', json=first['request'])
    assert response.status_code == 201, response.text
    async with launch.factory() as session:
        job = await session.get(Job, response.json()['id'])
        assert job.params['dorado_batch_size'] == expected
        invocation = compile_job_nextflow_invocation(job, job.params, job.output_dir)
        assert invocation.native_parameters['dorado_batch_size'] == expected
