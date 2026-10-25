"""Blind-round chemistry admission through the actual native compiler (no inference)."""
import json
from types import SimpleNamespace
import pytest
from schemas import SelectedRoundRequest
from services.binder_selected_round import normalize, prediction_request
from services.sequence_round_inputs import normalize_request
from services import nextflow
from test_protenix_chemistry_transport import compiler_environment


@pytest.mark.parametrize('model,mode', [('boltz2', 'complex'), ('esmfold2', 'predict')])
def test_round_native_chemistry_initial_selected_equivalence(model, mode, tmp_path, compiler_environment):
    bond = {'bond': {'atom1': ['A', 2, 'SG'], 'atom2': ['B', 2, 'SG']}}
    esm_bond = {'chain_id1': 'A', 'res_idx1': 1, 'atom_idx1': 5,
                'chain_id2': 'B', 'res_idx2': 1, 'atom_idx2': 5}
    params = ({'constraints': [bond, {'contact': {'token1': ['A', 1], 'token2': ['B', 1], 'max_distance': 4}}],
               'properties': [{'affinity': {'binder': 'L'}}], 'templates': [{'pdb': 'not-used.pdb'}],
               'boltz_use_msa': False, 'boltz_extra_config': '--write_full_pae'} if model == 'boltz2' else
              {'covalent_bonds': [esm_bond], 'distogram_conditioning': [{'chain_id': 'A', 'distogram': [[0]]}], 'esmf_use_msa': False})
    plan = {'schema_version': 2, 'stages': [{'id': 'prediction', 'kind': 'prediction',
        'input': {'selected': True, 'scope': 'whole_composition'},
        'models': [{'invocation_id': 'one', 'model_id': model, 'mode': mode, 'params': params}]}]}
    initial = normalize_request({'schema_version': 2, 'round_plan': plan})
    request = {'operation': 'round', 'source_job_id': 'root', 'idempotency_key': 'chemistry',
               'selection': {'candidate_refs': [{'kind': 'design', 'job_id': 'root', 'design_id': 'exact'}]}, 'round_plan': plan}
    selected = normalize(SelectedRoundRequest.model_validate(request))
    from_initial = normalize(SelectedRoundRequest.model_validate({**request, 'round_plan': initial.round_plan}))
    assert selected == from_initial
    components = [{'id': chain, 'type': 'protein', 'sequence': 'ACG'} for chain in ['A', 'B']]
    if model == 'boltz2':
        components.append({'id': 'L', 'type': 'ligand', 'ccd': 'ATP'})
    row = {'components': components, 'owner_job_id': 'root', 'ref': {'kind': 'design', 'design_id': 'exact'}}
    child, _ = prediction_request(SimpleNamespace(id='root'), row, None, model,
        selected.round_plan.stages[0].models[0].params, None, scope='whole_composition', mode=mode)
    assert 'templates' not in child.params and 'distogram_conditioning' not in child.params
    compiled = nextflow.compile_nextflow_invocation(model, mode, child.params, str(tmp_path))
    compiled.materialize_inputs(tmp_path)
    filename = 'complex_definition.json' if model == 'boltz2' else 'esmfold2_complex_components.json'
    document = json.loads((tmp_path / filename).read_text())
    if model == 'boltz2':
        assert document['constraints'] == [bond]
        assert document['properties'] == params['properties']
        assert child.params['boltz_extra_config'] == params['boltz_extra_config']
    else:
        assert document['covalent_bonds'] == [esm_bond]
    assert not document.get('templates') and not document.get('distogram_conditioning')
    # Historical draft remains exact; stripping belongs only to the child receiver.
    assert selected.round_plan.stages[0].models[0].params.keys() >= params.keys()


def test_protenix_defers_only_source_dependent_chemistry_validation():
    from scripts.protenix_chemistry import validate_chemistry
    bond = {'left_entity': 1, 'right_entity': 2, 'left_position': 2,
            'right_position': 2, 'left_atom': 'SG', 'right_atom': 'SG'}
    def request(value):
        return SelectedRoundRequest.model_validate({'operation': 'round', 'source_job_id': 'root',
            'idempotency_key': 'protenix', 'selection': {'candidate_refs': [{'kind': 'design', 'job_id': 'root', 'design_id': 'exact'}]},
            'round_plan': {'schema_version': 2, 'stages': [{'id': 'p', 'kind': 'prediction', 'input': {'selected': True, 'scope': 'whole_composition'},
                'models': [{'invocation_id': 'one', 'model_id': 'protenix', 'mode': 'complex', 'params': {'covalent_bonds': [value]}}]}]}})
    with pytest.raises(ValueError):
        normalize(request({**bond, 'left_entity': 0}))
    root = SimpleNamespace(id='root')
    row = {'components': [{'id': chain, 'type': 'protein', 'sequence': 'ACG'} for chain in ['A', 'B']],
           'owner_job_id': 'root', 'ref': {'kind': 'design', 'design_id': 'exact'}}
    normalized = normalize(request(bond))
    child, _ = prediction_request(root, row, None, 'protenix', normalized.round_plan.stages[0].models[0].params, None, scope='whole_composition')
    validate_chemistry(child.params)
    invalid = normalize(request({**bond, 'right_entity': 3}))
    with pytest.raises(ValueError, match='endpoint entity exceeds ordered component roster'):
        prediction_request(root, row, None, 'protenix', invalid.round_plan.stages[0].models[0].params, None, scope='whole_composition')
