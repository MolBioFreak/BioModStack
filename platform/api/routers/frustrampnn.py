"""
FrustraMPNN API Router - Energetic Frustration Analysis

Provides endpoint for running FrustraMPNN on PDB structures.
Returns per-residue frustration profiles for all amino acid mutations.
"""

import hashlib
import json
import logging
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    FrustraMPNNArtifact,
    FrustraMPNNLandscapeRow,
    FrustraMPNNResult,
    Job,
    get_session,
)
from services.frustrampnn.jobs import (
    FrustraMPNNChildError,
    child_receipt,
    create_child_job,
    create_reanalysis_child,
    design_selections,
    upload_selection,
)

router = APIRouter(prefix="/api/frustrampnn", tags=["frustrampnn"])
logger = logging.getLogger(__name__)

class DesignSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    design_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AnalyzeDesignsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selections: list[DesignSelectionRequest] = Field(min_length=1)


class ReanalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _child_job_receipt(session: AsyncSession, child: Job) -> dict[str, Any]:
    """Serialize a child that the service has already committed atomically."""
    return await child_receipt(session, child=child)


@router.post("/jobs/uploads/analyze", status_code=status.HTTP_202_ACCEPTED)
async def analyze_uploaded_structure(
    request: Request,
    pdb_file: UploadFile = File(..., description="PDB or mmCIF structure"),
    expected_sha256: Optional[str] = Query(None, pattern=r"^[0-9a-f]{64}$"),
    session: AsyncSession = Depends(get_session),
):
    """Snapshot an upload and return a persisted scheduler-owned child receipt."""
    unknown_query = set(request.query_params) - {"expected_sha256"}
    form = await request.form()
    unknown_form = set(form) - {"pdb_file"}
    if unknown_query or unknown_form:
        raise HTTPException(422, "FrustraMPNN launch overrides/unknown fields are forbidden")
    try:
        selection = upload_selection(
            filename=pdb_file.filename or "",
            payload=await pdb_file.read(),
            expected_sha256=expected_sha256,
        )
        job = await create_child_job(
            session,
            selections=[selection],
            source_parent=None,
            trigger="upload_analyze",
        )
        return await _child_job_receipt(session, job)
    except FrustraMPNNChildError as exc:
        await session.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.post("/jobs/{parent_job_id}/analyze", status_code=status.HTTP_202_ACCEPTED)
async def analyze_designs(
    parent_job_id: str,
    body: AnalyzeDesignsRequest,
    session: AsyncSession = Depends(get_session),
):
    """Queue immutable selected Designs under an existing parent authority."""
    parent = await session.get(Job, parent_job_id)
    if parent is None:
        raise HTTPException(404, "source parent Job not found")
    expected = {item.design_id: item.source_sha256 for item in body.selections}
    design_ids = [item.design_id for item in body.selections]
    try:
        selections = await design_selections(
            session,
            source_parent=parent,
            design_ids=design_ids,
            expected_sha256=expected,
        )
        job = await create_child_job(
            session,
            selections=selections,
            source_parent=parent,
            trigger="design_analyze",
        )
        return await _child_job_receipt(session, job)
    except FrustraMPNNChildError as exc:
        await session.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.post("/jobs/{child_job_id}/reanalyze", status_code=status.HTTP_202_ACCEPTED)
async def reanalyze_child(
    child_job_id: str,
    _body: ReanalyzeRequest,
    session: AsyncSession = Depends(get_session),
):
    prior = await session.get(Job, child_job_id)
    if prior is None:
        raise HTTPException(404, "FrustraMPNN child Job not found")
    try:
        job = await create_reanalysis_child(session, prior_child=prior)
        return await _child_job_receipt(session, job)
    except FrustraMPNNChildError as exc:
        await session.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.get("/jobs/{child_job_id}/receipt")
async def get_child_receipt(
    child_job_id: str,
    session: AsyncSession = Depends(get_session),
):
    child = await session.get(Job, child_job_id)
    if child is None:
        raise HTTPException(404, "FrustraMPNN child Job not found")
    try:
        return await child_receipt(session, child=child)
    except FrustraMPNNChildError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/health")
async def health_check():
    """Report scheduler integration health without probing runtime paths."""
    return {
        "scheduler_backed": True,
        "model_id": "frustrampnn",
        "mode": "analyze",
        "direct_execution": False,
    }


_RESULT_FIELDS = (
    "invocation_id",
    "parent_job_id",
    "parent_workflow_id",
    "candidate_id",
    "design_id",
    "requiredness",
    "source_artifact_id",
    "source_artifact_sha256",
    "request_sha256",
    "manifest_sha256",
    "summary_sha256",
    "created_at",
)
_LANDSCAPE_FIELDS = (
    "id",
    "invocation_id",
    "target_id",
    "entity_instance_id",
    "auth_asym_id",
    "auth_seq_id",
    "insertion_code",
    "sequence_index",
    "wt",
    "mutation_aa",
    "score",
    "score_class",
    "scoreable",
    "status",
    "reason",
)


