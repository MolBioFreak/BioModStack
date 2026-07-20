from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from jsonschema import Draft202012Validator

from scripts.bms_md.analysis import write_analysis_report
from services.md.results import MDJobRecord, MDResultError, analysis_report, artifact_inventory, build_analysis_work_items, resolve_artifact, summary

REPO_ROOT = Path(__file__).resolve().parents[3]


def _record(path: Path, root: Path, **extra: str) -> dict:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        **extra,
    }


def _md_job_tree(tmp_path: Path):
    job_id = "md-job-1"
    replica_root = tmp_path / "replicas" / "replica_0"
    replica_root.mkdir(parents=True)
    topology = replica_root / "final.pdb"
    trajectory = replica_root / "production.dcd"
    topology.write_text("ATOM\n", encoding="utf-8")
    trajectory.write_bytes(b"trajectory")
    identity = "atom-order-v1"
    manifest = {
        "schema": "bms.md.run.v1", "status": "completed", "job_id": job_id, "replica_index": 0,
        "artifacts": {
            "final_coordinates": _record(topology, replica_root, semantic_role="analysis_topology", atom_order_identity=identity),
            "representative_structure": _record(topology, replica_root, semantic_role="representative_structure"),
            "trajectory": _record(trajectory, replica_root, semantic_role="analysis_trajectory", atom_order_identity=identity),
        },
    }
    manifest_path = replica_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    aggregate = {
        "schema": "bms.md.aggregate.v1", "status": "completed", "job_id": job_id,
        "replicas": [{"replica_index": 0, "manifest": "replicas/replica_0/manifest.json"}],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(aggregate), encoding="utf-8")
    job = SimpleNamespace(id=job_id, model_id="molecular_dynamics", output_dir=str(tmp_path), child_output_dir=None)
    return job, manifest_path, trajectory


def test_analysis_failure_is_typed_and_schema_valid_when_runtime_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _job, manifest_path, _trajectory = _md_job_tree(tmp_path)
    monkeypatch.setitem(sys.modules, "MDAnalysis", None)
    output, success = write_analysis_report(manifest_path, tmp_path / "failure.json")
    report = json.loads(output.read_text(encoding="utf-8"))

    assert success is False
    assert report["schema"] == "bms.md.analysis.v1"
    assert report["failure"]["code"] == "MD_ANALYSIS_RUNTIME_UNAVAILABLE"
    Draft202012Validator(json.loads((REPO_ROOT / "schemas/md_analysis_v1.schema.json").read_text())).validate(report)


def test_analysis_fails_before_open_on_path_escape(tmp_path: Path) -> None:
    _job, manifest_path, _trajectory = _md_job_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"]["trajectory"]["path"] = "../escape.dcd"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    output, success = write_analysis_report(manifest_path, tmp_path / "failure.json")

    assert success is False
    assert json.loads(output.read_text())["failure"]["code"] == "MD_ANALYSIS_PATH_ESCAPE"


def test_md_inventory_is_opaque_job_owned_and_checksum_enforced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_MD_RESULT_ROOT", str(tmp_path))
    raw_job, _manifest_path, trajectory = _md_job_tree(tmp_path)
    job = cast(MDJobRecord, raw_job)
    inventory = artifact_inventory(job)

    assert inventory["source"] == "validated_job_owned_manifests"
    assert all("path" not in item for item in inventory["artifacts"])
    assert inventory["artifacts"][0]["content_url"].startswith(f"/api/jobs/{job.id}/md/artifacts/")
    assert summary(job)["trajectory_playback"]["supported"] is False
    assert build_analysis_work_items(job)["items"][0]["state_authority"] == "source_contract_only_no_durable_md_analysis_table"

    trajectory_id = next(item["id"] for item in inventory["artifacts"] if item["name"] == "trajectory")
    assert "trajectory" not in trajectory_id
    trajectory.write_bytes(b"tampered")
    with pytest.raises(MDResultError, match="immutable manifest"):
        resolve_artifact(job, trajectory_id, verify=True)


