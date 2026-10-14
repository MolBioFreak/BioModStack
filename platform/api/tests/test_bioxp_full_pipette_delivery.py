"""Unbound discovery -> ordinary scratch Save/cold Open -> native receiving.

Companions supply explicit SOFTWARE TEST inputs only, never starter defaults.
Native execution is separately guarded and consumes these actual HTTP outputs.
"""
from copy import deepcopy
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from database import get_session
from routers.bioxp import router
from bioxp_method_calibration import calibration_examples, calibration_bound_fixture
from test_bioxp_methods_api import store, BASE, create
from test_bioxp_cavro_finish import export


@asynccontextmanager
async def cold_client(path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{path}')
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async def session_dependency():
        async with sessions() as session:
            yield session
    app = FastAPI()
    app.include_router(router, prefix='/api/bioxp')
    app.dependency_overrides[get_session] = session_dependency
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://cold') as client:
            yield client
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('entry_index', range(5))
async def test_unbound_starter_save_cold_open_author_compile(store, entry_index):
    client, transport, path, _ = store
    entry = calibration_examples()[entry_index]
    discovery = (await client.get(BASE + '/examples')).json()
    actual = next(item for item in discovery if item['id'] == entry['id'])
    assert actual['method'] == entry['method'] and actual['bindings'] == {}
    raw = deepcopy(actual['method'])
    assert all('default' not in parameter for parameter in raw['parameters'])
    saved = await create(client, raw)
    url = BASE + '/library/' + saved['id']
    exact = await client.get(url + '/revisions/1')
    assert exact.status_code == 200 and exact.json()['method'] == raw
    async with cold_client(path) as cold:
        opened = (await cold.get(url + '/revisions/1')).json()
        assert opened == exact.json()
        incomplete = await cold.post(BASE + '/compile', json={'method': opened['method']})
        assert incomplete.status_code == 200 and incomplete.json()['document'] is None
        # Deliberate authored run bindings; no edit to the discovery entry.
        bindings = calibration_bound_fixture(entry)['bindings']
        if 'target' in bindings:
            bindings['target'] = '20.000000000000000001' if entry_index == 0 else '25.0000'
            bindings['commanded'] = '22.5000' if entry_index == 0 else '25.0000'
        if 'settle_inputs' in bindings:
            bindings['settle_inputs']['seconds'] = '0'  # explicitly authored inert test dwell
        authored = deepcopy(opened['method'])
        authored['editor_state'] = {'run_inputs': {'bindings': bindings}, 'raw': {'blank': '', 'null': None}}
        updated = await cold.put(url, json={'method': authored, 'expected_base_revision': 1})
        assert updated.status_code == 200, updated.text
    async with cold_client(path) as cold:
        reopened = (await cold.get(url + '/revisions/2')).json()
        assert reopened['method'] == authored
        request = {'method': reopened['method'], **reopened['method']['editor_state']['run_inputs']}
        response = await cold.post(BASE + '/compile', json=request)
        assert response.status_code == 200 and response.json()['document'], response.text
        result = response.json()
        actions = [a for s in result['document']['stages'] for a in s['actions']]
        if entry_index == 1:
            apply = next(a for a in actions if a['params'].get('operation') == 'height_apply')
            assert apply['params']['calculation']['path'] == ['height_adjustment']
        else:
            prompt = next(a for a in actions if a['params'].get('measurement_form'))
            from decimal import Decimal
            # Existing numeric parameter expressions canonicalize decimal spelling;
            # raw authored bindings remain byte-exact in both immutable revisions.
            assert Decimal(prompt['params']['measurement_form']['fields'][0]['target']) == Decimal(bindings['target'])
            assert reopened['method']['editor_state']['run_inputs']['bindings'] == bindings
        export('delivery-' + entry['id'], {'unbound': opened, 'cold': reopened, 'request': request, 'result': result})
    assert {k: actual[k] for k in entry} == entry and not transport.calls
    assert actual['bound_fixture'] == calibration_bound_fixture(entry)


@pytest.mark.asyncio
@pytest.mark.parametrize('key', [entry['id'] for entry in calibration_examples()] + ['custom-rc', 'custom-oc', 'custom-tc'])
async def test_real_delivery_result_report_and_recovery(store, key):
    root = os.environ.get('BIOXP_FULL_DELIVERY_NATIVE')
    if not root:
        pytest.skip('Requires actual separately guarded full delivery executor output')
    from test_bioxp_methods_native_snapshots import connect
    client, _, path, _ = store
    capture = json.loads((Path(root) / ('delivery-' + key + '.json')).read_text())
    native, calls, job = connect(client, json.dumps(capture['reopened']).encode())
    try:
        url = BASE + '/runs/' + job['job_id']
        read = await client.get(url)
        assert read.status_code == 200 and read.json() == capture['reopened']
        report = await client.get(url + '/report')
        assert report.status_code == 200
        assert report.json()['action_results'] == job['execution']['runtime_state']['action_results']
        recovered = await client.post(url + '/recovery-draft', json={})
        assert recovered.status_code == 200, recovered.text
        metadata = job['protocol']['document']['metadata']['bms_method']
        assert all(recovered.json()[k] == metadata[k] for k in ('method', 'bindings', 'dependencies', 'initial_state'))
        saved = await create(client, recovered.json()['method'])
        async with cold_client(path) as cold:
            reopened = await cold.get(BASE + '/library/' + saved['id'] + '/revisions/1')
            assert reopened.json()['method'] == metadata['method']
        assert calls and all(c.method == 'GET' for c in calls)
        export('delivery-received-' + key, {'job': read.json(), 'report': report.json(), 'recovery': recovered.json()})
    finally:
        await native.close()


@pytest.mark.asyncio
async def test_every_native_device_type_real_save_compile(store):
    root = os.environ.get('BIOXP_FULL_DEVICE_PRODUCER')
    if not root:
        pytest.skip('Requires guarded actual native command producer')
    producer = json.loads(Path(root).read_text())
    client, transport, path, _ = store
    from bioxp_method_native import EXPORT
    assert EXPORT['method_contract']['device_commands'] == producer['catalog']
    seen = set()
    async with cold_client(path) as cold:
        for row in producer['rows']:
            operation = {'operation': row['operation'], 'channels': [0], 'timeout_ms': 100,
                row['field']: deepcopy(row['request'])}
            application = {'implementation': 'original-adp.application.v1', 'operations': [operation]}
            raw = {'schema': 'bms.bioxp-method.v1', 'steps': [{'step_id': 'device', 'type': 'action',
                'action': 'cavro_application', 'inputs': {'application': application}}]}
            saved = await create(client, raw, name='Native device ' + row['id'])
            url = BASE + '/library/' + saved['id'] + '/revisions/1'
            exact = await client.get(url)
            opened = await cold.get(url)
            assert exact.status_code == opened.status_code == 200
            assert exact.json() == opened.json() and opened.json()['method'] == raw
            response = await cold.post(BASE + '/compile', json={'method': opened.json()['method']})
            assert response.status_code == 200 and response.json()['document'], (row['id'], response.text)
            result = response.json()
            assert result['document']['stages'][0]['actions'][0]['params']['application'] == application
            export('delivery-device-' + row['id'], {'request': {'method': raw}, 'result': result, 'cold': opened.json()})
            if row['field'] == 'command':
                seen.add(row['request']['command'])
    assert seen == set(producer['command_types'])
    assert not transport.calls
    export('device-producer', producer)


@pytest.mark.asyncio
@pytest.mark.parametrize('technique', ['single', 'multi', 'distribute', 'consolidate', 'source_mix'])
async def test_native_technique_save_cold_compile(store, technique):
    from test_bioxp_cavro_transfer import request as transfer_request
    request = transfer_request(multi=technique == 'multi')
    step = request['method']['steps'][0]
    if technique in ('distribute', 'consolidate'):
        step['action'] = technique
        step['inputs'] = {'mode': 'repeated_single', 'transfers': [deepcopy(step['inputs']), deepcopy(step['inputs'])]}
    elif technique == 'source_mix':
        from test_bioxp_deck_authoring_backend import source_mix_blank_delays
        step['action'] = 'source_mix'
        step['inputs'] = source_mix_blank_delays()
        step['inputs'].update(aspirate_delay_ms='0', dispense_delay_ms='0')
    client, transport, path, _ = store
    raw = deepcopy(request['method'])
    raw['editor_state'] = {'run_inputs': {k: deepcopy(v) for k, v in request.items() if k in ('bindings', 'dependencies', 'initial_state')}}
    saved = await create(client, raw, name='Explicit native technique ' + technique)
    url = BASE + '/library/' + saved['id'] + '/revisions/1'
    exact = await client.get(url)
    async with cold_client(path) as cold:
        opened = await cold.get(url)
        assert opened.json() == exact.json() and opened.json()['method'] == raw
        received = {'method': opened.json()['method'], **raw['editor_state']['run_inputs']}
        response = await cold.post(BASE + '/compile', json=received)
        assert response.status_code == 200 and response.json()['document'], response.text
        result = response.json()
        actions = [a for s in result['document']['stages'] for a in s['actions']]
        if technique != 'source_mix':
            assert all(a['params'].get('operation') == 'cavro_liquid_recipe' for a in actions)
            assert len(actions) == (2 if technique in ('distribute', 'consolidate') else 1)
        else:
            assert actions[0]['params']['operation'] == 'source_mix'
        export('delivery-technique-' + technique, {'request': received, 'result': result, 'cold': opened.json()})
    assert not transport.calls


@pytest.mark.asyncio
@pytest.mark.parametrize('module', ['rc', 'oc', 'tc'])
async def test_custom_thermal_points_save_cold_compile(store, module):
    client, transport, path, _ = store
    entry = next(e for e in calibration_examples() if e['id'] == module + '_calibration')
    bindings = calibration_bound_fixture(entry)['bindings']
    def author(value):
        if isinstance(value, dict):
            if set(value) == {'expr'} and value['expr']['op'] == 'param':
                return deepcopy(bindings[value['expr']['id']])
            return {k: author(v) for k, v in value.items()}
        return [author(v) for v in value] if isinstance(value, list) else value
    groups = []
    for index, target in enumerate(['31.2500', '37.0000']):
        bindings.update(target=target, commanded=target)
        bindings['setpoint_inputs']['target_temp_c'] = target
        bindings['settle_inputs']['seconds'] = '0'
        steps = author(entry['method']['steps'])
        if module == 'tc':
            steps[0]['action'] = 'thermal_hold'
            steps[0]['inputs'].update(duration_s='0', start='dispatch')
        sample = steps[-1]['inputs']['analysis']['samples'][0]
        sample['measured_c']['method_result']['step_path'] = f'/method/steps/{index}/steps/2'
        groups.append({'type': 'group', 'step_id': 'point-' + str(index), 'steps': steps})
    raw = {**deepcopy(entry['method']), 'name': 'Explicit custom ' + module, 'parameters': [], 'steps': groups}
    saved = await create(client, raw)
    url = BASE + '/library/' + saved['id'] + '/revisions/1'
    exact = await client.get(url)
    async with cold_client(path) as cold:
        opened = await cold.get(url)
        assert opened.json() == exact.json() and opened.json()['method'] == raw
        response = await cold.post(BASE + '/compile', json={'method': opened.json()['method']})
        assert response.status_code == 200 and response.json()['document'], response.text
        result = response.json()
        actions = [a for s in result['document']['stages'] for a in s['actions']]
        prompts = [a for a in actions if a['kind'] == 'pause_review']
        assert [a['params']['measurement_form']['fields'][0]['target'] for a in prompts] == ['31.2500', '37.0000']
        refs = [a['params']['analysis']['samples'][0]['measured_c'] for a in actions if a['kind'] == 'calibration']
        assert [ref['source_occurrence_id'] for ref in refs] == [a['source_occurrence_id'] for a in prompts]
        export('delivery-custom-' + module, {'request': {'method': raw}, 'cold': opened.json(), 'result': result})
    assert not transport.calls
