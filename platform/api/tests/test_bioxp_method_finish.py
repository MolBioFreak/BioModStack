from copy import deepcopy
import json
import os
from pathlib import Path

import pytest
from bioxp_method_compiler import compile_method
from bioxp_method_examples import bound_examples
from bioxp_method_liquids import starter_entries
from bioxp_method_model import method_examples
from bioxp_method_native import CONTRACTS


def node(action, inputs, sid='a', **kw):
    return {'step_id': sid, 'type': 'action', 'action': action, 'inputs': inputs, **kw}


def compile_nodes(*nodes, **kw):
    return compile_method({'method': {'schema': 'bms.bioxp-method.v1', 'steps': list(nodes), **kw}})


_DOCUMENTS = {}


@pytest.fixture(scope='module', autouse=True)
def export_all_compiled_documents():
    yield
    if os.environ.get('BIOXP_METHOD_FINISH_EXPORT'):
        Path(os.environ['BIOXP_METHOD_FINISH_EXPORT']).write_text(json.dumps(list(_DOCUMENTS.values())))


def actions(result):
    assert result['document'], result['issues']
    _DOCUMENTS[result['digest']] = deepcopy(result['document'])
    return [a for s in result['document']['stages'] for a in s['actions']]


def recipe():
    return {'mode': 'single', 'channels': [0, 1, 2, 3], 'timeout_ms': 1000,
        'target_liquid_ul': 20, 'commanded_aspiration_ul': 22.5, 'aspiration_speed_ul_s': 50,
        'aspiration_delay_ms': 0, 'leading_air': {'volume_ul': 20, 'speed_ul_s': 50},
        'trailing_air': {'volume_ul': 1.25, 'speed_ul_s': 50},
        'before_leading_air': [], 'before_liquid': [], 'after_liquid': [], 'before_dispense': [],
        'after_dispense': [], 'dispense_segments': [{'volume_ul': 22.5, 'speed_ul_s': 875}],
        'final_empty_speed_ul_s': None, 'final_empty_before': [], 'multi': None}


MECHANISMS = {
    'wait': {'seconds': '1.25'}, 'timer_start': {'timer_id': 't', 'seconds': 1}, 'timer_wait': {'timer_id': 't'},
    'thermal_setpoint': {'bank': 'lid', 'target_temp_c': 40},
    'thermal_hold': {'bank': 'nest', 'target_temp_c': 30, 'duration_s': 2, 'start': 'attainment', 'tolerance_c': 1, 'timeout_s': 3},
    'thermal_profile': {'segments': [{'bank': 'nest', 'target_temp_c': 30, 'duration_s': 1, 'start': 'dispatch'}], 'repeat': 2},
    'chiller_setpoint': {'bank': 'rc', 'target_temp_c': 4}, 'snapshot': {}, 'inspect': {},
    'camera_illumination': {'channel': 2, 'on': False}, 'barcode_read': {'mode': 'stationary'},
    'plate_catch': {'plate': 1, 'run_in_parallel': False},
    'plate_release': {'destination': 2, 'press_plate': False, 'run_in_parallel': False},
    'plate_press': {'plate': 1}, 'cut_seal': {'count': 1, 'cut_z_offset_steps': 0},
    'plate_move': {'plate_id': 'PL_POOL', 'target_location': 'LOC_TC'},
    'move_cover': {'cover_id': 'PL_POOL', 'target_location': 'LOC_TC'},
    'plate_prepare': {'plate_ids': ['PL_POOL']}, 'thermal_door': {'door_command': 'DO'},
}


@pytest.mark.parametrize('operation', sorted(MECHANISMS))
def test_mechanism_complete_mapping_and_no_ignored_fields(operation):
    inputs = deepcopy(MECHANISMS[operation])
    r = compile_nodes(node(operation, inputs, on_error='pause_for_operator'))
    a = actions(r)[0]
    assert a['kind'] == operation
    assert a['on_error'] == 'pause_for_operator'
    assert not a['review_required']
    inputs['not_a_native_setting'] = 9
    invalid = compile_nodes(node('note', {'message': 'prefix'}), node(operation, inputs, 'invalid'))
    assert invalid['document'] is None
    assert invalid['issues'][-1]['path'] == '/method/steps/1'


