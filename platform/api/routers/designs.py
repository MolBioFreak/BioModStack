"""
Designs API router - Query and manage protein designs.

Provides endpoints for listing, filtering, and managing designs
stored in the SQLite database after pipeline ingestion.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, case, inspect as sa_inspect
from sqlalchemy.exc import NoInspectionAvailable
from sqlalchemy.orm import load_only
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, ConfigDict
from datetime import datetime
from pathlib import Path
import math

from database import get_session, Design, Job
from paths import resolve_runtime_data_path, to_allowed_relative
from services.analysis_runs import get_matching_design_analysis_run, load_analysis_result
from services.cdr_annotator import extract_sequence_from_pdb, identify_binder_chains
from services.stage_review import REVIEWABLE_STAGES, ensure_stage_review_rows, load_review_gate_snapshot
from services.structure_utils import get_per_chain_fampnn_psce
from antibody_pipeline_contract import infer_antibody_artifact_class_from_stage, normalize_antibody_artifact_class


router = APIRouter()

_TWO_LETTER_ELEMENTS = {
    "BR", "CL", "NA", "MG", "AL", "SI", "CA", "SC", "TI", "CR", "MN", "FE", "CO", "NI", "CU",
    "ZN", "GA", "GE", "AS", "SE", "SR", "ZR", "MO", "RU", "RH", "PD", "AG", "CD", "IN", "SN",
    "SB", "TE", "CS", "BA", "LA", "CE", "PR", "ND", "SM", "EU", "GD", "TB", "DY", "HO", "ER",
    "TM", "YB", "LU", "HF", "TA", "RE", "OS", "IR", "PT", "AU", "HG", "TL", "PB", "BI",
}
_PRESERVED_REVIEW_PAYLOAD_KEYS = ("review_filter_sets",)


def _guess_pdb_element(atom_name: str) -> str:
    raw_name = (atom_name or "")[:4].ljust(4)
    letters = "".join(char for char in raw_name if char.isalpha()).upper()
    if not letters:
        return ""

    # PDB atom names are alignment-sensitive. When the first column is blank,
    # names like " CA " are alpha carbons, not the element calcium.
    if raw_name[0].isspace() or raw_name[0].isdigit():
        return letters[0]

    if len(letters) >= 2 and raw_name[0].isalpha() and raw_name[1].isalpha() and letters[:2] in _TWO_LETTER_ELEMENTS:
        return letters[:2].title()
    return letters[0]


def _normalize_pdb_for_viewer(pdb_text: str) -> str:
    normalized_lines: list[str] = []
    for raw_line in pdb_text.splitlines():
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            continue
        record = line[:6].strip().upper()
        if record in {"ATOM", "HETATM"}:
            padded = line.ljust(80)
            element = padded[76:78].strip() or _guess_pdb_element(padded[12:16].strip())
            charge = padded[78:80].strip()
            normalized_lines.append(f"{padded[:76]}{element:>2}{charge:>2}")
            continue
        if record in {"ANISOU", "TER"}:
            normalized_lines.append(line.ljust(80))
            continue
        normalized_lines.append(line)

    if not normalized_lines or normalized_lines[-1].strip().upper() != "END":
        normalized_lines.append("END")

    return "\n".join(normalized_lines) + "\n"


def _merge_review_payload(
    gate_payload: Optional[Dict[str, Any]],
    existing_payload: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    merged = dict(gate_payload or {})
    existing = existing_payload if isinstance(existing_payload, dict) else {}
    for key in _PRESERVED_REVIEW_PAYLOAD_KEYS:
        if key in merged:
            continue
        preserved_value = existing.get(key)
        if isinstance(preserved_value, list) and preserved_value:
            merged[key] = preserved_value
    return merged


async def _hydrate_review_job(session: AsyncSession, job: Optional[Job]) -> Optional[str]:
    if job is None:
        return None

    gate_stage, gate_payload = load_review_gate_snapshot(job.output_dir, job.awaiting_stage)
    gate_payload = _merge_review_payload(gate_payload, job.awaiting_payload or {})
    review_stage = str(job.awaiting_stage or gate_stage or "").strip().lower()
    changed = False

    if gate_stage and job.awaiting_stage != gate_stage:
        job.awaiting_stage = gate_stage
        review_stage = gate_stage
        changed = True
    if gate_payload and gate_payload != (job.awaiting_payload or {}):
        job.awaiting_payload = gate_payload
        changed = True
    if review_stage in REVIEWABLE_STAGES and not bool(job.awaiting_input):
        job.awaiting_input = True
        changed = True

    if review_stage in REVIEWABLE_STAGES and bool(job.awaiting_input):
        existing_review_design_count = await session.scalar(
            select(func.count()).select_from(Design).where(
                Design.job_id == job.id,
                Design.source_stage == review_stage,
            )
        )
        if not existing_review_design_count:
            await ensure_stage_review_rows(session, job)
            changed = True

    if changed:
        await session.commit()

    return review_stage if review_stage in REVIEWABLE_STAGES else None


def _should_force_review_stage_listing(job: Optional[Job], review_stage: Optional[str]) -> bool:
    normalized_stage = str(review_stage or "").strip().lower()
    return bool(job and job.awaiting_input and normalized_stage in REVIEWABLE_STAGES)


def _structure_file_response(path: Path, filename_root: str):
    if not path.exists():
        raise HTTPException(status_code=404, detail="Structure file not found on disk")

    if path.suffix.lower() == ".pdb":
        normalized_pdb = _normalize_pdb_for_viewer(path.read_text(errors="ignore"))
        filename = f"{filename_root}{path.suffix or '.pdb'}"
        return Response(
            content=normalized_pdb,
            media_type="text/plain",
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )

    return FileResponse(
        path=path,
        filename=f"{filename_root}{path.suffix or '.pdb'}",
        media_type="text/plain",
    )


# --- Pydantic Schemas ---


class ChainMetric(BaseModel):
    type: str
    length: int
    avg_plddt: Optional[float]
    plddt: Optional[List[float]]
    residue_numbers: Optional[List[int]]

class ChainMetricsResponse(BaseModel):
    design_id: str
    chains: Dict[str, ChainMetric]


class DesignResponse(BaseModel):
    id: str
    job_id: str
    name: str
    pdb_path: Optional[str]
    
    # Structural metrics
    num_helices: Optional[int]
    num_strands: Optional[int]
    rog: Optional[float]
    rfd_rog: Optional[float]
    
    # Sequence metrics
    mpnn_score: Optional[float]
    fampnn_psce: Optional[float]
    fampnn_max_residue_psce: Optional[float] = None
    fampnn_min_residue_psce: Optional[float] = None
    
    # Prediction metrics
    plddt_overall: Optional[float]
    plddt_binder: Optional[float]
    plddt_target: Optional[float]
    pae_interaction: Optional[float]
    pae_overall: Optional[float]
    rmsd_overall: Optional[float]
    rmsd_binder: Optional[float]
    rmsd_target: Optional[float]
    
    # Boltz-2 specific
    conf_score: Optional[float]
    ptm: Optional[float]
    ligand_iptm: Optional[float]
    affinity_score: Optional[float]
    binder_probability: Optional[float]
    
    # Interface metrics (complexes)
    iptm: Optional[float] = None
    protein_iptm: Optional[float] = None
    complex_iplddt: Optional[float] = None
    complex_ipde: Optional[float] = None
    disorder: Optional[float] = None
    num_recycles: Optional[int] = None
    has_clash: Optional[bool] = None
    chains_ptm: Optional[Union[Dict[str, float], List[float]]] = None  # {"0":0.76} or [0.76, ...]
    pair_chains_iptm: Optional[Union[Dict[str, Dict[str, float]], List[List[float]]]] = None  # matrix
    confidence_metrics: Optional[Dict[str, Any]] = None
    ipsae: Optional[float] = None
    ipsae_binder_to_target: Optional[float] = None
    ipsae_target_to_binder: Optional[float] = None
    ipsae_d0chn: Optional[float] = None
    ipsae_d0dom: Optional[float] = None
    ipsae_chain_pair: Optional[str] = None
    
    # Per-residue metrics (for charts)
    residue_plddt: Optional[List[float]] = None
    chain_metrics: Optional[Dict[str, ChainMetric]] = None
    
    # Antibody CDR annotation
    binder_length: Optional[int] = None
    binder_sequence: Optional[str] = None
    antibody_type: Optional[str] = None  # vhh, fab, scfv
    cdr_h1: Optional[str] = None
    cdr_h2: Optional[str] = None
    cdr_h3: Optional[str] = None
    cdr_l1: Optional[str] = None
    cdr_l2: Optional[str] = None
    cdr_l3: Optional[str] = None
    cdr_h1_length: Optional[int] = None
    cdr_h2_length: Optional[int] = None
    cdr_h3_length: Optional[int] = None
    cdr_l1_length: Optional[int] = None
    cdr_l2_length: Optional[int] = None
    cdr_l3_length: Optional[int] = None
    
    # User annotations
    is_favorite: bool
    notes: Optional[str]

    # Lineage / provenance
    lineage_root_job_id: Optional[str] = None
    parent_design_id: Optional[str] = None
    origin_design_id: Optional[str] = None
    origin_job_id: Optional[str] = None
    origin_backbone_design_id: Optional[str] = None
    stage_family: Optional[str] = None
    stage_mode: Optional[str] = None
    source_stage_job_id: Optional[str] = None
    source_stage_family: Optional[str] = None
    source_stage_mode: Optional[str] = None
    source_pdb_path: Optional[str] = None
    source_design_name: Optional[str] = None
    artifact_class: Optional[str] = None
    artifact_schema_version: Optional[int] = None
    result_set: Optional[str] = None
    result_set_label: Optional[str] = None
    selected_loop_scope: Optional[Dict[str, Any]] = None
    provenance: Optional[Dict[str, Any]] = None
    is_imported: bool = False
    import_source: Optional[str] = None
    import_method: Optional[str] = None
    import_label: Optional[str] = None
    
    # Backbone grouping & epitope analysis
    backbone_id: Optional[int] = None
    epitope_contact_count: Optional[int] = None
    epitope_min_distance: Optional[float] = None
    epitope_min_atom_distance: Optional[float] = None
    epitope_nearest_antibody_residue: Optional[str] = None
    epitope_nearest_target_residue: Optional[str] = None
    epitope_nearest_antibody_atom: Optional[str] = None
    epitope_nearest_target_atom: Optional[str] = None
    epitope_mapping_mode: Optional[str] = None
    epitope_centroid_distance: Optional[float] = None
    target_contact_count: Optional[int] = None
    target_min_distance: Optional[float] = None
    target_min_atom_distance: Optional[float] = None
    target_nearest_antibody_residue: Optional[str] = None
    target_nearest_target_residue: Optional[str] = None
    target_nearest_antibody_atom: Optional[str] = None
    target_nearest_target_atom: Optional[str] = None
    target_centroid_distance: Optional[float] = None
    detected_antibody_chains: Optional[str] = None
    detected_target_chain: Optional[str] = None
    antibody_residue_count: Optional[int] = None
    target_residue_count: Optional[int] = None
    epitope_residue_count: Optional[int] = None
    passed_screen: Optional[bool] = None
    screening_reason: Optional[str] = None
    source_stage: Optional[str] = None
    artifact_group: Optional[str] = None
    rfa_loop_metrics: Optional[Dict[str, Any]] = None
    rfa_hotspot_metrics: Optional[Dict[str, Any]] = None
    rfa_hotspot_covered_count: Optional[int] = None
    rfa_hotspot_min_distance: Optional[float] = None
    rfa_hotspot_avg_min_distance: Optional[float] = None
    rfa_runtime_seconds: Optional[float] = None
    rfa_device: Optional[str] = None
    rfa_diffusion_steps: Optional[int] = None
    rfa_noise_scale_ca: Optional[float] = None
    rfa_noise_scale_frame: Optional[float] = None
    rfa_guide_scale: Optional[float] = None
    rfa_plddt_initial: Optional[float] = None
    rfa_plddt_final: Optional[float] = None
    rfa_plddt_delta: Optional[float] = None
    rfa_plddt_selected: Optional[float] = None
    rfa_plddt_nonselected: Optional[float] = None
    rfa_plddt_primary: Optional[float] = None
    rfa_plddt_modifiable: Optional[float] = None
    rfa_plddt_all_residue: Optional[float] = None
    rfa_plddt_nonmodifiable: Optional[float] = None
    rfa_plddt_framework: Optional[float] = None
    rfa_plddt_target: Optional[float] = None
    rfa_modifiable_residues: Optional[List[Dict[str, Any]]] = None
    rfa_modifiable_ranges: Optional[List[Dict[str, Any]]] = None
    rfa_confidence_scope: Optional[Dict[str, Any]] = None
    rfa_design_loops: Optional[List[str]] = None
    rfa_hotspots: Optional[List[str]] = None
    
    # Frustration analysis (FrustraMPNN)
    frustration_high_count: Optional[int] = None
    frustration_min_count: Optional[int] = None
    frustration_pct_high: Optional[float] = None
    frustration_residues: Optional[List[dict]] = None  # [{pos, chain, frust, frustClass}]
    frustration_csv_path: Optional[str] = None
    frustration_csv_relpath: Optional[str] = None
    
    # PPIFlow Maturation
    maturation_delta_interface: Optional[float] = None
    maturation_interface_score: Optional[float] = None
    maturation_rmsd: Optional[float] = None
    maturation_selected_delta_interface: Optional[float] = None
    maturation_selected_interface_score: Optional[float] = None
    maturation_selected_rmsd: Optional[float] = None
    maturation_nonselected_rmsd: Optional[float] = None
    ppiflow_primary_loop: Optional[str] = None
    ppiflow_primary_loop_rmsd: Optional[float] = None
    ppiflow_primary_loop_target_contact_delta: Optional[int] = None
    ppiflow_primary_loop_target_distance_delta: Optional[float] = None
    ppiflow_primary_loop_epitope_contact_delta: Optional[int] = None
    ppiflow_primary_loop_epitope_distance_delta: Optional[float] = None
    ppiflow_objective_mode: Optional[str] = None
    ppiflow_objective_score: Optional[float] = None
    ppiflow_filter_passed: Optional[bool] = None
    ppiflow_filter_reason: Optional[str] = None
    ppiflow_loop_metrics: Optional[Dict[str, Any]] = None
    
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class DesignList(BaseModel):
    designs: List[DesignResponse]
    total: int


class DesignQueryRequest(BaseModel):
    job_id: Optional[str] = None
    include_children: Optional[bool] = True
    design_ids: Optional[List[str]] = None
    q: Optional[str] = None
    backbone_id: Optional[int] = None
    plddt_min: Optional[float] = None
    pae_max: Optional[float] = None
    iptm_min: Optional[float] = None
    ipsae_min: Optional[float] = None
    epitope_contacts_min: Optional[int] = None
    target_contacts_min: Optional[int] = None
    epitope_max_dist: Optional[float] = None
    target_max_dist: Optional[float] = None
    binder_length_min: Optional[int] = None
    binder_length_max: Optional[int] = None
    cdr_h1_min: Optional[int] = None
    cdr_h1_max: Optional[int] = None
    cdr_h2_min: Optional[int] = None
    cdr_h2_max: Optional[int] = None
    cdr_h3_min: Optional[int] = None
    cdr_h3_max: Optional[int] = None
    rog_min: Optional[float] = None
    rog_max: Optional[float] = None
    rfd_rog_min: Optional[float] = None
    rfd_rog_max: Optional[float] = None
    favorites_only: Optional[bool] = False
    artifact_group: Optional[str] = None
    artifact_class: Optional[str] = None
    stage_family: Optional[str] = None
    source_stage_family: Optional[str] = None
    sort_by: Optional[str] = None
    sort_desc: Optional[bool] = True
    limit: Optional[int] = 100
    offset: Optional[int] = 0


class FavoriteUpdate(BaseModel):
    is_favorite: bool


class NotesUpdate(BaseModel):
    notes: str


class PlotlyMetricPoint(BaseModel):
    id: str
    name: str
    metrics: Dict[str, float]


class PlotlyMetricMetadata(BaseModel):
    label: str
    description: Optional[str] = None
    unit: Optional[str] = None
    source: Optional[str] = None
    semantics: Optional[str] = None
    higher_is_better: Optional[bool] = None
    color: Optional[str] = None


class PlotlyChartSuggestion(BaseModel):
    id: str
    label: str
    type: str
    xAxis: Optional[str] = None
    yAxis: Optional[str] = None
    zAxis: Optional[str] = None
    colorBy: Optional[str] = None
    description: Optional[str] = None


class PlotlyMetricsResponse(BaseModel):
    job_id: str
    metric_keys: List[str]
    points: List[PlotlyMetricPoint]
    total: int
    metric_metadata: Dict[str, PlotlyMetricMetadata] = {}
    chart_suggestions: List[PlotlyChartSuggestion] = []


class PlotlyMetricsRequest(BaseModel):
    include_children: bool = True
    design_ids: Optional[List[str]] = None
    limit: int = 10000
    offset: int = 0


ANALYTICS_LOAD_ONLY_COLUMNS = (
    Design.id,
    Design.name,
    Design.created_at,
    Design.plddt_overall,
    Design.plddt_binder,
    Design.plddt_target,
    Design.pae_interaction,
    Design.pae_overall,
    Design.rmsd_overall,
    Design.rmsd_binder,
    Design.rmsd_target,
    Design.fampnn_psce,
    Design.conf_score,
    Design.ptm,
    Design.iptm,
    Design.protein_iptm,
    Design.ligand_iptm,
    Design.complex_iplddt,
    Design.complex_ipde,
    Design.ipsae,
    Design.ipsae_binder_to_target,
    Design.ipsae_target_to_binder,
    Design.ipsae_d0chn,
    Design.ipsae_d0dom,
    Design.disorder,
    Design.num_recycles,
    Design.affinity_score,
    Design.binder_probability,
    Design.rog,
    Design.rfd_rog,
    Design.mpnn_score,
    Design.cdr_h1_length,
    Design.cdr_h2_length,
    Design.cdr_h3_length,
    Design.binder_length,
    Design.epitope_contact_count,
    Design.epitope_min_distance,
    Design.epitope_min_atom_distance,
    Design.epitope_centroid_distance,
    Design.target_contact_count,
    Design.target_min_distance,
    Design.target_min_atom_distance,
    Design.target_centroid_distance,
    Design.rfa_hotspot_covered_count,
    Design.rfa_hotspot_min_distance,
    Design.rfa_hotspot_avg_min_distance,
    Design.rfa_runtime_seconds,
    Design.rfa_plddt_initial,
    Design.rfa_plddt_final,
    Design.rfa_plddt_delta,
    Design.rfa_plddt_selected,
    Design.rfa_plddt_nonselected,
    Design.frustration_high_count,
    Design.frustration_min_count,
    Design.frustration_pct_high,
    Design.maturation_delta_interface,
    Design.maturation_interface_score,
    Design.maturation_rmsd,
    Design.maturation_selected_delta_interface,
    Design.maturation_selected_interface_score,
    Design.maturation_selected_rmsd,
    Design.maturation_nonselected_rmsd,
    Design.ppiflow_primary_loop,
    Design.ppiflow_primary_loop_rmsd,
    Design.ppiflow_primary_loop_target_contact_delta,
    Design.ppiflow_primary_loop_target_distance_delta,
    Design.ppiflow_primary_loop_epitope_contact_delta,
    Design.ppiflow_primary_loop_epitope_distance_delta,
    Design.ppiflow_objective_mode,
    Design.ppiflow_objective_score,
    Design.ppiflow_filter_passed,
    Design.ppiflow_filter_reason,
    Design.ppiflow_loop_metrics,
    Design.lineage_root_job_id,
    Design.parent_design_id,
    Design.origin_design_id,
    Design.origin_job_id,
    Design.origin_backbone_design_id,
    Design.stage_family,
    Design.stage_mode,
    Design.source_stage_job_id,
    Design.source_stage_family,
    Design.source_stage_mode,
    Design.source_pdb_path,
    Design.source_design_name,
    Design.source_stage_job_id,
    Design.source_stage_family,
    Design.source_stage_mode,
    Design.source_pdb_path,
    Design.source_design_name,
    Design.source_stage_job_id,
    Design.source_stage_family,
    Design.source_stage_mode,
    Design.source_pdb_path,
    Design.source_design_name,
    Design.selected_loop_scope,
    Design.provenance,
    Design.screening_reason,
    Design.has_clash,
    Design.confidence_metrics,
)

DESIGN_LIST_LOAD_ONLY_COLUMNS = (
    Design.id,
    Design.job_id,
    Design.name,
    Design.pdb_path,
    Design.num_helices,
    Design.num_strands,
    Design.rog,
    Design.rfd_rog,
    Design.mpnn_score,
    Design.fampnn_psce,
    Design.plddt_overall,
    Design.plddt_binder,
    Design.plddt_target,
    Design.pae_interaction,
    Design.pae_overall,
    Design.rmsd_overall,
    Design.rmsd_binder,
    Design.rmsd_target,
    Design.conf_score,
    Design.confidence_metrics,
    Design.ptm,
    Design.ligand_iptm,
    Design.affinity_score,
    Design.binder_probability,
    Design.iptm,
    Design.protein_iptm,
    Design.complex_iplddt,
    Design.complex_ipde,
    Design.disorder,
    Design.num_recycles,
    Design.has_clash,
    Design.ipsae,
    Design.ipsae_binder_to_target,
    Design.ipsae_target_to_binder,
    Design.ipsae_d0chn,
    Design.ipsae_d0dom,
    Design.ipsae_chain_pair,
    Design.binder_length,
    Design.antibody_type,
    Design.cdr_h1,
    Design.cdr_h2,
    Design.cdr_h3,
    Design.cdr_l1,
    Design.cdr_l2,
    Design.cdr_l3,
    Design.cdr_h1_length,
    Design.cdr_h2_length,
    Design.cdr_h3_length,
    Design.cdr_l1_length,
    Design.cdr_l2_length,
    Design.cdr_l3_length,
    Design.is_favorite,
    Design.notes,
    Design.backbone_id,
    Design.epitope_contact_count,
    Design.epitope_min_distance,
    Design.epitope_min_atom_distance,
    Design.epitope_nearest_antibody_residue,
    Design.epitope_nearest_target_residue,
    Design.epitope_nearest_antibody_atom,
    Design.epitope_nearest_target_atom,
    Design.epitope_mapping_mode,
    Design.epitope_centroid_distance,
    Design.target_contact_count,
    Design.target_min_distance,
    Design.target_min_atom_distance,
    Design.target_nearest_antibody_residue,
    Design.target_nearest_target_residue,
    Design.target_nearest_antibody_atom,
    Design.target_nearest_target_atom,
    Design.target_centroid_distance,
    Design.detected_antibody_chains,
    Design.detected_target_chain,
    Design.antibody_residue_count,
    Design.target_residue_count,
    Design.epitope_residue_count,
    Design.passed_screen,
    Design.screening_reason,
    Design.source_stage,
    Design.artifact_group,
    Design.artifact_class,
    Design.artifact_schema_version,
    Design.rfa_hotspot_covered_count,
    Design.rfa_hotspot_min_distance,
    Design.rfa_hotspot_avg_min_distance,
    Design.rfa_runtime_seconds,
    Design.rfa_device,
    Design.rfa_diffusion_steps,
    Design.rfa_noise_scale_ca,
    Design.rfa_noise_scale_frame,
    Design.rfa_guide_scale,
    Design.rfa_plddt_initial,
    Design.rfa_plddt_final,
    Design.rfa_plddt_delta,
    Design.rfa_plddt_selected,
    Design.rfa_plddt_nonselected,
    Design.fr2_contacts,
    Design.de_loop,
    Design.fr3_contacts,
    Design.fr4_contacts,
    Design.frustration_high_count,
    Design.frustration_min_count,
    Design.frustration_pct_high,
    Design.maturation_delta_interface,
    Design.maturation_interface_score,
    Design.maturation_rmsd,
    Design.maturation_selected_delta_interface,
    Design.maturation_selected_interface_score,
    Design.maturation_selected_rmsd,
    Design.maturation_nonselected_rmsd,
    Design.ppiflow_primary_loop,
    Design.ppiflow_primary_loop_rmsd,
    Design.ppiflow_primary_loop_target_contact_delta,
    Design.ppiflow_primary_loop_target_distance_delta,
    Design.ppiflow_primary_loop_epitope_contact_delta,
    Design.ppiflow_primary_loop_epitope_distance_delta,
    Design.ppiflow_objective_mode,
    Design.ppiflow_objective_score,
    Design.ppiflow_filter_passed,
    Design.ppiflow_filter_reason,
    Design.ppiflow_loop_metrics,
    Design.lineage_root_job_id,
    Design.parent_design_id,
    Design.origin_design_id,
    Design.origin_job_id,
    Design.origin_backbone_design_id,
    Design.stage_family,
    Design.stage_mode,
    Design.source_stage_job_id,
    Design.source_stage_family,
    Design.source_stage_mode,
    Design.source_pdb_path,
    Design.source_design_name,
    Design.selected_loop_scope,
    Design.provenance,
    Design.created_at,
)

BACKBONE_SUMMARY_LOAD_ONLY_COLUMNS = (
    Design.id,
    Design.name,
    Design.pdb_path,
    Design.created_at,
    Design.backbone_id,
    Design.plddt_overall,
    Design.iptm,
    Design.ptm,
    Design.pae_overall,
    Design.target_contact_count,
    Design.epitope_contact_count,
    Design.target_min_distance,
    Design.epitope_min_distance,
    Design.cdr_h1_length,
    Design.cdr_h2_length,
    Design.cdr_h3_length,
    Design.rfa_hotspot_covered_count,
    Design.lineage_root_job_id,
    Design.parent_design_id,
    Design.origin_design_id,
    Design.origin_job_id,
    Design.origin_backbone_design_id,
    Design.stage_family,
    Design.stage_mode,
    Design.source_stage_job_id,
    Design.source_stage_family,
    Design.source_stage_mode,
    Design.source_pdb_path,
    Design.source_design_name,
    Design.selected_loop_scope,
    Design.provenance,
)


def _append_numeric_values(value: Any, out: List[float]) -> None:
    """Recursively collect finite numeric values from nested JSON-like structures."""
    if isinstance(value, bool):
        out.append(1.0 if value else 0.0)
        return
    if isinstance(value, (int, float)):
        val = float(value)
        if math.isfinite(val):
            out.append(val)
        return
    if isinstance(value, list):
        for item in value:
            _append_numeric_values(item, out)
        return
    if isinstance(value, dict):
        for item in value.values():
            _append_numeric_values(item, out)


def _inject_metric(metrics: Dict[str, float], key: str, value: Any) -> None:
    """Insert a scalar metric, or summarize nested metric values for plotting."""
    if isinstance(value, bool):
        metrics[key] = 1.0 if value else 0.0
        return
    if isinstance(value, (int, float)):
        val = float(value)
        if math.isfinite(val):
            metrics[key] = val
        return

    flattened: List[float] = []
    _append_numeric_values(value, flattened)
    if not flattened:
        return

    metrics[f"{key}_mean"] = float(sum(flattened) / len(flattened))
    metrics[f"{key}_min"] = float(min(flattened))
    metrics[f"{key}_max"] = float(max(flattened))
    metrics[f"{key}_n"] = float(len(flattened))


def _build_plotly_metrics(design: Design) -> Dict[str, float]:
    """Build a dense, plot-ready numeric metric map for a design."""
    metrics: Dict[str, float] = {}

    base_metrics = {
        "plddt_overall": design.plddt_overall,
        "plddt_binder": design.plddt_binder,
        "plddt_target": design.plddt_target,
        "pae_interaction": design.pae_interaction,
        "pae_overall": design.pae_overall,
        "rmsd_overall": design.rmsd_overall,
        "rmsd_binder": design.rmsd_binder,
        "rmsd_target": design.rmsd_target,
        "fampnn_psce": design.fampnn_psce,
        "conf_score": design.conf_score,
        "ptm": design.ptm,
        "iptm": design.iptm,
        "protein_iptm": design.protein_iptm,
        "ligand_iptm": design.ligand_iptm,
        "complex_iplddt": design.complex_iplddt,
        "complex_ipde": design.complex_ipde,
        "ipsae": design.ipsae,
        "ipsae_binder_to_target": design.ipsae_binder_to_target,
        "ipsae_target_to_binder": design.ipsae_target_to_binder,
        "ipsae_d0chn": design.ipsae_d0chn,
        "ipsae_d0dom": design.ipsae_d0dom,
        "disorder": design.disorder,
        "num_recycles": design.num_recycles,
        "affinity_score": design.affinity_score,
        "binder_probability": design.binder_probability,
        "rog": design.rog,
        "rfd_rog": design.rfd_rog,
        "mpnn_score": design.mpnn_score,
        "cdr_h1_length": design.cdr_h1_length,
        "cdr_h2_length": design.cdr_h2_length,
        "cdr_h3_length": design.cdr_h3_length,
        "binder_length": design.binder_length,
        "epitope_contact_count": design.epitope_contact_count,
        "epitope_min_distance": design.epitope_min_distance,
        "epitope_min_atom_distance": design.epitope_min_atom_distance,
        "epitope_centroid_distance": design.epitope_centroid_distance,
        "target_contact_count": design.target_contact_count,
        "target_min_distance": design.target_min_distance,
        "target_min_atom_distance": design.target_min_atom_distance,
        "target_centroid_distance": design.target_centroid_distance,
        "rfa_hotspot_covered_count": design.rfa_hotspot_covered_count,
        "rfa_hotspot_min_distance": design.rfa_hotspot_min_distance,
        "rfa_hotspot_avg_min_distance": design.rfa_hotspot_avg_min_distance,
        "rfa_runtime_seconds": design.rfa_runtime_seconds,
        "rfa_plddt_initial": design.rfa_plddt_initial,
        "rfa_plddt_final": design.rfa_plddt_final,
        "rfa_plddt_delta": design.rfa_plddt_delta,
        "rfa_plddt_selected": design.rfa_plddt_selected,
        "rfa_plddt_nonselected": design.rfa_plddt_nonselected,
        "frustration_high_count": design.frustration_high_count,
        "frustration_min_count": design.frustration_min_count,
        "frustration_pct_high": design.frustration_pct_high,
        "maturation_delta_interface": design.maturation_delta_interface,
        "maturation_interface_score": design.maturation_interface_score,
        "maturation_rmsd": design.maturation_rmsd,
        "maturation_selected_delta_interface": design.maturation_selected_delta_interface,
        "maturation_selected_interface_score": design.maturation_selected_interface_score,
        "maturation_selected_rmsd": design.maturation_selected_rmsd,
        "maturation_nonselected_rmsd": design.maturation_nonselected_rmsd,
        "ppiflow_primary_loop_rmsd": design.ppiflow_primary_loop_rmsd,
        "ppiflow_primary_loop_target_contact_delta": design.ppiflow_primary_loop_target_contact_delta,
        "ppiflow_primary_loop_target_distance_delta": design.ppiflow_primary_loop_target_distance_delta,
        "ppiflow_primary_loop_epitope_contact_delta": design.ppiflow_primary_loop_epitope_contact_delta,
        "ppiflow_primary_loop_epitope_distance_delta": design.ppiflow_primary_loop_epitope_distance_delta,
        "ppiflow_objective_score": design.ppiflow_objective_score,
    }
    _inject_metric(metrics, "screening_reason_present", 1.0 if design.screening_reason else None)
    for key, value in base_metrics.items():
        _inject_metric(metrics, key, value)
    if design.has_clash is not None:
        metrics["has_clash"] = 1.0 if design.has_clash else 0.0

    raw_conf = design.confidence_metrics if isinstance(design.confidence_metrics, dict) else {}
    for key, value in raw_conf.items():
        _inject_metric(metrics, key, value)

    confornets_sample = raw_conf.get("confornets_sample") if isinstance(raw_conf.get("confornets_sample"), dict) else {}
    confornets_ensemble = raw_conf.get("confornets_ensemble") if isinstance(raw_conf.get("confornets_ensemble"), dict) else {}
    confornets_manifest = raw_conf.get("confornets_artifact_manifest") if isinstance(raw_conf.get("confornets_artifact_manifest"), dict) else {}
    confornets_landscape = raw_conf.get("confornets_landscape") if isinstance(raw_conf.get("confornets_landscape"), dict) else {}
    confornets_training = raw_conf.get("confornets_training_loss_summary") if isinstance(raw_conf.get("confornets_training_loss_summary"), dict) else {}
    confornets_confidence = raw_conf.get("confornets_confidence") if isinstance(raw_conf.get("confornets_confidence"), dict) else {}
    confornets_reference = raw_conf.get("confornets_reference_evaluation") if isinstance(raw_conf.get("confornets_reference_evaluation"), dict) else {}
    confornets_pairwise = raw_conf.get("confornets_pairwise_diversity") if isinstance(raw_conf.get("confornets_pairwise_diversity"), dict) else {}
    confornets_landscape_point = raw_conf.get("confornets_landscape_point") if isinstance(raw_conf.get("confornets_landscape_point"), dict) else {}
    confornets_evaluation_summary = raw_conf.get("confornets_evaluation_summary") if isinstance(raw_conf.get("confornets_evaluation_summary"), dict) else {}
    confornets_sample_index = confornets_sample.get(
        "sample_index",
        confornets_sample.get(
            "frame_index",
            confornets_ensemble.get("sample_index", confornets_ensemble.get("frame_index")),
        ),
    )
    confornets_metrics = {
        "confornets_sample_index": confornets_sample_index,
        "confornets_frame_index": confornets_sample.get("frame_index", confornets_ensemble.get("frame_index")),
        "confornets_bytes": confornets_sample.get("bytes"),
        "confornets_sample_count": confornets_manifest.get("sample_count", confornets_landscape.get("sample_count")),
        "confornets_training_first_loss": confornets_training.get("first_loss"),
        "confornets_training_final_loss": confornets_training.get("final_loss"),
        "confornets_training_min_loss": confornets_training.get("min_loss"),
        "confornets_training_max_loss": confornets_training.get("max_loss"),
        "confornets_training_step_count": confornets_training.get("row_count"),
        "confornets_training_first_step": confornets_training.get("first_step"),
        "confornets_training_last_step": confornets_training.get("last_step"),
        "confornets_confidence_plddt": confornets_confidence.get("plddt"),
        "confornets_confidence_gpde": confornets_confidence.get("gpde"),
        "confornets_confidence_ptm": confornets_confidence.get("ptm"),
        "confornets_confidence_iptm": confornets_confidence.get("iptm"),
        "confornets_min_reference_rmsd": confornets_reference.get("min_reference_rmsd"),
        "confornets_reference_success_at_1": confornets_reference.get("success_at_1"),
        "confornets_pairwise_min_rmsd": confornets_pairwise.get("min_pairwise_rmsd"),
        "confornets_pairwise_mean_rmsd": confornets_pairwise.get("mean_pairwise_rmsd"),
        "confornets_pairwise_max_rmsd": confornets_pairwise.get("max_pairwise_rmsd"),
        "confornets_landscape_x": confornets_landscape_point.get("x"),
        "confornets_landscape_y": confornets_landscape_point.get("y"),
        "confornets_success_at_1_rate": confornets_evaluation_summary.get("success_at_1_rate"),
        "confornets_reference_count": confornets_evaluation_summary.get("reference_count"),
        "confornets_rmsd_threshold": confornets_evaluation_summary.get("rmsd_threshold"),
    }
    for key, value in confornets_metrics.items():
        _inject_metric(metrics, key, value)

    provenance = design.provenance if isinstance(design.provenance, dict) else {}
    ppiflow = provenance.get("ppiflow") if isinstance(provenance.get("ppiflow"), dict) else {}
    ppiflow_score = (
        ppiflow.get("maturation_score") if isinstance(ppiflow.get("maturation_score"), dict)
        else ppiflow.get("partial_flow_score") if isinstance(ppiflow.get("partial_flow_score"), dict)
        else None
    ) or {}
    ppiflow_filter = ppiflow.get("maturation_filter") if isinstance(ppiflow.get("maturation_filter"), dict) else {}
    ppiflow_anchors = ppiflow.get("anchors") if isinstance(ppiflow.get("anchors"), dict) else {}
    ppiflow_interface = ppiflow.get("interface_score") if isinstance(ppiflow.get("interface_score"), dict) else {}

    ppiflow_sample_index = ppiflow.get("sample_index")
    if ppiflow_sample_index is None and isinstance(design.name, str):
        import re
        sample_match = re.search(r"_ppiflow_sample(\d+)$", design.name, re.IGNORECASE)
        if sample_match:
            try:
                ppiflow_sample_index = int(sample_match.group(1))
            except ValueError:
                ppiflow_sample_index = None

    ppiflow_metrics = {
        "ppiflow_sample_index": ppiflow_sample_index,
        "ppiflow_interface_score_original": ppiflow_score.get("interface_score_original"),
        "ppiflow_interface_score_matured": ppiflow_score.get("interface_score_matured"),
        "ppiflow_selected_interface_score_original": ppiflow_score.get("selected_interface_score_original"),
        "ppiflow_selected_interface_score_matured": ppiflow_score.get("selected_interface_score_matured"),
        "ppiflow_selected_delta_interface_score": ppiflow_score.get("selected_delta_interface_score"),
        "ppiflow_selected_rmsd_backbone": ppiflow_score.get("selected_rmsd_backbone"),
        "ppiflow_nonselected_rmsd_backbone": ppiflow_score.get("nonselected_rmsd_backbone"),
        "ppiflow_sequence_identity": ppiflow_score.get("sequence_identity"),
        "ppiflow_clash_count_ca": ppiflow_score.get("clash_count_ca"),
        "ppiflow_interface_residue_count_original": ppiflow_score.get("interface_residue_count_original"),
        "ppiflow_interface_residue_count_matured": ppiflow_score.get("interface_residue_count_matured"),
        "ppiflow_selected_interface_residue_count_original": ppiflow_score.get("selected_interface_residue_count_original"),
        "ppiflow_selected_interface_residue_count_matured": ppiflow_score.get("selected_interface_residue_count_matured"),
        "ppiflow_filter_threshold": ppiflow_filter.get("threshold"),
        "ppiflow_filter_passed": design.ppiflow_filter_passed if design.ppiflow_filter_passed is not None else ppiflow_filter.get("passed"),
        "ppiflow_anchor_count": ppiflow_anchors.get("anchor_count"),
        "ppiflow_anchor_interface_residue_count": ppiflow_anchors.get("interface_residue_count"),
        "ppiflow_source_interface_score": ppiflow_interface.get("interface_score"),
        "ppiflow_source_interface_residue_count": ppiflow_interface.get("interface_residue_count"),
        "ppiflow_primary_loop_target_contact_delta": design.ppiflow_primary_loop_target_contact_delta if design.ppiflow_primary_loop_target_contact_delta is not None else ppiflow_score.get("primary_loop_target_contact_delta"),
        "ppiflow_primary_loop_target_distance_delta": design.ppiflow_primary_loop_target_distance_delta if design.ppiflow_primary_loop_target_distance_delta is not None else ppiflow_score.get("primary_loop_target_distance_delta"),
        "ppiflow_primary_loop_epitope_contact_delta": design.ppiflow_primary_loop_epitope_contact_delta if design.ppiflow_primary_loop_epitope_contact_delta is not None else ppiflow_score.get("primary_loop_epitope_contact_delta"),
        "ppiflow_primary_loop_epitope_distance_delta": design.ppiflow_primary_loop_epitope_distance_delta if design.ppiflow_primary_loop_epitope_distance_delta is not None else ppiflow_score.get("primary_loop_epitope_distance_delta"),
        "ppiflow_primary_loop_rmsd": design.ppiflow_primary_loop_rmsd if design.ppiflow_primary_loop_rmsd is not None else ppiflow_score.get("primary_loop_rmsd"),
        "ppiflow_objective_score": design.ppiflow_objective_score if design.ppiflow_objective_score is not None else ppiflow_score.get("objective_score"),
    }
    for key, value in ppiflow_metrics.items():
        _inject_metric(metrics, key, value)

    return metrics


_CONFORNETS_PLOTLY_METADATA: Dict[str, Dict[str, Any]] = {
    "confornets_sample_index": {
        "label": "ConforNets sample index",
        "description": "Zero-based independent generated conformer sample index. This is a sample selector, not a time-resolved simulation frame.",
        "source": "bms_wrapper",
        "semantics": "independent_generated_conformer_sample",
    },
    "confornets_frame_index": {
        "label": "ConforNets frame index",
        "description": "Legacy zero-based index carried by upstream/sample manifests; equivalent to sample index for current BMS ConforNets outputs.",
        "source": "bms_wrapper",
        "semantics": "independent_generated_conformer_sample",
    },
    "confornets_bytes": {
        "label": "Conformer file size",
        "description": "Size of the final normalized conformer structure artifact.",
        "unit": "bytes",
        "source": "bms_wrapper",
    },
    "confornets_sample_count": {
        "label": "ConforNets sample count",
        "description": "Number of final ConforNets conformer samples recorded in the final artifact manifest or landscape payload.",
        "source": "bms_wrapper",
    },
    "confornets_training_first_loss": {
        "label": "ConforNets first training loss",
        "description": "First parsed loss value from ConforNets training_loss.csv.",
        "source": "upstream_confornets",
        "higher_is_better": False,
    },
    "confornets_training_final_loss": {
        "label": "ConforNets final training loss",
        "description": "Final parsed loss value from ConforNets training_loss.csv.",
        "source": "upstream_confornets",
        "higher_is_better": False,
    },
    "confornets_training_min_loss": {
        "label": "ConforNets minimum training loss",
        "description": "Minimum parsed loss value from ConforNets training_loss.csv.",
        "source": "upstream_confornets",
        "higher_is_better": False,
    },
    "confornets_training_max_loss": {
        "label": "ConforNets maximum training loss",
        "description": "Maximum parsed loss value from ConforNets training_loss.csv.",
        "source": "upstream_confornets",
        "higher_is_better": False,
    },
    "confornets_training_step_count": {
        "label": "ConforNets training-loss rows",
        "description": "Number of parsed rows in ConforNets training_loss.csv.",
        "source": "bms_wrapper",
    },
    "confornets_training_first_step": {
        "label": "ConforNets first training step",
        "description": "First parsed step/iteration/epoch in ConforNets training_loss.csv.",
        "source": "upstream_confornets",
    },
    "confornets_training_last_step": {
        "label": "ConforNets last training step",
        "description": "Last parsed step/iteration/epoch in ConforNets training_loss.csv.",
        "source": "upstream_confornets",
    },
    "confornets_confidence_plddt": {
        "label": "ConforNets scalar pLDDT",
        "description": "Scalar pLDDT reported by ConforNets/OpenFold3 confidence evaluation for this sample. It is not a per-residue tensor unless a full confidence tensor artifact is present.",
        "source": "upstream_confornets",
        "semantics": "sample_scalar_confidence",
        "higher_is_better": True,
    },
    "confornets_confidence_gpde": {
        "label": "ConforNets gPDE",
        "description": "Global predicted distance error reported by ConforNets/OpenFold3 confidence evaluation for this sample.",
        "source": "upstream_confornets",
        "semantics": "sample_scalar_error",
        "higher_is_better": False,
    },
    "confornets_confidence_ptm": {
        "label": "ConforNets pTM",
        "description": "Predicted TM-score reported by ConforNets/OpenFold3 confidence evaluation for this sample.",
        "source": "upstream_confornets",
        "semantics": "sample_scalar_confidence",
        "higher_is_better": True,
    },
    "confornets_confidence_iptm": {
        "label": "ConforNets iPTM",
        "description": "Interface pTM if produced by the confidence path. For current monomer-only ConforNets this is usually absent.",
        "source": "upstream_confornets",
        "semantics": "sample_scalar_confidence",
        "higher_is_better": True,
    },
    "confornets_min_reference_rmsd": {
        "label": "Nearest staged-reference Cα RMSD",
        "description": "Minimum ordered Cα RMSD after Kabsch alignment between this generated sample and the staged reference structures. Only meaningful when references were supplied and evaluation was enabled.",
        "unit": "Å",
        "source": "bms_wrapper",
        "semantics": "reference_conditioned_evaluation",
        "higher_is_better": False,
    },
    "confornets_reference_success_at_1": {
        "label": "Reference success@1",
        "description": "Whether this sample met the configured RMSD threshold to its nearest staged reference.",
        "source": "bms_wrapper",
        "semantics": "reference_conditioned_evaluation",
        "higher_is_better": True,
    },
    "confornets_pairwise_min_rmsd": {
        "label": "Post-hoc pairwise minimum RMSD",
        "description": "Minimum post-hoc pairwise Cα RMSD from this sample to other generated samples; not a thermodynamic trajectory statistic.",
        "unit": "Å",
        "source": "bms_wrapper",
        "semantics": "post_hoc_sample_space_diversity",
    },
    "confornets_pairwise_mean_rmsd": {
        "label": "Post-hoc pairwise sample RMSD",
        "description": "Mean post-hoc pairwise Cα RMSD from this sample to other generated samples; not a thermodynamic trajectory statistic.",
        "unit": "Å",
        "source": "bms_wrapper",
        "semantics": "post_hoc_sample_space_diversity",
    },
    "confornets_pairwise_max_rmsd": {
        "label": "Post-hoc pairwise maximum RMSD",
        "description": "Maximum post-hoc pairwise Cα RMSD from this sample to other generated samples; not a thermodynamic trajectory statistic.",
        "unit": "Å",
        "source": "bms_wrapper",
        "semantics": "post_hoc_sample_space_diversity",
    },
    "confornets_landscape_x": {
        "label": "ConforNets landscape X",
        "description": "Post-hoc 2D sample-landscape coordinate derived from pairwise RMSD/MDS in the BMS wrapper. This is not calibrated thermodynamics.",
        "source": "bms_wrapper",
        "semantics": "post_hoc_sample_space_embedding",
    },
    "confornets_landscape_y": {
        "label": "ConforNets landscape Y",
        "description": "Post-hoc 2D sample-landscape coordinate derived from pairwise RMSD/MDS in the BMS wrapper. This is not calibrated thermodynamics.",
        "source": "bms_wrapper",
        "semantics": "post_hoc_sample_space_embedding",
    },
    "confornets_success_at_1_rate": {
        "label": "ConforNets success@1 rate",
        "description": "Aggregate fraction of samples meeting the configured staged-reference RMSD threshold.",
        "source": "bms_wrapper",
        "semantics": "reference_conditioned_evaluation_summary",
        "higher_is_better": True,
    },
    "confornets_reference_count": {
        "label": "Staged reference count",
        "description": "Number of reference structures used for ConforNets reference RMSD evaluation.",
        "source": "bms_wrapper",
        "semantics": "reference_conditioned_evaluation_summary",
    },
    "confornets_rmsd_threshold": {
        "label": "Reference RMSD threshold",
        "description": "Configured Cα RMSD threshold used for success@1 reporting.",
        "unit": "Å",
        "source": "bms_wrapper",
        "semantics": "reference_conditioned_evaluation_summary",
    },
}


_BASE_PLOTLY_METADATA: Dict[str, Dict[str, Any]] = {
    "plddt_overall": {"label": "pLDDT", "description": "Overall predicted local distance difference test score.", "higher_is_better": True},
    "pae_overall": {"label": "PAE", "description": "Overall predicted aligned error.", "unit": "Å", "higher_is_better": False},
    "pae_interaction": {"label": "Interaction PAE", "description": "Predicted aligned error across the modeled interface.", "unit": "Å", "higher_is_better": False},
    "ptm": {"label": "pTM", "description": "Predicted TM-score.", "higher_is_better": True},
    "iptm": {"label": "iPTM", "description": "Predicted interface TM-score.", "higher_is_better": True},
    "conf_score": {"label": "Confidence score", "description": "Model-native confidence score when available.", "higher_is_better": True},
    "rmsd_overall": {"label": "RMSD", "description": "Overall root-mean-square deviation.", "unit": "Å", "higher_is_better": False},
    "rog": {"label": "Radius of gyration", "description": "Structure radius of gyration.", "unit": "Å"},
}


def _fallback_plotly_metric_label(key: str) -> str:
    label = key
    for suffix, rendered in (("_mean", " (mean)"), ("_min", " (min)"), ("_max", " (max)"), ("_n", " (n)")):
        if label.endswith(suffix):
            label = f"{label[:-len(suffix)]}{rendered}"
            break
    return " ".join(part.capitalize() for part in label.replace("_", " ").split())


def _build_plotly_metric_metadata(metric_keys: Any) -> Dict[str, Dict[str, Any]]:
    """Return label/source/semantics metadata for available plot-ready metric keys."""
    metadata: Dict[str, Dict[str, Any]] = {}
    for key in sorted(str(metric_key) for metric_key in metric_keys if metric_key):
        configured = _CONFORNETS_PLOTLY_METADATA.get(key) or _BASE_PLOTLY_METADATA.get(key)
        if configured:
            metadata[key] = dict(configured)
            continue
        source = "confidence_metrics" if key.startswith("confornets_") or "_mean" in key or "_min" in key or "_max" in key else "design"
        metadata[key] = {
            "label": _fallback_plotly_metric_label(key),
            "description": f"Numeric design metric '{key}' exposed for exploratory Plotly charting.",
            "source": source,
        }
    return metadata


def _build_plotly_chart_suggestions(metric_keys: Any) -> List[Dict[str, Any]]:
    """Suggest useful Plotly chart presets for the metric keys actually present."""
    keys = {str(metric_key) for metric_key in metric_keys if metric_key}
    suggestions: List[Dict[str, Any]] = []

    def has(*required: str) -> bool:
        return all(key in keys for key in required)

    if has("confornets_min_reference_rmsd", "confornets_confidence_plddt"):
        suggestions.append({
            "id": "confornets_reference_confidence",
            "label": "ConforNets reference RMSD vs confidence",
            "type": "scatter",
            "xAxis": "confornets_min_reference_rmsd",
            "yAxis": "confornets_confidence_plddt",
            "colorBy": "confornets_confidence_gpde" if "confornets_confidence_gpde" in keys else "confornets_sample_index",
            "description": "Compare staged-reference Cα RMSD against ConforNets scalar confidence for generated samples.",
        })
    if has("confornets_landscape_x", "confornets_landscape_y"):
        suggestions.append({
            "id": "confornets_sample_landscape",
            "label": "ConforNets post-hoc sample landscape",
            "type": "scatter",
            "xAxis": "confornets_landscape_x",
            "yAxis": "confornets_landscape_y",
            "colorBy": "confornets_min_reference_rmsd" if "confornets_min_reference_rmsd" in keys else "confornets_sample_index",
            "description": "Plot post-hoc RMSD/MDS sample-space coordinates. This is an exploratory embedding, not a thermodynamic trajectory.",
        })
    if has("confornets_sample_index", "confornets_training_final_loss"):
        suggestions.append({
            "id": "confornets_sample_training_loss",
            "label": "ConforNets sample index vs final loss",
            "type": "scatter",
            "xAxis": "confornets_sample_index",
            "yAxis": "confornets_training_final_loss",
            "colorBy": "confornets_confidence_plddt" if "confornets_confidence_plddt" in keys else None,
            "description": "Check whether final loss varies across generated sample rows for the ingested ConforNets run.",
        })
    return suggestions


def _normalize_chain_scalar_map(raw: Any) -> Dict[str, float]:
    """Normalize dict/list chain scalar maps (e.g. chains_ptm) into string-keyed dicts."""
    normalized: Dict[str, float] = {}
    if isinstance(raw, list):
        for idx, value in enumerate(raw):
            try:
                val = float(value)
                if math.isfinite(val):
                    normalized[str(idx)] = val
            except (TypeError, ValueError):
                continue
        return normalized
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                val = float(value)
                if math.isfinite(val):
                    normalized[str(key)] = val
            except (TypeError, ValueError):
                continue
    return normalized


def _normalize_chain_matrix(raw: Any) -> Dict[str, Dict[str, float]]:
    """Normalize dict/list chain matrices into nested string-keyed dicts."""
    normalized: Dict[str, Dict[str, float]] = {}

    if isinstance(raw, list):
        for row_idx, row in enumerate(raw):
            if not isinstance(row, list):
                continue
            row_key = str(row_idx)
            normalized[row_key] = {}
            for col_idx, value in enumerate(row):
                try:
                    val = float(value)
                    if math.isfinite(val):
                        normalized[row_key][str(col_idx)] = val
                except (TypeError, ValueError):
                    continue
        return normalized

    if isinstance(raw, dict):
        for row_key, row in raw.items():
            row_dict: Dict[str, float] = {}
            if isinstance(row, dict):
                for col_key, value in row.items():
                    try:
                        val = float(value)
                        if math.isfinite(val):
                            row_dict[str(col_key)] = val
                    except (TypeError, ValueError):
                        continue
            elif isinstance(row, list):
                for col_idx, value in enumerate(row):
                    try:
                        val = float(value)
                        if math.isfinite(val):
                            row_dict[str(col_idx)] = val
                    except (TypeError, ValueError):
                        continue
            normalized[str(row_key)] = row_dict
    return normalized


def _chain_label(chain_idx: str) -> str:
    """Render numeric chain indices as A/B/C labels for readability."""
    if chain_idx.isdigit():
        idx = int(chain_idx)
        if 0 <= idx < 26:
            return f"Chain {chr(65 + idx)}"
    return f"Chain {chain_idx}"


def _round_nullable(value: Optional[float], digits: int) -> Optional[float]:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return round(numeric, digits)


def _numeric_record_value(record: Optional[Dict[str, Any]], *keys: str) -> Optional[float]:
    if not isinstance(record, dict):
        return None
    for key in keys:
        value = record.get(key)
        if isinstance(value, (int, float)):
            numeric = float(value)
            if math.isfinite(numeric):
                return numeric
    return None


def _text_record_value(record: Optional[Dict[str, Any]], *keys: str) -> Optional[str]:
    if not isinstance(record, dict):
        return None
    for key in keys:
        value = record.get(key)
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return None


def _normalize_stage_token(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    return text or None


def _infer_design_result_set(
    *,
    stage_family: Any,
    stage_mode: Any,
    artifact_class: Any,
    ppiflow_filter_passed: Any,
    passed_screen: Any,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return durable selector metadata for model-call result layers.

    Derived from stage/artifact/pass fields instead of filenames so old rows can
    be backfilled in API responses before every row has been reingested.
    """
    family = _normalize_stage_token(stage_family)
    mode = _normalize_stage_token(stage_mode)
    normalized_artifact = normalize_antibody_artifact_class(artifact_class)
    if normalized_artifact is None:
        normalized_artifact = normalize_antibody_artifact_class(
            infer_antibody_artifact_class_from_stage(family, mode)
        )

    if family == "rfantibody" or normalized_artifact == "backbone_complex":
        return normalized_artifact, "rfantibody_backbones", "RFA/backbone"

    if family in {"boltzgen", "fampnn", "proteinmpnn", "antifold", "frustrampnn", "caliby"}:
        return normalized_artifact, "sequence_designs", "Sequence designs"

    if family == "ppiflow" or (mode and ("ppiflow" in mode or "maturation" in mode)):
        passed = ppiflow_filter_passed
        if passed is None:
            passed = passed_screen
        if passed is True:
            return normalized_artifact, "ppiflow_passed", "PPIFlow passed"
        if passed is False:
            return normalized_artifact, "ppiflow_rejected", "PPIFlow rejected"
        return normalized_artifact, "ppiflow_candidates", "PPIFlow candidates"

    if normalized_artifact == "sequence_designed_complex":
        return normalized_artifact, "sequence_designs", "Sequence designs"
    if normalized_artifact == "validated_complex":
        return normalized_artifact, "validated", "Validated"
    if normalized_artifact == "post_validation_refined_complex":
        return normalized_artifact, "post_validation_refined", "Post-validation refined"

    return normalized_artifact, None, None


