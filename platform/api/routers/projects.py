"""Canonical Project, Global Experiment, and Domain Experiment routes."""
from __future__ import annotations

import base64
import binascii
import json
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_database import get_experiment_session
from experiment_models import (
    ExperimentAggregateHead,
    ExperimentAuditEvent,
    ExperimentResource,
    ExperimentResearchRecord,
    ExperimentRevision,
    ExperimentWorkflowRun,
)
from experiment_services import (
    ExperimentServiceError,
    IdempotencyConflict,
    NotFound,
    RevisionConflict,
    ValidationFailure,
    add_audit_event,
    append_research_record,
    create_domain_experiment,
    create_global_experiment,
    create_project,
    restore_aggregate,
    save_hierarchy_revision,
    archive_aggregate,
)
from routers.experiment_workspaces import _mutation_principal, _require_mutation_owner


router = APIRouter(prefix="/api/projects", tags=["projects"])


class StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExternalReference(StrictRequestModel):
    kind: Literal["doi", "url", "accession", "ticket", "other"]
    value: str = Field(min_length=1)
    label: str = ""


class ProjectCreateRequest(StrictRequestModel):
    schema_: Literal["bms.project.v1"] = Field(default="bms.project.v1", alias="schema")
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    research_objective: str = ""
    owner: str | None = None
    contributors: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    status: Literal["draft", "active", "on_hold", "completed"] = "draft"
    start_date: date | None = None
    target_end_date: date | None = None
    external_references: list[ExternalReference] = Field(default_factory=list)
    created_by: str | None = None
    change_summary: str = "created"


class ProjectPatchRequest(StrictRequestModel):
    expected_head_generation: int = Field(ge=0)
    schema_: Literal["bms.project.v1"] | None = Field(default=None, alias="schema")
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    research_objective: str | None = None
    owner: str | None = None
    contributors: list[str] | None = None
    tags: list[str] | None = None
    status: Literal["draft", "active", "on_hold", "completed"] | None = None
    start_date: date | None = None
    target_end_date: date | None = None
    external_references: list[ExternalReference] | None = None
    created_by: str | None = None
    change_summary: str | None = None


class GlobalExperimentCreateRequest(StrictRequestModel):
    schema_: Literal["bms.global-experiment.v1"] = Field(default="bms.global-experiment.v1", alias="schema")
    name: str = Field(min_length=1, max_length=255)
    objective: str = ""
    scientific_question: str = ""
    hypothesis: str | None = None
    description: str = ""
    status: Literal["draft", "planned", "active", "analysis", "review", "completed", "blocked"] = "draft"
    priority: Literal["low", "normal", "high", "critical"] = "normal"
    tags: list[str] = Field(default_factory=list)
    shared_source_receipt_ids: list[str] = Field(default_factory=list)
    shared_dataset_ids: list[str] = Field(default_factory=list)
    comparison_plan: str | None = None
    success_criteria: list[str] = Field(default_factory=list)
    review_summary: str | None = None
    conclusion: str | None = None
    created_by: str | None = None
    change_summary: str = "created"


class GlobalExperimentPatchRequest(StrictRequestModel):
    expected_head_generation: int = Field(ge=0)
    schema_: Literal["bms.global-experiment.v1"] | None = Field(default=None, alias="schema")
    name: str | None = Field(default=None, min_length=1, max_length=255)
    objective: str | None = None
    scientific_question: str | None = None
    hypothesis: str | None = None
    description: str | None = None
    status: Literal["draft", "planned", "active", "analysis", "review", "completed", "blocked"] | None = None
    priority: Literal["low", "normal", "high", "critical"] | None = None
    tags: list[str] | None = None
    shared_source_receipt_ids: list[str] | None = None
    shared_dataset_ids: list[str] | None = None
    comparison_plan: str | None = None
    success_criteria: list[str] | None = None
    review_summary: str | None = None
    conclusion: str | None = None
    created_by: str | None = None
    change_summary: str | None = None


class ProteinTarget(StrictRequestModel):
    target_id: str = Field(min_length=1)
    label: str = ""
    entity_receipt_ids: list[str] = Field(default_factory=list)
    role: Literal["target", "binder", "partner", "template", "reference", "control", "other"]


