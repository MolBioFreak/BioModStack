"""Replay the actual robot SQLite benchmark outputs at the receiving HTTP owner."""
import json
import os
from pathlib import Path
import pytest
from test_bioxp_operator_controls import make_client
from services.bioxp.protocol_models import ProtocolJobSummary


def test_retained_producer_summary_http_and_every_selected_full(monkeypatch):
    root = os.environ.get("BIOXP_SUMMARY_REPLAY_DIR")
    if not root:
        pytest.skip("set BIOXP_SUMMARY_REPLAY_DIR to robot benchmark output")
    root = Path(root)
    summaries = json.loads((root / "summary.json").read_text())
    full = json.loads((root / "full.json").read_text())["rows"]
    client, runtime = make_client(monkeypatch)
    runtime.connection.client.responses["protocol_jobs"] = summaries
    response = client.get("/api/bioxp/protocols/jobs")
    assert response.status_code == 200, response.text
    assert response.json() == summaries
    assert len(summaries["rows"]) == len(full)
    for row, detail in zip(summaries["rows"], full):
        ProtocolJobSummary.model_validate(row)
        assert row["job_id"] == detail["job_id"] and row["command"] == detail["command"]
        runtime.connection.client.responses["protocol_job"] = detail
        response = client.get("/api/bioxp/protocols/jobs/" + row["job_id"])
        assert response.status_code == 200, response.text
        assert response.json() == detail
        # Cloning's source is the selected full document, never the summary.
        assert response.json()["protocol"]["document"] == detail["protocol"]["document"]
