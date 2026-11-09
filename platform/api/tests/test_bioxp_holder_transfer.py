"""Combined holder Transfer receiving/compilation; no robot transport or motion."""
from copy import deepcopy
import json

import pytest
from jsonschema import Draft202012Validator

from bioxp_holder_endpoints import HOLDER_TARGETS, holder_row
from bioxp_workflow_authoring import expand_transfer
from bioxp_method_planning import positioned_transfer_recipes, prepare_occurrences, emission_inputs
from bioxp_method_compiler import compile_method
from bioxp_method_model import method_catalog
from test_bioxp_method_close import class_transfer
from test_bioxp_liquid_detection_planning import _inputs
from test_bioxp_method_finish import actions
from test_bioxp_methods_api import store  # noqa: F401


def endpoint(target=HOLDER_TARGETS[0]):
    return {'station': 'LOC_TROUGH', 'location_id': 16, 'target': target, 'wells': ['A1']}


def manual():
    return {'operation': 'transfer', 'source': endpoint(), 'destination': endpoint(HOLDER_TARGETS[1]),
            'channels': [2], 'volume_ul': '5', 'aspirate_speed': '20', 'dispense_speed': '30',
            'source_position_flag': 1, 'destination_position_flag': 1,
            'source_lift_height_steps': None, 'destination_lift_height_steps': '100'}


def request(inputs, action='transfer'):
    return {'method': {'schema': 'bms.bioxp-method.v1', 'name': 'Holder', 'steps': [
        {'step_id': 'transfer', 'type': 'action', 'action': action, 'inputs': deepcopy(inputs)}]}}


@pytest.mark.parametrize('target', HOLDER_TARGETS)
@pytest.mark.parametrize('side', ['source', 'destination'])
def test_all_exact_targets_manual_compile_and_catalog(target, side):
    intent = manual()
    intent[side] = endpoint(target)
    schema = next(a['inputs'] for a in method_catalog()['actions'] if a['action'] == 'transfer')
    Draft202012Validator(schema).validate({k: v for k, v in intent.items() if k != 'operation'})
    result = compile_method(request({k: v for k, v in intent.items() if k != 'operation'}))
    emitted = actions(result)
    assert len(emitted) == 8
    offset = 0 if side == 'source' else 4
    assert emitted[offset]['params'] == {'operation': 'move_sequence', 'targets': [{'target': target, 'camera_offset': False}]}
    assert emitted[offset + 1]['params']['holder_row'] == holder_row(target)
    assert emitted[offset + 3]['params']['holder_row'] == holder_row(target)
    assert emitted[offset + 2]['params']['channels'] == [2]


@pytest.mark.parametrize('invalid', [
    {'target': 'LOC_TROUGH_MICRO_ROW_1 '}, {'target': None}, {'location_id': 3},
    {'station': 'LOC_REAGENT'}, {'holder_row': 'micro_row_2'}, {'target': 'LOC_TROUGH_MICRO_ROW_1_WELL_1'}])
def test_bad_endpoint_is_representation_error(invalid):
    intent = manual()
    intent['source'].update(invalid)
    with pytest.raises(ValueError):
        expand_transfer(intent)
    result = compile_method(request({k: v for k, v in intent.items() if k != 'operation'}))
    assert result['document'] is None
    assert result['issues']


def test_manual_ordered_pairs_and_no_target_legacy_bytes():
    intent = manual()
    for side in ('source', 'destination'):
        intent[side].pop('target')
        intent[side]['wells'] = ['A1', 'B2']
    expanded = expand_transfer(intent)
    expected = []
    for pair, well in enumerate(['A1', 'B2']):
        for side, stroke in [('source', 'aspirate'), ('destination', 'dispense')]:
            for step in [
                {'operation': 'move', 'location_id': 16, 'well': well, 'position_flag': 1},
                {'operation': 'lower', 'location_id': 16},
                {'operation': stroke, 'channels': [2], 'volume_ul': 5., 'speed': 20. if side == 'source' else 30.},
                {'operation': 'lift', 'location_id': 16, 'height_steps': None if side == 'source' else 100}]:
                expected.append((step, pair, 'LOC_TROUGH', well))
    assert json.dumps(expanded) == json.dumps(expected)
    intent['destination']['wells'].pop()
    with pytest.raises(ValueError, match='no broadcasting'):
        expand_transfer(intent)


