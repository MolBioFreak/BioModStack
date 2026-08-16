"""Transactional scientific-history helpers for the owned Mol Bio store."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

import Bio
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from services.molbio_authority import SERVER_OWNED_ACTOR
from molbio_models import (
    MolecularDocument,
    MolecularOperation,
    MolecularOperationInput,
    MolecularOperationOutput,
    MolecularRevision,
    MolBioAuditEvent,
    MolBioOutboxEvent,
    NucleotideSequence,
    PCRExperiment,
    PCRExperimentRevision,
    PolymerasePreset,
    PolymerasePresetRevision,
    Primer,
    PrimerRevision,
    TmModel,
    TmModelRevision,
)


_UUID_NAMESPACE = uuid.UUID("68ce16ee-8da9-4dc1-9f3a-3a7b8845dd1d")


@dataclass(frozen=True)
class PCRPersistenceResult:
    experiment_id: str
    experiment_revision_id: str
    operation_id: str
    product_document_id: str | None
    product_revision_id: str | None
    request_fingerprint: str | None = None
    product_snapshot: dict[str, Any] | None = None
    product_sequence_snapshot: dict[str, Any] | None = None
    reused: bool = False


class IdempotencyConflictError(ValueError):
    """An idempotency key was already bound to a different canonical request."""


async def begin_immediate_molbio_write(session: AsyncSession) -> None:
    """Acquire SQLite's writer reservation before any mutation reads occur.

    A caller may already hold an implicit read-only transaction from diagnostics
    or object lookup. End that stale snapshot first, but never discard pending
    ORM writes. The subsequent ``BEGIN IMMEDIATE`` serializes revision-number
    allocation and projection mutation on this connection.
    """

    if session.in_transaction():
        if session.new or session.dirty or session.deleted:
            raise RuntimeError(
                "Mol Bio write serialization cannot discard pending session writes"
            )
        await session.commit()
    await session.execute(text("BEGIN IMMEDIATE"))


def canonical_request_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(_json_value(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def tm_model_revision_identity(tm_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return the deterministic identity used by the immutable Tm model revision."""

    settings = _json_value(tm_snapshot.get("settings") or {})
    algorithm = str(settings.get("algorithm") or "unknown")
    source = {
        "package": "biopython",
        "package_version": Bio.__version__,
        "module": "Bio.SeqUtils.MeltingTemp",
        "algorithm": algorithm,
        "algorithm_definition": _json_value(tm_snapshot.get("algorithm_definition") or {}),
        "salt_correction": settings.get("salt_correction"),
        "salt_correction_definition": _json_value(
            tm_snapshot.get("salt_correction_definition") or {}
        ),
        "settings": settings,
    }
    source_json = json.dumps(source, sort_keys=True, separators=(",", ":"))
    source_hash = hashlib.sha256(source_json.encode("utf-8")).hexdigest()
    return {
        "model_id": str(uuid.uuid5(_UUID_NAMESPACE, f"biopython-tm:{algorithm}")),
        "revision_id": str(uuid.uuid5(_UUID_NAMESPACE, f"biopython-tm:{source_hash}")),
        "revision_number": int(source_hash[:7], 16) + 1,
        "implementation": "Bio.SeqUtils.MeltingTemp",
        "implementation_version": f"biopython-{Bio.__version__}:{source_hash}",
        "parameter_schema": {"settings": sorted(settings)},
        "source": source,
    }


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def sequence_snapshot(sequence: NucleotideSequence) -> dict[str, Any]:
    return {
        field: _json_value(getattr(sequence, field))
        for field in (
            "id",
            "name",
            "description",
            "sequence",
            "sequence_type",
            "molecule_strandedness",
            "molecule_orientation",
            "is_circular",
            "length",
            "features",
            "primers",
            "analysis_tracks",
            "organism",
            "accession",
            "source_file",
            "parent_id",
            "operation",
            "operation_params",
            "version",
            "gc_content",
            "created_at",
            "updated_at",
        )
    }


