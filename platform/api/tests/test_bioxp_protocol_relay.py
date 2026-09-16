"""Finite workflow relay and passive custody readback; explicit robot doubles."""
import asyncio
import copy

import pytest
from pydantic import ValidationError

from test_bioxp_operator_controls import make_client
from services.bioxp.errors import ConnectionStateError, RobotResponseError, RobotTimeoutError
from services.bioxp.protocol_models import ProtocolSubmission
from services.bioxp.robot_client import DEFAULT_ROBOT_ROUTES

BASE = '/api/bioxp/protocols'
JOB = 'protocol-live-fixture'
KEY = 'workflow-original-key'


def bundle(status='dispatched'):
    return {
        'schema_version': 'bioxp.protocol_operator_bundle.v1', 'job_id': JOB,
        'created_at': '2026-09-15T12:00:00+00:00', 'updated_at': '2026-09-15T12:00:01+00:00',
        'status': status,
        'protocol': {'source_type': 'native', 'source_path': None, 'coverage': {},
                     'experiment': {}, 'inventory': {}, 'document': {'protocol_id': 'prepared', 'stages': []}},
        'execution': {'dry_run': False, 'runtime_state': {
            'protocol_id': 'prepared', 'dry_run': False, 'job_id': JOB,
            'current_stage_id': None, 'paused': False, 'awaiting_review': False,
            'completed': status == 'completed', 'pause_reason': None,
            'stage_states': {}, 'events': [], 'action_results': [],
            'workflow': {'command_id': JOB, 'phase': 'terminal' if status == 'completed' else 'executing',
                         'gate': None, 'gate_id': None, 'source_occurrence_id': 'oem:0',
                         'requested_control': None, 'last_control_id': None, 'reached_control_id': None,
                         'held_reason': None, 'child_command_ids': ['child-1']}}},
        'operator': {'manual_review_required': False, 'pending_review': None, 'reviews': []},
        'artifacts': {},
        'command': {'command_id': JOB, 'idempotency_key': KEY, 'ownership_generation': 7,
                    'state_version': 4, 'status': status, 'terminal': status == 'completed',
                    'status_path': f'/protocol/jobs/{JOB}'},
    }


def submit_body():
    return {'expected_connection_generation': 77, 'source_type': 'native',
            'document': {'protocol_id': 'prepared', 'operations': [
                {'oem_opcode': 'step', 'arguments': ['001'], 'source_key': 1}]},
            'dry_run': False, 'idempotency_key': KEY,
            'live_execution': {'operator_id': 'operator', 'live_execution_ack': True}}


def control_body(**fields):
    return {'expected_connection_generation': 77, 'expected_ownership_generation': 7,
            'command_id': JOB, 'idempotency_key': 'control-original-key', **fields}


def control_receipt():
    return {'control_command_id': 'control-1', 'idempotency_key': 'control-original-key',
            'command_id': JOB, 'job_id': JOB, 'ownership_generation': 7, 'state_version': 5,
            'accepted': True, 'reached': False, 'phase': 'executing', 'gate': None,
            'gate_id': None, 'status_path': f'/protocol/jobs/{JOB}'}


@pytest.mark.parametrize('state,http_status', [('dispatched', 202), ('completed', 200)])
def test_submit_relays_original_input_no_local_job(monkeypatch, state, http_status):
    client, runtime = make_client(monkeypatch)
    payload = bundle(state)
    runtime.connection.client.responses['protocol_execute'] = payload
    body = submit_body()
    response = client.post(BASE + '/submit', json=body)
    assert response.status_code == http_status, response.text
    assert response.json() == payload
    route, kwargs = runtime.connection.client.calls[0]
    assert route == 'protocol_execute'
    assert kwargs['json_data'] == {k: v for k, v in body.items() if k != 'expected_connection_generation'}
    assert runtime.connection.active_request_calls[0]['require_fresh'] is True
    assert not hasattr(runtime, 'jobs')  # no local create/transition authority


@pytest.mark.parametrize('variant', [
    {'action': 'pause', 'mode': 'ordinary'}, {'action': 'pause', 'mode': 'deferred'},
    {'action': 'wake', 'gate_id': 'pause-1'},
    {'action': 'continue', 'gate': 'deferred_pause', 'gate_id': 'pause-1'},
    {'action': 'continue', 'gate': 'delaypoint', 'gate_id': 'delay-1'},
    {'action': 'safe_stop'}, {'action': 'abort'},
])
def test_controls_finite_normal_lane_not_interrupts(monkeypatch, variant):
    client, runtime = make_client(monkeypatch)
    payload = control_receipt()
    runtime.connection.client.responses['protocol_control'] = payload
    response = client.post(BASE + f'/jobs/{JOB}/control', json=control_body(**variant))
    assert response.status_code == 200, response.text
    assert response.json() == payload
    assert response.json()['reached'] is False
    assert len(runtime.connection.client.calls) == 1
    assert not runtime.connection.safety_interrupt_calls


