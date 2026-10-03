from copy import deepcopy
import json
import os
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator
from bioxp_method_compiler import compile_method
from bioxp_method_examples import bound_examples
from bioxp_method_liquids import starter_entries
from bioxp_method_model import method_catalog
from test_bioxp_method_finish import node, compile_nodes, recipe, actions


def class_transfer():
    f = bound_examples()[0]
    inputs = deepcopy(f['bindings']['assemble_inputs'])
    inputs.pop('aspirate_speed')
    inputs.pop('dispense_speed')
    inputs['volume_ul'] = '20.000'
    selected = next(e for e in starter_entries() if 'glycerol' in e['family_id'] and
        e['context']['tip_profile_id'] == 'tecan-liha-T200' and e['context'].get('target_volume_ul') == 20)
    r = recipe()
    for k in ['mode', 'channels', 'target_liquid_ul', 'commanded_aspiration_ul', 'aspiration_speed_ul_s', 'dispense_segments',
              'before_leading_air', 'before_liquid', 'after_liquid', 'before_dispense', 'after_dispense']:
        r.pop(k)
    r['leading_air'].pop('volume_ul')
    r['trailing_air'].pop('volume_ul')
    inputs.update(recipe=r, liquid={'liquid_class': selected, 'context': selected['context']})
    f['method']['steps'] = [node('transfer', inputs)]
    return f, inputs


def test_positioned_class_transfer_segments_geometry_policy_and_raw():
    f, inputs = class_transfer()
    # Stable identity is metadata, not an endpoint field accepted by old emitter.
    schema = next(a['inputs'] for a in method_catalog()['actions'] if a['action'] == 'transfer')
    Draft202012Validator(schema).validate(inputs)
    f['method']['tip_policy'] = {'mode': 'per_transfer', 'tip_pickup': {'tray': 1, 'well': 'A1', 'overpress': False, 'lift_z': True, 'channels': [0,1,2,3]}, 'tip_eject': {'channels':[0,1,2,3]}}
    original = deepcopy(f)
    result = compile_method(f)
    emitted = actions(result)
    assert f == original
    assert [a['kind'] for a in emitted] == ['pipette_manual_physical', 'pipette_manual_physical', 'tip_eject']
    r = emitted[1]['params']['recipe']
    assert emitted[1]['params']['operation'] == 'cavro_liquid_recipe'
    assert r['commanded_aspiration_ul'] == 22.75
    assert r['dispense_segments'] == [{'volume_ul': 12.75, 'speed_ul_s': 375}, {'volume_ul': 10, 'speed_ul_s': 875}]
    assert r['before_leading_air'][0]['positioning']['well'] == 'A1'
    assert r['before_dispense'][0]['positioning']['well'] == 'A1'
    assert result['simulation']['accounting']['liquid'] == {'known_ul': '80.000', 'has_unknown': False}
    assert result['simulation']['accounting']['commanded_displacement']['known_ul'] == '91.00'
    assert result['simulation']['state']['vessels']['fixture-source:G1']['volume_ul'] == '980.000'
    assert all(x['applied']['status'] == 'unknown' for x in result['resolved']['liquids'][0]['fields'].values())
    phases = emitted[1]['metadata']['bms_method']['planned_recipe_phase_effects']
    assert len([p for p in phases if p['recipe_phase'] == 'dispense_segments']) == 8
    assert all(p['volume_ul'] is None for p in phases if p['recipe_phase'] == 'dispense_segments')
    assert result['simulation']['state']['head_reference']['operation'] == 'lift'


def test_class_transfer_pairs_keep_one_tip_policy_and_all_emission_ids():
    f, inputs = class_transfer()
    inputs['source']['wells'].append('B1')
    inputs['destination']['wells'].append('B1')
    f['method']['tip_policy'] = {'mode':'per_transfer', 'tip_pickup':{'tray':1,'well':'A1','overpress':False,'lift_z':True,'channels':[0,1,2,3]}, 'tip_eject':{'channels':[0,1,2,3]}}
    result = compile_method(f)
    emitted = actions(result)
    assert len(emitted) == 4
    assert [a['metadata'].get('pair_index') for a in emitted[1:3]] == [0,1]
    assert result['resolved']['liquids'][0]['fields']['dispense_segments']['emitted']['native_action_ids'] == [a['action_id'] for a in emitted[1:3]]


