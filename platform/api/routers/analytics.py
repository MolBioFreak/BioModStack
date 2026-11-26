"""
Analytics API router - Aggregated metrics and batch comparisons.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from sqlalchemy.orm import defer
from typing import List, Dict, Any, Optional
import hashlib
import json
from pydantic import BaseModel

from services.scientific_analytics import MetricState, MetricDescriptor, MetricSource, ScientificCohort, owning_jobs, projection, persisted_projection, revision_for_job, partition, native_scalar_owner
from database import get_session, Job, Design
from services.analysis_registry import (
    JOB_CORRELATION_MATRIX_ANALYSIS,
    JOB_METRIC_SUMMARY_ANALYSIS,
    SELECTION_BATCH_SUMMARY_ANALYSIS,
    SELECTION_METRIC_SUMMARY_ANALYSIS,
)
from services.analysis_runs import (
    extract_stats_run,
    get_latest_job_analysis_run_for_scope,
    get_matching_job_analysis_run,
    is_stats_toolkit_unavailable_run,
    load_analysis_result,
    request_selection_analysis,
    serialize_analysis_run,
    validate_job_analysis_request,
)
from services.stats_toolkit_client import STATS_TOOLKIT_UNAVAILABLE_REASON, StatsToolkitUnavailableError
from services.result_contracts import resolve_result_contract
from schemas import JobResponse

router = APIRouter()

# --- Schemas ---

class MetricDistribution(BaseModel):
    min: float
    max: float
    avg: float
    median: float
    std_dev: Optional[float] = None
    q1: Optional[float] = None
    q3: Optional[float] = None
    iqr: Optional[float] = None
    count: Optional[int] = None
    histogram_bins: List[float] = []
    histogram_counts: List[int] = []

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
    analysis_run_id: Optional[str] = None
    stats_run: Optional[Dict[str, Any]] = None
    stats_status: Optional[str] = None
    stale: bool = False
    reason: Optional[str] = None
    histogram_bins: Optional[int] = None
    shared_bins: Optional[bool] = None

class DesignMetricPoint(BaseModel):
    model_config = {"extra": "forbid"}
    contract_revision: int | None = None
    source_job_id: str | None = None
    cohort_key: str | None = None
    metric_states: Dict[str, MetricState] | None = None
    metric_descriptors: Dict[str, MetricDescriptor] | None = None
    metric_sources: Dict[str, MetricSource | None] | None = None
    publication_state: MetricState | None = None
    id: str
    name: str
    metrics: Dict[str, float]

class BatchAnalytics(BaseModel):
    scientific_cohorts: List[ScientificCohort] = []
    job_ids: List[str]
    metrics_summary: Dict[str, Dict[str, float]]  # metric -> {job_id -> avg}
    common_metrics: List[str]
    run_id: Optional[str] = None
    selection_signature: Optional[str] = None
    stats_run: Optional[Dict[str, Any]] = None
    stats_status: Optional[str] = None
    reason: Optional[str] = None

class SelectionSummaryRequest(BaseModel):
    design_ids: List[str] = []
    job_ids: List[str] = []
    selection_signature: Optional[str] = None
    histogram_bins: int = 10
    shared_bins: bool = False
    force_refresh: bool = False


# --- Helpers ---

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


def _scalar_design_query():
    """Avoid unrelated per-residue/matrix hydration for scalar endpoints.

    Native publication consumers still perform their own unchanged persisted
    reads and byte verification. These are not canonical metric projections.
    """
    return select(Design).options(
        defer(Design.pae_matrix, raiseload=True),
        defer(Design.stability_data, raiseload=True),
        defer(Design.residue_plddt, raiseload=True),
        defer(Design.chain_metrics, raiseload=True),
    )


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

    result = await session.execute(_scalar_design_query().where(Design.job_id.in_(job_ids)))
    return result.scalars().all()


# --- Endpoints ---

@router.get("/job/{job_id}", response_model=JobAnalytics)
async def get_job_analytics(
    job_id: str,
    include_children: bool = Query(True, description="Include child-job designs for parent jobs"),
    session: AsyncSession = Depends(get_session)
):
    """Get aggregated analytics for a single job from its stored durable Toolkit run (R11)."""
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    designs = await _load_designs_for_job(session, job_id, include_children=include_children)

    if not designs:
        return JobAnalytics(
            job_id=job_id,
            design_count=0,
            metrics=None,
            correlations=None,
            pipeline_summary={},
            stats_status="not_computed",
        )

    from services.scientific_analytics import assemble_cohort_from_toolkit, partition_rows
    owners = await owning_jobs(session, designs)
    legacy, grouped_cohorts = await partition_rows(designs, owners, session)

    # Raw scatter coordinates (not statistical aggregation)
    correlations: Dict[str, List[ScatterPoint]] = {}
    plddt_pae = [
        ScatterPoint(x=d.plddt_overall, y=d.pae_overall, id=d.id)
        for d in legacy if d.plddt_overall is not None and d.pae_overall is not None
    ]
    if plddt_pae:
        correlations["plddt_vs_pae"] = plddt_pae

    pipeline_summary = {
        "total_designs": len(designs),
        "favorites": sum(1 for d in designs if d.is_favorite),
    }

    run, _definition, _params, _cache_key, is_stale = await get_latest_job_analysis_run_for_scope(
        session,
        job,
        JOB_METRIC_SUMMARY_ANALYSIS,
        raw_params={"include_children": include_children},
    )
    if run is None:
        uncomputed_cohorts = [
            assemble_cohort_from_toolkit(
                key, rows, summary_columns={}, include_pairs=True, unavailable_reason="not_computed"
            )
            for key, rows in sorted(grouped_cohorts.items())
        ]
        return JobAnalytics(
            job_id=job_id,
            design_count=len(designs),
            scientific_cohorts=uncomputed_cohorts,
            metrics=None,
            correlations=correlations,
            pipeline_summary=pipeline_summary,
            stats_status="not_computed",
            stale=False,
        )

    stats_run = extract_stats_run(run)
    run_params = run.params_json or {}
    if is_stats_toolkit_unavailable_run(run):
        unavailable_cohorts = [
            assemble_cohort_from_toolkit(
                key, rows, summary_columns={}, include_pairs=True, unavailable_reason=STATS_TOOLKIT_UNAVAILABLE_REASON
            )
            for key, rows in sorted(grouped_cohorts.items())
        ]
        return JobAnalytics(
            job_id=job_id,
            design_count=len(designs),
            scientific_cohorts=unavailable_cohorts,
            metrics=None,
            correlations=correlations,
            pipeline_summary=pipeline_summary,
            analysis_run_id=run.id,
            stats_run=stats_run,
            stats_status="unavailable",
            stale=False,
            reason=STATS_TOOLKIT_UNAVAILABLE_REASON,
            histogram_bins=run_params.get("histogram_bins"),
            shared_bins=run_params.get("shared_bins"),
        )

    if run.status != "completed":
        pending_cohorts = [
            assemble_cohort_from_toolkit(
                key, rows, summary_columns={}, include_pairs=True, unavailable_reason=run.status
            )
            for key, rows in sorted(grouped_cohorts.items())
        ]
        return JobAnalytics(
            job_id=job_id,
            design_count=len(designs),
            scientific_cohorts=pending_cohorts,
            metrics=None,
            correlations=correlations,
            pipeline_summary=pipeline_summary,
            analysis_run_id=run.id,
            stats_run=stats_run,
            stats_status=run.status,
            stale=False,
            histogram_bins=run_params.get("histogram_bins"),
            shared_bins=run_params.get("shared_bins"),
        )

    try:
        payload = load_analysis_result(run)
    except StatsToolkitUnavailableError:
        unavailable_cohorts = [
            assemble_cohort_from_toolkit(
                key, rows, summary_columns={}, include_pairs=True, unavailable_reason=STATS_TOOLKIT_UNAVAILABLE_REASON
            )
            for key, rows in sorted(grouped_cohorts.items())
        ]
        return JobAnalytics(
            job_id=job_id,
            design_count=len(designs),
            scientific_cohorts=unavailable_cohorts,
            metrics=None,
            correlations=correlations,
            pipeline_summary=pipeline_summary,
            analysis_run_id=run.id,
            stats_run=stats_run,
            stats_status="unavailable",
            stale=bool(is_stale),
            reason=STATS_TOOLKIT_UNAVAILABLE_REASON,
            histogram_bins=run_params.get("histogram_bins"),
            shared_bins=run_params.get("shared_bins"),
        )

    payload_dict = payload if isinstance(payload, dict) else {}
    analyzed_metrics = {
        name: MetricDistribution.model_validate(dist)
        for name, dist in (payload_dict.get("metrics") or {}).items()
        if isinstance(dist, dict)
    }
    cohorts = [
        ScientificCohort.model_validate(item)
        for item in (payload_dict.get("scientific_cohorts") or [])
        if isinstance(item, dict)
    ]
    return JobAnalytics(
        job_id=job_id,
        design_count=len(designs),
        scientific_cohorts=cohorts,
        metrics=analyzed_metrics,
        correlations=correlations,
        pipeline_summary=pipeline_summary,
        analysis_run_id=run.id,
        stats_run=stats_run,
        stats_status="completed",
        stale=bool(is_stale),
        histogram_bins=run_params.get("histogram_bins"),
        shared_bins=run_params.get("shared_bins"),
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
    from services.scientific_analytics import bulk_scalar_projections
    projections = await bulk_scalar_projections(designs, owners, session)
    return [
        DesignMetricPoint(id=d.id, name=d.name, **projections[d.id])
        if native_scalar_owner(owners.get(d.job_id)) else DesignMetricPoint(
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
    """Compare metrics across multiple jobs via a saved durable selection run (R13/C15)."""
    from services.analysis_subprocess import execute_analysis_run_in_session

    normalized_job_ids = sorted({str(jid).strip() for jid in job_ids if str(jid).strip()})
    if not normalized_job_ids:
        return BatchAnalytics(
            job_ids=[],
            scientific_cohorts=[],
            metrics_summary={},
            common_metrics=[],
            stats_status="not_computed",
        )

    design_rows = list(
        (await session.execute(
            select(Design.id, Design.job_id)
            .where(Design.job_id.in_(normalized_job_ids))
            .order_by(Design.job_id.asc(), Design.id.asc())
        )).all()
    )
    selection_signature = hashlib.sha256(
        json.dumps(
            {
                "kind": "batch",
                "job_ids": normalized_job_ids,
                "designs": [[str(r[0]), str(r[1])] for r in design_rows],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    run, _cache_hit = await request_selection_analysis(
        session,
        selection_signature,
        SELECTION_BATCH_SUMMARY_ANALYSIS,
        raw_params={"job_ids": normalized_job_ids},
        requested_by="ui",
    )
    if run.status in {"queued", "failed"}:
        await execute_analysis_run_in_session(session, run.id)
        await session.refresh(run)

    stats_run = extract_stats_run(run)
    if is_stats_toolkit_unavailable_run(run):
        return BatchAnalytics(
            job_ids=normalized_job_ids,
            scientific_cohorts=[],
            metrics_summary={},
            common_metrics=[],
            run_id=run.id,
            selection_signature=selection_signature,
            stats_run=stats_run,
            stats_status="unavailable",
            reason=STATS_TOOLKIT_UNAVAILABLE_REASON,
        )
    if run.status != "completed":
        return BatchAnalytics(
            job_ids=normalized_job_ids,
            scientific_cohorts=[],
            metrics_summary={},
            common_metrics=[],
            run_id=run.id,
            selection_signature=selection_signature,
            stats_run=stats_run,
            stats_status=run.status,
        )
    try:
        payload = load_analysis_result(run)
    except StatsToolkitUnavailableError:
        return BatchAnalytics(
            job_ids=normalized_job_ids,
            scientific_cohorts=[],
            metrics_summary={},
            common_metrics=[],
            run_id=run.id,
            selection_signature=selection_signature,
            stats_run=stats_run,
            stats_status="unavailable",
            reason=STATS_TOOLKIT_UNAVAILABLE_REASON,
        )
    payload_dict = payload if isinstance(payload, dict) else {}
    cohorts = [
        ScientificCohort.model_validate(item)
        for item in (payload_dict.get("scientific_cohorts") or [])
        if isinstance(item, dict)
    ]
    metrics_summary = payload_dict.get("metrics_summary") or {}
    return BatchAnalytics(
        job_ids=normalized_job_ids,
        scientific_cohorts=cohorts,
        metrics_summary=metrics_summary,
        common_metrics=list(payload_dict.get("common_metrics") or sorted(metrics_summary.keys())),
        run_id=run.id,
        selection_signature=selection_signature,
        stats_run=stats_run,
        stats_status="completed",
    )


@router.post("/selection-summary")
async def request_selection_metric_summary(
    request: SelectionSummaryRequest,
    session: AsyncSession = Depends(get_session),
):
    """Compute or reuse a saved durable descriptive summary for a design selection (R13)."""
    from services.analysis_subprocess import execute_analysis_run_in_session

    design_ids = sorted({str(d).strip() for d in request.design_ids if str(d).strip()})
    job_ids = sorted({str(j).strip() for j in request.job_ids if str(j).strip()})
    selection_signature = request.selection_signature or hashlib.sha256(
        json.dumps(
            {"design_ids": design_ids, "job_ids": job_ids},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    run, cache_hit = await request_selection_analysis(
        session,
        selection_signature,
        SELECTION_METRIC_SUMMARY_ANALYSIS,
        raw_params={
            "design_ids": design_ids,
            "job_ids": job_ids,
            "histogram_bins": request.histogram_bins,
            "shared_bins": request.shared_bins,
        },
        force_refresh=request.force_refresh,
        requested_by="ui",
    )
    if run.status in {"queued", "failed"}:
        await execute_analysis_run_in_session(session, run.id)
        await session.refresh(run)
        cache_hit = False
    return serialize_analysis_run(
        run,
        analysis_type=run.analysis_type,
        subject_kind="selection",
        subject_id=selection_signature,
        params=run.params_json or {},
        cache_hit=cache_hit,
        include_result=True,
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
    matrix: List[List[float]]  # NxN correlation values
    sample_sizes: List[List[int]]  # NxN sample counts
    p_value_matrix: Optional[List[List[Optional[float]]]] = None
    adjusted_p_value_matrix: Optional[List[List[Optional[float]]]] = None
    pairs: Optional[List[Dict[str, Any]]] = None
    method: Optional[str] = None
    p_adjust_method: Optional[str] = None
    stats_backend: Optional[str] = None
    stats_tool_id: Optional[str] = None
    stats_method_version: Optional[str] = None
    analysis_run_id: Optional[str] = None
    stats_run: Optional[Dict[str, Any]] = None
    stale: bool = False

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


STANDARD_AAs = "ACDEFGHIKLMNPQRSTVWY"


async def _get_cached_job_analysis_payload(
    session: AsyncSession,
    job: Job,
    analysis_type: str,
    *,
    include_children: bool = True,
    design_ids: Optional[list[str]] = None,
    extra_params: Optional[dict[str, Any]] = None,
    explicit_option_keys: frozenset[str] = frozenset(),
) -> Any:
    params: dict[str, Any] = {
        "include_children": include_children,
        "design_ids": design_ids or [],
    }
    if extra_params:
        params.update(extra_params)
    try:
        await validate_job_analysis_request(session, job, analysis_type, params)
        run, _definition, _params, _cache_key, is_stale = await get_latest_job_analysis_run_for_scope(
            session,
            job,
            analysis_type,
            raw_params=params,
            explicit_option_keys=explicit_option_keys,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if run is None:
        raise HTTPException(status_code=404, detail=f"{analysis_type} not computed yet")
    if is_stats_toolkit_unavailable_run(run):
        raise HTTPException(status_code=503, detail="stats_toolkit_unavailable")
    if run.status != "completed":
        raise HTTPException(status_code=409, detail=f"{analysis_type} status is {run.status}")

    try:
        payload = load_analysis_result(run)
    except StatsToolkitUnavailableError:
        raise HTTPException(status_code=503, detail=STATS_TOOLKIT_UNAVAILABLE_REASON)
    if payload is None:
        raise HTTPException(status_code=500, detail=f"Cached {analysis_type} payload is unavailable")
    if isinstance(payload, dict) and _definition.toolkit_backed:
        enriched = dict(payload)
        enriched["analysis_run_id"] = run.id
        stats_run = extract_stats_run(run)
        if stats_run is not None:
            enriched.setdefault("stats_run", stats_run)
        enriched["stale"] = is_stale
        return enriched
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
