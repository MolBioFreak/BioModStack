"""Project Manager adapter, attachment, read-model, and surface routes."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session as get_core_session
from experiment_database import get_experiment_session
from experiment_operations import register_external_entity_receipt
from experiment_services import (
    ExperimentServiceError,
    IdempotencyConflict,
    NotFound,
    RevisionConflict,
    ValidationFailure,
)
from services.global_experiments.adapters import AdapterError, registry
from services.global_experiments.read_models import build_project_manager_read_model
from services.global_experiments.receipts import attach_verified_entity
from services.global_experiments.result_surfaces import result_surface_for_receipt


router = APIRouter(tags=["project-manager"])


class StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AttachRequest(StrictRequestModel):
    adapter_id: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    role: Literal["references", "uses_input", "produced", "validated_by"]
    expected_head_generation: int = Field(ge=0)


class ReceiptIssueRequest(StrictRequestModel):
    project_id: str = Field(min_length=1)


def _service_error(exc: ExperimentServiceError) -> HTTPException:
    message = str(exc)
    if isinstance(exc, NotFound):
        return HTTPException(404, detail={"code": "not_found", "message": message})
    if isinstance(exc, RevisionConflict):
        return HTTPException(409, detail={"code": "stale_generation", "message": message})
    if isinstance(exc, IdempotencyConflict):
        return HTTPException(409, detail={"code": "idempotency_conflict", "message": message})
    if isinstance(exc, ValidationFailure):
        return HTTPException(422, detail={"code": "validation_failed", "message": message})
    return HTTPException(400, detail={"code": "unsupported_operation", "message": message})


def _adapter_error(exc: AdapterError) -> HTTPException:
    status_by_code = {
        "unknown_adapter": 404,
        "entity_not_found": 404,
        "invalid_entity_id": 422,
        "invalid_limit": 422,
        "invalid_query": 422,
        "source_contract_invalid": 409,
        "source_contract_unavailable": 409,
        "source_artifact_unavailable": 409,
        "source_digest_mismatch": 409,
        "source_revision_unavailable": 503,
    }
    return HTTPException(
        status_by_code.get(exc.code, 422),
        detail={"code": exc.code, "message": str(exc)},
    )


@router.get("/api/domain-adapters")
async def list_domain_adapters() -> dict:
    return {"schema": "bms.global.adapter-registry.v1", "adapters": registry.list()}


@router.get("/api/domain-adapters/{adapter_id}/entities/search")
async def search_adapter_entities(
    adapter_id: str,
    q: str = Query(default="", max_length=255),
    limit: int = Query(default=25, ge=1, le=100),
    core_session: AsyncSession = Depends(get_core_session),
) -> dict:
    try:
        adapter = registry.get(adapter_id)
        projections = await adapter.search(core_session, query=q, limit=limit)
        items: list[dict] = []
        for projection in projections:
            item = {"adapter_id": adapter.adapter_id, **projection.as_dict()}
            try:
                verification = await adapter.verify(core_session, projection.entity_id)
                item.update(
                    attachable=True,
                    reason=None,
                    reopen_uri=str(verification["reopen_uri"]),
                )
            except AdapterError as verification_error:
                item.update(
                    attachable=False,
                    reason=verification_error.message,
                    reopen_uri="",
                )
            items.append(item)
        return {
            "schema": "bms.global.adapter-search.v1",
            "adapter_id": adapter.adapter_id,
            "adapter_version": adapter.adapter_version,
            "items": items,
            "next_cursor": None,
        }
    except AdapterError as exc:
        raise _adapter_error(exc) from exc


@router.post(
    "/api/domain-adapters/{adapter_id}/entities/{entity_id}/receipt",
    status_code=status.HTTP_201_CREATED,
)
async def issue_adapter_receipt(
    adapter_id: str,
    entity_id: str,
    payload: ReceiptIssueRequest,
    experiment_session: AsyncSession = Depends(get_experiment_session),
    core_session: AsyncSession = Depends(get_core_session),
) -> dict:
    try:
        adapter = registry.get(adapter_id)
        receipt = await adapter.verify(core_session, entity_id)
        receipt["verified_at"] = datetime.now(timezone.utc).isoformat()
        digest = receipt.get("content_digest") or receipt.get("contract_digest")
        if not isinstance(digest, str):
            raise AdapterError("source_contract_invalid", "verified source receipt has no digest")
        row = await register_external_entity_receipt(
            experiment_session,
            workspace_id=payload.project_id,
            store_id=str(receipt["store_id"]),
            entity_kind=str(receipt["entity_kind"]),
            entity_id=str(receipt["entity_id"]),
            generation_or_revision=str(receipt.get("entity_revision_id") or digest),
            content_digest=digest,
            availability="available",
            acknowledgement=receipt,
            verification_authority=adapter.adapter_id,
        )
        await experiment_session.commit()
        return {"receipt_id": row.id, "receipt": receipt}
    except AdapterError as exc:
        await experiment_session.rollback()
        raise _adapter_error(exc) from exc
    except ExperimentServiceError as exc:
        await experiment_session.rollback()
        raise _service_error(exc) from exc


@router.post(
    "/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/attach",
    status_code=status.HTTP_201_CREATED,
)
async def attach_domain_entity(
    project_id: str,
    experiment_id: str,
    domain_id: str,
    payload: AttachRequest,
    experiment_session: AsyncSession = Depends(get_experiment_session),
    core_session: AsyncSession = Depends(get_core_session),
) -> dict:
    try:
        receipt = await attach_verified_entity(
            experiment_session,
            core_session,
            project_id=project_id,
            global_experiment_id=experiment_id,
            domain_experiment_id=domain_id,
            adapter_id=payload.adapter_id,
            entity_id=payload.entity_id,
            role=payload.role,
            expected_head_generation=payload.expected_head_generation,
        )
        await experiment_session.commit()
        return receipt
    except AdapterError as exc:
        await experiment_session.rollback()
        raise _adapter_error(exc) from exc
    except ExperimentServiceError as exc:
        await experiment_session.rollback()
        raise _service_error(exc) from exc


@router.get("/api/projects/{project_id}/summary")
async def project_manager_summary(
    project_id: str,
    focus_id: str | None = Query(default=None),
    selected_node_key: str | None = Query(default=None),
    map_cursor: str | None = Query(default=None, max_length=512),
    run_cursor: str | None = Query(default=None, max_length=512),
    result_cursor: str | None = Query(default=None, max_length=512),
    lineage_cursor: str | None = Query(default=None, max_length=512),
    note_cursor: str | None = Query(default=None, max_length=512),
    activity_cursor: str | None = Query(default=None, max_length=512),
    map_limit: int = Query(default=50, ge=1, le=100),
    run_limit: int = Query(default=25, ge=1, le=100),
    result_limit: int = Query(default=25, ge=1, le=100),
    lineage_limit: int = Query(default=25, ge=1, le=100),
    note_limit: int = Query(default=25, ge=1, le=100),
    activity_limit: int = Query(default=25, ge=1, le=100),
    session: AsyncSession = Depends(get_experiment_session),
) -> dict:
    try:
        return await build_project_manager_read_model(
            session,
            project_id=project_id,
            focus_id=focus_id,
            selected_node_key=selected_node_key,
            map_cursor=map_cursor,
            run_cursor=run_cursor,
            result_cursor=result_cursor,
            lineage_cursor=lineage_cursor,
            note_cursor=note_cursor,
            activity_cursor=activity_cursor,
            map_limit=map_limit,
            run_limit=run_limit,
            result_limit=result_limit,
            lineage_limit=lineage_limit,
            note_limit=note_limit,
            activity_limit=activity_limit,
        )
    except ExperimentServiceError as exc:
        raise _service_error(exc) from exc


@router.get("/api/projects/{project_id}/receipts/{receipt_id}/surface")
async def project_receipt_surface(
    project_id: str,
    receipt_id: str,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict:
    try:
        return await result_surface_for_receipt(session, project_id=project_id, receipt_id=receipt_id)
    except ExperimentServiceError as exc:
        raise _service_error(exc) from exc


__all__ = ["router"]