def test_partial_segment_and_air_effects_do_not_become_liquid_delivery():
    from bioxp_method_simulation import simulate_method
    result = simulate_method([{'occurrence_id':'segment','action':'dispense','status':'partial', 'effects':[
        {'channel':0,'kind':'air','air_ul':2,'source':{'labware_id':'p','well':'A1'}},
        {'channel':0,'volume_ul':None,'commanded_displacement_ul':12.75,'air_ul':None,
         'destination':{'labware_id':'p','well':'A1'}}]}], {'vessels':{'p:A1':{'volume_ul':'30','materials':['m']}}})
    assert result['accounting']['liquid']['has_unknown']
    assert result['accounting']['commanded_displacement']['has_unknown']
    assert result['accounting']['commanded_displacement']['known_ul'] == '12.75'
    assert result['accounting']['air']['known_ul'] == '2'
    assert len(result['lineage']) == 1
    assert result['state']['vessels']['p:A1']['volume_ul'] is None


def test_class_transfer_missing_recipe_never_falls_back():
    f, inputs = class_transfer()
    inputs.pop('recipe')
    assert compile_method(f)['document'] is None


def test_actual_emitted_aspirate_preserves_unknown_liquid_not_zero():
    result = compile_nodes(node('aspirate', {'channels':[0], 'volume_ul':'12.5', 'speed':'30'}))
    assert actions(result)[0]['kind'] == 'pipette_aspirate'
    accounting = result['simulation']['accounting']
    assert accounting['liquid']['has_unknown']
    assert accounting['air']['has_unknown']
    assert accounting['commanded_displacement'] == {'known_ul':'12.5', 'has_unknown':False}


def test_actual_emitted_transfer_unbound_geometry_unknown():
    f = bound_examples()[0]
    result = compile_nodes(node('transfer', f['bindings']['assemble_inputs']))
    assert len(actions(result)) == 8
    assert result['simulation']['accounting']['liquid']['has_unknown']
    assert result['simulation']['accounting']['commanded_displacement']['has_unknown']


@pytest.mark.parametrize('operation, inputs', [
    ('park', {}), ('park', {'rehome': True}),
    ('status_light', {'red': 1, 'green': 2, 'blue': 3}), ('seal_separate', {}),
    ('thermal_setpoint', {'bank':'nest','target_temp_c':30,'fan_speed':12,'cool_rate_c_s':-1,'heat_rate_c_s':1}),
    ('thermal_hold', {'bank':'lid','target_temp_c':30,'duration_s':1,'start':'dispatch','fan_speed':0}),
    ('barcode_read', {'mode':'job_id'}), ('barcode_read', {'mode':'reagent_id'}),
    *[('pipette_pierce', {'plate': {'h':2, 't':0}.get(p, 1),'well':'A1','pattern':p}) for p in 'drht'],
    ('pipette_settings', {'channels':[0],'timeout_ms':1000,'values':{'start_speed_ul_s':2.5,'cutoff_speed_ul_s':200}}),
])
def test_new_native_contracts(operation, inputs):
    schema = next(a['inputs'] for a in method_catalog()['actions'] if a['action'] == operation)
    Draft202012Validator(schema).validate(inputs)
    actions(compile_nodes(node(operation, inputs)))


def test_start_cutoff_exact_decimal_not_binary_multiple():
    inputs = {'channels':[0], 'timeout_ms':1000, 'values':{'start_speed_ul_s':'2.502', 'cutoff_speed_ul_s':'199.999'}}
    emitted = actions(compile_nodes(node('pipette_settings', inputs)))[0]
    assert emitted['params']['application']['operations'][0]['values']['start_speed_ul_s'] == 2.502
    inputs['values']['start_speed_ul_s'] = '2.5021'
    assert compile_nodes(node('pipette_settings', inputs))['document'] is None


def test_all_eight_bound_documents_exported_separately():
    documents = [compile_method(e)['document'] for e in bound_examples()]
    assert len(documents) == 8 and all(documents)
    if os.environ.get('BIOXP_BOUND_EXPORT'):
        Path(os.environ['BIOXP_BOUND_EXPORT']).write_text(json.dumps(documents))
