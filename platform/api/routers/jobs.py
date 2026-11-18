"""
Jobs API router - Create, list, cancel pipeline jobs.
"""

from routers.job_stages import (
    _iter_saved_review_filter_sets,
    _normalize_design_ids,
)

from routers.job_results import (
    get_job,
)

from functools import partial
from services.job_control import resubmit_failed_job, resume_workflow_job

from services.job_control import (
    _resume_defaults_from_awaiting_payload,
)

from services.job_submission import (
    _candidate_child_batch_aliases,
    _resolve_resume_child_batch_name,
    _retain_ont_replay_lineage,
)

from services.job_requests import (
    ResubmitJobRequest,
    ResumeJobRequest,
)

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query, Body, Request, Response
from fastapi.params import Depends as DependsParam
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update, or_, and_
from sqlalchemy.exc import OperationalError
from typing import Optional, List, Dict, Any, Literal
from types import SimpleNamespace
from copy import deepcopy
import asyncio
import uuid
import os
import hashlib
import logging
import json
import shutil
import random
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

from antibody_pipeline_contract import (
    normalize_antibody_artifact_class,
    normalize_antibody_pipeline_contract_version,
)
from database import (
    current_launch_context_id,
    get_session,
    Job,
    ExecutionTarget,
    Design,
    FrustraMPNNResult,
    ConformationalMappingRequest,
)
from experiment_database import get_experiment_session
from services.job_stage_progress import project_execution_stages
from services.result_contracts import build_review_artifact_manifest, resolve_result_contract
from paths import (
    get_code_root,
    get_data_root,
    get_results_dir,
    get_work_dir,
    resolve_allowed_path,
    resolve_runtime_data_path,
    to_allowed_relative,
)
from services.binder_source_materialization import StructureSourceRequest
from runtime_policy import (
    acquire_workflow_mutation_lease,
    run_with_workflow_mutation_lease,
)
from schemas import ExecutionPolicy, JobCreate, JobResponse, JobList, JobSummaryResponse, JobSummaryList, JobStatus
from schemas import BinderNavigationJob, BinderNavigationJobList, BC2NavigationResponse
from services.binder_navigation_jobs import navigation_columns, navigation_job
from services.job_control import cancel_job_lineage, reject_generic_md_lifecycle_control
from services import alignment_access, ont_submission_trust, stage_reporting, ont_ngs_contract
from services.proteinbase_importer import import_proteinbase_bundle
from services.rfd3_generation import (
    file_sha256,
)
from services.global_experiments.launch_contexts import (
    LaunchContextError,
    resolve_launch_context,
    validate_bound_job_request,
    workflow_pinned_gpu,
)
from services.frustrampnn.settings import (
    FrustraMPNNRequestedSettings,
    default_settings as default_frustrampnn_settings,
    validate_complete_requested_settings,
)

from model_registry import get_registry
from services.stage_review import (
    REVIEWABLE_STAGES,
    ensure_stage_review_rows,
    gate_file_for_stage,
    has_stage_gate,
    load_review_gate_snapshot,
    nextflow_history_status,
    nextflow_history_status_for_run_dir,
    resolve_nextflow_run_dir,
    refresh_gate_payload,
)
from services.job_requests import (
    JobExecutionPlanPreview,
    _coerce_nonempty_text,
    _dedupe_preserve_order,
    _execution_plan_preview,
    _is_antibody_launch,
    _normalize_antibody_job_params,
    _normalize_antibody_runtime_paths,
    _normalize_boltz_cp_params_for_validation,
    _normalize_nanopore_runtime_paths,
    _normalize_structure_geometry_params,
    _normalize_structure_prediction_pred_method,
    _normalize_structure_runtime_paths,
    _selection_manifest_path,
    _should_normalize_antibody_job_params,
)
from services.job_readback import (
    _public_job_output_dir,
    _public_job_params,
    _resolve_requested_design_count,
    _resolve_stage_state_for_response,
    _saved_review_filter_entries,
    _serialized_saved_review_filter_sets,
    read_job,
    resolve_output_dir,
)
from services.job_readback import (
    SavedReviewFilterSet,
)
from services.job_control import (
    _load_remaining_output_dir_refs,
    _plan_output_dir_cleanup,
)
from services.binder_continuation import (
    AA_CODES,
    _awaiting_stage_to_resume_hint,
    _build_antibody_iteration_job,
    _build_selected_loop_scope,
    _create_antibody_selection_dir,
    _create_generic_selection_dir,
    _derive_source_stage_payload,
    _extract_chain_records_from_pdb,
    _is_protein_local_redesign_job,
    _materialize_antibody_selection,
    _materialize_protein_local_selection,
    _native_gate_resume_params,
    _prune_iteration_params,
    _resolve_antibody_root_job,
    _resolve_design_structure_path,
    _should_spawn_antibody_refinement_on_resume,
    _validate_selected_design_owners,
    _write_selection_manifest,
)
from services.job_submission import (
    _ensure_job_resume_identity,
    preview_submission,
    _raise_if_workflow_launches_disabled,
    _require_prepared_remote_review,
    _validate_antibody_runtime_paths,
    _validate_fampnn_checkpoint_requirements,
    _validate_protenix_checkpoint_requirements,
    _validate_protenix_template_requirements,
    submit_job,
)
async def _visible_job_path(request: Request, session: AsyncSession = Depends(get_session)):
    from services.job_control import visible_job_clause
    for key in ("job_id", "parent_id"):
        identifier = request.path_params.get(key)
        if identifier and await session.scalar(select(Job.id).where(
                Job.id == identifier, ~visible_job_clause())):
            raise HTTPException(status_code=404, detail="Job not found")


router = APIRouter(dependencies=[Depends(_visible_job_path)])

# Project root for resolving code-relative paths
BOLTZ_ITERATION_FORWARD_KEYS = (
    "boltz_use_msa",
    "boltz_sampling_steps",
    "boltz_recycling_steps",
    "boltz_num_samples",
    "boltz_diffusion_samples",
    "boltz_max_parallel_samples",
    "boltz_use_potentials",
    "boltz_step_scale",
    "boltz_method",
    "boltz_predict_affinity",
    "boltz_sampling_steps_affinity",
    "boltz_diffusion_samples_affinity",
    "boltz_affinity_mw_correction",
    "boltz_anchor_target",
    "boltz_anchor_strict",
    "boltz_target_geometry_mode",
    "boltz_extra_config",
    "msa_preset",
    "msa_use_gpu",
    "msa_local_db",
    "msa_cache_dir",
    "msa_threads",
    "colabfold_api_host",
    "colabfold_api_min_interval",
    "colabfold_api_poll_interval",
    "msa_gpu_mode",
    "msa_gpu_threshold",
    "msa_preferred_gpus",
    "msa_excluded_gpus",
    "msa_gpu_server_mode",
    "msa_gpu_server_wait_timeout",
    "msa_gpu_server_db_load_mode",
    "msa_gpu_server_startup_wait",
)
def _copy_present_params(source: Dict[str, Any], dest: Dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        if key in source:
            dest[key] = source[key]


class ContinueProteinLocalReviewRequest(BaseModel):
    """Continue a paused protein local redesign review using a selected subset."""
    design_ids: List[str] = Field(default_factory=list)
    name_suffix: Optional[str] = None


from schemas import CandidateRef


class ProteinBaseBundleImportRequest(BaseModel):
    """Request payload for importing a ProteinBase JSONL bundle into the job/design viewer."""
    bundle_path: str = Field(..., min_length=1)
    dataset_name: str = Field(..., min_length=1)
    job_name: Optional[str] = Field(default=None)


class AntibodyCdrIndelConfig(BaseModel):
    """Configuration for viewer-launched CDR indel rounds."""
    loop_ids: List[str] = Field(default_factory=list)
    variants_per_design: int = Field(default=8, ge=1, le=200)
    allow_insertions: bool = True
    allow_deletions: bool = True
    indel_sizes: List[int] = Field(default_factory=lambda: [1])
    indel_probability: float = Field(default=1.0, ge=0.0, le=1.0)
    allowed_aas: List[str] = Field(default_factory=list)
    blocked_aas: List[str] = Field(default_factory=list)
    predictor: str = Field(default="protenix")
    msa_provider: str = Field(default="colabfold_api")


class ManualMutagenesisConfig(BaseModel):
    """Configuration for explicit user-provided mutation rounds."""
    chain_id: Optional[str] = None
    mutation_sets: List[str] = Field(default_factory=list)
    predictor: str = Field(default="protenix")
    msa_provider: str = Field(default="colabfold_api")


class AntibodyIterationLaunchRequest(BaseModel):
    """Launch a new antibody round from selected design structures."""
    source_job_id: str = Field(..., min_length=1)
    design_ids: List[str] = Field(default_factory=list)
    review_filter_set_id: Optional[str] = None
    action: str = Field(..., min_length=1)
    name_suffix: Optional[str] = None
    param_overrides: Dict[str, Any] = Field(default_factory=dict)
    cdr_indel_config: Optional[AntibodyCdrIndelConfig] = None
    manual_mutagenesis_config: Optional[ManualMutagenesisConfig] = None
    frustrampnn_settings: Optional[FrustraMPNNRequestedSettings] = None

    @field_validator("frustrampnn_settings", mode="before")
    @classmethod
    def _complete_frustrampnn_settings(
        cls,
        value: Any,
    ) -> Optional[FrustraMPNNRequestedSettings]:
        if value is None:
            return None
        return validate_complete_requested_settings(value)


class AntibodyIterationLaunchResponse(BaseModel):
    """Response for viewer-driven antibody iteration actions."""

    model_config = ConfigDict(extra="forbid")

    message: str
    action: str
    source_job_id: str
    root_job_id: str
    selection_dir: str
    selected_design_count: int
    launched_job: JobResponse
    launched_jobs: List[JobResponse] = Field(default_factory=list)
    fanout_id: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ManualMutagenesisLaunchRequest(BaseModel):
    """Launch explicit manual mutation sets from selected structures."""
    source_job_id: str = Field(..., min_length=1)
    design_ids: List[str] = Field(default_factory=list)
    review_filter_set_id: Optional[str] = None
    config: ManualMutagenesisConfig
    name_suffix: Optional[str] = None
    param_overrides: Dict[str, Any] = Field(default_factory=dict)


class ResequenceLaunchRequest(BaseModel):
    """Resequence and validate exact structures from any generator."""
    sources: List[StructureSourceRequest] = Field(default_factory=list, max_length=500)
    backbone_input_pdbs: Optional[str] = None
    retained_params: Dict[str, Any] = Field(default_factory=dict)
    execution_target_id: Optional[str] = None
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    launch_context_id: Optional[str] = None
    seq_method: Literal["fampnn", "mpnn", "ligandmpnn", "caliby", "protonpottsmpnn"] = "fampnn"
    designer_mode: Optional[str] = None
    designer_settings: Dict[str, Any] = Field(default_factory=dict)
    validator_settings: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    design_chains: str = Field(..., min_length=1)
    fixed_positions: str = ""
    designable_positions: str = ""
    structure_validators: List[str] = Field(default_factory=lambda: ["protenix_v2"])
    msa_provider: Literal["colabfold_api", "neurosnap_api"] = "colabfold_api"
    name: str = Field("resequence", min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_source(self):
        if not self.sources and not str(self.backbone_input_pdbs or "").strip():
            raise ValueError("Resequence requires structures or a retained basket")
        return self


class ManualMutagenesisLaunchResponse(BaseModel):
    """Response for viewer-driven manual mutation launches."""
    message: str
    source_job_id: str
    selected_design_count: int
    variant_count: int
    launched_job: JobResponse


def _resolve_saved_review_filter_set(
    review_filter_set_id: Optional[str],
    candidate_jobs: List[Optional[Job]],
) -> Optional[SavedReviewFilterSet]:
    filter_set_id = str(review_filter_set_id or "").strip()
    if not filter_set_id:
        return None

    seen_job_ids: set[str] = set()
    for job in candidate_jobs:
        if job is None or job.id in seen_job_ids:
            continue
        seen_job_ids.add(job.id)
        for saved_set in _iter_saved_review_filter_sets(job):
            if saved_set.id == filter_set_id:
                return saved_set

    raise HTTPException(status_code=404, detail="Saved review dataset not found.")


def _resolve_launch_design_ids(
    requested_design_ids: List[str],
    saved_filter_set: Optional[SavedReviewFilterSet],
) -> List[str]:
    design_ids = _normalize_design_ids(requested_design_ids)
    if design_ids:
        return design_ids
    if saved_filter_set is None:
        return []

    saved_design_ids = _normalize_design_ids(saved_filter_set.design_ids)
    if not saved_design_ids:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Saved review dataset '{saved_filter_set.name}' does not have frozen design membership. "
                "Re-save the dataset from the review UI before launching it."
            ),
        )
    return saved_design_ids


