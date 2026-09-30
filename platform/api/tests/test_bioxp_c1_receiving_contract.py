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
from fastapi import HTTPException
from pydantic import ValidationError
from routers.bioxp import operator_controls as receiving
from services.bioxp.operator_models import PipetteReceipt
from bioxp_recorded_receipts import RecordedReceipts
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
def test_actual_public_history_crosses_existing_normalizer_and_relay(monkeypatch, case):
    payload = envelopes()[case]['public_history']
    client, runtime = make_client(monkeypatch, mutations=False)
    runtime.connection.client.responses['operator_action_history'] = payload
    response = client.get('/api/bioxp/operator-controls/history?limit=100')
    assert response.status_code == 200, response.text
    # Existing nullable interrupt normalizer applies to every history page.
    assert_supplied_fields_preserved(response.json(), receiving._normalize_interrupt_evidence(payload))
    assert any(row['command_id'] == envelopes()[case]['command_id'] for row in response.json()['items'])

@pytest.mark.parametrize('case', ['passive', 'explicit', 'partial', 'unknown'])
def test_unmodified_actual_liquid_status_receipt_matches_durable_readback(case):
    payload = envelopes()[case]
    assert payload['public_status']['latest_receipt'] == payload['receipt']
    assert payload['public_status']['live_query_performed'] is False
    parsed = receiving._validate(PipetteReceipt, payload['public_status']['latest_receipt'])
    assert parsed.model_dump(mode='json', by_alias=True, exclude_unset=True) == payload['receipt']

@pytest.mark.parametrize('case', CASES)
def test_native_receipt_roundtrips_existing_recorded_history_wrapper(case):
    raw = envelopes()[case]['receipt']
    assert RecordedReceipts.model_validate([raw]).model_dump(mode='json', by_alias=True, exclude_unset=True) == [raw]

@pytest.mark.parametrize('path,bad', [
    (('source_identity', 'release_identity'), None),
    (('source_identity', 'release_identity'), {}),
    (('source_identity', 'release_identity', 'verified'), 'true'),
    (('source_identity', 'release_identity', 'observation', 'pid'), True),
    (('source_identity', 'release_identity', 'invented'), 'private'),
    (('source_identity', 'source_sha256', 'pipette_receipts'), 'invalid'),
    (('source_identity', 'invented'), 'private'),
    (('deployment_identity',), None),
    (('deployment_identity',), {}),
    (('deployment_identity', 'verified'), 'false'),
    (('deployment_identity', 'observation', 'invented'), 'private'),
    (('truth', 'physical_effect_verified'), True),
    (('truth', 'completion_verified'), None),
    (('unexpected',), 'private'),
])
def test_supplied_malformed_identity_is_rejected_not_hidden_or_repaired(monkeypatch, path, bad):
    raw = copy.deepcopy(envelopes()['historical-served']['receipt'])
    node = raw
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = bad
    with pytest.raises(HTTPException) as exc:
        receiving._validate(PipetteReceipt, raw)
    assert exc.value.status_code == 502
    with pytest.raises(ValidationError):
        RecordedReceipts.model_validate([raw])
    # Preserve existing display isolation rather than manufacture a live 502.
    client, _ = dashboard_client(monkeypatch, raw)
    response = client.get('/api/bioxp/operator-controls/dashboard')
    assert response.status_code == 200
    assert response.json()['pipettes'] is None


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


@pytest.mark.parametrize('case', ['passive', 'historical-served'])
@pytest.mark.parametrize('semantic', [True, False])
def test_actual_source_identity_stays_private_in_direct_public_projection(case, semantic):
    # Controlled direct-response fixture, using the unchanged actual producer
    # source packet. This is filtering proof, not a fresh direct robot operation.
    from test_bioxp_direct_liquid_recovery import client_for, record, KEY
    result = copy.deepcopy(record('readback')['result'])
    result['receipt_truth']['semantic_query_response_verified'] = semantic
    upstream = {**result, 'source_identity': envelopes()[case]['receipt']['source_identity'],
        'semantic_query_response_verified': semantic}
    client, requests = client_for(upstream)
    response = client.post('/api/bioxp/operator-controls/pipettes/readback?expected_connection_generation=77',
        json={'include_data': False}, headers={'Idempotency-Key': KEY})
    assert response.status_code == 200, response.text
    assert response.json() == {**result, 'collection_source': None}
    assert 'source_identity' not in response.json()
    assert 'deployment_identity' not in response.json()
    assert len(requests) == 1


@pytest.mark.parametrize('change', ['operation', 'failed', 'unverified', 'entrypoint', 'source'])
def test_deployment_omission_is_only_the_existing_passive_representation(change):
    raw = copy.deepcopy(envelopes()['passive']['receipt'])
    if change == 'operation':
        raw['operation'] = 'init'
    elif change == 'failed':
        raw['result']['ok'] = False
    elif change == 'unverified':
        raw['truth']['semantic_query_response_verified'] = False
    elif change == 'entrypoint':
        raw['runtime_binding']['entrypoint_id'] = 'liquid.tip_status'
    else:
        del raw['result']['collection_source']
    with pytest.raises(ValidationError):
        PipetteReceipt.model_validate(raw)
    with pytest.raises(ValidationError):
        RecordedReceipts.model_validate([raw])


@pytest.mark.parametrize('value', [None, 'offline-host'])
def test_supplied_optional_release_metadata_is_preserved_exactly(value):
    raw = copy.deepcopy(envelopes()['historical-served']['receipt'])
    raw['source_identity']['release_identity']['source']['host_path'] = value
    raw['deployment_identity']['source']['host_path'] = value
    parsed = PipetteReceipt.model_validate(raw).model_dump(mode='json', by_alias=True)
    assert parsed['source_identity'] == raw['source_identity']
    assert parsed['deployment_identity'] == raw['deployment_identity']


def test_legacy_summary_identity_stays_exact_and_closed():
    raw = json.loads((Path(__file__).parent / 'fixtures/bioxp_legacy_pipette_history.json').read_text())['receipts']
    assert RecordedReceipts.model_validate(raw).model_dump(mode='json', by_alias=True, exclude_unset=True) == raw
    for key in ['deployed_sha_verified', 'runtime_sha_verified']:
        malformed = copy.deepcopy(raw)
        malformed[0]['deployment_identity'][key] = 'true'
        with pytest.raises(ValidationError):
            RecordedReceipts.model_validate(malformed)
