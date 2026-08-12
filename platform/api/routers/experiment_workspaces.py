"""Global workspace/workflow/dataset/run-group HTTP contract."""
from __future__ import annotations

import json
import os
import secrets
from typing import Any, Mapping

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_database import get_experiment_session
from experiment_models import (
    ExperimentAggregateHead,
    ExperimentAuditEvent,
    ExperimentResource,
    ExperimentRevision,
    ExperimentRunGroup,
    ExperimentRunAttempt,
    ExperimentValidation,
    ExperimentWorkflowPreparation,
    ExperimentWorkflowRun,
    ExperimentSyncState,
)
from experiment_operations import (
    BackupNotFound,
    ExportNotFound,
    ExperimentOperationError,
    build_workspace_export,
    create_online_backup,
    register_external_entity_receipt,
    verify_backup,
    verify_workspace_export,
    workspace_analytics,
)
from experiment_services import (
    ExperimentServiceError,
    IdempotencyConflict,
    NotFound,
    RevisionConflict,
    ValidationFailure,
    add_audit_event,
    create_dataset,
    create_global_experiment,
    create_project,
    create_run_group,
    create_workflow,
    resubmit_run_group,
    retry_failed_run_group,
    archive_aggregate,
    clone_workflow,
    prepare_workflow,
    save_dataset_revision,
    save_workflow_draft,
    save_workflow_revision,
)
from services.global_experiments.worker import global_experiment_worker


router = APIRouter(prefix="/api/experiment-workspaces", tags=["experiment-workspaces"])
_APPLICATION_PROXY_HEADER = "X-BMS-CM-Proxy-Secret"


def _authenticated_principal(request: Request) -> tuple[str, frozenset[str]]:
    """Resolve the authenticated actor and roles at the existing application boundary."""
    principal = getattr(request.state, "authenticated_principal", None)
    if principal is None:
        configured = os.getenv("BMS_CM_TRUSTED_PROXY_SECRET", "")
        supplied = request.headers.get(_APPLICATION_PROXY_HEADER, "")
        if configured and supplied and secrets.compare_digest(configured, supplied):
            return "local-application-operator", frozenset({"operator"})
        raise HTTPException(status_code=401, detail="authenticated global CM principal required")
    if isinstance(principal, Mapping):
        actor = principal.get("id") or principal.get("subject")
        roles = principal.get("roles") or []
    else:
        actor = getattr(principal, "id", None) or getattr(principal, "subject", None)
        roles = getattr(principal, "roles", [])
    if isinstance(roles, str):
        roles = [roles]
    normalized_roles = frozenset(str(role).strip().lower() for role in roles)
    if not actor:
        raise HTTPException(status_code=403, detail="authenticated global CM actor required")
    return str(actor), normalized_roles


def _mutation_principal(request: Request) -> str:
    """Require an authenticated global-CM scientist, operator, or admin."""

    actor, normalized_roles = _authenticated_principal(request)
    if not normalized_roles.intersection({"scientist", "operator", "admin"}):
        raise HTTPException(status_code=403, detail="global CM scientist/operator role required")
    return actor


def _operator_principal(request: Request) -> str:
    """Require operator authority for a mutation that cannot bind one workspace owner."""

    actor, normalized_roles = _authenticated_principal(request)
    if not normalized_roles.intersection({"operator", "admin"}):
        raise HTTPException(status_code=403, detail="global CM operator/admin role required")
    return actor


