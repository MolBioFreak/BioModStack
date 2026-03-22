"""
Analytics API router - Aggregated metrics and batch comparisons.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from typing import List, Dict, Any, Optional
import numpy as np
from pydantic import BaseModel

from database import get_session, Job, Design
from services.analysis_runs import get_matching_job_analysis_run, load_analysis_result
from schemas import JobResponse

router = APIRouter()

# --- Schemas ---

class MetricDistribution(BaseModel):
    min: float
    max: float
    avg: float
    median: float
    std_dev: float
    histogram_bins: List[float]
    histogram_counts: List[int]

class ScatterPoint(BaseModel):
    x: float
    y: float
    id: str

class JobAnalytics(BaseModel):
    job_id: str
    design_count: int
    metrics: Dict[str, Optional[MetricDistribution]] | None
    correlations: Dict[str, List[ScatterPoint]] | None
    pipeline_summary: Dict[str, Any]

class DesignMetricPoint(BaseModel):
    id: str
    name: str
    metrics: Dict[str, float]

class BatchAnalytics(BaseModel):
    job_ids: List[str]
    metrics_summary: Dict[str, Dict[str, float]]  # metric -> {job_id -> avg}
    common_metrics: List[str]


# --- Helpers ---

def calculate_distribution(values: List[float], bins: int = 10) -> MetricDistribution:
    """Calculate statistical distribution for a list of values."""
    if not values:
        return None
    
    arr = np.array(values)
    hist, bin_edges = np.histogram(arr, bins=bins)
    
    return MetricDistribution(
        min=float(np.min(arr)),
        max=float(np.max(arr)),
        avg=float(np.mean(arr)),
        median=float(np.median(arr)),
        std_dev=float(np.std(arr)),
        histogram_bins=[float(b) for b in bin_edges],
        histogram_counts=[int(c) for c in hist]
    )

def extract_metrics(designs: List[Design]) -> Dict[str, List[float]]:
    """Extract all available numerical metrics from designs."""
    metrics = {
        "plddt_overall": [],
        "plddt_binder": [],
        "pae_overall": [],
        "pae_interaction": [],
        "rmsd_binder": [],
        "rmsd_overall": [],
        "mpnn_score": [],
        "conf_score": [],
        "ptm": [],
        "rog": [],
        "ligand_iptm": [],
        "affinity_score": [],
        "binder_probability": []
    }
    
    for d in designs:
        if d.plddt_overall is not None: metrics["plddt_overall"].append(d.plddt_overall)
        if d.plddt_binder is not None: metrics["plddt_binder"].append(d.plddt_binder)
        if d.pae_overall is not None: metrics["pae_overall"].append(d.pae_overall)
        if d.pae_interaction is not None: metrics["pae_interaction"].append(d.pae_interaction)
        if d.rmsd_binder is not None: metrics["rmsd_binder"].append(d.rmsd_binder)
        if d.rmsd_overall is not None: metrics["rmsd_overall"].append(d.rmsd_overall)
        if d.mpnn_score is not None: metrics["mpnn_score"].append(d.mpnn_score)
        if d.conf_score is not None: metrics["conf_score"].append(d.conf_score)
        if d.ptm is not None: metrics["ptm"].append(d.ptm)
        if d.rog is not None: metrics["rog"].append(d.rog)
        if d.ligand_iptm is not None: metrics["ligand_iptm"].append(d.ligand_iptm)
        if d.affinity_score is not None: metrics["affinity_score"].append(d.affinity_score)
        if d.binder_probability is not None: metrics["binder_probability"].append(d.binder_probability)
            
    return metrics


# --- Endpoints ---

@router.get("/job/{job_id}", response_model=JobAnalytics)
async def get_job_analytics(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get aggregated analytics for a single job."""
    # Fetch designs
    query = select(Design).where(Design.job_id == job_id)
    result = await session.execute(query)
    designs = result.scalars().all()
    
    if not designs:
        return JobAnalytics(
            job_id=job_id,
            design_count=0,
            metrics=None,
            correlations=None,
            pipeline_summary={}
        )

    # Extract raw values
    raw_metrics = extract_metrics(designs)
    
    # Calculate distributions
    analyzed_metrics = {}
    for name, values in raw_metrics.items():
        if values:
            analyzed_metrics[name] = calculate_distribution(values)
            
    # Calculate correlations for scatter plots (e.g. pLDDT vs PAE)
    correlations = {}
    if raw_metrics["plddt_overall"] and raw_metrics["pae_overall"]:
        correlations["plddt_vs_pae"] = [
            ScatterPoint(x=d.plddt_overall, y=d.pae_overall, id=d.id)
            for d in designs if d.plddt_overall is not None and d.pae_overall is not None
        ]

    return JobAnalytics(
        job_id=job_id,
        design_count=len(designs),
        metrics=analyzed_metrics,
        correlations=correlations,
        pipeline_summary={
            "total_designs": len(designs),
            "favorites": sum(1 for d in designs if d.is_favorite)
        }
    )

