"""Phase N5 Dataset, admission, bounded-read, and operational authorities."""
from __future__ import annotations

import base64
import asyncio
import hashlib
import json
import re
import subprocess
from types import SimpleNamespace
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import (
    ExperimentAggregateHead,
    ExperimentAuditEvent,
    ExperimentDatasetRevisionMember,
    ExperimentDomainConnectorCommand,
    ExperimentDomainConnectorInbox,
    ExperimentDispatchOutbox,
    ExperimentExternalEntityReceipt,
    ExperimentIdempotencyClaim,
    ExperimentLineageEdge,
    ExperimentLogChunk,
    ExperimentLogStream,
    ExperimentOperationalReceipt,
    ExperimentResource,
    ExperimentResourceAdmission,
    ExperimentResourceAdmissionPolicy,
    ExperimentRevision,
    ExperimentRunAttempt,
    ExperimentRunGroup,
    ExperimentSyncState,
    ExperimentValidation,
    ExperimentWorkflowPreparation,
    ExperimentWorkflowRun,
)
from experiment_services import (
    ExperimentServiceError,
    IdempotencyConflict,
    NotFound,
    RevisionConflict,
    ValidationFailure,
    add_audit_event,
    archive_aggregate,
    canonical_json,
    create_dataset,
    now,
    restore_aggregate,
    save_dataset_revision,
    sha256_text,
)
from services.global_experiments.adapters import AdapterError, registry
from services.ngs_molbio_capabilities import NgsMolBioCapabilityError, contract_registry
from services.ngs_molbio_runtime_status import (
    NgsMolBioRuntimeAuthorityError,
    runtime_implementation_record,
)
from services.payload_ownership_audit import (
    PayloadOwnershipError,
    validate_retained_payload_ownership_receipt,
)
from services.resource_usage_evidence import (
    HISTORICAL_OWNER_ABSENCE_PARAM,
    ResourceUsageEvidenceError,
    attach_pre_spawn_nonexecution_receipt,
    build_historical_owner_absence_receipt,
    build_resource_admission_handoff,
    historical_owner_absence_unit_glob,
    validate_producer_resource_usage_receipt,
)


DATASET_POLICY_VERSION = "bms.ngs-molbio.dataset-kind-registry.v1"
ADMISSION_POLICY_VERSION = "bms.resource-admission-policy.v1"
CPU_THREAD_LIMIT = 24
DRAM_BYTE_LIMIT = 96 * 1024**3
ACTIVE_ADMISSION_STATES = frozenset({"admitted", "queued"})


def _runtime_source_authority(requests: list[dict[str, Any]]) -> tuple[str, str]:
    try:
        record = runtime_implementation_record()
        source_revision = str(record["successor_source_commit"])
        source_tree = str(record["successor_source_tree"])
    except (KeyError, ImportError, OSError, NgsMolBioRuntimeAuthorityError) as exc:
        raise ResourceAdmissionDenied(
            "resource_source_revision_unavailable",
            "package-local successor runtime source authority is required for resource evidence",
            requests,
        ) from exc
    if (
        not re.fullmatch(r"[0-9a-f]{40}", source_revision)
        or not re.fullmatch(r"[0-9a-f]{40}", source_tree)
    ):
        raise ResourceAdmissionDenied(
            "resource_source_revision_unavailable",
            "package-local successor runtime commit/tree authority is invalid",
            requests,
        )
    return source_revision, source_tree


# This operational registry is intentionally NGS/MolBio-only. Protein kinds remain absent and disabled.
DATASET_KINDS: dict[str, dict[str, frozenset[str]]] = {
    "ngs_molbio.molecular_construct_cohort.v1": {
        "bms.molbio.member-molecular-revision.adapter.v1": frozenset({"molecular_expected_construct", "molecular_input_fragment", "molecular_assembly_product", "molecular_pcr_template", "molecular_pcr_product"}),
        "bms.molbio.primer-revision.adapter.v1": frozenset({"molecular_primer_forward", "molecular_primer_reverse"}),
        "bms.molbio.pcr-experiment-revision.adapter.v1": frozenset({"molecular_pcr_experiment"}),
        "bms.molbio.member-operation.adapter.v1": frozenset({"molecular_operation"}),
    },
    "ngs_molbio.sample_cohort.v1": {
        "bms.ngs-molbio.sample-revision.adapter.v1": frozenset({"sample"}),
    },
    "ngs_molbio.reference_comparison_panel_cohort.v1": {
        "bms.ngs.reference-revision.adapter.v1": frozenset({"ngs_reference"}),
        "bms.ngs.comparison-panel.adapter.v1": frozenset({"ngs_comparison_panel"}),
        "bms.ngs.expected-reference-receipt.adapter.v1": frozenset({"ngs_expected_reference"}),
        "bms.ngs.reference-set-reference.adapter.v1": frozenset({"ngs_reference_set"}),
    },
    "ngs_molbio.acquisition_run_input_cohort.v1": {
        "bms.ngs.ont-observation.adapter.v1": frozenset({"ngs_instrument_run"}),
        "bms.ngs.job-reference.adapter.v1": frozenset({"ngs_analysis_job"}),
        "bms.ngs.pooled-assignment-release.adapter.v1": frozenset({"ngs_pooled_assignment_release"}),
    },
    "ngs_molbio.qc_analysis_result_cohort.v1": {
        "bms.ngs.result-manifest.adapter.v1": frozenset({"ngs_analysis_result_manifest"}),
        "bms.ngs.sequence-qc-reference.adapter.v1": frozenset({"ngs_sequence_qc_result"}),
        "bms.ngs.analysis-reference.adapter.v1": frozenset({"ngs_analysis_result"}),
        "bms.ngs.alignment-viewer-reference.adapter.v1": frozenset({"ngs_alignment_result"}),
        "bms.ngs-molbio.evidence-assessment.adapter.v1": frozenset({"ngs_verification_assessment"}),
    },
    "ngs_molbio.saved_review_comparison_cohort.v1": {
        "bms.ngs.result-manifest.adapter.v1": frozenset({"ngs_analysis_result_manifest"}),
        "bms.ngs.comparison-panel.adapter.v1": frozenset({"ngs_comparison_panel"}),
        "bms.ngs-molbio.evidence-assessment.adapter.v1": frozenset({"ngs_verification_assessment"}),
        "bms.ngs.alignment-viewer-reference.adapter.v1": frozenset({"ngs_alignment_result"}),
    },
}


