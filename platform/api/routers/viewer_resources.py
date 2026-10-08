from __future__ import annotations

import json
import os
import secrets
from collections.abc import Mapping
from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job, get_session
from routers.files import _iter_file_range, _parse_byte_range
from services.viewer_resource_contracts import MAX_SNAPSHOT_BYTES, ViewerResourceError, validate_snapshot_create, viewer_error_detail
from services.viewer_resources import (
    create_snapshot_record,
    delete_snapshot_record,
    get_snapshot_record,
    list_snapshot_records,
    load_volume_inventory,
    resolve_viewer_artifact,
    serialize_snapshot_record,
)

router = APIRouter()
MAX_RANGE_BYTES = 64 * 1024 * 1024
_TRUSTED_PROXY_HEADER = "x-bms-cm-proxy-secret"


def _trusted_application_boundary(request: Request) -> bool:
    configured = os.getenv("BMS_CM_TRUSTED_PROXY_SECRET", "")
    supplied = request.headers.get(_TRUSTED_PROXY_HEADER, "")
    return bool(configured and supplied and secrets.compare_digest(configured, supplied))


def _principal(request: Request) -> str:
    principal = getattr(request.state, "authenticated_principal", None)
    if principal is None:
        if _trusted_application_boundary(request):
            return "local-application-operator"
        raise HTTPException(status_code=401, detail={"schema": "bms.viewer.error.v1", "code": "VIEWER_AUTH_REQUIRED", "message": "Authenticated viewer principal required", "retryable": False})
    if isinstance(principal, Mapping):
        actor = principal.get("id") or principal.get("subject")
        roles = principal.get("roles") or []
    else:
        actor = getattr(principal, "id", None) or getattr(principal, "subject", None)
        roles = getattr(principal, "roles", [])
    if not actor or not {str(role).strip().lower() for role in roles}.intersection({"operator", "scientist", "admin"}):
        raise HTTPException(status_code=403, detail={"schema": "bms.viewer.error.v1", "code": "VIEWER_FORBIDDEN", "message": "Viewer scientist/operator role required", "retryable": False})
    return str(actor)


async def _job(job_id: str, session: AsyncSession) -> Job:
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail={"schema": "bms.viewer.error.v1", "code": "VIEWER_RESOURCE_NOT_FOUND", "message": "Job not found", "retryable": False})
    return job


def _raise(error: ViewerResourceError, *, resource_id: str | None = None) -> NoReturn:
    raise HTTPException(status_code=error.status_code, detail=viewer_error_detail(error, resource_id=resource_id))


async def _bounded_json(request: Request) -> Any:
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > MAX_SNAPSHOT_BYTES):
        raise ViewerResourceError("Snapshot request exceeds 8 MiB", code="VIEWER_REQUEST_TOO_LARGE", status_code=413)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_SNAPSHOT_BYTES:
            raise ViewerResourceError("Snapshot request exceeds 8 MiB", code="VIEWER_REQUEST_TOO_LARGE", status_code=413)
        chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ViewerResourceError("Snapshot request is not valid UTF-8 JSON") from exc


@router.get("/{job_id}/viewer/volumes")
async def get_viewer_volumes(job_id: str, request: Request, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    _principal(request)
    try:
        return load_volume_inventory(await _job(job_id, session))
    except ViewerResourceError as exc:
        _raise(exc)


@router.get("/{job_id}/viewer/volumes/{volume_id}")
async def get_viewer_volume(job_id: str, volume_id: str, request: Request, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    _principal(request)
    try:
        inventory = load_volume_inventory(await _job(job_id, session))
        volume = next((entry for entry in inventory["volumes"] if entry.get("volumeId") == volume_id), None)
        if volume is None:
            raise ViewerResourceError("Viewer volume not found", code="VIEWER_RESOURCE_NOT_FOUND", status_code=404)
        return volume
    except ViewerResourceError as exc:
        _raise(exc, resource_id=volume_id)


@router.get("/{job_id}/viewer/artifacts/{artifact_id}/content")
async def get_viewer_artifact_content(job_id: str, artifact_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    _principal(request)
    try:
        artifact = resolve_viewer_artifact(await _job(job_id, session), artifact_id, verify=True)
        headers = {
            "Accept-Ranges": "bytes", "ETag": f'"{artifact.sha256}"', "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-cache", "Content-Encoding": "identity",
        }
        range_header = request.headers.get("range")
        if not range_header:
            if artifact.size_bytes > MAX_RANGE_BYTES:
                raise ViewerResourceError("Artifact requires a bounded byte range", code="VIEWER_RANGE_REQUIRED", status_code=412)
            headers["Content-Length"] = str(artifact.size_bytes)
            return StreamingResponse(_iter_file_range(artifact.path, 0, artifact.size_bytes - 1), headers=headers, media_type=artifact.mime_type)
        try:
            start, end = _parse_byte_range(range_header, artifact.size_bytes)
        except ValueError:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{artifact.size_bytes}"})
        if end - start + 1 > MAX_RANGE_BYTES:
            end = start + MAX_RANGE_BYTES - 1
        headers.update({"Content-Range": f"bytes {start}-{end}/{artifact.size_bytes}", "Content-Length": str(end - start + 1)})
        return StreamingResponse(_iter_file_range(artifact.path, start, end), status_code=206, headers=headers, media_type=artifact.mime_type)
    except ViewerResourceError as exc:
        _raise(exc, resource_id=artifact_id)


@router.get("/{job_id}/viewer/snapshots")
async def get_viewer_snapshots(job_id: str, request: Request, limit: int = Query(100, ge=1, le=100), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    actor = _principal(request)
    await _job(job_id, session)
    records = await list_snapshot_records(session, job_id, limit=limit, created_by=actor)
    return {"schema": "bms.viewer.snapshot-list.v2", "jobId": job_id, "snapshots": [serialize_snapshot_record(record, include_snapshot=False) for record in records], "nextCursor": None}


@router.post("/{job_id}/viewer/snapshots", status_code=201)
async def post_viewer_snapshot(job_id: str, request: Request, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    actor = _principal(request)
    await _job(job_id, session)
    try:
        validated = validate_snapshot_create(await _bounded_json(request))
        snapshot_job = validated.snapshot.get("scene", {}).get("provenance", {}).get("jobId")
        if snapshot_job is not None and snapshot_job != job_id:
            raise ViewerResourceError("Snapshot job identity conflicts with route", code="VIEWER_STATE_CONFLICT", status_code=409)
        record = await create_snapshot_record(session, job_id, validated, created_by=actor)
        return serialize_snapshot_record(record, include_snapshot=True)
    except ViewerResourceError as exc:
        _raise(exc)


@router.get("/{job_id}/viewer/snapshots/{snapshot_id}")
async def get_viewer_snapshot(job_id: str, snapshot_id: str, request: Request, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    actor = _principal(request)
    await _job(job_id, session)
    try:
        return serialize_snapshot_record(await get_snapshot_record(session, job_id, snapshot_id, created_by=actor), include_snapshot=True)
    except ViewerResourceError as exc:
        _raise(exc, resource_id=snapshot_id)


@router.delete("/{job_id}/viewer/snapshots/{snapshot_id}", status_code=204)
async def remove_viewer_snapshot(job_id: str, snapshot_id: str, request: Request, session: AsyncSession = Depends(get_session)) -> Response:
    actor = _principal(request)
    await _job(job_id, session)
    try:
        await delete_snapshot_record(session, job_id, snapshot_id, created_by=actor)
        return Response(status_code=204)
    except ViewerResourceError as exc:
        _raise(exc, resource_id=snapshot_id)
