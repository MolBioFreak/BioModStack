"""Operator API for attaching an already-running execution target."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from services.remote_execution.contracts import (
    ExecutionTargetActivateRequest,
    PreloadRequest, ProvisionRequest, ProvisionSelection, ProvisionPreview, ObservedArtifactInventory,
    WorkflowProvisionSelection, WorkflowProvisionRequest, WorkflowPackSelection, WorkflowPackRequest,
    ExecutionTargetInventoryResponse,
    ExecutionTargetResponse, ExecutionTargetDetails, CachedArtifactPage, PreloadArtifactPage,
    HFAssetLinkStatus,
    HFAssetLinkCheckRequest,
)
from services.remote_execution.targets import (
    ExecutionTargetError,
    active_remote_telemetry,
    deactivate_target,
    list_targets, get_target, observed_artifact_inventory,
    refresh_vast_targets, target_status, artifact_page, _target_response,
)

from services.remote_execution.managed_inventory import (
    ManagedInventory, project_inventory, ManagedInventorySummary, ManagedInventoryArtifactPage,
    read_inventory_summary, read_inventory_artifacts,
)

router = APIRouter()


@router.get('/providers/huggingface', response_model=HFAssetLinkStatus)
async def hf_asset_link_status():
    """Deployment-owned config only. No cloud calls, keys or capability URLs."""
    from services.remote_execution.hf_assets import readiness
    return readiness()


@router.post('/providers/huggingface/check', response_model=HFAssetLinkStatus)
async def check_hf_asset_link(request: HFAssetLinkCheckRequest | None = None):
    """Explicit read-only private-bucket authentication; never upload or rent."""
    from services.remote_execution.hf_assets import check_connection
    return await check_connection()


@router.get('/{execution_target_id}/runtime-inventory', response_model=ManagedInventory | None)
async def runtime_inventory(execution_target_id: str, session: AsyncSession = Depends(get_session)):
    try:
        return project_inventory(await get_target(session, execution_target_id))
    except ExecutionTargetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get('/{execution_target_id}/runtime-inventory/summary', response_model=ManagedInventorySummary | None)
async def runtime_inventory_summary(execution_target_id: str, session: AsyncSession = Depends(get_session)):
    try:
        return await read_inventory_summary(session, execution_target_id)
    except ExecutionTargetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get('/{execution_target_id}/runtime-inventory/artifacts', response_model=ManagedInventoryArtifactPage)
async def runtime_inventory_artifacts(execution_target_id: str,
    observation_id: str = Query(pattern=r'^[0-9a-f]{64}$'),
    release_sha256: str = Query(pattern=r'^[0-9a-f]{64}$'),
    offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=250),
    session: AsyncSession = Depends(get_session)):
    try:
        return await read_inventory_artifacts(session, execution_target_id, observation_id, release_sha256, offset, limit)
    except ExecutionTargetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post('/{execution_target_id}/runtime-inventory/refresh', response_model=ManagedInventory | ManagedInventorySummary | None)
async def refresh_runtime_inventory(execution_target_id: str, http_request: Request,
                                    summary: bool = Query(default=False),
                                    session: AsyncSession = Depends(get_session)):
    controller = getattr(http_request.app.state, 'preload_controller', None)
    if controller is None:
        raise HTTPException(status_code=503, detail='Preload service is unavailable')
    try:
        if summary:
            return await controller.refresh_inventory(session, execution_target_id, summary=True)
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


@router.get("/provision/catalog", response_model=list[ProvisionSelection | WorkflowPackSelection])
async def provision_catalog():
    from model_registry import INDEPENDENT_RUNTIME_MODELS, INDEPENDENT_RUNTIME_IMAGES, get_registry
    return [ProvisionSelection(kind=kind, model_id=model_id)
        for kind, models in (("model", INDEPENDENT_RUNTIME_MODELS), ("image", INDEPENDENT_RUNTIME_IMAGES))
        for model_id in sorted(models)
        if get_registry().get_model(model_id) is not None] + [
            WorkflowPackSelection(kind="workflow_pack", workflow_id=workflow_id)
            for workflow_id in ("structure_prediction", "antibody_denovo")]


@router.post("/{execution_target_id}/provision/preview", response_model=ProvisionPreview)
async def preview_provision(execution_target_id: str, request: ProvisionSelection | WorkflowProvisionSelection | WorkflowPackSelection,
                            http_request: Request, session: AsyncSession = Depends(get_session)):
    controller = getattr(http_request.app.state, "preload_controller", None)
    if controller is None:
        raise HTTPException(status_code=503, detail="Preload service is unavailable")
    try:
        return await controller.preview(session, execution_target_id, request, http_request=http_request)
    except ExecutionTargetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{execution_target_id}/provision", response_model=ExecutionTargetResponse, status_code=202)
async def provision_execution_target(execution_target_id: str, request: ProvisionRequest | WorkflowProvisionRequest | WorkflowPackRequest,
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
                          request: ProvisionRequest | WorkflowProvisionRequest | WorkflowPackRequest, http_request: Request,
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


@router.get("/{execution_target_id}", response_model=ExecutionTargetResponse)
async def execution_target_status(execution_target_id: str, session: AsyncSession = Depends(get_session)):
    try:
        return await target_status(session, execution_target_id)
    except ExecutionTargetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{execution_target_id}/details", response_model=ExecutionTargetDetails)
async def execution_target_details(execution_target_id: str, session: AsyncSession = Depends(get_session)):
    try:
        return _target_response(await get_target(session, execution_target_id), details=True)
    except ExecutionTargetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{execution_target_id}/artifact-inventory/artifacts", response_model=CachedArtifactPage)
async def inventory_artifacts(execution_target_id: str, offset: int = Query(default=0, ge=0),
                              limit: int = Query(default=100, ge=1, le=250),
                              session: AsyncSession = Depends(get_session)):
    try:
        return await artifact_page(session, execution_target_id, offset=offset, limit=limit)
    except ExecutionTargetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{execution_target_id}/preload/{operation_id}/artifacts", response_model=PreloadArtifactPage)
async def preload_artifacts(execution_target_id: str, operation_id: str,
                            collection: Literal["progress", "cached"] = "progress",
                            offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=250),
                            session: AsyncSession = Depends(get_session)):
    try:
        return await artifact_page(session, execution_target_id, operation_id=operation_id,
                                   collection=collection, offset=offset, limit=limit)
    except ExecutionTargetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
