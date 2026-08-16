from __future__ import annotations

import hashlib
import io
import json
import copy
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from jsonschema import Draft202012Validator
from starlette.requests import Request

import services.md.results as md_results_module
from routers.md_results import _stream_verified_artifact

from scripts.bms_md.analysis import write_analysis_report
from scripts.bms_md.contract import write_atom_order_manifest
from services.md.results import MDJobRecord, MDResultError, analysis_report, artifact_inventory, build_analysis_work_items, completion_barrier, resolve_artifact, summary

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_unsatisfiable_md_artifact_range_is_standards_bound_and_closes_handle() -> None:
    handle = io.BytesIO(b"abcdef")
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"range", b"bytes=6-9")],
    })

    response = _stream_verified_artifact(handle, name="trajectory.xtc", size=6, request=request)

    assert response.status_code == 416
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-range"] == "bytes */6"
    assert response.headers["content-length"] == "0"
    assert response.body == b""
    assert handle.closed


def test_v2_replica_protocol_comparison_allows_only_hash_bound_input_relocation() -> None:
    requested = {
        "schema": "bms.md.job.v2",
        "input": {"structure": "/parent/inputs/structure.pdb", "structure_sha256": "a" * 64, "structure_bytes": 42},
        "execution": {"gpu_id": "2", "gpu_offload": "full", "ntmpi": 1, "ntomp": 8, "pin": "on"},
        "stages": {"production": {"steps": 5000}},
    }
    relocated = copy.deepcopy(requested)
    relocated["input"]["structure"] = "/worker/.worker_inputs/structure.pdb"

    assert md_results_module._replica_protocol_matches(requested, relocated) is True

    hash_drift = copy.deepcopy(relocated)
    hash_drift["input"]["structure_sha256"] = "b" * 64
    assert md_results_module._replica_protocol_matches(requested, hash_drift) is False

    protocol_drift = copy.deepcopy(relocated)
    protocol_drift["stages"]["production"]["steps"] = 5001
    assert md_results_module._replica_protocol_matches(requested, protocol_drift) is False

    realized = copy.deepcopy(relocated)
    realized["execution"] = {
        **requested["execution"],
        "gpu_id": "0",
        "scheduler_gpu_id": "2",
        "gpu_offload": "full_forces",
    }
    assert md_results_module._replica_protocol_matches(requested, realized) is False
    assert md_results_module._replica_protocol_matches(
        requested,
        realized,
        qualified_gpu_offload="full_forces",
    ) is True

    wrong_scheduler = copy.deepcopy(realized)
    wrong_scheduler["execution"]["scheduler_gpu_id"] = "3"
    assert md_results_module._replica_protocol_matches(
        requested,
        wrong_scheduler,
        qualified_gpu_offload="full_forces",
    ) is False

    unqualified_policy = copy.deepcopy(realized)
    unqualified_policy["execution"]["gpu_offload"] = "none"
    assert md_results_module._replica_protocol_matches(
        requested,
        unqualified_policy,
        qualified_gpu_offload="full_forces",
    ) is False


