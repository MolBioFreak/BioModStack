"""Offline HTTP tests; the only native boundary is the inert robot transport."""
import gzip
import json
from pathlib import Path

import pytest

from test_bioxp_operator_controls import make_client
from test_bioxp_protocol_relay import JOB, bundle, control_body, control_receipt


def recovery(**changes):
    return {"action": "recover", "decision": "retry", "gate_id": "hold-1",
            "source_occurrence_id": "occ", "native_action_id": "recipe-1",
            "expected_attempt": 1, "overrides": {}, **changes}


@pytest.mark.parametrize("decision", ["retry", "repeat", "skip", "manual_complete"])
def test_control_relay(monkeypatch, decision):
    client, runtime = make_client(monkeypatch)
    runtime.connection.client.responses["protocol_control"] = control_receipt()
    body = control_body(**recovery(decision=decision))
    response = client.post(f"/api/bioxp/methods/runs/{JOB}/control", json=body)
    assert response.status_code == 200, response.text
    call, = runtime.connection.client.calls
    assert call[0] == "protocol_control"
    assert call[1]["json_data"] == {k: v for k, v in body.items() if k != "expected_connection_generation"}


def held_bundle():
    job = bundle()
    state = job["execution"]["runtime_state"]
    state["workflow"].update(phase="waiting", gate="error_hold", gate_id="hold-1", source_occurrence_id="occ")
    corpus = Path(__file__).parent / 'fixtures/bioxp_methods/final-model-documents.json.gz'
    with gzip.open(corpus) as source:
        params = next(a['params'] for doc in json.load(source) for stage in doc['stages']
                      for a in stage['actions'] if a['params'].get('recipe', {}).get('mode') == 'single')
    params['recipe']['liquid_detection'] = {'location_id': 0, 'submerge_steps': 210}
    doc = job["protocol"]["document"]
    doc["metadata"] = {"bms_method_run": {"schema": "bms.bioxp-method-run.v1", "method": {},
        "compilation": {"provenance": [{"occurrence_id": "occ", "native_action_ids": ["earlier", "recipe-1"]}]}}}
    doc["stages"] = [{"actions": [{"action_id": "recipe-1", "source_occurrence_id": "occ",
                                   "kind": "pipette_manual_physical", "params": params}]}]
    state["action_results"] = [{"action_id": "earlier", "ok": True},
                               {"action_id": "recipe-1", "attempt": 1, "hold_id": "hold-1", "source_occurrence_id": "occ", "ok": False}]
    return job


def test_preview_reads_once_and_preserves_explicit_zero(monkeypatch):
    client, runtime = make_client(monkeypatch, mutations=False)
    job = held_bundle()
    runtime.connection.client.responses["protocol_job"] = job
    response = client.post(f"/api/bioxp/methods/runs/{JOB}/recovery-preview", json=recovery(
        overrides={"aspiration_delay_ms": 50, "submerge_steps": 0}))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['previous_settings']['submerge_steps'] == 210
    assert data['proposed_settings']['submerge_steps'] == 0
    assert data['proposed_settings']['aspiration_delay_ms'] == 50
    assert data['sibling_results'] == [{'action_id': 'earlier', 'ok': True}]
    assert data['submitted'] is False
    assert [call[0] for call in runtime.connection.client.calls] == ['protocol_job']


@pytest.mark.parametrize('changes', [dict(gate_id='old'), dict(expected_attempt=2),
                                     dict(native_action_id='earlier'), dict(source_occurrence_id='other'),
                                     dict(overrides={'on_contact_delay_ms': 5001}), dict(decision='repeat')])
def test_invalid_preview_does_not_submit(monkeypatch, changes):
    client, runtime = make_client(monkeypatch, mutations=False)
    runtime.connection.client.responses['protocol_job'] = held_bundle()
    response = client.post(f'/api/bioxp/methods/runs/{JOB}/recovery-preview', json=recovery(**changes))
    assert response.status_code == 422, response.text
    assert [call[0] for call in runtime.connection.client.calls] == ['protocol_job']


@pytest.mark.parametrize('overrides', [{'pressure_threshold': 7}, {'operations': []},
                                       {'submerge_steps': True}, {'submerge_steps': None},
                                       {'aspiration_delay_ms': -1}])
def test_invalid_control_has_zero_dispatches(monkeypatch, overrides):
    client, runtime = make_client(monkeypatch)
    response = client.post(f'/api/bioxp/methods/runs/{JOB}/control', json=control_body(**recovery(overrides=overrides)))
    assert response.status_code == 422
    assert not runtime.connection.client.calls


def test_preview_uses_last_attempt_not_original_settings(monkeypatch):
    client, runtime = make_client(monkeypatch, mutations=False)
    job = held_bundle()
    results = job['execution']['runtime_state']['action_results']
    results.append({**results[-1], 'attempt': 2, 'overrides': {'submerge_steps': 0}})
    runtime.connection.client.responses['protocol_job'] = job
    response = client.post(f'/api/bioxp/methods/runs/{JOB}/recovery-preview', json=recovery(
        expected_attempt=2, overrides={'aspiration_delay_ms': 123}))
    assert response.status_code == 200, response.text
    assert response.json()['previous_settings']['submerge_steps'] == response.json()['proposed_settings']['submerge_steps'] == 0
    assert len(response.json()['attempt_results']) == 2


@pytest.mark.parametrize('compact', [False, True])
def test_readback_accepts_native_recovery_signal(monkeypatch, compact):
    from test_bioxp_protocol_observation import observation
    client, runtime = make_client(monkeypatch)
    job = held_bundle()
    job['execution']['runtime_state']['workflow'].update(native_action_id='recipe-1', attempt=1)
    job['execution']['runtime_state']['workflow']['requested_control'] = {'action': 'recover', 'gate_id': 'hold-1'}
    payload = observation(job) if compact else job
    runtime.connection.client.responses['protocol_job'] = payload
    response = client.get(f'/api/bioxp/protocols/jobs/{JOB}', params={'observation': compact})
    assert response.status_code == 200, response.text
    assert response.json() == payload


@pytest.mark.parametrize('invalid', ['terminal', 'shared_action', 'wrong_attempt_occurrence', 'missing_attempt'])
def test_unavailable_scope_is_not_previewed(monkeypatch, invalid):
    client, runtime = make_client(monkeypatch, mutations=False)
    job = held_bundle()
    results = job['execution']['runtime_state']['action_results']
    if invalid == 'terminal':
        job['command']['terminal'] = True
    elif invalid == 'shared_action':
        job['protocol']['document']['metadata']['bms_method_run']['compilation']['provenance'].append(
            {'occurrence_id': 'another-loop', 'native_action_ids': ['recipe-1']})
    elif invalid == 'wrong_attempt_occurrence':
        results[-1]['source_occurrence_id'] = 'wrong-loop'
    else:
        results.clear()
    runtime.connection.client.responses['protocol_job'] = job
    response = client.post(f'/api/bioxp/methods/runs/{JOB}/recovery-preview', json=recovery())
    assert response.status_code == 422
    assert [call[0] for call in runtime.connection.client.calls] == ['protocol_job']
