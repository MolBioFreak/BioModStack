from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def test_sample_documents_and_native_sequence_cif_survive_publication(tmp_path, monkeypatch):
    """Inert documents only; a pre-rejected candidate never runs science."""
    import shutil
    test_shape_result_builder_copies_accepted_bundle_and_binds_hashes(tmp_path)
    bundle = tmp_path / 'bundle'
    request_path = tmp_path / 'request.json'
    metadata = json.loads((bundle / 'candidate_bundle.json').read_text())
    metadata.update(status='rejected', reason={'code': 'inert_fixture_rejection'})
    (bundle / 'candidate_bundle.json').write_text(json.dumps(metadata))
    evidence = tmp_path / 'evidence'
    evidence.mkdir()

    def descriptor(path):
        return {'filename': path.name, 'sha256': _sha(path), 'bytes': path.stat().st_size}

    samples, artifacts = [], []
    for key in ('shape_candidate_0001_001', 'shape_candidate_0001_000'):
        structure = evidence / (key + '.cif')
        metrics = evidence / (key + '.json')
        structure.write_text('data_EXPLICITLY_INERT_' + key)
        metrics.write_text(json.dumps({'sample_id': key}))
        row = {'schema': 'bms_shape_prediction_sample_v1', 'native_sample_key': key,
               'source_sequence_key': 'shape_candidate_0001', 'predictor': 'esmfold2',
               'structure': descriptor(structure), 'metrics': descriptor(metrics)}
        samples.append(row)
        artifacts.extend((row['structure'], row['metrics']))
    suite = {'schema': 'bms_shape_validator_suite_v2', 'sequence_name': 'shape_candidate_0001',
             'validators': ['esmfold2'], 'records': {'esmfold2': {'schema': 'bms_shape_validator_record_v1',
             'status': 'completed', 'samples': samples, 'artifacts': artifacts,
             'native_metrics': {'sample_id': 'shape_candidate_0001_000'}}}}
    suite_path = evidence / 'shape_validator_records.json'
    suite_path.write_text(json.dumps(suite))
    sequences = tmp_path / 'sequences'
    sequences.mkdir()
    native_cif = sequences / 'native.cif'
    native_cif.write_text('data_EXPLICITLY_INERT_CALIBY_NATIVE')
    (sequences / 'sequence_records.json').write_text(json.dumps({'schema': 'bms_shape_sequences_v2', 'records': [{
        'sequence_name': 'shape_candidate_0001', 'engine': 'caliby_experimental', 'backbone_candidate_id': 'a' * 64,
        'sample_index': 0, 'native_record_id': '0', 'native_structure': {**descriptor(native_cif), 'format': 'cif'}}]}))
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    attach_post_refold = __import__('attach_shape_post_refold').attach_post_refold
    attach_post_refold(bundle_dir=bundle, validator_records_path=suite_path, request_path=request_path,
                      sequence_bundle=sequences, geometry_manifest_path=tmp_path / 'UNUSED_INERT',
                      points_path=tmp_path / 'UNUSED_INERT', sdf_path=tmp_path / 'UNUSED_INERT')
    output = tmp_path / 'published'
    manifest = _module().build_result(job_id='inert', request_path=request_path, candidate_bundles=[bundle],
                                     output_dir=output, aggregate_path=tmp_path / 'aggregate.json')
    for source in (bundle, sequences, evidence):
        shutil.rmtree(source)
    assert manifest['candidate_count'] == 0 and manifest['rejected_count'] == 1
    row, = manifest['rejections']
    assert (output / row['sequence_native_structure']['relative_path']).read_text() == 'data_EXPLICITLY_INERT_CALIBY_NATIVE'
    published_suite = json.loads((output / row['validator_evidence']['relative_path']).read_text())
    bindings = {(item['validator'], item['native_path']): item for item in row['validator_artifacts']}
    for sample in published_suite['records']['esmfold2']['samples']:
        path = output / bindings['esmfold2', sample['structure']['filename']]['relative_path']
        assert path.read_text() == 'data_EXPLICITLY_INERT_' + sample['native_sample_key']
        assert _sha(path) == sample['structure']['sha256']

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
        "validator_suite": ["esmfold2"],
    }
    path.write_text(json.dumps(payload))
    aggregate = {"schema": "bms_rfd3_aggregate_manifest_v1", "request_sha256": payload["request_sha256"], "status": "complete"}
    aggregate["aggregate_sha256"] = hashlib.sha256(json.dumps(aggregate, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    path.with_name("aggregate.json").write_text(json.dumps(aggregate))
    return payload


def _suite(bundle: Path) -> dict:
    path = bundle / "suite.json"
    path.write_text(json.dumps({"schema": "bms_shape_validator_suite_v1", "sequence_name": "shape_candidate_0001",
        "validators": ["esmfold2"], "records": {"esmfold2": {"status": "completed", "artifacts": []}}}))
    return {"filename": path.name, "sha256": _sha(path), "bytes": path.stat().st_size}


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
                "validator_evidence": _suite(bundle),
                "candidate_id": "shape_candidate_0001",
                "name": "shape_candidate_0001",
                "structure": {"filename": structure.name, "sha256": _sha(structure), "bytes": structure.stat().st_size},
                "source_backbone": {"filename": source.name, "sha256": _sha(source), "bytes": source.stat().st_size},
                "metrics": {"filename": metrics.name, "sha256": _sha(metrics), "bytes": metrics.stat().st_size},
                "provenance": {"sequence_name": "shape_candidate_0001", "sequence_engine": "proteinmpnn", "predictor": "esmfold2"},
            }
        )
    )
    output = tmp_path / "out"
    manifest = module.build_result(
        job_id="job-shape",
        request_path=request_path,
        aggregate_path=request_path.with_name("aggregate.json"),
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
        "validator_evidence": _suite(bundle),
        "candidate_id": "shape_candidate_0001",
        "name": "shape_candidate_0001",
        "structure": {"filename": structure.name, "sha256": _sha(structure), "bytes": structure.stat().st_size},
        "source_backbone": {"filename": source.name, "sha256": _sha(source), "bytes": source.stat().st_size},
        "metrics": {"filename": metrics.name, "sha256": _sha(metrics), "bytes": metrics.stat().st_size},
        "provenance": {"sequence_name": "shape_candidate_0001"},
    }))
    with pytest.raises(ValueError, match="source-backbone binding mismatch"):
        module.build_result(
            job_id="job-shape",
            request_path=request_path,
            aggregate_path=request_path.with_name("aggregate.json"),
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
        aggregate_path=request_path.with_name("aggregate.json"),
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
                "validator_evidence": _suite(bundle),
                "candidate_id": "shape_candidate_0001",
                "name": "shape_candidate_0001",
                "structure": {"filename": "candidate.cif", "sha256": "0" * 64, "bytes": 15},
                "source_backbone": {"filename": "source.pdb", "sha256": _sha(bundle / "source.pdb"), "bytes": 12},
                "metrics": {"filename": "candidate.metrics.json", "sha256": "0" * 64, "bytes": 2},
                "provenance": {"sequence_name": "shape_candidate_0001"},
            }
        )
    )
    with pytest.raises(ValueError, match="SHA-256"):
        module.build_result(
            job_id="job-shape",
            request_path=request_path,
            aggregate_path=request_path.with_name("aggregate.json"),
            candidate_bundles=[bundle],
            output_dir=tmp_path / "out",
        )