def primer_snapshot(primer: Primer) -> dict[str, Any]:
    return {
        field: _json_value(getattr(primer, field))
        for field in (
            "id",
            "name",
            "sequence",
            "sequence_type",
            "length",
            "tm",
            "gc_percent",
            "tm_algorithm",
            "tm_salt_correction",
            "tm_settings",
            "primer_type",
            "description",
            "target_sequence_id",
            "binding_start",
            "binding_end",
            "binding_strand",
            "tags",
            "is_favorite",
            "deleted_at",
            "created_at",
            "updated_at",
        )
    }


async def _emit_history_events(
    session: AsyncSession,
    *,
    entity_kind: str,
    entity_id: str,
    event_kind: str,
    payload: dict[str, Any],
    actor: str | None,
) -> None:
    event_id = str(uuid.uuid4())
    session.add_all(
        [
            MolBioAuditEvent(
                id=event_id,
                entity_kind=entity_kind,
                entity_id=entity_id,
                event_kind=event_kind,
                payload=payload,
                actor=SERVER_OWNED_ACTOR,
            ),
            MolBioOutboxEvent(
                id=str(uuid.uuid4()),
                aggregate_kind=entity_kind,
                aggregate_id=entity_id,
                event_kind=event_kind,
                payload={"audit_event_id": event_id, **payload},
            ),
        ]
    )


async def current_molecular_revision(
    session: AsyncSession, document_id: str
) -> MolecularRevision | None:
    document = await session.get(MolecularDocument, document_id)
    if document is None or document.current_revision_id is None:
        return None
    return await session.get(MolecularRevision, document.current_revision_id)


async def create_operation(
    session: AsyncSession,
    *,
    operation_kind: str,
    implementation: str,
    parameters: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    provenance: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    request_fingerprint: str | None = None,
    created_by: str | None = None,
) -> MolecularOperation:
    operation = MolecularOperation(
        id=str(uuid.uuid4()),
        operation_kind=operation_kind,
        implementation=implementation,
        implementation_version="biomodstack-api-v1",
        status="completed",
        parameters=_json_value(parameters or {}),
        warnings=warnings or [],
        provenance=_json_value(provenance or {}),
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        created_by=SERVER_OWNED_ACTOR,
    )
    session.add(operation)
    await session.flush()
    return operation


async def record_sequence_revision(
    session: AsyncSession,
    sequence: NucleotideSequence,
    *,
    change_kind: str,
    provenance: dict[str, Any] | None = None,
    operation_id: str | None = None,
    created_by: str | None = None,
) -> MolecularRevision:
    """Append a complete sequence snapshot and atomically advance its head."""

    await session.flush()
    document = await session.get(MolecularDocument, sequence.id)
    if document is None:
        document = MolecularDocument(
            id=sequence.id,
            document_kind=sequence.sequence_type or "dna",
            name=sequence.name,
            created_at=sequence.created_at or datetime.utcnow(),
        )
        session.add(document)
        await session.flush()
    else:
        document.name = sequence.name
        document.document_kind = sequence.sequence_type or document.document_kind
        document.deleted_at = None

    max_revision = (
        await session.execute(
            select(func.max(MolecularRevision.revision_number)).where(
                MolecularRevision.document_id == sequence.id
            )
        )
    ).scalar_one()
    revision = MolecularRevision(
        id=str(uuid.uuid4()),
        document_id=sequence.id,
        revision_number=int(max_revision or 0) + 1,
        change_kind=change_kind,
        content_sha256=sha256_text(sequence.sequence),
        content_length=len(sequence.sequence),
        snapshot=sequence_snapshot(sequence),
        provenance=_json_value(provenance or {}),
        operation_id=operation_id,
        created_by=SERVER_OWNED_ACTOR,
    )
    session.add(revision)
    await session.flush()
    document.current_revision_id = revision.id
    await _emit_history_events(
        session,
        entity_kind="molecular_document",
        entity_id=sequence.id,
        event_kind=f"sequence.{change_kind}",
        payload={
            "revision_id": revision.id,
            "revision_number": revision.revision_number,
            "content_sha256": revision.content_sha256,
            "operation_id": operation_id,
        },
        actor=SERVER_OWNED_ACTOR,
    )
    return revision


