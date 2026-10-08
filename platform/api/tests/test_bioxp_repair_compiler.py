"""Compiler repair receiving through real pure owners and HTTP/persistence.

BIOXP_REPAIR_FROZEN points at the independent immutable counter-audit results.
BIOXP_REPAIR_EXPORT retains exact API/compiler cases for separate native receiving.
"""
from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
import pytest
from bioxp_method_compiler import compile_method
from bioxp_method_liquids import resolve_liquid_settings, starter_entries, FIELDS
from bioxp_method_planning import liquid_selection_schema, liquid_field_matrix
from bioxp_method_simulation import simulate_method
from test_bioxp_method_liquid_mapping import CONTEXT, entry
from test_bioxp_methods_api import store  # real isolated revision store, NOT compiler_stub


def node(action, inputs, sid='probe'):
    return dict(type='action', step_id=sid, action=action, inputs=inputs)


def request(*steps, **kwargs):
    return {'method': dict(schema='bms.bioxp-method.v1', steps=list(steps), **kwargs)}


def recipe(multi=False, channels=None):
    r = dict(mode='single', channels=channels or [2], timeout_ms=2000,
        target_liquid_ul=8, commanded_aspiration_ul=9, aspiration_speed_ul_s=45,
        aspiration_delay_ms=3, leading_air=None, trailing_air=None,
        before_leading_air=[], before_liquid=[], after_liquid=[], before_dispense=[], after_dispense=[],
        dispense_segments=[dict(volume_ul=9, speed_ul_s=65)], final_empty_speed_ul_s=None,
        final_empty_before=[], multi=None)
    if multi:
        r.update(mode='multi', target_liquid_ul=21, commanded_aspiration_ul=24, dispense_segments=[],
            multi=dict(sample_count=3, sample_volume_ul=4, conditioning_volume_ul=2,
                conditioning_back_to_source_count=3, conditioning_speed_ul_s=25,
                conditioning_before=[], conditioning_after=[], excess_volume_ul=3,
                excess=None, reaspiration=None, dispense_to_reaspiration_delay_ms=0,
                aliquots=[dict(before=[], segments=[dict(volume_ul=4, speed_ul_s=25)], after=[]) for _ in range(3)]))
    return r


def actions(result):
    return [a for s in (result['document'] or {}).get('stages', []) for a in s['actions']]


def export(name, q, r):
    if path := os.environ.get('BIOXP_REPAIR_EXPORT'):
        with Path(path).open('a') as stream:
            stream.write(json.dumps(dict(name=name, request=q, result=r)) + '\n')


def compiled(name, q):
    before = deepcopy(q)
    r = compile_method(q)
    assert q == before
    export(name, q, r)
    return r


@pytest.mark.parametrize('raw', ['2.00000000000000000000000000001', '1.99999999999999999999999999999', '-0.00000000000000000000000000001'])
@pytest.mark.parametrize('typed', [False, True])
def test_exact_integer_before_context_rounding(raw, typed):
    value = {'expr': {'version': 1, 'op': 'literal', 'type': 'number', 'value': raw}} if typed else raw
    note = node('note', {'message': 'not submitted'})
    q = request(dict(type='repeat', step_id='repeat', count=value, steps=[note]))
    assert compiled('repeat-fraction', q)['document'] is None
    for unit in (None, 'ms'):
        p = dict(id='count', type='integer', default=value)
        if unit:
            p['unit'] = unit
        assert compiled('integer-fraction', request(note, parameters=[p]))['document'] is None


@pytest.mark.parametrize('deck,path', [(None, ''), (False, ''), ('retained', ''), ([], ''),
    ({'labware': [None]}, '/labware/0'), ({'assignments': [None]}, '/assignments/0'),
    ({'labware': False}, '/labware'), ({'assignments': {'x': 1}}, '/assignments')])
def test_malformed_deck_is_advisory(deck, path):
    q = request(node('note', {'message': 'independent native action'}), deck_plan=deck)
    r = compiled('deck', q)
    assert r['document'] is not None, r['issues']
    assert any(i['path'] == '/method/deck_plan' + path and i['category'] == 'advisory' for i in r['issues'])
    assert r['simulation']['initial_state'] == {}