async def _require_mutation_owner(
    request: Request,
    session: AsyncSession,
    *,
    resource_id: str,
) -> str:
    """Require the persisted owner binding for the target resource's workspace."""

    principal_id = _mutation_principal(request)
    resource = await session.get(ExperimentResource, resource_id)
    if resource is None:
        raise HTTPException(status_code=404, detail="global CM mutation target not found")
    workspace_id = resource.id if resource.kind == "workspace" else resource.workspace_id
    if not workspace_id:
        raise HTTPException(status_code=404, detail="global CM mutation target not found")
    owner_events = (
        await session.execute(
            select(ExperimentAuditEvent).where(
                ExperimentAuditEvent.workspace_id == workspace_id,
                ExperimentAuditEvent.resource_id == workspace_id,
                ExperimentAuditEvent.event_type == "workspace_owner_bound",
            )
        )
    ).scalars().all()
    if len(owner_events) != 1:
        raise HTTPException(status_code=404, detail="global CM mutation target not found")
    try:
        owner_payload = json.loads(owner_events[0].payload_json)
    except (TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=404, detail="global CM mutation target not found")
    if not isinstance(owner_payload, dict) or owner_payload.get("principal_id") != principal_id:
        raise HTTPException(status_code=404, detail="global CM mutation target not found")
    return principal_id


class StrictRequestModel(BaseModel):
    model_config = {"extra": "forbid"}


class WorkspaceCreateRequest(StrictRequestModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""


class ExperimentCreateRequest(StrictRequestModel):
    name: str = Field(min_length=1, max_length=255)
    question: str = ""


class WorkflowCreateRequest(StrictRequestModel):
    name: str = Field(min_length=1, max_length=255)
    workflow_family: str = Field(min_length=1, max_length=128)
    domain_experiment_id: str = Field(
        min_length=1,
        max_length=128,
        validation_alias=AliasChoices("domain_experiment_id", "experiment_id"),
        serialization_alias="domain_experiment_id",
    )


class DatasetCreateRequest(StrictRequestModel):
    name: str = Field(min_length=1, max_length=255)
    dataset_kind: str = Field(min_length=1, max_length=128)
    domain_experiment_id: str = Field(
        min_length=1,
        max_length=128,
        validation_alias=AliasChoices("domain_experiment_id", "experiment_id"),
        serialization_alias="domain_experiment_id",
    )


class DraftSaveRequest(StrictRequestModel):
    payload: dict[str, Any]
    expected_generation: int = Field(ge=0)


class RevisionSaveRequest(StrictRequestModel):
    expected_head_generation: int = Field(ge=0)


class CloneRequest(StrictRequestModel):
    source_revision_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=255)


class DatasetRevisionSaveRequest(StrictRequestModel):
    payload: dict[str, Any]
    expected_head_generation: int = Field(ge=0)


class PrepareRequest(StrictRequestModel):
    input_dataset_revision_ids: list[str] = Field(default_factory=list)


class ArchiveRequest(StrictRequestModel):
    expected_head_generation: int | None = Field(default=None, ge=0)


class RunGroupCreateRequest(StrictRequestModel):
    preparation_ids: list[str] = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=255)


class RetryRunGroupRequest(StrictRequestModel):
    idempotency_key: str = Field(min_length=1, max_length=255)
    replacement_preparation_ids: dict[str, str] = Field(default_factory=dict)


class ResubmitRunGroupRequest(StrictRequestModel):
    idempotency_key: str = Field(min_length=1, max_length=255)


class StatsHandoffRequest(StrictRequestModel):
    stats_run_id: str = Field(min_length=1, max_length=255)
    toolkit_version: str = Field(min_length=1, max_length=128)
    source_resource_ids: list[str] = Field(min_length=1)
    source_content_digests: list[str] = Field(min_length=1)
    result_content_digest: str = Field(min_length=64, max_length=64)
    result_generation_or_revision: str = Field(min_length=1, max_length=255)
    acknowledgement: dict[str, Any] = Field(default_factory=dict)


class ExternalReceiptCreateRequest(StrictRequestModel):
    store_id: str = Field(min_length=1, max_length=128)
    entity_kind: str = Field(min_length=1, max_length=128)
    entity_id: str = Field(min_length=1, max_length=255)
    generation_or_revision: str = Field(min_length=1, max_length=255)
    content_digest: str = Field(min_length=64, max_length=64)
    availability: str = Field(default="available", min_length=1, max_length=32)
    acknowledgement: dict[str, Any] = Field(default_factory=dict)


