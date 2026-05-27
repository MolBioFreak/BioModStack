from __future__ import annotations

import sys
from pathlib import Path

import yaml


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.ont_ngs_contract import (  # noqa: E402
    ANALYSIS_OWNER,
    CANONICAL_ONT_WORKFLOW_IDS,
    DEVICE_CONTROL_OWNER,
    ONT_NGS_FAMILY_ID,
    ONT_QUALITY_MODE_CONTRACT,
    ONT_SEQUENCE_QC_MANIFEST_CONTRACT,
    get_ont_workflow_spec,
    normalize_ont_launch_params,
    resolve_ont_workflow_alias,
)
from services.sequence_qc_manifest import (  # noqa: E402
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    SCHEMA_BY_KIND,
)


EXPECTED_CANONICAL_WORKFLOWS = {
    "ont_basecall_dna",
    "ont_basecall_rna",
    "ont_plasmid_qc",
    "ont_construct_screening",
    "ont_methylation_analysis",
    "ont_fastq_qc",
}


def test_ont_ngs_registry_declares_full_non_methylation_family() -> None:
    assert ONT_NGS_FAMILY_ID == "ont_ngs"
    assert set(CANONICAL_ONT_WORKFLOW_IDS) == EXPECTED_CANONICAL_WORKFLOWS

    for workflow_id in EXPECTED_CANONICAL_WORKFLOWS:
        spec = get_ont_workflow_spec(workflow_id)
        assert spec.workflow_id == workflow_id
        assert spec.family_id == "ont_ngs"
        assert spec.analysis_owner == ANALYSIS_OWNER
        assert spec.device_control_owner == DEVICE_CONTROL_OWNER
        assert spec.manifest_filename == MANIFEST_FILENAME
        assert spec.manifest_schema == "sequence_qc.manifest.v1"
        assert spec.artifact_schema_version == MANIFEST_SCHEMA_VERSION
        assert spec.lifecycle in {"seed", "planned"}


def test_legacy_nanopore_methylation_alias_resolves_to_canonical_ont_workflow() -> None:
    assert resolve_ont_workflow_alias("nanopore_methylation") == "ont_methylation_analysis"
    assert resolve_ont_workflow_alias("methylation_analysis") == "ont_methylation_analysis"
    assert resolve_ont_workflow_alias("ont_methylation_analysis") == "ont_methylation_analysis"


def test_device_run_control_is_not_misclassified_as_nextflow_analysis() -> None:
    assert DEVICE_CONTROL_OWNER == "bms_service_api"
    assert ANALYSIS_OWNER == "nextflow_analysis"

    for workflow_id in EXPECTED_CANONICAL_WORKFLOWS:
        spec = get_ont_workflow_spec(workflow_id)
        assert spec.requires_live_device is False
        assert spec.device_control_owner == "bms_service_api"
        assert spec.analysis_owner == "nextflow_analysis"


def test_sequence_qc_manifest_contract_names_required_sections_and_artifact_kinds() -> None:
    contract = ONT_SEQUENCE_QC_MANIFEST_CONTRACT

    assert contract["manifest_filename"] == MANIFEST_FILENAME
    assert contract["artifact_schema_version"] == MANIFEST_SCHEMA_VERSION
    assert contract["schema"] == "sequence_qc.manifest.v1"
    assert set(contract["required_top_level_fields"]) >= {
        "artifact_schema_version",
        "workflow_id",
        "job_id",
        "input_mode",
        "analysis_status",
        "artifacts",
    }
    assert set(contract["artifact_states"]) >= {
        "present",
        "not_requested",
        "not_applicable_to_input_mode",
        "missing_optional",
        "missing_required",
    }

    expected_artifact_kinds = {
        "raw_reads",
        "basecall_reads",
        "alignment_bam",
        "alignment_bai",
        "reference",
        "reference_index",
        "read_qc_summary",
        "per_base_support",
        "consensus",
        "modified_bases",
        "modkit_summary",
        "methylation_bed",
        "plasmid_qc_summary",
        "construct_screening_summary",
    }
    assert set(contract["artifact_kinds"]) >= expected_artifact_kinds
    assert expected_artifact_kinds <= set(SCHEMA_BY_KIND)