@pytest.mark.parametrize('mode', ['by_level', 'together', 'one_at_a_time', 'on_contact'])
@pytest.mark.parametrize('when', ['off', 'first_draw', 'every_draw'])
def test_recipe_detection_modes_compile_and_final_empty_lift(mode, when):
    f, inputs = class_transfer()
    inputs['source'] = endpoint()
    inputs['destination'] = endpoint('LOC_TROUGH_PCR_ROW_2_WELL_2')
    inputs['liquid_detection'] = {'mode': mode, 'when': when}
    inputs['recipe']['final_empty_speed_ul_s'] = 50
    original = deepcopy(f)
    emitted = actions(compile_method(f))
    assert f == original
    r = emitted[0]['params']['recipe']
    assert r['before_leading_air'][0]['positioning'] == {'operation': 'move', 'target': HOLDER_TARGETS[0], 'camera_offset': False}
    if when == 'off':
        assert 'liquid_detection' not in r
        assert r['before_liquid'][-1]['positioning']['holder_row'] == 'micro_row_1'
    else:
        assert r['liquid_detection']['holder_row'] == 'micro_row_1'
        assert r['liquid_detection']['mode'] == mode
    assert r['after_liquid'][-1]['positioning']['holder_row'] == 'micro_row_1'
    assert r['before_dispense'][1]['positioning']['holder_row'] == 'pcr_row_2'
    assert emitted[-1]['params'] == {'operation': 'lift', 'location_id': 16, 'height_steps': int(inputs['destination_lift_height_steps']), 'holder_row': 'pcr_row_2'}


def test_first_draw_identity_is_target_and_actual_channels_not_reference_well():
    seen = set()
    def draw(channels, target=HOLDER_TARGETS[0], well='A1'):
        i = _inputs(liquid_detection={})
        i['source'] = {**endpoint(target), 'wells': [well]}
        i['destination']['wells'] = ['A1']
        i['channels'] = channels
        i['recipe']['channels'] = channels
        return positioned_transfer_recipes(i, seen)[0]
    assert 'liquid_detection' in draw([0])
    assert 'liquid_detection' in draw([1])
    assert 'liquid_detection' not in draw([0], well='B2')
    assert 'liquid_detection' in draw([0], HOLDER_TARGETS[1])
    assert 'liquid_detection' in draw([0, 2])
    assert 'liquid_detection' not in draw([0, 1, 2])
    i = _inputs(liquid_detection={})
    i['source'] = {'station': 'LOC_TROUGH', 'location_id': 16, 'wells': ['A1']}
    i['destination']['wells'] = ['A1']
    assert 'liquid_detection' in positioned_transfer_recipes(i, seen)[0]


def test_manual_authored_detection_is_explicitly_rejected_not_dropped():
    i = manual()
    i['liquid_detection'] = {}
    with pytest.raises(ValueError):
        expand_transfer(i)


def test_material_lowering_keeps_holder_target_and_accounting():
    from bioxp_method_deck import resolve_material_endpoint
    m = {'deck_plan': {'labware': [{'id': 'holder', 'station': 'LOC_TROUGH', 'profile_id': 'tubes'}],
        'assignments': [{'labware_id': 'holder', 'material_id': 'sample', 'well': 'A1'}]}}
    e = resolve_material_endpoint({'material_id': 'sample', **endpoint()}, m, {}, [0], '/source')
    assert e['target'] == HOLDER_TARGETS[0] and e['labware_id'] == 'holder'
    i = manual()
    i.pop('operation')
    i['source'] = e
    i['destination']['labware_id'] = 'holder'
    row = {'action': 'transfer', 'step_id': 'x', 'occurrence_id': 'x', 'inputs': i}
    planned, *_ = prepare_occurrences([row], m, {'labware_profiles': [{'id': 'tubes', 'native_addressing': {
        'row_increment': 0, 'column_increment': 1, 'reference_channel': 0}}]})
    assert planned[0]['inputs']['channel_transfers'][0]['source']['target'] == HOLDER_TARGETS[0]
    assert emission_inputs(planned[0])['source']['target'] == HOLDER_TARGETS[0]


@pytest.mark.asyncio
async def test_route_save_reopen_and_real_compile_holder(store, monkeypatch):
    from routers.bioxp import methods
    monkeypatch.setattr(methods, 'compile_method', compile_method)
    client = store[0]
    i = manual()
    i.pop('operation')
    raw = request(i)['method']
    raw['steps'][0]['inputs']['source']['wells'] = ['A1']
    response = await client.post('/api/bioxp/methods/library', json={'method': raw})
    assert response.status_code == 201, response.text
    saved = response.json()
    await store[3].kw['bind'].dispose()  # Force a cold SQLite connection for reopen.
    reopened = await client.get('/api/bioxp/methods/library/' + str(saved['id']))
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()['method'] == raw
    result = await client.post('/api/bioxp/methods/compile', json={'method': reopened.json()['method']})
    assert result.status_code == 200, result.text
    assert actions(result.json())[0]['params']['targets'][0]['target'] == HOLDER_TARGETS[0]


