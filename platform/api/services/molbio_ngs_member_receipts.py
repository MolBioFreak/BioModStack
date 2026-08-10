"""Server-owned external-member receipts for MolBio/NGS state lineage."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    ApprovedNgsComparisonPanel,
    Job,
    OntInstrumentRun,
    OntInstrumentRunEvent,
)
from molbio_models import (
    MolecularDocument,
    MolecularOperation,
    MolecularOperationInput,
    MolecularOperationOutput,
    MolecularRevision,
    PCRExperiment,
    PCRExperimentRevision,
    Primer,
    PrimerRevision,
)
from molbio_ngs_models import MolBioNGSMemberReceipt
from services.job_result_roots import resolve_persisted_job_result_root
from services.molbio_ngs_receipts import _snapshot_sequence
from services.ngs_comparison_panels import _validated_panel_manifest
from services.nucleotide_validation import canonicalize_nucleotide_sequence
from services.sequence_qc_manifest import (
    VERIFICATION_SCHEMA,
    find_manifest_in_result_root,
    load_sequence_qc_manifest,
)

RECEIPT_SCHEMA = "bms.molbio-ngs.external-member-receipt.v1"
RECEIPT_SCHEMA_NAME = "bms.molbio-ngs.external-member-receipt"
RECEIPT_SCHEMA_VERSION = "1"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CANONICAL_RECEIPT_KEYS = {
    "schema",
    "receipt_id",
    "source_store_id",
    "entity_kind",
    "entity_id",
    "source_generation_or_revision",
    "content_digest",
    "source_schema",
    "availability",
    "reopen_destination",
    "created_at",
}


@dataclass(frozen=True)
class ExternalMemberReceipt:
    receipt_id: str
    source_store_id: str
    entity_kind: str
    entity_id: str
    source_generation_or_revision: str
    content_digest: str
    source_schema: str
    availability: str
    reopen_destination: dict[str, Any]
    canonical_receipt: str
    receipt_sha256: str
    created_at: str


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        _json_value(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_payload(payload: Mapping[str, Any]) -> str:
    return _sha256_bytes(_canonical(payload).encode("utf-8"))


def _reopen(surface: str, **params: str | int) -> dict[str, Any]:
    if not surface or not params or any(not key or value is None for key, value in params.items()):
        raise ValueError("member receipt reopen destination must be a typed relative identity")
    return {"surface": surface, "params": params}


def build_external_member_receipt(
    *,
    source_store_id: str,
    entity_kind: str,
    entity_id: str,
    source_generation_or_revision: str | int,
    content_digest: str,
    source_schema: str,
    availability: str,
    reopen_destination: dict[str, Any],
    receipt_id: str | None = None,
    created_at: str | None = None,
) -> ExternalMemberReceipt:
    """Build the one canonical external-member receipt representation."""

    identity = str(entity_id).strip()
    generation = str(source_generation_or_revision).strip()
    if source_store_id not in {"molbio", "core-ngs", "molbio-ngs-domain"}:
        raise ValueError("unsupported member receipt source store")
    if entity_kind not in {
        "molecular_revision",
        "primer_revision",
        "pcr_experiment_revision",
        "molecular_operation",
        "ont_instrument_run",
        "ngs_job",
        "ngs_result_manifest",
        "ngs_comparison_panel",
        "ngs_reference_revision",
        "ngs_evidence_assessment",
    }:
        raise ValueError("unsupported member receipt entity kind")
    if not identity or not generation or not isinstance(source_schema, str) or not source_schema.strip():
        raise ValueError("member receipt source identity is incomplete")
    if not isinstance(content_digest, str) or not _DIGEST.fullmatch(content_digest):
        raise ValueError("member receipt content digest must be lowercase SHA-256")
    if availability not in {"available", "unavailable", "unknown"}:
        raise ValueError("member receipt availability is invalid")
    if set(reopen_destination) != {"surface", "params"} or not isinstance(
        reopen_destination.get("surface"), str
    ) or not isinstance(reopen_destination.get("params"), dict):
        raise ValueError("member receipt reopen destination must be typed and relative")

    issued_id = receipt_id or str(uuid.uuid4())
    issued_at = created_at or datetime.now(timezone.utc).isoformat()
    payload = {
        "schema": RECEIPT_SCHEMA,
        "receipt_id": issued_id,
        "source_store_id": source_store_id,
        "entity_kind": entity_kind,
        "entity_id": identity,
        "source_generation_or_revision": generation,
        "content_digest": content_digest,
        "source_schema": source_schema.strip(),
        "availability": availability,
        "reopen_destination": reopen_destination,
        "created_at": issued_at,
    }
    canonical = _canonical(payload)
    return ExternalMemberReceipt(
        receipt_id=issued_id,
        source_store_id=source_store_id,
        entity_kind=entity_kind,
        entity_id=identity,
        source_generation_or_revision=generation,
        content_digest=content_digest,
        source_schema=source_schema.strip(),
        availability=availability,
        reopen_destination=reopen_destination,
        canonical_receipt=canonical,
        receipt_sha256=_sha256_bytes(canonical.encode("utf-8")),
        created_at=issued_at,
    )


def parse_canonical_member_receipt(canonical_receipt: str) -> dict[str, Any]:
    """Strictly parse the sole authority body for a persisted member receipt."""

    if not isinstance(canonical_receipt, str):
        raise ValueError("member receipt canonical body must be JSON text")
    try:
        parsed = json.loads(canonical_receipt)
    except json.JSONDecodeError as exc:
        raise ValueError("member receipt canonical body is invalid JSON") from exc
    if not isinstance(parsed, dict) or set(parsed) != _CANONICAL_RECEIPT_KEYS:
        raise ValueError("member receipt canonical body has an invalid key shape")
    if _canonical(parsed) != canonical_receipt:
        raise ValueError("member receipt canonical body is not canonical JSON")
    if parsed["schema"] != RECEIPT_SCHEMA:
        raise ValueError("member receipt canonical schema is unsupported")
    if parsed["source_store_id"] not in {
        "molbio",
        "core-ngs",
        "molbio-ngs-domain",
    }:
        raise ValueError("member receipt canonical source store is unsupported")
    if parsed["entity_kind"] not in {
        "molecular_revision",
        "primer_revision",
        "pcr_experiment_revision",
        "molecular_operation",
        "ont_instrument_run",
        "ngs_job",
        "ngs_result_manifest",
        "ngs_comparison_panel",
        "ngs_reference_revision",
        "ngs_evidence_assessment",
    }:
        raise ValueError("member receipt canonical entity kind is unsupported")
    for field_name in (
        "receipt_id",
        "entity_id",
        "source_generation_or_revision",
        "source_schema",
        "created_at",
    ):
        value = parsed[field_name]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"member receipt canonical {field_name} is invalid")
    if not isinstance(parsed["content_digest"], str) or not _DIGEST.fullmatch(
        parsed["content_digest"]
    ):
        raise ValueError("member receipt canonical content digest is invalid")
    if parsed["availability"] not in {"available", "unavailable", "unknown"}:
        raise ValueError("member receipt canonical availability is invalid")
    reopen = parsed["reopen_destination"]
    if (
        not isinstance(reopen, dict)
        or set(reopen) != {"surface", "params"}
        or not isinstance(reopen["surface"], str)
        or not reopen["surface"].strip()
        or not isinstance(reopen["params"], dict)
        or not reopen["params"]
    ):
        raise ValueError("member receipt canonical reopen destination is invalid")
    return parsed


async def persist_member_receipt(
    session: AsyncSession,
    receipt: ExternalMemberReceipt,
) -> MolBioNGSMemberReceipt:
    """Persist a canonical receipt before any API response can expose its ID."""

    canonical_sha256 = _sha256_bytes(receipt.canonical_receipt.encode("utf-8"))
    if canonical_sha256 != receipt.receipt_sha256:
        raise ValueError("member receipt canonical digest mismatch")
    parsed = parse_canonical_member_receipt(receipt.canonical_receipt)
    row = MolBioNGSMemberReceipt(
        receipt_id=parsed["receipt_id"],
        source_store_id=parsed["source_store_id"],
        entity_kind=parsed["entity_kind"],
        entity_id=parsed["entity_id"],
        source_generation_or_revision=parsed["source_generation_or_revision"],
        content_digest=parsed["content_digest"],
        schema_name=RECEIPT_SCHEMA_NAME,
        schema_version=RECEIPT_SCHEMA_VERSION,
        availability=parsed["availability"],
        reopen_destination=_canonical(parsed["reopen_destination"]),
        canonical_receipt=receipt.canonical_receipt,
        receipt_sha256=canonical_sha256,
        created_at=parsed["created_at"],
    )
    session.add(row)
    await session.flush([row])
    return row


async def resolve_molecular_revision_receipt(
    session: AsyncSession,
    *,
    sequence_id: str,
    revision_id: str,
) -> ExternalMemberReceipt:
    document = await session.get(MolecularDocument, sequence_id)
    revision = await session.get(MolecularRevision, revision_id)
    if document is None or revision is None or revision.document_id != document.id:
        raise KeyError("molecular revision identity was not found")
    _snapshot_sequence(revision)
    return build_external_member_receipt(
        source_store_id="molbio",
        entity_kind="molecular_revision",
        entity_id=revision.id,
        source_generation_or_revision=revision.revision_number,
        content_digest=revision.content_sha256,
        source_schema="bms.molbio.molecular-revision.v1",
        availability="available",
        reopen_destination=_reopen(
            "molbio-sequence-revision",
            sequence_id=document.id,
            revision_id=revision.id,
        ),
    )


async def resolve_primer_revision_receipt(
    session: AsyncSession,
    *,
    primer_id: str,
    revision_id: str,
) -> ExternalMemberReceipt:
    primer = await session.get(Primer, primer_id)
    revision = await session.get(PrimerRevision, revision_id)
    if primer is None or revision is None or revision.primer_id != primer.id:
        raise KeyError("primer revision identity was not found")
    sequence = canonicalize_nucleotide_sequence(
        str((revision.snapshot or {}).get("sequence") or ""),
        sequence_type=str((revision.snapshot or {}).get("sequence_type") or "dna"),
    )
    if _sha256_bytes(sequence.encode("utf-8")) != revision.sequence_sha256:
        raise ValueError("primer revision digest does not match its immutable sequence")
    return build_external_member_receipt(
        source_store_id="molbio",
        entity_kind="primer_revision",
        entity_id=revision.id,
        source_generation_or_revision=revision.revision_number,
        content_digest=revision.sequence_sha256,
        source_schema="bms.molbio.primer-revision.v1",
        availability="available",
        reopen_destination=_reopen(
            "molbio-primer-revision",
            primer_id=primer.id,
            revision_id=revision.id,
        ),
    )


def _row_payload(row: Any, *, excluded: frozenset[str] = frozenset()) -> dict[str, Any]:
    return {
        column.name: _json_value(getattr(row, column.name))
        for column in row.__table__.columns
        if column.name not in excluded
    }


def pcr_experiment_revision_payload_sha256(revision: PCRExperimentRevision) -> str:
    """Return the canonical digest used by PCR revision member receipts."""

    return _sha256_payload(_row_payload(revision))


async def resolve_pcr_experiment_revision_receipt(
    session: AsyncSession,
    *,
    experiment_id: str,
    revision_id: str,
) -> ExternalMemberReceipt:
    experiment = await session.get(PCRExperiment, experiment_id)
    revision = await session.get(PCRExperimentRevision, revision_id)
    if experiment is None or revision is None or revision.experiment_id != experiment.id:
        raise KeyError("PCR experiment revision identity was not found")
    digest = pcr_experiment_revision_payload_sha256(revision)
    return build_external_member_receipt(
        source_store_id="molbio",
        entity_kind="pcr_experiment_revision",
        entity_id=revision.id,
        source_generation_or_revision=revision.revision_number,
        content_digest=digest,
        source_schema="bms.molbio.pcr-experiment-revision.v1",
        availability="available",
        reopen_destination=_reopen(
            "molbio-pcr-experiment-revision",
            experiment_id=experiment.id,
            revision_id=revision.id,
        ),
    )


async def resolve_molecular_operation_receipt(
    session: AsyncSession,
    *,
    operation_id: str,
) -> ExternalMemberReceipt:
    operation = await session.get(MolecularOperation, operation_id)
    if operation is None:
        raise KeyError("molecular operation identity was not found")
    inputs = (
        await session.execute(
            select(MolecularOperationInput)
            .where(MolecularOperationInput.operation_id == operation.id)
            .order_by(MolecularOperationInput.position, MolecularOperationInput.id)
        )
    ).scalars().all()
    outputs = (
        await session.execute(
            select(MolecularOperationOutput)
            .where(MolecularOperationOutput.operation_id == operation.id)
            .order_by(MolecularOperationOutput.position, MolecularOperationOutput.id)
        )
    ).scalars().all()
    payload = {
        "operation": _row_payload(operation),
        "inputs": [_row_payload(row) for row in inputs],
        "outputs": [_row_payload(row) for row in outputs],
    }
    return build_external_member_receipt(
        source_store_id="molbio",
        entity_kind="molecular_operation",
        entity_id=operation.id,
        source_generation_or_revision="event",
        content_digest=_sha256_payload(payload),
        source_schema="bms.molbio.molecular-operation.v1",
        availability="available",
        reopen_destination=_reopen("molbio-operation", operation_id=operation.id),
    )


async def resolve_ont_instrument_run_receipt(
    session: AsyncSession,
    *,
    run_id: str,
    observed_generation: int,
) -> ExternalMemberReceipt:
    run = await session.get(OntInstrumentRun, run_id)
    event = (
        await session.execute(
            select(OntInstrumentRunEvent).where(
                OntInstrumentRunEvent.run_id == run_id,
                OntInstrumentRunEvent.observed_generation == observed_generation,
            )
        )
    ).scalar_one_or_none()
    if run is None or event is None:
        raise KeyError("ONT run observation identity was not found")
    payload = {
        "run_id": run.id,
        "position_id": run.position_id,
        "observed_generation": event.observed_generation,
        "state": event.state,
        "observed_at": _json_value(event.observed_at),
        "event_type": event.event_type,
        "minknow_payload": event.minknow_payload,
        "output_files": event.output_files,
        "terminal_artifact_manifest_sha256": (
            run.terminal_artifact_manifest_sha256
            if run.observed_generation == event.observed_generation
            else None
        ),
    }
    return build_external_member_receipt(
        source_store_id="core-ngs",
        entity_kind="ont_instrument_run",
        entity_id=run.id,
        source_generation_or_revision=event.observed_generation,
        content_digest=_sha256_payload(payload),
        source_schema="bms.ont.instrument-run-observation.v1",
        availability="available",
        reopen_destination=_reopen(
            "ont-instrument-run",
            run_id=run.id,
            observed_generation=event.observed_generation,
        ),
    )


async def resolve_ngs_job_receipt(
    session: AsyncSession,
    *,
    job_id: str,
) -> ExternalMemberReceipt:
    job = await session.get(Job, job_id)
    if job is None or job.model_id != "nanopore":
        raise KeyError("core NGS job identity was not found")
    launch = {
        "id": job.id,
        "name": job.name,
        "model_id": job.model_id,
        "mode": job.mode,
        "params": job.params,
        "created_at": _json_value(job.created_at),
        "output_dir": job.output_dir,
        "child_output_dir": job.child_output_dir,
        "parent_job_id": job.parent_job_id,
        "source_stage_job_id": job.source_stage_job_id,
        "source_instrument_run_id": (job.params or {}).get("source_instrument_run_id"),
    }
    return build_external_member_receipt(
        source_store_id="core-ngs",
        entity_kind="ngs_job",
        entity_id=job.id,
        source_generation_or_revision="launch",
        content_digest=_sha256_payload(launch),
        source_schema="bms.core.ngs-job-launch.v1",
        availability="available",
        reopen_destination=_reopen("ngs-job", job_id=job.id),
    )


async def resolve_ngs_result_manifest_receipt(
    session: AsyncSession,
    *,
    job_id: str,
) -> ExternalMemberReceipt:
    job = await session.get(Job, job_id)
    if job is None or job.model_id != "nanopore":
        raise KeyError("core NGS job identity was not found")
    result_root = resolve_persisted_job_result_root(job)
    manifest_path = find_manifest_in_result_root(result_root)
    raw = manifest_path.read_bytes()
    manifest = load_sequence_qc_manifest(manifest_path, raw_bytes=raw)
    source_schema = manifest.get("schema")
    if source_schema == VERIFICATION_SCHEMA:
        if "MALFORMED_VERIFICATION_MANIFEST" in manifest.get("reason_codes", []):
            raise ValueError("NGS result verification manifest is malformed")
    elif source_schema is None and manifest.get("artifact_schema_version") in {1, 2}:
        required = {"job_id", "workflow_status", "verification_status", "artifacts"}
        if not required.issubset(manifest):
            raise ValueError("NGS result sequence-QC manifest is malformed")
        source_schema = (
            f"bms.sequence-qc.manifest.v{manifest['artifact_schema_version']}"
        )
    else:
        raise ValueError("NGS result manifest schema is unsupported")
    return build_external_member_receipt(
        source_store_id="core-ngs",
        entity_kind="ngs_result_manifest",
        entity_id=f"{job.id}:sequence-qc-manifest",
        source_generation_or_revision="result-manifest",
        content_digest=_sha256_bytes(raw),
        source_schema=source_schema,
        availability="available",
        reopen_destination=_reopen("ngs-job-evidence", job_id=job.id),
    )


async def resolve_approved_comparison_panel_receipt(
    session: AsyncSession,
    *,
    panel_id: str,
    panel_version: int,
) -> ExternalMemberReceipt:
    panel = await session.get(ApprovedNgsComparisonPanel, panel_id)
    if panel is None or panel.version != panel_version:
        raise KeyError("approved NGS comparison panel identity was not found")
    manifest = _validated_panel_manifest(panel)
    source_schema = str(manifest.get("schema") or "")
    if not source_schema:
        raise ValueError("approved comparison panel does not declare a schema")
    return build_external_member_receipt(
        source_store_id="core-ngs",
        entity_kind="ngs_comparison_panel",
        entity_id=panel.id,
        source_generation_or_revision=panel.version,
        content_digest=panel.snapshot_sha256,
        source_schema=source_schema,
        availability="available",
        reopen_destination=_reopen(
            "ngs-comparison-panel",
            panel_id=panel.id,
            panel_version=panel.version,
        ),
    )


def serialize_external_member_receipt(row: MolBioNGSMemberReceipt) -> dict[str, Any]:
    """Return only the canonical persisted receipt representation."""

    if _sha256_bytes(row.canonical_receipt.encode("utf-8")) != row.receipt_sha256:
        raise ValueError("persisted member receipt digest mismatch")
    return parse_canonical_member_receipt(row.canonical_receipt)


__all__ = [
    "ExternalMemberReceipt",
    "RECEIPT_SCHEMA",
    "build_external_member_receipt",
    "parse_canonical_member_receipt",
    "persist_member_receipt",
    "resolve_approved_comparison_panel_receipt",
    "resolve_molecular_operation_receipt",
    "resolve_molecular_revision_receipt",
    "resolve_ngs_job_receipt",
    "resolve_ngs_result_manifest_receipt",
    "resolve_ont_instrument_run_receipt",
    "resolve_pcr_experiment_revision_receipt",
    "resolve_primer_revision_receipt",
    "serialize_external_member_receipt",
]