def test_product_specific_manifest_artifact_contracts_are_explicit() -> None:
    methylation = set(get_ont_workflow_spec("ont_methylation_analysis").artifact_kinds)
    plasmid = set(get_ont_workflow_spec("ont_plasmid_qc").artifact_kinds)
    construct = set(get_ont_workflow_spec("ont_construct_screening").artifact_kinds)
    fastq_qc = set(get_ont_workflow_spec("ont_fastq_qc").artifact_kinds)

    assert {"modified_bases", "modkit_summary", "methylation_bed"} <= methylation
    assert {"plasmid_qc_summary", "per_base_support", "consensus", "igv_report"} <= plasmid
    assert {"construct_screening_summary", "per_base_support", "consensus"} <= construct
    assert {"basecall_reads", "read_qc_summary", "per_base_support"} <= fastq_qc

    for artifact_kind in methylation | plasmid | construct | fastq_qc:
        assert artifact_kind in ONT_SEQUENCE_QC_MANIFEST_CONTRACT["artifact_kinds"]
        assert artifact_kind in SCHEMA_BY_KIND


def test_quality_mode_contract_covers_dna_rna_cuda_and_barcode_runtime_controls() -> None:
    contract = ONT_QUALITY_MODE_CONTRACT

    assert contract["molecule_types"] == ("dna", "rna")
    assert contract["basecalling_modes"] == ("simplex", "duplex")
    assert contract["quality_modes"] == ("fast", "hac", "sup")
    assert contract["default_quality_mode"] == "sup"
    assert contract["default_device"] == "cuda:0"
    assert "cuda:all" in contract["device_examples"]
    assert "barcode_kit" in contract["sample_multiplexing_fields"]
    assert "sample_sheet" in contract["sample_multiplexing_fields"]
    assert "none" in contract["modified_base_models"]


def test_normalize_ont_launch_params_adds_workflow_quality_and_gpu_defaults() -> None:
    normalized = normalize_ont_launch_params(
        workflow_id="methylation_analysis",
        params={"dorado_model": "hac", "modified_bases": "5mC", "barcode_kit": "SQK-RBK114.96"},
    )

    assert normalized["ont_workflow_id"] == "ont_methylation_analysis"
    assert normalized["ont_molecule_type"] == "dna"
    assert normalized["dorado_model"] == "hac"
    assert normalized["dorado_quality_mode"] == "hac"
    assert normalized["dorado_basecall_mode"] == "simplex"
    assert normalized["dorado_device"] == "cuda:0"
    assert normalized["modified_bases"] == "5mC"
    assert normalized["barcode_kit"] == "SQK-RBK114.96"


def test_normalize_ont_launch_params_applies_workflow_specific_defaults() -> None:
    dna = normalize_ont_launch_params("ont_basecall_dna", {})
    rna = normalize_ont_launch_params("ont_basecall_rna", {})
    fastq_qc = normalize_ont_launch_params("ont_fastq_qc", {"fastq_path": "/tmp/reads.fastq"})

    assert dna["ont_molecule_type"] == "dna"
    assert dna["run_modkit"] is False
    assert dna["run_fastq_qc"] is False
    assert rna["ont_molecule_type"] == "rna"
    assert rna["modified_bases"] == "none"
    assert fastq_qc["run_modkit"] is False
    assert fastq_qc["run_fastq_qc"] is True
    assert fastq_qc["fastq_minimap2_preset"] == "map-ont"


def test_nanopore_model_yaml_references_ont_ngs_family_contract() -> None:
    config_path = REPO_ROOT / "platform" / "api" / "config" / "models" / "nanopore.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    workflow_family = config["workflow_family"]
    assert workflow_family["id"] == ONT_NGS_FAMILY_ID
    assert workflow_family["analysis_owner"] == ANALYSIS_OWNER
    assert workflow_family["device_control_owner"] == DEVICE_CONTROL_OWNER
    assert workflow_family["manifest_contract"] == "sequence_qc.manifest.v1"
    assert set(workflow_family["canonical_workflows"]) == EXPECTED_CANONICAL_WORKFLOWS

    quality_modes = config["quality_modes"]
    assert quality_modes["molecule_types"] == ["dna", "rna"]
    assert quality_modes["basecalling_modes"] == ["simplex", "duplex"]
    assert quality_modes["dorado_quality_modes"] == ["fast", "hac", "sup"]
    assert quality_modes["default_device"] == "cuda:0"
    assert quality_modes["barcode_fields"] == ["barcode_kit", "sample_sheet"]