async def record_sequence_deletion(
    session: AsyncSession,
    sequence: NucleotideSequence,
    *,
    provenance: dict[str, Any] | None = None,
    created_by: str | None = None,
) -> MolecularRevision:
    revision = await record_sequence_revision(
        session,
        sequence,
        change_kind="delete",
        provenance=provenance,
        created_by=SERVER_OWNED_ACTOR,
    )
    document = await session.get(MolecularDocument, sequence.id)
    if document is not None:
        document.deleted_at = datetime.utcnow()
    return revision


async def record_primer_revision(
    session: AsyncSession,
    primer: Primer,
    *,
    change_kind: str,
    provenance: dict[str, Any] | None = None,
    created_by: str | None = None,
) -> PrimerRevision:
    await session.flush()
    max_revision = (
        await session.execute(
            select(func.max(PrimerRevision.revision_number)).where(PrimerRevision.primer_id == primer.id)
        )
    ).scalar_one()
    revision = PrimerRevision(
        id=str(uuid.uuid4()),
        primer_id=primer.id,
        revision_number=int(max_revision or 0) + 1,
        change_kind=change_kind,
        sequence_sha256=sha256_text(primer.sequence),
        snapshot=primer_snapshot(primer),
        provenance=_json_value(provenance or {}),
        created_by=SERVER_OWNED_ACTOR,
    )
    session.add(revision)
    await _emit_history_events(
        session,
        entity_kind="primer",
        entity_id=primer.id,
        event_kind=f"primer.{change_kind}",
        payload={
            "revision_id": revision.id,
            "revision_number": revision.revision_number,
            "sequence_sha256": revision.sequence_sha256,
        },
        actor=SERVER_OWNED_ACTOR,
    )
    return revision


async def add_operation_edges(
    session: AsyncSession,
    operation: MolecularOperation,
    *,
    input_revisions: Iterable[tuple[MolecularRevision, str, dict[str, Any] | None]] = (),
    output_revisions: Iterable[tuple[MolecularRevision, str, dict[str, Any] | None]] = (),
) -> None:
    for position, (revision, role, snapshot) in enumerate(input_revisions):
        session.add(
            MolecularOperationInput(
                id=str(uuid.uuid4()),
                operation_id=operation.id,
                revision_id=revision.id,
                role=role,
                position=position,
                snapshot=_json_value(snapshot or {}),
            )
        )
    for position, (revision, role, snapshot) in enumerate(output_revisions):
        session.add(
            MolecularOperationOutput(
                id=str(uuid.uuid4()),
                operation_id=operation.id,
                revision_id=revision.id,
                role=role,
                position=position,
                snapshot=_json_value(snapshot or {}),
            )
        )


async def _record_inline_sequence_input(
    session: AsyncSession,
    sequence: NucleotideSequence,
    *,
    operation: MolecularOperation,
    provenance: dict[str, Any] | None,
    created_by: str | None,
) -> MolecularRevision:
    """Capture a request-only sequence as immutable private operation input history."""
    document = MolecularDocument(
        id=sequence.id,
        document_kind="inline_sequence_input",
        name=sequence.name,
        current_revision_id=None,
    )
    session.add(document)
    await session.flush()
    revision = MolecularRevision(
        id=str(uuid.uuid4()),
        document_id=document.id,
        revision_number=1,
        change_kind="operation_input",
        content_sha256=sha256_text(sequence.sequence),
        content_length=len(sequence.sequence),
        snapshot=sequence_snapshot(sequence),
        provenance=_json_value(
            {
                **(provenance or {}),
                "input_kind": "inline_sequence",
                "projection_persisted": False,
            }
        ),
        operation_id=operation.id,
        created_by=SERVER_OWNED_ACTOR,
    )
    session.add(revision)
    await session.flush()
    document.current_revision_id = revision.id
    await _emit_history_events(
        session,
        entity_kind="molecular_document",
        entity_id=document.id,
        event_kind="sequence.inline_input_captured",
        payload={
            "revision_id": revision.id,
            "content_sha256": revision.content_sha256,
            "operation_id": operation.id,
        },
        actor=SERVER_OWNED_ACTOR,
    )
    return revision


