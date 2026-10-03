"""Real BMS routes with native SQLite observation exports at inert transport."""
import copy
import json
import os
from pathlib import Path

import pytest
from test_bioxp_operator_controls import make_client
from test_bioxp_protocol_relay import bundle, JOB


def observation(full):
    return {"schema_version": "bioxp.protocol_job_observation.v1", "job_id": full["job_id"],
        "status": full["status"], "command": {k: v for k, v in full["command"].items() if k not in {"idempotency_key", "status_path"}},
        "execution": {"dry_run": full["execution"]["dry_run"],
            "runtime_state": {"workflow": full["execution"]["runtime_state"]["workflow"]}},
        "operator": {"pending_review": full["operator"]["pending_review"]}}


def test_projection_negotiation_full_compatibility_and_omission(monkeypatch):
    client, runtime = make_client(monkeypatch)
    full = bundle()
    compact = observation(full)
    compact["operator"] = {}
    runtime.connection.client.responses["protocol_job"] = compact
    response = client.get(f"/api/bioxp/protocols/jobs/{JOB}?observation=true")
    assert response.status_code == 200, response.text
    assert response.json() == compact
    assert runtime.connection.client.calls[-1][1]["params"] == {"observation": True}
    runtime.connection.client.responses["protocol_job"] = full
    for suffix in ("", "?observation=true"):
        response = client.get(f"/api/bioxp/protocols/jobs/{JOB}{suffix}")
        assert response.status_code == 200 and response.json() == full


@pytest.mark.parametrize("field", ["job_id", "command", "workflow", "boolean"])
def test_malformed_observation_is_visible_read_error(monkeypatch, field):
    client, runtime = make_client(monkeypatch)
    compact = observation(bundle())
    if field == "job_id": compact["job_id"] = "other"
    elif field == "command": compact["command"]["command_id"] = "other"
    elif field == "workflow": compact["execution"]["runtime_state"]["workflow"]["command_id"] = "other"
    else: compact["execution"]["dry_run"] = 0
    runtime.connection.client.responses["protocol_job"] = compact
    assert client.get(f"/api/bioxp/protocols/jobs/{JOB}?observation=true").status_code == 502


def test_actual_native_exports_through_http(monkeypatch):
    source = os.environ.get("BIOXP_OBSERVATION_EVIDENCE")
    if not source:
        pytest.skip("requires actual native SQLite capture replay export")
    evidence = json.loads(Path(source).read_text())
    client, runtime = make_client(monkeypatch)
    receiving = copy.deepcopy(evidence)
    for name, window in evidence["windows"].items():
        body_bytes, rows = [], []
        for native in window["rows"]:
            runtime.connection.client.responses["protocol_job"] = native
            response = client.get(f"/api/bioxp/protocols/jobs/{native['job_id']}?observation=true")
            assert response.status_code == 200, response.text
            assert response.json() == native
            rows.append(response.json())
            body_bytes.append(len(response.content))
        receiving["windows"][name].update(rows=rows, relay_body_bytes=sum(body_bytes))
        assert sum(body_bytes) / window["full_bytes"] <= .05
    output = os.environ.get("BIOXP_OBSERVATION_RECEIVING")
    if output:
        Path(output).write_text(json.dumps(receiving, indent=2))
