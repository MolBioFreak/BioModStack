"""Job-scoped ONT alignment viewer, artifact streaming, and read-inspection routes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO, Iterator
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from database import Job, get_session
from services import alignment_access
from services import ngs_alignment_sessions as service
from services.job_result_roots import JobResultRootError, resolve_persisted_job_result_root
from services.sequence_qc_manifest import (
    SequenceQcManifestError,
    find_manifest_in_result_root,
    load_sequence_qc_manifest,
)

router = APIRouter()
LOCAL_DEVELOPMENT_ADMIN_HOSTS = frozenset({None, "127.0.0.1", "::1", "localhost", "testclient"})


async def require_alignment_job(
    job_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Job:
    """Require a persisted job and its opaque creator capability."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    provenance = job.provenance if isinstance(job.provenance, dict) else {}
    token = alignment_access.request_alignment_token(request, job_id)
    if not alignment_access.capability_matches(
        token,
        provenance.get(alignment_access.PROVENANCE_DIGEST_KEY),
    ):
        raise HTTPException(status_code=403, detail="alignment access denied")
    return job


def _http_error(exc: service.AlignmentSessionError) -> HTTPException:
    message = str(exc)
    if "unsafe" in message.lower():
        return HTTPException(status_code=403, detail="alignment resource unavailable")
    if "not found" in message.lower():
        return HTTPException(status_code=404, detail="alignment resource not found")
    return HTTPException(status_code=400, detail=message)


def _job_output_dir(job: Job) -> str | None:
    return getattr(job, "child_output_dir", None) or job.output_dir


def _job_authority(job: Job) -> dict[str, str]:
    params = getattr(job, "params", None)
    params = params if isinstance(params, dict) else {}
    source_reference_sha256 = params.get("reference_sequence_sha256")
    workflow_id = params.get("ont_workflow_id") or params.get("workflow_id")
    input_mode = params.get("ont_input_mode") or params.get("input_mode")
    if not all(isinstance(value, str) and value for value in (source_reference_sha256, workflow_id, input_mode)):
        raise service.AlignmentSessionError("authorized alignment job provenance is required")
    return {
        "source_reference_sha256": str(source_reference_sha256),
        "workflow_id": str(workflow_id),
        "input_mode": str(input_mode),
    }


def _job_package_authority(job: Job) -> dict[str, str]:
    authority = _job_authority(job)
    params = getattr(job, "params", None)
    params = params if isinstance(params, dict) else {}
    source_key = {"fastq": "fastq_path", "bam": "bam_path", "pod5": "pod5_dir"}.get(authority["input_mode"])
    source_path = params.get(source_key) if source_key is not None else None
    if not isinstance(source_path, str) or not source_path.strip():
        raise service.AlignmentSessionError("authorized source input path is required")
    return {**authority, "source_input_path": source_path}


def _require_local_development_browser(request: Request) -> None:
    client_host = request.client.host if request.client is not None else None
    if client_host not in LOCAL_DEVELOPMENT_ADMIN_HOSTS or os.environ.get("BMS_RUNTIME_MODE") != "dev":
        raise HTTPException(status_code=403, detail="alignment capability rotation is limited to local Development")
    configured = urlsplit(os.environ.get("BMS_FRONTEND_HEALTH_URL", ""))
    supplied = urlsplit(request.headers.get("origin", ""))
    if (
        request.headers.get("sec-fetch-site", "").lower() != "same-origin"
        or configured.scheme not in {"http", "https"}
        or not configured.netloc
        or (supplied.scheme, supplied.netloc) != (configured.scheme, configured.netloc)
    ):
        raise HTTPException(status_code=403, detail="same-origin Development browser authorization is required")


@router.post("/jobs/{job_id}/alignment-access/rotate")
async def rotate_alignment_access(
    job_id: str,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    _require_local_development_browser(request)
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.model_id != "nanopore" or job.status != "completed":
        raise HTTPException(status_code=409, detail="alignment access can rotate only for completed nanopore jobs")
    try:
        sessions = await run_in_threadpool(
            service.build_alignment_sessions,
            job_id,
            **_job_authority(job),
            job_output_dir=_job_output_dir(job),
        )
    except service.AlignmentSessionError as exc:
        raise _http_error(exc) from exc
    if not any(item.get("mode") == "primary" and item.get("ready") is True for item in sessions):
        raise HTTPException(status_code=409, detail="no ready primary alignment session is available")
    previous = job.provenance if isinstance(job.provenance, dict) else {}
    previous_digest = previous.get(alignment_access.PROVENANCE_DIGEST_KEY)
    if not isinstance(previous_digest, str):
        raise HTTPException(status_code=409, detail="persisted alignment capability authority is missing")
    token, token_digest = alignment_access.issue_alignment_access_token()
    rotation_count = int(previous.get("alignment_access_rotation_count") or 0) + 1
    updated = {
        **previous,
        alignment_access.PROVENANCE_DIGEST_KEY: token_digest,
        alignment_access.PROVENANCE_SCHEME_KEY: alignment_access.SCHEME,
        "alignment_access_rotation_count": rotation_count,
    }
    changed = await session.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.status == "completed",
            Job.model_id == "nanopore",
            Job.provenance[alignment_access.PROVENANCE_DIGEST_KEY].as_string() == previous_digest,
        )
        .values(provenance=updated)
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        await session.rollback()
        raise HTTPException(status_code=409, detail="alignment capability authority changed concurrently")
    await session.commit()
    alignment_access.set_alignment_access_cookie(job_id, token, response, request)
    return {
        "job_id": job_id,
        "rotated": True,
        "scheme": alignment_access.SCHEME,
        "rotation_count": rotation_count,
    }


