"""N1/N2 NGS/MolBio hierarchy binding and cross-store convergence authority."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import (
    ExperimentAggregateHead,
    ExperimentAuditEvent,
    ExperimentDomainAdapterReceipt,
    ExperimentDomainConnectorCommand,
    ExperimentDomainConnectorConflict,
    ExperimentDomainConnectorInbox,
    ExperimentDomainConnectorStream,
    ExperimentExternalEntityReceipt,
    ExperimentLineageEdge,
    ExperimentResource,
    ExperimentRevision,
)
from experiment_operations import ExperimentOperationError, register_external_entity_receipt
from experiment_services import (
    IdempotencyConflict,
    NotFound,
    RevisionConflict,
    ValidationFailure,
    _validate_hierarchy_payload,
    canonical_json,
    new_id,
    now,
)
from molbio_ngs_models import (
    MolBioNGSConnectorAcknowledgement,
    MolBioNGSDomainState,
    MolBioNGSDomainStateRevision,
    MolBioNGSGlobalBinding,
    MolBioNGSOutboxEvent,
    MolBioNGSOutboxStream,
)
from services.global_experiments.adapters import _source_build_revision


BINDING_ADAPTER_ID = "bms.ngs-molbio.domain-binding.adapter.v1"
EVENT_VERIFIER_ID = "bms.ngs-molbio.domain-event.adapter.v1"
SOURCE_STORE_ID = "bms.molbio-ngs.domain-store.v1"
_SCHEMA_ROOT = Path(__file__).resolve().parents[3] / "schemas" / "ngs_molbio"
_HIERARCHY_SCHEMA_ROOT = Path(__file__).resolve().parents[3] / "docs" / "specs" / "schemas"
_SCHEMA_FILES = {
    "bms.ngs-molbio.global-binding-receipt.v1": "global-binding-receipt-v1.schema.json",
    "bms.ngs-molbio.connector-command.v1": "connector-command-v1.schema.json",
    "bms.ngs-molbio.connector-acknowledgement.v1": "connector-acknowledgement-v1.schema.json",
    "bms.ngs-molbio.connector-event.v1": "connector-event-v1.schema.json",
    "bms.ngs-molbio.binding-status.v1": "binding-status-v1.schema.json",
    "bms.molbio-ngs.binding-acknowledged.v1": "binding-acknowledged-v1.schema.json",
    "bms.molbio-ngs.binding-health-published.v1": "binding-health-published-v1.schema.json",
    "bms.molbio-ngs.domain-state-initialized.v1": "domain-state-initialized-v1.schema.json",
    "bms.molbio-ngs.domain-state-revision-saved.v1": "domain-state-revision-saved-v1.schema.json",
    "bms.molbio-ngs.sample-created.v1": "sample-created-v1.schema.json",
    "bms.molbio-ngs.sample-revision-saved.v1": "sample-revision-saved-v1.schema.json",
    "bms.molbio-ngs.reference-created.v1": "reference-created-v1.schema.json",
    "bms.molbio-ngs.reference-revision-saved.v1": "reference-revision-saved-v1.schema.json",
    "bms.molbio-ngs.reference-archived.v1": "reference-archived-v1.schema.json",
    "bms.molbio-ngs.instrument-run-evidence-attached.v1": "instrument-run-evidence-attached-v1.schema.json",
    "bms.molbio-ngs.evidence-assessed.v1": "evidence-assessed-v1.schema.json",
    "bms.molbio-ngs.member-receipt-published.v1": "member-receipt-published-v1.schema.json",
}
_HIERARCHY_SCHEMA_FILES = {
    "workspace": "project-v1.schema.json",
    "experiment": "global-experiment-v1.schema.json",
}
_MAX_RETRIES = 8


class ConnectorError(RuntimeError):
    code = "connector_error"


class ConnectorConflict(ConnectorError):
    code = "connector_conflict"


class ConnectorUnavailable(ConnectorError):
    code = "connector_unavailable"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate(schema_id: str, payload: dict[str, Any]) -> None:
    path = _SCHEMA_ROOT / _SCHEMA_FILES[schema_id]
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConnectorUnavailable(f"package-local N0 schema unavailable: {schema_id}") from exc
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        raise ConnectorConflict(f"{schema_id} validation failed: {errors[0].message}")


def _validate_hierarchy_revision_payload(aggregate_kind: str, payload: dict[str, Any]) -> None:
    """Apply the frozen closed schema plus hierarchy lifecycle contract."""
    if aggregate_kind != "domain_experiment":
        filename = _HIERARCHY_SCHEMA_FILES.get(aggregate_kind)
        if filename is None:
            raise ConnectorConflict(f"unsupported hierarchy revision kind: {aggregate_kind}")
        try:
            schema = json.loads((_HIERARCHY_SCHEMA_ROOT / filename).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConnectorUnavailable(f"hierarchy schema unavailable: {aggregate_kind}") from exc
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload),
            key=lambda item: list(item.absolute_path),
        )
        if errors:
            raise ConnectorConflict(
                f"{aggregate_kind} revision validation failed: {errors[0].message}"
            )
    _validate_hierarchy_payload(aggregate_kind, payload)


def _validate_event_payload(event_type: str, payload: dict[str, Any]) -> None:
    schema_ids = {
        "molbio_ngs.domain_state.initialized": "bms.molbio-ngs.domain-state-initialized.v1",
        "molbio_ngs.domain_state.revision_saved": "bms.molbio-ngs.domain-state-revision-saved.v1",
        "molbio_ngs.sample.created": "bms.molbio-ngs.sample-created.v1",
        "molbio_ngs.sample.revision_saved": "bms.molbio-ngs.sample-revision-saved.v1",
        "molbio_ngs.reference.created": "bms.molbio-ngs.reference-created.v1",
        "molbio_ngs.reference.revision_saved": "bms.molbio-ngs.reference-revision-saved.v1",
        "molbio_ngs.reference.archived": "bms.molbio-ngs.reference-archived.v1",
        "molbio_ngs.instrument_run_evidence.attached": "bms.molbio-ngs.instrument-run-evidence-attached.v1",
        "molbio_ngs.evidence.assessed": "bms.molbio-ngs.evidence-assessed.v1",
        "molbio_ngs.binding.acknowledged": "bms.molbio-ngs.binding-acknowledged.v1",
        "molbio_ngs.binding.health_published": "bms.molbio-ngs.binding-health-published.v1",
        "molbio_ngs.member_receipt.published": "bms.molbio-ngs.member-receipt-published.v1",
    }
    schema_id = schema_ids.get(event_type)
    if schema_id is None:
        raise ConnectorConflict(f"unregistered connector event type: {event_type}")
    if payload.get("schema") != schema_id:
        raise ConnectorConflict(f"connector event payload schema mismatch: {event_type}")
    _validate(schema_id, payload)


def _retry_at(retry_count: int) -> str:
    delay_seconds = min(300, 2 ** min(retry_count, 8))
    return (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)).isoformat()


async def _hierarchy(
    session: AsyncSession,
    *,
    project_id: str,
    global_experiment_id: str,
    domain_id: str,
    expected_domain_revision_id: str,
    allow_archived_domain: bool = False,
) -> tuple[ExperimentAggregateHead, ExperimentAggregateHead, ExperimentAggregateHead, ExperimentRevision, ExperimentRevision, ExperimentRevision]:
    project = await session.get(ExperimentAggregateHead, project_id)
    experiment = await session.get(ExperimentAggregateHead, global_experiment_id)
    domain = await session.get(ExperimentAggregateHead, domain_id)
    if project is None or project.aggregate_kind != "workspace":
        raise NotFound("Project binding authority was not found")
    if experiment is None or experiment.aggregate_kind != "experiment" or experiment.workspace_id != project_id or experiment.parent_id != project_id:
        raise NotFound("Global Experiment binding authority was not found")
    if domain is None or domain.aggregate_kind != "domain_experiment" or domain.workspace_id != project_id or domain.parent_id != global_experiment_id:
        raise NotFound("NGS/MolBio Domain Experiment binding authority was not found")
    if (
        project.lifecycle_state == "archived"
        or experiment.lifecycle_state == "archived"
        or (domain.lifecycle_state == "archived" and not allow_archived_domain)
    ):
        raise ConnectorConflict("archived hierarchy cannot issue a binding receipt")
    if domain.current_revision_id != expected_domain_revision_id:
        raise RevisionConflict("stale_revision")
    if not project.current_revision_id or not experiment.current_revision_id or not domain.current_revision_id:
        raise ConnectorConflict("hierarchy has no immutable current revision")
    project_revision = await session.get(ExperimentRevision, project.current_revision_id)
    experiment_revision = await session.get(ExperimentRevision, experiment.current_revision_id)
    domain_revision = await session.get(ExperimentRevision, domain.current_revision_id)
    if project_revision is None or experiment_revision is None or domain_revision is None:
        raise ConnectorConflict("hierarchy revision authority is unavailable")
    try:
        domain_payload = json.loads(domain_revision.canonical_payload)
    except json.JSONDecodeError as exc:
        raise ConnectorConflict("Domain Experiment revision is invalid JSON") from exc
    if domain_payload.get("schema") != "bms.domain-experiment.v2" or domain_payload.get("domain_kind") != "ngs_molbio" or domain_payload.get("domain_contract_version") != "2":
        raise ConnectorConflict("binding requires an exact NGS/MolBio Domain v2 revision")
    return project, experiment, domain, project_revision, experiment_revision, domain_revision


async def issue_binding_command(
    session: AsyncSession,
    *,
    project_id: str,
    global_experiment_id: str,
    domain_id: str,
    expected_domain_revision_id: str,
    idempotency_key: str,
    operation: str,
    expected_binding_revision_id: str | None = None,
    current_local_binding: MolBioNGSGlobalBinding | None = None,
) -> ExperimentDomainConnectorCommand:
    if operation not in {"initialize", "reverify"}:
        raise ValidationFailure("unsupported connector operation")
    if not idempotency_key or len(idempotency_key) > 255 or any(ord(char) < 33 or ord(char) > 126 for char in idempotency_key):
        raise ValidationFailure("Idempotency-Key must contain 1..255 visible ASCII characters")
    if operation == "reverify" and not expected_binding_revision_id:
        raise ValidationFailure("expected_binding_revision_id is required")
    normalized = {
        "operation": operation,
        "project_id": project_id,
        "global_experiment_id": global_experiment_id,
        "domain_id": domain_id,
        "expected_domain_revision_id": expected_domain_revision_id,
        "expected_binding_revision_id": expected_binding_revision_id,
    }
    request_sha256 = _digest(canonical_json(normalized))
    scope = f"ngs-molbio-binding:{project_id}:{global_experiment_id}:{domain_id}:{operation}"
    existing = await session.scalar(
        select(ExperimentDomainConnectorCommand).where(
            ExperimentDomainConnectorCommand.request_scope == scope,
            ExperimentDomainConnectorCommand.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.idempotency_request_sha256 != request_sha256:
            raise IdempotencyConflict("idempotency_conflict")
        return existing
    project, experiment, domain, project_revision, experiment_revision, domain_revision = await _hierarchy(
        session,
        project_id=project_id,
        global_experiment_id=global_experiment_id,
        domain_id=domain_id,
        expected_domain_revision_id=expected_domain_revision_id,
        allow_archived_domain=operation == "reverify",
    )
    if operation == "initialize":
        revision_command = await session.scalar(
            select(ExperimentDomainConnectorCommand)
            .where(
                ExperimentDomainConnectorCommand.domain_experiment_id == domain_id,
                ExperimentDomainConnectorCommand.domain_revision_id == expected_domain_revision_id,
                ExperimentDomainConnectorCommand.operation == operation,
            )
            .order_by(
                ExperimentDomainConnectorCommand.created_at,
                ExperimentDomainConnectorCommand.command_id,
            )
        )
        if revision_command is not None:
            if revision_command.idempotency_request_sha256 != request_sha256:
                raise IdempotencyConflict("idempotency_conflict")
            return revision_command
    if operation == "reverify":
        current = await session.scalar(
            select(ExperimentDomainConnectorCommand)
            .where(
                ExperimentDomainConnectorCommand.domain_experiment_id == domain_id,
                ExperimentDomainConnectorCommand.status.in_({"applied", "duplicate"}),
            )
            .order_by(ExperimentDomainConnectorCommand.updated_at.desc())
        )
        if current is not None:
            if current.binding_revision_id != expected_binding_revision_id:
                raise RevisionConflict("stale_revision")
        elif (
            current_local_binding is None
            or current_local_binding.binding_revision_id != expected_binding_revision_id
            or current_local_binding.global_domain_experiment_id != domain_id
            or current_local_binding.project_id != project_id
            or current_local_binding.global_experiment_id != global_experiment_id
            or current_local_binding.binding_state != "needs_reverification"
            or current_local_binding.supersedes_binding_revision_id is not None
            or current_local_binding.global_binding_receipt_id is not None
            or current_local_binding.global_binding_receipt_sha256 is not None
            or current_local_binding.connector_command_id is not None
        ):
            raise RevisionConflict("stale_revision")
    verified_at = _utc_now()
    receipt_id = new_id("ngs-molbio-binding-receipt")
    receipt = {
        "schema": "bms.ngs-molbio.global-binding-receipt.v1",
        "receipt_id": receipt_id,
        "project": {
            "id": project_id, "revision_id": project_revision.resource_id,
            "generation": project.head_generation, "digest": project_revision.payload_sha256,
            "reopen_destination": f"/projects/{project_id}",
        },
        "global_experiment": {
            "id": global_experiment_id, "revision_id": experiment_revision.resource_id,
            "generation": experiment.head_generation, "digest": experiment_revision.payload_sha256,
            "reopen_destination": f"/projects/{project_id}?focus={global_experiment_id}",
        },
        "domain_experiment": {
            "id": domain_id, "revision_id": domain_revision.resource_id,
            "generation": domain.head_generation, "digest": domain_revision.payload_sha256,
            "reopen_destination": f"/projects/{project_id}?focus={global_experiment_id}&selected=domain:{domain_id}",
            "lifecycle_state": domain.lifecycle_state, "domain_kind": "ngs_molbio", "domain_contract_version": "2",
        },
        "adapter_id": BINDING_ADAPTER_ID, "adapter_version": "1",
        "verified_at": verified_at, "acknowledgement": {"status": "verified"},
    }
    _validate(receipt["schema"], receipt)
    receipt_json = canonical_json(receipt)
    receipt_sha256 = _digest(receipt_json)
    session.add(ExperimentResource(
        id=receipt_id, kind="domain_adapter_receipt", workspace_id=project_id,
        lifecycle_owner_id=domain_id, created_at=now(),
    ))
    await session.flush()
    session.add(ExperimentDomainAdapterReceipt(
        resource_id=receipt_id, workspace_id=project_id, domain_experiment_id=domain_id,
        adapter_id=BINDING_ADAPTER_ID, adapter_version="1", operation_kind=operation,
        normalized_request_sha256=request_sha256, receipt_json=receipt_json, created_at=now(),
    ))
    command_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"bms:ngs-molbio:connector-command-v1:{scope}:{idempotency_key}:{request_sha256}"))
    command_body = {
        "schema": "bms.ngs-molbio.connector-command.v1", "command_id": command_id,
        "operation": operation, "project_id": project_id, "global_experiment_id": global_experiment_id,
        "domain_id": domain_id, "domain_revision_id": domain_revision.resource_id,
        "domain_revision_sha256": domain_revision.payload_sha256, "global_receipt_id": receipt_id,
        "global_receipt_sha256": receipt_sha256, "idempotency_request_sha256": request_sha256,
        "created_at": verified_at,
    }
    _validate(command_body["schema"], command_body)
    command_json = canonical_json(command_body)
    command = ExperimentDomainConnectorCommand(
        command_id=command_id, request_scope=scope, idempotency_key=idempotency_key,
        idempotency_request_sha256=request_sha256, operation=operation,
        project_id=project_id, project_revision_id=project_revision.resource_id,
        global_experiment_id=global_experiment_id, global_experiment_revision_id=experiment_revision.resource_id,
        domain_experiment_id=domain_id, domain_revision_id=domain_revision.resource_id,
        domain_revision_sha256=domain_revision.payload_sha256,
        prior_binding_revision_id=expected_binding_revision_id, global_receipt_id=receipt_id,
        global_receipt_sha256=receipt_sha256, command_json=command_json,
        command_sha256=_digest(command_json), status="pending", retry_count=0,
        created_at=verified_at, updated_at=verified_at,
    )
    session.add(command)
    await session.flush()
    return command


async def command_for_binding(
    session: AsyncSession, *, project_id: str, global_experiment_id: str, domain_id: str
) -> ExperimentDomainConnectorCommand:
    domain = await session.get(ExperimentAggregateHead, domain_id)
    if domain is None or domain.current_revision_id is None:
        raise NotFound("NGS/MolBio Domain Experiment binding authority was not found")
    await _hierarchy(
        session, project_id=project_id, global_experiment_id=global_experiment_id,
        domain_id=domain_id,
        expected_domain_revision_id=domain.current_revision_id,
    )
    command = await session.scalar(
        select(ExperimentDomainConnectorCommand)
        .where(
            ExperimentDomainConnectorCommand.project_id == project_id,
            ExperimentDomainConnectorCommand.global_experiment_id == global_experiment_id,
            ExperimentDomainConnectorCommand.domain_experiment_id == domain_id,
        )
        .order_by(ExperimentDomainConnectorCommand.created_at.desc(), ExperimentDomainConnectorCommand.command_id.desc())
    )
    if command is None:
        raise NotFound("binding command was not found")
    return command


def binding_status(command: ExperimentDomainConnectorCommand, *, local_state_id: str | None = None, head_generation: int = 0) -> dict[str, Any]:
    _validate_persisted_connector_command(command)
    if command.status in {"applied", "duplicate"}:
        try:
            acknowledgement = json.loads(command.acknowledgement_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ConnectorConflict("terminal connector acknowledgement is invalid") from exc
        if (
            command.binding_revision_id is None
            or command.acknowledgement_id is None
            or _digest(command.acknowledgement_json) != command.acknowledgement_sha256
            or canonical_json(acknowledgement) != command.acknowledgement_json
            or acknowledgement.get("acknowledgement_id") != command.acknowledgement_id
            or acknowledgement.get("command_id") != command.command_id
            or acknowledgement.get("binding_revision_id") != command.binding_revision_id
            or acknowledgement.get("disposition") != command.status
            or acknowledgement.get("accepted_payload_sha256")
            != command.global_receipt_sha256
        ):
            raise ConnectorConflict("terminal connector acknowledgement diverged")
        _validate("bms.ngs-molbio.connector-acknowledgement.v1", acknowledgement)
    provisioning = "ready" if command.status in {"applied", "duplicate"} else "degraded" if command.status == "conflicted" else "provisioning"
    payload = {
        "schema": "bms.ngs-molbio.binding-status.v1",
        "project_id": command.project_id, "project_revision_id": command.project_revision_id,
        "global_experiment_id": command.global_experiment_id,
        "global_experiment_revision_id": command.global_experiment_revision_id,
        "domain_id": command.domain_experiment_id, "domain_revision_id": command.domain_revision_id,
        "binding_revision_id": command.binding_revision_id, "global_receipt_id": command.global_receipt_id,
        "global_receipt_sha256": command.global_receipt_sha256, "connector_command_id": command.command_id,
        "command_state": command.status, "acknowledgement_id": command.acknowledgement_id,
        "acknowledgement_sha256": command.acknowledgement_sha256, "local_state_id": local_state_id,
        "provisioning_state": provisioning, "head_generation": head_generation,
        "created_at": command.created_at, "updated_at": command.updated_at,
    }
    _validate("bms.ngs-molbio.binding-status.v1", payload)
    return payload


def migrated_binding_status(
    binding: MolBioNGSGlobalBinding,
    *,
    project_revision_id: str,
    global_experiment_revision_id: str,
    local_state_id: str,
    head_generation: int,
) -> dict[str, Any]:
    payload = {
        "schema": "bms.ngs-molbio.binding-status.v1",
        "project_id": binding.project_id,
        "project_revision_id": project_revision_id,
        "global_experiment_id": binding.global_experiment_id,
        "global_experiment_revision_id": global_experiment_revision_id,
        "domain_id": binding.global_domain_experiment_id,
        "domain_revision_id": binding.global_domain_experiment_revision_id,
        "binding_revision_id": binding.binding_revision_id,
        "global_receipt_id": binding.global_binding_receipt_id,
        "global_receipt_sha256": binding.global_binding_receipt_sha256,
        "connector_command_id": binding.connector_command_id,
        "command_state": "needs_reverification",
        "acknowledgement_id": None,
        "acknowledgement_sha256": None,
        "local_state_id": local_state_id,
        "provisioning_state": "degraded",
        "head_generation": head_generation,
        "created_at": binding.created_at,
        "updated_at": binding.updated_at or binding.created_at,
    }
    _validate("bms.ngs-molbio.binding-status.v1", payload)
    return payload


async def exact_local_launch_authority(
    global_session: AsyncSession,
    domain_session: AsyncSession,
    *,
    project_id: str,
    global_experiment_id: str,
    domain_id: str,
    expected_domain_revision_id: str,
) -> dict[str, Any]:
    """Re-read both stores and return one exact accepted local launch proof."""
    project = await global_session.get(ExperimentAggregateHead, project_id)
    experiment = await global_session.get(ExperimentAggregateHead, global_experiment_id)
    domain = await global_session.get(ExperimentAggregateHead, domain_id)
    if (
        project is None or project.aggregate_kind != "workspace" or project.lifecycle_state == "archived"
        or not project.current_revision_id
        or experiment is None or experiment.aggregate_kind != "experiment"
        or experiment.workspace_id != project_id or experiment.parent_id != project_id
        or experiment.lifecycle_state == "archived" or not experiment.current_revision_id
        or domain is None or domain.aggregate_kind != "domain_experiment"
        or domain.workspace_id != project_id or domain.parent_id != global_experiment_id
        or domain.lifecycle_state == "archived"
        or domain.current_revision_id != expected_domain_revision_id
    ):
        raise ValidationFailure("replacement_preparation_required")

    project_revision = await global_session.get(ExperimentRevision, project.current_revision_id)
    experiment_revision = await global_session.get(ExperimentRevision, experiment.current_revision_id)
    domain_revision = await global_session.get(ExperimentRevision, domain.current_revision_id)
    if (
        project_revision is None or project_revision.subject_id != project_id
        or project_revision.revision_number != project.head_generation
        or project_revision.schema_name != "bms.project.v1"
        or project_revision.schema_version != "1"
        or _digest(project_revision.canonical_payload) != project_revision.payload_sha256
        or experiment_revision is None or experiment_revision.subject_id != global_experiment_id
        or experiment_revision.revision_number != experiment.head_generation
        or experiment_revision.schema_name != "bms.global-experiment.v1"
        or experiment_revision.schema_version != "1"
        or _digest(experiment_revision.canonical_payload) != experiment_revision.payload_sha256
        or domain_revision is None or domain_revision.subject_id != domain_id
        or domain_revision.revision_number != domain.head_generation
        or domain_revision.schema_name != "bms.domain-experiment.v2"
        or domain_revision.schema_version != "1"
        or _digest(domain_revision.canonical_payload) != domain_revision.payload_sha256
    ):
        raise ValidationFailure("replacement_preparation_required")
    try:
        project_payload = json.loads(project_revision.canonical_payload)
        experiment_payload = json.loads(experiment_revision.canonical_payload)
        domain_payload = json.loads(domain_revision.canonical_payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValidationFailure("replacement_preparation_required") from exc
    if (
        not isinstance(project_payload, dict)
        or canonical_json(project_payload) != project_revision.canonical_payload
        or not isinstance(experiment_payload, dict)
        or canonical_json(experiment_payload) != experiment_revision.canonical_payload
        or not isinstance(domain_payload, dict)
        or canonical_json(domain_payload) != domain_revision.canonical_payload
        or domain_payload.get("schema") != "bms.domain-experiment.v2"
        or domain_payload.get("domain_kind") != "ngs_molbio"
        or domain_payload.get("domain_contract_version") != "2"
        or domain_payload.get("status") == "archived"
    ):
        raise ValidationFailure("replacement_preparation_required")
    try:
        _validate_hierarchy_revision_payload("workspace", project_payload)
        _validate_hierarchy_revision_payload("experiment", experiment_payload)
        _validate_hierarchy_revision_payload("domain_experiment", domain_payload)
    except (ConnectorError, ValidationFailure) as exc:
        raise ValidationFailure("replacement_preparation_required") from exc

    local_state = await domain_session.get(MolBioNGSDomainState, domain_id)
    local_binding = await domain_session.get(
        MolBioNGSGlobalBinding,
        local_state.current_binding_revision_id if local_state is not None else "",
    )
    local_revision = await domain_session.get(
        MolBioNGSDomainStateRevision,
        local_state.current_state_revision_id if local_state is not None else "",
    )
    if (
        local_state is None or local_binding is None or local_revision is None
        or local_binding.binding_state != "acknowledged" or not local_binding.last_verified_at
        or local_binding.global_domain_experiment_id != domain_id
        or local_binding.project_id != project_id
        or local_binding.global_experiment_id != global_experiment_id
        or local_binding.global_domain_experiment_revision_id != domain.current_revision_id
        or local_binding.global_domain_experiment_revision_digest != domain_revision.payload_sha256
        or local_binding.project_generation != str(project.head_generation)
        or local_binding.project_digest != project_revision.payload_sha256
        or local_binding.global_experiment_generation != str(experiment.head_generation)
        or local_binding.global_experiment_digest != experiment_revision.payload_sha256
        or local_state.current_binding_revision_id != local_binding.binding_revision_id
        or local_state.current_state_revision_id != local_revision.id
        or local_state.head_generation != local_revision.revision_number
        or local_revision.global_domain_experiment_id != domain_id
        or local_revision.global_domain_experiment_revision_id != domain.current_revision_id
        or local_revision.binding_revision_id != local_binding.binding_revision_id
        or local_revision.schema_name != "bms.molbio-ngs.domain-state-revision"
        or local_revision.schema_version != "1"
        or _digest(local_revision.canonical_payload) != local_revision.payload_sha256
        or not local_revision.membership_graph_sha256
        or not local_binding.global_binding_receipt_id
        or not local_binding.global_binding_receipt_json
        or not local_binding.global_binding_receipt_sha256
        or _digest(local_binding.global_binding_receipt_json) != local_binding.global_binding_receipt_sha256
        or not local_binding.connector_command_id
    ):
        raise ValidationFailure("replacement_preparation_required")
    try:
        local_payload = json.loads(local_revision.canonical_payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValidationFailure("replacement_preparation_required") from exc
    if (
        not isinstance(local_payload, dict)
        or canonical_json(local_payload) != local_revision.canonical_payload
        or local_payload.get("schema") != "bms.molbio-ngs.domain-state-revision.v1"
    ):
        raise ValidationFailure("replacement_preparation_required")
    from molbio_ngs_services import StateIntegrityError, verify_state_revision_integrity

    try:
        verified_local_payload, _membership_graph = await verify_state_revision_integrity(
            domain_session, local_revision
        )
    except StateIntegrityError as exc:
        raise ValidationFailure("replacement_preparation_required") from exc
    if verified_local_payload != local_payload:
        raise ValidationFailure("replacement_preparation_required")

    receipt = await global_session.get(
        ExperimentDomainAdapterReceipt, local_binding.global_binding_receipt_id
    )
    command = await global_session.get(
        ExperimentDomainConnectorCommand, local_binding.connector_command_id
    )
    local_ack = await domain_session.scalar(
        select(MolBioNGSConnectorAcknowledgement).where(
            MolBioNGSConnectorAcknowledgement.command_id == local_binding.connector_command_id
        )
    )
    if (
        receipt is None or receipt.workspace_id != project_id
        or receipt.domain_experiment_id != domain_id or receipt.adapter_id != BINDING_ADAPTER_ID
        or receipt.receipt_json != local_binding.global_binding_receipt_json
        or _digest(receipt.receipt_json) != local_binding.global_binding_receipt_sha256
        or command is None or command.status not in {"applied", "duplicate"}
        or command.project_id != project_id or command.project_revision_id != project.current_revision_id
        or command.global_experiment_id != global_experiment_id
        or command.global_experiment_revision_id != experiment.current_revision_id
        or command.domain_experiment_id != domain_id or command.domain_revision_id != domain.current_revision_id
        or command.domain_revision_sha256 != domain_revision.payload_sha256
        or command.global_receipt_id != receipt.resource_id
        or command.global_receipt_sha256 != local_binding.global_binding_receipt_sha256
        or command.binding_revision_id != local_binding.binding_revision_id
        or not command.acknowledgement_id or not command.acknowledgement_json
        or not command.acknowledgement_sha256
        or _digest(command.command_json) != command.command_sha256
        or _digest(command.acknowledgement_json) != command.acknowledgement_sha256
        or local_ack is None or local_ack.acknowledgement_id != command.acknowledgement_id
        or local_ack.binding_revision_id != local_binding.binding_revision_id
        or local_ack.disposition not in {"applied", "duplicate"}
        or local_ack.acknowledgement_json != command.acknowledgement_json
        or local_ack.acknowledgement_sha256 != command.acknowledgement_sha256
    ):
        raise ValidationFailure("replacement_preparation_required")
    try:
        binding_receipt = json.loads(receipt.receipt_json)
        command_payload = json.loads(command.command_json)
        acknowledgement_payload = json.loads(local_ack.acknowledgement_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValidationFailure("replacement_preparation_required") from exc
    if (
        not isinstance(binding_receipt, dict)
        or canonical_json(binding_receipt) != receipt.receipt_json
        or binding_receipt.get("schema") != "bms.ngs-molbio.global-binding-receipt.v1"
        or binding_receipt.get("receipt_id") != receipt.resource_id
        or not isinstance(binding_receipt.get("project"), dict)
        or binding_receipt["project"].get("id") != project_id
        or binding_receipt["project"].get("revision_id") != project.current_revision_id
        or binding_receipt["project"].get("generation") != project.head_generation
        or binding_receipt["project"].get("digest") != project_revision.payload_sha256
        or not isinstance(binding_receipt.get("global_experiment"), dict)
        or binding_receipt["global_experiment"].get("id") != global_experiment_id
        or binding_receipt["global_experiment"].get("revision_id") != experiment.current_revision_id
        or binding_receipt["global_experiment"].get("generation") != experiment.head_generation
        or binding_receipt["global_experiment"].get("digest") != experiment_revision.payload_sha256
        or not isinstance(binding_receipt.get("domain_experiment"), dict)
        or binding_receipt["domain_experiment"].get("id") != domain_id
        or binding_receipt["domain_experiment"].get("revision_id") != domain.current_revision_id
        or binding_receipt["domain_experiment"].get("generation") != domain.head_generation
        or binding_receipt["domain_experiment"].get("digest") != domain_revision.payload_sha256
        or binding_receipt["domain_experiment"].get("domain_kind") != "ngs_molbio"
        or binding_receipt["domain_experiment"].get("domain_contract_version") != "2"
        or binding_receipt.get("adapter_id") != BINDING_ADAPTER_ID
        or binding_receipt.get("acknowledgement") != {"status": "verified"}
        or not isinstance(command_payload, dict)
        or canonical_json(command_payload) != command.command_json
        or command_payload.get("schema") != "bms.ngs-molbio.connector-command.v1"
        or command_payload.get("command_id") != command.command_id
        or command_payload.get("project_id") != project_id
        or command_payload.get("global_experiment_id") != global_experiment_id
        or command_payload.get("domain_id") != domain_id
        or command_payload.get("domain_revision_id") != domain.current_revision_id
        or command_payload.get("domain_revision_sha256") != domain_revision.payload_sha256
        or command_payload.get("global_receipt_id") != receipt.resource_id
        or command_payload.get("global_receipt_sha256") != local_binding.global_binding_receipt_sha256
        or command_payload.get("idempotency_request_sha256") != command.idempotency_request_sha256
        or not isinstance(acknowledgement_payload, dict)
        or canonical_json(acknowledgement_payload) != local_ack.acknowledgement_json
        or acknowledgement_payload.get("schema") != "bms.ngs-molbio.connector-acknowledgement.v1"
        or acknowledgement_payload.get("acknowledgement_id") != local_ack.acknowledgement_id
        or acknowledgement_payload.get("command_id") != command.command_id
        or acknowledgement_payload.get("binding_revision_id") != local_binding.binding_revision_id
        or acknowledgement_payload.get("disposition") != local_ack.disposition
        or acknowledgement_payload.get("accepted_payload_sha256") != local_binding.global_binding_receipt_sha256
        or acknowledgement_payload.get("reason_code") is not None
    ):
        raise ValidationFailure("replacement_preparation_required")

    state_event = await global_session.scalar(
        select(ExperimentDomainConnectorInbox).where(
            ExperimentDomainConnectorInbox.domain_experiment_id == domain_id,
            ExperimentDomainConnectorInbox.binding_revision_id == local_binding.binding_revision_id,
            ExperimentDomainConnectorInbox.state_revision_id == local_revision.id,
            ExperimentDomainConnectorInbox.event_type == "molbio_ngs.domain_state.revision_saved",
            ExperimentDomainConnectorInbox.source_generation == local_state.head_generation,
            ExperimentDomainConnectorInbox.disposition == "applied",
            ExperimentDomainConnectorInbox.applied_at.is_not(None),
        )
    )
    try:
        state_event_payload = json.loads(state_event.payload_json) if state_event is not None else None
        state_event_envelope = json.loads(state_event.envelope_json) if state_event is not None else None
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValidationFailure("replacement_preparation_required") from exc
    if (
        state_event is None or not isinstance(state_event_payload, dict)
        or not isinstance(state_event_envelope, dict)
        or state_event.source_store_id != SOURCE_STORE_ID
        or not state_event.payload_json or not state_event.payload_sha256
        or not state_event.envelope_json or not state_event.envelope_sha256
        or _digest(state_event.envelope_json) != state_event.envelope_sha256
        or canonical_json(state_event_envelope) != state_event.envelope_json
        or state_event_envelope != _inbox_event_envelope(state_event)
        or _digest(state_event.payload_json) != state_event.payload_sha256
        or canonical_json(state_event_payload) != state_event.payload_json
        or state_event_payload.get("schema") != "bms.molbio-ngs.domain-state-revision-saved.v1"
        or state_event_payload.get("global_domain_experiment_id") != domain_id
        or state_event_payload.get("global_domain_experiment_revision_id") != domain.current_revision_id
        or state_event_payload.get("state_revision_id") != local_revision.id
        or state_event_payload.get("state_revision_number") != local_revision.revision_number
        or state_event_payload.get("payload_sha256") != local_revision.payload_sha256
        or state_event_payload.get("membership_graph_sha256") != local_revision.membership_graph_sha256
    ):
        raise ValidationFailure("replacement_preparation_required")
    try:
        _validate_event_payload(state_event.event_type, state_event_payload)
        _validate("bms.ngs-molbio.connector-event.v1", state_event_envelope)
    except ConnectorError as exc:
        raise ValidationFailure("replacement_preparation_required") from exc

    return {
        "project_id": project_id,
        "project_revision_id": project_revision.resource_id,
        "project_revision_generation": project.head_generation,
        "project_revision_sha256": project_revision.payload_sha256,
        "global_experiment_id": global_experiment_id,
        "global_experiment_revision_id": experiment_revision.resource_id,
        "global_experiment_revision_generation": experiment.head_generation,
        "global_experiment_revision_sha256": experiment_revision.payload_sha256,
        "domain_id": domain_id,
        "domain_revision_id": domain.current_revision_id,
        "domain_revision_generation": domain.head_generation,
        "domain_revision_sha256": domain_revision.payload_sha256,
        "binding_revision_id": local_binding.binding_revision_id,
        "binding_generation": local_binding.revision_number,
        "connector_command_id": command.command_id,
        "connector_command_sha256": command.command_sha256,
        "connector_acknowledgement_id": command.acknowledgement_id,
        "connector_acknowledgement_sha256": command.acknowledgement_sha256,
        "global_binding_receipt_id": local_binding.global_binding_receipt_id,
        "global_binding_receipt_sha256": local_binding.global_binding_receipt_sha256,
        "local_state_revision_id": local_revision.id,
        "local_state_generation": local_state.head_generation,
        "local_state_payload_sha256": local_revision.payload_sha256,
        "local_state_membership_graph_sha256": local_revision.membership_graph_sha256,
        "global_state_event_id": state_event.event_id,
        "global_state_event_sha256": state_event.envelope_sha256,
    }


def _validate_persisted_connector_command(
    command: ExperimentDomainConnectorCommand,
) -> None:
    try:
        payload = json.loads(command.command_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ConnectorConflict("persisted connector command is invalid") from exc
    if (
        _digest(command.command_json) != command.command_sha256
        or canonical_json(payload) != command.command_json
        or payload.get("schema") != "bms.ngs-molbio.connector-command.v1"
        or payload.get("command_id") != command.command_id
        or payload.get("operation") != command.operation
        or payload.get("project_id") != command.project_id
        or payload.get("global_experiment_id") != command.global_experiment_id
        or payload.get("domain_id") != command.domain_experiment_id
        or payload.get("domain_revision_id") != command.domain_revision_id
        or payload.get("domain_revision_sha256") != command.domain_revision_sha256
        or payload.get("global_receipt_id") != command.global_receipt_id
        or payload.get("global_receipt_sha256") != command.global_receipt_sha256
        or payload.get("idempotency_request_sha256")
        != command.idempotency_request_sha256
        or payload.get("created_at") != command.created_at
    ):
        raise ConnectorConflict(
            "persisted connector command diverged from its immutable columns"
        )
    _validate("bms.ngs-molbio.connector-command.v1", payload)


async def _claim_command(session: AsyncSession, worker_id: str, lease_seconds: int = 30) -> ExperimentDomainConnectorCommand | None:
    timestamp = datetime.now(timezone.utc)
    row = await session.scalar(
        select(ExperimentDomainConnectorCommand)
        .where(or_(
            ExperimentDomainConnectorCommand.status == "pending",
            and_(ExperimentDomainConnectorCommand.status == "retryable", or_(ExperimentDomainConnectorCommand.next_retry_at.is_(None), ExperimentDomainConnectorCommand.next_retry_at <= timestamp.isoformat())),
            and_(ExperimentDomainConnectorCommand.status == "leased", ExperimentDomainConnectorCommand.lease_expires_at < timestamp.isoformat()),
        ))
        .order_by(ExperimentDomainConnectorCommand.created_at, ExperimentDomainConnectorCommand.command_id)
        .limit(1)
    )
    if row is None:
        return None
    token = str(uuid.uuid4())
    result = await session.execute(
        update(ExperimentDomainConnectorCommand)
        .where(
            ExperimentDomainConnectorCommand.command_id == row.command_id,
            ExperimentDomainConnectorCommand.status == row.status,
            ExperimentDomainConnectorCommand.lease_token.is_(row.lease_token)
            if row.lease_token is None
            else ExperimentDomainConnectorCommand.lease_token == row.lease_token,
        )
        .values(status="leased", lease_owner=worker_id, lease_token=token, lease_expires_at=(timestamp + timedelta(seconds=lease_seconds)).isoformat(), updated_at=timestamp.isoformat())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.rollback()
        return None
    await session.commit()
    return await session.get(ExperimentDomainConnectorCommand, row.command_id)


async def _append_local_binding(domain_session: AsyncSession, command: ExperimentDomainConnectorCommand, receipt_json: str) -> tuple[MolBioNGSConnectorAcknowledgement, MolBioNGSDomainState]:
    existing_ack = await domain_session.scalar(select(MolBioNGSConnectorAcknowledgement).where(MolBioNGSConnectorAcknowledgement.command_id == command.command_id))
    if existing_ack is not None:
        state = await domain_session.get(MolBioNGSDomainState, command.domain_experiment_id)
        binding = await domain_session.get(
            MolBioNGSGlobalBinding, existing_ack.binding_revision_id
        )
        try:
            acknowledgement_payload = json.loads(existing_ack.acknowledgement_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ConnectorConflict(
                "persisted connector acknowledgement is invalid"
            ) from exc
        if (
            state is None
            or binding is None
            or binding.global_domain_experiment_id != command.domain_experiment_id
            or binding.project_id != command.project_id
            or binding.global_experiment_id != command.global_experiment_id
            or binding.global_domain_experiment_revision_id != command.domain_revision_id
            or binding.global_domain_experiment_revision_digest
            != command.domain_revision_sha256
            or binding.global_binding_receipt_id != command.global_receipt_id
            or binding.global_binding_receipt_json != receipt_json
            or binding.global_binding_receipt_sha256
            != command.global_receipt_sha256
            or binding.connector_command_id != command.command_id
            or binding.binding_state != "acknowledged"
            or existing_ack.disposition not in {"applied", "duplicate"}
            or _digest(existing_ack.acknowledgement_json)
            != existing_ack.acknowledgement_sha256
            or canonical_json(acknowledgement_payload)
            != existing_ack.acknowledgement_json
            or acknowledgement_payload.get("schema")
            != "bms.ngs-molbio.connector-acknowledgement.v1"
            or acknowledgement_payload.get("acknowledgement_id")
            != existing_ack.acknowledgement_id
            or acknowledgement_payload.get("command_id") != command.command_id
            or acknowledgement_payload.get("event_id") is not None
            or acknowledgement_payload.get("binding_revision_id")
            != existing_ack.binding_revision_id
            or acknowledgement_payload.get("disposition")
            != existing_ack.disposition
            or acknowledgement_payload.get("accepted_payload_sha256")
            != command.global_receipt_sha256
            or acknowledgement_payload.get("last_applied_stream_generation")
            is not None
            or acknowledgement_payload.get("reason_code") is not None
        ):
            raise ConnectorConflict(
                "persisted connector acknowledgement diverged from command authority"
            )
        _validate(
            "bms.ngs-molbio.connector-acknowledgement.v1",
            acknowledgement_payload,
        )
        return existing_ack, state
    receipt = json.loads(receipt_json)
    _validate("bms.ngs-molbio.global-binding-receipt.v1", receipt)
    if _digest(receipt_json) != command.global_receipt_sha256 or receipt["receipt_id"] != command.global_receipt_id:
        raise ConnectorConflict("global binding receipt digest diverged")
    state = await domain_session.get(MolBioNGSDomainState, command.domain_experiment_id)
    created_state = state is None
    current: MolBioNGSGlobalBinding | None = None
    if state is not None:
        current = await domain_session.get(MolBioNGSGlobalBinding, state.current_binding_revision_id)
    if command.operation == "initialize" and current is not None and current.binding_state == "acknowledged" and current.global_domain_experiment_revision_id == command.domain_revision_id:
        disposition = "duplicate"
        binding = current
    else:
        if command.operation == "reverify" and (current is None or current.binding_revision_id != command.prior_binding_revision_id):
            raise ConnectorConflict("stale local binding revision")
        binding_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"bms:molbio-ngs:binding-revision-v1:{command.command_id}:{command.global_receipt_sha256}"))
        binding = await domain_session.get(MolBioNGSGlobalBinding, binding_id)
        if binding is None:
            number = 1 if current is None else current.revision_number + 1
            binding = MolBioNGSGlobalBinding(
                binding_revision_id=binding_id, global_domain_experiment_id=command.domain_experiment_id,
                revision_number=number, supersedes_binding_revision_id=current.binding_revision_id if current else None,
                global_domain_experiment_revision_id=command.domain_revision_id,
                global_domain_experiment_revision_digest=command.domain_revision_sha256,
                project_id=command.project_id, project_generation=str(receipt["project"]["generation"]), project_digest=receipt["project"]["digest"],
                project_receipt_id=command.global_receipt_id, project_reopen_destination=canonical_json({"uri": receipt["project"]["reopen_destination"]}), project_acknowledgement='{"status":"verified"}',
                global_experiment_id=command.global_experiment_id, global_experiment_generation=str(receipt["global_experiment"]["generation"]), global_experiment_digest=receipt["global_experiment"]["digest"],
                global_experiment_receipt_id=command.global_receipt_id, global_experiment_reopen_destination=canonical_json({"uri": receipt["global_experiment"]["reopen_destination"]}), global_experiment_acknowledgement='{"status":"verified"}',
                global_binding_receipt_id=command.global_receipt_id, global_binding_receipt_json=receipt_json,
                global_binding_receipt_sha256=command.global_receipt_sha256, connector_command_id=command.command_id,
                binding_state="acknowledged", last_verified_at=receipt["verified_at"], created_at=_utc_now(), updated_at=_utc_now(),
            )
            domain_session.add(binding)
            await domain_session.flush()
            if state is None:
                state = MolBioNGSDomainState(
                    global_domain_experiment_id=command.domain_experiment_id, current_state_revision_id=None,
                    current_binding_revision_id=binding_id, head_generation=0, created_at=_utc_now(), updated_at=_utc_now(),
                )
                domain_session.add(state)
            else:
                state.current_binding_revision_id = binding_id
                state.updated_at = _utc_now()
            disposition = "applied"
    ack_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"bms:ngs-molbio:connector-ack-v1:{command.command_id}:{binding.binding_revision_id}"))
    ack_payload = {
        "schema": "bms.ngs-molbio.connector-acknowledgement.v1", "acknowledgement_id": ack_id,
        "command_id": command.command_id, "event_id": None, "binding_revision_id": binding.binding_revision_id,
        "disposition": disposition, "accepted_payload_sha256": command.global_receipt_sha256,
        "last_applied_stream_generation": None, "reason_code": None, "acknowledged_at": _utc_now(),
    }
    _validate(ack_payload["schema"], ack_payload)
    ack_json = canonical_json(ack_payload)
    acknowledgement = MolBioNGSConnectorAcknowledgement(
        acknowledgement_id=ack_id, command_id=command.command_id, binding_revision_id=binding.binding_revision_id,
        disposition=disposition, acknowledgement_json=ack_json, acknowledgement_sha256=_digest(ack_json), created_at=ack_payload["acknowledged_at"],
    )
    domain_session.add(acknowledgement)
    if created_state:
        initialized_payload = {
            "schema": "bms.molbio-ngs.domain-state-initialized.v1",
            "global_domain_experiment_id": command.domain_experiment_id,
            "global_domain_experiment_revision_id": command.domain_revision_id,
            "global_domain_experiment_revision_digest": command.domain_revision_sha256,
            "project_id": command.project_id,
            "project_generation": int(receipt["project"]["generation"]),
            "project_digest": receipt["project"]["digest"],
            "global_experiment_id": command.global_experiment_id,
            "global_experiment_generation": int(receipt["global_experiment"]["generation"]),
            "global_experiment_digest": receipt["global_experiment"]["digest"],
        }
        await emit_ordered_event(
            domain_session,
            domain_id=command.domain_experiment_id,
            binding_revision_id=binding.binding_revision_id,
            event_stream="binding",
            event_type="molbio_ngs.domain_state.initialized",
            payload=initialized_payload,
            source_generation=0,
        )
    event_payload = {
        "schema": "bms.molbio-ngs.binding-acknowledged.v1", "binding_revision_id": binding.binding_revision_id,
        "binding_revision_number": binding.revision_number, "binding_receipt_sha256": command.global_receipt_sha256,
    }
    _validate(event_payload["schema"], event_payload)
    await emit_ordered_event(domain_session, domain_id=command.domain_experiment_id, binding_revision_id=binding.binding_revision_id, event_stream="binding", event_type="molbio_ngs.binding.acknowledged", payload=event_payload, source_generation=binding.revision_number)
    health_payload = {
        "schema": "bms.molbio-ngs.binding-health-published.v1",
        "binding_revision_id": binding.binding_revision_id,
        "binding_revision_number": binding.revision_number,
        "health_state": "ready",
        "observed_at": _utc_now(),
    }
    _validate_event_payload("molbio_ngs.binding.health_published", health_payload)
    await emit_ordered_event(
        domain_session,
        domain_id=command.domain_experiment_id,
        binding_revision_id=binding.binding_revision_id,
        event_stream="binding",
        event_type="molbio_ngs.binding.health_published",
        payload=health_payload,
        source_generation=binding.revision_number,
    )
    await domain_session.commit()
    return acknowledgement, state


async def emit_ordered_event(
    session: AsyncSession, *, domain_id: str, binding_revision_id: str, event_stream: str,
    event_type: str, payload: dict[str, Any], source_generation: int | None, state_revision_id: str | None = None,
) -> MolBioNGSOutboxEvent:
    timestamp = _utc_now()
    _validate_event_payload(event_type, payload)
    await session.execute(
        sqlite_insert(MolBioNGSOutboxStream).values(
            global_domain_experiment_id=domain_id, binding_revision_id=binding_revision_id,
            event_stream=event_stream, next_stream_generation=1, updated_at=timestamp,
        ).on_conflict_do_nothing(index_elements=["global_domain_experiment_id", "binding_revision_id", "event_stream"])
    )
    stream = await session.get(MolBioNGSOutboxStream, (domain_id, binding_revision_id, event_stream))
    if stream is None:
        raise ConnectorConflict("outbox stream allocation failed")
    generation = stream.next_stream_generation
    stream.next_stream_generation += 1
    stream.updated_at = timestamp
    payload_json = canonical_json(payload)
    event = MolBioNGSOutboxEvent(
        id=str(uuid.uuid4()), global_domain_experiment_id=domain_id, state_revision_id=state_revision_id,
        binding_revision_id=binding_revision_id, event_type=event_type, event_stream=event_stream,
        stream_generation=generation, source_generation=source_generation, payload_json=payload_json,
        payload_sha256=_digest(payload_json), status="pending", retry_count=0, created_at=timestamp, updated_at=timestamp,
    )
    session.add(event)
    await session.flush()
    return event


async def _finalize_command(global_session: AsyncSession, command: ExperimentDomainConnectorCommand, ack: MolBioNGSConnectorAcknowledgement, state: MolBioNGSDomainState) -> bool:
    result = await global_session.execute(
        update(ExperimentDomainConnectorCommand)
        .where(
            ExperimentDomainConnectorCommand.command_id == command.command_id,
            ExperimentDomainConnectorCommand.status == "leased",
            ExperimentDomainConnectorCommand.lease_token == command.lease_token,
        )
        .values(
            status=ack.disposition, lease_owner=None, lease_token=None, lease_expires_at=None,
            acknowledgement_id=ack.acknowledgement_id, acknowledgement_json=ack.acknowledgement_json,
            acknowledgement_sha256=ack.acknowledgement_sha256, binding_revision_id=ack.binding_revision_id,
            next_retry_at=None, last_error=None, updated_at=_utc_now(),
        ).execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await global_session.rollback()
        return False
    await global_session.commit()
    return True


async def process_command_once(global_session: AsyncSession, domain_session: AsyncSession, *, worker_id: str) -> int:
    command = await _claim_command(global_session, worker_id)
    if command is None:
        return 0
    try:
        _validate_persisted_connector_command(command)
        receipt_row = await global_session.get(ExperimentDomainAdapterReceipt, command.global_receipt_id)
        if receipt_row is None or receipt_row.adapter_id != BINDING_ADAPTER_ID or _digest(receipt_row.receipt_json) != command.global_receipt_sha256:
            raise ConnectorConflict("persisted global receipt is missing or divergent")
        ack, state = await _append_local_binding(domain_session, command, receipt_row.receipt_json)
        return 1 if await _finalize_command(global_session, command, ack, state) else 0
    except ConnectorConflict as exc:
        await domain_session.rollback()
        conflict = canonical_json({"code": exc.code, "message": str(exc), "command_id": command.command_id})
        await global_session.execute(
            update(ExperimentDomainConnectorCommand)
            .where(ExperimentDomainConnectorCommand.command_id == command.command_id, ExperimentDomainConnectorCommand.status == "leased", ExperimentDomainConnectorCommand.lease_token == command.lease_token)
            .values(status="conflicted", lease_owner=None, lease_token=None, lease_expires_at=None,
                    next_retry_at=None, conflict_json=conflict, conflict_sha256=_digest(conflict),
                    last_error=str(exc)[:1024], updated_at=_utc_now())
        )
        await global_session.commit()
        return 1
    except Exception as exc:
        await domain_session.rollback()
        retry_count = command.retry_count + 1
        terminal = retry_count >= _MAX_RETRIES
        conflict = canonical_json({
            "code": "connector_retry_exhausted",
            "message": str(exc)[:1024],
            "command_id": command.command_id,
            "retry_count": retry_count,
        }) if terminal else None
        await global_session.execute(
            update(ExperimentDomainConnectorCommand)
            .where(
                ExperimentDomainConnectorCommand.command_id == command.command_id,
                ExperimentDomainConnectorCommand.status == "leased",
                ExperimentDomainConnectorCommand.lease_token == command.lease_token,
            )
            .values(
                status="conflicted" if terminal else "retryable",
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                retry_count=retry_count,
                next_retry_at=None if terminal else _retry_at(retry_count),
                last_error=str(exc)[:1024],
                conflict_json=conflict,
                conflict_sha256=_digest(conflict) if conflict is not None else None,
                updated_at=_utc_now(),
            )
        )
        await global_session.commit()
        return 1


def _event_envelope(event: MolBioNGSOutboxEvent) -> dict[str, Any]:
    return {
        "schema": "bms.ngs-molbio.connector-event.v1", "source_store_id": SOURCE_STORE_ID,
        "event_id": event.id, "event_type": event.event_type,
        "global_domain_experiment_id": event.global_domain_experiment_id,
        "binding_revision_id": event.binding_revision_id, "state_revision_id": event.state_revision_id,
        "event_stream": event.event_stream, "stream_generation": event.stream_generation,
        "source_generation": event.source_generation, "payload": json.loads(event.payload_json),
        "payload_sha256": event.payload_sha256, "occurred_at": event.created_at,
    }


async def _claim_outbox(session: AsyncSession, worker_id: str, lease_seconds: int = 30) -> MolBioNGSOutboxEvent | None:
    timestamp = datetime.now(timezone.utc)
    event = await session.scalar(
        select(MolBioNGSOutboxEvent).where(or_(
            MolBioNGSOutboxEvent.status == "pending",
            and_(MolBioNGSOutboxEvent.status == "retryable_error", or_(MolBioNGSOutboxEvent.next_retry_at.is_(None), MolBioNGSOutboxEvent.next_retry_at <= timestamp.isoformat())),
            and_(MolBioNGSOutboxEvent.status == "leased", MolBioNGSOutboxEvent.lease_expires_at < timestamp.isoformat()),
        )).order_by(MolBioNGSOutboxEvent.created_at, MolBioNGSOutboxEvent.id).limit(1)
    )
    if event is None:
        return None
    token = str(uuid.uuid4())
    result = await session.execute(
        update(MolBioNGSOutboxEvent).where(
            MolBioNGSOutboxEvent.id == event.id,
            MolBioNGSOutboxEvent.status == event.status,
            MolBioNGSOutboxEvent.lease_token.is_(event.lease_token)
            if event.lease_token is None
            else MolBioNGSOutboxEvent.lease_token == event.lease_token,
        )
        .values(status="leased", lease_owner=worker_id, lease_token=token, lease_expires_at=(timestamp + timedelta(seconds=lease_seconds)).isoformat(), updated_at=timestamp.isoformat())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.rollback()
        return None
    await session.commit()
    return await session.get(MolBioNGSOutboxEvent, event.id)


def _event_ack(
    *,
    event_id: str,
    binding_revision_id: str,
    payload_sha256: str,
    disposition: str,
    last_applied_generation: int,
    acknowledged_at: datetime,
    reason_code: str | None = None,
) -> dict[str, Any]:
    seed = f"{event_id}:{payload_sha256}:{disposition}:{last_applied_generation}"
    acknowledgement = {
        "schema": "bms.ngs-molbio.connector-acknowledgement.v1",
        "acknowledgement_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"bms:ngs-molbio:event-ack:{seed}")),
        "command_id": None,
        "event_id": event_id,
        "binding_revision_id": binding_revision_id,
        "disposition": disposition,
        "accepted_payload_sha256": payload_sha256,
        "last_applied_stream_generation": last_applied_generation,
        "reason_code": reason_code,
        "acknowledged_at": acknowledged_at.isoformat(),
    }
    _validate(acknowledgement["schema"], acknowledgement)
    return acknowledgement


def _event_authority_spec(
    *, event_type: str, payload: dict[str, Any], payload_sha256: str, domain_id: str,
) -> dict[str, str] | None:
    if event_type in {
        "molbio_ngs.binding.acknowledged",
        "molbio_ngs.binding.health_published",
        "molbio_ngs.domain_state.initialized",
    }:
        return None
    if event_type == "molbio_ngs.domain_state.revision_saved":
        return {
            "entity_kind": "ngs_molbio_state_revision", "entity_id": str(payload["state_revision_id"]),
            "entity_revision_id": str(payload["state_revision_number"]), "content_digest": str(payload["payload_sha256"]),
            "availability": "available", "edge_mode": "publishes",
            "reopen_uri": f"/molbio-ngs/domain-experiments/{domain_id}?state_revision_id={payload['state_revision_id']}",
        }
    if event_type in {"molbio_ngs.sample.created", "molbio_ngs.sample.revision_saved"}:
        return {
            "entity_kind": "ngs_molbio_sample_revision", "entity_id": str(payload["sample_revision_id"]),
            "entity_revision_id": str(payload["sample_revision_number"]), "content_digest": str(payload["payload_sha256"]),
            "availability": "available", "edge_mode": "publishes",
            "reopen_uri": f"/api/molbio-ngs/experiments/{domain_id}/samples/{payload['sample_id']}/revisions/{payload['sample_revision_id']}",
        }
    if event_type in {"molbio_ngs.reference.created", "molbio_ngs.reference.revision_saved"}:
        return {
            "entity_kind": "ngs_molbio_reference_revision", "entity_id": str(payload["reference_revision_id"]),
            "entity_revision_id": str(payload["reference_revision_number"]), "content_digest": str(payload["canonical_fasta_sha256"]),
            "availability": "available", "edge_mode": "publishes",
            "reopen_uri": f"/api/molbio-ngs/references/{payload['reference_id']}/revisions/{payload['reference_revision_id']}",
        }
    if event_type == "molbio_ngs.reference.archived":
        return {
            "entity_kind": "ngs_molbio_reference_lifecycle", "entity_id": str(payload["reference_id"]),
            "entity_revision_id": str(payload["head_generation"]), "content_digest": payload_sha256,
            "availability": "unavailable", "edge_mode": "publishes",
            "reopen_uri": f"/api/molbio-ngs/references/{payload['reference_id']}",
        }
    if event_type == "molbio_ngs.instrument_run_evidence.attached":
        return {
            "entity_kind": "ont_instrument_run", "entity_id": str(payload["run_id"]),
            "entity_revision_id": str(payload["observed_generation"]), "content_digest": str(payload["observation_sha256"]),
            "availability": "available", "edge_mode": "validated_by",
            "reopen_uri": f"/molbio-ngs/domain-experiments/{domain_id}?instrument_run_id={payload['run_id']}",
        }
    if event_type == "molbio_ngs.evidence.assessed":
        return {
            "entity_kind": "ngs_molbio_evidence_assessment", "entity_id": str(payload["evidence_id"]),
            "entity_revision_id": str(payload["evidence_id"]), "content_digest": str(payload["wrapper_sha256"]),
            "availability": "available", "edge_mode": "validated_by",
            "reopen_uri": f"/api/molbio-ngs/experiments/{domain_id}/evidence/{payload['evidence_id']}",
        }
    if event_type == "molbio_ngs.member_receipt.published":
        return {
            "entity_kind": "ngs_molbio_member_receipt", "entity_id": str(payload["receipt_id"]),
            "entity_revision_id": str(payload["native_generation"]), "content_digest": str(payload["receipt_sha256"]),
            "availability": "available", "edge_mode": "publishes",
            "reopen_uri": f"/molbio-ngs/domain-experiments/{domain_id}?member_receipt_id={payload['receipt_id']}",
        }
    raise ConnectorConflict(f"event type {event_type!r} has no global authority materializer")


async def _binding_command_for_event(
    session: AsyncSession, *, domain_id: str, binding_revision_id: str,
) -> tuple[ExperimentDomainConnectorCommand, bool]:
    command = (
        await session.scalars(
            select(ExperimentDomainConnectorCommand)
            .where(
                ExperimentDomainConnectorCommand.domain_experiment_id == domain_id,
                ExperimentDomainConnectorCommand.binding_revision_id == binding_revision_id,
                ExperimentDomainConnectorCommand.status.in_(["applied", "duplicate"]),
            )
            .order_by(ExperimentDomainConnectorCommand.updated_at, ExperimentDomainConnectorCommand.command_id)
            .limit(1)
        )
    ).first()
    if command is not None:
        return command, True
    command = (
        await session.scalars(
            select(ExperimentDomainConnectorCommand)
            .where(
                ExperimentDomainConnectorCommand.domain_experiment_id == domain_id,
                ExperimentDomainConnectorCommand.prior_binding_revision_id == binding_revision_id,
                ExperimentDomainConnectorCommand.status.in_(["applied", "duplicate"]),
            )
            .order_by(ExperimentDomainConnectorCommand.updated_at, ExperimentDomainConnectorCommand.command_id)
            .limit(1)
        )
    ).first()
    if command is None:
        raise ConnectorConflict("event binding revision has no accepted global binding authority")
    return command, False


async def _materialize_event_authority(
    session: AsyncSession,
    *, event_id: str, domain_id: str, binding_revision_id: str, stream_key: str,
    stream_generation: int, event_type: str, payload: dict[str, Any], payload_sha256: str,
    envelope_sha256: str, applied_at: datetime,
) -> None:
    command, _exact_binding = await _binding_command_for_event(
        session, domain_id=domain_id, binding_revision_id=binding_revision_id,
    )
    receipt = await session.get(ExperimentDomainAdapterReceipt, command.global_receipt_id)
    if (
        receipt is None
        or receipt.workspace_id != command.project_id
        or receipt.resource_id != command.global_receipt_id
        or receipt.domain_experiment_id != domain_id
        or receipt.adapter_id != BINDING_ADAPTER_ID
        or _digest(receipt.receipt_json) != command.global_receipt_sha256
    ):
        raise ConnectorConflict("event binding command has no intact global receipt authority")
    try:
        binding_receipt = json.loads(receipt.receipt_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ConnectorConflict("event binding authority receipt is invalid") from exc
    if (
        binding_receipt.get("receipt_id") != command.global_receipt_id
        or binding_receipt.get("adapter_id") != BINDING_ADAPTER_ID
        or binding_receipt.get("project", {}).get("id") != command.project_id
        or binding_receipt.get("global_experiment", {}).get("id") != command.global_experiment_id
        or binding_receipt.get("domain_experiment", {}).get("id") != domain_id
        or binding_receipt.get("domain_experiment", {}).get("revision_id") != command.domain_revision_id
    ):
        raise ConnectorConflict("event binding authority does not match its global aggregate hierarchy")

    spec = _event_authority_spec(
        event_type=event_type, payload=payload, payload_sha256=payload_sha256, domain_id=domain_id,
    )
    authority_receipt: ExperimentExternalEntityReceipt | None = None
    if spec is not None:
        acknowledgement = {
            "schema": "bms.global.external-entity-receipt.v1",
            "store_id": SOURCE_STORE_ID,
            "entity_kind": spec["entity_kind"],
            "entity_id": spec["entity_id"],
            "entity_revision_id": spec["entity_revision_id"],
            "content_digest": spec["content_digest"],
            "contract_digest": envelope_sha256,
            "source_build_revision": _source_build_revision(),
            "verified_at": applied_at.isoformat(),
            "verifier_id": EVENT_VERIFIER_ID,
            "availability": spec["availability"],
            "reopen_uri": spec["reopen_uri"],
            "metadata": {
                "adapter_version": "1", "connector_event_id": event_id, "connector_event_type": event_type,
                "connector_stream_key": stream_key, "connector_stream_generation": stream_generation,
                "connector_envelope_sha256": envelope_sha256, "binding_revision_id": binding_revision_id,
                "global_domain_experiment_id": domain_id,
            },
        }
        if event_type == "molbio_ngs.member_receipt.published":
            acknowledgement["metadata"].update({
                "receipt_kind": payload["receipt_kind"], "native_entity_id": payload["native_entity_id"],
                "native_generation": payload["native_generation"],
            })
        try:
            authority_receipt = await register_external_entity_receipt(
                session, workspace_id=command.project_id, store_id=SOURCE_STORE_ID,
                entity_kind=spec["entity_kind"], entity_id=spec["entity_id"],
                generation_or_revision=spec["entity_revision_id"], content_digest=spec["content_digest"],
                availability=spec["availability"], acknowledgement=acknowledgement,
                verification_authority=EVENT_VERIFIER_ID,
            )
        except (ExperimentOperationError, IdempotencyConflict) as exc:
            raise ConnectorConflict(f"global event authority materialization conflicted: {exc}") from exc

    audit_id = f"ngs-event-audit:{uuid.uuid5(uuid.NAMESPACE_URL, event_id)}"
    audit_payload = canonical_json({
        "event_id": event_id, "event_type": event_type, "binding_revision_id": binding_revision_id,
        "stream_key": stream_key, "stream_generation": stream_generation, "payload_sha256": payload_sha256,
        "envelope_sha256": envelope_sha256,
        "authority_receipt_id": authority_receipt.id if authority_receipt is not None else None,
    })
    existing_audit = await session.get(ExperimentAuditEvent, audit_id)
    if existing_audit is None:
        session.add(ExperimentAuditEvent(
            id=audit_id, workspace_id=command.project_id, resource_id=domain_id,
            event_type="domain_connector_event_applied", generation=stream_generation,
            payload_json=audit_payload, created_at=applied_at.isoformat(),
        ))
    elif (
        existing_audit.workspace_id != command.project_id or existing_audit.resource_id != domain_id
        or existing_audit.generation != stream_generation or existing_audit.payload_json != audit_payload
    ):
        raise ConnectorConflict("global connector event audit identity already has different authority")

    if authority_receipt is not None:
        assert spec is not None
        edge_id = f"ngs-event-edge:{uuid.uuid5(uuid.NAMESPACE_URL, event_id)}"
        edge_metadata = canonical_json({
            "event_id": event_id, "event_type": event_type, "binding_revision_id": binding_revision_id,
            "stream_key": stream_key, "stream_generation": stream_generation, "envelope_sha256": envelope_sha256,
        })
        existing_edge = await session.get(ExperimentLineageEdge, edge_id)
        if existing_edge is None:
            session.add(ExperimentLineageEdge(
                id=edge_id, workspace_id=command.project_id, source_resource_id=domain_id,
                target_resource_id=authority_receipt.resource_id, edge_mode=spec["edge_mode"],
                edge_key=f"connector-event:{event_id}", metadata_json=edge_metadata, created_at=applied_at.isoformat(),
            ))
        elif (
            existing_edge.workspace_id != command.project_id or existing_edge.source_resource_id != domain_id
            or existing_edge.target_resource_id != authority_receipt.resource_id
            or existing_edge.edge_mode != spec["edge_mode"] or existing_edge.metadata_json != edge_metadata
        ):
            raise ConnectorConflict("global connector event lineage identity already has different authority")
    await session.flush()


def _inbox_event_envelope(row: ExperimentDomainConnectorInbox) -> dict[str, Any]:
    return {
        "schema": "bms.ngs-molbio.connector-event.v1", "source_store_id": SOURCE_STORE_ID,
        "event_id": row.event_id, "event_type": row.event_type,
        "global_domain_experiment_id": row.domain_experiment_id,
        "binding_revision_id": row.binding_revision_id, "state_revision_id": row.state_revision_id,
        "event_stream": row.event_stream, "stream_generation": row.stream_generation,
        "source_generation": row.source_generation, "payload": json.loads(row.payload_json),
        "payload_sha256": row.payload_sha256, "occurred_at": row.occurred_at,
    }


def _validated_inbox_acknowledgement(
    row: ExperimentDomainConnectorInbox,
) -> dict[str, Any]:
    try:
        acknowledgement = json.loads(row.acknowledgement_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ConnectorConflict(
            "persisted connector event acknowledgement is invalid"
        ) from exc
    expected_reason = "generation_gap" if row.disposition == "deferred_gap" else None
    last_generation = acknowledgement.get("last_applied_stream_generation")
    if (
        _digest(row.acknowledgement_json) != row.acknowledgement_sha256
        or canonical_json(acknowledgement) != row.acknowledgement_json
        or acknowledgement.get("schema")
        != "bms.ngs-molbio.connector-acknowledgement.v1"
        or acknowledgement.get("command_id") is not None
        or acknowledgement.get("event_id") != row.event_id
        or acknowledgement.get("binding_revision_id") != row.binding_revision_id
        or acknowledgement.get("disposition") != row.disposition
        or acknowledgement.get("accepted_payload_sha256") != row.payload_sha256
        or acknowledgement.get("reason_code") != expected_reason
        or not isinstance(last_generation, int)
        or (
            row.disposition == "applied"
            and last_generation != row.stream_generation
        )
        or (
            row.disposition == "deferred_gap"
            and not 0 <= last_generation < row.stream_generation
        )
    ):
        raise ConnectorConflict(
            "persisted connector event acknowledgement diverged from inbox authority"
        )
    _validate(
        "bms.ngs-molbio.connector-acknowledgement.v1", acknowledgement
    )
    return acknowledgement


async def _apply_inbox_row(
    session: AsyncSession, row: ExperimentDomainConnectorInbox, *, applied_at: datetime,
) -> dict[str, Any]:
    envelope = _inbox_event_envelope(row)
    envelope_sha256 = _digest(canonical_json(envelope))
    if envelope_sha256 != row.envelope_sha256:
        raise ConnectorConflict("deferred event envelope no longer matches its accepted digest")
    await _materialize_event_authority(
        session, event_id=row.event_id, domain_id=row.domain_experiment_id,
        binding_revision_id=row.binding_revision_id, stream_key=row.event_stream,
        stream_generation=row.stream_generation, event_type=row.event_type, payload=envelope["payload"],
        payload_sha256=row.payload_sha256, envelope_sha256=envelope_sha256, applied_at=applied_at,
    )
    acknowledgement = _event_ack(
        event_id=row.event_id, binding_revision_id=row.binding_revision_id,
        payload_sha256=row.payload_sha256, disposition="applied",
        last_applied_generation=row.stream_generation, acknowledged_at=applied_at,
    )
    row.disposition = "applied"
    row.acknowledgement_json = canonical_json(acknowledgement)
    row.acknowledgement_sha256 = _digest(row.acknowledgement_json)
    row.applied_at = applied_at.isoformat()
    return acknowledgement


async def _ingest_event(
    global_session: AsyncSession,
    event: MolBioNGSOutboxEvent,
) -> tuple[dict[str, Any], list[tuple[str, str, dict[str, Any]]]]:
    envelope = _event_envelope(event)
    if (
        canonical_json(envelope["payload"]) != event.payload_json
        or _digest(event.payload_json) != event.payload_sha256
    ):
        raise ConnectorConflict(
            "connector event payload is not canonical or digest-bound"
        )
    _validate(envelope["schema"], envelope)
    _validate_event_payload(event.event_type, envelope["payload"])
    envelope_json = canonical_json(envelope)
    envelope_sha256 = _digest(envelope_json)
    stream_identity = (event.global_domain_experiment_id, event.binding_revision_id, event.event_stream)
    stream = await global_session.get(ExperimentDomainConnectorStream, stream_identity)
    last = stream.last_applied_stream_generation if stream is not None else 0
    existing = await global_session.get(ExperimentDomainConnectorInbox, event.id)
    if existing is not None:
        if (
            existing.source_store_id != SOURCE_STORE_ID
            or existing.domain_experiment_id != event.global_domain_experiment_id
            or existing.binding_revision_id != event.binding_revision_id
            or existing.state_revision_id != event.state_revision_id
            or existing.event_type != event.event_type
            or existing.event_stream != event.event_stream
            or existing.stream_generation != event.stream_generation
            or existing.source_generation != event.source_generation
            or existing.payload_json != event.payload_json
            or existing.payload_sha256 != event.payload_sha256
            or existing.envelope_json != envelope_json
            or existing.envelope_sha256 != envelope_sha256
            or _digest(existing.envelope_json) != existing.envelope_sha256
            or existing.occurred_at != event.created_at
        ):
            raise ConnectorConflict("event ID replay authority diverged")
        if existing.disposition == "conflicted":
            raise ConnectorConflict("event is held by a durable semantic conflict")
        existing_acknowledgement = _validated_inbox_acknowledgement(existing)
        if existing.disposition != "deferred_gap":
            sync_rows = list((await global_session.scalars(
                select(ExperimentDomainConnectorInbox).where(
                    ExperimentDomainConnectorInbox.domain_experiment_id == event.global_domain_experiment_id,
                    ExperimentDomainConnectorInbox.binding_revision_id == event.binding_revision_id,
                    ExperimentDomainConnectorInbox.event_stream == event.event_stream,
                    ExperimentDomainConnectorInbox.disposition == "applied",
                    ExperimentDomainConnectorInbox.applied_at.is_not(None),
                    ExperimentDomainConnectorInbox.received_at != ExperimentDomainConnectorInbox.applied_at,
                ).order_by(ExperimentDomainConnectorInbox.stream_generation)
            )).all())
            return existing_acknowledgement, [
                (
                    row.event_id,
                    row.payload_sha256,
                    _validated_inbox_acknowledgement(row),
                )
                for row in sync_rows
            ]
        if event.stream_generation > last + 1:
            return existing_acknowledgement, []
        if event.stream_generation <= last:
            raise ConnectorConflict("deferred event is behind the accepted stream cursor")
        if stream is None:
            raise ConnectorConflict("deferred event is eligible without an authoritative stream cursor")
        current_row = existing
    else:
        occupied = await global_session.scalar(select(ExperimentDomainConnectorInbox).where(
            ExperimentDomainConnectorInbox.domain_experiment_id == event.global_domain_experiment_id,
            ExperimentDomainConnectorInbox.binding_revision_id == event.binding_revision_id,
            ExperimentDomainConnectorInbox.event_stream == event.event_stream,
            ExperimentDomainConnectorInbox.stream_generation == event.stream_generation,
        ))
        if occupied is not None:
            conflict_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"bms:ngs-molbio:event-conflict:{event.id}:{envelope_sha256}"))
            conflict = canonical_json({
                "code": "stream_generation_conflict", "event_id": event.id,
                "occupied_event_id": occupied.event_id, "stream_generation": event.stream_generation,
            })
            global_session.add(ExperimentDomainConnectorConflict(
                conflict_id=conflict_id, domain_experiment_id=event.global_domain_experiment_id,
                binding_revision_id=event.binding_revision_id, event_stream=event.event_stream,
                stream_generation=event.stream_generation, event_id=event.id,
                conflict_json=conflict, conflict_sha256=_digest(conflict), created_at=_utc_now(),
            ))
            await global_session.commit()
            raise ConnectorConflict("stream generation is occupied by different event authority")
        if event.stream_generation <= last:
            raise ConnectorConflict("event generation is behind the accepted stream cursor without inbox authority")
        disposition = "applied" if event.stream_generation == last + 1 else "deferred_gap"
        acknowledged_at = datetime.now(timezone.utc)
        acknowledgement = _event_ack(
            event_id=event.id, binding_revision_id=event.binding_revision_id,
            payload_sha256=event.payload_sha256, disposition=disposition,
            last_applied_generation=event.stream_generation if disposition == "applied" else last,
            acknowledged_at=acknowledged_at,
            reason_code="generation_gap" if disposition == "deferred_gap" else None,
        )
        acknowledgement_json = canonical_json(acknowledgement)
        current_row = ExperimentDomainConnectorInbox(
            event_id=event.id, source_store_id=SOURCE_STORE_ID,
            domain_experiment_id=event.global_domain_experiment_id,
            binding_revision_id=event.binding_revision_id, state_revision_id=event.state_revision_id,
            event_type=event.event_type, event_stream=event.event_stream,
            stream_generation=event.stream_generation, source_generation=event.source_generation,
            payload_json=event.payload_json, payload_sha256=event.payload_sha256,
            envelope_json=envelope_json, envelope_sha256=envelope_sha256, disposition=disposition,
            acknowledgement_json=acknowledgement_json,
            acknowledgement_sha256=_digest(acknowledgement_json),
            occurred_at=event.created_at, received_at=acknowledged_at.isoformat(),
            applied_at=None,
        )
        global_session.add(current_row)
        if disposition == "deferred_gap":
            await global_session.commit()
            return acknowledgement, []
        if stream is None:
            stream = ExperimentDomainConnectorStream(
                domain_experiment_id=event.global_domain_experiment_id,
                binding_revision_id=event.binding_revision_id, event_stream=event.event_stream,
                last_applied_stream_generation=0, last_event_id=None, last_payload_sha256=None,
                updated_at=acknowledged_at.isoformat(),
            )
            global_session.add(stream)
        await global_session.flush()

    applied_at = datetime.now(timezone.utc)
    acknowledgement = await _apply_inbox_row(global_session, current_row, applied_at=applied_at)
    stream.last_applied_stream_generation = current_row.stream_generation
    stream.last_event_id = current_row.event_id
    stream.last_payload_sha256 = current_row.payload_sha256
    stream.updated_at = applied_at.isoformat()
    drained: list[tuple[str, str, dict[str, Any]]] = []
    await global_session.flush()
    while True:
        next_row = await global_session.scalar(
            select(ExperimentDomainConnectorInbox)
            .where(
                ExperimentDomainConnectorInbox.domain_experiment_id == event.global_domain_experiment_id,
                ExperimentDomainConnectorInbox.binding_revision_id == event.binding_revision_id,
                ExperimentDomainConnectorInbox.event_stream == event.event_stream,
                ExperimentDomainConnectorInbox.stream_generation == stream.last_applied_stream_generation + 1,
                ExperimentDomainConnectorInbox.disposition == "deferred_gap",
            )
            .order_by(ExperimentDomainConnectorInbox.event_id)
            .limit(1)
        )
        if next_row is None:
            break
        drained_at = datetime.now(timezone.utc)
        drained_ack = await _apply_inbox_row(global_session, next_row, applied_at=drained_at)
        stream.last_applied_stream_generation = next_row.stream_generation
        stream.last_event_id = next_row.event_id
        stream.last_payload_sha256 = next_row.payload_sha256
        stream.updated_at = drained_at.isoformat()
        drained.append((next_row.event_id, next_row.payload_sha256, drained_ack))
        await global_session.flush()
    await global_session.commit()
    return acknowledgement, drained


async def process_outbox_once(global_session: AsyncSession, domain_session: AsyncSession, *, worker_id: str) -> int:
    event = await _claim_outbox(domain_session, worker_id)
    if event is None:
        return 0
    try:
        ack, drained_acknowledgements = await _ingest_event(global_session, event)
        ack_json = canonical_json(ack)
        result = await domain_session.execute(
            update(MolBioNGSOutboxEvent).where(
                MolBioNGSOutboxEvent.id == event.id, MolBioNGSOutboxEvent.status == "leased",
                MolBioNGSOutboxEvent.lease_token == event.lease_token,
            ).values(
                status="acknowledged", lease_owner=None, lease_token=None, lease_expires_at=None,
                next_retry_at=None, acknowledgement_json=ack_json,
                acknowledgement_sha256=_digest(ack_json), last_error=None, updated_at=_utc_now(),
            ).execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            await domain_session.rollback()
            return 0
        for drained_event_id, drained_payload_sha256, drained_ack in drained_acknowledgements:
            if drained_event_id == event.id:
                continue
            drained_json = canonical_json(drained_ack)
            drained_result = await domain_session.execute(
                update(MolBioNGSOutboxEvent).where(
                    MolBioNGSOutboxEvent.id == drained_event_id,
                    MolBioNGSOutboxEvent.payload_sha256 == drained_payload_sha256,
                    MolBioNGSOutboxEvent.status == "acknowledged",
                ).values(
                    acknowledgement_json=drained_json,
                    acknowledgement_sha256=_digest(drained_json),
                    last_error=None,
                    updated_at=_utc_now(),
                ).execution_options(synchronize_session=False)
            )
            if drained_result.rowcount != 1:
                raise RuntimeError("drained global event acknowledgement did not converge to its local outbox row")
        await domain_session.commit()
        return 1
    except ConnectorConflict as exc:
        await global_session.rollback()
        await domain_session.rollback()
        conflict = canonical_json({"code": exc.code, "message": str(exc), "event_id": event.id})
        await domain_session.execute(
            update(MolBioNGSOutboxEvent).where(
                MolBioNGSOutboxEvent.id == event.id, MolBioNGSOutboxEvent.status == "leased",
                MolBioNGSOutboxEvent.lease_token == event.lease_token,
            ).values(status="conflict", lease_owner=None, lease_token=None, lease_expires_at=None,
                     next_retry_at=None,
                     conflict_json=conflict, conflict_sha256=_digest(conflict), last_error=str(exc)[:1024], updated_at=_utc_now())
        )
        await domain_session.commit()
        return 1
    except Exception as exc:
        await global_session.rollback()
        await domain_session.rollback()
        retry_count = event.retry_count + 1
        terminal = retry_count >= _MAX_RETRIES
        conflict = canonical_json({
            "code": "connector_retry_exhausted",
            "message": str(exc)[:1024],
            "event_id": event.id,
            "retry_count": retry_count,
        }) if terminal else None
        await domain_session.execute(
            update(MolBioNGSOutboxEvent)
            .where(
                MolBioNGSOutboxEvent.id == event.id,
                MolBioNGSOutboxEvent.status == "leased",
                MolBioNGSOutboxEvent.lease_token == event.lease_token,
            )
            .values(
                status="conflict" if terminal else "retryable_error",
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                retry_count=retry_count,
                next_retry_at=None if terminal else _retry_at(retry_count),
                last_error=str(exc)[:1024],
                conflict_json=conflict,
                conflict_sha256=_digest(conflict) if conflict is not None else None,
                updated_at=_utc_now(),
            )
        )
        await domain_session.commit()
        return 1


async def connector_health(global_session: AsyncSession, domain_session: AsyncSession) -> dict[str, Any]:
    commands = list((await global_session.scalars(select(ExperimentDomainConnectorCommand))).all())
    events = list((await domain_session.scalars(select(MolBioNGSOutboxEvent))).all())
    now_dt = datetime.now(timezone.utc)
    pending_times = [datetime.fromisoformat(item.created_at.replace("Z", "+00:00")) for item in events if item.status in {"pending", "leased", "retryable_error"}]
    deferred_count = int((await global_session.scalar(
        select(func.count(ExperimentDomainConnectorInbox.event_id)).where(
            ExperimentDomainConnectorInbox.disposition == "deferred_gap"
        )
    )) or 0)
    inbox_conflict_count = int((await global_session.scalar(
        select(func.count(ExperimentDomainConnectorConflict.conflict_id))
    )) or 0)
    last_applied_at = await global_session.scalar(
        select(func.max(ExperimentDomainConnectorInbox.applied_at))
    )
    command_times = [
        datetime.fromisoformat(item.created_at.replace("Z", "+00:00"))
        for item in commands
        if item.status in {"pending", "leased", "retryable"}
    ]
    return {
        "schema": "bms.ngs-molbio.connector-health.v1",
        "command_pending_count": sum(item.status in {"pending", "leased", "retryable"} for item in commands),
        "command_conflict_count": sum(item.status == "conflicted" for item in commands),
        "outbox_pending_count": len(pending_times),
        "outbox_conflict_count": sum(item.status == "conflict" for item in events),
        "inbox_deferred_gap_count": deferred_count,
        "inbox_conflict_count": inbox_conflict_count,
        "oldest_command_age_seconds": max((int((now_dt - value).total_seconds()) for value in command_times), default=None),
        "oldest_outbox_age_seconds": max((int((now_dt - value).total_seconds()) for value in pending_times), default=None),
        "last_applied_at": last_applied_at,
    }


__all__ = [
    "ConnectorConflict", "ConnectorError", "ConnectorUnavailable", "binding_status",
    "command_for_binding", "connector_health", "emit_ordered_event", "issue_binding_command",
    "process_command_once", "process_outbox_once",
]