@pytest.mark.parametrize('value', [None, '', 0, '17.000'])
def test_authored_presence_overrides_manufacturer_null_and_water(value):
    cls = entry('class', {'aspirate_speed_ul_s': value}, 'manufacturer')
    cls['authored_settings'] = {'aspirate_speed_ul_s': value}
    water = entry('water', {'aspirate_speed_ul_s': 35}, 'manufacturer')
    r = resolve_liquid_settings({}, liquid_class=cls, water=water, context=CONTEXT)
    assert r['resolved']['aspirate_speed_ul_s'] == value
    assert not r['water_substitutions']
    assert r['fields']['aspirate_speed_ul_s']['authorship']['status'] == 'authored'
    r = resolve_liquid_settings({'aspirate_speed_ul_s': 42}, liquid_class=cls, water=water, context=CONTEXT)
    assert r['resolved']['aspirate_speed_ul_s'] == 42
    native = recipe(); del native['aspiration_speed_ul_s']
    q = request(node('liquid_recipe', dict(recipe=native, liquid=dict(context=CONTEXT, liquid_class=cls, water=water))))
    out = compiled('authored-presence', q)
    assert bool(out['document']) is (value == '17.000')


def test_legacy_manufacturer_null_and_equal_context_spelling():
    cls = entry('class', {'aspirate_speed_ul_s': None}, 'manufacturer')
    water = entry('water', {'aspirate_speed_ul_s': 35}, 'manufacturer')
    r = resolve_liquid_settings({}, liquid_class=cls, water=water, context=CONTEXT)
    assert r['resolved']['aspirate_speed_ul_s'] == 35
    for target, discrepancy in [('20.000', False), (8, True)]:
        native = recipe(); native['target_liquid_ul'] = target; del native['aspiration_speed_ul_s']
        out = compiled('context', request(node('liquid_recipe', dict(recipe=native, liquid=dict(context=CONTEXT, water=water)))))
        assert out['document'] is not None
        assert actions(out)[0]['params']['recipe']['aspiration_speed_ul_s'] == 35
        assert any(i['code'] == 'liquid_recipe_context_discrepancy' for i in out['issues']) is discrepancy
        assert all(i['category'] == 'advisory' for i in out['issues'])


@pytest.mark.parametrize('channels', [[2], [0, 1, 2, 3]])
@pytest.mark.parametrize('action', ['liquid_recipe', 'distribute'])
def test_true_multi_native_quantities_are_not_zero(channels, action):
    raw = recipe(True, channels)
    inputs = {'recipe': raw, **({'mode': 'multi'} if action == 'distribute' else {})}
    r = compiled('multi-' + action, request(node(action, inputs)))
    assert r['document'] is not None, r['issues']
    account = r['simulation']['accounting']
    expected = {'liquid': 12, 'conditioning_return': 6, 'excess': 3, 'retained': 3,
                'net_source_liquid': 15, 'air': 0, 'commanded_displacement': 42, 'waste': 0}
    for key, value in expected.items():
        assert Decimal(account[key]['known_ul']) == value * len(channels)
        assert not account[key]['has_unknown']
    assert not r['simulation']['lineage']  # no invented location


def test_multi_reaspiration_is_air_excess_destination_and_partial_unknown():
    raw = recipe(True)
    raw['multi']['reaspiration'] = dict(volume_ul=1.5, speed_ul_s=25)
    raw['multi']['excess'] = dict(before=[], segments=[dict(volume_ul=3, speed_ul_s=25)], after=[])
    r = compiled('multi-air-excess', request(node('liquid_recipe', {'recipe': raw})))
    assert r['document'] is not None, r['issues']
    a = r['simulation']['accounting']
    assert Decimal(a['air']['known_ul']) == Decimal('4.5')
    assert Decimal(a['commanded_displacement']['known_ul']) == Decimal('49.5')
    assert a['waste']['has_unknown'] and a['retained']['has_unknown']
    row = deepcopy(r['resolved']['occurrences'][0]); row['_native_recipe'] = actions(r)[0]['params']['recipe']
    row['status'] = 'partial'
    assert all(v['has_unknown'] for v in simulate_method([row])['accounting'].values())


