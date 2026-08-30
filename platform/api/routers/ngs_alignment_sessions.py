"""Job-scoped ONT alignment viewer, artifact streaming, and read-inspection routes."""

from __future__ import annotations

import os
import re
import stat
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from database import Job, get_session
from experiment_database import get_experiment_session
from molbio_ngs_database import get_molbio_ngs_session
from routers.experiment_workspaces import _authenticated_principal, _require_mutation_owner
from ont_ngs_result_response import OntFastqQcResultResponse
from services import alignment_access
from services import ngs_alignment_sessions as service
from services.ont_ngs_completion import (
    OntNgsCompletionError,
    canonical_ngs_package_authority,
    is_ont_fastq_qc_job,
    is_ont_signal_alignment_job,
)
from services.ont_ngs_results import (
    OntNgsResultError,
    _build_file_projection_from_pinned_root,
    build_ont_fastq_qc_result,
)
from services.ont_ngs_hierarchy import (
    PROVENANCE_HIERARCHY_KEY,
    OntNgsHierarchyError,
    capability_hierarchy_matches,
    hierarchy_authority_record,
    resolve_ont_ngs_hierarchy_authority,
)
from services.job_result_roots import JobResultRootError, resolve_persisted_job_result_root
from services.sequence_qc_manifest import (
    SequenceQcManifestError,
    find_canonical_fastq_manifest as _find_canonical_fastq_manifest,
    find_manifest_in_result_root as find_generic_manifest_in_result_root,
    load_sequence_qc_manifest,
)

router = APIRouter()


class OntNgsErrorV1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "allOf": [{
                "if": {"properties": {"code": {"enum": ["NGS_CAPABILITY_DENIED", "NGS_CAPABILITY_ROTATION_CONFLICT"]}}, "required": ["code"]},
                "then": {"properties": {"retryable": {"const": True}}, "required": ["retryable"]},
                "else": {"properties": {"retryable": {"const": False}}, "required": ["retryable"]},
            }],
        },
    )
    schema_version: Literal["bms.ngs.error.v1"] = Field(alias="schema")
    code: Literal[
        "NGS_CAPABILITY_DENIED", "NGS_HIERARCHY_DENIED", "NGS_PRINCIPAL_DENIED",
        "NGS_ROTATION_ORIGIN_DENIED", "NGS_RESOURCE_NOT_FOUND", "NGS_AUTHORITY_CONFLICT",
        "NGS_PACKAGE_INTEGRITY_CONFLICT", "NGS_CAPABILITY_ROTATION_CONFLICT",
        "NGS_ROTATION_INELIGIBLE", "NGS_ARTIFACT_INTEGRITY_CONFLICT",
        "NGS_READ_SCAN_TRUNCATED", "NGS_RANGE_INVALID", "NGS_RANGE_UNSATISFIABLE",
    ]
    message: str = Field(min_length=1, max_length=512)
    job_id: str = Field(json_schema_extra={"format": "uuid"})
    resource: Literal["result", "manifest", "session", "artifact", "range", "rotation", "read"]
    retryable: bool

    @model_validator(mode="after")
    def _retryable_matches_code(self):
        expected = self.code in {"NGS_CAPABILITY_DENIED", "NGS_CAPABILITY_ROTATION_CONFLICT"}
        if self.retryable is not expected:
            raise ValueError("retryable disagrees with governed NGS error code")
        return self


class OntFastqQcResultV1(OntFastqQcResultResponse):
    pass


class OntAlignmentArtifactV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str
    url: str
    sha256: str
    size_bytes: int
    mime_type: str
    range_capable: Literal[True]
    source_manifest_sha256: str


class OntAlignmentArtifactsV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alignment: OntAlignmentArtifactV1
    alignment_index: OntAlignmentArtifactV1
    coverage_depth: OntAlignmentArtifactV1 | None = None
    gc_content: OntAlignmentArtifactV1 | None = None
    gc_zscore: OntAlignmentArtifactV1 | None = None
    junction_hotspots: OntAlignmentArtifactV1 | None = None
    position_gradient: OntAlignmentArtifactV1 | None = None
    reference: OntAlignmentArtifactV1
    reference_index: OntAlignmentArtifactV1
    report: OntAlignmentArtifactV1 | None = None
    soft_clip_density: OntAlignmentArtifactV1 | None = None
    split_read_density: OntAlignmentArtifactV1 | None = None
    track_config: OntAlignmentArtifactV1 | None = None


class OntAlignmentReferenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contig: str
    length_bp: int
    topology: Literal["linear", "circular"]
    normalized_sequence_sha256: str
    fasta_sha256: str
    fai_sha256: str


