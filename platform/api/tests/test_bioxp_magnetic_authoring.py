"""Actual discovery/store/compiler receiving, offline; no robot submission.

All numerical and object choices below are software fixtures, not recipe defaults.
"""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bioxp_method_compiler import compile_method
from bioxp_method_examples import bound_examples
from bioxp_method_model import method_catalog, method_examples
from database import UserTemplate, get_session
from test_bioxp_methods_api import BASE, store

VARIANTS = ['purification', 'dna_purification', 'rna_purification', 'cfps_and_purification']


def walk(nodes):
    for node in nodes:
        yield node
        yield from walk(node.get('steps', []))


def variant(identifier):
    return next(e for e in method_examples() if e['id'] == identifier)['authoring_variants'][0]


def authored_request(identifier, count=2):
    method = deepcopy(variant(identifier)['method'])
    # Reuse previously explicit fixture native settings, not any scientific default.
    fixture = bound_examples()[0]
    settings = {n['action']: fixture['bindings'][n['inputs']['expr']['id']]
                for n in walk(fixture['method']['steps']) if n['type'] == 'action' and 'expr' in n['inputs']}
    settings['wait'] = {'seconds': 1}  # Explicit test-only timing.
    bindings: dict = {'wash_count': count}
    for node in walk(method['steps']):
        if node['type'] != 'action' or 'expr' not in node['inputs']:
            continue
        kind = node['action']
        if kind == 'plate_move':
            value = {'plate_id': 'PL_POOL', 'labware_id': 'test-sample',
                     'target_location': 'LOC_TC' if 'off_magnet' in node['step_id'] else 'LOC_MS'}
            # No mode is supplied: preserve native non-PRESS handling, not a new default.
        else:
            value = deepcopy(settings[kind])
            if kind == 'transfer':
                on_magnet = node['step_id'] in {'supernatant_remove', 'wash_remove', 'eluate_collect'}
                sample = {'station': 'LOC_MS' if on_magnet else 'LOC_TC',
                          'location_id': 0 if on_magnet else 2, 'wells': ['A1'], 'labware_id': 'test-sample'}
                reagent = {'station': 'LOC_RC', 'location_id': 3, 'wells': ['A1'], 'labware_id': 'test-reagent'}
                output = {'station': 'LOC_OC', 'location_id': 1, 'wells': ['A1'], 'labware_id': 'test-output'}
                value['source'], value['destination'] = (sample, output) if on_magnet else (reagent, sample)
        bindings[node['inputs']['expr']['id']] = value
    method['deck_plan'] = {'labware': [
        {'id': 'test-sample', 'station': 'LOC_TC', 'name': 'Fixture sample'},
        {'id': 'test-reagent', 'station': 'LOC_RC', 'name': 'Fixture reagent'},
        {'id': 'test-output', 'station': 'LOC_OC', 'name': 'Fixture output'}],
        'materials': [], 'assignments': []}
    return {'method': method, 'bindings': bindings, 'dependencies': {}}


def test_existing_eight_entries_and_bound_companions_are_byte_semantically_preserved():
    entries = [{k: v for k, v in e.items() if k != 'authoring_variants'} for e in method_examples()]
    assert len(entries) == 8
    # Canonical hash of original served discovery pinned at 7a1c4852 (including fixtures).
    assert sha256(json.dumps(entries, sort_keys=True, separators=(',', ':')).encode()).hexdigest() == '9ac1bb21e4d10afbea17b15b942c36f7bdac330866c58c3bcc4a85d685504ad3'
    assert [e['id'] for e in method_examples() if e.get('authoring_variants')] == VARIANTS


@pytest.mark.parametrize('identifier', VARIANTS)
def test_scaffold_is_explicit_unbound_complete_and_uses_existing_direct_inputs(identifier):
    entry = variant(identifier)
    assert entry['id'] == 'on_deck_magnetic' and entry['status'] == 'intentionally_unbound'
    assert entry['bindings'] == {}
    nodes = list(walk(entry['method']['steps']))
    ids = [n['step_id'] for n in nodes]
    assert len(ids) == len(set(ids))
    assert {c['step_id'] for c in entry['coverage']} == set(ids)
    assert len(entry['coverage']) == len(ids)
    actions = {a['action'] for a in method_catalog()['actions']}
    declared = {p['id']: p for p in entry['method']['parameters']}
    assert declared['wash_count'] == {'id': 'wash_count', 'label': 'Authored number of wash repetitions', 'type': 'integer'}
    for node in nodes:
        if node['type'] == 'repeat':
            assert node['count'] == {'expr': {'version': 1, 'op': 'param', 'id': 'wash_count'}}
        elif node['type'] == 'action':
            assert node['action'] in actions
            if node['action'] != 'checkpoint':
                assert node['inputs']['expr']['op'] == 'param'
                assert node['inputs']['expr']['id'] in declared
    for parameter in declared.values():
        if 'default' in parameter:
            assert parameter['default'] == {'target_location': 'LOC_MS'}
    result = compile_method({'method': entry['method']})
    assert result['document'] is None
    assert any(i['code'] == 'missing_binding' for i in result['issues'])