@pytest.mark.parametrize('variant', [
    {'action': 'abort', 'force': False}, {'action': 'pause'},
    {'action': 'continue', 'gate': 'review', 'gate_id': 'review-1'},
    {'action': 'wake'}, {'action': 'native_method', 'method': 'home'},
    {'action': 'safe_stop', 'state_version': 4},
])
def test_invalid_controls_never_reach_robot(monkeypatch, variant):
    client, runtime = make_client(monkeypatch)
    response = client.post(BASE + f'/jobs/{JOB}/control', json=control_body(**variant))
    assert response.status_code == 422
    assert not runtime.connection.client.calls


def test_control_target_and_generation_fenced(monkeypatch):
    client, runtime = make_client(monkeypatch)
    for changes, status in [({'command_id': 'other'}, 422), ({'expected_connection_generation': 76}, 409)]:
        response = client.post(BASE + f'/jobs/{JOB}/control', json={**control_body(action='abort'), **changes})
        assert response.status_code == status
    assert not runtime.connection.client.calls


@pytest.mark.parametrize('change', ['phase', 'extra', 'identity', 'boolean'])
def test_bad_robot_contract_is_not_success(monkeypatch, change):
    client, runtime = make_client(monkeypatch)
    payload = bundle()
    workflow = payload['execution']['runtime_state']['workflow']
    if change == 'phase': workflow['phase'] = 'invented'
    if change == 'extra': workflow['physical_idle'] = True
    if change == 'identity': workflow['command_id'] = 'other'
    if change == 'boolean': payload['command']['terminal'] = 1
    runtime.connection.client.responses['protocol_job'] = payload
    response = client.get(BASE + f'/jobs/{JOB}')
    assert response.status_code == 502
    assert len(runtime.connection.client.calls) == 1


def test_populated_gate_and_native_failure_are_preserved(monkeypatch):
    client, runtime = make_client(monkeypatch)
    payload = bundle()
    state = payload['execution']['runtime_state']
    state['workflow'].update(phase='waiting', gate='error_hold', gate_id='oem:1', held_reason='source_error_hold')
    state['action_results'] = [{'action_id': 'oem:1', 'ok': False, 'native_result': {'channels': [1, None, 3]}}]
    runtime.connection.client.responses['protocol_job'] = payload
    response = client.get(BASE + f'/jobs/{JOB}')
    assert response.status_code == 200, response.text
    assert response.json() == payload


def test_review_is_separate_bound_request(monkeypatch):
    client, runtime = make_client(monkeypatch)
    runtime.connection.client.responses['protocol_review'] = bundle()
    body = control_body(stage_id='stage-1', action_id='review-1', reviewer='operator', note='checked')
    response = client.post(BASE + f'/jobs/{JOB}/review', json=body)
    assert response.status_code == 200, response.text
    assert runtime.connection.client.calls[0][0] == 'protocol_review'


def test_timeout_has_one_attempt_and_preserves_uncertainty(monkeypatch):
    client, runtime = make_client(monkeypatch)
    calls = []
    async def timeout(route_name, **kwargs):
        calls.append((route_name, kwargs))
        raise RobotTimeoutError('lost response', dispatched=True)
    monkeypatch.setattr(runtime.connection.client, 'request', timeout)
    response = client.post(BASE + '/submit', json=submit_body())
    assert response.status_code == 504
    assert response.json()['detail']['dispatch_state'] == 'outcome_ambiguous'
    assert len(calls) == 1
    assert calls[0][1]['json_data']['idempotency_key'] == KEY


def test_robot_refusal_not_local_blocked_success(monkeypatch):
    client, runtime = make_client(monkeypatch)
    async def refusal(*args, **kwargs):
        raise RobotResponseError(409, {'detail': {'error': 'workflow_busy'}})
    monkeypatch.setattr(runtime.connection.client, 'request', refusal)
    response = client.post(BASE + '/submit', json=submit_body())
    assert response.status_code == 409
    assert response.json()['detail'] == {'error': 'workflow_busy'}


def test_mutation_guard_stays_closed(monkeypatch):
    client, runtime = make_client(monkeypatch, mutations=False)
    response = client.post(BASE + '/submit', json=submit_body())
    assert response.status_code == 503
    assert not runtime.connection.client.calls


@pytest.mark.parametrize('bad', [True, '77', -1])
def test_strict_generations(bad):
    with pytest.raises(ValidationError):
        ProtocolSubmission.model_validate({**submit_body(), 'expected_connection_generation': bad})


def test_live_workflow_reads_use_actual_passive_connection(tmp_path):
    from test_bioxp_connection import _load, _service
    _, Profile, _, _ = _load()
    clients = []
    service = _service(tmp_path, clients)
    async def scenario():
        await service.save_profile(Profile(api_url='http://robot:8123'))
        generation = (await service.connect()).generation
        service._last_reachable = False
        service._observed_at = None
        try:
            for route in ('protocol_jobs', 'protocol_job'):
                result = await service.request_active_v2_query(route, expected_generation=generation)
                assert result['route_name'] == route
                with pytest.raises(ConnectionStateError):
                    await service.request_active_v2_query(route, expected_generation=generation + 1)
            with pytest.raises(ConnectionStateError):
                await service.request_active('protocol_control', expected_generation=generation, json_data={})
        finally:
            await service.close()
    asyncio.run(scenario())
    assert DEFAULT_ROBOT_ROUTES['protocol_job'] == ('GET', '/protocol/jobs/{job_id}', 10.0)


