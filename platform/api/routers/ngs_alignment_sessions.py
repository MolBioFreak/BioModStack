"""Job-scoped ONT alignment viewer, artifact streaming, and read-inspection routes."""

from __future__ import annotations

import hashlib
import math
import os
import re
import stat
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import contextmanager_in_threadpool
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from services.verified_native_reads import run_in_threadpool
import rfc8785

from database import Job, NgsAlignmentPresentationJob, NgsAlignmentDerivedProduct, OntRawSignalRepresentation, async_session, get_session
from experiment_database import get_experiment_session
from molbio_ngs_database import get_molbio_ngs_session
from routers.experiment_workspaces import (
    _authenticated_principal,
    _mutation_principal,
    _require_mutation_owner,
)
from ont_ngs_result_response import OntFastqQcResultResponse
from services import alignment_access
from services import ngs_alignment_sessions as service
from services import ngs_alignment_presentation as presentation_lifecycle
from services import ngs_alignment_derived_products as derived_products
from services.ont_read_metrics import (
    OntReadMetricError,
    RAW_READ_METRICS_CONTRACT,
    load_read_metrics_for_ids,
)
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
    """Historical recovery-package contract; never expand its closed enums."""

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
        "NGS_CAPABILITY_DENIED",
        "NGS_HIERARCHY_DENIED",
        "NGS_PRINCIPAL_DENIED",
        "NGS_ROTATION_ORIGIN_DENIED",
        "NGS_RESOURCE_NOT_FOUND",
        "NGS_AUTHORITY_CONFLICT",
        "NGS_PACKAGE_INTEGRITY_CONFLICT",
        "NGS_CAPABILITY_ROTATION_CONFLICT",
        "NGS_ROTATION_INELIGIBLE",
        "NGS_ARTIFACT_INTEGRITY_CONFLICT",
        "NGS_READ_SCAN_TRUNCATED",
        "NGS_RANGE_INVALID",
        "NGS_RANGE_UNSATISFIABLE",
    ]
    message: str = Field(min_length=1, max_length=512)
    job_id: str = Field(json_schema_extra={"format": "uuid"})
    resource: Literal[
        "result", "manifest", "session", "artifact", "range", "rotation", "read"
    ]
    retryable: bool

    @model_validator(mode="after")
    def _retryable_matches_code(self):
        expected = self.code in {"NGS_CAPABILITY_DENIED", "NGS_CAPABILITY_ROTATION_CONFLICT"}
        if self.retryable is not expected:
            raise ValueError("retryable disagrees with governed NGS error code")
        return self


class OntNgsErrorV2(BaseModel):
    """Current governed failures, including presentation and indexed-read errors."""

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
    schema_version: Literal["bms.ngs.error.v2"] = Field(alias="schema")
    code: Literal[
        "NGS_CAPABILITY_DENIED", "NGS_HIERARCHY_DENIED", "NGS_PRINCIPAL_DENIED",
        "NGS_ROTATION_ORIGIN_DENIED", "NGS_RESOURCE_NOT_FOUND", "NGS_AUTHORITY_CONFLICT",
        "NGS_PACKAGE_INTEGRITY_CONFLICT", "NGS_CAPABILITY_ROTATION_CONFLICT",
        "NGS_PRESENTATION_ALREADY_READY", "NGS_PRESENTATION_SOURCE_STALE", "NGS_LEGACY_MUTATION_RETIRED",
        "NGS_ROTATION_INELIGIBLE", "NGS_ARTIFACT_INTEGRITY_CONFLICT",
        "NGS_READ_SCAN_TRUNCATED", "NGS_RANGE_INVALID", "NGS_RANGE_UNSATISFIABLE",
        "NGS_READ_POPULATION_INVALID", "NGS_READ_POPULATION_STALE",
        "NGS_READ_CURSOR_INVALID", "NGS_READ_CURSOR_STALE",
        "NGS_RECORD_CURSOR_INVALID", "NGS_RECORD_CURSOR_STALE", "NGS_CATALOG_NOT_READY",
        "NGS_READ_ID_INVALID", "NGS_READ_QUERY_INVALID", "NGS_READ_CAPACITY_UNAVAILABLE",
        "NGS_READ_ALREADY_IN_PREVIEW", "NGS_READ_NOT_OVERLAY_ELIGIBLE", "NGS_READ_OVERLAY_TIMEOUT",
    ]
    message: str = Field(min_length=1, max_length=512)
    job_id: str = Field(json_schema_extra={"format": "uuid"})
    resource: Literal[
        "result", "manifest", "session", "artifact", "range", "rotation", "read", "presentation"
    ]
    retryable: bool
    reason: Literal["already_in_preview", "unmapped", "ambiguous_primary", "no_primary", "record_limit", "writer_unsupported", "byte_limit"] | None = None

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