def test_analysis_workflow_is_cpu_hash_bound_retryable_and_separate() -> None:
    workflow = (REPO_ROOT / "workflows/experimental/molecular_dynamics/analyze.nf").read_text()
    module = (REPO_ROOT / "modules/experimental/molecular_dynamics/analyze.nf").read_text()
    nextflow_service = (REPO_ROOT / "platform/api/services/nextflow.py").read_text()
    config = (REPO_ROOT / "nextflow.config").read_text()

    assert "MD_ANALYZE_REPLICA" in workflow
    assert "manifest_sha256" in workflow
    assert "MolecularDynamicsAnalysis" in module
    assert "sha256sum --check --strict" in module
    assert "--report-failure-as-output" not in module
    assert "MD_ANALYZE_REPLICA(analysis_items)" in (REPO_ROOT / "workflows/experimental/molecular_dynamics/orchestrator.nf").read_text()
    assert "(\"molecular_dynamics\", \"analyze\")" in nextflow_service
    assert "molecular_dynamics_analysis" in config
    analysis_label = config.rindex("withLabel: MolecularDynamicsAnalysis")
    coordinator_label = config.index("withLabel: MolecularDynamicsCoordinator", analysis_label)
    assert "--nv" not in config[analysis_label:coordinator_label]


def test_replica_reporting_never_pools_frames_as_biological_replicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BMS_MD_RESULT_ROOT", str(tmp_path))
    raw_job, manifest_path, _trajectory = _md_job_tree(tmp_path)
    job = cast(MDJobRecord, raw_job)
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    report_path = analysis_dir / "md_analysis_replica_0.json"
    report = {
        "schema": "bms.md.analysis.v1",
        "status": "completed",
        "job_id": job.id,
        "replica": 0,
        "inputs": {
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "topology_sha256": json.loads(manifest_path.read_text())["artifacts"]["final_coordinates"]["sha256"],
            "trajectory_sha256": json.loads(manifest_path.read_text())["artifacts"]["trajectory"]["sha256"],
            "atom_order_identity": "atom-order-v1",
        },
        "points": [
            {"replica": 0, "source_frame": 0, "time_ps": 0.0, "rmsd_angstrom": 0.0},
            {"replica": 0, "source_frame": 10, "time_ps": 20.0, "rmsd_angstrom": 1.0},
        ],
        "summary": {"count": 2, "min": 0.0, "mean": 0.5, "max": 1.0, "final": 1.0},
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")
    sidecar = {
        "schema": "bms.md.analysis-artifacts.v1",
        "status": "completed",
        "job_id": job.id,
        "replica": 0,
        "input_manifest_sha256": report["inputs"]["manifest_sha256"],
        "artifacts": {"analysis_report": _record(report_path, analysis_dir, semantic_role="md_analysis_report")},
    }
    (analysis_dir / "md_analysis_replica_0.artifacts.json").write_text(json.dumps(sidecar), encoding="utf-8")

    result = analysis_report(job)

    assert result["ensemble"]["statistical_unit"] == "replica"
    assert result["ensemble"]["frame_pooling"] is False
    assert result["ensemble"]["completed_replicas"] == 1
    assert result["ensemble"]["sample_stdev_of_replica_mean_rmsd_angstrom"] is None
    assert result["evidence"]["status"] == "insufficient_evidence"


def test_job_owned_routes_reuse_range_response_only_after_md_resolution() -> None:
    router = (REPO_ROOT / "platform/api/routers/md_results.py").read_text()
    assert '@router.get("/{job_id}/md/summary")' in router
    assert '@router.get("/{job_id}/md/artifacts")' in router
    assert '@router.get("/{job_id}/md/analysis")' in router
    assert "artifact = resolve_artifact" in router
    assert "_serve_file_response(artifact.path" in router