async def record_generated_sequence(
    session: AsyncSession,
    sequence: NucleotideSequence,
    *,
    parent: NucleotideSequence | None,
    operation_kind: str,
    implementation: str,
    parameters: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    provenance: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    created_by: str | None = None,
    input_revisions: Iterable[
        tuple[MolecularRevision, str, dict[str, Any] | None]
    ] = (),
    inline_inputs: Iterable[
        tuple[NucleotideSequence, str, dict[str, Any] | None]
    ] = (),
) -> tuple[MolecularOperation, MolecularRevision]:
    session.add(sequence)
    operation = await create_operation(
        session,
        operation_kind=operation_kind,
        implementation=implementation,
        parameters=parameters,
        warnings=warnings,
        provenance=provenance,
        idempotency_key=idempotency_key,
        created_by=SERVER_OWNED_ACTOR,
    )
    revision = await record_sequence_revision(
        session,
        sequence,
        change_kind="operation_result",
        provenance=provenance,
        operation_id=operation.id,
        created_by=SERVER_OWNED_ACTOR,
    )
    inputs: list[tuple[MolecularRevision, str, dict[str, Any] | None]] = []
    for input_revision, role, snapshot in input_revisions:
        inputs.append(
            (
                input_revision,
                role,
                {
                    "document_id": input_revision.document_id,
                    "revision_id": input_revision.id,
                    "revision_sha256": input_revision.content_sha256,
                    **(snapshot or {}),
                },
            )
        )
    represented_documents = {input_revision.document_id for input_revision, _, _ in inputs}
    if parent is not None and parent.id not in represented_documents:
        parent_revision = await current_molecular_revision(session, parent.id)
        if parent_revision is not None:
            inputs.append(
                (
                    parent_revision,
                    "template",
                    {
                        "document_id": parent.id,
                        "revision_id": parent_revision.id,
                        "revision_sha256": parent_revision.content_sha256,
                    },
                )
            )
    for inline_sequence, role, snapshot in inline_inputs:
        inline_revision = await _record_inline_sequence_input(
            session,
            inline_sequence,
            operation=operation,
            provenance=provenance,
            created_by=SERVER_OWNED_ACTOR,
        )
        inputs.append(
            (
                inline_revision,
                role,
                {
                    "document_id": inline_revision.document_id,
                    "revision_id": inline_revision.id,
                    "revision_sha256": inline_revision.content_sha256,
                    "input_kind": "inline_sequence",
                    **(snapshot or {}),
                },
            )
        )
    await add_operation_edges(
        session,
        operation,
        input_revisions=inputs,
        output_revisions=[(revision, "product", {"document_id": sequence.id})],
    )
    return operation, revision


async def ensure_tm_model_revision(
    session: AsyncSession, tm_snapshot: dict[str, Any]
) -> TmModelRevision:
    """Resolve the exact requested algorithm, table, salt model, and settings."""

    identity = tm_model_revision_identity(tm_snapshot)
    model_id = str(identity["model_id"])
    revision_id = str(identity["revision_id"])

    await session.execute(
        sqlite_insert(TmModel)
        .values(
            id=model_id,
            name=f"Biopython MeltingTemp — {identity['source']['algorithm']}",
            current_revision_id=None,
        )
        .on_conflict_do_nothing(index_elements=[TmModel.id])
    )
    await session.execute(
        sqlite_insert(TmModelRevision)
        .values(
            id=revision_id,
            model_id=model_id,
            revision_number=identity["revision_number"],
            implementation=identity["implementation"],
            implementation_version=identity["implementation_version"],
            parameter_schema=identity["parameter_schema"],
            source=identity["source"],
        )
        .on_conflict_do_nothing(index_elements=[TmModelRevision.id])
    )
    await session.execute(
        update(TmModel).where(TmModel.id == model_id).values(current_revision_id=revision_id)
    )
    revision = await session.get(TmModelRevision, revision_id)
    if revision is None:  # defensive: an ignored insert must have an existing row
        raise RuntimeError("Failed to resolve the requested Tm model revision")
    return revision


