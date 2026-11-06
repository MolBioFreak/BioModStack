"""Real scratch custody/HTTP and exact readback mapping. Native image test is separate.

Transport doubles below are explicitly not evidence of native query evaluation.
"""
import copy
import hashlib
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from database import get_session
from routers import models
from services import rfd4_condition_inspection as inspection
from tests.test_binder_source_materialization import source_api, cif
from test_project_manager_adapters import stores
from scripts.bms_rfd4.inputs import condition_example_spec


def request():
    return {'input': {'kind': 'condition_spec', 'global_args': {'input_as_annotated_cif': True},
            'examples': [{'name': 'current', 'sources': [{'slot': 'a'}],
                          'conditions': {'C_SEQ': {'true': [{'select': 'atom_name == "CA"'}]}}}]},
            'row': {'exampleIndex': 0, 'family': 'C_SEQ', 'value': 'true', 'rowIndex': 0, 'select': 'atom_name == "CA"'},
            'native_sources': {'a': {'path': 'inputs/source.cif', 'model_number': 7}}}


def test_shared_native_spec_preserves_order_override_and_exact_global_replacement():
    payload = request(); inp = payload['input']; example = inp['examples'][0]
    inp['global_args']['conditions'] = {'C_CRD': {'false': []}}
    example['sources'].append({'slot': 'b'})
    snapshot = copy.deepcopy(inp)
    spec = condition_example_spec(inp, example, {'a': '/a.cif', 'b': '/b.cif'})
    assert spec['input'] == ['/a.cif', '/b.cif'] and spec['input_as_annotated_cif'] is True
    assert spec['conditions'] == example['conditions']
    assert inp == snapshot
    example['sources'][1]['input_as_annotated_cif'] = False
    with pytest.raises(ValueError, match='one input_as_annotated_cif policy'):
        condition_example_spec(inp, example, {'a': '/a.cif', 'b': '/b.cif'})
    example['sources'][0]['input_as_annotated_cif'] = False
    assert condition_example_spec(inp, example, {'a': '/a.cif', 'b': '/b.cif'})['input_as_annotated_cif'] is False


@pytest.mark.asyncio
async def test_real_http_exact_selected_model_and_readonly_request(source_api, monkeypatch):
    _, session, _, root = source_api
    (root / 'inputs/source.cif').write_text(cif(models=(3, 7)))
    payload = request()
    payload['native_sources']['unused'] = {'historical_unrecognized_binding': True}
    payload['input']['examples'].insert(0, {'name': 'inactive-incomplete', 'sources': [{'slot': 'missing'}]})
    payload['row']['exampleIndex'] = 1
    before = copy.deepcopy(payload); calls = []
    def native_double(body):
        calls.append(body)
        from Bio.PDB.MMCIF2Dict import MMCIF2Dict
        columns = MMCIF2Dict(body['paths']['a'])
        assert set(columns['_atom_site.pdbx_PDB_model_num']) == {'7'}
        return {'atoms': [{'chain_id': 'A', 'res_id': 1, 'ins_code': 'B', 'atom_name': 'CA', 'res_name': 'ALA'}],
                'row': body['row'], 'example_name': 'current', 'input_as_annotated_cif': True,
                'runtime_image': 'transport-double', 'scope': 'Native row targets'}
    monkeypatch.setattr(inspection, 'run_condition_inspection', native_double)
    app = FastAPI(); app.include_router(models.router, prefix='/api/models')
    async def session_dep(): yield session
    app.dependency_overrides[get_session] = session_dep
    async with AsyncClient(transport=ASGITransport(app), base_url='http://fixture') as client:
        response = await client.post('/api/models/rfd4_proteina/condition-inspection', json=payload)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['sources']['a']['identities'] == [{'modelNumber': 7, 'atomIndex': 0,
                'labelAsymId': 'A', 'labelSeqId': 1, 'authAsymId': 'a', 'authSeqId': 42,
                'insertionCode': 'B', 'componentId': 'ALA', 'labelAtomId': 'CA'}]
        assert result['sources']['a']['sha256'] == hashlib.sha256(Path(calls[0]['paths']['a']).read_bytes()).hexdigest()
        assert not result['unresolved_atoms'] and payload == before
        # Multi-model uncertainty is only an inspection error; no job path is involved.
        del payload['native_sources']['a']['model_number']
        response = await client.post('/api/models/rfd4_proteina/condition-inspection', json=payload)
        assert response.status_code == 422 and 'explicit source MODEL' in response.text
        assert len(calls) == 1
        payload['native_sources']['a']['expected_sha256'] = '0' * 64
        response = await client.post('/api/models/rfd4_proteina/condition-inspection', json=payload)
        assert response.status_code == 422 and 'bytes' in response.text
        assert len(calls) == 1