class ProteinInSilicoPayload(StrictRequestModel):
    schema_: Literal["bms.protein-in-silico-experiment.v1"] = Field(alias="schema")
    experiment_mode: Literal["exploration", "design", "redesign", "prediction", "validation", "comparison", "simulation", "analysis"]
    targets: list[ProteinTarget]
    scientific_objective: str
    design_constraints: list[dict[str, Any]]
    planned_capabilities: list[str]
    comparison_groups: list[dict[str, Any]]
    validation_strategy: list[str]


class NgsMolBioPayload(StrictRequestModel):
    schema_: Literal["bms.ngs-molbio-experiment.v1"] = Field(alias="schema")


class DomainExperimentCreateRequest(StrictRequestModel):
    schema_: Literal["bms.domain-experiment.v1"] = Field(default="bms.domain-experiment.v1", alias="schema")
    domain_kind: Literal["protein_in_silico", "ngs_molbio"]
    domain_contract_version: str = Field(default="1", min_length=1)
    name: str = Field(min_length=1, max_length=255)
    objective: str = ""
    status: Literal["draft", "planned", "active", "analysis", "review", "completed", "blocked"] = "draft"
    tags: list[str] = Field(default_factory=list)
    source_receipt_ids: list[str] = Field(default_factory=list)
    dataset_ids: list[str] = Field(default_factory=list)
    created_by: str | None = None
    change_summary: str = "created"
    domain_payload: ProteinInSilicoPayload | NgsMolBioPayload


class DomainExperimentPatchRequest(StrictRequestModel):
    expected_head_generation: int = Field(ge=0)
    schema_: Literal["bms.domain-experiment.v1"] | None = Field(default=None, alias="schema")
    domain_contract_version: str | None = Field(default=None, min_length=1)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    objective: str | None = None
    status: Literal["draft", "planned", "active", "analysis", "review", "completed", "blocked"] | None = None
    tags: list[str] | None = None
    source_receipt_ids: list[str] | None = None
    dataset_ids: list[str] | None = None
    created_by: str | None = None
    change_summary: str | None = None
    domain_payload: ProteinInSilicoPayload | NgsMolBioPayload | None = None


class LifecycleRequest(StrictRequestModel):
    expected_head_generation: int = Field(ge=0)


class ResearchRecordRequest(StrictRequestModel):
    record_kind: Literal["note", "observation", "decision", "conclusion"]
    body: str = Field(min_length=1)
    author: str | None = None
    source_receipt_ids: list[str] = Field(default_factory=list)
    supersedes_record_id: str | None = None


def _error(exc: ExperimentServiceError) -> HTTPException:
    message = str(exc)
    if isinstance(exc, NotFound):
        return HTTPException(status_code=404, detail={"code": "not_found", "message": message})
    if isinstance(exc, RevisionConflict):
        return HTTPException(status_code=409, detail={"code": "stale_generation", "message": message})
    if isinstance(exc, IdempotencyConflict):
        return HTTPException(status_code=409, detail={"code": "idempotency_conflict", "message": message})
    if isinstance(exc, ValidationFailure):
        transition = any(token in message.lower() for token in ("archive", "restore", "lifecycle", "transition"))
        code = "invalid_transition" if transition else "validation_failed"
        return HTTPException(status_code=422, detail={"code": code, "message": message})
    return HTTPException(status_code=400, detail={"code": "unsupported_operation", "message": message})


async def _head_or_404(
    session: AsyncSession,
    aggregate_id: str,
    aggregate_kind: str,
    *,
    project_id: str | None = None,
    parent_id: str | None = None,
) -> ExperimentAggregateHead:
    head = await session.get(ExperimentAggregateHead, aggregate_id)
    resource = await session.get(ExperimentResource, aggregate_id)
    if head is None or resource is None or head.aggregate_kind != aggregate_kind:
        raise NotFound(f"{aggregate_kind} not found: {aggregate_id}")
    if project_id is not None and head.workspace_id != project_id:
        raise NotFound(f"{aggregate_kind} not found: {aggregate_id}")
    if parent_id is not None and head.parent_id != parent_id:
        raise NotFound(f"{aggregate_kind} not found: {aggregate_id}")
    return head


async def _project(session: AsyncSession, project_id: str) -> ExperimentAggregateHead:
    return await _head_or_404(session, project_id, "workspace")


async def _global_experiment(
    session: AsyncSession, project_id: str, experiment_id: str
) -> ExperimentAggregateHead:
    await _project(session, project_id)
    return await _head_or_404(
        session,
        experiment_id,
        "experiment",
        project_id=project_id,
        parent_id=project_id,
    )