def test_every_example_bound_without_hidden_defaults_and_export():
    documents = []
    entries = bound_examples()
    assert len(entries) == 8
    for entry in entries:
        assert all('default' not in p for p in entry['method']['parameters'])
        r = compile_method(entry)
        emitted = actions(r)
        assert all(a['source_occurrence_id'] for a in emitted)
        assert len(r['provenance']) == len(entry['coverage'])
        assert emitted[-1]['kind'] == 'pause_review'
        documents.append(r['document'])
    assert len(method_examples()) == 8
    for name, inputs in MECHANISMS.items():
        actions(compile_nodes(node(name, inputs)))
    actions(compile_nodes(node('liquid_recipe', {'recipe': recipe()})))


def test_policy_before_positioning_and_fixed_head_effects():
    fixture = bound_examples()[0]
    m = fixture['method']
    m['steps'] = [node('transfer', fixture['bindings']['assemble_inputs'], 'transfer')]
    m['tip_policy'] = {'mode': 'per_transfer',
        'tip_pickup': {'tray': 1, 'well': 'A1', 'overpress': False, 'lift_z': True, 'channels': [0, 1, 2, 3]},
        'tip_eject': {'channels': [0, 1, 2, 3]}}
    r = compile_method(fixture)
    # Fixture's old bindings are removed when the parameter declarations are empty.
    assert r['document']
    emitted = actions(r)
    assert emitted[0]['params']['operation'] == 'load_tip'
    assert emitted[1]['params']['operation'] == 'move'
    assert emitted[-1]['kind'] == 'tip_eject'
    assert emitted[0]['metadata']['bms_method']['generated_by']['policy'] == 'per_transfer'
    assert r['simulation']['state']['vessels']['fixture-source:G1']['volume_ul'] == '990'
    assert r['simulation']['state']['vessels']['fixture-destination:G1']['known_delta_ul'] == '10'
    assert r['simulation']['state']['channels']['3']['tip_loaded'] is False


def test_water_recipe_resolution_emission_and_raw_absence():
    water = next(e for e in starter_entries() if e['family_id'] == 'tecan-original-adp-water' and e['settings'].get('target_volume_ul') == 20 and e['context']['tip_profile_id'] == 'tecan-liha-T200')
    raw = recipe()
    del raw['aspiration_speed_ul_s']
    context = {**water['context'], 'applicable_fields': ['aspirate_speed_ul_s']}
    inputs = {'recipe': raw, 'liquid': {'requested': {}, 'water': water, 'context': context}}
    original = deepcopy(inputs)
    r = compile_nodes(node('liquid_recipe', inputs))
    assert actions(r)[0]['params']['recipe']['aspiration_speed_ul_s'] == 50
    assert inputs == original
    assert r['water_substitutions'][0]['field'] == 'aspirate_speed_ul_s'
    field = r['resolved']['liquids'][0]['fields']['aspirate_speed_ul_s']
    assert field['requested'] == {'present': False}
    assert field['emitted']['status'] == 'emitted'
    assert field['applied']['status'] == 'unknown'
    inputs['liquid']['requested'] = {'aspirate_speed_ul_s': None}
    failed = compile_nodes(node('liquid_recipe', inputs))
    assert failed['document'] is None
    assert failed['water_substitutions'] == []


def test_stages_and_inherited_error_policy():
    r = compile_nodes({'step_id': 'g1', 'type': 'group', 'on_error': 'pause_for_operator', 'steps': [node('wait', {'seconds': 1})]},
        {'step_id': 'g2', 'type': 'group', 'steps': [node('note', {'message': 'done'})]})
    assert len(r['document']['stages']) == 2
    assert [a['on_error'] for a in actions(r)] == ['pause_for_operator', 'stop']
    assert r['document']['stages'][1]['metadata']['bms_method']['group_path'] == ['g2']