def test_preparation_gpu_policy_is_bound_to_requested_chemistry_and_input(tmp_path: Path) -> None:
    requested = {
        "schema": "bms.md.job.v2",
        "engine": "gromacs",
        "input": {"structure_sha256": "a" * 64, "structure_bytes": 42},
        "chemistry": {
            "profile_id": "amber_ff19sb_opc_protein_v1",
            "profile_sha256": "b" * 64,
            "runtime_identity": {"sif_sha256": "c" * 64},
        },
    }
    manifest_path = tmp_path / "preparation" / "preparation_bundle" / "preparation_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest = {
        "schema": "bms.md.preparation-bundle.v1",
        "profile": {"id": "amber_ff19sb_opc_protein_v1", "sha256": "b" * 64},
        "runtime": {"image_sha256": "c" * 64},
        "source": {"sha256": "a" * 64, "bytes": 42},
        "preparation": {"gromacs_gpu_offload": "full_forces"},
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert md_results_module._qualified_gpu_offload(tmp_path, requested) == "full_forces"

    manifest["source"]["sha256"] = "d" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(MDResultError, match="preparation policy"):
        md_results_module._qualified_gpu_offload(tmp_path, requested)


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
    topology.write_text(
        "ATOM      1  N   ALA A   1      11.000  12.000  13.000  1.00 20.00           N\nEND\n",
        encoding="utf-8",
    )
    trajectory.write_bytes(b"trajectory")
    atom_order = replica_root / "atom-order-manifest.json"
    _, identity = write_atom_order_manifest(topology, atom_order)
    engine_runtime = {"sif_sha256": "9" * 64, "image_name": "gromacs-md-2025.3.sif"}
    job_spec = {
        "schema": "bms.md.job.v1", "job_id": job_id, "engine": "gromacs",
        "replicas": 1, "random_seed": 20260717, "engine_runtime": engine_runtime,
    }
    manifest = {
        "schema": "bms.md.run.v1", "job_schema": "bms.md.job.v1", "status": "completed",
        "created_at": "2026-07-21T00:00:00Z", "job_id": job_id, "replica_index": 0,
        "replica_seed": 20260717,
        "engine": {"name": "gromacs", "version": "2025.3", "platform": "CUDA", "runtime": engine_runtime},
        "config": job_spec, "stages": {"production": {"status": "completed"}},
        "artifacts": {
            "final_coordinates": _record(topology, replica_root, semantic_role="analysis_topology", atom_order_identity=identity),
            "representative_structure": {**_record(topology, replica_root, semantic_role="representative_structure"), "source_trajectory_sha256": hashlib.sha256(trajectory.read_bytes()).hexdigest()},
            "trajectory": _record(trajectory, replica_root, semantic_role="analysis_trajectory", atom_order_identity=identity),
            "atom_order_manifest": _record(atom_order, replica_root, semantic_role="atom_order_manifest", atom_order_identity=identity),
        },
    }
    manifest_path = replica_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    aggregate = {
        "schema": "bms.md.aggregate.v1", "status": "completed", "job_id": job_id,
        "replicas": [{"replica_index": 0, "manifest": "replicas/replica_0/manifest.json"}],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(aggregate), encoding="utf-8")
    job = SimpleNamespace(
        id=job_id,
        model_id="molecular_dynamics",
        output_dir=str(tmp_path),
        child_output_dir=None,
        params={"md_job_spec": job_spec},
        provenance={},
    )
    return job, manifest_path, trajectory


def test_analysis_failure_is_typed_and_schema_valid_when_runtime_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _job, manifest_path, _trajectory = _md_job_tree(tmp_path)
    monkeypatch.setitem(sys.modules, "MDAnalysis", None)
    output, success = write_analysis_report(
        manifest_path,
        tmp_path / "failure.json",
        runtime_sha256="f" * 64,
    )
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

    output, success = write_analysis_report(
        manifest_path,
        tmp_path / "failure.json",
        runtime_sha256="f" * 64,
    )

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
    md_summary = summary(job)
    assert md_summary["trajectory_playback"]["supported"] is False
    assert md_summary["result_state"] is None
    assert md_summary["aggregate_manifest_sha256"] == hashlib.sha256((tmp_path / "manifest.json").read_bytes()).hexdigest()
    assert build_analysis_work_items(job)["items"][0]["state_authority"] == "source_contract_only_no_durable_md_analysis_table"

    trajectory_id = next(item["id"] for item in inventory["artifacts"] if item["name"] == "trajectory")
    assert "trajectory" not in trajectory_id
    trajectory.write_bytes(b"tampered")
    with pytest.raises(MDResultError, match="immutable manifest"):
        resolve_artifact(job, trajectory_id, verify=True)


