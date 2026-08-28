"""Closed typed API for the governed ONT Read and Signal Workbench."""
from __future__ import annotations

import asyncio
import os
import re
import secrets
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from experiment_database import get_experiment_session
from molbio_ngs_database import get_molbio_ngs_session
from routers.ont_runs import OntNgsSubmitRequest, _create_pipeline_job, _job_create_for_ont_submit
from schemas import JobResponse
from services import ont_signal_workbench as service

router = APIRouter()
OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_TRUSTED_PROXY_HEADER = "x-bms-cm-proxy-secret"


def _comparison_principal(request: Request) -> str:
    principal = getattr(request.state, "authenticated_principal", None)
    if principal is None:
        configured = os.getenv("BMS_CM_TRUSTED_PROXY_SECRET", "")
        supplied = request.headers.get(_TRUSTED_PROXY_HEADER, "")
        if configured and supplied and secrets.compare_digest(configured, supplied):
            return "local-application-operator"
        raise HTTPException(status_code=401, detail={
            "schema": "bms.ont-signal-comparison.error.v1",
            "code": "COMPARISON_AUTH_REQUIRED",
            "message": "Authenticated comparison principal required",
            "retryable": False,
        })
    if isinstance(principal, Mapping):
        actor = principal.get("id") or principal.get("subject")
        roles = principal.get("roles") or []
    else:
        actor = getattr(principal, "id", None) or getattr(principal, "subject", None)
        roles = getattr(principal, "roles", [])
    normalized_roles = {str(role).strip().lower() for role in roles}
    if not actor or not normalized_roles.intersection({"operator", "scientist", "admin"}):
        raise HTTPException(status_code=403, detail={
            "schema": "bms.ont-signal-comparison.error.v1",
            "code": "COMPARISON_FORBIDDEN",
            "message": "Comparison scientist/operator role required",
            "retryable": False,
        })
    return str(actor)[:255]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MoveSourceCreate(ClosedModel):
    raw_representation_id: str
    input_file_id: str
    molecule_type: Literal["dna", "rna"]
    source_job_id: str


class FreshMoveSourceAttemptCreate(ClosedModel):
    pass


class ExternalMoveBamRegistrationCreate(ClosedModel):
    candidate_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_representation_id: str
    molecule_type: Literal["dna", "rna"]

    @field_validator("raw_representation_id")
    @classmethod
    def opaque_raw_representation_id(cls, value: str) -> str:
        if not OPAQUE.fullmatch(value):
            raise ValueError("raw representation must be an opaque governed ID")
        return value


class ExternalAlignmentCreate(ClosedModel):
    move_source_id: str
    reference_revision_id: str
    name: str = Field(min_length=1, max_length=128)

    @field_validator("move_source_id", "reference_revision_id")
    @classmethod
    def opaque_authority_id(cls, value: str) -> str:
        if not OPAQUE.fullmatch(value):
            raise ValueError("alignment parents must be opaque governed IDs")
        return value


class ExternalAlignmentJobResponse(ClosedModel):
    job_id: str
    name: str
    status: str
    dataset_id: str
    run_id: str
    observed_generation: int = Field(ge=1)
    move_source_id: str
    reference_revision_id: str


class MappingProfileCreate(ClosedModel):
    name: str = Field(min_length=1, max_length=255)
    molecule_type: Literal["dna", "rna"]
    basecall_model_id: str = Field(min_length=1, max_length=255)
    kmer_length: int = Field(ge=1, le=32)
    signal_move_offset: int = Field(ge=-64, le=64)
    base_shift_value: int = Field(ge=-64, le=64, default=0)
    parameter_source: Literal["approved_calibration"]
    calibration_artifact_id: str
    primary_alignment_policy: Literal["primary_only"] = "primary_only"
    minimum_mapq: Literal[0] = 0
    include_supplementary: Literal[False] = False
    read_set_selection: Literal["immutable_full_set"] = "immutable_full_set"
    approval_receipt: dict[str, Any]
    approved_by: str | None = Field(default=None, max_length=255)


class MappingCreate(ClosedModel):
    mode: Literal["signal_to_read", "signal_to_reference"]
    raw_representation_id: str
    move_source_id: str
    mapping_profile_id: str
    reference_revision_id: str | None = None
    alignment_job_id: str | None = None
    alignment_session_id: str | None = None


class CalibrationCreate(ClosedModel):
    raw_representation_id: str
    move_source_id: str
    sample_count: int = Field(ge=1, le=100)

    @field_validator("raw_representation_id", "move_source_id")
    @classmethod
    def opaque_parent_id(cls, value: str) -> str:
        if not OPAQUE.fullmatch(value):
            raise ValueError("calibration parent must be an opaque governed ID")
        return value


class RenderParams(ClosedModel):
    strand: Literal["forward", "reverse"] = "forward"
    signal_units: Literal["pA", "raw_adc"] = "pA"
    scale: Literal["none", "medmad", "znorm", "scaledpA"] = "none"
    base_shift_source: Literal["profile", "explicit"] = "profile"
    base_shift_value: int = Field(ge=-64, le=64, default=0)
    fixed_width: StrictBool = False
    base_width: int = Field(ge=1, le=100, default=10)
    point_size: float = Field(ge=0.5, le=10, default=0.5)
    base_limit: int = Field(ge=1, le=100_000, default=1000)
    signal_sample_limit: int = Field(ge=1, le=2_000_000, default=100_000)
    pileup_read_limit: int = Field(ge=1, le=100, default=20)
    loose_bound: StrictBool = False
    show_samples: StrictBool = True
    show_base_colours: StrictBool = True
    remove_signal_outliers: StrictBool = False
    managed_bed_artifact_id: str | None = None

    @field_validator("point_size")
    @classmethod
    def squigualiser_point_size(cls, value: float) -> float:
        if value != 0.5 and not value.is_integer():
            raise ValueError("Squigualiser v0.7.0 accepts only its 0.5 default or an integer point size")
        return value


ComparisonProfileId = Literal[
    "dna-r9-min", "dna-r9-prom", "rna-r9-min", "rna-r9-prom",
    "dna-r10-min", "dna-r10-prom", "rna004-min", "rna004-prom",
]