def _fampnn_payload_records(design: Design) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    try:
        state = sa_inspect(design)
        unloaded = set(state.unloaded)
    except NoInspectionAvailable:
        unloaded = set()
    provenance_value = None if "provenance" in unloaded else getattr(design, "provenance", None)
    confidence_value = None if "confidence_metrics" in unloaded else getattr(design, "confidence_metrics", None)
    provenance = provenance_value if isinstance(provenance_value, dict) else {}
    ppiflow = provenance.get("ppiflow") if isinstance(provenance.get("ppiflow"), dict) else {}
    confidence = confidence_value if isinstance(confidence_value, dict) else {}
    for candidate in (provenance.get("fampnn"), ppiflow.get("fampnn"), confidence.get("fampnn")):
        if isinstance(candidate, dict):
            records.append(candidate)
    return records


def _import_payload_records(design: Design) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    try:
        state = sa_inspect(design)
        unloaded = set(state.unloaded)
    except NoInspectionAvailable:
        unloaded = set()
    provenance_value = None if "provenance" in unloaded else getattr(design, "provenance", None)
    confidence_value = None if "confidence_metrics" in unloaded else getattr(design, "confidence_metrics", None)
    artifact_class_value = None if "artifact_class" in unloaded else getattr(design, "artifact_class", None)
    stage_mode_value = None if "stage_mode" in unloaded else getattr(design, "stage_mode", None)

    provenance = provenance_value if isinstance(provenance_value, dict) else {}
    confidence = confidence_value if isinstance(confidence_value, dict) else {}
    artifact_class = str(artifact_class_value or "").strip().lower()
    stage_mode = str(stage_mode_value or "").strip().lower()
    imported_hint = artifact_class.startswith("imported") or "import" in stage_mode or bool(provenance.get("import_source"))

    if provenance and (
        imported_hint
        or provenance.get("sequence")
        or provenance.get("length_aa")
        or provenance.get("binder_sequence")
        or provenance.get("binder_length")
    ):
        records.append(provenance)

    for value in confidence.values():
        if not isinstance(value, dict):
            continue
        if any(key in value for key in ("sequence", "length_aa", "binder_sequence", "binder_length", "raw_evaluations")):
            records.append(value)

    return records