def test_summary_admits_one_checksum_bound_xtc_frame_map_for_playback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_MD_RESULT_ROOT", str(tmp_path))
    raw_job, manifest_path, trajectory = _md_job_tree(tmp_path)
    replica_root = manifest_path.parent
    xtc = trajectory.with_suffix(".xtc")
    trajectory.rename(xtc)
    trajectory = xtc
    frame_map = replica_root / "trajectory-frame-map.json"
    frame_map.write_text(json.dumps({
        "schema": "bms.md.trajectory-frame-map.v1",
        "replica": 0,
        "trajectory_sha256": hashlib.sha256(trajectory.read_bytes()).hexdigest(),
        "frames": [
            {"display_frame": 0, "source_frame": 0, "time_ps": 0.0, "step": 0},
            {"display_frame": 1, "source_frame": 10, "time_ps": 20.0, "step": 1000},
        ],
    }), encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["trajectory"] = _record(
        trajectory, replica_root, semantic_role="analysis_trajectory",
        atom_order_identity=manifest["artifacts"]["trajectory"]["atom_order_identity"],
    )
    manifest["artifacts"]["trajectory_frame_map"] = _record(
        frame_map, replica_root, semantic_role="trajectory_frame_map",
        source_trajectory_sha256=hashlib.sha256(trajectory.read_bytes()).hexdigest(),
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    playback = summary(cast(MDJobRecord, raw_job))["trajectory_playback"]

    assert playback["supported"] is True
    replica = playback["replicas"][0]
    assert replica["replica"] == 0
    assert replica["trajectory_sha256"] == hashlib.sha256(trajectory.read_bytes()).hexdigest()
    assert isinstance(replica["frame_map_artifact_id"], str)
    assert replica["frame_count"] == 2
    assert replica["first_source_frame"] == 0
    assert replica["last_source_frame"] == 10
    assert replica["first_time_ps"] == 0.0
    assert replica["last_time_ps"] == 20.0


def test_final_structure_provenance_must_match_governed_trajectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BMS_MD_RESULT_ROOT", str(tmp_path))
    raw_job, manifest_path, _trajectory = _md_job_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["representative_structure"]["source_trajectory_sha256"] = "c" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    aggregate_path = tmp_path / "manifest.json"
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    aggregate["replicas"][0]["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    aggregate_path.write_text(json.dumps(aggregate), encoding="utf-8")

    with pytest.raises(MDResultError) as exc_info:
        artifact_inventory(cast(MDJobRecord, raw_job))

    assert exc_info.value.code == "MD_REPRESENTATIVE_STRUCTURE_PROVENANCE_INVALID"


def test_open_verified_artifact_streams_the_same_inode_that_was_hashed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BMS_MD_RESULT_ROOT", str(tmp_path))
    raw_job, _manifest_path, trajectory = _md_job_tree(tmp_path)
    job = cast(MDJobRecord, raw_job)
    inventory = artifact_inventory(job)
    trajectory_id = next(item["id"] for item in inventory["artifacts"] if item["name"] == "trajectory")
    attacker = trajectory.with_name("attacker.dcd")
    attacker.write_bytes(b"attacker-content")
    real_read = md_results_module.os.read
    swapped = False

    def swap_after_open(descriptor: int, count: int) -> bytes:
        nonlocal swapped
        if not swapped:
            swapped = True
            attacker.replace(trajectory)
        return real_read(descriptor, count)

    monkeypatch.setattr(md_results_module.os, "read", swap_after_open)
    artifact, handle = md_results_module.open_verified_artifact(job, trajectory_id)
    try:
        assert artifact.sha256 == hashlib.sha256(b"trajectory").hexdigest()
        assert trajectory.read_bytes() == b"attacker-content"
        assert handle.read() == b"trajectory"
    finally:
        handle.close()


def test_analysis_workflow_is_cpu_hash_bound_retryable_and_separate() -> None:
    workflow = (REPO_ROOT / "workflows/experimental/molecular_dynamics/analyze.nf").read_text()
    module = (REPO_ROOT / "modules/experimental/molecular_dynamics/analyze.nf").read_text()
    nextflow_service = (REPO_ROOT / "platform/api/services/nextflow.py").read_text()
    config = (REPO_ROOT / "nextflow.config").read_text()

    assert "MD_ANALYZE_REPLICA" in workflow
    assert "manifest_sha256" in workflow
    assert "MolecularDynamicsAnalysis" in module
    assert "sha256sum --check --strict" in module
    assert "BMS_MD_ANALYSIS_SIF_SHA256" in module
    assert "--runtime-sha256" in module
    assert "md_analysis_sif_sha256" in config
    assert "/opt/bms-md-analysis-runtime.sif" in config
    assert "--report-failure-as-output" not in module
    orchestrator = (REPO_ROOT / "workflows/experimental/molecular_dynamics/orchestrator.nf").read_text()
    assert "MD_ANALYZE_REPLICA(analysis_items)" not in orchestrator
    assert "scripts.bms_md.spawn_analysis" in orchestrator
    assert "--stage md_analysis" in orchestrator
    assert "scripts.bms_md.collect_analysis" in orchestrator
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
    timeseries_path = analysis_dir / "timeseries.parquet"
    residue_metrics_path = analysis_dir / "residue_metrics.parquet"
    timeseries_path.write_bytes(b"t")
    residue_metrics_path.write_bytes(b"r")
    manifest = json.loads(manifest_path.read_text())
    report = {
        "schema": "bms.md.analysis.v1",
        "status": "completed",
        "method": "md_backbone_rmsd_v1",
        "created_at": "2026-07-21T00:00:00Z",
        "job_id": job.id,
        "replica": 0,
        "inputs": {
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "topology_sha256": manifest["artifacts"]["final_coordinates"]["sha256"],
            "trajectory_sha256": manifest["artifacts"]["trajectory"]["sha256"],
            "atom_order_manifest_sha256": manifest["artifacts"]["atom_order_manifest"]["sha256"],
            "atom_order_identity": manifest["artifacts"]["final_coordinates"]["atom_order_identity"],
        },
        "tool": {"name": "MDAnalysis", "version": "2.9.0", "implementation_sha256": md_results_module.expected_analysis_implementation_sha256(), "runtime_sif_sha256": "3a74031e20dbd5012b7e532134f81816d596521dde47c4439fd1d6ae54fa5c68"},
        "selection": "protein and backbone",
        "reference": "first_admitted_frame",
        "policy": {
            "pbc": "nojump_then_center_protein", "alignment": "mass_weighted_backbone_fit",
            "exclusions": "non_protein_and_non_backbone", "requested_stride": 1,
            "effective_stride": 1, "max_points": 2,
        },
        "policy_sha256": "3" * 64,
        "frame_admission": {
            "source_frame_count": 2, "admitted_frame_count": 2, "first_source_frame": 0,
            "last_source_frame": 10, "first_time_ps": 0.0, "last_time_ps": 20.0,
        },
        "units": {"time": "ps", "rmsd": "angstrom"},
        "points": [
            {"replica": 0, "source_frame": 0, "time_ps": 0.0, "rmsd_angstrom": 0.0, "radius_of_gyration_angstrom": 10.0},
            {"replica": 0, "source_frame": 10, "time_ps": 20.0, "rmsd_angstrom": 1.0, "radius_of_gyration_angstrom": 10.1},
        ],
        "summary": {"count": 2, "min": 0.0, "mean": 0.5, "max": 1.0, "final": 1.0},
        "residue_metrics": [],
        "block_statistics": [],
        "evidence": {"status": "insufficient_evidence", "reason": "one replica", "statistical_unit": "replica", "frames_are_independent_replicates": False},
        "observables": {"backbone_rmsd": "completed", "backbone_rmsf": "completed", "radius_of_gyration": "completed", "sasa": "unavailable_validated_backend"},
        "specialized_analyzers": [],
        "derived_artifacts": {
            "timeseries": _record(timeseries_path, analysis_dir),
            "residue_metrics": _record(residue_metrics_path, analysis_dir),
        },
    }
    scientific_content = {
        key: value for key, value in report.items()
        if key not in {"created_at", "analysis_identity_sha256"}
    }
    report["analysis_identity_sha256"] = hashlib.sha256(
        json.dumps(scientific_content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    report_path.write_text(json.dumps(report), encoding="utf-8")
    sidecar = {
        "schema": "bms.md.analysis-artifacts.v1",
        "status": "completed",
        "job_id": job.id,
        "replica": 0,
        "input_manifest_sha256": report["inputs"]["manifest_sha256"],
        "analysis_identity_sha256": report["analysis_identity_sha256"],
        "artifacts": {
            "analysis_report": _record(report_path, analysis_dir, semantic_role="md_analysis_report"),
            "timeseries": {**report["derived_artifacts"]["timeseries"], "semantic_role": "md_analysis_timeseries"},
            "residue_metrics": {**report["derived_artifacts"]["residue_metrics"], "semantic_role": "md_analysis_residue_metrics"},
        },
    }
    (analysis_dir / "md_analysis_replica_0.artifacts.json").write_text(json.dumps(sidecar), encoding="utf-8")

    result = analysis_report(job)

    assert result["ensemble"]["statistical_unit"] == "replica"
    assert result["ensemble"]["frame_pooling"] is False
    assert result["ensemble"]["completed_replicas"] == 1
    assert result["ensemble"]["sample_stdev_of_replica_mean_rmsd_angstrom"] is None
    assert result["evidence"]["status"] == "insufficient_evidence"

    sidecar_path = analysis_dir / "md_analysis_replica_0.artifacts.json"
    mismatched_sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    mismatched_sidecar["artifacts"]["timeseries"]["sha256"] = "6" * 64
    sidecar_path.write_text(json.dumps(mismatched_sidecar), encoding="utf-8")
    with pytest.raises(MDResultError) as derived_mismatch:
        analysis_report(job)
    assert derived_mismatch.value.code == "MD_ANALYSIS_REPORT_INVALID"
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")

    aggregate_path = tmp_path / "manifest.json"
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    aggregate["lineage"] = {
        "completed_children": 1,
        "failed_children": 0,
        "cancelled_children": 0,
        "child_ids": ["replica-child-0"],
    }
    aggregate_path.write_text(json.dumps(aggregate), encoding="utf-8")

    authoritative_spec = raw_job.params["md_job_spec"]
    raw_job.params = {"md_job_spec": {**authoritative_spec, "engine": "openmm"}}
    with pytest.raises(MDResultError) as protocol_mismatch:
        completion_barrier(job)
    assert protocol_mismatch.value.code == "MD_COMPLETION_BLOCKED"
    raw_job.params = {"md_job_spec": authoritative_spec}

    collection = {
        "schema": "bms.md.analysis-collection.v1",
        "status": "completed",
        "job_id": job.id,
        "aggregate_manifest_sha256": hashlib.sha256(aggregate_path.read_bytes()).hexdigest(),
        "replica_manifest_set_sha256": hashlib.sha256(
            json.dumps([(0, hashlib.sha256(manifest_path.read_bytes()).hexdigest())], separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "required_analysis_children": 1,
        "completed_analysis_children": 1,
        "failed_analysis_children": 0,
        "cancelled_analysis_children": 0,
        "child_ids": ["analysis-child-0"],
        "analyses": [{"replica_index": 0}],
    }
    collection_path = analysis_dir / "manifest.json"
    collection_path.write_text(json.dumps(collection), encoding="utf-8")
    barrier = {
        "schema": "bms.md.completion-barrier.v1",
        "status": "completed",
        "job_id": job.id,
        "aggregate_manifest_sha256": hashlib.sha256(aggregate_path.read_bytes()).hexdigest(),
        "analysis_manifest_sha256": hashlib.sha256(collection_path.read_bytes()).hexdigest(),
    }
    (tmp_path / "md_completion_barrier.json").write_text(json.dumps(barrier), encoding="utf-8")
    raw_job.provenance = {
        "md": {
            "dynamics_state": "completed",
            "analysis_state": "failed",
            "aggregate_manifest_sha256": "0" * 64,
            "replica_manifest_set_sha256": "1" * 64,
        }
    }
    with pytest.raises(MDResultError) as conflict:
        completion_barrier(job)
    assert conflict.value.code == "MD_COMPLETION_CONFLICT"
    raw_job.provenance = {}
    lifecycle = completion_barrier(job)
    assert lifecycle["dynamics_state"] == "completed"
    assert lifecycle["analysis_state"] == "completed"
    assert lifecycle["replica_child_ids"] == ["replica-child-0"]
    assert lifecycle["analysis_child_ids"] == ["analysis-child-0"]


def test_completion_barrier_blocks_parent_before_analysis_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BMS_MD_RESULT_ROOT", str(tmp_path))
    raw_job, _manifest_path, _trajectory = _md_job_tree(tmp_path)

    with pytest.raises(MDResultError) as exc_info:
        completion_barrier(cast(MDJobRecord, raw_job))

    assert exc_info.value.code == "MD_COMPLETION_BLOCKED"
    assert exc_info.value.status_code == 409


def test_api_json_loader_rejects_non_finite_authoritative_results(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    report_path.write_text('{"status":"completed","summary":{"mean":NaN}}', encoding="utf-8")

    with pytest.raises(MDResultError) as exc_info:
        md_results_module._load_json(report_path, "MD_ANALYSIS_REPORT_INVALID")

    assert exc_info.value.code == "MD_ANALYSIS_REPORT_INVALID"


def test_job_owned_routes_stream_the_same_verified_descriptor_with_range_support() -> None:
    router = (REPO_ROOT / "platform/api/routers/md_results.py").read_text()
    assert '"/{job_id}/md/summary"' in router
    assert '"/{job_id}/md/artifacts"' in router
    assert '"/{job_id}/md/analysis"' in router
    assert "job-bound/no-authenticated-principal" in router
    assert "artifact, handle = open_verified_artifact" in router
    assert "_stream_verified_artifact(handle" in router
    assert "_serve_file_response(artifact.path" not in router


def test_all_live_md_terminal_writers_route_through_the_md_completion_barrier() -> None:
    nextflow = (REPO_ROOT / "platform/api/services/nextflow.py").read_text()
    orchestrator = (REPO_ROOT / "platform/api/services/gpu_orchestrator.py").read_text()
    completion = (REPO_ROOT / "platform/api/services/md/completion.py").read_text()
    assert "await validate_and_finalize_md_job(job, session)" in nextflow
    assert "await validate_and_finalize_md_job(job, session)" in orchestrator
    assert "def validate_and_finalize_md_job" in completion


def test_nextflow_analysis_terminal_publication_cannot_flush_stale_state_before_guarded_cas() -> None:
    source = (REPO_ROOT / "platform/api/services/nextflow.py").read_text()
    start = source.index("md_analysis_parent_id = (")
    end = source.index("if md_analysis_parent_id:", start)
    publication = source[start:end]
    assert "await session.flush()" not in publication
    assert "session.expunge(job)" in publication
    assert "Job.status == JobStatus.RUNNING" in publication
    assert 'Job.queue_status == "running"' in publication
    assert publication.index("session.expunge(job)") < publication.index("update(Job)")