AA_CODES_REVERSE = {value: key for key, value in AA_CODES.items()}
PDB_BACKBONE_ATOMS = {"N", "CA", "C", "O", "OXT"}

STANDARD_AMINO_ACIDS = list("ACDEFGHIKLMNPQRSTVWY")
DEFAULT_CDR_POSITION_RANGES = {
    "H1": (27, 38),
    "H2": (56, 65),
    "H3": (105, 117),
    "L1": (27, 38),
    "L2": (56, 65),
    "L3": (105, 117),
}


def _parse_chain_list(raw_value: Any) -> List[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        values = raw_value
    else:
        values = [part.strip() for part in str(raw_value).split(",")]
    return [str(value).strip().upper() for value in values if str(value).strip()]


def _extract_chain_records_from_structure(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    if path.suffix.lower() not in {'.cif', '.mmcif'}:
        return _extract_chain_records_from_pdb(path)
    from Bio.PDB.MMCIF2Dict import MMCIF2Dict

    data = MMCIF2Dict(str(path))
    def column(*names: str) -> list[str]:
        for name in names:
            if name in data:
                return data[name]
        return []
    atoms = column('_atom_site.auth_atom_id', '_atom_site.label_atom_id')
    residues = column('_atom_site.auth_comp_id', '_atom_site.label_comp_id')
    chains = column('_atom_site.auth_asym_id', '_atom_site.label_asym_id')
    numbers = column('_atom_site.auth_seq_id', '_atom_site.label_seq_id')
    insertions = column('_atom_site.pdbx_PDB_ins_code')
    groups = column('_atom_site.group_PDB')
    if not (len(atoms) == len(residues) == len(chains) == len(numbers) == len(groups)):
        return {}
    if not insertions:
        insertions = ['?'] * len(atoms)
    if len(insertions) != len(atoms):
        return {}
    records: Dict[str, List[Dict[str, Any]]] = {}
    seen: Dict[str, set[tuple[int, str]]] = {}
    for group, atom, residue, chain, number, insertion in zip(
            groups, atoms, residues, chains, numbers, insertions):
        if group != 'ATOM' or atom != 'CA' or residue not in AA_CODES:
            continue
        try:
            resseq = int(number)
        except ValueError:
            continue
        chain_id = chain.upper()
        icode = '' if insertion in {'.', '?'} else insertion
        key = (resseq, icode)
        if key in seen.setdefault(chain_id, set()):
            continue
        seen[chain_id].add(key)
        records.setdefault(chain_id, []).append({'resseq': resseq, 'icode': icode,
                                                  'aa': AA_CODES[residue]})
    return records


def _resolve_loop_region_map(root_job: Job) -> Dict[str, tuple[int, int]]:
    params = root_job.params if isinstance(root_job.params, dict) else {}
    region_map: Dict[str, tuple[int, int]] = {}

    manual_defs = params.get("manual_cdr_definitions")
    if isinstance(manual_defs, list):
        for entry in manual_defs:
            if not isinstance(entry, dict):
                continue
            loop_id = str(entry.get("id") or "").strip().upper()
            residues = entry.get("residues") or []
            residue_numbers: List[int] = []
            for residue in residues:
                match = str(residue).strip().upper()
                parsed = match[1:] if match[:1].isalpha() else match
                if parsed.isdigit():
                    residue_numbers.append(int(parsed))
            if loop_id and residue_numbers:
                region_map[loop_id] = (min(residue_numbers), max(residue_numbers))

    return region_map


def _resolve_loop_target_chain(root_job: Job, loop_ids: List[str], available_chain_ids: List[str]) -> str:
    loop_prefixes = {loop_id[:1].upper() for loop_id in loop_ids if loop_id}
    if not loop_prefixes:
        raise HTTPException(status_code=422, detail="At least one CDR loop must be selected for indel generation.")
    if len(loop_prefixes) != 1:
        raise HTTPException(
            status_code=422,
            detail="CDR indel rounds currently require all selected loops to belong to the same chain family (all H or all L).",
        )

    prefix = next(iter(loop_prefixes))
    antibody_chains = _parse_chain_list((root_job.params or {}).get("antibody_chains") if isinstance(root_job.params, dict) else None)
    candidate = None
    if prefix == "H" and antibody_chains:
        candidate = antibody_chains[0]
    elif prefix == "L" and len(antibody_chains) > 1:
        candidate = antibody_chains[1]

    if candidate and candidate in available_chain_ids:
        return candidate

    if len(available_chain_ids) == 1:
        return available_chain_ids[0]

    for chain_id in available_chain_ids:
        if chain_id.startswith(prefix):
            return chain_id

    raise HTTPException(
        status_code=422,
        detail=f"Could not resolve binder chain for selected loops {', '.join(loop_ids)} from chains {', '.join(available_chain_ids)}.",
    )


def _build_mutation_regions(
    chain_records: List[Dict[str, Any]],
    region_map: Dict[str, tuple[int, int]],
    loop_ids: List[str],
) -> Dict[str, tuple[int, int]]:
    regions: Dict[str, tuple[int, int]] = {}
    for loop_id in loop_ids:
        loop_key = loop_id.strip().upper()
        bounds = region_map.get(loop_key) or DEFAULT_CDR_POSITION_RANGES.get(loop_key)
        if not bounds:
            continue
        start_resseq, end_resseq = bounds
        positions = [
            index
            for index, residue in enumerate(chain_records, start=1)
            if start_resseq <= int(residue["resseq"]) <= end_resseq
        ]
        if positions:
            regions[loop_key] = (min(positions), max(positions))
    return regions


def _detect_loop_sequence_regions(
    design_path: Path,
    binder_chain_id: str,
    loop_ids: List[str],
) -> Dict[str, tuple[int, int]]:
    try:
        from services.cdr_annotator import annotate_pdb
    except Exception:
        return {}

    prefixes = {loop_id[:1].upper() for loop_id in loop_ids if loop_id}
    if len(prefixes) != 1:
        return {}

    preferred_key = next(iter(prefixes))
    try:
        annotation = annotate_pdb(str(design_path), preferred_chains={preferred_key: binder_chain_id})
    except Exception:
        return {}
    if not annotation:
        return {}

    detected: Dict[str, tuple[int, int]] = {}
    for loop_id in loop_ids:
        attr_name = f"cdr_{loop_id.strip().lower()}_seq_range"
        seq_range = getattr(annotation, attr_name, None)
        if not seq_range or len(seq_range) != 2:
            continue
        try:
            start = int(seq_range[0]) + 1
            end = int(seq_range[1]) + 1
        except (TypeError, ValueError):
            continue
        if start > 0 and end >= start:
            detected[loop_id.strip().upper()] = (start, end)
    return detected


def _normalize_aa_pool(allowed_aas: List[str], blocked_aas: List[str]) -> List[str]:
    allowed = [aa.strip().upper() for aa in allowed_aas if aa and aa.strip().upper() in STANDARD_AMINO_ACIDS]
    blocked = {aa.strip().upper() for aa in blocked_aas if aa and aa.strip().upper() in STANDARD_AMINO_ACIDS}
    pool = allowed if allowed else STANDARD_AMINO_ACIDS
    filtered = [aa for aa in pool if aa not in blocked]
    return filtered or STANDARD_AMINO_ACIDS


MANUAL_MUTATION_TOKEN_RE = re.compile(r"^([A-Za-z])(\d+)([A-Za-z])$")


def _resolve_manual_mutagenesis_chain(
    requested_chain: Optional[str],
    source_job: Job,
    available_chain_ids: List[str],
) -> str:
    normalized_requested = str(requested_chain or "").strip().upper()
    if normalized_requested:
        if normalized_requested not in available_chain_ids:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Requested chain '{normalized_requested}' is not present in the selected structure. "
                    f"Available chains: {', '.join(available_chain_ids)}."
                ),
            )
        return normalized_requested

    antibody_chains = _parse_chain_list((source_job.params or {}).get("antibody_chains") if isinstance(source_job.params, dict) else None)
    for chain_id in antibody_chains:
        if chain_id in available_chain_ids:
            return chain_id

    antigen_chains = _parse_chain_list((source_job.params or {}).get("antigen_chains") if isinstance(source_job.params, dict) else None)
    non_antigen_candidates = [chain_id for chain_id in available_chain_ids if chain_id not in set(antigen_chains)]
    if antigen_chains and len(non_antigen_candidates) == 1:
        return non_antigen_candidates[0]

    if len(available_chain_ids) == 1:
        return available_chain_ids[0]

    raise HTTPException(
        status_code=422,
        detail=(
            "Selected designs contain multiple protein chains. Specify a chain ID for manual mutagenesis "
            f"(available: {', '.join(available_chain_ids)})."
        ),
    )


