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

from component_runtime import SourceIdentity

from experiment_models import (
    ExperimentAggregateHead,
    ExperimentAuditEvent,
    ExperimentDatasetRevisionMember,
    ExperimentDispatchOutbox,
    ExperimentExternalEntityReceipt,
    ExperimentIdempotencyClaim,
    ExperimentLineageEdge,
    ExperimentLogChunk,
    ExperimentLogStream,
    ExperimentOperationalReceipt,
    ExperimentResource,
    ExperimentRevision,
    ExperimentRunAttempt,
    ExperimentRunGroup,
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
from build_identity import deployed_source_identity
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
from biomodstack_local_resources import applied_local_policy
ACTIVE_ADMISSION_STATES = frozenset({"admitted", "queued"})


def _runtime_source_authority(*, source_identity: SourceIdentity | None = None) -> tuple[str, str]:
    if source_identity is not None:
        # Preserve the execution plan source when one is already bound.
        return source_identity.revision, source_identity.tree
    return deployed_source_identity()


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
    request_body = {"name": normalized_name, "dataset_kind": dataset_kind, "change_summary": change_summary.strip()}
    normalized = {"operation": "dataset_create", "project_id": project_id, "experiment_id": experiment_id, "domain_id": domain_id, **request_body}
    digest = sha256_text(canonical_json(normalized))
    scope = "dataset-create:" + sha256_text(canonical_json({"project_id": project_id, "domain_id": domain_id}))
    claim = await session.get(ExperimentIdempotencyClaim, (scope, idempotency_key))
    if claim is not None:
        if claim.request_sha256 != digest:
            raise IdempotencyConflict("idempotency key was reused with a different Dataset request")
        return json.loads(claim.response_json)
    _enabled_dataset_kind(dataset_kind)
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
    if not isinstance(tags, list) or len(tags) > 16 or any(type(tag) is not str for tag in tags) or len(tags) != len(set(tags)):
        raise ValidationFailure("dataset metadata tags are invalid")
    for field, maximum in (("display_label", 255), ("group_label", 128), ("condition_label", 128)):
        item = result.get(field)
        if item is not None and (type(item) is not str or not 1 <= len(item) <= maximum):
            raise ValidationFailure("dataset metadata labels are invalid")
    if len(canonical_json(result).encode("utf-8")) > 2048:
        raise ValidationFailure("dataset metadata exceeds 2 KiB")
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
    head = await require_dataset_read(session, project_id=project_id, domain_id=domain_id, dataset_id=dataset_id)
    normalized = {"operation": "dataset_revision_create", "project_id": project_id, "experiment_id": experiment_id, "domain_id": domain_id, "dataset_id": dataset_id, "expected_head_generation": expected_head_generation, "change_summary": change_summary.strip(), "members": members}
    digest = sha256_text(canonical_json(normalized))
    scope = "dataset-revision-create:" + sha256_text(canonical_json({"dataset_id": dataset_id}))
    claim = await session.get(ExperimentIdempotencyClaim, (scope, idempotency_key))
    if claim is not None:
        if claim.request_sha256 != digest:
            raise IdempotencyConflict("idempotency key was reused with a different Dataset revision")
        return json.loads(claim.response_json)
    if head.dataset_kind is None:
        raise ValidationFailure("legacy null-kind Dataset is read-only")
    _enabled_dataset_kind(head.dataset_kind)
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
    head = await require_dataset_read(
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
    if head.dataset_kind is None:
        raise ValidationFailure("legacy null-kind Dataset is read-only")
    _enabled_dataset_kind(head.dataset_kind)
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
    if not isinstance(cpu, int) or isinstance(cpu, bool) or cpu < 1 or cpu > applied_local_policy().cpu_threads:
        raise ResourceAdmissionDenied("invalid_cpu_request", f"effective CPU request is outside 1..{applied_local_policy().cpu_threads} threads", [])
    if not isinstance(dram_gib, (int, float)) or isinstance(dram_gib, bool) or dram_gib <= 0:
        raise ResourceAdmissionDenied("invalid_dram_request", "effective DRAM request must be positive", [])
    dram_bytes = int(dram_gib * 1024**3)
    if dram_bytes > applied_local_policy().memory_bytes:
        raise ResourceAdmissionDenied("resource_admission_denied", "effective DRAM request exceeds the applied local RAM budget", [])
    if gpu is not None and (not isinstance(gpu, int) or isinstance(gpu, bool) or gpu < 0):
        raise ResourceAdmissionDenied("gpu_inventory_degraded", "GPU request cannot be matched to a valid scheduler GPU index", [])
    gpu_uuid = None
    if gpu is not None:
        try:
            from services.gpu_metadata import query_smi_gpu_map

            inventory = query_smi_gpu_map()
            match = inventory.get(gpu)
        except Exception as exc:
            raise ResourceAdmissionDenied("gpu_inventory_degraded", "live GPU inventory is unavailable; GPU admission fails closed", []) from exc
        if not isinstance(match, dict):
            raise ResourceAdmissionDenied("gpu_inventory_degraded", "requested GPU is absent in live inventory", [])
        gpu_uuid = str(match.get("uuid") or "").strip()
        if not gpu_uuid:
            raise ResourceAdmissionDenied("gpu_inventory_degraded", "live GPU UUID authority is unavailable", [])
    return {"cpu_threads": cpu, "dram_bytes": dram_bytes, "gpu_index": gpu, "gpu_uuid": gpu_uuid}


async def release_attempt_admissions(session: AsyncSession, attempt_ids: list[str], *, reason: str) -> None:
    """Retired in M6 (R35); controller-local CPU/DRAM placement is tracked directly on canonical Job rows."""
    return None


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


_ATTEMPT_LOG_CHUNK_LIMIT = 10_000


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
    # The cap bounds open streams. The closing chunk is always accepted: it is
    # at most one more row, and refusing it left terminal attempts unable to
    # project, retrying the whole reconcile every sweep.
    if sequence >= _ATTEMPT_LOG_CHUNK_LIMIT and not close:
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
    failed_validations = int(
        await session.scalar(
            select(func.count()).select_from(ExperimentValidation).where(
                ExperimentValidation.outcome == "failed"
            )
        )
        or 0
    )
    connector = (0, None)
    dead_letters = 0
    receipts = list((await session.scalars(select(ExperimentOperationalReceipt).order_by(ExperimentOperationalReceipt.occurred_at.desc(), ExperimentOperationalReceipt.receipt_id.desc()).limit(100))).all())
    latest = {}
    for row in receipts:
        latest.setdefault(row.operation_kind, {"receipt_id": row.receipt_id, "native_identity": row.native_identity, "state": row.state, "occurred_at": row.occurred_at, "verified_at": row.verified_at, "source_revision": row.source_revision})
    return {
        "schema": "bms.ngs-molbio.operational-status.v1",
        "queue": {"depth": 0, "oldest_age_seconds": None},
        "resource_admission": {
            "cpu_threads_reserved": 0,
            "cpu_thread_limit": applied_local_policy().cpu_threads,
            "dram_bytes_reserved": 0,
            "dram_byte_limit": applied_local_policy().memory_bytes,
            "active_reservations": 0,
            "actual_cpu_threads_observed": 0,
            "actual_dram_bytes_observed": 0,
            "actual_evidence_missing": 0,
            "allocation_drift": 0,
        },
        "verification_failures": failed_validations,
        "connector": {"lagged_or_failed": int(connector[0]), "dead_letters": int(dead_letters or 0), "oldest_pending_at": connector[1]},
        "projection_cursor": None,
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
    "persist_attempt_validation", "release_attempt_admissions", "require_dataset_read",
    "require_domain_hierarchy", "require_mutable_dataset", "revise_project_dataset",
    "set_project_dataset_lifecycle",
]
