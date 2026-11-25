from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.params import Param
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Design, Job, get_session
from services.analysis_registry import (
    build_analysis_input_signature,
    get_analysis_definition,
)
from services.analysis_runs import (
    validate_owned_design_analysis_request,
    get_analysis_run_by_id,
    get_latest_job_analysis_run_for_scope,
    get_matching_design_analysis_run,
    get_matching_job_analysis_run,
    request_design_analysis,
    request_job_analysis,
    serialize_analysis_run,
    validate_job_analysis_request,
)


router = APIRouter()


class AnalysisRunRequest(BaseModel):
    params: Dict[str, Any] = Field(default_factory=dict)
    force_refresh: bool = False


class AnalysisRunResponse(BaseModel):
    run_id: Optional[str] = None
    analysis_type: str
    subject_kind: str
    subject_id: str
    status: str
    resource_class: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    cache_hit: bool = False
    summary: Optional[Dict[str, Any]] = None
    result: Optional[Any] = None
    stats_run: Optional[Dict[str, Any]] = None
    stale: bool = False
    supersedes_run_id: Optional[str] = None
    reason: Optional[str] = None
    error_message: Optional[str] = None
    artifacts: Dict[str, Any] = Field(default_factory=dict)
    queued_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_accessed_at: Optional[datetime] = None


def _unwrap_param(value: Any) -> Any:
    if isinstance(value, Param):
        return value.default
    return value


def _normalize_design_ids_query(raw: Optional[str]) -> list[str]:
    raw = _unwrap_param(raw)
    if not raw:
        return []
    return sorted({part.strip() for part in str(raw).split(",") if part.strip()})


def _design_analysis_params_from_query(analysis_type: str, max_size: Optional[int], chain_id: str | None = None, ignore_cbeta: bool | None = None) -> dict[str, Any]:
    normalized = str(analysis_type or "").strip().lower()
    if normalized == "fampnn_psce_profile":
        return {key: value for key, value in {"chain_id": chain_id, "ignore_cbeta": ignore_cbeta}.items() if value is not None}
    if normalized in {"contact_map", "pae_matrix"}:
        return {"max_size": max_size if max_size is not None else (300 if normalized == "contact_map" else 200)}
    return {}


def _job_analysis_params_from_query(
    *,
    include_children: bool,
    design_ids: Optional[str],
    method: Optional[str] = None,
    p_adjust_method: Optional[str] = None,
    histogram_bins: Optional[int] = None,
    shared_bins: Optional[bool] = None,
) -> tuple[dict[str, Any], frozenset[str]]:
    include_children = bool(_unwrap_param(include_children))
    method = _unwrap_param(method)
    p_adjust_method = _unwrap_param(p_adjust_method)
    histogram_bins = _unwrap_param(histogram_bins)
    shared_bins = _unwrap_param(shared_bins)
    params: dict[str, Any] = {
        "include_children": include_children,
        "design_ids": _normalize_design_ids_query(design_ids),
    }
    explicit: set[str] = set()
    if method is not None:
        params["method"] = method
        explicit.add("method")
    if p_adjust_method is not None:
        params["p_adjust_method"] = p_adjust_method
        explicit.add("p_adjust_method")
    if histogram_bins is not None:
        params["histogram_bins"] = histogram_bins
        explicit.add("histogram_bins")
    if shared_bins is not None:
        params["shared_bins"] = shared_bins
        explicit.add("shared_bins")
    return params, frozenset(explicit)


async def _enforce_design_analysis_contract(design: Design, analysis_type: str, session: AsyncSession) -> None:
    reason = await validate_owned_design_analysis_request(design, analysis_type, session)
    if reason:
        raise HTTPException(status_code=409, detail=reason)


