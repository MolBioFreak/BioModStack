"""Unmodified merged offline producer exports through real BMS HTTP owners.

RECEIVING_FINAL_ROOT binds freshly generated robot artifacts, never fabricated
receipt packets. Hardware exchange and infrastructure metadata fixture provenance
is retained in the producer evidence; these are not live deployment claims.
"""
import json
import os
from pathlib import Path
import pytest
from test_bioxp_operator_controls import make_client
from test_bioxp_c1_receiving_contract import assert_supplied_fields_preserved
from routers.bioxp import operator_controls as receiving

W1 = ['constructor'] + ['passive-' + name for name in ['eligible', 'eligible_true', 'failed_channel', 'missing_all', 'unknown_channel', 'semantic_unverified', 'absent_source', 'explicit']]
INSPECTION = ['before_ack', 'after_ack', 'ack_without_target', 'unequal', 'late', 'wrong_motor', 'old_generation', 'fault', 'stop', 'same_position', 'partial_failure']

def root():
    value = os.environ.get('RECEIVING_FINAL_ROOT')
    assert value, 'Generate actual merged robot exports and set RECEIVING_FINAL_ROOT'
    return Path(value)

def load(name):
    return json.loads((root() / name).read_text())

def readback(name, payload):
    target = root() / 'bms-readback'
    target.mkdir(exist_ok=True)
    (target / (name + '.json')).write_text(json.dumps(payload, indent=2, sort_keys=True))

def relay(monkeypatch, upstream, route, path):
    client, runtime = make_client(monkeypatch, mutations=False)
    runtime.connection.client.responses[route] = upstream
    response = client.get('/api/bioxp/operator-controls/' + path)
    readback('http-' + route + '-' + path.split('?')[0].replace('/', '_'), {'status_code': response.status_code, 'body': response.json()})
    assert response.status_code == 200, response.text
    actual = response.json()
    assert_supplied_fields_preserved(actual, receiving._normalize_interrupt_evidence(upstream))
    assert len(runtime.connection.client.calls) == 1
    return actual


@pytest.mark.parametrize('case', W1)
@pytest.mark.parametrize('owner', ['history', 'detail_v2', 'report_detail'])
def test_all_nine_actual_public_readers(monkeypatch, case, owner):
    upstream = load('w1/public-' + case + '.json')[owner]
    if owner == 'history':
        route, path = 'operator_action_history', 'history?limit=100'
    elif owner == 'detail_v2':
        route, path = 'operator_action_receipt_v2_detail', 'v2/receipts/' + upstream['command_id'] + '?detail=true'
    else:
        route, path = 'operator_report_pipette_detail', 'reports/pipette/' + upstream['pipette_operation_id']
    actual = relay(monkeypatch, upstream, route, path)
    readback(case + '-' + owner, actual)

@pytest.mark.parametrize('family,case', [('inspection', name) for name in INSPECTION] + [('document', name) for name in ['success', 'child_failure', 'stop']])
def test_merged_inspection_and_document_public_detail_history(monkeypatch, family, case):
    public = load(family + '-public-' + case + '.json')
    readbacks, errors = {}, []
    for cid, upstream in public.items():
        try:
            if cid == 'history':
                readbacks[cid] = relay(monkeypatch, upstream, 'operator_action_history', 'history?limit=100')
            else:
                actual = relay(monkeypatch, upstream, 'operator_action_receipt_v2_detail', 'v2/receipts/' + cid + '?detail=true')
                assert actual['physical_effect_verified'] is False
                readbacks[cid] = actual
        except AssertionError as exc:
            errors.append({'command_id': cid, 'error': str(exc)})
    readback(family + '-' + case, {'readbacks': readbacks, 'errors': errors})
    assert not errors, errors
