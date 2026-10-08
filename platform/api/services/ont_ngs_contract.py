"""ONT/NGS workflow-family and artifact-contract registry.

This module defines product/workflow intent for the BioModStack ONT/NGS
service family. It deliberately separates live device/run-control ownership
from reproducible Nextflow analysis ownership so MK1B/MK1D/MinKNOW work does
not get hidden inside a methylation-era pipeline branch.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from pathlib import Path
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
DORADO_LOCK_PATH = Path(__file__).resolve().parents[3] / "config" / "ngs" / "dorado_v1.3.1.lock.json"


def normalized_fasta_sequence_sha256(path: Path) -> str:
    """Hash one normalized FASTA record without trusting headers or line wrapping."""
    records = 0
    chunks: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(">"):
                records += 1
                if records > 1:
                    raise ValueError("reference_fasta must contain exactly one record")
                continue
            if records != 1:
                raise ValueError("reference_fasta sequence appears before its header")
            chunks.append(line.upper())
    sequence = "".join(chunks)
    if records != 1 or not sequence:
        raise ValueError("reference_fasta must contain one non-empty record")
    invalid = sorted(set(sequence) - set("ACGTN"))
    if invalid:
        raise ValueError(f"reference_fasta contains unsupported symbols: {''.join(invalid)}")
    return hashlib.sha256(sequence.encode("ascii")).hexdigest()


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
    "dorado_preflight",
    "dorado_runtime_provenance",
    "demux_manifest",
    "barcode_units",
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
        "clone_validation_adapter",
        "clone_validation_report",
        "clone_validation_runtime_provenance",
        "construct_verification",
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
            "dorado_preflight",
            "dorado_runtime_provenance",
            "demux_manifest",
            "barcode_units",
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
            "dorado_preflight",
            "dorado_runtime_provenance",
        ),
        lifecycle="seed",
    ),
    "ont_plasmid_qc": OntWorkflowSpec(
        workflow_id="ont_plasmid_qc",
        display_name="ONT Plasmid QC",
        description="Reference-required plasmid QC supporting POD5/BAM/FASTQ input modes with per-base support, consensus, and evidence artifacts.",
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
            "clone_validation_adapter",
            "clone_validation_report",
            "clone_validation_runtime_provenance",
            "construct_verification",
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
    "methylation_analysis": "ont_methylation_analysis",
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
        "dorado_quality_mode": "hac",
        "run_modkit": False,
        "run_fastq_qc": True,
        "modified_bases": "none",
    },
    "ont_fastq_qc": {
        "ont_molecule_type": "dna",
        "run_modkit": False,
        "run_fastq_qc": True,
        "fastq_minimap2_preset": "map-ont",
        "modified_bases": "none",
    },
    "wf_clone_validation": {
        "ont_molecule_type": "dna",
        "run_modkit": False,
        "run_fastq_qc": True,
        "modified_bases": "none",
        "wf_clone_assembly_tool": "flye",
        "wf_clone_basecaller_model": "dna_r10.4.1_e8.2_400bps_hac@v5.0.0",
        "wf_clone_min_quality": 9,
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

    lock_bytes = DORADO_LOCK_PATH.read_bytes()
    lock = json.loads(lock_bytes)
    current_lock_sha256 = hashlib.sha256(lock_bytes).hexdigest()
    submitted_lock_sha256 = str(normalized.get("dorado_lock_sha256") or "").strip().lower()
    if submitted_lock_sha256 and submitted_lock_sha256 != current_lock_sha256:
        raise ValueError("accepted Dorado lock identity changed before execution")
    submitted_model_id = str(normalized.get("dorado_resolved_model_id") or "").strip()
    supplied_model = str(normalized.get("dorado_model") or "").strip()
    quality_mode = str(normalized.get("dorado_quality_mode") or "").strip().lower()
    if supplied_model:
        if supplied_model in ONT_QUALITY_MODE_CONTRACT["quality_modes"]:
            if quality_mode and quality_mode != supplied_model:
                raise ValueError("dorado_model and dorado_quality_mode must preserve one exact quality choice")
            quality_mode = supplied_model
        elif supplied_model != str(normalized.get("dorado_resolved_model_id") or "").strip():
            raise ValueError("dorado quality must be fast, hac, or sup; exact model IDs are server-resolved")
    if not quality_mode:
        quality_mode = ONT_QUALITY_MODE_CONTRACT["default_quality_mode"]
    if quality_mode not in ONT_QUALITY_MODE_CONTRACT["quality_modes"]:
        raise ValueError("dorado quality must be fast, hac, or sup")

    basecall_mode = str(
        normalized.get("dorado_basecall_mode")
        or normalized.get("basecalling_mode")
        or ONT_QUALITY_MODE_CONTRACT["default_basecalling_mode"]
    ).strip()
    if not basecall_mode:
        basecall_mode = ONT_QUALITY_MODE_CONTRACT["default_basecalling_mode"]
    if basecall_mode not in ONT_QUALITY_MODE_CONTRACT["basecalling_modes"]:
        raise ValueError("dorado_basecall_mode must be simplex or duplex")

    required_molecule = "rna" if canonical_id == "ont_basecall_rna" else "dna"
    molecule_type = str(normalized.get("ont_molecule_type") or required_molecule).strip().lower()
    if molecule_type != required_molecule:
        raise ValueError(f"{canonical_id} requires ont_molecule_type={required_molecule}")
    if molecule_type == "rna" and basecall_mode == "duplex":
        raise ValueError("RNA duplex is unsupported")
    if molecule_type == "rna" and normalized.get("trim_adapters") is False:
        raise ValueError("Dorado RNA always trims adapters; trim_adapters=false is unsupported")
    if basecall_mode == "duplex" and normalized.get("trim_adapters") is False:
        raise ValueError("locked Dorado duplex lacks an adapter-trim control; trim_adapters=false is unsupported")

    barcode_kit = str(normalized.get("barcode_kit") or "").strip() or None
    if barcode_kit and barcode_kit not in lock["barcoding"]["accepted_kits"]:
        raise ValueError("unsupported barcode kit")
    if barcode_kit and canonical_id != "ont_basecall_dna":
        raise ValueError("inline barcode classification is only supported by ont_basecall_dna")
    if barcode_kit and basecall_mode == "duplex":
        raise ValueError("barcode classification is incompatible with duplex in the locked runtime")
    sample_sheet = str(normalized.get("sample_sheet") or "").strip() or None
    if sample_sheet and not barcode_kit:
        raise ValueError("sample_sheet requires barcode_kit")
    duplex_pairs = str(normalized.get("duplex_pairs") or "").strip() or None
    if basecall_mode == "duplex" and not duplex_pairs:
        raise ValueError("duplex mode requires duplex_pairs")
    if basecall_mode == "simplex" and duplex_pairs:
        raise ValueError("duplex_pairs is only valid in duplex mode")

    modified_bases = str(normalized.get("modified_bases") or "none").strip()
    if modified_bases not in {"none", "5mC_5hmC", "6mA"}:
        raise ValueError("unsupported modified-base selection")
    if modified_bases != "none" and (molecule_type != "dna" or quality_mode != "hac" or basecall_mode != "simplex"):
        raise ValueError("modified-base selection requires DNA HAC simplex basecalling")
    if barcode_kit and modified_bases != "none":
        raise ValueError("inline barcode classification and modified-base calling are mutually exclusive")

    raw_batch = normalized.get("dorado_batch_size")
    if raw_batch in (None, ""):
        batch_size = int(lock["policy"]["default_batch_size"][basecall_mode])
    elif isinstance(raw_batch, bool) or not (
        isinstance(raw_batch, int) or (isinstance(raw_batch, str) and re.fullmatch(r"[0-9]+", raw_batch.strip()))
    ):
        raise ValueError("dorado_batch_size must be an integer")
    else:
        batch_size = int(raw_batch)
    if not int(lock["policy"]["batch_size_min"]) <= batch_size <= int(lock["policy"]["batch_size_max"]):
        raise ValueError("dorado_batch_size is outside the locked bounded policy")
    raw_qscore = normalized.get("min_qscore", 10)
    if isinstance(raw_qscore, bool) or not (
        isinstance(raw_qscore, int) or (isinstance(raw_qscore, str) and re.fullmatch(r"[0-9]+", raw_qscore.strip()))
    ):
        raise ValueError("min_qscore must be an integer")
    min_qscore = int(raw_qscore)
    if min_qscore < 0 or min_qscore > 30:
        raise ValueError("min_qscore must be an integer from 0 through 30")
    resolved_model = lock["models"][molecule_type][quality_mode]["id"]
    if submitted_model_id and submitted_model_id != resolved_model:
        raise ValueError("accepted Dorado model identity changed before execution")

    normalized["ont_workflow_id"] = spec.workflow_id
    normalized["ont_molecule_type"] = molecule_type
    normalized["dorado_quality_mode"] = quality_mode
    normalized["dorado_model"] = resolved_model
    normalized["dorado_resolved_model_id"] = resolved_model
    normalized["dorado_basecall_mode"] = basecall_mode
    if basecall_mode == "duplex":
        normalized["dorado_stereo_model"] = lock["models"]["stereo"]["id"]
    else:
        normalized.pop("dorado_stereo_model", None)
    normalized["dorado_batch_size"] = batch_size
    normalized["min_qscore"] = min_qscore
    normalized["modified_bases"] = modified_bases
    for key, value in (("barcode_kit", barcode_kit), ("sample_sheet", sample_sheet), ("duplex_pairs", duplex_pairs)):
        if value is None:
            normalized.pop(key, None)
        else:
            normalized[key] = value
    normalized["dorado_lock_sha256"] = current_lock_sha256
    normalized["dorado_device"] = ONT_QUALITY_MODE_CONTRACT["default_device"]
    normalized["manifest_contract"] = MANIFEST_SCHEMA

    if canonical_id == "wf_clone_validation":
        normalized.pop("wf_clone_analyse_unclassified", None)
        assembly_tool = str(normalized.get("wf_clone_assembly_tool") or "").strip()
        if assembly_tool not in {"flye", "canu"}:
            raise ValueError("wf_clone_assembly_tool must preserve an exact supported value: flye or canu")
        model_id = str(normalized.get("wf_clone_basecaller_model") or "").strip()
        accepted_model = "dna_r10.4.1_e8.2_400bps_hac@v5.0.0"
        if model_id != accepted_model:
            raise ValueError(f"wf_clone_basecaller_model must equal the locked exact identity {accepted_model}")
        normalized["wf_clone_assembly_tool"] = assembly_tool
        normalized["wf_clone_basecaller_model"] = model_id

        def clone_bool(name: str, default: bool = False) -> bool:
            value = normalized.get(name, default)
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be boolean")
            return value

        def clone_int(name: str, default: int, minimum: int, maximum: int) -> int:
            value = normalized.get(name, default)
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be an integer from {minimum} through {maximum}")
            return value

        def clone_number(name: str, default: float, minimum: float, maximum: float) -> float:
            value = normalized.get(name, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be a number from {minimum:g} through {maximum:g}")
            return float(value)

        flye_quality = str(normalized.get("wf_clone_flye_quality") or "nano-hq").strip()
        if flye_quality not in {"nano-hq", "nano-corr", "nano-raw"}:
            raise ValueError("wf_clone_flye_quality must be nano-hq, nano-corr, or nano-raw")
        normalized["wf_clone_flye_quality"] = flye_quality
        normalized["wf_clone_non_uniform_coverage"] = clone_bool("wf_clone_non_uniform_coverage")
        normalized["wf_clone_canu_fast"] = clone_bool("wf_clone_canu_fast")
        normalized["wf_clone_min_quality"] = clone_int("wf_clone_min_quality", 9, 0, 60)
        normalized["wf_clone_cutsite_mismatch"] = clone_int("wf_clone_cutsite_mismatch", 1, 0, 10)
        normalized["wf_clone_primer_mismatch"] = clone_int("wf_clone_primer_mismatch", 2, 0, 10)
        normalized["wf_clone_expected_coverage"] = clone_number("wf_clone_expected_coverage", 95, 0, 100)
        normalized["wf_clone_expected_identity"] = clone_number("wf_clone_expected_identity", 99, 0, 100)
        primers = str(normalized.get("wf_clone_primers") or "").strip()
        insert_reference = str(normalized.get("wf_clone_insert_reference") or "").strip()
        host_reference = str(normalized.get("wf_clone_host_reference") or "").strip()
        regions_bedfile = str(normalized.get("wf_clone_regions_bedfile") or "").strip()
        if insert_reference and not primers:
            raise ValueError("wf_clone_insert_reference requires wf_clone_primers")
        if regions_bedfile and not host_reference:
            raise ValueError("wf_clone_regions_bedfile requires wf_clone_host_reference")
        for key, value in (
            ("wf_clone_primers", primers),
            ("wf_clone_insert_reference", insert_reference),
            ("wf_clone_host_reference", host_reference),
            ("wf_clone_regions_bedfile", regions_bedfile),
        ):
            if value:
                normalized[key] = value
            else:
                normalized.pop(key, None)

    dimer_workflows = {"ont_plasmid_qc", "ont_construct_screening", "ont_fastq_qc", "wf_clone_validation"}
    if canonical_id in dimer_workflows:
        def dimer_bool(name: str, default: bool) -> bool:
            value = normalized.get(name, default)
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be boolean")
            return value

        def dimer_int(name: str, default: int, minimum: int, maximum: int) -> int:
            value = normalized.get(name, default)
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be an integer from {minimum} through {maximum}")
            return value

        normalized["enable_rotating_reference_frames"] = dimer_bool("enable_rotating_reference_frames", True)
        normalized["rotation_scan_step_bp"] = dimer_int("rotation_scan_step_bp", 1, 1, 10_000)
        normalized["single_ref_split_min_mapq"] = dimer_int("single_ref_split_min_mapq", 20, 0, 60)
        normalized["single_ref_split_min_segment_bp"] = dimer_int("single_ref_split_min_segment_bp", 250, 1, 1_000_000)
        normalized["single_ref_split_max_query_gap_bp"] = dimer_int("single_ref_split_max_query_gap_bp", 500, 0, 1_000_000)

    return normalized
