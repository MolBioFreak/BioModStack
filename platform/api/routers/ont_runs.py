"""ONT instrument-run and ONT/NGS analysis submission API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from schemas import JobCreate, JobResponse
from services import ont_run_control
from services.host_agent_client import HostAgentRequestError
from services.ont_ngs_contract import (
    get_ont_workflow_spec,
    normalize_ont_launch_params,
    resolve_ont_workflow_alias,
)

router = APIRouter()


class OntNgsSubmitRequest(BaseModel):
    """Request body for submitting an ONT/NGS analysis workflow."""

    name: str | None = Field(default=None, description="Optional job name. Defaults to the workflow display name.")
    params: dict[str, Any] = Field(default_factory=dict)
    pinned_gpu: int | None = Field(default=None)
    source_instrument_run_id: str | None = Field(default=None)


def _mode_for_ont_workflow(workflow_id: str) -> str:
    canonical = resolve_ont_workflow_alias(workflow_id)
    return canonical.removeprefix("ont_")


def _job_create_for_ont_submit(workflow_id: str, request: OntNgsSubmitRequest) -> JobCreate:
    canonical_id = resolve_ont_workflow_alias(workflow_id)
    spec = get_ont_workflow_spec(canonical_id)
    params = normalize_ont_launch_params(canonical_id, request.params)
    if request.source_instrument_run_id:
        params["source_instrument_run_id"] = request.source_instrument_run_id

    return JobCreate(
        name=(request.name or f"{spec.display_name} analysis").strip(),
        model_id="nanopore",
        mode=_mode_for_ont_workflow(canonical_id),
        params=params,
        pinned_gpu=request.pinned_gpu,
    )


async def _create_pipeline_job(
    job: JobCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession,
) -> JobResponse:
    # Import lazily so importing the ONT router does not import the entire jobs
    # router stack unless a launch request actually reaches this endpoint.
    from routers.jobs import create_job  # noqa: PLC0415

    return await create_job(job, background_tasks, session)


@router.get("/positions/{position}/protocol-options")
async def ont_position_protocol_options(
    position: str,
    kit: str | None = Query(default=None),
    basecalling_enabled: bool = Query(default=True),
) -> dict[str, Any]:
    """Return truthful preflight/protocol options for a live ONT position."""
    return ont_run_control.get_position_protocol_options(
        position,
        kit=kit,
        basecalling_enabled=basecalling_enabled,
    )


@router.post("/positions/{position}/hardware-check")
async def ont_begin_position_hardware_check(position: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Start a guarded MinKNOW hardware diagnostic check for a position."""
    try:
        return ont_run_control.begin_position_hardware_check(position, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HostAgentRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/positions/{position}/refresh")
async def ont_refresh_position_state(position: str) -> dict[str, Any]:
    """Re-read MinKNOW state for a position without power-cycling the instrument."""
    try:
        return ont_run_control.refresh_position_state(position)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown ONT position: {position}") from exc


@router.post("/positions/{position}/restart")
async def ont_restart_position(position: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Expose explicit restart contract; host-agent refuses until live semantics are validated."""
    try:
        return ont_run_control.restart_position(position, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/positions/{position}/start")
async def ont_start_instrument_run(position: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Start a real MinKNOW run through host-agent after explicit confirmation."""
    try:
        return ont_run_control.start_instrument_run(position, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs/{run_id}")
async def ont_get_instrument_run(run_id: str) -> dict[str, Any]:
    try:
        return ont_run_control.refresh_instrument_run_status(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown ONT instrument run: {run_id}") from exc


@router.post("/runs/{run_id}/handoff/plasmid-qc")
async def ont_handoff_plasmid_qc(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return ont_run_control.build_plasmid_qc_handoff(run_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown ONT instrument run: {run_id}") from exc


@router.post("/ngs/{workflow_id}/submit", response_model=JobResponse, status_code=201)
async def ont_submit_ngs_workflow(
    workflow_id: str,
    request: OntNgsSubmitRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    """Submit a canonical ONT/NGS Nextflow analysis job.

    This is the typed ONT product-family launch seam. It normalizes workflow
    aliases/defaults through the ONT registry, then delegates to the canonical
    job creation path so queueing, runtime policy, validation, and orchestrator
    behavior remain identical to other BioModStack jobs.
    """
    try:
        job = _job_create_for_ont_submit(workflow_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return await _create_pipeline_job(job, background_tasks, session)


@router.post("/runs/{run_id}/handoff/plasmid-qc/submit", response_model=JobResponse, status_code=201)
async def ont_submit_plasmid_qc_from_run(
    run_id: str,
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    """Build the instrument-run plasmid-QC handoff and submit the Nextflow job."""
    try:
        handoff = ont_run_control.build_plasmid_qc_handoff(run_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown ONT instrument run: {run_id}") from exc

    extra_params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    submit_request = OntNgsSubmitRequest(
        name=str(payload.get("name") or "ONT plasmid QC").strip(),
        params={**handoff["params"], **extra_params},
        pinned_gpu=payload.get("pinned_gpu"),
        source_instrument_run_id=run_id,
    )
    return await _create_pipeline_job(_job_create_for_ont_submit("ont_plasmid_qc", submit_request), background_tasks, session)


@router.post("/runs/{run_id}/stop")
async def ont_stop_instrument_run(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return ont_run_control.stop_instrument_run(run_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown ONT instrument run: {run_id}") from exc
