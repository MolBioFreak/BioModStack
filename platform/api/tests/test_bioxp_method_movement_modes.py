"""Workflow move-step movement setting: full (default) vs fast-lane grouping."""
from copy import deepcopy

import pytest

from bioxp_method_compiler import compile_method
from bioxp_method_model import method_catalog


def move(sid, well, movement=None, location=0, flag=1):
    inputs = {'location_id': location, 'well': well, 'position_flag': flag}
    if movement is not None:
        inputs['movement'] = movement
    return {'step_id': sid, 'type': 'action', 'action': 'move', 'inputs': inputs}


def lower(sid):
    return {'step_id': sid, 'type': 'action', 'action': 'lower', 'inputs': {'location_id': 0}}


def compiled(*steps):
    request = {'method': {'schema': 'bms.bioxp-method.v1', 'name': 'Moves', 'parameters': [], 'procedures': [], 'steps': list(steps)}}
    original = deepcopy(request)
    result = compile_method(request)
    assert request == original  # the saved draft is never rewritten
    return result


def actions(result):
    assert result['document'], result['issues']
    return [a for stage in result['document']['stages'] for a in stage['actions']]


def test_consecutive_fast_moves_become_one_sequence_in_order():
    result = compiled(move('a', 'A1', 'fast'), move('b', 'B2', 'fast', location=4), move('c', 'C3', 'fast', flag=2))
    [action] = actions(result)
    assert action['kind'] == 'pipette_position'
    assert action['params'] == {'operation': 'move_sequence', 'targets': [
        {'location_id': 0, 'well': 'A1'}, {'location_id': 4, 'well': 'B2'}, {'location_id': 0, 'well': 'C3'}]}
    assert [m['step_id'] for m in action['metadata']['bms_method']['sequence']] == ['a', 'b', 'c']
    assert [m['target_index'] for m in action['metadata']['bms_method']['sequence']] == [0, 1, 2]


def test_full_moves_and_other_steps_break_groups():
    result = compiled(move('a', 'A1', 'fast'), move('b', 'B1', 'fast'), move('c', 'C1'), move('d', 'D1', 'fast'),
                      move('e', 'E1', 'full'), move('f', 'F1', 'fast'), lower('g'), move('h', 'H1', 'fast'))
    emitted = actions(result)
    assert [a['params']['operation'] for a in emitted] == ['move_sequence', 'move', 'move_sequence', 'move', 'move_sequence', 'lower', 'move_sequence']
    assert [len(a['params'].get('targets', [])) for a in emitted] == [2, 0, 1, 0, 1, 0, 1]
    # Full moves are byte-identical to the existing per-move emission (position_flag kept).
    assert emitted[1]['params'] == {'operation': 'move', 'location_id': 0, 'well': 'C1', 'position_flag': 1}
    assert emitted[3]['params'] == {'operation': 'move', 'location_id': 0, 'well': 'E1', 'position_flag': 1}


def test_single_fast_move_is_a_one_target_sequence_without_position_flag():
    [action] = actions(compiled(move('a', 'A1', 'fast', flag=2)))
    assert action['params'] == {'operation': 'move_sequence', 'targets': [{'location_id': 0, 'well': 'A1'}]}


def test_omitted_and_explicit_full_emit_the_existing_move():
    omitted, explicit = actions(compiled(move('a', 'A1'))), actions(compiled(move('a', 'A1', 'full')))
    assert omitted[0]['params'] == explicit[0]['params'] == {'operation': 'move', 'location_id': 0, 'well': 'A1', 'position_flag': 1}


def test_retarget_and_unknown_movement_are_rejected_plainly():
    for value in ('retarget', 'other'):
        result = compiled(move('a', 'A1', 'fast'), move('b', 'B1', value))
        assert result['document'] is None
        assert result['issues'][-1]['message'] == 'Retarget movement is only available in Live deck movement'
        assert result['issues'][-1]['path'] == '/method/steps/1'


def test_provenance_keeps_every_grouped_step():
    result = compiled(move('a', 'A1', 'fast'), move('b', 'B1', 'fast'), move('c', 'C1'))
    emitted = actions(result)
    by_step = {p['step_id']: p for p in result['provenance']}
    assert by_step['a']['native_action_ids'] == by_step['b']['native_action_ids'] == [emitted[0]['action_id']]
    assert by_step['c']['native_action_ids'] == [emitted[1]['action_id']]
    assert emitted[0]['source_occurrence_id'] == by_step['a']['occurrence_id']
    sequence = emitted[0]['metadata']['bms_method']['sequence']
    assert [m['occurrence_id'] for m in sequence] == [by_step['a']['occurrence_id'], by_step['b']['occurrence_id']]
    assert [m['path'] for m in sequence] == ['/method/steps/0', '/method/steps/1']


def test_groups_do_not_cross_structural_stages_or_exceed_64():
    group = {'step_id': 'g', 'type': 'group', 'steps': [move('in', 'B1', 'fast')]}
    emitted = actions(compiled(move('a', 'A1', 'fast'), group))
    assert [len(a['params']['targets']) for a in emitted] == [1, 1]
    many = actions(compiled(*[move(f's{i}', 'A1', 'fast') for i in range(65)]))
    assert [len(a['params']['targets']) for a in many] == [64, 1]


