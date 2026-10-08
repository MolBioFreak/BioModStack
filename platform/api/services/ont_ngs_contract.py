"""ONT/NGS workflow-family and artifact-contract registry.

This module defines product/workflow intent for the BioModStack ONT/NGS
service family. It deliberately separates live device/run-control ownership
from reproducible Nextflow analysis ownership so MK1B/MK1D/MinKNOW work does
not get hidden inside a methylation-era pipeline branch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from services.sequence_qc_manifest import (
    ARTIFACT_STATES,
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
)

ONT_NGS_FAMILY_ID = "ont_ngs"
ANALYSIS_OWNER = "nextflow_analysis"
DEVICE_CONTROL_OWNER = "bms_service_api"
MANIFEST_SCHEMA = "sequence_qc.manifest.v1"

ONT_QUALITY_MODE_CONTRACT: dict[str, Any] = {
    "molecule_types": ("dna", "rna"),
    "basecalling_modes": ("simplex", "duplex"),
    "quality_modes": ("fast", "hac", "sup"),
    "default_quality_mode": "sup",
    "default_basecalling_mode": "simplex",
    "default_device": "cuda:0",
    "device_examples": ("cuda:0", "cuda:all", "cpu"),
    "modified_base_models": ("none", "5mC", "6mA", "6mA 4mC_5mC", "6mA 5mC"),
    "sample_multiplexing_fields": ("barcode_kit", "sample_sheet"),
}

COMMON_ARTIFACT_KINDS = (
    "raw_reads",
    "basecall_reads",
    "alignment_bam",
    "alignment_bai",
    "reference",
    "reference_index",
    "read_qc_summary",
    "per_base_support",
    "consensus",
)

ONT_SEQUENCE_QC_MANIFEST_CONTRACT: dict[str, Any] = {
    "family_id": ONT_NGS_FAMILY_ID,
    "schema": MANIFEST_SCHEMA,
    "manifest_filename": MANIFEST_FILENAME,
    "artifact_schema_version": MANIFEST_SCHEMA_VERSION,
    "required_top_level_fields": (
        "artifact_schema_version",
        "workflow_id",
        "job_id",
        "input_mode",
        "analysis_status",
        "artifacts",
    ),
    "artifact_states": tuple(sorted(ARTIFACT_STATES)),
    "artifact_kinds": COMMON_ARTIFACT_KINDS
    + (
        "modified_bases",
        "modkit_summary",
        "methylation_bed",
        "plasmid_qc_summary",
        "construct_screening_summary",
        "clone_validation_assembly",
        "clone_validation_report",
        "igv_track_config",
        "igv_report",
        "igv_track",
    ),
    "path_policy": "manifest_relative_only",
    "unavailable_policy": "optional_artifacts_use_state_without_fake_paths",
}


@dataclass(frozen=True)
class OntWorkflowSpec:
    workflow_id: str
    display_name: str
    description: str
    input_modes: tuple[str, ...]
    artifact_kinds: tuple[str, ...]
    lifecycle: str
    family_id: str = ONT_NGS_FAMILY_ID
    analysis_owner: str = ANALYSIS_OWNER
    device_control_owner: str = DEVICE_CONTROL_OWNER
    requires_live_device: bool = False
    manifest_filename: str = MANIFEST_FILENAME
    manifest_schema: str = MANIFEST_SCHEMA
    artifact_schema_version: int = MANIFEST_SCHEMA_VERSION


CANONICAL_ONT_WORKFLOWS: dict[str, OntWorkflowSpec] = {
    "ont_basecall_dna": OntWorkflowSpec(
        workflow_id="ont_basecall_dna",
        display_name="ONT DNA Basecalling",
        description="Dorado DNA basecalling from existing POD5 run outputs using configured CUDA resources.",
        input_modes=("pod5",),
        artifact_kinds=(
            "raw_reads",
            "basecall_reads",
            "alignment_bam",
            "alignment_bai",
            "read_qc_summary",
        ),
        lifecycle="seed",
    ),
    "ont_basecall_rna": OntWorkflowSpec(
        workflow_id="ont_basecall_rna",
        display_name="ONT RNA Basecalling",
        description="Dorado RNA basecalling from existing POD5 run outputs with RNA-specific model selection.",
        input_modes=("pod5",),
        artifact_kinds=(
            "raw_reads",
            "basecall_reads",
            "alignment_bam",
            "alignment_bai",
            "read_qc_summary",
        ),
        lifecycle="seed",
    ),
    "ont_plasmid_qc": OntWorkflowSpec(
        workflow_id="ont_plasmid_qc",
        display_name="ONT Plasmid QC",
        description="Reference-optional plasmid QC supporting POD5/BAM/FASTQ input modes with per-base support, consensus, and evidence artifacts.",
        input_modes=("pod5", "bam", "fastq"),
        artifact_kinds=(
            "basecall_reads",
            "alignment_bam",
            "alignment_bai",
            "reference",
            "reference_index",
            "read_qc_summary",
            "per_base_support",
            "consensus",
            "plasmid_qc_summary",
            "igv_track_config",
            "igv_report",
        ),
        lifecycle="seed",
    ),
    "ont_construct_screening": OntWorkflowSpec(
        workflow_id="ont_construct_screening",
        display_name="ONT Construct Screening",
        description="Construct screening via CloneValidation over ONT POD5/BAM/FASTQ reads with truthful consensus/variant evidence contracts.",
        input_modes=("pod5", "bam", "fastq"),
        artifact_kinds=(
            "raw_reads",
            "basecall_reads",
            "alignment_bam",
            "alignment_bai",
            "reference",
            "reference_index",
            "per_base_support",
            "consensus",
            "clone_validation_assembly",
            "clone_validation_report",
            "construct_screening_summary",
            "plasmid_qc_summary",
        ),
        lifecycle="seed",
    ),
    "ont_methylation_analysis": OntWorkflowSpec(
        workflow_id="ont_methylation_analysis",
        display_name="ONT Methylation Analysis",
        description="Dorado/modkit methylation analysis from POD5/BAM inputs with modified-base tags; FASTQ-only reads do not carry MM/ML tags and are not accepted here.",
        input_modes=("pod5", "bam"),
        artifact_kinds=(
            "raw_reads",
            "basecall_reads",
            "alignment_bam",
            "alignment_bai",
            "reference",
            "modified_bases",
            "modkit_summary",
            "methylation_bed",
            "read_qc_summary",
            "per_base_support",
            "consensus",
        ),
        lifecycle="seed",
    ),
    "wf_clone_validation": OntWorkflowSpec(
        workflow_id="wf_clone_validation",
        display_name="Wf Clone Validation",
        description="EPI2ME wf-clone-validation assembly with optional FASTQ plasmid QC — full plasmid QC pipeline with construct screening via assembly.",
        input_modes=("pod5", "bam", "fastq"),
        artifact_kinds=(
            "raw_reads",
            "basecall_reads",
            "alignment_bam",
            "alignment_bai",
            "reference",
            "reference_index",
            "read_qc_summary",
            "per_base_support",
            "consensus",
            "clone_validation_assembly",
            "clone_validation_report",
            "construct_screening_summary",
            "plasmid_qc_summary",
            "igv_track_config",
            "igv_report",
        ),
        lifecycle="seed",
    ),
    "ont_fastq_qc": OntWorkflowSpec(
        workflow_id="ont_fastq_qc",
        display_name="ONT FASTQ QC",
        description="Read-length/Q-score/yield and alignment-optional QC from existing FASTQ inputs.",
        input_modes=("fastq",),
        artifact_kinds=(
            "basecall_reads",
            "alignment_bam",
            "alignment_bai",
            "reference",
            "reference_index",
            "read_qc_summary",
            "per_base_support",
            "consensus",
            "plasmid_qc_summary",
            "igv_track_config",
            "igv_report",
        ),
        lifecycle="seed",
    ),
}

CANONICAL_ONT_WORKFLOW_IDS = tuple(CANONICAL_ONT_WORKFLOWS)

ONT_WORKFLOW_ALIASES = {
    "basecall_dna": "ont_basecall_dna",
    "basecall_rna": "ont_basecall_rna",
    "plasmid_qc": "ont_plasmid_qc",
    "construct_screening": "ont_construct_screening",
    "fastq_qc": "ont_fastq_qc",
    "wf_clone": "wf_clone_validation",
    "clone_validation": "wf_clone_validation",
}


WORKFLOW_DEFAULTS: dict[str, dict[str, Any]] = {
    "ont_basecall_dna": {
        "ont_molecule_type": "dna",
        "run_modkit": False,
        "run_fastq_qc": False,
        "modified_bases": "none",
    },
    "ont_basecall_rna": {
        "ont_molecule_type": "rna",
        "run_modkit": False,
        "run_fastq_qc": False,
        "modified_bases": "none",
    },
    "ont_plasmid_qc": {
        "ont_molecule_type": "dna",
        "run_modkit": False,
        "run_fastq_qc": True,
        "fastq_minimap2_preset": "map-ont",
        "modified_bases": "none",
    },
    "ont_construct_screening": {
        "ont_molecule_type": "dna",
        "run_modkit": False,
        "run_fastq_qc": True,
        "fastq_minimap2_preset": "map-ont",
        "modified_bases": "none",
    },
    "ont_methylation_analysis": {
        "ont_molecule_type": "dna",
        "run_modkit": True,
        "run_fastq_qc": True,
        "modified_bases": "6mA 4mC_5mC",
    },
    "ont_fastq_qc": {
        "ont_molecule_type": "dna",
        "run_modkit": False,
        "run_fastq_qc": True,
        "fastq_minimap2_preset": "map-ont",
        "modified_bases": "none",
    },
}


def resolve_ont_workflow_alias(workflow_id: str) -> str:
    """Normalize legacy/current ONT workflow names to canonical registry IDs."""
    normalized = str(workflow_id or "").strip()
    return ONT_WORKFLOW_ALIASES.get(normalized, normalized)


def get_ont_workflow_spec(workflow_id: str) -> OntWorkflowSpec:
    """Return a canonical ONT workflow spec, accepting legacy aliases."""
    canonical_id = resolve_ont_workflow_alias(workflow_id)
    try:
        return CANONICAL_ONT_WORKFLOWS[canonical_id]
    except KeyError as exc:
        raise KeyError(f"unknown ONT/NGS workflow: {workflow_id!r}") from exc


def normalize_ont_launch_params(workflow_id: str, params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Apply canonical ONT product/quality defaults without mutating caller params."""
    canonical_id = resolve_ont_workflow_alias(workflow_id)
    spec = get_ont_workflow_spec(canonical_id)
    normalized: dict[str, Any] = dict(WORKFLOW_DEFAULTS.get(canonical_id, {}))
    normalized.update(dict(params or {}))

    quality_mode = str(
        normalized.get("dorado_quality_mode")
        or normalized.get("dorado_model")
        or ONT_QUALITY_MODE_CONTRACT["default_quality_mode"]
    ).strip()
    if not quality_mode:
        quality_mode = ONT_QUALITY_MODE_CONTRACT["default_quality_mode"]

    basecall_mode = str(
        normalized.get("dorado_basecall_mode")
        or normalized.get("basecalling_mode")
        or ONT_QUALITY_MODE_CONTRACT["default_basecalling_mode"]
    ).strip()
    if not basecall_mode:
        basecall_mode = ONT_QUALITY_MODE_CONTRACT["default_basecalling_mode"]

    molecule_type = str(normalized.get("ont_molecule_type") or ("rna" if canonical_id.endswith("_rna") else "dna")).strip().lower()
    if molecule_type not in ONT_QUALITY_MODE_CONTRACT["molecule_types"]:
        molecule_type = "dna"

    normalized["ont_workflow_id"] = spec.workflow_id
    normalized["ont_molecule_type"] = molecule_type
    normalized["dorado_quality_mode"] = quality_mode
    if normalized.get("dorado_model"):
        normalized["dorado_model"] = normalized["dorado_model"]
    elif molecule_type == "rna":
        normalized["dorado_model"] = f"rna004_{quality_mode}"
    else:
        normalized["dorado_model"] = quality_mode
    normalized["dorado_basecall_mode"] = basecall_mode
    normalized["dorado_device"] = normalized.get("dorado_device") or ONT_QUALITY_MODE_CONTRACT["default_device"]
    normalized["manifest_contract"] = MANIFEST_SCHEMA

    return normalized
