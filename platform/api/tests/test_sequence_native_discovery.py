"""Public discovery carries model-owned nested schemas, not generic object placeholders."""
import ast
from pathlib import Path

from fastapi import FastAPI
import httpx
from jsonschema import Draft202012Validator
import pytest

from routers import models, sequence_native
from services.caliby_native import normalize_request
from services.ligandmpnn_design import normalize_design_params, NativeOptions


@pytest.mark.asyncio
@pytest.mark.parametrize('model,mode', [
    ('caliby_experimental', 'ensemble_design'), ('caliby_experimental', 'sidechain_pack'),
    ('ligandmpnn', 'ligand_aware'), ('ligandmpnn', 'ntp_aware'),
    ('ligandmpnn', 'metal_aware'), ('ligandmpnn', 'dna_aware'),
])
async def test_detail_modes_and_catalog_preserve_native_nested_contract(model, mode):
    app = FastAPI()
    app.include_router(models.router, prefix='/api/models')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
        detail = await client.get(f'/api/models/{model}')
        modes = await client.get(f'/api/models/{model}/modes')
        catalog = await client.get('/api/models?include_experimental=true')
    assert detail.status_code == modes.status_code == catalog.status_code == 200
    schema = next(m for m in detail.json()['modes'] if m['id'] == mode)['parameter_schema']
    assert next(m for m in modes.json()['modes'] if m['id'] == mode)['parameter_schema'] == schema
    listed = next(item for item in catalog.json() if item['id'] == model)
    assert next(m for m in listed['modes'] if m['id'] == mode)['parameter_schema'] == schema
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    if model == 'caliby_experimental':
        state = {'state_id': 'declared-state', 'path': '/owned/source.cif'}
        request = ({'ensembles': [{'ensemble_id': 'declared-group', 'states': [state]}], 'omit_aas': [], 'verbose': False}
                   if mode == 'ensemble_design' else {'structures': [state]})
        normalized = normalize_request(mode, request)
        assert schema['discriminator']['mapping'].keys() == {mode}
        assert '$defs' in schema
        assert list(validator.iter_errors({**normalized, 'task': 'design'}))
    else:
        normalized = normalize_design_params(mode, {'target_pdb': '/owned/source.cif',
            'design_seed': 0, 'remove_waters': False, 'remove_ccds': [], 'temperature': None,
            'pair_bias_per_residue_pair': {'A1': {'B2': {'ALA': {'GLY': 0.}}}}})
        native = NativeOptions.model_json_schema()['properties']
        assert 'seed' not in schema['properties']
        assert schema['properties']['design_seed'] == native['seed']
        for key, value in native.items():
            if key != 'seed':
                assert schema['properties'][key] == value
        assert list(validator.iter_errors({**normalized, 'pair_bias_per_residue_pair': {'A1': 'not-an-axis'}}))
    validator.validate(normalized)
    assert list(validator.iter_errors({**normalized, 'unknown_scientific_field': True}))


@pytest.mark.asyncio
async def test_diagnostic_keeps_its_own_existing_contract():
    payload = await models.get_model('ligandmpnn')
    diagnostic = next(mode for mode in payload['modes'] if mode['id'] == 'interface_context')
    assert diagnostic['parameter_schema'] is None
    assert 'seed' in diagnostic['params'] and 'design_seed' not in diagnostic['params']


def test_production_app_mounts_native_router_once_with_exact_public_paths():
    # Execute only the actual production include call, without starting services
    # or invoking the application's unrelated startup/installation owners.
    path = Path(__file__).parents[1] / 'main.py'
    tree = ast.parse(path.read_text())
    calls = [node for node in tree.body if isinstance(node, ast.Expr)
             and isinstance(node.value, ast.Call)
             and isinstance(node.value.func, ast.Attribute)
             and node.value.func.attr == 'include_router'
             and any(isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name)
                     and arg.value.id == 'sequence_native' for arg in node.value.args)]
    assert len(calls) == 1
    app = FastAPI()
    exec(compile(ast.Module(body=calls, type_ignores=[]), str(path), 'exec'),
         {'app': app, 'sequence_native': sequence_native})
    paths = set(app.openapi()['paths'])
    assert '/api/jobs/{job_id}/caliby-native-results' in paths
    assert '/api/jobs/{job_id}/ligandmpnn-design-results' in paths
