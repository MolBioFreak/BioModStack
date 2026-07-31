from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[3] / "scripts" / "shape_blueprint" / "build_shape_result.py"


def _module():
    assert SCRIPT.exists(), "Shape result builder is absent"
    spec = importlib.util.spec_from_file_location("build_shape_result", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _request(path: Path) -> dict:
    payload = {
        "request_id": "request_shape_0000000000000000000000000001",
        "request_sha256": "2" * 64,
        "geometry_id": "geom_" + "1" * 32,
        "geometry_sha256": "3" * 64,
        "point_pool_sha256": "4" * 64,
        "sdf_sha256": "5" * 64,
        "sdf_sign": "positive_inside",
    }
    path.write_text(json.dumps(payload))
    return payload


def test_shape_result_builder_copies_accepted_bundle_and_binds_hashes(tmp_path: Path) -> None:
    module = _module()
    request_path = tmp_path / "request.json"
    request = _request(request_path)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    structure = bundle / "candidate.cif"
    source = bundle / "source.pdb"
    metrics = bundle / "candidate.metrics.json"
    structure.write_bytes(b"data_candidate\n")
    source.write_bytes(b"ATOM source\n")
    metrics.write_text(json.dumps({
        "schema": "bms_shape_candidate_metrics_v1",
        "candidate_id": "shape_candidate_0001",
        "geometry_sha256": request["geometry_sha256"],
        "point_pool_sha256": request["point_pool_sha256"],
        "sdf_sha256": request["sdf_sha256"],
        "source_backbone_sha256": _sha(source),
        "shape_total": 1.0,
    }))
    (bundle / "candidate_bundle.json").write_text(
        json.dumps(
            {
                "schema": "bms_shape_candidate_bundle_v1",
                "status": "accepted",
                "candidate_id": "shape_candidate_0001",
                "name": "shape_candidate_0001",
                "structure": {"filename": structure.name, "sha256": _sha(structure), "bytes": structure.stat().st_size},
                "source_backbone": {"filename": source.name, "sha256": _sha(source), "bytes": source.stat().st_size},
                "metrics": {"filename": metrics.name, "sha256": _sha(metrics), "bytes": metrics.stat().st_size},
                "provenance": {"sequence_engine": "proteinmpnn", "predictor": "esmfold2"},
            }
        )
    )
    output = tmp_path / "out"
    manifest = module.build_result(
        job_id="job-shape",
        request_path=request_path,
        candidate_bundles=[bundle],
        output_dir=output,
    )
    assert manifest["outcome"] == "candidates"
    assert manifest["candidate_count"] == 1
    assert manifest["request_sha256"] == request["request_sha256"]
    candidate = manifest["candidates"][0]
    assert candidate["structure"]["relative_path"] == "results/shape_candidates/shape_candidate_0001.cif"
    assert _sha(output / candidate["structure"]["relative_path"]) == candidate["structure"]["sha256"]


def test_shape_result_builder_rejects_source_backbone_binding_mismatch(tmp_path: Path) -> None:
    module = _module()
    request_path = tmp_path / "request.json"
    request = _request(request_path)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    structure = bundle / "candidate.cif"
    source = bundle / "source.pdb"
    metrics = bundle / "candidate.metrics.json"
    structure.write_bytes(b"data_candidate\n")
    source.write_bytes(b"ATOM substituted source\n")
    metrics.write_text(json.dumps({
        "schema": "bms_shape_candidate_metrics_v1",
        "candidate_id": "shape_candidate_0001",
        "geometry_sha256": request["geometry_sha256"],
        "point_pool_sha256": request["point_pool_sha256"],
        "sdf_sha256": request["sdf_sha256"],
        "source_backbone_sha256": "9" * 64,
    }))
    (bundle / "candidate_bundle.json").write_text(json.dumps({
        "schema": "bms_shape_candidate_bundle_v1",
        "status": "accepted",
        "candidate_id": "shape_candidate_0001",
        "name": "shape_candidate_0001",
        "structure": {"filename": structure.name, "sha256": _sha(structure), "bytes": structure.stat().st_size},
        "source_backbone": {"filename": source.name, "sha256": _sha(source), "bytes": source.stat().st_size},
        "metrics": {"filename": metrics.name, "sha256": _sha(metrics), "bytes": metrics.stat().st_size},
        "provenance": {},
    }))
    with pytest.raises(ValueError, match="source-backbone binding mismatch"):
        module.build_result(
            job_id="job-shape",
            request_path=request_path,
            candidate_bundles=[bundle],
            output_dir=tmp_path / "out",
        )


def test_shape_result_builder_emits_truthful_empty_success(tmp_path: Path) -> None:
    module = _module()
    request_path = tmp_path / "request.json"
    _request(request_path)
    output = tmp_path / "out"
    manifest = module.build_result(
        job_id="job-shape",
        request_path=request_path,
        candidate_bundles=[],
        output_dir=output,
    )
    assert manifest["outcome"] == "no_candidates"
    assert manifest["candidate_count"] == 0
    assert manifest["reason"]["code"] == "no_refolded_candidates"


def test_shape_result_builder_rejects_bundle_hash_mismatch(tmp_path: Path) -> None:
    module = _module()
    request_path = tmp_path / "request.json"
    _request(request_path)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "candidate.cif").write_bytes(b"data_candidate\n")
    (bundle / "source.pdb").write_bytes(b"ATOM source\n")
    (bundle / "candidate.metrics.json").write_text("{}")
    (bundle / "candidate_bundle.json").write_text(
        json.dumps(
            {
                "schema": "bms_shape_candidate_bundle_v1",
                "status": "accepted",
                "candidate_id": "shape_candidate_0001",
                "name": "shape_candidate_0001",
                "structure": {"filename": "candidate.cif", "sha256": "0" * 64, "bytes": 15},
                "source_backbone": {"filename": "source.pdb", "sha256": _sha(bundle / "source.pdb"), "bytes": 12},
                "metrics": {"filename": "candidate.metrics.json", "sha256": "0" * 64, "bytes": 2},
                "provenance": {},
            }
        )
    )
    with pytest.raises(ValueError, match="SHA-256"):
        module.build_result(
            job_id="job-shape",
            request_path=request_path,
            candidate_bundles=[bundle],
            output_dir=tmp_path / "out",
        )