async def _polymerase_snapshot(
    session: AsyncSession, revision_id: str | None
) -> tuple[PolymerasePresetRevision | None, dict[str, Any] | None]:
    if revision_id is None:
        return None, None
    revision = await session.get(PolymerasePresetRevision, revision_id)
    if revision is None:
        raise ValueError(f"Unknown polymerase preset revision: {revision_id}")
    preset = await session.get(PolymerasePreset, revision.preset_id)
    if preset is None:
        raise ValueError(f"Polymerase preset revision has no parent preset: {revision_id}")
    return revision, {
        "preset_id": preset.id,
        "revision_id": revision.id,
        "vendor": preset.vendor,
        "product_name": preset.product_name,
        "catalog_number": preset.catalog_number,
        "values": revision.values,
        "source": revision.source,
        "source_sha256": revision.source_sha256,
        "effective_at": _json_value(revision.effective_at),
    }


async def get_pcr_by_idempotency_key(
    session: AsyncSession,
    idempotency_key: str | None,
    request_fingerprint: str | None = None,
) -> PCRPersistenceResult | None:
    if not idempotency_key:
        return None
    revision = (
        await session.execute(
            select(PCRExperimentRevision).where(
                PCRExperimentRevision.idempotency_key == idempotency_key
            )
        )
    ).scalar_one_or_none()
    if revision is None:
        return None
    operation = await session.get(MolecularOperation, revision.operation_id)
    revision_fingerprint = revision.request_fingerprint
    operation_fingerprint = operation.request_fingerprint if operation else None
    if request_fingerprint is not None:
        if not revision_fingerprint or not operation_fingerprint:
            raise IdempotencyConflictError(
                "Idempotency key belongs to a legacy PCR record without a request fingerprint"
            )
        if revision_fingerprint != operation_fingerprint:
            raise IdempotencyConflictError(
                "Idempotency key belongs to an inconsistent PCR provenance record"
            )
        if revision_fingerprint != request_fingerprint:
            raise IdempotencyConflictError(
                "Idempotency key is already bound to a different PCR request"
            )
    stored_fingerprint = revision_fingerprint or operation_fingerprint
    product_sequence_snapshot = None
    if revision.product_revision_id:
        product_revision = await session.get(MolecularRevision, revision.product_revision_id)
        if product_revision is not None:
            product_sequence_snapshot = product_revision.snapshot
    return PCRPersistenceResult(
        experiment_id=revision.experiment_id,
        experiment_revision_id=revision.id,
        operation_id=revision.operation_id,
        product_document_id=revision.product_document_id,
        product_revision_id=revision.product_revision_id,
        request_fingerprint=stored_fingerprint,
        product_snapshot=revision.product_snapshot,
        product_sequence_snapshot=product_sequence_snapshot,
        reused=True,
    )


