"""Domain services for global-keyed MolBio/NGS scientific state."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import ExperimentAggregateHead, ExperimentDomainAdapterReceipt, ExperimentDomainConnectorCommand

from molbio_ngs_models import (
    MolBioNGSAuditEvent,
    MolBioNGSDomainState,
    MolBioNGSDomainStateMember,
    MolBioNGSDomainStateRevision,
    MolBioNGSEvidenceAssessment,
    MolBioNGSGlobalBinding,
    MolBioNGSIdempotencyClaim,
    MolBioNGSMemberReceipt,
    MolBioNGSOutboxEvent,
    MolBioNGSReferenceResource,
    MolBioNGSReferenceRevision,
    MolBioNGSSample,
    MolBioNGSSampleRevision,
)
from services.molbio_authority import SERVER_OWNED_ACTOR
from services.molbio_ngs_member_receipts import parse_canonical_member_receipt
from services.ngs_molbio_connector import BINDING_ADAPTER_ID, emit_ordered_event


STATE_SCHEMA = "bms.molbio-ngs.domain-state-revision.v1"
STATE_SCHEMA_NAME = "bms.molbio-ngs.domain-state-revision"
STATE_SCHEMA_VERSION = "1"
SAMPLE_SCHEMA = "bms.molbio-ngs.sample-revision.v1"
SAMPLE_SCHEMA_NAME = "bms.molbio-ngs.sample-revision"
SAMPLE_SCHEMA_VERSION = "1"
_SAMPLE_PAYLOAD_KEYS = {
    "schema",
    "name",
    "description",
    "sample_kind",
    "source",
    "preparation",
    "labels",
    "notes",
}
_STATE_PAYLOAD_KEYS = {
    "schema",
    "design",
    "reference_policy",
    "acquisition_policy",
    "analysis_policy",
    "assessment_policy",
    "notes",
}
_ROLE_ENTITY_KINDS = {
    "molecular_expected_construct": "molecular_revision",
    "molecular_input_fragment": "molecular_revision",
    "molecular_assembly_product": "molecular_revision",
    "molecular_pcr_template": "molecular_revision",
    "molecular_pcr_product": "molecular_revision",
    "molecular_primer_forward": "primer_revision",
    "molecular_primer_reverse": "primer_revision",
    "molecular_operation": "molecular_operation",
    "molecular_pcr_experiment": "pcr_experiment_revision",
    "ngs_reference": "ngs_reference_revision",
    "ngs_comparison_panel": "ngs_comparison_panel",
    "ngs_instrument_run": "ont_instrument_run",
    "ngs_analysis_job": "ngs_job",
    "ngs_analysis_result_manifest": "ngs_result_manifest",
    "ngs_verification_assessment": "ngs_evidence_assessment",
}
_DOMAIN_OWNED_RECEIPT_KINDS = frozenset(
    {
        "ngs_reference_revision",
        "ngs_job",
        "ngs_result_manifest",
        "ont_instrument_run",
        "ngs_evidence_assessment",
    }
)


class MolBioNGSServiceError(RuntimeError):
    pass


class DomainStateNotFound(MolBioNGSServiceError):
    pass


class DomainStateAlreadyExists(MolBioNGSServiceError):
    pass


class GlobalBindingError(MolBioNGSServiceError):
    pass


class GlobalAdapterUnavailable(GlobalBindingError):
    pass


class RevisionConflict(MolBioNGSServiceError):
    pass


class IdempotencyConflict(MolBioNGSServiceError):
    pass


class StateValidationError(MolBioNGSServiceError):
    pass


class StateIntegrityError(StateValidationError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4()}"


@dataclass(frozen=True)
class InternalVerifiedGlobalBinding:
    """Explicit internal fixture representing a finalized adapter acknowledgement."""

    global_domain_experiment_id: str
    global_domain_experiment_revision_id: str
    global_domain_experiment_revision_digest: str
    project_id: str
    project_generation: str
    project_digest: str
    project_receipt_id: str
    project_reopen_destination: Mapping[str, Any]
    global_experiment_id: str
    global_experiment_generation: str
    global_experiment_digest: str
    global_experiment_receipt_id: str
    global_experiment_reopen_destination: Mapping[str, Any]
    verified_at: str
    project_acknowledgement: Mapping[str, Any] = field(default_factory=dict)
    global_experiment_acknowledgement: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StateMember:
    receipt_id: str
    role: str
    ordinal: int
    sample_revision_id: str | None = None


async def verify_global_domain_binding(
    session: AsyncSession,
    global_domain_experiment_id: str,
    global_domain_experiment_revision_id: str,
) -> InternalVerifiedGlobalBinding:
    """Resolve the finalized server-issued combined hierarchy receipt."""

    domain = await session.get(ExperimentAggregateHead, global_domain_experiment_id)
    if domain is None or domain.aggregate_kind != "domain_experiment":
        raise GlobalBindingError("global Domain Experiment was not found")
    if domain.current_revision_id != global_domain_experiment_revision_id:
        raise GlobalBindingError("global Domain Experiment revision is stale")
    command = await session.scalar(
        select(ExperimentDomainConnectorCommand)
        .where(
            ExperimentDomainConnectorCommand.domain_experiment_id == global_domain_experiment_id,
            ExperimentDomainConnectorCommand.domain_revision_id == global_domain_experiment_revision_id,
            ExperimentDomainConnectorCommand.status.in_({"applied", "duplicate"}),
        )
        .order_by(ExperimentDomainConnectorCommand.updated_at.desc())
    )
    if command is None or command.acknowledgement_json is None:
        raise GlobalAdapterUnavailable("current NGS/MolBio binding is not acknowledged")
    receipt_row = await session.get(ExperimentDomainAdapterReceipt, command.global_receipt_id)
    if receipt_row is None or receipt_row.adapter_id != BINDING_ADAPTER_ID:
        raise GlobalBindingError("combined hierarchy receipt is unavailable")
    if _digest(receipt_row.receipt_json) != command.global_receipt_sha256:
        raise GlobalBindingError("combined hierarchy receipt digest diverged")
    if command.acknowledgement_sha256 != _digest(command.acknowledgement_json):
        raise GlobalBindingError("combined hierarchy acknowledgement digest diverged")
    try:
        receipt = json.loads(receipt_row.receipt_json)
        acknowledgement = json.loads(command.acknowledgement_json)
    except json.JSONDecodeError as exc:
        raise GlobalBindingError("combined hierarchy binding evidence is invalid JSON") from exc
    if (
        receipt.get("receipt_id") != command.global_receipt_id
        or receipt.get("adapter_id") != BINDING_ADAPTER_ID
        or receipt.get("domain_experiment", {}).get("id") != global_domain_experiment_id
        or receipt.get("domain_experiment", {}).get("revision_id")
        != global_domain_experiment_revision_id
        or acknowledgement.get("command_id") != command.command_id
        or acknowledgement.get("binding_revision_id") != command.binding_revision_id
        or acknowledgement.get("accepted_payload_sha256") != command.global_receipt_sha256
        or acknowledgement.get("disposition") not in {"applied", "duplicate"}
    ):
        raise GlobalBindingError("combined hierarchy binding evidence diverged")
    return InternalVerifiedGlobalBinding(
        global_domain_experiment_id=global_domain_experiment_id,
        global_domain_experiment_revision_id=global_domain_experiment_revision_id,
        global_domain_experiment_revision_digest=command.domain_revision_sha256,
        project_id=command.project_id,
        project_generation=str(receipt["project"]["generation"]),
        project_digest=str(receipt["project"]["digest"]),
        project_receipt_id=command.global_receipt_id,
        project_reopen_destination={"uri": receipt["project"]["reopen_destination"]},
        project_acknowledgement={"status": "verified"},
        global_experiment_id=command.global_experiment_id,
        global_experiment_generation=str(receipt["global_experiment"]["generation"]),
        global_experiment_digest=str(receipt["global_experiment"]["digest"]),
        global_experiment_receipt_id=command.global_receipt_id,
        global_experiment_reopen_destination={"uri": receipt["global_experiment"]["reopen_destination"]},
        global_experiment_acknowledgement={"status": "verified"},
        verified_at=str(receipt["verified_at"]),
    )


def _binding_request(binding: InternalVerifiedGlobalBinding) -> dict[str, object]:
    return {
        "global_domain_experiment_id": binding.global_domain_experiment_id,
        "global_domain_experiment_revision_id": binding.global_domain_experiment_revision_id,
        "global_domain_experiment_revision_digest": binding.global_domain_experiment_revision_digest,
        "project_id": binding.project_id,
        "project_generation": binding.project_generation,
        "project_digest": binding.project_digest,
        "global_experiment_id": binding.global_experiment_id,
        "global_experiment_generation": binding.global_experiment_generation,
        "global_experiment_digest": binding.global_experiment_digest,
    }


async def _reserve_idempotency(
    session: AsyncSession,
    *,
    scope: str,
    idempotency_key: str,
    request_sha256: str,
    result_resource_id: str,
) -> str | None:
    """Reserve a scoped key as the transaction's first write, or replay its winner."""

    now = _now()
    result = await session.execute(
        sqlite_insert(MolBioNGSIdempotencyClaim)
        .values(
            scope=scope,
            idempotency_key=idempotency_key,
            status="pending",
            request_sha256=request_sha256,
            result_resource_id=result_resource_id,
            response_json=None,
            created_at=now,
            completed_at=None,
        )
        .on_conflict_do_nothing(index_elements=["scope", "idempotency_key"])
    )
    if result.rowcount == 1:
        return None
    claim = await session.get(MolBioNGSIdempotencyClaim, (scope, idempotency_key))
    if claim is None:
        raise MolBioNGSServiceError("idempotency reservation winner was not visible")
    if claim.request_sha256 != request_sha256:
        raise IdempotencyConflict("idempotency key was already used for a different request")
    if claim.status != "completed" or claim.response_json is None or claim.completed_at is None:
        raise MolBioNGSServiceError("idempotency claim is incomplete")
    return claim.result_resource_id


