from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any, Literal
from pathlib import Path
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
import sqlalchemy

import database
from biomodstack_services import (
    CONTAINER_RUNTIME_MODE,
    CORE_RUNTIME_SERVICE,
    DEV_RUNTIME_MODE,
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
    workflow_adapter_service_for_lane,
)
from biomodstack_tailnet import (
    TailnetEnvironmentError,
    current_tailnet_environment,
    ensure_global_tailnet_routes,
    select_tailnet_environment,
)
from mobile_apk_auth import require_tailnet_environment_tailscale_identity
from services import nextflow
from services.md.feature_gate import require_molecular_dynamics_feature
from services.execution_ownership import (
    AdapterIdentity,
    DuplicateUnitError,
    ExecutionOwnershipError,
    LaneIdentityError,
    LaneMismatchError,
    adapter_identity_from_environment,
    append_execution_attempt,
    acquire_workflow_claim_lock,
    assert_unit_lane,
    deterministic_unit_name,
    execution_attempt_is_terminal,
    latest_execution_attempt,
    next_execution_identity,
    params_mapping,
    planned_execution_attempt,
    request_fingerprint,
    release_workflow_claim_lock,
    show_unit_properties,
    is_legacy_numeric_run_id,
    unit_has_empty_cgroup,
    update_execution_attempt,
    utc_timestamp,
    wait_for_unit_invocation,
    build_systemd_run_command,
    create_systemd_workflow_unit,
    TRANSIENT_WORKFLOW_OWNER_NONCE_ENV,
    TRANSIENT_WORKFLOW_UNIT_ENV,
    TRANSIENT_WORKFLOW_UNIT_NAME_ENV,
)


cancel_nextflow_job = nextflow.cancel_nextflow_job


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/workflow-adapter", tags=["workflow-adapter"])


class WorkflowAdapterLaunchRequest(BaseModel):
    job_id: str
    model_id: str
    mode: str
    params: dict[str, Any] = Field(default_factory=dict)
    output_dir: str
    lane: str | None = None


class WorkflowAdapterLaunchResponse(BaseModel):
    accepted: bool
    job_id: str
    nextflow_run_id: str
    launch_mode: str


class WorkflowAdapterCancelRequest(BaseModel):
    nextflow_run_id: str
    graceful_timeout_seconds: float = Field(default=5.0, ge=0.0, le=300.0)


class WorkflowAdapterCancelResponse(BaseModel):
    cancelled: bool
    resolved_nextflow_run_id: str


class WorkflowAdapterRunningJobsResponse(BaseModel):
    running_jobs: dict[str, int | str]


class WorkflowAdapterRuntimeStartTargetRequest(BaseModel):
    target: str | None = None


class WorkflowAdapterRuntimeActionRequest(BaseModel):
    runtime: str | None = None


class WorkflowAdapterTailnetEnvironmentRequest(BaseModel):
    environment: Literal["development", "production"]


LOCAL_ADAPTER_HOSTS = {None, "127.0.0.1", "::1", "localhost", "testclient"}
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}
API_ROOT = Path(__file__).resolve().parents[1]
_CLAIM_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


class _AsyncWorkflowClaimLock:
    def __init__(self, process_lock: asyncio.Lock, state_dir: Path, lane: str, job_id: str) -> None:
        self.process_lock = process_lock
        self.state_dir = state_dir
        self.lane = lane
        self.job_id = job_id
        self.file_handle = None

    async def __aenter__(self):
        await self.process_lock.acquire()
        try:
            self.file_handle = await asyncio.to_thread(
                acquire_workflow_claim_lock,
                self.state_dir,
                self.lane,
                self.job_id,
            )
        except Exception:
            self.process_lock.release()
            raise
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.file_handle is not None:
            await asyncio.to_thread(release_workflow_claim_lock, self.file_handle)
        self.process_lock.release()


def _claim_lock(lane: str, job_id: str, state_dir: Path) -> _AsyncWorkflowClaimLock:
    key = (str(lane), str(job_id))
    lock = _CLAIM_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _CLAIM_LOCKS[key] = lock
    return _AsyncWorkflowClaimLock(lock, state_dir, lane, job_id)


