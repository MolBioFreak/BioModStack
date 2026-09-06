"""Offline BMS boundaries; raw pending export is byte-preserved, other cases synthetic."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from services.bioxp.operator_models import (
    PipetteReadbackPostEnvelope, PipetteReadbackResponse,
    PipetteApplicationPlanPostEnvelope, PipetteDirectPostMetadata,
)
from test_bioxp_direct_liquid_recovery import client_for, record, envelope, URL

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


@pytest.mark.parametrize('outcome', [0, 1, True, '', 'x' * 121, 'MISSING'])
def test_lookup_pending_outcome_remains_required_strict_bounded(outcome):
    payload = json.loads(RAW)
    if outcome == 'MISSING':
        del payload['record']['outcome']
    else:
        payload['record']['outcome'] = outcome
    assert get(payload).status_code == 502


@pytest.mark.parametrize('change', [
    {'command_status': 'completed'}, {'pipette_status': 'failed'},
    {'pipette_operation_id': None}, {'requested_inputs': {}},
    {'result': record('readback')['result']},
])
def test_lookup_pending_null_preserves_state_result_guards(change):
    payload = json.loads(RAW)
    payload['record'].update(change)
    assert get(payload).status_code == 502


@pytest.mark.parametrize('reason', ['outcome_unresolved', 'receipt_incomplete'])
def test_lookup_incomplete_preserves_null_outcome(reason):
    payload = json.loads(RAW)
    payload.update(lookup_state='incomplete', reason=reason)
    payload['record'].update(command_status='completed', pipette_status='completed')
    assert get(payload).status_code == 200
    assert get(payload).json()['record']['outcome'] is None


def test_lookup_resolved_cannot_invent_null_outcome_success():
    payload = {**envelope(), 'lookup_state': 'resolved', 'reason': None,
               'record': {**record('readback'), 'outcome': None}}
    assert get(payload).status_code == 502


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


@pytest.mark.parametrize('value', [True, False])
def test_readback_post_semantic_envelope_required_strict_equal(value):
    # Focused model contract, deliberately omits optional source_identity.
    # This synthetic rich BMS result is NOT a modified raw robot export.
    result = record('readback')['result']
    result['receipt_truth']['semantic_query_response_verified'] = value
    payload = {**result, 'semantic_query_response_verified': value}
    validated = PipetteReadbackPostEnvelope.model_validate(payload)
    assert validated.semantic_query_response_verified is value
    assert validated.receipt_truth.semantic_query_response_verified is value
    for invalid in [None, 0, 1, 'true', 'false', not value]:
        with pytest.raises(ValidationError):
            PipetteReadbackPostEnvelope.model_validate({**payload, 'semantic_query_response_verified': invalid})
    with pytest.raises(ValidationError):
        PipetteReadbackPostEnvelope.model_validate(result)
    with pytest.raises(ValidationError):
        PipetteReadbackResponse.model_validate(payload)
    with pytest.raises(ValidationError):
        PipetteDirectPostMetadata.model_validate({'semantic_query_response_verified': value})
    with pytest.raises(ValidationError):
        PipetteApplicationPlanPostEnvelope.model_validate({**record('application_plan')['result'], 'semantic_query_response_verified': value})