async def _domain_experiment(
    session: AsyncSession,
    project_id: str,
    experiment_id: str,
    domain_id: str,
) -> ExperimentAggregateHead:
    await _global_experiment(session, project_id, experiment_id)
    return await _head_or_404(
        session,
        domain_id,
        "domain_experiment",
        project_id=project_id,
        parent_id=experiment_id,
    )


async def _payload(session: AsyncSession, head: ExperimentAggregateHead) -> dict[str, Any]:
    if not head.current_revision_id:
        return {}
    revision = await session.get(ExperimentRevision, head.current_revision_id)
    return json.loads(revision.canonical_payload) if revision is not None else {}


async def _head_json(
    session: AsyncSession,
    head: ExperimentAggregateHead,
    *,
    exposed_kind: str,
    storage_kind: str,
    parent_id: str | None = None,
) -> dict[str, Any]:
    payload = await _payload(session, head)
    lifecycle_state = head.lifecycle_state
    result = {
        "id": head.aggregate_id,
        "kind": exposed_kind,
        "storage_kind": storage_kind,
        "project_id": head.workspace_id,
        "workspace_id": head.workspace_id,
        "parent_id": parent_id if parent_id is not None else head.parent_id,
        "current_revision_id": head.current_revision_id,
        "head_generation": head.head_generation,
        "lifecycle_state": lifecycle_state,
        "status": "archived" if lifecycle_state == "archived" else payload.get("status", lifecycle_state),
        "name": payload.get("name", head.display_name),
        "description": payload.get("description", head.description),
        "payload": payload or None,
        "created_at": head.created_at,
        "updated_at": head.updated_at,
    }
    if exposed_kind == "project":
        result["project_id"] = head.aggregate_id
        result["workspace_id"] = head.aggregate_id
    if exposed_kind == "global_experiment":
        result["experiment_id"] = head.aggregate_id
    if exposed_kind == "domain_experiment":
        result["domain_experiment_id"] = head.aggregate_id
        result["global_experiment_id"] = head.parent_id
        result["domain_kind"] = payload.get("domain_kind")
    return result


def _encode_cursor(family: str, scope: str, sort_coordinate: str, identity: str) -> str:
    payload = json.dumps(
        [family, scope, sort_coordinate, identity],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None, family: str, scope: str) -> tuple[str, str] | None:
    if cursor is None:
        return None
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(cursor + padding).decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValidationFailure(f"invalid {family} cursor") from exc
    if (
        not isinstance(decoded, list)
        or len(decoded) != 4
        or decoded[0] != family
        or decoded[1] != scope
        or not isinstance(decoded[2], str)
        or not decoded[2]
        or not isinstance(decoded[3], str)
        or not decoded[3]
    ):
        raise ValidationFailure(f"invalid {family} cursor")
    return decoded[2], decoded[3]


async def _list_json(
    session: AsyncSession,
    *,
    project_id: str | None,
    aggregate_kind: str,
    exposed_kind: str,
    storage_kind: str,
    parent_id: str | None = None,
    cursor: str | None,
    limit: int,
    cursor_scope: str,
) -> dict[str, Any]:
    filters = [ExperimentAggregateHead.aggregate_kind == aggregate_kind]
    if project_id is not None:
        filters.append(ExperimentAggregateHead.workspace_id == project_id)
    if parent_id is not None:
        filters.append(ExperimentAggregateHead.parent_id == parent_id)
    decoded_cursor = _decode_cursor(cursor, exposed_kind, cursor_scope)
    if decoded_cursor is not None:
        created_at, aggregate_id = decoded_cursor
        filters.append(
            or_(
                ExperimentAggregateHead.created_at < created_at,
                and_(
                    ExperimentAggregateHead.created_at == created_at,
                    ExperimentAggregateHead.aggregate_id < aggregate_id,
                ),
            )
        )
    rows = (
        await session.execute(
            select(ExperimentAggregateHead)
            .where(*filters)
            .order_by(
                ExperimentAggregateHead.created_at.desc(),
                ExperimentAggregateHead.aggregate_id.desc(),
            )
            .limit(limit + 1)
        )
    ).scalars().all()
    page = rows[:limit]
    items = [
        await _head_json(
            session,
            head,
            exposed_kind=exposed_kind,
            storage_kind=storage_kind,
            parent_id=parent_id,
        )
        for head in page
    ]
    next_cursor = None
    if len(rows) > limit and page:
        anchor = page[-1]
        next_cursor = _encode_cursor(
            exposed_kind,
            cursor_scope,
            anchor.created_at,
            anchor.aggregate_id,
        )
    return {"items": items, "next_cursor": next_cursor}


