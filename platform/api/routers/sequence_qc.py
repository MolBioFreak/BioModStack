"""Sequence-QC artifact manifest API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from database import Job
from routers.ngs_alignment_sessions import (
    OntNgsErrorV1,
    OntNgsRouteError,
    _validated_pinned_result_root,
    require_alignment_job,
)

from services.job_result_roots import JobResultRootError
from services.ngs_alignment_sessions import AlignmentSessionError
from services.sequence_qc_manifest import (
    SequenceQcManifestError,
    find_canonical_fastq_manifest,
    find_manifest_in_result_root,
    load_sequence_qc_manifest,
    read_manifest_json_nofollow,
)

router = APIRouter()


def _error_to_http(exc: Exception, *, job_id: str) -> OntNgsRouteError:
    message = str(exc).lower()
    if "not found" in message:
        return OntNgsRouteError(
            status_code=404,
            code="NGS_RESOURCE_NOT_FOUND",
            message="The governed manifest was not found.",
            job_id=job_id,
            resource="manifest",
        )
    return OntNgsRouteError(
        status_code=409,
        code="NGS_PACKAGE_INTEGRITY_CONFLICT",
        message="The governed result package failed integrity validation.",
        job_id=job_id,
        resource="manifest",
    )


@router.get(
    "/jobs/{job_id}/manifest",
    responses={
        status: {"model": OntNgsErrorV1, "description": "Typed governed NGS failure"}
        for status in (403, 404, 409)
    },
)
async def get_sequence_qc_manifest_for_job(
    job_id: str,
    job: Job = Depends(require_alignment_job),
):
    """Return the typed sequence-QC manifest for a completed BMS job."""

    try:
        params = job.params if isinstance(job.params, dict) else {}
        workflow_id = str(
            params.get("ont_workflow_id")
            or params.get("ont_request_workflow_id")
            or params.get("workflow_id")
            or ""
        )
        async with _validated_pinned_result_root(job) as result_root:
            manifest_path = (
                find_canonical_fastq_manifest(result_root, pinned_root_descriptor=True)
                if workflow_id == "ont_fastq_qc"
                else find_manifest_in_result_root(result_root, pinned_root_descriptor=True)
            )
            _manifest_document, manifest_bytes, _manifest_digest, _manifest_size = read_manifest_json_nofollow(
                manifest_path,
                pinned_root_descriptor=result_root,
            )
            return load_sequence_qc_manifest(
                manifest_path,
                raw_bytes=manifest_bytes,
                expected_job_id=job.id,
                expected_workflow_id=workflow_id,
                expected_input_mode=str(params.get("ont_input_mode") or params.get("input_mode") or ""),
                expected_analysis_status="completed",
            )
    except (JobResultRootError, SequenceQcManifestError, AlignmentSessionError) as exc:
        raise _error_to_http(exc, job_id=job_id) from exc