async def persist_pcr_experiment(
    session: AsyncSession,
    *,
    template: NucleotideSequence,
    template_was_persisted: bool,
    forward_primer_snapshot: dict[str, Any],
    reverse_primer_snapshot: dict[str, Any],
    tm_snapshot: dict[str, Any],
    product_snapshot: dict[str, Any],
    reaction_settings: dict[str, Any],
    cycling_assumptions: dict[str, Any],
    warnings: list[str],
    notes: str | None,
    review_state: str,
    provenance: dict[str, Any],
    product_sequence: NucleotideSequence | None = None,
    polymerase_preset_revision_id: str | None = None,
    idempotency_key: str | None = None,
    request_fingerprint: str | None = None,
    created_by: str | None = None,
) -> PCRPersistenceResult:
    """Persist one PCR experiment and optional product document transactionally."""

    # Acquire SQLite's writer lock through the deterministic Tm upsert before
    # the lookup/insert sequence. Concurrent requests then observe the
    # committed winner instead of attempting to upgrade stale read snapshots.
    tm_revision = await ensure_tm_model_revision(session, tm_snapshot)
    existing = await get_pcr_by_idempotency_key(
        session, idempotency_key, request_fingerprint
    )
    if existing is not None:
        return existing

    polymerase_revision, polymerase_snapshot = await _polymerase_snapshot(
        session, polymerase_preset_revision_id
    )
    operation = await create_operation(
        session,
        operation_kind="pcr",
        implementation="routers.molbio_ops.pcr",
        parameters={
            "reaction_settings": reaction_settings,
            "cycling_assumptions": cycling_assumptions,
            "tm_model_revision_id": tm_revision.id,
            "polymerase_preset_revision_id": polymerase_preset_revision_id,
        },
        warnings=warnings,
        provenance=provenance,
        idempotency_key=f"pcr-operation:{idempotency_key}" if idempotency_key else None,
        request_fingerprint=request_fingerprint,
        created_by=SERVER_OWNED_ACTOR,
    )

    if template_was_persisted:
        template_revision = await current_molecular_revision(session, template.id)
        if template_revision is None:
            raise ValueError(
                f"Persisted PCR template has no immutable revision: {template.id}"
            )
    else:
        template_revision = await _record_inline_sequence_input(
            session,
            template,
            operation=operation,
            provenance=provenance,
            created_by=SERVER_OWNED_ACTOR,
        )
    product_revision: MolecularRevision | None = None
    if product_sequence is not None:
        session.add(product_sequence)
        product_revision = await record_sequence_revision(
            session,
            product_sequence,
            change_kind="pcr_product",
            provenance=provenance,
            operation_id=operation.id,
            created_by=SERVER_OWNED_ACTOR,
        )

    experiment = PCRExperiment(
        id=str(uuid.uuid4()),
        name=f"PCR — {template.name}",
        review_state=review_state,
    )
    session.add(experiment)
    await session.flush()
    experiment_revision = PCRExperimentRevision(
        id=str(uuid.uuid4()),
        experiment_id=experiment.id,
        revision_number=1,
        operation_id=operation.id,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        template_document_id=template_revision.document_id,
        template_revision_id=template_revision.id,
        template_sha256=sha256_text(template.sequence),
        template_snapshot=sequence_snapshot(template),
        forward_primer_snapshot=_json_value(forward_primer_snapshot),
        reverse_primer_snapshot=_json_value(reverse_primer_snapshot),
        tm_model_revision_id=tm_revision.id,
        tm_snapshot=_json_value(tm_snapshot),
        polymerase_preset_revision_id=polymerase_revision.id if polymerase_revision else None,
        polymerase_snapshot=polymerase_snapshot,
        reaction_settings=_json_value(reaction_settings),
        cycling_assumptions=_json_value(cycling_assumptions),
        product_document_id=product_sequence.id if product_sequence else None,
        product_revision_id=product_revision.id if product_revision else None,
        product_snapshot=_json_value(product_snapshot),
        warnings=warnings,
        notes=notes,
        review_state=review_state,
        provenance=_json_value(provenance),
        created_by=SERVER_OWNED_ACTOR,
    )
    session.add(experiment_revision)
    await session.flush()
    experiment.current_revision_id = experiment_revision.id

    input_edges = [
        (
            template_revision,
            "template",
            {
                "document_id": template_revision.document_id,
                "revision_id": template_revision.id,
                "revision_sha256": template_revision.content_sha256,
                "input_kind": "persisted_sequence"
                if template_was_persisted
                else "inline_sequence",
            },
        )
    ]
    output_edges = []
    if product_revision is not None:
        output_edges.append((product_revision, "pcr_product", {"document_id": product_sequence.id}))
    await add_operation_edges(
        session,
        operation,
        input_revisions=input_edges,
        output_revisions=output_edges,
    )
    await _emit_history_events(
        session,
        entity_kind="pcr_experiment",
        entity_id=experiment.id,
        event_kind="pcr_experiment.created",
        payload={
            "revision_id": experiment_revision.id,
            "operation_id": operation.id,
            "template_sha256": experiment_revision.template_sha256,
            "product_sha256": product_snapshot.get("sha256"),
        },
        actor=SERVER_OWNED_ACTOR,
    )
    return PCRPersistenceResult(
        experiment_id=experiment.id,
        experiment_revision_id=experiment_revision.id,
        operation_id=operation.id,
        product_document_id=product_sequence.id if product_sequence else None,
        product_revision_id=product_revision.id if product_revision else None,
        request_fingerprint=request_fingerprint,
        product_snapshot=_json_value(product_snapshot),
        product_sequence_snapshot=(product_revision.snapshot if product_revision else None),
    )


