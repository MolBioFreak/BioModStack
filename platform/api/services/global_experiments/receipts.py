"""Verified attachment receipts and lineage for Project Manager."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import (
    ExperimentAggregateHead,
    ExperimentDomainAdapterReceipt,
    ExperimentExternalEntityReceipt,
    ExperimentLineageEdge,
    ExperimentResource,
    ExperimentRevision,
    ExperimentRunAttempt,
    ExperimentWorkflowPreparation,
    ExperimentWorkflowRun,
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


ATTACHMENT_ROLES = frozenset({"references", "uses_input", "produced", "validated_by"})
ATTACHMENT_OPERATIONS = {
    "attach_reference": "references",
    "bind_input": "uses_input",
    "link_output": "produced",
    "attach_evidence": "validated_by",
}
SOURCE_REVERIFICATION_TTL = timedelta(hours=24)
DOMAIN_OWNED_ENTITY_KINDS = frozenset({
    "molecular_operation",
    "molecular_revision",
    "ngs_comparison_panel",
    "ngs_evidence_assessment",
    "ngs_job",
    "ngs_molbio_state_revision",
    "ngs_reference_revision",
    "ngs_result_manifest",
    "ont_instrument_run",
    "pcr_experiment_revision",
    "primer_revision",
    "sample_revision",
})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _reconcile_native_receipt_lineage(
    session: AsyncSession,
    workspace_id: str,
    domain_experiment_id: str,
) -> None:
    page_size = 500
    anchor: tuple[str, str] | None = None
    while True:
        statement = (
            select(ExperimentExternalEntityReceipt)
            .where(ExperimentExternalEntityReceipt.workspace_id == workspace_id)
            .order_by(
                ExperimentExternalEntityReceipt.created_at.desc(),
                ExperimentExternalEntityReceipt.id.desc(),
            )
            .limit(page_size)
        )
        if anchor is not None:
            statement = statement.where(
                (ExperimentExternalEntityReceipt.created_at < anchor[0])
                | (
                    (ExperimentExternalEntityReceipt.created_at == anchor[0])
                    & (ExperimentExternalEntityReceipt.id < anchor[1])
                )
            )
        receipts = list((await session.scalars(statement)).all())
        if not receipts:
            return
        for receipt in receipts:
            try:
                acknowledgement = json.loads(receipt.acknowledgement_json)
            except json.JSONDecodeError:
                continue
            metadata = acknowledgement.get("metadata")
            if not isinstance(metadata, dict):
                continue
            lineage = metadata.get("native_lineage", [])
            if not isinstance(lineage, list) or not lineage:
                continue
            if len(lineage) > 1_000:
                raise ValidationFailure("native receipt lineage exceeds the supported bound")
            receipt_owner = metadata.get("global_domain_experiment_id")
            if receipt_owner is not None and not isinstance(receipt_owner, str):
                raise ValidationFailure("native receipt Domain ownership metadata is invalid")
            if receipt_owner != domain_experiment_id:
                # Receipts owned by another Domain are unrelated to this
                # reconciliation pass.  Domain-owned receipts with missing
                # ownership are likewise ineligible; current attachments are
                # rejected earlier by attach_verified_entity.
                if receipt.entity_kind in DOMAIN_OWNED_ENTITY_KINDS or receipt_owner is not None:
                    continue
            for item in lineage:
                if not isinstance(item, dict):
                    raise ValidationFailure("native receipt lineage item is invalid")
                relation = str(item.get("relation") or "derived_from")
                native_relation = f"native_{relation}"
                if relation in {"compares", "compared_with"}:
                    edge_mode = "compared_with"
                    direction = "current_to_related"
                elif relation in {"derived_from", "derives_from"}:
                    edge_mode = "derived_from"
                    direction = "current_to_related"
                elif relation == "produced_child_analysis":
                    edge_mode = "derived_from"
                    direction = "related_to_current"
                elif relation in {"uses_input", "references"}:
                    edge_mode = relation
                    direction = "current_to_related"
                elif relation == "validated_by":
                    edge_mode = relation
                    direction = "related_to_current"
                elif relation == "produced":
                    edge_mode = relation
                    direction = "current_to_related"
                else:
                    continue
                entity_kind = item.get("entity_kind")
                entity_id = item.get("entity_id")
                expected_digest = item.get("receipt_content_digest")
                if (
                    not isinstance(entity_kind, str)
                    or not entity_kind
                    or not isinstance(entity_id, str)
                    or not entity_id
                    or not isinstance(expected_digest, str)
                    or len(expected_digest) != 64
                    or any(character not in "0123456789abcdef" for character in expected_digest)
                ):
                    raise ValidationFailure(
                        "native lineage target requires exact kind, identity, and content digest"
                    )
                source_digest = item.get("source_digest")
                if source_digest is not None and source_digest != expected_digest:
                    raise ValidationFailure("native lineage source digest diverges from receipt authority")
                candidate_statement = select(ExperimentExternalEntityReceipt).where(
                    ExperimentExternalEntityReceipt.workspace_id == workspace_id,
                    ExperimentExternalEntityReceipt.entity_kind == entity_kind,
                    ExperimentExternalEntityReceipt.entity_id == entity_id,
                    ExperimentExternalEntityReceipt.content_digest == expected_digest,
                )
                candidates = list((await session.scalars(candidate_statement.limit(2))).all())
                if not candidates:
                    continue
                if len(candidates) != 1:
                    raise ValidationFailure(
                        f"native lineage target is not uniquely attached: {entity_kind}:{entity_id}"
                    )
                related = candidates[0]
                try:
                    related_acknowledgement = json.loads(related.acknowledgement_json)
                except json.JSONDecodeError as exc:
                    raise ValidationFailure("native lineage target acknowledgement is invalid") from exc
                related_metadata = related_acknowledgement.get("metadata")
                related_owner = (
                    related_metadata.get("global_domain_experiment_id")
                    if isinstance(related_metadata, dict)
                    else None
                )
                if (
                    (related_owner is not None and related_owner != domain_experiment_id)
                    or (
                        related.entity_kind in DOMAIN_OWNED_ENTITY_KINDS
                        and related_owner != domain_experiment_id
                    )
                ):
                    raise ValidationFailure(
                        "native lineage receipts do not share exact Domain ownership"
                    )
                if direction == "related_to_current":
                    source_id, target_id = related.id, receipt.id
                else:
                    source_id, target_id = receipt.id, related.id
                ordinal = item.get("ordinal")
                role = item.get("role")
                edge_key = f"native:{relation}:{source_id}:{target_id}:{role}:{ordinal}"
                existing = await session.scalar(
                    select(ExperimentLineageEdge).where(
                        ExperimentLineageEdge.source_resource_id == source_id,
                        ExperimentLineageEdge.target_resource_id == target_id,
                        ExperimentLineageEdge.edge_mode == edge_mode,
                        ExperimentLineageEdge.edge_key == edge_key,
                    )
                )
                if existing is None:
                    session.add(ExperimentLineageEdge(
                        id=new_id("lineage"),
                        workspace_id=workspace_id,
                        source_resource_id=source_id,
                        target_resource_id=target_id,
                        edge_mode=edge_mode,
                        edge_key=edge_key,
                        metadata_json=canonical_json({
                            "source": "native_adapter_receipt",
                            "native": True,
                            "native_relation": native_relation,
                            "role": role,
                            "ordinal": ordinal,
                            "compatibility_contract_id": item.get("compatibility_contract_id"),
                            "source_digest": item.get("source_digest") or expected_digest,
                            "global_domain_experiment_id": receipt_owner,
                        }),
                        created_at=now(),
                    ))
        if len(receipts) < page_size:
            return
        last = receipts[-1]
        anchor = (last.created_at, last.id)


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
    operation: str,
    role: str,
    note: str | None,
    expected_head_generation: int,
) -> dict[str, Any]:
    if role not in ATTACHMENT_ROLES:
        raise ValidationFailure("attachment role is unsupported")
    if ATTACHMENT_OPERATIONS.get(operation) != role:
        raise ValidationFailure("attachment operation and lineage role do not match")
    normalized_note = note.strip() if isinstance(note, str) and note.strip() else None
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
    source_metadata = source_receipt.get("metadata")
    source_owner = (
        source_metadata.get("global_domain_experiment_id")
        if isinstance(source_metadata, dict)
        else None
    )
    source_entity_kind = str(source_receipt.get("entity_kind") or "")
    if source_entity_kind in DOMAIN_OWNED_ENTITY_KINDS and source_owner != domain_experiment_id:
        raise ValidationFailure(
            "verified native authority lacks exact ownership by the selected Domain Experiment"
        )
    if source_owner is not None and source_owner != domain_experiment_id:
        raise ValidationFailure(
            "verified native authority is owned by a different Domain Experiment"
        )
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
        "operation": operation,
        "role": role,
        "note": normalized_note,
    }
    request_sha256 = sha256_text(canonical_json(normalized_request))
    existing = (
        await experiment_session.execute(
            select(ExperimentDomainAdapterReceipt).where(
                ExperimentDomainAdapterReceipt.workspace_id == project_id,
                ExperimentDomainAdapterReceipt.domain_experiment_id == domain_experiment_id,
                ExperimentDomainAdapterReceipt.adapter_id == adapter.adapter_id,
                ExperimentDomainAdapterReceipt.operation_kind == operation,
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
                    "operation": operation,
                    "note": normalized_note,
                }
            ),
            created_at=now(),
        )
        experiment_session.add(edge)
        await experiment_session.flush()
    await _reconcile_native_receipt_lineage(
        experiment_session,
        project_id,
        domain_experiment_id,
    )
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
        "operation": operation,
        "role": role,
        "note": normalized_note,
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
            operation_kind=operation,
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
            "operation": operation,
            "role": role,
            "note": normalized_note,
        },
    )
    await experiment_session.flush()
    return receipt


async def reverify_source_receipt(
    experiment_session: AsyncSession,
    core_session: AsyncSession,
    *,
    project_id: str,
    global_experiment_id: str,
    domain_experiment_id: str,
    source_receipt_id: str,
) -> dict[str, Any]:
    project, _domain, payload = await _domain_payload(
        experiment_session,
        project_id=project_id,
        global_experiment_id=global_experiment_id,
        domain_experiment_id=domain_experiment_id,
    )
    persisted = await experiment_session.get(
        ExperimentExternalEntityReceipt,
        source_receipt_id,
    )
    if persisted is None or persisted.workspace_id != project_id:
        raise NotFound("source receipt not found in Project")
    attachment = (
        await experiment_session.execute(
            select(ExperimentLineageEdge).where(
                ExperimentLineageEdge.workspace_id == project_id,
                ExperimentLineageEdge.source_resource_id == domain_experiment_id,
                ExperimentLineageEdge.target_resource_id == source_receipt_id,
                ExperimentLineageEdge.edge_mode.in_(ATTACHMENT_ROLES),
            )
        )
    ).scalars().first()
    if attachment is None:
        raise ValidationFailure("source receipt is not attached to the selected Domain Experiment")

    adapter = registry.get(str(persisted.verification_authority or ""))
    if adapter.domain_kind != payload.get("domain_kind"):
        raise ValidationFailure("source receipt adapter does not match the Domain Experiment")
    verified = await adapter.verify(core_session, persisted.entity_id)
    verified_digest = verified.get("content_digest") or verified.get("contract_digest")
    expected_identity = {
        "store_id": persisted.store_id,
        "entity_kind": persisted.entity_kind,
        "entity_id": persisted.entity_id,
        "entity_revision_id": persisted.generation_or_revision,
    }
    if any(str(verified.get(field) or "") != str(value) for field, value in expected_identity.items()):
        raise ValidationFailure("source identity changed since the attached receipt was issued")
    if verified_digest != persisted.content_digest:
        raise ValidationFailure("source digest changed since the attached receipt was issued")
    if verified.get("availability") != "available":
        raise ValidationFailure("source is not currently available for re-verification")

    verified_at = datetime.now(timezone.utc)
    valid_until = verified_at + SOURCE_REVERIFICATION_TTL
    normalized_request = {
        "schema": "bms.global.source-reverification-request.v1",
        "project_id": project_id,
        "global_experiment_id": global_experiment_id,
        "domain_experiment_id": domain_experiment_id,
        "source_receipt_id": source_receipt_id,
        "source_digest": persisted.content_digest,
        "verified_at": verified_at.isoformat(),
    }
    request_sha256 = sha256_text(canonical_json(normalized_request))
    receipt_resource_id = new_id("adapter-receipt")
    experiment_session.add(
        ExperimentResource(
            id=receipt_resource_id,
            kind="domain_adapter_receipt",
            workspace_id=project_id,
            lifecycle_owner_id=domain_experiment_id,
            created_at=verified_at.isoformat(),
        )
    )
    await experiment_session.flush()
    receipt = {
        "schema": "bms.global.source-reverification-receipt.v1",
        "reverification_receipt_id": receipt_resource_id,
        "project_id": project_id,
        "global_experiment_id": global_experiment_id,
        "domain_experiment_id": domain_experiment_id,
        "adapter_id": adapter.adapter_id,
        "adapter_version": str(adapter.adapter_version),
        "source_receipt_id": source_receipt_id,
        "source_digest": persisted.content_digest,
        "verified_at": verified_at.isoformat(),
        "valid_until": valid_until.isoformat(),
        "normalized_request_sha256": request_sha256,
    }
    experiment_session.add(
        ExperimentDomainAdapterReceipt(
            resource_id=receipt_resource_id,
            workspace_id=project_id,
            domain_experiment_id=domain_experiment_id,
            adapter_id=adapter.adapter_id,
            adapter_version=str(adapter.adapter_version),
            operation_kind="reverify_source",
            normalized_request_sha256=request_sha256,
            receipt_json=canonical_json(receipt),
            created_at=verified_at.isoformat(),
        )
    )
    add_audit_event(
        experiment_session,
        workspace_id=project_id,
        resource_id=domain_experiment_id,
        event_type="source_receipt_reverified",
        generation=project.head_generation,
        payload={
            "reverification_receipt_id": receipt_resource_id,
            "source_receipt_id": source_receipt_id,
            "source_digest": persisted.content_digest,
            "valid_until": valid_until.isoformat(),
        },
    )
    await experiment_session.flush()
    return receipt


async def verify_and_link_terminal_outputs(
    experiment_session: AsyncSession,
    core_session: AsyncSession,
    *,
    attempt_id: str,
) -> dict[str, Any]:
    """Verify a typed terminal output and bind its opaque receipt to lineage."""
    attempt = await experiment_session.get(ExperimentRunAttempt, attempt_id)
    if attempt is None:
        raise NotFound("terminal attempt not found")
    run = await experiment_session.get(ExperimentWorkflowRun, attempt.workflow_run_id)
    if run is None:
        raise ValidationFailure("terminal attempt has no workflow run")
    preparation = await experiment_session.get(ExperimentWorkflowPreparation, run.preparation_id)
    if preparation is None:
        raise ValidationFailure("terminal run has no immutable preparation")
    revision = await experiment_session.get(ExperimentRevision, preparation.workflow_revision_id)
    if revision is None:
        raise ValidationFailure("terminal preparation has no workflow revision")
    workflow = await experiment_session.get(ExperimentAggregateHead, revision.subject_id)
    if workflow is None or workflow.aggregate_kind != "workflow" or workflow.parent_id is None:
        raise ValidationFailure("terminal workflow has no Domain Experiment owner")
    domain = await experiment_session.get(ExperimentAggregateHead, workflow.parent_id)
    if domain is None or domain.aggregate_kind != "domain_experiment":
        raise ValidationFailure("terminal workflow owner is not a Domain Experiment")

    workflow_payload = json.loads(revision.canonical_payload)
    adapter_id = str(workflow_payload.get("adapter_id") or "")
    adapter = registry.get(adapter_id)
    try:
        binding = json.loads(attempt.external_binding_receipt_json or "{}")
    except json.JSONDecodeError as exc:
        raise ValidationFailure("terminal attempt binding receipt is malformed") from exc
    if not isinstance(binding, dict):
        raise ValidationFailure("terminal attempt binding receipt is malformed")
    bound_job_id = binding.get("scheduler_job_id")
    if bound_job_id is not None and str(bound_job_id) != attempt.scheduler_job_id:
        raise ValidationFailure("terminal binding receipt disagrees with the scheduler job identity")
    entity_id = binding.get("entity_id") or binding.get("request_id") or attempt.scheduler_job_id
    source_receipt = await adapter.verify(core_session, str(entity_id))
    if source_receipt.get("verifier_id") != adapter_id:
        raise ValidationFailure("terminal output verifier identity disagrees with workflow intent")
    external = await register_external_entity_receipt(
        experiment_session,
        workspace_id=attempt.workspace_id,
        store_id=str(source_receipt["store_id"]),
        entity_kind=str(source_receipt["entity_kind"]),
        entity_id=str(source_receipt["entity_id"]),
        generation_or_revision=str(source_receipt["entity_revision_id"]),
        content_digest=str(source_receipt["content_digest"]),
        availability="available",
        acknowledgement=source_receipt,
        verification_authority=adapter_id,
    )
    for source_id, edge_key in (
        (domain.aggregate_id, f"terminal-output:{attempt.resource_id}"),
        (attempt.resource_id, "verified-terminal-output"),
    ):
        existing = (
            await experiment_session.execute(
                select(ExperimentLineageEdge).where(
                    ExperimentLineageEdge.source_resource_id == source_id,
                    ExperimentLineageEdge.target_resource_id == external.id,
                    ExperimentLineageEdge.edge_mode == "produced",
                    ExperimentLineageEdge.edge_key == edge_key,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            experiment_session.add(
                ExperimentLineageEdge(
                    id=new_id("terminal-output-lineage"),
                    workspace_id=attempt.workspace_id,
                    source_resource_id=source_id,
                    target_resource_id=external.id,
                    edge_mode="produced",
                    edge_key=edge_key,
                    metadata_json=canonical_json(
                        {
                            "attempt_id": attempt.resource_id,
                            "adapter_id": adapter_id,
                            "content_digest": external.content_digest,
                        }
                    ),
                    created_at=now(),
                )
            )
    metadata = source_receipt.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    count_values = [
        metadata.get("record_count"),
        metadata.get("candidate_count"),
        metadata.get("ready_session_count"),
    ]
    output_count = next(
        (
            value
            for value in count_values
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        ),
        1,
    )
    add_audit_event(
        experiment_session,
        workspace_id=attempt.workspace_id,
        resource_id=attempt.resource_id,
        event_type="terminal_outputs_verified",
        generation=run.generation,
        payload={
            "receipt_ids": [external.id],
            "output_count": output_count,
            "adapter_id": adapter_id,
        },
    )
    await experiment_session.flush()
    return {
        "output_count": output_count,
        "output_receipt_ids": [external.id],
        "output_content_digests": [external.content_digest],
        "adapter_id": adapter_id,
    }


__all__ = [
    "ATTACHMENT_ROLES",
    "attach_verified_entity",
    "verify_and_link_terminal_outputs",
]