@pytest.mark.parametrize('source', ['requested', 'authored', 'manufacturer', 'water'])
def test_source_slopes_and_top_speed_map_to_native_owner(source):
    values = {'slope_n1': 3, 'slope_n2': 4, 'start_speed_ul_s': 12, 'cutoff_speed_ul_s': 15,
              'aspiration_top_speed_ul_s': 47}
    select = {'context': CONTEXT, 'setting_phases': {key: 'aspirate' for key in values if key != 'aspiration_top_speed_ul_s'}}
    if source == 'requested':
        select['requested'] = values
    else:
        e = entry(source, values, 'manufacturer')
        if source == 'authored':
            e['authored_settings'] = deepcopy(values)
        select['water' if source == 'water' else 'liquid_class'] = e
    raw = recipe(); del raw['aspiration_speed_ul_s']
    r = compiled('mapped-' + source, request(node('liquid_recipe', {'recipe': raw, 'liquid': select})))
    assert r['document'] is not None, r['issues']
    native = actions(r)[0]['params']['recipe']
    assert native['aspiration_speed_ul_s'] == 47
    assert native['phase_settings']['aspirate'] == {'slope': [3, 4], 'start_speed_ul_s': 12, 'cutoff_speed_ul_s': 15}
    fields = r['resolved']['liquids'][0]['fields']
    assert fields['slope_n1']['emitted']['value'] == 3
    assert all(fields[key]['applied'] == {'status': 'unknown'} for key in values)
    nested = r['resolved']['occurrences'][0]['inputs']['recipe']['liquid_settings']['bms_resolution']
    assert nested == r['resolved']['liquids'][0]


def test_whole_document_failure_clears_all_owned_emission_copies():
    good = node('liquid_recipe', {'recipe': recipe(), 'liquid': {'context': CONTEXT, 'requested': {'aspirate_speed_ul_s': 45}}})
    r = compiled('later-failure', request(good, node('unsupported', {}, 'bad')))
    assert r['document'] is None
    def check(v):
        if isinstance(v, dict):
            if 'emitted' in v:
                assert v['emitted']['status'] == 'not_emitted'
            for x in v.values(): check(x)
        elif isinstance(v, list):
            for x in v: check(x)
    check(r['resolved'])
    ok = compiled('after-failure', request(good))
    assert ok['document'] is not None
    assert ok['resolved']['liquids'][0]['fields']['aspirate_speed_ul_s']['emitted']['status'] == 'emitted'


def test_discovery_inventory_and_effective_seed():
    schema = liquid_selection_schema()
    assert 'authored_settings' in schema['properties']['liquid_class']['anyOf'][1]['properties']
    rows = liquid_field_matrix()
    assert FIELDS <= {row['field'] for row in rows}
    assert all(row['native_paths'] or row['boundary'] for row in rows)
    q = request(node('note', {'message': 'not moving'}), deck_plan={
        'labware': [{'id': 'tube', 'station': 'LOC_MS'}],
        'assignments': [{'labware_id': 'tube', 'well': 'A1', 'volume_ul': '7'},
                        {'labware_id': 'tube', 'well': 'A2', 'volume_ul': None}]})
    q['initial_state'] = {'vessels': {'tube:A1': {'volume_ul': '9.5'}}}
    r = compiled('effective-seed', q)
    initial = r['simulation']['initial_state']
    assert initial['vessels']['tube:A1']['volume_ul'] == '9.5'
    assert initial['vessels']['tube:A2']['volume_ul'] is None
    assert initial['labware']['tube']['station'] == 'LOC_MS'
    r['simulation']['state']['vessels']['tube:A1']['volume_ul'] = 'changed'
    assert initial['vessels']['tube:A1']['volume_ul'] == '9.5'


