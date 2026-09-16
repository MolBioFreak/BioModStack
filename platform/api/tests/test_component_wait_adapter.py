"""Malformed wait projections cannot authorize native result retrieval."""
import pytest

from component_runtime import canonical_bytes
from test_frustrampnn_parent_workflow_fanout import _load_client


@pytest.mark.parametrize("defect", ["duplicate_ids", "duplicate_rows", "foreign_row", "missing_row", "false_success"])
def test_actual_fanout_wait_rejects_ambiguous_terminal_members(tmp_path, monkeypatch, defect):
    client = _load_client()
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "metadata.json").write_bytes(canonical_bytes(dict(
        candidate_id="candidate", parent_job_id="parent", parent_workflow_id="protein_design",
        producer_stage="terminal", producer_candidate_key="terminal/a.pdb", requiredness="required")))
    (candidate / "source.pdb").write_bytes(b"ATOM\nEND\n")
    class Response:
        def __init__(self, payload):
            self.payload = payload
        def raise_for_status(self):
            pass
        def json(self):
            return self.payload
    fanout = dict(schema_name="bms.structure-dataset-fanout.v1", parent_job_id="parent",
                  selected_structure_count=1, child_jobs=[dict(job_id="child", structure_count=1,
                                                              candidates=[dict(candidate_id="candidate")])])
    status = dict(child_ids=["child"], children=[dict(job_id="child", status="completed")],
                  all_done=True, completed=1, failed=0, cancelled=0)
    if defect == "duplicate_ids":
        status["child_ids"] *= 2
    elif defect == "duplicate_rows":
        status["children"] *= 2
    elif defect == "foreign_row":
        status["children"][0]["job_id"] = "foreign"
    elif defect == "missing_row":
        status["children"] = []
    else:
        status["children"][0]["status"] = "running"
    calls = []
    monkeypatch.setattr(client.requests, "post", lambda *a, **k: Response(fanout))
    def get(url, **kwargs):
        calls.append(url)
        assert url.endswith("/children/status"), "invalid wait must not retrieve a receipt"
        return Response(status)
    monkeypatch.setattr(client.requests, "get", get)
    with pytest.raises(RuntimeError, match="lineage|failed"):
        client.execute_parent_fanout(parent_job_id="parent", parent_workflow_id="protein_design",
            settings_json=canonical_bytes(dict(batching_enabled=False, structures_per_job=1)).decode(),
            candidate_dirs=[candidate], output_receipt=tmp_path / "terminal.json",
            output_bundles=tmp_path / "bundles", capability="offline-test", poll_interval=0)
    assert len(calls) == 1
    assert not (tmp_path / "terminal.json").exists()