def _parse_manual_mutation_set(raw_mutation_set: str) -> List[Dict[str, Any]]:
    tokens = [
        token.strip().upper()
        for token in re.split(r"[\s,;]+", str(raw_mutation_set or "").strip())
        if token.strip()
    ]
    if not tokens:
        raise HTTPException(status_code=422, detail="Each manual mutation set must contain at least one substitution like S31Y.")

    parsed: List[Dict[str, Any]] = []
    seen_positions: set[int] = set()
    for token in tokens:
        match = MANUAL_MUTATION_TOKEN_RE.match(token)
        if not match:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported manual mutation token '{token}'. Use substitutions like S31Y or comma-separated sets like S31Y,K58R.",
            )
        from_aa, pos_raw, to_aa = match.groups()
        position = int(pos_raw)
        if from_aa not in STANDARD_AMINO_ACIDS or to_aa not in STANDARD_AMINO_ACIDS:
            raise HTTPException(status_code=422, detail=f"Mutation token '{token}' contains a non-standard amino acid code.")
        if from_aa == to_aa:
            raise HTTPException(status_code=422, detail=f"Mutation token '{token}' does not change the residue.")
        if position in seen_positions:
            raise HTTPException(status_code=422, detail=f"Manual mutation set '{raw_mutation_set}' mutates position {position} more than once.")
        seen_positions.add(position)
        parsed.append(
            {
                "from": from_aa,
                "to": to_aa,
                "position": position,
                "summary": f"{from_aa}{position}{to_aa}",
            }
        )

    return sorted(parsed, key=lambda entry: int(entry["position"]))


def _generate_manual_mutagenesis_variants(
    source_job: Job,
    designs: List[Design],
    config: ManualMutagenesisConfig,
) -> List[Dict[str, Any]]:
    raw_mutation_sets = [entry.strip() for entry in config.mutation_sets if isinstance(entry, str) and entry.strip()]
    if not raw_mutation_sets:
        raise HTTPException(status_code=422, detail="Add at least one manual mutation set before launching.")

    parsed_mutation_sets = [_parse_manual_mutation_set(entry) for entry in raw_mutation_sets]
    variants: List[Dict[str, Any]] = []

    for design in designs:
        design_path = _resolve_design_structure_path(design.pdb_path)
        chain_records = _extract_chain_records_from_structure(design_path)
        if not chain_records:
            raise HTTPException(status_code=422, detail=f"Could not extract protein chains from '{design.name}'.")

        available_chain_ids = list(chain_records.keys())
        binder_chain_id = _resolve_manual_mutagenesis_chain(config.chain_id, source_job, available_chain_ids)
        binder_records = chain_records.get(binder_chain_id) or []
        base_sequence = "".join(record["aa"] for record in binder_records)
        if not base_sequence:
            raise HTTPException(status_code=422, detail=f"Could not extract a mutable sequence for chain '{binder_chain_id}' in '{design.name}'.")

        for idx, mutation_set in enumerate(parsed_mutation_sets, start=1):
            seq_chars = list(base_sequence)
            mutation_meta: List[Dict[str, Any]] = []
            summary_tokens: List[str] = []
            fixed_spec_tokens: List[str] = []
            for mutation in mutation_set:
                position = int(mutation["position"])
                if position < 1 or position > len(seq_chars):
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"Mutation '{mutation['summary']}' is outside the length of chain '{binder_chain_id}' "
                            f"for '{design.name}' (length {len(seq_chars)})."
                        ),
                    )
                wildtype = seq_chars[position - 1]
                if wildtype != mutation["from"]:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"Mutation '{mutation['summary']}' does not match '{design.name}' chain '{binder_chain_id}'. "
                            f"Expected {wildtype}{position}{mutation['to']}."
                        ),
                    )
                seq_chars[position - 1] = mutation["to"]
                mutation_meta.append(
                    {
                        "type": "substitution",
                        "position": position,
                        "from": mutation["from"],
                        "to": mutation["to"],
                        "summary": mutation["summary"],
                    }
                )
                summary_tokens.append(mutation["summary"])
                try:
                    residue_record = binder_records[position - 1]
                except IndexError as exc:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Could not map mutation '{mutation['summary']}' onto '{design.name}' chain '{binder_chain_id}'.",
                    ) from exc
                fixed_spec_tokens.append(f"{binder_chain_id}{int(residue_record['resseq'])}")

            variant_sequence = "".join(seq_chars)
            complex_components = []
            for chain_id, records in chain_records.items():
                sequence = variant_sequence if chain_id == binder_chain_id else "".join(record["aa"] for record in records)
                complex_components.append(
                    {
                        "type": "protein",
                        "id": chain_id,
                        "sequence": sequence,
                    }
                )

            summary = "_".join(summary_tokens)
            variants.append(
                {
                    "name": f"{design.name}_{summary}_{idx}",
                    "sequence": variant_sequence,
                    "complex_components": complex_components,
                    "source_design_id": design.id,
                    "source_design_name": design.name,
                    "source_design_job_id": design.job_id,
                    "source_stage_family": design.stage_family,
                    "source_stage_mode": design.stage_mode,
                    "lineage_root_job_id": design.lineage_root_job_id,
                    "parent_design_id": design.parent_design_id,
                    "origin_design_id": design.origin_design_id,
                    "origin_backbone_design_id": design.origin_backbone_design_id,
                    "selected_loop_scope": design.selected_loop_scope,
                    "source_pdb_path": str(design_path),
                    "binder_chain_id": binder_chain_id,
                    "mutation": mutation_meta,
                    "locked_positions_spec": ",".join(fixed_spec_tokens),
                }
            )

    return variants


def _write_mutated_seed_pdb(
    source_path: Path,
    dest_path: Path,
    binder_chain_id: str,
    mutation_meta: List[Dict[str, Any]],
) -> None:
    chain_records = _extract_chain_records_from_pdb(source_path)
    binder_records = chain_records.get(binder_chain_id) or []
    if not binder_records:
        raise HTTPException(
            status_code=422,
            detail=f"Could not find chain '{binder_chain_id}' in seed structure '{source_path.name}'.",
        )

    residue_updates: Dict[tuple[int, str], str] = {}
    for mutation in mutation_meta:
        if str(mutation.get("type")).lower() != "substitution":
            continue
        position = int(mutation["position"])
        if position < 1 or position > len(binder_records):
            raise HTTPException(
                status_code=422,
                detail=f"Mutation position {position} is outside chain '{binder_chain_id}' in '{source_path.name}'.",
            )
        residue_record = binder_records[position - 1]
        to_three = AA_CODES_REVERSE.get(str(mutation["to"]).upper())
        if not to_three:
            raise HTTPException(status_code=422, detail=f"Unsupported amino acid '{mutation['to']}' in mutation seed.")
        residue_updates[(int(residue_record["resseq"]), str(residue_record.get("icode") or ""))] = to_three

    with source_path.open() as src, dest_path.open("w") as dst:
        for line in src:
            if line.startswith(("ATOM  ", "HETATM")) and len(line) >= 27:
                chain_id = (line[21].strip() or "_").upper()
                if chain_id == binder_chain_id:
                    try:
                        resseq = int(line[22:26].strip())
                    except ValueError:
                        resseq = None
                    icode = line[26].strip()
                    if resseq is not None and (resseq, icode) in residue_updates:
                        atom_name = line[12:16].strip()
                        if atom_name not in PDB_BACKBONE_ATOMS:
                            continue
                        resname = residue_updates[(resseq, icode)]
                        line = f"{line[:17]}{resname:>3}{line[20:]}"
            dst.write(line)


