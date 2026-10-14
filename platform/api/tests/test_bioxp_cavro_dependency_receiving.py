"""Real scratch Methods persistence/compiler; native admission remains inert.

These are BMS receiving outputs, not native execution or device observations.
"""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest
from test_bioxp_methods_api import store, create, BASE, KEY, JOB
from test_bioxp_cavro_transfer import request
from bioxp_method_liquids import starter_entries, starter_profiles


@pytest.mark.asyncio
@pytest.mark.parametrize('assumptions', [{}, {'initial_state': None}, {'initial_state': {}}])
async def test_pinned_liquid_profile_and_calibration_survive_cold_store_and_recovery(store, assumptions):
    client, transport, _, _ = store
    req = request()
    water = next(e for e in starter_entries() if e['family_id'] == 'tecan-original-adp-water'
        and e['settings'].get('target_volume_ul') == 20 and e['context']['tip_profile_id'] == 'tecan-liha-T200')
    inputs = req['method']['steps'][0]['inputs']
    inputs['liquid'] = {'context': deepcopy(water['context']), 'water': water['id'], 'requested': {}}
    del inputs['recipe']['aspiration_speed_ul_s']
    deps = deepcopy(req['dependencies'])
    deps['liquid_classes'] = [water]
    deps['tip_profiles'] = starter_profiles()
    # Explicit documentary calibration pin, not an activated correction or
    # fabricated native analysis result. Runtime correction requires producer.
    deps['calibrations'] = [{'id': 'operator-selected-record', 'revision': 7,
        'model': None, 'context': deepcopy(water['context']), 'observations': []}]
    run_inputs = {'bindings': req.get('bindings', {}), 'dependencies': deps, **assumptions}
    raw = deepcopy(req['method'])
    raw.setdefault('editor_state', {})['run_inputs'] = deepcopy(run_inputs)
    saved = await create(client, raw)
    url = BASE + '/library/' + saved['id']
    cold = (await client.get(url + '/revisions/1')).json()
    assert cold['method'] == raw
    restored = deepcopy(cold['method']['editor_state']['run_inputs'])
    compiled_response = await client.post(BASE + '/compile', json={'method': cold['method'], **restored})
    assert compiled_response.status_code == 200, compiled_response.text
    compiled = compiled_response.json()
    assert compiled['document'], compiled['issues']
    assert compiled['dependencies'] == deps
    assert compiled['resolved']['liquids'][0]['fields']['aspirate_speed_ul_s']['emitted']['value'] == 50
    assert not transport.calls
    response = await client.post(url + '/runs', json={'revision': 1, 'idempotency_key': KEY,
        'expected_generation': 77, 'acknowledge_live': True, **restored})
    assert response.status_code == 202, response.text
    snapshot = response.json()['method_snapshot']
    assert snapshot['dependencies'] == deps and snapshot['compilation'] == compiled
    # Change saved authoring after submission; original received snapshot remains
    # immutable. The transport is explicitly inert, not a native producer.
    edited = deepcopy(raw)
    edited['editor_state']['run_inputs']['dependencies']['liquid_classes'][0]['revision'] = 999
    updated = await client.put(url, json={'method': edited, 'expected_base_revision': 1})
    assert updated.status_code == 200, updated.text
    original = (await client.get(BASE + '/runs/' + JOB)).json()
    recovered_response = await client.post(BASE + '/runs/' + JOB + '/recovery-draft', json={})
    assert recovered_response.status_code == 200, recovered_response.text
    recovered = recovered_response.json()
    assert recovered['dependencies'] == deps
    assert ('initial_state' in recovered) == ('initial_state' in assumptions)
    if assumptions:
        assert recovered['initial_state'] == assumptions['initial_state']
    recovered_method = deepcopy(recovered['method'])
    recovered_method.setdefault('editor_state', {})['run_inputs'] = {
        k: deepcopy(recovered[k]) for k in ('bindings', 'dependencies', 'initial_state') if k in recovered}
    saved_recovery = await create(client, recovered_method, name='Recovery copy')
    reopened = (await client.get(BASE + '/library/' + saved_recovery['id'] + '/revisions/1')).json()
    assert reopened['method'] == recovered_method
    assert (await client.get(BASE + '/runs/' + JOB)).json() == original
    assert [route for route, _ in transport.calls].count('protocol_execute') == 1
    output = os.environ.get('BIOXP_CAVRO_STORE_EXPORT')
    if output:
        directory = Path(output)
        directory.mkdir(parents=True, exist_ok=True)
        name = 'omitted' if not assumptions else 'null' if assumptions['initial_state'] is None else 'empty'
        (directory / (name + '.json')).write_text(json.dumps({'qualification': 'Actual BMS store/compile/relay with inert native transport; not native execution',
            'saved': saved, 'cold': cold, 'compile_request': {'method': cold['method'], **restored}, 'compiled': compiled,
            'run': response.json(), 'sent_native': transport.calls[0], 'recovery': recovered, 'reopened': reopened}, indent=2))
