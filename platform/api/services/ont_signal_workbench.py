"""Governed move-table, Squigualiser mapping, bounded view, and viewer-session contracts."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    InputFile,
    Job,
    OntInstrumentRunEvent,
    OntMoveTableSource,
    OntRawSignalRepresentation,
    OntSignalCalibrationArtifact,
    OntSignalCalibrationJob,
    OntSignalMappingArtifact,
    OntSignalMappingEvent,
    OntSignalMappingJob,
    OntSignalMappingProfile,
    OntSignalViewerSession,
    OntSquigualiserViewJob,
)
from molbio_ngs_models import MolBioNGSReferenceArtifact, MolBioNGSReferenceRevision
from paths import get_results_dir
from services import ngs_alignment_sessions

HEX64 = re.compile(r"^[0-9a-f]{64}$")
OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
CONTIG = re.compile(r"^[A-Za-z0-9_.:-]{1,255}$")
LEASE_SECONDS = 300
MAX_VIEW_HTML_BYTES = 8 * 1024 * 1024
MAX_VIEW_SVG_BYTES = 4 * 1024 * 1024
MAX_LOG_BYTES = 256 * 1024
MAX_REGION_BP = 250_000
MAX_BASE_LIMIT = 100_000
MAX_SAMPLE_LIMIT = 2_000_000
MAX_PILEUP_READS = 100
MIN_CALIBRATION_READS = 1
MAX_CALIBRATION_READS = 100
CALIBRATION_CANDIDATE_KMER_LENGTH = 9


class OntSignalError(ValueError):
    pass


def _now() -> datetime:
    return datetime.utcnow()


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _public_time(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _input_path(item: InputFile) -> Path:
    path = Path(item.directory) / item.filename
    if not path.is_absolute() or path.name != item.filename:
        raise OntSignalError("tracked move-table input authority is invalid")
    return path


def _stable_file_identity(path: Path) -> tuple[str, int]:
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise OntSignalError("move-table source must be a retained regular file")
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after or after.st_size <= 0:
        raise OntSignalError("move-table source identity changed during registration")
    return digest.hexdigest(), after.st_size


def _source_public(row: OntMoveTableSource) -> dict[str, Any]:
    return {
        "move_source_id": row.id,
        "run_id": row.run_id,
        "observed_generation": row.observed_generation,
        "raw_representation_id": row.raw_representation_id,
        "artifact_id": row.input_file_id,
        "artifact_sha256": row.artifact_sha256,
        "artifact_size_bytes": row.artifact_size_bytes,
        "bam_header_sha256": row.bam_header_sha256,
        "record_count": row.record_count,
        "unique_read_count": row.unique_read_count,
        "tag_counts": {"mv": row.mv_tag_count, "ts": row.ts_tag_count, "ns": row.ns_tag_count},
        "basecall_model_id": row.basecall_model_id,
        "molecule_type": row.molecule_type,
        "source_job_id": row.source_job_id,
        "external_registration_receipt_id": row.external_registration_receipt_id,
        "source_runtime_identity": row.source_runtime_identity,
        "read_inventory_sha256": row.read_inventory_sha256,
        "state": row.validation_state,
        "reason_code": row.reason_code,
        "validation_receipt": row.validation_receipt,
        "created_at": _public_time(row.created_at),
        "validated_at": _public_time(row.validated_at),
    }


def _profile_public(row: OntSignalMappingProfile) -> dict[str, Any]:
    return {
        "mapping_profile_id": row.id,
        "name": row.name,
        "molecule_type": row.molecule_type,
        "basecall_model_id": row.basecall_model_id,
        "kmer_length": row.kmer_length,
        "signal_move_offset": row.signal_move_offset,
        "parameter_source": row.parameter_source,
        "calibration_artifact_id": row.calibration_artifact_id,
        "primary_alignment_policy": row.primary_alignment_policy,
        "minimum_mapq": row.minimum_mapq,
        "include_supplementary": False,
        "read_set_selection": row.read_set_selection,
        "approval_receipt": row.approval_receipt,
        "approved_at": _public_time(row.approved_at),
        "approved_by": row.approved_by,
    }


def _artifact_public(row: OntSignalMappingArtifact) -> dict[str, Any]:
    return {
        "mapping_artifact_id": row.id,
        "mapping_job_id": row.mapping_job_id,
        "kind": row.kind,
        "sha256": row.sha256,
        "size_bytes": row.size_bytes,
        "media_type": row.media_type,
        "parent_identities": row.parent_identities,
        "runtime_identity": row.runtime_identity,
        "validation_receipt": row.validation_receipt,
        "created_at": _public_time(row.created_at),
    }


async def register_move_source(
    session: AsyncSession,
    *,
    run_id: str,
    observed_generation: int,
    raw_representation_id: str,
    input_file_id: str,
    molecule_type: str,
    source_job_id: str | None,
    external_registration_receipt_id: str | None,
    source_runtime_identity: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if molecule_type not in {"dna", "rna"}:
        raise OntSignalError("molecule_type must be dna or rna")
    if bool(source_job_id) == bool(external_registration_receipt_id):
        raise OntSignalError("exactly one governed move-table source receipt is required")
    representation = (
        await session.execute(
            select(OntRawSignalRepresentation).where(
                OntRawSignalRepresentation.id == raw_representation_id,
                OntRawSignalRepresentation.run_id == run_id,
                OntRawSignalRepresentation.observed_generation == observed_generation,
            )
        )
    ).scalar_one_or_none()
    if representation is None:
        raise KeyError("raw representation not found")
    receipts = representation.validation_receipts if isinstance(representation.validation_receipts, dict) else {}
    if representation.format != "blow5" or representation.state != "ready" or receipts.get("adjacent_index") is not True:
        raise OntSignalError("ready indexed BLOW5 authority is required")
    tracked = await session.get(InputFile, input_file_id)
    if tracked is None:
        raise KeyError("tracked move-table input not found")
    if not tracked.filename.lower().endswith((".bam", ".ubam")):
        raise OntSignalError("tracked move-table source must be BAM")
    if source_job_id is not None:
        source_job = await session.get(Job, source_job_id)
        if source_job is None or source_job.status != "completed":
            raise OntSignalError("completed source job authority is required")
    artifact_sha256, size_bytes = _stable_file_identity(_input_path(tracked))
    existing = (
        await session.execute(
            select(OntMoveTableSource).where(
                OntMoveTableSource.run_id == run_id,
                OntMoveTableSource.observed_generation == observed_generation,
                OntMoveTableSource.artifact_sha256 == artifact_sha256,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return _source_public(existing)
    source = OntMoveTableSource(
        id=_id("ont-moves"),
        run_id=run_id,
        observed_generation=observed_generation,
        raw_representation_id=raw_representation_id,
        input_file_id=input_file_id,
        source_job_id=source_job_id,
        external_registration_receipt_id=external_registration_receipt_id,
        artifact_sha256=artifact_sha256,
        artifact_size_bytes=size_bytes,
        molecule_type=molecule_type,
        source_runtime_identity=dict(source_runtime_identity or {}),
        validation_state="requested",
        reason_code="move_source_validation_requested",
        validation_receipt={"raw_manifest_sha256": representation.manifest_sha256},
        created_at=_now(),
    )
    session.add(source)
    await session.flush()
    return _source_public(source)


async def list_move_sources(session: AsyncSession, *, run_id: str, observed_generation: int) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(OntMoveTableSource)
            .where(OntMoveTableSource.run_id == run_id, OntMoveTableSource.observed_generation == observed_generation)
            .order_by(OntMoveTableSource.created_at, OntMoveTableSource.id)
        )
    ).scalars()
    return [_source_public(row) for row in rows]


async def list_mapping_profiles(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (await session.execute(select(OntSignalMappingProfile).order_by(OntSignalMappingProfile.approved_at))).scalars()
    return [_profile_public(row) for row in rows]


def _calibration_artifact_public(row: OntSignalCalibrationArtifact) -> dict[str, Any]:
    return {
        "calibration_artifact_id": row.id,
        "raw_representation_id": row.raw_representation_id,
        "move_source_id": row.move_source_id,
        "basecall_model_id": row.basecall_model_id,
        "sample_selection": row.sample_selection,
        "recommended_kmer_length": row.recommended_kmer_length,
        "recommended_signal_move_offset": row.recommended_signal_move_offset,
        "score_evidence": row.score_evidence,
        "runtime_identity": row.runtime_identity,
        "parent_sha256s": row.parent_sha256s,
        "artifact_sha256": row.artifact_sha256,
        "created_at": _public_time(row.created_at),
    }


async def list_calibration_artifacts(session: AsyncSession, *, move_source_id: str | None = None) -> list[dict[str, Any]]:
    statement = select(OntSignalCalibrationArtifact)
    if move_source_id:
        statement = statement.where(OntSignalCalibrationArtifact.move_source_id == move_source_id)
    rows = (await session.execute(statement.order_by(OntSignalCalibrationArtifact.created_at))).scalars()
    return [_calibration_artifact_public(row) for row in rows]


async def _calibration_public(session: AsyncSession, row: OntSignalCalibrationJob) -> dict[str, Any]:
    artifact = None if row.calibration_artifact_id is None else await session.get(OntSignalCalibrationArtifact, row.calibration_artifact_id)
    if (row.state == "ready") != (artifact is not None):
        raise OntSignalError("calibration state and immutable artifact publication diverged")
    return {
        "calibration_job_id": row.id,
        "run_id": row.run_id,
        "observed_generation": row.observed_generation,
        "raw_representation_id": row.raw_representation_id,
        "move_source_id": row.move_source_id,
        "sample_count": row.sample_count,
        "request_fingerprint": row.request_fingerprint,
        "state": row.state,
        "reason_code": row.reason_code,
        "attempt": row.attempt,
        "resource_snapshot": row.resource_snapshot,
        "stage_receipts": row.stage_receipts,
        "failure_code": row.failure_code,
        "failure_message": row.failure_message,
        "artifact": None if artifact is None else _calibration_artifact_public(artifact),
        "created_at": _public_time(row.created_at),
        "updated_at": _public_time(row.updated_at),
        "completed_at": _public_time(row.completed_at),
    }


async def create_calibration_job(
    session: AsyncSession,
    *,
    run_id: str,
    observed_generation: int,
    raw_representation_id: str,
    move_source_id: str,
    sample_count: int,
) -> dict[str, Any]:
    if not OPAQUE_ID.fullmatch(raw_representation_id) or not OPAQUE_ID.fullmatch(move_source_id):
        raise OntSignalError("calibration parents must be opaque governed IDs")
    if not MIN_CALIBRATION_READS <= sample_count <= MAX_CALIBRATION_READS:
        raise OntSignalError("calibration sample count is outside bounded policy")
    representation = await session.get(OntRawSignalRepresentation, raw_representation_id)
    source = await session.get(OntMoveTableSource, move_source_id)
    receipts = {} if representation is None or not isinstance(representation.validation_receipts, dict) else representation.validation_receipts
    if representation is None or source is None or (
        representation.run_id != run_id or representation.observed_generation != observed_generation
        or representation.format != "blow5" or representation.state != "ready"
        or receipts.get("adjacent_index") is not True
        or source.run_id != run_id or source.observed_generation != observed_generation
        or source.raw_representation_id != representation.id or source.validation_state != "ready"
        or not source.basecall_model_id or not source.read_inventory_sha256
    ):
        raise OntSignalError("calibration parents do not share one exact ready indexed-BLOW5 generation")
    request_identity = {
        "schema": "bms.ont-signal-calibration-request.v1",
        "run_id": run_id,
        "observed_generation": observed_generation,
        "raw_representation_id": raw_representation_id,
        "move_source_id": move_source_id,
        "sample_count": sample_count,
        "candidate_kmer_length": CALIBRATION_CANDIDATE_KMER_LENGTH,
        "squigualiser_version": "0.7.0",
        "squigualiser_commit": "5a2404f1f43bc3227a85475c59b2b77970078b2e",
    }
    fingerprint = _digest(request_identity)
    existing = (await session.execute(select(OntSignalCalibrationJob).where(OntSignalCalibrationJob.request_fingerprint == fingerprint))).scalar_one_or_none()
    if existing is not None:
        return await _calibration_public(session, existing)
    parents = {
        "raw_manifest_sha256": representation.manifest_sha256,
        "raw_artifacts": representation.artifact_manifest,
        "move_bam_sha256": source.artifact_sha256,
        "move_read_inventory_sha256": source.read_inventory_sha256,
        "basecall_model_id": source.basecall_model_id,
        "molecule_type": source.molecule_type,
    }
    row = OntSignalCalibrationJob(
        id=_id("ont-signal-calibration"), run_id=run_id, observed_generation=observed_generation,
        raw_representation_id=raw_representation_id, move_source_id=move_source_id,
        sample_count=sample_count, request_fingerprint=fingerprint, state="requested",
        reason_code="calibration_requested", resource_snapshot={"request": request_identity, "parents": parents},
        stage_receipts={"request_identity_sha256": fingerprint}, created_at=_now(), updated_at=_now(),
    )
    session.add(row)
    await session.flush()
    return await _calibration_public(session, row)


async def get_calibration_job(session: AsyncSession, calibration_job_id: str) -> dict[str, Any]:
    row = await session.get(OntSignalCalibrationJob, calibration_job_id)
    if row is None:
        raise KeyError("calibration job not found")
    return await _calibration_public(session, row)


async def cancel_calibration_job(session: AsyncSession, calibration_job_id: str) -> dict[str, Any]:
    row = await session.get(OntSignalCalibrationJob, calibration_job_id)
    if row is None:
        raise KeyError("calibration job not found")
    if row.state == "requested":
        cancelled_at = _now()
        row.state, row.reason_code = "cancelled", "cancelled_before_claim"
        row.cancel_requested_at = row.updated_at = row.completed_at = cancelled_at
        row.stage_receipts = {**(row.stage_receipts or {}), "cancellation": {"requested_at": cancelled_at.isoformat(), "disposition": "cancelled_before_claim"}}
    elif row.state == "running":
        requested_at = _now()
        row.cancel_requested_at = row.updated_at = requested_at
        row.reason_code = "cancellation_requested"
        row.stage_receipts = {**(row.stage_receipts or {}), "cancellation": {"requested_at": requested_at.isoformat(), "disposition": "worker_termination_requested"}}
    await session.flush()
    return await _calibration_public(session, row)


async def create_mapping_profile(
    session: AsyncSession,
    *,
    name: str,
    molecule_type: str,
    basecall_model_id: str,
    kmer_length: int,
    signal_move_offset: int,
    parameter_source: str,
    calibration_artifact_id: str | None,
    minimum_mapq: int,
    read_set_selection: str,
    approval_receipt: Mapping[str, Any],
    approved_by: str | None,
) -> dict[str, Any]:
    if parameter_source not in {"approved_calibration", "exact_upstream_profile"}:
        raise OntSignalError("unsupported mapping parameter source")
    if molecule_type not in {"dna", "rna"} or not 1 <= kmer_length <= 32 or not -64 <= signal_move_offset <= 64:
        raise OntSignalError("mapping profile parameters are outside bounded policy")
    if minimum_mapq != 0 or read_set_selection != "immutable_full_set":
        raise OntSignalError("mapping profile must use primary-only MAPQ 0 immutable full-set policy")
    receipt = dict(approval_receipt)
    if receipt.get("approved") is not True:
        raise OntSignalError("literal operator approval receipt is required")
    if parameter_source == "approved_calibration":
        calibration = await session.get(OntSignalCalibrationArtifact, calibration_artifact_id)
        if calibration is None:
            raise OntSignalError("governed calibration artifact is required")
        if (
            calibration.basecall_model_id != basecall_model_id
            or calibration.recommended_kmer_length != kmer_length
            or calibration.recommended_signal_move_offset != signal_move_offset
        ):
            raise OntSignalError("profile parameters do not equal approved calibration evidence")
        producer = (await session.execute(select(OntSignalCalibrationJob).where(
            OntSignalCalibrationJob.calibration_artifact_id == calibration.id,
            OntSignalCalibrationJob.state == "ready",
        ))).scalar_one_or_none()
        if producer is None:
            raise OntSignalError("calibration artifact has no ready governed producer")
    elif calibration_artifact_id is not None:
        raise OntSignalError("exact upstream profiles cannot claim calibration authority")
    existing = (await session.execute(select(OntSignalMappingProfile).where(
        OntSignalMappingProfile.basecall_model_id == basecall_model_id.strip(),
        OntSignalMappingProfile.molecule_type == molecule_type,
        OntSignalMappingProfile.kmer_length == kmer_length,
        OntSignalMappingProfile.signal_move_offset == signal_move_offset,
        OntSignalMappingProfile.parameter_source == parameter_source,
        OntSignalMappingProfile.calibration_artifact_id == calibration_artifact_id,
        OntSignalMappingProfile.minimum_mapq == 0,
        OntSignalMappingProfile.include_supplementary.is_(False),
        OntSignalMappingProfile.read_set_selection == "immutable_full_set",
    ))).scalar_one_or_none()
    if existing is not None:
        return _profile_public(existing)
    profile = OntSignalMappingProfile(
        id=_id("ont-signal-profile"), name=name.strip(), molecule_type=molecule_type,
        basecall_model_id=basecall_model_id.strip(), kmer_length=kmer_length,
        signal_move_offset=signal_move_offset, parameter_source=parameter_source,
        calibration_artifact_id=calibration_artifact_id, primary_alignment_policy="primary_only",
        minimum_mapq=minimum_mapq, include_supplementary=False,
        read_set_selection=read_set_selection, approval_receipt=receipt,
        approved_at=_now(), approved_by=approved_by, created_at=_now(),
    )
    session.add(profile)
    await session.flush()
    return _profile_public(profile)


async def _resolve_reference_authority(
    domain_session: AsyncSession,
    reference_revision_id: str,
) -> tuple[MolBioNGSReferenceRevision, MolBioNGSReferenceArtifact]:
    revision = await domain_session.get(MolBioNGSReferenceRevision, reference_revision_id)
    if revision is None:
        raise OntSignalError("managed immutable reference revision is unavailable")
    artifact = await domain_session.get(MolBioNGSReferenceArtifact, revision.artifact_id)
    if artifact is None or artifact.reference_id != revision.reference_id:
        raise OntSignalError("managed reference artifact authority is unavailable")
    if revision.canonical_fasta_sha256 != artifact.sha256 or revision.canonical_fasta_size_bytes != artifact.size_bytes:
        raise OntSignalError("managed reference revision digest authority diverged")
    return revision, artifact


def _alignment_authority(job: Job) -> dict[str, str]:
    params = job.params if isinstance(job.params, dict) else {}
    authority = {
        "source_reference_sha256": params.get("reference_sequence_sha256"),
        "workflow_id": params.get("ont_workflow_id") or params.get("workflow_id"),
        "input_mode": params.get("ont_input_mode") or params.get("input_mode"),
    }
    if not all(isinstance(value, str) and value for value in authority.values()):
        raise OntSignalError("alignment job provenance is incomplete")
    return {key: str(value) for key, value in authority.items()}


async def create_mapping_job(
    session: AsyncSession,
    domain_session: AsyncSession,
    *,
    mode: str,
    run_id: str,
    observed_generation: int,
    raw_representation_id: str,
    move_source_id: str,
    mapping_profile_id: str,
    reference_revision_id: str | None,
    alignment_job_id: str | None,
    alignment_session_id: str | None,
) -> dict[str, Any]:
    if mode not in {"signal_to_read", "signal_to_reference"}:
        raise OntSignalError("unsupported mapping mode")
    representation = await session.get(OntRawSignalRepresentation, raw_representation_id)
    source = await session.get(OntMoveTableSource, move_source_id)
    profile = await session.get(OntSignalMappingProfile, mapping_profile_id)
    if representation is None or source is None or profile is None:
        raise OntSignalError("mapping parent authority is unavailable")
    if (
        representation.run_id != run_id or representation.observed_generation != observed_generation
        or representation.format != "blow5" or representation.state != "ready"
        or source.run_id != run_id or source.observed_generation != observed_generation
        or source.raw_representation_id != raw_representation_id or source.validation_state != "ready"
    ):
        raise OntSignalError("mapping parents do not share one ready run-generation authority")
    if source.basecall_model_id != profile.basecall_model_id or source.molecule_type != profile.molecule_type:
        raise OntSignalError("move-table model is incompatible with the approved mapping profile")
    if profile.primary_alignment_policy != "primary_only" or profile.minimum_mapq != 0 or profile.include_supplementary or profile.read_set_selection != "immutable_full_set":
        raise OntSignalError("mapping profile does not use the fixed v1 primary full-set policy")
    if profile.parameter_source == "approved_calibration":
        calibration = await session.get(OntSignalCalibrationArtifact, profile.calibration_artifact_id)
        if calibration is None or calibration.raw_representation_id != raw_representation_id or calibration.move_source_id != move_source_id:
            raise OntSignalError("approved calibration profile is not exact for the selected signal and move parents")
    parent_mapping_job_id = None
    reference_identity: dict[str, Any] = {}
    if mode == "signal_to_reference":
        if not all((reference_revision_id, alignment_job_id, alignment_session_id)):
            raise OntSignalError("reference revision and alignment-session authority are required")
        revision, reference_artifact = await _resolve_reference_authority(domain_session, str(reference_revision_id))
        alignment_job = await session.get(Job, alignment_job_id)
        if alignment_job is None or alignment_job.status != "completed":
            raise OntSignalError("completed governed alignment job is required")
        authority = _alignment_authority(alignment_job)
        if authority["source_reference_sha256"] != revision.normalized_sequence_sha256:
            raise OntSignalError("alignment job reference does not equal the managed reference revision")
        alignment = ngs_alignment_sessions.resolve_alignment_session(
            alignment_job.id,
            str(alignment_session_id),
            **authority,
            job_output_dir=getattr(alignment_job, "child_output_dir", None) or alignment_job.output_dir,
        )
        if alignment.get("ready") is not True:
            raise OntSignalError("alignment session is not ready")
        parent = (
            await session.execute(
                select(OntSignalMappingJob).where(
                    OntSignalMappingJob.mode == "signal_to_read",
                    OntSignalMappingJob.raw_representation_id == raw_representation_id,
                    OntSignalMappingJob.move_source_id == move_source_id,
                    OntSignalMappingJob.mapping_profile_id == mapping_profile_id,
                    OntSignalMappingJob.state == "ready",
                )
            )
        ).scalar_one_or_none()
        if parent is None:
            raise OntSignalError("ready signal-to-read mapping is required")
        parent_mapping_job_id = parent.id
        reference_identity = {
            "reference_revision_id": revision.id,
            "reference_artifact_id": reference_artifact.id,
            "reference_fasta_sha256": reference_artifact.sha256,
            "contig_inventory_sha256": revision.contig_manifest_sha256,
            "alignment_session_id": alignment_session_id,
            "alignment_artifacts": alignment.get("artifacts"),
        }
    elif any((reference_revision_id, alignment_job_id, alignment_session_id)):
        raise OntSignalError("signal-to-read mapping cannot accept reference alignment parents")
    existing = (
        await session.execute(
            select(OntSignalMappingJob).where(
                OntSignalMappingJob.mode == mode,
                OntSignalMappingJob.raw_representation_id == raw_representation_id,
                OntSignalMappingJob.move_source_id == move_source_id,
                OntSignalMappingJob.mapping_profile_id == mapping_profile_id,
                OntSignalMappingJob.reference_revision_id.is_(None) if reference_revision_id is None else OntSignalMappingJob.reference_revision_id == reference_revision_id,
                OntSignalMappingJob.alignment_session_id.is_(None) if alignment_session_id is None else OntSignalMappingJob.alignment_session_id == alignment_session_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return await get_mapping_job(session, existing.id)
    parents = {
        "raw_manifest_sha256": representation.manifest_sha256,
        "raw_artifacts": representation.artifact_manifest,
        "move_bam_sha256": source.artifact_sha256,
        "move_read_inventory_sha256": source.read_inventory_sha256,
        "mapping_profile_id": profile.id,
        **reference_identity,
    }
    job = OntSignalMappingJob(
        id=_id("ont-signal-map"), mode=mode, run_id=run_id, observed_generation=observed_generation,
        raw_representation_id=raw_representation_id, move_source_id=move_source_id,
        mapping_profile_id=mapping_profile_id, reference_revision_id=reference_revision_id,
        alignment_job_id=alignment_job_id, alignment_session_id=alignment_session_id,
        parent_mapping_job_id=parent_mapping_job_id, state="requested",
        reason_code=f"{mode}_mapping_requested", resource_snapshot={"parents": parents},
        stage_receipts={}, created_at=_now(), updated_at=_now(),
    )
    session.add(job)
    session.add(OntSignalMappingEvent(id=_id("ont-signal-event"), job_id=job.id, state="requested", reason_code=job.reason_code, receipt={"parents_sha256": _digest(parents)}, created_at=_now()))
    await session.flush()
    return await get_mapping_job(session, job.id)


async def get_mapping_job(session: AsyncSession, job_id: str) -> dict[str, Any]:
    job = await session.get(OntSignalMappingJob, job_id)
    if job is None:
        raise KeyError("mapping job not found")
    artifacts = (
        await session.execute(select(OntSignalMappingArtifact).where(OntSignalMappingArtifact.mapping_job_id == job.id).order_by(OntSignalMappingArtifact.kind))
    ).scalars()
    return {
        "mapping_job_id": job.id, "mode": job.mode, "run_id": job.run_id,
        "observed_generation": job.observed_generation, "raw_representation_id": job.raw_representation_id,
        "move_source_id": job.move_source_id, "mapping_profile_id": job.mapping_profile_id,
        "reference_revision_id": job.reference_revision_id, "alignment_job_id": job.alignment_job_id,
        "alignment_session_id": job.alignment_session_id, "parent_mapping_job_id": job.parent_mapping_job_id,
        "state": job.state, "reason_code": job.reason_code, "attempt": job.attempt,
        "resource_snapshot": job.resource_snapshot, "stage_receipts": job.stage_receipts,
        "failure_code": job.failure_code, "failure_message": job.failure_message,
        "artifacts": [_artifact_public(row) for row in artifacts],
        "created_at": _public_time(job.created_at), "updated_at": _public_time(job.updated_at),
        "completed_at": _public_time(job.completed_at),
    }


async def cancel_mapping_job(session: AsyncSession, job_id: str) -> dict[str, Any]:
    job = await session.get(OntSignalMappingJob, job_id)
    if job is None:
        raise KeyError("mapping job not found")
    if job.state in {"ready", "failed", "cancelled"}:
        return await get_mapping_job(session, job.id)
    job.cancel_requested_at = _now()
    job.reason_code = "cancellation_requested"
    job.updated_at = _now()
    await session.flush()
    return await get_mapping_job(session, job.id)


def normalize_render_params(raw: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "strand", "signal_units", "scale", "base_shift_source", "base_shift_value",
        "fixed_width", "base_width", "point_size", "base_limit", "signal_sample_limit",
        "pileup_read_limit", "loose_bound", "show_samples", "show_base_colours",
        "remove_signal_outliers", "managed_bed_artifact_id",
    }
    if set(raw) - allowed:
        raise OntSignalError("render parameters contain unsupported fields")
    result = {
        "strand": raw.get("strand", "forward"), "signal_units": raw.get("signal_units", "pA"),
        "scale": raw.get("scale", "none"), "base_shift_source": raw.get("base_shift_source", "profile"),
        "base_shift_value": int(raw.get("base_shift_value", 0)), "fixed_width": bool(raw.get("fixed_width", False)),
        "base_width": int(raw.get("base_width", 10)), "point_size": float(raw.get("point_size", 0.5)),
        "base_limit": int(raw.get("base_limit", 1000)), "signal_sample_limit": int(raw.get("signal_sample_limit", 100000)),
        "pileup_read_limit": int(raw.get("pileup_read_limit", 20)), "loose_bound": bool(raw.get("loose_bound", False)),
        "show_samples": bool(raw.get("show_samples", True)), "show_base_colours": bool(raw.get("show_base_colours", True)),
        "remove_signal_outliers": bool(raw.get("remove_signal_outliers", False)),
        "managed_bed_artifact_id": raw.get("managed_bed_artifact_id"),
    }
    if result["strand"] not in {"forward", "reverse"} or result["signal_units"] not in {"pA", "raw_adc"}:
        raise OntSignalError("render strand or signal units are invalid")
    if result["scale"] not in {"none", "medmad", "znorm", "scaledpA"}:
        raise OntSignalError("render scale is invalid")
    if result["base_shift_source"] not in {"profile", "explicit"}:
        raise OntSignalError("base-shift source is invalid")
    if not -64 <= result["base_shift_value"] <= 64 or not 1 <= result["base_width"] <= 100:
        raise OntSignalError("render geometry is outside bounded policy")
    if not 0.05 <= result["point_size"] <= 10 or not 1 <= result["base_limit"] <= MAX_BASE_LIMIT:
        raise OntSignalError("render point/base limit is outside bounded policy")
    if not 1 <= result["signal_sample_limit"] <= MAX_SAMPLE_LIMIT or not 1 <= result["pileup_read_limit"] <= MAX_PILEUP_READS:
        raise OntSignalError("render sample/read limit is outside bounded policy")
    if result["managed_bed_artifact_id"] is not None and not OPAQUE_ID.fullmatch(str(result["managed_bed_artifact_id"])):
        raise OntSignalError("managed BED artifact ID is invalid")
    return result


async def create_view_job(
    session: AsyncSession,
    *, mapping_artifact_id: str, mode: str, read_id: str | None,
    reference_contig: str | None, reference_start: int | None, reference_end: int | None,
    render_params: Mapping[str, Any],
) -> dict[str, Any]:
    if mode not in {"read", "reference", "pileup"}:
        raise OntSignalError("unsupported Squigualiser view mode")
    artifact = await session.get(OntSignalMappingArtifact, mapping_artifact_id)
    if artifact is None:
        raise OntSignalError("validated mapping artifact is unavailable")
    mapping = await session.get(OntSignalMappingJob, artifact.mapping_job_id)
    if mapping is None or mapping.state != "ready":
        raise OntSignalError("ready mapping authority is required")
    if mode == "read":
        if not read_id or not OPAQUE_ID.fullmatch(read_id):
            raise OntSignalError("an exact governed read ID is required")
        if any(value is not None for value in (reference_contig, reference_start, reference_end)):
            raise OntSignalError("read view cannot accept a reference region")
    else:
        if read_id is not None or not reference_contig or not CONTIG.fullmatch(reference_contig):
            raise OntSignalError("reference view requires one governed contig region")
        if reference_start is None or reference_end is None or reference_start < 1 or reference_end < reference_start or reference_end - reference_start + 1 > MAX_REGION_BP:
            raise OntSignalError("reference region is outside bounded policy")
        if mapping.mode != "signal_to_reference":
            raise OntSignalError("signal-to-reference mapping is required")
    normalized = normalize_render_params(render_params)
    identity = {
        "mapping_artifact_id": mapping_artifact_id, "mapping_sha256": artifact.sha256,
        "mode": mode, "read_id": read_id, "reference_contig": reference_contig,
        "reference_start": reference_start, "reference_end": reference_end, "render_params": normalized,
    }
    fingerprint = _digest(identity)
    existing = (await session.execute(select(OntSquigualiserViewJob).where(OntSquigualiserViewJob.request_fingerprint == fingerprint))).scalar_one_or_none()
    if existing is not None:
        return _view_public(existing)
    view = OntSquigualiserViewJob(
        id=_id("ont-squig-view"), mapping_artifact_id=mapping_artifact_id, mode=mode,
        read_id=read_id, reference_contig=reference_contig, reference_start=reference_start,
        reference_end=reference_end, render_params=normalized, request_fingerprint=fingerprint,
        state="requested", reason_code="squigualiser_view_requested", output_manifest={},
        render_receipt={"request_identity_sha256": fingerprint}, created_at=_now(), updated_at=_now(),
    )
    session.add(view)
    await session.flush()
    return _view_public(view)


def _view_public(row: OntSquigualiserViewJob) -> dict[str, Any]:
    output = row.output_manifest if isinstance(row.output_manifest, dict) else {}
    artifacts = []
    for item in output.get("artifacts", []):
        public = {key: value for key, value in item.items() if key != "managed_relative_path"}
        if row.state == "ready" and isinstance(item.get("artifact_id"), str):
            public["url"] = f"/api/ont/signal-workbench/views/{row.id}/artifacts/{item['artifact_id']}"
        artifacts.append(public)
    return {
        "view_job_id": row.id, "mapping_artifact_id": row.mapping_artifact_id, "mode": row.mode,
        "read_id": row.read_id, "reference_region": None if row.reference_contig is None else {
            "contig": row.reference_contig, "start": row.reference_start, "end": row.reference_end,
        },
        "render_params": row.render_params, "state": row.state, "reason_code": row.reason_code,
        "output_manifest": {**output, "artifacts": artifacts}, "render_receipt": row.render_receipt,
        "failure_code": row.failure_code, "failure_message": row.failure_message,
        "created_at": _public_time(row.created_at), "updated_at": _public_time(row.updated_at),
        "completed_at": _public_time(row.completed_at),
    }


async def get_view_job(session: AsyncSession, view_job_id: str) -> dict[str, Any]:
    row = await session.get(OntSquigualiserViewJob, view_job_id)
    if row is None:
        raise KeyError("view job not found")
    return _view_public(row)


async def cancel_view_job(session: AsyncSession, view_job_id: str) -> dict[str, Any]:
    row = await session.get(OntSquigualiserViewJob, view_job_id)
    if row is None:
        raise KeyError("view job not found")
    if row.state not in {"ready", "failed", "cancelled"}:
        row.cancel_requested_at = _now()
        row.reason_code = "cancellation_requested"
        row.updated_at = _now()
        await session.flush()
    return _view_public(row)


def _managed_output_path(relative: str) -> Path:
    root = (get_results_dir() / "ont_signal_workbench").resolve()
    candidate_relative = Path(relative)
    if candidate_relative.is_absolute() or not candidate_relative.parts or any(part in {"", ".", ".."} for part in candidate_relative.parts):
        raise OntSignalError("view artifact path is invalid")
    path = root.joinpath(candidate_relative).resolve()
    if root not in path.parents:
        raise OntSignalError("view artifact escapes governed root")
    return path


async def resolve_view_artifact(session: AsyncSession, view_job_id: str, artifact_id: str) -> tuple[Path, dict[str, Any]]:
    row = await session.get(OntSquigualiserViewJob, view_job_id)
    if row is None or row.state != "ready":
        raise KeyError("ready view not found")
    output = row.output_manifest if isinstance(row.output_manifest, dict) else {}
    for item in output.get("artifacts", []):
        if item.get("artifact_id") != artifact_id:
            continue
        path = _managed_output_path(str(item.get("managed_relative_path") or ""))
        digest, size = _stable_file_identity(path)
        if digest != item.get("sha256") or size != item.get("size_bytes"):
            raise OntSignalError("view artifact integrity changed")
        return path, item
    raise KeyError("view artifact not found")


async def workbench_capabilities(session: AsyncSession, *, run_id: str, observed_generation: int) -> dict[str, Any]:
    representations = list((await session.execute(select(OntRawSignalRepresentation).where(
        OntRawSignalRepresentation.run_id == run_id,
        OntRawSignalRepresentation.observed_generation == observed_generation,
    ))).scalars())
    blow5 = next((row for row in representations if row.format == "blow5" and row.state == "ready" and isinstance(row.validation_receipts, dict) and row.validation_receipts.get("adjacent_index") is True), None)
    source = None if blow5 is None else (await session.execute(select(OntMoveTableSource).where(
        OntMoveTableSource.raw_representation_id == blow5.id,
        OntMoveTableSource.validation_state == "ready",
    ).order_by(OntMoveTableSource.validated_at.desc()))).scalars().first()
    calibration_job = None if source is None else (await session.execute(select(OntSignalCalibrationJob).where(
        OntSignalCalibrationJob.raw_representation_id == source.raw_representation_id,
        OntSignalCalibrationJob.move_source_id == source.id,
    ).order_by(OntSignalCalibrationJob.created_at.desc(), OntSignalCalibrationJob.id.desc()))).scalars().first()
    calibration_artifact = None if calibration_job is None or calibration_job.calibration_artifact_id is None else await session.get(OntSignalCalibrationArtifact, calibration_job.calibration_artifact_id)
    approved_profile = None
    if source is not None:
        candidates = list((await session.execute(select(OntSignalMappingProfile).where(
            OntSignalMappingProfile.basecall_model_id == source.basecall_model_id,
            OntSignalMappingProfile.molecule_type == source.molecule_type,
            OntSignalMappingProfile.primary_alignment_policy == "primary_only",
            OntSignalMappingProfile.minimum_mapq == 0,
            OntSignalMappingProfile.include_supplementary.is_(False),
            OntSignalMappingProfile.read_set_selection == "immutable_full_set",
        ).order_by(OntSignalMappingProfile.approved_at.desc()))).scalars())
        approved_profile = next((profile for profile in candidates if profile.parameter_source == "exact_upstream_profile" or (calibration_artifact is not None and profile.calibration_artifact_id == calibration_artifact.id)), None)
    read_mapping = None
    if blow5 is not None and source is not None:
        read_mapping = (await session.execute(select(OntSignalMappingJob).where(
            OntSignalMappingJob.raw_representation_id == blow5.id,
            OntSignalMappingJob.move_source_id == source.id,
            OntSignalMappingJob.mode == "signal_to_read",
            OntSignalMappingJob.state == "ready",
        ).order_by(OntSignalMappingJob.completed_at.desc()))).scalars().first()
    reference_mapping = None if read_mapping is None else (await session.execute(select(OntSignalMappingJob).where(
        OntSignalMappingJob.parent_mapping_job_id == read_mapping.id,
        OntSignalMappingJob.mode == "signal_to_reference",
        OntSignalMappingJob.state == "ready",
    ).order_by(OntSignalMappingJob.completed_at.desc()))).scalars().first()
    if blow5 is None:
        read_mode = {"state": "unavailable", "reason_code": "indexed_blow5_authority_missing"}
    elif source is None:
        read_mode = {"state": "unavailable", "reason_code": "compatible_move_table_source_missing"}
    elif read_mapping is None:
        read_mode = {"state": "preparable", "reason_code": "validated_move_source_ready"}
    else:
        read_mode = {"state": "ready", "reason_code": "validated_reform_mapping_ready"}
    if read_mapping is None:
        reference_mode = {"state": "unavailable", "reason_code": "signal_to_read_mapping_missing"}
    elif reference_mapping is None:
        reference_mode = {"state": "preparable", "reason_code": "governed_reference_alignment_required"}
    else:
        reference_mode = {"state": "ready", "reason_code": "validated_realign_mapping_ready"}
    return {
        "run_id": run_id, "observed_generation": observed_generation,
        "resolved": {
            "raw_representation_id": blow5.id if blow5 else None,
            "move_source_id": source.id if source else None,
            "mapping_profile_id": read_mapping.mapping_profile_id if read_mapping else (approved_profile.id if approved_profile else None),
            "calibration_job_id": calibration_job.id if calibration_job else None,
            "calibration_artifact_id": calibration_artifact.id if calibration_artifact else None,
            "signal_to_read_mapping_job_id": read_mapping.id if read_mapping else None,
            "signal_to_reference_mapping_job_id": reference_mapping.id if reference_mapping else None,
        },
        "modes": {
            "igv": {"state": "independent", "reason_code": "alignment_session_scoped"},
            "raw_waveform": {"state": "ready" if blow5 else "unavailable", "reason_code": "indexed_blow5_ready" if blow5 else "indexed_blow5_authority_missing"},
            "signal_to_read": read_mode,
            "signal_to_reference": reference_mode,
            "signal_pileup": {"state": "ready" if reference_mapping else "unavailable", "reason_code": "bounded_rendering_ready" if reference_mapping else "signal_to_reference_mapping_missing"},
        },
    }


async def create_viewer_session(
    session: AsyncSession,
    *, dataset_id: str, run_id: str, observed_generation: int,
    alignment_job_id: str | None, alignment_session_id: str | None,
    reference_revision_id: str | None, contig: str | None,
    locus_start: int | None, locus_end: int | None, selected_read_id: str | None,
) -> dict[str, Any]:
    event = (await session.execute(select(OntInstrumentRunEvent).where(
        OntInstrumentRunEvent.run_id == run_id,
        OntInstrumentRunEvent.observed_generation == observed_generation,
    ))).scalar_one_or_none()
    if event is None:
        raise KeyError("run generation not found")
    capabilities = await workbench_capabilities(session, run_id=run_id, observed_generation=observed_generation)
    resolved = capabilities["resolved"]
    viewer = OntSignalViewerSession(
        id=_id("ont-viewer"), dataset_id=dataset_id, run_id=run_id, observed_generation=observed_generation,
        alignment_job_id=alignment_job_id, alignment_session_id=alignment_session_id,
        reference_revision_id=reference_revision_id, raw_representation_id=resolved["raw_representation_id"],
        move_source_id=resolved["move_source_id"], mapping_profile_id=resolved["mapping_profile_id"],
        contig=contig, locus_start=locus_start, locus_end=locus_end, selected_read_id=selected_read_id,
        igv_state={"alignment_job_id": alignment_job_id, "alignment_session_id": alignment_session_id},
        signal_state={"capabilities": capabilities["modes"]}, revision=1, created_at=_now(), updated_at=_now(),
    )
    session.add(viewer)
    await session.flush()
    return _viewer_public(viewer)


def _viewer_public(row: OntSignalViewerSession) -> dict[str, Any]:
    return {
        "viewer_session_id": row.id, "dataset_id": row.dataset_id, "run_id": row.run_id,
        "observed_generation": row.observed_generation, "alignment_job_id": row.alignment_job_id,
        "alignment_session_id": row.alignment_session_id, "reference_revision_id": row.reference_revision_id,
        "raw_representation_id": row.raw_representation_id, "move_source_id": row.move_source_id,
        "mapping_profile_id": row.mapping_profile_id, "contig": row.contig,
        "locus_start": row.locus_start, "locus_end": row.locus_end,
        "selected_read_id": row.selected_read_id, "igv_state": row.igv_state,
        "signal_state": row.signal_state, "revision": row.revision,
        "created_at": _public_time(row.created_at), "updated_at": _public_time(row.updated_at),
        "reopen_url": f"/ngs?view=workbench&viewer_session_id={row.id}",
    }


async def get_viewer_session(session: AsyncSession, viewer_session_id: str) -> dict[str, Any]:
    row = await session.get(OntSignalViewerSession, viewer_session_id)
    if row is None:
        raise KeyError("viewer session not found")
    return _viewer_public(row)


async def update_viewer_session(
    session: AsyncSession, viewer_session_id: str, *, expected_revision: int,
    contig: str | None, locus_start: int | None, locus_end: int | None,
    selected_read_id: str | None, igv_state: Mapping[str, Any], signal_state: Mapping[str, Any],
) -> dict[str, Any]:
    row = await session.get(OntSignalViewerSession, viewer_session_id)
    if row is None:
        raise KeyError("viewer session not found")
    if row.revision != expected_revision:
        raise OntSignalError("viewer session changed concurrently")
    row.contig, row.locus_start, row.locus_end = contig, locus_start, locus_end
    row.selected_read_id = selected_read_id
    row.igv_state, row.signal_state = dict(igv_state), dict(signal_state)
    row.revision += 1
    row.updated_at = _now()
    await session.flush()
    return _viewer_public(row)
