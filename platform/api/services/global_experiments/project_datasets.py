"""Protein Project Dataset authority over verified native receipts.

Dataset revisions retain only immutable receipt identity, digests, role, ordinal,
and bounded display metadata. Protein sequences, structures, ensembles,
trajectories, landscapes, metrics, and model payloads remain in their native
stores and are revalidated through their producer-owned adapters.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import (
    ExperimentAggregateHead,
    ExperimentExternalEntityReceipt,
    ExperimentIdempotencyClaim,
    ExperimentLineageEdge,
    ExperimentRevision,
)
from experiment_services import (
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


PROTEIN_DOMAIN_KIND = "protein_in_silico"
MAX_DATASET_MEMBERS = 10_000

# Exact intersections with the frozen shared Dataset and adapter registries.
# The three omitted frozen kinds remain explicitly unavailable below because
# current source has no producer-native target/context/saved-review adapter.
PROTEIN_DATASET_KINDS: dict[str, dict[str, frozenset[str]]] = {
    "protein.generated_candidate_cohort.v1": {
        "bms.core.protein-result-reference.adapter.v1": frozenset({"protein_generated_candidate"}),
        "bms.core-job.protein_local_redesign.adapter.v1": frozenset({"protein_generated_candidate_cohort"}),
    },
    "protein.selected_finalist_cohort.v1": {
        "bms.core.protein-result-reference.adapter.v1": frozenset({"protein_selected_finalist"}),
    },
    "protein.structure_prediction_validation_result_cohort.v1": {
        "bms.core.protein-result-reference.adapter.v1": frozenset(
            {"protein_structure_prediction_result", "protein_structure_validation_result"}
        ),
    },
    "protein.cm_ensemble_conformer_cohort.v1": {
        "bms.cm.protenix_v2.adapter.v1": frozenset({"protein_cm_ensemble"}),
        "bms.cm.confornets.adapter.v1": frozenset({"protein_cm_ensemble"}),
    },
    "protein.md_replica_analysis_cohort.v1": {
        "bms.md.result-reference.adapter.v1": frozenset({"protein_md_run_result"}),
    },
    "protein.frustrampnn_landscape_guidance_cohort.v1": {
        "bms.frustrampnn.result-reference.adapter.v1": frozenset({"protein_frustrampnn_landscape"}),
        "bms.frustrampnn.guidance-reference.adapter.v1": frozenset({"protein_frustrampnn_guidance"}),
    },
    "protein.compatible_comparison_cohort.v1": {
        "bms.frustrampnn.comparison-reference.adapter.v1": frozenset({"protein_compatible_comparison"}),
    },
}

UNAVAILABLE_PROTEIN_DATASET_KINDS = frozenset(
    {
        "protein.target_set.v1",
        "protein.template_motif_partner_control_set.v1",
        "protein.saved_review_filter_selection.v1",
    }
)


class InvalidProteinDatasetLifecycle(ValidationFailure):
    """A Protein Dataset lifecycle transition has no valid state edge."""


def protein_dataset_kind_records() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return only frozen Protein kinds backed by exact registered adapters."""

    document = contract_registry("dataset")
    enabled: list[dict[str, Any]] = []
    observed_unavailable: set[str] = set()
    for record in document["entries"]:
        dataset_kind = record.get("dataset_kind")
        if dataset_kind in UNAVAILABLE_PROTEIN_DATASET_KINDS:
            if (
                record.get("owner_contract_state") != "unavailable"
                or record.get("allowed_members") != []
                or record.get("compatibility_rules")
                != ["no_immutable_producer_native_member_contract"]
            ):
                raise NgsMolBioCapabilityError(
                    f"unavailable Protein Dataset kind exposes unsupported authority: {dataset_kind}"
                )
            observed_unavailable.add(str(dataset_kind))
            continue
        expected = PROTEIN_DATASET_KINDS.get(str(dataset_kind))
        if expected is None:
            continue
        declared = {
            member["adapter_id"]: frozenset(member["allowed_roles"])
            for member in (record.get("allowed_members") or [])
        }
        if (
            record.get("owner_contract_state") != "closed"
            or record.get("allowed_domain_kinds") != [PROTEIN_DOMAIN_KIND]
            or record.get("minimum_members") != 0
            or record.get("maximum_members") != MAX_DATASET_MEMBERS
            or declared != expected
        ):
            raise NgsMolBioCapabilityError(
                f"Protein Dataset kind diverges from its frozen contract: {dataset_kind}"
            )
        try:
            adapters = [
                (member, registry.get(member["adapter_id"]))
                for member in record["allowed_members"]
            ]
        except AdapterError as exc:
            raise NgsMolBioCapabilityError(
                f"Protein Dataset kind has no registered adapter: {dataset_kind}"
            ) from exc
        if any(
            adapter.domain_kind != PROTEIN_DOMAIN_KIND
            or adapter.entity_kind != member["receipt_kind"]
            for member, adapter in adapters
        ):
            raise NgsMolBioCapabilityError(
                f"Protein Dataset kind adapter identity is incompatible: {dataset_kind}"
            )
        enabled.append({**record, "enabled": True})
    if set(PROTEIN_DATASET_KINDS) != {row["dataset_kind"] for row in enabled}:
        raise NgsMolBioCapabilityError("Protein Dataset kind denominator is incomplete")
    if observed_unavailable != set(UNAVAILABLE_PROTEIN_DATASET_KINDS):
        raise NgsMolBioCapabilityError("unavailable Protein Dataset kind denominator is incomplete")
    return document, enabled