def test_mapping_never_guesses_source_collisions_altloc_or_created_atoms():
    atom = {'chain_id': 'X', 'res_id': 9, 'ins_code': 'B', 'atom_name': 'CA', 'res_name': 'GLY'}
    receipt = {'model_number': 7, 'model_numbers': [3, 7], 'source_positions': [{
        'model_number': 7, 'label_asym_id': 'X', 'label_seq_id': 9, 'auth_asym_id': 'author', 'auth_seq_id': 80,
        'label_comp_id': 'GLY', 'pdbx_PDB_ins_code': 'B', 'atoms': [{'label_atom_id': 'CA', 'model_atom_index': 4}]}]}
    mapped, unresolved = inspection.map_condition_atoms([atom], {'one': receipt})
    assert mapped['one'][0]['atomIndex'] == 4 and not unresolved
    assert inspection.map_condition_atoms([atom], {'one': receipt, 'two': receipt}) == ({'one': [], 'two': []}, [atom])
    changed = copy.deepcopy(receipt); changed['source_positions'][0]['atoms'][0]['label_alt_id'] = 'A'
    assert inspection.map_condition_atoms([atom], {'one': changed}) == ({'one': []}, [atom])
    assert inspection.map_condition_atoms([{**atom, 'atom_name': 'created'}], {'one': receipt})[0] == {'one': []}
    mapped, unresolved = inspection.map_condition_atoms([{**atom, 'altloc_id': 'A'}], {'one': changed})
    assert mapped['one'][0]['altLoc'] == 'A' and not unresolved


@pytest.mark.asyncio
async def test_missing_runtime_is_advisory_http_error_not_a_request_mutation(source_api, monkeypatch):
    _, session, _, root = source_api
    (root / 'inputs/source.cif').write_text(cif(models=(7,)))
    payload = request(); before = copy.deepcopy(payload)
    def unavailable(_): raise ValueError('Native runtime unavailable')
    monkeypatch.setattr(inspection, 'run_condition_inspection', unavailable)
    with pytest.raises(ValueError, match='Native runtime unavailable'):
        await inspection.inspect_rfd4_condition(payload, session)
    assert payload == before


@pytest.mark.asyncio
async def test_pinned_source_cpu_through_real_http_custody_and_mapping(source_api, monkeypatch):
    """Real native subprocess; explicitly distinct from installed image qualification."""
    import subprocess
    import json
    interpreter = os.environ.get('BMS_TEST_RFD4_PYTHON')
    source = os.environ.get('BMS_TEST_RFD4_SOURCE')
    if not interpreter or not source:
        pytest.skip('Explicit isolated native interpreter/source required for real HTTP evaluator bridge')
    _, session, _, root = source_api
    (root / 'inputs/source.cif').write_text(cif(models=(3, 7)))
    scripts = Path(__file__).resolve().parents[3] / 'scripts'
    def run_native(body):
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(scripts), str(Path(source) / 'src'), os.environ.get('BMS_TEST_RFD4_EXTRA_PATH', '')])}
        result = subprocess.run([interpreter, str(scripts / 'inspect_rfd4_condition.py')],
                                input=json.dumps(body), text=True, capture_output=True, timeout=120, env=env)
        assert result.returncode == 0, result.stderr
        return {**json.loads(result.stdout), 'runtime_image': 'pinned-source CPU, not selected image'}
    monkeypatch.setattr(inspection, 'run_condition_inspection', run_native)
    app = FastAPI(); app.include_router(models.router, prefix='/api/models')
    async def session_dep(): yield session
    app.dependency_overrides[get_session] = session_dep
    async with AsyncClient(transport=ASGITransport(app), base_url='http://fixture') as client:
        response = await client.post('/api/models/rfd4_proteina/condition-inspection', json=request())
    assert response.status_code == 200, response.text
    result = response.json()
    assert not result['unresolved_atoms']
    assert result['sources']['a']['identities'] == [{'modelNumber': 7, 'atomIndex': 0,
        'labelAsymId': 'A', 'labelSeqId': 1, 'authAsymId': 'a', 'authSeqId': 42,
        'insertionCode': 'B', 'componentId': 'ALA', 'labelAtomId': 'CA'}]


def test_selected_native_image_evaluates_current_row(tmp_path):
    image = os.environ.get('BMS_TEST_RFD4_IMAGE')
    if not image or not Path(image).is_file():
        pytest.skip('Selected RFD4 image unavailable; native condition evaluation NOT qualified by transport tests')
    source = Path(__file__).resolve().parents[2] / 'frontend/tests/fixtures/rfd4-saved-conditions.cif'
    payload = request(); payload['input']['examples'][0]['sources'] = [{'slot': 'a', 'input_as_annotated_cif': True}]
    payload['input']['examples'][0]['conditions']['C_SEQ']['true'][0]['select'] = 'atom_name == "UNK"'
    payload['row']['select'] = 'atom_name == "UNK"'
    result = inspection.run_condition_inspection({'input': payload['input'], 'row': payload['row'], 'paths': {'a': str(source)}}, image=image)
    assert result['atoms'] and all(atom['atom_name'] == 'UNK' for atom in result['atoms'])
    assert result['input_as_annotated_cif'] is True
