"""Project-owned workflow setup contexts preceding immutable launch authority."""

from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import quote

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError as JsonSchemaValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import (
    ExperimentAggregateHead,
    ExperimentIdempotencyClaim,
    ExperimentResource,
    ExperimentRevision,
    ExperimentValidation,
    ExperimentWorkflowPreparation,
    ExperimentWorkflowSetupContext,
)
from experiment_services import (
    IdempotencyConflict,
    NotFound,
    RevisionConflict,
    ValidationFailure,
    canonical_json,
    create_domain_experiment,
    create_global_experiment,
    create_workflow,
    new_id,
    sha256_text,
)
from services.global_experiments.launch_contexts import create_prepared_launch_context
from services.protein_project_capabilities import (
    ProteinProjectCapabilityError,
    protein_capability_record,
    protein_parameter_schema,
)

RelationshipKind = Literal["primary", "follow_up"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request_digest(payload: dict[str, Any]) -> str:
    return sha256_text(canonical_json(payload))


def _return_uri(row: ExperimentWorkflowSetupContext) -> str:
    selected = quote(f"domain_experiment:{row.domain_experiment_id}", safe="")
    return f"/projects/{row.project_id}?focus={row.global_experiment_id}&selected={selected}"


def _document(row: ExperimentWorkflowSetupContext, *, detailed: bool = False) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": "bms.project-workflow-setup.v1",
        "setup_context_id": row.setup_context_id,
        "project_id": row.project_id,
        "global_experiment_id": row.global_experiment_id,
        "domain_experiment_id": row.domain_experiment_id,
        "workflow_id": row.workflow_id,
        "relationship_kind": row.relationship_kind,
        "capability_id": row.capability_id,
        "state": row.lifecycle_state,
        "validation_state": row.validation_state,
        "generation": row.generation,
        "setup_destination": row.setup_destination,
        "return_uri": _return_uri(row),
    }
    if detailed:
        document.update(
            draft=json.loads(row.draft_json),
            draft_sha256=row.draft_sha256,
            capability_contract_sha256=row.capability_contract_sha256,
            diagnostics={
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "submitted_at": row.submitted_at,
                "deleted_at": row.deleted_at,
            },
        )
    return document


async def _project(session: AsyncSession, project_id: str) -> ExperimentAggregateHead:
    project = await session.get(ExperimentAggregateHead, project_id)
    if project is None or project.aggregate_kind != "workspace":
        raise NotFound("Project not found")
    return project


async def _owned_setup(
    session: AsyncSession, project_id: str, setup_context_id: str
) -> ExperimentWorkflowSetupContext:
    row = await session.get(ExperimentWorkflowSetupContext, setup_context_id)
    if row is None:
        raise NotFound("Workflow setup context not found")
    if row.project_id != project_id:
        raise ValidationFailure("Workflow setup context is not owned by this Project")
    return row


async def _replay(
    session: AsyncSession,
    *,
    scope: str,
    key: str,
    request_sha256: str,
) -> dict[str, Any] | None:
    claim = await session.get(ExperimentIdempotencyClaim, (scope, key))
    if claim is None:
        return None
    if claim.request_sha256 != request_sha256:
        raise IdempotencyConflict("idempotency key was already used with a different request")
    return json.loads(claim.response_json)


async def _claim(
    session: AsyncSession,
    *,
    scope: str,
    key: str,
    request_sha256: str,
    result_resource_id: str,
    response: dict[str, Any],
) -> None:
    session.add(
        ExperimentIdempotencyClaim(
            scope=scope,
            idempotency_key=key,
            request_sha256=request_sha256,
            result_resource_id=result_resource_id,
            response_json=canonical_json(response),
            created_at=_now(),
        )
    )
    await session.flush()