def require_protein_dataset_kind(dataset_kind: str) -> dict[str, Any]:
    try:
        _document, enabled = protein_dataset_kind_records()
    except NgsMolBioCapabilityError as exc:
        raise ValidationFailure("Dataset kind authority is unavailable") from exc
    record = next((row for row in enabled if row["dataset_kind"] == dataset_kind), None)
    if record is None:
        raise ValidationFailure("unsupported_dataset_kind")
    return record


async def require_protein_domain_hierarchy(
    session: AsyncSession,
    *,
    project_id: str,
    experiment_id: str,
    domain_id: str,
) -> tuple[ExperimentAggregateHead, ExperimentAggregateHead, ExperimentAggregateHead]:
    project = await session.get(ExperimentAggregateHead, project_id)
    experiment = await session.get(ExperimentAggregateHead, experiment_id)
    domain = await session.get(ExperimentAggregateHead, domain_id)
    if project is None or project.aggregate_kind != "workspace":
        raise NotFound("Project not found")
    if (
        experiment is None
        or experiment.aggregate_kind != "experiment"
        or experiment.workspace_id != project_id
        or experiment.parent_id != project_id
    ):
        raise NotFound("Global Experiment not found in Project")
    if (
        domain is None
        or domain.aggregate_kind != "domain_experiment"
        or domain.workspace_id != project_id
        or domain.parent_id != experiment_id
    ):
        raise NotFound("Domain Experiment not found in Global Experiment")
    revision = await session.get(ExperimentRevision, domain.current_revision_id or "")
    payload = json.loads(revision.canonical_payload) if revision is not None else {}
    if payload.get("domain_kind") != PROTEIN_DOMAIN_KIND:
        raise ValidationFailure("unsupported_dataset_kind")
    return project, experiment, domain


async def require_protein_dataset(
    session: AsyncSession,
    *,
    project_id: str,
    domain_id: str,
    dataset_id: str,
    mutable: bool = False,
) -> ExperimentAggregateHead:
    head = await session.get(ExperimentAggregateHead, dataset_id)
    if (
        head is None
        or head.aggregate_kind != "dataset"
        or head.workspace_id != project_id
        or head.parent_id != domain_id
    ):
        raise NotFound("Dataset not found in Domain")
    if mutable:
        if head.dataset_kind is None:
            raise ValidationFailure("legacy null-kind Dataset is read-only")
        require_protein_dataset_kind(head.dataset_kind)
    return head


def _head_document(
    head: ExperimentAggregateHead,
    *,
    project_id: str,
    experiment_id: str,
    domain_id: str,
    request_sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": "bms.dataset-head.v1",
        "project_id": project_id,
        "global_experiment_id": experiment_id,
        "domain_id": domain_id,
        "dataset_id": head.aggregate_id,
        "name": head.display_name,
        "dataset_kind": head.dataset_kind,
        "current_revision_id": head.current_revision_id,
        "head_generation": head.head_generation,
        "lifecycle_state": head.lifecycle_state,
        "normalized_request_sha256": request_sha256,
        "created_at": head.created_at,
        "updated_at": head.updated_at,
    }