@pytest.mark.asyncio
async def test_actual_http_discovery_compile_and_class_revision(store):
    client, _, _, _ = store
    base = '/api/bioxp/methods'
    discovery = (await client.get(base + '/schema')).json()
    native_schema = await client.get(discovery['openapi'])
    assert native_schema.status_code == 200
    assert base + '/compile' in native_schema.json()['paths']
    assert native_schema.json() == (await client.get('/openapi.json')).json()
    q = request(node('note', {'message': 'offline receiving'}), deck_plan=None)
    r = await client.post(base + '/compile', json=q)
    assert r.status_code == 200 and r.json()['document'] is not None
    assert r.json()['simulation']['initial_state'] == {}
    cls = entry('saved', {'aspirate_speed_ul_s': None}, 'manufacturer')
    cls.update(schema='bms.bioxp-liquid-class.v1', authored_settings={'aspirate_speed_ul_s': None})
    saved = await client.post(base + '/liquid-classes', json={'name': 'authored null', 'method': cls})
    assert saved.status_code == 201, saved.text
    record = saved.json()
    fetched = await client.get(base + '/liquid-classes/' + record['id'])
    assert fetched.status_code == 200 and fetched.json()['method'] == cls


def test_independent_frozen_counterexamples():
    path = os.environ.get('BIOXP_REPAIR_FROZEN')
    if not path:
        pytest.skip('Set BIOXP_REPAIR_FROZEN to independent audit results for frozen receiving')
    cases = json.loads(Path(path).read_text())['cases']
    results = {name: compiled('frozen:' + name, case['request']) for name, case in cases.items()}
    for name, r in results.items():
        if name.startswith(('repeat:', 'integer:')):
            raw = name.split(':', 1)[1]
            assert bool(r['document']) is (Decimal(raw) >= 0 and Decimal(raw) == Decimal(raw).to_integral_value())
        if name.startswith('deck:'):
            assert r['document'] is not None
    def pickups(r): return sum(a['params'].get('operation') == 'load_tip' for a in actions(r))
    assert pickups(results['transfer_collision']) == pickups(results['transfer_unique']) == 2
    assert results['water_context_target:8']['document'] is not None
    assert any(i['code'] == 'liquid_recipe_context_discrepancy' for i in results['water_context_target:8']['issues'])
    assert results['emission_failure_ledger']['document'] is None
    assert Decimal(results['multi_direct']['simulation']['accounting']['conditioning_return']['known_ul']) == 6


def test_recompile_frozen_model_corpus(tmp_path):
    import gzip
    fixture = Path(__file__).parent / 'fixtures/bioxp_methods/final-model-documents.json.gz'
    documents = json.loads(gzip.decompress(fixture.read_bytes()))
    rebuilt = []
    for old in documents:
        metadata = old['metadata']['bms_method']
        q = {key: deepcopy(metadata[key]) for key in ('method', 'bindings', 'dependencies', 'initial_state')}
        r = compile_method(q)
        assert r['document'] is not None, r['issues']
        # Compare executable children, not deliberately repaired evidence/digests.
        def executable(doc):
            value = deepcopy(doc)
            value.pop('protocol_id')
            value.pop('metadata')
            for stage in value['stages']:
                for action in stage['actions']:
                    action['params'].get('recipe', {}).get('liquid_settings', {}).pop('bms_resolution', None)
            return value
        assert executable(r['document']) == executable(old)
        rebuilt.append(r['document'])
    assert len(rebuilt) == 87
    if path := os.environ.get('BIOXP_REPAIR_MODEL_DOCUMENTS'):
        Path(path).write_text(json.dumps(rebuilt))


@pytest.mark.parametrize('mode,expected', [('manual', 0), ('per_step', 4), ('per_transfer', 4), ('per_source', 1)])
def test_policy_scopes_calls_loops_groups_and_children(mode, expected):
    transfer = dict(source=dict(station='LOC_MS', location_id=0, wells=['B2']),
        destination=dict(station='LOC_TC', location_id=2, wells=['B3']), channels=[1,3],
        volume_ul='2.75', aspirate_speed='25', dispense_speed='35', source_position_flag=0,
        destination_position_flag=0, source_lift_height_steps=None, destination_lift_height_steps=None)
    policy = dict(mode=mode, tip_pickup=dict(tray=1, well='B2', overpress=False, lift_z=True, channels=[0,1,2,3]),
                  tip_eject=dict(channels=[0,1,2,3]))
    groups = [dict(type='group', step_id=sid, steps=[node('transfer', deepcopy(transfer), 'same')]) for sid in ('left', 'right')]
    q = request(dict(type='repeat', step_id='loop', count=2, steps=groups), tip_policy=policy)
    r = compiled('policy-' + mode, q)
    assert r['document'] is not None, r['issues']
    native = actions(r)
    assert sum(a['params'].get('operation') == 'load_tip' for a in native) == expected
    assert sum(a['kind'] == 'tip_eject' for a in native) == expected
    for i, a in enumerate(native):
        if a['kind'] == 'pipette_aspirate':
            following = next(x for x in native[i+1:] if x['kind'] in {'pipette_dispense', 'tip_eject'})
            assert following['kind'] == 'pipette_dispense'


