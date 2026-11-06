"""Read routes for a revision's related operations and the fragment chooser."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from molbio_database import get_molbio_session
from services.molbio_relatives import (
    CHOOSER_DEFAULT_LIMIT,
    CHOOSER_MAX_LIMIT,
    RELATIVES_DEFAULT_LIMIT,
    RELATIVES_MAX_LIMIT,
    chooser_items,
    revision_relatives,
)

router = APIRouter(prefix="/api/molbio", tags=["molbio-relatives"])


@router.get("/revisions/{revision_id}/relatives")
async def get_revision_relatives(
    revision_id: str,
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=RELATIVES_DEFAULT_LIMIT, ge=1, le=RELATIVES_MAX_LIMIT),
    session: AsyncSession = Depends(get_molbio_session),
) -> dict[str, Any]:
    try:
        return await revision_relatives(session, revision_id, cursor=cursor, limit=limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/chooser")
async def get_chooser(
    q: str = Query(default="", max_length=255),
    kinds: str | None = Query(default=None, max_length=512, description="comma-separated operation kinds"),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=CHOOSER_DEFAULT_LIMIT, ge=1, le=CHOOSER_MAX_LIMIT),
    session: AsyncSession = Depends(get_molbio_session),
) -> dict[str, Any]:
    kind_list = [item.strip() for item in (kinds or "").split(",") if item.strip()]
    try:
        return await chooser_items(session, q=q, kinds=kind_list, cursor=cursor, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
