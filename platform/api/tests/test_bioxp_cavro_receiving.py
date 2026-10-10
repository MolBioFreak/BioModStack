"""Typed relay receiving from actual native exported bundles, never hardware."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest
from test_bioxp_operator_controls import make_client
from test_bioxp_protocol_relay import control_body
from test_bioxp_cavro_finish import export
from services.bioxp.errors import RobotResponseError
from test_bioxp_methods_api import store, BASE, create


def captured(name):
    root = os.environ.get('BIOXP_CAVRO_NATIVE_RECEIVING')
    if not root:
        pytest.skip('Separate guarded native producer export required')
    return json.loads((Path(root) / name).read_text())


def test_native_pending_completed_and_review_relay(monkeypatch):
    capture = captured('thermal-rc-rc.json')
    client, runtime = make_client(monkeypatch)
    for label, payload in [('held', capture['held'][0]), ('completed', capture['completed']), ('reopened', capture['reopened'])]:
        job = payload['job_id']
        runtime.connection.client.responses['protocol_job'] = payload
        response = client.get('/api/bioxp/protocols/jobs/' + job)
        assert response.status_code == 200, response.text
        assert response.json() == payload
        export('received-native-' + label, response.json())
    held = capture['held'][0]
    pending = held['operator']['pending_review']
    body = {**deepcopy(capture['review_requests'][0]), 'expected_connection_generation': 77}
    assert all(body[key] == pending[key] for key in ('stage_id', 'action_id', 'source_occurrence_id'))
    runtime.connection.client.responses['protocol_review'] = capture['completed']
    response = client.post('/api/bioxp/protocols/jobs/' + held['job_id'] + '/review', json=body)
    assert response.status_code == 200, response.text
    assert runtime.connection.client.calls[-1][1]['json_data'] == {k: v for k, v in body.items() if k != 'expected_connection_generation'}
    export('review-relay', {'request': body, 'response': response.json()})


@pytest.mark.parametrize('name', ['pressure-public-receiving.json', 'review-pressure-readback-None.json', 'review-pressure-readback-chunks.json'])
def test_native_pressure_reader_is_read_only(monkeypatch, name):
    capture = captured(name)['capture']
    client, runtime = make_client(monkeypatch)
    runtime.connection.client.responses['pipette_pressure_capture'] = capture
    response = client.get('/api/bioxp/pipettes/pressure-captures/' + capture['stream']['stream_session_id'])
    assert response.status_code == 200, response.text
    assert response.json() == capture
    assert [route for route, _ in runtime.connection.client.calls] == ['pipette_pressure_capture']
    export('pressure-readback-' + Path(name).stem, response.json())


def test_native_analysis_pure_relay(monkeypatch):
    data = captured('analysis-producers.json')
    client, runtime = make_client(monkeypatch)
    for case in data:
        runtime.connection.client.responses['protocol_calibration_analyze'] = case['result']
        response = client.post('/api/bioxp/protocols/calibration/analyze?expected_connection_generation=77', json=case['request'])
        assert response.status_code == 200, response.text
        assert response.json() == case['result']
        assert runtime.connection.client.calls[-1] == ('protocol_calibration_analyze', {'json_data': case['request']})
    assert all(call['require_fresh'] is False for call in runtime.connection.active_request_calls)
    export('analysis-relay', data)


def test_offline_schema_and_non_numeric_measurement_rejection(monkeypatch):
    client, runtime = make_client(monkeypatch)
    response = client.get('/api/bioxp/protocols/calibration/schema')
    assert response.status_code == 200, response.text
    from bioxp_method_calibration import CALIBRATION
    assert response.json() == CALIBRATION
    assert not runtime.connection.client.calls
    body = control_body(stage_id='stage', measurements={'probe': [True]})
    response = client.post('/api/bioxp/protocols/jobs/' + body['command_id'] + '/review', json=body)
    assert response.status_code == 422 and not runtime.connection.client.calls


def test_review_explicit_native_rejection_preserved(monkeypatch):
    client, runtime = make_client(monkeypatch)
    detail = {'accepted': False, 'job_id': 'protocol-live-fixture', 'error': 'review host response was not persisted'}
    async def reject(route, **kwargs):
        runtime.connection.client.calls.append((route, kwargs))
        raise RobotResponseError(409, detail)
    monkeypatch.setattr(runtime.connection.client, 'request', reject)
    body = control_body(stage_id='stage', source_occurrence_id='occurrence', measurements={'probe': ['1.000']})
    response = client.post('/api/bioxp/protocols/jobs/' + body['command_id'] + '/review', json=body)
    assert response.status_code == 409, response.text
    assert response.json()['detail'] == detail
    assert len(runtime.connection.client.calls) == 1  # no retry or physical resubmit


@pytest.mark.asyncio
@pytest.mark.parametrize('index', [0, 1])
async def test_actual_analysis_correction_into_class_compiler(store, index):
    from test_bioxp_cavro_finish import correction_request
    from bioxp_method_compiler import compile_method
    data = captured('analysis-producers.json')[index]
    request = correction_request()
    cls = request['dependencies']['liquid_classes'][0]
    pin = {'calibration_id': 'actual-native-analysis-fixture', 'revision': 'r1',
        'context': deepcopy(data['request']['context']), 'function': deepcopy(data['result']['correction'])}
    cls['settings']['selected_correction'] = deepcopy(pin)
    cls['authored_settings']['selected_correction'] = deepcopy(pin)
    cls['measurement_provenance'] = deepcopy(data)
    result = compile_method(request)
    assert result['document'], result['issues']
    recipe = result['document']['stages'][0]['actions'][0]['params']['recipe']
    assert recipe['selected_correction'] == pin
    assert 'commanded_aspiration_ul' not in recipe
    assert result['dependencies'] == request['dependencies']
    client, transport, _, _ = store
    saved_class = await client.post(BASE + '/liquid-classes', json={'method': cls, 'name': 'Actual native fit ' + str(index)})
    assert saved_class.status_code == 201, saved_class.text
    cold_class = await client.get(BASE + '/liquid-classes/' + saved_class.json()['id'] + '/revisions/1')
    assert cold_class.json()['method'] == cls
    raw = deepcopy(request['method'])
    run_inputs = {k: deepcopy(request[k]) for k in ('bindings', 'dependencies')}
    raw.setdefault('editor_state', {})['run_inputs'] = deepcopy(run_inputs)
    saved = await create(client, raw, name='Explicit selected native fit ' + str(index))
    url = BASE + '/library/' + saved['id']
    cold = (await client.get(url + '/revisions/1')).json()
    assert cold['method'] == raw
    compiled = await client.post(BASE + '/compile', json={'method': cold['method'], **run_inputs})
    assert compiled.status_code == 200 and compiled.json()['document'], compiled.text
    from test_bioxp_methods_api import KEY, JOB
    run = await client.post(url + '/runs', json={'revision': 1, 'idempotency_key': KEY,
        'expected_generation': 77, 'acknowledge_live': True, **run_inputs})
    assert run.status_code == 202, run.text
    recovery = await client.post(BASE + '/runs/' + JOB + '/recovery-draft', json={})
    assert recovery.status_code == 200 and recovery.json()['dependencies'] == request['dependencies']
    export('actual-analysis-correction-store-' + str(index), {'class': cold_class.json(), 'cold': cold,
        'compiled': compiled.json(), 'run': run.json(), 'recovery': recovery.json()})
    export('actual-analysis-correction-' + str(index), {'request': request, 'result': result})


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['loop', 'procedure', 'groups'])
async def test_actual_bms_native_bms_cycle_and_scratch_recovery(store, name):
    from test_bioxp_methods_native_snapshots import connect
    client, _, _, _ = store
    capture = captured('bms-connected-' + name + '.json')
    native, calls, job = connect(client, json.dumps(capture['reopened']).encode())
    try:
        url = BASE + '/runs/' + job['job_id']
        read = await client.get(url)
        assert read.status_code == 200 and read.json() == job, read.text
        report = await client.get(url + '/report')
        assert report.status_code == 200, report.text
        assert report.json()['action_results'] == job['execution']['runtime_state']['action_results']
        response = await client.post(url + '/recovery-draft', json={})
        assert response.status_code == 200, response.text
        recovered = response.json()
        metadata = job['protocol']['document']['metadata']['bms_method']
        assert all(recovered[key] == metadata[key] for key in ('method', 'bindings', 'dependencies', 'initial_state'))
        raw = deepcopy(recovered['method'])
        raw.setdefault('editor_state', {})['run_inputs'] = {k: deepcopy(recovered[k]) for k in ('bindings', 'dependencies', 'initial_state')}
        saved = await create(client, raw, name='Recovered measurement ' + name)
        cold = await client.get(BASE + '/library/' + saved['id'] + '/revisions/1')
        assert cold.status_code == 200 and cold.json()['method'] == raw
        export('connected-bms-recovery-' + name, {'native_job': job, 'report': report.json(), 'recovery': recovered, 'cold': cold.json()})
        assert all(call.method == 'GET' for call in calls)
    finally:
        await native.close()