class OntNativeAlignmentSessionV2(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.native-alignment-session.v2"] = Field(alias="schema")
    session_id: str
    job_id: str
    mode: Literal["primary"]
    ready: Literal[True]
    unavailable_reason: None
    reads_url: str
    source_manifest_sha256: str
    source_authority_sha256: str
    artifact_set_sha256: str
    reference: OntAlignmentReferenceV1
    artifacts: OntAlignmentArtifactsV1
    alignment_pair_sha256: str


class OntNativeAlignmentSessionListV2(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.native-alignment-session-list.v2"] = Field(alias="schema")
    job_id: str
    sessions: list[OntNativeAlignmentSessionV2] = Field(max_length=1)


class OntNativeAlignmentSessionDetailV2(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.native-alignment-session-detail.v2"] = Field(alias="schema")
    job_id: str
    session: OntNativeAlignmentSessionV2


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


class OntDerivedUnavailableV3(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["unavailable"]
    reason: Literal["request_missing", "unsupported_source"]


class OntDerivedRequestedV3(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["requested"]
    request_id: str
    request_sha256: str | None
    attempt_count: int = Field(ge=0)
    manual_retry_count: int = Field(ge=0)
    blocked_on: Literal["catalog"] | None = None


class OntDerivedRunningV3(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["running"]
    request_id: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt_count: int = Field(ge=1)
    manual_retry_count: int = Field(ge=0)


class OntDerivedReadyV3(OntDerivedRunningV3):
    state: Literal["ready"]
    authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class OntDerivedFailedV3(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["failed"]
    request_id: str
    request_sha256: str | None
    attempt_count: int = Field(ge=0)
    manual_retry_count: int = Field(ge=0)
    code: Literal["source_invalid", "resource_limit", "cancelled", "infrastructure_failed", "publication_failed", "integrity_mismatch"]
    retryable: bool


OntDerivedStateV3 = OntDerivedUnavailableV3 | OntDerivedRequestedV3 | OntDerivedRunningV3 | OntDerivedReadyV3 | OntDerivedFailedV3


class OntAlignmentPresentationV3(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.alignment-presentation.v3"] = Field(alias="schema")
    job_id: str
    session_id: str
    catalog: OntDerivedStateV3 = Field(discriminator="state")
    preview: OntDerivedStateV3 = Field(discriminator="state")


class OntDerivedRetryV3(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=96)


class OntPresentationStateBaseV2(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.alignment-presentation.v2"] = Field(alias="schema")
    job_id: str
    session_id: str
    mode: Literal["primary", "dimer_candidates"]


class OntPresentationPreparingV2(OntPresentationStateBaseV2):
    state: Literal["preparing"]


class OntPresentationFailedV2(OntPresentationStateBaseV2):
    state: Literal["failed"]
    code: Literal[
        "source_invalid", "resource_limit", "build_timeout", "infrastructure_failed",
        "publication_failed", "integrity_mismatch",
    ]
    message: Literal["Reads unavailable. Retry from Diagnostics."]


class OntPresentationSourceV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_manifest_sha256: str
    artifact_set_sha256: str
    alignment_sha256: str
    alignment_size_bytes: int
    alignment_index_sha256: str
    alignment_index_size_bytes: int
    logical_read_count: int
    alignment_record_count: int


class OntPresentationPolicyV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Literal["primary-read-presentation-v4"]
    version: Literal[4]
    target_reads: int
    max_preview_records: int
    max_preview_bytes: int
    max_build_seconds: float


class OntPresentationPreviewV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected_read_count: int
    selected_record_count: int
    selected_read_set_sha256: str
    preview_complete_to_target: bool
    bam: OntDerivedArtifactV1
    index: OntDerivedArtifactV1


class OntPresentationCatalogV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.read-catalog.v1"] = Field(alias="schema")
    logical_read_count: int
    mapped_primary_read_count: int
    unmapped_read_count: int
    ambiguous_primary_read_count: int
    no_primary_read_count: int
    content_sha256: str
    size_bytes: int


class OntPresentationLocatorsV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.read-record-locators.v1"] = Field(alias="schema")
    record_count: int
    content_sha256: str
    size_bytes: int


class OntPresentationCoverageV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["full_source_primary_coverage"]
    primary_read_count: int
    descriptor: OntDerivedArtifactV1


class OntPresentationReadyV2(OntPresentationStateBaseV2):
    state: Literal["ready"]
    presentation_id: str
    source: OntPresentationSourceV2
    policy: OntPresentationPolicyV2
    preview: OntPresentationPreviewV2
    catalog: OntPresentationCatalogV1
    locators: OntPresentationLocatorsV1
    coverage: OntPresentationCoverageV2
    manifest: OntDerivedArtifactV1


OntAlignmentPresentationV2 = (
    OntPresentationPreparingV2 | OntPresentationReadyV2 | OntPresentationFailedV2
)


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


class OntSortableReadV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    read_id: str
    length: int | None = None
    mean_quality: float | None = None
    contig: str | None = None
    start_1based: int | None = None
    alignment_end_1based: int | None = None
    strand: Literal["+", "-"]
    mapq: int | None = None
    cigar: str | None = None
    flags: int
    unmapped: bool
    aligned_query_bases: int | None = None
    aligned_reference_bases: int | None = None
    inserted_bases: int | None = None
    deleted_bases: int | None = None
    skipped_reference_bases: int | None = None
    clipped_bases: int | None = None
    edit_distance: int | None = None
    reference_substitution_count: int | None = None
    reference_substitution_rate: float | None = None
    aligned_fraction: float | None = None
    clipped_fraction: float | None = None
    reference_disagreement_rate: float | None = None
    sample_count: int | None = None
    sampling_rate_hz: int | None = None
    duration_seconds: float | None = None
    channel_number: int | None = None
    start_mux: int | None = None
    start_time_samples: int | None = None
    acquisition_start_seconds: float | None = None
    time_since_mux_change_seconds: float | None = None
    num_reads_since_mux_change: int | None = None
    num_minknow_events: int | None = None
    minknow_event_rate_per_second: float | None = None
    median_before_pa: float | None = None
    open_pore_level_pa: float | None = None
    tracked_scaling_shift: float | None = None
    tracked_scaling_scale: float | None = None
    predicted_scaling_shift: float | None = None
    predicted_scaling_scale: float | None = None
    current_mean_pa: float | None = None
    current_median_pa: float | None = None
    current_stddev_pa: float | None = None
    current_mad_pa: float | None = None
    current_min_pa: float | None = None
    current_max_pa: float | None = None
    dorado_move_stride_samples: int | None = None
    dorado_emitted_bases: int | None = None
    mapped_signal_start_sample: int | None = None
    mapped_signal_end_sample: int | None = None
    mapped_signal_span_samples: int | None = None
    dorado_emission_rate_bases_per_second: float | None = None
    samples_per_aligned_reference_base: float | None = None
    signal_to_reference_dwell_mean_samples: float | None = None
    signal_to_reference_dwell_median_samples: float | None = None
    signal_to_reference_dwell_stddev_samples: float | None = None
    signal_to_reference_dwell_mad_samples: float | None = None


class OntSortableReadPageV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.sortable-read-page.v1"] = Field(alias="schema")
    job_id: str
    session_id: str
    slice_id: str
    authority_sha256: str
    selected_read_count: int
    overlapping_read_count: int
    capped: bool
    filtered_read_count: int
    sort_by: str
    sort_direction: Literal["asc", "desc"]
    null_order: Literal["last"]
    tie_breaker: list[Literal["read_id", "start_1based", "flags"]]
    signal_metrics_state: Literal["ready", "unavailable", "not_bound"]
    signal_metrics_artifact_sha256: str | None
    raw_representation_id: str | None
    mapping_metrics_state: Literal["not_bound"]
    metric_contract: Literal["bms.ont.literature-backed-read-metrics.v1"]
    reads: list[OntSortableReadV1]
    next_cursor: str | None
    limit: int


def _typed_errors(*statuses: int) -> dict[int | str, dict[str, Any]]:
    return {
        status: {"model": OntNgsErrorV2, "description": "Typed governed NGS failure"}
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
        "model": OntNgsErrorV2,
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
    "/read-overlays",
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
            "schema": "bms.ngs.error.v2",
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
    from services import ngs_native_alignment_sources as native
    if native.is_native(job):
        def verify():
            with native.result_root(job) as root:
                for artifact in native.artifact_descriptors(job):
                    service.verify_current_artifact_bytes(root / artifact["relative_path"],
                        expected_sha256=artifact["sha256"], expected_size=artifact["size_bytes"])
        await run_in_threadpool(verify)
        return
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
    external = urlsplit(str(request.base_url))
    supplied_origin = (supplied.scheme, supplied.netloc)
    configured_origin = (configured.scheme, configured.netloc)
    external_origin = (external.scheme, external.netloc)
    tailnet_user = request.headers.get("tailscale-user-login", "").strip()
    selected_tailnet_origin = (
        secure_transport
        and supplied.scheme == "https"
        and bool(supplied.hostname)
        and supplied.hostname.rstrip(".").casefold().endswith(".ts.net")
        and bool(tailnet_user)
    )
    allowed_origins = {
        configured_origin,
        external_origin if secure_transport else configured_origin,
    }
    if selected_tailnet_origin:
        allowed_origins.add(supplied_origin)
    if (
        request.headers.get("sec-fetch-site", "").lower() != "same-origin"
        or configured.scheme not in {"http", "https"}
        or not configured.netloc
        or supplied_origin not in allowed_origins
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
    from services import ont_pooled_reference_assignment as pooled
    from routers.experiment_workspaces import _operator_principal

    pooled_job = job.model_id == "nanopore" and getattr(job, "mode", None) == pooled.ASSIGNMENT_MODE
    if pooled_job:
        # Pooled jobs predate workspace hierarchy binding. Do not invent an owner
        # from a manifest path: use the existing application operator/admin lane.
        try:
            _operator_principal(request)
        except HTTPException as exc:
            raise OntNgsRouteError(
                status_code=403, code="NGS_PRINCIPAL_DENIED",
                message="Application operator authority is required to recover this pooled Job.",
                job_id=job_id, resource="rotation",
            ) from exc
    pooled_review = pooled_job and job.status in {"completed", "awaiting_input"} and (
        (job.params or {}).get("scientific_status") == "REVIEW"
        and (job.params or {}).get("release_state") == "awaiting_operator_release"
    )
    if job.model_id != "nanopore" or (pooled_job and not pooled_review) or (not pooled_job and job.status != "completed"):
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
        if pooled_job:
            await pooled.validate_pooled_reference_set_for_job(session, job)
        else:
            await _validate_rotation_package_authority(job)
    except (JobResultRootError, SequenceQcManifestError, OntNgsResultError, service.AlignmentSessionError, pooled.PooledAssignmentError) as exc:
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
        **({"pooled_review_params": dict(job.params)} if pooled_job else {}),
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
        await _validate_rotation_package_authority(job)
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
                # Abort an already-started response rather than silently deliver
                # a truncated object under an accepted digest/Content-Length.
                raise service.AlignmentSessionError("snapshot integrity: premature end of stream")
            remaining -= len(chunk)
            yield chunk
    finally:
        snapshot.close()


def _require_cache_retry_principal(request: Request, job_id: str) -> None:
    try:
        _mutation_principal(request)
    except HTTPException as exc:
        raise OntNgsRouteError(status_code=403, code="NGS_PRINCIPAL_DENIED",
            message="Job mutation authority is required.", job_id=job_id, resource="artifact") from exc


async def _retry_artifact_delivery(path: Path, metadata: dict, request: Request, job_id: str):
    _require_cache_retry_principal(request, job_id)
    try:
        await run_in_threadpool(service.retry_verified_artifact_cache, path,
            expected_size=int(metadata["size_bytes"]), expected_sha256=str(metadata["sha256"]))
    except service.AlignmentSessionError as exc:
        if "capacity" in str(exc).lower():
            return _ngs_error_response(status_code=503, code="NGS_READ_CAPACITY_UNAVAILABLE",
                message="Delivery cache is busy or capacity is unavailable. Retry explicitly.",
                job_id=job_id, resource="artifact")
        raise _http_error(exc, job_id=job_id, resource="artifact") from exc
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


async def _serve_artifact(
    path: Path,
    metadata: dict,
    request: Request,
    *,
    job_id: str,
) -> Response:
    if request.method == "POST":
        return await _retry_artifact_delivery(path, metadata, request, job_id)
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
    # A conditional response uses the same accepted delivery object as GET and
    # HEAD. Rehashing the mutable original here both defeats warm reuse and
    # assigns a different identity policy to 304 than to Range/full delivery.
    validators = [item.strip() for item in request.headers.get("if-none-match", "").split(",")]
    not_modified = any(item == "*" or item.removeprefix("W/") == etag for item in validators)

    start, end = 0, max(0, size - 1)
    status_code = 200
    range_header = request.headers.get("range") if request.method != "HEAD" and not not_modified else None
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
    except service.AlignmentSessionError as exc:
        capacity = any(term in str(exc).lower() for term in ("capacity", "snapshot limit"))
        return _ngs_error_response(
            status_code=503 if capacity else 409,
            code="NGS_READ_CAPACITY_UNAVAILABLE" if capacity else "NGS_ARTIFACT_INTEGRITY_CONFLICT",
            message="Verified artifact delivery capacity is unavailable." if capacity else "The governed artifact changed before delivery.",
            job_id=job_id, resource="artifact",
        )
    if not_modified:
        snapshot.close()
        return Response(status_code=304, headers=base_headers)
    if request.method == "HEAD":
        snapshot.close()
        return Response(status_code=200, headers=headers)
    return StreamingResponse(
        _iter_range(snapshot, start, end) if size else _iter_range(snapshot, 0, -1),
        background=BackgroundTask(snapshot.close),
        status_code=status_code,
        headers=headers,
        media_type=str(metadata["mime_type"]),
    )


@router.get(
    "/jobs/{job_id}/alignment-sessions",
    response_model=OntAlignmentSessionListV1 | OntNativeAlignmentSessionListV2,
    responses=_STANDARD_GOVERNED_ERRORS,
)
async def list_alignment_sessions(
    job_id: str,
    authorized_job: Job = Depends(require_alignment_job),
):
    from services import ngs_native_alignment_sources as native
    if native.is_native(authorized_job):
        try:
            def resolve():
                with native.result_root(authorized_job) as root:
                    return native.alignment_sessions(authorized_job, root)
            return {"schema": "bms.ngs.native-alignment-session-list.v2", "job_id": job_id,
                    "sessions": await run_in_threadpool(resolve)}
        except service.AlignmentSessionError as exc:
            raise _http_error(exc, job_id=job_id, resource="artifact") from exc
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
    response_model=OntAlignmentSessionDetailV1 | OntNativeAlignmentSessionDetailV2,
    responses=_STANDARD_GOVERNED_ERRORS,
)
async def get_alignment_session(
    job_id: str,
    session_id: str,
    authorized_job: Job = Depends(require_alignment_job),
):
    from services import ngs_native_alignment_sources as native
    if native.is_native(authorized_job):
        result = await list_alignment_sessions(job_id, authorized_job)
        current = next((item for item in result["sessions"] if item["session_id"] == session_id), None)
        if current is None:
            raise OntNgsRouteError(status_code=404, code="NGS_RESOURCE_NOT_FOUND",
                message="Native alignment session was not found.", job_id=job_id, resource="artifact")
        return {"schema": "bms.ngs.native-alignment-session-detail.v2", "job_id": job_id, "session": current}
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
    from services import ngs_native_alignment_sources as native
    if native.is_native(authorized_job):
        try:
            artifacts = await run_in_threadpool(native.artifact_descriptors, authorized_job)
            return {"job_id": job_id, "artifacts": [{**{key: value for key, value in item.items() if key != "relative_path"},
                "url": f"/api/jobs/{job_id}/ngs-artifacts/{item['artifact_id']}"} for item in artifacts]}
        except service.AlignmentSessionError as exc:
            raise _http_error(exc, job_id=job_id, resource="artifact") from exc
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


@router.post("/jobs/{job_id}/ngs-artifacts/{artifact_id}/cache/retry", status_code=204, responses=_STANDARD_GOVERNED_ERRORS)
@router.get("/jobs/{job_id}/ngs-artifacts/{artifact_id}", responses=_BINARY_RESPONSES)
@router.head("/jobs/{job_id}/ngs-artifacts/{artifact_id}", responses=_BINARY_RESPONSES)
async def get_ngs_package_artifact(
    job_id: str,
    artifact_id: str,
    request: Request,
    authorized_job: Job = Depends(require_alignment_job),
):
    from services import ngs_native_alignment_sources as native
    if native.is_native(authorized_job):
        try:
            artifacts = await run_in_threadpool(native.artifact_descriptors, authorized_job)
            metadata = next((item for item in artifacts if item["artifact_id"] == artifact_id), None)
            if metadata is None:
                raise service.AlignmentSessionError("native artifact not found")
            with native.result_root(authorized_job) as root:
                return await _serve_artifact(root / metadata["relative_path"], metadata, request, job_id=job_id)
        except service.AlignmentSessionError as exc:
            raise _http_error(exc, job_id=job_id, resource="artifact") from exc
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


@router.post("/jobs/{job_id}/alignment-artifacts/{artifact_id}/cache/retry", status_code=204, responses=_STANDARD_GOVERNED_ERRORS)
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


@router.post("/jobs/{job_id}/alignment-session-artifacts/{mode}/{role}/{sha256}/cache/retry", status_code=204, responses=_STANDARD_GOVERNED_ERRORS)
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


def _presentation_authority(row: NgsAlignmentPresentationJob) -> tuple[str, str]:
    authority_sha256 = row.authority_sha256
    manifest_sha256 = row.manifest_sha256
    if (
        row.state != "ready"
        or not isinstance(authority_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", authority_sha256) is None
        or not isinstance(manifest_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is None
    ):
        raise service.AlignmentSessionError("alignment presentation authority is invalid")
    return authority_sha256, manifest_sha256


async def _presentation_row(
    session: AsyncSession, job_id: str, session_id: str, authority_sha256: str | None = None,
) -> NgsAlignmentPresentationJob:
    try:
        row = await presentation_lifecycle.get_session_presentation(
            session, job_id=job_id, session_id=session_id, authority_sha256=authority_sha256,
        )
    except presentation_lifecycle.PresentationSourceStale as exc:
        raise OntNgsRouteError(status_code=409, code="NGS_AUTHORITY_CONFLICT",
            message=str(exc), job_id=job_id, resource="presentation") from exc
    if row is None:
        raise OntNgsRouteError(
            status_code=404, code="NGS_RESOURCE_NOT_FOUND",
            message="The governed NGS presentation was not found.",
            job_id=job_id, resource="presentation",
        )
    return row


@asynccontextmanager
async def _prepared_presentation(
    job_id: str,
    session_id: str,
    job: Job,
    presentation_row: NgsAlignmentPresentationJob | None = None,
):
    if presentation_row is None:
        async with async_session() as db:
            presentation_row = await _presentation_row(db, job_id, session_id)
    authority_sha256, manifest_sha256 = _presentation_authority(presentation_row)
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


async def _prepare_presentation(
    job_id: str, session_id: str, job: Job,
    presentation_row: NgsAlignmentPresentationJob | None = None,
) -> dict[str, Any]:
    async with _prepared_presentation(
        job_id, session_id, job, presentation_row,
    ) as (package, _pinned_result_root):
        return package


def _presentation_response(
    job_id: str,
    session_id: str,
    row: NgsAlignmentPresentationJob,
    package: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_response = {
        "schema": "bms.ngs.alignment-presentation.v2",
        "job_id": job_id,
        "session_id": session_id,
        "mode": row.mode,
    }
    if row.state in {"requested", "running"}:
        return {**base_response, "state": "preparing"}
    if row.state == "failed":
        return {
            **base_response,
            "state": "failed",
            "code": row.error_code,
            "message": "Reads unavailable. Retry from Diagnostics.",
        }
    if package is None:
        raise service.AlignmentSessionError("ready presentation package is unavailable")
    manifest = package["manifest"]
    base = (
        f"/api/jobs/{job_id}/alignment-sessions/{session_id}/presentation/"
        f"{manifest['authority_sha256']}"
    )
    return {
        **base_response,
        "state": "ready",
        "presentation_id": row.id,
        "source": {
            "package_manifest_sha256": manifest["package_manifest_sha256"],
            "artifact_set_sha256": manifest["artifact_set_sha256"],
            "alignment_sha256": manifest["source_alignment_sha256"],
            "alignment_size_bytes": manifest["source_alignment_size_bytes"],
            "alignment_index_sha256": manifest["source_index_sha256"],
            "alignment_index_size_bytes": manifest["source_index_size_bytes"],
            "logical_read_count": manifest["source_logical_read_count"],
            "alignment_record_count": manifest["source_alignment_record_count"],
        },
        "policy": {
            "id": manifest["policy"]["id"],
            "version": manifest["policy"]["version"],
            "target_reads": manifest["policy"]["target_reads"],
            "max_preview_records": manifest["policy"]["max_preview_records"],
            "max_preview_bytes": manifest["policy"]["max_preview_bytes"],
            "max_build_seconds": manifest["policy"]["max_build_seconds"],
        },
        "preview": {
            "selected_read_count": manifest["selected_read_count"],
            "selected_record_count": manifest["selected_alignment_record_count"],
            "selected_read_set_sha256": manifest["selected_read_set_sha256"],
            "preview_complete_to_target": manifest["preview_complete_to_target"],
            "bam": _derived_descriptor(package["bam_metadata"], f"{base}/bam"),
            "index": _derived_descriptor(package["index_metadata"], f"{base}/bai"),
        },
        "catalog": manifest["catalog"],
        "locators": {
            "schema": manifest["locators"]["schema"],
            "record_count": manifest["locators"]["record_count"],
            "content_sha256": manifest["locators"]["content_sha256"],
            "size_bytes": manifest["locators"]["size_bytes"],
        },
        "coverage": {
            "kind": "full_source_primary_coverage",
            "primary_read_count": manifest["coverage_primary_read_count"],
            "descriptor": _derived_descriptor(package["coverage_metadata"], f"{base}/coverage"),
        },
        "manifest": _derived_descriptor(package["manifest_metadata"], f"{base}/manifest"),
    }


def _require_current_locus_authority(receipt: dict[str, Any], presentation: dict[str, Any]) -> None:
    current = presentation["manifest"]
    current_source_fields = {
        "source_manifest_sha256": current.get("source_manifest_sha256"),
        "source_alignment_sha256": current.get("source_alignment_sha256"),
        "source_alignment_size_bytes": current.get("source_alignment_size_bytes"),
        "source_index_sha256": current.get("source_index_sha256"),
        "source_index_size_bytes": current.get("source_index_size_bytes"),
        "source_identity": current.get("source_identity"),
        "source_index_identity": current.get("source_index_identity"),
    }
    if any(receipt.get(key) != value for key, value in current_source_fields.items()):
        raise service.AlignmentSessionError("alignment locus slice source authority is stale")
    if (
        receipt.get("presentation_authority_sha256") != current.get("authority_sha256")
        or receipt.get("presentation_manifest_sha256") != presentation["manifest_metadata"].get("sha256")
    ):
        raise service.AlignmentSessionError("alignment locus slice presentation authority is stale")


@router.get(
    "/jobs/{job_id}/alignment-sessions/{session_id}/presentation",
    response_model=OntAlignmentPresentationV2,
    responses={**_STANDARD_GOVERNED_ERRORS, **_typed_errors(410)},
)
async def get_alignment_presentation(
    job_id: str,
    session_id: str,
    authorized_job: Job = Depends(require_alignment_job),
    db: AsyncSession = Depends(get_session),
):
    row = await _presentation_row(db, job_id, session_id)
    if row.state != "ready":
        raise OntNgsRouteError(status_code=410, code="NGS_LEGACY_MUTATION_RETIRED",
            message="This legacy request has no active builder. Use split product status or operator historical backfill.",
            job_id=job_id, resource="presentation")
    try:
        async with _prepared_presentation(
            job_id, session_id, authorized_job, row,
        ) as (package, _pinned_result_root):
            return _presentation_response(job_id, session_id, row, package)
    except service.AlignmentSessionError as exc:
        raise _http_error(exc, job_id=job_id, resource="artifact") from exc


@router.post(
    "/jobs/{job_id}/alignment-sessions/{session_id}/presentation/retry",
    response_model=OntAlignmentPresentationV2,
    responses={**_STANDARD_GOVERNED_ERRORS, **_typed_errors(410)},
)
async def retry_alignment_presentation(
    job_id: str,
    session_id: str,
    request: Request,
    authorized_job: Job = Depends(require_alignment_job),
    db: AsyncSession = Depends(get_session),
):
    try:
        _mutation_principal(request)
    except HTTPException as exc:
        raise OntNgsRouteError(
            status_code=403,
            code="NGS_PRINCIPAL_DENIED",
            message="Job mutation authority is required.",
            job_id=job_id,
            resource="presentation",
        ) from exc
    raise OntNgsRouteError(
        status_code=410, code="NGS_LEGACY_MUTATION_RETIRED",
        message="Combined presentation retries are retired. Use source-bound catalog/preview retry; historical requests require operator backfill.",
        job_id=job_id, resource="presentation",
    )

async def _current_derived_products(
    db: AsyncSession, job: Job, session_id: str, preview_request_id: str | None = None,
    *, include_preview: bool = True,
) -> tuple[NgsAlignmentDerivedProduct | None, NgsAlignmentDerivedProduct | None]:
    # Scientific completion receipts, not row timestamps, select the current
    # catalog generation. Old combined receipts are never relabelled as v2.
    provenance = job.provenance if isinstance(job.provenance, dict) else {}
    integrity = provenance.get("result_integrity")
    receipts = integrity.get("alignment_presentations", []) if isinstance(integrity, dict) else []
    matches = [item for item in receipts if isinstance(item, dict)
               and item.get("session_id") == session_id
               and str(item.get("request_id", "")).startswith("ngs-catalog-")]
    if not matches:
        from services.ngs_historical_backfill import current_historical_catalog
        try:
            catalog = await current_historical_catalog(db, job, session_id)
        except presentation_lifecycle.PresentationSourceStale as exc:
            raise OntNgsRouteError(status_code=409, code="NGS_PRESENTATION_SOURCE_STALE",
                message=str(exc), job_id=str(job.id), resource="presentation") from exc
        if catalog is None:
            return None, None
        matches = [{"request_id": catalog.id, "source_authority_sha256": catalog.source_authority_sha256}]
    if len(matches) != 1:
        raise OntNgsRouteError(status_code=409, code="NGS_AUTHORITY_CONFLICT",
            message="Catalog ownership is ambiguous.", job_id=str(job.id), resource="presentation")
    receipt = matches[0]
    catalog = await db.get(NgsAlignmentDerivedProduct, receipt["request_id"])
    if catalog is None:
        return None, None
    if (catalog.job_id != str(job.id) or catalog.session_id != session_id
            or catalog.product != "catalog"
            or catalog.source_authority_sha256 != receipt.get("source_authority_sha256")
            or catalog.source_authority_sha256 != derived_products.identity_sha256(catalog.source_identity)
            or derived_products.catalog_request_id(catalog.source_identity) != catalog.id):
        raise OntNgsRouteError(status_code=409, code="NGS_AUTHORITY_CONFLICT",
            message="Catalog receipt does not match its scientific source.",
            job_id=str(job.id), resource="presentation")
    if not include_preview:
        return catalog, None
    preview = None
    if preview_request_id is None:
        preview_request_id = derived_products.default_preview_request_id(catalog)
        preview = await db.get(NgsAlignmentDerivedProduct, preview_request_id)
        if preview is None:
            return catalog, None
    if preview_request_id is not None:
        preview = await db.get(NgsAlignmentDerivedProduct, preview_request_id)
        if (preview is None or preview.product != "preview" or preview.catalog_request_id != catalog.id
                or preview.job_id != str(job.id) or preview.session_id != session_id
                or preview.source_authority_sha256 != catalog.source_authority_sha256
                or preview.id != "ngs-preview-" + derived_products.identity_sha256(preview.request_contract)
                or preview.request_contract.get("catalog_request_id") != catalog.id):
            raise OntNgsRouteError(status_code=409, code="NGS_AUTHORITY_CONFLICT",
                message="Preview request does not belong to the current catalog.",
                job_id=str(job.id), resource="presentation")
    return catalog, preview


def _derived_response(job_id: str, session_id: str, catalog, preview):
    return {"schema": "bms.ngs.alignment-presentation.v3", "job_id": job_id,
            "session_id": session_id, "catalog": derived_products.product_status(catalog),
            "preview": derived_products.product_status(preview, catalog=catalog)}


@router.get("/jobs/{job_id}/alignment-sessions/{session_id}/presentation/status",
            response_model=OntAlignmentPresentationV3,
            responses=_STANDARD_GOVERNED_ERRORS)
async def get_alignment_derived_status(
    job_id: str, session_id: str, preview_request_id: str | None = Query(default=None, max_length=96),
    authorized_job: Job = Depends(require_alignment_job), db: AsyncSession = Depends(get_session),
):
    catalog, preview = await _current_derived_products(db, authorized_job, session_id, preview_request_id)
    return _derived_response(job_id, session_id, catalog, preview)


async def _current_derived_source(job: Job, row: NgsAlignmentDerivedProduct) -> str:
    current, _inputs = await run_in_threadpool(derived_products.resolve_product_source, job, row)
    return derived_products.identity_sha256(current)


@router.post("/jobs/{job_id}/alignment-sessions/{session_id}/presentation/products/{product}/retry",
             response_model=OntAlignmentPresentationV3,
             responses=_STANDARD_GOVERNED_ERRORS)
async def retry_alignment_derived_product(
    job_id: str, session_id: str, product: Literal["catalog", "preview"],
    payload: OntDerivedRetryV3, request: Request,
    authorized_job: Job = Depends(require_alignment_job), db: AsyncSession = Depends(get_session),
):
    try:
        _mutation_principal(request)
    except HTTPException as exc:
        raise OntNgsRouteError(status_code=403, code="NGS_PRINCIPAL_DENIED",
            message="Job mutation authority is required.", job_id=job_id, resource="presentation") from exc
    catalog, preview = await _current_derived_products(
        db, authorized_job, session_id, payload.request_id if product == "preview" else None)
    row = catalog if product == "catalog" else preview
    if row is None or row.id != payload.request_id:
        raise OntNgsRouteError(status_code=404, code="NGS_RESOURCE_NOT_FOUND",
            message="The current derived request was not found.", job_id=job_id, resource="presentation")
    try:
        current_source = await _current_derived_source(authorized_job, row)
        await derived_products.retry_product(db, row, current_source_authority_sha256=current_source)
    except presentation_lifecycle.PresentationAlreadyReady as exc:
        raise OntNgsRouteError(status_code=409, code="NGS_PRESENTATION_ALREADY_READY",
            message="This product is already ready.", job_id=job_id, resource="presentation") from exc
    except (presentation_lifecycle.PresentationSourceStale, service.AlignmentSessionError, ValueError) as exc:
        raise OntNgsRouteError(status_code=409, code="NGS_PRESENTATION_SOURCE_STALE",
            message="The source is no longer valid for this request. Reopen the accepted native result.",
            job_id=job_id, resource="presentation") from exc
    except presentation_lifecycle.PresentationClaimLost as exc:
        raise OntNgsRouteError(status_code=409, code="NGS_AUTHORITY_CONFLICT",
            message="Retry conflicted with another transition. Refresh the product state.",
            job_id=job_id, resource="presentation") from exc
    return _derived_response(job_id, session_id, catalog, preview)


@router.post("/jobs/{job_id}/alignment-sessions/{session_id}/presentation/products/{product}/cache/retry",
             response_model=OntAlignmentPresentationV3, responses=_STANDARD_GOVERNED_ERRORS)
async def retry_alignment_product_delivery_cache(
    job_id: str, session_id: str, product: Literal["catalog", "preview"],
    payload: OntDerivedRetryV3, request: Request,
    authorized_job: Job = Depends(require_alignment_job), db: AsyncSession = Depends(get_session),
):
    try:
        _mutation_principal(request)
    except HTTPException as exc:
        raise OntNgsRouteError(status_code=403, code="NGS_PRINCIPAL_DENIED",
            message="Job mutation authority is required.", job_id=job_id, resource="presentation") from exc
    catalog, preview = await _current_derived_products(
        db, authorized_job, session_id, payload.request_id if product == "preview" else None)
    row = catalog if product == "catalog" else preview
    if row is None or row.id != payload.request_id or row.state != "ready":
        raise OntNgsRouteError(status_code=409, code="NGS_AUTHORITY_CONFLICT",
            message="The exact ready product is required for delivery-cache retry.",
            job_id=job_id, resource="presentation")
    from services.ngs_alignment_catalog_reader import retry_delivery_cache
    try:
        await run_in_threadpool(retry_delivery_cache, authorized_job, row,
            Path(resolve_persisted_job_result_root(authorized_job)) / ".alignment-products")
    except presentation_lifecycle.PresentationSourceStale as exc:
        raise OntNgsRouteError(status_code=409, code="NGS_PRESENTATION_SOURCE_STALE",
            message="The accepted source changed. Reopen the native result.",
            job_id=job_id, resource="presentation") from exc
    except service.AlignmentSessionError as exc:
        if "capacity" in str(exc).lower():
            return _ngs_error_response(status_code=503, code="NGS_READ_CAPACITY_UNAVAILABLE",
                message="Delivery-cache capacity is unavailable or readers are still active. Close readers and retry explicitly.",
                job_id=job_id, resource="artifact")
        raise _http_error(exc, job_id=job_id, resource="artifact") from exc
    return _derived_response(job_id, session_id, catalog, preview)


@router.get("/jobs/{job_id}/alignment-sessions/{session_id}/presentation/{kind}", responses=_BINARY_RESPONSES)
@router.head("/jobs/{job_id}/alignment-sessions/{session_id}/presentation/{kind}", responses=_BINARY_RESPONSES)
@router.get("/jobs/{job_id}/alignment-sessions/{session_id}/presentation/{presentation_id}/{kind}", responses=_BINARY_RESPONSES)
@router.head("/jobs/{job_id}/alignment-sessions/{session_id}/presentation/{presentation_id}/{kind}", responses=_BINARY_RESPONSES)
async def get_alignment_presentation_artifact(job_id: str, session_id: str, kind: str, request: Request,
                                               authorized_job: Job = Depends(require_alignment_job),
                                               presentation_id: str | None = None,
                                               db: AsyncSession = Depends(get_session)):
    if kind not in {"bam", "bai", "coverage", "manifest"}:
        raise OntNgsRouteError(status_code=404, code="NGS_RESOURCE_NOT_FOUND",
                               message="The governed presentation artifact was not found.",
                               job_id=job_id, resource="artifact")
    try:
        row = await _presentation_row(db, job_id, session_id, presentation_id)
        async with _prepared_presentation(job_id, session_id, authorized_job, row) as (package, _pinned_result_root):
            if presentation_id is not None and presentation_id != package["manifest"].get("authority_sha256"):
                raise service.AlignmentSessionError("alignment presentation identity does not match")
            path_key, metadata_key = {"bam": ("bam_path", "bam_metadata"), "bai": ("index_path", "index_metadata"),
                                      "coverage": ("coverage_path", "coverage_metadata"),
                                      "manifest": ("manifest_path", "manifest_metadata")}[kind]
            return await _serve_artifact(package[path_key], package[metadata_key], request, job_id=job_id)
    except service.AlignmentSessionError as exc:
        raise _http_error(exc, job_id=job_id, resource="artifact") from exc


@router.post("/jobs/{job_id}/alignment-sessions/{session_id}/locus-slices",
             response_model=OntAlignmentLocusSliceV1, responses=_typed_errors(400, 403, 404, 409, 410))
async def create_alignment_locus_slice(job_id: str, session_id: str, body: OntAlignmentLocusSliceRequestV1,
                                       authorized_job: Job = Depends(require_alignment_job)):
    raise OntNgsRouteError(
        status_code=410, code="NGS_LEGACY_MUTATION_RETIRED",
        message="Legacy locus construction is retired. Use catalog queries and exact-read overlays; existing historical downloads remain available.",
        job_id=job_id, resource="presentation",
    )

@router.get("/jobs/{job_id}/alignment-sessions/{session_id}/locus-slices/{slice_id}/{artifact_sha256}/{kind}", responses=_BINARY_RESPONSES)
@router.head("/jobs/{job_id}/alignment-sessions/{session_id}/locus-slices/{slice_id}/{artifact_sha256}/{kind}", responses=_BINARY_RESPONSES)
async def get_alignment_locus_slice_artifact(job_id: str, session_id: str, slice_id: str,
                                              artifact_sha256: str, kind: str,
                                              request: Request, authorized_job: Job = Depends(require_alignment_job)):
    if kind not in {"bam", "bai", "manifest"}:
        raise OntNgsRouteError(status_code=404, code="NGS_RESOURCE_NOT_FOUND",
                               message="The governed locus artifact was not found.", job_id=job_id, resource="artifact")
    try:
        async with _prepared_presentation(job_id, session_id, authorized_job) as (presentation, _pinned_result_root):
            package = await run_in_threadpool(service.resolve_cached_alignment_locus_slice, slice_id)
            receipt = package["receipt"]
            if receipt.get("job_id") != job_id or receipt.get("session_id") != session_id:
                raise service.AlignmentSessionError("alignment locus slice not found")
            _require_current_locus_authority(receipt, presentation)
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


@router.get(
    "/jobs/{job_id}/alignment-sessions/{session_id}/locus-slices/{slice_id}/reads",
    response_model=OntSortableReadPageV1,
    responses=_READ_ERRORS,
)
async def list_sortable_alignment_reads(
    job_id: str,
    session_id: str,
    slice_id: str,
    sort_by: str = Query(default="mean_quality"),
    sort_direction: str = Query(default="desc"),
    q: str | None = Query(default=None),
    metric_min: str | None = Query(default=None),
    metric_max: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: str | None = Query(default=None),
    raw_run_id: str | None = Query(default=None),
    raw_observed_generation: str | None = Query(default=None),
    raw_representation_id: str | None = Query(default=None),
    authorized_job: Job = Depends(require_alignment_job),
    db: AsyncSession = Depends(get_session),
):
    try:
        parsed_limit = int(limit) if limit is not None else 50
        parsed_min = float(metric_min) if metric_min is not None else None
        parsed_max = float(metric_max) if metric_max is not None else None
        raw_values = (raw_run_id, raw_observed_generation, raw_representation_id)
        raw_bound = all(value not in (None, "") for value in raw_values)
        if any(value not in (None, "") for value in raw_values) and not raw_bound:
            raise ValueError("raw-signal authority is incomplete")
        parsed_generation = int(str(raw_observed_generation)) if raw_bound else None
        if (
            sort_by not in service.SORTABLE_READ_FIELDS
            or sort_direction not in {"asc", "desc"}
            or parsed_limit < 1
            or parsed_limit > service.MAX_READ_PAGE
            or (q is not None and len(q) > 255)
            or (cursor is not None and len(cursor) > service.MAX_SORTABLE_READ_CURSOR_BYTES)
            or (parsed_generation is not None and parsed_generation < 1)
            or (parsed_min is not None and not math.isfinite(parsed_min))
            or (parsed_max is not None and not math.isfinite(parsed_max))
            or (parsed_min is not None and parsed_max is not None and parsed_min > parsed_max)
        ):
            raise ValueError("sortable read parameters are invalid")
    except (TypeError, ValueError, OverflowError):
        return _ngs_error_response(
            status_code=400,
            code="NGS_RANGE_INVALID",
            message="The sortable read query parameters are invalid.",
            job_id=job_id,
            resource="read",
        )

    if raw_bound:
        job_params = authorized_job.params if isinstance(authorized_job.params, dict) else {}
        if (
            job_params.get("source_instrument_run_id") != raw_run_id
            or job_params.get("source_instrument_observed_generation") != parsed_generation
        ):
            raise _http_error(
                service.AlignmentSessionError("raw-signal authority does not match persisted job authority"),
                job_id=job_id,
                resource="artifact",
            )

    try:
        async with _prepared_presentation(job_id, session_id, authorized_job) as (presentation, _pinned_result_root):
            package = await run_in_threadpool(service.resolve_cached_alignment_locus_slice, slice_id)
            receipt = package["receipt"]
            if receipt.get("job_id") != job_id or receipt.get("session_id") != session_id:
                raise service.AlignmentSessionError("alignment locus slice not found")
            _require_current_locus_authority(receipt, presentation)
            reads = await run_in_threadpool(
                service.read_locus_primary_rows,
                package["bam_path"],
                bam_sha256=package["bam_metadata"]["sha256"],
                bam_size_bytes=package["bam_metadata"]["size_bytes"],
                index=package["index_path"],
                index_sha256=package["index_metadata"]["sha256"],
                index_size_bytes=package["index_metadata"]["size_bytes"],
            )
            if len(reads) != int(receipt.get("selected_read_count", -1)):
                raise service.AlignmentSessionError("sortable read population diverged from locus authority")

            raw_metrics: dict[str, dict[str, Any]] = {}
            metric_artifact = None
            representation = None
            signal_metrics_state = "not_bound"
            if raw_bound:
                representation = await db.get(OntRawSignalRepresentation, str(raw_representation_id))
                if (
                    representation is None
                    or representation.run_id != raw_run_id
                    or representation.observed_generation != parsed_generation
                    or representation.state != "ready"
                    or representation.format != "blow5"
                ):
                    raise service.AlignmentSessionError("raw-signal representation authority does not match")
                try:
                    raw_metrics, metric_artifact = await load_read_metrics_for_ids(
                        db,
                        representation=representation,
                        read_ids=[str(row["read_id"]) for row in reads],
                    )
                except OntReadMetricError as exc:
                    raise service.AlignmentSessionError(str(exc)) from exc
                signal_metrics_state = "ready" if metric_artifact is not None else "unavailable"

            enriched = await run_in_threadpool(service.enrich_locus_read_metrics, reads, raw_metrics)
            creation_revision, creation_source_tree = service._creation_authority()
            authority = {
                "schema": "bms.ngs.sortable-read-authority.v1",
                "slice_id": slice_id,
                "slice_manifest_sha256": package["manifest_metadata"]["sha256"],
                "slice_bam_sha256": package["bam_metadata"]["sha256"],
                "slice_index_sha256": package["index_metadata"]["sha256"],
                "package_authority": presentation.get("source") if isinstance(presentation, dict) else None,
                "raw_representation_id": representation.id if representation is not None else None,
                "raw_representation_manifest_sha256": representation.manifest_sha256 if representation is not None else None,
                "signal_metrics_artifact_sha256": metric_artifact.content_sha256 if metric_artifact is not None else None,
                "metric_contract": RAW_READ_METRICS_CONTRACT,
                "mapping_authority": {"state": "not_bound"},
                "creation_revision": creation_revision,
                "creation_source_tree": creation_source_tree,
            }
            authority_sha256 = hashlib.sha256(rfc8785.dumps(authority)).hexdigest()
            page = await run_in_threadpool(
                service.sort_locus_read_metrics_page,
                enriched,
                authority_sha256=authority_sha256,
                sort_by=sort_by,
                sort_direction=sort_direction,
                query=q,
                metric_min=parsed_min,
                metric_max=parsed_max,
                cursor=cursor,
                limit=parsed_limit,
            )
            return {
                "schema": "bms.ngs.sortable-read-page.v1",
                "job_id": job_id,
                "session_id": session_id,
                "slice_id": slice_id,
                "authority_sha256": authority_sha256,
                "selected_read_count": len(enriched),
                "overlapping_read_count": int(receipt["overlapping_read_count"]),
                "capped": bool(receipt["capped"]),
                "filtered_read_count": page["filtered_read_count"],
                "sort_by": sort_by,
                "sort_direction": sort_direction,
                "null_order": page["null_order"],
                "tie_breaker": page["tie_breaker"],
                "signal_metrics_state": signal_metrics_state,
                "signal_metrics_artifact_sha256": metric_artifact.content_sha256 if metric_artifact is not None else None,
                "raw_representation_id": representation.id if representation is not None else None,
                "mapping_metrics_state": "not_bound",
                "metric_contract": RAW_READ_METRICS_CONTRACT,
                "reads": page["reads"],
                "next_cursor": page["next_cursor"],
                "limit": parsed_limit,
            }
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


from services.ngs_alignment_catalog_query import SORT_FIELDS as CATALOG_SORT_FIELDS, unavailable_signal


class OntCatalogReadV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    read_id: str
    source_record_count: int
    mapped_primary_count: int
    supplementary_count: int
    mapped_supplementary_count: int
    secondary_count: int
    unmapped_count: int
    alignment_state: Literal["mapped_primary", "ambiguous_primary", "unmapped", "no_primary"]
    length: int | None
    mean_quality: float | None
    contig: str | None
    start_1based: int | None
    alignment_end_1based: int | None
    strand: str | None
    mapq: int | None
    cigar: str | None
    flags: int | None
    unmapped: bool
    aligned_query_bases: int | None
    aligned_reference_bases: int | None
    inserted_bases: int | None
    deleted_bases: int | None
    skipped_reference_bases: int | None
    clipped_bases: int | None
    edit_distance: int | None
    reference_substitution_count: int | None
    reference_substitution_rate: float | None
    aligned_fraction: float | None
    clipped_fraction: float | None
    reference_disagreement_rate: float | None
    in_preview: bool | None
    overlay_eligible: bool
    overlay_unavailable_reason: Literal["already_in_preview", "unmapped", "ambiguous_primary", "no_primary", "record_limit", "writer_unsupported", "byte_limit"] | None
    signal_available: bool
    sample_count: int | None = None
    duration_seconds: float | None = None
    sampling_rate_hz: int | None = None
    current_mean_pa: float | None = None
    current_median_pa: float | None = None
    current_stddev_pa: float | None = None
    current_mad_pa: float | None = None
    current_min_pa: float | None = None
    current_max_pa: float | None = None
    channel_number: int | None = None
    start_mux: int | None = None
    acquisition_start_seconds: float | None = None
    time_since_mux_change_seconds: float | None = None
    median_before_pa: float | None = None
    open_pore_level_pa: float | None = None
    minknow_event_rate_per_second: float | None = None
    dorado_emission_rate_bases_per_second: float | None = None
    mapped_signal_span_samples: int | None = None
    samples_per_aligned_reference_base: float | None = None


class OntCatalogRecordV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    read_id: str
    source_record_ordinal: int
    record_class: Literal["primary", "supplementary", "secondary", "unmapped"]
    length: int | None
    mean_quality: float | None
    contig: str | None
    start_1based: int | None
    alignment_end_1based: int | None
    strand: Literal["+", "-"]
    mapq: int | None
    cigar: str | None
    flags: int
    unmapped: bool
    sequence: str | None = None
    quality: str | None = None


class OntCatalogEnvelopeV2(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    job_id: str
    session_id: str
    population_id: str
    catalog_authority_sha256: str
    preview_authority: str | None
    signal_snapshot_id: str | None
    signal_metrics_state: Literal["ready", "unavailable", "invalid"] = "unavailable"
    signal_metrics_artifact_sha256: str | None = None
    signal_cache_retry_sha256: str | None = None
    raw_run_id: str | None = None
    raw_observed_generation: int | None = None
    raw_representation_id: str | None = None
    raw_representation_manifest_sha256: str | None = None


class OntCatalogPageV3(OntCatalogEnvelopeV2):
    schema_version: Literal["bms.ngs.read-page.v3"] = Field(alias="schema")
    source_read_count: int
    source_record_count: int
    filtered_read_count: int
    reads: list[OntCatalogReadV2]
    next_cursor: str | None
    limit: int
    sort_by: str
    sort_direction: Literal["asc", "desc"]
    locus: dict[str, str | int] | None
    null_order: Literal["last"]
    tie_breaker: list[Literal["read_id"]]


class OntCatalogLookupResponseV2(OntCatalogEnvelopeV2):
    schema_version: Literal["bms.ngs.read-lookup.v2"] = Field(alias="schema")
    read: OntCatalogReadV2
    sequence_available: bool
    record: OntCatalogRecordV2 | None


class OntCatalogRecordPageV2(OntCatalogEnvelopeV2):
    schema_version: Literal["bms.ngs.alignment-record-page.v2"] = Field(alias="schema")
    read_id: str
    total_record_count: int
    records: list[OntCatalogRecordV2]
    next_cursor: str | None
    limit: int


class OntCatalogLookupV2(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, strict=True)
    schema_version: Literal["bms.ngs.read-lookup-request.v2"] = Field(alias="schema")
    raw_run_id: str | None = None
    raw_observed_generation: int | None = Field(default=None, ge=1)
    raw_representation_id: str | None = None
    read_id: str = Field(min_length=1, max_length=254)
    include_sequence: bool = False
    population_id: str | None = None


class OntCatalogRecordQueryV2(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, strict=True)
    schema_version: Literal["bms.ngs.alignment-record-query.v2"] = Field(alias="schema")
    raw_run_id: str | None = None
    raw_observed_generation: int | None = Field(default=None, ge=1)
    raw_representation_id: str | None = None
    read_id: str = Field(min_length=1, max_length=254)
    include_sequence: bool = False
    population_id: str | None = None
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def sequence_page_limit(self):
        if self.include_sequence and self.limit > 10:
            raise ValueError("Sequence record pages allow at most 10 records.")
        return self


async def _catalog_signal(job, db, raw_run_id=None, raw_observed_generation=None, raw_representation_id=None):
    from services.ont_read_metrics import find_read_metric_receipt, _receipt_reference, RAW_READ_METRICS_OWNER_KIND, RAW_READ_METRICS_ROLE
    from database import ScientificArtifactReceipt
    state = unavailable_signal(raw_run_id=raw_run_id, raw_observed_generation=raw_observed_generation,
                               raw_representation_id=raw_representation_id)
    if all(value is None for value in (raw_run_id, raw_observed_generation, raw_representation_id)):
        return state
    params = job.params if isinstance(job.params, dict) else {}
    if (not raw_run_id or type(raw_observed_generation) is not int or raw_observed_generation < 1
            or not raw_representation_id or params.get("source_instrument_run_id") != raw_run_id
            or params.get("source_instrument_observed_generation") != raw_observed_generation):
        raise OntNgsRouteError(status_code=400, code="NGS_READ_QUERY_INVALID",
            message="Raw selectors must match the persisted scientific input.", job_id=str(job.id), resource="read")
    representation = await db.get(OntRawSignalRepresentation, raw_representation_id)
    if representation is None or representation.state != "ready":
        return state
    if (representation.run_id != raw_run_id or representation.observed_generation != raw_observed_generation
            or representation.format != "blow5"):
        raise OntNgsRouteError(status_code=400, code="NGS_READ_QUERY_INVALID",
            message="Raw representation is not bound to this scientific input.", job_id=str(job.id), resource="read")
    if not representation.manifest_sha256 or re.fullmatch(r"[0-9a-f]{64}", representation.manifest_sha256) is None:
        state["signal_metrics_state"] = "invalid"
        return state
    try:
        receipt = await find_read_metric_receipt(db, representation)
    except OntReadMetricError:
        state["raw_representation_manifest_sha256"] = representation.manifest_sha256
        state["signal_metrics_state"] = "invalid"
        return state
    if receipt is not None:
        state["raw_representation_manifest_sha256"] = representation.manifest_sha256
        state["artifact"] = _receipt_reference(receipt)
    else:
        purported = await db.scalar(select(ScientificArtifactReceipt.artifact_id).where(
            ScientificArtifactReceipt.owner_kind == RAW_READ_METRICS_OWNER_KIND,
            ScientificArtifactReceipt.owner_id == representation.id,
            ScientificArtifactReceipt.role == RAW_READ_METRICS_ROLE,
            ScientificArtifactReceipt.availability == "available",
        ).limit(1))
        if purported is not None:
            state["raw_representation_manifest_sha256"] = representation.manifest_sha256
            state["signal_metrics_state"] = "invalid"
    return state


async def _catalog_query(job, session_id, db, operation, **kwargs):
    from services import ngs_alignment_catalog_reader as reader
    from services.scientific_artifacts.query import ScientificArtifactQueryCapacityUnavailable
    if "read_id" in kwargs:
        try:
            reader.records._validate_read_id(kwargs["read_id"])
        except (ValueError, UnicodeError) as exc:
            raise OntNgsRouteError(status_code=400, code="NGS_READ_ID_INVALID",
                message="The literal BAM read identity is invalid.", job_id=str(job.id), resource="read") from exc
    catalog, _preview = await _current_derived_products(db, job, session_id, include_preview=False)
    if catalog is None or catalog.state != "ready":
        raise OntNgsRouteError(status_code=409, code="NGS_CATALOG_NOT_READY",
            message="The complete catalog is not ready.", job_id=str(job.id), resource="read")
    try:
        # The accepted receipt selects the catalog. Preview readiness never
        # participates in read authority and a GET never creates an intent.
        root = Path(resolve_persisted_job_result_root(job)) / ".alignment-products"
        args = (job, catalog, root)
        preview = await db.get(NgsAlignmentDerivedProduct, derived_products.default_preview_request_id(catalog))
        result = await run_in_threadpool(getattr(reader, operation), *args, **kwargs)
        return await run_in_threadpool(reader.decorate_preview, catalog, preview, root, result)
    except ScientificArtifactQueryCapacityUnavailable as exc:
        raise OntNgsRouteError(status_code=503, code="NGS_READ_CAPACITY_UNAVAILABLE",
            message="Read query capacity is unavailable.", job_id=str(job.id), resource="read") from exc
    except reader.CatalogReadError as exc:
        raise OntNgsRouteError(status_code=exc.status, code=exc.code, message=str(exc),
            job_id=str(job.id), resource="read") from exc
    except presentation_lifecycle.PresentationSourceStale as exc:
        raise OntNgsRouteError(status_code=409, code="NGS_PRESENTATION_SOURCE_STALE",
            message="The accepted catalog source changed.", job_id=str(job.id), resource="read") from exc
    except service.AlignmentSessionError as exc:
        if any(word in str(exc).lower() for word in ("capacity", "snapshot limit")):
            raise OntNgsRouteError(status_code=503, code="NGS_READ_CAPACITY_UNAVAILABLE",
                message="Verified read delivery capacity is unavailable.", job_id=str(job.id), resource="read") from exc
        raise _http_error(exc, job_id=str(job.id), resource="read") from exc
    except (ValueError, UnicodeError, KeyError, TypeError, OSError) as exc:
        raise OntNgsRouteError(status_code=409, code="NGS_ARTIFACT_INTEGRITY_CONFLICT",
            message="The catalog artifact authority is unavailable or invalid.", job_id=str(job.id), resource="read") from exc


@router.get("/jobs/{job_id}/alignment-sessions/{session_id}/reads", response_model=OntCatalogPageV3, responses=_typed_errors(400, 403, 404, 409, 503))
async def list_catalog_reads(
    job_id: str, session_id: str, request: Request,
    search: str = Query(default="", max_length=254), q: str | None = Query(default=None, max_length=254),
    alignment_state: Literal["mapped_primary", "ambiguous_primary", "unmapped", "no_primary"] | None = None,
    sort_by: str = "read_id", sort_direction: Literal["asc", "desc"] = "asc",
    contig: str | None = None, start_1based: int | None = None, end_1based: int | None = None,
    metric_min: float | None = None, metric_max: float | None = None,
    raw_run_id: str | None = None, raw_observed_generation: int | None = None, raw_representation_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200), cursor: str | None = Query(default=None, max_length=4096),
    population_id: str | None = None,
    authorized_job: Job = Depends(require_alignment_job), db: AsyncSession = Depends(get_session),
):
    allowed = {"search", "q", "alignment_state", "limit", "cursor", "population_id", "sort_by", "sort_direction",
        "contig", "start_1based", "end_1based", "metric_min", "metric_max", "raw_run_id", "raw_observed_generation", "raw_representation_id"}
    if (any(key not in allowed or len(request.query_params.getlist(key)) != 1 for key in request.query_params)
            or (q is not None and "search" in request.query_params) or sort_by not in CATALOG_SORT_FIELDS):
        raise OntNgsRouteError(status_code=400, code="NGS_READ_QUERY_INVALID",
            message="Unsupported, ambiguous or duplicate catalog query fields.", job_id=job_id, resource="read")
    signal = await _catalog_signal(authorized_job, db, raw_run_id, raw_observed_generation, raw_representation_id)
    return await _catalog_query(authorized_job, session_id, db, "read_page", search=q if q is not None else search,
        alignment_state=alignment_state, limit=limit, cursor=cursor, population_id=population_id,
        sort_by=sort_by, sort_direction=sort_direction, contig=contig, start_1based=start_1based,
        end_1based=end_1based, metric_min=metric_min, metric_max=metric_max, signal=signal)


@router.post("/jobs/{job_id}/alignment-sessions/{session_id}/reads/lookup", response_model=OntCatalogLookupResponseV2, responses=_typed_errors(400, 403, 404, 409, 503))
async def lookup_catalog_read(job_id: str, session_id: str, body: OntCatalogLookupV2,
    authorized_job: Job = Depends(require_alignment_job), db: AsyncSession = Depends(get_session),
):
    signal = await _catalog_signal(authorized_job, db, body.raw_run_id, body.raw_observed_generation, body.raw_representation_id)
    return await _catalog_query(authorized_job, session_id, db, "exact_read", read_id=body.read_id,
        include_sequence=body.include_sequence, population_id=body.population_id, signal=signal)


@router.post("/jobs/{job_id}/alignment-sessions/{session_id}/reads/records/query", response_model=OntCatalogRecordPageV2, responses=_typed_errors(400, 403, 404, 409, 503))
async def query_catalog_records(job_id: str, session_id: str, body: OntCatalogRecordQueryV2,
    authorized_job: Job = Depends(require_alignment_job), db: AsyncSession = Depends(get_session),
):
    signal = await _catalog_signal(authorized_job, db, body.raw_run_id, body.raw_observed_generation, body.raw_representation_id)
    return await _catalog_query(authorized_job, session_id, db, "record_page", read_id=body.read_id,
        include_sequence=body.include_sequence, population_id=body.population_id,
        cursor=body.cursor, limit=body.limit, signal=signal)


class OntNativeCatalogSessionV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    mode: Literal["primary"]
    source_authority_sha256: str
    reference: OntAlignmentReferenceV1


class OntNativeCatalogDiscoveryV2(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.native-catalog-sessions.v2"] = Field(alias="schema")
    job_id: str
    native: bool
    sessions: list[OntNativeCatalogSessionV2]
    unavailable_reason: Literal["unsupported_source", "source_invalid"] | None = None


@router.get("/jobs/{job_id}/catalog-sessions", response_model=OntNativeCatalogDiscoveryV2,
            responses=_STANDARD_GOVERNED_ERRORS)
async def discover_native_catalog_sessions(job_id: str, authorized_job: Job = Depends(require_alignment_job)):
    from services import ngs_native_alignment_sources as native
    response = {"schema": "bms.ngs.native-catalog-sessions.v2", "job_id": job_id,
                "native": native.is_native(authorized_job), "sessions": [], "unavailable_reason": None}
    if not response["native"]:
        return response
    if (authorized_job.status != "completed" or authorized_job.queue_status != "completed"
            or authorized_job.awaiting_input):
        response["unavailable_reason"] = "source_invalid"
        return response
    def discover():
        with native.result_root(authorized_job) as root:
            return [{"session_id": source["session_id"], "mode": source["mode"],
                     "source_authority_sha256": derived_products.identity_sha256(source),
                     "reference": source["reference"]}
                    for source, _inputs in native.sources(authorized_job, root)]
    try:
        response["sessions"] = await run_in_threadpool(discover)
        if not response["sessions"]:
            response["unavailable_reason"] = "unsupported_source"
    except (service.AlignmentSessionError, OSError, ValueError, TypeError, KeyError):
        response["unavailable_reason"] = "source_invalid"
    return response


class OntPreviewSourceV2(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.alignment-presentation-source.v2"] = Field(alias="schema")
    job_id: str
    session_id: str
    mode: Literal["primary", "dimer_candidates"]
    reference: OntAlignmentReferenceV1
    source_manifest_sha256: str
    source_artifact_set_sha256: str
    package_artifact_set_sha256: str
    alignment_pair_sha256: str
    alignment_sha256: str
    alignment_size_bytes: int = Field(gt=0)
    alignment_index_sha256: str
    alignment_index_size_bytes: int = Field(gt=0)


class OntPreviewWriterV6(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pysam: str
    htslib: str
    mode: Literal["wb6"]
    threads: Literal[1]
    record_order: Literal["reference_start_source_ordinal"]
    selection: Literal["stratified_largest_remainder_sha256_v1"]


class OntPreviewPolicyV6(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.alignment-preview-policy.v6"] = Field(alias="schema")
    target_reads: Literal[5000]
    max_records: Literal[20000]
    max_bytes: Literal[67108864]
    projection: Literal["alignment_core_projection_v1"]
    header_policy: Literal["sq_coordinate_v1"]
    bgzf_admission_version: Literal[2]
    writer_contract: OntPreviewWriterV6


class OntPreviewArtifactV6(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    mime_type: Literal["application/octet-stream"]
    range_capable: Literal[True]


class OntPreviewPopulationV6(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eligible_read_count: int = Field(ge=0)
    target_read_count: int = Field(ge=0, le=5000)
    excluded_long_cigar_reads: int = Field(ge=0, le=5000)
    population_state: Literal["empty", "reduced", "capped", "complete"]
    population_reasons: list[Literal["no_mapped_primary_reads", "read_limit", "long_cigar_exclusion", "record_limit", "byte_limit", "admission_limit_not_recorded"]]


class OntReadyPreviewV6(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.alignment-preview.v6"] = Field(alias="schema")
    job_id: str
    session_id: str
    source: OntPreviewSourceV2
    catalog_authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preview_request_id: str
    preview_authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy: OntPreviewPolicyV6
    population: OntPreviewPopulationV6
    selected_read_count: int = Field(ge=0, le=5000)
    selected_record_count: int = Field(ge=0, le=20000)
    bam: OntPreviewArtifactV6
    index: OntPreviewArtifactV6


@router.get("/jobs/{job_id}/alignment-sessions/{session_id}/preview-product", response_model=OntReadyPreviewV6, responses=_STANDARD_GOVERNED_ERRORS)
async def get_alignment_preview_product(job_id: str, session_id: str,
        preview_request_id: str = Query(max_length=96),
        authorized_job: Job = Depends(require_alignment_job), db: AsyncSession = Depends(get_session)):
    from services import ngs_alignment_product_reader as reader
    catalog, preview = await _current_derived_products(db, authorized_job, session_id, preview_request_id)
    try:
        root = Path(resolve_persisted_job_result_root(authorized_job)) / ".alignment-products"
        return await run_in_threadpool(reader.preview_response, authorized_job, catalog, preview, root)
    except reader.CatalogReadError as exc:
        raise OntNgsRouteError(status_code=exc.status, code=exc.code, message=str(exc), job_id=job_id, resource="presentation") from exc
    except (service.AlignmentSessionError, presentation_lifecycle.PresentationSourceStale, ValueError, OSError, KeyError, TypeError) as exc:
        raise _http_error(service.AlignmentSessionError(str(exc)), job_id=job_id, resource="presentation") from exc


@router.get("/jobs/{job_id}/alignment-sessions/{session_id}/presentation/products/preview/{preview_request_id}/{authority}/{kind}", responses=_BINARY_RESPONSES)
@router.head("/jobs/{job_id}/alignment-sessions/{session_id}/presentation/products/preview/{preview_request_id}/{authority}/{kind}", responses=_BINARY_RESPONSES)
async def get_alignment_preview_product_artifact(job_id: str, session_id: str, preview_request_id: str,
        authority: str, kind: Literal["bam", "index"], request: Request,
        authorized_job: Job = Depends(require_alignment_job), db: AsyncSession = Depends(get_session)):
    from services import ngs_alignment_product_reader as reader
    catalog, preview = await _current_derived_products(db, authorized_job, session_id, preview_request_id)
    try:
        if preview is None or preview.authority_sha256 != authority:
            raise service.AlignmentSessionError("preview artifact identity changed")
        root = Path(resolve_persisted_job_result_root(authorized_job)) / ".alignment-products"
        async with contextmanager_in_threadpool(reader.preview_snapshot(authorized_job, catalog, preview, root)) as (directory, manifest):
            metadata = manifest["authority"]["artifacts"][kind]
            return await _serve_artifact(directory / metadata["filename"],
                {**metadata, "mime_type": "application/octet-stream"}, request, job_id=job_id)
    except reader.CatalogReadError as exc:
        raise OntNgsRouteError(status_code=exc.status, code=exc.code, message=str(exc), job_id=job_id, resource="artifact") from exc
    except (service.AlignmentSessionError, presentation_lifecycle.PresentationSourceStale, ValueError, OSError, KeyError, TypeError) as exc:
        raise _http_error(service.AlignmentSessionError(str(exc)), job_id=job_id, resource="artifact") from exc


class OntReadOverlayRequestV2(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, strict=True)
    schema_version: Literal["bms.ngs.read-overlay-request.v2"] = Field(alias="schema")
    read_id: str = Field(min_length=1, max_length=254)
    population_id: str = Field(pattern=r"^[0-9a-f]{64}$")


class OntOverlayHeaderV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_bytes: int = Field(ge=12)


class OntOverlayCatalogArtifactV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: Literal["read-catalog.parquet", "read-record-locators.parquet"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)


class OntOverlayCatalogArtifactsV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    catalog: OntOverlayCatalogArtifactV2
    locators: OntOverlayCatalogArtifactV2


class OntOverlayWriterV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pysam: str = Field(min_length=1)
    htslib: str = Field(min_length=1)
    mode: Literal["wb6"]
    threads: Literal[1]
    order: Literal["reference_start_source_ordinal"]


class OntOverlayPolicyV2(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.read-overlay-policy.v2"] = Field(alias="schema")
    max_records: Literal[256]
    max_bam_bytes: Literal[16777216]
    max_index_bytes: Literal[1048576]
    max_seconds: Literal[10]
    tags: Literal["all_exact_source_tags_v1"]
    header: Literal["exact_source_header_v1"]
    bgzf_admission_version: Literal[2]
    writer: OntOverlayWriterV2


class OntOverlayIdentityV2(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.read-overlay-identity.v2"] = Field(alias="schema")
    source: OntPreviewSourceV2
    catalog_authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_artifacts: OntOverlayCatalogArtifactsV2
    source_header: OntOverlayHeaderV1
    read_id: str
    policy: OntOverlayPolicyV2


class OntReadOverlayErrorV2(OntNgsErrorV2):
    pass


class OntReadOverlayV2(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal["bms.ngs.read-overlay.v2"] = Field(alias="schema")
    job_id: str
    session_id: str
    read_id: str
    overlay_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity: OntOverlayIdentityV2
    state: Literal["ready"]
    primary_record_count: Literal[1]
    mapped_supplementary_record_count: int = Field(ge=0, le=255)
    bam: OntPreviewArtifactV6
    index: OntPreviewArtifactV6
    manifest: OntPreviewArtifactV6


def _overlay_error(exc, job_id):
    from services.ngs_read_overlays import OverlayError
    if isinstance(exc, OverlayError):
        payload = {"schema": "bms.ngs.error.v2", "code": exc.code, "message": str(exc),
            "job_id": job_id, "resource": "read", "retryable": False}
        if exc.reason is not None:
            payload["reason"] = exc.reason
        return JSONResponse(status_code=exc.status, content=payload)
    if isinstance(exc, service._AlignmentDerivativeTimeout):
        return _ngs_error_response(status_code=503, code="NGS_READ_OVERLAY_TIMEOUT",
            message="Selected-read preparation timed out. Retry explicitly.", job_id=job_id, resource="read")
    from services.ngs_alignment_catalog_reader import CatalogReadError
    if isinstance(exc, CatalogReadError):
        return _ngs_error_response(status_code=exc.status, code=exc.code, message=str(exc), job_id=job_id, resource="read")
    if isinstance(exc, OSError) and exc.errno in {28, 122} or any(word in str(exc).lower() for word in ("capacity", "snapshot limit")):
        return _ngs_error_response(status_code=503, code="NGS_READ_CAPACITY_UNAVAILABLE",
            message="Selected-read capacity is unavailable.", job_id=job_id, resource="read")
    return _ngs_error_response(status_code=409, code="NGS_ARTIFACT_INTEGRITY_CONFLICT",
        message="The exact selected-read authority could not be verified.", job_id=job_id, resource="read")


@router.post("/jobs/{job_id}/alignment-sessions/{session_id}/read-overlays/cache/retry",
             status_code=204, responses=_STANDARD_GOVERNED_ERRORS)
async def retry_selected_read_delivery_cache(job_id: str, session_id: str, body: OntReadOverlayRequestV2,
        request: Request, authorized_job: Job = Depends(require_alignment_job),
        db: AsyncSession = Depends(get_session)):
    _require_cache_retry_principal(request, job_id)
    from services import ngs_read_overlays as overlays
    catalog, _ = await _current_derived_products(db, authorized_job, session_id, include_preview=False)
    if catalog is None or catalog.state != "ready":
        return _ngs_error_response(status_code=409, code="NGS_CATALOG_NOT_READY",
            message="The complete catalog is not ready.", job_id=job_id, resource="read")
    try:
        root = Path(resolve_persisted_job_result_root(authorized_job)) / ".alignment-products"
        await run_in_threadpool(overlays.retry_selected_cache, authorized_job, catalog, root,
            read_id=body.read_id, population_id=body.population_id)
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    except (overlays.reader.CatalogReadError, service.AlignmentSessionError,
            presentation_lifecycle.PresentationSourceStale, OSError, ValueError, KeyError, TypeError) as exc:
        return _overlay_error(exc, job_id)


@router.post("/jobs/{job_id}/alignment-sessions/{session_id}/read-overlays", response_model=OntReadOverlayV2,
             responses={**_typed_errors(400, 403, 404, 409, 503), 409: {"model": OntReadOverlayErrorV2}})
async def create_read_overlay(job_id: str, session_id: str, body: OntReadOverlayRequestV2,
        authorized_job: Job = Depends(require_alignment_job), db: AsyncSession = Depends(get_session)):
    import time
    from services import ngs_read_overlays as overlays
    deadline = time.monotonic() + overlays.bounds.MAX_SECONDS
    try:
        overlays.reader.records._validate_read_id(body.read_id)
    except (ValueError, UnicodeError):
        return _ngs_error_response(status_code=400, code="NGS_READ_ID_INVALID",
            message="The literal BAM read identity is invalid.", job_id=job_id, resource="read")
    catalog, _ = await _current_derived_products(db, authorized_job, session_id, include_preview=False)
    if catalog is None or catalog.state != "ready":
        return _ngs_error_response(status_code=409, code="NGS_CATALOG_NOT_READY",
            message="The complete catalog is not ready.", job_id=job_id, resource="read")
    preview = await db.get(NgsAlignmentDerivedProduct, derived_products.default_preview_request_id(catalog))
    try:
        root = Path(resolve_persisted_job_result_root(authorized_job)) / ".alignment-products"
        return await run_in_threadpool(overlays.create_bounded, authorized_job, catalog, preview, root,
            read_id=body.read_id, population_id=body.population_id, deadline=deadline)
    except (overlays.reader.CatalogReadError, service.AlignmentSessionError,
            presentation_lifecycle.PresentationSourceStale, OSError, ValueError, KeyError, TypeError) as exc:
        return _overlay_error(exc, job_id)


@router.post("/jobs/{job_id}/alignment-sessions/{session_id}/read-overlays/{overlay_id}/{artifact_sha256}/{kind}/cache/retry", status_code=204, responses=_STANDARD_GOVERNED_ERRORS)
@router.get("/jobs/{job_id}/alignment-sessions/{session_id}/read-overlays/{overlay_id}/{artifact_sha256}/{kind}", responses=_BINARY_RESPONSES)
@router.head("/jobs/{job_id}/alignment-sessions/{session_id}/read-overlays/{overlay_id}/{artifact_sha256}/{kind}", responses=_BINARY_RESPONSES)
async def get_read_overlay_artifact(job_id: str, session_id: str, overlay_id: str, artifact_sha256: str,
        kind: Literal["bam", "bai", "manifest"], request: Request,
        authorized_job: Job = Depends(require_alignment_job), db: AsyncSession = Depends(get_session)):
    from services import ngs_read_overlays as overlays
    if request.method == "POST":
        _require_cache_retry_principal(request, job_id)
    catalog, _ = await _current_derived_products(db, authorized_job, session_id, include_preview=False)
    if catalog is None or catalog.state != "ready":
        return _ngs_error_response(status_code=409, code="NGS_CATALOG_NOT_READY",
            message="The complete catalog is not ready.", job_id=job_id, resource="read")
    try:
        root = Path(resolve_persisted_job_result_root(authorized_job)) / ".alignment-products"
        async with contextmanager_in_threadpool(overlays.artifact(authorized_job, catalog, root, overlay_id, artifact_sha256, kind, retry_cache=request.method == "POST")) as (path, metadata):
            if request.method == "POST":
                return Response(status_code=204, headers={"Cache-Control": "no-store"})
            return await _serve_artifact(path, metadata, request, job_id=job_id)
    except (overlays.reader.CatalogReadError, service.AlignmentSessionError,
            presentation_lifecycle.PresentationSourceStale, OSError, ValueError, KeyError, TypeError) as exc:
        return _overlay_error(exc, job_id)


class OntSignalCacheRetry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw_run_id: str = Field(min_length=1)
    raw_observed_generation: int = Field(ge=1)
    raw_representation_id: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.post("/jobs/{job_id}/alignment-sessions/{session_id}/reads/signal/cache/retry",
             status_code=204, responses=_STANDARD_GOVERNED_ERRORS)
async def retry_read_signal_cache(job_id: str, session_id: str, body: OntSignalCacheRetry,
        request: Request, authorized_job: Job = Depends(require_alignment_job),
        db: AsyncSession = Depends(get_session)):
    _require_cache_retry_principal(request, job_id)
    await _current_derived_products(db, authorized_job, session_id, include_preview=False)
    state = await _catalog_signal(authorized_job, db, body.raw_run_id,
        body.raw_observed_generation, body.raw_representation_id)
    artifact = state.get("artifact")
    if artifact is None or artifact["content_sha256"] != body.artifact_sha256:
        raise OntNgsRouteError(status_code=409, code="NGS_AUTHORITY_CONFLICT",
            message="The exact source-bound signal receipt is required.", job_id=job_id, resource="read")
    from services.scientific_artifacts.writer import artifact_root, _artifact_receipt_identity
    relative, digest, size = _artifact_receipt_identity(artifact)
    if not relative or Path(relative).is_absolute() or any(part in {"", ".", ".."} for part in relative.split("/")):
        raise OntNgsRouteError(status_code=409, code="NGS_AUTHORITY_CONFLICT",
            message="The signal receipt path is invalid.", job_id=job_id, resource="read")
    async with contextmanager_in_threadpool(service.open_presentation_authority_root(artifact_root(), create=False)) as root:
        return await _retry_artifact_delivery(root / relative,
            {"sha256": digest, "size_bytes": size}, request, job_id)
