"""Project Manager adapter, attachment, read-model, and surface routes."""
from __future__ import annotations

import json
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job, get_session as get_core_session
from experiment_database import get_experiment_session
from experiment_models import (
    ExperimentAggregateHead,
    ExperimentAuditEvent,
    ExperimentLineageEdge,
    ExperimentLaunchContext,
    ExperimentResource,
    ExperimentRevision,
    ExperimentRunAttempt,
    ExperimentRunGroup,
    ExperimentRunGroupPreparation,
    ExperimentWorkflowPreparation,
    ExperimentWorkflowRevisionNode,
    ExperimentWorkflowRun,
)
from experiment_operations import register_external_entity_receipt
from experiment_services import (
    ExperimentServiceError,
    IdempotencyConflict,
    NotFound,
    RevisionConflict,
    ValidationFailure,
)
from services.global_experiments.adapters import AdapterError, registry
from services.global_experiments.launch_contexts import (
    LaunchContextError,
    claim_launch_context,
    consume_launch_context,
    context_document,
    create_launch_context,
    resolve_launch_context_for_display,
    validate_bound_job,
)
from services.global_experiments.read_models import build_project_manager_read_model
from services.global_experiments.receipts import attach_verified_entity
from services.global_experiments.result_surfaces import result_surface_for_receipt
from schemas import LaunchContextCreateRequest, LaunchContextResponse
from routers.experiment_workspaces import _require_mutation_owner


router = APIRouter(tags=["project-manager"])


class StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AttachRequest(StrictRequestModel):
    adapter_id: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    operation: Literal["attach_reference", "bind_input", "link_output", "attach_evidence"]
    role: Literal["references", "uses_input", "produced", "validated_by"]
    note: str | None = Field(default=None, max_length=2000)
    expected_head_generation: int = Field(ge=0)


class ReceiptIssueRequest(StrictRequestModel):
    project_id: str = Field(min_length=1)


class LaunchContextBindRequest(StrictRequestModel):
    job_id: str = Field(min_length=1)


def _bound_id(kind: str, launch_context_id: str) -> str:
    return f"{kind}-{uuid.uuid5(uuid.UUID('ef4f2c67-07d4-4b70-8a1f-e6339288dbd0'), kind + ':' + launch_context_id)}"