def _materialize_substitution_seed_selection(
    root_job: Job,
    source_job: Job,
    designs: List[Design],
    variants: List[Dict[str, Any]],
    action: str,
) -> tuple[Path, Path]:
    selection_dir = _create_antibody_selection_dir(action)
    manifest_items: List[Dict[str, Any]] = []
    fixed_positions_by_pdb: Dict[str, str] = {}

    for idx, variant in enumerate(variants, start=1):
        source_path = Path(str(variant["source_pdb_path"]))
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(variant["name"]))[:120]
        dest_path = selection_dir / f"{idx:03d}_{safe_name}.pdb"
        _write_mutated_seed_pdb(
            source_path=source_path,
            dest_path=dest_path,
            binder_chain_id=str(variant["binder_chain_id"]),
            mutation_meta=list(variant.get("mutation") or []),
        )
        manifest_items.append({
            "variant_name": variant["name"],
            "design_id": variant.get("source_design_id"),
            "design_name": variant.get("source_design_name"),
            "design_job_id": variant.get("source_design_job_id"),
            "design_stage_family": variant.get("source_stage_family"),
            "design_stage_mode": variant.get("source_stage_mode"),
            "lineage_root_job_id": variant.get("lineage_root_job_id"),
            "parent_design_id": variant.get("parent_design_id"),
            "origin_design_id": variant.get("origin_design_id"),
            "origin_backbone_design_id": variant.get("origin_backbone_design_id"),
            "source_design_name": variant.get("source_design_name"),
            "source_pdb_path": str(source_path),
            "selection_pdb_path": str(dest_path),
            "selected_loop_scope": variant.get("selected_loop_scope"),
            "binder_chain_id": variant.get("binder_chain_id"),
            "mutation": variant.get("mutation"),
        })
        if variant.get("locked_positions_spec"):
            fixed_positions_by_pdb[dest_path.stem] = str(variant["locked_positions_spec"])

    _write_selection_manifest(
        selection_dir=selection_dir,
        root_job=root_job,
        source_job=source_job,
        action=action,
        manifest_items=manifest_items,
        source_stage_payload=_derive_source_stage_payload(source_job, designs, selection_dir),
        fixed_positions_by_pdb=fixed_positions_by_pdb,
    )
    return selection_dir, selection_dir / "mutation_fixed_positions.json"


def _build_manual_mutagenesis_iteration_job(
    source_job: Job,
    designs: List[Design],
    config: ManualMutagenesisConfig,
    name_suffix: Optional[str],
    param_overrides: Dict[str, Any],
) -> tuple[JobCreate, int, str]:
    predictor = str(config.predictor or "protenix").strip().lower()
    if predictor == "boltz":
        predictor = "boltz2"
    if predictor not in {"protenix", "boltz2"}:
        raise HTTPException(status_code=422, detail="Manual mutagenesis currently supports predictor='protenix' or 'boltz2'.")

    variants = _generate_manual_mutagenesis_variants(source_job, designs, config)

    base_params = _prune_iteration_params(source_job.params if isinstance(source_job.params, dict) else {})
    requested_msa_provider = str(config.msa_provider or "local").strip().lower()
    if requested_msa_provider not in {"local", "colabfold_api"}:
        raise HTTPException(status_code=422, detail="msa_provider must be 'local' or 'colabfold_api'.")

    from services.msa_policy import resolve_search_backend
    try:
        effective_msa_provider = resolve_search_backend(requested_msa_provider)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    launch_params: Dict[str, Any] = {
        "pred_method": "protenix" if predictor == "protenix" else "boltz",
        "mutagenesis_variants": variants,
        "msa_force_refresh": True,
        "msa_provider": effective_msa_provider,
        "pinned_gpus": base_params.get("pinned_gpus"),
        "lock_gpus": base_params.get("lock_gpus"),
        "iteration_source_job_id": source_job.id,
        "iteration_source_root_job_id": (
            ((source_job.params or {}).get("iteration_source_root_job_id") if isinstance(source_job.params, dict) else None)
            or source_job.parent_job_id
            or source_job.id
        ),
        "iteration_source_design_ids": [design.id for design in designs],
        "iteration_action": "manual_mutagenesis_round",
        "manual_mutagenesis_config": config.model_dump(),
        "run_frustrampnn": False,
    }

    if predictor == "protenix":
        for key in (
            "protenix_model_weights",
            "protenix_seeds",
            "protenix_n_sample",
            "protenix_n_step",
            "protenix_n_cycle",
            "protenix_use_msa",
            "protenix_use_template",
            "protenix_enable_cache",
            "protenix_enable_fusion",
            "protenix_msa_backend",
            "protenix_auto_oom_retry",
            "protenix_oom_retry_attempts",
            "msa_preset",
            "msa_use_gpu",
            "msa_local_db",
            "msa_cache_dir",
            "msa_threads",
            "colabfold_api_host",
            "colabfold_api_min_interval",
            "colabfold_api_poll_interval",
            "msa_gpu_mode",
            "msa_gpu_threshold",
            "msa_preferred_gpus",
            "msa_excluded_gpus",
            "msa_gpu_server_mode",
            "msa_gpu_server_wait_timeout",
            "msa_gpu_server_db_load_mode",
            "msa_gpu_server_startup_wait",
        ):
            if key in base_params:
                launch_params[key] = base_params[key]
    else:
        _copy_present_params(base_params, launch_params, BOLTZ_ITERATION_FORWARD_KEYS)

    if param_overrides:
        launch_params.update(dict(param_overrides))

    suffix = name_suffix.strip() if isinstance(name_suffix, str) and name_suffix.strip() else "manual_mutagenesis"
    model_id = "protenix" if predictor == "protenix" else "boltz2"
    launch_request = JobCreate(
        name=f"{source_job.name}_{suffix}",
        model_id=model_id,
        mode="complex",
        params=launch_params,
        execution_target_id=source_job.execution_target_id,
        execution_policy=ExecutionPolicy.from_params(source_job.params),
        pinned_gpu=None,
    )
    message_note = ""
    if requested_msa_provider == "colabfold_api":
        message_note = " ColabFold API batch admission remains blocked; no local fallback."
    return launch_request, len(variants), message_note


def _build_manual_mutagenesis_seeded_refinement_job(
    root_job: Job,
    source_job: Job,
    designs: List[Design],
    config: ManualMutagenesisConfig,
    name_suffix: Optional[str],
    param_overrides: Dict[str, Any],
) -> tuple[JobCreate, int, str]:
    variants = _generate_manual_mutagenesis_variants(source_job, designs, config)
    selection_dir, fixed_json_path = _materialize_substitution_seed_selection(
        root_job=root_job,
        source_job=source_job,
        designs=designs,
        variants=variants,
        action="mutation_seeded_refinement",
    )
    launch_request = _build_antibody_iteration_job(
        root_job=root_job,
        source_job=source_job,
        action="ui_refinement",
        selection_dir=selection_dir,
        design_ids=[str(variant.get("source_design_id")) for variant in variants if variant.get("source_design_id")],
        name_suffix=name_suffix or "mutation_seeded_refinement",
        param_overrides={
            **dict(param_overrides or {}),
            "manual_mutation_mode": "seeded_refinement",
            "manual_mutation_method": "explicit_substitutions",
            "manual_mutation_fixed_positions_json": str(fixed_json_path),
        },
        selected_designs=designs,
    )
    return launch_request, len(variants), ""


