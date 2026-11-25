"""ONT/NGS submission schemas, path confinement, and JobCreate assembly."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from database import current_launch_context_id
from paths import (
    get_allowed_roots,
    get_molbio_ngs_reference_root,
    resolve_allowed_path,
)
from schemas import ExecutionPolicy, JobCreate
from services import ont_submission_trust
from services.ont_ngs_contract import (
    get_ont_workflow_spec,
    normalize_ont_launch_params,
    normalized_fasta_sequence_identity,
    resolve_expected_plasmid_size,
    resolve_ont_workflow_alias,
)

# Canonical API workflow IDs and model-registry modes are distinct contracts.
# Keep this mapping explicit; prefix stripping does not work for wf_clone_validation.
ONT_WORKFLOW_MODEL_MODES: dict[str, str] = {
    "ont_basecall_dna": "basecall_dna",
    "ont_basecall_rna": "basecall_rna",
    "ont_plasmid_qc": "plasmid_qc",
    "ont_construct_screening": "construct_screening",
    "ont_methylation_analysis": "methylation_analysis",
    "ont_fastq_qc": "fastq_qc",
    "ont_pooled_reference_assignment": "pooled_reference_assignment",
    "wf_clone_validation": "clone_validation",
}

ONT_PRIMARY_INPUT_KEYS: dict[str, str] = {
    "pod5": "pod5_dir",
    "bam": "bam_path",
    "fastq": "fastq_path",
}

ONT_SERVER_CONTROLLED_PROVENANCE_PARAMS = ont_submission_trust.ONT_SERVER_CONTROLLED_PROVENANCE_PARAMS
ONT_SERVER_CONTROLLED_RUNTIME_PARAMS = ont_submission_trust.ONT_SERVER_CONTROLLED_RUNTIME_PARAMS

ONT_REFERENCE_REQUIRED_WORKFLOWS = frozenset(
    {
        "ont_plasmid_qc",
        "ont_construct_screening",
        "ont_methylation_analysis",
        "ont_fastq_qc",
        "wf_clone_validation",
    }
)
# A reference here is still server-controlled, but the workflow also runs without
# one: it assembles and annotates, and the expected-sequence checks are not run.
ONT_REFERENCE_OPTIONAL_WORKFLOWS = frozenset({"wf_clone_validation"})


class OntManagedReferenceRequest(BaseModel):
    """Opaque immutable identities; the server alone resolves the FASTA path."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    global_domain_experiment_id: str = Field(min_length=1, max_length=128)
    molbio_ngs_state_revision_id: str = Field(min_length=1, max_length=128)
    ngs_reference_revision_id: str = Field(min_length=1, max_length=128)

    launch_snapshot_id: str | None = None

    @field_validator("launch_snapshot_id")
    @classmethod
    def validate_snapshot_id(cls, value: str | None) -> str | None:
        return ont_submission_trust.canonical_launch_snapshot_id(value) if value is not None else None

    @field_validator("global_domain_experiment_id", "molbio_ngs_state_revision_id", "ngs_reference_revision_id")
    @classmethod
    def require_nonempty_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("managed reference identities must be nonempty")
        return normalized


class OntFastqLaunchSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    relative_path: str = Field(min_length=1, max_length=4096)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: StrictInt = Field(gt=0)

    @model_validator(mode="after")
    def validate_selector(self) -> "OntFastqLaunchSnapshot":
        ont_submission_trust.validate_fastq_snapshot_selector(self.relative_path, self.sha256)
        return self


