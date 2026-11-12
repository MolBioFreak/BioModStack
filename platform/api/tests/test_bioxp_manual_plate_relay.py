"""Inert real BMS HTTP receiving for manual catch/release; no native calls."""
import json
import os
from pathlib import Path
import pytest
from test_bioxp_operator_controls import make_client, v2_receipt


def envelope(inputs):
    return dict(schema_version='bioxp.operator_action_request.v2',
                expected_connection_generation=77, expected_ownership_generation=7,
                expected_board_epoch_by_board={'4': 64, '5': 1},
                idempotency_key='manual-plate-test-001', inputs=inputs)


@pytest.mark.parametrize('action,inputs', [
    ('catch_plate', {'plate': 2}),
    ('catch_plate', {'plate': 19, 'run_in_parallel': False}),
    ('catch_plate', {'plate': 0, 'run_in_parallel': True}),
    ('release_plate', {'destination': 25, 'press_plate': False}),
    ('release_plate', {'destination': 23}),
    ('release_plate', {'destination': 21, 'press_plate': True, 'run_in_parallel': True}),
])
def test_manual_plate_exact_http_relay_and_receipt(monkeypatch, action, inputs):
    client, runtime = make_client(monkeypatch)
    action_id = f'oem.deck.{action}'
    receipt = v2_receipt(action_id=action_id)
    receipt.update(status='queued', terminal=False, canonical_inputs=inputs)
    runtime.connection.client.responses['invoke_operator_action_v2'] = receipt
    body = envelope(inputs)
    response = client.post(f'/api/bioxp/operator-controls/v2/actions/{action_id}', json=body)
    assert response.status_code == 202, response.text
    assert response.json() == receipt
    route, call = runtime.connection.client.calls[-1]
    assert route == 'invoke_operator_action_v2'
    assert call['path_params'] == {'action_id': action_id}
    assert call['json_data'] == {k: v for k, v in body.items() if k != 'expected_connection_generation'}
    receipt.update(status='completed', terminal=True, completion_class='completed')
    runtime.connection.client.responses['operator_action_receipt_v2'] = receipt
    result = client.get(f'/api/bioxp/operator-controls/v2/receipts/{receipt["command_id"]}')
    assert result.status_code == 200, result.text
    assert result.json() == receipt
    assert [c[0] for c in runtime.connection.client.calls] == ['invoke_operator_action_v2', 'operator_action_receipt_v2']


@pytest.mark.parametrize('action,inputs', [
    ('catch_plate', {}), ('catch_plate', {'plate': True}),
    ('catch_plate', {'plate': '2'}), ('catch_plate', {'plate': 2.5}),
    ('catch_plate', {'plate': None}), ('catch_plate', {'plate': 2, 'target': 'LOC_OC'}),
    ('catch_plate', {'plate': 2, 'run_in_parallel': 1}),
    ('release_plate', {}), ('release_plate', {'destination': False}),
    ('release_plate', {'destination': '25'}), ('release_plate', {'destination': 25.5}),
    ('release_plate', {'destination': 25, 'press_plate': None}),
    ('release_plate', {'destination': 25, 'press_plate': 'false'}),
    ('release_plate', {'destination': 25, 'run_in_parallel': 0}),
    ('release_plate', {'destination': 25, 'camera_offset': False}),
])
def test_manual_plate_rejects_wrong_fields_before_proxy(monkeypatch, action, inputs):
    client, runtime = make_client(monkeypatch)
    result = client.post(f'/api/bioxp/operator-controls/v2/actions/oem.deck.{action}', json=envelope(inputs))
    assert result.status_code == 422, result.text
    assert runtime.connection.client.calls == []


def test_manual_plate_replays_actual_mounted_axios_wires(monkeypatch):
    path = os.environ.get('BIOXP_MANUAL_PLATE_UI_EXPORT')
    if not path:
        pytest.skip('Set BIOXP_MANUAL_PLATE_UI_EXPORT to the actual mounted Axios export')
    rows = json.loads(Path(path).read_text())['requests']
    rows = [row for row in rows if row['url'].endswith(('/oem.deck.catch_plate', '/oem.deck.release_plate'))]
    assert rows
    for row in rows:
        client, runtime = make_client(monkeypatch)
        # Adapt only the test connection lease; preserve the actual serialized native envelope.
        runtime.connection.value.generation = row['body']['expected_connection_generation']
        result = client.post(row['url'], json=row['body'])
        assert result.status_code == 202, result.text
        assert runtime.connection.client.calls[-1][1]['json_data'] == {
            k: v for k, v in row['body'].items() if k != 'expected_connection_generation'}
