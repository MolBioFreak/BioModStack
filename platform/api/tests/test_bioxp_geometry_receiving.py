"""Actual guarded geometry producer -> scratch HTTP receiving; no physical IO."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest
from test_bioxp_methods_api import store, BASE, create
from test_bioxp_methods_native_snapshots import connect
from test_bioxp_cavro_finish import export


def producer(name):
    directory = os.environ.get('BIOXP_GEOMETRY_PRODUCER')
    if not directory:
        pytest.skip('Requires separately guarded actual native geometry exports')
    return json.loads((Path(directory) / (name + '.json')).read_text())


@pytest.mark.asyncio
@pytest.mark.parametrize('name,action,field', [
    ('geometry-accounting-recipe', 'liquid_recipe', 'recipe'),
    ('geometry-accounting-application', 'cavro_application', 'application'),
    ('rack-True', 'cavro_application', 'application'),
])
async def test_actual_geometry_save_exact_get_compile(store, name, action, field):
    client, transport, _, _ = store
    payload = producer(name)
    if name == 'rack-True':
        payload = payload['request']
    method = {'schema': 'bms.bioxp-method.v1', 'steps': [
        {'step_id': 'geometry', 'type': 'action', 'action': action, 'inputs': {field: payload}}],
        'editor_state': {'raw_extension': {'blank': '', 'null': None, 'precision': '1.00000000000000000001'}}}
    raw = deepcopy(method)
    saved = await create(client, raw, name='Explicit native geometry ' + name)
    cold = (await client.get(BASE + '/library/' + saved['id'] + '/revisions/1')).json()
    assert cold['method'] == method
    response = await client.post(BASE + '/compile', json={'method': cold['method']})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['document'], result['issues']
    params = result['document']['stages'][0]['actions'][0]['params']
    assert params[field] == payload
    assert 'native_context' not in result['simulation']
    assert result['resolved']['occurrences'][0]['inputs'][field] == payload
    assert method == raw and not transport.calls
    export('geometry-compile-' + name, {'request': {'method': method}, 'result': result, 'cold': cold})


@pytest.mark.asyncio
@pytest.mark.parametrize('presence', ['omitted', 'null', 'empty', 'unknown'])
async def test_accounting_presence_raw_save(store, presence):
    client, transport, _, _ = store
    recipe = producer('geometry-accounting-recipe')
    if presence == 'omitted':
        del recipe['accounting_context']
    elif presence == 'null':
        recipe['accounting_context'] = None
    elif presence == 'empty':
        recipe['accounting_context']['initial_channels'][0]['pneumatic'] = []
    else:
        recipe['accounting_context']['initial_channels'][0]['pneumatic'] = None
    method = {'schema': 'bms.bioxp-method.v1', 'steps': [{'step_id': 'raw', 'type': 'action', 'action': 'liquid_recipe', 'inputs': {'recipe': recipe}}]}
    saved = await create(client, method, name='Raw accounting ' + presence)
    cold = (await client.get(BASE + '/library/' + saved['id'] + '/revisions/1')).json()
    assert cold['method'] == method
    response = await client.post(BASE + '/compile', json={'method': cold['method']})
    assert response.status_code == 200 and response.json()['document'], response.text
    assert not transport.calls
    export('geometry-presence-' + presence, {'request': {'method': method}, 'result': response.json()})


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['finish-True-True', 'finish-True-False', 'finish-False-True', 'finish-False-None',
    'finish-partial-http-device', 'finish-partial-http-missing', 'finish-partial-http-interrupt'])
async def test_genuine_native_job_and_report_http(store, name):
    client, _, _, _ = store
    capture = producer(name)
    native, calls, job = connect(client, json.dumps(capture['result']).encode())
    try:
        route = BASE + '/runs/' + job['job_id']
        read = await client.get(route)
        assert read.status_code == 200 and read.json() == job
        report = await client.get(route + '/report')
        assert report.status_code == 200, report.text
        assert report.json()['action_results'] == job['execution']['runtime_state']['action_results']
        summary = report.json()['action_results'][0]['pipette_result']['accounting_summary']
        assert summary['actual_delivery_ul'] is None
        assert summary['completed_prefix_only'] is True
        assert all(call.method == 'GET' for call in calls)
        export('geometry-received-' + name, {'job': read.json(), 'report': report.json()})
    finally:
        await native.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('name', [
    'review-lifetime-command', 'review-lifetime-program', 'review-lifetime-query',
    'review-lifetime-seed-manual', 'review-lifetime-absent', 'review-lifetime-empty_unknown',
    'review-projection-True', 'review-projection-False',
    'review-geometry-observation-unavailable', 'review-geometry-primary-failure',
])
async def test_refreshed_native_lifetime_and_observation_receiving(store, name):
    directory = os.environ.get('BIOXP_GEOMETRY_PRODUCER')
    if not directory or not (Path(directory) / (name + '.json')).is_file():
        pytest.skip('Requires separately guarded native lifetime-repair export')
    client, _, _, _ = store
    capture = producer(name)
    original = deepcopy(capture['job'])
    native, calls, job = connect(client, json.dumps(original).encode())
    try:
        route = BASE + '/runs/' + job['job_id']
        read = await client.get(route)
        assert read.status_code == 200 and read.json() == original
        report = await client.get(route + '/report')
        assert report.status_code == 200, report.text
        actions = report.json()['action_results']
        assert actions == original['execution']['runtime_state']['action_results']
        for action in actions:
            summary = action.get('pipette_result', {}).get('accounting_summary')
            if summary is not None:
                assert summary['actual_delivery_ul'] is None
                assert summary['completed_prefix_only'] is True
        assert capture['job'] == original
        assert calls and all(call.method == 'GET' for call in calls)
        export('geometry-received-' + name, {'job': read.json(), 'report': report.json()})
    finally:
        await native.close()