def _normalize_import_source_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    aliases = {
        "external_import": "external",
        "external-import": "external",
        "competition_import": "competition",
        "competition-import": "competition",
    }
    return aliases.get(text, text)


def _format_import_source_label(value: Optional[str]) -> Optional[str]:
    if not value or value == "external":
        return None
    labels = {
        "competition": "Competition",
        "dataset": "Dataset",
    }
    return labels.get(value, value.replace("_", " ").replace("-", " ").title())


def _format_import_method_label(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    labels = {
        "boltz2": "Boltz2",
        "esmfold": "ESMFold",
    }
    return labels.get(value, value.replace("_", " ").replace("-", " ").title())


def _iter_import_metric_names(design: Design) -> List[str]:
    metric_names: List[str] = []
    try:
        state = sa_inspect(design)
        unloaded = set(state.unloaded)
    except NoInspectionAvailable:
        unloaded = set()

    confidence_value = None if "confidence_metrics" in unloaded else getattr(design, "confidence_metrics", None)
    confidence = confidence_value if isinstance(confidence_value, dict) else {}
    for key in confidence.keys():
        key_text = str(key or "").strip().lower()
        if key_text:
            metric_names.append(key_text)

    for record in _import_payload_records(design):
        raw_evaluations = record.get("raw_evaluations")
        if isinstance(raw_evaluations, list):
            for evaluation in raw_evaluations:
                if not isinstance(evaluation, dict):
                    continue
                metric_name = str(evaluation.get("metric") or "").strip().lower()
                if metric_name:
                    metric_names.append(metric_name)

        for key in record.keys():
            key_text = str(key or "").strip().lower()
            if key_text:
                metric_names.append(key_text)

    return metric_names


def _has_boltz2_import_metrics(design: Design) -> bool:
    for field_name in (
        "ptm",
        "iptm",
        "ipsae",
        "complex_iplddt",
        "complex_ipde",
        "protein_iptm",
        "ligand_iptm",
    ):
        value = getattr(design, field_name, None)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return True

    return any(metric_name.startswith("boltz2_") for metric_name in _iter_import_metric_names(design))


def _compute_import_metadata(design: Design) -> Dict[str, Any]:
    try:
        state = sa_inspect(design)
        unloaded = set(state.unloaded)
    except NoInspectionAvailable:
        unloaded = set()

    provenance_value = None if "provenance" in unloaded else getattr(design, "provenance", None)
    confidence_value = None if "confidence_metrics" in unloaded else getattr(design, "confidence_metrics", None)
    artifact_class_value = None if "artifact_class" in unloaded else getattr(design, "artifact_class", None)
    stage_mode_value = None if "stage_mode" in unloaded else getattr(design, "stage_mode", None)

    provenance = provenance_value if isinstance(provenance_value, dict) else {}
    confidence = confidence_value if isinstance(confidence_value, dict) else {}
    artifact_class = str(artifact_class_value or "").strip().lower()
    stage_mode = str(stage_mode_value or "").strip().lower()

    import_source = _normalize_import_source_value(provenance.get("import_source"))
    if import_source is None:
        import_source = _normalize_import_source_value(confidence.get("import_source"))

    is_imported = bool(import_source) or "import" in stage_mode or artifact_class.startswith("imported")
    if not is_imported:
        return {
            "is_imported": False,
            "import_source": None,
            "import_method": None,
            "import_label": None,
        }

    import_source = import_source or "external"
    import_metric_names = _iter_import_metric_names(design)
    import_method: Optional[str] = None
    if _has_boltz2_import_metrics(design):
        import_method = "boltz2"
    elif any(metric_name.startswith("esmfold_") for metric_name in import_metric_names):
        import_method = "esmfold"

    source_label = _format_import_source_label(import_source)
    method_label = _format_import_method_label(import_method)
    label_parts = [part for part in (source_label, method_label) if part]
    import_label = f"Imported • {' • '.join(label_parts)}" if label_parts else "Imported"

    return {
        "is_imported": True,
        "import_source": import_source,
        "import_method": import_method,
        "import_label": import_label,
    }


def _sequence_text_length(sequence: Optional[str]) -> Optional[int]:
    if not isinstance(sequence, str):
        return None
    parts = ["".join(segment.split()) for segment in sequence.split("|")]
    lengths = [len(part) for part in parts if part]
    return sum(lengths) if lengths else None


def _compute_fampnn_response_metrics(
    design: Design,
    *,
    include_structure_fallback: bool = False,
) -> Dict[str, Optional[float]]:
    payload_records = _fampnn_payload_records(design)
    avg_psce = _round_nullable(
        design.fampnn_psce if design.fampnn_psce is not None else next(
            (
                value
                for value in (
                    _numeric_record_value(record, "fampnn_avg_psce", "avg_psce")
                    for record in payload_records
                )
                if value is not None
            ),
            None,
        ),
        3,
    )
    max_psce = _round_nullable(
        next(
            (
                value
                for value in (
                    _numeric_record_value(record, "fampnn_max_residue_psce", "max_residue_psce")
                    for record in payload_records
                )
                if value is not None
            ),
            None,
        ),
        3,
    )
    min_psce = _round_nullable(
        next(
            (
                value
                for value in (
                    _numeric_record_value(record, "fampnn_min_residue_psce", "min_residue_psce")
                    for record in payload_records
                )
                if value is not None
            ),
            None,
        ),
        3,
    )

    if include_structure_fallback and (avg_psce is None or max_psce is None or min_psce is None):
        has_fampnn_hints = avg_psce is not None or bool(payload_records) or str(getattr(design, "stage_family", "") or "").strip().lower() == "fampnn"
        if has_fampnn_hints and design.pdb_path:
            try:
                chain_profiles = get_per_chain_fampnn_psce(resolve_runtime_data_path(design.pdb_path))
            except Exception:
                chain_profiles = {}
            residue_psces: List[float] = []
            for profile in chain_profiles.values():
                values = profile.get("psce") if isinstance(profile, dict) else None
                if not isinstance(values, list):
                    continue
                for value in values:
                    if isinstance(value, (int, float)):
                        numeric = float(value)
                        if math.isfinite(numeric):
                            residue_psces.append(numeric)
            if residue_psces:
                if avg_psce is None:
                    avg_psce = _round_nullable(sum(residue_psces) / len(residue_psces), 3)
                if max_psce is None:
                    max_psce = _round_nullable(max(residue_psces), 3)
                if min_psce is None:
                    min_psce = _round_nullable(min(residue_psces), 3)

    return {
        "fampnn_psce": avg_psce,
        "fampnn_max_residue_psce": max_psce,
        "fampnn_min_residue_psce": min_psce,
    }


def _compute_binder_sequence_response_value(
    design: Design,
    *,
    include_structure_fallback: bool = False,
) -> Optional[str]:
    payload_records = _fampnn_payload_records(design)
    for record in payload_records:
        binder_sequence = _text_record_value(record, "binder_sequence")
        if binder_sequence:
            return binder_sequence

    for record in _import_payload_records(design):
        binder_sequence = _text_record_value(record, "binder_sequence", "sequence")
        if binder_sequence:
            return binder_sequence

    if not include_structure_fallback or not design.pdb_path:
        return None

    structure_path = resolve_runtime_data_path(design.pdb_path)
    if not structure_path.exists():
        return None

    try:
        sequences = extract_sequence_from_pdb(str(structure_path))
        if not sequences:
            return None

        binder_chains = identify_binder_chains(sequences, str(structure_path))
        ordered_chain_ids = [
            chain_id
            for chain_id in dict.fromkeys(binder_chains.values())
            if isinstance(chain_id, str) and chain_id in sequences and sequences.get(chain_id)
        ]
        if ordered_chain_ids:
            binder_sequences = [sequences[chain_id].strip() for chain_id in ordered_chain_ids if isinstance(sequences.get(chain_id), str) and sequences[chain_id].strip()]
            if binder_sequences:
                return "|".join(binder_sequences)

        heavy_chain = sequences.get("H")
        if isinstance(heavy_chain, str) and heavy_chain.strip():
            return heavy_chain.strip()

        if len(sequences) == 1:
            only_sequence = next(iter(sequences.values()))
            if isinstance(only_sequence, str) and only_sequence.strip():
                return only_sequence.strip()
    except Exception:
        return None

    return None


def _compute_binder_length_response_value(
    design: Design,
    *,
    binder_sequence: Optional[str] = None,
) -> Optional[int]:
    binder_length = getattr(design, "binder_length", None)
    if isinstance(binder_length, int) and binder_length > 0:
        return binder_length

    if isinstance(binder_length, float) and math.isfinite(binder_length):
        rounded_length = int(round(binder_length))
        if rounded_length > 0:
            return rounded_length

    for record in _fampnn_payload_records(design):
        payload_length = _numeric_record_value(record, "binder_length", "length")
        if payload_length is not None:
            rounded_length = int(round(payload_length))
            if rounded_length > 0:
                return rounded_length

    for record in _import_payload_records(design):
        payload_length = _numeric_record_value(record, "binder_length", "length_aa")
        if payload_length is not None:
            rounded_length = int(round(payload_length))
            if rounded_length > 0:
                return rounded_length

    return _sequence_text_length(binder_sequence)


def _design_summary_sort_key(design: Design) -> tuple:
    return (
        -(design.plddt_overall if design.plddt_overall is not None else float("-inf")),
        -(design.epitope_contact_count if design.epitope_contact_count is not None else float("-inf")),
        design.epitope_min_distance if design.epitope_min_distance is not None else float("inf"),
        design.created_at,
    )


def _safe_allowed_relative(path_str: Optional[str]) -> Optional[str]:
    if not path_str:
        return None
    try:
        return to_allowed_relative(Path(path_str))
    except Exception:
        return None


def _design_to_response(
    design: Design,
    *,
    include_fampnn_structure_fallback: bool = False,
) -> DesignResponse:
    state = sa_inspect(design)
    unloaded = set(state.unloaded)
    data: Dict[str, Any] = {}
    for field_name in DesignResponse.model_fields.keys():
        if field_name == "frustration_csv_relpath":
            continue
        if field_name in unloaded:
            data[field_name] = None
            continue
        data[field_name] = getattr(design, field_name, None)
    data["frustration_csv_relpath"] = None if "frustration_csv_path" in unloaded else _safe_allowed_relative(design.frustration_csv_path)
    fampnn_metrics = _compute_fampnn_response_metrics(
        design,
        include_structure_fallback=include_fampnn_structure_fallback,
    )
    data.update(fampnn_metrics)
    confidence_metrics = data.get("confidence_metrics") if isinstance(data.get("confidence_metrics"), dict) else {}
    nested_rfa_metrics = confidence_metrics.get("rfantibody") if isinstance(confidence_metrics.get("rfantibody"), dict) else None
    flat_scope = confidence_metrics.get("confidence_scope") if isinstance(confidence_metrics.get("confidence_scope"), dict) else None
    looks_like_flat_rfa = (
        isinstance(flat_scope, dict) and flat_scope.get("metric_family") == "rfantibody_plddt"
    ) or str(getattr(design, "source_stage", "") or getattr(design, "stage_family", "")).lower().find("rfantibody") >= 0
    rfa_metrics = nested_rfa_metrics if nested_rfa_metrics is not None else (confidence_metrics if looks_like_flat_rfa else None)
    first_present = lambda *values: next((value for value in values if value is not None), None)
    if isinstance(rfa_metrics, dict):
        rfa_confidence_scope = rfa_metrics.get("confidence_scope") if isinstance(rfa_metrics.get("confidence_scope"), dict) else None
        rfa_plddt = rfa_confidence_scope.get("plddt") if isinstance(rfa_confidence_scope, dict) and isinstance(rfa_confidence_scope.get("plddt"), dict) else {}
        data["rfa_confidence_scope"] = first_present(data.get("rfa_confidence_scope"), rfa_confidence_scope)
        data["rfa_modifiable_residues"] = first_present(data.get("rfa_modifiable_residues"), rfa_metrics.get("modifiable_residues"), (rfa_confidence_scope or {}).get("modifiable_residues"))
        data["rfa_modifiable_ranges"] = first_present(data.get("rfa_modifiable_ranges"), rfa_metrics.get("modifiable_ranges"), (rfa_confidence_scope or {}).get("modifiable_ranges"))
        data["rfa_plddt_primary"] = first_present(data.get("rfa_plddt_primary"), rfa_metrics.get("plddt_primary"), rfa_plddt.get("primary"))
        data["rfa_plddt_modifiable"] = first_present(data.get("rfa_plddt_modifiable"), rfa_metrics.get("plddt_modifiable"), rfa_metrics.get("plddt_selected"), rfa_plddt.get("modifiable"))
        data["rfa_plddt_all_residue"] = first_present(data.get("rfa_plddt_all_residue"), rfa_metrics.get("plddt_all_residue"), rfa_plddt.get("all_residue"), data.get("rfa_plddt_final"))
        data["rfa_plddt_nonmodifiable"] = first_present(data.get("rfa_plddt_nonmodifiable"), rfa_metrics.get("plddt_nonmodifiable"), rfa_metrics.get("plddt_nonselected"), rfa_plddt.get("nonmodifiable"))
        data["rfa_plddt_framework"] = first_present(data.get("rfa_plddt_framework"), rfa_metrics.get("plddt_framework"), rfa_plddt.get("framework"))
        data["rfa_plddt_target"] = first_present(data.get("rfa_plddt_target"), rfa_metrics.get("plddt_target"), rfa_plddt.get("target"))
    provenance = design.provenance if isinstance(design.provenance, dict) else {}
    ppiflow = provenance.get("ppiflow") if isinstance(provenance.get("ppiflow"), dict) else {}
    ppiflow_score = (
        ppiflow.get("maturation_score") if isinstance(ppiflow.get("maturation_score"), dict)
        else ppiflow.get("partial_flow_score") if isinstance(ppiflow.get("partial_flow_score"), dict)
        else None
    ) or {}
    ppiflow_filter = ppiflow.get("maturation_filter") if isinstance(ppiflow.get("maturation_filter"), dict) else {}
    fallback_fields = {
        "ppiflow_primary_loop": ppiflow_score.get("primary_loop"),
        "ppiflow_primary_loop_rmsd": ppiflow_score.get("primary_loop_rmsd"),
        "ppiflow_primary_loop_target_contact_delta": ppiflow_score.get("primary_loop_target_contact_delta"),
        "ppiflow_primary_loop_target_distance_delta": ppiflow_score.get("primary_loop_target_distance_delta"),
        "ppiflow_primary_loop_epitope_contact_delta": ppiflow_score.get("primary_loop_epitope_contact_delta"),
        "ppiflow_primary_loop_epitope_distance_delta": ppiflow_score.get("primary_loop_epitope_distance_delta"),
        "ppiflow_objective_mode": ppiflow_score.get("objective_mode"),
        "ppiflow_objective_score": ppiflow_score.get("objective_score"),
        "ppiflow_filter_passed": ppiflow_filter.get("passed"),
        "ppiflow_filter_reason": ppiflow_filter.get("filter_reason"),
        "ppiflow_loop_metrics": ppiflow_score.get("loop_metrics"),
    }
    for field_name, fallback_value in fallback_fields.items():
        if data.get(field_name) in (None, "", [], {}, ()):
            data[field_name] = fallback_value
    binder_sequence = _compute_binder_sequence_response_value(
        design,
        include_structure_fallback=include_fampnn_structure_fallback,
    )
    data["binder_sequence"] = binder_sequence
    data["binder_length"] = _compute_binder_length_response_value(
        design,
        binder_sequence=binder_sequence,
    )
    artifact_class, result_set, result_set_label = _infer_design_result_set(
        stage_family=data.get("stage_family"),
        stage_mode=data.get("stage_mode"),
        artifact_class=data.get("artifact_class"),
        ppiflow_filter_passed=data.get("ppiflow_filter_passed"),
        passed_screen=data.get("passed_screen"),
    )
    if not data.get("artifact_class") and artifact_class:
        data["artifact_class"] = artifact_class
    if artifact_class and data.get("artifact_schema_version") is None:
        data["artifact_schema_version"] = 1
    data["result_set"] = result_set
    data["result_set_label"] = result_set_label
    data.update(_compute_import_metadata(design))
    return DesignResponse.model_validate(data)


async def _get_cached_design_analysis_payload(
    session: AsyncSession,
    design: Design,
    analysis_type: str,
    raw_params: Optional[dict[str, Any]] = None,
) -> Any:
    try:
        run, _definition, _params, _cache_key = await get_matching_design_analysis_run(
            session,
            design,
            analysis_type,
            raw_params=raw_params,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if run is None:
        raise HTTPException(status_code=404, detail=f"{analysis_type} not computed yet")
    if run.status != "completed":
        raise HTTPException(status_code=409, detail=f"{analysis_type} status is {run.status}")

    payload = load_analysis_result(run)
    if payload is None:
        raise HTTPException(status_code=500, detail=f"Cached {analysis_type} payload is unavailable")
    return payload


async def _collect_plotly_metrics(
    job_id: str,
    include_children: bool,
    requested_design_ids: Optional[List[str]],
    limit: int,
    offset: int,
    session: AsyncSession,
) -> PlotlyMetricsResponse:
    job_result = await session.execute(select(Job).where(Job.id == job_id))
    job = job_result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not include_children:
        await _hydrate_review_job(session, job)

    job_ids = await _resolve_design_query_job_ids(
        session,
        job_id,
        include_children=include_children,
        job=job,
    )

    clean_design_ids = [design_id.strip() for design_id in (requested_design_ids or []) if design_id and design_id.strip()]

    query = (
        select(Design)
        .options(load_only(*ANALYTICS_LOAD_ONLY_COLUMNS))
        .where(Design.job_id.in_(job_ids))
        .order_by(Design.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    count_query = select(func.count(Design.id)).where(Design.job_id.in_(job_ids))
    if clean_design_ids:
        query = query.where(Design.id.in_(clean_design_ids))
        count_query = count_query.where(Design.id.in_(clean_design_ids))

    result = await session.execute(query)
    designs = result.scalars().all()
    total = (await session.execute(count_query)).scalar() or 0

    points: List[PlotlyMetricPoint] = []
    metric_keys: set[str] = set()
    for design in designs:
        metrics = _build_plotly_metrics(design)
        metric_keys.update(metrics.keys())
        points.append(
            PlotlyMetricPoint(
                id=design.id,
                name=design.name,
                metrics=metrics,
            )
        )

    sorted_metric_keys = sorted(metric_keys)
    return PlotlyMetricsResponse(
        job_id=job_id,
        metric_keys=sorted_metric_keys,
        points=points,
        total=int(total),
        metric_metadata=_build_plotly_metric_metadata(sorted_metric_keys),
        chart_suggestions=_build_plotly_chart_suggestions(sorted_metric_keys),
    )


async def _resolve_design_query_job_ids(
    session: AsyncSession,
    job_id: str,
    include_children: bool,
    job: Optional[Job] = None,
) -> list[str]:
    if not include_children:
        return [job_id]

    resolved_job = job
    if resolved_job is None:
        job_result = await session.execute(select(Job).where(Job.id == job_id))
        resolved_job = job_result.scalar_one_or_none()

    parent_design_count = await session.scalar(
        select(func.count(Design.id)).where(
            Design.job_id == job_id,
            Design.source_stage.is_(None),
        )
    )
    if parent_design_count and not bool(getattr(resolved_job, "awaiting_input", False)):
        return [job_id]

    child_result = await session.execute(select(Job.id).where(Job.parent_job_id == job_id))
    child_job_ids = [row[0] for row in child_result.all()]
    return [job_id] + child_job_ids


# --- Endpoints ---

@router.get("", response_model=DesignList)
async def list_designs(
    job_id: Optional[str] = None,
    include_children: bool = Query(True, description="Include designs from child jobs (for parent jobs)"),
    design_ids: Optional[List[str]] = Query(None, description="Restrict to explicit design ids"),
    q: Optional[str] = Query(None, description="Case-insensitive name search"),
    backbone_id: Optional[int] = Query(None, description="Filter by backbone ID"),
    plddt_min: Optional[float] = Query(None, description="Minimum pLDDT score"),
    pae_max: Optional[float] = Query(None, description="Maximum pAE score"),
    iptm_min: Optional[float] = Query(None, description="Minimum iPTM score"),
    ipsae_min: Optional[float] = Query(None, description="Minimum ipSAE score"),
    epitope_contacts_min: Optional[int] = Query(None, description="Minimum selected-epitope contact count"),
    target_contacts_min: Optional[int] = Query(None, description="Minimum whole-target contact count"),
    epitope_max_dist: Optional[float] = Query(None, description="Maximum nearest CA distance to selected epitope residues"),
    target_max_dist: Optional[float] = Query(None, description="Maximum nearest CA distance to any target residue"),
    binder_length_min: Optional[int] = Query(None, description="Minimum binder length"),
    binder_length_max: Optional[int] = Query(None, description="Maximum binder length"),
    cdr_h1_min: Optional[int] = Query(None, description="Minimum CDR-H1 length"),
    cdr_h1_max: Optional[int] = Query(None, description="Maximum CDR-H1 length"),
    cdr_h2_min: Optional[int] = Query(None, description="Minimum CDR-H2 length"),
    cdr_h2_max: Optional[int] = Query(None, description="Maximum CDR-H2 length"),
    cdr_h3_min: Optional[int] = Query(None, description="Minimum CDR-H3 length"),
    cdr_h3_max: Optional[int] = Query(None, description="Maximum CDR-H3 length"),
    rog_min: Optional[float] = Query(None, description="Minimum radius of gyration"),
    rog_max: Optional[float] = Query(None, description="Maximum radius of gyration"),
    rfd_rog_min: Optional[float] = Query(None, description="Minimum RFdiffusion radius of gyration"),
    rfd_rog_max: Optional[float] = Query(None, description="Maximum RFdiffusion radius of gyration"),
    favorites_only: bool = Query(False, description="Show only favorites"),
    artifact_group: Optional[str] = Query(None, description="Filter by review artifact group"),
    artifact_class: Optional[str] = Query(None, description="Filter by canonical artifact class"),
    stage_family: Optional[str] = Query(None, description="Filter by producing stage family"),
    source_stage_family: Optional[str] = Query(None, description="Filter by source stage family"),
    sort_by: Optional[str] = Query(None, description="Sort field for table ordering"),
    sort_desc: bool = Query(True, description="Sort descending"),
    limit: int = Query(100, le=50000),
    offset: int = Query(0),
    session: AsyncSession = Depends(get_session)
):
    """
    List designs with optional filtering.
    
    Filters:
    - job_id: Filter by specific job (if include_children=True, also includes child job designs)
    - include_children: When job_id is specified, also fetch designs from child jobs
    - q: Case-insensitive substring match on design name
    - backbone_id: Filter by backbone number
    - plddt_min: Minimum pLDDT threshold
    - pae_max: Maximum pAE threshold
    - iptm_min: Minimum iPTM threshold
    - favorites_only: Show only favorited designs
    - sort_by: Sort by specific field
    """
    selected_job: Optional[Job] = None
    review_stage: Optional[str] = None
    if job_id:
        job_result = await session.execute(select(Job).where(Job.id == job_id))
        selected_job = job_result.scalar_one_or_none()
        if selected_job:
            review_stage = await _hydrate_review_job(session, selected_job)
            if _should_force_review_stage_listing(selected_job, review_stage):
                include_children = False

    # Build base query with optional sorting
    sort_field_map = {
        'plddt': Design.plddt_overall,
        'plddt_overall': Design.plddt_overall,
        'plddt_binder': Design.plddt_binder,
        'plddt_target': Design.plddt_target,
        'name': Design.name,
        'iptm': Design.iptm,
        'ipsae': Design.ipsae,
        'ptm': Design.ptm,
        'pae': Design.pae_overall,
        'pae_overall': Design.pae_overall,
        'pae_interaction': Design.pae_interaction,
        'conf_score': Design.conf_score,
        'ligand_iptm': Design.ligand_iptm,
        'rmsd_binder': Design.rmsd_binder,
        'rmsd_overall': Design.rmsd_overall,
        'rmsd_target': Design.rmsd_target,
        'has_clash': Design.has_clash,
        'confidence': Design.conf_score,
        'backbone': Design.backbone_id,
        'backbone_id': Design.backbone_id,
        'rog': Design.rog,
        'rfd_rog': Design.rfd_rog,
        'binder_length': Design.binder_length,
        'cdr_h1_length': Design.cdr_h1_length,
        'cdr_h2_length': Design.cdr_h2_length,
        'cdr_h3_length': Design.cdr_h3_length,
        'epitope_contact_count': Design.epitope_contact_count,
        'target_contact_count': Design.target_contact_count,
        'epitope_min_distance': Design.epitope_min_distance,
        'target_min_distance': Design.target_min_distance,
        'epitope_min_atom_distance': Design.epitope_min_atom_distance,
        'target_min_atom_distance': Design.target_min_atom_distance,
        'epitope_centroid_distance': Design.epitope_centroid_distance,
        'target_centroid_distance': Design.target_centroid_distance,
        'affinity_score': Design.affinity_score,
        'binder_probability': Design.binder_probability,
        'fampnn_psce': Design.fampnn_psce,
        'rfa_hotspot_covered_count': Design.rfa_hotspot_covered_count,
        'rfa_hotspot_min_distance': Design.rfa_hotspot_min_distance,
        'rfa_hotspot_avg_min_distance': Design.rfa_hotspot_avg_min_distance,
        'rfa_runtime_seconds': Design.rfa_runtime_seconds,
        'rfa_plddt_final': Design.rfa_plddt_final,
        'rfa_plddt_selected': Design.rfa_plddt_selected,
        'rfa_plddt_delta': Design.rfa_plddt_delta,
        'frustration_high_count': Design.frustration_high_count,
        'frustration_pct_high': Design.frustration_pct_high,
        'maturation_delta_interface': Design.maturation_delta_interface,
        'maturation_interface_score': Design.maturation_interface_score,
        'maturation_rmsd': Design.maturation_rmsd,
        'maturation_selected_delta_interface': Design.maturation_selected_delta_interface,
        'maturation_selected_interface_score': Design.maturation_selected_interface_score,
        'maturation_selected_rmsd': Design.maturation_selected_rmsd,
        'maturation_nonselected_rmsd': Design.maturation_nonselected_rmsd,
        'ppiflow_objective_score': Design.ppiflow_objective_score,
        'ppiflow_primary_loop_rmsd': Design.ppiflow_primary_loop_rmsd,
        'ppiflow_primary_loop_target_contact_delta': Design.ppiflow_primary_loop_target_contact_delta,
        'ppiflow_primary_loop_target_distance_delta': Design.ppiflow_primary_loop_target_distance_delta,
        'ppiflow_primary_loop_epitope_contact_delta': Design.ppiflow_primary_loop_epitope_contact_delta,
        'ppiflow_primary_loop_epitope_distance_delta': Design.ppiflow_primary_loop_epitope_distance_delta,
        'fr2_contacts': Design.fr2_contacts,
        'is_favorite': Design.is_favorite,
        'binding_tier': case(
            (Design.ipsae.is_not(None), Design.ipsae),
            else_=func.coalesce(Design.iptm, 0.0) + case(
                (Design.epitope_contact_count >= 5, 0.05),
                else_=0.0,
            ),
        ),
    }
    
    order_col = sort_field_map.get(sort_by, Design.created_at)
    if sort_desc:
        query = select(Design).options(load_only(*DESIGN_LIST_LOAD_ONLY_COLUMNS)).order_by(order_col.desc().nulls_last())
    else:
        query = select(Design).options(load_only(*DESIGN_LIST_LOAD_ONLY_COLUMNS)).order_by(order_col.asc().nulls_last())
    
    # Apply filters - handle include_children for job_id
    conditions = []
    if job_id:
        if include_children:
            all_job_ids = await _resolve_design_query_job_ids(
                session,
                job_id,
                include_children=True,
                job=selected_job,
            )
            conditions.append(Design.job_id.in_(all_job_ids))
        else:
            conditions.append(Design.job_id == job_id)
            if review_stage:
                conditions.append(Design.source_stage == review_stage)
            else:
                conditions.append(Design.source_stage.is_(None))
    elif not include_children:
        conditions.append(Design.source_stage.is_(None))
    elif not job_id:
        conditions.append(Design.source_stage.is_(None))
    clean_design_ids = [design_id.strip() for design_id in (design_ids or []) if design_id and design_id.strip()]
    if clean_design_ids:
        conditions.append(Design.id.in_(clean_design_ids))
    if q and q.strip():
        conditions.append(Design.name.ilike(f"%{q.strip()}%"))
    if backbone_id is not None:
        conditions.append(Design.backbone_id == backbone_id)
    if plddt_min is not None:
        conditions.append(Design.plddt_overall >= plddt_min)
    if pae_max is not None:
        conditions.append(Design.pae_overall <= pae_max)
    if iptm_min is not None:
        conditions.append(Design.iptm >= iptm_min)
    if ipsae_min is not None:
        conditions.append(Design.ipsae >= ipsae_min)
    if epitope_contacts_min is not None:
        conditions.append(Design.epitope_contact_count >= epitope_contacts_min)
    if target_contacts_min is not None:
        conditions.append(Design.target_contact_count >= target_contacts_min)
    if epitope_max_dist is not None:
        conditions.append(Design.epitope_min_distance <= epitope_max_dist)
    if target_max_dist is not None:
        conditions.append(Design.target_min_distance <= target_max_dist)
    if binder_length_min is not None:
        conditions.append(Design.binder_length >= binder_length_min)
    if binder_length_max is not None:
        conditions.append(Design.binder_length <= binder_length_max)
    if cdr_h1_min is not None:
        conditions.append(Design.cdr_h1_length >= cdr_h1_min)
    if cdr_h1_max is not None:
        conditions.append(Design.cdr_h1_length <= cdr_h1_max)
    if cdr_h2_min is not None:
        conditions.append(Design.cdr_h2_length >= cdr_h2_min)
    if cdr_h2_max is not None:
        conditions.append(Design.cdr_h2_length <= cdr_h2_max)
    if cdr_h3_min is not None:
        conditions.append(Design.cdr_h3_length >= cdr_h3_min)
    if cdr_h3_max is not None:
        conditions.append(Design.cdr_h3_length <= cdr_h3_max)
    if rog_min is not None:
        conditions.append(Design.rog >= rog_min)
    if rog_max is not None:
        conditions.append(Design.rog <= rog_max)
    if rfd_rog_min is not None:
        conditions.append(Design.rfd_rog >= rfd_rog_min)
    if rfd_rog_max is not None:
        conditions.append(Design.rfd_rog <= rfd_rog_max)
    if favorites_only:
        conditions.append(Design.is_favorite == True)
    if artifact_group:
        conditions.append(Design.artifact_group == artifact_group)
    if artifact_class:
        conditions.append(Design.artifact_class == artifact_class)
    if stage_family:
        conditions.append(Design.stage_family == stage_family)
    if source_stage_family:
        conditions.append(Design.source_stage_family == source_stage_family)
    
    if conditions:
        query = query.where(and_(*conditions))
    
    # Get total count
    count_query = select(func.count(Design.id))
    if conditions:
        count_query = count_query.where(and_(*conditions))
    total = (await session.execute(count_query)).scalar()
    
    # Apply pagination
    query = query.limit(limit).offset(offset)
    result = await session.execute(query)
    designs = result.scalars().all()
    
    return DesignList(
        designs=[_design_to_response(d) for d in designs],
        total=total
    )


@router.post("/query", response_model=DesignList)
async def query_designs(
    request: DesignQueryRequest,
    session: AsyncSession = Depends(get_session),
):
    """List designs via POST for large explicit design-id subsets."""
    return await list_designs(
        job_id=request.job_id,
        include_children=True if request.include_children is None else request.include_children,
        design_ids=request.design_ids,
        q=request.q,
        backbone_id=request.backbone_id,
        plddt_min=request.plddt_min,
        pae_max=request.pae_max,
        iptm_min=request.iptm_min,
        ipsae_min=request.ipsae_min,
        epitope_contacts_min=request.epitope_contacts_min,
        target_contacts_min=request.target_contacts_min,
        epitope_max_dist=request.epitope_max_dist,
        target_max_dist=request.target_max_dist,
        binder_length_min=request.binder_length_min,
        binder_length_max=request.binder_length_max,
        cdr_h1_min=request.cdr_h1_min,
        cdr_h1_max=request.cdr_h1_max,
        cdr_h2_min=request.cdr_h2_min,
        cdr_h2_max=request.cdr_h2_max,
        cdr_h3_min=request.cdr_h3_min,
        cdr_h3_max=request.cdr_h3_max,
        rog_min=request.rog_min,
        rog_max=request.rog_max,
        rfd_rog_min=request.rfd_rog_min,
        rfd_rog_max=request.rfd_rog_max,
        favorites_only=bool(request.favorites_only),
        artifact_group=request.artifact_group,
        artifact_class=request.artifact_class,
        stage_family=request.stage_family,
        source_stage_family=request.source_stage_family,
        sort_by=request.sort_by,
        sort_desc=True if request.sort_desc is None else request.sort_desc,
        limit=100 if request.limit is None else request.limit,
        offset=0 if request.offset is None else request.offset,
        session=session,
    )


@router.get("/by-job/{job_id}/backbone-summary")
async def get_backbone_summary(
    job_id: str,
    artifact_group: Optional[str] = Query(None, description="Filter stage-review summary by artifact group"),
    session: AsyncSession = Depends(get_session)
):
    """
    Get backbone-level aggregate statistics for a job.
    
    Returns counts and average metrics per backbone for UI toggle display.
    """
    job_result = await session.execute(select(Job).where(Job.id == job_id))
    job = job_result.scalar_one_or_none()
    review_stage = await _hydrate_review_job(session, job)

    summary_conditions = [Design.job_id == job_id]
    if review_stage:
        summary_conditions.append(Design.source_stage == review_stage)
    else:
        summary_conditions.append(Design.source_stage.is_(None))
    if artifact_group:
        summary_conditions.append(Design.artifact_group == artifact_group)

    result = await session.execute(
        select(Design)
        .options(load_only(*BACKBONE_SUMMARY_LOAD_ONLY_COLUMNS))
        .where(and_(*summary_conditions))
        .order_by(Design.created_at.asc())
    )
    designs = result.scalars().all()

    backbones: Dict[int, Dict[str, Any]] = {}
    for design in designs:
        if design.backbone_id is None:
            continue

        entry = backbones.setdefault(
            design.backbone_id,
            {
                "count": 0,
                "plddt_values": [],
                "iptm_values": [],
                "ptm_values": [],
                "pae_values": [],
                "target_contact_values": [],
                "epitope_contact_values": [],
                "target_distance_values": [],
                "epitope_distance_values": [],
                "cdr_h1_length_values": [],
                "cdr_h2_length_values": [],
                "cdr_h3_length_values": [],
                "representative": None,
            },
        )
        entry["count"] += 1

        for key, value in (
            ("plddt_values", design.plddt_overall),
            ("iptm_values", design.iptm),
            ("ptm_values", design.ptm),
            ("pae_values", design.pae_overall),
            ("target_contact_values", design.target_contact_count),
            ("epitope_contact_values", design.epitope_contact_count),
            ("target_distance_values", design.target_min_distance),
            ("epitope_distance_values", design.epitope_min_distance),
            ("cdr_h1_length_values", design.cdr_h1_length),
            ("cdr_h2_length_values", design.cdr_h2_length),
            ("cdr_h3_length_values", design.cdr_h3_length),
        ):
            if value is None:
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(numeric):
                entry[key].append(numeric)

        representative: Optional[Design] = entry["representative"]
        if representative is None or _design_summary_sort_key(design) < _design_summary_sort_key(representative):
            entry["representative"] = design

    formatted_backbones: Dict[int, Dict[str, Any]] = {}
    for backbone_id, entry in sorted(backbones.items()):
        plddt_values = entry["plddt_values"]
        iptm_values = entry["iptm_values"]
        ptm_values = entry["ptm_values"]
        pae_values = entry["pae_values"]
        target_contact_values = entry["target_contact_values"]
        contact_values = entry["epitope_contact_values"]
        target_distance_values = entry["target_distance_values"]
        distance_values = entry["epitope_distance_values"]
        h1_values = entry["cdr_h1_length_values"]
        h2_values = entry["cdr_h2_length_values"]
        h3_values = entry["cdr_h3_length_values"]
        representative = entry["representative"]

        formatted_backbones[backbone_id] = {
            "count": entry["count"],
            "avg_plddt": _round_nullable(sum(plddt_values) / len(plddt_values), 1) if plddt_values else None,
            "max_plddt": _round_nullable(max(plddt_values), 1) if plddt_values else None,
            "avg_iptm": _round_nullable(sum(iptm_values) / len(iptm_values), 3) if iptm_values else None,
            "avg_ptm": _round_nullable(sum(ptm_values) / len(ptm_values), 3) if ptm_values else None,
            "min_pae": _round_nullable(min(pae_values), 1) if pae_values else None,
            "avg_target_contacts": _round_nullable(sum(target_contact_values) / len(target_contact_values), 1) if target_contact_values else None,
            "min_target_contacts": int(min(target_contact_values)) if target_contact_values else None,
            "max_target_contacts": int(max(target_contact_values)) if target_contact_values else None,
            "avg_epitope_contacts": _round_nullable(sum(contact_values) / len(contact_values), 1) if contact_values else None,
            "min_epitope_contacts": int(min(contact_values)) if contact_values else None,
            "max_epitope_contacts": int(max(contact_values)) if contact_values else None,
            "avg_target_distance": _round_nullable(sum(target_distance_values) / len(target_distance_values), 2) if target_distance_values else None,
            "min_target_distance": _round_nullable(min(target_distance_values), 2) if target_distance_values else None,
            "max_target_distance": _round_nullable(max(target_distance_values), 2) if target_distance_values else None,
            "avg_epitope_distance": _round_nullable(sum(distance_values) / len(distance_values), 2) if distance_values else None,
            "min_epitope_distance": _round_nullable(min(distance_values), 2) if distance_values else None,
            "max_epitope_distance": _round_nullable(max(distance_values), 2) if distance_values else None,
            "avg_cdr_h1_length": _round_nullable(sum(h1_values) / len(h1_values), 1) if h1_values else None,
            "avg_cdr_h2_length": _round_nullable(sum(h2_values) / len(h2_values), 1) if h2_values else None,
            "avg_cdr_h3_length": _round_nullable(sum(h3_values) / len(h3_values), 1) if h3_values else None,
            "representative": (
                {
                    "id": representative.id,
                    "name": representative.name,
                    "pdb_path": representative.pdb_path,
                    "plddt_overall": _round_nullable(representative.plddt_overall, 1),
                    "epitope_contact_count": representative.epitope_contact_count,
                    "epitope_min_distance": _round_nullable(representative.epitope_min_distance, 2),
                    "target_contact_count": representative.target_contact_count,
                    "target_min_distance": _round_nullable(representative.target_min_distance, 2),
                    "rfa_hotspot_covered_count": representative.rfa_hotspot_covered_count,
                }
                if representative
                else None
            ),
        }

    total = len(designs)
    assigned_total = sum(entry["count"] for entry in formatted_backbones.values())

    return {
        "job_id": job_id,
        "total": total,
        "assigned_total": assigned_total,
        "unassigned_total": max(total - assigned_total, 0),
        "backbones": formatted_backbones,
    }


@router.get("/{design_id}", response_model=DesignResponse)
async def get_design(
    design_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get a specific design by ID."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    return _design_to_response(design, include_fampnn_structure_fallback=True)


@router.get("/{design_id}/pdb")
async def get_design_pdb(
    design_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Download the PDB file for a design."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    if not design.pdb_path:
        raise HTTPException(status_code=404, detail="No PDB file for this design")
    
    pdb_path = resolve_runtime_data_path(design.pdb_path)
    return _structure_file_response(pdb_path, design.name)


@router.get("/{design_id}/source-pdb")
async def get_design_source_pdb(
    design_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Serve the source structure recorded in a design provenance payload."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()

    if not design:
        raise HTTPException(status_code=404, detail="Design not found")

    source_pdb_path = str(getattr(design, "source_pdb_path", "") or "").strip()
    source_design_name = str(getattr(design, "source_design_name", "") or "").strip()
    if not source_pdb_path:
        provenance = design.provenance if isinstance(design.provenance, dict) else {}
        ppiflow = provenance.get("ppiflow") if isinstance(provenance.get("ppiflow"), dict) else {}
        source_pdb_path = str(ppiflow.get("source_pdb_path") or "").strip()
        if not source_design_name:
            source_design_name = str(ppiflow.get("source_design_name") or "").strip()

    if not source_design_name:
        source_design_name = f"{design.name}_source"
    if not source_pdb_path:
        raise HTTPException(status_code=404, detail="No source structure recorded for this design")

    return _structure_file_response(resolve_runtime_data_path(source_pdb_path), source_design_name)


class ResidueMetrics(BaseModel):
    """Per-residue metrics for charting."""
    design_id: str
    design_name: str
    residue_numbers: List[int]
    plddt: List[float]
    length: int


@router.get("/{design_id}/residue-metrics", response_model=ResidueMetrics)
async def get_residue_metrics(
    design_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get per-residue metrics for a design (for line charts)."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    if not design.residue_plddt:
        raise HTTPException(status_code=404, detail="No per-residue data available for this design")
    
    plddt_values = design.residue_plddt
    residue_numbers = list(range(1, len(plddt_values) + 1))
    
    return ResidueMetrics(
        design_id=design.id,
        design_name=design.name,
        residue_numbers=residue_numbers,
        plddt=plddt_values,
        length=len(plddt_values)
    )


@router.get("/{design_id}/chain-metrics")
async def get_chain_metrics(design_id: str, session: AsyncSession = Depends(get_session)):
    """Return per-chain pLDDT and type information."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")

    payload = await _get_cached_design_analysis_payload(
        session,
        design,
        "chain_metrics",
        raw_params={},
    )
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="Cached chain-metrics payload is unavailable")
    return payload


@router.post("/{design_id}/favorite")
async def toggle_favorite(
    design_id: str,
    update: FavoriteUpdate,
    session: AsyncSession = Depends(get_session)
):
    """Toggle favorite status for a design."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    design.is_favorite = update.is_favorite
    await session.commit()
    
    return {"message": "Favorite updated", "is_favorite": design.is_favorite}


@router.patch("/{design_id}/notes")
async def update_notes(
    design_id: str,
    update: NotesUpdate,
    session: AsyncSession = Depends(get_session)
):
    """Update notes for a design."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    design.notes = update.notes
    await session.commit()
    
    return {"message": "Notes updated", "notes": design.notes}


@router.get("/by-job/{job_id}", response_model=DesignList)
async def get_designs_for_job(
    job_id: str,
    include_children: bool = Query(True, description="Include designs from child jobs (for parent jobs)"),
    limit: int = Query(100, le=50000),
    offset: int = Query(0),
    session: AsyncSession = Depends(get_session)
):
    """
    Get all designs for a specific job.
    
    If include_children is True (default), also fetches designs from all child jobs
    that have parent_job_id matching this job. This enables viewing all aggregated
    designs under the parent exploration job.
    """
    # Verify job exists
    job_result = await session.execute(select(Job).where(Job.id == job_id))
    job = job_result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not include_children:
        await _hydrate_review_job(session, job)
    
    # Build job_id filter - include children if requested
    if include_children:
        all_job_ids = await _resolve_design_query_job_ids(
            session,
            job_id,
            include_children=True,
            job=job,
        )
        query = select(Design).where(Design.job_id.in_(all_job_ids)).order_by(Design.name)
        count_query = select(func.count(Design.id)).where(Design.job_id.in_(all_job_ids))
    else:
        # Only this specific job
        query = select(Design).where(Design.job_id == job_id).order_by(Design.name)
        count_query = select(func.count(Design.id)).where(Design.job_id == job_id)
    
    # Apply pagination
    query = query.limit(limit).offset(offset)
    
    result = await session.execute(query)
    designs = result.scalars().all()
    
    # Count total
    total = (await session.execute(count_query)).scalar()
    
    return DesignList(
        designs=[_design_to_response(d) for d in designs],
        total=total
    )


@router.get("/by-job/{job_id}/plotly-metrics", response_model=PlotlyMetricsResponse)
async def get_plotly_metrics_for_job(
    job_id: str,
    include_children: bool = Query(True, description="Include child jobs when collecting metrics"),
    design_ids: Optional[str] = Query(None, description="Comma-separated design ids to restrict the analytics payload"),
    limit: int = Query(10000, ge=1, le=50000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session)
):
    """Return flattened numeric metrics for Plotly charting (including raw confidence metrics)."""
    requested_design_ids = [part.strip() for part in (design_ids or "").split(",") if part.strip()]
    return await _collect_plotly_metrics(
        job_id=job_id,
        include_children=include_children,
        requested_design_ids=requested_design_ids,
        limit=limit,
        offset=offset,
        session=session,
    )


@router.post("/by-job/{job_id}/plotly-metrics", response_model=PlotlyMetricsResponse)
async def post_plotly_metrics_for_job(
    job_id: str,
    request: PlotlyMetricsRequest,
    session: AsyncSession = Depends(get_session)
):
    """Return flattened numeric metrics for a specific design subset without overloading query strings."""
    return await _collect_plotly_metrics(
        job_id=job_id,
        include_children=request.include_children,
        requested_design_ids=request.design_ids,
        limit=max(1, min(request.limit, 50000)),
        offset=max(0, request.offset),
        session=session,
    )


# --- Phase 3: Biotite-Powered Structure Analysis Endpoints ---

class StructureAnalysis(BaseModel):
    """Computed structure analysis metrics (via Biotite)."""
    design_id: str
    design_name: str
    residue_count: int
    chain_ids: List[str]
    gyration_radius: Optional[float]
    secondary_structure: dict  # {"helix": n, "sheet": n, "coil": n}


@router.get("/{design_id}/structure-analysis", response_model=StructureAnalysis)
async def get_structure_analysis(
    design_id: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Get computed structure analysis for a design.
    
    Uses Biotite to compute:
    - Residue count
    - Chain IDs
    - Radius of gyration
    - Secondary structure (helix/sheet/coil counts)
    """
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    if not design.pdb_path:
        raise HTTPException(status_code=404, detail="No structure file for this design")
    
    structure_path = resolve_runtime_data_path(design.pdb_path)
    if not structure_path.exists():
        raise HTTPException(status_code=404, detail="Structure file not found on disk")
    
    try:
        run, _definition, _params, _cache_key = await get_matching_design_analysis_run(
            session,
            design,
            "structure_summary",
            raw_params={},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if run is None:
        raise HTTPException(status_code=404, detail="Structure analysis not computed yet")
    if run.status != "completed":
        raise HTTPException(status_code=409, detail=f"Structure analysis status is {run.status}")

    payload = load_analysis_result(run)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="Cached structure analysis payload is unavailable")
    return StructureAnalysis.model_validate(payload)


class StructureComparison(BaseModel):
    """RMSD comparison between two structures."""
    design_id: str
    other_design_id: str
    rmsd_backbone: Optional[float]
    rmsd_all_atom: Optional[float]


@router.get("/{design_id}/compare/{other_design_id}", response_model=StructureComparison)
async def compare_structures(
    design_id: str,
    other_design_id: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Compute RMSD between two design structures.
    
    Returns backbone RMSD (N, CA, C atoms) and all-atom RMSD.
    """
    # Get both designs
    result1 = await session.execute(select(Design).where(Design.id == design_id))
    design1 = result1.scalar_one_or_none()
    
    result2 = await session.execute(select(Design).where(Design.id == other_design_id))
    design2 = result2.scalar_one_or_none()
    
    if not design1:
        raise HTTPException(status_code=404, detail=f"Design {design_id} not found")
    if not design2:
        raise HTTPException(status_code=404, detail=f"Design {other_design_id} not found")
    
    if not design1.pdb_path or not design2.pdb_path:
        raise HTTPException(status_code=404, detail="One or both designs missing structure files")
    
    path1, path2 = resolve_runtime_data_path(design1.pdb_path), resolve_runtime_data_path(design2.pdb_path)
    if not path1.exists() or not path2.exists():
        raise HTTPException(status_code=404, detail="Structure files not found on disk")
    
    try:
        from services.structure_utils import compute_rmsd
    except ImportError:
        raise HTTPException(status_code=500, detail="Structure analysis module not available")
    
    return StructureComparison(
        design_id=design_id,
        other_design_id=other_design_id,
        rmsd_backbone=compute_rmsd(path1, path2, backbone_only=True),
        rmsd_all_atom=compute_rmsd(path1, path2, backbone_only=False)
    )


class PAEData(BaseModel):
    """PAE matrix data for heatmap visualization."""
    design_id: str
    design_name: str
    pae_matrix: List[List[float]]  # 2D matrix
    size: int  # Matrix dimension


@router.get("/{design_id}/pae", response_model=PAEData)
async def get_pae_data(
    design_id: str,
    max_size: int = Query(200, description="Maximum matrix dimension"),
    session: AsyncSession = Depends(get_session)
):
    """
    Get cached PAE matrix data for heatmap visualization.
    """
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    if not design.pdb_path:
        raise HTTPException(status_code=404, detail="No structure file for this design")
    
    payload = await _get_cached_design_analysis_payload(
        session,
        design,
        "pae_matrix",
        raw_params={"max_size": max_size},
    )
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="Cached PAE payload is unavailable")
    return PAEData.model_validate(payload)


class AntibodyData(BaseModel):
    """Aggregate antibody metrics."""
    design_id: str
    cdrs: Dict[str, Optional[str]]
    cdr_lengths: Dict[str, Optional[int]] = {}
    binder_length: Optional[int] = None
    antibody_type: Optional[str] = None
    humanness_score: Optional[float]
    stability_data: Optional[Dict[str, Any]]
    imgt_pdb_url: Optional[str]
    detected_antibody_chains: Optional[str] = None
    framework_regions: Dict[str, Optional[str]] = {}
    binder_chains: Dict[str, str] = {}
    overlay_selections: List[Dict[str, Union[str, int]]] = []


def _build_antibody_overlay_selections(
    design: Design,
    *,
    imgt_url: Optional[str],
    annotation,
    binder_chains: Dict[str, str],
) -> List[Dict[str, Union[str, int]]]:
    if not annotation:
        return []

    chain_metrics = design.chain_metrics if isinstance(design.chain_metrics, dict) else {}
    selections: List[Dict[str, Union[str, int]]] = []
    region_specs = [
        ("H1", "H", getattr(annotation, "cdr_h1_range", None), getattr(annotation, "cdr_h1_seq_range", None)),
        ("H2", "H", getattr(annotation, "cdr_h2_range", None), getattr(annotation, "cdr_h2_seq_range", None)),
        ("H3", "H", getattr(annotation, "cdr_h3_range", None), getattr(annotation, "cdr_h3_seq_range", None)),
        ("L1", "L", getattr(annotation, "cdr_l1_range", None), getattr(annotation, "cdr_l1_seq_range", None)),
        ("L2", "L", getattr(annotation, "cdr_l2_range", None), getattr(annotation, "cdr_l2_seq_range", None)),
        ("L3", "L", getattr(annotation, "cdr_l3_range", None), getattr(annotation, "cdr_l3_seq_range", None)),
    ]

    for region, chain_type, imgt_range, seq_range in region_specs:
        if imgt_url and imgt_range:
            selections.append(
                {
                    "region": region,
                    "chain_id": chain_type,
                    "start_residue_number": int(imgt_range[0]),
                    "end_residue_number": int(imgt_range[1]),
                }
            )
            continue

        chain_id = binder_chains.get(chain_type)
        chain_info = chain_metrics.get(chain_id or "", {}) if chain_id else {}
        residue_numbers = chain_info.get("residue_numbers") if isinstance(chain_info, dict) else None
        if not chain_id or not seq_range or not isinstance(residue_numbers, list):
            continue

        start_idx, end_idx = int(seq_range[0]), int(seq_range[1])
        if start_idx < 0 or end_idx < start_idx or end_idx >= len(residue_numbers):
            continue

        selections.append(
            {
                "region": region,
                "chain_id": str(chain_id),
                "start_residue_number": int(residue_numbers[start_idx]),
                "end_residue_number": int(residue_numbers[end_idx]),
            }
        )

    return selections

@router.get("/{design_id}/antibody", response_model=AntibodyData)
async def get_antibody_data(
    design_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get cached antibody-specific data (CDRs, overlays, stability)."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")

    payload = await _get_cached_design_analysis_payload(
        session,
        design,
        "antibody_annotation_pack",
        raw_params={},
    )
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="Cached antibody payload is unavailable")
    return AntibodyData.model_validate(payload)

@router.get("/{design_id}/pdb-imgt")
async def get_design_imgt_pdb(
    design_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Download the IMGT-renumbered PDB file for a design."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design or not design.pdb_path:
        raise HTTPException(status_code=404, detail="Design not found or no PDB")
    
    pdb_path = resolve_runtime_data_path(design.pdb_path)
    imgt_path = pdb_path.parent / f"{pdb_path.stem}_imgt.pdb"
    
    if not imgt_path.exists():
        raise HTTPException(status_code=404, detail="IMGT renumbered PDB not found")
    
    return FileResponse(
        path=imgt_path,
        filename=f"{design.name}_imgt.pdb",
        media_type="text/plain"
    )

@router.get("/{design_id}/antifold-logits")
async def get_antifold_logits(
    design_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get AntiFold probability CSV data (if available)."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design or not design.antifold_logits_path:
        raise HTTPException(status_code=404, detail="No AntiFold data for this design")
        
    path = resolve_runtime_data_path(design.antifold_logits_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Logits file not found")
        
    return FileResponse(path, media_type="text/csv", filename=f"{design.name}_logits.csv")


# --- Phase 3a: Plotly Analytics Endpoints ---

class ContactMapData(BaseModel):
    """Contact map data for heatmap visualization."""
    design_id: str
    design_name: str
    distance_matrix: List[List[float]]  # 2D distance matrix
    residue_numbers: List[int]
    chain_ids: List[str]
    size: int


@router.get("/{design_id}/contact-map", response_model=ContactMapData)
async def get_contact_map(
    design_id: str,
    max_size: int = Query(400, description="Maximum matrix dimension"),
    session: AsyncSession = Depends(get_session)
):
    """
    Get Cα-Cα distance matrix for contact map visualization.
    
    Computes pairwise distances between all Cα atoms in the structure.
    Large structures are automatically downsampled for rendering performance.
    """
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    if not design.pdb_path:
        raise HTTPException(status_code=404, detail="No structure file for this design")
    
    structure_path = resolve_runtime_data_path(design.pdb_path)
    if not structure_path.exists():
        raise HTTPException(status_code=404, detail="Structure file not found on disk")
    
    try:
        run, _definition, _params, _cache_key = await get_matching_design_analysis_run(
            session,
            design,
            "contact_map",
            raw_params={"max_size": max_size},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if run is None:
        raise HTTPException(status_code=404, detail="Contact map not computed yet")
    if run.status != "completed":
        raise HTTPException(status_code=409, detail=f"Contact map status is {run.status}")

    payload = load_analysis_result(run)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="Cached contact-map payload is unavailable")
    return ContactMapData.model_validate(payload)


class ChainPairIptmData(BaseModel):
    """Chain-pair iPTM matrix data."""
    design_id: str
    design_name: str
    chain_ids: List[str]
    iptm_matrix: List[List[Optional[float]]]  # NxN matrix
    size: int


@router.get("/{design_id}/chain-iptm", response_model=ChainPairIptmData)
async def get_chain_pair_iptm(
    design_id: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Get chain-pair iPTM matrix for interface quality visualization.
    
    Returns the NxN matrix of interface pTM scores between all chain pairs,
    already stored from Boltz2/AF3 predictions.
    """
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    pair_data = _normalize_chain_matrix(design.pair_chains_iptm)
    chains_ptm = _normalize_chain_scalar_map(design.chains_ptm)

    if not pair_data and not chains_ptm:
        raise HTTPException(status_code=404, detail="No chain-pair iPTM data for this design")

    # Get all chain indices from matrix rows/cols and diagonal chain pTM entries.
    chain_idx_set = set(chains_ptm.keys())
    for row_idx, row in pair_data.items():
        chain_idx_set.add(row_idx)
        chain_idx_set.update(row.keys())

    chain_indices = sorted(chain_idx_set, key=lambda x: int(x) if x.isdigit() else x)
    n = len(chain_indices)
    
    # Build symmetric matrix
    iptm_matrix = []
    for i in chain_indices:
        row = []
        for j in chain_indices:
            if i == j:
                # Diagonal: use chains_ptm if available
                if i in chains_ptm:
                    row.append(chains_ptm.get(i))
                else:
                    row.append(None)
            else:
                # Off-diagonal: get from pair_chains_iptm
                val = None
                if i in pair_data and j in pair_data[i]:
                    val = pair_data[i][j]
                elif j in pair_data and i in pair_data[j]:
                    val = pair_data[j][i]  # Symmetric
                row.append(val)
        iptm_matrix.append(row)
    
    # Convert chain indices to friendly names if possible
    chain_labels = [_chain_label(c) for c in chain_indices]
    
    return ChainPairIptmData(
        design_id=design.id,
        design_name=design.name,
        chain_ids=chain_labels,
        iptm_matrix=iptm_matrix,
        size=n
    )


@router.get("/export/fasta")
async def export_fasta(
    job_id: str = Query(...),
    mode: str = Query("binder", description="binder or cdr"),
    session: AsyncSession = Depends(get_session),
):
    """Export binder or CDR sequences as FASTA for all designs in a job."""
    from fastapi.responses import PlainTextResponse

    result = await session.execute(
        select(Design).where(Design.job_id == job_id)
    )
    designs = result.scalars().all()
    if not designs:
        raise HTTPException(status_code=404, detail="No designs found for this job")

    job_result = await session.execute(select(Job).where(Job.id == job_id))
    job_obj = job_result.scalar_one_or_none()
    target_chains: set[str] = set()
    if job_obj:
        job_params = job_obj.params if isinstance(job_obj.params, dict) else {}
        raw_antigen = job_params.get("antigen_chains") or ""
        if isinstance(raw_antigen, str):
            target_chains = {c.strip() for c in raw_antigen.split(",") if c.strip()}

    lines: list[str] = []
    for d in designs:
        name_lower = (d.name or "").lower()
        if "normalized_target" in name_lower or "target" == name_lower:
            continue

        if mode == "cdr":
            cdr_parts: list[tuple[str, str]] = []
            for label, attr in [("CDR-H1", "cdr_h1"), ("CDR-H2", "cdr_h2"), ("CDR-H3", "cdr_h3"),
                                ("CDR-L1", "cdr_l1"), ("CDR-L2", "cdr_l2"), ("CDR-L3", "cdr_l3")]:
                val = getattr(d, attr, None)
                if isinstance(val, str) and val.strip():
                    cdr_parts.append((label, val.strip()))
            if cdr_parts:
                header_annot = " ".join(f"{lbl}={seq}" for lbl, seq in cdr_parts)
                lines.append(f">{d.name} {header_annot}")
                lines.append("".join(seq for _, seq in cdr_parts))
                continue

        seq = _compute_binder_sequence_response_value(d, include_structure_fallback=True)
        if d.pdb_path:
            try:
                structure_path = resolve_runtime_data_path(d.pdb_path)
                if structure_path.exists():
                    sequences = extract_sequence_from_pdb(str(structure_path))
                    detected_binder_chains = {
                        chain_id.strip()
                        for chain_id in str(getattr(d, "detected_antibody_chains", "") or "").split(",")
                        if chain_id.strip()
                    }
                    detected_target_chains = {
                        chain_id.strip()
                        for chain_id in str(getattr(d, "detected_target_chain", "") or "").split(",")
                        if chain_id.strip()
                    }

                    if detected_binder_chains:
                        binder_seqs = [
                            sequences[cid].strip()
                            for cid in detected_binder_chains
                            if isinstance(sequences.get(cid), str) and sequences[cid].strip()
                        ]
                        if binder_seqs:
                            seq = "|".join(binder_seqs)
                    elif not seq:
                        excluded_chains = detected_target_chains or target_chains
                        binder_seqs = [
                            sequences[cid].strip()
                            for cid in sequences
                            if cid not in excluded_chains
                            and isinstance(sequences.get(cid), str)
                            and sequences[cid].strip()
                        ]
                        if binder_seqs:
                            seq = "|".join(binder_seqs)
            except Exception:
                pass
        if seq:
            chains = seq.split("|")
            if len(chains) > 1:
                for idx, chain in enumerate(chains):
                    if chain.strip():
                        lines.append(f">{d.name}_chain{idx + 1}")
                        lines.append(chain.strip())
            else:
                lines.append(f">{d.name}")
                lines.append(seq.strip())

    if not lines:
        raise HTTPException(status_code=404, detail="No sequences could be extracted")

    job_name = ((job_obj.name if job_obj else None) or "designs").replace(" ", "_")
    filename = f"{job_name}_{mode}_sequences.fasta"

    return PlainTextResponse(
        "\n".join(lines) + "\n",
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