def _result_payload(result: FrustraMPNNResult, *, detail: bool = False) -> dict[str, Any]:
    payload = {name: getattr(result, name) for name in _RESULT_FIELDS}
    terminal = dict(result.terminal_result_json)
    payload["status"] = terminal["status"]
    payload["component_contract_version"] = terminal["component_contract_version"]
    payload["runtime_identity"] = dict(terminal["runtime_identity"])
    payload["assigned_gpu"] = dict(result.assigned_gpu_json)
    payload["failure_class"] = terminal["failure_class"]
    payload["diagnostic"] = terminal["diagnostic"]
    if detail:
        payload["summary"] = dict(result.summary_json)
        payload["terminal_result"] = terminal
    return payload


def _artifact_payload(artifact: FrustraMPNNArtifact) -> dict[str, Any]:
    metadata = dict(artifact.metadata_json)
    return {
        "artifact_id": artifact.artifact_id,
        "invocation_id": artifact.invocation_id,
        "role": artifact.role,
        "relative_path": artifact.relative_path,
        "content_sha256": artifact.content_sha256,
        "size_bytes": artifact.size_bytes,
        "media_type": artifact.media_type,
        "schema_name": metadata.get("schema_name"),
        "schema_version": metadata.get("schema_version"),
        "cardinality": metadata.get("cardinality"),
    }


async def _scoped_result(
    invocation_id: str,
    job_id: str,
    session: AsyncSession,
) -> FrustraMPNNResult:
    result = await session.get(FrustraMPNNResult, (job_id, invocation_id))
    if result is None:
        raise HTTPException(status_code=404, detail="FrustraMPNN result not found")
    return result


