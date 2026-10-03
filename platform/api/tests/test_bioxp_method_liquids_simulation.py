from copy import deepcopy
from decimal import Decimal
import hashlib
import json

import pytest
from bioxp_method_liquids import (load_source_catalog, source_catalog_text, starter_entries,
                                 resolve_liquid_settings, record_liquid_application,
                                 starter_profiles, recipe_accounting)
from bioxp_method_simulation import simulate_method, channel_wells, plan_tip_policy


def entry(identifier):
    return next(e for e in starter_entries() if e['id'] == identifier)


def loc(well, labware='plate'):
    return {'labware_id': labware, 'well': well}


def occurrence(identifier, rows, action='transfer', **kwargs):
    return {'occurrence_id': identifier, 'step_id': identifier, 'action': action,
            'inputs': {'channel_transfers': rows}, **kwargs}


def test_source_bytes_counts_numeric_and_null_roundtrip():
    text = source_catalog_text()
    assert hashlib.sha256(text.encode()).hexdigest() == 'ff7687cc6458e7131f5c0ce2bf2ea94f446d2007d306ea5fd89b99377906b02e'
    original = json.loads(text, parse_float=Decimal)
    catalog = load_source_catalog()
    assert len(catalog['classes']) == 5
    assert sum(len(c['single_dispense_variants']) for c in catalog['classes']) == 30
    assert sum(len(c['multi_dispense_variants']) for c in catalog['classes']) == 9
    assert len(catalog['supplementary_water_performance_points']) == 12
    entries = starter_entries()
    assert len(entries) == len({e['id'] for e in entries}) == 51
    originals = [v for c in original['classes'] for group in ('single_dispense_variants', 'multi_dispense_variants') for v in c[group]] + original['supplementary_water_performance_points']
    for projected, raw in zip(entries, originals):
        assert json.loads(json.dumps(projected['source']), parse_float=Decimal) == raw
    params = catalog['original_lld_and_stream_defaults']['parameters']
    assert [p['published_default_n2'] for p in params[:2]] == [15, 5]


def test_profiles_and_multi_derived_accounting_are_not_calibration_or_source_aggregate():
    profiles = starter_profiles()
    assert {p['nominal_capacity_ul'] for p in profiles} == {10, 50, 200, 1000}
    assert all(p['tool_offsets'] is None and p['adapter_support'] == 'unknown' for p in profiles)
    values = recipe_accounting(entry('water-multi-p9-column-4')['settings'])
    assert values['sample_liquid_ul'] == '90'
    assert values['conditioning_return_ul'] == '30'
    assert values['reserved_excess_ul'] == '60'
    assert values['planned_loaded_liquid_ul'] == '180'
    assert values['commanded_corrected_aspiration_ul'] is None
    assert values['dispense_to_reaspiration_delay_ms'] is None
    assert values['final_empty_tip'] is None
    assert recipe_accounting({'number_of_dispenses': 6, 'dispense_volume_ul': 15})['planned_loaded_liquid_ul'] is None


def test_explicit_class_null_and_applicability_are_preserved():
    water = entry('water-t200-20ul-single-p8')
    authored = {**deepcopy(water), 'provenance_kind':'authored', 'settings':{'aspirate_speed_ul_s':None}}
    context = {**water['context'], 'applicable_fields':['aspirate_speed_ul_s']}
    result = resolve_liquid_settings({}, liquid_class=authored, water=water, context=context)
    assert result['resolved'] == {'aspirate_speed_ul_s':None}
    assert result['water_substitutions'] == []
    result = resolve_liquid_settings({}, water=water, context=context)
    assert result['resolved'] == {'aspirate_speed_ul_s':50}