def _generate_cdr_indel_variants(
    base_sequence: str,
    base_name: str,
    regions: List[tuple[int, int]],
    config: AntibodyCdrIndelConfig,
) -> List[Dict[str, Any]]:
    if not base_sequence:
        raise HTTPException(status_code=422, detail=f"Selected design '{base_name}' is missing a binder sequence.")
    if not regions:
        raise HTTPException(status_code=422, detail=f"Could not map selected CDR loops onto '{base_name}'.")
    if not config.allow_insertions and not config.allow_deletions:
        raise HTTPException(status_code=422, detail="Enable insertions, deletions, or both for a CDR indel round.")

    indel_sizes = sorted({size for size in config.indel_sizes if isinstance(size, int) and size > 0})
    if not indel_sizes:
        raise HTTPException(status_code=422, detail="At least one positive indel size is required.")

    position_pool = sorted({pos for start, end in regions for pos in range(start, end + 1)})
    if not position_pool:
        raise HTTPException(status_code=422, detail=f"No mutable positions were found in the requested CDR loops for '{base_name}'.")

    aa_pool = _normalize_aa_pool(config.allowed_aas, config.blocked_aas)
    variants: List[Dict[str, Any]] = []
    seen_sequences = {base_sequence}

    max_attempts = max(config.variants_per_design * 20, 50)
    attempts = 0
    while len(variants) < config.variants_per_design and attempts < max_attempts:
        attempts += 1
        seq_chars = list(base_sequence)
        mutation_meta: Dict[str, Any] | None = None
        choose_indel = random.random() <= config.indel_probability
        if not choose_indel:
            continue

        allowed_ops: List[str] = []
        if config.allow_insertions:
            allowed_ops.append("insertion")
        if config.allow_deletions:
            allowed_ops.append("deletion")
        op = random.choice(allowed_ops)
        size = random.choice(indel_sizes)

        if op == "insertion":
            pos = random.choice(position_pool)
            inserted = "".join(random.choice(aa_pool) for _ in range(size))
            seq_chars[pos:pos] = list(inserted)
            mutation_meta = {
                "type": "insertion",
                "position": pos,
                "from": "",
                "to": inserted,
                "summary": f"ins{pos}{inserted}",
            }
        else:
            delete_candidates = [
                pos
                for pos in position_pool
                if any(start <= pos and (pos + size - 1) <= end for start, end in regions)
                and (pos + size - 1) <= len(base_sequence)
            ]
            if not delete_candidates:
                continue
            pos = random.choice(delete_candidates)
            deleted = "".join(seq_chars[pos - 1:pos - 1 + size])
            del seq_chars[pos - 1:pos - 1 + size]
            mutation_meta = {
                "type": "deletion",
                "position": pos,
                "from": deleted,
                "to": "",
                "summary": f"del{pos}-{pos + size - 1}" if size > 1 else f"del{pos}{deleted}",
            }

        variant_sequence = "".join(seq_chars)
        if not variant_sequence or variant_sequence in seen_sequences:
            continue

        seen_sequences.add(variant_sequence)
        variants.append(
            {
                "name": f"{base_name}_{mutation_meta['summary']}_{len(variants) + 1}",
                "sequence": variant_sequence,
                "mutation": mutation_meta,
            }
        )

    if not variants:
        raise HTTPException(
            status_code=422,
            detail=f"Could not generate unique CDR indel variants for '{base_name}' with the requested settings.",
        )

    return variants


def _build_cdr_indel_iteration_job(
    root_job: Job,
    source_job: Job,
    designs: List[Design],
    config: AntibodyCdrIndelConfig,
    name_suffix: Optional[str],
    param_overrides: Dict[str, Any],
    *,
    seed_refinement_trigger: Optional[Dict[str, Any]] = None,
    iteration_action: str = "cdr_indel_round",
) -> tuple[JobCreate, int, str]:
    loop_ids = [loop_id.strip().upper() for loop_id in config.loop_ids if isinstance(loop_id, str) and loop_id.strip()]
    if not loop_ids:
        raise HTTPException(status_code=422, detail="Select at least one CDR loop for the indel round.")

    predictor = str(config.predictor or "protenix").strip().lower()
    if predictor not in {"protenix", "boltz2"}:
        raise HTTPException(status_code=422, detail="CDR indel rounds currently support predictor='protenix' or 'boltz2'.")

    msa_provider = str(config.msa_provider or "local").strip().lower()
    if msa_provider not in {"local", "colabfold_api"}:
        raise HTTPException(status_code=422, detail="msa_provider must be 'local' or 'colabfold_api'.")

    region_map = _resolve_loop_region_map(root_job)
    variants: List[Dict[str, Any]] = []

    for design in designs:
        design_path = _resolve_design_structure_path(design.pdb_path)
        chain_records = _extract_chain_records_from_structure(design_path)
        if not chain_records:
            raise HTTPException(status_code=422, detail=f"Could not extract chain sequences from '{design.name}'.")

        available_chain_ids = list(chain_records.keys())
        binder_chain_id = _resolve_loop_target_chain(root_job, loop_ids, available_chain_ids)
        binder_records = chain_records.get(binder_chain_id) or []
        fallback_regions = _build_mutation_regions(binder_records, region_map, loop_ids)
        detected_regions = _detect_loop_sequence_regions(design_path, binder_chain_id, loop_ids)
        mutation_regions = [
            detected_regions.get(loop_id) or fallback_regions.get(loop_id)
            for loop_id in loop_ids
            if detected_regions.get(loop_id) or fallback_regions.get(loop_id)
        ]
        if not mutation_regions:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Could not resolve CDR regions for '{design.name}'. "
                    "Provide manual CDR residue definitions or use a structure with detectable antibody numbering."
                ),
            )
        base_sequence = "".join(record["aa"] for record in binder_records)

        design_variants = _generate_cdr_indel_variants(
            base_sequence=base_sequence,
            base_name=design.name,
            regions=mutation_regions,
            config=config,
        )

        for variant in design_variants:
            complex_components = []
            for chain_id, records in chain_records.items():
                sequence = variant["sequence"] if chain_id == binder_chain_id else "".join(record["aa"] for record in records)
                complex_components.append({
                    "type": "protein",
                    "id": chain_id,
                    "sequence": sequence,
                })
            variants.append(
                {
                    "name": variant["name"],
                    "sequence": variant["sequence"],
                    "complex_components": complex_components,
                    "source_design_id": design.id,
                    "source_design_name": design.name,
                    "binder_chain_id": binder_chain_id,
                    "loop_ids": loop_ids,
                    "mutation": variant["mutation"],
                }
            )

    if not variants:
        raise HTTPException(status_code=422, detail="No CDR indel variants were generated from the selected designs.")

    from services.msa_policy import resolve_search_backend
    try:
        effective_msa_provider = resolve_search_backend(msa_provider)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    base_params = _prune_iteration_params(root_job.params if isinstance(root_job.params, dict) else {})
    launch_params: Dict[str, Any] = {
        "pred_method": "protenix" if predictor == "protenix" else "boltz",
        "mutagenesis_variants": variants,
        "msa_force_refresh": True,
        "msa_provider": effective_msa_provider,
        "pinned_gpus": base_params.get("pinned_gpus"),
        "lock_gpus": base_params.get("lock_gpus"),
        "iteration_source_job_id": source_job.id,
        "iteration_source_root_job_id": root_job.id,
        "iteration_source_design_ids": [design.id for design in designs],
        "iteration_action": iteration_action,
        "cdr_indel_config": config.model_dump(),
        "run_frustrampnn": False,
    }
    if seed_refinement_trigger:
        launch_params["mutation_seed_refinement_trigger"] = seed_refinement_trigger

    if predictor == "protenix":
        for key in (
            "protenix_model_weights",
            "protenix_seeds",
            "protenix_n_sample",
            "protenix_n_step",
            "protenix_n_cycle",
            "protenix_use_msa",
            "protenix_use_template",
            "protenix_enable_cache",
            "protenix_enable_fusion",
            "protenix_msa_backend",
            "protenix_auto_oom_retry",
            "protenix_oom_retry_attempts",
            "msa_preset",
            "msa_use_gpu",
            "msa_local_db",
            "msa_cache_dir",
            "msa_threads",
            "colabfold_api_host",
            "colabfold_api_min_interval",
            "colabfold_api_poll_interval",
            "msa_gpu_mode",
            "msa_gpu_threshold",
            "msa_preferred_gpus",
            "msa_excluded_gpus",
            "msa_gpu_server_mode",
            "msa_gpu_server_wait_timeout",
            "msa_gpu_server_db_load_mode",
            "msa_gpu_server_startup_wait",
        ):
            if key in base_params:
                launch_params[key] = base_params[key]
    else:
        _copy_present_params(base_params, launch_params, BOLTZ_ITERATION_FORWARD_KEYS)

    if param_overrides:
        cleaned_overrides = dict(param_overrides)
        for key in ("epitope_residues", "target_pdb"):
            value = cleaned_overrides.get(key)
            if value is None or (isinstance(value, str) and (not value.strip() or value.strip() == "refinement_mode")):
                cleaned_overrides.pop(key, None)
        if not cleaned_overrides.get("selected_residues"):
            cleaned_overrides.pop("selected_residues", None)
        launch_params.update(cleaned_overrides)

    suffix = name_suffix.strip() if isinstance(name_suffix, str) and name_suffix.strip() else "cdr_indel_round"
    model_id = "protenix" if predictor == "protenix" else "boltz2"
    launch_request = JobCreate(
        name=f"{root_job.name}_{suffix}",
        model_id=model_id,
        mode="complex",
        params=launch_params,
        execution_target_id=root_job.execution_target_id,
        execution_policy=ExecutionPolicy.from_params(root_job.params),
        pinned_gpu=None,
    )
    message_note = ""
    if msa_provider == "colabfold_api" and effective_msa_provider == "local":
        message_note = " ColabFold API was downgraded to local MSA because the indel round generated multiple variants."
    return launch_request, len(variants), message_note


def _build_cdr_indel_seeded_refinement_job(
    root_job: Job,
    source_job: Job,
    designs: List[Design],
    config: AntibodyCdrIndelConfig,
    name_suffix: Optional[str],
    param_overrides: Dict[str, Any],
) -> tuple[JobCreate, int, str]:
    trigger_payload = {
        "source_job_id": source_job.id,
        "root_job_id": root_job.id,
        "name_suffix": name_suffix or "mutation_seeded_refinement",
        "param_overrides": dict(param_overrides or {}),
        "manual_mutation_mode": "seeded_refinement",
        "manual_mutation_method": "cdr_indels",
    }
    return _build_cdr_indel_iteration_job(
        root_job=root_job,
        source_job=source_job,
        designs=designs,
        config=config,
        name_suffix=name_suffix or "mutation_seed_builder",
        param_overrides=param_overrides,
        seed_refinement_trigger=trigger_payload,
        iteration_action="mutation_seed_build",
    )


