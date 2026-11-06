"""Invocation wire and native ordered-input checks; no inference/provider calls."""
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from schemas import (BinderRoundRequest, CandidateRef, JobCreate, SelectedRoundPlan,
                     SelectedRoundRequest, SelectedRoundState)
from services.caliby_native import bind_operation, materialize_request, operation_settings


def plan():
    return {'schema_version': 2, 'stages': [
        {'id': 'design', 'kind': 'sequence_design', 'input': {'selected': True}, 'models': [
            {'invocation_id': 'first', 'model_id': 'caliby_experimental', 'mode': 'ensemble_design',
             'input_groups': [{'ensemble_id': 'ensemble', 'states': [
                 {'state_id': 'primary', 'candidate_ref': {'kind': 'native_document', 'job_id': 'j', 'artifact_id': 'a', 'model': 2},
                  'settings': {'fixed_pos_seq': 'A1-4', 'fixed_pos_scn': 'B2', 'symmetry_pos': 'A1,B1'}},
                 {'state_id': 'alternate', 'candidate_ref': {'kind': 'native_document', 'job_id': 'j', 'artifact_id': 'b'}}]}]},
            {'invocation_id': 'second', 'model_id': 'caliby_experimental', 'mode': 'sidechain_pack', 'execution_target_id': None}]},
        {'id': 'predict', 'kind': 'prediction', 'input': {'stage_id': 'design', 'invocation_id': 'second', 'scope': 'whole_composition'},
         'models': [{'invocation_id': 'blind', 'model_id': 'protenix', 'mode': 'complex'}]}]}


def test_repeated_invocations_ordered_exact_sources_and_json_recovery():
    original = SelectedRoundPlan.model_validate(plan())
    recovered = SelectedRoundPlan.model_validate_json(original.model_dump_json())
    assert recovered == original
    first, second = recovered.stages[0].models
    assert first.model_id == second.model_id
    assert first.invocation_id != second.invocation_id
    states = first.input_groups[0].states
    assert [s.state_id for s in states] == ['primary', 'alternate']
    assert states[0].candidate_ref.model == 2
    assert states[0].settings.fixed_pos_seq == 'A1-4'
    assert states[0].settings.symmetry_pos == 'A1,B1'
    assert recovered.stages[1].input.scope == 'whole_composition'
    assert recovered.stages[1].input.invocation_id == 'second'
    assert 'execution_target_id' not in first.model_fields_set
    assert 'execution_target_id' in second.model_fields_set
    assert second.execution_target_id is None
    explicit = deepcopy(plan())
    explicit['stages'][0]['models'][0]['execution_target_id'] = None
    assert SelectedRoundPlan.model_validate(explicit).model_dump_json() != original.model_dump_json()


@pytest.mark.parametrize('change', ['unknown_state', 'unknown_mask', 'mixed_ref', 'duplicate_call', 'missing_call', 'forward_edge'])
def test_invalid_wire_is_not_silently_ignored(change):
    value = plan()
    call = value['stages'][0]['models'][0]
    state = call['input_groups'][0]['states'][0]
    if change == 'unknown_state': state['path'] = '/caller/path.pdb'
    if change == 'unknown_mask': state['settings']['made_up_mask'] = 'A1'
    if change == 'mixed_ref': state['candidate_ref']['design_id'] = 'not-a-design'
    if change == 'duplicate_call': value['stages'][0]['models'][1]['invocation_id'] = 'first'
    if change == 'missing_call': value['stages'][1]['input']['invocation_id'] = 'absent'
    if change == 'forward_edge': value['stages'][0]['input'] = {'stage_id': 'predict'}
    with pytest.raises(ValidationError): SelectedRoundPlan.model_validate(value)


