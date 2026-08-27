from __future__ import annotations

import base64
import importlib.util
import json
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / "workflows" / "complex_prediction.nf"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _prepare_module():
    path = REPO_ROOT / "scripts" / "prepare_frustrampnn_candidate.py"
    spec = importlib.util.spec_from_file_location("prepare_frustrampnn_candidate_complex_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_complex_producer_boundary_emits_typed_manifest_without_adapter_inference(
    tmp_path: Path,
) -> None:
    producer_script = REPO_ROOT / "scripts" / "write_structure_producer_manifest.py"
    assert producer_script.is_file(), "complex producers must own their candidate metadata"
    spec = importlib.util.spec_from_file_location("write_structure_producer_manifest_test", producer_script)
    assert spec is not None and spec.loader is not None
    producer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(producer)

    predictions = tmp_path / "predictions"
    dotted = predictions / "batch.v1" / "seed.42"
    dotted.mkdir(parents=True)
    (dotted / "candidate_sample_0.cif").write_bytes(_minimal_mmcif())
    (dotted / "candidate_sample_1.cif").write_bytes(_minimal_mmcif() + b"# second\n")

    manifest = producer.build_manifest(
        predictions_root=predictions,
        producer_method="protenix",
        producer_sample="batch.v1",
        formats=("mmcif",),
    )
    assert [record["producer_rank"] for record in manifest["candidates"]] == [0, 1]
    assert [record["producer_output_key"] for record in manifest["candidates"]] == [
        "batch.v1/seed.42/candidate_sample_0.cif",
        "batch.v1/seed.42/candidate_sample_1.cif",
    ]
    assert all(record["producer_sample"] == "batch.v1" for record in manifest["candidates"])

    module = (REPO_ROOT / "modules" / "structure_prediction.nf").read_text(encoding="utf-8")
    protenix = (REPO_ROOT / "modules" / "protenix.nf").read_text(encoding="utf-8")
    assert "write_structure_producer_manifest.py" in module
    assert "write_structure_producer_manifest.py" in protenix
    assert module.count('path("producer_candidates.json")') >= 1
    assert protenix.count('path("producer_candidates.json")') >= 1
    complex_helper = module.split("def complexCanonicalProducerOutputs", 1)[1].split(
        "// Generate MSA", 1
    )[0]
    assert "rankMatcher" not in complex_helper
    assert "predicted.getName()" not in complex_helper


def test_complex_producer_binding_preserves_nested_keys_across_nextflow_staging() -> None:
    module = (REPO_ROOT / "modules" / "structure_prediction.nf").read_text(encoding="utf-8")
    protenix = (REPO_ROOT / "modules" / "protenix.nf").read_text(encoding="utf-8")
    complex_helper = module.split("def complexCanonicalProducerOutputs", 1)[1].split(
        "// Generate MSA", 1
    )[0]
    protenix_process = protenix.split("process ProtenixFromComplex", 1)[1].split(
        "process ", 1
    )[0]

    assert (
        'tuple val(input_sample), path("producer_candidates.json"), '
        'path("predictions/**/*.cif"), emit: canonical_structures, optional: true'
    ) in protenix_process
    assert "manifest.candidates.size() != predictedFiles.size()" in complex_helper
    assert "(manifestNames as Set).size() != manifestNames.size()" in complex_helper
    assert "ambiguous output filenames" in complex_helper
    assert "binds one output more than once" in complex_helper
    assert "boundKeys" in complex_helper
    assert "record.producer_output_key.toString().tokenize('/')[-1] == stagedOutputName" in complex_helper
    assert "record.producer_artifact_sha256 == artifactDigest" in complex_helper
    assert "boundKeys != manifestKeys" in complex_helper
    assert "predicted.baseName" not in complex_helper