async def create_protein_dataset(
    session: AsyncSession,
    *,
    project_id: str,
    experiment_id: str,
    domain_id: str,
    name: str,
    dataset_kind: str,
    change_summary: str,
    actor: str,
    idempotency_key: str,
) -> dict[str, Any]:
    await require_protein_domain_hierarchy(
        session, project_id=project_id, experiment_id=experiment_id, domain_id=domain_id
    )
    require_protein_dataset_kind(dataset_kind)
    normalized_name = name.strip()
    normalized_summary = change_summary.strip()
    if not normalized_name or not normalized_summary:
        raise ValidationFailure("Dataset name and change_summary are required")
    normalized = {
        "operation": "dataset_create",
        "project_id": project_id,
        "experiment_id": experiment_id,
        "domain_id": domain_id,
        "name": normalized_name,
        "dataset_kind": dataset_kind,
        "change_summary": normalized_summary,
    }
    digest = sha256_text(canonical_json(normalized))
    scope = "protein-dataset-create:" + sha256_text(
        canonical_json({"project_id": project_id, "domain_id": domain_id})
    )
    claim = await session.get(ExperimentIdempotencyClaim, (scope, idempotency_key))
    if claim is not None:
        if claim.request_sha256 != digest:
            raise IdempotencyConflict("idempotency key was reused with a different Dataset request")
        return json.loads(claim.response_json)
    head = await create_dataset(
        session, project_id, normalized_name, dataset_kind, experiment_id=domain_id
    )
    head.lifecycle_state = "active"
    head.description = ""
    head.updated_at = now()
    response = _head_document(
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
            result_resource_id=head.aggregate_id,
            response_json=canonical_json(response),
            created_at=now(),
        )
    )
    add_audit_event(
        session,
        workspace_id=project_id,
        resource_id=head.aggregate_id,
        event_type="dataset_created",
        generation=0,
        payload={
            "dataset_kind": dataset_kind,
            "change_summary": normalized_summary,
            "normalized_request_sha256": digest,
            "actor_id": actor,
        },
    )
    await session.flush()
    return response


def _bounded_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"display_label", "group_label", "condition_label", "tags"}
    if set(value) - allowed:
        raise ValidationFailure("dataset metadata has unsupported fields")
    result = dict(value)
    tags = result.get("tags", [])
    if not isinstance(tags, list) or len(tags) > 16 or len(tags) != len(set(tags)):
        raise ValidationFailure("dataset metadata tags are invalid")
    if len(canonical_json(result).encode("utf-8")) > 2048:
        raise ValidationFailure("dataset metadata exceeds 2 KiB")
    forbidden = (
        "sequence", "structure", "trajectory", "ensemble", "landscape", "metric",
        "manifest", "payload", "base64", "digest", "path", "uri",
    )
    if any(token in str(item).lower() for item in result.values() for token in forbidden):
        raise ValidationFailure("dataset_metadata_payload_forbidden")
    return result


