"""Inert discovery/public-normalization/preparation checks; no app/services/model work.

Also supplies real discovery and wire replay to the dedicated mounted suite.
"""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]


def owners():
    sys.path[:0] = [str(ROOT), str(ROOT / 'platform/api')]
    # Do not execute routers/__init__ (which mounts unrelated application routes).
    if 'routers' not in sys.modules:
        package = types.ModuleType('routers')
        package.__path__ = [str(ROOT / 'platform/api/routers')]
        sys.modules['routers'] = package
    from routers.models import get_generation_settings
    from routers.jobs import normalize_job_request
    from schemas import JobCreate
    return get_generation_settings, normalize_job_request, JobCreate


def inventory():
    discover, _, _ = owners()
    return asyncio.run(discover('protein_modification_experimental', 'de_novo_design', 'rfd3'))


def replay(params, destination=None):
    _, normalize, JobCreate = owners()
    normalized = normalize(JobCreate(name='rfd3-gap', model_id='protein_modification_experimental', mode='de_novo_design', params=params))
    request = normalized.params['rfd3_generation_request']
    from scripts.rfd3_generation import contract
    previous = sys.modules.get('contract')
    sys.modules['contract'] = contract
    try:
        spec = importlib.util.spec_from_file_location('rfd3_gap_prepare', ROOT / 'scripts/rfd3_generation/prepare_native_input.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if destination is None and 'source' in request:
            import tempfile
            with tempfile.TemporaryDirectory(prefix='rfd3-gap-') as directory:
                native, receipt = module.prepare(request, source_destination=Path(directory) / 'source.cif')
        else:
            native, receipt = module.prepare(request, source_destination=destination)
    finally:
        if previous is None:
            sys.modules.pop('contract', None)
        else:
            sys.modules['contract'] = previous
    return {'request': request, 'native': native, 'receipt': receipt}


def test_public_inventory_and_admission_unchanged():
    from fastapi import HTTPException
    import pytest
    data = inventory()
    assert data['tasks'] == data['generation_mode']['enum'] == ['unconditional_monomer']
    assert 'partial_diffusion' in data['native_tasks']
    for name in ('min_length', 'max_length'):
        assert data['generation'][name]['minimum'] == 40
        assert data['generation'][name]['maximum'] == 600
        assert data['native_generation'][name]['minimum'] == 1
        assert 'maximum' not in data['native_generation'][name]
    for length in (20, 40, 600, 601):
        params = {'generator': 'rfd3', 'min_length': length, 'max_length': length}
        if length in (40, 600):
            assert replay(params)['native']['generation_0']['length'] == f'{length}-{length}'
        else:
            with pytest.raises(HTTPException) as error:
                replay(params)
            assert error.value.status_code == 422
    with pytest.raises(HTTPException):
        replay({'generator': 'rfd3', 'min_length': 100, 'max_length': 40})
    with pytest.raises(HTTPException):
        replay({'generator': 'rfd3', 'generation_mode': 'conditioned'})


def test_sparse_execution_and_source_free_conditions():
    for supplied in ({}, {'skip_existing': False}, {'skip_existing': True}):
        condition = {'symmetry': {'id': 'C2', 'is_symmetric_motif': False, 'is_unsym_motif': None}, 'plddt_enhanced': False, 'ori_token': [0, 0, 0], 'select_hotspots': None}
        result = replay({'generator': 'rfd3', 'min_length': 40, 'max_length': 40, 'conditioning': condition, **supplied})
        assert result['request']['conditioning'] == condition
        assert 'source' not in result['request']
        execution = result['request']['execution']
        assert ('skip_existing' in execution) == ('skip_existing' in supplied)
        if supplied:
            assert execution['skip_existing'] is supplied['skip_existing']
        for key, value in condition.items():
            assert result['native']['generation_0'][key] == value


if __name__ == '__main__':
    if sys.argv[1] == 'inventory':
        print(json.dumps(inventory()))
    elif sys.argv[1] == 'replay':
        print(json.dumps(replay(json.load(sys.stdin))))
