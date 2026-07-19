from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from database import ExternalResultImport, async_session, get_session
from paths import resolve_allowed_path
from schemas import (
    ExternalImportCreateRequest,
    ExternalImportPreviewRequest,
    ExternalImportPreviewResponse,
    ExternalImportResponse,
)
from services.external_imports.boltz_api import BoltzImportError, preview_boltz_api_run
from services.external_imports.service import (
    process_external_import,
    queue_external_import,
    retry_external_import,
)


logger = logging.getLogger(__name__)
router = APIRouter()


def _http_error(exc: BoltzImportError, *, status_code: int = 422) -> HTTPException:
    if exc.code in {"SOURCE_CHANGED_AFTER_PREVIEW", "IMPORT_IDENTITY_CONFLICT"}:
        status_code = 409
    if exc.code == "IMPORT_NOT_FOUND":
        status_code = 404
    return HTTPException(status_code=status_code, detail={"code": exc.code, "message": exc.message})


def _resolve_source(source_path: str) -> Path:
    try:
        source = resolve_allowed_path(source_path)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "SOURCE_NOT_ALLOWED", "message": str(exc)},
        ) from exc
    if not source.is_dir() or source.is_symlink():
        raise HTTPException(
            status_code=422,
            detail={"code": "SOURCE_NOT_ALLOWED", "message": "source path must be a real directory"},
        )
    return source


@router.post("/preview", response_model=ExternalImportPreviewResponse)
async def preview_external_import(payload: ExternalImportPreviewRequest) -> ExternalImportPreviewResponse:
    if payload.provider_hint != "boltz_api":
        raise HTTPException(
            status_code=422,
            detail={"code": "PROVIDER_UNSUPPORTED", "message": "only boltz_api is supported"},
        )
    try:
        preview = await asyncio.to_thread(preview_boltz_api_run, _resolve_source(payload.source_path))
    except BoltzImportError as exc:
        raise _http_error(exc) from exc
    return ExternalImportPreviewResponse(**preview.to_dict())


async def _run_import(import_id: str) -> None:
    async with async_session() as session:
        try:
            await process_external_import(session, import_id=import_id)
        except Exception:
            logger.exception("External result import %s failed", import_id)


@router.post("", response_model=ExternalImportResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_external_import(
    payload: ExternalImportCreateRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ExternalImportResponse:
    if payload.provider != "boltz_api":
        raise HTTPException(
            status_code=422,
            detail={"code": "PROVIDER_UNSUPPORTED", "message": "only boltz_api is supported"},
        )
    source = _resolve_source(payload.source_path)
    try:
        record = await queue_external_import(
            session,
            source_dir=source,
            preview_fingerprint=payload.preview_fingerprint,
            dataset_name=payload.dataset_name,
            job_name=payload.job_name,
        )
    except BoltzImportError as exc:
        raise _http_error(exc) from exc
    if record.state == "discovered":
        background_tasks.add_task(_run_import, record.id)
    return ExternalImportResponse.model_validate(record)


@router.get("/{import_id}", response_model=ExternalImportResponse)
async def get_external_import(
    import_id: str,
    session: AsyncSession = Depends(get_session),
) -> ExternalImportResponse:
    record = await session.get(ExternalResultImport, import_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "IMPORT_NOT_FOUND", "message": "external import was not found"},
        )
    return ExternalImportResponse.model_validate(record)


@router.post("/{import_id}/retry", response_model=ExternalImportResponse, status_code=status.HTTP_202_ACCEPTED)
async def retry_external_import_route(
    import_id: str,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ExternalImportResponse:
    try:
        record = await retry_external_import(session, import_id=import_id)
    except BoltzImportError as exc:
        raise _http_error(exc) from exc
    if record.state == "discovered":
        background_tasks.add_task(_run_import, record.id)
    return ExternalImportResponse.model_validate(record)