def _record_json(record: ExperimentResearchRecord) -> dict[str, Any]:
    return {
        "id": record.resource_id,
        "workspace_id": record.workspace_id,
        "subject_resource_id": record.subject_resource_id,
        "record_kind": record.record_kind,
        "body": record.body,
        "author": record.author,
        "source_receipt_ids": json.loads(record.source_receipt_ids_json),
        "supersedes_record_id": record.supersedes_record_id,
        "created_at": record.created_at,
    }


async def _records(
    session: AsyncSession,
    subject_resource_id: str,
    kind: str | None,
    cursor: str | None,
    limit: int,
) -> dict[str, Any]:
    filters = [ExperimentResearchRecord.subject_resource_id == subject_resource_id]
    if kind is not None:
        filters.append(ExperimentResearchRecord.record_kind == kind)
    if cursor is not None:
        anchor = await session.get(ExperimentResearchRecord, cursor)
        if anchor is None or anchor.subject_resource_id != subject_resource_id:
            raise ValidationFailure("record cursor is invalid for this subject")
        filters.append(
            or_(
                ExperimentResearchRecord.created_at < anchor.created_at,
                and_(
                    ExperimentResearchRecord.created_at == anchor.created_at,
                    ExperimentResearchRecord.resource_id < anchor.resource_id,
                ),
            )
        )
    rows = (
        await session.execute(
            select(ExperimentResearchRecord)
            .where(*filters)
            .order_by(ExperimentResearchRecord.created_at.desc(), ExperimentResearchRecord.resource_id.desc())
            .limit(limit + 1)
        )
    ).scalars().all()
    has_more = len(rows) > limit
    page = rows[:limit]
    return {"items": [_record_json(row) for row in page], "next_cursor": page[-1].resource_id if has_more else None}


