from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import ValidationError

from services.bioxp.errors import ConnectionStateError, RobotResponseError, RobotTransportError
from services.bioxp.protocols import BioXpProtocol, compile_protocol
from services.bioxp.protocol_models import (
    ProtocolSubmission, ProtocolControlRequest, ProtocolControlResponse,
    ProtocolReviewRequest, ProtocolJob, ProtocolJobs,
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
        return await runtime.connection.request_active(
            route,
            expected_generation=request.expected_connection_generation,
            require_fresh=True,
            json_data=request.model_dump(mode="json", exclude_unset=True, exclude={"expected_connection_generation"}),
            path_params={"job_id": job_id} if job_id is not None else None,
        )
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc


async def _query(runtime, route, expected_generation, *, job_id=None, limit=None):
    generation = runtime.connection.snapshot().generation if expected_generation is None else expected_generation
    try:
        kwargs = {}
        if job_id is not None:
            kwargs["path_params"] = {"job_id": job_id}
        if limit is not None:
            kwargs["params"] = {"limit": limit}
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


@router.post("/protocols/compile")
async def compile_bioxp_protocol(protocol: BioXpProtocol) -> dict[str, Any]:
    """Historical local step validation only; not robot executable compilation."""
    return compile_protocol(protocol).model_dump(mode="json")


@router.post("/protocols/submit")
async def submit_bioxp_protocol(
    submission: ProtocolSubmission,
    response: Response,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    job = _job(await _mutate(runtime, "protocol_execute", submission), live=not submission.dry_run)
    if not submission.dry_run:
        assert job.command is not None
        if job.command.idempotency_key != (submission.idempotency_key or "").strip():
            raise HTTPException(status_code=502, detail="BioXP robot returned a different submission identity")
        response.status_code = 200 if job.command.terminal else 202
    return job.model_dump(mode="json", exclude_unset=True)


@router.get("/protocols/jobs")
async def list_protocol_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    expected_connection_generation: int | None = Query(default=None, ge=0),
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    page = _validated(ProtocolJobs, await _query(runtime, "protocol_jobs", expected_connection_generation, limit=limit))
    return page.model_dump(mode="json", exclude_unset=True)


@router.get("/protocols/jobs/{job_id}")
async def get_protocol_job(
    job_id: str,
    expected_connection_generation: int | None = Query(default=None, ge=0),
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    job = _job(await _query(runtime, "protocol_job", expected_connection_generation, job_id=job_id), job_id=job_id)
    return job.model_dump(mode="json", exclude_unset=True)


@router.post("/protocols/jobs/{job_id}/control")
async def control_protocol_job(
    job_id: str,
    control: ProtocolControlRequest,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    receipt = _validated(ProtocolControlResponse, await _mutate(runtime, "protocol_control", control, job_id=job_id))
    if (receipt.job_id != job_id or receipt.command_id != job_id
            or receipt.idempotency_key != control.idempotency_key
            or receipt.ownership_generation != control.expected_ownership_generation):
        raise HTTPException(status_code=502, detail="BioXP robot returned a different control identity")
    return receipt.model_dump(mode="json", exclude_unset=True)


@router.post("/protocols/jobs/{job_id}/review")
async def review_protocol_job(
    job_id: str,
    review: ProtocolReviewRequest,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    job = _job(await _mutate(runtime, "protocol_review", review, job_id=job_id), job_id=job_id, live=True)
    return job.model_dump(mode="json", exclude_unset=True)
