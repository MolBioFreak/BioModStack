"""Business services for global experiment/workspace persistence and dispatch."""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from model_registry import get_registry
from scripts.rfd3_local_redesign.contract import ContractError
from services.rfd3_local_redesign import (
    canonical_local_redesign_data_alias,
    local_redesign_requests_semantically_equal,
    prepare_local_redesign_scheduler_params,
    validate_local_redesign_workflow_params,
)

from experiment_models import (
    ExperimentAggregateHead,
    ExperimentAuditEvent,
    ExperimentDatasetRevisionMember,
    ExperimentDispatchOutbox,
    ExperimentExternalEntityReceipt,
    ExperimentIdempotencyClaim,
    ExperimentLineageEdge,
    ExperimentResource,
    ExperimentResearchRecord,
    ExperimentRevision,
    ExperimentRevisionEdge,
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


PROJECT_STATUSES = {"draft", "active", "on_hold", "completed", "archived"}
EXPERIMENT_STATUSES = {
    "draft",
    "planned",
    "active",
    "analysis",
    "review",
    "completed",
    "blocked",
    "archived",
}
PROJECT_LIFECYCLE_TRANSITIONS = {
    "draft": {"draft", "active", "on_hold"},
    "active": {"active", "on_hold", "completed"},
    "on_hold": {"on_hold", "active", "completed"},
    "completed": {"completed"},
    "archived": {"archived"},
}
EXPERIMENT_LIFECYCLE_TRANSITIONS = {
    "draft": {"draft", "planned", "active", "blocked"},
    "planned": {"planned", "active", "blocked"},
    "active": {"active", "analysis", "blocked"},
    "analysis": {"analysis", "review", "blocked"},
    "review": {"review", "completed", "blocked"},
    "blocked": {"blocked", "planned", "active", "analysis", "review"},
    "completed": {"completed"},
    "archived": {"archived"},
}
DOMAIN_KINDS = {"protein_in_silico", "ngs_molbio"}
RESEARCH_RECORD_KINDS = {"note", "observation", "decision", "conclusion"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def public_workflow_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Publish native workflow intent while hiding every private runtime path."""
    scheduler = payload.get("scheduler")
    if not isinstance(scheduler, dict) or scheduler.get("model_id") != "protein_local_redesign":
        return copy.deepcopy(payload)

    def redact(value: Any) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, child in value.items():
                normalized = str(key).lower()
                if normalized == "input_structure":
                    try:
                        result[str(key)] = canonical_local_redesign_data_alias(child)
                    except ContractError:
                        pass
                    continue
                if normalized in {
                    "input",
                    "input_pdb",
                    "input_cif",
                    "plr_input_pdb",
                    "rfd3_request",
                }:
                    continue
                if any(
                    token in normalized
                    for token in ("path", "directory", "output_dir", "command", "executable")
                ):
                    continue
                result[str(key)] = redact(child)
            return result
        if isinstance(value, list):
            return [redact(child) for child in value]
        return value

    return redact(payload)


def public_preparation_scheduler(payload: dict[str, Any]) -> dict[str, Any]:
    """Publish a prepared scheduler without private runtime paths or native request bodies."""
    if payload.get("model_id") != "protein_local_redesign":
        return copy.deepcopy(payload)
    public = public_workflow_payload({"scheduler": payload})
    scheduler = public.get("scheduler")
    return scheduler if isinstance(scheduler, dict) else {}


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


def scheduler_job_id_for_attempt(attempt_id: str) -> str:
    """Return the deterministic UUIDv5 identity accepted by canonical Job creation."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"bms:global-experiment:core-job:{attempt_id}"))


TYPED_CORE_JOB_MODELS = {
    "boltz2",
    "boltz_cp_experimental",
    "boltzgen",
    "esmfold2",
    "molecular_dynamics",
    "nanopore",
    "ngs_alignment",
    "oligo_builder",
    "oligo_design",
    "ont_fastq_qc",
    "ppiflow",
    "protein_local_redesign",
    "protein_modification_experimental",
    "protenix",
    "rf3",
    "sequence_qc",
    "template_antibody_denovo",
}
TYPED_CORE_JOB_ADAPTERS = {
    f"bms.core-job.{model_id}.adapter.v1": model_id
    for model_id in sorted(TYPED_CORE_JOB_MODELS)
}


def scheduler_job_identity(attempt_id: str, scheduler: Mapping[str, Any]) -> str:
    """Keep CM attempt identity while giving typed core Jobs deterministic UUIDv5 identity."""
    params = scheduler.get("params")
    adapter_id = str(params.get("workflow_adapter") or "") if isinstance(params, dict) else ""
    return scheduler_job_id_for_attempt(attempt_id) if adapter_id in TYPED_CORE_JOB_ADAPTERS else attempt_id


WORKFLOW_ADAPTER_REGISTRY: dict[str, set[str]] = {
    "generic_test": {"generic.test.adapter.v1"},
    "typed_core_job": set(TYPED_CORE_JOB_ADAPTERS),
    "conformational_mapping": {
        "bms.cm.protenix_v2.adapter.v1",
        "bms.cm.confornets.adapter.v1",
    },
}


def register_workflow_adapter(workflow_family: str, adapter_id: str) -> None:
    """Register a server-owned workflow adapter; callers cannot register via HTTP."""
    WORKFLOW_ADAPTER_REGISTRY.setdefault(workflow_family, set()).add(adapter_id)


def _cm_submission_source_ids(submission: dict[str, Any]) -> list[str]:
    backend = submission.get("backend")
    if backend == "protenix_v2_ensemble":
        values = [submission.get("registered_snapshot_id")]
    elif backend == "confornets":
        values = [
            submission.get("registered_sequence_id"),
            submission.get("registered_checkpoint_id"),
            *(submission.get("registered_reference_ids") or []),
        ]
        if submission.get("registered_config_id"):
            values.append(submission["registered_config_id"])
        if submission.get("registered_transfer_id"):
            values.append(submission["registered_transfer_id"])
    else:
        raise ValidationFailure("CM global workflow backend has no materializer")
    if any(not isinstance(value, str) or not value for value in values):
        raise ValidationFailure("CM global workflow source identities are incomplete")
    source_ids = [str(value) for value in values]
    if len(source_ids) != len(set(source_ids)):
        raise ValidationFailure("CM global workflow source identities must be unique")
    return source_ids


def _validate_workflow_payload(payload: dict[str, Any]) -> None:
    required = ("schema", "workflow_family", "contract_version", "adapter_id", "nodes", "edges")
    missing = [field for field in required if field not in payload]
    if missing:
        raise ValidationFailure(f"workflow revision missing required fields: {', '.join(missing)}")
    allowed_top_level = set(required) | {
        "parameters",
        "scheduler",
        "stage",
        "backend",
        "source_receipt_ids",
        "expected_cardinality",
        "depends_on",
    }
    unknown_top_level = sorted(set(payload) - allowed_top_level)
    if unknown_top_level:
        raise ValidationFailure(f"workflow revision has unknown fields: {', '.join(unknown_top_level)}")
    family = str(payload["workflow_family"])
    if str(payload["contract_version"]) != "1":
        raise ValidationFailure("unsupported workflow contract_version")
    schema = str(payload["schema"])
    if not schema.startswith("bms.workflow.") or not schema.endswith(".v1"):
        raise ValidationFailure("unsupported workflow schema")
    adapter_id = str(payload["adapter_id"])
    if adapter_id not in WORKFLOW_ADAPTER_REGISTRY.get(family, set()):
        raise ValidationFailure(f"workflow adapter is not registered: {family}/{adapter_id}")
    if family == "typed_core_job":
        scheduler = payload.get("scheduler")
        expected_model_id = TYPED_CORE_JOB_ADAPTERS[adapter_id]
        if not isinstance(scheduler, dict):
            raise ValidationFailure("typed core workflow requires scheduler settings")
        if scheduler.get("model_id") != expected_model_id:
            raise ValidationFailure("typed core workflow adapter and model_id disagree")
        if not isinstance(scheduler.get("name"), str) or not scheduler["name"].strip():
            raise ValidationFailure("typed core workflow requires a scheduler name")
        if not isinstance(scheduler.get("mode"), str) or not scheduler["mode"].strip():
            raise ValidationFailure("typed core workflow requires a scheduler mode")
        if not isinstance(scheduler.get("params"), dict):
            raise ValidationFailure("typed core workflow scheduler params must be an object")
        if scheduler["params"].get("workflow_adapter") != adapter_id:
            raise ValidationFailure("typed core workflow scheduler adapter identity disagrees")
        if expected_model_id == "protein_local_redesign":
            if scheduler.get("mode") != "local_redesign":
                raise ValidationFailure("native RFD3 typed workflow requires local_redesign mode")
            resources = scheduler.get("resources")
            pinned_gpu = resources.get("pinned_gpu") if isinstance(resources, dict) else None
            if isinstance(pinned_gpu, bool) or not isinstance(pinned_gpu, int) or pinned_gpu < 0:
                raise ValidationFailure(
                    "native RFD3 typed workflow requires scheduler.resources.pinned_gpu as a non-negative integer"
                )
            try:
                validate_local_redesign_workflow_params(
                    scheduler["params"],
                    expected_adapter_id=adapter_id,
                )
            except ContractError as exc:
                raise ValidationFailure(str(exc)) from exc
    if family == "conformational_mapping":
        stage = payload.get("stage")
        stage_by_adapter = {
            "bms.cm.protenix_v2.adapter.v1": ("protenix_v2_sampling", "protenix_v2_ensemble"),
            "bms.cm.confornets.adapter.v1": ("confornets_sampling", "confornets"),
        }
        if adapter_id not in stage_by_adapter:
            raise ValidationFailure(f"CM workflow adapter has no executable materializer: {adapter_id}")
        expected_stage, expected_backend = stage_by_adapter[adapter_id]
        if stage != expected_stage or payload.get("backend") != expected_backend:
            raise ValidationFailure("CM workflow stage, backend, and adapter identity disagree")
        receipt_ids = payload.get("source_receipt_ids")
        if not isinstance(receipt_ids, list) or not receipt_ids or any(
            not isinstance(value, str) or not value for value in receipt_ids
        ):
            raise ValidationFailure("CM workflow requires explicit source receipt IDs")
        scheduler = payload.get("scheduler")
        params = scheduler.get("params") if isinstance(scheduler, dict) else None
        if not isinstance(params, dict):
            raise ValidationFailure("CM workflow requires typed scheduler params")
        if params.get("workflow_adapter") != adapter_id:
            raise ValidationFailure("CM nested workflow adapter disagrees with its authoritative outer adapter")
        submission = params.get("cm_submission")
        if not isinstance(submission, dict):
            raise ValidationFailure("CM workflow requires one typed generator submission")
        if submission.get("backend") != expected_backend:
            raise ValidationFailure("CM nested submission backend disagrees with its authoritative outer backend")
        if receipt_ids != _cm_submission_source_ids(submission):
            raise ValidationFailure("CM workflow source receipt IDs do not bind its submitted sources")
        cardinality = payload.get("expected_cardinality")
        if isinstance(cardinality, bool) or not isinstance(cardinality, int) or cardinality < 1:
            raise ValidationFailure("CM workflow expected_cardinality must be a positive integer")
        dependencies = payload.get("depends_on", [])
        if not isinstance(dependencies, list) or any(not isinstance(value, str) or not value for value in dependencies):
            raise ValidationFailure("CM workflow depends_on must be an ordered ID list")

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
    forbidden_keys = {
        "binary",
        "cmd",
        "command",
        "entry_point",
        "entrypoint",
        "executable",
        "factory",
        "import",
        "loader",
        "module",
        "path",
        "plugin",
        "python_path",
        "runtime_hook",
        "script",
        "shell",
        "callable",
    }
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized_key = str(key).strip().lower().replace("-", "_")
                key_tokens = set(normalized_key.split("_"))
                if (
                    normalized_key in forbidden_keys
                    or normalized_key.endswith("_path")
                    or forbidden_keys.intersection(key_tokens)
                ):
                    raise ValidationFailure("workflow revisions cannot contain executable references")
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(payload)
    allowed_node_keys = {"id", "kind", "required", "adapter_id", "parameters", "label", "depends_on"}
    for index, node in enumerate(nodes):
        unknown_node_keys = sorted(set(node) - allowed_node_keys)
        if unknown_node_keys:
            raise ValidationFailure(
                f"workflow node {index} has unknown fields: {', '.join(unknown_node_keys)}"
            )
    allowed_edge_keys = {"source", "target", "from", "to", "kind", "edge_kind"}
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValidationFailure("workflow edges must be objects")
        unknown_edge_keys = sorted(set(edge) - allowed_edge_keys)
        if unknown_edge_keys:
            raise ValidationFailure(f"workflow edge has unknown fields: {', '.join(unknown_edge_keys)}")
        source = edge.get("source", edge.get("from"))
        target = edge.get("target", edge.get("to"))
        if str(source) not in node_ids or str(target) not in node_ids:
            raise ValidationFailure("workflow edge references an unknown node")
    scheduler = payload.get("scheduler")
    if scheduler is not None:
        if not isinstance(scheduler, dict):
            raise ValidationFailure("workflow scheduler must be an object")
        unknown_scheduler_keys = sorted(
            set(scheduler) - {"name", "model_id", "mode", "params", "resources", "profile"}
        )
        if unknown_scheduler_keys:
            raise ValidationFailure(
                f"workflow scheduler has unknown fields: {', '.join(unknown_scheduler_keys)}"
            )


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
        allowed_parent_kinds = {"workspace", "experiment"}
        if kind == "domain_experiment":
            allowed_parent_kinds = {"experiment"}
        elif kind in {"workflow", "dataset"}:
            allowed_parent_kinds = {"domain_experiment"}
        if parent is None or parent.kind not in allowed_parent_kinds:
            expected = (
                "an experiment"
                if kind == "domain_experiment"
                else "a Domain Experiment"
                if kind in {"workflow", "dataset"}
                else "a workspace or experiment"
            )
            raise ValidationFailure(f"aggregate parent must be {expected}")
        if parent.kind != "workspace" and parent.workspace_id != workspace_id:
            raise ValidationFailure("aggregate parent belongs to another workspace")
        if parent.kind == "workspace" and parent.id != workspace_id:
            raise ValidationFailure("aggregate parent belongs to another workspace")
        parent_head = await session.get(ExperimentAggregateHead, parent_id)
        if parent_head is None:
            raise NotFound(f"aggregate parent not found: {parent_id}")
        if parent_head.lifecycle_state == "archived":
            raise ValidationFailure("aggregate parent is archived")
    resource = await _resource(
        session,
        kind=kind,
        workspace_id=workspace_id,
        lifecycle_owner_id=(
            parent_id
            if kind in {"domain_experiment", "workflow", "dataset"} and parent_id
            else workspace_id
        ),
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
    *,
    payload: dict[str, Any] | None = None,
) -> ExperimentAggregateHead:
    head = await _create_aggregate(
        session,
        workspace_id=workspace_id,
        kind="experiment",
        display_name=name,
        description=question,
        parent_id=workspace_id,
    )
    if payload is not None:
        await _save_revision(
            session,
            aggregate_id=head.aggregate_id,
            aggregate_kind="experiment",
            payload=payload,
            expected_head_generation=0,
        )
        await session.refresh(head)
    return head


async def create_project(
    session: AsyncSession,
    payload: dict[str, Any],
) -> ExperimentAggregateHead:
    payload = {**payload, "needs_metadata_review": False}
    _validate_hierarchy_payload("workspace", payload)
    head = await create_experiment_workspace(
        session,
        str(payload["name"]),
        str(payload.get("description") or ""),
    )
    await _save_revision(
        session,
        aggregate_id=head.aggregate_id,
        aggregate_kind="workspace",
        payload=payload,
        expected_head_generation=0,
    )
    await session.refresh(head)
    return head


async def create_global_experiment(
    session: AsyncSession,
    project_id: str,
    payload: dict[str, Any],
) -> ExperimentAggregateHead:
    payload = {**payload, "needs_metadata_review": False}
    _validate_hierarchy_payload("experiment", payload)
    return await create_experiment(
        session,
        project_id,
        str(payload["name"]),
        str(payload.get("scientific_question") or payload.get("description") or ""),
        payload=payload,
    )


async def create_domain_experiment(
    session: AsyncSession,
    project_id: str,
    global_experiment_id: str,
    payload: dict[str, Any],
) -> ExperimentAggregateHead:
    _validate_hierarchy_payload("domain_experiment", payload)
    head = await _create_aggregate(
        session,
        workspace_id=project_id,
        kind="domain_experiment",
        display_name=str(payload["name"]),
        description=str(payload.get("objective") or ""),
        parent_id=global_experiment_id,
    )
    await _save_revision(
        session,
        aggregate_id=head.aggregate_id,
        aggregate_kind="domain_experiment",
        payload=payload,
        expected_head_generation=0,
    )
    await session.refresh(head)
    return head


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
    _validate_workflow_payload(payload)
    draft.canonical_payload = canonical_json(payload)
    draft.generation += 1
    draft.updated_at = now()
    await session.flush()
    return draft


def _validate_hierarchy_payload(aggregate_kind: str, payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise ValidationFailure("aggregate payload must be an object")
    expected_schema = {
        "workspace": "bms.project.v1",
        "experiment": "bms.global-experiment.v1",
        "domain_experiment": "bms.domain-experiment.v1",
    }[aggregate_kind]
    if payload.get("schema") != expected_schema:
        raise ValidationFailure(f"{aggregate_kind} payload schema must be {expected_schema}")
    required = {
        "workspace": {"name", "description", "research_objective", "status", "needs_metadata_review"},
        "experiment": {"name", "objective", "scientific_question", "description", "status", "priority", "success_criteria", "needs_metadata_review"},
        "domain_experiment": {"domain_kind", "domain_contract_version", "name", "objective", "status", "domain_payload"},
    }[aggregate_kind]
    missing = sorted(field for field in required if field not in payload)
    if missing:
        raise ValidationFailure(f"{aggregate_kind} payload missing required fields: {', '.join(missing)}")
    statuses = PROJECT_STATUSES if aggregate_kind == "workspace" else EXPERIMENT_STATUSES
    if payload.get("status") not in statuses:
        raise ValidationFailure(f"invalid {aggregate_kind} lifecycle status")
    if aggregate_kind == "experiment":
        if payload.get("status") == "active":
            criteria = payload.get("success_criteria")
            if not isinstance(criteria, list) or not criteria:
                raise ValidationFailure("active global experiments require success criteria")
        if payload.get("status") == "completed":
            if not str(payload.get("review_summary") or "").strip() or not str(payload.get("conclusion") or "").strip():
                raise ValidationFailure("completed global experiments require review_summary and conclusion")
    if aggregate_kind == "domain_experiment":
        domain_kind = payload.get("domain_kind")
        if domain_kind not in DOMAIN_KINDS:
            raise ValidationFailure("domain_kind must be protein_in_silico or ngs_molbio")
        if payload.get("domain_contract_version") != "1":
            raise ValidationFailure("unsupported domain_contract_version")
        domain_payload = payload.get("domain_payload")
        if not isinstance(domain_payload, dict):
            raise ValidationFailure("domain_payload must be an object")
        if domain_kind == "ngs_molbio":
            if domain_payload != {"schema": "bms.ngs-molbio-experiment.v1"}:
                raise ValidationFailure("ngs_molbio domain_payload has unsupported or unknown fields")
            return
        expected_keys = {
            "schema",
            "experiment_mode",
            "targets",
            "scientific_objective",
            "design_constraints",
            "planned_capabilities",
            "comparison_groups",
            "validation_strategy",
        }
        if set(domain_payload) != expected_keys:
            raise ValidationFailure("protein_in_silico domain_payload fields do not match the frozen contract")
        if domain_payload.get("schema") != "bms.protein-in-silico-experiment.v1":
            raise ValidationFailure("protein_in_silico domain_payload schema is invalid")
        if domain_payload.get("experiment_mode") not in {
            "exploration", "design", "redesign", "prediction", "validation", "comparison", "simulation", "analysis"
        }:
            raise ValidationFailure("protein_in_silico experiment_mode is invalid")
        targets = domain_payload.get("targets")
        if not isinstance(targets, list):
            raise ValidationFailure("protein_in_silico targets must be an array")
        target_keys = {"target_id", "label", "entity_receipt_ids", "role"}
        target_roles = {"target", "binder", "partner", "template", "reference", "control", "other"}
        for target in targets:
            if not isinstance(target, dict) or set(target) != target_keys:
                raise ValidationFailure("protein_in_silico target fields do not match the frozen contract")
            if not str(target.get("target_id") or "").strip() or target.get("role") not in target_roles:
                raise ValidationFailure("protein_in_silico target identity or role is invalid")
            receipt_ids = target.get("entity_receipt_ids")
            if not isinstance(receipt_ids, list) or any(not isinstance(value, str) or not value for value in receipt_ids):
                raise ValidationFailure("protein_in_silico target receipt IDs are invalid")
        if not isinstance(domain_payload.get("scientific_objective"), str):
            raise ValidationFailure("protein_in_silico scientific_objective must be a string")
        for field in ("design_constraints", "comparison_groups"):
            values = domain_payload.get(field)
            if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
                raise ValidationFailure(f"protein_in_silico {field} must contain objects")
        for field in ("planned_capabilities", "validation_strategy"):
            values = domain_payload.get(field)
            if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
                raise ValidationFailure(f"protein_in_silico {field} must contain non-empty capability IDs")


def _hierarchy_reference_ids(payload: dict[str, Any]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    receipt_references: list[tuple[str, str]] = []
    dataset_references: list[tuple[str, str]] = []

    def collect(field: str, *, role: str, target: list[tuple[str, str]], source: dict[str, Any]) -> None:
        values = source.get(field, [])
        if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
            raise ValidationFailure(f"{field} must contain non-empty resource IDs")
        target.extend((role, value) for value in values)

    schema = payload.get("schema")
    if schema == "bms.global-experiment.v1":
        collect(
            "shared_source_receipt_ids",
            role="shared_source_receipt",
            target=receipt_references,
            source=payload,
        )
        collect("shared_dataset_ids", role="shared_dataset", target=dataset_references, source=payload)
    elif schema == "bms.domain-experiment.v1":
        collect("source_receipt_ids", role="source_receipt", target=receipt_references, source=payload)
        collect("dataset_ids", role="dataset", target=dataset_references, source=payload)
        domain_payload = payload.get("domain_payload")
        if isinstance(domain_payload, dict):
            targets = domain_payload.get("targets", [])
            if isinstance(targets, list):
                for target_index, target_payload in enumerate(targets):
                    if isinstance(target_payload, dict):
                        collect(
                            "entity_receipt_ids",
                            role=f"target_entity_receipt:{target_index}",
                            target=receipt_references,
                            source=target_payload,
                        )
    return receipt_references, dataset_references


async def _resolve_hierarchy_references(
    session: AsyncSession,
    *,
    workspace_id: str,
    payload: dict[str, Any],
) -> list[dict[str, str | int]]:
    receipt_references, dataset_references = _hierarchy_reference_ids(payload)
    bindings: list[dict[str, str | int]] = []

    receipt_ids = {receipt_id for _role, receipt_id in receipt_references}
    receipts = (
        (
            await session.execute(
                select(ExperimentExternalEntityReceipt).where(
                    ExperimentExternalEntityReceipt.id.in_(receipt_ids)
                )
            )
        ).scalars().all()
        if receipt_ids
        else []
    )
    receipts_by_id = {receipt.id: receipt for receipt in receipts}
    if set(receipts_by_id) != receipt_ids:
        raise ValidationFailure("one or more hierarchy receipt references are unknown")
    receipt_ordinals: dict[str, int] = {}
    for role, receipt_id in receipt_references:
        receipt = receipts_by_id[receipt_id]
        receipt_resource = await session.get(ExperimentResource, receipt.resource_id)
        if (
            receipt_resource is None
            or receipt_resource.kind != "external_entity_receipt"
            or receipt_resource.workspace_id != workspace_id
            or receipt_resource.archived_at is not None
        ):
            raise ValidationFailure("hierarchy receipt resource is unavailable or belongs to another project")
        if receipt.workspace_id != workspace_id:
            raise ValidationFailure("hierarchy receipt reference belongs to another project")
        if receipt.availability != "available":
            raise ValidationFailure("hierarchy receipt reference is not verified as available")
        authority = str(receipt.verification_authority or "").strip()
        if (
            not authority
            or authority in {"legacy_unverified", "caller_unverified"}
            or authority.startswith("unverified:")
        ):
            raise ValidationFailure("hierarchy receipt reference has no durable server verification authority")
        try:
            acknowledgement = json.loads(receipt.acknowledgement_json or "{}")
        except json.JSONDecodeError as exc:
            raise ValidationFailure("hierarchy receipt acknowledgement is malformed") from exc
        expected_acknowledgement = {
            "schema": "bms.global.external-entity-receipt.v1",
            "store_id": receipt.store_id,
            "entity_kind": receipt.entity_kind,
            "entity_id": receipt.entity_id,
            "entity_revision_id": receipt.generation_or_revision,
            "content_digest": receipt.content_digest,
            "availability": receipt.availability,
            "verifier_id": authority,
        }
        if not isinstance(acknowledgement, dict) or any(
            str(acknowledgement.get(field) or "") != str(value)
            for field, value in expected_acknowledgement.items()
        ):
            raise ValidationFailure("hierarchy receipt acknowledgement does not match persisted authority")
        if len(receipt.content_digest) != 64 or any(
            character not in "0123456789abcdef" for character in receipt.content_digest
        ):
            raise ValidationFailure("hierarchy receipt has no immutable digest")
        ordinal = receipt_ordinals.get(role, 0)
        receipt_ordinals[role] = ordinal + 1
        bindings.append(
            {
                "role": role,
                "ordinal": ordinal,
                "target_resource_id": receipt.id,
                "expected_sha256": receipt.content_digest,
            }
        )

    dataset_ordinals: dict[str, int] = {}
    for role, dataset_id in dataset_references:
        resource = await session.get(ExperimentResource, dataset_id)
        head = await session.get(ExperimentAggregateHead, dataset_id)
        if resource is None or head is None or resource.kind != "dataset" or head.aggregate_kind != "dataset":
            raise ValidationFailure("one or more hierarchy dataset references are unknown")
        if resource.workspace_id != workspace_id or head.workspace_id != workspace_id:
            raise ValidationFailure("hierarchy dataset reference belongs to another project")
        if resource.archived_at is not None or head.lifecycle_state == "archived" or head.current_revision_id is None:
            raise ValidationFailure("hierarchy dataset reference is not available")
        revision = await session.get(ExperimentRevision, head.current_revision_id)
        if revision is None or revision.subject_id != dataset_id:
            raise ValidationFailure("hierarchy dataset reference has no durable server revision authority")
        if revision.payload_sha256 != sha256_text(revision.canonical_payload):
            raise ValidationFailure("hierarchy dataset revision immutable digest does not match")
        ordinal = dataset_ordinals.get(role, 0)
        dataset_ordinals[role] = ordinal + 1
        bindings.append(
            {
                "role": role,
                "ordinal": ordinal,
                "target_resource_id": dataset_id,
                "expected_sha256": revision.payload_sha256,
            }
        )
    return bindings


def _validate_lifecycle_transition(
    aggregate_kind: str,
    current_status: str,
    requested_status: str,
    lifecycle_operation: str | None,
) -> None:
    if lifecycle_operation == "archive":
        if current_status == "archived" or requested_status != "archived":
            raise ValidationFailure(
                f"invalid lifecycle transition for {aggregate_kind}: {current_status} -> {requested_status}"
            )
        return
    if lifecycle_operation == "restore":
        if current_status != "archived" or requested_status == "archived":
            raise ValidationFailure(
                f"invalid lifecycle transition for {aggregate_kind}: {current_status} -> {requested_status}"
            )
        return
    if lifecycle_operation is not None:
        raise ValidationFailure(f"unsupported lifecycle operation: {lifecycle_operation}")
    transitions = (
        PROJECT_LIFECYCLE_TRANSITIONS
        if aggregate_kind == "workspace"
        else EXPERIMENT_LIFECYCLE_TRANSITIONS
    )
    if requested_status not in transitions.get(current_status, set()):
        raise ValidationFailure(
            f"invalid lifecycle transition for {aggregate_kind}: {current_status} -> {requested_status}"
        )


async def _save_revision(
    session: AsyncSession,
    *,
    aggregate_id: str,
    aggregate_kind: str,
    payload: dict[str, Any],
    expected_head_generation: int,
    lifecycle_operation: str | None = None,
) -> ExperimentRevision:
    head = await _head(session, aggregate_id, aggregate_kind)
    if head.head_generation != expected_head_generation:
        raise RevisionConflict(
            f"{aggregate_kind} head generation conflict: expected {expected_head_generation}, current {head.head_generation}"
        )
    workspace_id = await _resource_workspace(session, aggregate_id)
    hierarchy_bindings: list[dict[str, str | int]] = []
    previous_status: str | None = None
    if aggregate_kind == "workflow":
        _validate_workflow_payload(payload)
    elif aggregate_kind in {"workspace", "experiment", "domain_experiment"}:
        _validate_hierarchy_payload(aggregate_kind, payload)
        current_payload: dict[str, Any] | None = None
        if head.current_revision_id:
            current_revision = await session.get(ExperimentRevision, head.current_revision_id)
            if current_revision is None or current_revision.subject_id != aggregate_id:
                raise ValidationFailure("aggregate current revision is unavailable or belongs to another aggregate")
            current_payload = json.loads(current_revision.canonical_payload)
            if not isinstance(current_payload, dict):
                raise ValidationFailure("aggregate current revision payload is invalid")
            previous_status = str(current_payload.get("status") or "")
            if previous_status != head.lifecycle_state:
                raise ValidationFailure("aggregate lifecycle projection is inconsistent with current revision")
            _validate_lifecycle_transition(
                aggregate_kind,
                previous_status,
                str(payload["status"]),
                lifecycle_operation,
            )
        if aggregate_kind == "domain_experiment" and current_payload is not None:
            if current_payload.get("domain_kind") != payload.get("domain_kind"):
                raise ValidationFailure("domain_kind is immutable; create a new Domain Experiment")
        if payload.get("status") == "archived" and lifecycle_operation != "archive":
            raise ValidationFailure("archival is a lifecycle operation; use the archive route")
        if head.lifecycle_state == "archived" and lifecycle_operation != "restore":
            raise ValidationFailure("archived aggregates must be restored before revision")
        hierarchy_bindings = await _resolve_hierarchy_references(
            session,
            workspace_id=workspace_id,
            payload=payload,
        )
    payload_json = canonical_json(payload)
    graph_json = canonical_json(
        {
            "nodes": payload.get("nodes", []),
            "edges": payload.get("edges", []),
            "references": hierarchy_bindings,
        }
    )
    parent_revision_id = head.current_revision_id
    next_generation = expected_head_generation + 1
    revision: ExperimentRevision
    try:
        async with session.begin_nested():
            revision_resource = await _resource(
                session,
                kind="revision",
                workspace_id=workspace_id,
                lifecycle_owner_id=aggregate_id,
            )
            revision = ExperimentRevision(
                resource_id=revision_resource.id,
                subject_id=aggregate_id,
                revision_number=next_generation,
                parent_revision_id=parent_revision_id,
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
            for binding in hierarchy_bindings:
                session.add(
                    ExperimentRevisionEdge(
                        revision_id=revision.resource_id,
                        target_resource_id=str(binding["target_resource_id"]),
                        role=str(binding["role"]),
                        ordinal=int(binding["ordinal"]),
                        expected_sha256=str(binding["expected_sha256"]),
                        metadata_json=canonical_json({"authority": "server_resolved"}),
                    )
                )
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
                    ExperimentAggregateHead.aggregate_kind == aggregate_kind,
                    ExperimentAggregateHead.head_generation == expected_head_generation,
                    ExperimentAggregateHead.current_revision_id == parent_revision_id,
                )
                .values(
                    current_revision_id=revision.resource_id,
                    head_generation=next_generation,
                    lifecycle_state=(
                        str(payload["status"])
                        if aggregate_kind in {"workspace", "experiment", "domain_experiment"}
                        else "validated"
                    ),
                    updated_at=now(),
                )
                .execution_options(synchronize_session=False)
            )
            if changed.rowcount != 1:
                raise RevisionConflict("aggregate head changed while saving revision")
    except (RevisionConflict, IntegrityError) as exc:
        await session.refresh(head)
        raise RevisionConflict(
            f"{aggregate_kind} head generation conflict: expected {expected_head_generation}, current {head.head_generation}"
        ) from exc
    add_audit_event(
        session,
        workspace_id=workspace_id,
        resource_id=revision.resource_id,
        event_type="immutable_revision_saved",
        generation=revision.revision_number,
        payload={"subject_id": aggregate_id, "payload_sha256": revision.payload_sha256},
    )
    requested_status = str(payload.get("status") or "")
    if previous_status is not None and previous_status != requested_status:
        add_audit_event(
            session,
            workspace_id=workspace_id,
            resource_id=aggregate_id,
            event_type="aggregate_lifecycle_transitioned",
            generation=revision.revision_number,
            payload={"from": previous_status, "to": requested_status},
        )
    await session.refresh(head)
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
        experiment_id=source.parent_id,
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
    clone_revision = await _save_revision(
        session,
        aggregate_id=clone.aggregate_id,
        aggregate_kind="workflow",
        payload=json.loads(revision.canonical_payload),
        expected_head_generation=0,
    )
    draft.base_revision_id = clone_revision.resource_id
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
        generation=clone.head_generation,
        payload={
            "source_workflow_id": source_workflow_id,
            "source_revision_id": revision.resource_id,
            "clone_revision_id": clone_revision.resource_id,
        },
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
    expected_generation = head.head_generation if expected_head_generation is None else expected_head_generation
    if head.head_generation != expected_generation:
        raise RevisionConflict("aggregate head changed before archive")
    resource = await session.get(ExperimentResource, aggregate_id)
    if resource is None:
        raise NotFound(f"aggregate not found: {aggregate_id}")
    if head.lifecycle_state == "archived" or resource.archived_at is not None:
        raise ValidationFailure("aggregate is already archived")
    if head.aggregate_kind in {"workspace", "experiment", "domain_experiment"}:
        if head.current_revision_id is None:
            raise ValidationFailure("hierarchy aggregate has no immutable revision")
        current_revision = await session.get(ExperimentRevision, head.current_revision_id)
        if current_revision is None:
            raise ValidationFailure("hierarchy aggregate current revision is unavailable")
        payload = json.loads(current_revision.canonical_payload)
        payload["status"] = "archived"
        payload["change_summary"] = "archived"
        await _save_revision(
            session,
            aggregate_id=aggregate_id,
            aggregate_kind=head.aggregate_kind,
            payload=payload,
            expected_head_generation=expected_generation,
            lifecycle_operation="archive",
        )
        await session.refresh(head)
    else:
        head.lifecycle_state = "archived"
        head.updated_at = now()
    resource.archived_at = now()
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


async def restore_aggregate(
    session: AsyncSession,
    aggregate_id: str,
    *,
    expected_head_generation: int | None = None,
) -> ExperimentAggregateHead:
    head = await _head(session, aggregate_id)
    expected_generation = head.head_generation if expected_head_generation is None else expected_head_generation
    if head.head_generation != expected_generation:
        raise RevisionConflict("aggregate head changed before restore")
    resource = await session.get(ExperimentResource, aggregate_id)
    if resource is None:
        raise NotFound(f"aggregate not found: {aggregate_id}")
    if head.lifecycle_state != "archived" and resource.archived_at is None:
        raise ValidationFailure("aggregate is not archived")
    lifecycle_state = "draft"
    if head.aggregate_kind in {"workspace", "experiment", "domain_experiment"}:
        if head.current_revision_id is None:
            raise ValidationFailure("hierarchy aggregate has no immutable revision")
        archived_revision = await session.get(ExperimentRevision, head.current_revision_id)
        if archived_revision is None:
            raise ValidationFailure("hierarchy aggregate current revision is unavailable")
        archived_payload = json.loads(archived_revision.canonical_payload)
        prior_revision = (
            await session.get(ExperimentRevision, archived_revision.parent_revision_id)
            if archived_revision.parent_revision_id
            else None
        )
        prior_payload = json.loads(prior_revision.canonical_payload) if prior_revision is not None else {}
        lifecycle_state = str(prior_payload.get("status") or "draft")
        if lifecycle_state == "archived":
            lifecycle_state = "draft"
        archived_payload["status"] = lifecycle_state
        archived_payload["change_summary"] = "restored"
        await _save_revision(
            session,
            aggregate_id=aggregate_id,
            aggregate_kind=head.aggregate_kind,
            payload=archived_payload,
            expected_head_generation=expected_generation,
            lifecycle_operation="restore",
        )
        await session.refresh(head)
    else:
        head.lifecycle_state = "draft"
        head.updated_at = now()
    resource.archived_at = None
    workspace_id = await _resource_workspace(session, aggregate_id)
    add_audit_event(
        session,
        workspace_id=workspace_id,
        resource_id=aggregate_id,
        event_type="aggregate_restored",
        generation=head.head_generation,
        payload={"lifecycle_state": lifecycle_state},
    )
    await session.flush()
    return head


async def save_hierarchy_revision(
    session: AsyncSession,
    aggregate_id: str,
    aggregate_kind: str,
    payload: dict[str, Any],
    *,
    expected_head_generation: int,
) -> ExperimentRevision:
    if aggregate_kind not in {"workspace", "experiment", "domain_experiment"}:
        raise ValidationFailure("unsupported hierarchy aggregate kind")
    return await _save_revision(
        session,
        aggregate_id=aggregate_id,
        aggregate_kind=aggregate_kind,
        payload=payload,
        expected_head_generation=expected_head_generation,
    )


async def append_research_record(
    session: AsyncSession,
    *,
    workspace_id: str,
    subject_resource_id: str,
    record_kind: str,
    body: str,
    author: str | None = None,
    source_receipt_ids: list[str] | None = None,
    supersedes_record_id: str | None = None,
) -> ExperimentResearchRecord:
    await _workspace(session, workspace_id)
    if record_kind not in RESEARCH_RECORD_KINDS:
        raise ValidationFailure("record_kind must be note, observation, decision, or conclusion")
    if not isinstance(body, str) or not body.strip():
        raise ValidationFailure("research record body must not be empty")
    subject = await session.get(ExperimentResource, subject_resource_id)
    if subject is None:
        raise NotFound(f"research record subject not found: {subject_resource_id}")
    subject_workspace = subject.id if subject.kind == "workspace" else subject.workspace_id
    if subject_workspace != workspace_id or subject.kind not in {"workspace", "experiment", "domain_experiment"}:
        raise ValidationFailure("research record subject belongs to another project or is not a hierarchy aggregate")
    if supersedes_record_id is not None:
        prior = await session.get(ExperimentResearchRecord, supersedes_record_id)
        if prior is None:
            raise NotFound(f"research record not found: {supersedes_record_id}")
        if prior.subject_resource_id != subject_resource_id or prior.workspace_id != workspace_id:
            raise ValidationFailure("replacement record must keep the same project scope")
    receipt_ids = source_receipt_ids or []
    if any(not isinstance(receipt_id, str) or not receipt_id for receipt_id in receipt_ids):
        raise ValidationFailure("source_receipt_ids must contain non-empty strings")
    if receipt_ids:
        receipts = (
            await session.execute(
                select(ExperimentExternalEntityReceipt).where(
                    ExperimentExternalEntityReceipt.id.in_(receipt_ids)
                )
            )
        ).scalars().all()
        by_id = {receipt.id: receipt for receipt in receipts}
        if set(by_id) != set(receipt_ids):
            raise ValidationFailure("one or more source receipts are unknown")
        if any(receipt.workspace_id != workspace_id for receipt in receipts):
            raise ValidationFailure("source receipt belongs to another project")
        if any(receipt.availability != "available" for receipt in receipts):
            raise ValidationFailure("source receipt is not currently verified as available")
        if any(
            receipt.verification_authority in {"legacy_unverified", "caller_unverified"}
            for receipt in receipts
        ):
            raise ValidationFailure("source receipt has no persisted server verification authority")
        for receipt in receipts:
            try:
                acknowledgement = json.loads(receipt.acknowledgement_json or "{}")
            except json.JSONDecodeError as exc:
                raise ValidationFailure("source receipt acknowledgement is invalid") from exc
            if (
                acknowledgement.get("schema") != "bms.global.external-entity-receipt.v1"
                or acknowledgement.get("verifier_id") != receipt.verification_authority
                or acknowledgement.get("store_id") != receipt.store_id
                or acknowledgement.get("entity_kind") != receipt.entity_kind
                or acknowledgement.get("entity_id") != receipt.entity_id
                or str(acknowledgement.get("entity_revision_id")) != receipt.generation_or_revision
                or acknowledgement.get("content_digest") != receipt.content_digest
                or not acknowledgement.get("verifier_id")
                or not acknowledgement.get("source_build_revision")
                or not acknowledgement.get("verified_at")
                or not acknowledgement.get("reopen_uri")
            ):
                raise ValidationFailure("source receipt is not server verified")
    resource = await _resource(
        session,
        kind="research_record",
        workspace_id=workspace_id,
        lifecycle_owner_id=subject_resource_id,
    )
    record = ExperimentResearchRecord(
        resource_id=resource.id,
        workspace_id=workspace_id,
        subject_resource_id=subject_resource_id,
        record_kind=record_kind,
        body=body,
        author=author,
        source_receipt_ids_json=canonical_json(receipt_ids),
        supersedes_record_id=supersedes_record_id,
        created_at=now(),
    )
    session.add(record)
    await session.flush()
    subject_head = await session.get(ExperimentAggregateHead, subject_resource_id)
    add_audit_event(
        session,
        workspace_id=workspace_id,
        resource_id=record.resource_id,
        event_type="research_record_appended",
        generation=subject_head.head_generation if subject_head is not None else 0,
        payload={
            "subject_resource_id": subject_resource_id,
            "record_kind": record_kind,
            "supersedes_record_id": supersedes_record_id,
        },
    )
    return record


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
    try:
        _validate_workflow_payload(payload)
    except ValidationFailure as exc:
        reasons.append(str(exc))
    source_receipt_ids = payload.get("source_receipt_ids") or []
    if not isinstance(source_receipt_ids, list):
        reasons.append("workflow source_receipt_ids must be a list")
        source_receipt_ids = []
    for source_receipt_id in source_receipt_ids:
        receipt = await session.get(ExperimentExternalEntityReceipt, str(source_receipt_id))
        if receipt is None or receipt.workspace_id != workspace_id:
            reasons.append(f"source receipt is unavailable: {source_receipt_id}")
        elif receipt.availability != "available":
            reasons.append(f"source receipt is not available: {source_receipt_id}")
    if not isinstance(scheduler_payload, dict):
        scheduler_payload = {}
        reasons.append("workflow revision has no scheduler payload")
    else:
        scheduler_payload = copy.deepcopy(scheduler_payload)
        for field in ("name", "model_id", "mode", "params"):
            if field not in scheduler_payload:
                reasons.append(f"scheduler payload missing {field}")
        if not isinstance(scheduler_payload.get("params", {}), dict):
            reasons.append("scheduler params must be an object")
        elif payload.get("workflow_family") == "typed_core_job":
            model_id = scheduler_payload.get("model_id")
            mode = scheduler_payload.get("mode")
            params = scheduler_payload.get("params")
            if isinstance(model_id, str) and isinstance(mode, str) and isinstance(params, dict):
                if model_id == "protein_local_redesign":
                    try:
                        params = prepare_local_redesign_scheduler_params(
                            params,
                            job_name=str(scheduler_payload.get("name") or ""),
                            expected_adapter_id=str(payload.get("adapter_id") or ""),
                        )
                    except ContractError as exc:
                        reasons.append(str(exc))
                    else:
                        scheduler_payload["params"] = params
                reasons.extend(get_registry().validate_job_params(model_id, mode, params))
        elif payload.get("workflow_family") == "conformational_mapping":
            scheduler_payload["params"]["cm_source_receipt_ids"] = list(
                payload.get("source_receipt_ids") or []
            )
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
        "validator": "global-workflow-contract.v2",
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
            validator_version="v2",
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


async def _validate_preparation_authority(
    session: AsyncSession, preparation: ExperimentWorkflowPreparation
) -> None:
    try:
        normalized = json.loads(preparation.normalized_request_json)
        scheduler = json.loads(preparation.scheduler_payload_json)
        receipt = json.loads(preparation.validation_receipt_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValidationFailure("preparation validation authority is malformed") from exc
    validation = await session.get(ExperimentValidation, preparation.validation_resource_id)
    if (
        validation is None
        or validation.subject_resource_id != preparation.resource_id
        or validation.outcome != "valid"
        or validation.receipt_json != preparation.validation_receipt_json
        or validation.receipt_sha256 != sha256_text(validation.receipt_json)
        or receipt != json.loads(validation.receipt_json)
    ):
        raise ValidationFailure("preparation does not match its immutable validation authority")
    if (
        not isinstance(normalized, dict)
        or not isinstance(scheduler, dict)
        or not isinstance(receipt, dict)
        or sha256_text(preparation.normalized_request_json) != preparation.normalized_request_sha256
        or receipt.get("status") != "valid"
        or receipt.get("workflow_revision_id") != preparation.workflow_revision_id
        or receipt.get("normalized_request_sha256") != preparation.normalized_request_sha256
        or normalized.get("workflow_revision_id") != preparation.workflow_revision_id
    ):
        raise ValidationFailure("preparation no longer matches its immutable validation authority")
    normalized_workflow = normalized.get("workflow")
    if not isinstance(normalized_workflow, dict):
        raise ValidationFailure("preparation workflow authority is unavailable")
    raw_expected_scheduler = normalized_workflow.get("scheduler")
    if not isinstance(raw_expected_scheduler, dict):
        raise ValidationFailure("preparation scheduler authority is unavailable")
    expected_scheduler = copy.deepcopy(raw_expected_scheduler)
    if (
        normalized_workflow.get("workflow_family") == "typed_core_job"
        and expected_scheduler.get("model_id") == "protein_local_redesign"
    ):
        expected_params = expected_scheduler.get("params")
        if not isinstance(expected_params, dict):
            raise ValidationFailure("native RFD3 preparation scheduler parameters are malformed")
        try:
            expected_scheduler["params"] = prepare_local_redesign_scheduler_params(
                expected_params,
                job_name=str(expected_scheduler.get("name") or ""),
                expected_adapter_id=str(normalized_workflow.get("adapter_id") or ""),
            )
        except ContractError as exc:
            raise ValidationFailure(str(exc)) from exc
    if normalized_workflow.get("workflow_family") == "conformational_mapping":
        expected_params = expected_scheduler.get("params")
        if not isinstance(expected_params, dict):
            raise ValidationFailure("preparation scheduler parameters are malformed")
        expected_params["cm_source_receipt_ids"] = list(normalized_workflow.get("source_receipt_ids") or [])
    if canonical_json(scheduler) != canonical_json(expected_scheduler):
        raise ValidationFailure("preparation scheduler no longer matches its validated workflow")


async def create_run_group(
    session: AsyncSession,
    workspace_id: str,
    preparation_ids: list[str],
    *,
    idempotency_key: str,
    idempotency_authority: dict[str, Any] | None = None,
) -> ExperimentRunGroup:
    await _workspace(session, workspace_id)
    request: dict[str, Any] = {
        "workspace_id": workspace_id,
        "preparation_ids": [str(value) for value in preparation_ids],
    }
    if idempotency_authority is not None:
        request["idempotency_authority"] = idempotency_authority
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
    for preparation in preparations.values():
        await _validate_preparation_authority(session, preparation)
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
        scheduler_payload = json.loads(preparation.scheduler_payload_json)
        attempt = ExperimentRunAttempt(
            resource_id=attempt_resource.id,
            workspace_id=workspace_id,
            workflow_run_id=workflow_run.resource_id,
            attempt_number=1,
            scheduler_job_id=scheduler_job_identity(attempt_resource.id, scheduler_payload),
            state="pending",
            created_at=now(),
        )
        session.add(attempt)
        await session.flush()
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
LIVE_CORE_JOB_STATE_MAP = {
    "pending": "dispatched",
    "queued": "dispatched",
    "dispatching": "dispatched",
    "processing": "running",
    "running": "running",
}
LIVE_CORE_JOB_STATES = set(LIVE_CORE_JOB_STATE_MAP)


def _public_runtime_metadata(value: Any) -> Any:
    """Remove filesystem/process launch details from durable public receipts."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            normalized = str(key).lower()
            if any(token in normalized for token in ("path", "directory", "output_dir", "command", "executable")):
                continue
            result[str(key)] = _public_runtime_metadata(child)
        return result
    if isinstance(value, list):
        return [_public_runtime_metadata(child) for child in value]
    return value


def _project_run_group_state(states: list[str]) -> str:
    if not states:
        return "dispatch_pending"
    terminal = {"completed", "failed", "cancelled"}
    if all(state == "cancelled" for state in states):
        return "cancelled"
    if all(state in terminal for state in states):
        if any(state == "failed" for state in states):
            return "failed"
        return "completed"
    if any(state == "running" for state in states):
        return "running"
    if any(state in {"pending", "queued"} for state in states):
        return "queued"
    if all(state == "dispatched" for state in states):
        return "dispatched"
    return "partially_dispatched"


def _core_job_progress(stage_progress: Any) -> dict[str, float | str | None]:
    raw = str(stage_progress or "").strip()
    if raw.endswith("%"):
        try:
            percentage = float(raw[:-1].strip())
        except ValueError:
            percentage = -1.0
        if 0.0 <= percentage <= 100.0:
            return {"kind": "fraction", "value": percentage / 100.0}
    if "/" in raw:
        numerator_text, denominator_text = raw.split("/", 1)
        try:
            numerator = float(numerator_text.strip())
            denominator = float(denominator_text.strip())
        except ValueError:
            numerator = -1.0
            denominator = 0.0
        if denominator > 0.0 and 0.0 <= numerator <= denominator:
            return {"kind": "fraction", "value": numerator / denominator}
    return {"kind": "indeterminate", "value": None}


def _core_job_elapsed_seconds(started_at: Any) -> int:
    if not isinstance(started_at, datetime):
        return 0
    current = datetime.now(started_at.tzinfo) if started_at.tzinfo is not None else datetime.utcnow()
    return max(0, int((current - started_at).total_seconds()))


def _core_job_live_receipt(job: Any, status: str) -> dict[str, Any]:
    return {
        "schema": "bms.experiment.runtime-receipt.v1",
        "job_id": str(job.id),
        "status": status,
        "canonical_state": status,
        "queue_status": str(job.queue_status) if job.queue_status is not None else None,
        "stage": str(job.current_stage) if job.current_stage is not None else None,
        "stage_progress": str(job.stage_progress) if job.stage_progress is not None else None,
        "progress": _core_job_progress(job.stage_progress),
        "started_at": str(job.started_at) if job.started_at is not None else None,
        "elapsed_seconds": _core_job_elapsed_seconds(job.started_at),
        "assigned_gpu": job.assigned_gpu,
        "provenance": _public_runtime_metadata(job.provenance or {}),
    }


async def reconcile_run_group(
    session: AsyncSession,
    core_session: AsyncSession,
    workspace_id: str,
    run_group_id: str,
) -> ExperimentRunGroup:
    """Project authoritative core live and terminal state into global attempts."""
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
        if attempt.state in {"completed", "failed", "cancelled"}:
            continue
        job = await core_session.get(Job, attempt.scheduler_job_id)
        if job is None:
            continue
        status = str(job.status).lower()
        event_type = ""
        idempotency_key = ""
        if status in TERMINAL_CORE_JOB_STATES:
            projected_state = (
                "completed"
                if status in {"completed", "succeeded"}
                else "cancelled"
                if status in {"cancelled", "canceled"}
                else "failed"
            )
            receipt = {
                "schema": "bms.experiment.terminal-receipt.v1",
                "job_id": attempt.scheduler_job_id,
                "status": status,
                "terminal_state": projected_state,
                "completed_at": str(job.completed_at) if job.completed_at else None,
                "error_message": job.error_message,
                "provenance": _public_runtime_metadata(job.provenance or {}),
            }
            if projected_state == "completed":
                try:
                    from services.global_experiments.receipts import verify_and_link_terminal_outputs

                    receipt.update(
                        await verify_and_link_terminal_outputs(
                            session,
                            core_session,
                            attempt_id=attempt.resource_id,
                        )
                    )
                except Exception as exc:
                    condition_code = str(getattr(exc, "code", "terminal_output_verification_pending"))
                    pending_receipt = {
                        "schema": "bms.experiment.output-reconciliation.v1",
                        "job_id": attempt.scheduler_job_id,
                        "core_status": status,
                        "state": condition_code if condition_code in {"source_unavailable", "source_contract_unavailable", "source_digest_mismatch", "digest_mismatch"} else "pending",
                        "message": str(exc)[:512],
                        "provenance": _public_runtime_metadata(job.provenance or {}),
                    }
                    pending_json = canonical_json(pending_receipt)
                    if attempt.runtime_identity_json == pending_json:
                        continue
                    attempt.runtime_identity_json = pending_json
                    projected_state = "running"
                    event_type = "core_output_reconciliation_pending"
                    idempotency_key = f"core-output-pending:{attempt.scheduler_job_id}:{sha256_text(pending_json)}"
                    receipt = None
            if receipt is not None:
                terminal_receipt_json = canonical_json(receipt)
                attempt.terminal_receipt_json = terminal_receipt_json
                attempt.terminal_receipt_sha256 = sha256_text(terminal_receipt_json)
                attempt.runtime_identity_json = canonical_json(_public_runtime_metadata(job.provenance or {}))
                event_type = "core_terminal_projected"
                idempotency_key = f"core-terminal:{attempt.scheduler_job_id}:{status}"
        elif status in LIVE_CORE_JOB_STATES:
            projected_state = LIVE_CORE_JOB_STATE_MAP[status]
            receipt = _core_job_live_receipt(job, status)
            receipt_json = canonical_json(receipt)
            if (
                attempt.state == projected_state
                and run.state == projected_state
                and attempt.runtime_identity_json == receipt_json
            ):
                continue
            attempt.runtime_identity_json = receipt_json
            event_type = "core_live_projected"
            idempotency_key = (
                f"core-live:{attempt.scheduler_job_id}:{sha256_text(receipt_json)}"
            )
        else:
            continue
        expected_generation = int(run.generation)
        attempt.state = projected_state
        run.state = projected_state
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
                idempotency_key=idempotency_key,
                event_type=event_type,
                payload_json=canonical_json(receipt),
                created_at=now(),
            )
        )
        changed = True
    projected_group_state = _project_run_group_state([run.state for run in runs])
    if group.state != projected_group_state:
        group.state = projected_group_state
        changed = True
    if changed:
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
    replacement_preparation_ids = replacement_preparation_ids or {}
    scope = f"run_group_retry:{workspace_id}:{run_group_id}"
    existing_claim = await session.get(ExperimentIdempotencyClaim, (scope, idempotency_key))
    if existing_claim is not None:
        try:
            stored_response = json.loads(existing_claim.response_json)
        except json.JSONDecodeError as exc:
            raise DispatchFailure("retry idempotency claim response is malformed") from exc
        stored_replacements = stored_response.get("replacement_preparation_ids")
        if stored_replacements is not None and stored_replacements != replacement_preparation_ids:
            raise IdempotencyConflict("retry idempotency key was reused with different replacement preparations")
        existing = await session.get(ExperimentRunGroup, existing_claim.result_resource_id)
        if existing is None:
            raise DispatchFailure("retry idempotency claim points to a missing run group")
        return existing
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
            if attempts[0].state in {"failed", "cancelled"}:
                failed_run_ids.append(run.resource_id)
    if not failed_run_ids:
        raise ValidationFailure("run group has no reconciled failed runs eligible for retry")
    request_sha256 = sha256_text(
        canonical_json(
            {
                "run_group_id": run_group_id,
                "failed_run_ids": failed_run_ids,
                "replacement_preparation_ids": replacement_preparation_ids,
            }
        )
    )
    session.add(
        ExperimentIdempotencyClaim(
            scope=scope,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            result_resource_id=run_group_id,
            response_json=canonical_json({
                "run_group_id": run_group_id,
                "failed_run_ids": failed_run_ids,
                "replacement_preparation_ids": replacement_preparation_ids,
            }),
            created_at=now(),
        )
    )
    for run in runs:
        previous = latest_by_run.get(run.resource_id)
        if previous is None or previous.state not in {"failed", "cancelled"}:
            continue
        preparation_id = replacement_preparation_ids.get(run.resource_id, run.preparation_id)
        preparation = await session.get(ExperimentWorkflowPreparation, preparation_id)
        if preparation is None or preparation.workspace_id != workspace_id or preparation.validation_status != "valid":
            raise ValidationFailure("failed run has no valid replacement preparation in this workspace")
        await _validate_preparation_authority(session, preparation)
        attempt_resource = await _resource(
            session,
            kind="run_attempt",
            workspace_id=workspace_id,
            lifecycle_owner_id=run.resource_id,
        )
        scheduler_payload = json.loads(preparation.scheduler_payload_json)
        attempt = ExperimentRunAttempt(
            resource_id=attempt_resource.id,
            workspace_id=workspace_id,
            workflow_run_id=run.resource_id,
            attempt_number=previous.attempt_number + 1,
            scheduler_job_id=scheduler_job_identity(attempt_resource.id, scheduler_payload),
            state="pending",
            created_at=now(),
        )
        session.add(attempt)
        await session.flush()
        session.add(
            ExperimentLineageEdge(
                id=new_id("retry-lineage"),
                workspace_id=workspace_id,
                source_resource_id=attempt.resource_id,
                target_resource_id=previous.resource_id,
                edge_mode="retried_from",
                edge_key="immediate-prior-attempt",
                metadata_json=canonical_json(
                    {
                        "run_group_id": run_group_id,
                        "previous_attempt_number": previous.attempt_number,
                        "attempt_number": attempt.attempt_number,
                    }
                ),
                created_at=now(),
            )
        )
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


async def resubmit_run_group(
    session: AsyncSession,
    workspace_id: str,
    run_group_id: str,
    *,
    idempotency_key: str,
) -> ExperimentRunGroup:
    """Create a new run group from immutable preparations and link its lineage."""
    source = await session.get(ExperimentRunGroup, run_group_id)
    if source is None or source.workspace_id != workspace_id:
        raise NotFound("run group not found")
    if source.state not in {"completed", "failed", "cancelled"}:
        raise ValidationFailure("only terminal run groups can be resubmitted")
    source_runs = (
        await session.execute(
            select(ExperimentWorkflowRun)
            .where(ExperimentWorkflowRun.run_group_id == run_group_id)
            .order_by(ExperimentWorkflowRun.created_at, ExperimentWorkflowRun.resource_id)
        )
    ).scalars().all()
    if not source_runs:
        raise ValidationFailure("run group has no immutable run intent to resubmit")
    resubmitted = await create_run_group(
        session,
        workspace_id,
        [run.preparation_id for run in source_runs],
        idempotency_key=idempotency_key,
        idempotency_authority={"operation": "resubmit", "source_run_group_id": source.resource_id},
    )
    if resubmitted.resource_id == source.resource_id:
        raise IdempotencyConflict("resubmit idempotency cannot resolve to its source run group")
    existing_edge = (
        await session.execute(
            select(ExperimentLineageEdge).where(
                ExperimentLineageEdge.source_resource_id == resubmitted.resource_id,
                ExperimentLineageEdge.target_resource_id == source.resource_id,
                ExperimentLineageEdge.edge_mode == "resubmitted_from",
            )
        )
    ).scalar_one_or_none()
    if existing_edge is None:
        session.add(
            ExperimentLineageEdge(
                id=new_id("resubmit-lineage"),
                workspace_id=workspace_id,
                source_resource_id=resubmitted.resource_id,
                target_resource_id=source.resource_id,
                edge_mode="resubmitted_from",
                edge_key="source-run-group",
                metadata_json=canonical_json({"source_request_sha256": source.request_sha256}),
                created_at=now(),
            )
        )
        add_audit_event(
            session,
            workspace_id=workspace_id,
            resource_id=resubmitted.resource_id,
            event_type="run_group_resubmitted",
            generation=resubmitted.generation,
            payload={"source_run_group_id": source.resource_id},
        )
        await session.flush()
    return resubmitted


class DispatchMaterializer(Protocol):
    async def materialize(self, attempt_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class ExistingJobMaterializer:
    """Dispatch only through explicitly registered typed materializers.

    The historical generic ``Job(...)`` fallback is intentionally removed: a
    prepared payload is not executable authority unless a server-owned typed
    adapter is registered for it.
    """

    _CM_MATERIALIZERS = {
        "bms.cm.protenix_v2.adapter.v1",
        "bms.cm.confornets.adapter.v1",
    }

    def __init__(self, core_session: AsyncSession):
        self.core_session = core_session

    async def materialize(self, attempt_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        scheduler = payload.get("scheduler")
        if not isinstance(scheduler, dict):
            raise DispatchFailure("dispatch payload has no scheduler object")
        params = scheduler.get("params")
        if not isinstance(params, dict):
            raise DispatchFailure("dispatch scheduler payload has no typed params")
        adapter_id = str(params.get("workflow_adapter") or "")
        if adapter_id in TYPED_CORE_JOB_ADAPTERS:
            from database import Job

            scheduler_job_id = str(payload.get("scheduler_job_id") or "")
            if scheduler_job_id != scheduler_job_id_for_attempt(attempt_id):
                raise DispatchFailure("typed core scheduler job identity disagrees with its run attempt")
            expected_model_id = TYPED_CORE_JOB_ADAPTERS[adapter_id]
            expected_mode = str(scheduler.get("mode") or "run")
            if scheduler.get("model_id") != expected_model_id:
                raise DispatchFailure("typed workflow adapter and scheduler model_id disagree")
            pinned_gpu: int | None = None
            if expected_model_id == "protein_local_redesign":
                resources = scheduler.get("resources")
                raw_pinned_gpu = resources.get("pinned_gpu") if isinstance(resources, dict) else None
                if (
                    isinstance(raw_pinned_gpu, bool)
                    or not isinstance(raw_pinned_gpu, int)
                    or raw_pinned_gpu < 0
                ):
                    raise DispatchFailure("native RFD3 dispatch has no authoritative pinned GPU")
                pinned_gpu = raw_pinned_gpu
            existing_job = await self.core_session.get(Job, scheduler_job_id)
            if existing_job is not None:
                params_match = dict(existing_job.params or {}) == params
                if expected_model_id == "protein_local_redesign":
                    expected_request = params.get("rfd3_request")
                    existing_params = dict(existing_job.params or {})
                    existing_request = existing_params.get("rfd3_request")
                    params_match = (
                        existing_params.get("workflow_adapter") == adapter_id
                        and isinstance(expected_request, dict)
                        and isinstance(existing_request, dict)
                        and local_redesign_requests_semantically_equal(existing_request, expected_request)
                    )
                if (
                    existing_job.model_id != expected_model_id
                    or existing_job.mode != expected_mode
                    or not params_match
                    or (
                        expected_model_id == "protein_local_redesign"
                        and existing_job.pinned_gpu != pinned_gpu
                    )
                ):
                    raise DispatchFailure("preallocated Job identity conflicts with typed dispatch replay")
                job = existing_job
            else:
                from fastapi import BackgroundTasks
                from routers.jobs import _create_job
                from schemas import JobCreate

                request = JobCreate(
                    name=str(scheduler.get("name") or f"Global Experiment {expected_model_id}"),
                    model_id=expected_model_id,
                    mode=expected_mode,
                    params=params,
                    pinned_gpu=pinned_gpu,
                )
                await _create_job(
                    request,
                    BackgroundTasks(),
                    self.core_session,
                    scheduler_job_id,
                    True,
                    None,
                    None,
                    True,
                )
                job = await self.core_session.get(Job, scheduler_job_id)
                if job is None:
                    raise DispatchFailure("canonical typed Job creation did not persist the preallocated Job")
            return {
                "external_job_id": job.id,
                "acknowledgement": {
                    "schema": "bms.global.external-binding-receipt.v1",
                    "adapter_id": adapter_id,
                    "attempt_id": attempt_id,
                    "external_store": "core.jobs",
                    "external_job_id": job.id,
                    "external_model_id": job.model_id,
                    "external_mode": job.mode,
                    "external_state": job.status,
                    **(
                        {"pinned_gpu": job.pinned_gpu}
                        if expected_model_id == "protein_local_redesign"
                        else {}
                    ),
                },
            }
        if adapter_id not in self._CM_MATERIALIZERS:
            raise DispatchFailure("no registered typed materializer accepts this workflow adapter")
        from services.conformational_mapping.global_adapter import materialize_preallocated_cm_job

        return await materialize_preallocated_cm_job(
            self.core_session,
            attempt_id=attempt_id,
            scheduler=scheduler,
            run_group_id=str(payload.get("run_group_id") or ""),
        )


def _outbox_values(**values: Any) -> dict[str, Any]:
    """Use v8 lease columns when present while remaining compatible with v7."""
    columns = ExperimentDispatchOutbox.__table__.columns
    return {key: value for key, value in values.items() if key in columns}


async def dispatch_pending_outbox(
    session: AsyncSession,
    materializer: DispatchMaterializer,
    *,
    lease_owner: str | None = None,
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
                        or_(
                            ExperimentDispatchOutbox.lease_expires_at < now(),
                            and_(
                                ExperimentDispatchOutbox.lease_expires_at.is_(None),
                                ExperimentDispatchOutbox.updated_at < lease_cutoff,
                            ),
                        ),
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
                    or_(
                        ExperimentDispatchOutbox.lease_expires_at < now(),
                        and_(
                            ExperimentDispatchOutbox.lease_expires_at.is_(None),
                            ExperimentDispatchOutbox.updated_at < lease_cutoff,
                        ),
                    ),
                ),
            ),
        )
        .values(
            **_outbox_values(
                status="dispatching",
                dispatch_attempts=ExperimentDispatchOutbox.dispatch_attempts + 1,
                lease_token=lease_token,
                lease_owner=lease_owner,
                lease_acquired_at=now(),
                lease_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                updated_at=now(),
            )
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
        if sha256_text(row.payload_json) != row.payload_sha256:
            raise DispatchFailure("dispatch payload digest does not match the durable outbox authority")
        receipt = await materializer.materialize(row.run_attempt_id, payload)
    except Exception as exc:
        failed_update = await session.execute(
            update(ExperimentDispatchOutbox)
            .where(
                ExperimentDispatchOutbox.id == row.id,
                ExperimentDispatchOutbox.status == "dispatching",
                ExperimentDispatchOutbox.lease_token == lease_token,
            )
            .values(
                **_outbox_values(
                    status="failed",
                    lease_token=None,
                    lease_owner=None,
                    lease_acquired_at=None,
                    lease_expires_at=None,
                    last_error=str(exc)[:2048],
                    updated_at=now(),
                )
            )
            .execution_options(synchronize_session=False)
        )
        if failed_update.rowcount == 1:
            attempt = await session.get(ExperimentRunAttempt, row.run_attempt_id)
            if attempt is not None and attempt.state not in {"completed", "cancelled"}:
                attempt.state = "failed"
                run = await session.get(ExperimentWorkflowRun, attempt.workflow_run_id)
                if run is not None and run.state not in {"completed", "cancelled"}:
                    run.state = "failed"
                    run.generation = int(run.generation) + 1
                    group = await session.get(ExperimentRunGroup, run.run_group_id)
                    if group is not None:
                        group.state = "failed"
                        group.generation = int(group.generation) + 1
                        group.updated_at = now()
            await session.commit()
        else:
            await session.rollback()
        raise
    acknowledgement_json = canonical_json(_public_runtime_metadata(receipt))
    acknowledged_update = await session.execute(
        update(ExperimentDispatchOutbox)
        .where(
            ExperimentDispatchOutbox.id == row.id,
            ExperimentDispatchOutbox.status == "dispatching",
            ExperimentDispatchOutbox.lease_token == lease_token,
        )
        .values(
            **_outbox_values(
                status="acknowledged",
                lease_token=None,
                lease_owner=None,
                lease_acquired_at=None,
                lease_expires_at=None,
                acknowledgement_json=acknowledgement_json,
                last_error=None,
                acknowledged_at=now(),
                updated_at=now(),
            )
        )
        .execution_options(synchronize_session=False)
    )
    if acknowledged_update.rowcount != 1:
        await session.rollback()
        return 0
    attempt = await session.get(ExperimentRunAttempt, row.run_attempt_id)
    if attempt is None:
        raise DispatchFailure("outbox references a missing attempt")
    attempt.state = "dispatched"
    attempt.external_binding_receipt_json = acknowledgement_json
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
            payload_json=acknowledgement_json,
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
    "append_research_record",
    "canonical_json",
    "create_dataset",
    "create_domain_experiment",
    "create_experiment",
    "create_global_experiment",
    "create_experiment_workspace",
    "create_project",
    "create_run_group",
    "create_workflow",
    "dispatch_pending_outbox",
    "now",
    "prepare_workflow",
    "restore_aggregate",
    "save_hierarchy_revision",
    "save_dataset_revision",
    "save_workflow_draft",
    "save_workflow_revision",
    "sha256_text",
]