def test_legacy_rounds_and_selected_wire_remain_replayable():
    selected = {'stages': [{'id': 'd', 'kind': 'sequence_design', 'input': {'selected': True},
                            'models': [{'model_id': 'proteinmpnn'}]}]}
    parsed = SelectedRoundPlan.model_validate(selected)
    assert SelectedRoundPlan.model_validate_json(parsed.model_dump_json()) == parsed
    assert parsed.model_dump(mode='json') == {
        'schema_version': 1, 'stages': [{'id': 'd', 'kind': 'sequence_design',
            'input': {'selected': True, 'stage_id': None},
            'models': [{'model_id': 'proteinmpnn', 'params': {}}],
            'model_id': None, 'mode': None, 'params': {}}]}
    for version in (1, 2):
        wire = {'schema_version': version, 'sequence_design': {'model_id': 'proteinmpnn'},
                'prediction': {'model_id': 'protenix'}}
        if version == 2:
            for field in ('sequence_design', 'prediction'): wire[field] = [wire[field]]
        old = BinderRoundRequest.model_validate(wire)
        assert BinderRoundRequest.model_validate_json(old.model_dump_json()) == old
    selected['stages'][0]['models'] *= 2
    with pytest.raises(ValidationError): SelectedRoundPlan.model_validate(selected)
    # Existing OFF request does not acquire any operation envelope.
    job = JobCreate(name='off', model_id='proteinmpnn', mode='design', params={},
                    sequence_design={'enabled': False, 'model_id': 'proteinmpnn'})
    assert not job.sequence_design.enabled
    assert job.binder_round is None


@pytest.mark.parametrize('mode', ['ensemble_design', 'sidechain_pack'])
def test_actual_native_binding_and_writer_preserve_order_and_pack_sequence(tmp_path, mode):
    states = []
    originals = []
    for index in range(2):
        path = tmp_path / f'source{index}.pdb'
        content = f'REMARK source {index}\nATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\nEND\n'
        path.write_text(content)
        originals.append(content)
        states.append({'state_id': f's{index}', 'path': str(path)})
    if mode == 'ensemble_design': states[0]['fixed_pos_seq'] = 'A1'
    bound = bind_operation(mode, operation_settings(mode, {}), [{'ensemble_id': 'ordered', 'states': states}])
    destination = tmp_path / 'prepared'
    result = materialize_request(mode, bound, destination)
    retained = json.loads((destination / 'request.json').read_text())
    assert [s['state_id'] for s in retained['sources']] == ['s0', 's1']
    for row, original in zip(retained['sources'], originals):
        assert (destination / row['path']).read_text() == original
    if mode == 'ensemble_design':
        assert retained['effective']['ensembles'][0]['states'][0]['fixed_pos_seq'] == 'A1'
    else:
        assert [s['state_id'] for s in retained['effective']['structures']] == ['s0', 's1']
        assert 'ensembles' not in retained['effective']
        assert all('fixed_pos_seq' not in s for s in retained['effective']['structures'])


def test_whole_composition_blind_builder_keeps_native_chemistry_and_no_pose():
    from types import SimpleNamespace
    from services.binder_selected_round import prediction_request
    components = [
        {'id': 'protein', 'type': 'protein', 'sequence': 'AG'},
        {'id': 'dna', 'type': 'dna', 'sequence': 'AC'},
        {'id': 'rna', 'type': 'rna', 'sequence': 'AU'},
        {'id': 'ligand', 'type': 'ligand', 'smiles': 'CCO'},
    ]
    row = {'components': deepcopy(components), 'owner_job_id': 'producer',
           'ref': {'kind': 'native_document', 'job_id': 'producer', 'artifact_id': 'doc'},
           'path': '/not-read/ancestral-pose.pdb'}
    for model in ('protenix', 'boltz2'):
        child, binding = prediction_request(SimpleNamespace(id='root'), row, None, model, {}, None,
                                            scope='whole_composition')
        assert child.params['complex_components'] == components
        assert child.params['sequence'] == 'AG'
        assert child.params['iteration_source_design_ids'] == []
        assert child.execution_target_id is None
        assert binding['target_binding'] is None
        assert not any(key in child.params for key in ('input_pdb', 'template_pdb', 'target_pdb'))
    assert row['components'] == components
    child, _ = prediction_request(SimpleNamespace(id='root'), row, None, 'protenix', {}, None,
                                  scope='whole_composition', selected_chains=['dna', 'rna'])
    assert child.params['complex_components'] == components[1:3]


def test_source_candidate_forward_reference_is_preserved():
    schema = JobCreate.model_json_schema()
    assert 'CandidateRef' in schema['$defs']
    assert 'SelectedRoundState' in SelectedRoundPlan.model_json_schema()['$defs']