def _capability_contract(capability_id: str) -> tuple[dict[str, Any], str, str]:
    try:
        capability = protein_capability_record(capability_id)
        parameter_schema = protein_parameter_schema(capability_id)
    except ProteinProjectCapabilityError as exc:
        raise ValidationFailure(str(exc)) from exc
    if capability.get("project_setup_state") != "ready":
        raise ValidationFailure("Protein capability is not ready for Project workflow setup")
    adapter_id = capability.get("project_setup_adapter_id")
    destination = capability.get("safe_setup_destination")
    if not isinstance(adapter_id, str) or not adapter_id or not isinstance(destination, str):
        raise ValidationFailure("Project workflow setup adapter authority is incomplete")
    contract = {
        "schema": "bms.project-workflow-setup-capability.v1",
        "capability": capability,
        "parameter_schema": parameter_schema,
    }
    return contract, adapter_id, destination


def _global_payload(name: str, objective: str) -> dict[str, Any]:
    return {
        "schema": "bms.global-experiment.v1",
        "name": name,
        "objective": objective,
        "scientific_question": objective,
        "description": objective,
        "status": "active",
        "priority": "normal",
        "success_criteria": ["Complete the configured primary Protein workflow"],
    }


def _domain_payload(
    name: str, objective: str, capability_id: str, experiment_mode: str
) -> dict[str, Any]:
    return {
        "schema": "bms.domain-experiment.v1",
        "domain_kind": "protein_in_silico",
        "domain_contract_version": "1",
        "name": name,
        "objective": objective,
        "status": "active",
        "source_receipt_ids": [],
        "dataset_ids": [],
        "domain_payload": {
            "schema": "bms.protein-in-silico-experiment.v1",
            "experiment_mode": experiment_mode,
            "targets": [],
            "scientific_objective": objective,
            "design_constraints": [],
            "planned_capabilities": [capability_id],
            "comparison_groups": [],
            "validation_strategy": [],
        },
    }


