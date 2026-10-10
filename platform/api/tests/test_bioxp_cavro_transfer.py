from copy import deepcopy
import json
import os
from pathlib import Path

import pytest
from bioxp_method_examples import bound_examples
from bioxp_method_compiler import compile_method
from test_bioxp_method_finish import recipe, node, actions
from test_bioxp_method_liquid_mapping import CONTEXT


def request(multi=False, final_empty=False, pairs=1):
    fixture = deepcopy(bound_examples()[0])
    inputs = deepcopy(fixture['bindings']['assemble_inputs'])
    inputs.pop('aspirate_speed', None)
    inputs.pop('dispense_speed', None)
    inputs['volume_ul'] = 10 if multi else 20
    inputs['source']['wells'] = ['A1', 'A2'][:pairs]
    inputs['destination']['wells'] = (['A1', 'A2', 'A3', 'A4'] if multi else ['A1', 'A2'])[:pairs * (2 if multi else 1)]
    raw = recipe()
    raw['channels'] = inputs['channels']
    raw['final_empty_speed_ul_s'] = 30 if final_empty else None
    if multi:
        raw.update(mode='multi', dispense_segments=[], multi={
            'sample_count': 2, 'sample_volume_ul': 10, 'conditioning_volume_ul': 1,
            'conditioning_back_to_source_count': 1, 'conditioning_speed_ul_s': 30,
            'conditioning_before': [], 'conditioning_after': [], 'excess_volume_ul': 1,
            'excess': None, 'reaspiration': {'volume_ul': 1, 'speed_ul_s': 30},
            'dispense_to_reaspiration_delay_ms': 1,
            'aliquots': [{'before': [], 'segments': [{'volume_ul': 10, 'speed_ul_s': 30}], 'after': []} for _ in range(2)]})
    inputs['recipe'] = raw
    inputs['liquid'] = {'context': {**CONTEXT, 'mode': 'multi-dispense' if multi else 'single-dispense'}, 'requested': {}}
    fixture['method']['steps'] = [node('transfer', inputs, on_error='pause_for_operator')]
    return fixture


def export_case(name, req, result):
    directory = os.environ.get('BIOXP_CAVRO_TRANSFER_EXPORT')
    if directory:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (path / (name + '.json')).write_text(json.dumps({'request': req, 'result': result}, indent=2))


@pytest.mark.parametrize('multi', [False, True])
@pytest.mark.parametrize('final_empty', [False, True])
@pytest.mark.parametrize('pairs', [1, 2])
def test_positioned_transfer_native_recipe_and_final_lift(multi, final_empty, pairs):
    req = request(multi, final_empty, pairs)
    original = deepcopy(req)
    result = compile_method(req)
    aa = actions(result)
    recipes = [a['params']['recipe'] for a in aa if a['params'].get('operation') == 'cavro_liquid_recipe']
    assert len(recipes) == pairs
    assert all(a['on_error'] == 'pause_for_operator' for a in aa)
    for index, r in enumerate(recipes):
        assert r['before_leading_air'][0]['positioning']['well'] == ['A1', 'A2'][index]
        assert r['commanded_aspiration_ul'] == 22.5
        assert r['target_liquid_ul'] == 20
        assert bool(r['after_dispense']) is not final_empty
        if multi:
            assert [x['before'][0]['positioning']['well'] for x in r['multi']['aliquots']] == ['A1', 'A2', 'A3', 'A4'][index*2:index*2+2]
            assert r['multi']['conditioning_before'][-1]['positioning']['operation'] == 'lower'
            assert r['multi']['conditioning_after'][-1]['positioning']['operation'] == 'lift'
    if final_empty:
        assert len(aa) == pairs * 2
        assert [a['params']['operation'] for a in aa] == ['cavro_liquid_recipe', 'lift'] * pairs
    assert req == original
    assert 'state' in result['simulation'], result['simulation']
    strokes = [s for s in result['simulation']['strokes'] if 'recipe_quantities' in s]
    assert len(strokes) == pairs * len(recipes[0]['channels'])
    if multi:
        occurrence = next(o for o in result['resolved']['occurrences'] if o['action'] == 'transfer')
        effects = occurrence['inputs']['channel_transfers']
        assert len(effects) == pairs * 2 * len(recipes[0]['channels'])
        assert {(e['pair_index'], e['aliquot_index']) for e in effects} == {(p, a) for p in range(pairs) for a in range(2)}
        for action in aa:
            if action['params'].get('operation') == 'cavro_liquid_recipe':
                phases = action['metadata']['bms_method']['planned_recipe_phase_effects']
                assert all(p['destination'] is not None for p in phases if p['recipe_phase'] == 'aliquot')
                assert all(p['kind'] == 'air' for p in phases if p['recipe_phase'] == 'reaspiration')
    export_case(f'transfer-{multi}-{final_empty}-{pairs}', req, result)


def test_authored_search_retract_and_postdispense_order_retained():
    req = request(final_empty=True)
    raw = req['method']['steps'][0]['inputs']['recipe']
    raw['before_liquid'] = [{'operation': 'plld', 'channels': [0], 'timeout_ms': 1000,
        'start_steps': 100, 'search_target_steps': 200, 'search_speed_native': 10,
        'z_motor_current': 2, 'after_detection_steps': None, 'after_detection_speed_native': None}]
    raw['after_liquid'] = [{'operation': 'z_move', 'target_steps': 100, 'speed_native': 10}]
    raw['after_dispense'] = [{'operation': 'delay', 'duration_ms': 2}]
    result = compile_method(req)
    aa = actions(result)
    r = aa[0]['params']['recipe']
    assert [op['operation'] for op in r['before_liquid']] == ['plld', 'position']
    assert [op['operation'] for op in r['after_liquid']] == ['z_move', 'position']
    assert r['after_dispense'] == raw['after_dispense']
    assert aa[1]['params']['operation'] == 'lift'
    export_case('authored-phases', req, result)