def test_simulator_head_reference_follows_each_sequence_target():
    result = compiled(move('a', 'A1', 'fast'), move('b', 'B2', 'fast', location=4))
    simulation = result['simulation']
    assert simulation['state']['head_reference'] == {'operation': 'move_sequence', 'location_id': 4, 'well': 'B2'}
    assert [s['state_delta']['head_reference'] for s in simulation['after_occurrences']] == [
        {'operation': 'move_sequence', 'location_id': 0, 'well': 'A1'}, {'operation': 'move_sequence', 'location_id': 4, 'well': 'B2'}]


def test_saved_draft_roundtrip_preserves_movement_presence():
    import json
    steps = [move('a', 'A1'), move('b', 'B1', 'full'), move('c', 'C1', 'fast')]
    draft = {'schema': 'bms.bioxp-method.v1', 'steps': steps}
    reopened = json.loads(json.dumps(draft))
    assert 'movement' not in reopened['steps'][0]['inputs']
    assert reopened['steps'][1]['inputs']['movement'] == 'full'
    assert reopened['steps'][2]['inputs']['movement'] == 'fast'
    result = compile_method({'method': reopened})
    assert reopened == draft and result['document'] is not None
    assert result['document']['metadata']['bms_method']['method'] == draft


def test_catalog_publishes_the_movement_setting():
    entry = next(a for a in method_catalog()['actions'] if a['action'] == 'move')
    assert entry['inputs']['properties']['movement']['enum'] == ['full', 'fast']
    assert 'movement' not in entry['inputs']['required']


def holder_move(sid, target, movement=None, **extra):
    inputs = {'target': target, **extra}
    if movement is not None:
        inputs['movement'] = movement
    return {'step_id': sid, 'type': 'action', 'action': 'move', 'inputs': inputs}


def test_fast_holder_and_well_moves_share_one_sequence():
    [action] = actions(compiled(move('a', 'A1', 'fast'), holder_move('b', 'LOC_TROUGH_MICRO_ROW_1', 'fast'),
                                holder_move('c', 'LOC_TROUGH_PCR_ROW_2_WELL_2', 'fast')))
    assert action['params'] == {'operation': 'move_sequence', 'targets': [
        {'location_id': 0, 'well': 'A1'}, {'target': 'LOC_TROUGH_MICRO_ROW_1', 'camera_offset': False},
        {'target': 'LOC_TROUGH_PCR_ROW_2_WELL_2', 'camera_offset': False}]}
    assert [m['step_id'] for m in action['metadata']['bms_method']['sequence']] == ['a', 'b', 'c']


def test_full_holder_moves_are_separate_reported_actions():
    emitted = actions(compiled(holder_move('a', 'LOC_TROUGH_MICRO_ROW_2'), holder_move('b', 'LOC_TROUGH_PCR_ROW_1_WELL_1', 'full'), move('c', 'C1')))
    assert [a['params'] for a in emitted] == [
        {'operation': 'move_sequence', 'targets': [{'target': 'LOC_TROUGH_MICRO_ROW_2', 'camera_offset': False}]},
        {'operation': 'move_sequence', 'targets': [{'target': 'LOC_TROUGH_PCR_ROW_1_WELL_1', 'camera_offset': False}]},
        {'operation': 'move', 'location_id': 0, 'well': 'C1', 'position_flag': 1}]


def test_catalog_offers_every_holder_target():
    entry = next(a for a in method_catalog()['actions'] if a['action'] == 'move')
    assert len(entry['inputs']['properties']['target']['enum']) == 20


@pytest.mark.parametrize('step', [holder_move('a', 'LOC_TROUGH_PCR_ROW_3', 'fast'),
                                  holder_move('a', 'LOC_TROUGH_MICRO_ROW_1', 'fast', position_flag=2),
                                  holder_move('a', 'LOC_TROUGH_MICRO_ROW_1', location_id=16)])
def test_bad_holder_moves_do_not_compile(step):
    assert compiled(step)['document'] is None


def test_lower_lift_carry_holder_row_to_native_params():
    steps = [{'step_id': 'l', 'type': 'action', 'action': 'lower', 'inputs': {'operation': 'lower', 'location_id': 16, 'holder_row': 'micro_row_1'}},
             {'step_id': 'u', 'type': 'action', 'action': 'lift', 'inputs': {'operation': 'lift', 'location_id': 16, 'holder_row': 'micro_row_1', 'height_steps': 2016}},
             {'step_id': 'o', 'type': 'action', 'action': 'lower', 'inputs': {'operation': 'lower', 'location_id': 1}}]
    params = [a['params'] for a in actions(compiled(*steps))]
    assert params == [{'operation': 'lower', 'location_id': 16, 'holder_row': 'micro_row_1'},
                      {'operation': 'lift', 'location_id': 16, 'holder_row': 'micro_row_1', 'height_steps': 2016},
                      {'operation': 'lower', 'location_id': 1}]
