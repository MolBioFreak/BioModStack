from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from services.bioxp.job_store import JobConflictError
from services.bioxp.protocols import BioXpProtocol, compile_protocol
from services.bioxp.runtime import BioXpRuntime

from .dependencies import get_bioxp_runtime

router = APIRouter()


class ProtocolSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: BioXpProtocol
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


@router.post("/protocols/compile")
async def compile_bioxp_protocol(protocol: BioXpProtocol) -> dict[str, Any]:
    """Validate and canonicalize locally; never contact or claim compatibility with a robot."""

    return compile_protocol(protocol).model_dump(mode="json")


@router.post("/protocols/submit", status_code=status.HTTP_202_ACCEPTED)
async def submit_bioxp_protocol(
    submission: ProtocolSubmission,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    compiled = compile_protocol(submission.protocol)
    try:
        job = runtime.jobs.create_validated_job(
            protocol=submission.protocol.model_dump(mode="json"),
            compiled_hash=compiled.compiled_hash,
            idempotency_key=submission.idempotency_key,
        )
    except JobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if job.state == "validated_offline":
        job = runtime.jobs.transition(
            job.job_id,
            "submission_blocked",
            detail="Normal OEM command mappings are not verified; no robot delivery was attempted",
        )
    return {
        "job": job.model_dump(mode="json"),
        "delivery_attempted": False,
        "robot_compatible": None,
    }
