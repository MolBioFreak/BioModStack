from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import ValidationError

from services.bioxp.errors import ConnectionStateError, RobotResponseError, RobotTransportError
from services.bioxp.protocol_models import (
    ProtocolSubmission, ProtocolControlRequest, ProtocolControlResponse,
    ProtocolReviewRequest, ProtocolJob, ProtocolJobs, ProtocolJobObservation,
)
from services.bioxp.runtime import BioXpRuntime

from .dependencies import get_bioxp_runtime, require_bioxp_mutation_access
from .operator_controls import _translate_robot_error

router = APIRouter(dependencies=[Depends(require_bioxp_mutation_access)])


def _validated(model, payload):
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=502, detail="BioXP robot returned an invalid protocol contract") from exc


async def _mutate(runtime, route, request, *, job_id=None):
    if job_id is not None and request.command_id != job_id:
        raise HTTPException(status_code=422, detail="Control command_id must match the addressed job")
    try:
        # Retain the original client across I/O without sharing the unrelated
        # v1 workflow lane. Admission and physical ordering belong to the robot.
        async with runtime.connection.active_request_lease(
            expected_generation=request.expected_connection_generation,
            require_fresh=False,
        ) as client:
            kwargs = {"json_data": request.model_dump(
                mode="json", exclude_unset=True, exclude={"expected_connection_generation"})}
            if job_id is not None:
                kwargs["path_params"] = {"job_id": job_id}
            return await client.request(route, **kwargs)
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc


async def _query(runtime, route, expected_generation, *, job_id=None, limit=None, observation=False):
    generation = runtime.connection.generation if expected_generation is None else expected_generation
    try:
        kwargs = {}
        if job_id is not None:
            kwargs["path_params"] = {"job_id": job_id}
        if limit is not None:
            kwargs["params"] = {"limit": limit, "summary": True}
        elif observation:
            kwargs["params"] = {"observation": True}
        return await runtime.connection.request_active_v2_query(
            route, expected_generation=generation, **kwargs,
        )
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc


def _job(payload, *, job_id=None, live=False):
    job = _validated(ProtocolJob, payload)
    if (job_id is not None and job.job_id != job_id) or (live and job.command is None):
        raise HTTPException(status_code=502, detail="BioXP robot returned a different or noncanonical job")
    return job


@router.post("/protocols/submit", response_model=ProtocolJob, response_model_exclude_unset=True)
async def submit_bioxp_protocol(
    submission: ProtocolSubmission,
    response: Response,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> ProtocolJob:
    job = _job(await _mutate(runtime, "protocol_execute", submission), live=not submission.dry_run)
    if not submission.dry_run:
        assert job.command is not None
        if job.command.idempotency_key != (submission.idempotency_key or "").strip():
            raise HTTPException(status_code=502, detail="BioXP robot returned a different submission identity")
        response.status_code = 200 if job.command.terminal else 202
    return job


@router.get("/protocols/jobs", response_model=ProtocolJobs, response_model_exclude_unset=True)
async def list_protocol_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    expected_connection_generation: int | None = Query(default=None, ge=0),
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> ProtocolJobs:
    page = _validated(ProtocolJobs, await _query(runtime, "protocol_jobs", expected_connection_generation, limit=limit))
    return page


@router.get("/protocols/jobs/{job_id}", response_model=ProtocolJob | ProtocolJobObservation, response_model_exclude_unset=True)
async def get_protocol_job(
    job_id: str,
    expected_connection_generation: int | None = Query(default=None, ge=0),
    observation: bool = Query(default=False),
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> ProtocolJob | ProtocolJobObservation:
    payload = await _query(runtime, "protocol_job", expected_connection_generation, job_id=job_id, observation=observation)
    if observation and payload.get("schema_version") == "bioxp.protocol_job_observation.v1":
        job = _validated(ProtocolJobObservation, payload)
        if job.job_id != job_id:
            raise HTTPException(status_code=502, detail="BioXP robot returned a different job observation")
        return job
    return _job(payload, job_id=job_id)


@router.post("/protocols/jobs/{job_id}/control", response_model=ProtocolControlResponse, response_model_exclude_unset=True)
async def control_protocol_job(
    job_id: str,
    control: ProtocolControlRequest,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> ProtocolControlResponse:
    receipt = _validated(ProtocolControlResponse, await _mutate(runtime, "protocol_control", control, job_id=job_id))
    if (receipt.job_id != job_id or receipt.command_id != job_id
            or receipt.idempotency_key != control.idempotency_key
            or receipt.ownership_generation != control.expected_ownership_generation):
        raise HTTPException(status_code=502, detail="BioXP robot returned a different control identity")
    return receipt


@router.post("/protocols/jobs/{job_id}/review", response_model=ProtocolJob, response_model_exclude_unset=True)
async def review_protocol_job(
    job_id: str,
    review: ProtocolReviewRequest,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> ProtocolJob:
    job = _job(await _mutate(runtime, "protocol_review", review, job_id=job_id), job_id=job_id, live=True)
    return job
