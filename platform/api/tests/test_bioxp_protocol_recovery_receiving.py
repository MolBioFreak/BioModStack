"""Read-only HTTP receiving of a captured native completed cover workflow.

Transport is inert; settled variants are projections, not new robot jobs.
"""
import copy
import json
from pathlib import Path

import pytest
from test_bioxp_operator_controls import make_client
from test_bioxp_protocol_observation import observation


CAPTURE = Path(__file__).parent / "fixtures/bioxp_methods/protocol-recovery-native-cover.json"


@pytest.mark.parametrize("compact", [False, True], ids=["full", "observation"])
@pytest.mark.parametrize("resolution", [None, {
    "recovery_command_id": "offline-home-evidence",
    "native_detail": {"channels": [0, None, False, {"steps": -15000}],
                      "note": "retained native JSON", "ratio": 0.125},
}], ids=["null", "nested"])
def test_captured_recovery_resolution_is_lossless(monkeypatch, compact, resolution):
    payload = json.loads(CAPTURE.read_text())
    payload["command"]["recovery_resolution"] = copy.deepcopy(resolution)
    if compact:
        payload = observation(payload)
    client, runtime = make_client(monkeypatch)
    runtime.connection.client.responses["protocol_job"] = payload
    suffix = "?observation=true" if compact else ""
    response = client.get(f"/api/bioxp/protocols/jobs/{payload['job_id']}{suffix}")
    assert response.status_code == 200, response.text
    assert response.json() == payload
    assert len(runtime.connection.client.calls) == 1
    assert runtime.connection.client.calls[0][0] == "protocol_job"
    assert all(call["route_name"] == "protocol_job" for call in runtime.connection.active_request_calls)


@pytest.mark.parametrize("compact", [False, True], ids=["full", "observation"])
@pytest.mark.parametrize("recorded", ["ambiguous", "interrupted"])
def test_settled_status_retains_recorded_status_and_identity(monkeypatch, compact, recorded):
    payload = json.loads(CAPTURE.read_text())
    payload["status"] = payload["command"]["status"] = "cleared"
    payload["command"]["recovery_resolution"] = {
        "recovery_command_id": "offline-home-evidence", "original_status": recorded,
        "evidence": {"axes": ["x", "y", "z"], "untranslated": [None, 0, False]},
    }
    if compact:
        payload = observation(payload)
    payload["recorded_status"] = recorded
    client, runtime = make_client(monkeypatch)
    runtime.connection.client.responses["protocol_job"] = payload
    suffix = "?observation=true" if compact else ""
    response = client.get(f"/api/bioxp/protocols/jobs/{payload['job_id']}{suffix}")
    assert response.status_code == 200, response.text
    assert response.json() == payload
    assert len(runtime.connection.client.calls) == 1
    assert runtime.connection.client.calls[0][0] == "protocol_job"
    assert all(call["route_name"] == "protocol_job" for call in runtime.connection.active_request_calls)


@pytest.mark.parametrize("compact", [False, True], ids=["full", "observation"])
def test_absent_recovery_fields_stay_absent(monkeypatch, compact):
    payload = json.loads(CAPTURE.read_text())
    payload["command"].pop("recovery_resolution")
    if compact:
        payload = observation(payload)
    client, runtime = make_client(monkeypatch)
    runtime.connection.client.responses["protocol_job"] = payload
    suffix = "?observation=true" if compact else ""
    response = client.get(f"/api/bioxp/protocols/jobs/{payload['job_id']}{suffix}")
    assert response.status_code == 200, response.text
    assert response.json() == payload


@pytest.mark.parametrize("compact", [False, True], ids=["full", "observation"])
@pytest.mark.parametrize("invalid", ["envelope_extra", "command_extra", "recorded_status", "resolution", "identity"])
def test_recovery_receiving_remains_closed(monkeypatch, compact, invalid):
    payload = json.loads(CAPTURE.read_text())
    if compact:
        payload = observation(payload)
    if invalid == "envelope_extra":
        payload["unowned_field"] = True
    elif invalid == "command_extra":
        payload["command"]["unowned_field"] = True
    elif invalid == "recorded_status":
        payload["recorded_status"] = "invented"
    elif invalid == "resolution":
        payload["command"]["recovery_resolution"] = ["not an object"]
    else:
        payload["command"]["command_id"] = "other-command"
    client, runtime = make_client(monkeypatch)
    runtime.connection.client.responses["protocol_job"] = payload
    suffix = "?observation=true" if compact else ""
    response = client.get(f"/api/bioxp/protocols/jobs/{payload['job_id']}{suffix}")
    assert response.status_code == 502
    assert len(runtime.connection.client.calls) == 1
    assert runtime.connection.client.calls[0][0] == "protocol_job"