@router.get("/jobs/{job_id}/results")
async def list_results(
    job_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1_000_000),
    candidate_id: str | None = Query(None),
    design_id: str | None = Query(None),
    parent_workflow_id: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    filters = [FrustraMPNNResult.parent_job_id == job_id]
    if candidate_id is not None:
        filters.append(FrustraMPNNResult.candidate_id == candidate_id)
    if design_id is not None:
        filters.append(FrustraMPNNResult.design_id == design_id)
    if parent_workflow_id is not None:
        filters.append(FrustraMPNNResult.parent_workflow_id == parent_workflow_id)
    total = int(
        (await session.execute(select(func.count()).select_from(FrustraMPNNResult).where(*filters))).scalar_one()
    )
    rows = (
        await session.execute(
            select(FrustraMPNNResult)
            .where(*filters)
            .order_by(FrustraMPNNResult.created_at.asc(), FrustraMPNNResult.invocation_id.asc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()
    return {
        "items": [_result_payload(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/results/{invocation_id}")
async def result_detail(
    invocation_id: str,
    job_id: str = Query(...),
    session: AsyncSession = Depends(get_session),
):
    return _result_payload(await _scoped_result(invocation_id, job_id, session), detail=True)


@router.get("/results/{invocation_id}/landscape")
async def result_landscape(
    invocation_id: str,
    job_id: str = Query(...),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=1_000_000),
    target_id: str | None = Query(None),
    entity_instance_id: str | None = Query(None),
    auth_asym_id: str | None = Query(None),
    auth_seq_id: str | None = Query(None),
    insertion_code: str | None = Query(None),
    sequence_index: int | None = Query(None),
    mutation_aa: str | None = Query(None, min_length=1, max_length=1),
    status: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    result = await _scoped_result(invocation_id, job_id, session)
    filters = [
        FrustraMPNNLandscapeRow.parent_job_id == job_id,
        FrustraMPNNLandscapeRow.invocation_id == invocation_id,
    ]
    exact = {
        "target_id": target_id,
        "entity_instance_id": entity_instance_id,
        "auth_asym_id": auth_asym_id,
        "auth_seq_id": auth_seq_id,
        "insertion_code": insertion_code,
        "sequence_index": sequence_index,
        "mutation_aa": mutation_aa,
        "status": status,
    }
    for field, value in exact.items():
        if value is not None:
            filters.append(getattr(FrustraMPNNLandscapeRow, field) == value)
    total = int(
        (
            await session.execute(
                select(func.count()).select_from(FrustraMPNNLandscapeRow).where(*filters)
            )
        ).scalar_one()
    )
    rows = (
        await session.execute(
            select(FrustraMPNNLandscapeRow)
            .where(*filters)
            .order_by(
                FrustraMPNNLandscapeRow.entity_instance_id.asc(),
                FrustraMPNNLandscapeRow.sequence_index.asc(),
                FrustraMPNNLandscapeRow.mutation_aa.asc(),
                FrustraMPNNLandscapeRow.id.asc(),
            )
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()
    items = []
    for row in rows:
        stored = dict(row.row_json)
        residue = stored.get("residue")
        items.append({
            **{name: getattr(row, name) for name in _LANDSCAPE_FIELDS},
            "candidate_id": result.candidate_id,
            "auth_seq_id": int(row.auth_seq_id),
            "class": row.score_class,
            "native": row.mutation_aa == row.wt,
            "provenance": dict(row.provenance_json),
            "residue": dict(residue) if isinstance(residue, dict) else None,
        })
    return {
        "items": items,
        "candidate_id": result.candidate_id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": offset + len(items) if offset + len(items) < total else None,
    }


@router.get("/results/{invocation_id}/artifacts")
async def result_artifacts(
    invocation_id: str,
    job_id: str = Query(...),
    session: AsyncSession = Depends(get_session),
):
    await _scoped_result(invocation_id, job_id, session)
    rows = (
        await session.execute(
            select(FrustraMPNNArtifact)
            .where(
                FrustraMPNNArtifact.parent_job_id == job_id,
                FrustraMPNNArtifact.invocation_id == invocation_id,
            )
            .order_by(FrustraMPNNArtifact.role.asc(), FrustraMPNNArtifact.artifact_id.asc())
        )
    ).scalars().all()
    return {"items": [_artifact_payload(row) for row in rows], "total": len(rows)}


def _artifact_byte_range(value: str | None, size_bytes: int) -> tuple[int, int, int]:
    if not value:
        return (0, size_bytes - 1, 200)
    try:
        unit, bounds = value.split("=", 1)
        left, right = bounds.split("-", 1)
        if unit != "bytes" or "," in bounds or size_bytes <= 0:
            raise ValueError
        if left:
            start = int(left)
            if start < 0 or start >= size_bytes:
                raise ValueError
            end = min(int(right), size_bytes - 1) if right else size_bytes - 1
            if end < start:
                raise ValueError
        else:
            suffix = int(right)
            if suffix <= 0:
                raise ValueError
            start = max(0, size_bytes - suffix)
            end = size_bytes - 1
        return start, end, 206
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=416,
            detail="invalid artifact byte range",
            headers={"Content-Range": f"bytes */{size_bytes}"},
        )


async def _verified_artifact_snapshot(
    artifact: FrustraMPNNArtifact,
    session: AsyncSession,
):
    siblings = (
        await session.execute(
            select(FrustraMPNNArtifact.storage_path).where(
                FrustraMPNNArtifact.parent_job_id == artifact.parent_job_id,
                FrustraMPNNArtifact.invocation_id == artifact.invocation_id,
            )
        )
    ).scalars().all()
    relative = Path(artifact.relative_path)
    storage = Path(artifact.storage_path).absolute()
    roots = {str(Path(path).absolute().parent) for path in siblings}
    if (
        relative.is_absolute()
        or len(relative.parts) != 1
        or len(roots) != 1
        or storage != Path(next(iter(roots))) / relative
    ):
        raise OSError("artifact storage authority is inconsistent")

    root_fd = -1
    descriptor = -1
    snapshot = tempfile.TemporaryFile(mode="w+b")
    try:
        root_fd = os.open(
            next(iter(roots)),
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        )
        descriptor = os.open(
            relative.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root_fd,
        )
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size != artifact.size_bytes:
            raise OSError("artifact is not the registered regular file")
        digest = hashlib.sha256()
        remaining = artifact.size_bytes
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise OSError("artifact is truncated")
            digest.update(chunk)
            snapshot.write(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise OSError("artifact exceeds its registered size")
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if identity_before != identity_after or digest.hexdigest() != artifact.content_sha256:
            raise OSError("artifact byte identity changed")
        snapshot.seek(0)
        return snapshot
    except Exception:
        snapshot.close()
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if root_fd >= 0:
            os.close(root_fd)


@router.get("/artifacts/{artifact_id}")
async def download_artifact(
    artifact_id: str,
    request: Request,
    job_id: str = Query(...),
    session: AsyncSession = Depends(get_session),
):
    artifact = await session.get(FrustraMPNNArtifact, artifact_id)
    if artifact is None or artifact.parent_job_id != job_id:
        raise HTTPException(status_code=404, detail="FrustraMPNN artifact not found")
    await _scoped_result(artifact.invocation_id, job_id, session)
    try:
        snapshot = await _verified_artifact_snapshot(artifact, session)
    except (OSError, ValueError):
        raise HTTPException(status_code=409, detail="artifact byte identity is unavailable")
    try:
        start, end, status_code = _artifact_byte_range(
            request.headers.get("range"), artifact.size_bytes
        )
    except HTTPException:
        snapshot.close()
        raise
    snapshot.seek(start)
    remaining = max(0, end - start + 1)

    def content():
        nonlocal remaining
        try:
            while remaining:
                chunk = snapshot.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise RuntimeError("verified artifact snapshot became truncated")
                remaining -= len(chunk)
                yield chunk
        finally:
            snapshot.close()

    safe_name = Path(artifact.relative_path).name.replace('"', "")
    headers = {
        "ETag": f'"{artifact.content_sha256}"',
        "X-Content-Type-Options": "nosniff",
        "Accept-Ranges": "bytes",
        "Content-Length": str(max(0, end - start + 1)),
        "Content-Disposition": f'attachment; filename="{safe_name}"',
    }
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{artifact.size_bytes}"
    return StreamingResponse(
        content(),
        status_code=status_code,
        media_type=artifact.media_type,
        headers=headers,
    )