def _parse_range(value: str, size: int) -> tuple[int, int]:
    if not value.startswith("bytes=") or "," in value or size <= 0:
        raise ValueError("invalid range")
    raw = value[6:]
    if "-" not in raw:
        raise ValueError("invalid range")
    start_raw, end_raw = raw.split("-", 1)
    if not start_raw:
        suffix = int(end_raw)
        if suffix <= 0:
            raise ValueError("invalid suffix")
        return max(0, size - suffix), size - 1
    start = int(start_raw)
    end = size - 1 if not end_raw else int(end_raw)
    if start < 0 or start >= size or end < start:
        raise ValueError("unsatisfiable range")
    return start, min(end, size - 1)


def _iter_range(
    snapshot: BinaryIO,
    start: int,
    end: int,
    chunk_size: int = 1024 * 1024,
) -> Iterator[bytes]:
    remaining = end - start + 1
    try:
        snapshot.seek(start)
        while remaining:
            chunk = snapshot.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        snapshot.close()


async def _serve_artifact(path: Path, metadata: dict, request: Request) -> Response:
    size = int(metadata["size_bytes"])
    digest = str(metadata["sha256"])
    etag = f'"{digest}"'
    base_headers = {
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Cache-Control": "private, no-cache, must-revalidate",
        "X-Content-Type-Options": "nosniff",
    }
    if metadata.get("role") == "report":
        base_headers["Content-Disposition"] = f'attachment; filename="{path.name}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=base_headers)
    range_header = request.headers.get("range")
    if range_header:
        try:
            start, end = _parse_range(range_header, size)
        except (TypeError, ValueError):
            return Response(status_code=416, headers={**base_headers, "Content-Range": f"bytes */{size}"})
        status_code = 206
        headers = {
            **base_headers,
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(end - start + 1),
        }
    else:
        start, end = 0, size - 1
        status_code = 200
        headers = {**base_headers, "Content-Length": str(size)}
    snapshot = await run_in_threadpool(
        service.open_verified_artifact_snapshot,
        path,
        expected_size=size,
        expected_sha256=digest,
    )
    return StreamingResponse(
        _iter_range(snapshot, start, end),
        status_code=status_code,
        headers=headers,
        media_type=str(metadata["mime_type"]),
    )


@router.get("/jobs/{job_id}/alignment-sessions")
async def list_alignment_sessions(
    job_id: str,
    authorized_job: Job = Depends(require_alignment_job),
):
    try:
        sessions = await run_in_threadpool(
            service.build_alignment_sessions,
            job_id,
            **_job_authority(authorized_job),
            job_output_dir=_job_output_dir(authorized_job),
        )
        return {
            "job_id": job_id,
            "sessions": sessions,
        }
    except service.AlignmentSessionError as exc:
        raise _http_error(exc) from exc


@router.get("/jobs/{job_id}/sequence-qc-manifest")
async def get_job_scoped_sequence_qc_manifest(
    job_id: str,
    authorized_job: Job = Depends(require_alignment_job),
):
    try:
        result_root = resolve_persisted_job_result_root(authorized_job)
        manifest_path = find_manifest_in_result_root(result_root)
        _manifest_document, manifest_bytes, _manifest_digest, _manifest_size = service._read_bounded_json_nofollow(
            manifest_path,
            label="job-scoped sequence-QC manifest",
        )
        authority = _job_authority(authorized_job)
        return load_sequence_qc_manifest(
            manifest_path,
            raw_bytes=manifest_bytes,
            expected_job_id=job_id,
            expected_workflow_id=authority["workflow_id"],
            expected_input_mode=authority["input_mode"],
            expected_analysis_status="completed",
        )
    except service.AlignmentSessionError as exc:
        raise _http_error(exc) from exc
    except (JobResultRootError, SequenceQcManifestError) as exc:
        raise _http_error(service.AlignmentSessionError(str(exc))) from exc


@router.get("/jobs/{job_id}/alignment-sessions/{session_id}")
async def get_alignment_session(
    job_id: str,
    session_id: str,
    authorized_job: Job = Depends(require_alignment_job),
):
    try:
        return await run_in_threadpool(
            service.resolve_alignment_session,
            job_id,
            session_id,
            **_job_authority(authorized_job),
            job_output_dir=_job_output_dir(authorized_job),
        )
    except service.AlignmentSessionError as exc:
        raise _http_error(exc) from exc


