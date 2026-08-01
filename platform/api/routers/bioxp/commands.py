from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from services.bioxp.command_coordinator import (
    CommandBusyError,
    CommandDeniedError,
    IdempotencyConflictError,
)
from services.bioxp.command_models import parse_command_request
from services.bioxp.errors import ConnectionStateError, TargetPolicyError
from services.bioxp.operator_semantic_quarantine import EMERGENCY_STOP_QUARANTINE_REASON
from services.bioxp.runtime import BioXpRuntime

from .dependencies import get_bioxp_runtime, require_bioxp_mutation_access

router = APIRouter(dependencies=[Depends(require_bioxp_mutation_access)])


class EmergencyStopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_generation: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


@router.post("/commands")
async def execute_command(
    payload: dict[str, Any] = Body(...),
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    try:
        request = parse_command_request(payload)
        result = await runtime.commands.execute(
            request,
            mutations_enabled=True,
        )
        if (
            result.remote_acknowledged
            and result.status != "queued"
            and request.command != "stop_axis_diagnostic"
        ):
            try:
                await runtime.connection.probe_status_only()
            except (ConnectionStateError, TargetPolicyError):
                pass
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (CommandBusyError, CommandDeniedError, IdempotencyConflictError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except ConnectionStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result.model_dump(mode="json")


@router.get("/commands")
async def command_history(runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> dict[str, Any]:
    return {"commands": [entry.model_dump(mode="json") for entry in runtime.commands.history()]}


@router.get("/commands/{command_id}")
async def command_status(
    command_id: str,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    result = runtime.commands.get(command_id)
    if result is None:
        raise HTTPException(status_code=404, detail="BioXP command not found")
    return result.model_dump(mode="json")


@router.post("/emergency-stop")
async def emergency_stop(
    request: EmergencyStopRequest,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    del request, runtime
    raise HTTPException(status_code=409, detail=EMERGENCY_STOP_QUARANTINE_REASON)