class ComparisonSimulationSettings(ClosedModel):
    profile_id: ComparisonProfileId
    seed: int = Field(default=1, ge=1, le=2_147_483_647)


class ComparisonPointSize(float, Enum):
    HALF = 0.5
    ONE = 1
    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5
    SIX = 6
    SEVEN = 7
    EIGHT = 8
    NINE = 9
    TEN = 10


class ComparisonRenderParams(ClosedModel):
    scale: Literal["none", "medmad", "znorm"] = "none"
    point_size: ComparisonPointSize = ComparisonPointSize.HALF
    fixed_width: StrictBool = False
    base_width: int = Field(default=10, ge=1, le=100)
    base_limit: int = Field(default=1000, ge=1, le=1000)
    signal_sample_limit: int = Field(default=100_000, ge=1, le=2_000_000)
    show_samples: StrictBool = True
    show_base_colours: StrictBool = True
    remove_signal_outliers: StrictBool = False


class ComparisonPreviewCreate(ClosedModel):
    viewer_session_id: str
    expected_viewer_revision: int = Field(ge=1)
    mapping_artifact_id: str
    selected_read_id: str
    reference_contig: str
    reference_start: int = Field(ge=1)
    reference_end: int = Field(ge=1)
    simulation_settings: ComparisonSimulationSettings
    render_params: ComparisonRenderParams

    @model_validator(mode="after")
    def closed_interval(self):
        if self.reference_end < self.reference_start:
            raise ValueError("reference interval must be 1-based closed")
        if self.reference_end - self.reference_start + 1 > 1000:
            raise ValueError("comparison interval exceeds 1000 bases")
        return self


class ComparisonCreate(ComparisonPreviewCreate):
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ComparisonReviewCreate(ClosedModel):
    predecessor_review_id: str | None = None
    review_question: str = Field(min_length=1, max_length=1000)
    required_outcome: Literal["approve", "reject", "record_only"]
    note: str = Field(min_length=1, max_length=4000)
    reviewed_start: int = Field(ge=1)
    reviewed_end: int = Field(ge=1)

    @model_validator(mode="after")
    def closed_review_interval(self):
        if self.reviewed_end < self.reviewed_start:
            raise ValueError("reviewed interval must be 1-based closed")
        return self


class ComparisonReadSpan(ClosedModel):
    contig: str
    start: int = Field(ge=1)
    end: int = Field(ge=1)
    strand: Literal["forward", "reverse", "+", "-"]


class ComparisonInterval(ClosedModel):
    contig: str
    start: int = Field(ge=1)
    end: int = Field(ge=1)


class ComparisonProfileFixed(ClosedModel):
    molecule_type: Literal["dna", "rna"]
    flow_cell_generation: str
    device_class: Literal["MinION", "PromethION"]
    pore_model_identity: str
    kmer_length: int = Field(ge=1)
    digitisation: float
    sample_rate: float
    translocation_speed: float
    range: float
    offset_mean: float
    offset_standard_deviation: float
    median_before_mean: float
    median_before_standard_deviation: float
    dwell_mean: float
    dwell_standard_deviation: float
    model_quality_warning: str | None
    compatibility_floor: Literal["matched_profile", "approximate_profile"]


class ComparisonOperatorSettings(ComparisonSimulationSettings, ComparisonRenderParams):
    pass


class ComparisonWorkflowFixed(ClosedModel):
    simulation_mode: Literal["ideal"]
    full_contigs: Literal[True]
    amplitude_noise_factor: Literal[0]
    dwell_noise: Literal[0]
    prefix: Literal[False]
    input_sequence_count: Literal[1]
    simulated_signal_record_count: Literal[1]
    signal_units: Literal["pA"]
    real_read_count: Literal[1]
    reference_hypothesis_count: Literal[1]
    sequence_basis: Literal["managed_reference"]
    threads: Literal[1]
    batch_size: Literal[1]


class ComparisonUpstream(ClosedModel):
    name: Literal["Squigulator"]
    version: Literal["0.5.0"]
    commit: str
    release_source_asset: str
    release_source_asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ComparisonCompatibilityEvidence(ClosedModel):
    mapping_profile_molecule_type: str | None
    mapping_profile_basecall_model_id: str | None
    mapping_profile_kmer_length: int | None
    move_source_molecule_type: str | None
    move_source_basecall_model_id: str | None
    move_source_runtime_authority: str | None
    raw_sample_rate: str | int | float | None
    raw_digitisation: str | int | float | None
    raw_range: str | int | float | None
    run_flow_cell_generation: str | None
    run_device_class: str | None


class ComparisonCompatibilityReceipt(ClosedModel):
    disposition: Literal["matched_profile", "approximate_profile", "legacy_unknown", "incompatible"]
    evidence: ComparisonCompatibilityEvidence
    missing_authorities: list[str]
    mismatches: list[str]


class ComparisonEffectiveSettings(ClosedModel):
    schema_id: Literal["bms.ont-squigulator-ideal-comparison-effective.v1"] = Field(alias="schema")
    operator_owned: ComparisonOperatorSettings
    profile_id: ComparisonProfileId
    profile: ComparisonProfileFixed
    workflow_fixed: ComparisonWorkflowFixed
    compatibility_floor: Literal["matched_profile", "approximate_profile"]
    warnings: list[str]
    upstream: ComparisonUpstream
    compatibility_disposition: Literal["matched_profile", "approximate_profile", "legacy_unknown", "incompatible"]
    compatibility_evidence: ComparisonCompatibilityReceipt


class ComparisonAuthority(ClosedModel):
    viewer_session_id: str
    viewer_session_revision: int
    run_id: str
    observed_generation: int
    raw_representation_id: str
    raw_manifest_sha256: str
    mapping_artifact_id: str
    mapping_artifact_sha256: str
    mapping_job_id: str
    mapping_profile_id: str
    move_source_id: str
    move_source_artifact_sha256: str
    reference_revision_id: str
    reference_artifact_id: str
    reference_fasta_sha256: str
    reference_topology: str
    coordinate_contract: str
    selected_read_id: str
    selected_read_span: ComparisonReadSpan
    simulation_orientation: Literal["forward", "reverse"]
    derived_window: ComparisonInterval