# Every published direct Recipe/Settings mapping is compiled from each source.
from bioxp_method_planning import RECIPE_FIELDS, AIR_GAP_SPEED_FIELD
from bioxp_method_native import SETTINGS


@pytest.mark.parametrize('field', sorted(set(RECIPE_FIELDS) | set(SETTINGS['properties']) | {AIR_GAP_SPEED_FIELD}))
@pytest.mark.parametrize('source', ['requested', 'authored', 'manufacturer', 'water'])
def test_each_published_mapping_received(field, source):
    raw = recipe(multi=field != 'dispense_segments')
    raw['leading_air'] = {'volume_ul': 1, 'speed_ul_s': 25}
    raw['trailing_air'] = {'volume_ul': 2, 'speed_ul_s': 25}
    if raw['multi'] is not None:
        raw['multi']['reaspiration'] = {'volume_ul': 0.5, 'speed_ul_s': 25}
    context = deepcopy(CONTEXT)
    context['mode'] = 'multi-dispense' if raw['multi'] else 'single-dispense'
    if raw['multi']:
        context.pop('target_volume_ul')
        context.update(aliquot_volume_ul=4, sample_count=3)
    else:
        context['target_volume_ul'] = 8
    phases = {}
    if field in RECIPE_FIELDS:
        path = RECIPE_FIELDS[field]
        target = raw
        for key in path[:-1]: target = target[key]
        value = target.pop(path[-1])
    elif field == AIR_GAP_SPEED_FIELD:
        value = raw['leading_air'].pop('speed_ul_s')
        raw['trailing_air'].pop('speed_ul_s')
    else:
        schema = SETTINGS['properties'][field]
        value = [3, 4] if field == 'slope' else True if schema['type'] == 'boolean' else schema.get('minimum', 1)
        phases[field] = 'aspirate'
    select = {'context': context, 'setting_phases': phases}
    if source == 'requested':
        select['requested'] = {field: value}
    else:
        cls = dict(id=source, revision=1, context=deepcopy(context), settings={field: value}, provenance_kind='manufacturer')
        if source == 'authored': cls['authored_settings'] = {field: value}
        select['water' if source == 'water' else 'liquid_class'] = cls
    q = request(node('liquid_recipe', {'recipe': raw, 'liquid': select}))
    r = compiled('field:' + field + ':' + source, q)
    assert r['document'] is not None, r['issues']
    f = r['resolved']['liquids'][0]['fields'][field]
    assert f['emitted']['status'] == 'emitted'
    assert f['resolved']['source'] == {'requested': 'requested', 'authored': 'liquid_class', 'manufacturer': 'liquid_class', 'water': 'water'}[source]
    assert f['applied']['status'] == 'unknown'


