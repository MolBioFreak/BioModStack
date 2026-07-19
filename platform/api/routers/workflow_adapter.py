from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import subprocess
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
import sqlalchemy

import database
from biomodstack_services import (
    CONTAINER_RUNTIME_MODE,
    CORE_RUNTIME_SERVICE,
    WORKFLOW_ADAPTER_SERVICE,
    ServiceManagerError,
    resolve_runtime_mode,
    restart_all,
    restart_api,
    run_core_runtime_script,
    runtime_descriptor,
    start_all,
    start_api,
    start_runtime_target,
    stop_all,
    stop_api,
)
from services import nextflow
from services.md.feature_gate import require_molecular_dynamics_feature


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


class WorkflowAdapterRuntimeActionRequest(BaseModel):
    runtime: str | None = None


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
        await asyncio.to_thread(
            start_runtime_target,
            target=normalized_target,
            skip_api_wait=True,
            skip_workflow_adapter_wait=True,
        )
    except (ServiceManagerError, FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"target": normalized_target, "control_mode": "host-adapter"}


def _run_delayed(action) -> None:
    def runner() -> None:
        time.sleep(0.75)
        action()

    threading.Thread(target=runner, daemon=True).start()


def _mark_current_adapter_ready(payload: dict[str, object]) -> dict[str, object]:
    """Normalize status emitted by the adapter process that is serving this route."""
    if not isinstance(payload, dict):
        return payload
    normalized = dict(payload)
    health_raw = normalized.get("health")
    if isinstance(health_raw, dict):
        health = dict(health_raw)
        if "adapter_ready" in health:
            health["adapter_ready"] = True
        normalized["health"] = health
    else:
        health = {}

    services_raw = normalized.get("services")
    if isinstance(services_raw, list):
        services: list[object] = []
        for service in services_raw:
            if not isinstance(service, dict):
                services.append(service)
                continue
            item = dict(service)
            if item.get("name") == WORKFLOW_ADAPTER_SERVICE:
                item["active"] = True
                item["active_source"] = "current-adapter-process"
            services.append(item)
        normalized["services"] = services
        if services and all(isinstance(service, dict) for service in services):
            runtime_ready = all(bool(value) for value in health.values()) if health else bool(normalized.get("runtime_ready"))
            normalized["runtime_ready"] = runtime_ready
            normalized["runtime_active"] = all(bool(service.get("active")) for service in services if isinstance(service, dict)) and runtime_ready
    return normalized


def _run_container_api_web_action(action_name: str) -> dict[str, object]:
    if action_name == "stop":
        _run_delayed(lambda: run_core_runtime_script("stop", "bms-web", "bms-api"))
    elif action_name == "restart":
        _run_delayed(lambda: run_core_runtime_script("restart", "bms-api", "bms-web"))
    elif action_name == "stop-api":
        _run_delayed(lambda: run_core_runtime_script("stop", "bms-api"))
    elif action_name == "restart-api":
        _run_delayed(lambda: run_core_runtime_script("restart", "bms-api"))
    else:
        raise KeyError(action_name)
    return {
        "accepted": True,
        "background": True,
        "runtime_mode": CONTAINER_RUNTIME_MODE,
        "action": action_name,
        "control_mode": "host-adapter",
        "note": "Accepted by host adapter; API/web container action runs after this response so the caller is not killed mid-request.",
    }


@router.get("/runtime/state")
async def workflow_adapter_runtime_state(
    request: Request,
    runtime: str | None = None,
) -> dict[str, object]:
    _require_local_adapter_request(request)
    runtime_mode = resolve_runtime_mode(runtime)
    try:
        descriptor = await asyncio.to_thread(runtime_descriptor, runtime_mode=runtime_mode)
        payload = _mark_current_adapter_ready(descriptor)
    except (ServiceManagerError, FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    payload["control_mode"] = "host-adapter"
    return payload


@router.post("/runtime/{action_name}")
async def workflow_adapter_runtime_action(
    request: Request,
    action_name: str,
    payload: WorkflowAdapterRuntimeActionRequest | None = None,
    runtime: str | None = None,
) -> dict[str, object]:
    _require_local_adapter_request(request)
    normalized_action = str(action_name or "").strip().lower()
    runtime_mode = resolve_runtime_mode(runtime or (payload.runtime if payload else None))
    actions = {
        "start": start_all,
        "start-api": start_api,
        "stop": stop_all,
        "stop-api": stop_api,
        "restart": restart_all,
        "restart-api": restart_api,
    }
    if normalized_action not in actions:
        raise HTTPException(status_code=400, detail=f"unsupported runtime action: {action_name}")

    if runtime_mode == CONTAINER_RUNTIME_MODE and normalized_action in {"stop", "restart", "stop-api", "restart-api"}:
        return _run_container_api_web_action(normalized_action)

    try:
        await asyncio.to_thread(actions[normalized_action], runtime_mode=runtime_mode)
        payload_out = await asyncio.to_thread(runtime_descriptor, runtime_mode=runtime_mode)
    except (ServiceManagerError, FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    payload_out["control_mode"] = "host-adapter"
    payload_out["action"] = normalized_action
    return payload_out


@router.post("/launch", response_model=WorkflowAdapterLaunchResponse, status_code=202)
async def workflow_adapter_launch(request: WorkflowAdapterLaunchRequest) -> WorkflowAdapterLaunchResponse:
    require_molecular_dynamics_feature(request.model_id)
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