@router.post("/imports/proteinbase", response_model=JobResponse, status_code=201)
async def import_proteinbase_bundle_job(
    request: ProteinBaseBundleImportRequest,
    session: AsyncSession = Depends(get_session),
):
    """Import an uploaded ProteinBase CSV or JSONL bundle as a completed job."""
    try:
        resolved_bundle_path = resolve_allowed_path(request.bundle_path)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Access denied to this import bundle path") from exc

    if not resolved_bundle_path.exists():
        raise HTTPException(status_code=404, detail="Import bundle not found")
    if not resolved_bundle_path.is_file():
        raise HTTPException(status_code=400, detail="Import bundle path must point to a file")

    try:
        job = await import_proteinbase_bundle(
            session=session,
            bundle_path=resolved_bundle_path,
            dataset_name=request.dataset_name,
            job_name=request.job_name,
        )
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"ProteinBase bundle must be UTF-8 text: {exc}") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"ProteinBase bundle is invalid: {exc}") from exc

    await session.refresh(job)

    design_count = (
        await session.execute(select(func.count(Design.id)).where(Design.job_id == job.id))
    ).scalar_one()

    try:
        from services.analysis_autorun import schedule_viewer_minimum_analyses_for_job

        schedule_viewer_minimum_analyses_for_job(str(job.id))
    except Exception:
        pass

    return JobResponse(
        id=job.id,
        name=job.name,
        status=job.status,
        model_id=job.model_id,
        mode=job.mode,
        params=_public_job_params(job),
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        output_dir=_public_job_output_dir(job),
        error_message=job.error_message,
        design_count=design_count or 0,
        requested_design_count=_resolve_requested_design_count(job),
        batch_id=job.batch_id,
        batch_name=job.batch_name,
        parent_job_id=job.parent_job_id,
        child_stage=job.child_stage,
        lineage_root_job_id=job.lineage_root_job_id,
        stage_family=job.stage_family,
        stage_mode=job.stage_mode,
        source_stage_job_id=job.source_stage_job_id,
        source_stage_family=job.source_stage_family,
        source_stage_mode=job.source_stage_mode,
        source_selection_manifest_path=job.source_selection_manifest_path,
        source_selection_count=job.source_selection_count,
        selected_input_artifact_class=job.selected_input_artifact_class,
        selected_input_schema_version=job.selected_input_schema_version,
        selection_source_type=job.selection_source_type,
        selection_source_job_id=job.selection_source_job_id,
        selection_dataset_name=job.selection_dataset_name,
        selected_loop_scope=job.selected_loop_scope,
        provenance=job.provenance,
        saved_selection_sets=_serialized_saved_review_filter_sets(job),
        pinned_gpu=job.pinned_gpu,
        execution_target_id=job.execution_target_id,
        execution_source_revision=job.execution_source_revision,
        execution_source_tree=job.execution_source_tree,
        execution_bundle_sha256=job.execution_bundle_sha256,
        remote_attempt_id=job.remote_attempt_id,
        remote_state=job.remote_state,
        execution_stages=project_execution_stages(job),
        current_stage=job.current_stage,
        completed_stages=job.completed_stages,
        stage_outputs=job.stage_outputs,
        awaiting_input=job.awaiting_input,
        awaiting_stage=job.awaiting_stage,
        awaiting_payload=job.awaiting_payload,
        decision_history=job.decision_history,
    )


@router.post('/execution-plan/preview', response_model=JobExecutionPlanPreview)
async def preview_job_execution_plan(
    job_data: JobCreate, session: AsyncSession = Depends(get_session),
    experiment_session: AsyncSession = Depends(get_experiment_session),
):
    """Browser and agent use the exact same typed, nonexecuting preview."""
    return await preview_submission(job_data, session, experiment_session)


@router.post("", response_model=JobResponse, status_code=201)
async def create_job(
    job_data: JobCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    _preallocated_job_id: Any = Depends(lambda: None),
    _commit: Any = Depends(lambda: True),
    _skip_parent_lineage_update: Any = Depends(lambda: False),
    _md_output_creation: Any = Depends(lambda: None),
    _md_input_resolver: Any = Depends(lambda: None),
    _typed_md_project_launch: Any = Depends(lambda: None),
    experiment_session: AsyncSession = Depends(get_experiment_session),
    _approved_execution_plan: Any = Depends(lambda: None),
) -> JobResponse:
    """Canonical Job submission, optionally bound by one opaque launch context."""
    return await submit_job(
        job_data,
        background_tasks,
        session,
        _preallocated_job_id=_preallocated_job_id,
        _commit=_commit,
        _md_output_creation=_md_output_creation,
        _md_input_resolver=_md_input_resolver,
        _typed_md_project_launch=(
            None
            if isinstance(_typed_md_project_launch, DependsParam)
            else _typed_md_project_launch
        ),
        experiment_session=experiment_session,
        _approved_execution_plan=_approved_execution_plan,
    )


