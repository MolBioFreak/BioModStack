"""Closed typed API for the governed ONT Read and Signal Workbench."""
from __future__ import annotations

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
    source_job_id: str | None = None
    external_registration_receipt_id: str | None = None

    @model_validator(mode="after")
    def one_source(self):
        if bool(self.source_job_id) == bool(self.external_registration_receipt_id):
            raise ValueError("exactly one source job or external registration receipt is required")
        return self


class MappingProfileCreate(ClosedModel):
    name: str = Field(min_length=1, max_length=255)
    molecule_type: Literal["dna", "rna"]
    basecall_model_id: str = Field(min_length=1, max_length=255)
    kmer_length: int = Field(ge=1, le=32)
    signal_move_offset: int = Field(ge=-64, le=64)
    parameter_source: Literal["approved_calibration", "exact_upstream_profile"]
    calibration_artifact_id: str | None = None
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
    point_size: float = Field(ge=0.05, le=10, default=0.5)
    base_limit: int = Field(ge=1, le=100_000, default=1000)
    signal_sample_limit: int = Field(ge=1, le=2_000_000, default=100_000)
    pileup_read_limit: int = Field(ge=1, le=100, default=20)
    loose_bound: StrictBool = False
    show_samples: StrictBool = True
    show_base_colours: StrictBool = True
    remove_signal_outliers: StrictBool = False
    managed_bed_artifact_id: str | None = None


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


class ViewerSessionUpdate(ClosedModel):
    expected_revision: int = Field(ge=1)
    contig: str | None = None
    locus_start: int | None = Field(default=None, ge=1)
    locus_end: int | None = Field(default=None, ge=1)
    selected_read_id: str | None = None
    igv_state: dict[str, Any]
    signal_state: dict[str, Any]


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail="governed signal-workbench authority not found")
    return HTTPException(status_code=409, detail=str(exc))


@router.get("/runs/{run_id}/generations/{observed_generation}/capabilities")
async def capabilities(run_id: str, observed_generation: int, session: AsyncSession = Depends(get_session)):
    return await service.workbench_capabilities(session, run_id=run_id, observed_generation=observed_generation)


@router.get("/runs/{run_id}/generations/{observed_generation}/move-sources")
async def move_sources(run_id: str, observed_generation: int, session: AsyncSession = Depends(get_session)):
    return {"items": await service.list_move_sources(session, run_id=run_id, observed_generation=observed_generation)}


@router.post("/runs/{run_id}/generations/{observed_generation}/move-sources", status_code=202)
async def register_move_source(run_id: str, observed_generation: int, request: MoveSourceCreate, session: AsyncSession = Depends(get_session)):
    try:
        value = await service.register_move_source(
            session, run_id=run_id, observed_generation=observed_generation,
            raw_representation_id=request.raw_representation_id, input_file_id=request.input_file_id,
            molecule_type=request.molecule_type, source_job_id=request.source_job_id,
            external_registration_receipt_id=request.external_registration_receipt_id,
            source_runtime_identity=None,
        )
        await session.commit()
        return value
    except (KeyError, service.OntSignalError) as exc:
        await session.rollback(); raise _error(exc) from exc


@router.get("/mapping-profiles")
async def mapping_profiles(session: AsyncSession = Depends(get_session)):
    return {"items": await service.list_mapping_profiles(session)}


@router.post("/mapping-profiles", status_code=201)
async def create_mapping_profile(request: MappingProfileCreate, session: AsyncSession = Depends(get_session)):
    try:
        value = await service.create_mapping_profile(session, **request.model_dump(exclude={"primary_alignment_policy", "include_supplementary"}))
        await session.commit(); return value
    except service.OntSignalError as exc:
        await session.rollback(); raise _error(exc) from exc


@router.get("/calibrations")
async def calibrations(move_source_id: str | None = None, session: AsyncSession = Depends(get_session)):
    return {"items": await service.list_calibration_artifacts(session, move_source_id=move_source_id)}


@router.post("/runs/{run_id}/generations/{observed_generation}/calibrations", status_code=202)
async def create_calibration(run_id: str, observed_generation: int, request: CalibrationCreate, session: AsyncSession = Depends(get_session)):
    try:
        value = await service.create_calibration_job(session, run_id=run_id, observed_generation=observed_generation, **request.model_dump())
        await session.commit()
        return value
    except (KeyError, service.OntSignalError) as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.get("/calibrations/{calibration_job_id}")