@router.get("/designs/{design_id}/analyses/{analysis_type}", response_model=AnalysisRunResponse)
async def get_design_analysis(
    design_id: str,
    analysis_type: str,
    max_size: Optional[int] = Query(None, description="Matrix dimension cap for contact-map / PAE analyses"),
    session: AsyncSession = Depends(get_session),
    chain_id: Optional[str] = None,
    ignore_cbeta: Optional[bool] = None,
    include_result: bool = True,
):
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    if design is None:
        raise HTTPException(status_code=404, detail="Design not found")
    await _enforce_design_analysis_contract(design, analysis_type, session)

    params = _design_analysis_params_from_query(analysis_type, max_size, chain_id, ignore_cbeta)
    try:
        run, definition, normalized_params, _cache_key = await get_matching_design_analysis_run(
            session,
            design,
            analysis_type,
            raw_params=params,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return AnalysisRunResponse.model_validate(
        serialize_analysis_run(
            run,
            analysis_type=definition.analysis_type,
            subject_kind="design",
            subject_id=design.id,
            params=normalized_params,
            cache_hit=run is not None and run.status == "completed",
            include_result=include_result,
        )
    )


@router.post("/designs/{design_id}/analyses/{analysis_type}", response_model=AnalysisRunResponse)
async def trigger_design_analysis(
    design_id: str,
    analysis_type: str,
    request: AnalysisRunRequest,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    if design is None:
        raise HTTPException(status_code=404, detail="Design not found")
    await _enforce_design_analysis_contract(design, analysis_type, session)

    try:
        run, cache_hit = await request_design_analysis(
            session,
            design,
            analysis_type,
            raw_params=request.params,
            force_refresh=request.force_refresh,
            requested_by="ui",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return AnalysisRunResponse.model_validate(
        serialize_analysis_run(
            run,
            analysis_type=run.analysis_type,
            subject_kind="design",
            subject_id=design.id,
            params=run.params_json or {},
            cache_hit=cache_hit,
            include_result=True,
        )
    )


@router.get("/jobs/{job_id}/analyses/{analysis_type}", response_model=AnalysisRunResponse)
async def get_job_analysis(
    job_id: str,
    analysis_type: str,
    include_children: bool = Query(True, description="Include child-job designs in the analysis scope"),
    design_ids: Optional[str] = Query(None, description="Comma-separated design ids to restrict the analysis scope"),
    method: Optional[str] = Query(None, description="Optional correlation method filter"),
    p_adjust_method: Optional[str] = Query(None, description="Optional p-value adjustment method filter"),
    histogram_bins: Optional[int] = Query(None, description="Optional histogram bin count filter"),
    shared_bins: Optional[bool] = Query(None, description="Optional shared histogram edges filter"),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    params, explicit_option_keys = _job_analysis_params_from_query(
        include_children=include_children,
        design_ids=design_ids,
        method=method,
        p_adjust_method=p_adjust_method,
        histogram_bins=histogram_bins,
        shared_bins=shared_bins,
    )
    try:
        await validate_job_analysis_request(session, job, analysis_type, params)
        run, definition, normalized_params, _cache_key, is_stale = await get_latest_job_analysis_run_for_scope(
            session,
            job,
            analysis_type,
            raw_params=params,
            explicit_option_keys=explicit_option_keys,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return AnalysisRunResponse.model_validate(
        serialize_analysis_run(
            run,
            analysis_type=definition.analysis_type,
            subject_kind="job",
            subject_id=job.id,
            params=normalized_params,
            cache_hit=run is not None and run.status == "completed" and not is_stale,
            include_result=True,
            stale=is_stale,
        )
    )


@router.post("/jobs/{job_id}/analyses/{analysis_type}", response_model=AnalysisRunResponse)
async def trigger_job_analysis(
    job_id: str,
    analysis_type: str,
    request: AnalysisRunRequest,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        run, cache_hit = await request_job_analysis(
            session,
            job,
            analysis_type,
            raw_params=request.params,
            force_refresh=request.force_refresh,
            requested_by="ui",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return AnalysisRunResponse.model_validate(
        serialize_analysis_run(
            run,
            analysis_type=run.analysis_type,
            subject_kind="job",
            subject_id=job.id,
            params=run.params_json or {},
            cache_hit=cache_hit,
            include_result=True,
        )
    )


@router.get("/analyses/{run_id}", response_model=AnalysisRunResponse)
async def get_analysis_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),
):
    run = await get_analysis_run_by_id(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Analysis run not found")

    is_stale = False
    authority_error: Optional[str] = None
    if run.subject_kind == "design":
        subject_result = await session.execute(select(Design).where(Design.id == run.subject_id))
        design = subject_result.scalar_one_or_none()
        if design is None:
            raise HTTPException(status_code=404, detail="Analysis subject not found")
        authority_error = await validate_owned_design_analysis_request(design, run.analysis_type, session)
    elif run.subject_kind == "job":
        subject_result = await session.execute(select(Job).where(Job.id == run.subject_id))
        job = subject_result.scalar_one_or_none()
        if job is None:
            raise HTTPException(status_code=404, detail="Analysis subject not found")
        try:
            authority_error = await validate_job_analysis_request(
                session,
                job,
                run.analysis_type,
                run.params_json or {},
            )
        except ValueError as exc:
            authority_error = str(exc)
        if not authority_error and run.status == "completed":
            definition = get_analysis_definition(run.analysis_type)
            if definition is not None and definition.toolkit_backed:
                normalized = definition.normalize_params(run.params_json or {})
                current_sig = await build_analysis_input_signature(definition, job, normalized, session)
                is_stale = run.input_signature != current_sig
    elif run.subject_kind == "selection":
        definition = get_analysis_definition(run.analysis_type)
        if definition is None or definition.subject_kind != "selection":
            authority_error = f"Unknown selection analysis type: {run.analysis_type}"
        else:
            authority_error = None
    elif run.subject_kind != "design":
        authority_error = "unsupported analysis subject kind"
    if authority_error:
        raise HTTPException(status_code=409, detail=authority_error)

    return AnalysisRunResponse.model_validate(
        serialize_analysis_run(
            run,
            analysis_type=run.analysis_type,
            subject_kind=run.subject_kind,
            subject_id=run.subject_id,
            params=run.params_json or {},
            cache_hit=run.status == "completed" and not is_stale,
            include_result=True,
            stale=is_stale,
        )
    )
