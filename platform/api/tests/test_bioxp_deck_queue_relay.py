"""Canonical admission readback through mounted BMS routes; no robot IO."""
import copy
import json
from pathlib import Path
import pytest
from test_bioxp_operator_controls import make_client, v2_receipt
from services.bioxp.errors import RobotResponseError
from services.bioxp.robot_client import DEFAULT_ROBOT_ROUTES

KEY = 'deck-request-retained-001'
PATH = f'/api/bioxp/operator-controls/v2/requests/{KEY}'


def identity(command_id='accepted-1'):
    return {'schema_version': 'bioxp.operator_idempotency_receipt.v1',
            'operation_kind': 'command', 'idempotency_key': KEY,
            'robot_identity': 'serial206', 'command_id': command_id, 'method_id': None,
            'fingerprint': 'f' * 64, 'response': {'command_id': command_id, 'status': 'queued'}}


def test_lookup_reads_current_receipt_not_saved_admission(monkeypatch):
    client, runtime = make_client(monkeypatch)
    upstream = runtime.connection.client.responses
    upstream['operator_command_identity'] = identity()
    row = v2_receipt(action_id='oem.deck.move_to_location', command_id='accepted-1')
    row.update(status='completed', terminal=True, completion_class='completed', finished_at=2.0,
               terminal_receipt_id='accepted-1', expected_board_epoch_by_board={'4': 2, '5': 3})
    upstream['operator_action_receipt_v2'] = row
    response = client.get(PATH, params={'expected_connection_generation': 77})
    assert response.status_code == 200, response.text
    assert {key: response.json()[key] for key in row} == row
    assert [call[0] for call in runtime.connection.client.calls] == ['operator_command_identity', 'operator_action_receipt_v2']
    assert DEFAULT_ROBOT_ROUTES['operator_command_identity'] == ('GET', '/operator/idempotency/command/{key}', 5.0)


@pytest.mark.parametrize('change', ['key', 'command', 'kind'])
def test_identity_mismatch_is_not_acceptance(monkeypatch, change):
    client, runtime = make_client(monkeypatch)
    row = identity()
    if change == 'key': row['idempotency_key'] = 'other-key'
    if change == 'kind': row['operation_kind'] = 'interrupt'
    if change == 'command': row['command_id'] = None
    runtime.connection.client.responses['operator_command_identity'] = row
    response = client.get(PATH, params={'expected_connection_generation': 77})
    assert response.status_code == 502
    assert len(runtime.connection.client.calls) == 1


def test_old_generation_never_queries_new_robot(monkeypatch):
    client, runtime = make_client(monkeypatch)
    response = client.get(PATH, params={'expected_connection_generation': 76})
    assert response.status_code == 409
    assert not runtime.connection.client.calls


def test_generation_change_between_identity_and_receipt_is_fenced(monkeypatch):
    client, runtime = make_client(monkeypatch)
    runtime.connection.client.responses['operator_command_identity'] = identity()
    original = runtime.connection.client.request
    async def request(route_name, **kwargs):
        result = await original(route_name, **kwargs)
        if route_name == 'operator_command_identity': runtime.connection.value.generation = 78
        return result
    monkeypatch.setattr(runtime.connection.client, 'request', request)
    response = client.get(PATH, params={'expected_connection_generation': 77})
    assert response.status_code == 409
    assert len(runtime.connection.client.calls) == 1


@pytest.mark.parametrize('projection', ['missing', 'empty', 'populated'])
def test_queue_model_and_mounted_relay_preserve_unknown_and_canonical_items(monkeypatch, projection):
    client, runtime = make_client(monkeypatch)
    dashboard = runtime.connection.client.responses['operator_dashboard_v2']
    dashboard.pop('command_queue', None)
    expected = None
    if projection != 'missing':
        expected = {'schema_version': 'bioxp.oem_command_queue.v1', 'generated_at': 1.0, 'items': []}
        if projection == 'populated':
            expected['items'] = [dict(command_id=f'canonical-{i}', sequence=i+1,
                status='queued' if i else 'dispatched', method_id=None, resource_keys=['axis:x'], accepted_at=1.0)
                for i in range(8)]
        dashboard['command_queue'] = copy.deepcopy(expected)
    response = client.get('/api/bioxp/operator-controls/v2/dashboard')
    assert response.status_code == 200, response.text
    assert response.json()['command_queue'] == expected


def test_real_canonical_producer_catalog_round_trips_bms_models(monkeypatch):
    client, runtime = make_client(monkeypatch)
    source = Path(__file__).parents[2] / 'frontend/tests/fixtures/bioxp_deck_canonical_queue.json'
    catalog = json.loads(source.read_text())
    runtime.connection.client.responses['operator_control_catalog_v2'] = catalog
    response = client.get('/api/bioxp/operator-controls/v2/catalog')
    assert response.status_code == 200, response.text
    assert response.json()['dashboard']['command_queue'] == catalog['dashboard']['command_queue']


def test_request_lookup_uses_real_passive_connection_lane_through_status_failure(tmp_path):
    import asyncio
    from test_bioxp_connection import _load, _service
    from services.bioxp.errors import ConnectionStateError
    _, Profile, _, _ = _load()
    clients = []
    service = _service(tmp_path, clients)
    async def scenario():
        await service.save_profile(Profile(api_url='http://robot:8123'))
        generation = (await service.connect()).generation
        service._last_reachable = False
        service._observed_at = None
        try:
            assert service.snapshot().observation_fresh is not True
            result = await service.request_active_v2_query('operator_command_identity', expected_generation=generation, path_params={'key': KEY})
            assert result['route_name'] == 'operator_command_identity'
            with pytest.raises(ConnectionStateError):
                await service.request_active_v2_enqueue('invoke_operator_action_v2', expected_generation=generation, json_data={})
            with pytest.raises(ConnectionStateError):
                await service.request_active_v2_query('operator_command_identity', expected_generation=generation + 1, path_params={'key': KEY})
        finally:
            await service.close()
    asyncio.run(scenario())


def test_missing_identity_404_is_propagated_without_post(monkeypatch):
    client, runtime = make_client(monkeypatch)
    async def request(route_name, **kwargs):
        assert route_name == 'operator_command_identity'
        raise RobotResponseError(404, {'error': 'idempotency_not_found'})
    monkeypatch.setattr(runtime.connection.client, 'request', request)
    response = client.get(PATH, params={'expected_connection_generation': 77})
    assert response.status_code == 404