def enabled_dataset_kind_records() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Expose N5 Dataset kinds whose frozen member contracts are implemented."""

    registry_document = contract_registry("dataset")
    enabled: list[dict[str, Any]] = []
    for record in registry_document["entries"]:
        dataset_kind = record.get("dataset_kind")
        declared = {
            member["adapter_id"]: frozenset(member["allowed_roles"])
            for member in (record.get("allowed_members") or [])
        }
        if (
            dataset_kind not in DATASET_KINDS
            or record.get("owner_contract_state") != "closed"
            or "ngs_molbio" not in record.get("allowed_domain_kinds", [])
            or declared != DATASET_KINDS[dataset_kind]
        ):
            continue
        try:
            adapters = [
                (member, registry.get(member["adapter_id"]))
                for member in record["allowed_members"]
            ]
        except AdapterError:
            continue
        if any(
            adapter.entity_kind != member["receipt_kind"]
            for member, adapter in adapters
        ):
            continue
        enabled.append(record)
    return registry_document, enabled


def _enabled_dataset_kind(dataset_kind: str) -> dict[str, Any]:
    try:
        _registry_document, enabled = enabled_dataset_kind_records()
    except NgsMolBioCapabilityError as exc:
        raise ValidationFailure("Dataset kind authority is unavailable") from exc
    record = next(
        (row for row in enabled if row["dataset_kind"] == dataset_kind),
        None,
    )
    if record is None:
        raise ValidationFailure("unsupported_dataset_kind")
    return record


class ResourceAdmissionDenied(ValidationFailure):
    def __init__(self, code: str, reason: str, requests: list[dict[str, Any]]):
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.requests = requests


class InvalidLifecycleTransition(ExperimentServiceError):
    """Raised when a Dataset lifecycle operation has no valid state edge."""


class ResourceUsageEvidenceUnavailable(ExperimentServiceError):
    """Raised when terminal producer accounting is absent or divergent."""


def encode_cursor(*, scope: str, created_at: str, stable_id: str, limit: int) -> str:
    body = {"v": 1, "scope": scope, "created_at": created_at, "stable_id": stable_id, "limit": limit}
    body["digest"] = sha256_text(canonical_json(body))
    return base64.urlsafe_b64encode(canonical_json(body).encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(value: str | None, *, scope: str, limit: int) -> tuple[str, str] | None:
    if value is None:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        body = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        digest = body.pop("digest")
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValidationFailure("invalid or stale cursor") from exc
    if (
        set(body) != {"v", "scope", "created_at", "stable_id", "limit"}
        or body["v"] != 1 or body["scope"] != scope or body["limit"] != limit
        or digest != sha256_text(canonical_json(body))
        or not isinstance(body["created_at"], str) or not isinstance(body["stable_id"], str)
    ):
        raise ValidationFailure("invalid or stale cursor")
    return body["created_at"], body["stable_id"]


async def require_domain_hierarchy(
    session: AsyncSession, project_id: str, experiment_id: str, domain_id: str
) -> tuple[ExperimentAggregateHead, ExperimentAggregateHead, ExperimentAggregateHead]:
    project = await session.get(ExperimentAggregateHead, project_id)
    experiment = await session.get(ExperimentAggregateHead, experiment_id)
    domain = await session.get(ExperimentAggregateHead, domain_id)
    if project is None or project.aggregate_kind != "workspace":
        raise NotFound("Project not found")
    if experiment is None or experiment.aggregate_kind != "experiment" or experiment.workspace_id != project_id or experiment.parent_id != project_id:
        raise NotFound("Global Experiment not found in Project")
    if domain is None or domain.aggregate_kind != "domain_experiment" or domain.workspace_id != project_id or domain.parent_id != experiment_id:
        raise NotFound("Domain Experiment not found in Global Experiment")
    revision = await session.get(ExperimentRevision, domain.current_revision_id or "")
    payload = json.loads(revision.canonical_payload) if revision is not None else {}
    if payload.get("domain_kind") != "ngs_molbio":
        raise ValidationFailure("unsupported_dataset_kind")
    return project, experiment, domain


async def require_dataset_read(
    session: AsyncSession, *, project_id: str, domain_id: str, dataset_id: str
) -> ExperimentAggregateHead:
    head = await session.get(ExperimentAggregateHead, dataset_id)
    if head is None or head.aggregate_kind != "dataset" or head.workspace_id != project_id or head.parent_id != domain_id:
        raise NotFound("Dataset not found in Domain")
    return head


async def require_mutable_dataset(
    session: AsyncSession, *, project_id: str, domain_id: str, dataset_id: str
) -> ExperimentAggregateHead:
    head = await require_dataset_read(
        session, project_id=project_id, domain_id=domain_id, dataset_id=dataset_id
    )
    if head.dataset_kind is None:
        raise ValidationFailure("legacy null-kind Dataset is read-only")
    _enabled_dataset_kind(head.dataset_kind)
    return head


def _dataset_head_document(head: ExperimentAggregateHead, *, project_id: str, experiment_id: str, domain_id: str, request_sha256: str | None = None) -> dict[str, Any]:
    return {
        "schema": "bms.dataset-head.v1", "project_id": project_id,
        "global_experiment_id": experiment_id, "domain_id": domain_id,
        "dataset_id": head.aggregate_id, "name": head.display_name,
        "dataset_kind": head.dataset_kind, "current_revision_id": head.current_revision_id,
        "head_generation": head.head_generation, "lifecycle_state": head.lifecycle_state,
        "normalized_request_sha256": request_sha256,
        "created_at": head.created_at, "updated_at": head.updated_at,
    }


async def create_project_dataset(
    session: AsyncSession, *, project_id: str, experiment_id: str, domain_id: str,
    name: str, dataset_kind: str, change_summary: str, actor: str,
    idempotency_key: str,
) -> dict[str, Any]:
    await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
    normalized_name = name.strip()
    _enabled_dataset_kind(dataset_kind)
    request_body = {"name": normalized_name, "dataset_kind": dataset_kind, "change_summary": change_summary.strip()}
    normalized = {"operation": "dataset_create", "project_id": project_id, "experiment_id": experiment_id, "domain_id": domain_id, **request_body}
    digest = sha256_text(canonical_json(normalized))
    scope = "dataset-create:" + sha256_text(canonical_json({"project_id": project_id, "domain_id": domain_id}))
    claim = await session.get(ExperimentIdempotencyClaim, (scope, idempotency_key))
    if claim is not None:
        if claim.request_sha256 != digest:
            raise IdempotencyConflict("idempotency key was reused with a different Dataset request")
        return json.loads(claim.response_json)
    head = await create_dataset(session, project_id, normalized_name, dataset_kind, experiment_id=domain_id)
    head.lifecycle_state = "active"
    head.description = ""
    head.updated_at = now()
    response = _dataset_head_document(head, project_id=project_id, experiment_id=experiment_id, domain_id=domain_id, request_sha256=digest)
    session.add(ExperimentIdempotencyClaim(scope=scope, idempotency_key=idempotency_key, request_sha256=digest, result_resource_id=head.aggregate_id, response_json=canonical_json(response), created_at=now()))
    add_audit_event(session, workspace_id=project_id, resource_id=head.aggregate_id, event_type="dataset_created", generation=0, payload={"dataset_kind": dataset_kind, "change_summary": request_body["change_summary"], "normalized_request_sha256": digest, "actor_id": actor})
    await session.flush()
    return response


def _metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"display_label", "group_label", "condition_label", "tags"}
    if set(value) - allowed:
        raise ValidationFailure("dataset metadata has unsupported fields")
    result = dict(value)
    tags = result.get("tags", [])
    if not isinstance(tags, list) or len(tags) > 16 or len(tags) != len(set(tags)):
        raise ValidationFailure("dataset metadata tags are invalid")
    if len(canonical_json(result).encode("utf-8")) > 2048:
        raise ValidationFailure("dataset metadata exceeds 2 KiB")
    forbidden = ("sequence", "reads", "alignment", "signal", "manifest", "structure", "payload", "base64", "digest", "path", "uri")
    if any(token in str(item).lower() for item in result.values() for token in forbidden):
        raise ValidationFailure("dataset_metadata_payload_forbidden")
    return result


async def _verified_member(
    session: AsyncSession, core_session: AsyncSession, *, project_id: str, domain_id: str,
    dataset_kind: str, item: Mapping[str, Any], ordinal: int,
) -> dict[str, Any]:
    if item.get("ordinal") != ordinal:
        raise ValidationFailure("Dataset member ordinal must equal its array position")
    receipt_id = str(item.get("receipt_id") or "")
    role = str(item.get("role") or "")
    receipt = await session.get(ExperimentExternalEntityReceipt, receipt_id)
    if receipt is None or receipt.workspace_id != project_id or receipt.availability != "available":
        raise ValidationFailure("unsupported_dataset_member")
    allowed = DATASET_KINDS[dataset_kind]
    if receipt.verification_authority not in allowed or role not in allowed[receipt.verification_authority]:
        raise ValidationFailure("unsupported_dataset_member")
    attached = await session.scalar(select(ExperimentLineageEdge.id).where(
        ExperimentLineageEdge.workspace_id == project_id,
        ExperimentLineageEdge.source_resource_id == domain_id,
        ExperimentLineageEdge.target_resource_id == receipt_id,
    ))
    if attached is None:
        raise ValidationFailure("Dataset member belongs to another Domain or is not attached")
    try:
        persisted = json.loads(receipt.acknowledgement_json or "{}")
        verification_entity_id = receipt.entity_id
        if receipt.verification_authority == "bms.molbio.member-molecular-revision.adapter.v1":
            verification_entity_id = (
                f"{verification_entity_id}&domain_experiment_id={domain_id}"
            )
        fresh = await registry.get(receipt.verification_authority).verify(
            core_session, verification_entity_id
        )
    except (json.JSONDecodeError, AdapterError) as exc:
        raise ValidationFailure(f"Dataset member native authority failed: {exc}") from exc
    owner = fresh.get("metadata", {}).get("global_domain_experiment_id")
    if owner is not None and owner != domain_id:
        raise ValidationFailure("Dataset member native owner is wrong")
    exact_fields = ("store_id", "entity_kind", "entity_id", "entity_revision_id", "content_digest", "contract_digest", "verifier_id", "reopen_uri")
    mismatched_fields = [
        key for key in exact_fields if fresh.get(key) != persisted.get(key)
    ]
    if mismatched_fields:
        raise ValidationFailure(
            "Dataset member immutable revision, generation, digest, or bytes changed: "
            + ",".join(mismatched_fields)
        )
    if (receipt.store_id, receipt.entity_kind, receipt.entity_id, receipt.generation_or_revision, receipt.content_digest) != (
        fresh.get("store_id"), fresh.get("entity_kind"), fresh.get("entity_id"), str(fresh.get("entity_revision_id")), fresh.get("content_digest")
    ):
        raise ValidationFailure("Dataset member persisted receipt diverges from native authority")
    media_type = item.get("media_type")
    native_media_type = fresh.get("metadata", {}).get("media_type")
    if native_media_type is not None and media_type != native_media_type:
        raise ValidationFailure("Dataset member media type disagrees with native authority")
    native_size = fresh.get("metadata", {}).get("size_bytes")
    if native_size is not None and (not isinstance(native_size, int) or isinstance(native_size, bool) or native_size < 0 or native_size > 2**63 - 1):
        raise ValidationFailure("Dataset member native size is invalid")
    value = {
        "schema": "bms.dataset-member.v1", "receipt_id": receipt.id,
        "adapter_id": receipt.verification_authority, "store_id": receipt.store_id,
        "entity_kind": receipt.entity_kind, "entity_id": receipt.entity_id,
        "native_revision_or_generation": receipt.generation_or_revision,
        "native_content_sha256": receipt.content_digest, "role": role, "ordinal": ordinal,
        "media_type": media_type, "metadata": _metadata(item.get("metadata") or {}),
        "reopen_uri": fresh["reopen_uri"],
    }
    return {"role": role, "identity": receipt.id, "value": value, "size_bytes": native_size, "media_type": media_type}


async def revise_project_dataset(
    session: AsyncSession, core_session: AsyncSession, *, project_id: str, experiment_id: str,
    domain_id: str, dataset_id: str, expected_head_generation: int, change_summary: str,
    members: list[Mapping[str, Any]], actor: str, idempotency_key: str,
) -> dict[str, Any]:
    await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
    head = await require_mutable_dataset(session, project_id=project_id, domain_id=domain_id, dataset_id=dataset_id)
    normalized = {"operation": "dataset_revision_create", "project_id": project_id, "experiment_id": experiment_id, "domain_id": domain_id, "dataset_id": dataset_id, "expected_head_generation": expected_head_generation, "change_summary": change_summary.strip(), "members": members}
    digest = sha256_text(canonical_json(normalized))
    scope = "dataset-revision-create:" + sha256_text(canonical_json({"dataset_id": dataset_id}))
    claim = await session.get(ExperimentIdempotencyClaim, (scope, idempotency_key))
    if claim is not None:
        if claim.request_sha256 != digest:
            raise IdempotencyConflict("idempotency key was reused with a different Dataset revision")
        return json.loads(claim.response_json)
    if head.lifecycle_state != "active":
        raise ValidationFailure("Dataset is not active")
    if head.head_generation != expected_head_generation:
        raise RevisionConflict("stale_generation")
    if len(members) > 10_000:
        raise ValidationFailure("Dataset member count exceeds 10000")
    pairs = [(str(item.get("receipt_id") or ""), str(item.get("role") or "")) for item in members]
    if len(pairs) != len(set(pairs)):
        raise ValidationFailure("Dataset members contain duplicate receipt and role pairs")
    verified = [await _verified_member(session, core_session, project_id=project_id, domain_id=domain_id, dataset_kind=str(head.dataset_kind), item=item, ordinal=ordinal) for ordinal, item in enumerate(members)]
    revision = await save_dataset_revision(session, dataset_id, {"schema": "bms.dataset-revision.v1", "change_summary": change_summary.strip(), "members": verified}, expected_head_generation=expected_head_generation)
    await session.refresh(head)
    head.lifecycle_state = "active"
    head.updated_at = now()
    await session.flush()
    response = {
        "schema": "bms.dataset-revision.v1", "project_id": project_id,
        "global_experiment_id": experiment_id, "domain_id": domain_id,
        "dataset_id": dataset_id, "revision_id": revision.resource_id,
        "revision_number": revision.revision_number, "head_generation": head.head_generation,
        "member_count": len(verified), "revision_sha256": revision.payload_sha256,
        "normalized_request_sha256": digest, "created_at": revision.created_at,
    }
    session.add(ExperimentIdempotencyClaim(scope=scope, idempotency_key=idempotency_key, request_sha256=digest, result_resource_id=revision.resource_id, response_json=canonical_json(response), created_at=now()))
    add_audit_event(session, workspace_id=project_id, resource_id=dataset_id, event_type="dataset_revision_created", generation=head.head_generation, payload={"revision_id": revision.resource_id, "change_summary": change_summary.strip(), "normalized_request_sha256": digest, "actor_id": actor})
    await session.flush()
    return response


async def set_project_dataset_lifecycle(
    session: AsyncSession, *, project_id: str, experiment_id: str, domain_id: str,
    dataset_id: str, operation: str, expected_head_generation: int, change_summary: str, actor: str,
    idempotency_key: str,
) -> dict[str, Any]:
    await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
    head = await require_mutable_dataset(
        session, project_id=project_id, domain_id=domain_id, dataset_id=dataset_id
    )
    if operation not in {"archive", "restore"}:
        raise ValidationFailure("unsupported Dataset lifecycle operation")
    normalized_change_summary = change_summary.strip()
    if not normalized_change_summary:
        raise ValidationFailure("Dataset lifecycle change_summary is required")
    normalized = {
        "operation": f"dataset_{operation}",
        "project_id": project_id,
        "experiment_id": experiment_id,
        "domain_id": domain_id,
        "dataset_id": dataset_id,
        "expected_head_generation": expected_head_generation,
        "change_summary": normalized_change_summary,
    }
    digest = sha256_text(canonical_json(normalized))
    scope = f"dataset-{operation}:" + sha256_text(canonical_json({"dataset_id": dataset_id}))
    claim = await session.get(ExperimentIdempotencyClaim, (scope, idempotency_key))
    if claim is not None:
        if claim.request_sha256 != digest:
            raise IdempotencyConflict(
                f"idempotency key was reused with a different Dataset {operation} request"
            )
        return json.loads(claim.response_json)
    if operation == "archive":
        if head.lifecycle_state != "active":
            raise InvalidLifecycleTransition("Dataset is not active")
        head = await archive_aggregate(
            session, dataset_id, expected_head_generation=expected_head_generation
        )
    else:
        if head.lifecycle_state != "archived":
            raise InvalidLifecycleTransition("Dataset is not archived")
        head = await restore_aggregate(
            session, dataset_id, expected_head_generation=expected_head_generation
        )
    response = _dataset_head_document(
        head,
        project_id=project_id,
        experiment_id=experiment_id,
        domain_id=domain_id,
        request_sha256=digest,
    )
    session.add(
        ExperimentIdempotencyClaim(
            scope=scope,
            idempotency_key=idempotency_key,
            request_sha256=digest,
            result_resource_id=dataset_id,
            response_json=canonical_json(response),
            created_at=now(),
        )
    )
    add_audit_event(
        session,
        workspace_id=project_id,
        resource_id=dataset_id,
        event_type=f"dataset_{operation}_requested",
        generation=head.head_generation,
        payload={
            "actor_id": actor,
            "normalized_request_sha256": digest,
            "domain_id": domain_id,
            "change_summary": normalized_change_summary,
        },
    )
    await session.flush()
    return response


def _resource_request(scheduler: Mapping[str, Any]) -> dict[str, Any]:
    resources = scheduler.get("resources") if isinstance(scheduler.get("resources"), Mapping) else {}
    cpu = resources.get("cpu_threads", resources.get("cpus", 1))
    dram_gib = resources.get("dram_gib", resources.get("memory_gib", 1))
    if str(scheduler.get("model_id") or "").strip().lower() == "esmfold2":
        if not isinstance(dram_gib, (int, float)) or isinstance(dram_gib, bool):
            raise ResourceAdmissionDenied("invalid_dram_request", "effective DRAM request must be numeric", [])
        dram_gib = max(float(dram_gib), 16.0)
    gpu = resources.get("pinned_gpu", resources.get("gpu_index"))
    if not isinstance(cpu, int) or isinstance(cpu, bool) or cpu < 1 or cpu > CPU_THREAD_LIMIT:
        raise ResourceAdmissionDenied("invalid_cpu_request", "effective CPU request is outside 1..24 threads", [])
    if not isinstance(dram_gib, (int, float)) or isinstance(dram_gib, bool) or dram_gib <= 0:
        raise ResourceAdmissionDenied("invalid_dram_request", "effective DRAM request must be positive", [])
    dram_bytes = int(dram_gib * 1024**3)
    if dram_bytes > DRAM_BYTE_LIMIT:
        raise ResourceAdmissionDenied("resource_admission_denied", "effective DRAM request exceeds the 96 GiB deployment limit", [])
    if gpu is not None and (not isinstance(gpu, int) or isinstance(gpu, bool) or gpu < 0):
        raise ResourceAdmissionDenied("gpu_inventory_degraded", "GPU request cannot be matched to a valid scheduler GPU index", [])
    gpu_uuid = None
    if gpu is not None:
        try:
            from routers.gpu import _query_smi_gpu_map

            inventory = _query_smi_gpu_map()
            match = inventory.get(gpu)
        except Exception as exc:
            raise ResourceAdmissionDenied("gpu_inventory_degraded", "live GPU inventory is unavailable; GPU admission fails closed", []) from exc
        if not isinstance(match, dict):
            raise ResourceAdmissionDenied("gpu_inventory_degraded", "requested GPU is absent in live inventory", [])
        gpu_uuid = str(match.get("uuid") or "").strip()
        if not gpu_uuid:
            raise ResourceAdmissionDenied("gpu_inventory_degraded", "live GPU UUID authority is unavailable", [])
    return {"cpu_threads": cpu, "dram_bytes": dram_bytes, "gpu_index": gpu, "gpu_uuid": gpu_uuid}


async def reserve_run_group(
    session: AsyncSession, *, group_id: str, domain_id: str, actor: str
) -> list[ExperimentResourceAdmission]:
    policy = await session.get(ExperimentResourceAdmissionPolicy, "managed-workflows")
    if policy is None or policy.policy_version != ADMISSION_POLICY_VERSION or policy.cpu_thread_limit != CPU_THREAD_LIMIT or policy.dram_byte_limit != DRAM_BYTE_LIMIT:
        raise ResourceAdmissionDenied("resource_policy_unavailable", "resource admission policy is unavailable or divergent", [])
    await session.execute(update(ExperimentResourceAdmissionPolicy).where(ExperimentResourceAdmissionPolicy.policy_id == policy.policy_id).values(lock_generation=ExperimentResourceAdmissionPolicy.lock_generation + 1, updated_at=now()))
    runs = list((await session.scalars(select(ExperimentWorkflowRun).where(ExperimentWorkflowRun.run_group_id == group_id))).all())
    attempts = list((await session.scalars(select(ExperimentRunAttempt).where(ExperimentRunAttempt.workflow_run_id.in_([row.resource_id for row in runs])))).all()) if runs else []
    admitted_attempt_ids = set((await session.scalars(select(ExperimentResourceAdmission.run_attempt_id).where(ExperimentResourceAdmission.run_attempt_id.in_([row.resource_id for row in attempts])))).all()) if attempts else set()
    attempts = [row for row in attempts if row.resource_id not in admitted_attempt_ids]
    requests: list[dict[str, Any]] = []
    for attempt in attempts:
        preparation = await session.get(ExperimentWorkflowPreparation, attempt.preparation_id)
        revision = await session.get(ExperimentRevision, preparation.workflow_revision_id if preparation else "")
        plan = await session.get(ExperimentAggregateHead, revision.subject_id if revision else "")
        if preparation is None or plan is None or plan.parent_id != domain_id:
            raise ResourceAdmissionDenied("resource_authority_unavailable", "attempt has no exact Domain preparation authority", requests)
        try:
            request = _resource_request(json.loads(preparation.scheduler_payload_json))
        except ResourceAdmissionDenied as exc:
            exc.requests.append({"attempt": attempt, "preparation": preparation, "plan": plan, "cpu_threads": 1, "dram_bytes": 1024**3, "gpu_index": None, "gpu_uuid": None})
            raise
        request.update(attempt=attempt, preparation=preparation, plan=plan)
        requests.append(request)
    totals = (await session.execute(select(func.coalesce(func.sum(ExperimentResourceAdmission.cpu_threads), 0), func.coalesce(func.sum(ExperimentResourceAdmission.dram_bytes), 0)).where(ExperimentResourceAdmission.state.in_(ACTIVE_ADMISSION_STATES)))).one()
    requested_cpu = sum(item["cpu_threads"] for item in requests)
    requested_dram = sum(item["dram_bytes"] for item in requests)
    if int(totals[0]) + requested_cpu > CPU_THREAD_LIMIT or int(totals[1]) + requested_dram > DRAM_BYTE_LIMIT:
        raise ResourceAdmissionDenied("resource_admission_denied", f"aggregate request would exceed {CPU_THREAD_LIMIT} CPU threads or 96 GiB DRAM", requests)
    requested_gpus = [item["gpu_index"] for item in requests if item["gpu_index"] is not None]
    active_gpus = set(
        (
            await session.scalars(
                select(ExperimentResourceAdmission.gpu_index).where(
                    ExperimentResourceAdmission.state.in_(ACTIVE_ADMISSION_STATES),
                    ExperimentResourceAdmission.gpu_index.is_not(None),
                )
            )
        ).all()
    )
    if len(requested_gpus) != len(set(requested_gpus)) or active_gpus.intersection(requested_gpus):
        raise ResourceAdmissionDenied(
            "resource_admission_denied",
            "requested physical GPU is already reserved by an active admission",
            requests,
        )
    created: list[ExperimentResourceAdmission] = []
    timestamp = now()
    source_revision, source_tree = _runtime_source_authority(requests)
    for item in requests:
        attempt = item["attempt"]
        row = ExperimentResourceAdmission(
            admission_id=str(uuid.uuid4()), workspace_id=attempt.workspace_id,
            domain_experiment_id=domain_id, plan_id=item["plan"].aggregate_id,
            preparation_id=attempt.preparation_id, run_attempt_id=attempt.resource_id,
            canonical_job_id=attempt.scheduler_job_id, state="queued",
            cpu_threads=item["cpu_threads"], dram_bytes=item["dram_bytes"],
            gpu_index=item["gpu_index"], gpu_uuid=item["gpu_uuid"],
            policy_source="project-scheduler", policy_version=ADMISSION_POLICY_VERSION,
            owner=actor, lease_token=str(uuid.uuid4()), admitted_at=timestamp,
            queued_at=timestamp, created_at=timestamp, updated_at=timestamp,
        )
        session.add(row)
        created.append(row)
    await session.flush()
    request_by_attempt = {
        str(item["attempt"].resource_id): item
        for item in requests
    }
    for row in created:
        item = request_by_attempt[str(row.run_attempt_id)]
        attempt = item["attempt"]
        handoff = build_resource_admission_handoff(
            admission_id=row.admission_id,
            run_attempt_id=str(row.run_attempt_id),
            canonical_job_id=str(row.canonical_job_id),
            preparation_id=row.preparation_id,
            cpu_threads=row.cpu_threads,
            dram_bytes=row.dram_bytes,
            gpu_index=row.gpu_index,
            gpu_uuid=row.gpu_uuid,
            policy_source=row.policy_source,
            policy_version=row.policy_version,
            owner=row.owner,
            lease_token=str(row.lease_token),
            source_revision=source_revision,
            source_tree=source_tree,
        )
        authority_document = {
            "schema": "bms.resource-admission-authority.v1",
            "admission_id": row.admission_id,
            "run_attempt_id": row.run_attempt_id,
            "canonical_job_id": row.canonical_job_id,
            "preparation_id": row.preparation_id,
            "handoff_sha256": handoff["handoff_sha256"],
            "source_revision": source_revision,
            "source_tree": source_tree,
        }
        handoff_json = canonical_json(authority_document)
        session.add(
            ExperimentOperationalReceipt(
                receipt_id=f"resource-admission:{row.admission_id}",
                operation_kind="resource_admission",
                workspace_id=row.workspace_id,
                native_identity=str(row.canonical_job_id),
                state="sealed",
                receipt_json=handoff_json,
                receipt_sha256=sha256_text(handoff_json),
                source_revision=source_revision,
                occurred_at=timestamp,
                verified_at=timestamp,
            )
        )
        outbox = await session.scalar(
            select(ExperimentDispatchOutbox).where(
                ExperimentDispatchOutbox.run_attempt_id == attempt.resource_id
            )
        )
        if outbox is None:
            continue
        if (
            outbox.status != "pending"
            or outbox.dispatch_attempts != 0
            or outbox.acknowledgement_json is not None
        ):
            raise ResourceAdmissionDenied(
                "resource_dispatch_already_visible",
                "dispatch became visible before its resource admission was sealed",
                requests,
            )
        try:
            dispatch_payload = json.loads(outbox.payload_json)
        except json.JSONDecodeError as exc:
            raise ResourceAdmissionDenied(
                "resource_dispatch_payload_invalid",
                "dispatch payload is not canonical JSON",
                requests,
            ) from exc
        if not isinstance(dispatch_payload, dict) or "resource_admission" in dispatch_payload:
            raise ResourceAdmissionDenied(
                "resource_dispatch_payload_invalid",
                "dispatch payload resource authority is missing or ambiguous",
                requests,
            )
        dispatch_payload["resource_admission"] = handoff
        outbox.payload_json = canonical_json(dispatch_payload)
        outbox.payload_sha256 = sha256_text(outbox.payload_json)
    return created


async def persist_admission_refusal(
    session: AsyncSession, *, domain_id: str, actor: str, denial: ResourceAdmissionDenied
) -> None:
    timestamp = now()
    for item in denial.requests:
        preparation = item.get("preparation")
        plan = item.get("plan")
        workspace_id = item.get("workspace_id") or preparation.workspace_id
        preparation_id = item.get("preparation_id") or preparation.resource_id
        plan_id = item.get("plan_id") or plan.aggregate_id
        session.add(ExperimentResourceAdmission(
            admission_id=str(uuid.uuid4()), workspace_id=workspace_id,
            domain_experiment_id=domain_id, plan_id=plan_id,
            preparation_id=preparation_id, run_attempt_id=None,
            canonical_job_id=None, state="refused",
            cpu_threads=max(1, min(CPU_THREAD_LIMIT, int(item.get("cpu_threads") or 1))),
            dram_bytes=max(1, min(DRAM_BYTE_LIMIT, int(item.get("dram_bytes") or 1))),
            gpu_index=item.get("gpu_index"), gpu_uuid=item.get("gpu_uuid"),
            policy_source="project-scheduler", policy_version=ADMISSION_POLICY_VERSION,
            owner=actor, refusal_code=denial.code, refusal_reason=denial.reason[:2048],
            created_at=timestamp, updated_at=timestamp,
        ))
    await session.flush()


async def _resource_admission_authority_for_attempt(
    session: AsyncSession,
    *,
    run_attempt_id: str,
    canonical_job_id: str,
) -> tuple[ExperimentRunAttempt, ExperimentResourceAdmission, dict[str, Any]]:
    attempt = await session.get(ExperimentRunAttempt, run_attempt_id)
    if attempt is None or attempt.scheduler_job_id != canonical_job_id:
        raise ResourceUsageEvidenceUnavailable("resource evidence has no exact run-attempt Job authority")
    admissions = list(
        (
            await session.scalars(
                select(ExperimentResourceAdmission).where(
                    ExperimentResourceAdmission.run_attempt_id == run_attempt_id,
                    ExperimentResourceAdmission.canonical_job_id == canonical_job_id,
                )
            )
        ).all()
    )
    if len(admissions) != 1:
        raise ResourceUsageEvidenceUnavailable("resource admission cardinality is not exact")
    admission = admissions[0]
    authority = await session.get(
        ExperimentOperationalReceipt,
        f"resource-admission:{admission.admission_id}",
    )
    if (
        authority is None
        or authority.operation_kind != "resource_admission"
        or authority.state != "sealed"
        or authority.workspace_id != admission.workspace_id
        or authority.native_identity != canonical_job_id
        or sha256_text(authority.receipt_json) != authority.receipt_sha256
    ):
        raise ResourceUsageEvidenceUnavailable("immutable resource admission handoff is unavailable")
    try:
        authority_document = json.loads(authority.receipt_json)
    except json.JSONDecodeError as exc:
        raise ResourceUsageEvidenceUnavailable("immutable resource admission authority is invalid") from exc
    if (
        not isinstance(authority_document, dict)
        or authority_document.get("schema") != "bms.resource-admission-authority.v1"
        or authority_document.get("admission_id") != admission.admission_id
        or authority_document.get("run_attempt_id") != run_attempt_id
        or authority_document.get("canonical_job_id") != canonical_job_id
        or authority_document.get("preparation_id") != attempt.preparation_id
        or authority_document.get("source_revision") != authority.source_revision
        or not isinstance(authority_document.get("source_tree"), str)
    ):
        raise ResourceUsageEvidenceUnavailable("immutable resource admission authority diverged")
    try:
        handoff = build_resource_admission_handoff(
            admission_id=admission.admission_id,
            run_attempt_id=run_attempt_id,
            canonical_job_id=canonical_job_id,
            preparation_id=attempt.preparation_id,
            cpu_threads=admission.cpu_threads,
            dram_bytes=admission.dram_bytes,
            gpu_index=admission.gpu_index,
            gpu_uuid=admission.gpu_uuid,
            policy_source=admission.policy_source,
            policy_version=admission.policy_version,
            owner=admission.owner,
            lease_token=str(admission.lease_token),
            source_revision=str(authority.source_revision),
            source_tree=str(authority_document["source_tree"]),
        )
    except ResourceUsageEvidenceError as exc:
        raise ResourceUsageEvidenceUnavailable("resource admission row cannot reconstruct its handoff") from exc
    if handoff["handoff_sha256"] != authority_document.get("handoff_sha256"):
        raise ResourceUsageEvidenceUnavailable("resource admission handoff digest diverged")
    return attempt, admission, handoff


async def resource_admission_handoff_for_attempt(
    session: AsyncSession,
    *,
    run_attempt_id: str,
    canonical_job_id: str,
) -> dict[str, Any]:
    """Return the one sealed handoff that must enter canonical Job params."""

    _, _, handoff = await _resource_admission_authority_for_attempt(
        session,
        run_attempt_id=run_attempt_id,
        canonical_job_id=canonical_job_id,
    )
    return handoff


def _producer_receipt_finished_at(receipt: Mapping[str, Any]) -> str:
    observed = receipt.get("observed")
    candidate = observed.get("finished_at") if isinstance(observed, Mapping) else receipt.get("finished_at")
    if not isinstance(candidate, str) or not candidate:
        raise ResourceUsageEvidenceUnavailable("producer resource receipt finished_at is unavailable")
    return candidate


async def persist_producer_resource_usage_evidence(
    session: AsyncSession,
    *,
    core_job: Any,
    run_attempt_id: str,
) -> ExperimentOperationalReceipt:
    """Project one exact producer receipt, then release its admission."""

    canonical_job_id = str(getattr(core_job, "id", ""))
    _, admission, handoff = await _resource_admission_authority_for_attempt(
        session,
        run_attempt_id=run_attempt_id,
        canonical_job_id=canonical_job_id,
    )
    try:
        producer_receipt = validate_producer_resource_usage_receipt(core_job, handoff)
    except ResourceUsageEvidenceError as exc:
        raise ResourceUsageEvidenceUnavailable(str(exc)) from exc
    producer_digest = str(producer_receipt["receipt_sha256"])
    producer_finished_at = _producer_receipt_finished_at(producer_receipt)
    public_producer_receipt = json.loads(canonical_json(producer_receipt))
    projected = {
        "schema": "bms.experiment-resource-usage-projection.v1",
        "workspace_id": admission.workspace_id,
        "domain_experiment_id": admission.domain_experiment_id,
        "plan_id": admission.plan_id,
        "preparation_id": admission.preparation_id,
        "run_attempt_id": run_attempt_id,
        "admission_id": admission.admission_id,
        "canonical_job_id": str(core_job.id),
        "producer_receipt": public_producer_receipt,
        "producer_receipt_sha256": producer_digest,
        "admission_handoff_sha256": handoff["handoff_sha256"],
        "projected_at": producer_finished_at,
    }
    projected_json = canonical_json(projected)
    receipt_id = f"resource-usage:{producer_digest}"
    existing = await session.get(ExperimentOperationalReceipt, receipt_id)
    if existing is not None:
        if (
            existing.operation_kind != "resource_usage"
            or existing.workspace_id != admission.workspace_id
            or existing.native_identity != str(core_job.id)
            or existing.state != "complete"
            or existing.receipt_json != projected_json
            or existing.receipt_sha256 != sha256_text(projected_json)
            or existing.source_revision != handoff["source_revision"]
        ):
            raise ResourceUsageEvidenceUnavailable("resource usage projection identity has conflicting bytes")
        await release_attempt_admissions(
            session,
            [run_attempt_id],
            reason=f"producer-resource-receipt:{producer_digest}",
        )
        return existing
    projected_row = ExperimentOperationalReceipt(
        receipt_id=receipt_id,
        operation_kind="resource_usage",
        workspace_id=admission.workspace_id,
        native_identity=str(core_job.id),
        state="complete",
        receipt_json=projected_json,
        receipt_sha256=sha256_text(projected_json),
        source_revision=handoff["source_revision"],
        occurred_at=producer_finished_at,
        verified_at=now(),
    )
    session.add(projected_row)
    await session.flush()
    await release_attempt_admissions(
        session,
        [run_attempt_id],
        reason=f"producer-resource-receipt:{producer_digest}",
    )
    return projected_row


async def persist_never_launched_resource_usage_evidence(
    session: AsyncSession,
    *,
    run_attempt_id: str,
    command_id: str,
    request_sha256: str,
    launch_kind: str,
    sealed_at: str,
) -> ExperimentOperationalReceipt:
    """Seal zero-use authority after cancellation proves a Job never existed."""

    attempt = await session.get(ExperimentRunAttempt, run_attempt_id)
    if (
        attempt is None
        or attempt.state != "cancelled"
        or launch_kind not in {"managed", "typed"}
        or not isinstance(sealed_at, str)
        or not sealed_at
        or len(sealed_at) > 64
    ):
        raise ResourceUsageEvidenceUnavailable("never-launched resource authority is unavailable")
    admissions = list(
        (
            await session.scalars(
                select(ExperimentResourceAdmission).where(
                    ExperimentResourceAdmission.run_attempt_id == run_attempt_id,
                    ExperimentResourceAdmission.canonical_job_id == attempt.scheduler_job_id,
                )
            )
        ).all()
    )
    if len(admissions) != 1:
        raise ResourceUsageEvidenceUnavailable("never-launched resource admission cardinality is not exact")
    admission = admissions[0]
    authority = await session.get(
        ExperimentOperationalReceipt,
        f"resource-admission:{admission.admission_id}",
    )
    if (
        authority is None
        or authority.operation_kind != "resource_admission"
        or authority.state != "sealed"
        or authority.native_identity != attempt.scheduler_job_id
        or sha256_text(authority.receipt_json) != authority.receipt_sha256
    ):
        raise ResourceUsageEvidenceUnavailable("never-launched admission authority is unavailable")
    try:
        authority_document = json.loads(authority.receipt_json)
    except json.JSONDecodeError as exc:
        raise ResourceUsageEvidenceUnavailable("never-launched admission authority is invalid") from exc
    if (
        not isinstance(authority_document, dict)
        or authority_document.get("schema") != "bms.resource-admission-authority.v1"
        or authority_document.get("admission_id") != admission.admission_id
        or authority_document.get("run_attempt_id") != run_attempt_id
        or authority_document.get("canonical_job_id") != attempt.scheduler_job_id
        or authority_document.get("preparation_id") != attempt.preparation_id
        or authority_document.get("source_revision") != authority.source_revision
        or not isinstance(authority_document.get("source_tree"), str)
    ):
        raise ResourceUsageEvidenceUnavailable("never-launched admission authority diverged")
    handoff = build_resource_admission_handoff(
        admission_id=admission.admission_id,
        run_attempt_id=run_attempt_id,
        canonical_job_id=attempt.scheduler_job_id,
        preparation_id=attempt.preparation_id,
        cpu_threads=admission.cpu_threads,
        dram_bytes=admission.dram_bytes,
        gpu_index=admission.gpu_index,
        gpu_uuid=admission.gpu_uuid,
        policy_source=admission.policy_source,
        policy_version=admission.policy_version,
        owner=admission.owner,
        lease_token=str(admission.lease_token),
        source_revision=str(authority.source_revision),
        source_tree=str(authority_document["source_tree"]),
    )
    if handoff["handoff_sha256"] != authority_document.get("handoff_sha256"):
        raise ResourceUsageEvidenceUnavailable("never-launched handoff digest diverged")
    evidence = {
        "schema": "bms.experiment-resource-nonexecution.v1",
        "workspace_id": admission.workspace_id,
        "domain_experiment_id": admission.domain_experiment_id,
        "plan_id": admission.plan_id,
        "preparation_id": admission.preparation_id,
        "run_attempt_id": run_attempt_id,
        "admission_id": admission.admission_id,
        "canonical_job_id": attempt.scheduler_job_id,
        "command_id": command_id,
        "request_sha256": request_sha256,
        "launch_kind": launch_kind,
        "outcome": "launch_fenced_absent",
        "cpu_usage_usec": 0,
        "memory_peak_bytes": 0,
        "gpu_peak_by_uuid": {},
        "admission_handoff_sha256": handoff["handoff_sha256"],
        "sealed_at": sealed_at,
    }
    evidence_json = canonical_json(evidence)
    evidence_digest = sha256_text(evidence_json)
    receipt_id = f"resource-nonexecution:{evidence_digest}"
    existing = await session.get(ExperimentOperationalReceipt, receipt_id)
    if existing is not None:
        if (
            existing.operation_kind != "resource_usage"
            or existing.workspace_id != admission.workspace_id
            or existing.native_identity != attempt.scheduler_job_id
            or existing.state != "complete"
            or existing.receipt_json != evidence_json
            or existing.receipt_sha256 != evidence_digest
            or existing.source_revision != authority.source_revision
        ):
            raise ResourceUsageEvidenceUnavailable("never-launched resource receipt conflicts")
        await release_attempt_admissions(
            session,
            [run_attempt_id],
            reason=f"never-launched-resource-receipt:{evidence_digest}",
        )
        return existing
    row = ExperimentOperationalReceipt(
        receipt_id=receipt_id,
        operation_kind="resource_usage",
        workspace_id=admission.workspace_id,
        native_identity=attempt.scheduler_job_id,
        state="complete",
        receipt_json=evidence_json,
        receipt_sha256=evidence_digest,
        source_revision=authority.source_revision,
        occurred_at=evidence["sealed_at"],
        verified_at=evidence["sealed_at"],
    )
    session.add(row)
    await session.flush()
    await release_attempt_admissions(
        session,
        [run_attempt_id],
        reason=f"never-launched-resource-receipt:{evidence_digest}",
    )
    return row


async def release_attempt_admissions(session: AsyncSession, attempt_ids: list[str], *, reason: str) -> None:
    if not attempt_ids:
        return
    await session.execute(update(ExperimentResourceAdmission).where(
        ExperimentResourceAdmission.run_attempt_id.in_(attempt_ids),
        ExperimentResourceAdmission.state.in_(ACTIVE_ADMISSION_STATES),
    ).values(state="released", release_reason=reason[:1024], released_at=now(), updated_at=now()))


async def _accepted_persisted_resource_evidence(
    session: AsyncSession,
    admission: ExperimentResourceAdmission,
) -> bool:
    authority = await session.get(
        ExperimentOperationalReceipt,
        f"resource-admission:{admission.admission_id}",
    )
    if (
        authority is None
        or authority.operation_kind != "resource_admission"
        or authority.state != "sealed"
        or authority.native_identity != admission.canonical_job_id
        or sha256_text(authority.receipt_json) != authority.receipt_sha256
    ):
        return False
    try:
        authority_document = json.loads(authority.receipt_json)
    except (TypeError, json.JSONDecodeError):
        return False
    if (
        not isinstance(authority_document, dict)
        or authority_document.get("schema") != "bms.resource-admission-authority.v1"
        or authority_document.get("admission_id") != admission.admission_id
        or authority_document.get("run_attempt_id") != admission.run_attempt_id
        or authority_document.get("canonical_job_id") != admission.canonical_job_id
        or authority_document.get("preparation_id") != admission.preparation_id
        or authority_document.get("source_revision") != authority.source_revision
        or not isinstance(authority_document.get("source_tree"), str)
        or not re.fullmatch(r"[0-9a-f]{40}", authority_document["source_tree"])
    ):
        return False
    rows = list(
        (
            await session.scalars(
                select(ExperimentOperationalReceipt).where(
                    ExperimentOperationalReceipt.operation_kind == "resource_usage",
                    ExperimentOperationalReceipt.workspace_id == admission.workspace_id,
                    ExperimentOperationalReceipt.native_identity == admission.canonical_job_id,
                    ExperimentOperationalReceipt.state == "complete",
                )
            )
        ).all()
    )
    accepted = 0
    for row in rows:
        try:
            document = json.loads(row.receipt_json)
        except (TypeError, json.JSONDecodeError):
            continue
        if (
            not isinstance(document, dict)
            or canonical_json(document) != row.receipt_json
            or sha256_text(row.receipt_json) != row.receipt_sha256
            or row.source_revision != authority.source_revision
            or document.get("admission_id") != admission.admission_id
            or document.get("run_attempt_id") != admission.run_attempt_id
            or document.get("canonical_job_id") != admission.canonical_job_id
            or document.get("admission_handoff_sha256")
            != authority_document.get("handoff_sha256")
        ):
            continue
        if document.get("schema") == "bms.experiment-resource-usage-projection.v1":
            producer = document.get("producer_receipt")
            producer_digest = document.get("producer_receipt_sha256")
            unsigned_producer = dict(producer) if isinstance(producer, dict) else {}
            unsigned_producer.pop("receipt_sha256", None)
            if (
                not isinstance(producer, dict)
                or hashlib.sha256(
                    json.dumps(
                        unsigned_producer,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode("utf-8")
                ).hexdigest()
                != producer_digest
                or producer.get("receipt_sha256") != producer_digest
                or producer.get("complete") is not True
                or producer.get("admission_id") != admission.admission_id
                or producer.get("producer_source_revision") != authority.source_revision
                or producer.get("producer_source_tree") != authority_document.get("source_tree")
            ):
                continue
        elif document.get("schema") == "bms.experiment-resource-nonexecution.v1":
            if (
                document.get("outcome") != "launch_fenced_absent"
                or document.get("cpu_usage_usec") != 0
                or document.get("memory_peak_bytes") != 0
                or document.get("gpu_peak_by_uuid") != {}
            ):
                continue
        else:
            continue
        accepted += 1
    return accepted == 1


def _systemd_units_for_job(job_id: object) -> list[str]:
    unit_glob = historical_owner_absence_unit_glob(job_id)
    result = subprocess.run(
        [
            "systemctl",
            "--user",
            "list-units",
            "--all",
            "--plain",
            "--no-legend",
            "--no-pager",
            unit_glob,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise ResourceUsageEvidenceUnavailable(
            "historical systemd owner inventory is unavailable"
        )
    return sorted(
        {
            line.split(maxsplit=1)[0].strip()
            for line in result.stdout.splitlines()
            if line.strip()
        }
    )


async def _recover_terminal_nonexecution_evidence(
    core_session: Any,
    core_job: Any,
) -> bool | None:
    """Seal producer-owned zero-use evidence for an exact historical pre-spawn terminal Job."""

    from database import Job

    terminal_status = str(getattr(core_job, "status", "") or "").strip().lower()
    if terminal_status not in {"failed", "cancelled", "canceled"}:
        return False
    completed_at = getattr(core_job, "completed_at", None)
    if isinstance(completed_at, datetime):
        finished_at = completed_at.isoformat(timespec="microseconds") + "Z"
    elif isinstance(completed_at, str) and completed_at:
        finished_at = completed_at
    else:
        raise ResourceUsageEvidenceError(
            "historical pre-spawn terminal timestamp is unavailable"
        )
    original_params = dict(getattr(core_job, "params", {}) or {})
    original_owner = getattr(core_job, "nextflow_run_id", None)
    original_error = getattr(core_job, "error_message", None)
    matched_units = await asyncio.to_thread(_systemd_units_for_job, str(core_job.id))
    observed_at = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )
    absence_receipt = build_historical_owner_absence_receipt(
        core_job,
        observed_at=observed_at,
        matched_units=matched_units,
    )
    candidate = SimpleNamespace(
        id=str(core_job.id),
        status=getattr(core_job, "status", None),
        completed_at=completed_at,
        error_message=original_error,
        nextflow_run_id=original_owner,
        params={
            **original_params,
            HISTORICAL_OWNER_ABSENCE_PARAM: absence_receipt,
        },
    )
    recovered = attach_pre_spawn_nonexecution_receipt(
        candidate,
        finished_at=finished_at,
        owner_absence_receipt=absence_receipt,
    )
    transition = await core_session.execute(
        update(Job)
        .where(
            Job.id == str(core_job.id),
            Job.status == getattr(core_job, "status", None),
            Job.completed_at == completed_at,
            Job.error_message == original_error,
            Job.nextflow_run_id == original_owner,
            Job.params == original_params,
        )
        .values(params=recovered)
        .execution_options(synchronize_session=False)
    )
    if int(transition.rowcount or 0) != 1:
        await core_session.rollback()
        if hasattr(core_session, "refresh"):
            await core_session.refresh(core_job)
        return None
    await core_session.commit()
    core_job.params = recovered
    if hasattr(core_session, "refresh"):
        await core_session.refresh(core_job)
    return True


async def reconcile_startup_admissions(
    session: AsyncSession,
    core_session: AsyncSession | None = None,
) -> int:
    rows = list(
        (
            await session.scalars(
                select(ExperimentResourceAdmission).where(
                    ExperimentResourceAdmission.state.in_(ACTIVE_ADMISSION_STATES)
                )
            )
        ).all()
    )
    pending_evidence = 0
    for row in rows:
        attempt = await session.get(ExperimentRunAttempt, row.run_attempt_id or "")
        accepted = await _accepted_persisted_resource_evidence(session, row)
        if not accepted and core_session is not None and attempt is not None:
            from database import Job

            core_job = await core_session.get(Job, str(row.canonical_job_id or ""))
            if core_job is not None and str(core_job.status).lower() in {
                "completed",
                "succeeded",
                "awaiting_input",
                "failed",
                "cancelled",
                "canceled",
            }:
                try:
                    await persist_producer_resource_usage_evidence(
                        session,
                        core_job=core_job,
                        run_attempt_id=attempt.resource_id,
                    )
                    accepted = True
                except ResourceUsageEvidenceUnavailable:
                    try:
                        recovered = await _recover_terminal_nonexecution_evidence(
                            core_session,
                            core_job,
                        )
                        if recovered:
                            await persist_producer_resource_usage_evidence(
                                session,
                                core_job=core_job,
                                run_attempt_id=attempt.resource_id,
                            )
                            accepted = True
                        else:
                            accepted = False
                    except (
                        ResourceUsageEvidenceError,
                        ResourceUsageEvidenceUnavailable,
                    ):
                        accepted = False
        if accepted:
            await release_attempt_admissions(
                session,
                [str(row.run_attempt_id)],
                reason="startup-accepted-resource-evidence",
            )
            row.reconciled_at = now()
            row.recovery_evidence_json = canonical_json(
                {
                    "schema": "bms.resource-admission-recovery.v1",
                    "disposition": "accepted_evidence",
                }
            )
            row.updated_at = now()
            continue
        if attempt is None or attempt.state in {"completed", "failed", "cancelled"}:
            row.recovery_evidence_json = canonical_json(
                {
                    "schema": "bms.resource-admission-recovery.v1",
                    "disposition": "producer_resource_evidence_pending",
                    "attempt_present": attempt is not None,
                    "attempt_state": attempt.state if attempt else None,
                    "canonical_job_id": row.canonical_job_id,
                }
            )
            row.updated_at = now()
            pending_evidence += 1
    await session.flush()
    return pending_evidence


_SECRET_LOG_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|authorization)\b(\s*[:=]\s*)([^\s,;]+)"
)


def _public_operational_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if any(marker in str(key).lower() for marker in ("password", "passwd", "token", "secret", "api_key", "api-key", "authorization"))
                else _public_operational_value(child)
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_public_operational_value(child) for child in value]
    if isinstance(value, str):
        return _SECRET_LOG_ASSIGNMENT.sub(r"\1\2[REDACTED]", value.replace("\x00", ""))
    return value


async def append_attempt_log_chunk(
    session: AsyncSession,
    *,
    attempt_id: str,
    stream_name: str,
    content: str,
    close: bool = False,
) -> ExperimentLogChunk:
    """Append one bounded, redacted, idempotent chunk to an attempt-owned log stream."""
    attempt = await session.get(ExperimentRunAttempt, attempt_id)
    if attempt is None:
        raise NotFound("run attempt not found")
    normalized_stream = stream_name.strip().lower()
    if not normalized_stream or len(normalized_stream) > 64 or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", normalized_stream):
        raise ValidationFailure("invalid attempt log stream name")
    public_text = str(_public_operational_value(content))
    encoded = public_text.encode("utf-8")
    if len(encoded) > 16 * 1024:
        raise ValidationFailure("attempt log chunk exceeds 16 KiB")
    content_sha256 = hashlib.sha256(encoded).hexdigest()
    stream_id = "log-" + hashlib.sha256(f"{attempt_id}\x00{normalized_stream}".encode("utf-8")).hexdigest()
    stream = await session.get(ExperimentLogStream, stream_id)
    if stream is None:
        session.add(
            ExperimentResource(
                id=stream_id,
                kind="log_stream",
                workspace_id=attempt.workspace_id,
                lifecycle_owner_id=attempt.resource_id,
                created_at=now(),
            )
        )
        await session.flush()
        stream = ExperimentLogStream(
            resource_id=stream_id,
            attempt_id=attempt.resource_id,
            stream_name=normalized_stream,
            state="open",
            created_at=now(),
        )
        session.add(stream)
        await session.flush()
    elif stream.attempt_id != attempt.resource_id or stream.stream_name != normalized_stream:
        raise ValidationFailure("attempt log stream identity conflict")
    last = await session.scalar(
        select(ExperimentLogChunk)
        .where(ExperimentLogChunk.stream_id == stream_id)
        .order_by(ExperimentLogChunk.sequence_number.desc())
        .limit(1)
    )
    if last is not None and last.content_sha256 == content_sha256:
        if close and stream.state != "closed":
            stream.state = "closed"
            stream.closed_at = now()
        return last
    if stream.state == "closed":
        raise ValidationFailure("attempt log stream is closed")
    sequence = (int(last.sequence_number) + 1) if last is not None else 0
    if sequence >= 10_000:
        raise ValidationFailure("attempt log stream chunk limit reached")
    chunk = ExperimentLogChunk(
        stream_id=stream_id,
        sequence_number=sequence,
        content_sha256=content_sha256,
        content_text=public_text,
        created_at=now(),
    )
    session.add(chunk)
    if close:
        stream.state = "closed"
        stream.closed_at = now()
    await session.flush()
    return chunk


async def persist_attempt_validation(
    session: AsyncSession,
    *,
    attempt_id: str,
    validator_name: str,
    validator_version: str,
    outcome: str,
    reason: str,
    input_graph_sha256: str,
    receipt: Mapping[str, Any],
) -> ExperimentValidation:
    """Persist an immutable, public validation receipt and its typed lineage edge."""
    attempt = await session.get(ExperimentRunAttempt, attempt_id)
    if attempt is None:
        raise NotFound("run attempt not found")
    requested_outcome = outcome.strip().lower()
    outcome_map = {
        "passed": "valid",
        "failed": "invalid",
        "review": "incomplete",
    }
    normalized_outcome = outcome_map.get(requested_outcome)
    if normalized_outcome is None:
        raise ValidationFailure("invalid validation outcome")
    name = validator_name.strip()
    version = validator_version.strip()
    if not name or len(name) > 255 or not version or len(version) > 64:
        raise ValidationFailure("invalid validator identity")
    public_reason = str(_public_operational_value(reason)).strip()
    if not public_reason or len(public_reason) > 1024:
        raise ValidationFailure("validation reason must contain 1 to 1024 characters")
    if not re.fullmatch(r"[0-9a-f]{64}", input_graph_sha256):
        raise ValidationFailure("validation input graph SHA-256 is invalid")
    public_receipt = _public_operational_value(dict(receipt))
    receipt_json = canonical_json(public_receipt)
    if len(receipt_json.encode("utf-8")) > 1024 * 1024:
        raise ValidationFailure("validation receipt exceeds 1 MiB")
    receipt_sha256 = sha256_text(receipt_json)
    validation_id = "validation-" + hashlib.sha256(
        f"{attempt_id}\x00{name}\x00{version}\x00{input_graph_sha256}\x00{public_reason}\x00{receipt_sha256}".encode("utf-8")
    ).hexdigest()
    existing = await session.get(ExperimentValidation, validation_id)
    if existing is not None:
        if (
            existing.subject_resource_id != attempt.resource_id
            or existing.validator_name != name
            or existing.validator_version != version
            or existing.outcome != normalized_outcome
            or existing.input_graph_sha256 != input_graph_sha256
            or existing.receipt_sha256 != receipt_sha256
        ):
            raise ValidationFailure("validation identity conflict")
        return existing
    session.add(
        ExperimentResource(
            id=validation_id,
            kind="validation",
            workspace_id=attempt.workspace_id,
            lifecycle_owner_id=attempt.resource_id,
            created_at=now(),
        )
    )
    validation = ExperimentValidation(
        resource_id=validation_id,
        subject_resource_id=attempt.resource_id,
        validator_name=name,
        validator_version=version,
        outcome=normalized_outcome,
        input_graph_sha256=input_graph_sha256,
        receipt_json=receipt_json,
        receipt_sha256=receipt_sha256,
        created_at=now(),
    )
    session.add(validation)
    await session.flush()
    edge_key = f"{name}:{version}:{receipt_sha256}"
    edge_id = "lineage-" + hashlib.sha256(
        f"{attempt.resource_id}\x00{validation_id}\x00validated_by\x00{edge_key}".encode("utf-8")
    ).hexdigest()
    session.add(
        ExperimentLineageEdge(
            id=edge_id,
            workspace_id=attempt.workspace_id,
            source_resource_id=attempt.resource_id,
            target_resource_id=validation_id,
            edge_mode="validated_by",
            edge_key=edge_key,
            metadata_json=canonical_json(
                {
                    "validator_name": name,
                    "validator_version": version,
                    "reason": public_reason,
                    "input_graph_sha256": input_graph_sha256,
                }
            ),
            created_at=now(),
        )
    )
    await session.flush()
    return validation


async def operational_status(session: AsyncSession) -> dict[str, Any]:
    current = datetime.now(timezone.utc)
    queue = (await session.execute(select(func.count(), func.min(ExperimentResourceAdmission.queued_at)).where(ExperimentResourceAdmission.state == "queued"))).one()
    oldest_age = None
    if queue[1]:
        oldest_age = max(0, int((current - datetime.fromisoformat(str(queue[1]).replace("Z", "+00:00"))).total_seconds()))
    reserved = (await session.execute(select(func.coalesce(func.sum(ExperimentResourceAdmission.cpu_threads), 0), func.coalesce(func.sum(ExperimentResourceAdmission.dram_bytes), 0)).where(ExperimentResourceAdmission.state.in_(ACTIVE_ADMISSION_STATES)))).one()
    active_admissions = list(
        (
            await session.scalars(
                select(ExperimentResourceAdmission).where(
                    ExperimentResourceAdmission.state.in_(ACTIVE_ADMISSION_STATES)
                )
            )
        ).all()
    )
    attempt_ids = [row.run_attempt_id for row in active_admissions if row.run_attempt_id]
    attempts = {
        row.resource_id: row
        for row in (
            (
                await session.scalars(
                    select(ExperimentRunAttempt).where(ExperimentRunAttempt.resource_id.in_(attempt_ids))
                )
            ).all()
            if attempt_ids
            else []
        )
    }
    actual_cpu_threads = 0
    actual_dram_bytes = 0
    actual_evidence_missing = 0
    allocation_drift = 0
    for admission in active_admissions:
        attempt = attempts.get(admission.run_attempt_id or "")
        try:
            runtime = json.loads(attempt.runtime_identity_json) if attempt and attempt.runtime_identity_json else {}
        except (TypeError, json.JSONDecodeError):
            runtime = {}
        observed_cpu = runtime.get("actual_cpu_threads")
        observed_dram = runtime.get("actual_dram_bytes")
        if (
            not isinstance(observed_cpu, int)
            or isinstance(observed_cpu, bool)
            or observed_cpu < 0
            or not isinstance(observed_dram, int)
            or isinstance(observed_dram, bool)
            or observed_dram < 0
        ):
            actual_evidence_missing += 1
            continue
        actual_cpu_threads += observed_cpu
        actual_dram_bytes += observed_dram
        if observed_cpu > admission.cpu_threads or observed_dram > admission.dram_bytes:
            allocation_drift += 1
    failed_validations = int(
        await session.scalar(
            select(func.count()).select_from(ExperimentValidation).where(
                ExperimentValidation.outcome == "failed"
            )
        )
        or 0
    )
    connector = (await session.execute(select(func.count(), func.min(ExperimentDomainConnectorCommand.created_at)).where(ExperimentDomainConnectorCommand.status.in_({"pending", "retryable", "conflicted"})))).one()
    dead_letters = await session.scalar(select(func.count()).select_from(ExperimentDomainConnectorCommand).where(ExperimentDomainConnectorCommand.status == "conflicted"))
    projection = await session.scalar(select(ExperimentSyncState).order_by(ExperimentSyncState.updated_at.desc()).limit(1))
    receipts = list((await session.scalars(select(ExperimentOperationalReceipt).order_by(ExperimentOperationalReceipt.occurred_at.desc(), ExperimentOperationalReceipt.receipt_id.desc()).limit(100))).all())
    latest = {}
    for row in receipts:
        latest.setdefault(row.operation_kind, {"receipt_id": row.receipt_id, "native_identity": row.native_identity, "state": row.state, "occurred_at": row.occurred_at, "verified_at": row.verified_at, "source_revision": row.source_revision})
    return {
        "schema": "bms.ngs-molbio.operational-status.v1",
        "queue": {"depth": int(queue[0]), "oldest_age_seconds": oldest_age},
        "resource_admission": {
            "cpu_threads_reserved": int(reserved[0]),
            "cpu_thread_limit": CPU_THREAD_LIMIT,
            "dram_bytes_reserved": int(reserved[1]),
            "dram_byte_limit": DRAM_BYTE_LIMIT,
            "active_reservations": len(active_admissions),
            "actual_cpu_threads_observed": actual_cpu_threads,
            "actual_dram_bytes_observed": actual_dram_bytes,
            "actual_evidence_missing": actual_evidence_missing,
            "allocation_drift": allocation_drift,
        },
        "verification_failures": failed_validations,
        "connector": {"lagged_or_failed": int(connector[0]), "dead_letters": int(dead_letters or 0), "oldest_pending_at": connector[1]},
        "projection_cursor": ({"state_key": projection.state_key, "local_generation": projection.local_generation, "remote_generation": projection.remote_generation, "pending_changes": projection.pending_changes, "last_success_at": projection.last_success_at, "last_error": projection.last_error, "updated_at": projection.updated_at} if projection else None),
        "provenance": {"backup": latest.get("backup"), "export": latest.get("export"), "restoration": latest.get("restoration")},
        "payload_audit": latest.get("payload_audit"),
        "package_acceptance": latest.get("package_acceptance"),
        "observed_at": current.isoformat(),
    }


def operational_receipt(*, operation_kind: str, native_identity: str, state: str, receipt: Mapping[str, Any], workspace_id: str | None = None, verified_at: str | None = None) -> ExperimentOperationalReceipt:
    body = canonical_json(dict(receipt))
    return ExperimentOperationalReceipt(receipt_id=str(uuid.uuid4()), operation_kind=operation_kind, workspace_id=workspace_id, native_identity=native_identity, state=state, receipt_json=body, receipt_sha256=sha256_text(body), source_revision=str(receipt.get("source_revision") or receipt.get("source_commit") or "") or None, occurred_at=now(), verified_at=verified_at)


async def persist_payload_audit_operational_receipt(
    session: AsyncSession,
    retention_receipt: Mapping[str, Any],
) -> ExperimentOperationalReceipt:
    """Idempotently project one immutable retained payload audit into operations.

    The retained store is written first.  The retained audit digest is then used
    as this row's deterministic identity, allowing an interrupted cross-store
    publication to be reconciled from the exact retained receipt without a new
    scan or mutation of either immutable record.
    """

    try:
        receipt = validate_retained_payload_ownership_receipt(retention_receipt)
    except PayloadOwnershipError as exc:
        raise ValidationFailure("payload audit retention receipt is invalid or digest-divergent") from exc
    audit_id = str(receipt.get("audit_id") or "")
    source_commit = str(receipt.get("source_commit") or "")
    retained_at = str(receipt.get("retained_at") or "").strip()

    body = canonical_json(receipt)
    receipt_id = f"payload-audit-{audit_id}"
    expected = {
        "receipt_id": receipt_id,
        "operation_kind": "payload_audit",
        "workspace_id": None,
        "native_identity": audit_id,
        "state": "verified",
        "receipt_json": body,
        "receipt_sha256": sha256_text(body),
        "source_revision": source_commit,
        "occurred_at": retained_at,
        "verified_at": retained_at,
    }
    existing_native = list(
        (
            await session.scalars(
                select(ExperimentOperationalReceipt).where(
                    ExperimentOperationalReceipt.operation_kind == "payload_audit",
                    ExperimentOperationalReceipt.native_identity == audit_id,
                )
            )
        ).all()
    )
    if existing_native:
        if len(existing_native) != 1 or any(
            getattr(existing_native[0], field) != value for field, value in expected.items()
        ):
            raise ValidationFailure("payload audit operational receipt binding conflict")
        return existing_native[0]

    await session.execute(
        text(
            "INSERT OR IGNORE INTO operational_receipts "
            "(receipt_id, operation_kind, workspace_id, native_identity, state, receipt_json, "
            "receipt_sha256, source_revision, occurred_at, verified_at) "
            "VALUES (:receipt_id, :operation_kind, :workspace_id, :native_identity, :state, "
            ":receipt_json, :receipt_sha256, :source_revision, :occurred_at, :verified_at)"
        ),
        expected,
    )
    persisted = await session.get(ExperimentOperationalReceipt, receipt_id)
    if persisted is None or any(getattr(persisted, field) != value for field, value in expected.items()):
        raise ValidationFailure("payload audit operational receipt identity conflict")
    return persisted


__all__ = [
    "DATASET_KINDS", "InvalidLifecycleTransition", "ResourceAdmissionDenied", "ResourceUsageEvidenceUnavailable", "append_attempt_log_chunk", "create_project_dataset", "enabled_dataset_kind_records",
    "decode_cursor", "encode_cursor", "operational_receipt", "operational_status",
    "persist_payload_audit_operational_receipt",
    "persist_admission_refusal", "persist_attempt_validation", "persist_never_launched_resource_usage_evidence", "persist_producer_resource_usage_evidence", "reconcile_startup_admissions", "release_attempt_admissions", "require_dataset_read",
    "resource_admission_handoff_for_attempt", "require_domain_hierarchy", "require_mutable_dataset", "reserve_run_group", "revise_project_dataset",
    "set_project_dataset_lifecycle",
]
