from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SHARED_MODULE = REPO_ROOT / "modules" / "frustrampnn_parent_fanout.nf"
CLIENT_SCRIPT = REPO_ROOT / "scripts" / "run_frustrampnn_parent_fanout.py"
PARENTS = {
    "structure_prediction": REPO_ROOT / "workflows" / "structure_prediction.nf",
    "complex_prediction": REPO_ROOT / "workflows" / "complex_prediction.nf",
    "protein_design": REPO_ROOT / "workflows" / "protein_design.nf",
    "antibody_denovo": REPO_ROOT / "workflows" / "antibody_denovo.nf",
    "conformational_mapping": REPO_ROOT / "workflows" / "conformational_mapping.nf",
}


def _load_client():
    assert CLIENT_SCRIPT.is_file(), "the shared scheduler fan-out client is missing"
    spec = importlib.util.spec_from_file_location("frustrampnn_parent_fanout_test", CLIENT_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_all_five_parent_workflows_reach_one_scheduler_fanout_owner_without_inline_execution() -> None:
    assert SHARED_MODULE.is_file(), "the shared structure-workflow fan-out owner is missing"
    shared = SHARED_MODULE.read_text(encoding="utf-8")
    assert shared.count("workflow SchedulerFrustraMPNNParentFanout") == 1
    assert "run_frustrampnn_parent_fanout.py" in shared
    assert "CanonicalFrustraMPNNV2(" not in shared

    for workflow_id, path in PARENTS.items():
        source = path.read_text(encoding="utf-8")
        assert "SchedulerFrustraMPNNParentFanout" in source, workflow_id
        assert "CanonicalFrustraMPNNV2(" not in source, workflow_id


def test_shared_client_spawns_exact_grouped_children_waits_and_seals_terminal_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_client()
    candidates = []
    for ordinal in range(3):
        candidate_dir = tmp_path / f"candidate-{ordinal}"
        candidate_dir.mkdir()
        source = candidate_dir / "source.pdb"
        source.write_text(
            f"ATOM      1  CA  GLY {chr(65 + ordinal)}   1       1.000   2.000   3.000  1.00 20.00           C  \nEND\n",
            encoding="ascii",
        )
        metadata = {
            "candidate_id": f"candidate-{ordinal}",
            "parent_job_id": "parent-1",
            "parent_workflow_id": "protein_design",
            "producer_stage": "protein_design:terminal",
            "producer_candidate_key": f"terminal/candidate-{ordinal}.pdb",
            "requiredness": "required",
        }
        (candidate_dir / "metadata.json").write_text(
            json.dumps(metadata, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )
        candidates.append(candidate_dir)

    settings = {
        "schema_name": "frustrampnn_settings",
        "schema_version": 2,
        "batching_enabled": True,
        "structures_per_job": 2,
    }
    child_ids = ["child-a", "child-b"]
    calls: list[tuple[str, str]] = []

    class Response:
        def __init__(self, payload: dict[str, object], status_code: int = 200):
            self._payload = payload
            self.status_code = status_code
            self.ok = status_code < 400
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

        def raise_for_status(self):
            if not self.ok:
                raise RuntimeError(self.text)

    def fake_post(url, **kwargs):
        calls.append(("POST", url))
        assert url.endswith("/api/frustrampnn/jobs/parent-1/workflow-dataset/analyze")
        assert kwargs["data"]["parent_workflow_id"] == "protein_design"
        manifest = json.loads(kwargs["data"]["dataset_manifest"])
        assert [item["candidate_id"] for item in manifest["candidates"]] == [
            "candidate-0", "candidate-1", "candidate-2"
        ]
        assert len(kwargs["files"]) == 3
        return Response(
            {
                "schema_name": "bms.structure-dataset-fanout.v1",
                "fanout_id": "a" * 64,
                "parent_job_id": "parent-1",
                "selected_structure_count": 3,
                "structures_per_job": 2,
                "effective_structures_per_job": 2,
                "replayed": False,
                "child_jobs": [
                    {"job_id": child_ids[0], "structure_count": 2},
                    {"job_id": child_ids[1], "structure_count": 1},
                ],
            }
        )

    output_roots = {"child-a": tmp_path / "child-a", "child-b": tmp_path / "child-b"}
    for child_id, candidate_ids in {"child-a": ["candidate-0", "candidate-1"], "child-b": ["candidate-2"]}.items():
        for candidate_id in candidate_ids:
            bundle = output_roots[child_id] / "frustrampnn" / "results" / candidate_id
            bundle.mkdir(parents=True)
            (bundle / "workflow_component_result_v3.json").write_text("{}\n", encoding="utf-8")

    def fake_get(url, **kwargs):
        calls.append(("GET", url))
        if url.endswith("/api/jobs/parent-1/children/status"):
            return Response(
                {
                    "total": 2,
                    "completed": 2,
                    "failed": 0,
                    "cancelled": 0,
                    "running": 0,
                    "pending": 0,
                    "all_done": True,
                    "child_ids": child_ids,
                    "children": [
                        {"job_id": child_id, "status": "completed", "output_dir": str(output_roots[child_id])}
                        for child_id in child_ids
                    ],
                }
            )
        child_id = url.rsplit("/", 2)[-2]
        candidates_for_child = ["candidate-0", "candidate-1"] if child_id == "child-a" else ["candidate-2"]
        return Response(
            {
                "job_id": child_id,
                "status": "completed",
                "parent_job_id": "parent-1",
                "candidates": [{"candidate_id": item} for item in candidates_for_child],
                "results": [{"candidate_id": item, "status": "succeeded", "manifest_sha256": "b" * 64} for item in candidates_for_child],
                "batch_manifest": {"sha256": "c" * 64},
                "grouped_terminal_artifact": {"content_sha256": "d" * 64} if len(candidates_for_child) > 1 else None,
            }
        )

    monkeypatch.setattr(module.requests, "post", fake_post)
    monkeypatch.setattr(module.requests, "get", fake_get)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    receipt_path = tmp_path / "terminal.json"
    bundle_root = tmp_path / "bundles"
    receipt = module.execute_parent_fanout(
        parent_job_id="parent-1",
        parent_workflow_id="protein_design",
        settings_json=json.dumps(settings, sort_keys=True, separators=(",", ":")),
        candidate_dirs=candidates,
        output_receipt=receipt_path,
        output_bundles=bundle_root,
        api_url="http://api",
        poll_interval=0,
    )

    assert receipt["status"] == "complete"
    assert receipt["candidate_count"] == 3
    assert receipt["child_job_ids"] == child_ids
    assert receipt["fanout"]["effective_structures_per_job"] == 2
    assert receipt_path.read_bytes().endswith(b"\n")
    assert hashlib.sha256(receipt_path.read_bytes()).hexdigest() == receipt["receipt_file_sha256"]
    assert sorted(path.name for path in bundle_root.iterdir()) == ["candidate-0", "candidate-1", "candidate-2"]
    assert calls[0] == ("POST", "http://api/api/frustrampnn/jobs/parent-1/workflow-dataset/analyze")


def test_shared_client_fails_closed_when_any_required_child_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_client()
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "source.pdb").write_text("ATOM\n", encoding="ascii")
    (candidate / "metadata.json").write_text(
        json.dumps(
            {
                "candidate_id": "candidate-0",
                "parent_job_id": "parent-1",
                "parent_workflow_id": "structure_prediction",
                "producer_stage": "structure_prediction:boltz",
                "producer_candidate_key": "terminal/candidate-0.pdb",
                "requiredness": "required",
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    class Response:
        ok = True
        status_code = 200
        text = ""
        def __init__(self, payload): self.payload = payload
        def json(self): return self.payload
        def raise_for_status(self): return None

    monkeypatch.setattr(module.requests, "post", lambda *args, **kwargs: Response({
        "schema_name": "bms.structure-dataset-fanout.v1", "fanout_id": "a" * 64,
        "parent_job_id": "parent-1", "selected_structure_count": 1,
        "structures_per_job": 1, "effective_structures_per_job": 1,
        "replayed": False, "child_jobs": [{"job_id": "child-a", "structure_count": 1}],
    }))
    monkeypatch.setattr(module.requests, "get", lambda *args, **kwargs: Response({
        "total": 1, "completed": 0, "failed": 1, "cancelled": 0,
        "running": 0, "pending": 0, "all_done": True,
        "child_ids": ["child-a"], "children": [{"job_id": "child-a", "status": "failed"}],
    }))

    with pytest.raises(RuntimeError, match="required FrustraMPNN child Jobs failed"):
        module.execute_parent_fanout(
            parent_job_id="parent-1",
            parent_workflow_id="structure_prediction",
            settings_json=json.dumps(
                {"batching_enabled": False, "structures_per_job": 1},
                sort_keys=True,
                separators=(",", ":"),
            ),
            candidate_dirs=[candidate],
            output_receipt=tmp_path / "terminal.json",
            output_bundles=tmp_path / "bundles",
            api_url="http://api",
            poll_interval=0,
        )
