"""
Designs API router - Query and manage protein designs.

Provides endpoints for listing, filtering, and managing designs
stored in the SQLite database after pipeline ingestion.
"""

import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel
from datetime import datetime
from pathlib import Path
import math

from database import get_session, Design, Job
from paths import to_allowed_relative
from services.stage_review import REVIEWABLE_STAGES, ensure_stage_review_rows


router = APIRouter()


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
    
    # Per-residue metrics (for charts)
    residue_plddt: Optional[List[float]] = None
    chain_metrics: Optional[Dict[str, ChainMetric]] = None
    
    # Antibody CDR annotation
    binder_length: Optional[int] = None
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
    
    # Backbone grouping & epitope analysis
    backbone_id: Optional[int] = None
    epitope_contact_count: Optional[int] = None
    epitope_min_distance: Optional[float] = None
    epitope_min_atom_distance: Optional[float] = None
    epitope_nearest_antibody_residue: Optional[str] = None
    epitope_nearest_target_residue: Optional[str] = None
    epitope_nearest_antibody_atom: Optional[str] = None
    epitope_nearest_target_atom: Optional[str] = None
    target_contact_count: Optional[int] = None
    target_min_distance: Optional[float] = None
    target_min_atom_distance: Optional[float] = None
    target_nearest_antibody_residue: Optional[str] = None
    target_nearest_target_residue: Optional[str] = None
    target_nearest_antibody_atom: Optional[str] = None
    target_nearest_target_atom: Optional[str] = None
    screening_reason: Optional[str] = None
    
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
    
    created_at: datetime
    
    class Config:
        from_attributes = True


class DesignList(BaseModel):
    designs: List[DesignResponse]
    total: int


class FavoriteUpdate(BaseModel):
    is_favorite: bool


class NotesUpdate(BaseModel):
    notes: str


class PlotlyMetricPoint(BaseModel):
    id: str
    name: str
    metrics: Dict[str, float]


class PlotlyMetricsResponse(BaseModel):
    job_id: str
    metric_keys: List[str]
    points: List[PlotlyMetricPoint]
    total: int


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
        "target_contact_count": design.target_contact_count,
        "target_min_distance": design.target_min_distance,
        "target_min_atom_distance": design.target_min_atom_distance,
        "frustration_high_count": design.frustration_high_count,
        "frustration_min_count": design.frustration_min_count,
        "frustration_pct_high": design.frustration_pct_high,
        "maturation_delta_interface": design.maturation_delta_interface,
        "maturation_interface_score": design.maturation_interface_score,
        "maturation_rmsd": design.maturation_rmsd,
    }
    _inject_metric(metrics, "screening_reason_present", 1.0 if design.screening_reason else None)
    for key, value in base_metrics.items():
        _inject_metric(metrics, key, value)
    if design.has_clash is not None:
        metrics["has_clash"] = 1.0 if design.has_clash else 0.0

    raw_conf = design.confidence_metrics if isinstance(design.confidence_metrics, dict) else {}
    for key, value in raw_conf.items():
        _inject_metric(metrics, key, value)

    return metrics


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


def _design_to_response(design: Design) -> DesignResponse:
    data = DesignResponse.model_validate(design).model_dump()
    data["frustration_csv_relpath"] = _safe_allowed_relative(design.frustration_csv_path)
    return DesignResponse.model_validate(data)


# --- Endpoints ---

