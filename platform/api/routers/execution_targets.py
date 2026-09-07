"""Operator API for attaching an already-running execution target."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from services.remote_execution.contracts import (
    ExecutionTargetActivateRequest,
    ExecutionTargetInventoryResponse,
    ExecutionTargetResponse,
)
from services.remote_execution.targets import (
    ExecutionTargetError,
    active_remote_telemetry,
    deactivate_target,
    list_targets,
    refresh_vast_targets,
)

router = APIRouter()


@router.get("", response_model=list[ExecutionTargetResponse])
async def execution_targets(session: AsyncSession = Depends(get_session)):
    try:
        return await list_targets(session)
    except ExecutionTargetError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/providers/vast/refresh",
    response_model=ExecutionTargetInventoryResponse,
)
async def refresh_vast_inventory(session: AsyncSession = Depends(get_session)):
    try:
        return await refresh_vast_targets(session)
    except ExecutionTargetError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/activate", response_model=ExecutionTargetResponse, status_code=202)
async def activate_execution_target(
    request: ExecutionTargetActivateRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
):
    try:
        controller = getattr(http_request.app.state, "attachment_controller", None)
        if controller is None:
            raise HTTPException(status_code=503, detail="Attachment service is unavailable")
        return await controller.attach(session, request)
    except ExecutionTargetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{execution_target_id}/deactivate", response_model=ExecutionTargetResponse)
async def deactivate_execution_target(
    execution_target_id: str,
    session: AsyncSession = Depends(get_session),
):
    try:
        return await deactivate_target(session, execution_target_id)
    except ExecutionTargetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/active/telemetry")
async def execution_target_telemetry(session: AsyncSession = Depends(get_session), since: str | None = None):
    return await active_remote_telemetry(session, since)
