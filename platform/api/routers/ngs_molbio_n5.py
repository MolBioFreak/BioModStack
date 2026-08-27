"""Canonical nested Phase N5 backend routes for NGS/MolBio Projects."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, TypeVar
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session as get_core_session
from experiment_database import get_experiment_session
from experiment_models import (
    ExperimentAggregateHead,
    ExperimentAuditEvent,
    ExperimentDatasetRevisionMember,
    ExperimentDomainAdapterReceipt,
    ExperimentExternalEntityReceipt,
    ExperimentLineageEdge,
    ExperimentLogChunk,
    ExperimentLogStream,
    ExperimentRevision,
    ExperimentRunAttempt,
    ExperimentRunGroup,
    ExperimentValidation,
    ExperimentWorkflowPreparation,
    ExperimentWorkflowRun,
)
from experiment_services import ExperimentServiceError, IdempotencyConflict, NotFound, RevisionConflict, ValidationFailure
from molbio_database import get_molbio_session
from molbio_models import (
    MolBioAuditEvent,
    MolBioOutboxEvent,
    MolecularDocument,
    MolecularOperation,
    MolecularOperationInput,
    MolecularRevision,
    NucleotideSequence,
    ProjectPlasmidMetadata,
)
from molbio_ngs_database import get_molbio_ngs_session
from molbio_ngs_models import (
    MolBioNGSDomainState,
    MolBioNGSDomainStateMember,
    MolBioNGSDomainStateRevision,
    MolBioNGSEvidenceAssessment,
    MolBioNGSMemberReceipt,
    MolBioNGSReferenceResource,
    MolBioNGSSample,
)
from routers.experiment_workspaces import _operator_principal, _require_mutation_owner
from services.ngs_molbio_capabilities import NgsMolBioCapabilityError
from services.ngs_molbio_runtime_status import (
    NgsMolBioRuntimeAuthorityError,
    runtime_implementation_record,
)
from services.molbio_ngs_member_receipts import persist_member_receipt, resolve_molecular_revision_receipt
from services.molbio_persistence import begin_immediate_molbio_write, record_sequence_revision
from molbio_ngs_services import RevisionConflict as NativeRevisionConflict, StateMember, save_state_revision
from services.ngs_molbio_n5 import (
    InvalidLifecycleTransition,
    create_project_dataset,
    enabled_dataset_kind_records,
    decode_cursor,
    encode_cursor,
    operational_status,
    require_dataset_read,
    require_domain_hierarchy,
    revise_project_dataset,
    set_project_dataset_lifecycle,
)


router = APIRouter(tags=["ngs-molbio-n5"])
D = "/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


ModelT = TypeVar("ModelT", bound=BaseModel)


class ProjectHubMolecularFields(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    molecule_type: str = Field(min_length=1, max_length=32)
    topology: str = Field(pattern="^(linear|circular)$")
    description: str = Field(max_length=4000)
    organism_host_context: str | None = Field(default=None, max_length=255)


class ProjectHubMembershipMetadata(StrictModel):
    project_tags: list[str] = Field(default_factory=list, max_length=32)
    project_notes: str = Field(default="", max_length=4000)


class ProjectHubEditInfo(StrictModel):
    expected_molecular_revision_id: str = Field(min_length=1, max_length=128)
    expected_state_revision_id: str = Field(min_length=1, max_length=128)
    expected_state_head_generation: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=255)
    molecular_fields: ProjectHubMolecularFields
    project_metadata: ProjectHubMembershipMetadata


class DatasetCreate(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    dataset_kind: str = Field(min_length=1, max_length=255)
    change_summary: str = Field(min_length=1, max_length=1024)


class DatasetMemberMetadata(StrictModel):
    display_label: str | None = Field(default=None, min_length=1, max_length=255)
    group_label: str | None = Field(default=None, min_length=1, max_length=128)
    condition_label: str | None = Field(default=None, min_length=1, max_length=128)
    tags: list[str] = Field(default_factory=list, max_length=16)


class DatasetMember(StrictModel):
    receipt_id: str = Field(min_length=1, max_length=128)
    role: str = Field(min_length=1, max_length=128, pattern=r"^[\x21-\x7e]+$")
    ordinal: int = Field(ge=0)
    media_type: str | None = Field(default=None, max_length=128, pattern=r"^[\x21-\x7e]+$")
    metadata: DatasetMemberMetadata = Field(default_factory=DatasetMemberMetadata)


class DatasetRevisionCreate(StrictModel):
    expected_head_generation: int = Field(ge=0)
    change_summary: str = Field(min_length=1, max_length=1024)
    members: list[DatasetMember] = Field(max_length=10_000)


class DatasetLifecycleRequest(StrictModel):
    expected_head_generation: int = Field(ge=0)
    change_summary: str = Field(min_length=1, max_length=1024)


def _error(exc: ExperimentServiceError) -> HTTPException:
    message = str(exc)
    if isinstance(exc, NotFound):
        return HTTPException(404, detail={"code": "not_found", "message": message})
    if isinstance(exc, IdempotencyConflict):
        return HTTPException(409, detail={"code": "idempotency_conflict", "message": message})
    if isinstance(exc, InvalidLifecycleTransition):
        return HTTPException(409, detail={"code": "invalid_lifecycle_transition", "message": message})
    if isinstance(exc, RevisionConflict):
        return HTTPException(409, detail={"code": "stale_generation", "message": message})
    code = message if message in {"unsupported_dataset_kind", "unsupported_dataset_member", "dataset_metadata_payload_forbidden"} else "validation_failed"
    return HTTPException(422, detail={"code": code, "message": message})


def _key(request: Request) -> str:
    value = request.headers.get("Idempotency-Key", "")
    if not value or len(value) > 255 or any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise HTTPException(422, detail={"code": "invalid_idempotency_key", "message": "Idempotency-Key must contain 1..255 visible ASCII characters."})
    return value


async def _body(request: Request, model: type[ModelT], maximum: int) -> ModelT:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > maximum:
                raise HTTPException(413, detail={"code": "dataset_request_too_large"})
        except ValueError as exc:
            raise HTTPException(400, detail={"code": "invalid_content_length"}) from exc
    raw = await request.body()
    if len(raw) > maximum:
        raise HTTPException(413, detail={"code": "dataset_request_too_large"})
    try:
        value = model.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(422, detail={"code": "invalid_dataset_request", "errors": exc.errors(include_url=False)}) from exc
    if len(json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) > maximum:
        raise HTTPException(413, detail={"code": "dataset_request_too_large"})
    return value


def _bounded_response(document: dict[str, Any], maximum: int = 256 * 1024) -> dict[str, Any]:
    encoded = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    if len(encoded) > maximum:
        raise HTTPException(
            status_code=413,
            detail={"code": "response_too_large", "maximum_bytes": maximum},
        )
    return document


def _member_doc(row: ExperimentDatasetRevisionMember) -> dict[str, Any]:
    value = json.loads(row.value_json)
    return {**value, "canonical_member_sha256": row.content_sha256, "size_bytes": row.size_bytes}


@router.get(D + "/dataset-kinds")
async def list_dataset_kinds(
    project_id: str,
    experiment_id: str,
    domain_id: str,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
        registry_document, enabled_records = enabled_dataset_kind_records()
        items = [
            {
                "dataset_kind": row["dataset_kind"],
                "label": row["label"],
                "minimum_members": row["minimum_members"],
                "maximum_members": row["maximum_members"],
                "allowed_members": row["allowed_members"],
                "compatibility_rules": row["compatibility_rules"],
            }
            for row in enabled_records
        ]
        items.sort(key=lambda item: (item["label"].casefold(), item["dataset_kind"]))
        return _bounded_response({
            "schema": "bms.dataset-kind-list.v1",
            "registry_schema": registry_document["schema"],
            "registry_sha256": registry_document["content_sha256"],
            "items": items,
        })
    except NgsMolBioCapabilityError as exc:
        raise HTTPException(503, detail={"code": "dataset_kind_authority_unavailable", "message": str(exc)}) from exc
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.get(D + "/datasets")
async def list_datasets(project_id: str, experiment_id: str, domain_id: str, cursor: str | None = Query(default=None, max_length=1024), limit: int = Query(default=50, ge=1, le=100), session: AsyncSession = Depends(get_experiment_session)) -> dict[str, Any]:
    try:
        await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
        scope = f"datasets:{project_id}:{experiment_id}:{domain_id}"
        anchor = decode_cursor(cursor, scope=scope, limit=limit)
        statement = select(ExperimentAggregateHead).where(ExperimentAggregateHead.aggregate_kind == "dataset", ExperimentAggregateHead.workspace_id == project_id, ExperimentAggregateHead.parent_id == domain_id).order_by(ExperimentAggregateHead.created_at.desc(), ExperimentAggregateHead.aggregate_id.desc()).limit(limit + 1)
        if anchor:
            statement = statement.where(or_(ExperimentAggregateHead.created_at < anchor[0], (ExperimentAggregateHead.created_at == anchor[0]) & (ExperimentAggregateHead.aggregate_id < anchor[1])))
        rows = list((await session.scalars(statement)).all())
        items = [{"dataset_id": row.aggregate_id, "name": row.display_name, "dataset_kind": row.dataset_kind, "current_revision_id": row.current_revision_id, "head_generation": row.head_generation, "lifecycle_state": row.lifecycle_state, "created_at": row.created_at, "updated_at": row.updated_at} for row in rows[:limit]]
        next_cursor = encode_cursor(scope=scope, created_at=rows[limit - 1].created_at, stable_id=rows[limit - 1].aggregate_id, limit=limit) if len(rows) > limit else None
        return _bounded_response({"schema": "bms.dataset-list.v1", "items": items, "next_cursor": next_cursor, "has_more": next_cursor is not None})
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.post(D + "/datasets", status_code=201)
async def create_dataset_route(project_id: str, experiment_id: str, domain_id: str, request: Request, session: AsyncSession = Depends(get_experiment_session)) -> dict[str, Any]:
    payload = await _body(request, DatasetCreate, 64 * 1024)
    try:
        actor = await _require_mutation_owner(request, session, resource_id=project_id)
        result = await create_project_dataset(session, project_id=project_id, experiment_id=experiment_id, domain_id=domain_id, actor=actor, idempotency_key=_key(request), **payload.model_dump())
        await session.commit()
        return _bounded_response(result)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.get(D + "/datasets/{dataset_id}")
async def get_dataset_route(project_id: str, experiment_id: str, domain_id: str, dataset_id: str, session: AsyncSession = Depends(get_experiment_session)) -> dict[str, Any]:
    try:
        await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
        head = await require_dataset_read(session, project_id=project_id, domain_id=domain_id, dataset_id=dataset_id)
        return _bounded_response({"schema": "bms.dataset-head.v1", "project_id": project_id, "global_experiment_id": experiment_id, "domain_id": domain_id, "dataset_id": dataset_id, "name": head.display_name, "dataset_kind": head.dataset_kind, "current_revision_id": head.current_revision_id, "head_generation": head.head_generation, "lifecycle_state": head.lifecycle_state, "created_at": head.created_at, "updated_at": head.updated_at})
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.post(D + "/datasets/{dataset_id}/archive")
async def archive_dataset_route(
    project_id: str,
    experiment_id: str,
    domain_id: str,
    dataset_id: str,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    payload = await _body(request, DatasetLifecycleRequest, 16 * 1024)
    try:
        actor = await _require_mutation_owner(request, session, resource_id=project_id)
        result = await set_project_dataset_lifecycle(
            session,
            project_id=project_id,
            experiment_id=experiment_id,
            domain_id=domain_id,
            dataset_id=dataset_id,
            operation="archive",
            expected_head_generation=payload.expected_head_generation,
            change_summary=payload.change_summary,
            actor=actor,
            idempotency_key=_key(request),
        )
        await session.commit()
        return _bounded_response(result)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.post(D + "/datasets/{dataset_id}/restore")
async def restore_dataset_route(
    project_id: str,
    experiment_id: str,
    domain_id: str,
    dataset_id: str,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    payload = await _body(request, DatasetLifecycleRequest, 16 * 1024)
    try:
        actor = await _require_mutation_owner(request, session, resource_id=project_id)
        result = await set_project_dataset_lifecycle(
            session,
            project_id=project_id,
            experiment_id=experiment_id,
            domain_id=domain_id,
            dataset_id=dataset_id,
            operation="restore",
            expected_head_generation=payload.expected_head_generation,
            change_summary=payload.change_summary,
            actor=actor,
            idempotency_key=_key(request),
        )
        await session.commit()
        return _bounded_response(result)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.post(D + "/datasets/{dataset_id}/revisions", status_code=201)
async def revise_dataset_route(project_id: str, experiment_id: str, domain_id: str, dataset_id: str, request: Request, session: AsyncSession = Depends(get_experiment_session), core_session: AsyncSession = Depends(get_core_session)) -> dict[str, Any]:
    payload = await _body(request, DatasetRevisionCreate, 1024 * 1024)
    try:
        actor = await _require_mutation_owner(request, session, resource_id=project_id)
        body = payload.model_dump(mode="json")
        result = await revise_project_dataset(session, core_session, project_id=project_id, experiment_id=experiment_id, domain_id=domain_id, dataset_id=dataset_id, actor=actor, idempotency_key=_key(request), **body)
        await session.commit()
        return _bounded_response(result)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _error(exc) from exc


@router.get(D + "/datasets/{dataset_id}/revisions")
async def list_dataset_revisions(project_id: str, experiment_id: str, domain_id: str, dataset_id: str, cursor: str | None = Query(default=None, max_length=1024), limit: int = Query(default=50, ge=1, le=100), session: AsyncSession = Depends(get_experiment_session)) -> dict[str, Any]:
    try:
        await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
        await require_dataset_read(session, project_id=project_id, domain_id=domain_id, dataset_id=dataset_id)
        scope = f"dataset-revisions:{project_id}:{domain_id}:{dataset_id}"
        anchor = decode_cursor(cursor, scope=scope, limit=limit)
        statement = select(ExperimentRevision).where(ExperimentRevision.subject_id == dataset_id).order_by(ExperimentRevision.created_at.desc(), ExperimentRevision.resource_id.desc()).limit(limit + 1)
        if anchor:
            statement = statement.where(or_(ExperimentRevision.created_at < anchor[0], (ExperimentRevision.created_at == anchor[0]) & (ExperimentRevision.resource_id < anchor[1])))
        rows = list((await session.scalars(statement)).all())
        items = [{"revision_id": row.resource_id, "revision_number": row.revision_number, "parent_revision_id": row.parent_revision_id, "revision_sha256": row.payload_sha256, "created_at": row.created_at} for row in rows[:limit]]
        next_cursor = encode_cursor(scope=scope, created_at=rows[limit - 1].created_at, stable_id=rows[limit - 1].resource_id, limit=limit) if len(rows) > limit else None
        return _bounded_response({"schema": "bms.dataset-revision-list.v1", "items": items, "next_cursor": next_cursor, "has_more": next_cursor is not None})
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


async def _exact_revision(session: AsyncSession, *, project_id: str, domain_id: str, dataset_id: str, revision_id: str) -> ExperimentRevision:
    await require_dataset_read(session, project_id=project_id, domain_id=domain_id, dataset_id=dataset_id)
    revision = await session.get(ExperimentRevision, revision_id)
    if revision is None or revision.subject_id != dataset_id:
        raise NotFound("exact Dataset revision not found")
    return revision


@router.get(D + "/datasets/{dataset_id}/revisions/{revision_id}")
async def get_dataset_revision(project_id: str, experiment_id: str, domain_id: str, dataset_id: str, revision_id: str, session: AsyncSession = Depends(get_experiment_session)) -> dict[str, Any]:
    try:
        await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
        revision = await _exact_revision(session, project_id=project_id, domain_id=domain_id, dataset_id=dataset_id, revision_id=revision_id)
        rows = list((await session.scalars(select(ExperimentDatasetRevisionMember).where(ExperimentDatasetRevisionMember.revision_id == revision_id).order_by(ExperimentDatasetRevisionMember.ordinal).limit(101))).all())
        result = {"schema": "bms.dataset-revision.v1", "dataset_id": dataset_id, "revision_id": revision_id, "revision_number": revision.revision_number, "parent_revision_id": revision.parent_revision_id, "revision_sha256": revision.payload_sha256, "member_count": len(json.loads(revision.canonical_payload).get("members", [])), "created_at": revision.created_at}
        if len(rows) <= 100:
            result["members"] = [_member_doc(row) for row in rows]
        else:
            result["members_uri"] = f"/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/datasets/{dataset_id}/revisions/{revision_id}/members"
        return _bounded_response(result, 1024 * 1024)
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.get(D + "/datasets/{dataset_id}/revisions/{revision_id}/members")
async def list_dataset_members(project_id: str, experiment_id: str, domain_id: str, dataset_id: str, revision_id: str, cursor: str | None = Query(default=None, max_length=1024), limit: int = Query(default=100, ge=1, le=100), session: AsyncSession = Depends(get_experiment_session)) -> dict[str, Any]:
    try:
        await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
        await _exact_revision(session, project_id=project_id, domain_id=domain_id, dataset_id=dataset_id, revision_id=revision_id)
        scope = f"dataset-members:{project_id}:{domain_id}:{revision_id}"
        anchor = decode_cursor(cursor, scope=scope, limit=limit)
        after = int(anchor[1]) if anchor else -1
        rows = list((await session.scalars(select(ExperimentDatasetRevisionMember).where(ExperimentDatasetRevisionMember.revision_id == revision_id, ExperimentDatasetRevisionMember.ordinal > after).order_by(ExperimentDatasetRevisionMember.ordinal).limit(limit + 1))).all())
        next_cursor = encode_cursor(scope=scope, created_at=revision_id, stable_id=str(rows[limit - 1].ordinal), limit=limit) if len(rows) > limit else None
        return _bounded_response({"schema": "bms.dataset-member-page.v1", "items": [_member_doc(row) for row in rows[:limit]], "next_cursor": next_cursor, "has_more": next_cursor is not None})
    except (ExperimentServiceError, ValueError) as exc:
        mapped = exc if isinstance(exc, ExperimentServiceError) else ValidationFailure("invalid or stale cursor")
        raise _error(mapped) from exc


def _native_page(rows: list[Any], *, scope: str, limit: int, identity: str, document: Any) -> dict[str, Any]:
    page = rows[:limit]
    next_cursor = encode_cursor(scope=scope, created_at=str(getattr(page[-1], "created_at")), stable_id=str(getattr(page[-1], identity)), limit=limit) if len(rows) > limit else None
    return {"items": [document(row) for row in page], "next_cursor": next_cursor, "has_more": next_cursor is not None}


async def _native_rows(session: AsyncSession, model: Any, *, domain_id: str, identity: str, cursor: str | None, limit: int, scope: str) -> list[Any]:
    anchor = decode_cursor(cursor, scope=scope, limit=limit)
    id_column = getattr(model, identity)
    statement = select(model).where(model.global_domain_experiment_id == domain_id).order_by(model.created_at.desc(), id_column.desc()).limit(limit + 1)
    if anchor:
        statement = statement.where(or_(model.created_at < anchor[0], (model.created_at == anchor[0]) & (id_column < anchor[1])))
    return list((await session.scalars(statement)).all())


async def _project_hub_linked_receipts(
    session: AsyncSession,
    *,
    project_id: str,
    domain_id: str,
    kinds: set[str],
    limit: int = 100,
) -> list[ExperimentExternalEntityReceipt]:
    return list((await session.scalars(
        select(ExperimentExternalEntityReceipt)
        .join(ExperimentLineageEdge, ExperimentLineageEdge.target_resource_id == ExperimentExternalEntityReceipt.id)
        .where(
            ExperimentExternalEntityReceipt.workspace_id == project_id,
            ExperimentLineageEdge.workspace_id == project_id,
            ExperimentLineageEdge.source_resource_id == domain_id,
            ExperimentExternalEntityReceipt.entity_kind.in_(kinds),
        )
        .order_by(ExperimentExternalEntityReceipt.created_at.desc(), ExperimentExternalEntityReceipt.id.desc())
        .limit(limit)
    )).unique().all())


def _hub_acknowledgement(row: ExperimentExternalEntityReceipt) -> dict[str, Any]:
    try:
        value = json.loads(row.acknowledgement_json or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _hub_href(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    surface = value.get("surface")
    params = value.get("params")
    if surface == "molbio-sequence-revision" and isinstance(params, dict):
        return f"/designer?molbio_sequence_id={params.get('sequence_id')}&molbio_revision_id={params.get('revision_id')}"
    return None


def _project_hub_molecular_href(
    *,
    project_id: str,
    experiment_id: str,
    domain_id: str,
    state_revision_id: str,
    sequence_id: str,
    revision_id: str,
) -> str:
    return "/designer?" + urlencode({
        "workspace_id": project_id,
        "global_experiment_id": experiment_id,
        "domain_experiment_id": domain_id,
        "state_revision_id": state_revision_id,
        "section": "plasmids",
        "molbio_sequence_id": sequence_id,
        "molbio_revision_id": revision_id,
    })


@router.get(D + "/project-hub")
async def project_hub(
    project_id: str,
    experiment_id: str,
    domain_id: str,
    state_revision_id: str = Query(min_length=1, max_length=128),
    session: AsyncSession = Depends(get_experiment_session),
    native: AsyncSession = Depends(get_molbio_ngs_session),
    molbio: AsyncSession = Depends(get_molbio_session),
) -> dict[str, Any]:
    """Compose a bounded operator read model without copying scientific payloads."""
    try:
        project, experiment, domain = await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
    except ExperimentServiceError as exc:
        raise _error(exc) from exc
    state = await native.get(MolBioNGSDomainState, domain_id)
    selected = await native.get(MolBioNGSDomainStateRevision, state_revision_id)
    if state is None or selected is None or selected.global_domain_experiment_id != domain_id:
        raise HTTPException(404, detail={"code": "not_found", "message": "exact Domain state revision not found"})

    member_rows = list((await native.execute(
        select(MolBioNGSDomainStateMember, MolBioNGSMemberReceipt)
        .join(MolBioNGSMemberReceipt, MolBioNGSMemberReceipt.receipt_id == MolBioNGSDomainStateMember.receipt_id)
        .where(MolBioNGSDomainStateMember.state_revision_id == selected.id)
        .order_by(MolBioNGSDomainStateMember.ordinal, MolBioNGSDomainStateMember.receipt_id)
        .limit(101)
    )).all())
    if len(member_rows) > 100:
        raise HTTPException(413, detail={"code": "project_hub_member_limit"})
    molecular_receipts = [receipt for _member, receipt in member_rows if receipt.entity_kind == "molecular_revision"]
    revision_ids = [receipt.entity_id for receipt in molecular_receipts]
    revisions = {
        row.id: row for row in (await molbio.scalars(
            select(MolecularRevision).where(MolecularRevision.id.in_(revision_ids))
        )).all()
    } if revision_ids else {}
    metadata_rows = list((await molbio.scalars(
        select(ProjectPlasmidMetadata).where(
            ProjectPlasmidMetadata.project_id == project_id,
            ProjectPlasmidMetadata.domain_experiment_id == domain_id,
            ProjectPlasmidMetadata.active_state_revision_id == selected.id,
        ).limit(100)
    )).all())
    project_metadata = {row.sequence_id: row for row in metadata_rows}

    operation_receipts = await _project_hub_linked_receipts(
        session, project_id=project_id, domain_id=domain_id,
        kinds={"molecular_operation", "pcr_experiment_revision"},
    )
    operation_ids = [row.entity_id for row in operation_receipts if row.entity_kind == "molecular_operation"]
    operations = {
        row.id: row for row in (await molbio.scalars(
            select(MolecularOperation).where(MolecularOperation.id.in_(operation_ids))
        )).all()
    } if operation_ids else {}
    input_rows = list((await molbio.scalars(
        select(MolecularOperationInput).where(MolecularOperationInput.operation_id.in_(operation_ids))
    )).all()) if operation_ids else []
    operation_sequence_ids: dict[str, set[str]] = {}
    if input_rows:
        input_revisions = {
            row.id: row for row in (await molbio.scalars(
                select(MolecularRevision).where(MolecularRevision.id.in_([item.revision_id for item in input_rows]))
            )).all()
        }
        for item in input_rows:
            revision = input_revisions.get(item.revision_id)
            if revision is not None:
                operation_sequence_ids.setdefault(item.operation_id, set()).add(revision.document_id)
    receipt_sequence_ids: dict[str, set[str]] = {}
    for row in operation_receipts:
        linked = set(operation_sequence_ids.get(row.entity_id, set()))
        ack = _hub_acknowledgement(row)
        metadata = ack.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        many = metadata.get("plasmid_sequence_ids")
        if isinstance(many, list):
            linked.update(str(value) for value in many if isinstance(value, str) and value)
        for key in ("plasmid_sequence_id", "sequence_id", "molecular_sequence_id"):
            value = metadata.get(key)
            if isinstance(value, str) and value:
                linked.add(value)
        receipt_sequence_ids[row.id] = linked

    plasmids: list[dict[str, Any]] = []
    names_by_sequence: dict[str, str] = {}
    for receipt in molecular_receipts:
        revision = revisions.get(receipt.entity_id)
        if revision is None or not isinstance(revision.snapshot, dict):
            try:
                destination = json.loads(receipt.reopen_destination)
            except (TypeError, ValueError):
                destination = {}
            params = destination.get("params") if isinstance(destination, dict) and isinstance(destination.get("params"), dict) else {}
            sequence_id = str(params.get("sequence_id") or receipt.entity_id)
            plasmids.append({
                "sequence_id": sequence_id, "revision_id": receipt.entity_id,
                "receipt_id": receipt.receipt_id, "receipt_sha256": receipt.receipt_sha256,
                "content_digest": receipt.content_digest, "source_store_id": receipt.source_store_id,
                "schema_name": receipt.schema_name, "revision_number": 0,
                "name": sequence_id, "description": "Molecular member unavailable",
                "availability": "unavailable", "unavailable_reason": "Molecular member unavailable",
                "length_bp": 0, "gc_percent": None, "feature_count": 0, "feature_labels": [],
                "cmv_promoter": None, "neor_kanr": None, "replication_origin_count": None,
                "saved_experiment_count": 0, "molecule_type": None, "topology": None,
                "organism_host_context": None, "project_tags": [], "project_notes": "",
                "reopen_href": _project_hub_molecular_href(
                    project_id=project_id, experiment_id=experiment_id, domain_id=domain_id,
                    state_revision_id=selected.id, sequence_id=sequence_id, revision_id=receipt.entity_id,
                ),
                "map_segments": [],
            })
            continue
        snapshot = revision.snapshot
        features = snapshot.get("features") if isinstance(snapshot.get("features"), list) else []
        labels = [str(item.get("name") or item.get("label") or item.get("type") or "Feature") for item in features if isinstance(item, dict)]
        folded = [label.casefold() for label in labels]
        sequence_id = revision.document_id
        name = str(snapshot.get("name") or sequence_id)
        names_by_sequence[sequence_id] = name
        saved_count = sum(sequence_id in receipt_sequence_ids.get(row.id, set()) for row in operation_receipts)
        map_segments = [
            {"start": int(item.get("start", 0)), "end": int(item.get("end", 0)), "tone": "accent"}
            for item in features[:24] if isinstance(item, dict)
        ]
        metadata = project_metadata.get(sequence_id)
        plasmids.append({
            "sequence_id": sequence_id,
            "revision_id": revision.id,
            "receipt_id": receipt.receipt_id,
            "receipt_sha256": receipt.receipt_sha256,
            "content_digest": receipt.content_digest,
            "source_store_id": receipt.source_store_id,
            "schema_name": receipt.schema_name,
            "revision_number": revision.revision_number,
            "name": name,
            "description": str(snapshot.get("description") or ""),
            "availability": receipt.availability,
            "unavailable_reason": None,
            "length_bp": revision.content_length,
            "gc_percent": snapshot.get("gc_content"),
            "feature_count": len(features),
            "feature_labels": labels,
            "cmv_promoter": any("cmv" in label and "promoter" in label for label in folded),
            "neor_kanr": any("neor" in label or "kanr" in label for label in folded),
            "replication_origin_count": sum("origin" in label or "ori" in label for label in folded),
            "saved_experiment_count": saved_count,
            "molecule_type": snapshot.get("sequence_type") or "dna",
            "topology": "circular" if snapshot.get("is_circular") else "linear",
            "organism_host_context": snapshot.get("organism"),
            "project_tags": list(metadata.tags or []) if metadata is not None and metadata.molecular_revision_id == revision.id else [],
            "project_notes": str(metadata.notes or "") if metadata is not None and metadata.molecular_revision_id == revision.id else "",
            "reopen_href": _project_hub_molecular_href(
                project_id=project_id,
                experiment_id=experiment_id,
                domain_id=domain_id,
                state_revision_id=selected.id,
                sequence_id=sequence_id,
                revision_id=revision.id,
            ),
            "map_segments": map_segments,
        })

    experiments: list[dict[str, Any]] = []
    kind_map = {"digest": "restriction_digest", "alignment": "alignment", "pcr": "pcr"}
    for row in operation_receipts:
        ack = _hub_acknowledgement(row)
        metadata = ack.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        operation = operations.get(row.entity_id)
        sequence_ids = sorted(sequence_id for sequence_id in receipt_sequence_ids.get(row.id, set()) if sequence_id in names_by_sequence)
        if not sequence_ids:
            continue
        sequence_id = sequence_ids[0]
        operation_kind = operation.operation_kind if operation is not None else "pcr"
        summary = dict(operation.parameters or {}).get("summary") if operation is not None else None
        title = metadata.get("title") or (summary.get("title") if isinstance(summary, dict) else None) or operation_kind.replace("_", " ").title()
        experiments.append({
            "id": row.entity_id, "persistence": "saved", "kind": kind_map.get(operation_kind, "sequence_change"),
            "plasmid_sequence_id": sequence_id, "plasmid_name": names_by_sequence.get(sequence_id, sequence_id),
            "plasmid_sequence_ids": sequence_ids,
            "title": str(title), "status": operation.status if operation is not None else "saved",
            "created_at": row.created_at, "reopen_href": ack.get("reopen_uri"),
        })

    sequence_kinds = {"ont_instrument_run", "ngs_job", "ngs_read_set", "ngs_alignment_job"}
    result_kinds = {"ngs_result_manifest", "sequence_qc_job", "ngs_analysis_job", "ngs_alignment_job"}
    evidence_receipts = await _project_hub_linked_receipts(
        session, project_id=project_id, domain_id=domain_id, kinds=sequence_kinds | result_kinds,
    )
    sequence_items: list[dict[str, Any]] = []
    result_items: list[dict[str, Any]] = []
    for row in evidence_receipts:
        ack = _hub_acknowledgement(row)
        metadata = ack.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        sequence_id = str(metadata.get("plasmid_sequence_id") or "")
        plasmid_name = names_by_sequence.get(sequence_id, str(metadata.get("plasmid_name") or "Unassigned plasmid"))
        if row.entity_kind in sequence_kinds:
            sequence_items.append({
                "id": row.entity_id, "plasmid_sequence_id": sequence_id, "plasmid_name": plasmid_name,
                "kind": "run" if row.entity_kind in {"ont_instrument_run", "ngs_job"} else "read_set" if row.entity_kind == "ngs_read_set" else "alignment",
                "title": str(metadata.get("title") or row.entity_kind.replace("_", " ").title()),
                "summary": str(metadata.get("summary") or ""), "status": str(metadata.get("status") or row.availability),
                "created_at": row.created_at, "reopen_href": ack.get("reopen_uri"),
            })
        if row.entity_kind in result_kinds:
            result_items.append({
                "id": row.entity_id, "plasmid_name": plasmid_name,
                "type": str(metadata.get("title") or row.entity_kind.replace("_", " ").title()),
                "status": str(metadata.get("status") or row.availability), "owner": str(metadata.get("owner") or row.entity_id),
                "created_at": row.created_at, "summary": metadata.get("summary"), "reopen_href": ack.get("reopen_uri"),
            })

    activities = list((await session.scalars(
        select(ExperimentAuditEvent).where(
            ExperimentAuditEvent.workspace_id == project_id,
            ExperimentAuditEvent.resource_id == domain_id,
        ).order_by(ExperimentAuditEvent.created_at.desc(), ExperimentAuditEvent.id.desc()).limit(50)
    )).all())
    activity_items = []
    for row in activities:
        try:
            body = json.loads(row.payload_json)
        except (TypeError, ValueError):
            body = {}
        sequence_id = body.get("sequence_id") if isinstance(body, dict) else None
        name = body.get("name") if isinstance(body, dict) else None
        readable = f"{name or names_by_sequence.get(str(sequence_id), 'Plasmid')} added to the project" if row.event_type == "molecular_member_attached" else row.event_type.replace("_", " ").replace(".", " ").capitalize()
        activity_items.append({
            "id": row.id, "summary": readable, "occurred_at": row.created_at,
            "technical_event_type": row.event_type, "receipt_id": str(body.get("receipt_id") or ""),
            "envelope_sha256": __import__("hashlib").sha256(row.payload_json.encode("utf-8")).hexdigest(),
        })

    return _bounded_response({
        "schema": "bms.project-hub.v1",
        "project": {
            "id": project_id, "name": project.display_name, "objective": project.description,
            "lifecycle_state": project.lifecycle_state, "created_at": project.created_at, "plasmid_count": len(plasmids),
            "settings_href": f"/projects/{project_id}", "add_plasmid_href": f"/designer?workspace_id={project_id}&section=plasmids&action=add-plasmid",
        },
        "identity": {
            "workspace_id": project_id, "global_experiment_id": experiment_id, "domain_experiment_id": domain_id,
            "selected_state_revision_id": selected.id, "current_state_revision_id": state.current_state_revision_id,
            "state_head_generation": state.head_generation, "global_domain_revision_id": selected.global_domain_experiment_revision_id,
            "membership_graph_sha256": selected.membership_graph_sha256, "binding_status": "acknowledged", "adapter_status": "available",
        },
        "plasmids": plasmids,
        "sequence_data": {
            "items": sequence_items,
            "import_href": f"/ngs?workspace_id={project_id}&global_experiment_id={experiment_id}&domain_experiment_id={domain_id}&state_revision_id={selected.id}&section=sequence-data&action=import-ont",
            "launcher_href": f"/ngs?workspace_id={project_id}&global_experiment_id={experiment_id}&domain_experiment_id={domain_id}&state_revision_id={selected.id}&section=sequence-data",
        },
        "experiments": experiments,
        "results": result_items,
        "activity": activity_items,
    })


async def _compensate_project_hub_molecular_edit(
    molbio: AsyncSession,
    *,
    sequence_id: str,
    old_revision_id: str,
    old_snapshot: dict[str, Any],
    metadata_id: str,
    new_revision_id: str,
    prior_metadata: dict[str, Any] | None,
) -> None:
    await molbio.rollback()
    await begin_immediate_molbio_write(molbio)
    sequence = await molbio.get(NucleotideSequence, sequence_id)
    document = await molbio.get(MolecularDocument, sequence_id)
    if sequence is None or document is None:
        raise RuntimeError("project-hub compensation authority is missing")
    sequence.name = str(old_snapshot.get("name") or sequence.name)
    sequence.description = old_snapshot.get("description")
    sequence.sequence_type = str(old_snapshot.get("sequence_type") or sequence.sequence_type)
    sequence.is_circular = bool(old_snapshot.get("is_circular"))
    sequence.organism = old_snapshot.get("organism")
    sequence.version = int(old_snapshot.get("version") or max(int(sequence.version or 2) - 1, 1))
    sequence.updated_at = old_snapshot.get("updated_at")
    document.name = sequence.name
    document.document_kind = sequence.sequence_type
    document.current_revision_id = old_revision_id
    audit_rows = list((await molbio.scalars(select(MolBioAuditEvent).where(
        MolBioAuditEvent.entity_kind == "molecular_document",
        MolBioAuditEvent.entity_id == sequence_id,
        MolBioAuditEvent.event_kind == "sequence.project_metadata_update",
    ))).all())
    for row in audit_rows:
        if isinstance(row.payload, dict) and row.payload.get("revision_id") == new_revision_id:
            await molbio.delete(row)
    outbox_rows = list((await molbio.scalars(select(MolBioOutboxEvent).where(
        MolBioOutboxEvent.aggregate_kind == "molecular_document",
        MolBioOutboxEvent.aggregate_id == sequence_id,
        MolBioOutboxEvent.event_kind == "sequence.project_metadata_update",
    ))).all())
    for row in outbox_rows:
        if isinstance(row.payload, dict) and row.payload.get("revision_id") == new_revision_id:
            await molbio.delete(row)
    metadata = await molbio.get(ProjectPlasmidMetadata, metadata_id)
    if prior_metadata is None:
        if metadata is not None:
            await molbio.delete(metadata)
    elif metadata is not None:
        for key, value in prior_metadata.items():
            setattr(metadata, key, value)
    await molbio.flush()
    leaked_revision = await molbio.get(MolecularRevision, new_revision_id)
    if leaked_revision is not None:
        await molbio.delete(leaked_revision)
    await molbio.commit()


@router.post(D + "/project-hub/plasmids/{sequence_id}/info")
async def edit_project_hub_plasmid_info(
    project_id: str,
    experiment_id: str,
    domain_id: str,
    sequence_id: str,
    payload: ProjectHubEditInfo,
    session: AsyncSession = Depends(get_experiment_session),
    native: AsyncSession = Depends(get_molbio_ngs_session),
    molbio: AsyncSession = Depends(get_molbio_session),
) -> dict[str, Any]:
    try:
        await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
    except ExperimentServiceError as exc:
        raise _error(exc) from exc
    fingerprint = hashlib.sha256(
        json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    metadata_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"bms:project-plasmid:{project_id}:{domain_id}:{sequence_id}"))
    existing_metadata = await molbio.get(ProjectPlasmidMetadata, metadata_id)
    state = await native.get(MolBioNGSDomainState, domain_id)
    if (
        existing_metadata is not None
        and existing_metadata.idempotency_key == payload.idempotency_key
    ):
        if existing_metadata.request_fingerprint != fingerprint:
            raise HTTPException(409, detail={"code": "idempotency_conflict", "message": "idempotency key was used with another edit"})
        if existing_metadata.active_state_revision_id and state is not None and state.current_state_revision_id == existing_metadata.active_state_revision_id:
            return await project_hub(project_id, experiment_id, domain_id, state.current_state_revision_id, session, native, molbio)
    if (
        state is None
        or state.head_generation != payload.expected_state_head_generation
        or state.current_state_revision_id != payload.expected_state_revision_id
    ):
        raise HTTPException(409, detail={"code": "stale_generation", "message": "Domain state head changed"})
    selected = await native.get(MolBioNGSDomainStateRevision, payload.expected_state_revision_id)
    document = await molbio.get(MolecularDocument, sequence_id)
    sequence = await molbio.get(NucleotideSequence, sequence_id)
    expected_revision = await molbio.get(MolecularRevision, payload.expected_molecular_revision_id)
    if (
        selected is None
        or selected.global_domain_experiment_id != domain_id
        or document is None
        or sequence is None
        or expected_revision is None
        or expected_revision.document_id != sequence_id
        or document.current_revision_id != expected_revision.id
    ):
        raise HTTPException(409, detail={"code": "stale_molecular_revision", "message": "molecular revision changed"})
    member_rows = list((await native.execute(
        select(MolBioNGSDomainStateMember, MolBioNGSMemberReceipt)
        .join(MolBioNGSMemberReceipt, MolBioNGSMemberReceipt.receipt_id == MolBioNGSDomainStateMember.receipt_id)
        .where(MolBioNGSDomainStateMember.state_revision_id == selected.id)
        .order_by(MolBioNGSDomainStateMember.ordinal)
    )).all())
    matching = [(member, receipt) for member, receipt in member_rows if receipt.entity_kind == "molecular_revision" and receipt.entity_id == expected_revision.id]
    if len(matching) != 1:
        raise HTTPException(409, detail={"code": "member_revision_mismatch", "message": "exact molecular member is not present once"})

    old_snapshot = dict(expected_revision.snapshot)
    prior_metadata = None
    if existing_metadata is not None:
        prior_metadata = {
            "molecular_revision_id": existing_metadata.molecular_revision_id,
            "active_state_revision_id": existing_metadata.active_state_revision_id,
            "tags": list(existing_metadata.tags or []),
            "notes": existing_metadata.notes,
            "idempotency_key": existing_metadata.idempotency_key,
            "request_fingerprint": existing_metadata.request_fingerprint,
            "updated_at": existing_metadata.updated_at,
        }
    await begin_immediate_molbio_write(molbio)
    sequence.name = payload.molecular_fields.name
    sequence.description = payload.molecular_fields.description
    sequence.sequence_type = payload.molecular_fields.molecule_type
    sequence.is_circular = payload.molecular_fields.topology == "circular"
    sequence.organism = payload.molecular_fields.organism_host_context
    sequence.version = int(sequence.version or 1) + 1
    sequence.updated_at = datetime.utcnow()
    new_revision = await record_sequence_revision(
        molbio, sequence, change_kind="project_metadata_update",
        provenance={"source": "project_hub", "project_id": project_id, "domain_experiment_id": domain_id},
    )
    metadata = existing_metadata or ProjectPlasmidMetadata(
        id=metadata_id, project_id=project_id, domain_experiment_id=domain_id, sequence_id=sequence_id,
        molecular_revision_id=new_revision.id, tags=[], notes="", idempotency_key=payload.idempotency_key,
        request_fingerprint=fingerprint,
    )
    if existing_metadata is None:
        molbio.add(metadata)
    metadata.molecular_revision_id = new_revision.id
    metadata.active_state_revision_id = None
    metadata.tags = list(dict.fromkeys(tag.strip() for tag in payload.project_metadata.project_tags if tag.strip()))
    metadata.notes = payload.project_metadata.project_notes
    metadata.idempotency_key = payload.idempotency_key
    metadata.request_fingerprint = fingerprint
    metadata.updated_at = datetime.utcnow()
    await molbio.commit()

    try:
        resolved = await resolve_molecular_revision_receipt(molbio, sequence_id=sequence_id, revision_id=new_revision.id)
        new_receipt = await persist_member_receipt(native, resolved)
        replacement_member = matching[0][0]
        next_members = [
            StateMember(
                receipt_id=new_receipt.receipt_id if member is replacement_member else member.receipt_id,
                role=member.role,
                ordinal=member.ordinal,
                sample_revision_id=member.sample_revision_id,
            )
            for member, _receipt in member_rows
        ]
        next_revision = await save_state_revision(
            native,
            global_domain_experiment_id=domain_id,
            global_domain_experiment_revision_id=selected.global_domain_experiment_revision_id,
            payload=json.loads(selected.canonical_payload),
            members=next_members,
            expected_head_generation=payload.expected_state_head_generation,
            parent_revision_id=selected.id,
            idempotency_key=f"project-hub-edit:{payload.idempotency_key}",
        )
        await begin_immediate_molbio_write(molbio)
        metadata = await molbio.get(ProjectPlasmidMetadata, metadata_id)
        if metadata is None or metadata.molecular_revision_id != new_revision.id:
            raise RuntimeError("project metadata activation authority was lost")
        metadata.active_state_revision_id = next_revision.id
        metadata.updated_at = datetime.utcnow()
        await molbio.commit()
        await native.commit()
    except Exception as exc:
        await native.rollback()
        await _compensate_project_hub_molecular_edit(
            molbio, sequence_id=sequence_id, old_revision_id=expected_revision.id,
            old_snapshot=old_snapshot, metadata_id=metadata_id, new_revision_id=new_revision.id,
            prior_metadata=prior_metadata,
        )
        if isinstance(exc, NativeRevisionConflict):
            raise HTTPException(409, detail={"code": "stale_generation", "message": str(exc)}) from exc
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(500, detail={"code": "project_hub_edit_failed", "message": f"Edit info failed without visible partial success: {exc}"}) from exc

    return await project_hub(project_id, experiment_id, domain_id, next_revision.id, session, native, molbio)


@router.get(D + "/state-revisions")
async def state_revisions(project_id: str, experiment_id: str, domain_id: str, cursor: str | None = Query(default=None, max_length=1024), limit: int = Query(default=50, ge=1, le=100), session: AsyncSession = Depends(get_experiment_session), native: AsyncSession = Depends(get_molbio_ngs_session)) -> dict[str, Any]:
    try:
        await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
        scope = f"state-revisions:{project_id}:{domain_id}"
        rows = await _native_rows(native, MolBioNGSDomainStateRevision, domain_id=domain_id, identity="id", cursor=cursor, limit=limit, scope=scope)
        return _bounded_response({"schema": "bms.ngs-molbio.state-revision-list.v1", **_native_page(rows, scope=scope, limit=limit, identity="id", document=lambda row: {"state_revision_id": row.id, "revision_number": row.revision_number, "binding_revision_id": row.binding_revision_id, "payload_sha256": row.payload_sha256, "membership_graph_sha256": row.membership_graph_sha256, "reopen_uri": f"/molbio-ngs/domain-experiments/{domain_id}?state_revision_id={row.id}", "created_at": row.created_at})})
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.get(D + "/samples")
async def samples(project_id: str, experiment_id: str, domain_id: str, cursor: str | None = Query(default=None, max_length=1024), limit: int = Query(default=50, ge=1, le=100), session: AsyncSession = Depends(get_experiment_session), native: AsyncSession = Depends(get_molbio_ngs_session)) -> dict[str, Any]:
    try:
        await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
        scope = f"samples:{project_id}:{domain_id}"
        rows = await _native_rows(native, MolBioNGSSample, domain_id=domain_id, identity="id", cursor=cursor, limit=limit, scope=scope)
        return _bounded_response({"schema": "bms.ngs-molbio.sample-list.v1", **_native_page(rows, scope=scope, limit=limit, identity="id", document=lambda row: {"sample_id": row.id, "current_revision_id": row.current_revision_id, "head_generation": row.head_generation, "archived_at": row.archived_at, "reopen_uri": f"/molbio-ngs/domain-experiments/{domain_id}?sample_id={row.id}", "created_at": row.created_at, "updated_at": row.updated_at})})
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.get(D + "/references")
async def references(project_id: str, experiment_id: str, domain_id: str, cursor: str | None = Query(default=None, max_length=1024), limit: int = Query(default=50, ge=1, le=100), session: AsyncSession = Depends(get_experiment_session), native: AsyncSession = Depends(get_molbio_ngs_session)) -> dict[str, Any]:
    try:
        await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
        scope = f"references:{project_id}:{domain_id}"
        rows = await _native_rows(native, MolBioNGSReferenceResource, domain_id=domain_id, identity="id", cursor=cursor, limit=limit, scope=scope)
        return _bounded_response({"schema": "bms.ngs-molbio.reference-list.v1", **_native_page(rows, scope=scope, limit=limit, identity="id", document=lambda row: {"reference_id": row.id, "name": row.name, "current_revision_id": row.current_revision_id, "head_generation": row.head_generation, "archived_at": row.archived_at, "reopen_uri": f"/molbio-ngs/references/{row.id}", "created_at": row.created_at, "updated_at": row.updated_at})})
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.get(D + "/evidence")
async def evidence(project_id: str, experiment_id: str, domain_id: str, cursor: str | None = Query(default=None, max_length=1024), limit: int = Query(default=50, ge=1, le=100), session: AsyncSession = Depends(get_experiment_session), native: AsyncSession = Depends(get_molbio_ngs_session)) -> dict[str, Any]:
    try:
        await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
        scope = f"evidence:{project_id}:{domain_id}"
        rows = await _native_rows(native, MolBioNGSEvidenceAssessment, domain_id=domain_id, identity="evidence_id", cursor=cursor, limit=limit, scope=scope)
        return _bounded_response({"schema": "bms.ngs-molbio.evidence-list.v1", **_native_page(rows, scope=scope, limit=limit, identity="evidence_id", document=lambda row: {"evidence_id": row.evidence_id, "state_revision_id": row.state_revision_id, "sample_revision_id": row.sample_revision_id, "wrapper_sha256": row.wrapper_sha256, "reopen_uri": f"/molbio-ngs/domain-experiments/{domain_id}/evidence/{row.evidence_id}", "created_at": row.created_at})})
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


async def _receipt_collection(session: AsyncSession, *, project_id: str, domain_id: str, kinds: set[str], cursor: str | None, limit: int, scope: str) -> dict[str, Any]:
    anchor = decode_cursor(cursor, scope=scope, limit=limit)
    statement = select(ExperimentExternalEntityReceipt).join(ExperimentLineageEdge, ExperimentLineageEdge.target_resource_id == ExperimentExternalEntityReceipt.id).where(ExperimentExternalEntityReceipt.workspace_id == project_id, ExperimentLineageEdge.workspace_id == project_id, ExperimentLineageEdge.source_resource_id == domain_id, ExperimentExternalEntityReceipt.entity_kind.in_(kinds)).order_by(ExperimentExternalEntityReceipt.created_at.desc(), ExperimentExternalEntityReceipt.id.desc()).limit(limit + 1)
    if anchor:
        statement = statement.where(or_(ExperimentExternalEntityReceipt.created_at < anchor[0], (ExperimentExternalEntityReceipt.created_at == anchor[0]) & (ExperimentExternalEntityReceipt.id < anchor[1])))
    rows = list((await session.scalars(statement)).unique().all())
    return _native_page(rows, scope=scope, limit=limit, identity="id", document=lambda row: {"receipt_id": row.id, "entity_kind": row.entity_kind, "entity_id": row.entity_id, "native_revision_or_generation": row.generation_or_revision, "content_digest": row.content_digest, "availability": row.availability, "reopen_uri": json.loads(row.acknowledgement_json or "{}").get("reopen_uri"), "created_at": row.created_at})


@router.get(D + "/operations")
async def operations(project_id: str, experiment_id: str, domain_id: str, cursor: str | None = Query(default=None, max_length=1024), limit: int = Query(default=50, ge=1, le=100), session: AsyncSession = Depends(get_experiment_session)) -> dict[str, Any]:
    try:
        await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
        return _bounded_response({"schema": "bms.ngs-molbio.operation-list.v1", **await _receipt_collection(session, project_id=project_id, domain_id=domain_id, kinds={"molecular_operation"}, cursor=cursor, limit=limit, scope=f"operations:{project_id}:{domain_id}")})
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.get(D + "/results")
async def results(project_id: str, experiment_id: str, domain_id: str, cursor: str | None = Query(default=None, max_length=1024), limit: int = Query(default=50, ge=1, le=100), session: AsyncSession = Depends(get_experiment_session)) -> dict[str, Any]:
    try:
        await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
        kinds = {"ngs_result_manifest", "sequence_qc_job", "ngs_analysis_job", "ngs_alignment_job"}
        return _bounded_response({"schema": "bms.ngs-molbio.result-list.v1", **await _receipt_collection(session, project_id=project_id, domain_id=domain_id, kinds=kinds, cursor=cursor, limit=limit, scope=f"results:{project_id}:{domain_id}")})
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.get(D + "/comparisons")
async def comparisons(project_id: str, experiment_id: str, domain_id: str, cursor: str | None = Query(default=None, max_length=1024), limit: int = Query(default=50, ge=1, le=100), session: AsyncSession = Depends(get_experiment_session)) -> dict[str, Any]:
    try:
        await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
        scope = f"comparisons:{project_id}:{domain_id}"
        anchor = decode_cursor(cursor, scope=scope, limit=limit)
        statement = select(ExperimentDomainAdapterReceipt).where(ExperimentDomainAdapterReceipt.workspace_id == project_id, ExperimentDomainAdapterReceipt.domain_experiment_id == domain_id, ExperimentDomainAdapterReceipt.operation_kind == "create_comparison").order_by(ExperimentDomainAdapterReceipt.created_at.desc(), ExperimentDomainAdapterReceipt.resource_id.desc()).limit(limit + 1)
        if anchor:
            statement = statement.where(or_(ExperimentDomainAdapterReceipt.created_at < anchor[0], (ExperimentDomainAdapterReceipt.created_at == anchor[0]) & (ExperimentDomainAdapterReceipt.resource_id < anchor[1])))
        rows = list((await session.scalars(statement)).all())
        return _bounded_response({"schema": "bms.ngs-molbio.comparison-list.v1", **_native_page(rows, scope=scope, limit=limit, identity="resource_id", document=lambda row: {"comparison_receipt_id": row.resource_id, "adapter_id": row.adapter_id, "normalized_request_sha256": row.normalized_request_sha256, "receipt": json.loads(row.receipt_json), "created_at": row.created_at})})
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.get(D + "/run-groups")
async def run_groups(project_id: str, experiment_id: str, domain_id: str, cursor: str | None = Query(default=None, max_length=1024), limit: int = Query(default=50, ge=1, le=100), session: AsyncSession = Depends(get_experiment_session)) -> dict[str, Any]:
    try:
        await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
        scope = f"run-groups:{project_id}:{domain_id}"
        anchor = decode_cursor(cursor, scope=scope, limit=limit)
        statement = select(ExperimentRunGroup).join(ExperimentWorkflowRun, ExperimentWorkflowRun.run_group_id == ExperimentRunGroup.resource_id).join(ExperimentWorkflowPreparation, ExperimentWorkflowPreparation.resource_id == ExperimentWorkflowRun.preparation_id).join(ExperimentRevision, ExperimentRevision.resource_id == ExperimentWorkflowPreparation.workflow_revision_id).join(ExperimentAggregateHead, ExperimentAggregateHead.aggregate_id == ExperimentRevision.subject_id).where(ExperimentRunGroup.workspace_id == project_id, ExperimentWorkflowRun.workspace_id == project_id, ExperimentWorkflowPreparation.workspace_id == project_id, ExperimentAggregateHead.workspace_id == project_id, ExperimentAggregateHead.aggregate_kind == "workflow", ExperimentAggregateHead.parent_id == domain_id).order_by(ExperimentRunGroup.created_at.desc(), ExperimentRunGroup.resource_id.desc()).limit(limit + 1)
        if anchor:
            statement = statement.where(or_(ExperimentRunGroup.created_at < anchor[0], (ExperimentRunGroup.created_at == anchor[0]) & (ExperimentRunGroup.resource_id < anchor[1])))
        rows = list((await session.scalars(statement)).unique().all())
        return _bounded_response({"schema": "bms.run-group-list.v1", **_native_page(rows, scope=scope, limit=limit, identity="resource_id", document=lambda row: {"run_group_id": row.resource_id, "state": row.state, "generation": row.generation, "request_sha256": row.request_sha256, "created_at": row.created_at, "updated_at": row.updated_at})})
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


async def _attempt_in_domain(
    session: AsyncSession,
    *,
    project_id: str,
    domain_id: str,
    attempt_id: str,
) -> ExperimentRunAttempt:
    attempt = await session.get(ExperimentRunAttempt, attempt_id)
    workflow_run = await session.get(
        ExperimentWorkflowRun,
        attempt.workflow_run_id if attempt else "",
    )
    run_group = await session.get(
        ExperimentRunGroup,
        workflow_run.run_group_id if workflow_run else "",
    )
    preparation = await session.get(
        ExperimentWorkflowPreparation,
        attempt.preparation_id if attempt else "",
    )
    revision = await session.get(
        ExperimentRevision,
        preparation.workflow_revision_id if preparation else "",
    )
    plan = await session.get(
        ExperimentAggregateHead,
        revision.subject_id if revision else "",
    )
    if (
        attempt is None
        or attempt.workspace_id != project_id
        or workflow_run is None
        or workflow_run.workspace_id != project_id
        or workflow_run.preparation_id != attempt.preparation_id
        or run_group is None
        or run_group.workspace_id != project_id
        or preparation is None
        or preparation.workspace_id != project_id
        or revision is None
        or plan is None
        or plan.workspace_id != project_id
        or plan.aggregate_kind != "workflow"
        or plan.parent_id != domain_id
    ):
        raise NotFound("attempt not found in Domain")
    return attempt


@router.get(D + "/attempts/{attempt_id}")
async def attempt_detail(project_id: str, experiment_id: str, domain_id: str, attempt_id: str, session: AsyncSession = Depends(get_experiment_session)) -> dict[str, Any]:
    try:
        await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
        attempt = await _attempt_in_domain(
            session,
            project_id=project_id,
            domain_id=domain_id,
            attempt_id=attempt_id,
        )
        return _bounded_response({"schema": "bms.run-attempt-detail.v1", "attempt_id": attempt.resource_id, "workflow_run_id": attempt.workflow_run_id, "preparation_id": attempt.preparation_id, "attempt_number": attempt.attempt_number, "canonical_job_id": attempt.scheduler_job_id, "state": attempt.state, "runtime_identity": json.loads(attempt.runtime_identity_json) if attempt.runtime_identity_json else None, "terminal_receipt": json.loads(attempt.terminal_receipt_json) if attempt.terminal_receipt_json else None, "created_at": attempt.created_at})
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.get(D + "/attempts/{attempt_id}/logs")
async def attempt_logs(
    project_id: str,
    experiment_id: str,
    domain_id: str,
    attempt_id: str,
    cursor: str | None = Query(default=None, max_length=1024),
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
        await _attempt_in_domain(
            session,
            project_id=project_id,
            domain_id=domain_id,
            attempt_id=attempt_id,
        )
        scope = f"attempt-logs:{project_id}:{domain_id}:{attempt_id}"
        anchor = decode_cursor(cursor, scope=scope, limit=limit)
        statement = (
            select(ExperimentLogChunk, ExperimentLogStream)
            .join(ExperimentLogStream, ExperimentLogStream.resource_id == ExperimentLogChunk.stream_id)
            .where(ExperimentLogStream.attempt_id == attempt_id)
            .order_by(
                ExperimentLogChunk.created_at.desc(),
                ExperimentLogChunk.stream_id.desc(),
                ExperimentLogChunk.sequence_number.desc(),
            )
            .limit(limit + 1)
        )
        if anchor:
            stable_stream, separator, stable_sequence = anchor[1].rpartition(":")
            if not separator or not stable_sequence.isdigit():
                raise ValidationFailure("cursor stable identity is invalid")
            sequence = int(stable_sequence)
            statement = statement.where(
                or_(
                    ExperimentLogChunk.created_at < anchor[0],
                    (ExperimentLogChunk.created_at == anchor[0])
                    & or_(
                        ExperimentLogChunk.stream_id < stable_stream,
                        (ExperimentLogChunk.stream_id == stable_stream)
                        & (ExperimentLogChunk.sequence_number < sequence),
                    ),
                )
            )
        rows = list((await session.execute(statement)).all())
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            final_chunk = page[-1][0]
            next_cursor = encode_cursor(
                scope=scope,
                created_at=final_chunk.created_at,
                stable_id=f"{final_chunk.stream_id}:{final_chunk.sequence_number}",
                limit=limit,
            )
        return _bounded_response(
            {
                "schema": "bms.attempt-log-page.v1",
                "attempt_id": attempt_id,
                "items": [
                    {
                        "stream_id": chunk.stream_id,
                        "stream_name": stream.stream_name,
                        "sequence": chunk.sequence_number,
                        "content": chunk.content_text,
                        "content_sha256": chunk.content_sha256,
                        "stream_state": stream.state,
                        "created_at": chunk.created_at,
                    }
                    for chunk, stream in page
                ],
                "next_cursor": next_cursor,
                "has_more": next_cursor is not None,
            },
        )
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.get(D + "/attempts/{attempt_id}/validations")
async def attempt_validations(
    project_id: str,
    experiment_id: str,
    domain_id: str,
    attempt_id: str,
    cursor: str | None = Query(default=None, max_length=1024),
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
        await _attempt_in_domain(
            session,
            project_id=project_id,
            domain_id=domain_id,
            attempt_id=attempt_id,
        )
        scope = f"attempt-validations:{project_id}:{domain_id}:{attempt_id}"
        anchor = decode_cursor(cursor, scope=scope, limit=limit)
        statement = (
            select(ExperimentValidation)
            .where(ExperimentValidation.subject_resource_id == attempt_id)
            .order_by(ExperimentValidation.created_at.desc(), ExperimentValidation.resource_id.desc())
            .limit(limit + 1)
        )
        if anchor:
            statement = statement.where(
                or_(
                    ExperimentValidation.created_at < anchor[0],
                    (ExperimentValidation.created_at == anchor[0])
                    & (ExperimentValidation.resource_id < anchor[1]),
                )
            )
        rows = list((await session.scalars(statement)).all())
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            final = page[-1]
            next_cursor = encode_cursor(
                scope=scope,
                created_at=final.created_at,
                stable_id=final.resource_id,
                limit=limit,
            )
        return _bounded_response(
            {
                "schema": "bms.attempt-validation-list.v1",
                "attempt_id": attempt_id,
                "items": [
                    {
                        "validation_id": row.resource_id,
                        "validator_name": row.validator_name,
                        "validator_version": row.validator_version,
                        "input_graph_sha256": row.input_graph_sha256,
                        "outcome": row.outcome,
                        "receipt_sha256": row.receipt_sha256,
                        "detail_uri": f"{D.replace('{project_id}', project_id).replace('{experiment_id}', experiment_id).replace('{domain_id}', domain_id)}/attempts/{attempt_id}/validations/{row.resource_id}",
                        "created_at": row.created_at,
                    }
                    for row in page
                ],
                "next_cursor": next_cursor,
                "has_more": next_cursor is not None,
            }
        )
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.get(D + "/attempts/{attempt_id}/validations/{validation_id}")
async def attempt_validation_detail(
    project_id: str,
    experiment_id: str,
    domain_id: str,
    attempt_id: str,
    validation_id: str,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
        await _attempt_in_domain(
            session,
            project_id=project_id,
            domain_id=domain_id,
            attempt_id=attempt_id,
        )
        validation = await session.get(ExperimentValidation, validation_id)
        if validation is None or validation.subject_resource_id != attempt_id:
            raise NotFound("validation not found for attempt")
        return _bounded_response(
            {
                "schema": "bms.attempt-validation-detail.v1",
                "attempt_id": attempt_id,
                "validation_id": validation.resource_id,
                "validator_name": validation.validator_name,
                "validator_version": validation.validator_version,
                "input_graph_sha256": validation.input_graph_sha256,
                "outcome": validation.outcome,
                "receipt": json.loads(validation.receipt_json),
                "receipt_sha256": validation.receipt_sha256,
                "created_at": validation.created_at,
            },
        )
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.get(D + "/audit")
async def audit(project_id: str, experiment_id: str, domain_id: str, cursor: str | None = Query(default=None, max_length=1024), limit: int = Query(default=50, ge=1, le=100), session: AsyncSession = Depends(get_experiment_session)) -> dict[str, Any]:
    try:
        await require_domain_hierarchy(session, project_id, experiment_id, domain_id)
        scope = f"audit:{project_id}:{domain_id}"
        anchor = decode_cursor(cursor, scope=scope, limit=limit)
        child_ids = select(ExperimentAggregateHead.aggregate_id).where(
            ExperimentAggregateHead.workspace_id == project_id,
            ExperimentAggregateHead.parent_id == domain_id,
        )
        related_targets = select(ExperimentLineageEdge.target_resource_id).where(
            ExperimentLineageEdge.workspace_id == project_id,
            or_(
                ExperimentLineageEdge.source_resource_id == domain_id,
                ExperimentLineageEdge.source_resource_id.in_(child_ids),
            ),
        )
        statement = select(ExperimentAuditEvent).where(
            ExperimentAuditEvent.workspace_id == project_id,
            or_(
                ExperimentAuditEvent.resource_id == domain_id,
                ExperimentAuditEvent.resource_id.in_(child_ids),
                ExperimentAuditEvent.resource_id.in_(related_targets),
            ),
        ).order_by(
            ExperimentAuditEvent.created_at.desc(), ExperimentAuditEvent.id.desc()
        ).limit(limit + 1)
        if anchor:
            statement = statement.where(or_(ExperimentAuditEvent.created_at < anchor[0], (ExperimentAuditEvent.created_at == anchor[0]) & (ExperimentAuditEvent.id < anchor[1])))
        rows = list((await session.scalars(statement)).all())
        return _bounded_response({"schema": "bms.retained-audit-list.v1", **_native_page(rows, scope=scope, limit=limit, identity="id", document=lambda row: {"audit_id": row.id, "resource_id": row.resource_id, "event_type": row.event_type, "generation": row.generation, "payload": json.loads(row.payload_json), "created_at": row.created_at})})
    except ExperimentServiceError as exc:
        raise _error(exc) from exc


@router.get("/api/operations/ngs-molbio/runtime-implementation")
def runtime_implementation_status(request: Request) -> dict[str, Any]:
    _operator_principal(request)
    try:
        return _bounded_response(runtime_implementation_record())
    except NgsMolBioRuntimeAuthorityError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "runtime_implementation_authority_unavailable",
                "message": str(exc),
            },
        ) from exc


@router.get("/api/operations/ngs-molbio/status")
async def status_surface(
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    _operator_principal(request)
    return _bounded_response(await operational_status(session))