def _require_adapter_identity(requested_lane: str | None = None) -> AdapterIdentity:
    try:
        identity = adapter_identity_from_environment()
        if requested_lane is not None and str(requested_lane).strip().lower() != identity.lane:
            raise LaneMismatchError(
                f"Request lane {requested_lane!r} does not match adapter lane {identity.lane!r}"
            )
        return identity
    except (LaneIdentityError, LaneMismatchError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _require_local_adapter_request(request: Request) -> None:
    # Core-runtime uses host networking for bms-api, so API-to-adapter calls land
    # on the host adapter as loopback. Do not widen this to Docker bridge/LAN
    # clients; the host adapter owns systemd/runtime controls.
    if request.client and request.client.host not in LOCAL_ADAPTER_HOSTS:
        raise HTTPException(status_code=403, detail="BioModStack workflow-adapter control routes are local-only")


def _runtime_mode_for_adapter(identity: AdapterIdentity, requested: str | None) -> str:
    expected = DEV_RUNTIME_MODE if identity.lane == "development" else CONTAINER_RUNTIME_MODE
    if requested is None:
        return expected
    try:
        resolved = resolve_runtime_mode(requested)
    except ServiceManagerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if resolved != expected:
        raise HTTPException(
            status_code=409,
            detail=f"Runtime mode {resolved!r} is not owned by adapter lane {identity.lane!r}",
        )
    return resolved


@router.get("/health")
async def workflow_adapter_health() -> dict[str, str]:
    identity = _require_adapter_identity()
    return {
        "status": "healthy",
        "service": f"biomodstack-{identity.lane}-workflow-adapter",
        "lane": identity.lane,
        "state_dir": str(identity.state_dir),
        "db_path": str(identity.db_path),
        "work_dir": str(identity.work_dir),
        "results_root": str(identity.results_root),
    }


@router.post("/runtime/start-target")
async def workflow_adapter_start_runtime_target(
    request: Request,
    payload: WorkflowAdapterRuntimeStartTargetRequest | None = None,
    target: str | None = None,
) -> dict[str, str]:
    _require_local_adapter_request(request)
    identity = _require_adapter_identity()
    normalized_target = str(target or (payload.target if payload else None) or "prod").strip().lower()
    expected_targets = {"dev"} if identity.lane == "development" else {"prod", "production", "stable", "container"}
    if normalized_target not in expected_targets:
        raise HTTPException(
            status_code=409,
            detail=f"Runtime target {normalized_target!r} is not owned by adapter lane {identity.lane!r}",
        )
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


def _mark_current_adapter_ready(payload: dict[str, object], *, lane: str | None = None) -> dict[str, object]:
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

    current_service = (
        workflow_adapter_service_for_lane(lane)
        if lane is not None
        else WORKFLOW_ADAPTER_SERVICE
    )
    services_raw = normalized.get("services")
    if isinstance(services_raw, list):
        services: list[object] = []
        for service in services_raw:
            if not isinstance(service, dict):
                services.append(service)
                continue
            item = dict(service)
            if item.get("name") == current_service:
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


def _launch_request_fingerprint(payload: WorkflowAdapterLaunchRequest, lane: str) -> str:
    return request_fingerprint(
        {
            "job_id": payload.job_id,
            "model_id": payload.model_id,
            "mode": payload.mode,
            "params": payload.params,
            "output_dir": payload.output_dir,
            "lane": lane,
        }
    )


def _runner_environment(*, identity: AdapterIdentity, unit_name: str, owner_nonce: str) -> dict[str, object]:
    """Pass the lane authority into systemd without passing request authority."""
    environment: dict[str, object] = {
        key: value
        for key, value in os.environ.items()
        if key.startswith("BMS_")
        or key in {"PATH", "HOME", "JAVA_HOME", "NXF_HOME", "CUDA_VISIBLE_DEVICES"}
    }
    environment.update(
        {
            "BMS_HOME": str(API_ROOT.parent.parent),
            "BMS_WORKFLOW_ADAPTER_URL": "",
            "BMS_CORE_RUNTIME_MODE": "0",
            "BMS_WORKFLOW_ADAPTER_LANE": identity.lane,
            "BMS_STATE_DIR": str(identity.state_dir),
            "BMS_DB_PATH": str(identity.db_path),
            "BMS_WORK": str(identity.work_dir),
            "BMS_RESULTS_DIR": str(identity.results_root),
            "BMS_RESULTS_ROOT": str(identity.results_root),
            TRANSIENT_WORKFLOW_UNIT_ENV: "1",
            TRANSIENT_WORKFLOW_UNIT_NAME_ENV: unit_name,
            TRANSIENT_WORKFLOW_OWNER_NONCE_ENV: owner_nonce,
        }
    )
    return environment


def _runner_command(job_id: str, lane: str) -> list[str]:
    return [
        sys.executable,
        str(API_ROOT / "workflow_job_runner.py"),
        "--job-id",
        str(job_id),
        "--lane",
        str(lane),
    ]


def _attempt_identity(receipt: dict[str, Any]) -> tuple[str, int, int, str, str]:
    try:
        return (
            str(receipt["lane"]),
            int(receipt["generation"]),
            int(receipt["attempt"]),
            str(receipt["unit"]),
            str(receipt["owner_nonce"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ExecutionOwnershipError(f"Malformed execution-attempt receipt: {receipt!r}") from exc


async def _publish_interrupted_owner(
    session,
    job,
    receipt: dict[str, Any],
    *,
    reason: str,
) -> None:
    lane, generation, attempt, unit_name, owner_nonce = _attempt_identity(receipt)
    params = update_execution_attempt(
        job.params,
        lane=lane,
        generation=generation,
        attempt=attempt,
        unit=unit_name,
        owner_nonce=owner_nonce,
        changes={
            "state": "interrupted_owner",
            "terminal_at": utc_timestamp(),
            "terminal_reason": "INTERRUPTED_OWNER",
            "detail": str(reason)[:2000],
        },
    )
    job.params = params
    if str(getattr(job, "status", "")).lower() not in TERMINAL_JOB_STATUSES:
        job.status = "failed"
        job.queue_status = "failed"
        job.error_message = f"INTERRUPTED_OWNER: {str(reason)[:1800]}"
        job.completed_at = datetime.utcnow()


async def _publish_owner_conflict(
    session,
    job,
    receipt: dict[str, Any],
    *,
    reason: str,
) -> None:
    """Persist a nonterminal conflict while an unmatched unit remains active."""

    lane, generation, attempt, unit_name, owner_nonce = _attempt_identity(receipt)
    job.params = update_execution_attempt(
        job.params,
        lane=lane,
        generation=generation,
        attempt=attempt,
        unit=unit_name,
        owner_nonce=owner_nonce,
        changes={
            "state": "ownership_conflict",
            "conflict_detected_at": utc_timestamp(),
            "detail": str(reason)[:2000],
        },
    )
    job.queue_status = "ownership_conflict"
    job.error_message = f"OWNERSHIP_CONFLICT: {str(reason)[:1800]}"
    job.completed_at = None


async def reconcile_workflow_adapter_startup() -> dict[str, int]:
    """Reconcile only this adapter lane; never relaunch stale ownership."""
    identity = _require_adapter_identity()
    counts = {"active": 0, "interrupted": 0, "critical": 0}
    async with database.async_session() as session:
        result = await session.execute(sqlalchemy.select(database.Job))
        jobs = list(result.scalars().all())
        for job in jobs:
            if str(getattr(job, "status", "")).lower() in TERMINAL_JOB_STATUSES:
                continue
            receipt = latest_execution_attempt(getattr(job, "params", {}))
            if receipt is None or str(receipt.get("lane", "")) != identity.lane:
                continue
            if execution_attempt_is_terminal(receipt):
                continue

            unit_name = str(receipt.get("unit") or "")
            try:
                properties = await asyncio.to_thread(show_unit_properties, unit_name, identity.lane)
            except ExecutionOwnershipError as exc:
                logger.critical(
                    "Workflow owner missing for nonterminal job %s: %s",
                    job.id,
                    exc,
                )
                counts["critical"] += 1
                await _publish_interrupted_owner(session, job, receipt, reason=str(exc))
                counts["interrupted"] += 1
                continue

            active = properties.active_state in {"active", "activating", "reloading"}
            if active:
                expected_invocation = str(receipt.get("invocation_id") or "")
                actual_invocation = str(properties.invocation_id or "")
                if not expected_invocation or not actual_invocation or expected_invocation != actual_invocation:
                    logger.critical(
                        "Workflow owner InvocationID mismatch for job %s unit %s: receipt=%r systemd=%r; retaining active unit",
                        job.id,
                        unit_name,
                        expected_invocation,
                        actual_invocation,
                    )
                    counts["critical"] += 1
                    await _publish_owner_conflict(
                        session,
                        job,
                        receipt,
                        reason=(
                            "InvocationID mismatch; active unit retained "
                            f"(receipt={expected_invocation!r}, systemd={actual_invocation!r})"
                        ),
                    )
                else:
                    counts["active"] += 1
                continue

            if not unit_has_empty_cgroup(properties):
                logger.critical(
                    "Workflow unit %s is inactive but its cgroup is not empty; retaining unit and DB state for safety",
                    unit_name,
                )
                counts["critical"] += 1
                continue

            logger.critical(
                "Workflow owner unit %s is inactive or failed for nonterminal job %s; no relaunch will be attempted",
                unit_name,
                job.id,
            )
            counts["critical"] += 1
            await _publish_interrupted_owner(
                session,
                job,
                receipt,
                reason=f"owner unit {unit_name} is {properties.active_state}/{properties.sub_state}",
            )
            counts["interrupted"] += 1
        await session.commit()
    return counts


@router.get("/runtime/state")
async def workflow_adapter_runtime_state(
    request: Request,
    runtime: str | None = None,
) -> dict[str, object]:
    _require_local_adapter_request(request)
    identity = _require_adapter_identity()
    runtime_mode = _runtime_mode_for_adapter(identity, runtime)
    try:
        descriptor = await asyncio.to_thread(runtime_descriptor, runtime_mode=runtime_mode)
        payload = _mark_current_adapter_ready(descriptor, lane=identity.lane)
    except (ServiceManagerError, FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    payload["control_mode"] = "host-adapter"
    return payload


@router.get(
    "/tailnet-environment/status",
    dependencies=[Depends(require_tailnet_environment_tailscale_identity)],
)
async def workflow_adapter_tailnet_environment_status(request: Request) -> dict[str, object]:
    _require_local_adapter_request(request)
    _require_adapter_identity()
    try:
        return await asyncio.to_thread(current_tailnet_environment)
    except (ServiceManagerError, FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/tailnet-environment/select",
    dependencies=[Depends(require_tailnet_environment_tailscale_identity)],
)
async def workflow_adapter_select_tailnet_environment(
    request: Request,
    payload: WorkflowAdapterTailnetEnvironmentRequest,
) -> dict[str, object]:
    _require_local_adapter_request(request)
    # The authenticated Tailnet selector is hosted by the Development control
    # adapter, but its explicit selection payload may target either supported
    # environment. This is a Tailnet routing transaction, not a workflow launch
    # or runtime action, so it must not be confused with adapter-lane ownership.
    _require_adapter_identity()
    try:
        await asyncio.to_thread(ensure_global_tailnet_routes)
        return await asyncio.to_thread(select_tailnet_environment, payload.environment)
    except TailnetEnvironmentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ServiceManagerError, FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/runtime/{action_name}")
async def workflow_adapter_runtime_action(
    request: Request,
    action_name: str,
    payload: WorkflowAdapterRuntimeActionRequest | None = None,
    runtime: str | None = None,
) -> dict[str, object]:
    _require_local_adapter_request(request)
    identity = _require_adapter_identity()
    normalized_action = str(action_name or "").strip().lower()
    runtime_mode = _runtime_mode_for_adapter(
        identity,
        runtime or (payload.runtime if payload else None),
    )
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
async def workflow_adapter_launch(
    payload: WorkflowAdapterLaunchRequest,
    request: Request,
) -> WorkflowAdapterLaunchResponse:
    _require_local_adapter_request(request)
    identity = _require_adapter_identity(payload.lane)
    require_molecular_dynamics_feature(payload.model_id)

    fingerprint = _launch_request_fingerprint(payload, identity.lane)
    unit_name: str | None = None
    owner_nonce: str | None = None
    generation: int | None = None
    attempt: int | None = None

    # The deterministic systemd unit is the execution claim.  The lane/job
    # lock also spans receipt planning through the systemd acceptance update,
    # so another adapter process cannot observe a short planned interval and
    # race a second spawn; systemd remains the final atomic claim authority.
    async with _claim_lock(identity.lane, payload.job_id, identity.state_dir):
        async with database.async_session() as session:
            try:
                await session.execute(sqlalchemy.text("BEGIN IMMEDIATE"))
            except Exception:
                # PostgreSQL and test session doubles may already have an
                # implicit transaction.  The unit claim below remains atomic.
                logger.debug("Could not explicitly begin the workflow claim transaction", exc_info=True)
            result = await session.execute(
                sqlalchemy.select(database.Job).where(database.Job.id == payload.job_id)
            )
            job = result.scalar_one_or_none()
            if job is None:
                raise HTTPException(status_code=404, detail=f"Job {payload.job_id} not found")

            job_model_id = getattr(job, "model_id", None)
            job_mode = getattr(job, "mode", None)
            if job_model_id is not None and str(job_model_id) != payload.model_id:
                raise HTTPException(
                    status_code=409,
                    detail=f"Launch model {payload.model_id!r} does not match authoritative job model {job_model_id!r}",
                )
            if job_mode is not None and str(job_mode) != payload.mode:
                raise HTTPException(
                    status_code=409,
                    detail=f"Launch mode {payload.mode!r} does not match authoritative job mode {job_mode!r}",
                )
            authoritative_output = getattr(job, "output_dir", None)
            if authoritative_output and str(authoritative_output) != str(payload.output_dir):
                raise HTTPException(
                    status_code=409,
                    detail="Launch output_dir does not match the authoritative lane-local Job.output_dir",
                )

            status = str(getattr(job, "status", "queued") or "queued").lower()
            if status in TERMINAL_JOB_STATUSES:
                raise HTTPException(
                    status_code=409,
                    detail=f"Job {payload.job_id} is already terminal with status {status}",
                )

            params = params_mapping(getattr(job, "params", {}))
            latest = latest_execution_attempt(params)
            if latest is not None and not execution_attempt_is_terminal(latest):
                latest_lane = str(latest.get("lane", ""))
                if latest_lane != identity.lane:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Job {payload.job_id} is owned by workflow lane {latest_lane!r}; "
                            f"adapter lane is {identity.lane!r}"
                        ),
                    )
                if str(latest.get("request_fingerprint", "")) != fingerprint:
                    raise HTTPException(
                        status_code=409,
                        detail="A live workflow unit already owns this job with a different request fingerprint",
                    )
                unit_name = str(latest.get("unit") or "")
                try:
                    assert_unit_lane(unit_name, identity.lane)
                    properties = await asyncio.to_thread(
                        show_unit_properties,
                        unit_name,
                        identity.lane,
                    )
                except ExecutionOwnershipError as exc:
                    await _publish_interrupted_owner(
                        session,
                        job,
                        latest,
                        reason=str(exc),
                    )
                    await session.commit()
                    raise HTTPException(
                        status_code=409,
                        detail=f"Stale workflow ownership for {payload.job_id}; no relaunch was attempted",
                    ) from exc
                if properties.active_state in {"active", "activating", "reloading"}:
                    expected_invocation = str(latest.get("invocation_id") or "")
                    actual_invocation = str(properties.invocation_id or "")
                    if not expected_invocation or expected_invocation != actual_invocation:
                        logger.critical(
                            "Duplicate launch found InvocationID mismatch for active unit %s: receipt=%r systemd=%r",
                            unit_name,
                            expected_invocation,
                            actual_invocation,
                        )
                        raise HTTPException(
                            status_code=409,
                            detail="Active workflow unit InvocationID mismatch; unit was retained",
                        )
                    return WorkflowAdapterLaunchResponse(
                        accepted=True,
                        job_id=payload.job_id,
                        nextflow_run_id=unit_name,
                        launch_mode="transient-systemd",
                    )
                if not unit_has_empty_cgroup(properties):
                    raise HTTPException(
                        status_code=409,
                        detail="Workflow unit is inactive but its cgroup is not empty; no relaunch was attempted",
                    )
                await _publish_interrupted_owner(
                    session,
                    job,
                    latest,
                    reason=f"owner unit {unit_name} is {properties.active_state}/{properties.sub_state}",
                )
                await session.commit()
                raise HTTPException(
                    status_code=409,
                    detail=f"Stale workflow ownership for {payload.job_id}; no relaunch was attempted",
                )

            generation, attempt = next_execution_identity(params, identity.lane)
            owner_nonce = uuid.uuid4().hex
            unit_name = deterministic_unit_name(identity.lane, payload.job_id, attempt)
            receipt = planned_execution_attempt(
                lane=identity.lane,
                job_id=payload.job_id,
                generation=generation,
                attempt=attempt,
                unit=unit_name,
                owner_nonce=owner_nonce,
                request_fingerprint_value=fingerprint,
            )
            job.params = append_execution_attempt(params, receipt)
            # Publish the deterministic unit identity with the planned receipt
            # so an API cancellation racing systemd acceptance can address the
            # exact unit.  InvocationID and started state are still withheld
            # until systemd has accepted and identified the invocation.
            job.nextflow_run_id = unit_name
            # Commit the durable planned receipt before systemd is invoked. The
            # in-process claim lock prevents a second adapter coroutine from
            # treating this short planned interval as a new attempt.
            await session.commit()

        assert unit_name is not None
        assert generation is not None
        assert attempt is not None
        assert owner_nonce is not None
        command = build_systemd_run_command(
            lane=identity.lane,
            job_id=payload.job_id,
            attempt=attempt,
            command=_runner_command(payload.job_id, identity.lane),
            environment=_runner_environment(
                identity=identity,
                unit_name=unit_name,
                owner_nonce=owner_nonce,
            ),
            working_directory=API_ROOT,
        )
        try:
            accepted_unit = await asyncio.to_thread(
                create_systemd_workflow_unit,
                command,
            )
            if accepted_unit != unit_name:
                raise ExecutionOwnershipError(
                    f"systemd accepted unexpected workflow unit {accepted_unit!r}; expected {unit_name!r}"
                )
            properties = await asyncio.to_thread(
                wait_for_unit_invocation,
                unit_name,
                identity.lane,
            )
        except DuplicateUnitError as exc:
            logger.critical(
                "Deterministic workflow unit claim collided without a reusable receipt: %s",
                exc,
            )
            raise HTTPException(
                status_code=409,
                detail="Deterministic workflow unit already exists; launch was rejected",
            ) from exc
        except ExecutionOwnershipError as exc:
            logger.exception("Workflow unit claim failed after receipt planning for %s", payload.job_id)
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        invocation_id = str(properties.invocation_id or "")
        if not invocation_id:
            raise HTTPException(status_code=503, detail="systemd accepted the unit without an InvocationID")

        async with database.async_session() as session:
            try:
                await session.execute(sqlalchemy.text("BEGIN IMMEDIATE"))
            except Exception:
                logger.debug("Could not explicitly begin the workflow start transaction", exc_info=True)
            result = await session.execute(
                sqlalchemy.select(database.Job).where(database.Job.id == payload.job_id)
            )
            job = result.scalar_one_or_none()
            if job is None:
                raise HTTPException(status_code=404, detail=f"Job {payload.job_id} disappeared during launch")
            current_params = params_mapping(getattr(job, "params", {}))
            current_params = update_execution_attempt(
                current_params,
                lane=identity.lane,
                generation=generation,
                attempt=attempt,
                unit=unit_name,
                owner_nonce=owner_nonce,
                changes={
                    "state": "started",
                    "invocation_id": invocation_id,
                    "started_at": utc_timestamp(),
                },
            )
            job.params = current_params
            job.nextflow_run_id = unit_name
            if str(getattr(job, "status", "queued") or "queued").lower() not in TERMINAL_JOB_STATUSES:
                job.status = "running"
                job.queue_status = "running"
                if getattr(job, "started_at", None) is None:
                    job.started_at = datetime.utcnow()
            await session.commit()

    return WorkflowAdapterLaunchResponse(
        accepted=True,
        job_id=payload.job_id,
        nextflow_run_id=unit_name,
        launch_mode="transient-systemd",
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
    identity = _require_adapter_identity()
    resolved_nextflow_run_id = await _resolve_native_run_id(request.nextflow_run_id)
    if not is_legacy_numeric_run_id(resolved_nextflow_run_id):
        try:
            assert_unit_lane(resolved_nextflow_run_id, identity.lane)
        except LaneMismatchError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    cancelled = await cancel_nextflow_job(
        resolved_nextflow_run_id,
        graceful_timeout_seconds=request.graceful_timeout_seconds,
    )
    return WorkflowAdapterCancelResponse(
        cancelled=bool(cancelled),
        resolved_nextflow_run_id=resolved_nextflow_run_id,
    )


@router.get("/running-jobs", response_model=WorkflowAdapterRunningJobsResponse)
async def workflow_adapter_running_jobs() -> WorkflowAdapterRunningJobsResponse:
    _require_adapter_identity()
    return WorkflowAdapterRunningJobsResponse(running_jobs=nextflow.get_running_jobs())