async def verify_protein_dataset_member(
    session: AsyncSession,
    core_session: AsyncSession,
    *,
    project_id: str,
    domain_id: str,
    dataset_kind: str,
    item: Mapping[str, Any],
    ordinal: int,
) -> dict[str, Any]:
    if item.get("ordinal") != ordinal:
        raise ValidationFailure("Dataset member ordinal must equal its array position")
    receipt_id = str(item.get("receipt_id") or "")
    role = str(item.get("role") or "")
    receipt = await session.get(ExperimentExternalEntityReceipt, receipt_id)
    if receipt is None or receipt.workspace_id != project_id or receipt.availability != "available":
        raise ValidationFailure("unsupported_dataset_member")
    allowed = PROTEIN_DATASET_KINDS[dataset_kind]
    if receipt.verification_authority not in allowed or role not in allowed[receipt.verification_authority]:
        raise ValidationFailure("unsupported_dataset_member")
    attached = await session.scalar(
        select(ExperimentLineageEdge.id).where(
            ExperimentLineageEdge.workspace_id == project_id,
            ExperimentLineageEdge.source_resource_id == domain_id,
            ExperimentLineageEdge.target_resource_id == receipt_id,
        )
    )
    if attached is None:
        raise ValidationFailure("Dataset member belongs to another Domain or is not attached")
    try:
        persisted = json.loads(receipt.acknowledgement_json or "{}")
        fresh = await registry.get(receipt.verification_authority).verify(
            core_session, receipt.entity_id
        )
    except (json.JSONDecodeError, AdapterError) as exc:
        raise ValidationFailure(f"Dataset member native authority failed: {exc}") from exc
    exact_fields = (
        "store_id", "entity_kind", "entity_id", "entity_revision_id",
        "content_digest", "contract_digest", "verifier_id", "reopen_uri",
    )
    if any(fresh.get(key) != persisted.get(key) for key in exact_fields):
        raise ValidationFailure(
            "Dataset member immutable revision, generation, digest, or native bytes changed"
        )
    if (
        receipt.store_id,
        receipt.entity_kind,
        receipt.entity_id,
        receipt.generation_or_revision,
        receipt.content_digest,
        receipt.verification_authority,
    ) != (
        fresh.get("store_id"),
        fresh.get("entity_kind"),
        fresh.get("entity_id"),
        str(fresh.get("entity_revision_id")),
        fresh.get("content_digest"),
        fresh.get("verifier_id"),
    ):
        raise ValidationFailure("Dataset member persisted receipt diverges from native authority")
    media_type = item.get("media_type")
    native_media_type = fresh.get("metadata", {}).get("media_type")
    if native_media_type is not None and media_type != native_media_type:
        raise ValidationFailure("Dataset member media type disagrees with native authority")
    native_size = fresh.get("metadata", {}).get("size_bytes")
    if native_size is not None and (
        isinstance(native_size, bool)
        or not isinstance(native_size, int)
        or native_size < 0
        or native_size > 2**63 - 1
    ):
        raise ValidationFailure("Dataset member native size is invalid")
    value = {
        "schema": "bms.dataset-member.v1",
        "receipt_id": receipt.id,
        "adapter_id": receipt.verification_authority,
        "store_id": receipt.store_id,
        "entity_kind": receipt.entity_kind,
        "entity_id": receipt.entity_id,
        "native_revision_or_generation": receipt.generation_or_revision,
        "native_content_sha256": receipt.content_digest,
        "role": role,
        "ordinal": ordinal,
        "media_type": media_type,
        "metadata": _bounded_metadata(item.get("metadata") or {}),
        "reopen_uri": fresh["reopen_uri"],
    }
    return {
        "role": role,
        "identity": receipt.id,
        "value": value,
        "size_bytes": native_size,
        "media_type": media_type,
    }


async def revise_protein_dataset(
    session: AsyncSession,
    core_session: AsyncSession,
    *,
    project_id: str,
    experiment_id: str,
    domain_id: str,
    dataset_id: str,
    expected_head_generation: int,
    change_summary: str,
    members: list[Mapping[str, Any]],
    actor: str,
    idempotency_key: str,
) -> dict[str, Any]:
    await require_protein_domain_hierarchy(
        session, project_id=project_id, experiment_id=experiment_id, domain_id=domain_id
    )
    head = await require_protein_dataset(
        session, project_id=project_id, domain_id=domain_id, dataset_id=dataset_id, mutable=True
    )
    contract = require_protein_dataset_kind(str(head.dataset_kind))
    normalized_summary = change_summary.strip()
    if not normalized_summary:
        raise ValidationFailure("Dataset revision change_summary is required")
    if not contract["minimum_members"] <= len(members) <= contract["maximum_members"]:
        raise ValidationFailure("Dataset member cardinality violates its declared registry contract")
    normalized = {
        "operation": "dataset_revision_create",
        "project_id": project_id,
        "experiment_id": experiment_id,
        "domain_id": domain_id,
        "dataset_id": dataset_id,
        "expected_head_generation": expected_head_generation,
        "change_summary": normalized_summary,
        "members": members,
    }
    digest = sha256_text(canonical_json(normalized))
    scope = "protein-dataset-revision-create:" + sha256_text(canonical_json({"dataset_id": dataset_id}))
    claim = await session.get(ExperimentIdempotencyClaim, (scope, idempotency_key))
    if claim is not None:
        if claim.request_sha256 != digest:
            raise IdempotencyConflict("idempotency key was reused with a different Dataset revision")
        return json.loads(claim.response_json)
    if head.lifecycle_state != "active":
        raise ValidationFailure("Dataset is not active")
    if head.head_generation != expected_head_generation:
        raise RevisionConflict("stale_generation")
    pairs = [(str(item.get("receipt_id") or ""), str(item.get("role") or "")) for item in members]
    if len(pairs) != len(set(pairs)):
        raise ValidationFailure("Dataset members contain duplicate receipt and role pairs")
    verified = [
        await verify_protein_dataset_member(
            session,
            core_session,
            project_id=project_id,
            domain_id=domain_id,
            dataset_kind=str(head.dataset_kind),
            item=item,
            ordinal=ordinal,
        )
        for ordinal, item in enumerate(members)
    ]
    revision = await save_dataset_revision(
        session,
        dataset_id,
        {"schema": "bms.dataset-revision.v1", "change_summary": normalized_summary, "members": verified},
        expected_head_generation=expected_head_generation,
    )
    await session.refresh(head)
    head.lifecycle_state = "active"
    head.updated_at = now()
    await session.flush()
    response = {
        "schema": "bms.dataset-revision.v1",
        "project_id": project_id,
        "global_experiment_id": experiment_id,
        "domain_id": domain_id,
        "dataset_id": dataset_id,
        "revision_id": revision.resource_id,
        "revision_number": revision.revision_number,
        "head_generation": head.head_generation,
        "member_count": len(verified),
        "revision_sha256": revision.payload_sha256,
        "normalized_request_sha256": digest,
        "created_at": revision.created_at,
    }
    session.add(
        ExperimentIdempotencyClaim(
            scope=scope,
            idempotency_key=idempotency_key,
            request_sha256=digest,
            result_resource_id=revision.resource_id,
            response_json=canonical_json(response),
            created_at=now(),
        )
    )
    add_audit_event(
        session,
        workspace_id=project_id,
        resource_id=dataset_id,
        event_type="dataset_revision_created",
        generation=head.head_generation,
        payload={
            "revision_id": revision.resource_id,
            "change_summary": normalized_summary,
            "normalized_request_sha256": digest,
            "actor_id": actor,
        },
    )
    await session.flush()
    return response