def test_lowered_transfer_tip_continuity_air_and_noncontact_records():
    rows = [occurrence('asp',[{'channel':1, 'source':loc('A1'), 'volume_ul':'10', 'air_ul':'2'}], 'aspirate',transfer_id='t'),
            occurrence('disp',[{'channel':1,'destination':loc('A2'),'volume_ul':'10','air_ul':'2','contact_mode':'free'}],'dispense',transfer_id='t')]
    policy = plan_tip_policy(rows, {'mode':'per_transfer','tip_pickup':{'channels':[1]},'tip_eject':{'channels':[1]}})
    assert [o['action'] for o in policy['occurrences']] == ['tip_pickup','aspirate','dispense','tip_eject']
    result = simulate_method(policy['occurrences'][:-1], {'vessels':{'plate:A1':{'volume_ul':'10','materials':['sample']},'plate:A2':{'volume_ul':'0','materials':[]}}})
    tip = result['state']['channels']['1']
    assert tip['air_ul'] == '0'
    assert tip['volume_ul'] == '0'
    assert [c['contact'] for c in tip['contacts']] == [True,False]
    assert result['state']['vessels']['plate:A2']['materials'] == ['sample']


@pytest.mark.parametrize('family,corrected,segments', [
    ('water', 22.5, [None, 22.5]), ('glycerol', 22.75, [12.75, 10]),
    ('dmso', 20.75, [10.75, 10]), ('ethanol', 24.5, [None, 24.5]),
    ('bsa', 23.75, [None, 23.75])])
def test_exact_twenty_microliter_recipes(family, corrected, segments):
    item = entry(f'{family}-t200-20ul-single-p8')
    assert item['settings']['commanded_corrected_aspiration_ul'] == corrected
    assert [s['volume_ul'] for s in item['settings']['dispense_segments']] == segments
    assert item['settings']['aspiration_delay_ms'] is None


def test_water_fills_only_omitted_matching_applicable_values_and_four_records():
    water = entry('water-t200-20ul-single-p8')
    custom = deepcopy(water)
    custom.update(id='authored-mixture', provenance_kind='BMS choice')
    custom['settings'] = {'aspirate_speed_ul_s': '12.500'}
    requested = {'carry_trailing_air_gap_ul': None, 'blowout_leading_air_gap_ul': '',
                 'target_volume_ul': False, 'unknown': {'raw': '2.000'}}
    before = deepcopy(requested)
    result = resolve_liquid_settings(requested, liquid_class=custom, water=water, context=water['context'])
    assert requested == before
    assert result['resolved']['aspirate_speed_ul_s'] == '12.500'
    assert result['resolved']['carry_trailing_air_gap_ul'] is None
    assert result['resolved']['blowout_leading_air_gap_ul'] == ''
    assert result['resolved']['target_volume_ul'] is False
    assert result['resolved']['unknown'] == {'raw': '2.000'}
    assert 'aspiration_delay_ms' not in result['resolved']
    assert 'dispense_segments' in {s['field'] for s in result['water_substitutions']}
    records = result['fields']['dispense_segments']
    assert records['requested'] == {'present': False}
    assert records['emitted'] == {'status': 'not_emitted'}
    assert records['applied'] == {'status': 'unknown'}
    applied = record_liquid_application(result, emitted={'dispense_segments': {'status': 'emitted', 'operation': 'split', 'value': [22.5]}}, applied={'dispense_segments': {'status': 'unsupported'}})
    assert applied['fields']['dispense_segments']['applied']['status'] == 'unsupported'
    assert result['fields']['dispense_segments']['applied']['status'] == 'unknown'
    assert result['fields']['aspirate_speed_ul_s']['resolved']['reference']['provenance_kind'] == 'BMS choice'


@pytest.mark.parametrize('field,value', [('filter_type','filtered'), ('tip_profile_id','tecan-liha-T10'), ('generation','ADP Detect'), ('target_volume_ul',21), ('recipe_context','399156-V1.0-p6')])
def test_no_nearby_context_or_generation_fallback(field, value):
    water = entry('water-t200-20ul-single-p8')
    context = {**water['context'], field: value}
    result = resolve_liquid_settings({}, water=water, context=context)
    assert result['resolved'] == {}
    assert result['water_substitutions'] == []
    assert result['issues'][0]['code'] == 'liquid_context_mismatch'


