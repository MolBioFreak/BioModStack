"""Offline BMS boundaries; raw pending export is byte-preserved, other cases synthetic."""
import json
from pathlib import Path

import pytest
from test_bioxp_direct_liquid_recovery import client_for, URL

RAW = Path(__file__).with_name('fixtures').joinpath('f33_pending_get.json').read_bytes()


def get(payload):
    data = json.loads(payload) if isinstance(payload, bytes) else payload
    client, requests = client_for(payload)
    response = client.get(URL, params={'request_kind': data['request_kind'],
        'expected_connection_generation': 77}, headers={'Idempotency-Key': data['idempotency_key']})
    assert len(requests) == 1
    assert requests[0].method == 'GET'
    assert requests[0].content == b''
    return response


def test_lookup_actual_pending_null_outcome_raw_bytes():
    response = get(RAW)
    assert response.status_code == 200, response.text
    assert response.json() == json.loads(RAW)
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('reason', ['outcome_unresolved', 'receipt_incomplete'])
def test_lookup_incomplete_preserves_null_outcome(reason):
    payload = json.loads(RAW)
    payload.update(lookup_state='incomplete', reason=reason)
    payload['record'].update(command_status='completed', pipette_status='completed')
    assert get(payload).status_code == 200
    assert get(payload).json()['record']['outcome'] is None


def test_lookup_failed_plan_completed_outcome_is_evidence_not_physical_success():
    raw = Path(__file__).with_name('fixtures').joinpath('f33_plan_waste_get.json').read_bytes()
    response = get(raw)
    assert response.status_code == 200, response.text
    r = response.json()['record']
    assert r['command_status'] == r['pipette_status'] == 'failed'
    assert r['outcome'] == 'completed'
    assert r['result']['ok'] is True
    for flag in ['physical_effect_verified', 'execution_admitted', 'motion_commanded',
                 'liquid_mutation_commanded', 'completion_verified', 'controller_acknowledged']:
        assert r['result'][flag] is False
    # Optional model defaults may be materialized; no success/status rewriting.
    assert r['command_id'] == json.loads(raw)['record']['command_id']