@pytest.mark.asyncio
async def test_pinned_authored_revision_compile_receiving(store):
    client, transport, _, _ = store
    base = '/api/bioxp/methods'
    water = entry('water', {'aspirate_speed_ul_s': 35}, 'manufacturer')
    cls = entry('class', {'aspirate_speed_ul_s': None}, 'manufacturer')
    cls.update(schema='bms.bioxp-liquid-class.v1', authored_settings={'aspirate_speed_ul_s': None},
               source={'documentary_null': None, 'raw': '003.7500'})
    created = (await client.post(base + '/liquid-classes', json={'name': 'source copy', 'method': cls})).json()
    url = base + '/liquid-classes/' + created['id']
    changed = deepcopy(cls)
    changed['settings']['aspirate_speed_ul_s'] = '17.000'
    changed['authored_settings']['aspirate_speed_ul_s'] = '17.000'
    update = await client.put(url, json={'method': changed, 'expected_base_revision': 1})
    assert update.status_code == 200, update.text
    old = (await client.get(url + '/revisions/1')).json()['method']
    new = (await client.get(url + '/revisions/2')).json()['method']
    assert old == cls and new == changed and new['source'] == old['source']
    for revision, expected in [(old, None), (new, 17)]:
        raw = recipe(); del raw['aspiration_speed_ul_s']
        q = request(node('liquid_recipe', {'recipe': raw,
            'liquid': {'context': CONTEXT, 'liquid_class': 'class', 'water': water}}))
        q['dependencies'] = {'liquid_classes': [revision]}
        response = await client.post(base + '/compile', json=q)
        assert response.status_code == 200
        r = response.json()
        export('HTTP-class-revision', q, r)
        assert bool(r['document']) is (expected is not None)
        if expected is not None:
            assert actions(r)[0]['params']['recipe']['aspiration_speed_ul_s'] == expected
    assert not transport.calls


def test_source_projection_bytes_and_discovery_export():
    from bioxp_method_liquids import source_catalog_text, SOURCE_PATH
    from bioxp_method_model import method_catalog, method_examples
    import hashlib
    source = source_catalog_text()
    assert source.encode() == SOURCE_PATH.read_bytes()
    original = json.loads(source)
    variants = [v for c in original['classes'] for key in ('single_dispense_variants', 'multi_dispense_variants')
                for v in c[key]] + original['supplementary_water_performance_points']
    assert [e['source'] for e in starter_entries()] == variants
    assert len(variants) == 51
    combined = next(e for e in method_examples() if e['id'] == 'cfps_and_purification')['method']
    assert [(n['step_id'], n['label']) for n in combined['steps']] == [('expression', 'Expression'), ('purification', 'Purification')]
    if target := os.environ.get('BIOXP_REPAIR_DISCOVERY'):
        Path(target).write_text(json.dumps({'catalog': method_catalog(), 'liquid_selection': liquid_selection_schema(),
            'source_sha256': hashlib.sha256(source.encode()).hexdigest(), 'source_entries': len(variants)}, indent=2))


def test_multi_endpoint_rows_do_not_double_count_and_extra_phases_are_unknown():
    raw = recipe(True)
    q = request(node('liquid_recipe', {'recipe': raw, 'channel_transfers': [
        {'channel': 2, 'source': {'labware_id': 'source', 'well': 'A1'},
         'destination': {'labware_id': 'destination', 'well': 'A1'}, 'volume_ul': 12}]}))
    q['initial_state'] = {'vessels': {'source:A1': {'volume_ul': None}, 'destination:A1': {'volume_ul': '0'}}}
    r = compiled('multi-explicit-effects', q)
    assert r['document'] is not None
    assert Decimal(r['simulation']['accounting']['liquid']['known_ul']) == 12
    assert Decimal(r['simulation']['accounting']['conditioning_return']['known_ul']) == 6
    assert r['simulation']['state']['vessels']['source:A1']['volume_ul'] is None
    assert Decimal(r['simulation']['state']['vessels']['source:A1']['known_delta_ul']) == -12
    raw['after_dispense'] = [{'operation': 'aspirate', 'channels': [2], 'timeout_ms': 2000,
                              'volume_ul': 1, 'speed_ul_s': 25}]
    r = compiled('multi-extra-phase', q)
    assert r['document'] is not None
    assert r['simulation']['accounting']['liquid']['has_unknown']
    assert any(i['code'] == 'recipe_additional_effects_unknown' for i in r['simulation']['issues'])


def test_final_empty_retains_known_displacement_subtotal_without_inventing_waste():
    raw = recipe(True)
    raw['final_empty_speed_ul_s'] = 25
    r = compiled('multi-final-empty', request(node('liquid_recipe', {'recipe': raw})))
    assert r['document'] is not None
    account = r['simulation']['accounting']
    assert Decimal(account['commanded_displacement']['known_ul']) == 42
    assert account['commanded_displacement']['has_unknown']
    assert account['waste']['has_unknown'] and account['retained']['has_unknown']
