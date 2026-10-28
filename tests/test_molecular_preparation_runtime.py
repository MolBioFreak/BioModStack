"""Real isolated CPU transport; run from the dependency-light API interpreter."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'platform/api'))
sys.path.insert(0, str(ROOT))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


payload = load('geometry_fixture', ROOT / 'platform/api/tests/test_molecular_geometry.py').payload


@pytest.fixture
def client(monkeypatch):
    python = os.environ.get('STUDIO_TEST_PYTHON')
    if not python:
        pytest.skip('requires the prepared isolated CPU runtime')
    monkeypatch.setenv('BMS_MOLECULAR_PREPARATION_PYTHON', python)
    app = FastAPI()
    for name in ('molecular_construction', 'molecular_geometry'):
        app.include_router(load(name, ROOT / f'platform/api/routers/{name}.py').router)
    with TestClient(app) as client:
        yield client


def direct(operation, request):
    # Independently call the unchanged native leaf in the same locked interpreter.
    code = '''import json,sys,os,types
services=types.ModuleType('services')
services.__path__=[os.path.join(os.environ['PYTHONPATH'].split(os.pathsep)[0],'services')]
sys.modules['services']=services
from services.molecular_construction import construct_graph, ConstructionUnavailable
from services.molecular_construction_recipe import MolecularConstructionRecipe
from services.molecular_geometry import construct_geometry, GeometryUnavailable
from services.molecular_geometry_recipe import MolecularGeometryRequest
schema,fn = (MolecularConstructionRecipe,construct_graph) if sys.argv[1]=='graph' else (MolecularGeometryRequest,construct_geometry)
try: print(json.dumps({'result':fn(schema.model_validate_json(sys.stdin.read())).model_dump(mode='json')}))
except (ConstructionUnavailable,GeometryUnavailable,ValueError,RuntimeError) as e: print(json.dumps({'error':type(e).__name__,'detail':str(e)}))
'''
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'platform/api') + os.pathsep + str(ROOT)}
    result = subprocess.run([os.environ['STUDIO_TEST_PYTHON'], '-B', '-c', code, operation],
                            input=json.dumps(request), capture_output=True, text=True, env=env, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize('form', ['abdna', 'adna', 'arna'])
def test_real_typed_graph_and_geometry_exact_direct_native(client, form):
    request = payload(form)
    from services.molecular_construction_recipe import ConstructionResult
    from services.molecular_geometry_recipe import MolecularGeometryResult
    for op, route, data, schema in [
        ('graph', '/api/molecular-construction/graph', request['construction'], ConstructionResult),
        ('geometry', '/api/molecular-geometry/construct', request, MolecularGeometryResult),
    ]:
        response = client.post(route, json=data)
        assert response.status_code == 200, response.text
        assert response.json() == direct(op, data)['result']
        schema.model_validate(response.json())
        if op == 'geometry':
            result = response.json()
            assert hashlib.sha256(result['coordinate_text'].encode()).hexdigest() == result['coordinate_sha256']
            assert len(result['atom_map']) == len(result['graph']['atoms'])


def test_native_errors_remain_exact_and_schema_validation_unchanged(client, monkeypatch):
    request = payload()
    request['construction']['pairing'] = []
    native = direct('geometry', request)
    assert native['error'] == 'ValueError'
    response = client.post('/api/molecular-geometry/construct', json=request)
    assert response.status_code == 422
    assert response.json() == {'detail': native['detail']}
    monkeypatch.delenv('BMS_MOLECULAR_GEOMETRY_BUNDLE', raising=False)
    request = payload()
    native = direct('geometry', request)
    assert native['error'] == 'GeometryUnavailable'
    response = client.post('/api/molecular-geometry/construct', json=request)
    assert response.status_code == 503
    assert response.json() == {'detail': native['detail']}
    assert client.post('/api/molecular-geometry/construct', json={}).status_code == 422
    assert client.post('/api/molecular-construction/graph', json={}).status_code == 422


def test_missing_interpreter_only_affects_explicit_preparation(client, monkeypatch):
    monkeypatch.setenv('BMS_MOLECULAR_PREPARATION_PYTHON', '/nonexistent/isolated-python')
    response = client.post('/api/molecular-construction/graph', json=payload()['construction'])
    assert response.status_code == 503
    assert response.json() == {'detail': 'isolated molecular preparation runtime unavailable'}
    assert client.get('/api/molecular-construction/schema').status_code == 200


def test_protocol_runtime_error_preserves_original_route_status(client, monkeypatch):
    # Protocol-only fault injection, separate from real native execution above.
    from services import molecular_preparation_transport as transport
    from types import SimpleNamespace
    monkeypatch.setattr(transport.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        stdout=json.dumps({'error': 'RuntimeError', 'detail': 'native identity mismatch'})))
    graph = client.post('/api/molecular-construction/graph', json=payload()['construction'])
    geometry = client.post('/api/molecular-geometry/construct', json=payload())
    assert graph.status_code == 422
    assert geometry.status_code == 502
    assert graph.json() == geometry.json() == {'detail': 'native identity mismatch'}


def test_service_descriptor_uses_existing_environment_and_lifecycle(tmp_path, monkeypatch):
    import biomodstack_services as manager
    monkeypatch.setattr(manager, 'runtime_profile_config_dir', lambda: tmp_path / 'biomodstack')
    assert manager.studio_development_units(ROOT) == ({}, '')
    descriptor = {'schema_version': 'bms.atomworks-studio-runtime.v1', 'python': '/isolated/bin/python',
                  'environment_file': '/private/studio.env', 'sessions': '/private/sessions', 'port': 18092}
    directory = tmp_path / 'biomodstack'
    directory.mkdir()
    (directory / 'atomworks-studio-development.json').write_text(json.dumps(descriptor))
    units, environment = manager.studio_development_units(ROOT)
    unit = units[manager.STUDIO_SERVICE]
    assert 'EnvironmentFile=/private/studio.env' in unit
    assert environment == 'EnvironmentFile=/private/studio.env'
    assert 'PartOf=' + manager.DEV_TARGET_UNIT in unit
    assert 'KillMode=control-group' in unit
    assert '--host 127.0.0.1 --port 18092' in unit
    assert 'SECRET=' not in unit
    assert 'Requires=' not in unit
    monkeypatch.delenv('BMS_TELEMETRY_DB_PATH', raising=False)
    monkeypatch.setattr(manager, 'install_profile_snapshot', lambda **kwargs: {'resolved': {
        'container_dir': str(tmp_path / 'containers'), 'data_root': str(tmp_path / 'data')}})
    configured = manager.render_user_units(ROOT, runtime_mode='dev')
    production = manager.render_user_units(ROOT, runtime_mode='container')
    assert manager.STUDIO_SERVICE in configured
    assert environment in configured[manager.API_SERVICE]
    assert environment not in configured[manager.FRONTEND_SERVICE]
    assert environment not in configured[manager.DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE]
    assert f'Wants={manager.STUDIO_SERVICE}' in configured[manager.API_SERVICE]
    assert manager.STUDIO_SERVICE not in production
    (directory / 'atomworks-studio-development.json').unlink()
    assert production == manager.render_user_units(ROOT, runtime_mode='container')
    unconfigured = manager.render_user_units(ROOT, runtime_mode='dev')
    for name, text in unconfigured.items():
        if name not in (manager.API_SERVICE, manager.DEV_TARGET_UNIT):
            assert text == configured[name]


@pytest.mark.parametrize('polymer', ['DNA', 'RNA'])
def test_single_strand_graph_and_original_native_graph_error(client, polymer):
    recipe = payload('arna' if polymer == 'RNA' else 'abdna')['construction']
    recipe['strands'] = recipe['strands'][:1]
    recipe['pairing'] = []
    response = client.post('/api/molecular-construction/graph', json=recipe)
    assert response.status_code == 200, response.text
    assert response.json() == direct('graph', recipe)['result']
    assert len(response.json()['connected_components']) == 1
    recipe['covalent_links'] = [{'order': 'single',
        'first': {'strand_id': 'first', 'copy_id': 'copy', 'residue_id': 'r1', 'atom_name': 'absent'},
        'second': {'strand_id': 'first', 'copy_id': 'copy', 'residue_id': 'r2', 'atom_name': "O3'"}}]
    native = direct('graph', recipe)
    assert native['error'] == 'ValueError'
    response = client.post('/api/molecular-construction/graph', json=recipe)
    assert response.status_code == 422
    assert response.json() == {'detail': native['detail']}
    # RDKit raises a ValueError subclass: retain original 422/detail, not 503.
    recipe['covalent_links'][0]['first']['atom_name'] = "C1'"
    recipe['covalent_links'][0]['second']['atom_name'] = "C1'"
    recipe['covalent_links'][0]['order'] = 'triple'
    native = direct('graph', recipe)
    assert native['error'] == 'AtomValenceException'
    response = client.post('/api/molecular-construction/graph', json=recipe)
    assert response.status_code == 422
    assert response.json() == {'detail': native['detail']}
