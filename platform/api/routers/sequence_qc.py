"""Sequence-QC artifact manifest API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from database import Job
from routers.ngs_alignment_sessions import require_alignment_job

from services.job_result_roots import JobResultRootError, resolve_persisted_job_result_root
from services.sequence_qc_manifest import (
    SequenceQcManifestError,
    find_manifest_in_result_root,
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
async def get_sequence_qc_manifest_for_job(
    job_id: str,
    job: Job = Depends(require_alignment_job),
):
    """Return the typed sequence-QC manifest for a completed BMS job."""

    try:
        result_root = resolve_persisted_job_result_root(job)
        manifest_path = find_manifest_in_result_root(result_root)
        params = job.params if isinstance(job.params, dict) else {}
        return load_sequence_qc_manifest(
            manifest_path,
            expected_job_id=job.id,
            expected_workflow_id=str(params.get("ont_workflow_id") or params.get("workflow_id") or ""),
            expected_input_mode=str(params.get("ont_input_mode") or params.get("input_mode") or ""),
            expected_analysis_status="completed",
        )
    except (JobResultRootError, SequenceQcManifestError) as exc:
        raise _error_to_http(exc) from exc