async def _project_bound_job(session: AsyncSession, context: ExperimentLaunchContext, job: Job, binding: dict[str, object]) -> dict[str, str]:
    run_id = _bound_id("workflow-run", context.launch_context_id)
    existing = await session.get(ExperimentWorkflowRun, run_id)
    if existing is not None:
        return {"workflow_run_id": existing.id, "run_group_id": existing.run_group_id}
    timestamp = datetime.now(timezone.utc).isoformat()
    workflow_id = context.workflow_id or _bound_id("workflow", context.launch_context_id)
    revision_id = context.workflow_revision_id or _bound_id("revision", context.launch_context_id)
    adapter_id = f"bms.core-job.{job.model_id}.adapter.v1"
    if context.workflow_id is None:
        projected_params = {**(job.params or {}), "workflow_adapter": adapter_id}
        payload = {"schema": "bms.workflow.generic.v1", "workflow_family": "typed_core_job", "contract_version": "1", "adapter_id": adapter_id, "nodes": [{"id": "bound-job", "kind": "bound_core_job", "required": True}], "edges": [], "parameters": {"canonical_job_id": job.id, "params_sha256": hashlib.sha256(json.dumps(projected_params, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}, "scheduler": {"name": job.name, "model_id": job.model_id, "mode": job.mode, "params": projected_params}}
        canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        session.add_all([
            ExperimentResource(id=workflow_id, kind="workflow", workspace_id=context.project_id, lifecycle_owner_id=context.domain_experiment_id, created_at=timestamp),
            ExperimentResource(id=revision_id, kind="revision", workspace_id=context.project_id, lifecycle_owner_id=workflow_id, created_at=timestamp),
            ExperimentRevision(resource_id=revision_id, subject_id=workflow_id, revision_number=1, schema_name=payload["schema"], schema_version="1", canonical_payload=canonical_payload, payload_sha256=hashlib.sha256(canonical_payload.encode()).hexdigest(), dependency_graph_sha256=hashlib.sha256(b"[]").hexdigest(), provenance_json=json.dumps({"source": "launch_context_binding"}), created_at=timestamp),
        ])
        await session.flush()
        session.add_all([
            ExperimentAggregateHead(aggregate_id=workflow_id, aggregate_kind="workflow", workspace_id=context.project_id, parent_id=context.domain_experiment_id, current_revision_id=revision_id, head_generation=1, lifecycle_state="active", display_name=f"{job.name} plan", description="Server-projected typed Job plan", created_at=timestamp, updated_at=timestamp),
            ExperimentWorkflowRevisionNode(revision_id=revision_id, ordinal=0, node_id="bound-job", node_kind="bound_core_job", node_json=json.dumps(payload["nodes"][0], sort_keys=True, separators=(",", ":"))),
            ExperimentLineageEdge(id=_bound_id("lineage", context.launch_context_id), workspace_id=context.project_id, source_resource_id=context.domain_experiment_id, target_resource_id=workflow_id, edge_mode="owns", edge_key=f"owns:{context.domain_experiment_id}:{workflow_id}", metadata_json="{}", created_at=timestamp),
        ])
    preparation_id = _bound_id("preparation", context.launch_context_id)
    run_group_id = _bound_id("run-group", context.launch_context_id)
    attempt_id = _bound_id("attempt", context.launch_context_id)
    for resource_id, kind, owner in ((preparation_id, "workflow_preparation", workflow_id), (run_group_id, "run_group", workflow_id), (run_id, "workflow_run", workflow_id), (attempt_id, "run_attempt", run_id)):
        session.add(ExperimentResource(id=resource_id, kind=kind, workspace_id=context.project_id, lifecycle_owner_id=owner, created_at=timestamp))
    await session.flush()
    status_value = str(job.status).lower()
    run_state = "completed" if status_value in {"completed", "succeeded"} else "failed" if status_value == "failed" else "cancelled" if status_value in {"cancelled", "canceled"} else "running" if status_value == "running" else "dispatched"
    request_payload = {"canonical_job_id": job.id, "model_id": job.model_id, "mode": job.mode, "params_sha256": hashlib.sha256(json.dumps(job.params or {}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    request_json = json.dumps(request_payload, sort_keys=True, separators=(",", ":"))
    session.add_all([
        ExperimentWorkflowPreparation(resource_id=preparation_id, workspace_id=context.project_id, workflow_revision_id=revision_id, normalized_request_json=request_json, normalized_request_sha256=hashlib.sha256(request_json.encode()).hexdigest(), scheduler_payload_json=request_json, validation_status="valid", validation_receipt_json=json.dumps({"schema": "bms.bound-job-preparation.v1", "verified": True}), expected_cardinality=1, created_at=timestamp, prepared_at=timestamp),
        ExperimentRunGroup(resource_id=run_group_id, workspace_id=context.project_id, launch_idempotency_key=f"launch-context:{context.launch_context_id}", request_sha256=hashlib.sha256(request_json.encode()).hexdigest(), state=run_state, generation=1, created_at=timestamp, updated_at=timestamp),
    ])
    await session.flush()
    session.add_all([
        ExperimentRunGroupPreparation(run_group_id=run_group_id, preparation_id=preparation_id, ordinal=0),
        ExperimentWorkflowRun(resource_id=run_id, workspace_id=context.project_id, run_group_id=run_group_id, preparation_id=preparation_id, node_id="bound-job", requiredness="required", state=run_state, generation=1, created_at=timestamp),
    ])
    await session.flush()
    session.add_all([
        ExperimentRunAttempt(resource_id=attempt_id, workspace_id=context.project_id, workflow_run_id=run_id, attempt_number=1, scheduler_job_id=job.id, state=run_state, external_binding_receipt_json=json.dumps(binding, sort_keys=True, separators=(",", ":")), runtime_identity_json=json.dumps({"canonical_job_id": job.id, "model_id": job.model_id, "mode": job.mode}), created_at=timestamp),
        ExperimentLineageEdge(id=_bound_id("launched", context.launch_context_id), workspace_id=context.project_id, source_resource_id=workflow_id, target_resource_id=run_id, edge_mode="produces", edge_key=f"produces:{workflow_id}:{run_id}", metadata_json=json.dumps({"canonical_job_id": job.id}), created_at=timestamp),
        ExperimentAuditEvent(id=_bound_id("audit", context.launch_context_id), workspace_id=context.project_id, resource_id=run_id, event_type="launch_context_job_bound", generation=1, payload_json=json.dumps({"canonical_job_id": job.id, "launch_context_id": context.launch_context_id}), created_at=timestamp),
    ])
    await session.flush()
    return {"workflow_run_id": run_id, "run_group_id": run_group_id}


def _service_error(exc: ExperimentServiceError) -> HTTPException:
    message = str(exc)
    if isinstance(exc, NotFound):
        return HTTPException(404, detail={"code": "not_found", "message": message})
    if isinstance(exc, RevisionConflict):
        return HTTPException(409, detail={"code": "stale_generation", "message": message})
    if isinstance(exc, IdempotencyConflict):
        return HTTPException(409, detail={"code": "idempotency_conflict", "message": message})
    if isinstance(exc, ValidationFailure):
        return HTTPException(422, detail={"code": "validation_failed", "message": message})
    return HTTPException(400, detail={"code": "unsupported_operation", "message": message})


def _adapter_error(exc: AdapterError) -> HTTPException:
    status_by_code = {
        "unknown_adapter": 404,
        "entity_not_found": 404,
        "invalid_entity_id": 422,
        "invalid_limit": 422,
        "invalid_query": 422,
        "source_contract_invalid": 409,
        "source_contract_unavailable": 409,
        "source_artifact_unavailable": 409,
        "source_digest_mismatch": 409,
        "source_revision_unavailable": 503,
    }
    return HTTPException(
        status_by_code.get(exc.code, 422),
        detail={"code": exc.code, "message": str(exc)},
    )


def _launch_context_error(exc: LaunchContextError) -> HTTPException:
    return HTTPException(
        exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


@router.get("/api/domain-adapters")
async def list_domain_adapters() -> dict:
    return {"schema": "bms.global.adapter-registry.v1", "adapters": registry.list()}


@router.post(
    "/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/launch-contexts",
    response_model=LaunchContextResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue_launch_context(
    project_id: str,
    experiment_id: str,
    domain_id: str,
    payload: LaunchContextCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict:
    try:
        await _require_mutation_owner(request, session, resource_id=project_id)
        context = await create_launch_context(
            session,
            project_id=project_id,
            global_experiment_id=experiment_id,
            domain_experiment_id=domain_id,
            workflow_id=payload.workflow_id,
            workflow_revision_id=payload.workflow_revision_id,
            return_uri=payload.return_uri,
        )
        await session.commit()
        return context_document(context)
    except LaunchContextError as exc:
        await session.rollback()
        raise _launch_context_error(exc) from exc


@router.get("/api/launch-contexts/{launch_context_id}", response_model=LaunchContextResponse)
async def get_launch_context(
    launch_context_id: str,
    session: AsyncSession = Depends(get_experiment_session),
    core_session: AsyncSession = Depends(get_core_session),
) -> dict[str, object]:
    try:
        context = await resolve_launch_context_for_display(session, launch_context_id)
        document = context_document(context)
        if context.canonical_job_id is None:
            job_ids = list((await core_session.scalars(
                select(Job.id)
                .where(func.json_extract(Job.provenance, "$.launch_context_id") == launch_context_id)
                .limit(2)
            )).all())
            if len(job_ids) == 1:
                document["recovery_job_id"] = str(job_ids[0])
            elif len(job_ids) > 1:
                raise LaunchContextError("launch_context_job_ambiguous", "Multiple Jobs claim this launch context.", status_code=409)
        return document
    except LaunchContextError as exc:
        raise _launch_context_error(exc) from exc


@router.post("/api/launch-contexts/{launch_context_id}/bind")
async def bind_launch_context_to_job(
    launch_context_id: str,
    payload: LaunchContextBindRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
    core_session: AsyncSession = Depends(get_core_session),
) -> dict:
    try:
        context = await resolve_launch_context_for_display(session, launch_context_id)
        await _require_mutation_owner(request, session, resource_id=context.project_id)
        job = await core_session.get(Job, payload.job_id)
        if job is None:
            raise LaunchContextError("launch_context_job_unavailable", "Bound Job does not exist.", status_code=409)
        await validate_bound_job(session, context, job)
        job_provenance = dict(job.provenance or {})
        if job_provenance.get("launch_context_id") != launch_context_id:
            raise LaunchContextError(
                "launch_context_job_mismatch",
                "Job was not created by this launch context.",
                status_code=409,
            )
        if context.state == "consumed":
            if context.canonical_job_id != payload.job_id or not context.binding_receipt_json:
                raise LaunchContextError("launch_context_consumed", "Launch context was consumed by another Job.", status_code=409)
            binding = json.loads(context.binding_receipt_json)
        else:
            if context.state == "claimed":
                if not context.claim_token:
                    raise LaunchContextError("launch_context_claim_invalid", "Claimed launch context has no token.", status_code=409)
                claim_token = context.claim_token
            else:
                context, claim_token = await claim_launch_context(session, launch_context_id)
            context, binding = await consume_launch_context(
                session,
                launch_context_id=launch_context_id,
                claim_token=claim_token,
                canonical_job_id=payload.job_id,
                canonical_batch_id=None,
            )
        projection = await _project_bound_job(session, context, job, binding)
        await session.commit()
        job_provenance["global_experiment_binding"] = binding
        job.provenance = job_provenance
        await core_session.commit()
        return {
            "schema": "bms.launch-context-binding-response.v1",
            "launch_context_id": launch_context_id,
            "job_id": payload.job_id,
            "binding_receipt": binding,
            "project_projection": projection,
            "return_uri": context.return_uri,
        }
    except (LaunchContextError, json.JSONDecodeError) as exc:
        await session.rollback()
        await core_session.rollback()
        if isinstance(exc, LaunchContextError):
            raise _launch_context_error(exc) from exc
        raise HTTPException(status_code=500, detail="stored launch binding receipt is malformed") from exc


@router.get("/api/domain-adapters/{adapter_id}/entities/search")
async def search_adapter_entities(
    adapter_id: str,
    q: str = Query(default="", max_length=255),
    limit: int = Query(default=25, ge=1, le=100),
    core_session: AsyncSession = Depends(get_core_session),
) -> dict:
    try:
        adapter = registry.get(adapter_id)
        projections = await adapter.search(core_session, query=q, limit=limit)
        items: list[dict] = []
        for projection in projections:
            item = {"adapter_id": adapter.adapter_id, **projection.as_dict()}
            try:
                verification = await adapter.verify(core_session, projection.entity_id)
                item.update(
                    attachable=True,
                    reason=None,
                    reopen_uri=str(verification["reopen_uri"]),
                )
            except AdapterError as verification_error:
                item.update(
                    attachable=False,
                    reason=verification_error.message,
                    reopen_uri="",
                )
            items.append(item)
        return {
            "schema": "bms.global.adapter-search.v1",
            "adapter_id": adapter.adapter_id,
            "adapter_version": adapter.adapter_version,
            "items": items,
            "next_cursor": None,
        }
    except AdapterError as exc:
        raise _adapter_error(exc) from exc


@router.post(
    "/api/domain-adapters/{adapter_id}/entities/{entity_id}/receipt",
    status_code=status.HTTP_201_CREATED,
)
async def issue_adapter_receipt(
    adapter_id: str,
    entity_id: str,
    payload: ReceiptIssueRequest,
    request: Request,
    experiment_session: AsyncSession = Depends(get_experiment_session),
    core_session: AsyncSession = Depends(get_core_session),
) -> dict:
    try:
        await _require_mutation_owner(request, experiment_session, resource_id=payload.project_id)
        adapter = registry.get(adapter_id)
        receipt = await adapter.verify(core_session, entity_id)
        receipt["verified_at"] = datetime.now(timezone.utc).isoformat()
        digest = receipt.get("content_digest") or receipt.get("contract_digest")
        if not isinstance(digest, str):
            raise AdapterError("source_contract_invalid", "verified source receipt has no digest")
        row = await register_external_entity_receipt(
            experiment_session,
            workspace_id=payload.project_id,
            store_id=str(receipt["store_id"]),
            entity_kind=str(receipt["entity_kind"]),
            entity_id=str(receipt["entity_id"]),
            generation_or_revision=str(receipt.get("entity_revision_id") or digest),
            content_digest=digest,
            availability="available",
            acknowledgement=receipt,
            verification_authority=adapter.adapter_id,
        )
        await experiment_session.commit()
        return {"receipt_id": row.id, "receipt": receipt}
    except AdapterError as exc:
        await experiment_session.rollback()
        raise _adapter_error(exc) from exc
    except ExperimentServiceError as exc:
        await experiment_session.rollback()
        raise _service_error(exc) from exc


@router.post(
    "/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/attach",
    status_code=status.HTTP_201_CREATED,
)
async def attach_domain_entity(
    project_id: str,
    experiment_id: str,
    domain_id: str,
    payload: AttachRequest,
    request: Request,
    experiment_session: AsyncSession = Depends(get_experiment_session),
    core_session: AsyncSession = Depends(get_core_session),
) -> dict:
    try:
        await _require_mutation_owner(request, experiment_session, resource_id=project_id)
        receipt = await attach_verified_entity(
            experiment_session,
            core_session,
            project_id=project_id,
            global_experiment_id=experiment_id,
            domain_experiment_id=domain_id,
            adapter_id=payload.adapter_id,
            entity_id=payload.entity_id,
            operation=payload.operation,
            role=payload.role,
            note=payload.note,
            expected_head_generation=payload.expected_head_generation,
        )
        await experiment_session.commit()
        return receipt
    except AdapterError as exc:
        await experiment_session.rollback()
        raise _adapter_error(exc) from exc
    except ExperimentServiceError as exc:
        await experiment_session.rollback()
        raise _service_error(exc) from exc


@router.get("/api/projects/{project_id}/summary")
async def project_manager_summary(
    project_id: str,
    focus_id: str | None = Query(default=None),
    selected_node_key: str | None = Query(default=None),
    map_cursor: str | None = Query(default=None, max_length=512),
    run_cursor: str | None = Query(default=None, max_length=512),
    result_cursor: str | None = Query(default=None, max_length=512),
    lineage_cursor: str | None = Query(default=None, max_length=512),
    note_cursor: str | None = Query(default=None, max_length=512),
    decision_cursor: str | None = Query(default=None, max_length=512),
    dataset_cursor: str | None = Query(default=None, max_length=512),
    activity_cursor: str | None = Query(default=None, max_length=512),
    map_limit: int = Query(default=50, ge=1, le=100),
    run_limit: int = Query(default=25, ge=1, le=100),
    result_limit: int = Query(default=25, ge=1, le=100),
    lineage_limit: int = Query(default=25, ge=1, le=100),
    note_limit: int = Query(default=25, ge=1, le=100),
    decision_limit: int = Query(default=25, ge=1, le=100),
    dataset_limit: int = Query(default=25, ge=1, le=100),
    activity_limit: int = Query(default=25, ge=1, le=100),
    session: AsyncSession = Depends(get_experiment_session),
) -> dict:
    try:
        return await build_project_manager_read_model(
            session,
            project_id=project_id,
            focus_id=focus_id,
            selected_node_key=selected_node_key,
            map_cursor=map_cursor,
            run_cursor=run_cursor,
            result_cursor=result_cursor,
            lineage_cursor=lineage_cursor,
            note_cursor=note_cursor,
            decision_cursor=decision_cursor,
            dataset_cursor=dataset_cursor,
            activity_cursor=activity_cursor,
            map_limit=map_limit,
            run_limit=run_limit,
            result_limit=result_limit,
            lineage_limit=lineage_limit,
            note_limit=note_limit,
            decision_limit=decision_limit,
            dataset_limit=dataset_limit,
            activity_limit=activity_limit,
        )
    except ExperimentServiceError as exc:
        raise _service_error(exc) from exc


@router.get("/api/projects/{project_id}/receipts/{receipt_id}/surface")
async def project_receipt_surface(
    project_id: str,
    receipt_id: str,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict:
    try:
        return await result_surface_for_receipt(session, project_id=project_id, receipt_id=receipt_id)
    except ExperimentServiceError as exc:
        raise _service_error(exc) from exc


__all__ = ["router"]