async def _complete_idempotency(
    session: AsyncSession,
    *,
    scope: str,
    idempotency_key: str,
    request_sha256: str,
    result_resource_id: str,
    response: Mapping[str, Any],
) -> None:
    result = await session.execute(
        update(MolBioNGSIdempotencyClaim)
        .where(
            MolBioNGSIdempotencyClaim.scope == scope,
            MolBioNGSIdempotencyClaim.idempotency_key == idempotency_key,
            MolBioNGSIdempotencyClaim.status == "pending",
            MolBioNGSIdempotencyClaim.request_sha256 == request_sha256,
            MolBioNGSIdempotencyClaim.result_resource_id == result_resource_id,
            MolBioNGSIdempotencyClaim.response_json.is_(None),
            MolBioNGSIdempotencyClaim.completed_at.is_(None),
        )
        .values(
            status="completed",
            response_json=_canonical(response),
            completed_at=_now(),
        )
    )
    if result.rowcount != 1:
        raise StateIntegrityError("idempotency claim could not be completed exactly once")


async def _audit_and_outbox(
    session: AsyncSession,
    *,
    domain_id: str,
    resource_id: str,
    state_revision_id: str | None,
    event_type: str,
    generation: int,
    payload: Mapping[str, Any],
    created_by: str | None,
) -> None:
    now = _now()
    canonical_payload = _canonical(payload)
    payload_sha256 = _digest(canonical_payload)
    session.add(
        MolBioNGSAuditEvent(
            id=_id("audit"),
            global_domain_experiment_id=domain_id,
            resource_id=resource_id,
            event_type=event_type,
            generation=generation,
            payload_json=canonical_payload,
            payload_sha256=payload_sha256,
            created_at=now,
            created_by=SERVER_OWNED_ACTOR,
        )
    )
    state = await session.get(MolBioNGSDomainState, domain_id)
    if state is None or not state.current_binding_revision_id:
        raise StateIntegrityError("ordered outbox requires a current binding revision")
    if event_type == "molbio_ngs.domain_state.initialized":
        event_stream, source_generation = "binding", 0
    elif event_type == "molbio_ngs.domain_state.revision_saved":
        event_stream, source_generation = "state", int(payload["state_revision_number"])
    elif event_type.startswith("molbio_ngs.sample."):
        event_stream = f"sample:{payload['sample_id']}"
        source_generation = int(payload["sample_revision_number"])
    elif event_type.startswith("molbio_ngs.reference."):
        event_stream = f"reference:{payload['reference_id']}"
        source_generation = int(payload.get("reference_revision_number", payload.get("head_generation", 0)))
    elif event_type == "molbio_ngs.instrument_run_evidence.attached":
        event_stream = f"member:ont_instrument_run:{payload['run_id']}"
        source_generation = int(payload["observed_generation"])
    elif event_type == "molbio_ngs.evidence.assessed":
        event_stream, source_generation = f"evidence:{payload['evidence_id']}", None
    elif event_type == "molbio_ngs.member_receipt.published":
        event_stream = (
            f"member:{payload['receipt_kind']}:{payload['native_entity_id']}"
        )
        source_generation = None
    else:
        raise StateValidationError(f"event type has no frozen stream mapping: {event_type}")
    await emit_ordered_event(
        session,
        domain_id=domain_id,
        binding_revision_id=state.current_binding_revision_id,
        event_stream=event_stream,
        event_type=event_type,
        payload=dict(payload),
        source_generation=source_generation,
        state_revision_id=state_revision_id,
    )


async def initialize_domain_state(
    session: AsyncSession,
    binding: InternalVerifiedGlobalBinding,
    *,
    idempotency_key: str,
    created_by: str | None = None,
) -> MolBioNGSDomainState:
    if not idempotency_key.strip():
        raise StateValidationError("idempotency_key is required")
    request_json = _canonical(_binding_request(binding))
    request_sha256 = _digest(request_json)
    scope = f"initialize:{binding.global_domain_experiment_id}"
    replay_id = await _reserve_idempotency(
        session,
        scope=scope,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        result_resource_id=binding.global_domain_experiment_id,
    )
    if replay_id is not None:
        state = await session.get(MolBioNGSDomainState, replay_id)
        if state is None:
            raise MolBioNGSServiceError("idempotency claim references missing state")
        return state
    state = await session.get(MolBioNGSDomainState, binding.global_domain_experiment_id)
    if state is None:
        raise GlobalAdapterUnavailable(
            "local initialization is owned by the managed connector command"
        )
    await acknowledge_global_binding(session, binding)
    await session.flush()
    await _complete_idempotency(
        session,
        scope=scope,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        result_resource_id=binding.global_domain_experiment_id,
        response={"global_domain_experiment_id": binding.global_domain_experiment_id},
    )
    await session.flush()
    return state


async def acknowledge_global_binding(
    session: AsyncSession,
    binding: InternalVerifiedGlobalBinding,
) -> None:
    state = await session.get(MolBioNGSDomainState, binding.global_domain_experiment_id)
    stored = (
        await session.get(MolBioNGSGlobalBinding, state.current_binding_revision_id)
        if state is not None else None
    )
    if stored is None:
        raise DomainStateNotFound("MolBio/NGS state has not been initialized")
    supplied_authority = {
        "global_domain_experiment_revision_id": binding.global_domain_experiment_revision_id,
        "global_domain_experiment_revision_digest": binding.global_domain_experiment_revision_digest,
        "project_id": binding.project_id,
        "project_generation": binding.project_generation,
        "project_digest": binding.project_digest,
        "global_experiment_id": binding.global_experiment_id,
        "global_experiment_generation": binding.global_experiment_generation,
        "global_experiment_digest": binding.global_experiment_digest,
    }
    changed_authority = [
        field_name
        for field_name, supplied_value in supplied_authority.items()
        if getattr(stored, field_name) != supplied_value
    ]
    if changed_authority:
        raise GlobalBindingError(
            "global binding authority changed: " + ", ".join(changed_authority)
        )
    if stored.binding_state != "acknowledged" or stored.global_binding_receipt_sha256 is None:
        raise GlobalBindingError("current local binding is not acknowledged by the managed connector")


