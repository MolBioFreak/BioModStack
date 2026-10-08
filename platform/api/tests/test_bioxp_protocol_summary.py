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
