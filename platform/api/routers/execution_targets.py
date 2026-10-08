"""Operator API for attaching an already-running execution target."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from services.remote_execution.contracts import (
    ExecutionTargetActivateRequest,
    PreloadRequest, ProvisionRequest, ProvisionSelection, ProvisionPreview, ObservedArtifactInventory,
    WorkflowProvisionSelection, WorkflowProvisionRequest,
    ExecutionTargetInventoryResponse,
    ExecutionTargetResponse,
)
from services.remote_execution.targets import (
    ExecutionTargetError,
    active_remote_telemetry,
    deactivate_target,
    list_targets, get_target, observed_artifact_inventory,
    refresh_vast_targets,
)

from services.remote_execution.managed_inventory import ManagedInventory, project_inventory

router = APIRouter()


@router.get('/{execution_target_id}/runtime-inventory', response_model=ManagedInventory | None)
async def runtime_inventory(execution_target_id: str, session: AsyncSession = Depends(get_session)):
    try:
        return project_inventory(await get_target(session, execution_target_id))
    except ExecutionTargetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post('/{execution_target_id}/runtime-inventory/refresh', response_model=ManagedInventory)
async def refresh_runtime_inventory(execution_target_id: str, http_request: Request,
                                    session: AsyncSession = Depends(get_session)):
    controller = getattr(http_request.app.state, 'preload_controller', None)
    if controller is None:
        raise HTTPException(status_code=503, detail='Preload service is unavailable')
    try:
        return await controller.refresh_inventory(session, execution_target_id)
    except ExecutionTargetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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


@router.post("/{execution_target_id}/preload", response_model=ExecutionTargetResponse, status_code=202)
async def preload_execution_target(
    execution_target_id: str,
    request: PreloadRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
):
    controller = getattr(http_request.app.state, "preload_controller", None)
    if controller is None:
        raise HTTPException(status_code=503, detail="Preload service is unavailable")
    try:
        return await controller.start(session, execution_target_id, request)
    except ExecutionTargetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/provision/catalog", response_model=list[ProvisionSelection])
async def provision_catalog():
    from model_registry import INDEPENDENT_RUNTIME_MODELS, get_registry
    return [ProvisionSelection(kind=kind, model_id=model_id)
        for model_id in sorted(INDEPENDENT_RUNTIME_MODELS)
        if get_registry().get_model(model_id) is not None for kind in ("model", "image")]


@router.post("/{execution_target_id}/provision/preview", response_model=ProvisionPreview)
async def preview_provision(execution_target_id: str, request: ProvisionSelection | WorkflowProvisionSelection,
                            http_request: Request, session: AsyncSession = Depends(get_session)):
    controller = getattr(http_request.app.state, "preload_controller", None)
    if controller is None:
        raise HTTPException(status_code=503, detail="Preload service is unavailable")
    try:
        return await controller.preview(session, execution_target_id, request, http_request=http_request)
    except ExecutionTargetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{execution_target_id}/provision", response_model=ExecutionTargetResponse, status_code=202)
async def provision_execution_target(execution_target_id: str, request: ProvisionRequest | WorkflowProvisionRequest,
                                      http_request: Request, session: AsyncSession = Depends(get_session)):
    controller = getattr(http_request.app.state, "preload_controller", None)
    if controller is None:
        raise HTTPException(status_code=503, detail="Preload service is unavailable")
    try:
        return await controller.start(session, execution_target_id, request, http_request=http_request)
    except ExecutionTargetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{execution_target_id}/provision/{operation_id}/cancel", response_model=ExecutionTargetResponse, status_code=202)
async def cancel_provision(execution_target_id: str, operation_id: str, http_request: Request,
                           session: AsyncSession = Depends(get_session)):
    controller = getattr(http_request.app.state, "preload_controller", None)
    if controller is None:
        raise HTTPException(status_code=503, detail="Preload service is unavailable")
    try:
        return await controller.cancel(session, execution_target_id, operation_id)
    except ExecutionTargetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{execution_target_id}/provision/{operation_id}/retry", response_model=ExecutionTargetResponse, status_code=202)
async def retry_provision(execution_target_id: str, operation_id: str,
                          request: ProvisionRequest | WorkflowProvisionRequest, http_request: Request,
                          session: AsyncSession = Depends(get_session)):
    controller = getattr(http_request.app.state, "preload_controller", None)
    if controller is None:
        raise HTTPException(status_code=503, detail="Preload service is unavailable")
    try:
        return await controller.start(session, execution_target_id, request, retry_operation_id=operation_id, http_request=http_request)
    except ExecutionTargetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{execution_target_id}/artifact-inventory", response_model=ObservedArtifactInventory | None)
async def artifact_inventory(execution_target_id: str, session: AsyncSession = Depends(get_session)):
    try:
        return observed_artifact_inventory(await get_target(session, execution_target_id))
    except ExecutionTargetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/active/telemetry")
async def execution_target_telemetry(
    session: AsyncSession = Depends(get_session), since: str | None = None,
    execution_target_id: str | None = None,
):
    return await active_remote_telemetry(session, since, execution_target_id)