def _materialize_draft(schema: dict[str, Any], supplied: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if not isinstance(supplied, dict):
        raise ValidationFailure("workflow setup draft must be an object")
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise ValidationFailure("workflow setup parameter schema is unavailable")
    unknown = sorted(set(supplied) - set(properties))
    if unknown:
        raise ValidationFailure(f"workflow setup draft contains unknown fields: {', '.join(unknown)}")
    materialized: dict[str, Any] = {}
    for name, field_schema in properties.items():
        if not isinstance(field_schema, dict):
            raise ValidationFailure("workflow setup parameter schema is malformed")
        if "const" in field_schema:
            materialized[name] = copy.deepcopy(field_schema["const"])
        elif name in supplied:
            materialized[name] = copy.deepcopy(supplied[name])
        elif "default" in field_schema:
            materialized[name] = copy.deepcopy(field_schema["default"])
    for name, value in supplied.items():
        field_schema = properties[name]
        if "const" in field_schema and value != field_schema["const"]:
            raise ValidationFailure(f"workflow setup field {name} is server-owned")
        materialized[name] = copy.deepcopy(value)
    required = schema.get("required") or []
    missing = [name for name in required if name not in materialized]
    if missing:
        return materialized, "incomplete"
    try:
        Draft202012Validator(schema).validate(materialized)
    except (JsonSchemaValidationError, SchemaError) as exc:
        raise ValidationFailure(f"workflow setup draft is invalid: {exc.message}") from exc
    return materialized, "ready"


async def create_workflow_setup(
    session: AsyncSession,
    *,
    project_id: str,
    relationship_kind: RelationshipKind,
    global_experiment_id: str | None,
    experiment_name: str | None,
    experiment_objective: str | None,
    domain_kind: str,
    capability_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    request = {
        "schema": "bms.project-workflow-setup.create.v1",
        "project_id": project_id,
        "relationship_kind": relationship_kind,
        "global_experiment_id": global_experiment_id,
        "experiment_name": experiment_name,
        "experiment_objective": experiment_objective,
        "domain_kind": domain_kind,
        "capability_id": capability_id,
    }
    request_sha256 = _request_digest(request)
    scope = f"workflow-setup:create:{project_id}"
    replay = await _replay(session, scope=scope, key=idempotency_key, request_sha256=request_sha256)
    if replay is not None:
        return replay
    await _project(session, project_id)
    if relationship_kind not in {"primary", "follow_up"}:
        raise ValidationFailure("relationship_kind must be primary or follow_up")
    if domain_kind != "protein_in_silico":
        raise ValidationFailure("Project workflow setup supports only protein_in_silico")
    contract, _adapter_id, destination = _capability_contract(capability_id)
    if relationship_kind == "primary":
        if global_experiment_id is not None:
            raise ValidationFailure("primary workflow setup cannot select an existing Global Experiment")
        if not str(experiment_name or "").strip() or not str(experiment_objective or "").strip():
            raise ValidationFailure("primary workflow setup requires experiment name and objective")
        global_head = await create_global_experiment(
            session, project_id, _global_payload(str(experiment_name).strip(), str(experiment_objective).strip())
        )
    else:
        if experiment_name is not None or experiment_objective is not None:
            raise ValidationFailure("follow-up workflow setup reuses its Global Experiment metadata")
        global_head = await session.get(ExperimentAggregateHead, global_experiment_id or "")
        if (
            global_head is None
            or global_head.aggregate_kind != "experiment"
            or global_head.workspace_id != project_id
            or global_head.parent_id != project_id
        ):
            raise ValidationFailure("Global Experiment is not owned by this Project")
    objective = str(experiment_objective or global_head.description or global_head.display_name)
    capability = contract["capability"]
    family = (capability.get("product_taxonomy") or {}).get("family")
    experiment_mode = (
        "prediction"
        if family == "structure_prediction"
        else "redesign"
        if capability_id == "protein.de_novo.local_redesign"
        else "exploration"
    )
    domain = await create_domain_experiment(
        session,
        project_id,
        global_head.aggregate_id,
        _domain_payload(
            f"{global_head.display_name} — {capability_id}",
            objective,
            capability_id,
            experiment_mode,
        ),
    )
    workflow = await create_workflow(
        session,
        project_id,
        f"{global_head.display_name} — {protein_capability_record(capability_id)['label']}",
        capability_id,
        experiment_id=domain.aggregate_id,
    )
    setup_context_id = f"workflow-setup:{uuid.uuid4()}"
    timestamp = _now()
    contract_json = canonical_json(contract)
    session.add(
        ExperimentResource(
            id=setup_context_id,
            kind="workflow_setup_context",
            workspace_id=project_id,
            lifecycle_owner_id=workflow.aggregate_id,
            created_at=timestamp,
        )
    )
    await session.flush()
    row = ExperimentWorkflowSetupContext(
        setup_context_id=setup_context_id,
        project_id=project_id,
        global_experiment_id=global_head.aggregate_id,
        domain_experiment_id=domain.aggregate_id,
        workflow_id=workflow.aggregate_id,
        relationship_kind=relationship_kind,
        capability_id=capability_id,
        capability_contract_json=contract_json,
        capability_contract_sha256=sha256_text(contract_json),
        setup_destination=destination,
        draft_json="{}",
        draft_sha256=sha256_text("{}"),
        generation=0,
        validation_state="incomplete",
        lifecycle_state="open",
        created_at=timestamp,
        updated_at=timestamp,
    )
    session.add(row)
    await session.flush()
    response = _document(row)
    await _claim(
        session,
        scope=scope,
        key=idempotency_key,
        request_sha256=request_sha256,
        result_resource_id=setup_context_id,
        response=response,
    )
    return response


async def get_workflow_setup(
    session: AsyncSession,
    *,
    project_id: str,
    setup_context_id: str,
) -> dict[str, Any]:
    await _project(session, project_id)
    return _document(await _owned_setup(session, project_id, setup_context_id), detailed=True)


async def save_workflow_setup_draft(
    session: AsyncSession,
    *,
    project_id: str,
    setup_context_id: str,
    draft: dict[str, Any],
    expected_generation: int,
    idempotency_key: str,
) -> dict[str, Any]:
    request = {"draft": draft, "expected_generation": expected_generation}
    request_sha256 = _request_digest(request)
    scope = f"workflow-setup:save:{project_id}:{setup_context_id}"
    replay = await _replay(session, scope=scope, key=idempotency_key, request_sha256=request_sha256)
    if replay is not None:
        return replay
    row = await _owned_setup(session, project_id, setup_context_id)
    if row.lifecycle_state != "open":
        raise ValidationFailure("only an open workflow setup can be edited")
    if row.generation != expected_generation:
        raise RevisionConflict(
            f"workflow setup generation conflict: expected {expected_generation}, current {row.generation}"
        )
    contract = json.loads(row.capability_contract_json)
    if sha256_text(row.capability_contract_json) != row.capability_contract_sha256:
        raise ValidationFailure("workflow setup capability contract digest mismatch")
    materialized, validation_state = _materialize_draft(contract["parameter_schema"], draft)
    row.draft_json = canonical_json(materialized)
    row.draft_sha256 = sha256_text(row.draft_json)
    row.generation += 1
    row.validation_state = validation_state
    row.updated_at = _now()
    await session.flush()
    response = _document(row, detailed=True)
    await _claim(
        session, scope=scope, key=idempotency_key, request_sha256=request_sha256,
        result_resource_id=row.setup_context_id, response=response,
    )
    return response


async def prepare_workflow_setup_launch(
    session: AsyncSession,
    *,
    project_id: str,
    setup_context_id: str,
    expected_generation: int,
    idempotency_key: str,
) -> dict[str, Any]:
    request = {"expected_generation": expected_generation}
    request_sha256 = _request_digest(request)
    scope = f"workflow-setup:prepare:{project_id}:{setup_context_id}"
    replay = await _replay(session, scope=scope, key=idempotency_key, request_sha256=request_sha256)
    if replay is not None:
        return replay
    row = await _owned_setup(session, project_id, setup_context_id)
    if row.lifecycle_state != "open" or row.validation_state != "ready":
        raise ValidationFailure("workflow setup must be open and ready before preparation")
    if row.generation != expected_generation:
        raise RevisionConflict(
            f"workflow setup generation conflict: expected {expected_generation}, current {row.generation}"
        )
    workflow = await session.get(ExperimentAggregateHead, row.workflow_id)
    if workflow is None or workflow.workspace_id != project_id or workflow.parent_id != row.domain_experiment_id:
        raise ValidationFailure("workflow setup ownership authority is invalid")
    timestamp = _now()
    revision_id = new_id("revision")
    payload = {
        "schema": "bms.workflow.project-setup.v1",
        "capability_id": row.capability_id,
        "adapter_id": json.loads(row.capability_contract_json)["capability"]["project_setup_adapter_id"],
        "native_request": json.loads(row.draft_json),
        "setup_context_id": row.setup_context_id,
    }
    payload_json = canonical_json(payload)
    session.add(
        ExperimentResource(
            id=revision_id, kind="revision", workspace_id=project_id,
            lifecycle_owner_id=row.workflow_id, created_at=timestamp,
        )
    )
    await session.flush()
    session.add(
        ExperimentRevision(
            resource_id=revision_id,
            subject_id=row.workflow_id,
            revision_number=workflow.head_generation + 1,
            parent_revision_id=workflow.current_revision_id,
            schema_name=payload["schema"],
            schema_version="1",
            canonical_payload=payload_json,
            payload_sha256=sha256_text(payload_json),
            dependency_graph_sha256=sha256_text("[]"),
            provenance_json=canonical_json({"setup_context_id": row.setup_context_id}),
            created_at=timestamp,
        )
    )
    workflow.current_revision_id = revision_id
    workflow.head_generation += 1
    workflow.lifecycle_state = "active"
    workflow.updated_at = timestamp
    await session.flush()
    preparation_id = new_id("preparation")
    validation_id = new_id("validation")
    for resource_id, kind, owner in (
        (preparation_id, "workflow_preparation", row.workflow_id),
        (validation_id, "validation", preparation_id),
    ):
        session.add(
            ExperimentResource(
                id=resource_id, kind=kind, workspace_id=project_id,
                lifecycle_owner_id=owner, created_at=timestamp,
            )
        )
    await session.flush()
    validation_receipt = {
        "schema": "bms.project-workflow-setup-validation.v1",
        "setup_context_id": row.setup_context_id,
        "capability_contract_sha256": row.capability_contract_sha256,
        "draft_sha256": row.draft_sha256,
        "outcome": "valid",
    }
    validation_json = canonical_json(validation_receipt)
    validation_sha256 = sha256_text(validation_json)
    session.add_all(
        [
            ExperimentValidation(
                resource_id=validation_id,
                subject_resource_id=preparation_id,
                validator_name="project_workflow_setup_adapter",
                validator_version="1",
                outcome="valid",
                input_graph_sha256=row.draft_sha256,
                receipt_json=validation_json,
                receipt_sha256=validation_sha256,
                created_at=timestamp,
            ),
            ExperimentWorkflowPreparation(
                resource_id=preparation_id,
                workspace_id=project_id,
                workflow_revision_id=revision_id,
                normalized_request_json=row.draft_json,
                normalized_request_sha256=row.draft_sha256,
                scheduler_payload_json=payload_json,
                validation_status="valid",
                validation_receipt_json=validation_json,
                validation_resource_id=validation_id,
                expected_cardinality=1,
                created_at=timestamp,
                prepared_at=timestamp,
            ),
        ]
    )
    await session.flush()
    context = await create_prepared_launch_context(
        session,
        project_id=project_id,
        global_experiment_id=row.global_experiment_id,
        domain_experiment_id=row.domain_experiment_id,
        preparation_id=preparation_id,
        return_uri=_return_uri(row),
    )
    row.lifecycle_state = "submitted"
    row.submitted_at = timestamp
    row.updated_at = timestamp
    await session.flush()
    response = {
        **_document(row, detailed=True),
        "preparation_id": preparation_id,
        "launch_context_id": context.launch_context_id,
    }
    await _claim(
        session, scope=scope, key=idempotency_key, request_sha256=request_sha256,
        result_resource_id=row.setup_context_id, response=response,
    )
    return response


async def delete_workflow_setup(
    session: AsyncSession,
    *,
    project_id: str,
    setup_context_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    request_sha256 = _request_digest({"setup_context_id": setup_context_id})
    scope = f"workflow-setup:delete:{project_id}:{setup_context_id}"
    replay = await _replay(session, scope=scope, key=idempotency_key, request_sha256=request_sha256)
    if replay is not None:
        return replay
    row = await _owned_setup(session, project_id, setup_context_id)
    if row.lifecycle_state == "submitted":
        raise ValidationFailure("submitted workflow setup cannot be deleted")
    if row.lifecycle_state != "open":
        raise ValidationFailure("workflow setup is not open")
    timestamp = _now()
    row.lifecycle_state = "deleted"
    row.deleted_at = timestamp
    row.updated_at = timestamp
    resource = await session.get(ExperimentResource, row.setup_context_id)
    if resource is not None:
        resource.archived_at = timestamp
    await session.flush()
    response = _document(row, detailed=True)
    await _claim(
        session, scope=scope, key=idempotency_key, request_sha256=request_sha256,
        result_resource_id=row.setup_context_id, response=response,
    )
    return response


__all__ = [
    "create_workflow_setup",
    "delete_workflow_setup",
    "get_workflow_setup",
    "prepare_workflow_setup_launch",
    "save_workflow_setup_draft",
]