class ComparisonEffectiveRequest(ClosedModel):
    authority: ComparisonAuthority
    effective_settings: ComparisonEffectiveSettings
    reference_interval: ComparisonInterval


class ComparisonRuntimeIdentityResponse(ClosedModel):
    stage: Literal["squigulator_producer", "squigualiser_comparison_renderer"]
    image: str
    image_digest: str
    policy_sha256: str
    wrapper_sha256: str


class ComparisonExecutionReceipt(ClosedModel):
    argv_sha256: str
    returncode: Literal[0]
    stdout_sha256: str
    stdout_size_bytes: int
    stderr_sha256: str
    stderr_size_bytes: int
    stderr_tail: str
    container_name_sha256: str
    runtime_identity: ComparisonRuntimeIdentityResponse


class ComparisonLeaseRecoveryReceipt(ClosedModel):
    recovered_at: str
    expired_attempt: int
    max_attempts: int


class ComparisonStageReceipts(ClosedModel):
    squigulator_producer: ComparisonExecutionReceipt | None = None
    squigualiser_comparison_renderer: ComparisonExecutionReceipt | None = None
    lease_recoveries: list[ComparisonLeaseRecoveryReceipt] | None = None


class ComparisonRawPartitionIdentity(ClosedModel):
    sha256: str
    index_sha256: str


class ComparisonRawSignalParents(ClosedModel):
    routing_sha256: str | None
    blow5: list[ComparisonRawPartitionIdentity]


class ComparisonParentIdentities(ClosedModel):
    reference_fasta_sha256: str
    mapping_sha256: str
    mapping_index_sha256: str
    real_blow5: ComparisonRawSignalParents
    real_moves_sha256: str
    raw_manifest_sha256: str
    run_id: str
    observed_generation: int
    selected_read_id: str


class ComparisonReceiptAuthority(ClosedModel):
    schema_id: str | None = Field(default=None, alias="schema")
    content_sha256: str


class ComparisonResourceSnapshot(ClosedModel):
    parents: ComparisonParentIdentities | None = None


class ComparisonManifestArtifact(ClosedModel):
    kind: Literal[
        "simulation_input_fasta", "simulation_coordinate_map", "simulated_blow5",
        "simulated_blow5_index", "simulated_read_fasta", "simulated_read_id_map",
        "simulated_source_paf", "simulated_normalized_paf", "simulated_source_sam",
        "simulated_normalized_sam", "comparison_html", "comparison_manifest",
    ]
    media_type: str
    sha256: str
    size_bytes: int
    validation_receipt: ComparisonReceiptAuthority


class ComparisonRuntimeIdentities(ClosedModel):
    squigulator_producer: ComparisonRuntimeIdentityResponse | None = None
    squigualiser_comparison_renderer: ComparisonRuntimeIdentityResponse | None = None


class ComparisonOutputManifest(ClosedModel):
    schema_id: Literal["bms.ont-signal-comparison-manifest.v1"] | None = Field(default=None, alias="schema")
    parents: ComparisonParentIdentities | None = None
    runtime_identities: ComparisonRuntimeIdentities | None = None
    stage_receipts: ComparisonStageReceipts | None = None
    artifacts: list[ComparisonManifestArtifact] | None = None
    producer: ComparisonReceiptAuthority | None = None
    renderer: ComparisonReceiptAuthority | None = None


class ComparisonViewerSettings(ClosedModel):
    simulation_settings: ComparisonSimulationSettings
    render_params: ComparisonRenderParams


class ComparisonPreviewResponse(ClosedModel):
    viewer_session_id: str
    viewer_session_revision: int
    run_id: str
    observed_generation: int
    raw_representation_id: str
    raw_manifest_sha256: str
    mapping_artifact_id: str
    mapping_artifact_sha256: str
    mapping_job_id: str
    mapping_profile_id: str
    reference_revision_id: str
    reference_artifact_id: str
    reference_fasta_sha256: str
    reference_topology: str
    coordinate_contract: str
    selected_read_id: str
    selected_read_span: ComparisonReadSpan
    simulation_orientation: Literal["forward", "reverse"]
    derived_window: ComparisonInterval
    compatibility_disposition: Literal["matched_profile", "approximate_profile", "legacy_unknown", "incompatible"]
    warnings: list[str]
    effective_request: ComparisonEffectiveRequest
    preview_digest: str


class ComparisonArtifactResponse(ClosedModel):
    artifact_id: str
    kind: str
    authority_class: Literal["simulated_derived", "comparison_derived"]
    media_type: str
    sha256: str
    size_bytes: int
    parent_identities: ComparisonParentIdentities
    squigulator_runtime_identity: ComparisonRuntimeIdentityResponse | None
    squigualiser_runtime_identity: ComparisonRuntimeIdentityResponse | None
    validation_receipt: ComparisonReceiptAuthority
    created_at: str


class ComparisonJobResponse(ClosedModel):
    comparison_job_id: str
    viewer_session_id: str
    viewer_session_revision: int
    run_id: str
    observed_generation: int
    raw_representation_id: str
    mapping_artifact_id: str
    reference_revision_id: str
    selected_read_id: str
    reference_contig: str
    reference_start: int
    reference_end: int
    simulation_orientation: Literal["forward", "reverse"]
    simulation_settings: ComparisonEffectiveSettings
    sequence_basis: Literal["managed_reference"]
    generated_read_id: str | None
    render_params: ComparisonRenderParams
    preview_digest: str
    request_fingerprint: str
    attempt_number: int
    predecessor_job_id: str | None
    state: Literal["requested", "running", "ready", "failed", "cancelled"]
    reason_code: str
    resource_snapshot: ComparisonResourceSnapshot
    stage_receipts: ComparisonStageReceipts
    output_manifest: ComparisonOutputManifest
    failure_code: str | None
    failure_message: str | None
    artifacts: list[ComparisonArtifactResponse]
    created_at: str
    updated_at: str
    completed_at: str | None