def test_history_is_not_reclassified(monkeypatch, tmp_path):
    from services.bioxp.job_store import BioXpJobStore
    client, runtime = make_client(monkeypatch)
    runtime.jobs = BioXpJobStore(tmp_path / 'history.sqlite3')
    job = runtime.jobs.create_validated_job(protocol={'name': 'old', 'steps': []}, compiled_hash='a' * 64, idempotency_key='old-local-key')
    job = runtime.jobs.transition(job.job_id, 'submission_blocked', detail='No delivery was attempted')
    before = copy.deepcopy(job.model_dump(mode='json'))
    response = client.get('/api/bioxp/jobs/' + job.job_id)
    assert response.status_code == 200, response.text
    assert response.json()['job'] == before
    retained = runtime.jobs.get(job.job_id)
    assert retained is not None
    assert retained.model_dump(mode='json') == before
    assert not runtime.connection.client.calls


def test_list_keeps_canonical_bundles_and_historical_summaries_distinct(monkeypatch):
    client, runtime = make_client(monkeypatch)
    live = bundle()
    historical = {'job_id': 'old-dry-run', 'status': 'completed', 'dry_run': True,
                  'protocol_id': 'old', 'source_type': 'native', 'created_at': live['created_at'],
                  'updated_at': live['updated_at'], 'pending_review': None}
    payload = {'rows': [live, historical]}
    runtime.connection.client.responses['protocol_jobs'] = payload
    response = client.get(BASE + '/jobs', params={'expected_connection_generation': 77, 'limit': 20})
    assert response.status_code == 200, response.text
    assert response.json() == payload
    assert runtime.connection.client.calls[0][1]['params'] == {'limit': 20}


def test_dry_run_is_nonphysical_robot_result_not_local_live_authority(monkeypatch):
    client, runtime = make_client(monkeypatch)
    payload = bundle('completed')
    payload.pop('command')
    payload['execution']['dry_run'] = True
    state = payload['execution']['runtime_state']
    state['dry_run'] = True
    state.pop('workflow')
    runtime.connection.client.responses['protocol_execute'] = payload
    response = client.post(BASE + '/submit', json={**submit_body(), 'dry_run': True})
    assert response.status_code == 200, response.text
    assert response.json() == payload


def test_legacy_local_submission_is_not_reinterpreted_as_live(monkeypatch):
    client, runtime = make_client(monkeypatch)
    response = client.post(BASE + '/submit', json={'protocol': {'schema_version': 1, 'name': 'old',
        'steps': [{'action': 'initialize_motors'}]}, 'idempotency_key': KEY})
    assert response.status_code == 422
    assert not runtime.connection.client.calls


@pytest.mark.parametrize('value', [float('inf'), float('-inf'), float('nan')])
def test_nonfinite_nested_scientific_input_rejected(value):
    payload = submit_body()
    payload['document']['operations'][0]['arguments'] = [value]
    with pytest.raises(ValidationError):
        ProtocolSubmission.model_validate(payload)


def test_status_mismatch_never_reclassified_locally(monkeypatch):
    client, runtime = make_client(monkeypatch)
    payload = bundle()
    payload['status'] = 'completed'
    runtime.connection.client.responses['protocol_job'] = payload
    assert client.get(BASE + f'/jobs/{JOB}').status_code == 502


@pytest.mark.parametrize('route,path', [
    ('protocol_execute', '/protocol/execute'),
    ('protocol_control', f'/protocol/jobs/{JOB}/control'),
    ('protocol_review', f'/protocol/jobs/{JOB}/review'),
])
def test_real_client_finite_post_routes_never_retry(route, path):
    import httpx
    from ipaddress import ip_address
    from services.bioxp.robot_client import BioXpRobotClient
    from services.bioxp.target_policy import ValidatedBioXpTarget
    calls = []
    async def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout('lost accepted response', request=request)
    target = ValidatedBioXpTarget(api_url='http://robot:8123', scheme='http', hostname='robot',
                                 port=8123, resolved_addresses=(ip_address('100.64.0.10'),))
    client = BioXpRobotClient(target, transport=httpx.MockTransport(handler))
    async def scenario():
        try:
            with pytest.raises(RobotTimeoutError):
                await client.request(route, path_params={'job_id': JOB} if route != 'protocol_execute' else None,
                                     json_data={'idempotency_key': KEY}, retry_read_once=True)
        finally:
            await client.close()
    asyncio.run(scenario())
    assert len(calls) == 1
    assert calls[0].method == 'POST'
    assert calls[0].url.path == path
    assert calls[0].url.host == '100.64.0.10'