class OntNgsSubmitRequest(BaseModel):
    """Request body for submitting an ONT/NGS analysis workflow."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: str | None = Field(default=None, description="Optional job name. Defaults to the workflow display name.")
    params: dict[str, Any] = Field(default_factory=dict)
    pinned_gpu: int | None = Field(default=None)
    execution_target_id: str | None = Field(default=None)
    execution_policy: ExecutionPolicy | None = None
    source_instrument_run_id: str | None = Field(default=None)
    managed_reference: OntManagedReferenceRequest | None = None
    fastq_snapshot: OntFastqLaunchSnapshot | None = None
    comparison_launch_id: str | None = None
    execution_plan_approval: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("comparison_launch_id")
    @classmethod
    def validate_comparison_launch_id(cls, value: str | None) -> str | None:
        return ont_submission_trust.canonical_launch_snapshot_id(value) if value is not None else None

    @model_validator(mode="after")
    def validate_managed_reference_exclusivity(self) -> "OntNgsSubmitRequest":
        if self.execution_target_id is not None and self.pinned_gpu is not None:
            raise ValueError("remote placement cannot select a controller physical GPU")
        if {"execution_target_id", "execution_policy"}.intersection(self.params):
            raise ValueError("execution placement and policy are typed top-level fields")
        if "managed_reference" in self.params or any(
            key in self.params
            for key in ("managed_reference_path", "managed_reference_fasta_path")
        ):
            raise ValueError("managed_reference is accepted only as the typed top-level object")
        if self.comparison_launch_id is not None and not (
            self.params.get("molbio_ngs_receipt_id") and self.params.get("ngs_comparison_panel_receipt_id")
        ):
            raise ValueError("comparison launch replay requires both receipt identities")
        if self.managed_reference is None:
            return self
        conflicting = sorted(
            {
                "molbio_ngs_receipt_id",
                "reference_fasta",
                "ngs_comparison_panel_receipt_id",
                "comparison_panel_snapshot",
                "comparison_panel_min_mapq",
            }.intersection(self.params)
        )
        if conflicting:
            raise ValueError(
                "managed_reference is mutually exclusive with receipt, reference_fasta, and comparison-panel launch fields: "
                + ", ".join(conflicting)
            )
        return self


def mode_for_ont_workflow(workflow_id: str) -> str:
    canonical = resolve_ont_workflow_alias(workflow_id)
    return ONT_WORKFLOW_MODEL_MODES[canonical]


def validate_ont_input_contract(canonical_id: str, params: dict[str, Any]) -> tuple[str, str]:
    """Validate one truthful primary input and return its mode/path."""
    spec = get_ont_workflow_spec(canonical_id)
    selected = [
        (input_mode, key, str(params[key]).strip())
        for input_mode, key in ONT_PRIMARY_INPUT_KEYS.items()
        if params.get(key) is not None and str(params[key]).strip()
    ]
    if len(selected) != 1:
        keys = ", ".join(ONT_PRIMARY_INPUT_KEYS.values())
        raise ValueError(f"exactly one primary ONT input is required ({keys}); found {len(selected)}")

    input_mode, input_key, input_path = selected[0]
    if input_mode not in spec.input_modes:
        raise ValueError(
            f"workflow {canonical_id!r} does not accept {input_mode!r} input via {input_key!r}; "
            f"accepted modes: {', '.join(spec.input_modes)}"
        )

    if (canonical_id in ONT_REFERENCE_REQUIRED_WORKFLOWS and canonical_id not in ONT_REFERENCE_OPTIONAL_WORKFLOWS
            and not str(params.get("reference_fasta") or "").strip()):
        raise ValueError(f"workflow {canonical_id!r} requires reference_fasta for construct verification")

    if canonical_id == "ont_basecall_dna" and (input_mode == "fastq" or params.get("run_fastq_qc") or params.get("run_modkit")) and not str(params.get("reference_fasta") or "").strip():
        raise ValueError("DNA reference analysis requires reference_fasta")

    if (canonical_id == "ont_basecall_rna" and params.get("run_modkit")
            and str(params.get("modified_bases") or "none") != "none" and not str(params.get("reference_fasta") or "").strip()):
        raise ValueError("RNA modification tables require reference_fasta")
    if input_mode == "fastq" and bool(params.get("run_modkit")):
        raise ValueError("modkit requires a BAM with meaningful MM/ML tags or POD5 basecalled with modified bases")

    return input_mode, input_path


def confine_submitted_path(value: Any, label: str, *, directory: bool, allow_results: bool = False) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{label} is empty")
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        lexical = candidate.absolute()
    else:
        lexical = resolve_allowed_path(raw)
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} must be an existing allowed path") from exc
    matched_root: Path | None = None
    relative: Path | None = None
    roots = get_allowed_roots()
    result_keys = tuple(key for key in ("bms_results", "results") if key in roots)
    if not allow_results:
        for key in result_keys:
            try:
                resolved.relative_to(roots[key].resolve())
            except ValueError:
                continue
            raise ValueError(f"{label} points into protected job results and requires source authorization")
    root_keys = ("inputs", "uploads", "downloads", "data", "sequencer_runs") + (result_keys if allow_results else ())
    for root in (roots[key] for key in root_keys if key in roots):
        root_resolved = root.resolve()
        try:
            relative = lexical.relative_to(root_resolved)
            resolved.relative_to(root_resolved)
        except ValueError:
            continue
        matched_root = root_resolved
        break
    if matched_root is None or relative is None:
        raise ValueError(f"{label} must be confined beneath an allowed data root")
    cursor = matched_root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"{label} may not traverse symlinks")
    if directory and not resolved.is_dir():
        raise ValueError(f"{label} must be an existing directory")
    if not directory and not resolved.is_file():
        raise ValueError(f"{label} must be an existing regular file")
    return str(resolved)


def safe_ont_job_name(value: Any, fallback: str) -> str:
    name = str(value or fallback).strip()
    if not name or len(name) > 128 or ".." in name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]*", name):
        raise ValueError("ONT job name must be 1-128 safe filename characters without traversal components")
    return name


def job_create_for_ont_submit(
    workflow_id: str,
    request: OntNgsSubmitRequest,
    *,
    trusted_server_params: frozenset[str] = frozenset(),
    trusted_result_paths: frozenset[str] = frozenset(),
    trusted_reference_fasta: Path | None = None,
) -> JobCreate:
    canonical_id = resolve_ont_workflow_alias(workflow_id)
    if canonical_id == "ont_pooled_reference_assignment":
        raise ValueError(
            "pooled reference assignment requires the dedicated atomic submission endpoint"
        )
    submitted_params = dict(request.params)
    if "run_multimer_qc" in submitted_params:
        raise ValueError(
            "run_multimer_qc is read-only legacy compatibility and cannot be submitted for a fresh ONT job"
        )
    if any(key in submitted_params for key in ("comparison_panel_snapshot", "comparison_panel_min_mapq", "ngs_comparison_panel_receipt_id")):
        raise ValueError(
            "comparison-panel paths are not accepted from ordinary NGS submissions; use a server-staged operator receipt"
        )
    reserved_params = ONT_SERVER_CONTROLLED_PROVENANCE_PARAMS | ONT_SERVER_CONTROLLED_RUNTIME_PARAMS
    submitted_server_controlled = sorted(
        reserved_params.intersection(submitted_params) - trusted_server_params
    )
    if submitted_server_controlled:
        raise ValueError(
            "server-controlled ONT provenance/runtime parameters cannot be supplied by a generic submit request: "
            + ", ".join(submitted_server_controlled)
        )
    if (
        request.source_instrument_run_id
        and "source_instrument_run_id" not in trusted_server_params
    ):
        raise ValueError(
            "server-controlled ONT instrument snapshot authority cannot be supplied by a generic submit request"
        )

    spec = get_ont_workflow_spec(canonical_id)
    # Remote defaults must not inherit the controller installation's CPU choice.
    # Explicit operator devices survive placement and normalization unchanged.
    if request.execution_target_id and not submitted_params.get("dorado_device"):
        submitted_params["dorado_device"] = "cuda:0"
    params = normalize_ont_launch_params(canonical_id, submitted_params)
    path_contract = {
        "pod5_dir": True,
        "bam_path": False,
        "fastq_path": False,
        "reference_fasta": False,
        "wf_clone_primers": False,
        "wf_clone_insert_reference": False,
        "wf_clone_host_reference": False,
        "wf_clone_regions_bedfile": False,
        "sample_sheet": False,
        "duplex_pairs": False,
    }
    for key, is_directory in path_contract.items():
        if str(params.get(key) or "").strip():
            if key == "reference_fasta" and trusted_reference_fasta is not None:
                trusted_path = str(trusted_reference_fasta)
                if str(params[key]) != trusted_path or not trusted_reference_fasta.is_file():
                    raise ValueError("managed reference path does not match server-resolved authority")
                params[key] = trusted_path
                continue
            confined_path = confine_submitted_path(
                params[key], key, directory=is_directory, allow_results=key in trusted_result_paths
            )
            if key == "reference_fasta":
                confined_reference = Path(confined_path)
                managed_root = get_molbio_ngs_reference_root().resolve()
                if confined_reference == managed_root or managed_root in confined_reference.parents:
                    raise ValueError(
                        "managed reference paths cannot be supplied by ordinary callers; use managed_reference identities"
                    )
            params[key] = confined_path
    input_mode, input_path = validate_ont_input_contract(canonical_id, params)
    reference_raw = str(params.get("reference_fasta") or "").strip()
    if reference_raw:
        reference_path = Path(reference_raw).expanduser()
        if reference_path.is_file():
            digest, reference_length = normalized_fasta_sequence_identity(reference_path)
            params["reference_sequence_sha256"] = digest
            if "expected_plasmid_size" in params:
                resolve_expected_plasmid_size(params, reference_length)
    model_mode = mode_for_ont_workflow(canonical_id)
    params["ont_request_workflow_id"] = workflow_id
    params["ont_workflow_id"] = canonical_id
    params["ont_model_mode"] = model_mode
    params["ont_input_mode"] = input_mode
    params["ont_input_provenance"] = {
        "mode": input_mode,
        "path": input_path,
        "source": "submitted_path",
    }
    if request.source_instrument_run_id:
        params["source_instrument_run_id"] = request.source_instrument_run_id

    job_kwargs: dict[str, Any] = {
        "name": safe_ont_job_name(request.name, f"{spec.display_name} analysis"),
        "model_id": "nanopore",
        "mode": model_mode,
        "params": params,
        "pinned_gpu": request.pinned_gpu,
        "launch_context_id": current_launch_context_id.get(),
        "execution_plan_approval": request.execution_plan_approval,
        "execution_policy": request.execution_policy if request.execution_policy is not None else ExecutionPolicy(),
    }
    if "execution_target_id" in request.model_fields_set:
        job_kwargs["execution_target_id"] = request.execution_target_id
    return JobCreate(**job_kwargs)