def test_multi_and_incomplete_small_tip_evidence_retained():
    multi = entry('water-multi-p9-column-4')
    assert {k: multi['settings'][k] for k in ['number_of_dispenses','number_back_to_source','conditioning_volume_ul','reaspiration_volume_ul','slope_n1','slope_n2']} == dict(number_of_dispenses=6, number_back_to_source=2, conditioning_volume_ul=15, reaspiration_volume_ul=2.75, slope_n1=20, slope_n2=10)
    result = resolve_liquid_settings({'dispense_to_reaspiration_delay_ms':'20.000'}, water=multi, context=multi['context'])
    assert result['resolved']['dispense_to_reaspiration_delay_ms'] == '20.000'
    assert 'excess_destination' not in result['resolved']
    for size in (10,50):
        small = [e for e in starter_entries() if e['context']['tip_profile_id'] == f'tecan-liha-T{size}']
        assert small
        assert all(e['settings']['aspirate_speed_ul_s'] is None for e in small)
        assert all('incomplete' in e['source']['recipe_kind'] for e in small)


def test_96_well_numerical_fixed_head_liquid_air_and_lineage():
    vessels = {'source:A1': {'volume_ul':'3000', 'materials':['water']}}
    rows, occurrences = [], []
    for column in range(1,13):
        for reference in ('A', 'B'):
            associations = channel_wells(f'{reference}{column}', [1,2,3,4], row_increment=2, column_increment=0, reference_channel=1)
            transfers = []
            for association in associations:
                well = association['well']
                vessels[f'plate:{well}'] = {'volume_ul': '0', 'materials': []}
                transfers.append({'channel':association['channel'], 'source':loc('A1','source'), 'destination':loc(well), 'volume_ul':'20', 'commanded_displacement_ul':'22.5', 'air_ul':'21.25'})
            occurrences.append(occurrence(f'column{column}{reference}', transfers))
    result = simulate_method(occurrences, {'vessels':vessels})
    assert len(result['lineage']) == 96
    assert result['state']['vessels']['source:A1']['volume_ul'] == '1080'
    assert all(v['volume_ul'] == '20' for key,v in result['state']['vessels'].items() if key.startswith('plate:'))
    assert result['accounting']['liquid']['known_ul'] == '1920'
    assert result['accounting']['commanded_displacement']['known_ul'] == '2160.0'
    assert result['accounting']['air']['known_ul'] == '2040.00'
    assert result['observed'] is None and result['time']['eta_ms'] is None
    assert result['state']['vessels']['plate:A1']['materials'] == ['water']
    assert vessels['source:A1']['volume_ul'] == '3000'
    assert channel_wells('G1',[1,2,3,4],row_increment=-2,column_increment=0,reference_channel=1)[-1]['well'] == 'A1'


def test_cleanup_explicit_multi_returns_excess_unknowns_and_moving_identity():
    initial = {'vessels':{'source:A1':{'volume_ul':'1000','materials':['wash']},
                           'waste:A1':{'volume_ul':None,'materials':None},
                           **{f'plate:A{i}':{'volume_ul':'0','materials':[]} for i in range(1,7)}}}
    # Authored accounting: six 15-uL aliquots, two 15-uL returns, 60-uL reserved excess.
    occurrences = [occurrence('load',[{'channel':1,'source':loc('A1','source'),'volume_ul':'180'}], 'aspirate')]
    for i in range(2):
        occurrences.append(occurrence(f'return{i}',[{'channel':1,'destination':loc('A1','source'),'volume_ul':'15','kind':'conditioning_return'}],'dispense'))
    for i in range(1,7):
        occurrences.append(occurrence(f'aliquot{i}',[{'channel':1,'destination':loc(f'A{i}'),'volume_ul':'15','final_empty_tip':False}],'dispense'))
    occurrences.append(occurrence('excess',[{'channel':1,'destination':loc('A1','waste'),'volume_ul':'60','kind':'excess'}],'dispense'))
    occurrences.append({'occurrence_id':'magnet','action':'plate_move','inputs':{'labware_id':'plate','destination_station':'magnet'}})
    initial['channels'] = {'1':{'volume_ul':'0','materials':[],'contacts':[]}}
    result = simulate_method(occurrences, initial)
    assert result['state']['vessels']['source:A1']['volume_ul'] == '850'
    assert result['state']['channels']['1']['volume_ul'] == '0'
    assert result['state']['vessels']['waste:A1']['volume_ul'] is None
    assert result['state']['vessels']['waste:A1']['known_delta_ul'] == '60'
    assert result['accounting']['conditioning_return']['known_ul'] == '30'
    assert result['accounting']['excess']['known_ul'] == '60'
    assert result['state']['labware']['plate']['station'] == 'magnet'
    assert result['state']['vessels']['plate:A6']['volume_ul'] == '15'
    assert all(s['final_empty_tip'] is False for s in result['strokes'] if s['occurrence_id'].startswith('aliquot'))