async def revise_pcr_review_state(
    session: AsyncSession,
    *,
    experiment_id: str,
    review_state: str,
    notes: str | None = None,
    provenance: dict[str, Any] | None = None,
) -> PCRExperimentRevision:
    """Append a review-state revision; never mutate an experimental snapshot."""

    allowed = {"draft", "in_review", "approved", "rejected"}
    if review_state not in allowed:
        raise ValueError(f"Invalid PCR review state: {review_state}")
    experiment = await session.get(PCRExperiment, experiment_id)
    if experiment is None or not experiment.current_revision_id:
        raise ValueError(f"PCR experiment not found: {experiment_id}")
    current = await session.get(PCRExperimentRevision, experiment.current_revision_id)
    if current is None:
        raise ValueError(f"PCR experiment current revision is missing: {experiment_id}")

    review_provenance = dict(current.provenance or {})
    review_provenance.update(provenance or {})
    review_provenance["review_transition"] = {
        "from": current.review_state,
        "to": review_state,
        "actor": SERVER_OWNED_ACTOR,
        "at": datetime.utcnow().isoformat() + "Z",
    }
    revision = PCRExperimentRevision(
        id=str(uuid.uuid4()),
        experiment_id=experiment.id,
        revision_number=current.revision_number + 1,
        idempotency_key=None,
        request_fingerprint=current.request_fingerprint,
        operation_id=current.operation_id,
        template_document_id=current.template_document_id,
        template_revision_id=current.template_revision_id,
        template_sha256=current.template_sha256,
        template_snapshot=current.template_snapshot,
        forward_primer_snapshot=current.forward_primer_snapshot,
        reverse_primer_snapshot=current.reverse_primer_snapshot,
        tm_model_revision_id=current.tm_model_revision_id,
        tm_snapshot=current.tm_snapshot,
        polymerase_preset_revision_id=current.polymerase_preset_revision_id,
        polymerase_snapshot=current.polymerase_snapshot,
        reaction_settings=current.reaction_settings,
        cycling_assumptions=current.cycling_assumptions,
        product_document_id=current.product_document_id,
        product_revision_id=current.product_revision_id,
        product_snapshot=current.product_snapshot,
        warnings=current.warnings,
        notes=current.notes if notes is None else notes,
        review_state=review_state,
        provenance=review_provenance,
        created_by=SERVER_OWNED_ACTOR,
    )
    session.add(revision)
    await session.flush()
    experiment.current_revision_id = revision.id
    experiment.review_state = review_state
    experiment.updated_at = datetime.utcnow()
    await _emit_history_events(
        session,
        entity_kind="pcr_experiment",
        entity_id=experiment.id,
        event_kind="pcr.review_state_changed",
        payload={
            "from": current.review_state,
            "to": review_state,
            "revision_id": revision.id,
        },
        actor=SERVER_OWNED_ACTOR,
    )
    return revision
