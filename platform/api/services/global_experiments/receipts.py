"""Verified attachment receipts and lineage for Project Manager."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import (
    ExperimentAggregateHead,
    ExperimentDomainAdapterReceipt,
    ExperimentLineageEdge,
    ExperimentResource,
    ExperimentRevision,
)
from experiment_operations import register_external_entity_receipt
from experiment_services import (
    NotFound,
    RevisionConflict,
    ValidationFailure,
    add_audit_event,
    canonical_json,
    new_id,
    now,
    sha256_text,
)
from services.global_experiments.adapters import AdapterError, registry


ATTACHMENT_ROLES = {"references", "uses_input", "produced", "validated_by"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _domain_payload(
    session: AsyncSession,
    *,
    project_id: str,
    global_experiment_id: str,
    domain_experiment_id: str,
) -> tuple[ExperimentAggregateHead, ExperimentAggregateHead, dict[str, Any]]:
    project = await session.get(ExperimentAggregateHead, project_id)
    experiment = await session.get(ExperimentAggregateHead, global_experiment_id)
    domain = await session.get(ExperimentAggregateHead, domain_experiment_id)
    if project is None or project.aggregate_kind != "workspace":
        raise NotFound(f"project not found: {project_id}")
    if (
        experiment is None
        or experiment.aggregate_kind != "experiment"
        or experiment.workspace_id != project_id
        or experiment.parent_id != project_id
    ):
        raise NotFound(f"global experiment not found: {global_experiment_id}")
    if (
        domain is None
        or domain.aggregate_kind != "domain_experiment"
        or domain.workspace_id != project_id
        or domain.parent_id != global_experiment_id
    ):
        raise NotFound(f"domain experiment not found: {domain_experiment_id}")
    if domain.lifecycle_state == "archived":
        raise ValidationFailure("archived Domain Experiments cannot receive attachments")
    if domain.current_revision_id is None:
        raise ValidationFailure("Domain Experiment has no immutable revision")
    revision = await session.get(ExperimentRevision, domain.current_revision_id)
    if revision is None:
        raise ValidationFailure("Domain Experiment current revision is unavailable")
    return project, domain, json.loads(revision.canonical_payload)


async def attach_verified_entity(
    experiment_session: AsyncSession,
    core_session: AsyncSession,
    *,
    project_id: str,
    global_experiment_id: str,
    domain_experiment_id: str,
    adapter_id: str,
    entity_id: str,
    role: str,
    expected_head_generation: int,
) -> dict[str, Any]:
    if role not in ATTACHMENT_ROLES:
        raise ValidationFailure("attachment role is unsupported")
    project, domain, payload = await _domain_payload(
        experiment_session,
        project_id=project_id,
        global_experiment_id=global_experiment_id,
        domain_experiment_id=domain_experiment_id,
    )
    adapter = registry.get(adapter_id)
    if adapter.domain_kind != payload.get("domain_kind"):
        raise ValidationFailure("adapter domain kind does not match the Domain Experiment")
    source_receipt = await adapter.verify(core_session, entity_id)
    source_receipt["verified_at"] = _utc_now()
    source_digest = source_receipt.get("content_digest") or source_receipt.get("contract_digest")
    if not isinstance(source_digest, str):
        raise AdapterError("source_contract_invalid", "verified source receipt has no digest")
    external = await register_external_entity_receipt(
        experiment_session,
        workspace_id=project_id,
        store_id=str(source_receipt["store_id"]),
        entity_kind=str(source_receipt["entity_kind"]),
        entity_id=str(source_receipt["entity_id"]),
        generation_or_revision=str(source_receipt.get("entity_revision_id") or source_digest),
        content_digest=source_digest,
        availability="available",
        acknowledgement=source_receipt,
        verification_authority=adapter.adapter_id,
    )
    normalized_request = {
        "schema": "bms.global.attachment-request.v1",
        "project_id": project_id,
        "global_experiment_id": global_experiment_id,
        "domain_experiment_id": domain_experiment_id,
        "adapter_id": adapter.adapter_id,
        "adapter_version": adapter.adapter_version,
        "entity_kind": source_receipt["entity_kind"],
        "entity_id": source_receipt["entity_id"],
        "source_receipt_id": external.id,
        "source_digest": source_digest,
        "role": role,
    }
    request_sha256 = sha256_text(canonical_json(normalized_request))
    existing = (
        await experiment_session.execute(
            select(ExperimentDomainAdapterReceipt).where(
                ExperimentDomainAdapterReceipt.workspace_id == project_id,
                ExperimentDomainAdapterReceipt.domain_experiment_id == domain_experiment_id,
                ExperimentDomainAdapterReceipt.adapter_id == adapter.adapter_id,
                ExperimentDomainAdapterReceipt.operation_kind == f"attach:{role}",
                ExperimentDomainAdapterReceipt.normalized_request_sha256 == request_sha256,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return json.loads(existing.receipt_json)
    next_head_generation = expected_head_generation + 1
    cas_result = await experiment_session.execute(
        update(ExperimentAggregateHead)
        .where(
            ExperimentAggregateHead.aggregate_id == project_id,
            ExperimentAggregateHead.head_generation == expected_head_generation,
        )
        .values(head_generation=next_head_generation, updated_at=now())
        .execution_options(synchronize_session=False)
    )
    if cas_result.rowcount != 1:
        await experiment_session.refresh(project)
        raise RevisionConflict(
            f"stale head generation for {project_id}: expected {expected_head_generation}, "
            f"current {project.head_generation}"
        )
    edge_key = f"attachment:{adapter.adapter_id}:{external.id}:{role}"
    edge = (
        await experiment_session.execute(
            select(ExperimentLineageEdge).where(
                ExperimentLineageEdge.source_resource_id == domain_experiment_id,
                ExperimentLineageEdge.target_resource_id == external.id,
                ExperimentLineageEdge.edge_mode == role,
                ExperimentLineageEdge.edge_key == edge_key,
            )
        )
    ).scalar_one_or_none()
    if edge is None:
        edge = ExperimentLineageEdge(
            id=new_id("lineage"),
            workspace_id=project_id,
            source_resource_id=domain_experiment_id,
            target_resource_id=external.id,
            edge_mode=role,
            edge_key=edge_key,
            metadata_json=canonical_json(
                {
                    "adapter_id": adapter.adapter_id,
                    "adapter_version": adapter.adapter_version,
                    "source_digest": source_digest,
                }
            ),
            created_at=now(),
        )
        experiment_session.add(edge)
        await experiment_session.flush()
    receipt_resource_id = new_id("adapter-receipt")
    experiment_session.add(
        ExperimentResource(
            id=receipt_resource_id,
            kind="domain_adapter_receipt",
            workspace_id=project_id,
            lifecycle_owner_id=domain_experiment_id,
            created_at=now(),
        )
    )
    await experiment_session.flush()
    receipt = {
        "schema": "bms.global.attachment-receipt.v1",
        "attachment_receipt_id": receipt_resource_id,
        "project_id": project_id,
        "global_experiment_id": global_experiment_id,
        "domain_experiment_id": domain_experiment_id,
        "adapter_id": adapter.adapter_id,
        "adapter_version": adapter.adapter_version,
        "source_receipt_id": external.id,
        "source_receipt": source_receipt,
        "lineage_edge_id": edge.id,
        "role": role,
        "project_head_generation": next_head_generation,
        "normalized_request_sha256": request_sha256,
        "attached_at": _utc_now(),
    }
    experiment_session.add(
        ExperimentDomainAdapterReceipt(
            resource_id=receipt_resource_id,
            workspace_id=project_id,
            domain_experiment_id=domain_experiment_id,
            adapter_id=adapter.adapter_id,
            adapter_version=adapter.adapter_version,
            operation_kind=f"attach:{role}",
            normalized_request_sha256=request_sha256,
            receipt_json=canonical_json(receipt),
            created_at=now(),
        )
    )
    add_audit_event(
        experiment_session,
        workspace_id=project_id,
        resource_id=domain_experiment_id,
        event_type="verified_entity_attached",
        generation=domain.head_generation,
        payload={
            "attachment_receipt_id": receipt_resource_id,
            "adapter_id": adapter.adapter_id,
            "source_receipt_id": external.id,
            "lineage_edge_id": edge.id,
            "role": role,
        },
    )
    await experiment_session.flush()
    return receipt


__all__ = ["ATTACHMENT_ROLES", "attach_verified_entity"]