def test_custody_is_documentary_not_native_string_validation():
    catalog = method_catalog()
    custody = catalog['authoring']['custody']
    assert len(custody['objects']) == 17
    assert next(o for o in custody['objects'] if o['token'] == 'PL_POOL')['ordinal'] == 0
    magnet = next(d for d in custody['destinations'] if d['token'] == 'LOC_MS')
    assert magnet['plate_destination'] == 25 and magnet['cover_destination'] is None
    assert catalog['locations']['0'] == 'LOC_MS' and catalog['locations']['25'] == 'LOC_P_MS'
    assert next(d for d in custody['destinations'] if d['token'] == 'LOC_RC')['plate_destination'] is None
    plate = next(a for a in catalog['actions'] if a['action'] == 'plate_move')['inputs']['properties']
    assert plate['plate_id'] == {'type': 'string'} and plate['target_location'] == {'type': 'string'}
    assert plate['move_mode'] == {'type': 'string'}
    method = {'schema': 'bms.bioxp-method.v1', 'steps': [{'step_id': 'retained', 'type': 'action',
        'action': 'plate_move', 'inputs': {'plate_id': 'unknown-retained-token', 'target_location': 'retained-target', 'move_mode': 'retained-mode'}}]}
    result = compile_method({'method': method})
    assert result['document'] is not None, result['issues']


@pytest.mark.parametrize('identifier', VARIANTS)
@pytest.mark.parametrize('count', [0, 2])
def test_exact_test_authored_scaffold_compiles_and_expands_authored_washes(identifier, count):
    request = authored_request(identifier, count)
    before = deepcopy(request)
    result = compile_method(request)
    assert request == before
    assert result['document'] is not None, result['issues']
    wash = [p for p in result['provenance'] if p['step_id'] == 'wash_off_magnet']
    assert len(wash) == count
    native = [a for s in result['document']['stages'] for a in s['actions']]
    placements = [a for a in native if a['kind'] == 'plate_move']
    assert len(placements) == 3 + count * 2
    assert placements[-1]['params']['target_location'] == 'LOC_MS'
    assert all('labware_id' not in a['params'] for a in placements)
    state = result['simulation']['state']
    assert state['labware']['test-sample']['station'] == 'LOC_MS'
    assert all(channel['tip_loaded'] is None for channel in state['channels'].values())
    assert result['simulation']['observed'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('identifier', VARIANTS)
async def test_real_facade_raw_save_cold_reopen_then_actual_compile(store, monkeypatch, identifier):
    client, transport, db_path, _ = store
    monkeypatch.setenv('BMS_BIOXP_MUTATIONS_ENABLED', '0')
    discovered = (await client.get(BASE + '/examples')).json()
    raw = deepcopy(next(e for e in discovered if e['id'] == identifier)['authoring_variants'][0]['method'])
    raw['unknown_extension'] = {'blank': '', 'null': None, 'raw': '01.000', 'false': False, 'zero': 0}
    raw['editor_state'] = {'run_inputs': {'bindings': {'wash_count': None}, 'dependencies': {}, 'initial_state': None}}
    incomplete = {'method': raw, **deepcopy(raw['editor_state']['run_inputs'])}
    response = await client.post(BASE + '/library', json={'method': raw, 'name': identifier + ' incomplete magnetic'})
    assert response.status_code == 201, response.text
    saved = response.json()
    # Fresh engine/session and ASGI facade, not a warm identity-map or client cache.
    engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}')
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async def cold_session():
        async with sessions() as session:
            yield session
    app = client._transport.app
    old_dependency = app.dependency_overrides[get_session]
    app.dependency_overrides[get_session] = cold_session
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://cold') as cold:
            url = BASE + '/library/' + saved['id']
            reopened = (await cold.get(url + '/revisions/1')).json()
            assert reopened == saved
            assert reopened['method'] == raw
            assert reopened['method']['editor_state']['run_inputs']['bindings'] == {'wash_count': None}
            assert reopened['method']['editor_state']['run_inputs']['initial_state'] is None
            missing = (await cold.post(BASE + '/compile', json=incomplete)).json()
            assert missing['document'] is None
            authored = authored_request(identifier)
            authored['method']['unknown_extension'] = raw['unknown_extension']
            authored['method']['editor_state'] = {'run_inputs': {k: deepcopy(authored[k]) for k in ('bindings', 'dependencies')}}
            authored['method']['editor_state']['run_inputs']['initial_state'] = None
            updated = await cold.put(url, json={'method': authored['method'], 'expected_base_revision': 1})
            assert updated.status_code == 200, updated.text
            exact = (await cold.get(url + '/revisions/2')).json()
            assert exact['method'] == authored['method']
            assert exact['method']['editor_state']['run_inputs']['bindings'] == authored['bindings']
            compile_request = {'method': exact['method'], **deepcopy(exact['method']['editor_state']['run_inputs'])}
            response = await cold.post(BASE + '/compile', json=compile_request)
            assert response.status_code == 200, response.text
            result = response.json()
            assert result['document'] is not None, result['issues']
            assert result == compile_method(compile_request)
            output = os.environ.get('BIOXP_MAGNETIC_AUTHORING_EXPORT')
            if output:
                folder = Path(output)
                folder.mkdir(parents=True, exist_ok=True)
                (folder / (identifier + '.json')).write_text(json.dumps({
                    'incomplete_request': incomplete, 'incomplete_saved': reopened,
                    'incomplete_compile': missing, 'authored_saved': exact,
                    'compile_request': compile_request, 'compile_result': result}, indent=2))
        assert not transport.calls
    finally:
        app.dependency_overrides[get_session] = old_dependency
        await engine.dispose()
