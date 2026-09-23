"""Offline robot doubles: the cockpit's finite MP/MC/inspection protocol wire."""
import pytest

from test_bioxp_operator_controls import make_client
from test_bioxp_protocol_relay import BASE, JOB, KEY, bundle


@pytest.mark.parametrize('kind,params', [
    ('move_cover', {'cover_id': 'CV_OUTPUT', 'target_location': 'LOC_OCS'}),
    ('plate_move', {'plate_id': 'PL_POOL', 'target_location': 'LOC_TC'}),
    ('inspect', {}),
])
@pytest.mark.parametrize('status', ['dispatched', 'completed', 'failed'])
def test_compound_intent_relay_and_custody_readback(monkeypatch, kind, params, status):
    client, runtime = make_client(monkeypatch)
    document = {'protocol_id': 'bms-deck-compound', 'version': 1, 'stages': [{
        'stage_id': 'deck', 'actions': [{'stage_id': 'deck',
            'action_id': 'inspect-covers' if kind == 'inspect' else 'transfer', 'kind': kind, 'params': params}]}]}
    # Explicit offline operator fixture. Production UI only forwards a selected
    # operator-verified contract, never inventing these reference/console facts.
    contract = {'operator_id': 'offline-test-operator', 'physical_console_verified': True,
                'live_execution_ack': True, 'deck_manifest': {'fixture': 'offline'},
                'preflight': {'reference_snapshot': {'rows': {}}, 'artifact_refs': ['offline-fixture']}}
    payload = bundle(status)
    payload['protocol']['document'] = document
    payload['command']['terminal'] = status != 'dispatched'
    state = payload['execution']['runtime_state']
    state['workflow']['phase'] = 'executing' if status == 'dispatched' else 'terminal'
    state['action_results'] = [{'action_id': 'transfer', 'result': {
        'ok': status == 'completed', 'custody': {'source': 'robot-owned', 'destination': params.get('target_location')},
        'error': 'pickup_failed' if status == 'failed' else None, 'physical_effect_verified': False}}]
    runtime.connection.client.responses['protocol_execute'] = payload
    runtime.connection.client.responses['protocol_job'] = payload
    response = client.post(BASE + '/submit', json={'expected_connection_generation': 77,
        'source_type': 'native', 'document': document, 'live_execution': contract,
        'dry_run': False, 'idempotency_key': KEY})
    assert response.status_code == (202 if status == 'dispatched' else 200), response.text
    route, kwargs = runtime.connection.client.calls[0]
    assert route == 'protocol_execute'
    assert kwargs['json_data']['document'] == document
    assert kwargs['json_data']['live_execution'] == contract
    assert 'source_location' not in params
    assert runtime.connection.active_request_calls[0]['require_fresh'] is True
    readback = client.get(BASE + f'/jobs/{JOB}', params={'expected_connection_generation': 77})
    assert readback.status_code == 200, readback.text
    assert readback.json() == payload
    assert readback.json()['execution']['runtime_state']['action_results'] == state['action_results']
