"""C1: unmodified native receipts cross actual BMS public receiving owners.

The checked-in replay bundle contains e113 offline service/SQLite/public exports
with explicitly fixture-controlled infrastructure identities, plus one unchanged
historical f344265 receipt captured on GET /liquid/status. No hardware is used.
Set BMS_C1_ENVELOPES to a merged producer bundle for integration requalification.
"""
import copy
import json
import os
from pathlib import Path

import pytest
from test_bioxp_operator_controls import make_client, catalog

FIXTURE = Path(os.environ.get('BMS_C1_ENVELOPES', Path(__file__).parent / 'fixtures/bioxp_c1_receiving_envelopes.json'))
CASES = ['constructor-initial', 'constructor-completion_failed', 'passive', 'explicit', 'partial', 'unknown', 'historical-served']

def envelopes():
    return json.loads(FIXTURE.read_text())['envelopes']


def assert_supplied_fields_preserved(actual, raw):
    if isinstance(raw, dict):
        for key, value in raw.items():
            assert key in actual, key
            assert_supplied_fields_preserved(actual[key], value)
    elif isinstance(raw, list):
        assert len(actual) == len(raw)
        for left, right in zip(actual, raw):
            assert_supplied_fields_preserved(left, right)
    else:
        assert actual == raw


def dashboard_client(monkeypatch, receipt):
    client, runtime = make_client(monkeypatch, mutations=False)
    dashboard = copy.deepcopy(catalog()['dashboard'])
    dashboard['pipettes']['latest_receipt'] = receipt
    runtime.connection.client.responses['operator_dashboard'] = dashboard
    return client, runtime

@pytest.mark.parametrize('case', CASES)
def test_native_receipt_survives_public_dashboard_without_quarantine(monkeypatch, case):
    raw = envelopes()[case]['receipt']
    client, runtime = dashboard_client(monkeypatch, raw)
    response = client.get('/api/bioxp/operator-controls/dashboard')
    assert response.status_code == 200, response.text
    group = response.json()['pipettes']
    assert group is not None  # A 200 with isolated/null section is not compatibility.
    actual = group['latest_receipt']
    for key, value in raw.items():
        assert actual[key] == value, key
    assert ('deployment_identity' in actual) == ('deployment_identity' in raw)
    assert actual['truth']['physical_effect_verified'] is False
    assert actual['source_identity'] == raw['source_identity']
    assert runtime.connection.client.calls == [('operator_dashboard', {})]

@pytest.mark.parametrize('case', CASES[:-1])
def test_actual_reopened_report_detail_crosses_public_relay(monkeypatch, case):
    payload = envelopes()[case]['report_detail']
    client, runtime = make_client(monkeypatch, mutations=False)
    runtime.connection.client.responses['operator_report_pipette_detail'] = payload
    response = client.get('/api/bioxp/operator-controls/reports/pipette/' + payload['pipette_operation_id'])
    assert response.status_code == 200, response.text
    assert_supplied_fields_preserved(response.json(), payload)
    assert response.json()['physical_effect_verified'] is False
    assert len(runtime.connection.client.calls) == 1  # No invented legacy context.

@pytest.mark.parametrize('case', ['passive', 'explicit', 'partial', 'unknown'])
def test_actual_public_history_relays_unchanged(monkeypatch, case):
    payload = envelopes()[case]['public_history']
    client, runtime = make_client(monkeypatch, mutations=False)
    runtime.connection.client.responses['operator_action_history'] = payload
    response = client.get('/api/bioxp/operator-controls/history?limit=100')
    assert response.status_code == 200, response.text
    assert response.json() == payload
    assert any(row['command_id'] == envelopes()[case]['command_id'] for row in response.json()['items'])


def test_compact_absence_and_actual_false_unknown_are_not_fabricated(monkeypatch):
    passive = envelopes()['passive']['receipt']
    assert 'deployment_identity' not in passive
    assert passive['source_identity']['release_identity']['release_id'] == 'c1-offline-release-fixture'
    client, _ = dashboard_client(monkeypatch, passive)
    actual = client.get('/api/bioxp/operator-controls/dashboard').json()['pipettes']['latest_receipt']
    assert 'deployment_identity' not in actual
    assert actual['result'] == passive['result']
    for case in ['partial', 'unknown', 'constructor-completion_failed']:
        raw = envelopes()[case]['receipt']
        assert raw['truth']['completion_verified'] is False
        client, _ = dashboard_client(monkeypatch, raw)
        result = client.get('/api/bioxp/operator-controls/dashboard').json()['pipettes']['latest_receipt']
        assert result['result'] == raw['result']  # Includes genuine null/unknown facts.
        assert result['truth'] == raw['truth']