def test_complex_producer_coordinates_are_explicit_typed_and_preserved(tmp_path: Path) -> None:
    module = _prepare_module()
    source = tmp_path / "candidate_sample_0.cif"
    source.write_bytes(_minimal_mmcif())
    digest = __import__("hashlib").sha256(source.read_bytes()).hexdigest()
    identity = {
        "producer_method": "protenix",
        "producer_sample": "batch-a",
        "producer_rank": 0,
        "producer_output_key": "batch-a/seed_42/candidate_sample_0.cif",
    }
    metadata = {
        "parent_job_id": "job-complex-1",
        "parent_workflow_id": "complex_prediction",
        "producer_stage": "complex_prediction:protenix:protein_only",
        "producer_candidate_key": "frustrampnn/sources/protenix/batch-a/seed-42/sample-0.pdb",
        **identity,
        "producer_identity_sha256": module.producer_identity_sha256(identity),
        "producer_artifact_sha256": digest,
        "source_format": "mmcif",
        "requiredness": "required",
        "checkpoint_id": "megascale.ckpt",
    }
    encoded = base64.b64encode(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")

    decoded = module._decode_metadata(encoded, source=source)

    assert decoded["producer_method"] == "protenix"
    assert decoded["producer_sample"] == "batch-a"
    assert decoded["producer_rank"] == 0
    assert decoded["producer_output_key"] == "batch-a/seed_42/candidate_sample_0.cif"
    assert decoded["producer_artifact_sha256"] == digest
    assert decoded["producer_sample"] != digest
    assert decoded["producer_rank"] != digest

    unavailable_identity = dict(identity, producer_sample=None, producer_rank=None)
    unavailable = dict(
        metadata,
        **unavailable_identity,
        producer_identity_sha256=module.producer_identity_sha256(unavailable_identity),
    )
    encoded_unavailable = base64.b64encode(
        json.dumps(unavailable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    unavailable_decoded = module._decode_metadata(encoded_unavailable, source=source)
    assert unavailable_decoded["producer_sample"] is None
    assert unavailable_decoded["producer_rank"] is None

    fabricated = dict(metadata, producer_rank=digest)
    encoded_fabricated = base64.b64encode(
        json.dumps(fabricated, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    with pytest.raises(ValueError, match="producer_rank"):
        module._decode_metadata(encoded_fabricated, source=source)


def _minimal_mmcif(*, include_dna: bool = False) -> bytes:
    columns = [
        "group_PDB", "id", "type_symbol", "label_atom_id", "label_alt_id",
        "label_comp_id", "label_asym_id", "label_entity_id", "label_seq_id",
        "pdbx_PDB_ins_code", "Cartn_x", "Cartn_y", "Cartn_z", "occupancy",
        "B_iso_or_equiv", "auth_seq_id", "auth_comp_id", "auth_asym_id",
        "auth_atom_id", "pdbx_PDB_model_num",
    ]
    lines = ["data_candidate\n", "loop_\n", *[f"_atom_site.{name}\n" for name in columns]]
    for atom_id, (atom, element) in enumerate((("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")), 1):
        row = [
            "ATOM", str(atom_id), element, atom, ".", "GLY", "A", "1", "1", "?",
            str(atom_id), str(atom_id + 1), str(atom_id + 2), "1.0", "20.0",
            "1", "GLY", "A", atom, "1",
        ]
        lines.append(" ".join(row) + "\n")
    if include_dna:
        for atom_id, (atom, element) in enumerate(
            (("P", "P"), ("OP1", "O"), ("OP2", "O"), ("O5P", "O")),
            5,
        ):
            row = [
                "ATOM", str(atom_id), element, atom, ".", "DA", "B", "2", "1", "?",
                str(atom_id), str(atom_id + 1), str(atom_id + 2), "1.0", "20.0",
                "1", "DA", "B", atom, "1",
            ]
            lines.append(" ".join(row) + "\n")
    lines.append("#\n")
    return "".join(lines).encode("utf-8")


@pytest.mark.parametrize("suffix", [".cif", ".mmcif"])
def test_complex_metadata_accepts_mixed_protein_dna_mmcif_aliases_and_rejects_pdb_claim(
    tmp_path: Path,
    suffix: str,
) -> None:
    module = _prepare_module()
    source = tmp_path / f"candidate_sample_0{suffix}"
    source.write_bytes(_minimal_mmcif(include_dna=True))
    source_sha = __import__("hashlib").sha256(source.read_bytes()).hexdigest()
    identity = {
        "producer_method": "protenix",
        "producer_sample": "batch-a",
        "producer_rank": 0,
        "producer_output_key": f"batch-a/candidate_sample_0{suffix}",
    }
    metadata = {
        "parent_job_id": "job-complex-1",
        "parent_workflow_id": "complex_prediction",
        "producer_stage": "complex_prediction:protenix:protein_only",
        "producer_candidate_key": "frustrampnn/sources/protenix/candidate.pdb",
        **identity,
        "producer_identity_sha256": module.producer_identity_sha256(identity),
        "producer_artifact_sha256": source_sha,
        "source_format": "mmcif",
        "requiredness": "required",
        "checkpoint_id": "megascale.ckpt",
    }

    def decode(candidate: dict[str, object]) -> dict[str, object]:
        encoded = base64.b64encode(
            json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        return module._decode_metadata(encoded, source=source)

    assert decode(metadata)["source_format"] == "mmcif"
    with pytest.raises(ValueError, match="source_format"):
        decode(dict(metadata, source_format="pdb"))


def test_complex_request_preserves_original_producer_provenance_and_binding(tmp_path: Path) -> None:
    module = _prepare_module()
    source = tmp_path / "candidate_sample_0.cif"
    source.write_bytes(_minimal_mmcif())
    original_sha = __import__("hashlib").sha256(source.read_bytes()).hexdigest()
    identity_fields = {
        "producer_method": "protenix",
        "producer_sample": "batch-a",
        "producer_rank": 0,
        "producer_output_key": "batch-a/seed_42/candidate_sample_0.cif",
    }
    metadata = {
        "parent_job_id": "job-complex-1",
        "parent_workflow_id": "complex_prediction",
        "producer_stage": "complex_prediction:protenix:protein_only",
        "producer_candidate_key": "frustrampnn/sources/protenix/candidate.pdb",
        **identity_fields,
        "producer_identity_sha256": module.producer_identity_sha256(identity_fields),
        "producer_artifact_sha256": original_sha,
        "source_format": "mmcif",
        "requiredness": "required",
        "checkpoint_id": "megascale.ckpt",
    }
    metadata["candidate_id"] = module.deterministic_candidate_id(
        parent_job_id=metadata["parent_job_id"],
        parent_workflow_id=metadata["parent_workflow_id"],
        producer_stage=metadata["producer_stage"],
        producer_candidate_key=metadata["producer_candidate_key"],
    )
    normalized = tmp_path / "normalized.pdb"
    request_path = tmp_path / "request.json"

    request = module.prepare_candidate(
        source=source,
        output_pdb=normalized,
        request_path=request_path,
        metadata=metadata,
    )

    normalized_sha = __import__("hashlib").sha256(normalized.read_bytes()).hexdigest()
    assert request["source_artifact"]["sha256"] == normalized_sha
    assert request["source_artifact"]["media_type"] == "chemical/x-pdb"
    assert request["producer_provenance"] == {
        **identity_fields,
        "producer_identity_sha256": metadata["producer_identity_sha256"],
        "original_source_format": "mmcif",
        "original_source_sha256": original_sha,
        "source_to_normalized_binding": {
            "kind": "sha256_pair_v1",
            "source_sha256": original_sha,
            "normalized_pdb_sha256": normalized_sha,
        },
    }
    assert json.loads(request_path.read_text(encoding="utf-8")) == request
    assert "producer_method: candidate_meta.producer_method" in _workflow()


def test_complex_metadata_rejects_identity_source_and_path_tampering(tmp_path: Path) -> None:
    module = _prepare_module()
    source = tmp_path / "candidate_sample_0.cif"
    source.write_bytes(_minimal_mmcif())
    source_sha = __import__("hashlib").sha256(source.read_bytes()).hexdigest()
    identity = {
        "producer_method": "protenix",
        "producer_sample": "batch-a",
        "producer_rank": 0,
        "producer_output_key": "batch.v1/seed.42/candidate_sample_0.cif",
    }
    base = {
        "parent_job_id": "job-complex-1",
        "parent_workflow_id": "complex_prediction",
        "producer_stage": "complex_prediction:protenix:protein_only",
        "producer_candidate_key": "frustrampnn/sources/protenix/candidate.pdb",
        **identity,
        "producer_identity_sha256": module.producer_identity_sha256(identity),
        "producer_artifact_sha256": source_sha,
        "source_format": "mmcif",
        "requiredness": "required",
        "checkpoint_id": "megascale.ckpt",
    }

    def decode(metadata: dict[str, object]) -> None:
        encoded = base64.b64encode(
            json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        module._decode_metadata(encoded, source=source)

    with pytest.raises(ValueError, match="producer_identity_sha256"):
        decode(dict(base, producer_identity_sha256="0" * 64))
    with pytest.raises(ValueError, match="physical source bytes"):
        decode(dict(base, producer_artifact_sha256="0" * 64))
    with pytest.raises(ValueError, match="source_format"):
        decode(dict(base, source_format="pdb"))
    with pytest.raises(ValueError, match="producer_rank"):
        decode(dict(base, producer_rank=-1))

    for output_key in ("../escape.cif", "/absolute.cif", "batch//candidate.cif", "batch/./candidate.cif", "batch\\candidate.cif"):
        bad_identity = dict(identity, producer_output_key=output_key)
        bad: dict[str, object] = dict(base, **bad_identity)
        try:
            bad["producer_identity_sha256"] = module.producer_identity_sha256(bad_identity)
        except ValueError:
            pass
        with pytest.raises(ValueError, match="producer_output_key"):
            decode(bad)


def test_complex_representation_identity_keeps_distinct_producers_samples_and_ranks() -> None:
    module = _prepare_module()
    assert hasattr(module, "producer_identity_sha256")
    base = {
        "producer_method": "boltz",
        "producer_sample": "batch-a",
        "producer_rank": 0,
        "producer_output_key": "batch-a/model_0.pdb",
    }
    same_candidate_mmcif = dict(base, producer_output_key="batch-a/model_0.cif")
    other_producer = dict(base, producer_method="protenix")
    other_sample = dict(base, producer_sample="batch-b")
    other_rank = dict(base, producer_rank=1)

    base_identity = module.producer_identity_sha256(base)
    assert module.producer_identity_sha256(same_candidate_mmcif) == base_identity
    assert module.producer_identity_sha256(other_producer) != base_identity
    assert module.producer_identity_sha256(other_sample) != base_identity
    assert module.producer_identity_sha256(other_rank) != base_identity


def test_complex_representation_dedup_groups_by_identity_before_normalized_bytes() -> None:
    workflow = _workflow()
    assert "candidate_meta.producer_identity_sha256" in workflow
    assert re.search(
        r"tuple\(candidate_meta\.producer_identity_sha256,\s*normalized_sha,",
        workflow,
    )
    assert "source_format == 'mmcif'" in workflow


def test_complex_module_exposes_typed_canonical_candidate_channel() -> None:
    module = (REPO_ROOT / "modules" / "structure_prediction.nf").read_text(encoding="utf-8")
    complex_block = module.split("workflow complex_prediction_wf", 1)[1]
    assert "canonical_candidates" in complex_block
    assert "producer_method" in module
    assert "producer_sample" in module
    assert "producer_candidates.json" in module
    assert "producer_output_key" in module
    assert re.search(r"emit:\s+structures\s+canonical_candidates", complex_block)


def test_complex_prediction_uses_one_canonical_component_for_actual_candidates() -> None:
    workflow = _workflow()

    assert "include { CanonicalFrustraMPNNV2 } from '../modules/frustrampnn.nf'" in workflow
    assert workflow.count("CanonicalFrustraMPNNV2(") == 1
    assert "CanonicalFrustraMPNN(" not in workflow
    assert "FrustrampnnQC" not in workflow
    assert "AggregateFrustrationReports" not in workflow
    assert "placeholder.pdb" not in workflow
    assert "complex_prediction:no_candidates" in workflow
    assert "complex_prediction_wf.out.structures.flatten()" in workflow
    assert "PrepareComplexPredictionFrustraMPNNCandidate" in workflow
    assert "MaterializeComplexPredictionFrustraMPNNCandidate" in workflow
    assert "CanonicalFrustraMPNNV2.out.result" in workflow
    assert re.search(r"emit:\s+structures\s+frustrampnn_results", workflow)


def test_complex_candidate_identity_is_byte_bound_and_representation_deduplicated() -> None:
    workflow = _workflow()

    assert "MessageDigest.getInstance('SHA-256')" in workflow
    assert "producer_artifact_sha256" in workflow
    assert "producer_method" in workflow
    assert "producer_sample" in workflow
    assert "producer_rank" in workflow
    assert "source_format" in workflow
    assert ".groupTuple(by: [0, 1])" in workflow
    assert "source_format == 'mmcif'" in workflow
    assert "candidatePreference" in workflow
    assert "producer_candidate_key" in workflow
    assert "complex_prediction_wf.out.canonical_candidates" in workflow
    assert "predicted.baseName" not in workflow
    assert "predicted.getName()" not in workflow
    assert ".withIndex()" not in workflow
    assert ".sort(" not in workflow


def test_complex_candidate_preparation_materializes_exact_pdb_before_component() -> None:
    workflow = _workflow()

    prepare_block = workflow.split("process PrepareComplexPredictionFrustraMPNNCandidate", 1)[1]
    prepare_block = prepare_block.split("process MaterializeComplexPredictionFrustraMPNNCandidate", 1)[0]
    materialize_block = workflow.split("process MaterializeComplexPredictionFrustraMPNNCandidate", 1)[1]
    materialize_block = materialize_block.split("workflow COMPLEX_PREDICTION", 1)[0]

    assert "prepare_frustrampnn_candidate.py" in prepare_block
    assert "--output-pdb prepared_source.pdb" in prepare_block
    assert "--request prepared_request.json" in prepare_block
    assert "path('prepared_source.pdb')" in prepare_block
    assert "path('prepared_request.json')" in prepare_block
    assert "canonical_source.pdb" in materialize_block
    assert "workflow_component_request_v3.json" in materialize_block
    assert "frustrampnn_structure_map_v1.json" in materialize_block
    assert "workflow_component_request_v1.json" not in materialize_block
    assert "CanonicalFrustraMPNNV2(MaterializeComplexPredictionFrustraMPNNCandidate.out.prepared)" in workflow


def test_complex_prediction_transports_complete_bounded_typed_v3_settings() -> None:
    workflow = _workflow()
    prepare_block = workflow.split("process PrepareComplexPredictionFrustraMPNNCandidate", 1)[1]
    prepare_block = prepare_block.split("process MaterializeComplexPredictionFrustraMPNNCandidate", 1)[0]
    enabled = workflow.split("if (params.run_frustrampnn == true)", 1)[1].split("} else {", 1)[0]

    assert "FRUSTRAMPNN_SETTINGS_MAX_BYTES" in workflow
    assert "requireCompleteFrustraMPNNSettings" in workflow
    assert "frustrampnn_settings_value_origin" in enabled
    assert "canonicalJsonBytes(rawSettings)" in enabled
    assert "Arrays.equals(settingsBytes, canonicalSettingsBytes)" in enabled
    assert "--request-version 3" in prepare_block
    assert "--structure-map prepared_structure_map.json" in prepare_block
    assert "--settings-base64" in prepare_block
    assert "--settings-sha256" in prepare_block
    assert "--settings-value-origin" in prepare_block


def test_complex_prediction_reports_closed_v2_publication_marker_before_completion() -> None:
    workflow = _workflow()
    reporter = workflow.split("process ReportComplexPredictionFrustraMPNNComplete", 1)[1]
    reporter = reporter.split("workflow COMPLEX_PREDICTION", 1)[0]

    validator_index = reporter.index("validate_frustrampnn_publication_markers.py")
    stage_reporter_index = reporter.index("stage_reporter.py")
    assert validator_index < stage_reporter_index
    assert "stageInMode 'copy'" in reporter
    assert "--job-root '${params.out_dir}'" in reporter
    assert "published_*.json" in reporter
    assert "json.loads" not in reporter
    assert "set(payload)" not in reporter


def test_complex_prediction_reports_terminal_states_and_protein_only_scope() -> None:
    workflow = _workflow()

    assert "params.frustrampnn_requiredness ?: 'required'" in workflow
    assert "frustrampnn_requiredness must be required" in workflow
    assert "'optional'" not in workflow
    assert "ReportComplexPredictionFrustraMPNNNotRequested" in workflow
    assert "ReportComplexPredictionFrustraMPNNComplete" in workflow
    assert "frustrampnn not_requested" in workflow
    assert "complex_prediction_frustrampnn_terminal_manifest" in workflow
    assert "status: 'not_requested'" in workflow
    assert "requiredness: 'not_requested'" in workflow
    assert "candidate_count: 0" in workflow
    assert "publish_frustrampnn_bundle.py" in workflow
    assert "frustrampnn complete" in workflow
    assert "def reportStage" not in workflow
    assert ".subscribe" not in workflow
    assert "workflow.onError" not in workflow
    assert ".execute()" not in workflow
    assert "protein entities only" in workflow
    assert "ligand/nucleic-acid context is not analyzed" in workflow
    assert "protein_only" in workflow


def test_complex_candidate_preparation_rejects_optional_requiredness(tmp_path: Path) -> None:
    module = _prepare_module()
    source = tmp_path / "candidate_sample_0.cif"
    source.write_bytes(_minimal_mmcif())
    source_sha = __import__("hashlib").sha256(source.read_bytes()).hexdigest()
    identity = {
        "producer_method": "protenix",
        "producer_sample": "batch-a",
        "producer_rank": 0,
        "producer_output_key": "batch-a/candidate_sample_0.cif",
    }
    metadata = {
        "parent_job_id": "job-complex-1",
        "parent_workflow_id": "complex_prediction",
        "producer_stage": "complex_prediction:protenix:protein_only",
        "producer_candidate_key": "frustrampnn/sources/protenix/candidate.pdb",
        **identity,
        "producer_identity_sha256": module.producer_identity_sha256(identity),
        "producer_artifact_sha256": source_sha,
        "source_format": "mmcif",
        "requiredness": "optional",
        "checkpoint_id": "megascale.ckpt",
    }
    encoded = base64.b64encode(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    with pytest.raises(ValueError, match="requiredness must be required"):
        module._decode_metadata(encoded, source=source)