def test_multi_complete_recipe_and_missing_values_not_prefix():
    raw = recipe()
    raw.update(mode='multi', dispense_segments=[], multi={
        'sample_count': 2, 'sample_volume_ul': 10, 'conditioning_volume_ul': 1,
        'conditioning_back_to_source_count': 1, 'conditioning_speed_ul_s': 30,
        'conditioning_before': [], 'conditioning_after': [], 'excess_volume_ul': 1,
        'excess': None, 'reaspiration': {'volume_ul': 1, 'speed_ul_s': 30},
        'dispense_to_reaspiration_delay_ms': 1,
        'aliquots': [{'before': [], 'segments': [{'volume_ul': 10, 'speed_ul_s': 30}], 'after': []} for _ in range(2)]})
    assert actions(compile_nodes(node('liquid_recipe', {'recipe': raw})))[0]['params']['recipe']['multi'] == raw['multi']
    raw['multi']['aliquots'].pop()
    assert compile_nodes(node('note', {'message': 'prefix'}), node('liquid_recipe', {'recipe': raw}, 'bad'))['document'] is None


@pytest.mark.parametrize('operation', ['distribute', 'consolidate'])
def test_explicit_repeated_single_compounds(operation):
    from bioxp_method_model import method_catalog
    from jsonschema import Draft202012Validator
    f = bound_examples()[0]
    transfer = f['bindings']['assemble_inputs']
    inputs = {'mode': 'repeated_single', 'transfers': [transfer, deepcopy(transfer)]}
    contract = next(a['inputs'] for a in method_catalog()['actions'] if a['action'] == operation)
    Draft202012Validator(contract).validate(inputs)
    f['method']['steps'] = [node(operation, inputs)]
    result = compile_method(f)
    aa = actions(result)
    assert len(aa) == 16
    assert [p['compound_index'] for p in result['provenance']] == [0, 1]
    assert aa[2]['metadata']['bms_method']['effect_phase'] == 'aspirate'
    assert 'destination' not in aa[2]['metadata']['bms_method']['planned_liquid_effects'][0]
    assert 'source' not in aa[6]['metadata']['bms_method']['planned_liquid_effects'][0]


def test_application_all_phases_and_native_settings():
    from bioxp_method_native import EXPORT
    settings = {k: s['published_default'] for k, s in EXPORT['capabilities']['pressure_parameters'].items()}
    settings.update(slope=[1, 2], pressure_streaming=True)
    selected = {'channels': [0, 1, 2, 3], 'timeout_ms': 1000}
    application = {'implementation': 'original-adp.application.v1', 'event_policy': 'pause_for_operator', 'operations': [
        {'operation': 'settings', **selected, 'values': settings},
        {'operation': 'position', 'positioning': {'operation': 'move', 'location_id': 2, 'well': 'A1', 'position_flag': 0}},
        {'operation': 'z_move', 'target_steps': 100, 'speed_native': 10},
        {'operation': 'plld', **selected, 'start_steps': 100, 'search_target_steps': 200,
         'search_speed_native': 10, 'z_motor_current': 2, 'after_detection_steps': 90, 'after_detection_speed_native': 10},
        *[{'operation': op, **selected, 'volume_ul': 1, 'speed_ul_s': 30} for op in ('leading_air', 'aspirate', 'trailing_air', 'dispense', 'reaspirate')],
        {'operation': 'delay', 'duration_ms': 1}, {'operation': 'empty', **selected, 'speed_ul_s': 30}]}
    assert actions(compile_nodes(node('cavro_application', {'application': application})))[0]['params']['application'] == application
    for field in ('start_speed_ul_s', 'cutoff_speed_ul_s', 'clot_classifier'):
        invalid = deepcopy(application)
        invalid['operations'][0]['values'][field] = 1
        assert compile_nodes(node('cavro_application', {'application': invalid}))['document'] is None