@router.get("", response_model=DesignList)
async def list_designs(
    job_id: Optional[str] = None,
    include_children: bool = Query(True, description="Include designs from child jobs (for parent jobs)"),
    backbone_id: Optional[int] = Query(None, description="Filter by backbone ID"),
    plddt_min: Optional[float] = Query(None, description="Minimum pLDDT score"),
    pae_max: Optional[float] = Query(None, description="Maximum pAE score"),
    iptm_min: Optional[float] = Query(None, description="Minimum iPTM score"),
    rog_min: Optional[float] = Query(None, description="Minimum radius of gyration"),
    rog_max: Optional[float] = Query(None, description="Maximum radius of gyration"),
    rfd_rog_min: Optional[float] = Query(None, description="Minimum RFdiffusion radius of gyration"),
    rfd_rog_max: Optional[float] = Query(None, description="Maximum RFdiffusion radius of gyration"),
    favorites_only: bool = Query(False, description="Show only favorites"),
    sort_by: Optional[str] = Query(None, description="Sort field: plddt, iptm, ptm, pae, conf_score, rog, rfd_rog, backbone"),
    sort_desc: bool = Query(True, description="Sort descending"),
    limit: int = Query(100, le=10000),
    offset: int = Query(0),
    session: AsyncSession = Depends(get_session)
):
    """
    List designs with optional filtering.
    
    Filters:
    - job_id: Filter by specific job (if include_children=True, also includes child job designs)
    - include_children: When job_id is specified, also fetch designs from child jobs
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
        if (
            selected_job
            and not include_children
            and bool(selected_job.awaiting_input)
            and str(selected_job.awaiting_stage or "").strip().lower() in REVIEWABLE_STAGES
        ):
            review_stage = str(selected_job.awaiting_stage or "").strip().lower()
            await ensure_stage_review_rows(session, selected_job)

    # Build base query with optional sorting
    sort_field_map = {
        'plddt': Design.plddt_overall,
        'name': Design.name,
        'iptm': Design.iptm,
        'ptm': Design.ptm,
        'pae': Design.pae_overall,
        'conf_score': Design.conf_score,
        'confidence': Design.conf_score,
        'backbone': Design.backbone_id,
        'rog': Design.rog,
        'rfd_rog': Design.rfd_rog,
        'frustration_high_count': Design.frustration_high_count,
        'frustration_pct_high': Design.frustration_pct_high,
    }
    
    order_col = sort_field_map.get(sort_by, Design.created_at)
    if sort_desc:
        query = select(Design).order_by(order_col.desc().nulls_last())
    else:
        query = select(Design).order_by(order_col.asc().nulls_last())
    
    # Apply filters - handle include_children for job_id
    conditions = []
    if job_id:
        if include_children:
            # Get all child job IDs for this parent
            child_query = select(Job.id).where(Job.parent_job_id == job_id)
            child_result = await session.execute(child_query)
            child_job_ids = [row[0] for row in child_result.all()]
            
            # Include both parent and children
            all_job_ids = [job_id] + child_job_ids
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
    if backbone_id is not None:
        conditions.append(Design.backbone_id == backbone_id)
    if plddt_min is not None:
        conditions.append(Design.plddt_overall >= plddt_min)
    if pae_max is not None:
        conditions.append(Design.pae_overall <= pae_max)
    if iptm_min is not None:
        conditions.append(Design.iptm >= iptm_min)
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


@router.get("/by-job/{job_id}/backbone-summary")
async def get_backbone_summary(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """
    Get backbone-level aggregate statistics for a job.
    
    Returns counts and average metrics per backbone for UI toggle display.
    """
    job_result = await session.execute(select(Job).where(Job.id == job_id))
    job = job_result.scalar_one_or_none()
    review_stage: Optional[str] = None
    if (
        job
        and bool(job.awaiting_input)
        and str(job.awaiting_stage or "").strip().lower() in REVIEWABLE_STAGES
    ):
        review_stage = str(job.awaiting_stage or "").strip().lower()
        await ensure_stage_review_rows(session, job)

    result = await session.execute(
        select(Design)
        .where(
            Design.job_id == job_id,
            Design.source_stage == review_stage if review_stage else Design.source_stage.is_(None),
        )
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
                "epitope_contact_values": [],
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
            ("epitope_contact_values", design.epitope_contact_count),
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
        contact_values = entry["epitope_contact_values"]
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
            "max_epitope_contacts": int(max(contact_values)) if contact_values else None,
            "min_epitope_distance": _round_nullable(min(distance_values), 2) if distance_values else None,
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
    
    return _design_to_response(design)


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
    
    pdb_path = Path(design.pdb_path)
    if not pdb_path.exists():
        raise HTTPException(status_code=404, detail="PDB file not found on disk")
    
    return FileResponse(
        path=pdb_path,
        filename=f"{design.name}.pdb",
        media_type="text/plain"  # Changed from chemical/x-pdb for Mol* compatibility
    )


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
        
    # Compute on-the-fly if not cached
    if not design.chain_metrics and design.pdb_path:
        try:
            from services.structure_utils import get_per_chain_metrics
            metrics = get_per_chain_metrics(design.pdb_path)
            if metrics:
                design.chain_metrics = metrics
                await session.commit()
        except Exception as e:
            print(f"Failed to compute chain metrics: {e}")
            # Don't fail the request, just return empty
    
    return design.chain_metrics or {}


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
    limit: int = Query(100, le=10000),
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
    if not job_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Build job_id filter - include children if requested
    if include_children:
        # Get all child job IDs for this parent
        child_query = select(Job.id).where(Job.parent_job_id == job_id)
        child_result = await session.execute(child_query)
        child_job_ids = [row[0] for row in child_result.all()]
        
        # Include both parent and children
        all_job_ids = [job_id] + child_job_ids
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
    limit: int = Query(10000, ge=1, le=50000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session)
):
    """Return flattened numeric metrics for Plotly charting (including raw confidence metrics)."""
    job_result = await session.execute(select(Job).where(Job.id == job_id))
    if not job_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Job not found")

    job_ids = [job_id]
    if include_children:
        child_result = await session.execute(select(Job.id).where(Job.parent_job_id == job_id))
        job_ids.extend([row[0] for row in child_result.all()])

    query = (
        select(Design)
        .where(Design.job_id.in_(job_ids))
        .order_by(Design.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(query)
    designs = result.scalars().all()

    count_query = select(func.count(Design.id)).where(Design.job_id.in_(job_ids))
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
                metrics=metrics
            )
        )

    return PlotlyMetricsResponse(
        job_id=job_id,
        metric_keys=sorted(metric_keys),
        points=points,
        total=int(total)
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
    
    structure_path = Path(design.pdb_path)
    if not structure_path.exists():
        raise HTTPException(status_code=404, detail="Structure file not found on disk")
    
    # Import Biotite utilities
    try:
        from services.structure_utils import (
            get_residue_count, get_chain_ids, 
            compute_gyration_radius, get_secondary_structure
        )
    except ImportError:
        raise HTTPException(status_code=500, detail="Structure analysis module not available")
    
    return StructureAnalysis(
        design_id=design.id,
        design_name=design.name,
        residue_count=get_residue_count(structure_path),
        chain_ids=[str(c) for c in get_chain_ids(structure_path)],
        gyration_radius=compute_gyration_radius(structure_path),
        secondary_structure=get_secondary_structure(structure_path)
    )


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
    
    path1, path2 = Path(design1.pdb_path), Path(design2.pdb_path)
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
    session: AsyncSession = Depends(get_session)
):
    """
    Get PAE matrix data for heatmap visualization.
    
    Searches for confidence JSON files associated with the design's PDB path.
    Supports multiple formats:
    - *_confidences.json (RF3/Boltz2 format)
    - confidence_*.json (Antibody/IgFold format)
    """
    import json
    
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    if not design.pdb_path:
        raise HTTPException(status_code=404, detail="No structure file for this design")
    
    pdb_path = Path(design.pdb_path)
    parent_dir = pdb_path.parent
    design_stem = pdb_path.stem  # e.g., "antibody_job_1_seq_2_model_0"
    
    # Search for confidence files with multiple patterns
    confidence_file = None
    
    # Pattern 1: *_confidences.json (RF3/Boltz2 format)
    candidates = list(parent_dir.glob("*_confidences.json"))
    if not candidates:
        candidates = list(parent_dir.parent.glob("*_confidences.json"))
    if candidates:
        # Prefer file matching design name
        for c in candidates:
            if design_stem in c.stem:
                confidence_file = c
                break
        if not confidence_file:
            confidence_file = candidates[0]
    
    # Pattern 2: confidence_*.json (Antibody/IgFold format)
    if not confidence_file:
        candidates = list(parent_dir.glob(f"confidence_{design_stem}.json"))
        if candidates:
            confidence_file = candidates[0]
        else:
            # Try broader search
            candidates = list(parent_dir.glob("confidence_*.json"))
            for c in candidates:
                if design_stem in c.stem:
                    confidence_file = c
                    break
    
    # Pattern 3: Check parent directory for antibody format
    if not confidence_file:
        candidates = list(parent_dir.parent.glob(f"confidence_{design_stem}.json"))
        if candidates:
            confidence_file = candidates[0]
    
    if not confidence_file:
        # No confidence file found - try to generate pseudo-PAE from pLDDT
        if design.residue_plddt and len(design.residue_plddt) > 0:
            plddt = design.residue_plddt
            size = len(plddt)
            # Generate diagonal-weighted pseudo-PAE matrix
            # High pLDDT = low PAE on diagonal, off-diagonal weighted by distance
            pae_matrix = []
            for i in range(size):
                row = []
                for j in range(size):
                    # Convert pLDDT to PAE-like scale (0-30)
                    avg_conf = (plddt[i] + plddt[j]) / 2
                    base_pae = 30 * (1 - avg_conf / 100)  # Lower pLDDT = higher PAE
                    # Add distance penalty for off-diagonal
                    dist_penalty = min(abs(i - j) * 0.1, 10)
                    row.append(min(base_pae + dist_penalty, 30))
                pae_matrix.append(row)
            
            # Downsample if needed
            if size > 200:
                step = size // 200
                pae_matrix = [[pae_matrix[i][j] for j in range(0, size, step)] for i in range(0, size, step)]
                size = len(pae_matrix)
            
            return PAEData(
                design_id=design.id,
                design_name=design.name + " (estimated)",
                pae_matrix=pae_matrix,
                size=size
            )
        raise HTTPException(status_code=404, detail="No PAE data found for this design")
    
    # Read the confidence file
    try:
        with open(confidence_file, 'r') as f:
            data = json.load(f)
        
        pae_matrix = data.get('pae')
        
        # If no PAE matrix, try to generate from pLDDT in file or design
        if not pae_matrix:
            plddt = data.get('plddt') or data.get('per_residue_plddt')
            if not plddt and design.residue_plddt:
                plddt = design.residue_plddt
            
            if plddt and len(plddt) > 0:
                size = len(plddt)
                pae_matrix = []
                for i in range(size):
                    row = []
                    for j in range(size):
                        avg_conf = (plddt[i] + plddt[j]) / 2
                        base_pae = 30 * (1 - avg_conf / 100)
                        dist_penalty = min(abs(i - j) * 0.1, 10)
                        row.append(min(base_pae + dist_penalty, 30))
                    pae_matrix.append(row)
            else:
                raise HTTPException(status_code=404, detail="PAE matrix not found in confidence file and cannot be estimated")
        
        # Downsample if too large (for rendering performance)
        size = len(pae_matrix)
        if size > 200:
            step = size // 200
            pae_matrix = [[pae_matrix[i][j] for j in range(0, size, step)] for i in range(0, size, step)]
            size = len(pae_matrix)
        
        return PAEData(
            design_id=design.id,
            design_name=design.name,
            pae_matrix=pae_matrix,
            size=size
        )
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Failed to parse confidence file as JSON")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read PAE data: {str(e)}")


class AntibodyData(BaseModel):
    """Aggregate antibody metrics."""
    design_id: str
    cdrs: Dict[str, Optional[str]]
    humanness_score: Optional[float]
    stability_data: Optional[Dict[str, Any]]
    imgt_pdb_url: Optional[str]
    
@router.get("/{design_id}/antibody", response_model=AntibodyData)
async def get_antibody_data(
    design_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get antibody-specific data (CDRs, humanness, stability)."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")

    has_annotation = any(
        getattr(design, field)
        for field in ("cdr_h1", "cdr_h2", "cdr_h3", "cdr_l1", "cdr_l2", "cdr_l3")
    )
    if not has_annotation and design.pdb_path:
        try:
            from services.cdr_annotator import annotate_pdb

            annotation = await asyncio.to_thread(annotate_pdb, design.pdb_path)
            if annotation:
                design.antibody_type = annotation.antibody_type
                design.binder_length = annotation.binder_length
                design.cdr_h1 = annotation.cdr_h1
                design.cdr_h2 = annotation.cdr_h2
                design.cdr_h3 = annotation.cdr_h3
                design.cdr_l1 = annotation.cdr_l1
                design.cdr_l2 = annotation.cdr_l2
                design.cdr_l3 = annotation.cdr_l3
                design.cdr_h1_length = annotation.cdr_h1_length
                design.cdr_h2_length = annotation.cdr_h2_length
                design.cdr_h3_length = annotation.cdr_h3_length
                design.cdr_l1_length = annotation.cdr_l1_length
                design.cdr_l2_length = annotation.cdr_l2_length
                design.cdr_l3_length = annotation.cdr_l3_length
                design.fr2_contacts = annotation.fr2_contacts
                design.de_loop = annotation.de_loop
                design.fr3_contacts = annotation.fr3_contacts
                design.fr4_contacts = annotation.fr4_contacts
                await session.commit()
        except Exception:
            await session.rollback()

    imgt_url = None
    if design.pdb_path:
        pdb_path = Path(design.pdb_path)
        # Check for _imgt.pdb variant
        # If original is "X.pdb", look for "X_imgt.pdb"
        # If original is "X_imgt.pdb", we are good.
        if "_imgt" in pdb_path.name:
             imgt_url = f"/api/designs/{design.id}/pdb"
        else:
             imgt_chk = pdb_path.parent / f"{pdb_path.stem}_imgt.pdb"
             if imgt_chk.exists():
                 imgt_url = f"/api/designs/{design.id}/pdb-imgt"

    return AntibodyData(
        design_id=design.id,
        cdrs={
            "H1": design.cdr_h1, "H2": design.cdr_h2, "H3": design.cdr_h3,
            "L1": design.cdr_l1, "L2": design.cdr_l2, "L3": design.cdr_l3
        },
        humanness_score=design.humanness_score,
        stability_data=design.stability_data,
        imgt_pdb_url=imgt_url
    )

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
    
    pdb_path = Path(design.pdb_path)
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
        
    path = Path(design.antifold_logits_path)
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
    
    structure_path = Path(design.pdb_path)
    if not structure_path.exists():
        raise HTTPException(status_code=404, detail="Structure file not found on disk")
    
    try:
        from services.structure_utils import compute_contact_map
    except ImportError:
        raise HTTPException(status_code=500, detail="Structure analysis module not available")
    
    distance_matrix, res_ids, chain_ids = compute_contact_map(structure_path, max_size=max_size)
    
    if distance_matrix is None:
        raise HTTPException(status_code=404, detail="Could not compute contact map for this structure")
    
    return ContactMapData(
        design_id=design.id,
        design_name=design.name,
        distance_matrix=distance_matrix,
        residue_numbers=res_ids,
        chain_ids=chain_ids,
        size=len(distance_matrix)
    )


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