async def set_protein_dataset_lifecycle(
    session: AsyncSession,
    *,
    project_id: str,
    experiment_id: str,
    domain_id: str,
    dataset_id: str,
    operation: str,
    expected_head_generation: int,
    change_summary: str,
    actor: str,
    idempotency_key: str,
) -> dict[str, Any]:
    await require_protein_domain_hierarchy(
        session, project_id=project_id, experiment_id=experiment_id, domain_id=domain_id
    )
    head = await require_protein_dataset(
        session, project_id=project_id, domain_id=domain_id, dataset_id=dataset_id, mutable=True
    )
    if operation not in {"archive", "restore"}:
        raise ValidationFailure("unsupported Dataset lifecycle operation")
    normalized_summary = change_summary.strip()
    if not normalized_summary:
        raise ValidationFailure("Dataset lifecycle change_summary is required")
    normalized = {
        "operation": f"dataset_{operation}",
        "project_id": project_id,
        "experiment_id": experiment_id,
        "domain_id": domain_id,
        "dataset_id": dataset_id,
        "expected_head_generation": expected_head_generation,
        "change_summary": normalized_summary,
    }
    digest = sha256_text(canonical_json(normalized))
    scope = f"protein-dataset-{operation}:" + sha256_text(canonical_json({"dataset_id": dataset_id}))
    claim = await session.get(ExperimentIdempotencyClaim, (scope, idempotency_key))
    if claim is not None:
        if claim.request_sha256 != digest:
            raise IdempotencyConflict(
                f"idempotency key was reused with a different Dataset {operation} request"
            )
        return json.loads(claim.response_json)
    if operation == "archive":
        if head.lifecycle_state != "active":
            raise InvalidProteinDatasetLifecycle("Dataset is not active")
        head = await archive_aggregate(
            session, dataset_id, expected_head_generation=expected_head_generation
        )
    else:
        if head.lifecycle_state != "archived":
            raise InvalidProteinDatasetLifecycle("Dataset is not archived")
        head = await restore_aggregate(
            session, dataset_id, expected_head_generation=expected_head_generation
        )
    response = _head_document(
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
        event_type=f"dataset_{operation}d",
        generation=head.head_generation,
        payload={
            "change_summary": normalized_summary,
            "normalized_request_sha256": digest,
            "actor_id": actor,
        },
    )
    await session.flush()
    return response


__all__ = [
    "InvalidProteinDatasetLifecycle",
    "PROTEIN_DATASET_KINDS",
    "UNAVAILABLE_PROTEIN_DATASET_KINDS",
    "create_protein_dataset",
    "protein_dataset_kind_records",
    "require_protein_dataset",
    "require_protein_dataset_kind",
    "require_protein_domain_hierarchy",
    "revise_protein_dataset",
    "set_protein_dataset_lifecycle",
    "verify_protein_dataset_member",
]