def test_source_segment_dash_preserved_but_not_invented_stroke():
    water = next(e for e in starter_entries() if e['family_id'] == 'tecan-original-adp-water' and e['settings'].get('target_volume_ul') == 20 and e['context']['tip_profile_id'] == 'tecan-liha-T200')
    raw = recipe()
    raw.pop('dispense_segments')
    inputs = {'recipe': raw, 'liquid': {'water': water, 'context': {**water['context'], 'applicable_fields': ['dispense_segments']}}}
    r = compile_nodes(node('liquid_recipe', inputs))
    assert len(actions(r)[0]['params']['recipe']['dispense_segments']) == 1
    field = r['resolved']['liquids'][0]['fields']['dispense_segments']
    assert len(field['resolved']['value']) == 2
    assert len(field['emitted']['value']) == 1
    assert field['applied']['status'] == 'unknown'


def test_water_unknown_fields_and_explicit_phase_mapping():
    raw = recipe()
    context = {'generation': 'original ADP', 'tip_profile_id': 'fixture', 'filter_type': 'unfiltered',
        'mode': 'single-dispense', 'recipe_context': 'fixture', 'target_volume_ul': 20}
    inputs = {'recipe': raw, 'liquid': {'requested': {'slope': [2, 3]}, 'context': context,
        'setting_phases': {'slope': 'aspirate'}}}
    result = compile_nodes(node('liquid_recipe', inputs))
    assert actions(result)[0]['params']['recipe']['phase_settings']['aspirate']['slope'] == [2, 3]
    inputs['liquid']['requested']['cutoff_speed_ul_s'] = 10
    assert compile_nodes(node('liquid_recipe', inputs))['document'] is None


def test_nested_native_expansion_bounded_before_emission():
    r = compile_nodes(node('thermal_profile', {'segments': [MECHANISMS['thermal_hold']], 'repeat': 100001}))
    assert r['document'] is None
    assert r['issues'][0]['code'] == 'resource_limit'


@pytest.mark.parametrize(('operation', 'inputs', 'instruction'), [
    ('pressure_stream', {'channels': [0], 'timeout_ms': 1000, 'enabled': False}, 'settings'),
    ('pipette_settings', {'channels': [0], 'timeout_ms': 1000, 'values': {'slope': [1, 2]}}, 'settings'),
    ('fluid_search', {'channels': [0], 'timeout_ms': 1000, 'start_steps': 100, 'search_target_steps': 200,
        'search_speed_native': 10, 'z_motor_current': 2}, 'plld'),
])
def test_typed_application_convenience_actions(operation, inputs, instruction):
    result = compile_nodes(node(operation, inputs))
    assert actions(result)[0]['params']['application']['operations'][0]['operation'] == instruction


def test_moving_labware_identity_and_mixture_seed_survive_native_lowering():
    f = bound_examples()[0]
    deck = f['method']['deck_plan']
    deck['assignments'].append({'id': 'extra', 'labware_id': 'fixture-source', 'well': 'A1',
        'material_id': 'second', 'volume_ul': '5.5', 'concentration': {'value': '2', 'unit': 'mM'}})
    transfer = deepcopy(f['bindings']['assemble_inputs'])
    transfer['source'].update(location_id=3, station='incorrect display label')
    f['method']['steps'] = [node('plate_move', {'plate_id': 'PL_POOL', 'target_location': 'LOC_RC', 'labware_id': 'fixture-source'}, 'relocate'),
        node('transfer', transfer, 'take')]
    r = compile_method(f)
    aa = actions(r)
    assert 'labware_id' not in aa[0]['params']
    assert aa[0]['metadata']['bms_method']['labware_id'] == 'fixture-source'
    state = r['simulation']['state']
    assert state['labware']['fixture-source']['station'] == 'LOC_RC'
    assert state['vessels']['fixture-source:A1']['volume_ul'] == '995.5'
    assert state['vessels']['fixture-source:A1']['materials'] == ['fixture-material', 'second']
    assert state['head_reference']['location_id'] == 2
    assert state['head_reference']['well'] == 'A1'
    assert state['head_reference']['operation'] == 'lift'