@pytest.mark.parametrize('excess', [False, True])
def test_multi_conditioning_aliquots_excess_and_phase_hooks_preserved(excess):
    from test_bioxp_method_finish import recipe
    from bioxp_method_native import native_action
    raw = recipe()
    hook = {'operation': 'delay', 'duration_ms': 3}
    raw.update(mode='multi', dispense_segments=[], multi={
        'sample_count': 2, 'sample_volume_ul': 10, 'conditioning_volume_ul': 1,
        'conditioning_back_to_source_count': 1, 'conditioning_speed_ul_s': 30,
        'conditioning_before': [hook], 'conditioning_after': [hook], 'excess_volume_ul': 1,
        'excess': {'before': [hook], 'segments': [{'volume_ul': 1, 'speed_ul_s': 30}], 'after': [hook]} if excess else None,
        'reaspiration': {'volume_ul': 1, 'speed_ul_s': 30}, 'dispense_to_reaspiration_delay_ms': 1,
        'aliquots': [{'before': [hook], 'segments': [{'volume_ul': 10, 'speed_ul_s': 30}], 'after': [hook]} for _ in range(2)]})
    i = _inputs(recipe=raw, liquid_detection={})
    i['source'] = endpoint()
    i['destination'] = {**endpoint('LOC_TROUGH_PCR_ROW_1_WELL_8'), 'wells': ['A1', 'A2']}
    original = deepcopy(i)
    r = positioned_transfer_recipes(i)[0]
    assert i == original
    m = r['multi']
    assert m['conditioning_before'][:2] == [
        {'operation': 'position', 'positioning': {'operation': 'move', 'target': HOLDER_TARGETS[0], 'camera_offset': False}},
        {'operation': 'position', 'positioning': {'operation': 'lower', 'location_id': 16, 'holder_row': 'micro_row_1'}}]
    assert m['conditioning_before'][-1] == hook
    assert m['conditioning_after'][0] == hook
    assert m['conditioning_after'][-1]['positioning']['holder_row'] == 'micro_row_1'
    for index, a in enumerate(m['aliquots']):
        assert a['before'][0]['positioning']['target'] == 'LOC_TROUGH_PCR_ROW_1_WELL_8'
        assert a['before'][1]['positioning']['holder_row'] == 'pcr_row_1'
        assert a['before'][2] == hook and a['after'][0] == hook
        if index == 0 or excess:
            assert a['after'][-1]['positioning']['holder_row'] == 'pcr_row_1'
        else:
            assert a['after'] == [hook]
    assert m['excess'] == raw['multi']['excess']
    # Exact native exported Recipe receiving validation, not only helper inspection.
    assert native_action('liquid_recipe', {'recipe': r})[0] == 'pipette_manual_physical'
    assert actions(compile_method(request(i)))[0]['params']['recipe']['multi'] == m


@pytest.mark.parametrize('movement', ['full', 'fast'])
def test_combined_transfer_between_existing_movement_lanes(movement):
    i = manual()
    i.pop('operation')
    f = request(i)
    f['method']['steps'].insert(0, {'step_id': 'hover', 'type': 'action', 'action': 'move',
        'inputs': {'target': HOLDER_TARGETS[0], 'movement': movement}})
    aa = actions(compile_method(f))
    assert aa[0]['params']['operation'] == 'move_sequence'
    assert aa[1]['params']['targets'][0]['target'] == HOLDER_TARGETS[0]
    assert aa[-1]['params']['holder_row'] == 'micro_row_2'


@pytest.mark.parametrize('side', ['source', 'destination'])
@pytest.mark.parametrize('class_recipe', [False, True])
def test_mixed_legacy_and_holder_endpoints(side, class_recipe):
    if class_recipe:
        f, i = class_transfer()
        i[side] = endpoint()
        i['liquid_detection'] = {}
        aa = actions(compile_method(f))
        r = aa[0]['params']['recipe']
        if side == 'source':
            assert r['liquid_detection']['holder_row'] == 'micro_row_1'
            assert 'target' not in r['before_dispense'][0]['positioning']
        else:
            assert 'holder_row' not in r['liquid_detection']
            assert 'target' not in r['before_leading_air'][0]['positioning']
            assert r['before_dispense'][0]['positioning']['target'] == HOLDER_TARGETS[0]
    else:
        i = manual()
        i.pop('operation')
        legacy = 'destination' if side == 'source' else 'source'
        i[legacy] = {'station': 'LOC_RC', 'location_id': 3, 'wells': ['B2']}
        aa = actions(compile_method(request(i)))
        index = 0 if legacy == 'source' else 4
        assert aa[index]['params'] == {'operation': 'move', 'location_id': 3, 'well': 'B2', 'position_flag': 1}
        assert 'holder_row' not in aa[index + 1]['params']