class ComparisonReviewResponse(ClosedModel):
    review_id: str
    comparison_job_id: str
    predecessor_review_id: str | None
    review_question: str
    required_outcome: Literal["approve", "reject", "record_only"]
    note: str
    reviewed_start: int
    reviewed_end: int
    comparison_html_artifact_id: str
    comparison_html_sha256: str
    comparison_request_fingerprint: str
    reviewer_identity: str
    created_at: str


class ComparisonReviewListResponse(ClosedModel):
    items: list[ComparisonReviewResponse]


class ViewCreate(ClosedModel):
    mapping_artifact_id: str
    mode: Literal["read", "reference", "pileup"]
    read_id: str | None = None
    reference_contig: str | None = None
    reference_start: int | None = Field(default=None, ge=1)
    reference_end: int | None = Field(default=None, ge=1)
    render_params: RenderParams = Field(default_factory=RenderParams)

    @model_validator(mode="after")
    def closed_target(self):
        if self.mode == "read" and (not self.read_id or any(value is not None for value in (self.reference_contig, self.reference_start, self.reference_end))):
            raise ValueError("read mode requires only read_id")
        if self.mode != "read" and (self.read_id is not None or not self.reference_contig or self.reference_start is None or self.reference_end is None):
            raise ValueError("reference and pileup modes require only a complete reference region")
        return self


class ViewerIgvStateUpdate(ClosedModel):
    alignment_display_mode: Literal["EXPANDED", "SQUISHED", "FULL"]
    alignment_color_by: Literal[
        "none", "strand", "firstOfPairStrand", "pairOrientation", "tlen",
        "unexpectedPair", "basemod", "basemod2",
    ]
    alignment_group_by: Literal[
        "none", "strand", "firstOfPairStrand", "pairOrientation", "mateChr",
        "chimeric", "supplementary", "readOrder",
    ]
    reads_track_loaded: StrictBool


class ViewerSignalStateUpdate(ClosedModel):
    mode: Literal["raw_waveform", "read", "reference", "pileup", "ideal_comparison"]
    render_params: RenderParams
    view_job_id: str | None
    read_mapping_job_id: str | None
    reference_mapping_job_id: str | None
    comparison_job_id: str | None = None
    comparison_preview_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    comparison_settings: ComparisonViewerSettings | None = None
    comparison_review_id: str | None = None


class ViewerSessionCreate(ClosedModel):
    dataset_id: str
    run_id: str
    observed_generation: int = Field(ge=1)
    alignment_job_id: str | None = None
    alignment_session_id: str | None = None
    reference_revision_id: str | None = None
    contig: str | None = None
    locus_start: int | None = Field(default=None, ge=1)
    locus_end: int | None = Field(default=None, ge=1)
    selected_read_id: str | None = None
    igv_state: ViewerIgvStateUpdate
    signal_state: ViewerSignalStateUpdate


class ViewerSessionUpdate(ClosedModel):
    expected_revision: int = Field(ge=1)
    contig: str | None = None
    locus_start: int | None = Field(default=None, ge=1)
    locus_end: int | None = Field(default=None, ge=1)
    selected_read_id: str | None = None
    igv_state: ViewerIgvStateUpdate
    signal_state: ViewerSignalStateUpdate


class CapabilityModeResponse(ClosedModel):
    state: Literal["ready", "preparable", "unavailable", "independent"]
    reason_code: str


class WorkbenchResolvedResponse(ClosedModel):
    raw_representation_id: str | None
    move_source_id: str | None
    mapping_profile_id: str | None
    calibration_job_id: str | None
    calibration_artifact_id: str | None
    signal_to_read_mapping_job_id: str | None
    signal_to_reference_mapping_job_id: str | None


class WorkbenchModesResponse(ClosedModel):
    igv: CapabilityModeResponse
    raw_waveform: CapabilityModeResponse
    signal_to_read: CapabilityModeResponse
    signal_to_reference: CapabilityModeResponse
    signal_pileup: CapabilityModeResponse


class WorkbenchCapabilitiesResponse(ClosedModel):
    run_id: str
    observed_generation: int
    resolved: WorkbenchResolvedResponse
    modes: WorkbenchModesResponse


class MoveTagCountsResponse(ClosedModel):
    mv: int | None
    ts: int | None
    ns: int | None


class ExternalMoveBamCandidateResponse(ClosedModel):
    candidate_id: str
    display_name: str
    size_bytes: int
    modified_at_ns: int


class ExternalMoveBamCandidateListResponse(ClosedModel):
    items: list[ExternalMoveBamCandidateResponse]


class MoveSourceResponse(ClosedModel):
    move_source_id: str
    run_id: str
    observed_generation: int
    raw_representation_id: str
    artifact_id: str
    artifact_sha256: str
    artifact_size_bytes: int
    bam_header_sha256: str | None
    record_count: int | None
    unique_read_count: int | None
    tag_counts: MoveTagCountsResponse
    basecall_model_id: str | None
    molecule_type: Literal["dna", "rna"]
    source_job_id: str | None
    external_registration_receipt_id: str | None
    attempt_number: int
    predecessor_move_source_id: str | None
    source_runtime_identity: dict[str, Any]
    read_inventory_sha256: str | None
    state: str
    reason_code: str
    validation_receipt: dict[str, Any]
    created_at: str
    validated_at: str | None


class MoveSourceListResponse(ClosedModel):
    items: list[MoveSourceResponse]


class MappingProfileResponse(ClosedModel):
    mapping_profile_id: str
    name: str
    molecule_type: Literal["dna", "rna"]
    basecall_model_id: str
    kmer_length: int
    signal_move_offset: int
    base_shift_value: int
    parameter_source: Literal["approved_calibration"]
    calibration_artifact_id: str
    primary_alignment_policy: Literal["primary_only"]
    minimum_mapq: Literal[0]
    include_supplementary: Literal[False]
    read_set_selection: Literal["immutable_full_set"]
    approval_receipt: dict[str, Any]
    approved_at: str
    approved_by: str | None


class MappingProfileListResponse(ClosedModel):
    items: list[MappingProfileResponse]


class CalibrationSampleSelectionResponse(ClosedModel):
    method: str
    requested_count: int
    selected_count: int
    intersection_count: int
    read_ids: list[str]
    selection_sha256: str


