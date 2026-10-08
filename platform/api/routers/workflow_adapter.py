from __future__ import annotations

from contextlib import asynccontextmanager
import subprocess
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
import sqlalchemy

import database
from biomodstack_services import ServiceManagerError, start_runtime_target
from services import nextflow


cancel_nextflow_job = nextflow.cancel_nextflow_job


router = APIRouter(prefix="/workflow-adapter", tags=["workflow-adapter"])


class WorkflowAdapterLaunchRequest(BaseModel):
    job_id: str
    model_id: str
    mode: str
    params: dict[str, Any] = Field(default_factory=dict)
    output_dir: str


class WorkflowAdapterLaunchResponse(BaseModel):
    accepted: bool
    job_id: str
    nextflow_run_id: str
    launch_mode: str


class WorkflowAdapterCancelRequest(BaseModel):
    nextflow_run_id: str


class WorkflowAdapterCancelResponse(BaseModel):
    cancelled: bool
    resolved_nextflow_run_id: str


class WorkflowAdapterRunningJobsResponse(BaseModel):
    running_jobs: dict[str, int]


class WorkflowAdapterRuntimeStartTargetRequest(BaseModel):
    target: str | None = None


LOCAL_ADAPTER_HOSTS = {None, "127.0.0.1", "::1", "localhost", "testclient"}


def _require_local_adapter_request(request: Request) -> None:
    # Core-runtime uses host networking for bms-api, so API-to-adapter calls land
    # on the host adapter as loopback. Do not widen this to Docker bridge/LAN
    # clients; the host adapter owns systemd/runtime controls.
    if request.client and request.client.host not in LOCAL_ADAPTER_HOSTS:
        raise HTTPException(status_code=403, detail="BioModStack workflow-adapter control routes are local-only")


@router.get("/health")
async def workflow_adapter_health() -> dict[str, str]:
    return {"status": "healthy", "service": "biomodstack-workflow-adapter"}


@router.post("/runtime/start-target")
async def workflow_adapter_start_runtime_target(
    request: Request,
    payload: WorkflowAdapterRuntimeStartTargetRequest | None = None,
    target: str | None = None,
) -> dict[str, str]:
    _require_local_adapter_request(request)
    normalized_target = str(target or (payload.target if payload else None) or "prod").strip().lower()
    try:
        start_runtime_target(target=normalized_target, skip_api_wait=True, skip_workflow_adapter_wait=True)
    except (ServiceManagerError, FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"target": normalized_target, "control_mode": "host-adapter"}


@router.post("/launch", response_model=WorkflowAdapterLaunchResponse, status_code=202)
async def workflow_adapter_launch(request: WorkflowAdapterLaunchRequest) -> WorkflowAdapterLaunchResponse:
    nextflow.launch_nextflow_job_detached(
        job_id=request.job_id,
        model_id=request.model_id,
        mode=request.mode,
        params=request.params,
        output_dir=request.output_dir,
        allow_running_job=True,
    )
    return WorkflowAdapterLaunchResponse(
        accepted=True,
        job_id=request.job_id,
        nextflow_run_id=request.job_id,
        launch_mode="native-host",
    )


async def _resolve_native_run_id(job_handle: str) -> str:
    async with database.async_session() as session:
        query = sqlalchemy.select(database.Job).where(
            sqlalchemy.or_(
                database.Job.id == job_handle,
                database.Job.nextflow_run_id == job_handle,
            )
        )
        result = await session.execute(query)
        job = result.scalar_one_or_none()

    resolved_run_id = getattr(job, "nextflow_run_id", None)
    if resolved_run_id is None:
        return str(job_handle)
    return str(resolved_run_id)


@router.post("/cancel", response_model=WorkflowAdapterCancelResponse)
async def workflow_adapter_cancel(request: WorkflowAdapterCancelRequest) -> WorkflowAdapterCancelResponse:
    resolved_nextflow_run_id = await _resolve_native_run_id(request.nextflow_run_id)
    cancelled = await cancel_nextflow_job(resolved_nextflow_run_id)
    return WorkflowAdapterCancelResponse(
        cancelled=bool(cancelled),
        resolved_nextflow_run_id=resolved_nextflow_run_id,
    )


@router.get("/running-jobs", response_model=WorkflowAdapterRunningJobsResponse)
async def workflow_adapter_running_jobs() -> WorkflowAdapterRunningJobsResponse:
    return WorkflowAdapterRunningJobsResponse(running_jobs=nextflow.get_running_jobs())