def test_partial_only_known_effects_unknown_fill_and_advisory_deficit():
    planned = {'channel':1,'source':loc('A1'),'destination':loc('A2'),'volume_ul':'20'}
    result = simulate_method([occurrence('partial',[planned],status='partial',effects=[{**planned,'volume_ul':'3'}])],
                             {'vessels':{'plate:A1':{'volume_ul':'2'},'plate:A2':{'volume_ul':None}}})
    assert result['state']['vessels']['plate:A1']['volume_ul'] == '-1'
    assert result['state']['vessels']['plate:A2']['volume_ul'] is None
    assert result['state']['vessels']['plate:A2']['known_delta_ul'] == '3'
    assert {i['code'] for i in result['issues']} == {'partial_effects_unknown','planned_volume_deficit'}
    assert all(i['category'] == 'advisory' for i in result['issues'])


def test_segment_displacement_is_not_added_to_intended_liquid():
    segments = entry('glycerol-t200-20ul-single-p8')['settings']['dispense_segments']
    result = simulate_method([occurrence('split',[{'channel':1,'source':loc('A1'),'destination':loc('A2'),
                                                 'volume_ul':'20','commanded_displacement_ul':'22.75',
                                                 'dispense_segments':segments}])],
                             {'vessels':{'plate:A1':{'volume_ul':'20'},'plate:A2':{'volume_ul':'0'}}})
    assert result['state']['vessels']['plate:A2']['volume_ul'] == '20'
    assert result['strokes'][0]['dispense_segments'] == segments
    assert result['accounting']['commanded_displacement']['known_ul'] == '22.75'


def test_ten_thousand_occurrences_retain_linear_delta_history():
    result = simulate_method([occurrence(str(i),[{'channel':1,'source':loc('A1','source'),
                            'destination':loc(f'A{i+1}'),'volume_ul':'0.01'}]) for i in range(10000)],
                            {'vessels':{'source:A1':{'volume_ul':'100','materials':['sample']}}})
    assert len(result['after_occurrences']) == len(result['lineage']) == 10000
    assert result['state']['vessels']['source:A1']['volume_ul'] == '0.00'
    assert all(len(s['state_delta']['vessels']) == 2 for s in result['after_occurrences'])
    assert all(len(s['state_delta']['channels']['1']['contacts_append']) == 2 for s in result['after_occurrences'])


@pytest.mark.parametrize('mode,pickups',[('manual',0),('per_transfer',3),('per_source',2),('per_step',3)])
def test_tip_policy_order_and_contact_intent(mode,pickups):
    occurrences = [occurrence(str(i),[{'channel':1,'source':loc('A1' if i<2 else 'A2'),'destination':loc('A3'),'volume_ul':'1'}]) for i in range(3)]
    result = plan_tip_policy(occurrences, {'mode':mode,'tip_pickup':{'channels':[1],'head_reference':'A1'},'tip_eject':{'channels':[1]}})
    rows = result['occurrences']
    assert sum(o['action']=='tip_pickup' for o in rows) == pickups
    assert sum(o['action']=='tip_eject' for o in rows) == pickups
    assert [o for o in rows if 'generated_by' not in o] == occurrences
    if pickups:
        assert rows[0]['action'] == 'tip_pickup' and rows[-1]['action'] == 'tip_eject'
        assert all(o['generated_by']['policy']==mode for o in rows if 'generated_by' in o)