@router.get("/job/{job_id}/designs", response_model=List[DesignMetricPoint])
async def get_job_design_metrics(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get raw metrics for all designs in a job (for custom client-side charting)."""
    query = select(Design).where(Design.job_id == job_id)
    result = await session.execute(query)
    designs = result.scalars().all()
    
    return [
        DesignMetricPoint(
            id=d.id,
            name=d.name,
            metrics={
                "plddt_overall": d.plddt_overall or 0,
                "pae_overall": d.pae_overall or 0,
                "ptm": d.ptm or 0,
                "rmsd_binder": d.rmsd_binder or 0
            }
        )
        for d in designs
    ]

@router.post("/batch", response_model=BatchAnalytics)
async def get_batch_analytics(
    job_ids: List[str],
    session: AsyncSession = Depends(get_session)
):
    """Compare metrics across multiple jobs."""
    summary = {}
    
    # Simple loop for now - optimal would be single query with group_by
    for jid in job_ids:
        query = select(Design).where(Design.job_id == jid)
        result = await session.execute(query)
        designs = result.scalars().all()
        
        if not designs:
            continue
            
        metrics = extract_metrics(designs)
        
        # Calculate averages for comparison
        for m_name, values in metrics.items():
            if values:
                if m_name not in summary:
                    summary[m_name] = {}
                summary[m_name][jid] = float(np.mean(values))
                
    return BatchAnalytics(
        job_ids=job_ids,
        metrics_summary=summary,
        common_metrics=list(summary.keys())
    )


# === NEW ADVANCED ANALYTICS ENDPOINTS ===

class CorrelationPoint(BaseModel):
    metric_x: str
    metric_y: str
    r: float  # Pearson R
    n: int    # Sample size

class CorrelationMatrix(BaseModel):
    job_id: str
    metrics: List[str]
    matrix: List[List[float]]  # NxN Pearson R values
    sample_sizes: List[List[int]]  # NxN sample counts

class AACount(BaseModel):
    aa: str
    count: int
    frequency: float

class CDRComposition(BaseModel):
    cdr_name: str
    total_residues: int
    composition: List[AACount]

class AACompositionResponse(BaseModel):
    job_id: str
    overall: List[AACount]
    by_cdr: List[CDRComposition]

class PositionFrequency(BaseModel):
    position: int
    frequencies: Dict[str, float]  # AA -> frequency

class SequenceLogoData(BaseModel):
    cdr_name: str
    length: int
    positions: List[PositionFrequency]
    consensus: str
    sequence_count: int

class CDRAnalysisResponse(BaseModel):
    job_id: str
    logos: List[SequenceLogoData]


def pearson_r(x: List[float], y: List[float]) -> float:
    """Calculate Pearson correlation coefficient."""
    if len(x) < 3 or len(y) < 3:
        return 0.0
    x_arr = np.array(x)
    y_arr = np.array(y)
    # Filter to only paired non-null values
    mask = ~(np.isnan(x_arr) | np.isnan(y_arr))
    x_arr = x_arr[mask]
    y_arr = y_arr[mask]
    if len(x_arr) < 3:
        return 0.0
    return float(np.corrcoef(x_arr, y_arr)[0, 1])


STANDARD_AAs = "ACDEFGHIKLMNPQRSTVWY"


async def _get_cached_job_analysis_payload(
    session: AsyncSession,
    job: Job,
    analysis_type: str,
    *,
    include_children: bool = True,
    design_ids: Optional[list[str]] = None,
) -> Any:
    try:
        run, _definition, _params, _cache_key = await get_matching_job_analysis_run(
            session,
            job,
            analysis_type,
            raw_params={
                "include_children": include_children,
                "design_ids": design_ids or [],
            },
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


@router.get("/job/{job_id}/correlation-matrix", response_model=CorrelationMatrix)
async def get_correlation_matrix(
    job_id: str,
    include_children: bool = Query(True, description="Include child-job designs in the analysis scope"),
    design_ids: Optional[str] = Query(None, description="Comma-separated design ids to restrict the analysis scope"),
    session: AsyncSession = Depends(get_session)
):
    """Return cached pairwise Pearson correlations between numeric metrics."""
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    design_scope = [part.strip() for part in (design_ids or "").split(",") if part.strip()]
    payload = await _get_cached_job_analysis_payload(
        session,
        job,
        "job_correlation_matrix",
        include_children=include_children,
        design_ids=design_scope,
    )
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="Cached correlation-matrix payload is unavailable")
    return CorrelationMatrix.model_validate(payload)


@router.get("/job/{job_id}/aa-composition", response_model=AACompositionResponse)
async def get_aa_composition(
    job_id: str,
    include_children: bool = Query(True, description="Include child-job designs in the analysis scope"),
    design_ids: Optional[str] = Query(None, description="Comma-separated design ids to restrict the analysis scope"),
    session: AsyncSession = Depends(get_session)
):
    """Return cached amino acid composition from CDR sequences."""
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    design_scope = [part.strip() for part in (design_ids or "").split(",") if part.strip()]
    payload = await _get_cached_job_analysis_payload(
        session,
        job,
        "job_aa_composition",
        include_children=include_children,
        design_ids=design_scope,
    )
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="Cached aa-composition payload is unavailable")
    return AACompositionResponse.model_validate(payload)


@router.get("/job/{job_id}/cdr-logos", response_model=CDRAnalysisResponse)
async def get_cdr_sequence_logos(
    job_id: str,
    include_children: bool = Query(True, description="Include child-job designs in the analysis scope"),
    design_ids: Optional[str] = Query(None, description="Comma-separated design ids to restrict the analysis scope"),
    session: AsyncSession = Depends(get_session)
):
    """Return cached sequence logo data for CDR regions."""
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    design_scope = [part.strip() for part in (design_ids or "").split(",") if part.strip()]
    payload = await _get_cached_job_analysis_payload(
        session,
        job,
        "job_cdr_logo_pack",
        include_children=include_children,
        design_ids=design_scope,
    )
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="Cached cdr-logo payload is unavailable")
    return CDRAnalysisResponse.model_validate(payload)