@pytest.mark.parametrize('family', ['water', 'glycerol', 'dmso', 'ethanol', 'bsa'])
def test_exact_source_single_families_preserve_segmented_displacement(family):
    entries = [e for e in starter_entries() if e['context']['tip_profile_id'] == 'tecan-liha-T200'
        and e['context'].get('target_volume_ul') == 20 and e['context']['recipe_context'] == '399156-V1.0-p8']
    # Match source identity, never create a replacement numeric fixture.
    selected = next(e for e in entries if family in e['family_id'])
    water = next(e for e in entries if e['family_id'].endswith('-water'))
    fields = ['target_volume_ul', 'commanded_corrected_aspiration_ul', 'aspirate_speed_ul_s',
        'blowout_leading_air_gap_ul', 'carry_trailing_air_gap_ul', 'dispense_segments']
    raw = recipe()
    for field in ('target_liquid_ul', 'commanded_aspiration_ul', 'aspiration_speed_ul_s', 'dispense_segments'):
        raw.pop(field)
    raw['leading_air'].pop('volume_ul')
    raw['trailing_air'].pop('volume_ul')
    result = compile_nodes(node('liquid_recipe', {'recipe': raw, 'liquid': {
        'context': {**selected['context'], 'applicable_fields': fields}, 'liquid_class': selected, 'water': water}}))
    emitted = actions(result)[0]['params']['recipe']
    assert emitted['commanded_aspiration_ul'] == selected['settings']['commanded_corrected_aspiration_ul']
    expected = [{k: s[k] for k in ('volume_ul', 'speed_ul_s')} for s in selected['settings']['dispense_segments'] if s['evidence'] == 'reported']
    assert emitted['dispense_segments'] == expected
    assert emitted['target_liquid_ul'] == 20


def test_explicit_recipe_value_is_requested_not_water_omission():
    water = next(e for e in starter_entries() if e['family_id'].endswith('-water') and e['context'].get('target_volume_ul') == 20 and e['context']['tip_profile_id'] == 'tecan-liha-T200')
    raw = recipe()
    raw['aspiration_speed_ul_s'] = '55.000'
    inputs = {'recipe': raw, 'liquid': {'water': water, 'context': {
        **water['context'], 'target_volume_ul': '20.00', 'applicable_fields': ['aspirate_speed_ul_s']}}}
    result = compile_nodes(node('liquid_recipe', inputs))
    assert actions(result)[0]['params']['recipe']['aspiration_speed_ul_s'] == 55
    assert not result['water_substitutions']
    assert result['resolved']['liquids'][0]['fields']['aspirate_speed_ul_s']['requested']['value'] == '55.000'
    assert inputs['recipe']['aspiration_speed_ul_s'] == '55.000'
    inputs['recipe'].pop('aspiration_speed_ul_s')
    inherited = compile_nodes(node('liquid_recipe', inputs))
    assert actions(inherited)[0]['params']['recipe']['aspiration_speed_ul_s'] == 50
    assert inherited['water_substitutions'][0]['field'] == 'aspirate_speed_ul_s'


@pytest.mark.parametrize('direction', ['plunger_up', 'plunger_down'])
def test_plunger_alias_uses_actual_diagnostic_contract(direction):
    result = compile_nodes(node('plunger', {'action': direction, 'steps': '10'}))
    assert actions(result)[0]['params'] == {'operation': 'diagnostic_pipette', 'diagnostic': {'action': direction, 'steps': 10}}
    assert compile_nodes(node('plunger', {'action': 'aspirate', 'steps': 10}))['document'] is None