@router.post("/antibody-iteration/from-designs", response_model=AntibodyIterationLaunchResponse, status_code=201)
async def launch_antibody_iteration_from_designs(
    request: AntibodyIterationLaunchRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Launch a focused antibody iteration round from selected design structures."""
    source_job, root_job = await _resolve_antibody_root_job(session, request.source_job_id)
    saved_filter_set = _resolve_saved_review_filter_set(
        request.review_filter_set_id,
        [source_job, root_job],
    )
    design_ids = _resolve_launch_design_ids(request.design_ids, saved_filter_set)
    if not design_ids:
        raise HTTPException(
            status_code=422,
            detail="Select at least one design or load a saved review dataset before launching a new round.",
        )

    result = await session.execute(select(Design).where(Design.id.in_(design_ids)))
    found_designs = result.scalars().all()
    design_by_id = {design.id: design for design in found_designs}
    missing_designs = [design_id for design_id in design_ids if design_id not in design_by_id]
    if missing_designs:
        raise HTTPException(
            status_code=404,
            detail=f"Some selected designs were not found: {', '.join(missing_designs[:10])}",
        )

    action = request.action.strip().lower()
    ordered_designs = [design_by_id[design_id] for design_id in design_ids]
    await _validate_selected_design_owners(session, source_job, root_job, ordered_designs)
    if action == "frustrampnn":
        if (
            request.param_overrides
            or request.cdr_indel_config is not None
            or request.manual_mutagenesis_config is not None
            or request.name_suffix is not None
        ):
            raise HTTPException(
                status_code=422,
                detail="FrustraMPNN runtime, path, GPU, and naming overrides are server-owned.",
            )
        from services.frustrampnn.jobs import (
            FrustraMPNNChildError,
            design_selections as resolve_frustrampnn_selections,
        )
        from services.structure_dataset_fanout import StructureDatasetFanoutError
        from routers.frustrampnn import _fanout_design_selections

        try:
            selections = await resolve_frustrampnn_selections(
                session,
                source_parent=source_job,
                design_ids=design_ids,
            )
            requested_settings = (
                request.frustrampnn_settings or default_frustrampnn_settings()
            )
            fanout = await _fanout_design_selections(
                session,
                parent=source_job,
                selections=selections,
                trigger="antibody_iteration",
                requested_settings=requested_settings,
            )
        except (FrustraMPNNChildError, StructureDatasetFanoutError) as exc:
            await session.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        launched_jobs = [await get_job(str(child.id), session) for child in fanout.child_jobs]
        launched_job = launched_jobs[0]
        return AntibodyIterationLaunchResponse(
            message=(
                f"Queued {len(launched_jobs)} FrustraMPNN scheduler jobs for "
                f"{len(ordered_designs)} selected designs."
            ),
            action=action,
            source_job_id=source_job.id,
            root_job_id=root_job.id,
            selection_dir=str(fanout.child_jobs[0].output_dir),
            selected_design_count=len(ordered_designs),
            launched_job=launched_job,
            launched_jobs=launched_jobs,
            fanout_id=fanout.fanout_id,
        )

    selection_dir = _materialize_antibody_selection(root_job, source_job, ordered_designs, request.action)
    variant_note = ""
    variant_count = len(ordered_designs)
    if action == "cdr_indel_round":
        if request.cdr_indel_config is None:
            raise HTTPException(status_code=422, detail="cdr_indel_config is required for action 'cdr_indel_round'.")
        launch_request, variant_count, variant_note = _build_cdr_indel_iteration_job(
            root_job=root_job,
            source_job=source_job,
            designs=ordered_designs,
            config=request.cdr_indel_config,
            name_suffix=request.name_suffix,
            param_overrides=request.param_overrides,
        )
    elif action == "mutation_seeded_refinement":
        if request.manual_mutagenesis_config is not None:
            launch_request, variant_count, variant_note = _build_manual_mutagenesis_seeded_refinement_job(
                root_job=root_job,
                source_job=source_job,
                designs=ordered_designs,
                config=request.manual_mutagenesis_config,
                name_suffix=request.name_suffix,
                param_overrides=request.param_overrides,
            )
        elif request.cdr_indel_config is not None:
            launch_request, variant_count, variant_note = _build_cdr_indel_seeded_refinement_job(
                root_job=root_job,
                source_job=source_job,
                designs=ordered_designs,
                config=request.cdr_indel_config,
                name_suffix=request.name_suffix,
                param_overrides=request.param_overrides,
            )
        else:
            raise HTTPException(
                status_code=422,
                detail="mutation_seeded_refinement requires manual_mutagenesis_config or cdr_indel_config.",
            )
    else:
        launch_request = _build_antibody_iteration_job(
            root_job=root_job,
            source_job=source_job,
            action=action,
            selection_dir=selection_dir,
            design_ids=design_ids,
            name_suffix=request.name_suffix,
            param_overrides=request.param_overrides,
            saved_filter_set=saved_filter_set,
            selected_designs=ordered_designs,
        )
    if isinstance(launch_request.params, dict):
        source_stage_payload = _derive_source_stage_payload(source_job, ordered_designs, selection_dir)
        launch_request.params.update({
            "lineage_root_job_id": root_job.id,
            "stage_family": launch_request.params.get("stage_family"),
            "stage_mode": launch_request.params.get("stage_mode"),
            "selection_source_type": "saved_dataset" if saved_filter_set is not None else "selected_designs",
            "selection_source_job_id": source_job.id,
            "selection_dataset_name": saved_filter_set.name if saved_filter_set is not None else None,
            "selected_loop_scope": _build_selected_loop_scope(launch_request.params),
            **source_stage_payload,
        })
    launch_selection_dir = str(launch_request.params.get("iteration_selection_dir") or selection_dir)
    _require_prepared_remote_review(launch_request, {
        'message': f"Launched antibody iteration action '{action}' from {len(ordered_designs)} selected designs.",
        'action': action, 'source_job_id': source_job.id, 'root_job_id': root_job.id,
        'selection_dir': launch_selection_dir, 'selected_design_count': len(ordered_designs),
    })
    launched_job = await submit_job(launch_request, background_tasks, session)
    selection_source_note = (
        f" using saved dataset '{saved_filter_set.name}'"
        if saved_filter_set is not None and not _normalize_design_ids(request.design_ids)
        else ""
    )

    return AntibodyIterationLaunchResponse(
        message=(
            f"Launched antibody iteration action '{action}' from {len(ordered_designs)} selected designs{selection_source_note}."
            + (f" Generated {variant_count} indel variants." if action == "cdr_indel_round" else "")
            + variant_note
        ),
        action=action,
        source_job_id=source_job.id,
        root_job_id=root_job.id,
        selection_dir=launch_selection_dir,
        selected_design_count=len(ordered_designs),
        launched_job=launched_job,
    )


@router.post("/mutagenesis/from-designs", response_model=ManualMutagenesisLaunchResponse, status_code=201)
async def launch_manual_mutagenesis_from_designs(
    request: ManualMutagenesisLaunchRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Launch an explicit manual mutation batch from selected structure designs."""
    source_job = await session.get(Job, request.source_job_id)
    if source_job is None:
        raise HTTPException(status_code=404, detail="Source job not found.")

    root_job: Optional[Job] = None
    try:
        _, root_job = await _resolve_antibody_root_job(session, request.source_job_id)
    except HTTPException:
        root_job = None

    saved_filter_set = _resolve_saved_review_filter_set(
        request.review_filter_set_id,
        [source_job, root_job],
    )
    design_ids = _resolve_launch_design_ids(request.design_ids, saved_filter_set)
    if not design_ids:
        raise HTTPException(
            status_code=422,
            detail="Select at least one design or load a saved review dataset before launching a new round.",
        )

    result = await session.execute(select(Design).where(Design.id.in_(design_ids)))
    found_designs = result.scalars().all()
    design_by_id = {design.id: design for design in found_designs}
    missing_designs = [design_id for design_id in design_ids if design_id not in design_by_id]
    if missing_designs:
        raise HTTPException(
            status_code=404,
            detail=f"Some selected designs were not found: {', '.join(missing_designs[:10])}",
        )

    ordered_designs = [design_by_id[design_id] for design_id in design_ids]
    await _validate_selected_design_owners(session, source_job, root_job, ordered_designs)
    launch_request, variant_count, variant_note = _build_manual_mutagenesis_iteration_job(
        source_job=source_job,
        designs=ordered_designs,
        config=request.config,
        name_suffix=request.name_suffix,
        param_overrides=request.param_overrides,
    )
    _require_prepared_remote_review(launch_request, {
        'message': f'Launched manual mutagenesis from {len(ordered_designs)} selected designs. Generated {variant_count} explicit variants.',
        'source_job_id': source_job.id, 'selected_design_count': len(ordered_designs),
        'variant_count': variant_count,
    })
    launched_job = await submit_job(launch_request, background_tasks, session)
    selection_source_note = (
        f" using saved dataset '{saved_filter_set.name}'"
        if saved_filter_set is not None and not _normalize_design_ids(request.design_ids)
        else ""
    )

    return ManualMutagenesisLaunchResponse(
        message=(
            f"Launched manual mutagenesis from {len(ordered_designs)} selected designs{selection_source_note}."
            f" Generated {variant_count} explicit variants."
            + variant_note
        ),
        source_job_id=source_job.id,
        selected_design_count=len(ordered_designs),
        variant_count=variant_count,
        launched_job=launched_job,
    )


def _structure_stem(source) -> str:
    """File name without any structure extension (.cif.gz, .pdb, ...).

    FAMPNN strips .pdb/.cif from its fixed-position row names but not from the staged
    file stem, so a name like design.cif.pdb would miss its own row.
    """
    name = Path(str(source)).name
    while True:
        base, ext = os.path.splitext(name)
        if ext.lower() not in {".gz", ".cif", ".mmcif", ".pdb", ".ent", ".bcif"} or not base:
            return name
        name = base


async def _prepare_resequence_job(request: ResequenceLaunchRequest, session: AsyncSession) -> JobCreate:
    """Acquire exact cold-portable sources and only authored scientific settings."""
    from services.binder_source_materialization import (prepare_resequence_sources,
        prepare_resequence_existing_basket, begin_resequence_preparation, resequence_submission_guard)
    from services.binder_source_materialization import governed_ngs_result_roots
    from services.resequence_settings import designer_params, validator_params
    from services.resequence_designers import build_template

    selection_dir = _create_generic_selection_dir("protein_local_redesign", "resequence")
    template_path = selection_dir.with_name(selection_dir.name + "_designer.json")
    try:
        async with resequence_submission_guard(request.model_dump(mode="json"), session):
            roots = await governed_ngs_result_roots(session)
            if request.sources:
                roster = await prepare_resequence_sources(request.sources, session, selection_dir, roots)
            elif request.backbone_input_pdbs:
                roster = await prepare_resequence_existing_basket(request.backbone_input_pdbs,
                    request.retained_params, session, selection_dir, roots)
            else:
                raise ValueError("Resequence requires structures or a retained basket")
            if not roster:
                raise ValueError("Resequence requires at least one structure")
            params: Dict[str, Any] = dict(backbone_input_pdbs=str(selection_dir), resequence_sources=roster,
                design_chains=request.design_chains, seq_method=request.seq_method,
                structure_validators=request.structure_validators, protenix_msa_backend=request.msa_provider)
            if request.fixed_positions.strip():
                params["fixed_positions"] = request.fixed_positions.strip()
            if request.designable_positions.strip():
                params["designable_positions"] = request.designable_positions.strip()
            if request.seq_method in ("ligandmpnn", "caliby", "protonpottsmpnn"):
                retained = request.retained_params
                retained_template = retained.get("designer_template")
                same_designer = retained.get("seq_method") == request.seq_method
                retained_bytes = None
                retained_mode = retained.get("designer_mode")
                if "designer_settings" not in request.model_fields_set and retained_template and same_designer:
                    retained_bytes = resolve_allowed_path(
                        to_allowed_relative(Path(retained_template)) if Path(retained_template).is_absolute()
                        else retained_template).read_bytes()
                    native_template = json.loads(retained_bytes)
                    retained_mode = retained_mode or native_template.get("mode") or (
                        "ensemble_design" if request.seq_method == "caliby" else None)
                same_mode = (not request.designer_mode or retained_mode == request.designer_mode
                             or request.seq_method == "protonpottsmpnn")
                if retained_bytes is not None and same_mode:
                    template_path.write_bytes(retained_bytes)
                else:
                    template = build_template(request.seq_method, mode=request.designer_mode,
                        design_chains=request.design_chains, settings=request.designer_settings)
                    template_path.write_text(json.dumps(template, sort_keys=True))
                params["designer_template"] = str(template_path)
                if request.designer_mode:
                    params["designer_mode"] = request.designer_mode
            else:
                params.update(designer_params(request.seq_method, request.designer_settings))
            params.update(validator_params(request.structure_validators, request.validator_settings))
            params["resequence_preparation"] = begin_resequence_preparation(selection_dir)
            return JobCreate(name=request.name, model_id="protein_modification_experimental",
                mode="region_redesign", params=params, execution_target_id=request.execution_target_id,
                execution_policy=request.execution_policy, launch_context_id=request.launch_context_id)
    except BaseException as exc:
        shutil.rmtree(selection_dir, ignore_errors=True)
        template_path.unlink(missing_ok=True)
        if isinstance(exc, (ValueError, OSError, KeyError)):
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        raise


@router.post("/resequence/from-structures/cleanup")
async def cleanup_resequence_from_structures(request: Dict[str, Any], session: AsyncSession = Depends(get_session)):
    from services.binder_source_materialization import cleanup_resequence_preparation
    try:
        return {"cleaned": await cleanup_resequence_preparation(request["preparation"], session)}
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/resequence/from-structures", response_model=JobResponse, status_code=201)
async def launch_resequence_from_structures(
    request: ResequenceLaunchRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Resequence and validate exact structures from any generator (RFD4, RFD3, DISCO, ...) on this system.

    Sources are the same exact-structure references the continuation links use, so the source
    job's model does not matter. Fixed or designable positions are caller-selected; with neither
    given every residue of the design chains is designable.
    """
    return await submit_job(await _prepare_resequence_job(request, session), background_tasks, session)


@router.post("/resequence/from-structures/prepare")
async def prepare_resequence_from_structures(
    request: ResequenceLaunchRequest,
    session: AsyncSession = Depends(get_session),
):
    """The same staged request as the launch above, returned for the launcher to place and submit.

    A worker launch needs the reviewed plan to cover the exact staged structures, so staging happens first and the
    launcher then submits this body with the chosen execution target through the normal plan-approval flow.
    """
    return (await _prepare_resequence_job(request, session)).model_dump(mode="json", exclude_unset=True)


@router.delete("/{job_id}")
async def cancel_job(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Cancel a job and any active descendant jobs it spawned."""
    await reject_generic_md_lifecycle_control(job_id, session)
    job, lineage = await cancel_job_lineage(job_id, session)
    return {
        "message": "Job cancelled",
        "job_id": job_id,
        "jobs_cancelled": len(lineage),
        "root_job_name": job.name,
    }


@router.delete("/{job_id}/permanent")
async def delete_job_permanently(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Permanently delete a job and ALL its data.
    
    DEBUG FEATURE - Removes:
    - Job from database
    - All child jobs from database
    - All designs from database  
    - Output directory (bms_results/...)
    - Work directory (work/...)
    
    This is irreversible!
    """
    await reject_generic_md_lifecycle_control(job_id, session)
    import shutil
    
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Cancel any active lineage members before removing their DB rows/files.
    deletion_lineage = [job]
    try:
        _, deletion_lineage = await cancel_job_lineage(job_id, session, error_message="Deleted by user")
    except HTTPException as exc:
        if exc.status_code not in {400, 404}:
            raise
    
    job_name = job.name
    output_dir = job.output_dir
    
    # Delete designs associated with this job
    from database import Design
    await session.execute(
        Design.__table__.delete().where(Design.job_id == job_id)
    )
    
    # Delete child jobs first
    child_jobs = [member for member in deletion_lineage if member.id != job_id]
    from services.job_control import retain_permanent_delete_cleanup
    retained_ids = set()
    for member in deletion_lineage:
        if await retain_permanent_delete_cleanup(session, member):
            retained_ids.add(member.id)

    child_output_dirs = []
    for child in child_jobs:
        if child.output_dir:
            child_output_dirs.append(child.output_dir)

    delete_job_ids = {job.id, *(str(child.id) for child in child_jobs)} - retained_ids
    remaining_output_refs = await _load_remaining_output_dir_refs(session, delete_job_ids)
    output_dirs_to_delete, preserved_output_dirs = _plan_output_dir_cleanup(
        [output_dir, *child_output_dirs],
        remaining_output_refs,
    )

    for preserved in preserved_output_dirs:
        logger.warning(
            "[DELETE] Preserving shared output dir %s because it is still referenced by %s",
            preserved["path"],
            ", ".join(
                f"{ref['job_id']}:{ref['field']}"
                for ref in preserved.get("referenced_by", [])
            ) or "unknown jobs",
        )

    for child in child_jobs:
        # Delete child's designs
        await session.execute(
            Design.__table__.delete().where(Design.job_id == child.id)
        )
        if child.id not in retained_ids:
            await session.delete(child)

    # Retained hidden rows remain the existing stop/claim cleanup owner.
    if job.id not in retained_ids:
        await session.delete(job)
    await session.commit()
    
    # Delete output directories
    deleted_paths = []
    for raw_path in output_dirs_to_delete:
        output_path = resolve_output_dir(raw_path)
        if output_path and output_path.exists():
            try:
                shutil.rmtree(output_path)
                deleted_paths.append(str(output_path))
            except Exception as e:
                print(f"Warning: Failed to delete output dir {output_path}: {e}")
    
    return {
        "message": f"Job '{job_name}' permanently deleted",
        "job_id": job_id,
        "children_deleted": len(child_jobs),
        "directories_deleted": deleted_paths,
        "directories_preserved": preserved_output_dirs,
    }


from services.core_protein_execution_settings import ExecutionSettings


@router.post("/{job_id}/resubmit")
async def resubmit_job(
    job_id: str,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    placement: ResubmitJobRequest | None = Body(default=None),
):

    """
    Resubmit a failed or cancelled job with the same parameters.
    Creates a new job with a fresh ID but copies all settings from the original.
    """
    return await resubmit_failed_job(
        job_id, session, placement,
        authorize=partial(alignment_access.request_is_authorized, request),
        grant_access=partial(alignment_access.grant_alignment_access, response=response, request=request),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# RE-INGESTION ENDPOINT
# ═══════════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE CHECKPOINTING ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════════
# CHILD JOB TRACKING (Spawn-Wait-Aggregate Pattern)
# ═══════════════════════════════════════════════════════════════════════════════


@router.post("/{job_id}/resume")
async def resume_job(
    job_id: str,
    request_context: Request,
    response: Response,
    from_stage: str = None,
    request: Optional[ResumeJobRequest] = Body(default=None),
    background_tasks: BackgroundTasks = None,
    session: AsyncSession = Depends(get_session)
):
    """
    Resume a failed job from a checkpoint.
    
    If from_stage is specified, it is recorded as a stage hint for cache-based
    resume behavior. The underlying Nextflow resume remains cache-driven.
    """
    return await resume_workflow_job(
        job_id, session, request, from_stage, background_tasks,
        authorize=partial(alignment_access.request_is_authorized, request_context),
        grant_access=partial(alignment_access.grant_alignment_access, response=response, request=request_context),
    )


@router.post("/{job_id}/continue-protein-local-review")
async def continue_protein_local_review(
    job_id: str,
    request_context: Request,
    response: Response,
    request: ContinueProteinLocalReviewRequest,
    session: AsyncSession = Depends(get_session),
):
    """Resume a paused protein-local-redesign job from a filtered subset."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not _is_protein_local_redesign_job(job):
        raise HTTPException(status_code=422, detail="This continue endpoint only supports Protein Local Redesign jobs.")
    if not job.awaiting_input or not job.awaiting_stage:
        raise HTTPException(status_code=422, detail="Protein local redesign job is not currently paused for review.")

    selected_ids = [str(design_id).strip() for design_id in request.design_ids if str(design_id).strip()]
    if not selected_ids:
        raise HTTPException(status_code=422, detail="Select at least one design before continuing the workflow.")

    design_result = await session.execute(
        select(Design)
        .where(
            Design.job_id == job_id,
            Design.id.in_(selected_ids),
        )
        .order_by(Design.created_at.asc(), Design.name.asc())
    )
    selected_designs = design_result.scalars().all()

    if len(selected_designs) != len(set(selected_ids)):
        found_ids = {design.id for design in selected_designs}
        missing = [design_id for design_id in selected_ids if design_id not in found_ids]
        raise HTTPException(
            status_code=422,
            detail=f"Some selected designs could not be resolved for this paused job: {', '.join(missing[:5])}",
        )

    selection_dir = _materialize_protein_local_selection(job, job, selected_designs, "continue_review")
    source_stage_payload = _derive_source_stage_payload(job, selected_designs, selection_dir)
    output_path = Path(job.output_dir)
    if not output_path.is_absolute():
        output_path = get_data_root() / output_path
    region_manifest_path = output_path / "inputs" / "protein_local_redesign" / "region_manifest.json"
    param_overrides: Dict[str, Any] = {
        **source_stage_payload,
        "selected_input_dir": str(selection_dir),
        "selected_input_manifest": str(_selection_manifest_path(selection_dir)),
    }
    from_stage = None

    if job.awaiting_stage == "post_rfantibody":
        param_overrides.update({
            "plr_backbone_input_pdbs": str(selection_dir),
            "plr_region_manifest": str(region_manifest_path),
        })
        from_stage = "rfantibody"
    elif job.awaiting_stage == "post_fampnn":
        param_overrides["plr_sequence_input_pdbs"] = str(selection_dir)
        from_stage = "fampnn"
    elif job.awaiting_stage == "post_structure_validation":
        param_overrides.update({
            "plr_validation_input_pdbs": str(selection_dir),
            "plr_final_candidate_dir": str(selection_dir),
        })
        from_stage = "structure_validation"
    else:
        raise HTTPException(status_code=422, detail=f"Unsupported PLR review stage '{job.awaiting_stage}'.")

    return await resume_job(
        job_id=job_id,
        request_context=request_context,
        response=response,
        from_stage=from_stage,
        request=ResumeJobRequest(
            from_stage=from_stage,
            param_overrides=param_overrides,
            name_suffix=request.name_suffix or "_continued",
        ),
        session=session,
    )


from routers import job_results, job_stages

router.include_router(job_results.router)
router.include_router(job_stages.router)