@router.get("/jobs/{job_id}/ngs-artifacts")
async def list_ngs_package_artifacts(
    job_id: str,
    authorized_job: Job = Depends(require_alignment_job),
):
    try:
        artifacts = await run_in_threadpool(
            service.build_ngs_package_artifacts,
            job_id,
            **_job_package_authority(authorized_job),
            job_output_dir=_job_output_dir(authorized_job),
        )
        return {"job_id": job_id, "artifacts": artifacts}
    except service.AlignmentSessionError as exc:
        raise _http_error(exc) from exc


@router.get("/jobs/{job_id}/ngs-artifacts/{sha256}")
async def get_ngs_package_artifact(
    job_id: str,
    sha256: str,
    request: Request,
    authorized_job: Job = Depends(require_alignment_job),
):
    try:
        path, metadata = await run_in_threadpool(
            service.resolve_ngs_package_artifact,
            job_id,
            sha256,
            **_job_package_authority(authorized_job),
            job_output_dir=_job_output_dir(authorized_job),
        )
        return await _serve_artifact(path, metadata, request)
    except service.AlignmentSessionError as exc:
        raise _http_error(exc) from exc


@router.get("/jobs/{job_id}/alignment-artifacts/{artifact_id}")
async def get_alignment_artifact(
    job_id: str,
    artifact_id: str,
    request: Request,
    authorized_job: Job = Depends(require_alignment_job),
):
    try:
        path, metadata = await run_in_threadpool(
            service._resolve_internal_artifact,
            job_id,
            artifact_id,
            **_job_authority(authorized_job),
            job_output_dir=_job_output_dir(authorized_job),
        )
        return await _serve_artifact(path, metadata, request)
    except service.AlignmentSessionError as exc:
        raise _http_error(exc) from exc


@router.get("/jobs/{job_id}/alignment-session-artifacts/{mode}/{role}/{sha256}")
async def get_alignment_session_artifact(
    job_id: str,
    mode: str,
    role: str,
    sha256: str,
    request: Request,
    authorized_job: Job = Depends(require_alignment_job),
):
    try:
        path, metadata = await run_in_threadpool(
            service.resolve_alignment_artifact_by_role,
            job_id,
            mode,
            role,
            sha256,
            **_job_authority(authorized_job),
            job_output_dir=_job_output_dir(authorized_job),
        )
        return await _serve_artifact(path, metadata, request)
    except service.AlignmentSessionError as exc:
        raise _http_error(exc) from exc


@router.get("/jobs/{job_id}/reads")
async def list_alignment_reads(
    job_id: str,
    session_id: str = Query(...),
    contig: str | None = Query(default=None),
    start: int | None = Query(default=None, ge=1),
    end: int | None = Query(default=None, ge=1),
    q: str | None = Query(default=None, max_length=255),
    cursor: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=50, ge=1, le=service.MAX_READ_PAGE),
    include_sequence: bool = Query(default=False),
    authorized_job: Job = Depends(require_alignment_job),
):
    try:
        bam, bam_metadata, index, index_metadata = await run_in_threadpool(
            service.resolve_session_alignment_bundle,
            job_id,
            session_id,
            **_job_authority(authorized_job),
            job_output_dir=_job_output_dir(authorized_job),
        )
        return await run_in_threadpool(
            service.read_bam_page,
            bam,
            bam_sha256=bam_metadata["sha256"],
            bam_size_bytes=bam_metadata["size_bytes"],
            index=index,
            index_sha256=index_metadata["sha256"],
            index_size_bytes=index_metadata["size_bytes"],
            contig=contig,
            start=start,
            end=end,
            q=q,
            cursor=cursor,
            limit=limit,
            include_sequence=include_sequence,
        )
    except service.AlignmentSessionError as exc:
        raise _http_error(exc) from exc


@router.get("/jobs/{job_id}/reads/{read_id}")
async def get_alignment_read(
    job_id: str,
    read_id: str,
    session_id: str = Query(...),
    contig: str | None = Query(default=None),
    start: int | None = Query(default=None, ge=1),
    end: int | None = Query(default=None, ge=1),
    authorized_job: Job = Depends(require_alignment_job),
):
    if not read_id or len(read_id) > 255:
        raise HTTPException(status_code=400, detail="invalid read ID")
    try:
        bam, bam_metadata, index, index_metadata = await run_in_threadpool(
            service.resolve_session_alignment_bundle,
            job_id,
            session_id,
            **_job_authority(authorized_job),
            job_output_dir=_job_output_dir(authorized_job),
        )
        payload = await run_in_threadpool(
            service.read_bam_exact,
            bam,
            read_id,
            bam_sha256=bam_metadata["sha256"],
            bam_size_bytes=bam_metadata["size_bytes"],
            index=index,
            index_sha256=index_metadata["sha256"],
            index_size_bytes=index_metadata["size_bytes"],
            contig=contig,
            start=start,
            end=end,
        )
    except service.AlignmentSessionError as exc:
        raise _http_error(exc) from exc
    if payload["read"] is not None:
        return JSONResponse(payload["read"])
    if payload["scan_truncated"]:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "exact read lookup scan budget exhausted; absence is not proven",
                "scan_truncated": True,
            },
        )
    raise HTTPException(status_code=404, detail="read not found")
