"""Sequence-QC artifact manifest API routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from paths import resolve_allowed_path
from services.sequence_qc_manifest import (
    SequenceQcManifestError,
    find_manifest_for_job,
    load_sequence_qc_manifest,
)

router = APIRouter()


def _error_to_http(exc: SequenceQcManifestError) -> HTTPException:
    message = str(exc)
    status_code = 404 if "not found" in message else 400
    if "unsafe" in message or "escapes" in message:
        status_code = 403
    return HTTPException(status_code=status_code, detail=message)


@router.get("/jobs/{job_id}/manifest")
async def get_sequence_qc_manifest_for_job(job_id: str):
    """Return the typed sequence-QC manifest for a completed BMS job."""
    try:
        manifest_path = find_manifest_for_job(job_id)
        return load_sequence_qc_manifest(manifest_path)
    except SequenceQcManifestError as exc:
        raise _error_to_http(exc) from exc


@router.get("/manifest")
async def get_sequence_qc_manifest_by_path(
    path: str = Query(..., description="Allowed-root relative manifest path, e.g. bms_results/job/fastq_qc/qc_manifest.json"),
):
    """Return a typed sequence-QC manifest by an allowed-root relative path."""
    try:
        manifest_path = resolve_allowed_path(path)
        if Path(manifest_path).name != "qc_manifest.json":
            raise SequenceQcManifestError("path must point to qc_manifest.json")
        return load_sequence_qc_manifest(manifest_path)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SequenceQcManifestError as exc:
        raise _error_to_http(exc) from exc
