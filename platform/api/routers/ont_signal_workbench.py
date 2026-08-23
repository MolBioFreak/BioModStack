"""Closed typed API for the governed ONT Read and Signal Workbench."""
from __future__ import annotations

import asyncio
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from molbio_ngs_database import get_molbio_ngs_session
from services import ont_signal_workbench as service

router = APIRouter()
OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


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
    mode: Literal["raw_waveform", "read", "reference", "pileup"]
    render_params: RenderParams
    view_job_id: str | None
    read_mapping_job_id: str | None
    reference_mapping_job_id: str | None


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
    mode: Literal["raw_waveform", "read", "reference", "pileup"] | None = None
    render_params: RenderParams | None = None
    view_job_id: str | None = None
    read_mapping_job_id: str | None = None
    reference_mapping_job_id: str | None = None
    selected_read_id: str | None = None
    capabilities: WorkbenchModesResponse | None = None


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
        value = await service.create_viewer_session(session, **request.model_dump()); await session.commit(); return ViewerSessionResponse.model_validate(value)
    except (KeyError, service.OntSignalError) as exc:
        await session.rollback(); raise _error(exc) from exc


@router.get("/viewer-sessions/{viewer_session_id}", response_model=ViewerSessionResponse, response_model_exclude_unset=True)
async def get_viewer_session(viewer_session_id: str, session: AsyncSession = Depends(get_session)) -> ViewerSessionResponse:
    try: return ViewerSessionResponse.model_validate(await service.get_viewer_session(session, viewer_session_id))
    except KeyError as exc: raise _error(exc) from exc


@router.patch("/viewer-sessions/{viewer_session_id}", response_model=ViewerSessionResponse, response_model_exclude_unset=True)
async def update_viewer_session(viewer_session_id: str, request: ViewerSessionUpdate, session: AsyncSession = Depends(get_session)) -> ViewerSessionResponse:
    try:
        value = await service.update_viewer_session(session, viewer_session_id, **request.model_dump()); await session.commit(); return ViewerSessionResponse.model_validate(value)
    except (KeyError, service.OntSignalError) as exc:
        await session.rollback(); raise _error(exc) from exc