def _error(exc: ExperimentServiceError) -> HTTPException:
    if isinstance(exc, (NotFound, BackupNotFound, ExportNotFound)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (RevisionConflict, IdempotencyConflict)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValidationFailure):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _public_receipt(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _public_receipt(child)
            for key, child in value.items()
            if not any(
                token in str(key).lower()
                for token in ("path", "directory", "output_dir", "command", "executable")
            )
        }
    if isinstance(value, list):
        return [_public_receipt(child) for child in value]
    return value


def _head_json(head: ExperimentAggregateHead) -> dict[str, Any]:
    return {
        "id": head.aggregate_id,
        "kind": head.aggregate_kind,
        "workspace_id": head.workspace_id,
        "parent_id": head.parent_id,
        "current_revision_id": head.current_revision_id,
        "head_generation": head.head_generation,
        "lifecycle_state": head.lifecycle_state,
        "name": head.display_name,
        "description": head.description,
        "created_at": head.created_at,
        "updated_at": head.updated_at,
        "deprecation": {
            "deprecated": True,
            "replacement": "/api/projects",
            "storage_authority": "shared experiment services",
        },
    }


def _revision_json(revision: ExperimentRevision) -> dict[str, Any]:
    return {
        "id": revision.resource_id,
        "subject_id": revision.subject_id,
        "revision_number": revision.revision_number,
        "parent_revision_id": revision.parent_revision_id,
        "schema_name": revision.schema_name,
        "schema_version": revision.schema_version,
        "payload": json.loads(revision.canonical_payload),
        "payload_sha256": revision.payload_sha256,
        "dependency_graph_sha256": revision.dependency_graph_sha256,
        "provenance": json.loads(revision.provenance_json),
        "created_at": revision.created_at,
    }


async def _list_heads(
    session: AsyncSession,
    workspace_id: str,
    kind: str,
) -> list[dict[str, Any]]:
    workspace = await session.get(ExperimentResource, workspace_id)
    if workspace is None or workspace.kind != "workspace":
        raise HTTPException(status_code=404, detail=f"workspace not found: {workspace_id}")
    heads = (
        await session.execute(
            select(ExperimentAggregateHead)
            .where(
                ExperimentAggregateHead.workspace_id == workspace_id,
                ExperimentAggregateHead.aggregate_kind == kind,
            )
            .order_by(ExperimentAggregateHead.created_at)
        )
    ).scalars().all()
    return [_head_json(head) for head in heads]


@router.get("")
async def list_workspaces(
    session: AsyncSession = Depends(get_experiment_session),
) -> list[dict[str, Any]]:
    heads = (
        await session.execute(
            select(ExperimentAggregateHead)
            .where(ExperimentAggregateHead.aggregate_kind == "workspace")
            .order_by(ExperimentAggregateHead.created_at)
        )
    ).scalars().all()
    return [_head_json(head) for head in heads]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        principal_id = _mutation_principal(request)
        head = await create_project(
            session,
            {
                "schema": "bms.project.v1",
                "name": payload.name,
                "description": payload.description,
                "research_objective": "",
                "owner": None,
                "contributors": [],
                "tags": [],
                "status": "draft",
                "start_date": None,
                "target_end_date": None,
                "external_references": [],
                "created_by": None,
                "change_summary": "created through deprecated workspace route",
            },
        )
        add_audit_event(
            session,
            workspace_id=head.aggregate_id,
            resource_id=head.aggregate_id,
            event_type="workspace_owner_bound",
            generation=0,
            payload={"principal_id": principal_id},
        )
        await session.commit()
        return _head_json(head)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc
    except Exception:
        await session.rollback()
        raise