class CalibrationArtifactResponse(ClosedModel):
    calibration_artifact_id: str
    raw_representation_id: str
    move_source_id: str
    basecall_model_id: str
    sample_selection: CalibrationSampleSelectionResponse
    recommended_kmer_length: int
    recommended_signal_move_offset: int
    score_evidence: list[dict[str, Any]]
    runtime_identity: dict[str, Any]
    parent_sha256s: dict[str, Any]
    artifact_sha256: str
    created_at: str


class CalibrationArtifactListResponse(ClosedModel):
    items: list[CalibrationArtifactResponse]


class CalibrationJobResponse(ClosedModel):
    calibration_job_id: str
    run_id: str
    observed_generation: int
    raw_representation_id: str
    move_source_id: str
    sample_count: int
    request_fingerprint: str
    state: str
    reason_code: str
    attempt: int
    resource_snapshot: dict[str, Any]
    stage_receipts: dict[str, Any]
    failure_code: str | None
    failure_message: str | None
    artifact: CalibrationArtifactResponse | None
    created_at: str
    updated_at: str
    completed_at: str | None


class MappingArtifactResponse(ClosedModel):
    mapping_artifact_id: str
    mapping_job_id: str
    kind: Literal["reform_paf", "realign_paf"]
    sha256: str
    size_bytes: int
    media_type: str
    parent_identities: dict[str, Any]
    runtime_identity: dict[str, Any]
    validation_receipt: dict[str, Any]
    created_at: str


class MappingJobResponse(ClosedModel):
    mapping_job_id: str
    mode: Literal["signal_to_read", "signal_to_reference"]
    run_id: str
    observed_generation: int
    raw_representation_id: str
    move_source_id: str
    mapping_profile_id: str
    reference_revision_id: str | None
    alignment_job_id: str | None
    alignment_session_id: str | None
    parent_mapping_job_id: str | None
    domain_revision: dict[str, Any] | None
    request_fingerprint: str
    state: str
    reason_code: str
    attempt: int
    resource_snapshot: dict[str, Any]
    stage_receipts: dict[str, Any]
    failure_code: str | None
    failure_message: str | None
    artifacts: list[MappingArtifactResponse]
    created_at: str
    updated_at: str
    completed_at: str | None


class ReferenceRegionResponse(ClosedModel):
    contig: str
    start: int
    end: int


class RenderParamsResponse(RenderParams):
    base_shift_profile_id: str | None = None
    base_shift_profile_sha256: str | None = None
    base_shift_effective_value: int | None = None
    managed_bed_source_job_id: str | None = None
    managed_bed_sha256: str | None = None
    managed_bed_size_bytes: int | None = None


class ViewArtifactDescriptorResponse(ClosedModel):
    artifact_id: str
    sha256: str
    size_bytes: int
    media_type: str
    url: str | None = None


class ViewOutputManifestResponse(ClosedModel):
    schema_name: str | None = Field(default=None, alias="schema")
    artifacts: list[ViewArtifactDescriptorResponse] = Field(default_factory=list)
    command: dict[str, Any] | None = None
    network: str | None = None


class ViewJobResponse(ClosedModel):
    view_job_id: str
    mapping_artifact_id: str
    mode: Literal["read", "reference", "pileup"]
    read_id: str | None
    reference_region: ReferenceRegionResponse | None
    render_params: RenderParamsResponse
    request_fingerprint: str
    state: str
    reason_code: str
    output_manifest: ViewOutputManifestResponse
    render_receipt: dict[str, Any]
    failure_code: str | None
    failure_message: str | None
    created_at: str
    updated_at: str
    completed_at: str | None


class ViewerIgvStateResponse(ClosedModel):
    alignment_display_mode: Literal["EXPANDED", "SQUISHED", "FULL"] | None = None
    alignment_color_by: Literal[
        "none", "strand", "firstOfPairStrand", "pairOrientation", "tlen",
        "unexpectedPair", "basemod", "basemod2",
    ] | None = None
    alignment_group_by: Literal[
        "none", "strand", "firstOfPairStrand", "pairOrientation", "mateChr",
        "chimeric", "supplementary", "readOrder",
    ] | None = None
    reads_track_loaded: StrictBool | None = None
    alignment_job_id: str | None = None
    alignment_session_id: str | None = None
    reference_revision_id: str | None = None
    locus: str | None = None


class ViewerSignalStateResponse(ClosedModel):
    mode: Literal["raw_waveform", "read", "reference", "pileup", "ideal_comparison"] | None = None
    render_params: RenderParams | None = None
    view_job_id: str | None = None
    read_mapping_job_id: str | None = None
    reference_mapping_job_id: str | None = None
    selected_read_id: str | None = None
    capabilities: WorkbenchModesResponse | None = None
    comparison_job_id: str | None = None
    comparison_preview_digest: str | None = None
    comparison_settings: ComparisonViewerSettings | None = None
    comparison_review_id: str | None = None


class ViewerSessionResponse(ClosedModel):
    viewer_session_id: str
    dataset_id: str
    run_id: str
    observed_generation: int
    alignment_job_id: str | None
    alignment_session_id: str | None
    reference_revision_id: str | None
    raw_representation_id: str | None
    move_source_id: str | None
    mapping_profile_id: str | None
    contig: str | None
    locus_start: int | None
    locus_end: int | None
    selected_read_id: str | None
    igv_state: ViewerIgvStateResponse
    signal_state: ViewerSignalStateResponse
    revision: int
    created_at: str
    updated_at: str
    reopen_url: str


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail="governed signal-workbench authority not found")
    return HTTPException(status_code=409, detail=str(exc))