async def get_calibration(calibration_job_id: str, session: AsyncSession = Depends(get_session)):
    try:
        return await service.get_calibration_job(session, calibration_job_id)
    except (KeyError, service.OntSignalError) as exc:
        raise _error(exc) from exc


@router.post("/calibrations/{calibration_job_id}/cancel", status_code=202)
async def cancel_calibration(calibration_job_id: str, session: AsyncSession = Depends(get_session)):
    try:
        value = await service.cancel_calibration_job(session, calibration_job_id)
        await session.commit()
        return value
    except (KeyError, service.OntSignalError) as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.post("/runs/{run_id}/generations/{observed_generation}/mappings", status_code=202)
async def create_mapping(run_id: str, observed_generation: int, request: MappingCreate, session: AsyncSession = Depends(get_session), domain_session: AsyncSession = Depends(get_molbio_ngs_session)):
    try:
        value = await service.create_mapping_job(session, domain_session, run_id=run_id, observed_generation=observed_generation, **request.model_dump())
        await session.commit(); return value
    except (KeyError, service.OntSignalError, service.ngs_alignment_sessions.AlignmentSessionError) as exc:
        await session.rollback(); raise _error(exc) from exc


@router.get("/mappings/{mapping_job_id}")
async def get_mapping(mapping_job_id: str, session: AsyncSession = Depends(get_session)):
    try: return await service.get_mapping_job(session, mapping_job_id)
    except KeyError as exc: raise _error(exc) from exc


@router.post("/mappings/{mapping_job_id}/cancel", status_code=202)
async def cancel_mapping(mapping_job_id: str, session: AsyncSession = Depends(get_session)):
    try:
        value = await service.cancel_mapping_job(session, mapping_job_id); await session.commit(); return value
    except KeyError as exc:
        await session.rollback(); raise _error(exc) from exc


@router.post("/views", status_code=202)
async def create_view(request: ViewCreate, session: AsyncSession = Depends(get_session)):
    try:
        value = await service.create_view_job(session, **request.model_dump(exclude={"render_params"}), render_params=request.render_params.model_dump())
        await session.commit(); return value
    except service.OntSignalError as exc:
        await session.rollback(); raise _error(exc) from exc


@router.get("/views/{view_job_id}")
async def get_view(view_job_id: str, session: AsyncSession = Depends(get_session)):
    try: return await service.get_view_job(session, view_job_id)
    except KeyError as exc: raise _error(exc) from exc


@router.post("/views/{view_job_id}/cancel", status_code=202)
async def cancel_view(view_job_id: str, session: AsyncSession = Depends(get_session)):
    try:
        value = await service.cancel_view_job(session, view_job_id); await session.commit(); return value
    except KeyError as exc:
        await session.rollback(); raise _error(exc) from exc


@router.get("/views/{view_job_id}/artifacts/{artifact_id}")
async def get_view_artifact(view_job_id: str, artifact_id: str, session: AsyncSession = Depends(get_session)):
    try:
        path, metadata = await service.resolve_view_artifact(session, view_job_id, artifact_id)
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
        return Response(path.read_bytes(), media_type=media, headers=headers)
    except (KeyError, service.OntSignalError) as exc:
        raise _error(exc) from exc


@router.post("/viewer-sessions", status_code=201)
async def create_viewer_session(request: ViewerSessionCreate, session: AsyncSession = Depends(get_session)):
    try:
        value = await service.create_viewer_session(session, **request.model_dump()); await session.commit(); return value
    except (KeyError, service.OntSignalError) as exc:
        await session.rollback(); raise _error(exc) from exc


@router.get("/viewer-sessions/{viewer_session_id}")
async def get_viewer_session(viewer_session_id: str, session: AsyncSession = Depends(get_session)):
    try: return await service.get_viewer_session(session, viewer_session_id)
    except KeyError as exc: raise _error(exc) from exc


@router.patch("/viewer-sessions/{viewer_session_id}")
async def update_viewer_session(viewer_session_id: str, request: ViewerSessionUpdate, session: AsyncSession = Depends(get_session)):
    try:
        value = await service.update_viewer_session(session, viewer_session_id, **request.model_dump()); await session.commit(); return value
    except (KeyError, service.OntSignalError) as exc:
        await session.rollback(); raise _error(exc) from exc
