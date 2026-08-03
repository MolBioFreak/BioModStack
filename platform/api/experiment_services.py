"""Business services for global experiment/workspace persistence and dispatch."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import (
    ExperimentAggregateHead,
    ExperimentAuditEvent,
    ExperimentDatasetRevisionMember,
    ExperimentDispatchOutbox,
    ExperimentIdempotencyClaim,
    ExperimentLineageEdge,
    ExperimentResource,
    ExperimentRevision,
    ExperimentRunAttempt,
    ExperimentWorkflowRevisionEdge,
    ExperimentWorkflowRevisionNode,
    ExperimentRunEvent,
    ExperimentRunGroup,
    ExperimentRunGroupPreparation,
    ExperimentValidation,
    ExperimentWorkflowDraft,
    ExperimentWorkflowPreparation,
    ExperimentWorkflowRun,
)


class ExperimentServiceError(RuntimeError):
    """Base error for global experiment mutations."""


class NotFound(ExperimentServiceError):
    pass


class RevisionConflict(ExperimentServiceError):
    pass


class ValidationFailure(ExperimentServiceError):
    pass


class IdempotencyConflict(ExperimentServiceError):
    pass


class DispatchFailure(ExperimentServiceError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_audit_event(
    session: AsyncSession,
    *,
    workspace_id: str,
    resource_id: str,
    event_type: str,
    generation: int,
    payload: dict[str, Any],
) -> None:
    session.add(
        ExperimentAuditEvent(
            id=new_id("audit"),
            workspace_id=workspace_id,
            resource_id=resource_id,
            event_type=event_type,
            generation=generation,
            payload_json=canonical_json(payload),
            created_at=now(),
        )
    )


def new_id(_prefix: str) -> str:
    """Return an opaque UUID-sized identity portable to the existing core store."""
    return str(uuid.uuid4())


WORKFLOW_ADAPTER_REGISTRY: dict[str, set[str]] = {
    "generic_test": {"generic.test.adapter.v1"},
    "conformational_mapping": {
        "bms.cm.protenix_v2.adapter.v1",
        "bms.cm.confornets.adapter.v1",
        "bms.cm.frustrampnn.adapter.v1",
        "bms.cm.comparison.adapter.v1",
    },
}


def register_workflow_adapter(workflow_family: str, adapter_id: str) -> None:
    """Register a server-owned workflow adapter; callers cannot register via HTTP."""
    WORKFLOW_ADAPTER_REGISTRY.setdefault(workflow_family, set()).add(adapter_id)


def _validate_workflow_payload(payload: dict[str, Any]) -> None:
    required = ("schema", "workflow_family", "contract_version", "adapter_id", "nodes", "edges")
    missing = [field for field in required if field not in payload]
    if missing:
        raise ValidationFailure(f"workflow revision missing required fields: {', '.join(missing)}")
    family = str(payload["workflow_family"])
    adapter_id = str(payload["adapter_id"])
    if adapter_id not in WORKFLOW_ADAPTER_REGISTRY.get(family, set()):
        raise ValidationFailure(f"workflow adapter is not registered: {family}/{adapter_id}")
    if family == "conformational_mapping":
        stage = payload.get("stage")
        stage_by_adapter = {
            "bms.cm.protenix_v2.adapter.v1": ("protenix_v2_sampling", "protenix_v2_ensemble"),
            "bms.cm.confornets.adapter.v1": ("confornets_sampling", "confornets"),
            "bms.cm.frustrampnn.adapter.v1": ("frustrampnn_analysis", "frustrampnn"),
            "bms.cm.comparison.adapter.v1": ("cross_ensemble_comparison", "comparison"),
        }
        expected_stage, expected_backend = stage_by_adapter[adapter_id]
        if stage != expected_stage or payload.get("backend") != expected_backend:
            raise ValidationFailure("CM workflow stage, backend, and adapter identity disagree")
        receipt_ids = payload.get("source_receipt_ids")
        if not isinstance(receipt_ids, list) or not receipt_ids or any(
            not isinstance(value, str) or not value for value in receipt_ids
        ):
            raise ValidationFailure("CM workflow requires explicit source receipt IDs")
        cardinality = payload.get("expected_cardinality")
        if isinstance(cardinality, bool) or not isinstance(cardinality, int) or cardinality < 1:
            raise ValidationFailure("CM workflow expected_cardinality must be a positive integer")
        dependencies = payload.get("depends_on", [])
        if not isinstance(dependencies, list) or any(not isinstance(value, str) or not value for value in dependencies):
            raise ValidationFailure("CM workflow depends_on must be an ordered ID list")
        if adapter_id in {"bms.cm.frustrampnn.adapter.v1", "bms.cm.comparison.adapter.v1"} and not dependencies:
            raise ValidationFailure("CM analysis/comparison stages require explicit dependencies")
    nodes = payload["nodes"]
    edges = payload["edges"]
    if not isinstance(nodes, list) or not nodes:
        raise ValidationFailure("workflow nodes must be a non-empty list")
    if not isinstance(edges, list):
        raise ValidationFailure("workflow edges must be a list")
    node_ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict) or not node.get("id") or not node.get("kind"):
            raise ValidationFailure("workflow nodes require unique id and kind")
        node_id = str(node["id"])
        if node_id in node_ids:
            raise ValidationFailure(f"duplicate workflow node id: {node_id}")
        node_ids.add(node_id)
    forbidden_keys = {"command", "shell", "executable", "executable_path", "script_path"}
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if forbidden_keys.intersection(value):
                raise ValidationFailure("workflow revisions cannot contain executable paths or commands")
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(payload)
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValidationFailure("workflow edges must be objects")
        source = edge.get("source", edge.get("from"))
        target = edge.get("target", edge.get("to"))
        if str(source) not in node_ids or str(target) not in node_ids:
            raise ValidationFailure("workflow edge references an unknown node")


async def _resource(
    session: AsyncSession,
    *,
    kind: str,
    workspace_id: str | None,
    lifecycle_owner_id: str | None,
    resource_id: str | None = None,
) -> ExperimentResource:
    resource = ExperimentResource(
        id=resource_id or new_id(kind),
        kind=kind,
        workspace_id=workspace_id,
        lifecycle_owner_id=lifecycle_owner_id,
        created_at=now(),
    )
    session.add(resource)
    await session.flush()
    if lifecycle_owner_id is not None:
        session.add(
            ExperimentLineageEdge(
                id=new_id("owns"),
                workspace_id=workspace_id,
                source_resource_id=lifecycle_owner_id,
                target_resource_id=resource.id,
                edge_mode="owns",
                edge_key=f"lifecycle-owner:{resource.kind}",
                metadata_json="{}",
                created_at=now(),
            )
        )
        await session.flush()
    return resource


async def _head(session: AsyncSession, aggregate_id: str, kind: str | None = None) -> ExperimentAggregateHead:
    head = await session.get(ExperimentAggregateHead, aggregate_id)
    if head is None or (kind is not None and head.aggregate_kind != kind):
        raise NotFound(f"aggregate not found: {aggregate_id}")
    return head


async def _workspace(session: AsyncSession, workspace_id: str) -> ExperimentResource:
    resource = await session.get(ExperimentResource, workspace_id)
    if resource is None or resource.kind != "workspace":
        raise NotFound(f"workspace not found: {workspace_id}")
    return resource


async def _resource_workspace(session: AsyncSession, resource_id: str) -> str:
    resource = await session.get(ExperimentResource, resource_id)
    if resource is None:
        raise NotFound(f"resource not found: {resource_id}")
    return resource.id if resource.kind == "workspace" else str(resource.workspace_id)


async def _create_aggregate(
    session: AsyncSession,
    *,
    workspace_id: str,
    kind: str,
    display_name: str,
    description: str = "",
    parent_id: str | None = None,
) -> ExperimentAggregateHead:
    await _workspace(session, workspace_id)
    if parent_id is not None:
        parent = await session.get(ExperimentResource, parent_id)
        if parent is None or (parent.kind not in {"workspace", "experiment"}):
            raise ValidationFailure("aggregate parent must be a workspace or experiment")
        if parent.kind != "workspace" and parent.workspace_id != workspace_id:
            raise ValidationFailure("aggregate parent belongs to another workspace")
    resource = await _resource(
        session,
        kind=kind,
        workspace_id=workspace_id,
        lifecycle_owner_id=workspace_id,
    )
    head = ExperimentAggregateHead(
        aggregate_id=resource.id,
        aggregate_kind=kind,
        workspace_id=workspace_id,
        parent_id=parent_id,
        lifecycle_state="draft",
        display_name=display_name,
        description=description,
        created_at=now(),
        updated_at=now(),
    )
    session.add(head)
    await session.flush()
    if kind == "workflow":
        draft_resource = await _resource(
            session,
            kind="workflow_draft",
            workspace_id=workspace_id,
            lifecycle_owner_id=resource.id,
        )
        session.add(
            ExperimentWorkflowDraft(
                resource_id=draft_resource.id,
                workflow_id=resource.id,
                canonical_payload="{}",
                generation=0,
                created_at=now(),
                updated_at=now(),
            )
        )
        await session.flush()
    add_audit_event(
        session,
        workspace_id=workspace_id,
        resource_id=head.aggregate_id,
        event_type="aggregate_created",
        generation=head.head_generation,
        payload={"kind": kind, "name": display_name},
    )
    return head


async def create_experiment_workspace(
    session: AsyncSession, name: str, description: str = ""
) -> ExperimentAggregateHead:
    resource = await _resource(session, kind="workspace", workspace_id=None, lifecycle_owner_id=None)
    head = ExperimentAggregateHead(
        aggregate_id=resource.id,
        aggregate_kind="workspace",
        workspace_id=resource.id,
        lifecycle_state="draft",
        display_name=name,
        description=description,
        created_at=now(),
        updated_at=now(),
    )
    session.add(head)
    await session.flush()
    add_audit_event(
        session,
        workspace_id=resource.id,
        resource_id=resource.id,
        event_type="workspace_created",
        generation=0,
        payload={"name": name},
    )
    return head


async def create_experiment(
    session: AsyncSession,
    workspace_id: str,
    name: str,
    question: str = "",
) -> ExperimentAggregateHead:
    return await _create_aggregate(
        session,
        workspace_id=workspace_id,
        kind="experiment",
        display_name=name,
        description=question,
        parent_id=workspace_id,
    )


async def create_workflow(
    session: AsyncSession,
    workspace_id: str,
    name: str,
    workflow_family: str,
    *,
    experiment_id: str | None = None,
) -> ExperimentAggregateHead:
    return await _create_aggregate(
        session,
        workspace_id=workspace_id,
        kind="workflow",
        display_name=name,
        description=workflow_family,
        parent_id=experiment_id or workspace_id,
    )


async def create_dataset(
    session: AsyncSession,
    workspace_id: str,
    name: str,
    dataset_kind: str,
    *,
    experiment_id: str | None = None,
) -> ExperimentAggregateHead:
    return await _create_aggregate(
        session,
        workspace_id=workspace_id,
        kind="dataset",
        display_name=name,
        description=dataset_kind,
        parent_id=experiment_id or workspace_id,
    )


async def save_workflow_draft(
    session: AsyncSession,
    workflow_id: str,
    payload: dict[str, Any],
    *,
    expected_generation: int,
) -> ExperimentWorkflowDraft:
    await _head(session, workflow_id, "workflow")
    result = await session.execute(
        select(ExperimentWorkflowDraft).where(ExperimentWorkflowDraft.workflow_id == workflow_id)
    )
    draft = result.scalar_one_or_none()
    if draft is None:
        raise NotFound(f"workflow draft not found: {workflow_id}")
    if draft.generation != expected_generation:
        raise RevisionConflict(
            f"workflow draft generation conflict: expected {expected_generation}, current {draft.generation}"
        )
    draft.canonical_payload = canonical_json(payload)
    draft.generation += 1
    draft.updated_at = now()
    await session.flush()
    return draft


async def _save_revision(
    session: AsyncSession,
    *,
    aggregate_id: str,
    aggregate_kind: str,
    payload: dict[str, Any],
    expected_head_generation: int,
) -> ExperimentRevision:
    head = await _head(session, aggregate_id, aggregate_kind)
    if aggregate_kind == "workflow":
        _validate_workflow_payload(payload)
    if head.head_generation != expected_head_generation:
        raise RevisionConflict(
            f"{aggregate_kind} head generation conflict: expected {expected_head_generation}, current {head.head_generation}"
        )
    workspace_id = await _resource_workspace(session, aggregate_id)
    payload_json = canonical_json(payload)
    graph_json = canonical_json({"nodes": payload.get("nodes", []), "edges": payload.get("edges", [])})
    revision_resource = await _resource(
        session,
        kind="revision",
        workspace_id=workspace_id,
        lifecycle_owner_id=aggregate_id,
    )
    revision = ExperimentRevision(
        resource_id=revision_resource.id,
        subject_id=aggregate_id,
        revision_number=head.head_generation + 1,
        parent_revision_id=head.current_revision_id,
        schema_name=str(payload.get("schema") or f"bms.workflow.{aggregate_kind}.v1"),
        schema_version=str(payload.get("contract_version") or "1"),
        canonical_payload=payload_json,
        payload_sha256=sha256_text(payload_json),
        dependency_graph_sha256=sha256_text(graph_json),
        provenance_json=canonical_json(payload.get("provenance", {})),
        created_at=now(),
    )
    session.add(revision)
    await session.flush()
    if aggregate_kind == "workflow":
        for ordinal, node in enumerate(payload["nodes"]):
            session.add(
                ExperimentWorkflowRevisionNode(
                    revision_id=revision.resource_id,
                    ordinal=ordinal,
                    node_id=str(node["id"]),
                    node_kind=str(node["kind"]),
                    node_json=canonical_json(node),
                )
            )
        for ordinal, edge in enumerate(payload["edges"]):
            session.add(
                ExperimentWorkflowRevisionEdge(
                    revision_id=revision.resource_id,
                    ordinal=ordinal,
                    source_node_id=str(edge.get("source", edge.get("from"))),
                    target_node_id=str(edge.get("target", edge.get("to"))),
                    edge_json=canonical_json(edge),
                )
            )
        await session.flush()
    changed = await session.execute(
        update(ExperimentAggregateHead)
        .where(
            ExperimentAggregateHead.aggregate_id == aggregate_id,
            ExperimentAggregateHead.head_generation == expected_head_generation,
        )
        .values(
            current_revision_id=revision.resource_id,
            head_generation=expected_head_generation + 1,
            lifecycle_state="validated",
            updated_at=now(),
        )
    )
    if changed.rowcount != 1:
        await session.rollback()
        raise RevisionConflict("aggregate head changed while saving revision")
    add_audit_event(
        session,
        workspace_id=workspace_id,
        resource_id=revision.resource_id,
        event_type="immutable_revision_saved",
        generation=revision.revision_number,
        payload={"subject_id": aggregate_id, "payload_sha256": revision.payload_sha256},
    )
    return revision


async def save_workflow_revision(
    session: AsyncSession,
    workflow_id: str,
    *,
    expected_head_generation: int,
) -> ExperimentRevision:
    await _head(session, workflow_id, "workflow")
    result = await session.execute(
        select(ExperimentWorkflowDraft).where(ExperimentWorkflowDraft.workflow_id == workflow_id)
    )
    draft = result.scalar_one_or_none()
    if draft is None:
        raise NotFound(f"workflow draft not found: {workflow_id}")
    payload = json.loads(draft.canonical_payload)
    revision = await _save_revision(
        session,
        aggregate_id=workflow_id,
        aggregate_kind="workflow",
        payload=payload,
        expected_head_generation=expected_head_generation,
    )
    draft.base_revision_id = revision.resource_id
    draft.updated_at = now()
    return revision


async def clone_workflow(
    session: AsyncSession,
    source_workflow_id: str,
    *,
    source_revision_id: str | None = None,
    name: str | None = None,
) -> ExperimentAggregateHead:
    source = await _head(session, source_workflow_id, "workflow")
    revision_id = source_revision_id or source.current_revision_id
    if revision_id is None:
        raise ValidationFailure("workflow has no immutable revision to clone")
    revision = await session.get(ExperimentRevision, revision_id)
    if revision is None or revision.subject_id != source_workflow_id:
        raise NotFound("source workflow revision not found")
    workspace_id = await _resource_workspace(session, source_workflow_id)
    clone = await create_workflow(
        session,
        workspace_id,
        name or f"{source.display_name} (clone)",
        json.loads(revision.canonical_payload).get("workflow_family", source.description),
    )
    draft = (
        await session.execute(
            select(ExperimentWorkflowDraft).where(ExperimentWorkflowDraft.workflow_id == clone.aggregate_id)
        )
    ).scalar_one()
    draft.canonical_payload = revision.canonical_payload
    draft.base_revision_id = revision.resource_id
    draft.generation = 1
    draft.updated_at = now()
    session.add(
        ExperimentLineageEdge(
            id=new_id("fork"),
            workspace_id=workspace_id,
            source_resource_id=clone.aggregate_id,
            target_resource_id=revision.resource_id,
            edge_mode="forked_from",
            edge_key="origin-revision",
            metadata_json=canonical_json({"source_workflow_id": source_workflow_id}),
            created_at=now(),
        )
    )
    add_audit_event(
        session,
        workspace_id=workspace_id,
        resource_id=clone.aggregate_id,
        event_type="workflow_cloned",
        generation=0,
        payload={"source_workflow_id": source_workflow_id, "source_revision_id": revision.resource_id},
    )
    await session.flush()
    return clone


async def archive_aggregate(
    session: AsyncSession,
    aggregate_id: str,
    *,
    expected_head_generation: int | None = None,
) -> ExperimentAggregateHead:
    head = await _head(session, aggregate_id)
    if expected_head_generation is not None and head.head_generation != expected_head_generation:
        raise RevisionConflict("aggregate head changed before archive")
    resource = await session.get(ExperimentResource, aggregate_id)
    if resource is None:
        raise NotFound(f"aggregate not found: {aggregate_id}")
    resource.archived_at = now()
    head.lifecycle_state = "archived"
    head.updated_at = now()
    workspace_id = await _resource_workspace(session, aggregate_id)
    add_audit_event(
        session,
        workspace_id=workspace_id,
        resource_id=aggregate_id,
        event_type="aggregate_archived",
        generation=head.head_generation,
        payload={},
    )
    await session.flush()
    return head


async def save_dataset_revision(
    session: AsyncSession,
    dataset_id: str,
    payload: dict[str, Any],
    *,
    expected_head_generation: int,
) -> ExperimentRevision:
    revision = await _save_revision(
        session,
        aggregate_id=dataset_id,
        aggregate_kind="dataset",
        payload=payload,
        expected_head_generation=expected_head_generation,
    )
    members = payload.get("members") or []
    if not isinstance(members, list):
        raise ValidationFailure("dataset members must be a list")
    for ordinal, member in enumerate(members):
        if not isinstance(member, dict):
            raise ValidationFailure("dataset members must be objects")
        value = member.get("value", member)
        value_json = canonical_json(value)
        session.add(
            ExperimentDatasetRevisionMember(
                revision_id=revision.resource_id,
                ordinal=ordinal,
                role=str(member.get("role") or "member"),
                semantic_identity=str(member.get("identity") or f"member:{ordinal}"),
                value_json=value_json,
                content_sha256=str(member.get("content_sha256") or sha256_text(value_json)),
                size_bytes=member.get("size_bytes"),
                media_type=member.get("media_type"),
            )
        )
    await session.flush()
    return revision


async def prepare_workflow(
    session: AsyncSession,
    workflow_revision_id: str,
    bindings: dict[str, Any],
) -> ExperimentWorkflowPreparation:
    revision = await session.get(ExperimentRevision, workflow_revision_id)
    if revision is None:
        raise NotFound(f"workflow revision not found: {workflow_revision_id}")
    workflow_resource = await session.get(ExperimentResource, revision.subject_id)
    if workflow_resource is None or workflow_resource.kind != "workflow":
        raise ValidationFailure("preparation requires a workflow revision")
    workspace_id = str(workflow_resource.workspace_id)
    dataset_revision_ids = bindings.get("input_dataset_revision_ids") or []
    if not isinstance(dataset_revision_ids, list):
        raise ValidationFailure("input_dataset_revision_ids must be a list")
    for dataset_revision_id in dataset_revision_ids:
        dataset_revision = await session.get(ExperimentRevision, str(dataset_revision_id))
        if dataset_revision is None:
            raise NotFound(f"dataset revision not found: {dataset_revision_id}")
        dataset_resource = await session.get(ExperimentResource, dataset_revision.subject_id)
        if dataset_resource is None or dataset_resource.kind != "dataset":
            raise ValidationFailure("input binding is not a dataset revision")
        if dataset_resource.workspace_id != workspace_id:
            raise ValidationFailure("dataset revision belongs to another workspace")
    payload = json.loads(revision.canonical_payload)
    scheduler_payload = payload.get("scheduler")
    reasons: list[str] = []
    if not isinstance(scheduler_payload, dict):
        scheduler_payload = {}
        reasons.append("workflow revision has no scheduler payload")
    else:
        for field in ("name", "model_id", "mode", "params"):
            if field not in scheduler_payload:
                reasons.append(f"scheduler payload missing {field}")
        if not isinstance(scheduler_payload.get("params", {}), dict):
            reasons.append("scheduler params must be an object")
    normalized = {
        "workflow_revision_id": workflow_revision_id,
        "input_dataset_revision_ids": [str(value) for value in dataset_revision_ids],
        "workflow": payload,
    }
    normalized_json = canonical_json(normalized)
    validation_status = "valid" if not reasons else "invalid"
    receipt = {
        "schema": "bms.experiment.validation.v1",
        "status": validation_status,
        "validator": "global-workflow-contract.v1",
        "reasons": reasons,
        "workflow_revision_id": workflow_revision_id,
        "normalized_request_sha256": sha256_text(normalized_json),
    }
    preparation_resource = await _resource(
        session,
        kind="preparation",
        workspace_id=workspace_id,
        lifecycle_owner_id=workflow_resource.id,
    )
    preparation = ExperimentWorkflowPreparation(
        resource_id=preparation_resource.id,
        workspace_id=workspace_id,
        workflow_revision_id=workflow_revision_id,
        normalized_request_json=normalized_json,
        normalized_request_sha256=sha256_text(normalized_json),
        scheduler_payload_json=canonical_json(scheduler_payload),
        validation_status=validation_status,
        validation_receipt_json=canonical_json(receipt),
        expected_cardinality=payload.get("expected_cardinality") if isinstance(payload.get("expected_cardinality"), int) else None,
        created_at=now(),
        prepared_at=now() if validation_status == "valid" else None,
    )
    session.add(preparation)
    await session.flush()
    validation_resource = await _resource(
        session,
        kind="validation",
        workspace_id=workspace_id,
        lifecycle_owner_id=preparation.resource_id,
    )
    receipt_json = canonical_json(receipt)
    session.add(
        ExperimentValidation(
            resource_id=validation_resource.id,
            subject_resource_id=preparation.resource_id,
            validator_name="global-workflow-contract",
            validator_version="v1",
            outcome="valid" if validation_status == "valid" else "invalid",
            input_graph_sha256=revision.dependency_graph_sha256,
            receipt_json=receipt_json,
            receipt_sha256=sha256_text(receipt_json),
            created_at=now(),
        )
    )
    preparation.validation_resource_id = validation_resource.id
    await session.flush()
    add_audit_event(
        session,
        workspace_id=workspace_id,
        resource_id=preparation.resource_id,
        event_type="workflow_prepared",
        generation=0,
        payload={"workflow_revision_id": workflow_revision_id, "validation_status": validation_status},
    )
    return preparation


async def create_run_group(
    session: AsyncSession,
    workspace_id: str,
    preparation_ids: list[str],
    *,
    idempotency_key: str,
) -> ExperimentRunGroup:
    await _workspace(session, workspace_id)
    request = {"workspace_id": workspace_id, "preparation_ids": [str(value) for value in preparation_ids]}
    request_json = canonical_json(request)
    request_sha256 = sha256_text(request_json)
    scope = f"run_group:{workspace_id}"
    existing_claim = await session.get(ExperimentIdempotencyClaim, (scope, idempotency_key))
    if existing_claim is not None:
        if existing_claim.request_sha256 != request_sha256:
            raise IdempotencyConflict("idempotency key was already used for a different launch request")
        group = await session.get(ExperimentRunGroup, existing_claim.result_resource_id)
        if group is None:
            raise DispatchFailure("idempotency claim points to a missing run group")
        return group
    if not preparation_ids:
        raise ValidationFailure("run group requires at least one preparation")
    result = await session.execute(
        select(ExperimentWorkflowPreparation).where(
            ExperimentWorkflowPreparation.resource_id.in_([str(value) for value in preparation_ids])
        )
    )
    preparations = {row.resource_id: row for row in result.scalars().all()}
    if len(preparations) != len(set(preparation_ids)):
        raise NotFound("one or more preparations were not found")
    if any(row.workspace_id != workspace_id for row in preparations.values()):
        raise ValidationFailure("all preparations must belong to the selected workspace")
    if any(row.validation_status != "valid" for row in preparations.values()):
        raise ValidationFailure("run group cannot launch an invalid preparation")
    group_resource = await _resource(
        session,
        kind="run_group",
        workspace_id=workspace_id,
        lifecycle_owner_id=workspace_id,
    )
    group = ExperimentRunGroup(
        resource_id=group_resource.id,
        workspace_id=workspace_id,
        launch_idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        state="dispatch_pending",
        generation=0,
        created_at=now(),
        updated_at=now(),
    )
    session.add(group)
    await session.flush()
    claim = ExperimentIdempotencyClaim(
        scope=scope,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        result_resource_id=group.resource_id,
        response_json=canonical_json({"run_group_id": group.resource_id}),
        created_at=now(),
    )
    session.add(claim)
    for ordinal, preparation_id in enumerate(preparation_ids):
        preparation = preparations[str(preparation_id)]
        session.add(
            ExperimentRunGroupPreparation(
                run_group_id=group.resource_id,
                preparation_id=preparation.resource_id,
                ordinal=ordinal,
            )
        )
        run_resource = await _resource(
            session,
            kind="workflow_run",
            workspace_id=workspace_id,
            lifecycle_owner_id=group.resource_id,
        )
        workflow_run = ExperimentWorkflowRun(
            resource_id=run_resource.id,
            workspace_id=workspace_id,
            run_group_id=group.resource_id,
            preparation_id=preparation.resource_id,
            node_id="main",
            requiredness="required",
            state="dispatch_pending",
            generation=0,
            created_at=now(),
        )
        session.add(workflow_run)
        await session.flush()
        attempt_resource = await _resource(
            session,
            kind="run_attempt",
            workspace_id=workspace_id,
            lifecycle_owner_id=workflow_run.resource_id,
        )
        attempt = ExperimentRunAttempt(
            resource_id=attempt_resource.id,
            workspace_id=workspace_id,
            workflow_run_id=workflow_run.resource_id,
            attempt_number=1,
            scheduler_job_id=attempt_resource.id,
            state="pending",
            created_at=now(),
        )
        session.add(attempt)
        await session.flush()
        scheduler_payload = json.loads(preparation.scheduler_payload_json)
        outbox_payload = {
            "schema": "bms.experiment.dispatch.v1",
            "run_group_id": group.resource_id,
            "workflow_run_id": workflow_run.resource_id,
            "attempt_id": attempt.resource_id,
            "scheduler_job_id": attempt.scheduler_job_id,
            "workflow_revision_id": preparation.workflow_revision_id,
            "scheduler": scheduler_payload,
        }
        outbox_json = canonical_json(outbox_payload)
        session.add(
            ExperimentDispatchOutbox(
                id=new_id("dispatch"),
                workspace_id=workspace_id,
                run_attempt_id=attempt.resource_id,
                event_type="materialize_scheduler_job",
                payload_json=outbox_json,
                payload_sha256=sha256_text(outbox_json),
                status="pending",
                dispatch_attempts=0,
                created_at=now(),
                updated_at=now(),
            )
        )
        session.add(
            ExperimentRunEvent(
                workspace_id=workspace_id,
                workflow_run_id=workflow_run.resource_id,
                sequence_number=1,
                expected_generation=0,
                resulting_generation=0,
                idempotency_key=f"run-group-created:{group.resource_id}",
                event_type="run_group_created",
                payload_json=canonical_json({"run_group_id": group.resource_id}),
                created_at=now(),
            )
        )
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise IdempotencyConflict("launch idempotency claim raced with another request") from exc
    add_audit_event(
        session,
        workspace_id=workspace_id,
        resource_id=group.resource_id,
        event_type="run_group_launch_intent_created",
        generation=0,
        payload={"preparation_ids": [str(value) for value in preparation_ids], "idempotency_key": idempotency_key},
    )
    return group


TERMINAL_CORE_JOB_STATES = {"completed", "succeeded", "failed", "cancelled", "canceled"}


async def reconcile_run_group(
    session: AsyncSession,
    core_session: AsyncSession,
    workspace_id: str,
    run_group_id: str,
) -> ExperimentRunGroup:
    """Project authoritative core terminal state into global attempts exactly once."""
    from database import Job

    group = await session.get(ExperimentRunGroup, run_group_id)
    if group is None or group.workspace_id != workspace_id:
        raise NotFound("run group not found")
    runs = (
        await session.execute(
            select(ExperimentWorkflowRun).where(ExperimentWorkflowRun.run_group_id == run_group_id)
        )
    ).scalars().all()
    changed = False
    for run in runs:
        attempts = (
            await session.execute(
                select(ExperimentRunAttempt)
                .where(ExperimentRunAttempt.workflow_run_id == run.resource_id)
                .order_by(ExperimentRunAttempt.attempt_number.desc())
            )
        ).scalars().all()
        if not attempts:
            continue
        attempt = attempts[0]
        if attempt.state in {"succeeded", "failed", "cancelled"}:
            continue
        job = await core_session.get(Job, attempt.scheduler_job_id)
        if job is None or str(job.status).lower() not in TERMINAL_CORE_JOB_STATES:
            continue
        status = str(job.status).lower()
        terminal_state = "succeeded" if status in {"completed", "succeeded"} else "cancelled" if status in {"cancelled", "canceled"} else "failed"
        receipt = {
            "schema": "bms.experiment.terminal-receipt.v1",
            "job_id": attempt.scheduler_job_id,
            "status": status,
            "terminal_state": terminal_state,
            "completed_at": str(job.completed_at) if job.completed_at else None,
            "error_message": job.error_message,
            "output_dir": job.output_dir,
            "provenance": job.provenance,
        }
        expected_generation = int(run.generation)
        attempt.state = terminal_state
        attempt.terminal_receipt_json = canonical_json(receipt)
        attempt.runtime_identity_json = canonical_json(job.provenance or {})
        run.state = terminal_state
        run.generation = expected_generation + 1
        sequence = int(
            (
                await session.execute(
                    select(func.max(ExperimentRunEvent.sequence_number)).where(
                        ExperimentRunEvent.workflow_run_id == run.resource_id
                    )
                )
            ).scalar_one()
            or 0
        ) + 1
        session.add(
            ExperimentRunEvent(
                workspace_id=workspace_id,
                workflow_run_id=run.resource_id,
                sequence_number=sequence,
                expected_generation=expected_generation,
                resulting_generation=run.generation,
                idempotency_key=f"core-terminal:{attempt.scheduler_job_id}:{status}",
                event_type="core_terminal_projected",
                payload_json=canonical_json(receipt),
                created_at=now(),
            )
        )
        changed = True
    if changed:
        states = [run.state for run in runs]
        if all(state == "succeeded" for state in states):
            group.state = "succeeded"
        elif any(state == "failed" for state in states):
            group.state = "failed" if all(state in {"failed", "cancelled", "succeeded"} for state in states) else "partially_dispatched"
        elif any(state in {"dispatch_pending", "dispatched", "running"} for state in states):
            group.state = "partially_dispatched"
        group.generation += 1
        group.updated_at = now()
        add_audit_event(
            session,
            workspace_id=workspace_id,
            resource_id=run_group_id,
            event_type="run_group_reconciled",
            generation=group.generation,
            payload={"state": group.state},
        )
        await session.flush()
    return group


async def retry_failed_run_group(
    session: AsyncSession,
    workspace_id: str,
    run_group_id: str,
    *,
    idempotency_key: str,
    replacement_preparation_ids: dict[str, str] | None = None,
) -> ExperimentRunGroup:
    """Create fresh attempts for failed runs; terminal attempts are never reused."""
    group = await session.get(ExperimentRunGroup, run_group_id)
    if group is None or group.workspace_id != workspace_id:
        raise NotFound("run group not found")
    runs = (
        await session.execute(
            select(ExperimentWorkflowRun).where(ExperimentWorkflowRun.run_group_id == run_group_id)
        )
    ).scalars().all()
    failed_run_ids: list[str] = []
    latest_by_run: dict[str, ExperimentRunAttempt] = {}
    for run in runs:
        attempts = (
            await session.execute(
                select(ExperimentRunAttempt)
                .where(ExperimentRunAttempt.workflow_run_id == run.resource_id)
                .order_by(ExperimentRunAttempt.attempt_number.desc())
            )
        ).scalars().all()
        if attempts:
            latest_by_run[run.resource_id] = attempts[0]
            if attempts[0].state == "failed":
                failed_run_ids.append(run.resource_id)
    if not failed_run_ids:
        raise ValidationFailure("run group has no reconciled failed runs eligible for retry")
    replacement_preparation_ids = replacement_preparation_ids or {}
    request_sha256 = sha256_text(
        canonical_json(
            {
                "run_group_id": run_group_id,
                "failed_run_ids": failed_run_ids,
                "replacement_preparation_ids": replacement_preparation_ids,
            }
        )
    )
    scope = f"run_group_retry:{workspace_id}:{run_group_id}"
    existing_claim = await session.get(ExperimentIdempotencyClaim, (scope, idempotency_key))
    if existing_claim is not None:
        if existing_claim.request_sha256 != request_sha256:
            raise IdempotencyConflict("retry idempotency key was reused with a different failed-run set")
        existing = await session.get(ExperimentRunGroup, existing_claim.result_resource_id)
        if existing is None:
            raise DispatchFailure("retry idempotency claim points to a missing run group")
        return existing
    session.add(
        ExperimentIdempotencyClaim(
            scope=scope,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            result_resource_id=run_group_id,
            response_json=canonical_json({"run_group_id": run_group_id}),
            created_at=now(),
        )
    )
    for run in runs:
        previous = latest_by_run.get(run.resource_id)
        if previous is None or previous.state != "failed":
            continue
        preparation_id = replacement_preparation_ids.get(run.resource_id, run.preparation_id)
        preparation = await session.get(ExperimentWorkflowPreparation, preparation_id)
        if preparation is None or preparation.workspace_id != workspace_id or preparation.validation_status != "valid":
            raise ValidationFailure("failed run has no valid replacement preparation in this workspace")
        attempt_resource = await _resource(
            session,
            kind="run_attempt",
            workspace_id=workspace_id,
            lifecycle_owner_id=run.resource_id,
        )
        attempt = ExperimentRunAttempt(
            resource_id=attempt_resource.id,
            workspace_id=workspace_id,
            workflow_run_id=run.resource_id,
            attempt_number=previous.attempt_number + 1,
            scheduler_job_id=attempt_resource.id,
            state="pending",
            created_at=now(),
        )
        session.add(attempt)
        await session.flush()
        scheduler_payload = json.loads(preparation.scheduler_payload_json)
        outbox_payload = {
            "schema": "bms.experiment.dispatch.v1",
            "run_group_id": run_group_id,
            "workflow_run_id": run.resource_id,
            "attempt_id": attempt.resource_id,
            "scheduler_job_id": attempt.scheduler_job_id,
            "workflow_revision_id": preparation.workflow_revision_id,
            "scheduler": scheduler_payload,
        }
        outbox_json = canonical_json(outbox_payload)
        session.add(
            ExperimentDispatchOutbox(
                id=new_id("dispatch"),
                workspace_id=workspace_id,
                run_attempt_id=attempt.resource_id,
                event_type="materialize_scheduler_job",
                payload_json=outbox_json,
                payload_sha256=sha256_text(outbox_json),
                status="pending",
                dispatch_attempts=0,
                created_at=now(),
                updated_at=now(),
            )
        )
        expected_generation = int(run.generation)
        previous_preparation_id = run.preparation_id
        run.preparation_id = preparation.resource_id
        run.state = "dispatch_pending"
        run.generation = expected_generation + 1
        sequence = int(
            (
                await session.execute(
                    select(func.max(ExperimentRunEvent.sequence_number)).where(
                        ExperimentRunEvent.workflow_run_id == run.resource_id
                    )
                )
            ).scalar_one()
            or 0
        ) + 1
        session.add(
            ExperimentRunEvent(
                workspace_id=workspace_id,
                workflow_run_id=run.resource_id,
                sequence_number=sequence,
                expected_generation=expected_generation,
                resulting_generation=run.generation,
                idempotency_key=f"retry:{run_group_id}:{attempt.resource_id}",
                event_type="run_attempt_retry_created",
                payload_json=canonical_json(
                    {
                        "previous_attempt_id": previous.resource_id,
                        "attempt_id": attempt.resource_id,
                        "previous_preparation_id": previous_preparation_id,
                        "replacement_preparation_id": preparation.resource_id,
                    }
                ),
                created_at=now(),
            )
        )
    group.state = "dispatch_pending"
    group.generation += 1
    group.updated_at = now()
    add_audit_event(
        session,
        workspace_id=workspace_id,
        resource_id=run_group_id,
        event_type="run_group_retry_created",
        generation=group.generation,
        payload={"failed_run_ids": failed_run_ids, "idempotency_key": idempotency_key},
    )
    await session.flush()
    return group


class DispatchMaterializer(Protocol):
    async def materialize(self, attempt_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class ExistingJobMaterializer:
    """Idempotently materialize a trusted prepared payload into the core Job store."""

    def __init__(self, core_session: AsyncSession):
        self.core_session = core_session

    async def materialize(self, attempt_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        from database import Job

        scheduler = payload.get("scheduler")
        if not isinstance(scheduler, dict):
            raise DispatchFailure("dispatch payload has no scheduler object")
        scheduler_params = scheduler.get("params")
        if isinstance(scheduler_params, dict) and str(scheduler_params.get("workflow_adapter", "")).startswith("bms.cm."):
            from services.conformational_mapping.global_adapter import materialize_preallocated_cm_job

            return await materialize_preallocated_cm_job(
                self.core_session,
                attempt_id=attempt_id,
                scheduler=scheduler,
                run_group_id=str(payload.get("run_group_id") or ""),
            )
        required = ("name", "model_id", "mode", "params")
        if any(field not in scheduler for field in required) or not isinstance(scheduler["params"], dict):
            raise DispatchFailure("dispatch scheduler payload is incomplete")
        job_id = str(payload.get("scheduler_job_id") or attempt_id)
        existing = await self.core_session.get(Job, job_id)
        if existing is not None:
            if (
                existing.model_id != scheduler["model_id"]
                or existing.mode != scheduler["mode"]
                or canonical_json(existing.params) != canonical_json(scheduler["params"])
            ):
                raise DispatchFailure("existing scheduler job conflicts with prepared dispatch payload")
        else:
            self.core_session.add(
                Job(
                    id=job_id,
                    name=str(scheduler["name"]),
                    status="queued",
                    model_id=str(scheduler["model_id"]),
                    mode=str(scheduler["mode"]),
                    params=scheduler["params"],
                    batch_id=payload.get("run_group_id"),
                    lineage_root_job_id=job_id,
                )
            )
            await self.core_session.commit()
        return {
            "store_id": "core",
            "entity_kind": "job",
            "entity_id": job_id,
            "generation": 1,
            "content_digest": sha256_text(canonical_json(scheduler)),
        }


async def dispatch_pending_outbox(
    session: AsyncSession,
    materializer: DispatchMaterializer,
) -> int:
    lease_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    candidate = (
        await session.execute(
            select(ExperimentDispatchOutbox)
            .where(
                or_(
                    ExperimentDispatchOutbox.status == "pending",
                    and_(
                        ExperimentDispatchOutbox.status == "dispatching",
                        ExperimentDispatchOutbox.updated_at < lease_cutoff,
                    ),
                )
            )
            .order_by(ExperimentDispatchOutbox.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if candidate is None:
        return 0
    lease_token = new_id("lease")
    claimed = await session.execute(
        update(ExperimentDispatchOutbox)
        .where(
            ExperimentDispatchOutbox.id == candidate.id,
            or_(
                ExperimentDispatchOutbox.status == "pending",
                and_(
                    ExperimentDispatchOutbox.status == "dispatching",
                    ExperimentDispatchOutbox.updated_at < lease_cutoff,
                ),
            ),
        )
        .values(
            status="dispatching",
            dispatch_attempts=ExperimentDispatchOutbox.dispatch_attempts + 1,
            lease_token=lease_token,
            updated_at=now(),
        )
    )
    if claimed.rowcount != 1:
        await session.rollback()
        return 0
    await session.commit()
    row = await session.get(ExperimentDispatchOutbox, candidate.id)
    if row is None:
        raise DispatchFailure("outbox row disappeared after lease claim")
    payload = json.loads(row.payload_json)
    try:
        receipt = await materializer.materialize(row.run_attempt_id, payload)
    except Exception as exc:
        failed = await session.get(ExperimentDispatchOutbox, row.id)
        if failed is not None:
            failed.status = "failed"
            failed.last_error = str(exc)
            failed.updated_at = now()
            attempt = await session.get(ExperimentRunAttempt, failed.run_attempt_id)
            if attempt is not None:
                attempt.state = "failed"
                run = await session.get(ExperimentWorkflowRun, attempt.workflow_run_id)
                if run is not None:
                    run.state = "failed"
                    group = await session.get(ExperimentRunGroup, run.run_group_id)
                    if group is not None:
                        group.state = "failed"
                        group.updated_at = now()
        await session.commit()
        raise
    acknowledged = await session.get(ExperimentDispatchOutbox, row.id)
    if acknowledged is None:
        raise DispatchFailure("outbox row disappeared during dispatch")
    acknowledged.status = "acknowledged"
    acknowledged.acknowledgement_json = canonical_json(receipt)
    acknowledged.updated_at = now()
    attempt = await session.get(ExperimentRunAttempt, acknowledged.run_attempt_id)
    if attempt is None:
        raise DispatchFailure("outbox references a missing attempt")
    attempt.state = "dispatched"
    attempt.external_binding_receipt_json = canonical_json(receipt)
    run = await session.get(ExperimentWorkflowRun, attempt.workflow_run_id)
    if run is None:
        raise DispatchFailure("attempt references a missing workflow run")
    run.state = "dispatched"
    expected_generation = int(run.generation)
    run.generation = expected_generation + 1
    sequence = int(
        (
            await session.execute(
                select(func.max(ExperimentRunEvent.sequence_number)).where(
                    ExperimentRunEvent.workflow_run_id == run.resource_id
                )
            )
        ).scalar_one()
        or 0
    ) + 1
    session.add(
        ExperimentRunEvent(
            workspace_id=run.workspace_id,
            workflow_run_id=run.resource_id,
            sequence_number=sequence,
            expected_generation=expected_generation,
            resulting_generation=run.generation,
            idempotency_key=f"scheduler-materialized:{attempt.resource_id}",
            event_type="scheduler_job_materialized",
            payload_json=canonical_json(receipt),
            created_at=now(),
        )
    )
    group = await session.get(ExperimentRunGroup, run.run_group_id)
    if group is not None:
        remaining = (
            await session.execute(
                select(ExperimentDispatchOutbox).where(
                    ExperimentDispatchOutbox.run_attempt_id.in_(
                        select(ExperimentRunAttempt.resource_id).where(
                            ExperimentRunAttempt.workflow_run_id.in_(
                                select(ExperimentWorkflowRun.resource_id).where(
                                    ExperimentWorkflowRun.run_group_id == group.resource_id
                                )
                            )
                        )
                    ),
                    ExperimentDispatchOutbox.status != "acknowledged",
                )
            )
        ).scalars().all()
        group.state = "dispatched" if not remaining else "partially_dispatched"
        group.generation += 1
        group.updated_at = now()
    await session.commit()
    return 1


__all__ = [
    "DispatchFailure",
    "DispatchMaterializer",
    "ExistingJobMaterializer",
    "ExperimentServiceError",
    "IdempotencyConflict",
    "NotFound",
    "RevisionConflict",
    "ValidationFailure",
    "canonical_json",
    "create_dataset",
    "create_experiment",
    "create_experiment_workspace",
    "create_run_group",
    "create_workflow",
    "dispatch_pending_outbox",
    "now",
    "prepare_workflow",
    "save_dataset_revision",
    "save_workflow_draft",
    "save_workflow_revision",
    "sha256_text",
]