@router.get("/runs/{run_id}/generations/{observed_generation}/capabilities", response_model=WorkbenchCapabilitiesResponse)
async def capabilities(
    run_id: str,
    observed_generation: int,
    alignment_job_id: str | None = None,
    alignment_session_id: str | None = None,
    reference_revision_id: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> WorkbenchCapabilitiesResponse:
    try:
        value = await service.workbench_capabilities(
            session,
            run_id=run_id,
            observed_generation=observed_generation,
            alignment_job_id=alignment_job_id,
            alignment_session_id=alignment_session_id,
            reference_revision_id=reference_revision_id,
        )
        return WorkbenchCapabilitiesResponse.model_validate(value)
    except service.OntSignalError as exc:
        raise _error(exc) from exc


@router.get("/runs/{run_id}/generations/{observed_generation}/move-sources", response_model=MoveSourceListResponse)
async def move_sources(run_id: str, observed_generation: int, session: AsyncSession = Depends(get_session)) -> MoveSourceListResponse:
    return MoveSourceListResponse.model_validate({
        "items": await service.list_move_sources(session, run_id=run_id, observed_generation=observed_generation)
    })


@router.get("/external-move-bam-candidates", response_model=ExternalMoveBamCandidateListResponse)
async def external_move_bam_candidates() -> ExternalMoveBamCandidateListResponse:
    try:
        return ExternalMoveBamCandidateListResponse.model_validate({
            "items": await asyncio.to_thread(service.list_external_move_bam_candidates)
        })
    except service.OntSignalError as exc:
        raise HTTPException(
            status_code=503,
            detail="external move-BAM source is unavailable",
        ) from exc


@router.post(
    "/runs/{run_id}/generations/{observed_generation}/external-move-bam-candidates/register",
    status_code=202,
    response_model=MoveSourceResponse,
)
async def register_external_move_bam_candidate(
    run_id: str,
    observed_generation: int,
    request: ExternalMoveBamRegistrationCreate,
    session: AsyncSession = Depends(get_session),
) -> MoveSourceResponse:
    try:
        value = await service.register_external_move_bam_candidate(
            session,
            run_id=run_id,
            observed_generation=observed_generation,
            **request.model_dump(),
        )
        await session.commit()
        return MoveSourceResponse.model_validate(value)
    except (KeyError, service.OntSignalError) as exc:
        await session.rollback()
        raise _error(exc) from exc
    except BaseException:
        await session.rollback()
        raise


@router.post(
    "/move-sources/{move_source_id}/fresh-attempt",
    status_code=202,
    response_model=MoveSourceResponse,
)
async def request_fresh_external_move_source_attempt(
    move_source_id: str,
    request: FreshMoveSourceAttemptCreate,
    session: AsyncSession = Depends(get_session),
) -> MoveSourceResponse:
    del request
    try:
        value = await service.request_fresh_external_move_source_attempt(
            session,
            predecessor_move_source_id=move_source_id,
        )
        await session.commit()
        return MoveSourceResponse.model_validate(value)
    except (KeyError, service.OntSignalError) as exc:
        await session.rollback()
        raise _error(exc) from exc
    except BaseException:
        await session.rollback()
        raise


@router.post("/runs/{run_id}/generations/{observed_generation}/move-sources", status_code=202, response_model=MoveSourceResponse)
async def register_move_source(run_id: str, observed_generation: int, request: MoveSourceCreate, session: AsyncSession = Depends(get_session)) -> MoveSourceResponse:
    try:
        value = await service.register_move_source(
            session, run_id=run_id, observed_generation=observed_generation,
            raw_representation_id=request.raw_representation_id, input_file_id=request.input_file_id,
            molecule_type=request.molecule_type, source_job_id=request.source_job_id,
            external_registration_receipt_id=None,
            source_runtime_identity=None,
        )
        await session.commit()
        return MoveSourceResponse.model_validate(value)
    except (KeyError, service.OntSignalError) as exc:
        await session.rollback(); raise _error(exc) from exc


@router.get("/mapping-profiles", response_model=MappingProfileListResponse)
async def mapping_profiles(session: AsyncSession = Depends(get_session)) -> MappingProfileListResponse:
    return MappingProfileListResponse.model_validate({"items": await service.list_mapping_profiles(session)})


@router.post("/mapping-profiles", status_code=201, response_model=MappingProfileResponse)
async def create_mapping_profile(request: MappingProfileCreate, session: AsyncSession = Depends(get_session)) -> MappingProfileResponse:
    try:
        value = await service.create_mapping_profile(session, **request.model_dump(exclude={"primary_alignment_policy", "include_supplementary"}))
        await session.commit(); return MappingProfileResponse.model_validate(value)
    except service.OntSignalError as exc:
        await session.rollback(); raise _error(exc) from exc


@router.get("/calibrations", response_model=CalibrationArtifactListResponse)
async def calibrations(move_source_id: str | None = None, session: AsyncSession = Depends(get_session)) -> CalibrationArtifactListResponse:
    return CalibrationArtifactListResponse.model_validate({
        "items": await service.list_calibration_artifacts(session, move_source_id=move_source_id)
    })


@router.post("/runs/{run_id}/generations/{observed_generation}/calibrations", status_code=202, response_model=CalibrationJobResponse)
async def create_calibration(run_id: str, observed_generation: int, request: CalibrationCreate, session: AsyncSession = Depends(get_session)) -> CalibrationJobResponse:
    try:
        value = await service.create_calibration_job(session, run_id=run_id, observed_generation=observed_generation, **request.model_dump())
        await session.commit()
        return CalibrationJobResponse.model_validate(value)
    except (KeyError, service.OntSignalError) as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.get("/calibrations/{calibration_job_id}", response_model=CalibrationJobResponse)
async def get_calibration(calibration_job_id: str, session: AsyncSession = Depends(get_session)) -> CalibrationJobResponse:
    try:
        return CalibrationJobResponse.model_validate(
            await service.get_calibration_job(session, calibration_job_id)
        )
    except (KeyError, service.OntSignalError) as exc:
        raise _error(exc) from exc


@router.post("/calibrations/{calibration_job_id}/cancel", status_code=202, response_model=CalibrationJobResponse)
async def cancel_calibration(calibration_job_id: str, session: AsyncSession = Depends(get_session)) -> CalibrationJobResponse:
    try:
        value = await service.cancel_calibration_job(session, calibration_job_id)
        await session.commit()
        return CalibrationJobResponse.model_validate(value)
    except (KeyError, service.OntSignalError) as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.post("/runs/{run_id}/generations/{observed_generation}/mappings", status_code=202, response_model=MappingJobResponse)
async def create_mapping(run_id: str, observed_generation: int, request: MappingCreate, session: AsyncSession = Depends(get_session), domain_session: AsyncSession = Depends(get_molbio_ngs_session)) -> MappingJobResponse:
    try:
        value = await service.create_mapping_job(session, domain_session, run_id=run_id, observed_generation=observed_generation, **request.model_dump())
        await session.commit(); return MappingJobResponse.model_validate(value)
    except (KeyError, service.OntSignalError, service.ngs_alignment_sessions.AlignmentSessionError) as exc:
        await session.rollback(); raise _error(exc) from exc


@router.post("/external-alignment-jobs", status_code=201, response_model=ExternalAlignmentJobResponse)
async def create_external_alignment_job(
    request: ExternalAlignmentCreate,
    background_tasks: BackgroundTasks,
    http_request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    experiment_session: AsyncSession = Depends(get_experiment_session),
    domain_session: AsyncSession = Depends(get_molbio_ngs_session),
) -> ExternalAlignmentJobResponse:
    _comparison_principal(http_request)
    try:
        authority = await service.resolve_external_alignment_launch_authority(
            session,
            domain_session,
            move_source_id=request.move_source_id,
            reference_revision_id=request.reference_revision_id,
        )
        server_params = dict(authority["params"])
        submit_request = OntNgsSubmitRequest(
            name=request.name,
            params={
                "bam_path": authority["bam_path"],
                "reference_fasta": authority["reference_fasta"],
                **server_params,
            },
            source_instrument_run_id=server_params["source_instrument_run_id"],
        )
        job = _job_create_for_ont_submit(
            "ont_plasmid_qc",
            submit_request,
            trusted_server_params=frozenset(server_params),
            trusted_result_paths=frozenset({"bam_path"}),
            trusted_reference_fasta=Path(str(authority["reference_fasta"])),
        )
        created = await _create_pipeline_job(
            job,
            background_tasks,
            session,
            experiment_session,
            response,
            http_request,
        )
        created_job = JobResponse.model_validate(created)
        created_status = (
            created_job.status.value
            if hasattr(created_job.status, "value")
            else str(created_job.status)
        )
        return ExternalAlignmentJobResponse(
            job_id=created_job.id,
            name=created_job.name,
            status=created_status,
            dataset_id=str(authority["dataset_id"]),
            run_id=str(server_params["source_instrument_run_id"]),
            observed_generation=int(server_params["source_instrument_observed_generation"]),
            move_source_id=request.move_source_id,
            reference_revision_id=request.reference_revision_id,
        )
    except (KeyError, ValueError, service.OntSignalError) as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.get("/mappings/{mapping_job_id}", response_model=MappingJobResponse)
async def get_mapping(mapping_job_id: str, session: AsyncSession = Depends(get_session)) -> MappingJobResponse:
    try: return MappingJobResponse.model_validate(await service.get_mapping_job(session, mapping_job_id))
    except KeyError as exc: raise _error(exc) from exc


@router.post("/mappings/{mapping_job_id}/cancel", status_code=202, response_model=MappingJobResponse)
async def cancel_mapping(mapping_job_id: str, session: AsyncSession = Depends(get_session)) -> MappingJobResponse:
    try:
        value = await service.cancel_mapping_job(session, mapping_job_id); await session.commit(); return MappingJobResponse.model_validate(value)
    except KeyError as exc:
        await session.rollback(); raise _error(exc) from exc


@router.post("/views", status_code=202, response_model=ViewJobResponse, response_model_exclude_unset=True)
async def create_view(request: ViewCreate, session: AsyncSession = Depends(get_session)) -> ViewJobResponse:
    try:
        value = await service.create_view_job(session, **request.model_dump(exclude={"render_params"}), render_params=request.render_params.model_dump())
        await session.commit(); return ViewJobResponse.model_validate(value)
    except service.OntSignalError as exc:
        await session.rollback(); raise _error(exc) from exc


@router.get("/views/{view_job_id}", response_model=ViewJobResponse, response_model_exclude_unset=True)
async def get_view(view_job_id: str, session: AsyncSession = Depends(get_session)) -> ViewJobResponse:
    try: return ViewJobResponse.model_validate(await service.get_view_job(session, view_job_id))
    except KeyError as exc: raise _error(exc) from exc


@router.post("/views/{view_job_id}/cancel", status_code=202, response_model=ViewJobResponse, response_model_exclude_unset=True)
async def cancel_view(view_job_id: str, session: AsyncSession = Depends(get_session)) -> ViewJobResponse:
    try:
        value = await service.cancel_view_job(session, view_job_id); await session.commit(); return ViewJobResponse.model_validate(value)
    except KeyError as exc:
        await session.rollback(); raise _error(exc) from exc


@router.get("/views/{view_job_id}/artifacts/{artifact_id}", response_class=Response)
async def get_view_artifact(view_job_id: str, artifact_id: str, session: AsyncSession = Depends(get_session)) -> Response:
    try:
        artifact_bytes, metadata = await service.resolve_view_artifact(session, view_job_id, artifact_id)
        media = str(metadata["media_type"])
        headers = {
            "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; font-src data:; media-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'; sandbox allow-scripts",
            "Cross-Origin-Resource-Policy": "same-origin",
            "Referrer-Policy": "no-referrer",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
            "Cache-Control": "private, no-store",
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        }
        return Response(artifact_bytes, media_type=media, headers=headers)
    except (KeyError, service.OntSignalError) as exc:
        raise _error(exc) from exc


@router.post("/viewer-sessions", status_code=201, response_model=ViewerSessionResponse, response_model_exclude_unset=True)
async def create_viewer_session(request: ViewerSessionCreate, session: AsyncSession = Depends(get_session)) -> ViewerSessionResponse:
    try:
        payload = request.model_dump()
        signal_state = payload["signal_state"]
        for key in ("comparison_job_id", "comparison_review_id", "comparison_preview_digest", "comparison_settings"):
            if signal_state.get(key) is None:
                signal_state.pop(key, None)
        value = await service.create_viewer_session(session, **payload); await session.commit(); return ViewerSessionResponse.model_validate(value)
    except (KeyError, service.OntSignalError) as exc:
        await session.rollback(); raise _error(exc) from exc


@router.get("/viewer-sessions/{viewer_session_id}", response_model=ViewerSessionResponse, response_model_exclude_unset=True)
async def get_viewer_session(viewer_session_id: str, session: AsyncSession = Depends(get_session)) -> ViewerSessionResponse:
    try: return ViewerSessionResponse.model_validate(await service.get_viewer_session(session, viewer_session_id))
    except KeyError as exc: raise _error(exc) from exc


@router.patch("/viewer-sessions/{viewer_session_id}", response_model=ViewerSessionResponse, response_model_exclude_unset=True)
async def update_viewer_session(viewer_session_id: str, request: ViewerSessionUpdate, session: AsyncSession = Depends(get_session)) -> ViewerSessionResponse:
    try:
        payload = request.model_dump()
        signal_state = payload["signal_state"]
        for key in ("comparison_job_id", "comparison_review_id", "comparison_preview_digest", "comparison_settings"):
            if signal_state.get(key) is None:
                signal_state.pop(key, None)
        value = await service.update_viewer_session(session, viewer_session_id, **payload); await session.commit(); return ViewerSessionResponse.model_validate(value)
    except (KeyError, service.OntSignalError) as exc:
        await session.rollback(); raise _error(exc) from exc


@router.post("/comparisons/preview", response_model=ComparisonPreviewResponse)
async def preview_comparison(request: ComparisonPreviewCreate, _actor: str = Depends(_comparison_principal), session: AsyncSession = Depends(get_session), domain_session: AsyncSession = Depends(get_molbio_ngs_session)) -> ComparisonPreviewResponse:
    try:
        value = await service.preview_signal_comparison(session, domain_session, **request.model_dump(exclude={"simulation_settings", "render_params"}), simulation_settings=request.simulation_settings.model_dump(), render_params=request.render_params.model_dump())
        return ComparisonPreviewResponse.model_validate(value)
    except (KeyError, service.OntSignalError) as exc:
        raise _error(exc) from exc


@router.post("/comparisons", status_code=202, response_model=ComparisonJobResponse)
async def create_comparison(request: ComparisonCreate, _actor: str = Depends(_comparison_principal), session: AsyncSession = Depends(get_session), domain_session: AsyncSession = Depends(get_molbio_ngs_session)) -> ComparisonJobResponse:
    try:
        value = await service.create_signal_comparison(session, domain_session, **request.model_dump(exclude={"simulation_settings", "render_params"}), simulation_settings=request.simulation_settings.model_dump(), render_params=request.render_params.model_dump())
        await session.commit(); return ComparisonJobResponse.model_validate(value)
    except (KeyError, service.OntSignalError) as exc:
        await session.rollback(); raise _error(exc) from exc


@router.get("/comparisons/{comparison_job_id}", response_model=ComparisonJobResponse)
async def get_comparison(comparison_job_id: str, _actor: str = Depends(_comparison_principal), session: AsyncSession = Depends(get_session)) -> ComparisonJobResponse:
    try:
        return ComparisonJobResponse.model_validate(await service.get_signal_comparison(session, comparison_job_id))
    except (KeyError, service.OntSignalError) as exc:
        raise _error(exc) from exc


@router.post("/comparisons/{comparison_job_id}/cancel", status_code=202, response_model=ComparisonJobResponse)
async def cancel_comparison(comparison_job_id: str, _actor: str = Depends(_comparison_principal), session: AsyncSession = Depends(get_session)) -> ComparisonJobResponse:
    try:
        value = await service.cancel_signal_comparison(session, comparison_job_id)
        await session.commit(); return ComparisonJobResponse.model_validate(value)
    except (KeyError, service.OntSignalError) as exc:
        await session.rollback(); raise _error(exc) from exc


@router.post("/comparisons/{comparison_job_id}/fresh-attempt", status_code=202, response_model=ComparisonJobResponse)
async def fresh_comparison_attempt(comparison_job_id: str, _actor: str = Depends(_comparison_principal), session: AsyncSession = Depends(get_session)) -> ComparisonJobResponse:
    try:
        value = await service.fresh_signal_comparison_attempt(session, comparison_job_id)
        await session.commit(); return ComparisonJobResponse.model_validate(value)
    except (KeyError, service.OntSignalError) as exc:
        await session.rollback(); raise _error(exc) from exc


@router.get("/comparisons/{comparison_job_id}/artifacts/{artifact_id}", response_class=Response)
async def get_comparison_artifact(comparison_job_id: str, artifact_id: str, _actor: str = Depends(_comparison_principal), session: AsyncSession = Depends(get_session)) -> Response:
    try:
        body, metadata = await service.resolve_signal_comparison_artifact(session, comparison_job_id, artifact_id)
        return Response(body, media_type=str(metadata["media_type"]), headers={
            "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; font-src data:; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'; sandbox allow-scripts",
            "Cross-Origin-Resource-Policy": "same-origin", "Referrer-Policy": "no-referrer",
            "Cache-Control": "private, no-store", "Content-Disposition": "inline", "X-Content-Type-Options": "nosniff",
        })
    except (KeyError, service.OntSignalError) as exc:
        raise _error(exc) from exc


@router.get("/comparisons/{comparison_job_id}/reviews", response_model=ComparisonReviewListResponse)
async def comparison_reviews(comparison_job_id: str, _actor: str = Depends(_comparison_principal), session: AsyncSession = Depends(get_session)) -> ComparisonReviewListResponse:
    try:
        return ComparisonReviewListResponse.model_validate({"items": await service.list_signal_comparison_reviews(session, comparison_job_id)})
    except (KeyError, service.OntSignalError) as exc:
        raise _error(exc) from exc


@router.post("/comparisons/{comparison_job_id}/reviews", status_code=201, response_model=ComparisonReviewResponse)
async def create_comparison_review(comparison_job_id: str, request: ComparisonReviewCreate, reviewer: str = Depends(_comparison_principal), session: AsyncSession = Depends(get_session)) -> ComparisonReviewResponse:
    try:
        value = await service.create_signal_comparison_review(session, comparison_job_id, reviewer_identity=reviewer, **request.model_dump())
        await session.commit(); return ComparisonReviewResponse.model_validate(value)
    except (KeyError, service.OntSignalError) as exc:
        await session.rollback(); raise _error(exc) from exc
