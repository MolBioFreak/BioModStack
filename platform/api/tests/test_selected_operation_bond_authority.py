"""Selected operation repairs at normalization, retained child and compiler owners."""
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from schemas import JobCreate, SelectedRoundRequest
from services.binder_selected_round import normalize, prediction_request
from services import nextflow
from services.caliby_native import bind_operation, prepare_for_job, read_prepared_request
from test_protenix_chemistry_transport import compiler_environment

REF = {'kind': 'design', 'job_id': 'root', 'design_id': 'exact-model-2'}
BOND = dict(left_entity=2, right_entity=3, left_copy=2, right_copy=1,
            left_position=2, right_position=2, left_atom='SG', right_atom='SG')
ESM = dict(chain_id1='Y', chain_id2='Z', res_idx1=2, res_idx2=2, atom_idx1=5, atom_idx2=5)


def request(model, params):
    return SelectedRoundRequest.model_validate(dict(operation='round', source_job_id='root',
        idempotency_key='authority', selection={'candidate_refs': [REF]}, round_plan={
        'schema_version': 2, 'stages': [{'id': 'p', 'kind': 'prediction',
        'input': {'selected': True, 'scope': 'whole_composition'}, 'models': [dict(
        invocation_id='one', model_id=model, mode='predict' if model == 'esmfold2' else 'complex', params=params)]}]}))


@pytest.mark.parametrize('model', ['esmfold2', 'protenix'])
@pytest.mark.parametrize('authority', ['omitted', 'empty', 'changed'])
@pytest.mark.parametrize('source_present', [False, True])
@pytest.mark.parametrize('selected', [False, True, 'copy'])
def test_authority_through_normalize_child_and_compiler(model, authority, source_present, selected, tmp_path, compiler_environment):
    source = dict(components=[{'id': 'discard', 'type': 'protein', 'sequence': 'GG'},
        {'id': 'protein', 'copy_ids': ['X', 'Y'], 'count': 2, 'type': 'protein', 'sequence': 'ACC'},
        {'id': 'Z', 'type': 'protein', 'sequence': 'ACC'}],
        covalent_bonds=[BOND] if source_present else [], ref=REF, owner_job_id='root')
    before = deepcopy(source)
    change = ESM if model == 'esmfold2' else {**BOND, 'left_entity': 1 if selected else 2,
        'right_entity': 2 if selected else 3, 'left_copy': 1 if selected == 'copy' else 2,
        'left_position': 3, 'right_position': 3}
    params = {} if authority == 'omitted' else {'covalent_bonds': [] if authority == 'empty' else [change]}
    params['esmf_use_msa' if model == 'esmfold2' else 'protenix_use_msa'] = False
    normalized = normalize(request(model, params))
    normalized = normalize(SelectedRoundRequest.model_validate_json(normalized.model_dump_json()))
    call = normalized.round_plan.stages[0].models[0]
    assert ('covalent_bonds' in call.params) == (authority != 'omitted')
    child, lineage = prediction_request(SimpleNamespace(id='root'), source, None, model, call.params,
        None, scope='whole_composition', selected_chains=(['Y', 'Z'] if selected == 'copy' else ['protein', 'Z']) if selected else None)
    child = JobCreate.model_validate_json(child.model_dump_json())
    inherited = {**BOND, 'left_entity': 1, 'right_entity': 2} if selected else BOND
    if selected == 'copy':
        inherited = {**inherited, 'left_copy': 1}
    expected = ([inherited] if source_present else []) if authority == 'omitted' else ([] if authority == 'empty' else [change])
    assert child.params.get('covalent_bonds', []) == expected
    assert child.params['source_design_id'] == REF['design_id']
    assert child.params['complex_components'][-2]['copy_ids'] == (['Y'] if selected == 'copy' else ['X', 'Y'])
    assert source == before
    compiled = nextflow.compile_nextflow_invocation(model, child.mode, child.params, str(tmp_path))
    compiled.materialize_inputs(tmp_path)
    if model == 'esmfold2':
        document = json.loads((tmp_path / 'esmfold2_complex_components.json').read_text())
    else:
        from scripts.prepare_protenix_msa import load_native_protenix_input
        document = load_native_protenix_input(compiled.native_parameters)[0]
    assert document.get('covalent_bonds', []) == expected
    if os.environ.get('BMS_OPERATION_EVIDENCE'):
        out = Path(os.environ['BMS_OPERATION_EVIDENCE']); out.mkdir(parents=True, exist_ok=True)
        (out / f'{model}-{authority}-{source_present}-{selected}.json').write_text(json.dumps(
            {'document': document, 'child': child.model_dump(mode='json'), 'lineage': lineage}, indent=2))


def test_packing_projects_once_without_changing_design_draft(tmp_path):
    source = tmp_path / 'source.pdb'
    source.write_text('ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\nEND\n')
    constraints = dict(fixed_pos_seq='A1', fixed_pos_scn='A1', fixed_pos_override_seq='A1',
                       pos_restrict_aatype='A1:AG', symmetry_pos='A1')
    groups = [{'ensemble_id': 'e', 'states': [{'state_id': 'exact-state', 'path': str(source), **constraints}]}]
    before = deepcopy(groups)
    for fresh in (False, True):
        retained = json.loads(json.dumps(groups if not fresh else [{'ensemble_id': 'e', 'states': [
            {'state_id': 'exact-state', 'path': str(source)}]}]))
        packed = bind_operation('sidechain_pack', {}, retained)
        assert packed['structures'] == [{'state_id': 'exact-state', 'path': str(source)}]
        directory = tmp_path / str(fresh)
        prepared = prepare_for_job('sidechain_pack', packed, directory, allowed_roots=[tmp_path])
        payload = read_prepared_request('sidechain_pack', packed, prepared['caliby_request_dir'])
        assert payload['requested'] == packed
    designed = bind_operation('ensemble_design', {}, groups)
    assert designed['ensembles'] == groups == before