class OntEmptyAlignmentArtifactsV1(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OntReadyAlignmentSessionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.alignment-session.v1"] = Field(alias="schema")
    session_id: str
    job_id: str
    mode: Literal["primary", "dimer_candidates"]
    ready: Literal[True]
    unavailable_reason: None
    reads_url: str
    sequence_qc_manifest_sha256: str
    verification_manifest_sha256: str
    artifact_set_sha256: str
    reference: OntAlignmentReferenceV1
    artifacts: OntAlignmentArtifactsV1
    alignment_pair_sha256: str


class OntUnavailableAlignmentSessionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.alignment-session.v1"] = Field(alias="schema")
    session_id: str
    job_id: str
    mode: Literal["dimer_candidates"]
    ready: Literal[False]
    unavailable_reason: str
    reads_url: None
    sequence_qc_manifest_sha256: None
    verification_manifest_sha256: None
    artifact_set_sha256: None
    reference: None
    artifacts: OntEmptyAlignmentArtifactsV1
    alignment_pair_sha256: None


class OntAlignmentSessionV1(RootModel[OntReadyAlignmentSessionV1 | OntUnavailableAlignmentSessionV1]):
    pass


class OntAlignmentSessionListV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.alignment-session-list.v1"] = Field(alias="schema")
    job_id: str = Field(json_schema_extra={"format": "uuid"})
    sessions: list[OntAlignmentSessionV1] = Field(
        min_length=1,
        max_length=2,
        json_schema_extra={
            "items": False,
            "prefixItems": [
                {
                    "allOf": [
                        {"$ref": "#/components/schemas/OntReadyAlignmentSessionV1"},
                        {"properties": {"mode": {"const": "primary"}, "ready": {"const": True}}, "required": ["mode", "ready"]},
                    ],
                },
                {
                    "allOf": [
                        {"$ref": "#/components/schemas/OntAlignmentSessionV1"},
                        {"properties": {"mode": {"const": "dimer_candidates"}}, "required": ["mode"]},
                    ],
                },
            ],
        },
    )

    @model_validator(mode="after")
    def _closed_session_order(self):
        first = self.sessions[0].root
        if first.mode != "primary" or first.ready is not True:
            raise ValueError("the first alignment session must be a ready primary session")
        if len(self.sessions) == 2 and self.sessions[1].root.mode != "dimer_candidates":
            raise ValueError("the optional second alignment session must be dimer candidates")
        return self


class OntAlignmentSessionDetailV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.alignment-session-detail.v1"] = Field(alias="schema")
    job_id: str
    session: OntAlignmentSessionV1


class OntNgsRotationSuccessV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.rotation-success.v1"] = Field(alias="schema")
    job_id: str
    rotated: Literal[True]
    scheme: Literal["opaque_job_capability_v1"]
    rotation_count: int
    expires_at: datetime


class OntNgsCapabilityRevocationSuccessV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.capability-revocation-success.v1"] = Field(alias="schema")
    job_id: str
    revoked: Literal[True]
    scheme: Literal["opaque_job_capability_v1"]


class BinaryArtifactResponse(RootModel[bytes]):
    pass


class OntDerivedArtifactV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    url: str
    sha256: str
    size_bytes: int
    mime_type: str
    range_capable: Literal[True]


class OntPresentationSourceV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_manifest_sha256: str
    alignment_sha256: str
    alignment_size_bytes: int
    alignment_index_sha256: str
    alignment_index_size_bytes: int
    primary_read_count: int
    alignment_record_count: int


class OntPresentationPolicyV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    version: int
    target_reads: int
    max_preview_bytes: int
    max_coverage_bins: int
    max_seconds: float


class OntPresentationPreviewV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["primary_read_preview"]
    selected_read_count: int
    selected_record_count: int
    selected_read_set_sha256: str
    forward_count: int
    reverse_count: int
    bam: OntDerivedArtifactV1
    index: OntDerivedArtifactV1


class OntPresentationCoverageV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["full_source_primary_coverage"]
    bin_width_bp: int
    primary_read_count: int
    artifact: OntDerivedArtifactV1


class OntAlignmentPresentationV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.alignment-presentation.v1"] = Field(alias="schema")
    job_id: str
    session_id: str
    mode: Literal["primary", "dimer_candidates"]
    state: Literal["ready"]
    source: OntPresentationSourceV1
    policy: OntPresentationPolicyV1
    preview: OntPresentationPreviewV1
    coverage: OntPresentationCoverageV1
    manifest: OntDerivedArtifactV1


class OntAlignmentLocusSliceRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contig: str
    start_1based: int
    end_1based: int
    max_reads: int


class OntAlignmentLocusPolicyV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Literal["bounded-full-source-locus-slice"]
    version: Literal[1]
    max_reads: int
    max_records: int
    max_bytes: int
    max_span_bp: int
    max_seconds: float


class OntAlignmentLocusSliceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.alignment-locus-slice.v1"] = Field(alias="schema")
    job_id: str
    session_id: str
    slice_id: str
    state: Literal["ready"]
    contig: str
    start_1based: int
    end_1based: int
    overlapping_read_count: int
    selected_read_count: int
    selected_record_count: int
    capped: bool
    policy: OntAlignmentLocusPolicyV1
    bam: OntDerivedArtifactV1
    index: OntDerivedArtifactV1
    manifest: OntDerivedArtifactV1


def _typed_errors(*statuses: int) -> dict[int | str, dict[str, Any]]:
    return {
        status: {"model": OntNgsErrorV1, "description": "Typed governed NGS failure"}
        for status in statuses
    }


_STANDARD_GOVERNED_ERRORS = _typed_errors(403, 404, 409)
_READ_ERRORS = _typed_errors(400, 403, 404, 409)
_BINARY_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {"model": BinaryArtifactResponse, "description": "Complete immutable artifact"},
    206: {
        "model": BinaryArtifactResponse,
        "description": "Immutable artifact byte range",
        "headers": {"Content-Range": {"schema": {"type": "string"}}},
    },
    304: {"description": "Not modified"},
    **_typed_errors(400, 403, 404, 409),
    416: {
        "model": OntNgsErrorV1,
        "description": "Typed governed NGS range failure",
        "headers": {"Content-Range": {"schema": {"type": "string"}}},
    },
}

_GOVERNED_OPENAPI_SUFFIXES = (
    "/alignment-access/rotate",
    "/alignment-access",
    "/alignment-sessions",
    "/ngs-result",
    "/ngs-artifacts",
    "/alignment-artifacts",
    "/alignment-session-artifacts",
    "/preview/{kind}",
    "/presentation",
    "/presentation/{kind}",
    "/locus-slices",
    "/locus-slices/{slice_id}/{artifact_sha256}/{kind}",
    "/reads",
    "/sequence-qc-manifest",
    "/manifest",
)


def install_governed_ngs_openapi(app: Any) -> None:
    """Remove FastAPI's generic 422 from the closed governed-route contract."""

    original_openapi = app.openapi

    def governed_openapi() -> dict[str, Any]:
        document = original_openapi()
        for path, path_item in document.get("paths", {}).items():
            if "/jobs/{job_id}/" not in path or not any(marker in path for marker in _GOVERNED_OPENAPI_SUFFIXES):
                continue
            for method in ("get", "head", "post", "delete"):
                operation = path_item.get(method)
                if isinstance(operation, dict):
                    operation.get("responses", {}).pop("422", None)
        return document

    app.openapi = governed_openapi

_CLOSED_ARTIFACT_EXTENSIONS = (
    "fastq.gz", "vcf.gz", "bedgraph", "fasta", "fastq", "json", "html", "csv", "tsv",
    "bam", "bai", "fai", "vcf", "bed", "log", "txt", "gz", "bin",
)
_INLINE_ARTIFACT_EXTENSIONS = frozenset({"bam", "bai", "fasta", "fai"})


class _InvalidRange(ValueError):
    pass


class _UnsatisfiableRange(ValueError):
    pass


class OntNgsRouteError(Exception):
    def __init__(
        self, *, status_code: int, code: str, message: str, job_id: str, resource: str,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.job_id = job_id
        self.resource = resource
        self.headers = headers


def _ngs_error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    job_id: str,
    resource: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "schema": "bms.ngs.error.v1",
            "code": code,
            "message": message,
            "job_id": job_id,
            "resource": resource,
            "retryable": code in {"NGS_CAPABILITY_DENIED", "NGS_CAPABILITY_ROTATION_CONFLICT"},
        },
        headers=headers,
    )


def _artifact_content_disposition(path: Path, metadata: dict, digest: str) -> str:
    raw_kind = str(metadata.get("kind") or "artifact").lower()
    kind = raw_kind if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", raw_kind) else "artifact"
    declared_extension = metadata.get("filename_extension")
    if isinstance(declared_extension, str) and declared_extension in _CLOSED_ARTIFACT_EXTENSIONS:
        extension = declared_extension
    else:
        lower_name = path.name.lower()
        extension = next(
            (candidate for candidate in _CLOSED_ARTIFACT_EXTENSIONS if lower_name.endswith(f".{candidate}")),
            "bin",
        )
    declared_disposition = metadata.get("content_disposition")
    if declared_disposition in {"inline", "attachment"}:
        disposition = str(declared_disposition)
    else:
        disposition = "inline" if extension in _INLINE_ARTIFACT_EXTENSIONS else "attachment"
    return f'{disposition}; filename="{kind}-{digest[:12]}.{extension}"'


async def ont_ngs_route_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, OntNgsRouteError):
        raise exc
    return _ngs_error_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        job_id=exc.job_id,
        resource=exc.resource,
        headers=exc.headers,
    )


async def _require_governed_project_principal(
    request: Request,
    experiment_session: AsyncSession,
    project_id: str,
    job_id: str,
) -> str:
    try:
        actor, roles = _authenticated_principal(request)
        if roles.intersection({"operator", "admin"}):
            return actor
        return await _require_mutation_owner(
            request,
            experiment_session,
            resource_id=project_id,
        )
    except HTTPException as exc:
        raise OntNgsRouteError(
            status_code=403,
            code="NGS_PRINCIPAL_DENIED",
            message="Project operator authority is required.",
            job_id=job_id,
            resource="result",
        ) from exc


def _requires_governed_ont_hierarchy(job: Job) -> bool:
    try:
        return is_ont_fastq_qc_job(job)
    except OntNgsCompletionError as exc:
        raise OntNgsRouteError(
            status_code=409,
            code="NGS_AUTHORITY_CONFLICT",
            message="The persisted NGS authority is inconsistent.",
            job_id=str(job.id),
            resource="result",
        ) from exc


LOCAL_DEVELOPMENT_ADMIN_HOSTS = frozenset({"127.0.0.1", "::1"})


