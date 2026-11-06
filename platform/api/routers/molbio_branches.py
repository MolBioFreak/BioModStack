"""Portable branch export/import routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from molbio_database import get_molbio_session
from services.molbio_branch_bundle import export_branch, import_branch
from services.molbio_persistence import IdempotencyConflictError

router = APIRouter(prefix="/api/molbio/branches", tags=["molbio-branches"])


class BranchExportRequest(BaseModel):
    revision_ids: list[str] = Field(min_length=1)


class BranchImportRequest(BaseModel):
    bundle: dict[str, Any]
    idempotency_key: str | None = Field(default=None, max_length=255)


@router.post("/export")
async def export_route(body: BranchExportRequest, session: AsyncSession = Depends(get_molbio_session)):
    try:
        return await export_branch(session, body.revision_ids)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/import")
async def import_route(body: BranchImportRequest, session: AsyncSession = Depends(get_molbio_session)):
    try:
        return await import_branch(session, body.bundle, idempotency_key=body.idempotency_key)
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, KeyError, TypeError) as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=f"invalid branch bundle: {exc}") from exc
