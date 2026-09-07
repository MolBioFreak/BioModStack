"""
Analytics API router - Aggregated metrics and batch comparisons.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from typing import List, Dict, Any, Optional
import numpy as np
from pydantic import BaseModel

from services.scientific_analytics import MetricState, MetricDescriptor, MetricSource, ScientificCohort, owning_jobs, projection, persisted_projection, revision_for_job, partition
from database import get_session, Job, Design
from services.analysis_runs import get_matching_job_analysis_run, load_analysis_result, validate_job_analysis_request
from services.result_contracts import resolve_result_contract
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
    scientific_cohorts: List[ScientificCohort] = []
    job_id: str
    design_count: int
    metrics: Dict[str, Optional[MetricDistribution]] | None
    correlations: Dict[str, List[ScatterPoint]] | None
    pipeline_summary: Dict[str, Any]

class DesignMetricPoint(BaseModel):
    model_config = {"extra": "forbid"}
    contract_revision: int | None = None
    source_job_id: str | None = None
    cohort_key: str | None = None
    metric_states: Dict[str, MetricState] | None = None
    metric_descriptors: Dict[str, MetricDescriptor] | None = None
    metric_sources: Dict[str, MetricSource | None] | None = None
    id: str
    name: str
    metrics: Dict[str, float]

class BatchAnalytics(BaseModel):
    scientific_cohorts: List[ScientificCohort] = []
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
        "binder_probability": [],
        "fampnn_psce": [],
        "maturation_interface_score": [],
        "maturation_rmsd": [],
        "maturation_delta_interface": [],
        "maturation_selected_interface_score": [],
        "maturation_selected_rmsd": [],
        "maturation_nonselected_rmsd": [],
        "ppiflow_objective_score": [],
        "ppiflow_primary_loop_rmsd": [],
        "frustration_high_count": [],
        "frustration_min_count": [],
        "frustration_pct_high": [],
    }
    
    for d in designs:
        capabilities = resolve_result_contract(
            review_profile_id=d.review_profile_id,
        ).viewer_capabilities
        has_interface = "complex_interface_metrics" in capabilities
        has_antibody = "antibody_backbone_metrics" in capabilities
        has_sequence = "sequence_design_metrics" in capabilities
        has_ppiflow = "ppiflow_maturation_metrics" in capabilities
        if d.plddt_overall is not None: metrics["plddt_overall"].append(d.plddt_overall)
        if has_interface and d.plddt_binder is not None: metrics["plddt_binder"].append(d.plddt_binder)
        if d.pae_overall is not None: metrics["pae_overall"].append(d.pae_overall)
        if has_interface and d.pae_interaction is not None: metrics["pae_interaction"].append(d.pae_interaction)
        if has_interface and d.rmsd_binder is not None: metrics["rmsd_binder"].append(d.rmsd_binder)
        if d.rmsd_overall is not None: metrics["rmsd_overall"].append(d.rmsd_overall)
        if has_sequence and d.mpnn_score is not None: metrics["mpnn_score"].append(d.mpnn_score)
        if d.conf_score is not None: metrics["conf_score"].append(d.conf_score)
        if d.ptm is not None: metrics["ptm"].append(d.ptm)
        if d.rog is not None: metrics["rog"].append(d.rog)
        if has_interface and d.ligand_iptm is not None: metrics["ligand_iptm"].append(d.ligand_iptm)
        if has_interface and d.affinity_score is not None: metrics["affinity_score"].append(d.affinity_score)
        if has_interface and d.binder_probability is not None: metrics["binder_probability"].append(d.binder_probability)
        if has_sequence and d.fampnn_psce is not None: metrics["fampnn_psce"].append(d.fampnn_psce)
        if has_ppiflow and d.maturation_interface_score is not None: metrics["maturation_interface_score"].append(d.maturation_interface_score)
        if has_ppiflow and d.maturation_rmsd is not None: metrics["maturation_rmsd"].append(d.maturation_rmsd)
        if has_ppiflow and d.maturation_delta_interface is not None: metrics["maturation_delta_interface"].append(d.maturation_delta_interface)
        if has_ppiflow and d.maturation_selected_interface_score is not None: metrics["maturation_selected_interface_score"].append(d.maturation_selected_interface_score)
        if has_ppiflow and d.maturation_selected_rmsd is not None: metrics["maturation_selected_rmsd"].append(d.maturation_selected_rmsd)
        if has_ppiflow and d.maturation_nonselected_rmsd is not None: metrics["maturation_nonselected_rmsd"].append(d.maturation_nonselected_rmsd)
        if has_ppiflow and d.ppiflow_objective_score is not None: metrics["ppiflow_objective_score"].append(d.ppiflow_objective_score)
        if has_ppiflow and d.ppiflow_primary_loop_rmsd is not None: metrics["ppiflow_primary_loop_rmsd"].append(d.ppiflow_primary_loop_rmsd)
        if d.frustration_high_count is not None: metrics["frustration_high_count"].append(float(d.frustration_high_count))
        if d.frustration_min_count is not None: metrics["frustration_min_count"].append(float(d.frustration_min_count))
        if d.frustration_pct_high is not None: metrics["frustration_pct_high"].append(d.frustration_pct_high)
            
    return metrics


async def _load_designs_for_job(
    session: AsyncSession,
    job_id: str,
    *,
    include_children: bool = True,
) -> List[Design]:
    """Load designs for a job, including child-job designs for parent lineage views."""
    job_result = await session.execute(select(Job).where(Job.id == job_id))
    job = job_result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    job_ids = [job_id]
    if include_children:
        child_result = await session.execute(select(Job.id).where(Job.parent_job_id == job_id))
        job_ids.extend(str(child_id) for child_id in child_result.scalars().all())

    result = await session.execute(select(Design).where(Design.job_id.in_(job_ids)))
    return result.scalars().all()


# --- Endpoints ---

@router.get("/job/{job_id}", response_model=JobAnalytics)
async def get_job_analytics(
    job_id: str,
    include_children: bool = Query(True, description="Include child-job designs for parent jobs"),
    session: AsyncSession = Depends(get_session)
):
    """Get aggregated analytics for a single job."""
    designs = await _load_designs_for_job(session, job_id, include_children=include_children)
    
    if not designs:
        return JobAnalytics(
            job_id=job_id,
            design_count=0,
            metrics=None,
            correlations=None,
            pipeline_summary={}
        )

    # Legacy meanings remain in the original fields; revision-one cohorts are separate.
    legacy, cohorts = await partition(designs, await owning_jobs(session, designs), session)
    raw_metrics = extract_metrics(legacy)
    
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
            for d in legacy if d.plddt_overall is not None and d.pae_overall is not None
        ]

    return JobAnalytics(
        job_id=job_id,
        design_count=len(designs),
        scientific_cohorts=cohorts,
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
    include_children: bool = Query(True, description="Include child-job designs for parent jobs"),
    session: AsyncSession = Depends(get_session)
):
    """Get raw metrics for all designs in a job (for custom client-side charting)."""
    designs = await _load_designs_for_job(session, job_id, include_children=include_children)
    
    owners = await owning_jobs(session, designs)
    return [
        DesignMetricPoint(id=d.id, name=d.name, **(await persisted_projection(d, session)))
        if revision_for_job(owners.get(d.job_id)) == 1 else DesignMetricPoint(
            id=d.id,
            name=d.name,
            metrics={
                "plddt_overall": d.plddt_overall or 0,
                "pae_overall": d.pae_overall or 0,
                "ptm": d.ptm or 0,
                **(
                    {
                        "rmsd_binder": d.rmsd_binder or 0,
                    }
                    if "complex_interface_metrics" in resolve_result_contract(
                        review_profile_id=d.review_profile_id,
                    ).viewer_capabilities
                    else {}
                ),
                **(
                    {"fampnn_psce": d.fampnn_psce or 0}
                    if "sequence_design_metrics" in resolve_result_contract(
                        review_profile_id=d.review_profile_id,
                    ).viewer_capabilities
                    else {}
                ),
                **(
                    {
                        "frustration_high_count": float(d.frustration_high_count),
                        "frustration_min_count": float(d.frustration_min_count),
                        "frustration_pct_high": d.frustration_pct_high,
                    }
                    if d.frustration_high_count is not None
                    and d.frustration_min_count is not None
                    and d.frustration_pct_high is not None
                    else {}
                ),
                **(
                    {
                        "maturation_interface_score": d.maturation_interface_score or 0,
                        "maturation_rmsd": d.maturation_rmsd or 0,
                        "maturation_delta_interface": d.maturation_delta_interface or 0,
                        "maturation_selected_interface_score": d.maturation_selected_interface_score or 0,
                        "maturation_selected_rmsd": d.maturation_selected_rmsd or 0,
                        "maturation_nonselected_rmsd": d.maturation_nonselected_rmsd or 0,
                        "ppiflow_objective_score": d.ppiflow_objective_score or 0,
                        "ppiflow_primary_loop_rmsd": d.ppiflow_primary_loop_rmsd or 0,
                    }
                    if "ppiflow_maturation_metrics" in resolve_result_contract(
                        review_profile_id=d.review_profile_id,
                    ).viewer_capabilities
                    else {}
                ),
            },
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
    cohorts = []
    
    # Simple loop for now - optimal would be single query with group_by
    for jid in job_ids:
        query = select(Design).where(Design.job_id == jid)
        result = await session.execute(query)
        designs = result.scalars().all()
        
        if not designs:
            continue
            
        legacy, new_cohorts = await partition(designs, await owning_jobs(session, designs), session)
        cohorts.extend(new_cohorts)
        metrics = extract_metrics(legacy)
        
        # Calculate averages for comparison
        for m_name, values in metrics.items():
            if values:
                if m_name not in summary:
                    summary[m_name] = {}
                summary[m_name][jid] = float(np.mean(values))
                
    return BatchAnalytics(
        job_ids=job_ids,
        scientific_cohorts=cohorts,
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
    scientific_cohorts: List[ScientificCohort] = []
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
    params = {
        "include_children": include_children,
        "design_ids": design_ids or [],
    }
    try:
        await validate_job_analysis_request(session, job, analysis_type, params)
        run, _definition, _params, _cache_key = await get_matching_job_analysis_run(
            session,
            job,
            analysis_type,
            raw_params=params,
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