@router.get("/{workspace_id}")
async def get_workspace(
    workspace_id: str,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        head = await session.get(ExperimentAggregateHead, workspace_id)
        resource = await session.get(ExperimentResource, workspace_id)
        if head is None or resource is None or resource.kind != "workspace":
            raise NotFound(f"workspace not found: {workspace_id}")
        return _head_json(head)
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.post("/{workspace_id}/aggregates/{aggregate_id}/archive")
async def archive_workspace_aggregate(
    workspace_id: str,
    aggregate_id: str,
    payload: ArchiveRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=aggregate_id)
        head = await session.get(ExperimentAggregateHead, aggregate_id)
        if head is None or head.workspace_id != workspace_id:
            raise NotFound(f"aggregate not found: {aggregate_id}")
        archived = await archive_aggregate(
            session,
            aggregate_id,
            expected_head_generation=payload.expected_head_generation,
        )
        await session.commit()
        return _head_json(archived)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.get("/{workspace_id}/experiments")
async def list_workspace_experiments(
    workspace_id: str,
    session: AsyncSession = Depends(get_experiment_session),
) -> list[dict[str, Any]]:
    return await _list_heads(session, workspace_id, "experiment")


@router.get("/{workspace_id}/experiments/{experiment_id}")
async def get_workspace_experiment(
    workspace_id: str,
    experiment_id: str,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    head = await session.get(ExperimentAggregateHead, experiment_id)
    if head is None or head.workspace_id != workspace_id or head.aggregate_kind != "experiment":
        raise HTTPException(status_code=404, detail=f"experiment not found: {experiment_id}")
    return _head_json(head)


@router.get("/{workspace_id}/workflows")
async def list_workspace_workflows(
    workspace_id: str,
    session: AsyncSession = Depends(get_experiment_session),
) -> list[dict[str, Any]]:
    return await _list_heads(session, workspace_id, "workflow")


@router.get("/{workspace_id}/datasets")
async def list_workspace_datasets(
    workspace_id: str,
    session: AsyncSession = Depends(get_experiment_session),
) -> list[dict[str, Any]]:
    return await _list_heads(session, workspace_id, "dataset")


@router.post("/{workspace_id}/experiments", status_code=status.HTTP_201_CREATED)
async def create_workspace_experiment(
    workspace_id: str,
    payload: ExperimentCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=workspace_id)
        head = await create_global_experiment(
            session,
            workspace_id,
            {
                "schema": "bms.global-experiment.v1",
                "name": payload.name,
                "objective": "",
                "scientific_question": payload.question,
                "hypothesis": None,
                "description": payload.question,
                "status": "draft",
                "priority": "normal",
                "tags": [],
                "shared_source_receipt_ids": [],
                "shared_dataset_ids": [],
                "comparison_plan": None,
                "success_criteria": [],
                "review_summary": None,
                "conclusion": None,
                "created_by": None,
                "change_summary": "created through deprecated experiment route",
            },
        )
        await session.commit()
        return _head_json(head)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.post("/{workspace_id}/workflows", status_code=status.HTTP_201_CREATED)
async def create_workspace_workflow(
    workspace_id: str,
    payload: WorkflowCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=workspace_id)
        head = await create_workflow(
            session,
            workspace_id,
            payload.name,
            payload.workflow_family,
            experiment_id=payload.domain_experiment_id,
        )
        await session.commit()
        return _head_json(head)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.patch("/{workspace_id}/workflows/{workflow_id}/draft")
async def save_workflow_draft_route(
    workspace_id: str,
    workflow_id: str,
    payload: DraftSaveRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=workflow_id)
        head = await session.get(ExperimentAggregateHead, workflow_id)
        if head is None or head.workspace_id != workspace_id:
            raise NotFound(f"workflow not found: {workflow_id}")
        draft = await save_workflow_draft(
            session,
            workflow_id,
            payload.payload,
            expected_generation=payload.expected_generation,
        )
        await session.commit()
        return {
            "id": draft.resource_id,
            "workflow_id": draft.workflow_id,
            "generation": draft.generation,
            "payload": json.loads(draft.canonical_payload),
            "updated_at": draft.updated_at,
        }
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.post("/{workspace_id}/workflows/{workflow_id}/revisions", status_code=status.HTTP_201_CREATED)
async def save_workflow_revision_route(
    workspace_id: str,
    workflow_id: str,
    payload: RevisionSaveRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=workflow_id)
        head = await session.get(ExperimentAggregateHead, workflow_id)
        if head is None or head.workspace_id != workspace_id:
            raise NotFound(f"workflow not found: {workflow_id}")
        revision = await save_workflow_revision(
            session,
            workflow_id,
            expected_head_generation=payload.expected_head_generation,
        )
        await session.commit()
        return _revision_json(revision)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.post("/{workspace_id}/workflows/{workflow_id}/clone", status_code=status.HTTP_201_CREATED)
async def clone_workspace_workflow(
    workspace_id: str,
    workflow_id: str,
    payload: CloneRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=workflow_id)
        source = await session.get(ExperimentAggregateHead, workflow_id)
        if source is None or source.workspace_id != workspace_id or source.aggregate_kind != "workflow":
            raise NotFound(f"workflow not found: {workflow_id}")
        clone = await clone_workflow(
            session,
            workflow_id,
            source_revision_id=payload.source_revision_id,
            name=payload.name,
        )
        await session.commit()
        return _head_json(clone)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.get("/{workspace_id}/workflows/{workflow_id}/revisions")
async def list_workflow_revisions(
    workspace_id: str,
    workflow_id: str,
    session: AsyncSession = Depends(get_experiment_session),
) -> list[dict[str, Any]]:
    head = await session.get(ExperimentAggregateHead, workflow_id)
    if head is None or head.workspace_id != workspace_id or head.aggregate_kind != "workflow":
        raise HTTPException(status_code=404, detail=f"workflow not found: {workflow_id}")
    revisions = (
        await session.execute(
            select(ExperimentRevision)
            .where(ExperimentRevision.subject_id == workflow_id)
            .order_by(ExperimentRevision.revision_number)
        )
    ).scalars().all()
    return [_revision_json(revision) for revision in revisions]


@router.get("/{workspace_id}/revisions/{revision_id}")
async def get_revision(
    workspace_id: str,
    revision_id: str,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    revision = await session.get(ExperimentRevision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail=f"revision not found: {revision_id}")
    subject = await session.get(ExperimentResource, revision.subject_id)
    if subject is None or subject.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail=f"revision not found: {revision_id}")
    return _revision_json(revision)


@router.post("/{workspace_id}/datasets", status_code=status.HTTP_201_CREATED)
async def create_workspace_dataset(
    workspace_id: str,
    payload: DatasetCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=workspace_id)
        head = await create_dataset(
            session,
            workspace_id,
            payload.name,
            payload.dataset_kind,
            experiment_id=payload.domain_experiment_id,
        )
        await session.commit()
        return _head_json(head)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.post("/{workspace_id}/datasets/{dataset_id}/revisions", status_code=status.HTTP_201_CREATED)
async def save_dataset_revision_route(
    workspace_id: str,
    dataset_id: str,
    payload: DatasetRevisionSaveRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=dataset_id)
        head = await session.get(ExperimentAggregateHead, dataset_id)
        if head is None or head.workspace_id != workspace_id:
            raise NotFound(f"dataset not found: {dataset_id}")
        revision = await save_dataset_revision(
            session,
            dataset_id,
            payload.payload,
            expected_head_generation=payload.expected_head_generation,
        )
        await session.commit()
        return _revision_json(revision)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.post("/{workspace_id}/preparations", status_code=status.HTTP_201_CREATED)
async def prepare_workspace_workflow(
    workspace_id: str,
    payload: PrepareRequest,
    workflow_revision_id: str,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        _mutation_principal(request)
        revision = await session.get(ExperimentRevision, workflow_revision_id)
        if revision is None:
            raise NotFound(f"workflow revision not found: {workflow_revision_id}")
        subject = await session.get(ExperimentResource, revision.subject_id)
        if subject is None or subject.workspace_id != workspace_id:
            raise NotFound(f"workflow revision not found: {workflow_revision_id}")
        await _require_mutation_owner(request, session, resource_id=subject.id)
        preparation = await prepare_workflow(
            session,
            workflow_revision_id,
            {"input_dataset_revision_ids": payload.input_dataset_revision_ids},
        )
        await session.commit()
        return {
            "id": preparation.resource_id,
            "workspace_id": preparation.workspace_id,
            "workflow_revision_id": preparation.workflow_revision_id,
            "validation_status": preparation.validation_status,
            "validation_receipt": json.loads(preparation.validation_receipt_json),
            "normalized_request_sha256": preparation.normalized_request_sha256,
            "scheduler_payload": json.loads(preparation.scheduler_payload_json),
            "prepared_at": preparation.prepared_at,
        }
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.get("/{workspace_id}/preparations/{preparation_id}")
async def get_workspace_preparation(
    workspace_id: str,
    preparation_id: str,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    preparation = await session.get(ExperimentWorkflowPreparation, preparation_id)
    if preparation is None or preparation.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail=f"preparation not found: {preparation_id}")
    return {
        "id": preparation.resource_id,
        "workspace_id": preparation.workspace_id,
        "workflow_revision_id": preparation.workflow_revision_id,
        "validation_status": preparation.validation_status,
        "validation_resource_id": preparation.validation_resource_id,
        "validation_receipt": json.loads(preparation.validation_receipt_json),
        "normalized_request_sha256": preparation.normalized_request_sha256,
        "scheduler_payload": json.loads(preparation.scheduler_payload_json),
        "prepared_at": preparation.prepared_at,
    }


@router.get("/{workspace_id}/validations/{validation_id}")
async def get_workspace_validation(
    workspace_id: str,
    validation_id: str,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    validation = await session.get(ExperimentValidation, validation_id)
    if validation is None:
        raise HTTPException(status_code=404, detail=f"validation not found: {validation_id}")
    resource = await session.get(ExperimentResource, validation.resource_id)
    if resource is None or resource.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail=f"validation not found: {validation_id}")
    return {
        "id": validation.resource_id,
        "subject_resource_id": validation.subject_resource_id,
        "validator": f"{validation.validator_name}/{validation.validator_version}",
        "outcome": validation.outcome,
        "input_graph_sha256": validation.input_graph_sha256,
        "receipt": json.loads(validation.receipt_json),
        "receipt_sha256": validation.receipt_sha256,
        "created_at": validation.created_at,
    }


@router.post("/{workspace_id}/run-groups", status_code=status.HTTP_201_CREATED)
async def create_workspace_run_group(
    workspace_id: str,
    payload: RunGroupCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=workspace_id)
        worker = global_experiment_worker()
        if worker is None:
            raise HTTPException(status_code=503, detail="managed dispatcher is not installed")
        if not worker.ensure_dispatcher_available():
            raise HTTPException(status_code=503, detail="managed dispatcher ownership is unavailable")
        group = await create_run_group(
            session,
            workspace_id,
            payload.preparation_ids,
            idempotency_key=payload.idempotency_key,
        )
        await session.commit()
        return {
            "id": group.resource_id,
            "workspace_id": group.workspace_id,
            "state": group.state,
            "request_sha256": group.request_sha256,
            "idempotency_key": group.launch_idempotency_key,
        }
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.get("/{workspace_id}/run-groups/{run_group_id}")
async def get_workspace_run_group(
    workspace_id: str,
    run_group_id: str,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    group = await session.get(ExperimentRunGroup, run_group_id)
    if group is None or group.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail=f"run group not found: {run_group_id}")
    runs = (
        await session.execute(
            select(ExperimentWorkflowRun)
            .where(ExperimentWorkflowRun.run_group_id == group.resource_id)
            .order_by(ExperimentWorkflowRun.created_at)
        )
    ).scalars().all()
    attempts = (
        await session.execute(
            select(ExperimentRunAttempt).where(
                ExperimentRunAttempt.workflow_run_id.in_([run.resource_id for run in runs])
            )
        )
    ).scalars().all() if runs else []
    attempts_by_run: dict[str, list[dict[str, Any]]] = {}
    for attempt in attempts:
        attempts_by_run.setdefault(attempt.workflow_run_id, []).append(
            {
                "id": attempt.resource_id,
                "attempt_number": attempt.attempt_number,
                "scheduler_job_id": attempt.scheduler_job_id,
                "state": attempt.state,
                "external_binding_receipt": _public_receipt(json.loads(attempt.external_binding_receipt_json)) if attempt.external_binding_receipt_json else None,
                "runtime_identity": _public_receipt(json.loads(attempt.runtime_identity_json)) if attempt.runtime_identity_json else None,
                "terminal_receipt": _public_receipt(json.loads(attempt.terminal_receipt_json)) if attempt.terminal_receipt_json else None,
            }
        )
    return {
        "id": group.resource_id,
        "workspace_id": group.workspace_id,
        "state": group.state,
        "generation": group.generation,
        "request_sha256": group.request_sha256,
        "idempotency_key": group.launch_idempotency_key,
        "runs": [
            {
                "id": run.resource_id,
                "preparation_id": run.preparation_id,
                "node_id": run.node_id,
                "requiredness": run.requiredness,
                "state": run.state,
                "attempts": attempts_by_run.get(run.resource_id, []),
            }
            for run in runs
        ],
        "created_at": group.created_at,
        "updated_at": group.updated_at,
    }


@router.post("/{workspace_id}/run-groups/{run_group_id}/reconcile")
async def reconcile_workspace_run_group(
    workspace_id: str,
    run_group_id: str,
    request: Request,
) -> dict[str, Any]:
    _operator_principal(request)
    raise HTTPException(
        status_code=409,
        detail="run-group reconciliation is owned by the managed dispatcher/reconciler",
    )


@router.post("/{workspace_id}/run-groups/{run_group_id}/retry")
async def retry_workspace_run_group(
    workspace_id: str,
    run_group_id: str,
    payload: RetryRunGroupRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=run_group_id)
        group = await retry_failed_run_group(
            session,
            workspace_id,
            run_group_id,
            idempotency_key=payload.idempotency_key,
            replacement_preparation_ids=payload.replacement_preparation_ids,
        )
        await session.commit()
        return {"id": group.resource_id, "state": group.state, "generation": group.generation}
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.post("/{workspace_id}/run-groups/{run_group_id}/resubmit", status_code=status.HTTP_201_CREATED)
async def resubmit_workspace_run_group(
    workspace_id: str,
    run_group_id: str,
    payload: ResubmitRunGroupRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=run_group_id)
        group = await resubmit_run_group(
            session,
            workspace_id,
            run_group_id,
            idempotency_key=payload.idempotency_key,
        )
        await session.commit()
        return {"id": group.resource_id, "state": group.state, "generation": group.generation}
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.post("/ops/backup", status_code=status.HTTP_201_CREATED)
async def create_experiment_backup(request: Request) -> dict[str, Any]:
    try:
        _operator_principal(request)
        return create_online_backup()
    except ExperimentOperationError as exc:
        raise _error(exc) from exc


@router.get("/ops/backups/{backup_id}/verify")
async def verify_experiment_backup(backup_id: str) -> dict[str, Any]:
    try:
        return verify_backup(backup_id)
    except ExperimentOperationError as exc:
        raise _error(exc) from exc


@router.get("/ops/sync-health")
async def get_experiment_sync_health(
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    states = (
        await session.execute(select(ExperimentSyncState).order_by(ExperimentSyncState.state_key))
    ).scalars().all()
    return {
        "schema": "bms.experiment.sync-health.v1",
        "single_writer": True,
        "credentials_exposed": False,
        "states": [
            {
                "state_key": state.state_key,
                "local_generation": state.local_generation,
                "remote_generation": state.remote_generation,
                "pending_changes": state.pending_changes,
                "last_success_at": state.last_success_at,
                "last_error": state.last_error,
                "updated_at": state.updated_at,
            }
            for state in states
        ],
    }


@router.post("/{workspace_id}/exports", status_code=status.HTTP_201_CREATED)
async def export_workspace(
    workspace_id: str,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=workspace_id)
        return await build_workspace_export(session, workspace_id)
    except ExperimentOperationError as exc:
        raise _error(exc) from exc


@router.get("/exports/{export_id}/verify")
async def verify_workspace_export_route(export_id: str) -> dict[str, Any]:
    try:
        return verify_workspace_export(export_id)
    except ExperimentOperationError as exc:
        raise _error(exc) from exc


@router.get("/{workspace_id}/analytics/summary")
async def get_workspace_analytics_summary(
    workspace_id: str,
    limit: int = 100,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        return await workspace_analytics(session, workspace_id, limit=limit)
    except ExperimentOperationError as exc:
        raise _error(exc) from exc


@router.post("/{workspace_id}/analytics/stats-handoffs", status_code=status.HTTP_201_CREATED)
async def register_stats_toolkit_handoff(
    workspace_id: str,
    payload: StatsHandoffRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=workspace_id)
        if len(payload.source_resource_ids) != len(payload.source_content_digests):
            raise ExperimentOperationError("Stats Toolkit source IDs and digests must have equal length")
        for resource_id, digest in zip(payload.source_resource_ids, payload.source_content_digests):
            resource = await session.get(ExperimentResource, resource_id)
            if resource is None or resource.workspace_id not in {workspace_id, None} and resource.id != workspace_id:
                raise ExperimentOperationError("Stats Toolkit source is not owned by this workspace")
            if len(digest) != 64 or digest != digest.lower() or any(char not in "0123456789abcdef" for char in digest):
                raise ExperimentOperationError("Stats Toolkit source digest must be lowercase SHA-256")
        receipt = await register_external_entity_receipt(
            session,
            workspace_id=workspace_id,
            store_id="stats-toolkit",
            entity_kind="stats_toolkit_run",
            entity_id=payload.stats_run_id,
            generation_or_revision=payload.result_generation_or_revision,
            content_digest=payload.result_content_digest,
            availability="available",
            acknowledgement={
                "schema": "bms.experiment.stats-handoff.v1",
                "toolkit_version": payload.toolkit_version,
                "source_resource_ids": payload.source_resource_ids,
                "source_content_digests": payload.source_content_digests,
                **payload.acknowledgement,
            },
        )
        await session.commit()
        return {
            "schema": "bms.experiment.stats-handoff.v1",
            "receipt_id": receipt.id,
            "workspace_id": workspace_id,
            "stats_run_id": payload.stats_run_id,
            "result_content_digest": receipt.content_digest,
            "source_resource_ids": payload.source_resource_ids,
            "toolkit_version": payload.toolkit_version,
        }
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.post("/{workspace_id}/external-receipts", status_code=status.HTTP_201_CREATED)
async def register_workspace_external_receipt(
    workspace_id: str,
    payload: ExternalReceiptCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=workspace_id)
        receipt = await register_external_entity_receipt(
            session,
            workspace_id=workspace_id,
            store_id=payload.store_id,
            entity_kind=payload.entity_kind,
            entity_id=payload.entity_id,
            generation_or_revision=payload.generation_or_revision,
            content_digest=payload.content_digest,
            availability=payload.availability,
            acknowledgement=payload.acknowledgement,
        )
        await session.commit()
        return {
            "id": receipt.id,
            "workspace_id": receipt.workspace_id,
            "store_id": receipt.store_id,
            "entity_kind": receipt.entity_kind,
            "entity_id": receipt.entity_id,
            "generation_or_revision": receipt.generation_or_revision,
            "content_digest": receipt.content_digest,
            "availability": receipt.availability,
            "acknowledgement": json.loads(receipt.acknowledgement_json or "{}"),
            "created_at": receipt.created_at,
        }
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.post("/dispatch/once")
async def dispatch_one_experiment_outbox(request: Request) -> dict[str, Any]:
    _operator_principal(request)
    raise HTTPException(
        status_code=409,
        detail="dispatch is owned by the managed single-owner dispatcher/reconciler",
    )


@router.get("/ops/worker-health")
async def experiment_worker_health(
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    worker = global_experiment_worker()
    if worker is None:
        raise HTTPException(status_code=503, detail="global experiment worker is not installed")
    return await worker.health_snapshot(session)


__all__ = ["router"]