async def require_acknowledged_local_domain(
    session: AsyncSession,
    global_domain_experiment_id: str,
) -> MolBioNGSDomainState:
    """Require acknowledged local authority without querying global tables."""

    state = await session.get(MolBioNGSDomainState, global_domain_experiment_id)
    binding = (
        await session.get(MolBioNGSGlobalBinding, state.current_binding_revision_id)
        if state is not None else None
    )
    if state is None or binding is None or binding.binding_state != "acknowledged":
        raise DomainStateNotFound(
            "acknowledged MolBio/NGS Domain Experiment state was not found"
        )
    return state


def _bounded_text(value: Any, field_name: str, *, maximum: int, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise StateValidationError(f"{field_name} must be bounded non-empty text")


def _validate_sample_payload(payload: Mapping[str, Any]) -> None:
    if set(payload) != _SAMPLE_PAYLOAD_KEYS or payload.get("schema") != SAMPLE_SCHEMA:
        raise StateValidationError(f"sample payload must exactly implement {SAMPLE_SCHEMA}")
    _bounded_text(payload["name"], "sample.name", maximum=255)
    if not isinstance(payload["description"], str) or len(payload["description"]) > 4000:
        raise StateValidationError("sample.description must be bounded text")
    _bounded_text(payload["sample_kind"], "sample.sample_kind", maximum=128)
    source = payload["source"]
    if not isinstance(source, Mapping) or set(source) != {"organism", "strain", "external_ids"}:
        raise StateValidationError("sample.source has an invalid shape")
    for field_name in ("organism", "strain"):
        value = source[field_name]
        if value is not None:
            _bounded_text(value, f"sample.source.{field_name}", maximum=255)
    external_ids = source["external_ids"]
    if not isinstance(external_ids, list) or len(external_ids) > 100 or any(
        not isinstance(item, str) or not item.strip() or len(item) > 255
        for item in external_ids
    ):
        raise StateValidationError("sample.source.external_ids is invalid")
    preparation = payload["preparation"]
    if not isinstance(preparation, Mapping) or set(preparation) != {
        "method", "batch_id", "prepared_at"
    }:
        raise StateValidationError("sample.preparation has an invalid shape")
    _bounded_text(preparation["method"], "sample.preparation.method", maximum=255)
    for field_name in ("batch_id", "prepared_at"):
        value = preparation[field_name]
        if value is not None:
            _bounded_text(value, f"sample.preparation.{field_name}", maximum=255)
    labels = payload["labels"]
    if not isinstance(labels, Mapping) or set(labels) != {
        "container_label", "barcode", "minknow_sample_id"
    }:
        raise StateValidationError("sample.labels has an invalid shape")
    for field_name, value in labels.items():
        if value is not None:
            _bounded_text(value, f"sample.labels.{field_name}", maximum=255)
    if not isinstance(payload["notes"], str) or len(payload["notes"]) > 4000:
        raise StateValidationError("sample.notes must be bounded text")


async def create_sample(
    session: AsyncSession,
    *,
    global_domain_experiment_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
    created_by: str | None = None,
) -> tuple[MolBioNGSSample, MolBioNGSSampleRevision]:
    if not idempotency_key.strip():
        raise StateValidationError("idempotency_key is required")
    _validate_sample_payload(payload)
    await require_acknowledged_local_domain(session, global_domain_experiment_id)
    canonical_payload = _canonical(payload)
    request_sha256 = _digest(
        _canonical(
            {
                "global_domain_experiment_id": global_domain_experiment_id,
                "payload": json.loads(canonical_payload),
                "created_by": SERVER_OWNED_ACTOR,
            }
        )
    )
    scope = f"create-sample:{global_domain_experiment_id}"
    sample_id = _id("molbio_ngs_sample")
    replay_id = await _reserve_idempotency(
        session,
        scope=scope,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        result_resource_id=sample_id,
    )
    if replay_id is not None:
        sample = await session.get(MolBioNGSSample, replay_id)
        if sample is None or sample.current_revision_id is None:
            raise StateIntegrityError("idempotency claim references missing sample")
        revision = await session.get(MolBioNGSSampleRevision, sample.current_revision_id)
        if revision is None:
            raise StateIntegrityError("sample head references missing revision")
        return sample, revision

    now = _now()
    sample = MolBioNGSSample(
        id=sample_id,
        global_domain_experiment_id=global_domain_experiment_id,
        current_revision_id=None,
        head_generation=0,
        archived_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(sample)
    await session.flush([sample])
    revision = MolBioNGSSampleRevision(
        id=_id("molbio_ngs_sample_revision"),
        sample_id=sample.id,
        global_domain_experiment_id=global_domain_experiment_id,
        revision_number=1,
        parent_revision_id=None,
        schema_name=SAMPLE_SCHEMA_NAME,
        schema_version=SAMPLE_SCHEMA_VERSION,
        canonical_payload=canonical_payload,
        payload_sha256=_digest(canonical_payload),
        created_at=now,
        created_by=SERVER_OWNED_ACTOR,
    )
    session.add(revision)
    await session.flush([revision])
    sample.current_revision_id = revision.id
    sample.head_generation = 1
    sample.updated_at = now
    await _audit_and_outbox(
        session,
        domain_id=global_domain_experiment_id,
        resource_id=sample.id,
        state_revision_id=None,
        event_type="molbio_ngs.sample.created",
        generation=1,
        payload={
            "schema": "bms.molbio-ngs.sample-created.v1",
            "sample_id": sample.id,
            "sample_revision_id": revision.id,
            "payload_sha256": revision.payload_sha256,
            "sample_revision_number": revision.revision_number,
        },
        created_by=SERVER_OWNED_ACTOR,
    )
    await session.flush()
    await _complete_idempotency(
        session,
        scope=scope,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        result_resource_id=sample.id,
        response={"sample_id": sample.id, "sample_revision_id": revision.id},
    )
    await session.flush()
    return sample, revision


async def append_sample_revision(
    session: AsyncSession,
    *,
    global_domain_experiment_id: str,
    sample_id: str,
    payload: Mapping[str, Any],
    expected_head_generation: int,
    parent_revision_id: str | None,
    idempotency_key: str,
    created_by: str | None = None,
) -> MolBioNGSSampleRevision:
    if not idempotency_key.strip():
        raise StateValidationError("idempotency_key is required")
    _validate_sample_payload(payload)
    canonical_payload = _canonical(payload)
    request_sha256 = _digest(
        _canonical(
            {
                "global_domain_experiment_id": global_domain_experiment_id,
                "sample_id": sample_id,
                "payload": json.loads(canonical_payload),
                "expected_head_generation": expected_head_generation,
                "parent_revision_id": parent_revision_id,
                "created_by": SERVER_OWNED_ACTOR,
            }
        )
    )
    scope = f"append-sample-revision:{sample_id}"
    revision_id = _id("molbio_ngs_sample_revision")
    replay_id = await _reserve_idempotency(
        session,
        scope=scope,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        result_resource_id=revision_id,
    )
    if replay_id is not None:
        revision = await session.get(MolBioNGSSampleRevision, replay_id)
        if revision is None:
            raise StateIntegrityError("idempotency claim references missing sample revision")
        return revision
    sample = await get_sample(session, global_domain_experiment_id, sample_id)
    if sample.archived_at is not None:
        raise RevisionConflict("archived sample cannot receive a revision")
    if sample.head_generation != expected_head_generation or sample.current_revision_id != parent_revision_id:
        raise RevisionConflict("sample head generation or parent revision changed")
    now = _now()
    revision = MolBioNGSSampleRevision(
        id=revision_id,
        sample_id=sample.id,
        global_domain_experiment_id=global_domain_experiment_id,
        revision_number=expected_head_generation + 1,
        parent_revision_id=parent_revision_id,
        schema_name=SAMPLE_SCHEMA_NAME,
        schema_version=SAMPLE_SCHEMA_VERSION,
        canonical_payload=canonical_payload,
        payload_sha256=_digest(canonical_payload),
        created_at=now,
        created_by=SERVER_OWNED_ACTOR,
    )
    session.add(revision)
    await session.flush([revision])
    result = await session.execute(
        update(MolBioNGSSample)
        .where(
            MolBioNGSSample.id == sample_id,
            MolBioNGSSample.global_domain_experiment_id == global_domain_experiment_id,
            MolBioNGSSample.head_generation == expected_head_generation,
            MolBioNGSSample.current_revision_id == parent_revision_id,
        )
        .values(
            current_revision_id=revision.id,
            head_generation=expected_head_generation + 1,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        raise RevisionConflict("sample head changed during revision save")
    await _audit_and_outbox(
        session,
        domain_id=global_domain_experiment_id,
        resource_id=sample.id,
        state_revision_id=None,
        event_type="molbio_ngs.sample.revision_saved",
        generation=expected_head_generation + 1,
        payload={
            "schema": "bms.molbio-ngs.sample-revision-saved.v1",
            "sample_id": sample.id,
            "sample_revision_id": revision.id,
            "payload_sha256": revision.payload_sha256,
            "sample_revision_number": revision.revision_number,
        },
        created_by=SERVER_OWNED_ACTOR,
    )
    await session.flush()
    await _complete_idempotency(
        session,
        scope=scope,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        result_resource_id=revision.id,
        response={"sample_revision_id": revision.id},
    )
    await session.flush()
    return revision


async def get_sample(
    session: AsyncSession, global_domain_experiment_id: str, sample_id: str
) -> MolBioNGSSample:
    sample = await session.get(MolBioNGSSample, sample_id)
    if sample is None or sample.global_domain_experiment_id != global_domain_experiment_id:
        raise DomainStateNotFound("MolBio/NGS sample was not found")
    return sample


async def list_samples(
    session: AsyncSession, global_domain_experiment_id: str
) -> list[MolBioNGSSample]:
    return list(
        (
            await session.execute(
                select(MolBioNGSSample)
                .where(MolBioNGSSample.global_domain_experiment_id == global_domain_experiment_id)
                .order_by(MolBioNGSSample.created_at, MolBioNGSSample.id)
            )
        ).scalars()
    )


async def get_sample_revision(
    session: AsyncSession,
    global_domain_experiment_id: str,
    sample_id: str,
    revision_id: str,
) -> MolBioNGSSampleRevision:
    revision = await session.get(MolBioNGSSampleRevision, revision_id)
    if (
        revision is None
        or revision.sample_id != sample_id
        or revision.global_domain_experiment_id != global_domain_experiment_id
    ):
        raise DomainStateNotFound("MolBio/NGS sample revision was not found")
    if (
        revision.schema_name != SAMPLE_SCHEMA_NAME
        or revision.schema_version != SAMPLE_SCHEMA_VERSION
        or _digest(revision.canonical_payload) != revision.payload_sha256
    ):
        raise StateIntegrityError("sample revision authority is invalid")
    try:
        payload = json.loads(revision.canonical_payload)
        _validate_sample_payload(payload)
    except (json.JSONDecodeError, StateValidationError) as exc:
        raise StateIntegrityError("sample revision payload is invalid") from exc
    if _canonical(payload) != revision.canonical_payload:
        raise StateIntegrityError("sample revision payload is not canonical")
    return revision


async def list_sample_revisions(
    session: AsyncSession,
    global_domain_experiment_id: str,
    sample_id: str,
) -> list[MolBioNGSSampleRevision]:
    await get_sample(session, global_domain_experiment_id, sample_id)
    revisions = list(
        (
            await session.execute(
                select(MolBioNGSSampleRevision)
                .where(MolBioNGSSampleRevision.sample_id == sample_id)
                .order_by(MolBioNGSSampleRevision.revision_number.desc())
            )
        ).scalars()
    )
    for revision in revisions:
        await get_sample_revision(
            session, global_domain_experiment_id, sample_id, revision.id
        )
    return revisions


def _receipt_authority(row: MolBioNGSMemberReceipt) -> dict[str, Any]:
    if _digest(row.canonical_receipt) != row.receipt_sha256:
        raise StateIntegrityError("persisted member receipt digest is invalid")
    try:
        canonical = parse_canonical_member_receipt(row.canonical_receipt)
    except ValueError as exc:
        raise StateIntegrityError("persisted member receipt canonical body is invalid") from exc
    expected = {
        "receipt_id": row.receipt_id,
        "source_store_id": row.source_store_id,
        "entity_kind": row.entity_kind,
        "entity_id": row.entity_id,
        "source_generation_or_revision": row.source_generation_or_revision,
        "content_digest": row.content_digest,
        "availability": row.availability,
        "reopen_destination": canonical["reopen_destination"],
        "created_at": row.created_at,
    }
    if (
        row.schema_name != "bms.molbio-ngs.external-member-receipt"
        or row.schema_version != "1"
        or row.reopen_destination != _canonical(canonical["reopen_destination"])
        or any(canonical[key] != value for key, value in expected.items())
    ):
        raise StateIntegrityError("persisted member receipt authority fields diverge")
    return {
        **expected,
        "receipt_schema_name": row.schema_name,
        "receipt_schema_version": row.schema_version,
        "source_schema": canonical["source_schema"],
        "receipt_sha256": row.receipt_sha256,
    }


def _string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise StateValidationError(f"{field_name} must be a list of non-empty strings")
    return value


def _validate_server_owned_policy_registries(payload: Mapping[str, Any]) -> None:
    """Require state policy identities to exist in the installed server registries."""

    from services.molbio_ngs_evidence import ASSESSMENT_RULE_REGISTRY  # noqa: PLC0415
    from services.ont_ngs_contract import CANONICAL_ONT_WORKFLOWS  # noqa: PLC0415

    analysis_policy = payload["analysis_policy"]
    assessment_policy = payload["assessment_policy"]
    workflow_ids = analysis_policy["allowed_workflow_ids"]
    manifest_schemas = analysis_policy["required_manifest_schemas"]
    if len(workflow_ids) != len(set(workflow_ids)):
        raise StateValidationError("analysis_policy.allowed_workflow_ids must be unique")
    if len(manifest_schemas) != len(set(manifest_schemas)):
        raise StateValidationError("analysis_policy.required_manifest_schemas must be unique")
    if any(workflow_id not in CANONICAL_ONT_WORKFLOWS for workflow_id in workflow_ids):
        raise StateValidationError("analysis policy names an unregistered canonical workflow")
    rule = ASSESSMENT_RULE_REGISTRY.get(assessment_policy["rule_id"])
    if rule is None:
        raise StateValidationError("assessment policy names an unregistered server rule")
    registered_manifest_schemas = frozenset(
        schema
        for registered_rule in ASSESSMENT_RULE_REGISTRY.values()
        for schema in registered_rule.pass_manifest_schemas
    )
    if any(schema not in registered_manifest_schemas for schema in manifest_schemas):
        raise StateValidationError("analysis policy names an unregistered result manifest schema")
    if any(schema not in rule.pass_manifest_schemas for schema in manifest_schemas):
        raise StateValidationError("analysis policy manifest schema is incompatible with its assessment rule")


async def resolve_state_analysis_launch_policy(
    session: AsyncSession,
    *,
    global_domain_experiment_id: str,
    state_revision_id: str,
    canonical_workflow_id: str,
) -> str:
    """Resolve one exact workflow/schema launch authorization from immutable state."""

    from services.molbio_ngs_evidence import ASSESSMENT_RULE_REGISTRY  # noqa: PLC0415
    from services.ont_ngs_contract import get_ont_workflow_spec  # noqa: PLC0415

    workflow = get_ont_workflow_spec(canonical_workflow_id)
    if workflow.workflow_id != canonical_workflow_id:
        raise StateValidationError("managed launch workflow must already be canonical")
    revision = await get_state_revision(
        session, global_domain_experiment_id, state_revision_id
    )
    payload, _membership = await verify_state_revision_integrity(session, revision)
    _validate_server_owned_policy_registries(payload)
    analysis_policy = payload["analysis_policy"]
    if canonical_workflow_id not in analysis_policy["allowed_workflow_ids"]:
        raise StateValidationError("workflow is not authorized by the exact state revision")
    rule = ASSESSMENT_RULE_REGISTRY[payload["assessment_policy"]["rule_id"]]
    eligible_schemas = [
        schema
        for schema in analysis_policy["required_manifest_schemas"]
        if schema in rule.pass_manifest_schemas
    ]
    if len(eligible_schemas) != 1:
        raise StateValidationError(
            "state revision must authorize exactly one registered result manifest schema"
        )
    return eligible_schemas[0]


def _validate_payload_shape(payload: Mapping[str, Any]) -> None:
    if set(payload) != _STATE_PAYLOAD_KEYS or payload.get("schema") != STATE_SCHEMA:
        raise StateValidationError(f"state payload must exactly implement {STATE_SCHEMA}")
    design = payload.get("design")
    if not isinstance(design, Mapping) or set(design) != {
        "sample_revision_ids",
        "conditions",
        "replicates",
        "expected_molecule_roles",
    }:
        raise StateValidationError("state payload design has an invalid shape")
    sample_revision_ids = _string_list(
        design["sample_revision_ids"], "design.sample_revision_ids"
    )
    if len(sample_revision_ids) != len(set(sample_revision_ids)):
        raise StateValidationError("design.sample_revision_ids must be unique")
    if not isinstance(design["conditions"], list) or not isinstance(design["replicates"], list):
        raise StateValidationError("design conditions and replicates must be lists")
    _string_list(design["expected_molecule_roles"], "design.expected_molecule_roles")

    reference_policy = payload.get("reference_policy")
    if not isinstance(reference_policy, Mapping) or set(reference_policy) != {
        "required_roles",
        "coordinate_policy",
    }:
        raise StateValidationError("state payload reference_policy has an invalid shape")
    _string_list(reference_policy["required_roles"], "reference_policy.required_roles")
    if reference_policy["coordinate_policy"] != "exact_revision":
        raise StateValidationError("reference_policy.coordinate_policy must be exact_revision")

    acquisition_policy = payload.get("acquisition_policy")
    if not isinstance(acquisition_policy, Mapping) or set(acquisition_policy) != {
        "platform",
        "required_terminal_manifest",
    }:
        raise StateValidationError("state payload acquisition_policy has an invalid shape")
    if acquisition_policy["platform"] not in {"ont", "external", "none"}:
        raise StateValidationError("acquisition_policy.platform is unsupported")
    if not isinstance(acquisition_policy["required_terminal_manifest"], bool):
        raise StateValidationError("acquisition_policy.required_terminal_manifest must be boolean")

    analysis_policy = payload.get("analysis_policy")
    if not isinstance(analysis_policy, Mapping) or set(analysis_policy) != {
        "allowed_workflow_ids",
        "required_manifest_schemas",
    }:
        raise StateValidationError("state payload analysis_policy has an invalid shape")
    _string_list(analysis_policy["allowed_workflow_ids"], "analysis_policy.allowed_workflow_ids")
    _string_list(
        analysis_policy["required_manifest_schemas"],
        "analysis_policy.required_manifest_schemas",
    )

    assessment_policy = payload.get("assessment_policy")
    if not isinstance(assessment_policy, Mapping) or set(assessment_policy) != {
        "rule_id",
        "completion_is_scientific_pass",
    }:
        raise StateValidationError("state payload assessment_policy has an invalid shape")
    if (
        not isinstance(assessment_policy["rule_id"], str)
        or not assessment_policy["rule_id"].strip()
        or len(assessment_policy["rule_id"]) > 128
    ):
        raise StateValidationError("assessment_policy.rule_id must be bounded")
    if assessment_policy["completion_is_scientific_pass"] is not False:
        raise StateValidationError("completion_is_scientific_pass must be false")
    _validate_server_owned_policy_registries(payload)
    notes = payload.get("notes")
    if not isinstance(notes, str) or len(notes) > 4000:
        raise StateValidationError("notes must be bounded text")


def _validate_payload(
    payload: Mapping[str, Any],
    members: Sequence[StateMember],
    receipt_rows: Mapping[str, MolBioNGSMemberReceipt],
    sample_rows: Mapping[str, MolBioNGSSampleRevision],
    *,
    global_domain_experiment_id: str,
) -> None:
    _validate_payload_shape(payload)
    sample_revision_ids = set(payload["design"]["sample_revision_ids"])
    if set(sample_rows) != sample_revision_ids or any(
        row.global_domain_experiment_id != global_domain_experiment_id
        for row in sample_rows.values()
    ):
        raise StateValidationError(
            "design.sample_revision_ids must resolve to revisions owned by this Domain Experiment"
        )
    ordinals: set[int] = set()
    for member in members:
        if (
            not member.receipt_id
            or not member.role.strip()
            or member.ordinal < 0
            or member.ordinal in ordinals
        ):
            raise StateValidationError("state member receipt, role, and unique ordinal are required")
        if (
            member.sample_revision_id is not None
            and member.sample_revision_id not in sample_revision_ids
        ):
            raise StateValidationError(
                "member sample_revision_id must be declared in design.sample_revision_ids"
            )
        ordinals.add(member.ordinal)
        row = receipt_rows.get(member.receipt_id)
        if row is None:
            raise StateValidationError("state member receipt is not persisted")
        authority = _receipt_authority(row)
        expected_kind = _ROLE_ENTITY_KINDS.get(member.role)
        if expected_kind is None:
            raise StateValidationError("state member role is reserved or unsupported")
        if authority["entity_kind"] != expected_kind:
            raise StateValidationError("state member role is incompatible with receipt entity kind")


def _require_resolved_receipt_authority(
    persisted: Mapping[str, Any],
    resolved: Any,
    *,
    label: str,
) -> None:
    expected = {
        "source_store_id": resolved.source_store_id,
        "entity_kind": resolved.entity_kind,
        "entity_id": resolved.entity_id,
        "source_generation_or_revision": resolved.source_generation_or_revision,
        "content_digest": resolved.content_digest,
        "source_schema": resolved.source_schema,
        "availability": resolved.availability,
        "reopen_destination": resolved.reopen_destination,
    }
    if {key: persisted[key] for key in expected} != expected:
        raise StateValidationError(
            f"{label} receipt no longer matches authoritative source"
        )


async def _validate_domain_owned_receipts(
    session: AsyncSession,
    core_session: AsyncSession | None,
    members: Sequence[StateMember],
    receipt_rows: Mapping[str, MolBioNGSMemberReceipt],
    *,
    global_domain_experiment_id: str,
    parent_revision_id: str | None,
) -> None:
    """Re-resolve every domain-owned receipt from its authoritative source row."""

    from database import Job  # noqa: PLC0415
    from services.molbio_ngs_evidence import (  # noqa: PLC0415
        resolve_evidence_assessment_receipt,
    )
    from services.molbio_ngs_member_receipts import (  # noqa: PLC0415
        resolve_ngs_job_receipt,
        resolve_ngs_result_manifest_receipt,
        resolve_ont_instrument_run_receipt,
    )
    from services.molbio_ngs_references import (  # noqa: PLC0415
        resolve_ngs_reference_revision_receipt,
    )

    for member in members:
        authority = _receipt_authority(receipt_rows[member.receipt_id])
        entity_kind = authority["entity_kind"]
        if entity_kind not in _DOMAIN_OWNED_RECEIPT_KINDS:
            continue

        try:
            if entity_kind == "ngs_reference_revision":
                revision = await session.get(
                    MolBioNGSReferenceRevision, authority["entity_id"]
                )
                resource = (
                    await session.get(MolBioNGSReferenceResource, revision.reference_id)
                    if revision is not None
                    else None
                )
                if (
                    revision is None
                    or resource is None
                    or revision.reference_id != resource.id
                    or revision.global_domain_experiment_id
                    != global_domain_experiment_id
                    or resource.global_domain_experiment_id
                    != global_domain_experiment_id
                ):
                    raise StateValidationError(
                        "managed reference receipt is not owned by this Domain Experiment"
                    )
                resolved = await resolve_ngs_reference_revision_receipt(
                    session,
                    global_domain_experiment_id=global_domain_experiment_id,
                    reference_id=resource.id,
                    revision_id=revision.id,
                )
                _require_resolved_receipt_authority(
                    authority, resolved, label="managed reference"
                )
                continue

            if entity_kind == "ngs_evidence_assessment":
                assessment = await session.get(
                    MolBioNGSEvidenceAssessment, authority["entity_id"]
                )
                if (
                    assessment is None
                    or assessment.global_domain_experiment_id
                    != global_domain_experiment_id
                    or parent_revision_id is None
                    or assessment.state_revision_id != parent_revision_id
                ):
                    raise StateValidationError(
                        "evidence assessment receipt is not owned by this Domain Experiment or exact parent state"
                    )
                resolved = await resolve_evidence_assessment_receipt(
                    session,
                    global_domain_experiment_id=global_domain_experiment_id,
                    evidence_id=assessment.evidence_id,
                )
                _require_resolved_receipt_authority(
                    authority, resolved, label="evidence assessment"
                )
                continue

            if core_session is None:
                raise StateValidationError(
                    "core NGS authority is required for domain-owned receipt admission"
                )

            if entity_kind in {"ngs_job", "ngs_result_manifest"}:
                reopen = authority["reopen_destination"]
                reopen_params = reopen.get("params") if isinstance(reopen, dict) else None
                job_id = (
                    authority["entity_id"]
                    if entity_kind == "ngs_job"
                    else reopen_params.get("job_id")
                    if isinstance(reopen_params, dict)
                    else None
                )
                if not isinstance(job_id, str) or not job_id:
                    raise StateValidationError("NGS receipt lacks exact job identity")
                job = await core_session.get(Job, job_id)
                params = job.params if job is not None and isinstance(job.params, dict) else {}
                if (
                    job is None
                    or job.model_id != "nanopore"
                    or params.get("global_domain_experiment_id")
                    != global_domain_experiment_id
                ):
                    raise StateValidationError(
                        "NGS job receipt is not bound to this Domain Experiment"
                    )
                bound_state_revision_id = params.get("molbio_ngs_state_revision_id")
                if (
                    not isinstance(bound_state_revision_id, str)
                    or not bound_state_revision_id
                    or parent_revision_id is None
                    or bound_state_revision_id != parent_revision_id
                ):
                    raise StateValidationError(
                        "NGS job receipt lacks an exact state binding to the target state revision"
                    )
                bound_state = await session.get(
                    MolBioNGSDomainStateRevision, bound_state_revision_id
                )
                if (
                    bound_state is None
                    or bound_state.global_domain_experiment_id
                    != global_domain_experiment_id
                ):
                    raise StateValidationError(
                        "NGS job state binding is not owned by this Domain Experiment"
                    )
                resolved = (
                    await resolve_ngs_job_receipt(core_session, job_id=job.id)
                    if entity_kind == "ngs_job"
                    else await resolve_ngs_result_manifest_receipt(
                        core_session, job_id=job.id
                    )
                )
                _require_resolved_receipt_authority(
                    authority,
                    resolved,
                    label="NGS job"
                    if entity_kind == "ngs_job"
                    else "NGS result manifest",
                )
                continue

            try:
                observed_generation = int(
                    authority["source_generation_or_revision"]
                )
            except (TypeError, ValueError) as exc:
                raise StateValidationError("ONT receipt generation is invalid") from exc
            resolved = await resolve_ont_instrument_run_receipt(
                core_session,
                run_id=authority["entity_id"],
                observed_generation=observed_generation,
            )
            _require_resolved_receipt_authority(
                authority, resolved, label="ONT instrument run"
            )
            association_rows = list(
                (
                    await session.execute(
                        select(MolBioNGSAuditEvent).where(
                            MolBioNGSAuditEvent.global_domain_experiment_id
                            == global_domain_experiment_id,
                            MolBioNGSAuditEvent.resource_id == member.receipt_id,
                            MolBioNGSAuditEvent.event_type
                            == "molbio_ngs.instrument_run_evidence.attached",
                        )
                    )
                ).scalars()
            )
            if len(association_rows) != 1:
                raise StateValidationError(
                    "ONT receipt lacks an exact server-owned persisted association"
                )
            association = association_rows[0]
            if _digest(association.payload_json) != association.payload_sha256:
                raise StateValidationError("ONT persisted association digest is invalid")
            try:
                association_payload = json.loads(association.payload_json)
            except json.JSONDecodeError as exc:
                raise StateValidationError(
                    "ONT persisted association payload is invalid"
                ) from exc
            expected_association = {
                "schema": "bms.molbio-ngs.instrument-run-evidence-attached.v1",
                "global_domain_experiment_id": global_domain_experiment_id,
                "state_revision_id": association_payload.get("state_revision_id"),
                "receipt_id": member.receipt_id,
                "run_id": authority["entity_id"],
                "observed_generation": observed_generation,
                "observation_sha256": authority["content_digest"],
            }
            if association_payload != expected_association:
                raise StateValidationError("ONT persisted association authority is invalid")
            association_state_id = association_payload["state_revision_id"]
            association_state = await session.get(
                MolBioNGSDomainStateRevision, association_state_id
            )
            if (
                association_state is None
                or association_state.global_domain_experiment_id
                != global_domain_experiment_id
                or (
                    parent_revision_id is not None
                    and association_state.id != parent_revision_id
                )
            ):
                raise StateValidationError(
                    "ONT persisted association is not bound to the target Domain Experiment state"
                )
        except StateValidationError:
            raise
        except (KeyError, ValueError, OSError) as exc:
            raise StateValidationError(
                "domain-owned receipt authoritative source could not be resolved"
            ) from exc


def _member_graph(
    members: Sequence[StateMember],
    receipt_rows: Mapping[str, MolBioNGSMemberReceipt],
) -> list[dict[str, object]]:
    return sorted(
        [
            {
                **_receipt_authority(receipt_rows[member.receipt_id]),
                "role": member.role,
                "ordinal": member.ordinal,
                "sample_revision_id": member.sample_revision_id,
            }
            for member in members
        ],
        key=lambda item: (
            int(item["ordinal"]),
            str(item["receipt_id"]),
            str(item["role"]),
        ),
    )


async def save_state_revision(
    session: AsyncSession,
    *,
    core_session: AsyncSession | None = None,
    global_domain_experiment_id: str,
    global_domain_experiment_revision_id: str,
    payload: Mapping[str, Any],
    members: Sequence[StateMember],
    expected_head_generation: int,
    parent_revision_id: str | None,
    idempotency_key: str,
    created_by: str | None = None,
) -> MolBioNGSDomainStateRevision:
    if not idempotency_key.strip():
        raise StateValidationError("idempotency_key is required")
    receipt_ids = [member.receipt_id for member in members]
    if len(receipt_ids) != len(set(receipt_ids)):
        raise StateValidationError("a member receipt may appear only once in a state revision")
    canonical_payload = _canonical(payload)
    request_json = _canonical(
        {
            "global_domain_experiment_id": global_domain_experiment_id,
            "global_domain_experiment_revision_id": global_domain_experiment_revision_id,
            "payload": json.loads(canonical_payload),
            "members": sorted(
                [
                    {
                        "receipt_id": member.receipt_id,
                        "role": member.role,
                        "ordinal": member.ordinal,
                        "sample_revision_id": member.sample_revision_id,
                    }
                    for member in members
                ],
                key=lambda item: (
                    int(item["ordinal"]), str(item["receipt_id"]), str(item["role"])
                ),
            ),
            "expected_head_generation": expected_head_generation,
            "parent_revision_id": parent_revision_id,
            "created_by": SERVER_OWNED_ACTOR,
        }
    )
    request_sha256 = _digest(request_json)
    scope = f"save-state-revision:{global_domain_experiment_id}"
    revision_id = _id("molbio_ngs_state_revision")
    replay_id = await _reserve_idempotency(
        session,
        scope=scope,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        result_resource_id=revision_id,
    )
    if replay_id is not None:
        revision = await session.get(MolBioNGSDomainStateRevision, replay_id)
        if revision is None:
            raise MolBioNGSServiceError("idempotency claim references missing revision")
        return revision

    receipt_rows = {
        row.receipt_id: row
        for row in (
            await session.execute(
                select(MolBioNGSMemberReceipt).where(
                    MolBioNGSMemberReceipt.receipt_id.in_(receipt_ids)
                )
            )
        ).scalars()
    } if receipt_ids else {}
    _validate_payload_shape(payload)
    sample_revision_ids = list(payload["design"]["sample_revision_ids"])
    sample_rows = {
        row.id: row
        for row in (
            await session.execute(
                select(MolBioNGSSampleRevision).where(
                    MolBioNGSSampleRevision.id.in_(sample_revision_ids)
                )
            )
        ).scalars()
    } if sample_revision_ids else {}
    _validate_payload(
        payload,
        members,
        receipt_rows,
        sample_rows,
        global_domain_experiment_id=global_domain_experiment_id,
    )
    await _validate_domain_owned_receipts(
        session,
        core_session,
        members,
        receipt_rows,
        global_domain_experiment_id=global_domain_experiment_id,
        parent_revision_id=parent_revision_id,
    )
    membership_graph = _member_graph(members, receipt_rows)
    membership_json = _canonical(membership_graph)

    state = await session.get(MolBioNGSDomainState, global_domain_experiment_id)
    if state is None:
        raise DomainStateNotFound("MolBio/NGS state has not been initialized")
    binding = await session.get(MolBioNGSGlobalBinding, state.current_binding_revision_id)
    if (
        binding is None
        or binding.binding_state != "acknowledged"
        or binding.global_domain_experiment_revision_id != global_domain_experiment_revision_id
    ):
        raise GlobalBindingError("exact global Domain Experiment revision is not acknowledged")
    if (
        state.head_generation != expected_head_generation
        or state.current_state_revision_id != parent_revision_id
    ):
        raise RevisionConflict("state head generation or parent revision changed")

    now = _now()
    revision = MolBioNGSDomainStateRevision(
        id=revision_id,
        global_domain_experiment_id=global_domain_experiment_id,
        global_domain_experiment_revision_id=global_domain_experiment_revision_id,
        binding_revision_id=binding.binding_revision_id,
        revision_number=expected_head_generation + 1,
        parent_revision_id=parent_revision_id,
        schema_name=STATE_SCHEMA_NAME,
        schema_version=STATE_SCHEMA_VERSION,
        canonical_payload=canonical_payload,
        payload_sha256=_digest(canonical_payload),
        membership_graph_sha256=_digest(membership_json),
        created_at=now,
        created_by=SERVER_OWNED_ACTOR,
    )
    session.add(revision)
    # Persist the immutable parent before member rows; the circular stable-head
    # reference otherwise prevents SQLAlchemy from deriving a safe insert order.
    await session.flush([revision])
    for member in members:
        session.add(
            MolBioNGSDomainStateMember(
                state_revision_id=revision.id,
                receipt_id=member.receipt_id,
                role=member.role,
                ordinal=member.ordinal,
                sample_revision_id=member.sample_revision_id,
                created_at=now,
            )
        )
    await session.flush()

    next_generation = expected_head_generation + 1
    result = await session.execute(
        update(MolBioNGSDomainState)
        .where(
            MolBioNGSDomainState.global_domain_experiment_id == global_domain_experiment_id,
            MolBioNGSDomainState.head_generation == expected_head_generation,
            MolBioNGSDomainState.current_state_revision_id.is_(parent_revision_id)
            if parent_revision_id is None
            else MolBioNGSDomainState.current_state_revision_id == parent_revision_id,
        )
        .values(
            current_state_revision_id=revision.id,
            head_generation=next_generation,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        raise RevisionConflict("state head changed during revision save")
    await _audit_and_outbox(
        session,
        domain_id=global_domain_experiment_id,
        resource_id=revision.id,
        state_revision_id=revision.id,
        event_type="molbio_ngs.domain_state.revision_saved",
        generation=next_generation,
        payload={
            "schema": "bms.molbio-ngs.domain-state-revision-saved.v1",
            "global_domain_experiment_id": global_domain_experiment_id,
            "global_domain_experiment_revision_id": global_domain_experiment_revision_id,
            "state_revision_id": revision.id,
            "state_revision_number": revision.revision_number,
            "payload_sha256": revision.payload_sha256,
            "membership_graph_sha256": revision.membership_graph_sha256,
        },
        created_by=SERVER_OWNED_ACTOR,
    )
    for member in sorted(
        members,
        key=lambda item: (item.ordinal, item.receipt_id, item.role),
    ):
        authority = _receipt_authority(receipt_rows[member.receipt_id])
        await _audit_and_outbox(
            session,
            domain_id=global_domain_experiment_id,
            resource_id=member.receipt_id,
            state_revision_id=revision.id,
            event_type="molbio_ngs.member_receipt.published",
            generation=next_generation,
            payload={
                "schema": "bms.molbio-ngs.member-receipt-published.v1",
                "receipt_id": member.receipt_id,
                "receipt_kind": authority["entity_kind"],
                "native_entity_id": authority["entity_id"],
                "native_generation": next_generation,
                "receipt_sha256": authority["receipt_sha256"],
            },
            created_by=SERVER_OWNED_ACTOR,
        )
    await session.flush()
    await _complete_idempotency(
        session,
        scope=scope,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        result_resource_id=revision.id,
        response={"revision_id": revision.id},
    )
    await session.flush()
    return revision


async def get_domain_state(
    session: AsyncSession, global_domain_experiment_id: str
) -> MolBioNGSDomainState:
    state = await session.get(MolBioNGSDomainState, global_domain_experiment_id)
    if state is None:
        raise DomainStateNotFound("MolBio/NGS state was not found")
    return state


async def list_domain_states(session: AsyncSession) -> list[MolBioNGSDomainState]:
    return list(
        (
            await session.execute(
                select(MolBioNGSDomainState).order_by(MolBioNGSDomainState.created_at)
            )
        ).scalars()
    )


async def _local_domain_counts(
    session: AsyncSession,
    domain_experiment_id: str,
) -> dict[str, int]:
    sample_count = (
        await session.execute(
            select(func.count(MolBioNGSSample.id)).where(
                MolBioNGSSample.global_domain_experiment_id == domain_experiment_id
            )
        )
    ).scalar_one()
    reference_count = (
        await session.execute(
            select(func.count(MolBioNGSReferenceResource.id)).where(
                MolBioNGSReferenceResource.global_domain_experiment_id
                == domain_experiment_id
            )
        )
    ).scalar_one()
    evidence_count = (
        await session.execute(
            select(func.count(MolBioNGSEvidenceAssessment.evidence_id)).where(
                MolBioNGSEvidenceAssessment.global_domain_experiment_id
                == domain_experiment_id
            )
        )
    ).scalar_one()
    return {
        "samples": int(sample_count),
        "references": int(reference_count),
        "evidence_assessments": int(evidence_count),
    }


async def _domain_experiment_view(
    session: AsyncSession,
    state: MolBioNGSDomainState,
    binding: MolBioNGSGlobalBinding,
) -> dict[str, Any]:
    if (
        binding.global_domain_experiment_id != state.global_domain_experiment_id
        or binding.binding_state != "acknowledged"
    ):
        raise StateIntegrityError("local Domain Experiment binding is not acknowledged")
    return {
        "project_id": binding.project_id,
        "global_experiment_id": binding.global_experiment_id,
        "domain_experiment_id": state.global_domain_experiment_id,
        "global_domain_experiment_revision_id": (
            binding.global_domain_experiment_revision_id
        ),
        "local_state_revision_id": state.current_state_revision_id,
        "local_state_head_generation": state.head_generation,
        "local_counts": await _local_domain_counts(
            session, state.global_domain_experiment_id
        ),
        "availability": {
            "local_state": "available",
            "persisted_global_binding": "acknowledged",
            "global_adapter": "available",
        },
        "created_at": state.created_at,
        "updated_at": state.updated_at,
        "reopen_destination": {
            "surface": "molbio-ngs-domain-experiment",
            "params": {
                "domain_experiment_id": state.global_domain_experiment_id,
            },
        },
    }


async def get_domain_experiment_view(
    session: AsyncSession,
    domain_experiment_id: str,
) -> dict[str, Any]:
    row = (
        await session.execute(
            select(MolBioNGSDomainState, MolBioNGSGlobalBinding)
            .join(
                MolBioNGSGlobalBinding,
                MolBioNGSGlobalBinding.binding_revision_id
                == MolBioNGSDomainState.current_binding_revision_id,
            )
            .where(
                MolBioNGSDomainState.global_domain_experiment_id
                == domain_experiment_id,
                MolBioNGSGlobalBinding.binding_state == "acknowledged",
            )
        )
    ).one_or_none()
    if row is None:
        raise DomainStateNotFound(
            "acknowledged local MolBio/NGS Domain Experiment was not found"
        )
    state, binding = row
    return await _domain_experiment_view(session, state, binding)


async def list_project_domain_experiments(
    session: AsyncSession,
    project_id: str,
) -> list[dict[str, Any]]:
    normalized_project_id = project_id.strip()
    if not normalized_project_id:
        raise StateValidationError("project_id is required")
    rows = list(
        (
            await session.execute(
                select(MolBioNGSDomainState, MolBioNGSGlobalBinding)
                .join(
                    MolBioNGSGlobalBinding,
                    MolBioNGSGlobalBinding.binding_revision_id
                    == MolBioNGSDomainState.current_binding_revision_id,
                )
                .where(
                    MolBioNGSGlobalBinding.project_id == normalized_project_id,
                    MolBioNGSGlobalBinding.binding_state == "acknowledged",
                )
                .order_by(
                    MolBioNGSDomainState.created_at,
                    MolBioNGSDomainState.global_domain_experiment_id,
                )
            )
        ).all()
    )
    return [
        await _domain_experiment_view(session, state, binding)
        for state, binding in rows
    ]


async def get_project_domain_summary(
    session: AsyncSession,
    project_id: str,
) -> dict[str, Any]:
    normalized_project_id = project_id.strip()
    experiments = await list_project_domain_experiments(
        session, normalized_project_id
    )
    totals = {
        "samples": sum(item["local_counts"]["samples"] for item in experiments),
        "references": sum(
            item["local_counts"]["references"] for item in experiments
        ),
        "evidence_assessments": sum(
            item["local_counts"]["evidence_assessments"] for item in experiments
        ),
    }
    return {
        "project_id": normalized_project_id,
        "domain_experiment_count": len(experiments),
        "local_totals": totals,
        "availability": {
            "persisted_global_bindings": "acknowledged_only",
            "global_adapter": "available",
        },
        "reopen_destination": {
            "surface": "molbio-ngs-project-summary",
            "params": {"project_id": normalized_project_id},
        },
    }


async def get_state_revision(
    session: AsyncSession,
    global_domain_experiment_id: str,
    revision_id: str,
) -> MolBioNGSDomainStateRevision:
    revision = await session.get(MolBioNGSDomainStateRevision, revision_id)
    if revision is None or revision.global_domain_experiment_id != global_domain_experiment_id:
        raise DomainStateNotFound("MolBio/NGS state revision was not found")
    return revision


async def list_state_revisions(
    session: AsyncSession, global_domain_experiment_id: str
) -> list[MolBioNGSDomainStateRevision]:
    return list(
        (
            await session.execute(
                select(MolBioNGSDomainStateRevision)
                .where(
                    MolBioNGSDomainStateRevision.global_domain_experiment_id
                    == global_domain_experiment_id
                )
                .order_by(MolBioNGSDomainStateRevision.revision_number.desc())
            )
        ).scalars()
    )


async def list_revision_members(
    session: AsyncSession, revision_id: str
) -> list[MolBioNGSDomainStateMember]:
    return list(
        (
            await session.execute(
                select(MolBioNGSDomainStateMember)
                .where(MolBioNGSDomainStateMember.state_revision_id == revision_id)
                .order_by(MolBioNGSDomainStateMember.ordinal)
            )
        ).scalars()
    )


async def verify_state_revision_integrity(
    session: AsyncSession,
    revision: MolBioNGSDomainStateRevision,
) -> tuple[dict[str, Any], list[dict[str, object]]]:
    """Re-prove payload and complete ordered membership authority before reads."""

    if (
        revision.schema_name != STATE_SCHEMA_NAME
        or revision.schema_version != STATE_SCHEMA_VERSION
        or _digest(revision.canonical_payload) != revision.payload_sha256
    ):
        raise StateIntegrityError("state revision payload authority is invalid")
    try:
        payload = json.loads(revision.canonical_payload)
    except json.JSONDecodeError as exc:
        raise StateIntegrityError("state revision payload is invalid JSON") from exc
    if not isinstance(payload, dict) or _canonical(payload) != revision.canonical_payload:
        raise StateIntegrityError("state revision payload is not canonical JSON")
    try:
        _validate_payload_shape(payload)
    except StateValidationError as exc:
        raise StateIntegrityError("state revision payload schema is invalid") from exc

    member_rows = await list_revision_members(session, revision.id)
    members = [
        StateMember(
            receipt_id=row.receipt_id,
            role=row.role,
            ordinal=row.ordinal,
            sample_revision_id=row.sample_revision_id,
        )
        for row in member_rows
    ]
    receipt_ids = [member.receipt_id for member in members]
    receipt_rows = {
        row.receipt_id: row
        for row in (
            await session.execute(
                select(MolBioNGSMemberReceipt).where(
                    MolBioNGSMemberReceipt.receipt_id.in_(receipt_ids)
                )
            )
        ).scalars()
    } if receipt_ids else {}
    sample_revision_ids = list(payload["design"]["sample_revision_ids"])
    sample_rows = {
        row.id: row
        for row in (
            await session.execute(
                select(MolBioNGSSampleRevision).where(
                    MolBioNGSSampleRevision.id.in_(sample_revision_ids)
                )
            )
        ).scalars()
    } if sample_revision_ids else {}
    try:
        _validate_payload(
            payload,
            members,
            receipt_rows,
            sample_rows,
            global_domain_experiment_id=revision.global_domain_experiment_id,
        )
        graph = _member_graph(members, receipt_rows)
    except StateValidationError as exc:
        raise StateIntegrityError("state revision membership authority is invalid") from exc
    if _digest(_canonical(graph)) != revision.membership_graph_sha256:
        raise StateIntegrityError("state revision membership graph digest is invalid")
    return payload, graph
