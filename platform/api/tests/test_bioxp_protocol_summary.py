"""Explicit compact negotiation; full selected payload remains authoritative."""
from test_bioxp_operator_controls import make_client
from test_bioxp_protocol_relay import bundle, JOB
from services.bioxp.protocol_models import ProtocolJobSummary


def test_summary_list_then_selected_full(monkeypatch):
    client, runtime = make_client(monkeypatch)
    full = bundle("completed")
    summary = dict(job_id=JOB, status=full["status"], dry_run=False, protocol_id="prepared",
        source_type="native", created_at=full["created_at"], updated_at=full["updated_at"],
        pending_review=None, command=full["command"])
    runtime.connection.client.responses["protocol_jobs"] = {"rows": [summary]}
    runtime.connection.client.responses["protocol_job"] = full
    response = client.get("/api/bioxp/protocols/jobs?limit=13")
    assert response.status_code == 200, response.text
    assert response.json() == {"rows": [summary]}
    ProtocolJobSummary.model_validate(response.json()["rows"][0])
    assert runtime.connection.client.calls[-1][1]["params"] == {"limit": 13, "summary": True}
    selected = client.get(f"/api/bioxp/protocols/jobs/{JOB}")
    assert selected.status_code == 200 and selected.json() == full
    assert "params" not in runtime.connection.client.calls[-1][1]


def test_old_robot_full_list_still_accepted(monkeypatch):
    client, runtime = make_client(monkeypatch)
    full = bundle("completed")
    runtime.connection.client.responses["protocol_jobs"] = {"rows": [full]}
    response = client.get("/api/bioxp/protocols/jobs")
    assert response.status_code == 200 and response.json() == {"rows": [full]}


import pytest

@pytest.mark.parametrize("status", ["outcome_unknown", "reconciliation_required", "issued_pending", "stop_requested", "abort_requested", "stopped", "aborted", "cancelled"])
def test_native_statuses_survive_both_discovery_facades_and_observation(monkeypatch, status):
    from test_bioxp_protocol_observation import observation
    client, runtime = make_client(monkeypatch)
    full = bundle("completed")
    full["status"] = full["command"]["status"] = status
    summary = dict(job_id=JOB, status=status, dry_run=False, protocol_id="prepared",
        source_type="native", created_at=full["created_at"], updated_at=full["updated_at"],
        pending_review=None, command=full["command"])
    runtime.connection.client.responses["protocol_jobs"] = {"rows": [summary]}
    runtime.connection.client.responses["protocol_job"] = observation(full)
    for path in ("protocols/jobs", "methods/runs"):
        response = client.get(f"/api/bioxp/{path}")
        assert response.status_code == 200, response.text
        assert response.json()["rows"] == [summary]
        response = client.get(f"/api/bioxp/{path}/{JOB}?observation=true")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == status


def test_immutable_run_context_uses_original_mapping_without_result_payload(monkeypatch):
    client, runtime = make_client(monkeypatch)
    full = bundle("completed")
    method = {"schema": "bms.bioxp-method.v1", "name": "API authored", "steps": []}
    provenance = [{"occurrence_id": "repeat/2/wash", "step_id": "wash", "native_action_ids": ["a1"]}]
    full["protocol"]["document"]["metadata"] = {"bms_method_run": {
        "schema": "bms.bioxp-method-run.v1", "method": method,
        "compilation": {"provenance": provenance, "simulation": {"large": "omitted"}}}}
    runtime.connection.client.responses["protocol_job"] = full
    response = client.get(f"/api/bioxp/methods/runs/{JOB}/context")
    assert response.status_code == 200, response.text
    assert response.json() == {"job_id": JOB, "method": method, "provenance": provenance}
    full["protocol"]["document"]["metadata"] = {}
    response = client.get(f"/api/bioxp/methods/runs/{JOB}/context")
    assert response.json() == {"job_id": JOB, "method": None, "provenance": []}
