from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
import sqlalchemy

import database
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


@router.get("/health")
async def workflow_adapter_health() -> dict[str, str]:
    return {"status": "healthy", "service": "biomodstack-workflow-adapter"}


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