async def require_alignment_job(
    job_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    domain_session: AsyncSession = Depends(get_molbio_ngs_session),
    experiment_session: AsyncSession = Depends(get_experiment_session),
) -> Job:
    """Require capability, hierarchy, principal, and persisted package authority."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise OntNgsRouteError(
            status_code=404,
            code="NGS_RESOURCE_NOT_FOUND",
            message="The governed NGS Job was not found.",
            job_id=job_id,
            resource="result",
        )
    provenance = job.provenance if isinstance(job.provenance, dict) else {}
    token = alignment_access.request_alignment_token(request, job_id)
    if not alignment_access.capability_matches(
        token,
        provenance.get(alignment_access.PROVENANCE_DIGEST_KEY),
    ):
        raise OntNgsRouteError(
            status_code=403,
            code="NGS_CAPABILITY_DENIED",
            message="The Job-scoped NGS capability is invalid.",
            job_id=job_id,
            resource="result",
        )
    canonical_fastq = _requires_governed_ont_hierarchy(job)
    if canonical_fastq:
        try:
            hierarchy = await resolve_ont_ngs_hierarchy_authority(
                job,
                domain_session,
                experiment_session,
            )
        except OntNgsHierarchyError as exc:
            raise OntNgsRouteError(
                status_code=403,
                code="NGS_HIERARCHY_DENIED",
                message="The frozen NGS hierarchy is unavailable.",
                job_id=job_id,
                resource="result",
            ) from exc
        if not capability_hierarchy_matches(job, hierarchy):
            raise OntNgsRouteError(
                status_code=403,
                code="NGS_HIERARCHY_DENIED",
                message="The NGS capability does not match the frozen hierarchy.",
                job_id=job_id,
                resource="result",
            )
        await _require_governed_project_principal(
            request,
            experiment_session,
            hierarchy.project_id,
            job_id,
        )
        try:
            result_projection = await build_ont_fastq_qc_result(job)
        except (JobResultRootError, SequenceQcManifestError, OntNgsResultError, service.AlignmentSessionError) as exc:
            raise OntNgsRouteError(
                status_code=409,
                code="NGS_PACKAGE_INTEGRITY_CONFLICT",
                message="The current NGS package differs from persisted authority.",
                job_id=job_id,
                resource="result",
            ) from exc
        request.state.ont_fastq_qc_result = result_projection
    return job


def _http_error(
    exc: service.AlignmentSessionError,
    *,
    job_id: str,
    resource: str,
) -> OntNgsRouteError:
    message = str(exc).lower()
    if "not found" in message:
        return OntNgsRouteError(
            status_code=404,
            code="NGS_RESOURCE_NOT_FOUND",
            message="The governed NGS resource was not found.",
            job_id=job_id,
            resource=resource,
        )
    if any(term in message for term in ("digest", "size", "inode", "changed", "integrity")):
        return OntNgsRouteError(
            status_code=409,
            code="NGS_ARTIFACT_INTEGRITY_CONFLICT",
            message="The governed artifact changed before delivery.",
            job_id=job_id,
            resource="artifact",
        )
    return OntNgsRouteError(
        status_code=409,
        code="NGS_AUTHORITY_CONFLICT",
        message="The persisted NGS authority is inconsistent.",
        job_id=job_id,
        resource=resource,
    )


def _job_output_dir(job: Job) -> str | None:
    return getattr(job, "child_output_dir", None) or job.output_dir


@asynccontextmanager
async def _validated_pinned_result_root(job: Job):
    try:
        persisted_root = (
            resolve_persisted_job_result_root(job)
            if isinstance(job, Job)
            else _job_output_dir(job)
        )
        if persisted_root is None:
            raise JobResultRootError("persisted NGS result root is unavailable")
        if not isinstance(job, Job) and not Path(persisted_root).is_dir():
            yield Path(persisted_root)
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(persisted_root, flags)
    except (OSError, JobResultRootError) as exc:
        raise service.AlignmentSessionError("persisted NGS result root is unavailable") from exc
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise service.AlignmentSessionError("persisted result root is not a directory")
        pinned_root = Path(f"/proc/self/fd/{descriptor}")
        if isinstance(job, Job):
            try:
                if is_ont_signal_alignment_job(job):
                    package_authority = _job_package_authority(job)
                    descriptors = await run_in_threadpool(
                        service.build_ngs_package_artifacts,
                        str(job.id),
                        **package_authority,
                        job_output_dir=pinned_root,
                        pinned_root_descriptor=True,
                    )
                    observed_authority = canonical_ngs_package_authority(descriptors)
                    provenance = job.provenance if isinstance(job.provenance, dict) else {}
                    integrity = provenance.get("result_integrity")
                    if not isinstance(integrity, dict) or any(
                        integrity.get(field) != observed_authority[field]
                        for field in (
                            "artifact_set_sha256",
                            "declared_artifact_count",
                            "present_artifact_count",
                            "unavailable_artifact_count",
                        )
                    ):
                        raise service.AlignmentSessionError(
                            "current signal-alignment package differs from persisted authority"
                        )
                else:
                    await run_in_threadpool(_build_file_projection_from_pinned_root, job, pinned_root)
            except (JobResultRootError, SequenceQcManifestError, OntNgsResultError, service.AlignmentSessionError) as exc:
                raise service.AlignmentSessionError("current NGS package differs from persisted authority") from exc
        yield pinned_root
    finally:
        os.close(descriptor)


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


def _job_session_authority(job: Job) -> dict[str, Any]:
    authority = _job_authority(job)
    provenance = getattr(job, "provenance", None)
    provenance = provenance if isinstance(provenance, dict) else {}
    integrity = provenance.get("result_integrity")
    package_digest = integrity.get("artifact_set_sha256") if isinstance(integrity, dict) else None
    if not isinstance(package_digest, str) or re.fullmatch(r"[0-9a-f]{64}", package_digest) is None:
        reconciliation = provenance.get("ont_fastq_qc_reconciliation_v1")
        if not (
            isinstance(reconciliation, dict)
            and reconciliation.get("schema") == "bms.ont-fastq-qc-reconciliation.v1"
            and reconciliation.get("job_id") == str(job.id)
            and reconciliation.get("workflow_id") == authority["workflow_id"]
            and reconciliation.get("input_mode") == authority["input_mode"]
        ):
            raise service.AlignmentSessionError("persisted package artifact-set authority is required")
        package_digest = reconciliation.get("artifact_set_sha256")
    if not isinstance(package_digest, str) or re.fullmatch(r"[0-9a-f]{64}", package_digest) is None:
        raise service.AlignmentSessionError("persisted package artifact-set authority is required")
    return {**authority, "package_artifact_set_sha256": package_digest}


def _job_package_authority(job: Job) -> dict[str, str]:
    authority = _job_authority(job)
    params = getattr(job, "params", None)
    params = params if isinstance(params, dict) else {}
    source_key = {"fastq": "fastq_path", "bam": "bam_path", "pod5": "pod5_dir"}.get(authority["input_mode"])
    source_path = params.get(source_key) if source_key is not None else None
    if not isinstance(source_path, str) or not source_path.strip():
        raise service.AlignmentSessionError("authorized source input path is required")
    return {**authority, "source_input_path": source_path}


async def _validate_rotation_package_authority(job: Job) -> None:
    if is_ont_signal_alignment_job(job):
        async with _validated_pinned_result_root(job):
            return
    await build_ont_fastq_qc_result(job)


def _require_local_development_browser(request: Request, job_id: str) -> None:
    client_host = request.client.host if request.client is not None else None
    secure_transport = alignment_access.secure_alignment_transport(request)
    if (
        not secure_transport
        and (client_host not in LOCAL_DEVELOPMENT_ADMIN_HOSTS or os.environ.get("BMS_RUNTIME_MODE") != "dev")
    ):
        raise OntNgsRouteError(
            status_code=403, code="NGS_ROTATION_ORIGIN_DENIED",
            message="Capability rotation requires a local Development operator.",
            job_id=job_id, resource="rotation",
        )
    configured = urlsplit(os.environ.get("BMS_FRONTEND_HEALTH_URL", ""))
    supplied = urlsplit(request.headers.get("origin", ""))
    if (
        request.headers.get("sec-fetch-site", "").lower() != "same-origin"
        or configured.scheme not in {"http", "https"}
        or not configured.netloc
        or (supplied.scheme, supplied.netloc) != (configured.scheme, configured.netloc)
    ):
        raise OntNgsRouteError(
            status_code=403, code="NGS_ROTATION_ORIGIN_DENIED",
            message="Same-origin Development browser authority is required.",
            job_id=job_id, resource="rotation",
        )


@router.post(
    "/jobs/{job_id}/alignment-access/rotate",
    response_model=OntNgsRotationSuccessV1,
    responses=_STANDARD_GOVERNED_ERRORS,
)
async def rotate_alignment_access(
    job_id: str,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    domain_session: AsyncSession = Depends(get_molbio_ngs_session),
    experiment_session: AsyncSession = Depends(get_experiment_session),
):
    _require_local_development_browser(request, job_id)
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise OntNgsRouteError(
            status_code=404, code="NGS_RESOURCE_NOT_FOUND", message="The governed NGS Job was not found.",
            job_id=job_id, resource="rotation",
        )
    if job.model_id != "nanopore" or job.status != "completed":
        raise OntNgsRouteError(
            status_code=409, code="NGS_ROTATION_INELIGIBLE",
            message="Capability rotation requires a completed Nanopore Job.",
            job_id=job_id, resource="rotation",
        )
    hierarchy = None
    if _requires_governed_ont_hierarchy(job):
        try:
            hierarchy = await resolve_ont_ngs_hierarchy_authority(
                job,
                domain_session,
                experiment_session,
            )
        except OntNgsHierarchyError as exc:
            raise OntNgsRouteError(
                status_code=403, code="NGS_HIERARCHY_DENIED",
                message="The frozen NGS hierarchy is unavailable.",
                job_id=job_id, resource="rotation",
            ) from exc
        existing_hierarchy_record = (
            job.provenance.get(PROVENANCE_HIERARCHY_KEY)
            if isinstance(job.provenance, dict)
            else None
        )
        if existing_hierarchy_record is not None and not capability_hierarchy_matches(job, hierarchy):
            raise OntNgsRouteError(
                status_code=403, code="NGS_HIERARCHY_DENIED",
                message="The capability hierarchy is stale.",
                job_id=job_id, resource="rotation",
            )
        await _require_governed_project_principal(
            request,
            experiment_session,
            hierarchy.project_id,
            job_id,
        )
    try:
        await _validate_rotation_package_authority(job)
    except (JobResultRootError, SequenceQcManifestError, OntNgsResultError, service.AlignmentSessionError) as exc:
        raise OntNgsRouteError(
            status_code=409, code="NGS_PACKAGE_INTEGRITY_CONFLICT",
            message="The persisted NGS package authority is unavailable.",
            job_id=job_id, resource="rotation",
        ) from exc
    previous = job.provenance if isinstance(job.provenance, dict) else {}
    previous_digest = previous.get(alignment_access.PROVENANCE_DIGEST_KEY)
    revoked_authority = (
        previous_digest is None
        and previous.get("alignment_access_revoked") is True
        and previous.get(alignment_access.PROVENANCE_SCHEME_KEY) == alignment_access.SCHEME
    )
    if not isinstance(previous_digest, str) and not revoked_authority:
        raise OntNgsRouteError(
            status_code=409, code="NGS_AUTHORITY_CONFLICT",
            message="Persisted alignment capability authority is missing.",
            job_id=job_id, resource="rotation",
        )
    previous_rotation_count = previous.get("alignment_access_rotation_count", 0)
    if isinstance(previous_rotation_count, bool) or not isinstance(previous_rotation_count, int) or previous_rotation_count < 0:
        raise OntNgsRouteError(
            status_code=409, code="NGS_AUTHORITY_CONFLICT",
            message="Persisted alignment capability authority is malformed.",
            job_id=job_id, resource="rotation",
        )
    token, token_digest = alignment_access.issue_alignment_access_token()
    rotation_count = previous_rotation_count + 1
    updated = {
        **previous,
        alignment_access.PROVENANCE_DIGEST_KEY: token_digest,
        alignment_access.PROVENANCE_SCHEME_KEY: alignment_access.SCHEME,
        "alignment_access_rotation_count": rotation_count,
    }
    updated.pop("alignment_access_revoked", None)
    if hierarchy is not None:
        updated[PROVENANCE_HIERARCHY_KEY] = hierarchy_authority_record(hierarchy)
    changed = await alignment_access.rotate_alignment_authority_cas(
        session,
        job_id=job_id,
        previous=previous,
        updated=updated,
    )
    if not changed:
        await session.rollback()
        raise OntNgsRouteError(
            status_code=409, code="NGS_CAPABILITY_ROTATION_CONFLICT",
            message="Alignment capability authority changed concurrently.",
            job_id=job_id, resource="rotation",
        )
    await session.commit()
    alignment_access.set_alignment_access_cookie(job_id, token, response, request)
    return {
        "schema": "bms.ngs.rotation-success.v1",
        "job_id": job_id,
        "rotated": True,
        "scheme": alignment_access.SCHEME,
        "rotation_count": rotation_count,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=30),
    }


@router.delete(
    "/jobs/{job_id}/alignment-access",
    response_model=OntNgsCapabilityRevocationSuccessV1,
    responses=_STANDARD_GOVERNED_ERRORS,
)
async def revoke_alignment_access(
    job_id: str,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    domain_session: AsyncSession = Depends(get_molbio_ngs_session),
    experiment_session: AsyncSession = Depends(get_experiment_session),
):
    _require_local_development_browser(request, job_id)
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise OntNgsRouteError(
            status_code=404, code="NGS_RESOURCE_NOT_FOUND", message="The governed NGS Job was not found.",
            job_id=job_id, resource="rotation",
        )
    try:
        hierarchy = await resolve_ont_ngs_hierarchy_authority(job, domain_session, experiment_session)
    except OntNgsHierarchyError as exc:
        raise OntNgsRouteError(
            status_code=403, code="NGS_HIERARCHY_DENIED", message="The frozen NGS hierarchy is unavailable.",
            job_id=job_id, resource="rotation",
        ) from exc
    await _require_governed_project_principal(
        request, experiment_session, hierarchy.project_id, job_id,
    )
    previous = job.provenance if isinstance(job.provenance, dict) else {}
    token = alignment_access.request_alignment_token(request, job_id)
    previous_digest = previous.get(alignment_access.PROVENANCE_DIGEST_KEY)
    if token is not None and not alignment_access.capability_matches(
        token, previous_digest if isinstance(previous_digest, str) else None,
    ):
        alignment_access.expire_alignment_access_cookie(job_id, response, request)
        raise OntNgsRouteError(
            status_code=403, code="NGS_CAPABILITY_DENIED", message="Alignment capability access was denied.",
            job_id=job_id, resource="rotation",
            headers={"Set-Cookie": alignment_access.alignment_access_cookie_expiration_header(job_id, request)},
        )
    if token is not None and not capability_hierarchy_matches(job, hierarchy):
        alignment_access.expire_alignment_access_cookie(job_id, response, request)
        raise OntNgsRouteError(
            status_code=403, code="NGS_HIERARCHY_DENIED", message="The capability hierarchy is stale.",
            job_id=job_id, resource="rotation",
            headers={"Set-Cookie": alignment_access.alignment_access_cookie_expiration_header(job_id, request)},
        )
    try:
        await build_ont_fastq_qc_result(job)
    except (JobResultRootError, SequenceQcManifestError, OntNgsResultError, service.AlignmentSessionError) as exc:
        alignment_access.expire_alignment_access_cookie(job_id, response, request)
        raise OntNgsRouteError(
            status_code=409, code="NGS_PACKAGE_INTEGRITY_CONFLICT",
            message="The persisted NGS package authority is unavailable.",
            job_id=job_id, resource="rotation",
            headers={"Set-Cookie": alignment_access.alignment_access_cookie_expiration_header(job_id, request)},
        ) from exc
    updated = dict(previous)
    updated.pop(alignment_access.PROVENANCE_DIGEST_KEY, None)
    updated[alignment_access.PROVENANCE_SCHEME_KEY] = alignment_access.SCHEME
    updated["alignment_access_revoked"] = True
    changed = await alignment_access.rotate_alignment_authority_cas(
        session, job_id=job_id, previous=previous, updated=updated,
    )
    if not changed:
        await session.rollback()
        current_result = await session.execute(
            select(Job).where(Job.id == job_id).execution_options(populate_existing=True)
        )
        current = current_result.scalar_one_or_none()
        current_provenance = current.provenance if current is not None and isinstance(current.provenance, dict) else {}
        current_digest = current_provenance.get(alignment_access.PROVENANCE_DIGEST_KEY)
        if isinstance(current_digest, str):
            raise OntNgsRouteError(
                status_code=409, code="NGS_CAPABILITY_ROTATION_CONFLICT",
                message="Alignment capability authority changed concurrently.",
                job_id=job_id, resource="rotation",
            )
        if (
            current_provenance.get("alignment_access_revoked") is not True
            or current_provenance.get(alignment_access.PROVENANCE_SCHEME_KEY) != alignment_access.SCHEME
        ):
            raise OntNgsRouteError(
                status_code=409, code="NGS_CAPABILITY_ROTATION_CONFLICT",
                message="Alignment capability revocation was not persisted.",
                job_id=job_id, resource="rotation",
            )
    else:
        await session.commit()
    alignment_access.expire_alignment_access_cookie(job_id, response, request)
    return {
        "schema": "bms.ngs.capability-revocation-success.v1",
        "job_id": job_id,
        "revoked": True,
        "scheme": alignment_access.SCHEME,
    }


def _parse_range(value: str, size: int) -> tuple[int, int]:
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
    if match is None or "," in value:
        raise _InvalidRange("range syntax is invalid")
    start_raw, end_raw = match.groups()
    if not start_raw and not end_raw:
        raise _InvalidRange("range bounds are empty")
    if any(len(raw) > 19 or (raw and int(raw) > 2**63 - 1) for raw in (start_raw, end_raw)):
        raise _InvalidRange("range bound overflows")
    if not start_raw:
        suffix = int(end_raw)
        if suffix == 0:
            raise _InvalidRange("range suffix is zero")
        if size == 0:
            raise _UnsatisfiableRange("empty artifact has no satisfiable range")
        return max(0, size - suffix), size - 1
    start = int(start_raw)
    if size == 0 or start >= size:
        raise _UnsatisfiableRange("range starts beyond the artifact")
    end = size - 1 if not end_raw else int(end_raw)
    if end < start:
        raise _InvalidRange("range is reversed")
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


async def _serve_artifact(
    path: Path,
    metadata: dict,
    request: Request,
    *,
    job_id: str,
) -> Response:
    size = int(metadata["size_bytes"])
    digest = str(metadata["sha256"])
    etag = f'"sha256:{digest}"'
    base_headers = {
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Cache-Control": "private, no-cache, must-revalidate",
        "X-Content-Type-Options": "nosniff",
        "Content-Type": str(metadata["mime_type"]),
        "Content-Disposition": _artifact_content_disposition(path, metadata, digest),
    }
    if metadata.get("mime_type") == "text/html":
        base_headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    if request.headers.get("if-none-match") == etag:
        try:
            await run_in_threadpool(
                service.verify_current_artifact_bytes,
                path,
                expected_size=size,
                expected_sha256=digest,
            )
        except service.AlignmentSessionError:
            return _ngs_error_response(
                status_code=409,
                code="NGS_ARTIFACT_INTEGRITY_CONFLICT",
                message="The governed artifact changed before delivery.",
                job_id=job_id,
                resource="artifact",
            )
        return Response(status_code=304, headers=base_headers)

    start, end = 0, max(0, size - 1)
    status_code = 200
    range_header = request.headers.get("range") if request.method != "HEAD" else None
    parsed_range: tuple[int, int] | None = None
    if range_header is not None:
        try:
            parsed_range = _parse_range(range_header, size)
        except _InvalidRange:
            return _ngs_error_response(
                status_code=400,
                code="NGS_RANGE_INVALID",
                message="The requested byte range is invalid.",
                job_id=job_id,
                resource="range",
            )
        except _UnsatisfiableRange:
            return _ngs_error_response(
                status_code=416,
                code="NGS_RANGE_UNSATISFIABLE",
                message="The requested byte range is outside the artifact.",
                job_id=job_id,
                resource="range",
                headers={
                    "Accept-Ranges": "bytes",
                    "ETag": etag,
                    "Content-Range": f"bytes */{size}",
                },
            )
        if_range = request.headers.get("if-range")
        if if_range is None or if_range == etag:
            start, end = parsed_range
            status_code = 206

    headers = dict(base_headers)
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(end - start + 1)
    else:
        headers["Content-Length"] = str(size)

    try:
        snapshot = await run_in_threadpool(
            service.open_verified_artifact_snapshot,
            path,
            expected_size=size,
            expected_sha256=digest,
        )
    except service.AlignmentSessionError:
        return _ngs_error_response(
            status_code=409,
            code="NGS_ARTIFACT_INTEGRITY_CONFLICT",
            message="The governed artifact changed before delivery.",
            job_id=job_id,
            resource="artifact",
        )
    if request.method == "HEAD":
        snapshot.close()
        return Response(status_code=200, headers=headers)
    return StreamingResponse(
        _iter_range(snapshot, start, end),
        status_code=status_code,
        headers=headers,
        media_type=str(metadata["mime_type"]),
    )


@router.get(
    "/jobs/{job_id}/alignment-sessions",
    response_model=OntAlignmentSessionListV1,
    responses=_STANDARD_GOVERNED_ERRORS,
)
async def list_alignment_sessions(
    job_id: str,
    authorized_job: Job = Depends(require_alignment_job),
):
    try:
        async with _validated_pinned_result_root(authorized_job) as pinned_root:
            sessions = await run_in_threadpool(
                service.build_alignment_sessions,
                job_id,
                **_job_session_authority(authorized_job),
                job_output_dir=pinned_root,
                pinned_root_descriptor=True,
            )
        return {
            "schema": "bms.ngs.alignment-session-list.v1",
            "job_id": job_id,
            "sessions": sessions,
        }
    except service.AlignmentSessionError as exc:
        raise _http_error(exc, job_id=job_id, resource="artifact") from exc


def find_canonical_fastq_manifest(result_root: Path) -> Path:
    """Resolve the canonical manifest below an already pinned result root."""
    return _find_canonical_fastq_manifest(result_root, pinned_root_descriptor=True)


@router.get("/jobs/{job_id}/sequence-qc-manifest", responses=_STANDARD_GOVERNED_ERRORS)
async def get_job_scoped_sequence_qc_manifest(
    job_id: str,
    authorized_job: Job = Depends(require_alignment_job),
):
    try:
        async with _validated_pinned_result_root(authorized_job) as result_root:
            manifest_path = (
                find_canonical_fastq_manifest(result_root)
                if _requires_governed_ont_hierarchy(authorized_job)
                else find_generic_manifest_in_result_root(result_root)
            )
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
        raise _http_error(exc, job_id=job_id, resource="artifact") from exc
    except (JobResultRootError, SequenceQcManifestError) as exc:
        raise _http_error(
            service.AlignmentSessionError(str(exc)), job_id=job_id, resource="manifest"
        ) from exc


@router.get(
    "/jobs/{job_id}/ngs-result",
    response_model=OntFastqQcResultV1,
    responses=_STANDARD_GOVERNED_ERRORS,
)
async def get_job_scoped_ngs_result(
    job_id: str,
    request: Request,
    authorized_job: Job = Depends(require_alignment_job),
):
    cached = getattr(request.state, "ont_fastq_qc_result", None)
    if cached is not None:
        return cached
    try:
        return await build_ont_fastq_qc_result(authorized_job)
    except (JobResultRootError, SequenceQcManifestError, OntNgsResultError, service.AlignmentSessionError) as exc:
        raise _http_error(
            service.AlignmentSessionError(str(exc)), job_id=job_id, resource="result"
        ) from exc


@router.get(
    "/jobs/{job_id}/alignment-sessions/{session_id}",
    response_model=OntAlignmentSessionDetailV1,
    responses=_STANDARD_GOVERNED_ERRORS,
)
async def get_alignment_session(
    job_id: str,
    session_id: str,
    authorized_job: Job = Depends(require_alignment_job),
):
    try:
        async with _validated_pinned_result_root(authorized_job) as pinned_root:
            session = await run_in_threadpool(
                service.resolve_alignment_session,
                job_id,
                session_id,
                **_job_session_authority(authorized_job),
                job_output_dir=pinned_root,
                pinned_root_descriptor=True,
            )
        return {
            "schema": "bms.ngs.alignment-session-detail.v1",
            "job_id": job_id,
            "session": session,
        }
    except service.AlignmentSessionError as exc:
        raise _http_error(exc, job_id=job_id, resource="artifact") from exc


@router.get("/jobs/{job_id}/ngs-artifacts", responses=_STANDARD_GOVERNED_ERRORS)
async def list_ngs_package_artifacts(
    job_id: str,
    authorized_job: Job = Depends(require_alignment_job),
):
    try:
        async with _validated_pinned_result_root(authorized_job) as pinned_root:
            artifacts = await run_in_threadpool(
                service.build_ngs_package_artifacts,
                job_id,
                **_job_package_authority(authorized_job),
                job_output_dir=pinned_root,
                pinned_root_descriptor=True,
            )
        return {
            "job_id": job_id,
            "artifacts": [
                {key: value for key, value in artifact.items() if key != "relative_path"}
                for artifact in artifacts
            ],
        }
    except service.AlignmentSessionError as exc:
        raise _http_error(exc, job_id=job_id, resource="artifact") from exc


@router.get("/jobs/{job_id}/ngs-artifacts/{artifact_id}", responses=_BINARY_RESPONSES)
@router.head("/jobs/{job_id}/ngs-artifacts/{artifact_id}", responses=_BINARY_RESPONSES)
async def get_ngs_package_artifact(
    job_id: str,
    artifact_id: str,
    request: Request,
    authorized_job: Job = Depends(require_alignment_job),
):
    try:
        async with _validated_pinned_result_root(authorized_job) as pinned_root:
            path, metadata = await run_in_threadpool(
                service.resolve_ngs_package_artifact,
                job_id,
                artifact_id,
                **_job_package_authority(authorized_job),
                job_output_dir=pinned_root,
                pinned_root_descriptor=True,
            )
            return await _serve_artifact(path, metadata, request, job_id=job_id)
    except service.AlignmentSessionError as exc:
        raise _http_error(exc, job_id=job_id, resource="artifact") from exc


@router.get("/jobs/{job_id}/alignment-artifacts/{artifact_id}", responses=_BINARY_RESPONSES)
@router.head("/jobs/{job_id}/alignment-artifacts/{artifact_id}", responses=_BINARY_RESPONSES)
async def get_alignment_artifact(
    job_id: str,
    artifact_id: str,
    request: Request,
    authorized_job: Job = Depends(require_alignment_job),
):
    try:
        async with _validated_pinned_result_root(authorized_job) as pinned_root:
            path, metadata = await run_in_threadpool(
                service._resolve_internal_artifact,
                job_id,
                artifact_id,
                **_job_authority(authorized_job),
                job_output_dir=pinned_root,
                pinned_root_descriptor=True,
            )
            return await _serve_artifact(path, metadata, request, job_id=job_id)
    except service.AlignmentSessionError as exc:
        raise _http_error(exc, job_id=job_id, resource="artifact") from exc


@router.get("/jobs/{job_id}/alignment-session-artifacts/{mode}/{role}/{sha256}", responses=_BINARY_RESPONSES)
@router.head("/jobs/{job_id}/alignment-session-artifacts/{mode}/{role}/{sha256}", responses=_BINARY_RESPONSES)
async def get_alignment_session_artifact(
    job_id: str,
    mode: str,
    role: str,
    sha256: str,
    request: Request,
    authorized_job: Job = Depends(require_alignment_job),
):
    try:
        async with _validated_pinned_result_root(authorized_job) as pinned_root:
            path, metadata = await run_in_threadpool(
                service.resolve_alignment_artifact_by_role,
                job_id,
                mode,
                role,
                sha256,
                **_job_authority(authorized_job),
                job_output_dir=pinned_root,
                pinned_root_descriptor=True,
            )
            return await _serve_artifact(path, metadata, request, job_id=job_id)
    except service.AlignmentSessionError as exc:
        raise _http_error(exc, job_id=job_id, resource="artifact") from exc


def _presentation_root_for_job(job: Job) -> Path:
    root = _job_output_dir(job)
    if not isinstance(root, str) or not root:
        raise service.AlignmentSessionError("persisted NGS result root is unavailable")
    return Path(root) / ".alignment-presentations"


def _derived_descriptor(metadata: dict[str, Any], url: str) -> dict[str, Any]:
    return {"kind": metadata["kind"], "url": url, "sha256": metadata["sha256"],
            "size_bytes": metadata["size_bytes"], "mime_type": metadata["mime_type"], "range_capable": True}


def _presentation_authority(job: Job, session_id: str) -> tuple[str, str]:
    provenance = job.provenance if isinstance(job.provenance, dict) else {}
    integrity = provenance.get("result_integrity") if isinstance(provenance, dict) else None
    presentations = integrity.get("alignment_presentations") if isinstance(integrity, dict) else None
    matches = [
        item for item in presentations or []
        if isinstance(item, dict) and item.get("session_id") == session_id
    ]
    if len(matches) != 1:
        raise service.AlignmentSessionError("alignment presentation authority is unavailable")
    authority_sha256 = matches[0].get("authority_sha256")
    manifest_sha256 = matches[0].get("manifest_sha256")
    if (
        not isinstance(authority_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", authority_sha256) is None
        or not isinstance(manifest_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is None
    ):
        raise service.AlignmentSessionError("alignment presentation authority is invalid")
    return authority_sha256, manifest_sha256


@asynccontextmanager
async def _prepared_presentation(job_id: str, session_id: str, job: Job):
    authority_sha256, manifest_sha256 = _presentation_authority(job, session_id)
    async with _validated_pinned_result_root(job) as pinned_result_root:
        package = await run_in_threadpool(
            service.resolve_cached_alignment_presentation,
            job_id,
            session_id,
            cache_root=pinned_result_root / ".alignment-presentations",
            expected_authority_sha256=authority_sha256,
            expected_manifest_sha256=manifest_sha256,
        )
        yield package, pinned_result_root


async def _prepare_presentation(job_id: str, session_id: str, job: Job) -> dict[str, Any]:
    async with _prepared_presentation(job_id, session_id, job) as (package, _pinned_result_root):
        return package


def _presentation_response(job_id: str, session_id: str, package: dict[str, Any]) -> dict[str, Any]:
    manifest = package["manifest"]
    base = (
        f"/api/jobs/{job_id}/alignment-sessions/{session_id}/presentation/"
        f"{manifest['authority_sha256']}"
    )
    return {
        "schema": "bms.ngs.alignment-presentation.v1", "job_id": job_id, "session_id": session_id,
        "mode": manifest["mode"], "state": "ready",
        "source": {"package_manifest_sha256": manifest["package_manifest_sha256"],
                   "alignment_sha256": manifest["source_alignment_sha256"],
                   "alignment_size_bytes": manifest["source_alignment_size_bytes"],
                   "alignment_index_sha256": manifest["source_index_sha256"],
                   "alignment_index_size_bytes": manifest["source_index_size_bytes"],
                   "primary_read_count": manifest["source_primary_mapped_read_count"],
                   "alignment_record_count": manifest["source_alignment_record_count"]},
        "policy": manifest["policy"],
        "preview": {"kind": "primary_read_preview", "selected_read_count": manifest["selected_read_count"],
                    "selected_record_count": manifest["selected_alignment_record_count"],
                    "selected_read_set_sha256": manifest["selected_read_set_sha256"],
                    "forward_count": manifest["selected_strand_counts"]["forward"],
                    "reverse_count": manifest["selected_strand_counts"]["reverse"],
                    "bam": _derived_descriptor(package["bam_metadata"], f"{base}/bam"),
                    "index": _derived_descriptor(package["index_metadata"], f"{base}/bai")},
        "coverage": {"kind": "full_source_primary_coverage", "bin_width_bp": manifest["coverage_bin_width"],
                     "primary_read_count": manifest["source_primary_mapped_read_count"],
                     "artifact": _derived_descriptor(package["coverage_metadata"], f"{base}/coverage")},
        "manifest": _derived_descriptor(package["manifest_metadata"], f"{base}/manifest"),
    }


@router.get("/jobs/{job_id}/alignment-sessions/{session_id}/presentation",
            response_model=OntAlignmentPresentationV1, responses=_STANDARD_GOVERNED_ERRORS)
async def get_alignment_presentation(job_id: str, session_id: str, authorized_job: Job = Depends(require_alignment_job)):
    try:
        async with _prepared_presentation(job_id, session_id, authorized_job) as (package, _pinned_result_root):
            return _presentation_response(job_id, session_id, package)
    except service.AlignmentSessionError as exc:
        raise _http_error(exc, job_id=job_id, resource="artifact") from exc


@router.get("/jobs/{job_id}/alignment-sessions/{session_id}/presentation/{kind}", responses=_BINARY_RESPONSES)
@router.head("/jobs/{job_id}/alignment-sessions/{session_id}/presentation/{kind}", responses=_BINARY_RESPONSES)
@router.get("/jobs/{job_id}/alignment-sessions/{session_id}/presentation/{presentation_id}/{kind}", responses=_BINARY_RESPONSES)
@router.head("/jobs/{job_id}/alignment-sessions/{session_id}/presentation/{presentation_id}/{kind}", responses=_BINARY_RESPONSES)
async def get_alignment_presentation_artifact(job_id: str, session_id: str, kind: str, request: Request,
                                               authorized_job: Job = Depends(require_alignment_job),
                                               presentation_id: str | None = None):
    if kind not in {"bam", "bai", "coverage", "manifest"}:
        raise OntNgsRouteError(status_code=404, code="NGS_RESOURCE_NOT_FOUND",
                               message="The governed presentation artifact was not found.",
                               job_id=job_id, resource="artifact")
    try:
        async with _prepared_presentation(job_id, session_id, authorized_job) as (package, _pinned_result_root):
            if presentation_id is not None and presentation_id != package["manifest"].get("authority_sha256"):
                raise service.AlignmentSessionError("alignment presentation identity does not match")
            path_key, metadata_key = {"bam": ("bam_path", "bam_metadata"), "bai": ("index_path", "index_metadata"),
                                      "coverage": ("coverage_path", "coverage_metadata"),
                                      "manifest": ("manifest_path", "manifest_metadata")}[kind]
            return await _serve_artifact(package[path_key], package[metadata_key], request, job_id=job_id)
    except service.AlignmentSessionError as exc:
        raise _http_error(exc, job_id=job_id, resource="artifact") from exc


@router.post("/jobs/{job_id}/alignment-sessions/{session_id}/locus-slices",
             response_model=OntAlignmentLocusSliceV1, responses=_typed_errors(400, 403, 404, 409))
async def create_alignment_locus_slice(job_id: str, session_id: str, body: OntAlignmentLocusSliceRequestV1,
                                       authorized_job: Job = Depends(require_alignment_job)):
    if (not service.SAFE_CONTIG_RE.fullmatch(body.contig) or body.start_1based < 1
            or body.end_1based < body.start_1based
            or body.end_1based - body.start_1based + 1 > service.LOCUS_MAX_SPAN
            or body.max_reads < 1 or body.max_reads > service.LOCUS_MAX_READS):
        return _ngs_error_response(status_code=400, code="NGS_RANGE_INVALID",
                                   message="The locus slice request is invalid.", job_id=job_id, resource="range")
    try:
        async with _prepared_presentation(job_id, session_id, authorized_job) as (presentation, pinned_root):
            manifest = presentation["manifest"]
            with service.open_presentation_source_bundle(
                presentation, pinned_root,
            ) as (bam, index, identity, index_identity):
                package = await run_in_threadpool(
                    service.build_alignment_locus_slice, bam,
                    bam_sha256=manifest["source_alignment_sha256"],
                    bam_size_bytes=manifest["source_alignment_size_bytes"], index=index,
                    index_sha256=manifest["source_index_sha256"],
                    index_size_bytes=manifest["source_index_size_bytes"],
                    source_identity=identity, source_index_identity=index_identity,
                    source_manifest_sha256=manifest["package_manifest_sha256"],
                    job_id=job_id, session_id=session_id, contig=body.contig,
                    start=body.start_1based, end=body.end_1based, max_reads=body.max_reads,
                )
        receipt = package["receipt"]
        base = f"/api/jobs/{job_id}/alignment-sessions/{session_id}/locus-slices/{package['slice_id']}"
        return {"schema": "bms.ngs.alignment-locus-slice.v1", "job_id": job_id, "session_id": session_id,
                "slice_id": package["slice_id"], "state": "ready", "contig": body.contig,
                "start_1based": body.start_1based, "end_1based": body.end_1based,
                "overlapping_read_count": receipt["overlapping_read_count"],
                "selected_read_count": receipt["selected_read_count"],
                "selected_record_count": receipt["selected_record_count"], "capped": receipt["capped"],
                "policy": receipt["policy"],
                "bam": _derived_descriptor(package["bam_metadata"], f"{base}/{package['bam_metadata']['sha256']}/bam"),
                "index": _derived_descriptor(package["index_metadata"], f"{base}/{package['index_metadata']['sha256']}/bai"),
                "manifest": _derived_descriptor(package["manifest_metadata"], f"{base}/{package['manifest_metadata']['sha256']}/manifest")}
    except service.AlignmentSessionError as exc:
        raise _http_error(exc, job_id=job_id, resource="artifact") from exc


@router.get("/jobs/{job_id}/alignment-sessions/{session_id}/locus-slices/{slice_id}/{artifact_sha256}/{kind}", responses=_BINARY_RESPONSES)
@router.head("/jobs/{job_id}/alignment-sessions/{session_id}/locus-slices/{slice_id}/{artifact_sha256}/{kind}", responses=_BINARY_RESPONSES)
async def get_alignment_locus_slice_artifact(job_id: str, session_id: str, slice_id: str,
                                              artifact_sha256: str, kind: str,
                                              request: Request, authorized_job: Job = Depends(require_alignment_job)):
    if kind not in {"bam", "bai", "manifest"}:
        raise OntNgsRouteError(status_code=404, code="NGS_RESOURCE_NOT_FOUND",
                               message="The governed locus artifact was not found.", job_id=job_id, resource="artifact")
    try:
        package = await run_in_threadpool(service.resolve_cached_alignment_locus_slice, slice_id)
        if package["receipt"].get("job_id") != job_id or package["receipt"].get("session_id") != session_id:
            raise service.AlignmentSessionError("alignment locus slice not found")
        path_key, metadata_key = {"bam": ("bam_path", "bam_metadata"), "bai": ("index_path", "index_metadata"),
                                  "manifest": ("manifest_path", "manifest_metadata")}[kind]
        if package[metadata_key].get("sha256") != artifact_sha256:
            raise service.AlignmentSessionError("alignment locus artifact digest does not match")
        return await _serve_artifact(package[path_key], package[metadata_key], request, job_id=job_id)
    except service.AlignmentSessionError as exc:
        raise _http_error(exc, job_id=job_id, resource="artifact") from exc


@router.get("/jobs/{job_id}/alignment-sessions/{session_id}/preview/{kind}", responses=_BINARY_RESPONSES)
@router.head("/jobs/{job_id}/alignment-sessions/{session_id}/preview/{kind}", responses=_BINARY_RESPONSES)
async def get_alignment_preview(
    job_id: str,
    session_id: str,
    kind: str,
    request: Request,
    authorized_job: Job = Depends(require_alignment_job),
):
    if kind not in {"bam", "bai"}:
        raise OntNgsRouteError(
            status_code=404,
            code="NGS_RESOURCE_NOT_FOUND",
            message="The governed alignment preview was not found.",
            job_id=job_id,
            resource="artifact",
        )
    try:
        async with _prepared_presentation(job_id, session_id, authorized_job) as (package, _pinned_result_root):
            if kind == "bam":
                return await _serve_artifact(package["bam_path"], package["bam_metadata"], request, job_id=job_id)
            return await _serve_artifact(package["index_path"], package["index_metadata"], request, job_id=job_id)
    except service.AlignmentSessionError as exc:
        raise _http_error(exc, job_id=job_id, resource="artifact") from exc


@router.get("/jobs/{job_id}/reads", responses=_READ_ERRORS)
async def list_alignment_reads(
    job_id: str,
    session_id: str | None = Query(default=None),
    contig: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    q: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: str | None = Query(default=None),
    include_sequence: str | None = Query(default=None),
    authorized_job: Job = Depends(require_alignment_job),
):
    try:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id is required")
        parsed_start = int(start) if start is not None else None
        parsed_end = int(end) if end is not None else None
        parsed_limit = int(limit) if limit is not None else 50
        if (parsed_start is not None and parsed_start < 1) or (parsed_end is not None and parsed_end < 1):
            raise ValueError("region is invalid")
        if parsed_limit < 1 or parsed_limit > service.MAX_READ_PAGE:
            raise ValueError("limit is invalid")
        if q is not None and len(q) > 255:
            raise ValueError("query is invalid")
        if cursor is not None and len(cursor) > 32:
            raise ValueError("cursor is invalid")
        normalized_include_sequence = (include_sequence or "false").lower()
        if normalized_include_sequence not in {"true", "false"}:
            raise ValueError("include_sequence is invalid")
    except (TypeError, ValueError):
        return _ngs_error_response(
            status_code=400, code="NGS_RANGE_INVALID",
            message="The read query parameters are invalid.",
            job_id=job_id, resource="read",
        )
    try:
        async with _validated_pinned_result_root(authorized_job) as pinned_root:
            bam, bam_metadata, index, index_metadata = await run_in_threadpool(
                service.resolve_session_alignment_bundle,
                job_id,
                session_id.strip(),
                **_job_authority(authorized_job),
                job_output_dir=pinned_root,
                pinned_root_descriptor=True,
            )
            return await run_in_threadpool(
                service.read_bam_page,
                bam,
                bam_sha256=bam_metadata["sha256"], bam_size_bytes=bam_metadata["size_bytes"],
                index=index, index_sha256=index_metadata["sha256"], index_size_bytes=index_metadata["size_bytes"],
                contig=contig, start=parsed_start, end=parsed_end, q=q, cursor=cursor,
                limit=parsed_limit, include_sequence=normalized_include_sequence == "true",
            )
    except service.AlignmentSessionError as exc:
        raise _http_error(exc, job_id=job_id, resource="artifact") from exc


@router.get("/jobs/{job_id}/reads/{read_id}", responses=_READ_ERRORS)
async def get_alignment_read(
    job_id: str,
    read_id: str,
    session_id: str | None = Query(default=None),
    contig: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    authorized_job: Job = Depends(require_alignment_job),
):
    if not read_id or len(read_id) > 255:
        raise OntNgsRouteError(
            status_code=404, code="NGS_RESOURCE_NOT_FOUND", message="The governed read was not found.",
            job_id=job_id, resource="read",
        )
    try:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id is required")
        parsed_start = int(start) if start is not None else None
        parsed_end = int(end) if end is not None else None
        if (parsed_start is not None and parsed_start < 1) or (parsed_end is not None and parsed_end < 1):
            raise ValueError("region is invalid")
    except (TypeError, ValueError):
        return _ngs_error_response(
            status_code=400, code="NGS_RANGE_INVALID",
            message="The read query parameters are invalid.",
            job_id=job_id, resource="read",
        )
    try:
        async with _validated_pinned_result_root(authorized_job) as pinned_root:
            bam, bam_metadata, index, index_metadata = await run_in_threadpool(
                service.resolve_session_alignment_bundle,
                job_id,
                session_id.strip(),
                **_job_authority(authorized_job),
                job_output_dir=pinned_root,
                pinned_root_descriptor=True,
            )
            payload = await run_in_threadpool(
                service.read_bam_exact,
                bam, read_id,
                bam_sha256=bam_metadata["sha256"], bam_size_bytes=bam_metadata["size_bytes"],
                index=index, index_sha256=index_metadata["sha256"], index_size_bytes=index_metadata["size_bytes"],
                contig=contig, start=parsed_start, end=parsed_end,
            )
    except service.AlignmentSessionError as exc:
        raise _http_error(exc, job_id=job_id, resource="artifact") from exc
    if payload["read"] is not None:
        return JSONResponse(payload["read"])
    if payload["scan_truncated"]:
        raise OntNgsRouteError(
            status_code=409, code="NGS_READ_SCAN_TRUNCATED",
            message="The bounded read scan ended before absence could be proved.",
            job_id=job_id, resource="read",
        )
    raise OntNgsRouteError(
        status_code=404, code="NGS_RESOURCE_NOT_FOUND", message="The governed read was not found.",
        job_id=job_id, resource="read",
    )
