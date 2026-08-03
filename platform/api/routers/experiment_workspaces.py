"""Global workspace/workflow/dataset/run-group HTTP contract."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session as get_core_session
from experiment_database import get_experiment_session
from experiment_models import (
    ExperimentAggregateHead,
    ExperimentResource,
    ExperimentRevision,
    ExperimentRunGroup,
    ExperimentRunAttempt,
    ExperimentValidation,
    ExperimentWorkflowPreparation,
    ExperimentWorkflowRun,
)
from experiment_services import (
    ExistingJobMaterializer,
    ExperimentServiceError,
    IdempotencyConflict,
    NotFound,
    RevisionConflict,
    ValidationFailure,
    create_dataset,
    create_experiment,
    create_experiment_workspace,
    create_run_group,
    create_workflow,
    dispatch_pending_outbox,
    archive_aggregate,
    clone_workflow,
    prepare_workflow,
    save_dataset_revision,
    save_workflow_draft,
    save_workflow_revision,
)


router = APIRouter(prefix="/api/experiment-workspaces", tags=["experiment-workspaces"])


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
    experiment_id: str | None = None


class DatasetCreateRequest(StrictRequestModel):
    name: str = Field(min_length=1, max_length=255)
    dataset_kind: str = Field(min_length=1, max_length=128)
    experiment_id: str | None = None


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


def _error(exc: ExperimentServiceError) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (RevisionConflict, IdempotencyConflict)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValidationFailure):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


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
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        head = await create_experiment_workspace(session, payload.name, payload.description)
        await session.commit()
        return _head_json(head)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


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
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
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
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        head = await create_experiment(session, workspace_id, payload.name, payload.question)
        await session.commit()
        return _head_json(head)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.post("/{workspace_id}/workflows", status_code=status.HTTP_201_CREATED)
async def create_workspace_workflow(
    workspace_id: str,
    payload: WorkflowCreateRequest,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        head = await create_workflow(
            session,
            workspace_id,
            payload.name,
            payload.workflow_family,
            experiment_id=payload.experiment_id,
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
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
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
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
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
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
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
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        head = await create_dataset(
            session,
            workspace_id,
            payload.name,
            payload.dataset_kind,
            experiment_id=payload.experiment_id,
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
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
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
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        revision = await session.get(ExperimentRevision, workflow_revision_id)
        if revision is None:
            raise NotFound(f"workflow revision not found: {workflow_revision_id}")
        subject = await session.get(ExperimentResource, revision.subject_id)
        if subject is None or subject.workspace_id != workspace_id:
            raise NotFound(f"workflow revision not found: {workflow_revision_id}")
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
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
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
                "external_binding_receipt": json.loads(attempt.external_binding_receipt_json) if attempt.external_binding_receipt_json else None,
                "runtime_identity": json.loads(attempt.runtime_identity_json) if attempt.runtime_identity_json else None,
                "terminal_receipt": json.loads(attempt.terminal_receipt_json) if attempt.terminal_receipt_json else None,
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


@router.post("/dispatch/once")
async def dispatch_one_experiment_outbox(
    session: AsyncSession = Depends(get_experiment_session),
    core_session: AsyncSession = Depends(get_core_session),
) -> dict[str, Any]:
    try:
        dispatched = await dispatch_pending_outbox(session, ExistingJobMaterializer(core_session))
        return {"dispatched": dispatched}
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


__all__ = ["router"]