async def _activity(
    session: AsyncSession,
    subject_resource_id: str,
    cursor: str | None,
    limit: int,
) -> dict[str, Any]:
    filters = [ExperimentAuditEvent.resource_id == subject_resource_id]
    if cursor is not None:
        anchor = await session.get(ExperimentAuditEvent, cursor)
        if anchor is None or anchor.resource_id != subject_resource_id:
            raise ValidationFailure("activity cursor is invalid for this subject")
        filters.append(
            or_(
                ExperimentAuditEvent.created_at < anchor.created_at,
                and_(ExperimentAuditEvent.created_at == anchor.created_at, ExperimentAuditEvent.id < anchor.id),
            )
        )
    rows = (
        await session.execute(
            select(ExperimentAuditEvent)
            .where(*filters)
            .order_by(ExperimentAuditEvent.created_at.desc(), ExperimentAuditEvent.id.desc())
            .limit(limit + 1)
        )
    ).scalars().all()
    has_more = len(rows) > limit
    page = rows[:limit]
    return {
        "items": [
            {
                "id": row.id,
                "workspace_id": row.workspace_id,
                "resource_id": row.resource_id,
                "event_type": row.event_type,
                "generation": row.generation,
                "payload": json.loads(row.payload_json),
                "created_at": row.created_at,
            }
            for row in page
        ],
        "next_cursor": page[-1].id if has_more else None,
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


async def _revisions(
    session: AsyncSession,
    subject_resource_id: str,
    cursor: str | None,
    limit: int,
) -> dict[str, Any]:
    filters = [ExperimentRevision.subject_id == subject_resource_id]
    if cursor is not None:
        anchor = await session.get(ExperimentRevision, cursor)
        if anchor is None or anchor.subject_id != subject_resource_id:
            raise ValidationFailure("revision cursor is invalid for this subject")
        filters.append(ExperimentRevision.revision_number < anchor.revision_number)
    rows = (
        await session.execute(
            select(ExperimentRevision)
            .where(*filters)
            .order_by(ExperimentRevision.revision_number.desc())
            .limit(limit + 1)
        )
    ).scalars().all()
    has_more = len(rows) > limit
    page = rows[:limit]
    return {"items": [_revision_json(row) for row in page], "next_cursor": page[-1].resource_id if has_more else None}


async def _merge_patch(
    session: AsyncSession,
    head: ExperimentAggregateHead,
    patch: BaseModel,
) -> dict[str, Any]:
    current = await _payload(session, head)
    updates = patch.model_dump(mode="json", by_alias=True, exclude_unset=True)
    updates.pop("expected_head_generation", None)
    current.update(updates)
    return current


@router.get("")
async def list_projects(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    return await search_projects(
        q="",
        status_filter=None,
        archive="all",
        cursor=cursor,
        limit=limit,
        session=session,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project_route(
    payload: ProjectCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        owner_principal = _mutation_principal(request)
        if payload.owner not in {None, owner_principal} or payload.created_by not in {None, owner_principal}:
            raise HTTPException(status_code=403, detail="Project owner must match the authenticated principal")
        project_payload = payload.model_dump(mode="json", by_alias=True)
        project_payload["owner"] = owner_principal
        project_payload["created_by"] = owner_principal
        head = await create_project(session, project_payload)
        add_audit_event(
            session,
            workspace_id=head.aggregate_id,
            resource_id=head.aggregate_id,
            event_type="workspace_owner_bound",
            generation=0,
            payload={"principal_id": owner_principal},
        )
        await session.commit()
        return await _head_json(session, head, exposed_kind="project", storage_kind="workspace")
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.get("/search")
async def search_projects(
    q: str = Query(default="", max_length=256),
    status_filter: str | None = Query(default=None, alias="status"),
    archive: Literal["active", "archived", "all"] = Query(default="active"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    filters = [ExperimentAggregateHead.aggregate_kind == "workspace"]
    if archive == "active":
        filters.append(ExperimentAggregateHead.lifecycle_state != "archived")
    elif archive == "archived":
        filters.append(ExperimentAggregateHead.lifecycle_state == "archived")
    if status_filter:
        allowed_statuses = {"draft", "active", "on_hold", "completed", "archived"}
        if status_filter not in allowed_statuses:
            raise HTTPException(status_code=422, detail={"code": "validation_failed", "message": "invalid Project status filter"})
        filters.append(ExperimentAggregateHead.lifecycle_state == status_filter)
    normalized = q.strip()
    cursor_scope = json.dumps(
        [normalized, status_filter or "", archive],
        separators=(",", ":"),
        ensure_ascii=True,
    )
    statement = select(ExperimentAggregateHead).join(
        ExperimentRevision,
        ExperimentRevision.resource_id == ExperimentAggregateHead.current_revision_id,
    )
    if normalized:
        pattern = f"%{normalized}%"
        filters.append(
            or_(
                ExperimentAggregateHead.display_name.ilike(pattern),
                func.json_extract(ExperimentRevision.canonical_payload, "$.research_objective").ilike(pattern),
                func.json_extract(ExperimentRevision.canonical_payload, "$.owner").ilike(pattern),
                func.json_extract(ExperimentRevision.canonical_payload, "$.tags").ilike(pattern),
            )
        )
    try:
        decoded_cursor = _decode_cursor(cursor, "project-search", cursor_scope)
    except ValidationFailure as exc:
        raise _error(exc) from exc
    if decoded_cursor is not None:
        updated_at, aggregate_id = decoded_cursor
        filters.append(
            or_(
                ExperimentAggregateHead.updated_at < updated_at,
                and_(
                    ExperimentAggregateHead.updated_at == updated_at,
                    ExperimentAggregateHead.aggregate_id < aggregate_id,
                ),
            )
        )
    rows = (
        await session.execute(
            statement.where(*filters)
            .order_by(ExperimentAggregateHead.updated_at.desc(), ExperimentAggregateHead.aggregate_id.desc())
            .limit(limit + 1)
        )
    ).scalars().all()
    page = rows[:limit]
    project_ids = [row.aggregate_id for row in page]
    active_counts: dict[str, int] = {}
    blocked_counts: dict[str, int] = {}
    failed_run_counts: dict[str, int] = {}
    if project_ids:
        active_counts = dict((await session.execute(
            select(ExperimentAggregateHead.workspace_id, func.count())
            .where(
                ExperimentAggregateHead.workspace_id.in_(project_ids),
                ExperimentAggregateHead.aggregate_kind.in_(("experiment", "domain_experiment")),
                ExperimentAggregateHead.lifecycle_state == "active",
            )
            .group_by(ExperimentAggregateHead.workspace_id)
        )).all())
        blocked_counts = dict((await session.execute(
            select(ExperimentAggregateHead.workspace_id, func.count())
            .where(
                ExperimentAggregateHead.workspace_id.in_(project_ids),
                ExperimentAggregateHead.aggregate_kind.in_(("experiment", "domain_experiment")),
                ExperimentAggregateHead.lifecycle_state == "blocked",
            )
            .group_by(ExperimentAggregateHead.workspace_id)
        )).all())
        failed_run_counts = dict((await session.execute(
            select(ExperimentWorkflowRun.workspace_id, func.count())
            .where(
                ExperimentWorkflowRun.workspace_id.in_(project_ids),
                ExperimentWorkflowRun.state == "failed",
            )
            .group_by(ExperimentWorkflowRun.workspace_id)
        )).all())
    items: list[dict[str, Any]] = []
    for head in page:
        item = await _head_json(session, head, exposed_kind="project", storage_kind="workspace")
        item["active_experiment_count"] = int(active_counts.get(head.aggregate_id, 0))
        item["unresolved_failure_count"] = int(blocked_counts.get(head.aggregate_id, 0)) + int(failed_run_counts.get(head.aggregate_id, 0))
        items.append(item)
    return {
        "items": items,
        "next_cursor": (
            _encode_cursor(
                "project-search",
                cursor_scope,
                page[-1].updated_at,
                page[-1].aggregate_id,
            )
            if len(rows) > limit and page
            else None
        ),
    }


@router.get("/{project_id}")
async def get_project(project_id: str, session: AsyncSession = Depends(get_experiment_session)) -> dict[str, Any]:
    try:
        head = await _project(session, project_id)
        return await _head_json(session, head, exposed_kind="project", storage_kind="workspace")
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.patch("/{project_id}")
async def patch_project(
    project_id: str,
    payload: ProjectPatchRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=project_id)
        head = await _project(session, project_id)
        await save_hierarchy_revision(
            session,
            project_id,
            "workspace",
            await _merge_patch(session, head, payload),
            expected_head_generation=payload.expected_head_generation,
        )
        await session.commit()
        refreshed = await _project(session, project_id)
        return await _head_json(session, refreshed, exposed_kind="project", storage_kind="workspace")
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.post("/{project_id}/archive")
async def archive_project(
    project_id: str,
    payload: LifecycleRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=project_id)
        head = await _project(session, project_id)
        archived = await archive_aggregate(session, head.aggregate_id, expected_head_generation=payload.expected_head_generation)
        await session.commit()
        return await _head_json(session, archived, exposed_kind="project", storage_kind="workspace")
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.post("/{project_id}/restore")
async def restore_project(
    project_id: str,
    payload: LifecycleRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=project_id)
        head = await _project(session, project_id)
        restored = await restore_aggregate(session, head.aggregate_id, expected_head_generation=payload.expected_head_generation)
        await session.commit()
        return await _head_json(session, restored, exposed_kind="project", storage_kind="workspace")
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.get("/{project_id}/experiments")
async def list_global_experiments(
    project_id: str,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _project(session, project_id)
        return await _list_json(
            session,
            project_id=project_id,
            aggregate_kind="experiment",
            exposed_kind="global_experiment",
            storage_kind="experiment",
            parent_id=project_id,
            cursor=cursor,
            limit=limit,
            cursor_scope=project_id,
        )
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.post("/{project_id}/experiments", status_code=status.HTTP_201_CREATED)
async def create_global_experiment_route(
    project_id: str,
    payload: GlobalExperimentCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=project_id)
        await _project(session, project_id)
        head = await create_global_experiment(session, project_id, payload.model_dump(mode="json", by_alias=True))
        await session.commit()
        return await _head_json(
            session,
            head,
            exposed_kind="global_experiment",
            storage_kind="experiment",
            parent_id=project_id,
        )
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.get("/{project_id}/experiments/{experiment_id}")
async def get_global_experiment(
    project_id: str,
    experiment_id: str,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        head = await _global_experiment(session, project_id, experiment_id)
        return await _head_json(
            session,
            head,
            exposed_kind="global_experiment",
            storage_kind="experiment",
            parent_id=project_id,
        )
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.patch("/{project_id}/experiments/{experiment_id}")
async def patch_global_experiment(
    project_id: str,
    experiment_id: str,
    payload: GlobalExperimentPatchRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=project_id)
        head = await _global_experiment(session, project_id, experiment_id)
        await save_hierarchy_revision(
            session,
            experiment_id,
            "experiment",
            await _merge_patch(session, head, payload),
            expected_head_generation=payload.expected_head_generation,
        )
        await session.commit()
        refreshed = await _global_experiment(session, project_id, experiment_id)
        return await _head_json(
            session,
            refreshed,
            exposed_kind="global_experiment",
            storage_kind="experiment",
            parent_id=project_id,
        )
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.post("/{project_id}/experiments/{experiment_id}/archive")
async def archive_global_experiment(
    project_id: str,
    experiment_id: str,
    payload: LifecycleRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=project_id)
        head = await _global_experiment(session, project_id, experiment_id)
        archived = await archive_aggregate(session, head.aggregate_id, expected_head_generation=payload.expected_head_generation)
        await session.commit()
        return await _head_json(session, archived, exposed_kind="global_experiment", storage_kind="experiment", parent_id=project_id)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.post("/{project_id}/experiments/{experiment_id}/restore")
async def restore_global_experiment(
    project_id: str,
    experiment_id: str,
    payload: LifecycleRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=project_id)
        head = await _global_experiment(session, project_id, experiment_id)
        restored = await restore_aggregate(session, head.aggregate_id, expected_head_generation=payload.expected_head_generation)
        await session.commit()
        return await _head_json(session, restored, exposed_kind="global_experiment", storage_kind="experiment", parent_id=project_id)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.get("/{project_id}/experiments/{experiment_id}/domains")
async def list_domain_experiments(
    project_id: str,
    experiment_id: str,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _global_experiment(session, project_id, experiment_id)
        return await _list_json(
            session,
            project_id=project_id,
            aggregate_kind="domain_experiment",
            exposed_kind="domain_experiment",
            storage_kind="domain_experiment",
            parent_id=experiment_id,
            cursor=cursor,
            limit=limit,
            cursor_scope=f"{project_id}:{experiment_id}",
        )
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.post("/{project_id}/experiments/{experiment_id}/domains", status_code=status.HTTP_201_CREATED)
async def create_domain_experiment_route(
    project_id: str,
    experiment_id: str,
    payload: DomainExperimentCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=project_id)
        await _global_experiment(session, project_id, experiment_id)
        head = await create_domain_experiment(
            session,
            project_id,
            experiment_id,
            payload.model_dump(mode="json", by_alias=True),
        )
        await session.commit()
        return await _head_json(
            session,
            head,
            exposed_kind="domain_experiment",
            storage_kind="domain_experiment",
            parent_id=experiment_id,
        )
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.get("/{project_id}/experiments/{experiment_id}/domains/{domain_id}")
async def get_domain_experiment(
    project_id: str,
    experiment_id: str,
    domain_id: str,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        head = await _domain_experiment(session, project_id, experiment_id, domain_id)
        return await _head_json(
            session,
            head,
            exposed_kind="domain_experiment",
            storage_kind="domain_experiment",
            parent_id=experiment_id,
        )
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.patch("/{project_id}/experiments/{experiment_id}/domains/{domain_id}")
async def patch_domain_experiment(
    project_id: str,
    experiment_id: str,
    domain_id: str,
    payload: DomainExperimentPatchRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=project_id)
        head = await _domain_experiment(session, project_id, experiment_id, domain_id)
        await save_hierarchy_revision(
            session,
            domain_id,
            "domain_experiment",
            await _merge_patch(session, head, payload),
            expected_head_generation=payload.expected_head_generation,
        )
        await session.commit()
        refreshed = await _domain_experiment(session, project_id, experiment_id, domain_id)
        return await _head_json(
            session,
            refreshed,
            exposed_kind="domain_experiment",
            storage_kind="domain_experiment",
            parent_id=experiment_id,
        )
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.post("/{project_id}/experiments/{experiment_id}/domains/{domain_id}/archive")
async def archive_domain_experiment(
    project_id: str,
    experiment_id: str,
    domain_id: str,
    payload: LifecycleRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=project_id)
        head = await _domain_experiment(session, project_id, experiment_id, domain_id)
        archived = await archive_aggregate(session, head.aggregate_id, expected_head_generation=payload.expected_head_generation)
        await session.commit()
        return await _head_json(session, archived, exposed_kind="domain_experiment", storage_kind="domain_experiment", parent_id=experiment_id)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.post("/{project_id}/experiments/{experiment_id}/domains/{domain_id}/restore")
async def restore_domain_experiment(
    project_id: str,
    experiment_id: str,
    domain_id: str,
    payload: LifecycleRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=project_id)
        head = await _domain_experiment(session, project_id, experiment_id, domain_id)
        restored = await restore_aggregate(session, head.aggregate_id, expected_head_generation=payload.expected_head_generation)
        await session.commit()
        return await _head_json(session, restored, exposed_kind="domain_experiment", storage_kind="domain_experiment", parent_id=experiment_id)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


async def _append_record_route(
    session: AsyncSession,
    *,
    project_id: str,
    subject_resource_id: str,
    payload: ResearchRecordRequest,
) -> dict[str, Any]:
    record = await append_research_record(
        session,
        workspace_id=project_id,
        subject_resource_id=subject_resource_id,
        record_kind=payload.record_kind,
        body=payload.body,
        author=payload.author,
        source_receipt_ids=payload.source_receipt_ids,
        supersedes_record_id=payload.supersedes_record_id,
    )
    await session.commit()
    return _record_json(record)


@router.get("/{project_id}/revisions")
async def list_project_revisions(
    project_id: str,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _project(session, project_id)
        return await _revisions(session, project_id, cursor, limit)
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.get("/{project_id}/experiments/{experiment_id}/revisions")
async def list_global_experiment_revisions(
    project_id: str,
    experiment_id: str,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _global_experiment(session, project_id, experiment_id)
        return await _revisions(session, experiment_id, cursor, limit)
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.get("/{project_id}/records")
async def list_project_records(
    project_id: str,
    kind: Literal["note", "observation", "decision", "conclusion"] | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _project(session, project_id)
        return await _records(session, project_id, kind, cursor, limit)
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.post("/{project_id}/records", status_code=status.HTTP_201_CREATED)
async def append_project_record(
    project_id: str,
    payload: ResearchRecordRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=project_id)
        await _project(session, project_id)
        return await _append_record_route(session, project_id=project_id, subject_resource_id=project_id, payload=payload)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.get("/{project_id}/experiments/{experiment_id}/records")
async def list_global_experiment_records(
    project_id: str,
    experiment_id: str,
    kind: Literal["note", "observation", "decision", "conclusion"] | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _global_experiment(session, project_id, experiment_id)
        return await _records(session, experiment_id, kind, cursor, limit)
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.post("/{project_id}/experiments/{experiment_id}/records", status_code=status.HTTP_201_CREATED)
async def append_global_experiment_record(
    project_id: str,
    experiment_id: str,
    payload: ResearchRecordRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=project_id)
        await _global_experiment(session, project_id, experiment_id)
        return await _append_record_route(session, project_id=project_id, subject_resource_id=experiment_id, payload=payload)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.get("/{project_id}/experiments/{experiment_id}/domains/{domain_id}/records")
async def list_domain_experiment_records(
    project_id: str,
    experiment_id: str,
    domain_id: str,
    kind: Literal["note", "observation", "decision", "conclusion"] | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _domain_experiment(session, project_id, experiment_id, domain_id)
        return await _records(session, domain_id, kind, cursor, limit)
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.post("/{project_id}/experiments/{experiment_id}/domains/{domain_id}/records", status_code=status.HTTP_201_CREATED)
async def append_domain_experiment_record(
    project_id: str,
    experiment_id: str,
    domain_id: str,
    payload: ResearchRecordRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _require_mutation_owner(request, session, resource_id=project_id)
        await _domain_experiment(session, project_id, experiment_id, domain_id)
        return await _append_record_route(session, project_id=project_id, subject_resource_id=domain_id, payload=payload)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.get("/{project_id}/activity")
async def list_project_activity(
    project_id: str,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _project(session, project_id)
        return await _activity(session, project_id, cursor, limit)
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.get("/{project_id}/experiments/{experiment_id}/activity")
async def list_global_experiment_activity(
    project_id: str,
    experiment_id: str,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _global_experiment(session, project_id, experiment_id)
        return await _activity(session, experiment_id, cursor, limit)
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.get("/{project_id}/experiments/{experiment_id}/domains/{domain_id}/activity")
async def list_domain_experiment_activity(
    project_id: str,
    experiment_id: str,
    domain_id: str,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await _domain_experiment(session, project_id, experiment_id, domain_id)
        return await _activity(session, domain_id, cursor, limit)
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


__all__ = ["router"]
