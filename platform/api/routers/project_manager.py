"""Project Manager adapter, attachment, read-model, and surface routes."""
from __future__ import annotations

import base64
import binascii
import json
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job, get_session as get_core_session
from experiment_database import get_experiment_session
from molbio_ngs_database import get_molbio_ngs_session
from molbio_ngs_models import MolBioNGSDomainState, MolBioNGSGlobalBinding
from experiment_models import (
    ExperimentAggregateHead,
    ExperimentAuditEvent,
    ExperimentDispatchOutbox,
    ExperimentIdempotencyClaim,
    ExperimentLineageEdge,
    ExperimentLaunchContext,
    ExperimentResource,
    ExperimentRevision,
    ExperimentRunAttempt,
    ExperimentRunGroup,
    ExperimentRunGroupPreparation,
    ExperimentWorkflowPreparation,
    ExperimentWorkflowPlanAuthority,
    ExperimentWorkflowDraft,
    ExperimentWorkflowRevisionNode,
    ExperimentWorkflowRun,
)
from experiment_operations import register_external_entity_receipt
from experiment_services import (
    add_audit_event,
    canonical_json,
    ExperimentServiceError,
    IdempotencyConflict,
    NotFound,
    RevisionConflict,
    ValidationFailure,
    create_run_group,
    create_workflow,
    load_workflow_plan_authority,
    new_id,
    persist_workflow_plan_authority,
    prepare_workflow,
    derive_run_group_state,
    public_preparation_scheduler,
    public_workflow_payload,
    resubmit_run_group,
    retry_failed_run_group,
    save_workflow_draft,
    save_workflow_revision,
    validate_preparation_authority,
    validate_workflow_payload_for_plan,
    workflow_plan_capability_contract,
)
from services.global_experiments.adapters import AdapterError, registry
from services.global_experiments.launch_contexts import (
    LaunchContextError,
    claim_launch_context,
    consume_launch_context,
    context_document,
    create_launch_context,
    create_prepared_launch_context,
    publish_launch_context_binding,
    resolve_launch_context_for_display,
    validate_bound_job,
    workflow_pinned_gpu,
)
from services.global_experiments.read_models import build_project_manager_read_model
from services.global_experiments.receipts import attach_verified_entity
from services.global_experiments.result_surfaces import result_surface_for_receipt
from services.ngs_molbio_connector import exact_local_launch_authority
from services.ngs_molbio_capabilities import NgsMolBioCapabilityError, capability_inventory
from services.protein_project_capabilities import protein_capability_inventory
from services.ngs_molbio_n5 import (
    ResourceAdmissionDenied,
    persist_admission_refusal,
    reserve_run_group,
)
from services.ngs_molbio_run_control import (
    command_document,
    process_run_control_command,
    request_run_group_cancellation,
    require_launch_not_fenced,
)
from services.rfd3_local_redesign import project_local_redesign_scheduler_params
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


class PlanCreateRequest(StrictRequestModel):
    name: str = Field(min_length=1, max_length=255)
    capability_id: str = Field(min_length=1, max_length=255)
    expected_domain_revision_id: str = Field(min_length=1, max_length=128)


class DraftReplaceRequest(StrictRequestModel):
    expected_draft_generation: int = Field(ge=0)
    payload: dict[str, Any]


class RevisionPublishRequest(StrictRequestModel):
    expected_head_generation: int = Field(ge=0)
    expected_draft_generation: int = Field(ge=0)
    change_summary: str = Field(min_length=1, max_length=1024)


class PreparationRequest(StrictRequestModel):
    input_dataset_revision_ids: list[str] = Field(max_length=128)


class PreparedHandoffRequest(StrictRequestModel):
    return_uri: str = Field(min_length=1, max_length=1000)


class PreparationLaunch(StrictRequestModel):
    preparation_id: str = Field(min_length=1, max_length=128)
    launch_context_id: str | None = Field(default=None, min_length=1, max_length=128)


class RunGroupLaunchRequest(StrictRequestModel):
    preparation_launches: list[PreparationLaunch] = Field(min_length=1, max_length=128)


class RetryReplacement(StrictRequestModel):
    run_id: str = Field(min_length=1, max_length=128)
    preparation_id: str = Field(min_length=1, max_length=128)
    launch_context_id: str | None = Field(default=None, min_length=1, max_length=128)


class RunGroupRetryRequest(StrictRequestModel):
    expected_run_group_generation: int = Field(ge=0)
    replacements: list[RetryReplacement] = Field(min_length=1, max_length=128)


class RunGroupResubmitRequest(StrictRequestModel):
    expected_run_group_generation: int = Field(ge=0)
    preparation_launches: list[PreparationLaunch] = Field(min_length=1, max_length=128)


class RunCloneRequest(StrictRequestModel):
    schema_id: Literal["bms.run-clone-request.v1"] = Field(
        default="bms.run-clone-request.v1",
        alias="schema",
    )
    source_run_id: str = Field(min_length=1, max_length=128)
    source_attempt_id: str = Field(min_length=1, max_length=128)
    new_workflow_name: str = Field(min_length=1, max_length=255)
    change_summary: str = Field(min_length=1, max_length=1024)
    expected_domain_revision_id: str = Field(min_length=1, max_length=128)
    expected_run_group_generation: int = Field(ge=0)
    idempotency_key: str = Field(min_length=16, max_length=128)


class RunGroupCancelRequest(StrictRequestModel):
    expected_run_group_generation: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1024)


def _bound_id(kind: str, launch_context_id: str) -> str:
    return f"{kind}-{uuid.uuid5(uuid.UUID('ef4f2c67-07d4-4b70-8a1f-e6339288dbd0'), kind + ':' + launch_context_id)}"


async def _project_bound_job(
    session: AsyncSession,
    core_session: AsyncSession,
    context: ExperimentLaunchContext,
    job: Job,
    binding: dict[str, object],
) -> dict[str, str]:
    if context.contract_version != "2":
        raise LaunchContextError(
            "launch_context_version_read_only",
            "Historical v1 launch contexts are display-only and cannot be projected.",
            status_code=409,
        )
    if context.contract_version == "2" and context.run_attempt_id:
        attempt = await session.get(ExperimentRunAttempt, context.run_attempt_id)
        run = await session.get(ExperimentWorkflowRun, attempt.workflow_run_id if attempt else "")
        preparation = await session.get(
            ExperimentWorkflowPreparation, attempt.preparation_id if attempt else ""
        )
        if (
            attempt is None
            or run is None
            or preparation is None
            or attempt.scheduler_job_id != job.id
            or run.preparation_id != attempt.preparation_id
            or context.preparation_id != attempt.preparation_id
            or preparation.workspace_id != context.project_id
            or preparation.workflow_revision_id != context.workflow_revision_id
            or preparation.normalized_request_sha256 != context.normalized_request_sha256
            or preparation.validation_status != "valid"
        ):
            raise ValidationFailure("prepared launch context does not bind this canonical Job")
        await validate_preparation_authority(
            session, preparation, core_session=core_session
        )
        attempt.external_binding_receipt_json = json.dumps(binding, sort_keys=True, separators=(",", ":"))
        attempt.state = "dispatched"
        run.state = "dispatched"
        group = await session.get(ExperimentRunGroup, run.run_group_id)
        if group is not None:
            await require_launch_not_fenced(session, group.resource_id)
            await session.flush()
            group.state = await derive_run_group_state(session, run.run_group_id)
            group.generation += 1
            group.updated_at = datetime.now(timezone.utc).isoformat()
        return {"workflow_run_id": run.resource_id, "run_group_id": run.run_group_id}
    run_id = _bound_id("workflow-run", context.launch_context_id)
    workflow_id = context.workflow_id or _bound_id("workflow", context.launch_context_id)
    revision_id = context.workflow_revision_id or _bound_id("revision", context.launch_context_id)
    is_native_rfd3 = job.model_id == "protein_local_redesign" and job.mode == "local_redesign"
    native_preparation = None
    if is_native_rfd3:
        if not context.preparation_id:
            raise ValidationFailure("native RFD3 launch context has no immutable preparation")
        native_preparation = await session.get(
            ExperimentWorkflowPreparation, context.preparation_id
        )
        if (
            native_preparation is None
            or native_preparation.workspace_id != context.project_id
            or native_preparation.workflow_revision_id != context.workflow_revision_id
            or native_preparation.normalized_request_sha256 != context.normalized_request_sha256
            or native_preparation.validation_status != "valid"
        ):
            raise ValidationFailure("native RFD3 launch context preparation authority is invalid")
        await validate_preparation_authority(
            session, native_preparation, core_session=core_session
        )
    existing = await session.get(ExperimentWorkflowRun, run_id)
    if existing is not None:
        return {"workflow_run_id": existing.resource_id, "run_group_id": existing.run_group_id}
    timestamp = datetime.now(timezone.utc).isoformat()
    adapter_id = f"bms.core-job.{job.model_id}.adapter.v1"
    if context.workflow_id is None:
        projected_params = (
            project_local_redesign_scheduler_params(job.params or {})
            if job.model_id == "protein_local_redesign"
            else {**(job.params or {}), "workflow_adapter": adapter_id}
        )
        scheduler: dict[str, object] = {
            "name": job.name,
            "model_id": job.model_id,
            "mode": job.mode,
            "params": projected_params,
        }
        if job.model_id == "protein_local_redesign" and job.pinned_gpu is not None:
            scheduler["resources"] = {"pinned_gpu": job.pinned_gpu}
        payload = {"schema": "bms.workflow.generic.v1", "workflow_family": "typed_core_job", "contract_version": "1", "adapter_id": adapter_id, "nodes": [{"id": "bound-job", "kind": "bound_core_job", "required": True}], "edges": [], "parameters": {"canonical_job_id": job.id, "params_sha256": hashlib.sha256(json.dumps(projected_params, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}, "scheduler": scheduler}
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
    preparation_id = (
        native_preparation.resource_id
        if native_preparation is not None
        else _bound_id("preparation", context.launch_context_id)
    )
    run_group_id = _bound_id("run-group", context.launch_context_id)
    attempt_id = _bound_id("attempt", context.launch_context_id)
    projected_resources = [
        (run_group_id, "run_group", workflow_id),
        (run_id, "workflow_run", workflow_id),
        (attempt_id, "run_attempt", run_id),
    ]
    if native_preparation is None:
        projected_resources.insert(0, (preparation_id, "workflow_preparation", workflow_id))
    for resource_id, kind, owner in projected_resources:
        session.add(ExperimentResource(id=resource_id, kind=kind, workspace_id=context.project_id, lifecycle_owner_id=owner, created_at=timestamp))
    await session.flush()
    status_value = str(job.status).lower()
    run_state = "completed" if status_value in {"completed", "succeeded"} else "failed" if status_value == "failed" else "cancelled" if status_value in {"cancelled", "canceled"} else "running" if status_value == "running" else "dispatched"
    request_payload = {
        "canonical_job_id": job.id,
        "model_id": job.model_id,
        "mode": job.mode,
        "params_sha256": hashlib.sha256(
            json.dumps(job.params or {}, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    if job.model_id == "protein_local_redesign":
        request_payload["pinned_gpu"] = job.pinned_gpu
    request_json = json.dumps(request_payload, sort_keys=True, separators=(",", ":"))
    request_sha256 = (
        native_preparation.normalized_request_sha256
        if native_preparation is not None
        else hashlib.sha256(request_json.encode()).hexdigest()
    )
    if native_preparation is None:
        session.add(
            ExperimentWorkflowPreparation(resource_id=preparation_id, workspace_id=context.project_id, workflow_revision_id=revision_id, normalized_request_json=request_json, normalized_request_sha256=request_sha256, scheduler_payload_json=request_json, validation_status="valid", validation_receipt_json=json.dumps({"schema": "bms.bound-job-preparation.v1", "verified": True}), expected_cardinality=1, created_at=timestamp, prepared_at=timestamp)
        )
    session.add(
        ExperimentRunGroup(resource_id=run_group_id, workspace_id=context.project_id, launch_idempotency_key=f"launch-context:{context.launch_context_id}", request_sha256=request_sha256, state=run_state, generation=1, created_at=timestamp, updated_at=timestamp)
    )
    await session.flush()
    session.add_all([
        ExperimentRunGroupPreparation(run_group_id=run_group_id, preparation_id=preparation_id, ordinal=0),
        ExperimentWorkflowRun(resource_id=run_id, workspace_id=context.project_id, run_group_id=run_group_id, preparation_id=preparation_id, node_id="bound-job", requiredness="required", state=run_state, generation=1, created_at=timestamp),
    ])
    await session.flush()
    runtime_identity = {
        "canonical_job_id": job.id,
        "model_id": job.model_id,
        "mode": job.mode,
    }
    if job.model_id == "protein_local_redesign":
        runtime_identity["pinned_gpu"] = job.pinned_gpu
    session.add_all([
        ExperimentRunAttempt(resource_id=attempt_id, workspace_id=context.project_id, workflow_run_id=run_id, preparation_id=preparation_id, attempt_number=1, scheduler_job_id=job.id, state=run_state, external_binding_receipt_json=json.dumps(binding, sort_keys=True, separators=(",", ":")), runtime_identity_json=json.dumps(runtime_identity, sort_keys=True, separators=(",", ":")), created_at=timestamp),
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
        if message == "replacement_preparation_required":
            return HTTPException(409, detail={"code": "replacement_preparation_required", "message": message})
        if message == "canonical Job authority is not yet available for cancellation":
            return HTTPException(409, detail={"code": "canonical_job_authority_required", "message": message})
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


async def _launch_context_document(
    session: AsyncSession,
    context: ExperimentLaunchContext,
) -> dict[str, object]:
    document = context_document(context)
    if context.preparation_id:
        preparation = await session.get(ExperimentWorkflowPreparation, context.preparation_id)
        if preparation is not None:
            try:
                scheduler = json.loads(preparation.scheduler_payload_json)
            except (TypeError, ValueError) as exc:
                raise LaunchContextError(
                    "launch_context_preparation_invalid",
                    "Prepared scheduler payload is invalid.",
                    status_code=409,
                ) from exc
            if not isinstance(scheduler, dict):
                raise LaunchContextError(
                    "launch_context_preparation_invalid",
                    "Prepared scheduler payload is not an object.",
                    status_code=409,
                )
            document["pinned_scheduler"] = scheduler
    return document


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
        document = await _launch_context_document(session, context)
        document["pinned_gpu"] = await workflow_pinned_gpu(session, context)
        await session.commit()
        return document
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
        document = await _launch_context_document(session, context)
        document["pinned_gpu"] = await workflow_pinned_gpu(session, context)
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
        if context.contract_version != "2":
            raise LaunchContextError(
                "launch_context_version_read_only",
                "Historical v1 launch contexts are display-only and cannot be bound.",
                status_code=409,
            )
        await _require_mutation_owner(request, session, resource_id=context.project_id)
        if context.run_attempt_id:
            fenced_attempt = await session.get(ExperimentRunAttempt, context.run_attempt_id)
            fenced_run = await session.get(
                ExperimentWorkflowRun,
                fenced_attempt.workflow_run_id if fenced_attempt is not None else "",
            )
            if fenced_attempt is None or fenced_run is None:
                raise ValidationFailure("prepared launch context attempt authority is unavailable")
            await require_launch_not_fenced(session, fenced_run.run_group_id)
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
            if context.state == "reserved" and context.claim_token:
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
        projection = await _project_bound_job(session, core_session, context, job, binding)
        await session.commit()
        await publish_launch_context_binding(
            core_session,
            context=context,
            job=job,
            binding=binding,
        )
        return {
            "schema": "bms.launch-context-binding-response.v1",
            "launch_context_id": launch_context_id,
            "job_id": payload.job_id,
            "binding_receipt": binding,
            "project_projection": projection,
            "return_uri": context.return_uri,
        }
    except ExperimentServiceError as exc:
        await session.rollback()
        await core_session.rollback()
        raise _service_error(exc) from exc
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


def _idempotency_key(request: Request) -> str:
    value = request.headers.get("Idempotency-Key", "")
    if not value or len(value) > 255 or any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise HTTPException(422, detail={"code": "invalid_idempotency_key", "message": "Idempotency-Key must contain 1..255 visible ASCII characters."})
    return value


async def _domain_hierarchy(session: AsyncSession, project_id: str, experiment_id: str, domain_id: str) -> tuple[ExperimentAggregateHead, ExperimentAggregateHead, ExperimentAggregateHead]:
    project = await session.get(ExperimentAggregateHead, project_id)
    experiment = await session.get(ExperimentAggregateHead, experiment_id)
    domain = await session.get(ExperimentAggregateHead, domain_id)
    if project is None or project.aggregate_kind != "workspace":
        raise NotFound("Project not found")
    if experiment is None or experiment.aggregate_kind != "experiment" or experiment.workspace_id != project_id or experiment.parent_id != project_id:
        raise NotFound("Global Experiment not found in Project")
    if domain is None or domain.aggregate_kind != "domain_experiment" or domain.workspace_id != project_id or domain.parent_id != experiment_id:
        raise NotFound("Domain Experiment not found in Global Experiment")
    return project, experiment, domain


def _domain_capability_authority(
    revision: ExperimentRevision | None,
) -> tuple[str, dict[str, Any]]:
    if revision is None:
        raise ValidationFailure("current Domain revision authority is unavailable")
    try:
        payload = json.loads(revision.canonical_payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValidationFailure("current Domain revision authority is malformed") from exc
    if not isinstance(payload, dict) or payload.get("domain_kind") not in {"ngs_molbio", "protein_in_silico"}:
        raise ValidationFailure("current Domain kind has no capability authority")
    raw_domain_payload = payload.get("domain_payload")
    if not isinstance(raw_domain_payload, dict):
        raise ValidationFailure("current Domain revision has no exact domain payload authority")
    experiment_mode = raw_domain_payload.get("experiment_mode")
    if not isinstance(experiment_mode, str) or not experiment_mode:
        raise ValidationFailure("current Domain revision has no exact experiment_mode authority")
    inventory = (
        protein_capability_inventory()
        if payload["domain_kind"] == "protein_in_silico"
        else capability_inventory()
    )
    return experiment_mode, inventory


def _capability_is_allowed_for_domain(capability: dict[str, Any], experiment_mode: str) -> bool:
    allowed_modes = capability.get("allowed_domain_modes")
    return (
        capability.get("plannable") is True
        and capability.get("exposure_state") == "accepted"
        and isinstance(allowed_modes, list)
        and experiment_mode in allowed_modes
    )


async def _active_ngs_binding(domain_session: AsyncSession, *, project_id: str, experiment_id: str, domain: ExperimentAggregateHead) -> MolBioNGSGlobalBinding:
    state = await domain_session.get(MolBioNGSDomainState, domain.aggregate_id)
    binding = await domain_session.get(MolBioNGSGlobalBinding, state.current_binding_revision_id if state else "")
    if (
        state is None or binding is None or binding.binding_state != "acknowledged"
        or binding.project_id != project_id or binding.global_experiment_id != experiment_id
        or binding.global_domain_experiment_id != domain.aggregate_id
        or binding.global_domain_experiment_revision_id != domain.current_revision_id
        or not binding.global_binding_receipt_id or not binding.global_binding_receipt_sha256
    ):
        raise HTTPException(409, detail={"code": "current_acknowledged_binding_required", "message": "The exact current Domain revision has no active acknowledged binding."})
    return binding


async def _current_preparation_launch_authority(
    global_session: AsyncSession,
    domain_session: AsyncSession,
    *,
    project_id: str,
    global_experiment_id: str,
    domain_id: str,
    plan_authority: ExperimentWorkflowPlanAuthority,
    preparation: ExperimentWorkflowPreparation,
    proof_cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    current_authority = (
        proof_cache.get(plan_authority.expected_domain_revision_id)
        if proof_cache is not None
        else None
    )
    if current_authority is None:
        current_authority = await exact_local_launch_authority(
            global_session,
            domain_session,
            project_id=project_id,
            global_experiment_id=global_experiment_id,
            domain_id=domain_id,
            expected_domain_revision_id=plan_authority.expected_domain_revision_id,
        )
        if proof_cache is not None:
            proof_cache[plan_authority.expected_domain_revision_id] = current_authority
    current_authority = dict(current_authority)
    current_authority["capability_contract_sha256"] = plan_authority.capability_contract_sha256
    try:
        normalized_preparation = json.loads(preparation.normalized_request_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValidationFailure("replacement_preparation_required") from exc
    if (
        not isinstance(normalized_preparation, dict)
        or normalized_preparation.get("launch_authority") != current_authority
    ):
        raise ValidationFailure("replacement_preparation_required")
    return current_authority


async def _require_v2_plan_launch_context(
    session: AsyncSession,
    *,
    project_id: str,
    global_experiment_id: str,
    domain_id: str,
    workflow_id: str,
    workflow_revision_id: str,
    preparation: ExperimentWorkflowPreparation,
    launch_context_id: str | None,
    execution_mode: str,
) -> ExperimentLaunchContext | None:
    if execution_mode != "typed_launcher_handoff":
        if launch_context_id is not None:
            raise ValidationFailure("replacement_preparation_required")
        return None
    if not launch_context_id:
        raise ValidationFailure("replacement_preparation_required")
    context = await session.get(ExperimentLaunchContext, launch_context_id)
    if (
        context is None
        or context.contract_version != "2"
        or context.project_id != project_id
        or context.global_experiment_id != global_experiment_id
        or context.domain_experiment_id != domain_id
        or context.workflow_id != workflow_id
        or context.workflow_revision_id != workflow_revision_id
        or context.preparation_id != preparation.resource_id
        or context.normalized_request_sha256 != preparation.normalized_request_sha256
        or context.validation_receipt_id != preparation.validation_resource_id
        or context.validation_receipt_sha256
        != hashlib.sha256(preparation.validation_receipt_json.encode("utf-8")).hexdigest()
        or context.source_receipt_id != workflow_revision_id
    ):
        raise ValidationFailure("replacement_preparation_required")
    return context


async def _plan_head(session: AsyncSession, *, project_id: str, domain_id: str, plan_id: str) -> ExperimentAggregateHead:
    plan = await session.get(ExperimentAggregateHead, plan_id)
    if plan is None or plan.aggregate_kind != "workflow" or plan.workspace_id != project_id or plan.parent_id != domain_id:
        raise NotFound("Workflow Plan not found in Domain")
    return plan


def _is_plan_create_idempotency_integrity_error(exc: IntegrityError) -> bool:
    message = str(getattr(exc, "orig", exc)).lower()
    return (
        "unique constraint failed: idempotency_claims.scope, idempotency_claims.idempotency_key"
        in message
        or "idempotency_claims_pkey" in message
    )


async def _replay_plan_create_claim(
    session: AsyncSession,
    *,
    project_id: str,
    domain_id: str,
    scope: str,
    key: str,
    request_sha256: str,
    expected_domain_revision_id: str,
    capability_id: str,
) -> tuple[ExperimentAggregateHead, ExperimentWorkflowPlanAuthority, dict[str, Any]]:
    claim = await session.get(ExperimentIdempotencyClaim, (scope, key))
    if claim is None:
        raise IdempotencyConflict("concurrent Plan idempotency authority is unavailable")
    if claim.request_sha256 != request_sha256:
        raise IdempotencyConflict("idempotency key was reused with a different plan request")
    plan = await _plan_head(
        session,
        project_id=project_id,
        domain_id=domain_id,
        plan_id=claim.result_resource_id,
    )
    authority, capability_contract = await _stored_plan_authority(session, plan)
    if (
        authority.expected_domain_revision_id != expected_domain_revision_id
        or capability_contract["capability"].get("capability_id") != capability_id
    ):
        raise IdempotencyConflict("Plan replay conflicts with its immutable stored authority")
    return plan, authority, capability_contract


def _encode_plan_cursor(*, project_id: str, domain_id: str, limit: int, created_at: str, aggregate_id: str) -> str:
    body = {
        "schema": "bms.workflow-plan-cursor.v1",
        "project_id": project_id,
        "domain_id": domain_id,
        "limit": limit,
        "created_at": created_at,
        "aggregate_id": aggregate_id,
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_plan_cursor(cursor: str | None, *, project_id: str, domain_id: str, limit: int) -> tuple[str, str] | None:
    if cursor is None:
        return None
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = json.loads(base64.b64decode(cursor + padding, altchars=b"-_", validate=True).decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationFailure("Workflow Plan cursor is invalid") from exc
    expected_keys = {"schema", "project_id", "domain_id", "limit", "created_at", "aggregate_id"}
    if (
        not isinstance(decoded, dict)
        or set(decoded) != expected_keys
        or decoded.get("schema") != "bms.workflow-plan-cursor.v1"
        or decoded.get("project_id") != project_id
        or decoded.get("domain_id") != domain_id
        or decoded.get("limit") != limit
        or not isinstance(decoded.get("created_at"), str)
        or not decoded["created_at"]
        or not isinstance(decoded.get("aggregate_id"), str)
        or not decoded["aggregate_id"]
        or _encode_plan_cursor(
            project_id=project_id,
            domain_id=domain_id,
            limit=limit,
            created_at=decoded["created_at"],
            aggregate_id=decoded["aggregate_id"],
        ) != cursor
    ):
        raise ValidationFailure("Workflow Plan cursor is invalid or does not match this Project/Domain page")
    return decoded["created_at"], decoded["aggregate_id"]


async def _stored_plan_authority(
    session: AsyncSession,
    head: ExperimentAggregateHead,
) -> tuple[ExperimentWorkflowPlanAuthority, dict[str, Any]]:
    loaded = await load_workflow_plan_authority(session, head.aggregate_id)
    if loaded is None:
        raise ValidationFailure("Workflow Plan authority is unavailable")
    authority, contract = loaded
    if authority.workspace_id != head.workspace_id or authority.domain_experiment_id != head.parent_id:
        raise ValidationFailure("Workflow Plan authority does not match its Project/Domain")
    return authority, contract


def _plan_document(
    head: ExperimentAggregateHead,
    draft: ExperimentWorkflowDraft | None,
    authority: ExperimentWorkflowPlanAuthority,
    capability_contract: dict[str, Any],
) -> dict[str, Any]:
    capability = capability_contract["capability"]
    return {
        "schema": "bms.workflow-plan-head.v1", "plan_id": head.aggregate_id,
        "name": head.display_name, "capability_id": capability["capability_id"],
        "current_revision_id": head.current_revision_id, "head_generation": head.head_generation,
        "draft_id": draft.resource_id if draft else None,
        "draft_generation": draft.generation if draft else None,
        "domain_revision_id": authority.expected_domain_revision_id,
        "capability_contract": capability_contract,
        "capability_contract_sha256": authority.capability_contract_sha256,
        "workflow_family": capability["workflow_family"],
        "adapter_id": capability["workflow_adapter_id"],
        "lifecycle_state": head.lifecycle_state,
        "created_at": head.created_at, "updated_at": head.updated_at,
    }


def _revision_document(revision: ExperimentRevision) -> dict[str, Any]:
    return {
        "schema": "bms.workflow-plan-revision.v1", "revision_id": revision.resource_id,
        "plan_id": revision.subject_id, "revision_number": revision.revision_number,
        "parent_revision_id": revision.parent_revision_id,
        "payload": public_workflow_payload(json.loads(revision.canonical_payload)),
        "payload_sha256": revision.payload_sha256,
        "dependency_graph_sha256": revision.dependency_graph_sha256,
        "created_at": revision.created_at,
    }


def _preparation_document(preparation: ExperimentWorkflowPreparation) -> dict[str, Any]:
    normalized = json.loads(preparation.normalized_request_json)
    input_authority = normalized.get("input_authority")
    if isinstance(input_authority, dict):
        datasets = input_authority.get("dataset_inputs")
        sources = input_authority.get("workflow_source_receipts")
        normalized["input_authority"] = {
            "schema": input_authority.get("schema"),
            "workflow_revision_id": input_authority.get("workflow_revision_id"),
            "project_id": input_authority.get("project_id"),
            "global_experiment_id": input_authority.get("global_experiment_id"),
            "domain_id": input_authority.get("domain_id"),
            "dataset_count": len(datasets) if isinstance(datasets, list) else None,
            "source_receipt_count": len(sources) if isinstance(sources, list) else None,
            "authority_sha256": hashlib.sha256(
                json.dumps(input_authority, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        }
    public_scheduler = public_preparation_scheduler(json.loads(preparation.scheduler_payload_json))
    return {
        "schema": "bms.workflow-preparation.v1", "preparation_id": preparation.resource_id,
        "workflow_revision_id": preparation.workflow_revision_id,
        "normalized_request": normalized,
        "normalized_request_sha256": preparation.normalized_request_sha256,
        "requested_settings": normalized.get("workflow", {}).get("parameters", {}),
        "effective_settings": public_scheduler.get("params", {}),
        "scheduler": public_scheduler,
        "validation_receipt_id": preparation.validation_resource_id,
        "validation": json.loads(preparation.validation_receipt_json),
        "status": preparation.validation_status,
        "expected_cardinality": preparation.expected_cardinality,
        "created_at": preparation.created_at, "prepared_at": preparation.prepared_at,
    }


async def _run_group_document(session: AsyncSession, group: ExperimentRunGroup) -> dict[str, Any]:
    runs = (await session.scalars(select(ExperimentWorkflowRun).where(ExperimentWorkflowRun.run_group_id == group.resource_id).order_by(ExperimentWorkflowRun.created_at, ExperimentWorkflowRun.resource_id))).all()
    items: list[dict[str, Any]] = []
    for run in runs:
        attempts = (await session.scalars(select(ExperimentRunAttempt).where(ExperimentRunAttempt.workflow_run_id == run.resource_id).order_by(ExperimentRunAttempt.attempt_number))).all()
        projected_attempts = []
        for attempt in attempts:
            context = await session.scalar(select(ExperimentLaunchContext).where(ExperimentLaunchContext.run_attempt_id == attempt.resource_id))
            projected_attempts.append({
                "attempt_id": attempt.resource_id, "attempt_number": attempt.attempt_number,
                "preparation_id": attempt.preparation_id, "state": attempt.state,
                "canonical_job_id": attempt.scheduler_job_id,
                "launch_context": context_document(context) if context else None,
                "terminal_receipt": json.loads(attempt.terminal_receipt_json) if attempt.terminal_receipt_json else None,
            })
        items.append({"run_id": run.resource_id, "preparation_id": run.preparation_id, "state": run.state, "generation": run.generation, "attempts": projected_attempts})
    return {"schema": "bms.run-group.v1", "run_group_id": group.resource_id, "request_sha256": group.request_sha256, "state": group.state, "generation": group.generation, "runs": items, "created_at": group.created_at, "updated_at": group.updated_at}


@router.get("/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/capabilities")
async def list_domain_capabilities(
    project_id: str,
    experiment_id: str,
    domain_id: str,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    try:
        _project, _experiment, domain = await _domain_hierarchy(session, project_id, experiment_id, domain_id)
        revision = await session.get(ExperimentRevision, domain.current_revision_id or "")
        experiment_mode, inventory = _domain_capability_authority(revision)
        items = []
        for capability in inventory["capabilities"]:
            if not _capability_is_allowed_for_domain(capability, experiment_mode):
                continue
            capability_contract = workflow_plan_capability_contract(capability["capability_id"])
            pinned = capability_contract["capability"]
            contract_json = json.dumps(capability_contract, sort_keys=True, separators=(",", ":"))
            items.append({
                "capability_id": pinned["capability_id"],
                "capability_version": pinned["capability_version"],
                "label": pinned["label"],
                "scientific_role": pinned["scientific_role"],
                "launch_mode": pinned["launch_mode"],
                "workflow_family": pinned["workflow_family"],
                "workflow_adapter_id": pinned["workflow_adapter_id"],
                "parameter_schema_id": pinned["parameter_schema_id"],
                "parameter_schema": capability_contract["parameter_schema"],
                "allowed_model_modes": capability_contract["allowed_model_modes"],
                "result_contracts": pinned["result_contracts"],
                "canonical_source_destination": pinned["canonical_source_destination"],
                "accepted_source_roles": pinned["accepted_source_roles"],
                "capability_contract": capability_contract,
                "capability_contract_sha256": hashlib.sha256(contract_json.encode("utf-8")).hexdigest(),
            })
        items.sort(key=lambda item: (item["label"].casefold(), item["capability_id"]))
        return {
            "schema": "bms.ngs-molbio.domain-capability-list.v1",
            "domain_id": domain_id,
            "domain_revision_id": domain.current_revision_id,
            "experiment_mode": experiment_mode,
            "inventory_sha256": inventory["content_sha256"],
            "items": items,
        }
    except NgsMolBioCapabilityError as exc:
        raise HTTPException(503, detail={"code": "capability_authority_unavailable", "message": str(exc)}) from exc
    except ExperimentServiceError as exc:
        raise _service_error(exc) from exc


@router.get("/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/plans")
async def list_domain_plans(project_id: str, experiment_id: str, domain_id: str, cursor: str | None = Query(default=None, max_length=1024), limit: int = Query(default=50, ge=1, le=100), session: AsyncSession = Depends(get_experiment_session)) -> dict:
    try:
        await _domain_hierarchy(session, project_id, experiment_id, domain_id)
        decoded_cursor = _decode_plan_cursor(cursor, project_id=project_id, domain_id=domain_id, limit=limit)
        statement = (
            select(ExperimentAggregateHead)
            .join(ExperimentWorkflowPlanAuthority, ExperimentWorkflowPlanAuthority.workflow_id == ExperimentAggregateHead.aggregate_id)
            .where(
                ExperimentAggregateHead.aggregate_kind == "workflow",
                ExperimentAggregateHead.workspace_id == project_id,
                ExperimentAggregateHead.parent_id == domain_id,
                ExperimentWorkflowPlanAuthority.workspace_id == project_id,
                ExperimentWorkflowPlanAuthority.domain_experiment_id == domain_id,
            )
            .order_by(ExperimentAggregateHead.created_at.desc(), ExperimentAggregateHead.aggregate_id.desc())
            .limit(limit + 1)
        )
        if decoded_cursor is not None:
            created_at, aggregate_id = decoded_cursor
            statement = statement.where(or_(
                ExperimentAggregateHead.created_at < created_at,
                and_(ExperimentAggregateHead.created_at == created_at, ExperimentAggregateHead.aggregate_id < aggregate_id),
            ))
        rows = list((await session.scalars(statement)).all())
        page = rows[:limit]
        drafts = {row.workflow_id: row for row in (await session.scalars(select(ExperimentWorkflowDraft).where(ExperimentWorkflowDraft.workflow_id.in_([item.aggregate_id for item in page])))).all()} if page else {}
        items = []
        for row in page:
            authority, capability_contract = await _stored_plan_authority(session, row)
            items.append(_plan_document(row, drafts.get(row.aggregate_id), authority, capability_contract))
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1]
            next_cursor = _encode_plan_cursor(project_id=project_id, domain_id=domain_id, limit=limit, created_at=last.created_at, aggregate_id=last.aggregate_id)
        return {"schema": "bms.workflow-plan-list.v1", "items": items, "next_cursor": next_cursor}
    except ExperimentServiceError as exc:
        raise _service_error(exc) from exc


@router.post("/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/plans", status_code=201)
async def create_domain_plan(project_id: str, experiment_id: str, domain_id: str, payload: PlanCreateRequest, request: Request, session: AsyncSession = Depends(get_experiment_session), domain_session: AsyncSession = Depends(get_molbio_ngs_session)) -> dict:
    try:
        await _require_mutation_owner(request, session, resource_id=project_id)
        _project, _experiment, domain = await _domain_hierarchy(session, project_id, experiment_id, domain_id)
        key = _idempotency_key(request)
        normalized = {"project_id": project_id, "experiment_id": experiment_id, "domain_id": domain_id, **payload.model_dump()}
        digest = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        scope = f"plan-create:{hashlib.sha256(f'{project_id}:{domain_id}'.encode()).hexdigest()}"
        claim = await session.get(ExperimentIdempotencyClaim, (scope, key))
        if claim:
            plan, authority, capability_contract = await _replay_plan_create_claim(
                session,
                project_id=project_id,
                domain_id=domain_id,
                scope=scope,
                key=key,
                request_sha256=digest,
                expected_domain_revision_id=payload.expected_domain_revision_id,
                capability_id=payload.capability_id,
            )
        else:
            await _active_ngs_binding(domain_session, project_id=project_id, experiment_id=experiment_id, domain=domain)
            if domain.current_revision_id != payload.expected_domain_revision_id:
                raise RevisionConflict("Domain revision changed")
            domain_revision = await session.get(ExperimentRevision, payload.expected_domain_revision_id)
            experiment_mode, inventory = _domain_capability_authority(domain_revision)
            selected_capability = next(
                (
                    capability
                    for capability in inventory["capabilities"]
                    if capability.get("capability_id") == payload.capability_id
                ),
                None,
            )
            if selected_capability is None or not _capability_is_allowed_for_domain(
                selected_capability, experiment_mode
            ):
                raise ValidationFailure(
                    "capability is not accepted for the exact Domain experiment_mode"
                )
            try:
                async with session.begin_nested():
                    plan = await create_workflow(session, project_id, payload.name, payload.capability_id, experiment_id=domain_id)
                    authority, capability_contract = await persist_workflow_plan_authority(
                        session,
                        workflow_id=plan.aggregate_id,
                        workspace_id=project_id,
                        domain_experiment_id=domain_id,
                        expected_domain_revision_id=payload.expected_domain_revision_id,
                        capability_id=payload.capability_id,
                    )
                    session.add(ExperimentIdempotencyClaim(scope=scope, idempotency_key=key, request_sha256=digest, result_resource_id=plan.aggregate_id, response_json=json.dumps({"plan_id": plan.aggregate_id}), created_at=datetime.now(timezone.utc).isoformat()))
                    await session.flush()
            except IntegrityError as exc:
                if not _is_plan_create_idempotency_integrity_error(exc):
                    raise
                await session.rollback()
                plan, authority, capability_contract = await _replay_plan_create_claim(
                    session,
                    project_id=project_id,
                    domain_id=domain_id,
                    scope=scope,
                    key=key,
                    request_sha256=digest,
                    expected_domain_revision_id=payload.expected_domain_revision_id,
                    capability_id=payload.capability_id,
                )
        draft = await session.scalar(select(ExperimentWorkflowDraft).where(ExperimentWorkflowDraft.workflow_id == plan.aggregate_id))
        await session.commit()
        return _plan_document(plan, draft, authority, capability_contract)
    except NgsMolBioCapabilityError as exc:
        await session.rollback()
        raise HTTPException(503, detail={"code": "capability_authority_unavailable", "message": str(exc)}) from exc
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _service_error(exc) from exc
    except IntegrityError:
        await session.rollback()
        raise


@router.get("/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/plans/{plan_id}")
async def get_domain_plan(project_id: str, experiment_id: str, domain_id: str, plan_id: str, session: AsyncSession = Depends(get_experiment_session)) -> dict:
    try:
        await _domain_hierarchy(session, project_id, experiment_id, domain_id)
        plan = await _plan_head(session, project_id=project_id, domain_id=domain_id, plan_id=plan_id)
        authority, capability_contract = await _stored_plan_authority(session, plan)
        draft = await session.scalar(select(ExperimentWorkflowDraft).where(ExperimentWorkflowDraft.workflow_id == plan_id))
        result = _plan_document(plan, draft, authority, capability_contract)
        result["draft"] = public_workflow_payload(json.loads(draft.canonical_payload)) if draft else None
        return result
    except ExperimentServiceError as exc:
        raise _service_error(exc) from exc


@router.put("/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/plans/{plan_id}/draft")
async def replace_domain_plan_draft(project_id: str, experiment_id: str, domain_id: str, plan_id: str, payload: DraftReplaceRequest, request: Request, session: AsyncSession = Depends(get_experiment_session), domain_session: AsyncSession = Depends(get_molbio_ngs_session)) -> dict:
    try:
        await _require_mutation_owner(request, session, resource_id=project_id)
        _project, _experiment, domain = await _domain_hierarchy(session, project_id, experiment_id, domain_id)
        binding = await _active_ngs_binding(domain_session, project_id=project_id, experiment_id=experiment_id, domain=domain)
        plan = await _plan_head(session, project_id=project_id, domain_id=domain_id, plan_id=plan_id)
        authority, _capability_contract = await _stored_plan_authority(session, plan)
        if authority.expected_domain_revision_id != binding.global_domain_experiment_revision_id:
            raise RevisionConflict("Workflow Plan Domain revision changed")
        draft = await save_workflow_draft(session, plan_id, payload.payload, expected_generation=payload.expected_draft_generation)
        await session.commit()
        return {"schema": "bms.workflow-plan-draft.v1", "draft_id": draft.resource_id, "plan_id": plan_id, "generation": draft.generation, "payload": public_workflow_payload(json.loads(draft.canonical_payload)), "payload_sha256": hashlib.sha256(draft.canonical_payload.encode()).hexdigest(), "updated_at": draft.updated_at}
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _service_error(exc) from exc


@router.post("/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/plans/{plan_id}/revisions", status_code=201)
async def publish_domain_plan_revision(project_id: str, experiment_id: str, domain_id: str, plan_id: str, payload: RevisionPublishRequest, request: Request, session: AsyncSession = Depends(get_experiment_session), domain_session: AsyncSession = Depends(get_molbio_ngs_session)) -> dict:
    try:
        await _require_mutation_owner(request, session, resource_id=project_id)
        _project, _experiment, domain = await _domain_hierarchy(session, project_id, experiment_id, domain_id)
        binding = await _active_ngs_binding(domain_session, project_id=project_id, experiment_id=experiment_id, domain=domain)
        plan = await _plan_head(session, project_id=project_id, domain_id=domain_id, plan_id=plan_id)
        authority, _capability_contract = await _stored_plan_authority(session, plan)
        if authority.expected_domain_revision_id != binding.global_domain_experiment_revision_id:
            raise RevisionConflict("Workflow Plan Domain revision changed")
        draft = await session.scalar(select(ExperimentWorkflowDraft).where(ExperimentWorkflowDraft.workflow_id == plan_id))
        if draft is None or draft.generation != payload.expected_draft_generation:
            raise RevisionConflict("Workflow Plan draft generation changed")
        revision = await save_workflow_revision(session, plan_id, expected_head_generation=payload.expected_head_generation, change_summary=payload.change_summary)
        await session.commit()
        result = _revision_document(revision)
        result["change_summary"] = payload.change_summary
        return result
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _service_error(exc) from exc


@router.get("/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/plans/{plan_id}/revisions")
async def list_domain_plan_revisions(project_id: str, experiment_id: str, domain_id: str, plan_id: str, cursor: int | None = Query(default=None, ge=1), limit: int = Query(default=25, ge=1, le=100), session: AsyncSession = Depends(get_experiment_session)) -> dict:
    try:
        await _domain_hierarchy(session, project_id, experiment_id, domain_id)
        await _plan_head(session, project_id=project_id, domain_id=domain_id, plan_id=plan_id)
        statement = select(ExperimentRevision).where(ExperimentRevision.subject_id == plan_id).order_by(ExperimentRevision.revision_number).limit(limit + 1)
        if cursor is not None:
            statement = statement.where(ExperimentRevision.revision_number > cursor)
        rows = list((await session.scalars(statement)).all())
        return {"schema": "bms.workflow-plan-revision-list.v1", "items": [_revision_document(row) for row in rows[:limit]], "next_cursor": rows[limit - 1].revision_number if len(rows) > limit else None}
    except ExperimentServiceError as exc:
        raise _service_error(exc) from exc


@router.get("/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/plans/{plan_id}/revisions/{revision_id}")
async def get_domain_plan_revision(project_id: str, experiment_id: str, domain_id: str, plan_id: str, revision_id: str, session: AsyncSession = Depends(get_experiment_session)) -> dict:
    try:
        await _domain_hierarchy(session, project_id, experiment_id, domain_id)
        await _plan_head(session, project_id=project_id, domain_id=domain_id, plan_id=plan_id)
        revision = await session.get(ExperimentRevision, revision_id)
        if revision is None or revision.subject_id != plan_id:
            raise NotFound("Workflow Plan revision not found")
        return _revision_document(revision)
    except ExperimentServiceError as exc:
        raise _service_error(exc) from exc


@router.post("/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/plans/{plan_id}/revisions/{revision_id}/preparations", status_code=201)
async def prepare_domain_plan(project_id: str, experiment_id: str, domain_id: str, plan_id: str, revision_id: str, payload: PreparationRequest, request: Request, session: AsyncSession = Depends(get_experiment_session), core_session: AsyncSession = Depends(get_core_session), domain_session: AsyncSession = Depends(get_molbio_ngs_session)) -> dict:
    try:
        await _require_mutation_owner(request, session, resource_id=project_id)
        _project, _experiment, _domain = await _domain_hierarchy(session, project_id, experiment_id, domain_id)
        plan = await _plan_head(session, project_id=project_id, domain_id=domain_id, plan_id=plan_id)
        plan_authority, _capability_contract = await _stored_plan_authority(session, plan)
        revision = await session.get(ExperimentRevision, revision_id)
        if revision is None or revision.subject_id != plan_id:
            raise NotFound("Workflow Plan revision not found")
        launch_authority = await exact_local_launch_authority(
            session,
            domain_session,
            project_id=project_id,
            global_experiment_id=experiment_id,
            domain_id=domain_id,
            expected_domain_revision_id=plan_authority.expected_domain_revision_id,
        )
        launch_authority["capability_contract_sha256"] = plan_authority.capability_contract_sha256
        key = _idempotency_key(request)
        normalized_request = {"project_id": project_id, "experiment_id": experiment_id, "domain_id": domain_id, "plan_id": plan_id, "revision_id": revision_id, "input_dataset_revision_ids": payload.input_dataset_revision_ids, "launch_authority": launch_authority}
        digest = hashlib.sha256(json.dumps(normalized_request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        scope = f"prepare:{hashlib.sha256(revision_id.encode()).hexdigest()}"
        claim = await session.get(ExperimentIdempotencyClaim, (scope, key))
        if claim:
            if claim.request_sha256 != digest:
                raise IdempotencyConflict("idempotency key was reused with a different preparation request")
            preparation = await session.get(ExperimentWorkflowPreparation, claim.result_resource_id)
            if preparation is None:
                raise ValidationFailure("preparation idempotency authority is unavailable")
            await validate_preparation_authority(
                session, preparation, core_session=core_session
            )
            if json.loads(preparation.normalized_request_json).get("launch_authority") != launch_authority:
                raise IdempotencyConflict("preparation replay conflicts with its immutable launch authority")
        else:
            prior = await session.scalar(select(ExperimentWorkflowPreparation).join(ExperimentRevision, ExperimentRevision.resource_id == ExperimentWorkflowPreparation.workflow_revision_id).where(ExperimentRevision.subject_id == plan_id).order_by(ExperimentWorkflowPreparation.created_at.desc(), ExperimentWorkflowPreparation.resource_id.desc()))
            prior_authority = None
            if prior is not None:
                try:
                    prior_authority = json.loads(prior.normalized_request_json).get("launch_authority")
                except (TypeError, ValueError):
                    prior_authority = None
            if (
                prior is not None
                and prior.workflow_revision_id == revision_id
                and prior_authority == launch_authority
            ):
                preparation = prior
            else:
                preparation = await prepare_workflow(
                    session,
                    revision_id,
                    {"input_dataset_revision_ids": payload.input_dataset_revision_ids, "launch_authority": launch_authority},
                    core_session=core_session,
                )
                if prior is not None and prior.resource_id != preparation.resource_id:
                    session.add(ExperimentLineageEdge(id=f"preparation-supersedes:{uuid.uuid4()}", workspace_id=project_id, source_resource_id=preparation.resource_id, target_resource_id=prior.resource_id, edge_mode="supersedes", edge_key="prior-preparation", metadata_json=json.dumps({"reason": "current-authority-revalidation"}), created_at=datetime.now(timezone.utc).isoformat()))
            session.add(ExperimentIdempotencyClaim(scope=scope, idempotency_key=key, request_sha256=digest, result_resource_id=preparation.resource_id, response_json=json.dumps({"preparation_id": preparation.resource_id}), created_at=datetime.now(timezone.utc).isoformat()))
        await session.commit()
        return _preparation_document(preparation)
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _service_error(exc) from exc


@router.get("/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/preparations/{preparation_id}")
async def get_domain_preparation(project_id: str, experiment_id: str, domain_id: str, preparation_id: str, session: AsyncSession = Depends(get_experiment_session)) -> dict:
    try:
        await _domain_hierarchy(session, project_id, experiment_id, domain_id)
        preparation = await session.get(ExperimentWorkflowPreparation, preparation_id)
        revision = await session.get(ExperimentRevision, preparation.workflow_revision_id if preparation else "")
        plan = await session.get(ExperimentAggregateHead, revision.subject_id if revision else "")
        if preparation is None or revision is None or plan is None or preparation.workspace_id != project_id or plan.parent_id != domain_id:
            raise NotFound("Preparation not found in Domain")
        return _preparation_document(preparation)
    except ExperimentServiceError as exc:
        raise _service_error(exc) from exc


@router.post("/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/preparations/{preparation_id}/launch-contexts", status_code=201)
async def issue_prepared_handoff(project_id: str, experiment_id: str, domain_id: str, preparation_id: str, payload: PreparedHandoffRequest, request: Request, session: AsyncSession = Depends(get_experiment_session), core_session: AsyncSession = Depends(get_core_session), domain_session: AsyncSession = Depends(get_molbio_ngs_session)) -> dict:
    try:
        await _require_mutation_owner(request, session, resource_id=project_id)
        _project, _experiment, _domain = await _domain_hierarchy(session, project_id, experiment_id, domain_id)
        preparation = await session.get(ExperimentWorkflowPreparation, preparation_id)
        revision = await session.get(ExperimentRevision, preparation.workflow_revision_id if preparation else "")
        plan = await session.get(ExperimentAggregateHead, revision.subject_id if revision else "")
        if preparation is None or revision is None or plan is None or preparation.workspace_id != project_id or plan.parent_id != domain_id:
            raise NotFound("Preparation not found in Domain")
        await validate_preparation_authority(
            session, preparation, core_session=core_session
        )
        plan_authority, capability_contract = await _stored_plan_authority(session, plan)
        await _current_preparation_launch_authority(
            session,
            domain_session,
            project_id=project_id,
            global_experiment_id=experiment_id,
            domain_id=domain_id,
            plan_authority=plan_authority,
            preparation=preparation,
        )
        if capability_contract["capability"].get("launch_mode") != "typed_launcher_handoff":
            raise ValidationFailure("capability does not use typed launcher handoff")
        key = _idempotency_key(request)
        normalized = {"project_id": project_id, "experiment_id": experiment_id, "domain_id": domain_id, "preparation_id": preparation_id, "return_uri": payload.return_uri}
        digest = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        scope = f"handoff:{hashlib.sha256(preparation_id.encode()).hexdigest()}"
        claim = await session.get(ExperimentIdempotencyClaim, (scope, key))
        if claim:
            if claim.request_sha256 != digest:
                raise IdempotencyConflict("idempotency key was reused with a different handoff request")
            context = await session.get(ExperimentLaunchContext, claim.result_resource_id)
            if (
                context is None
                or context.launch_context_id != claim.result_resource_id
                or context.return_uri != payload.return_uri
            ):
                raise ValidationFailure("launch-context idempotency authority is unavailable or mismatched")
        else:
            context = await create_prepared_launch_context(session, project_id=project_id, global_experiment_id=experiment_id, domain_experiment_id=domain_id, preparation_id=preparation_id, return_uri=payload.return_uri)
            session.add(ExperimentIdempotencyClaim(scope=scope, idempotency_key=key, request_sha256=digest, result_resource_id=context.launch_context_id, response_json=json.dumps({"launch_context_id": context.launch_context_id}), created_at=datetime.now(timezone.utc).isoformat()))
        await _require_v2_plan_launch_context(
            session,
            project_id=project_id,
            global_experiment_id=experiment_id,
            domain_id=domain_id,
            workflow_id=plan.aggregate_id,
            workflow_revision_id=revision.resource_id,
            preparation=preparation,
            launch_context_id=context.launch_context_id,
            execution_mode="typed_launcher_handoff",
        )
        await session.commit()
        return context_document(context)
    except (ExperimentServiceError, LaunchContextError) as exc:
        await session.rollback()
        if isinstance(exc, LaunchContextError):
            raise _launch_context_error(exc) from exc
        raise _service_error(exc) from exc


async def _launch_authority(
    session: AsyncSession,
    core_session: AsyncSession,
    domain_session: AsyncSession,
    *,
    project_id: str,
    global_experiment_id: str,
    domain_id: str,
    launches: list[PreparationLaunch],
) -> tuple[list[str], dict[str, str]]:
    preparation_ids = [item.preparation_id for item in launches]
    if len(preparation_ids) != len(set(preparation_ids)):
        raise ValidationFailure("preparation_launches must contain unique preparation IDs")
    context_ids = [item.launch_context_id for item in launches if item.launch_context_id]
    if len(context_ids) != len(set(context_ids)):
        raise ValidationFailure("launch contexts must be unique")
    contexts: dict[str, str] = {}
    proof_cache: dict[str, dict[str, Any]] = {}
    for item in launches:
        preparation = await session.get(ExperimentWorkflowPreparation, item.preparation_id)
        revision = await session.get(ExperimentRevision, preparation.workflow_revision_id if preparation else "")
        plan = await session.get(ExperimentAggregateHead, revision.subject_id if revision else "")
        if preparation is None or revision is None or plan is None or preparation.workspace_id != project_id or plan.parent_id != domain_id:
            raise NotFound("Preparation not found in Domain")
        await validate_preparation_authority(
            session, preparation, core_session=core_session
        )
        plan_authority, capability_contract = await _stored_plan_authority(session, plan)
        await _current_preparation_launch_authority(
            session,
            domain_session,
            project_id=project_id,
            global_experiment_id=global_experiment_id,
            domain_id=domain_id,
            plan_authority=plan_authority,
            preparation=preparation,
            proof_cache=proof_cache,
        )
        mode = capability_contract["capability"].get("launch_mode")
        if mode not in {"typed_launcher_handoff", "managed_materialization"}:
            raise ValidationFailure("capability is not accepted for managed Workflow Plan launch")
        context = await _require_v2_plan_launch_context(
            session,
            project_id=project_id,
            global_experiment_id=global_experiment_id,
            domain_id=domain_id,
            workflow_id=plan.aggregate_id,
            workflow_revision_id=revision.resource_id,
            preparation=preparation,
            launch_context_id=item.launch_context_id,
            execution_mode=mode,
        )
        if context is not None:
            contexts[item.preparation_id] = context.launch_context_id
    return preparation_ids, contexts


@router.post("/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/run-groups", status_code=201)
async def launch_domain_run_group(project_id: str, experiment_id: str, domain_id: str, payload: RunGroupLaunchRequest, request: Request, session: AsyncSession = Depends(get_experiment_session), core_session: AsyncSession = Depends(get_core_session), domain_session: AsyncSession = Depends(get_molbio_ngs_session)) -> dict:
    actor = ""
    try:
        actor = await _require_mutation_owner(request, session, resource_id=project_id)
        await _domain_hierarchy(session, project_id, experiment_id, domain_id)
        preparation_ids, contexts = await _launch_authority(
            session,
            core_session,
            domain_session,
            project_id=project_id,
            global_experiment_id=experiment_id,
            domain_id=domain_id,
            launches=payload.preparation_launches,
        )
        group = await create_run_group(
            session,
            project_id,
            preparation_ids,
            idempotency_key=_idempotency_key(request),
            idempotency_authority={"project_id": project_id, "experiment_id": experiment_id, "domain_id": domain_id},
            launch_context_ids=contexts,
            core_session=core_session,
            source_domain_id=domain_id,
        )
        await reserve_run_group(session, group_id=group.resource_id, domain_id=domain_id, actor=actor)
        await session.commit()
        return await _run_group_document(session, group)
    except ResourceAdmissionDenied as exc:
        refusal_requests = [{"workspace_id": item["preparation"].workspace_id, "preparation_id": item["preparation"].resource_id, "plan_id": item["plan"].aggregate_id, "cpu_threads": item.get("cpu_threads"), "dram_bytes": item.get("dram_bytes"), "gpu_index": item.get("gpu_index"), "gpu_uuid": item.get("gpu_uuid")} for item in exc.requests]
        await session.rollback()
        exc.requests = refusal_requests
        await persist_admission_refusal(session, domain_id=domain_id, actor=actor, denial=exc)
        await session.commit()
        raise HTTPException(409, detail={"code": exc.code, "message": exc.reason}) from exc
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _service_error(exc) from exc


@router.get("/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/run-groups/{run_group_id}")
async def get_domain_run_group(project_id: str, experiment_id: str, domain_id: str, run_group_id: str, session: AsyncSession = Depends(get_experiment_session)) -> dict:
    try:
        await _domain_hierarchy(session, project_id, experiment_id, domain_id)
        group = await session.get(ExperimentRunGroup, run_group_id)
        if group is None or group.workspace_id != project_id:
            raise NotFound("Run group not found")
        runs = (await session.scalars(select(ExperimentWorkflowRun).where(ExperimentWorkflowRun.run_group_id == run_group_id))).all()
        for run in runs:
            preparation = await session.get(ExperimentWorkflowPreparation, run.preparation_id)
            revision = await session.get(ExperimentRevision, preparation.workflow_revision_id if preparation else "")
            plan = await session.get(ExperimentAggregateHead, revision.subject_id if revision else "")
            if plan is None or plan.parent_id != domain_id:
                raise NotFound("Run group not found in Domain")
        return await _run_group_document(session, group)
    except ExperimentServiceError as exc:
        raise _service_error(exc) from exc


async def _require_run_group_domain(
    session: AsyncSession,
    *,
    project_id: str,
    domain_id: str,
    run_group_id: str,
) -> ExperimentRunGroup:
    group = await session.get(ExperimentRunGroup, run_group_id)
    if group is None or group.workspace_id != project_id:
        raise NotFound("Run group not found")
    runs = list(
        (
            await session.scalars(
                select(ExperimentWorkflowRun).where(
                    ExperimentWorkflowRun.run_group_id == run_group_id
                )
            )
        ).all()
    )
    if not runs:
        raise NotFound("Run group not found in Domain")
    for run in runs:
        preparation = await session.get(ExperimentWorkflowPreparation, run.preparation_id)
        revision = await session.get(
            ExperimentRevision,
            preparation.workflow_revision_id if preparation else "",
        )
        plan = await session.get(
            ExperimentAggregateHead, revision.subject_id if revision else ""
        )
        if (
            preparation is None
            or preparation.workspace_id != project_id
            or revision is None
            or plan is None
            or plan.aggregate_kind != "workflow"
            or plan.workspace_id != project_id
            or plan.parent_id != domain_id
        ):
            raise NotFound("Run group not found in Domain")
    return group


@router.post("/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/run-groups/{run_group_id}/retry")
async def retry_domain_run_group(project_id: str, experiment_id: str, domain_id: str, run_group_id: str, payload: RunGroupRetryRequest, request: Request, session: AsyncSession = Depends(get_experiment_session), core_session: AsyncSession = Depends(get_core_session), domain_session: AsyncSession = Depends(get_molbio_ngs_session)) -> dict:
    actor = ""
    try:
        actor = await _require_mutation_owner(request, session, resource_id=project_id)
        await _domain_hierarchy(session, project_id, experiment_id, domain_id)
        await _require_run_group_domain(
            session,
            project_id=project_id,
            domain_id=domain_id,
            run_group_id=run_group_id,
        )
        if len({item.run_id for item in payload.replacements}) != len(payload.replacements):
            raise ValidationFailure("retry replacements must contain unique run IDs")
        replacement_context_ids = [
            item.launch_context_id for item in payload.replacements if item.launch_context_id
        ]
        if len(replacement_context_ids) != len(set(replacement_context_ids)):
            raise ValidationFailure("retry replacement launch contexts must be unique")
        preparation_by_run: dict[str, str] = {}
        context_by_run: dict[str, str] = {}
        for item in payload.replacements:
            preparation_ids, context_by_preparation = await _launch_authority(
                session,
                core_session,
                domain_session,
                project_id=project_id,
                global_experiment_id=experiment_id,
                domain_id=domain_id,
                launches=[
                    PreparationLaunch(
                        preparation_id=item.preparation_id,
                        launch_context_id=item.launch_context_id,
                    )
                ],
            )
            preparation_id = preparation_ids[0]
            preparation_by_run[item.run_id] = preparation_id
            if preparation_id in context_by_preparation:
                context_by_run[item.run_id] = context_by_preparation[preparation_id]
        group = await retry_failed_run_group(session, project_id, run_group_id, idempotency_key=_idempotency_key(request), replacement_preparation_ids=preparation_by_run, replacement_launch_context_ids=context_by_run, expected_generation=payload.expected_run_group_generation, core_session=core_session, source_domain_id=domain_id)
        await reserve_run_group(session, group_id=group.resource_id, domain_id=domain_id, actor=actor)
        await session.commit()
        return await _run_group_document(session, group)
    except ResourceAdmissionDenied as exc:
        refusal_requests = [{"workspace_id": item["preparation"].workspace_id, "preparation_id": item["preparation"].resource_id, "plan_id": item["plan"].aggregate_id, "cpu_threads": item.get("cpu_threads"), "dram_bytes": item.get("dram_bytes"), "gpu_index": item.get("gpu_index"), "gpu_uuid": item.get("gpu_uuid")} for item in exc.requests]
        await session.rollback()
        exc.requests = refusal_requests
        await persist_admission_refusal(session, domain_id=domain_id, actor=actor, denial=exc)
        await session.commit()
        raise HTTPException(409, detail={"code": exc.code, "message": exc.reason}) from exc
    except ExperimentServiceError as exc:
        await session.rollback()
        if isinstance(exc, ValidationFailure) and str(exc) == "replacement_preparation_required":
            raise HTTPException(409, detail={"code": "replacement_preparation_required", "message": str(exc)}) from exc
        raise _service_error(exc) from exc


@router.post("/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/run-groups/{run_group_id}/resubmit", status_code=201)
async def resubmit_domain_run_group(project_id: str, experiment_id: str, domain_id: str, run_group_id: str, payload: RunGroupResubmitRequest, request: Request, session: AsyncSession = Depends(get_experiment_session), core_session: AsyncSession = Depends(get_core_session), domain_session: AsyncSession = Depends(get_molbio_ngs_session)) -> dict:
    actor = ""
    try:
        actor = await _require_mutation_owner(request, session, resource_id=project_id)
        await _domain_hierarchy(session, project_id, experiment_id, domain_id)
        preparation_ids, contexts = await _launch_authority(
            session,
            core_session,
            domain_session,
            project_id=project_id,
            global_experiment_id=experiment_id,
            domain_id=domain_id,
            launches=payload.preparation_launches,
        )
        group = await resubmit_run_group(session, project_id, run_group_id, idempotency_key=_idempotency_key(request), preparation_ids=preparation_ids, launch_context_ids=contexts, expected_generation=payload.expected_run_group_generation, core_session=core_session, source_domain_id=domain_id)
        await reserve_run_group(session, group_id=group.resource_id, domain_id=domain_id, actor=actor)
        await session.commit()
        return await _run_group_document(session, group)
    except ResourceAdmissionDenied as exc:
        refusal_requests = [{"workspace_id": item["preparation"].workspace_id, "preparation_id": item["preparation"].resource_id, "plan_id": item["plan"].aggregate_id, "cpu_threads": item.get("cpu_threads"), "dram_bytes": item.get("dram_bytes"), "gpu_index": item.get("gpu_index"), "gpu_uuid": item.get("gpu_uuid")} for item in exc.requests]
        await session.rollback()
        exc.requests = refusal_requests
        await persist_admission_refusal(session, domain_id=domain_id, actor=actor, denial=exc)
        await session.commit()
        raise HTTPException(409, detail={"code": exc.code, "message": exc.reason}) from exc
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _service_error(exc) from exc


@router.post("/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/run-groups/{run_group_id}/clone", status_code=201)
async def clone_domain_run_intent(
    project_id: str,
    experiment_id: str,
    domain_id: str,
    run_group_id: str,
    payload: RunCloneRequest,
    request: Request,
    session: AsyncSession = Depends(get_experiment_session),
) -> dict[str, Any]:
    scope = ""
    key = ""
    request_sha256 = ""
    try:
        actor = await _require_mutation_owner(request, session, resource_id=project_id)
        if not actor or len(actor) > 255:
            raise ValidationFailure("run clone actor identity is outside the closed receipt bound")
        _project, _experiment, domain = await _domain_hierarchy(
            session, project_id, experiment_id, domain_id
        )
        key = payload.idempotency_key
        normalized_request = {
            "operation": "run-clone",
            "project_id": project_id,
            "global_experiment_id": experiment_id,
            "domain_experiment_id": domain_id,
            "source_run_group_id": run_group_id,
            "created_by": actor,
            **payload.model_dump(by_alias=True),
        }
        request_sha256 = hashlib.sha256(
            canonical_json(normalized_request).encode("utf-8")
        ).hexdigest()
        scope = f"run-clone:{hashlib.sha256(f'{project_id}:{domain_id}:{run_group_id}'.encode()).hexdigest()}"
        claim = await session.get(ExperimentIdempotencyClaim, (scope, key))
        if claim is not None:
            if claim.request_sha256 != request_sha256:
                raise IdempotencyConflict("run clone idempotency key conflicts with another request")
            receipt_resource = await session.get(ExperimentResource, claim.result_resource_id)
            if receipt_resource is None or receipt_resource.kind != "run_clone_receipt":
                raise ValidationFailure("persisted run clone receipt authority is unavailable")
            return json.loads(claim.response_json)

        group = await session.get(ExperimentRunGroup, run_group_id)
        if group is None or group.workspace_id != project_id:
            raise NotFound("Run group not found")
        if group.generation != payload.expected_run_group_generation:
            raise RevisionConflict("Run group generation changed")
        if domain.current_revision_id != payload.expected_domain_revision_id:
            raise RevisionConflict("Domain revision changed")

        source_run = await session.get(ExperimentWorkflowRun, payload.source_run_id)
        source_attempt = await session.get(ExperimentRunAttempt, payload.source_attempt_id)
        if (
            source_run is None
            or source_run.workspace_id != project_id
            or source_run.run_group_id != run_group_id
            or source_attempt is None
            or source_attempt.workspace_id != project_id
            or source_attempt.workflow_run_id != source_run.resource_id
        ):
            raise ValidationFailure("source attempt does not belong to the exact source run and run group")
        source_preparation = await session.get(
            ExperimentWorkflowPreparation, source_attempt.preparation_id
        )
        source_revision = await session.get(
            ExperimentRevision,
            source_preparation.workflow_revision_id if source_preparation else "",
        )
        source_plan = await session.get(
            ExperimentAggregateHead, source_revision.subject_id if source_revision else ""
        )
        if (
            source_preparation is None
            or source_preparation.workspace_id != project_id
            or source_revision is None
            or source_plan is None
            or source_plan.aggregate_kind != "workflow"
            or source_plan.workspace_id != project_id
            or source_plan.parent_id != domain_id
        ):
            raise ValidationFailure("source attempt has no exact immutable Plan authority")
        source_authority, source_contract = await _stored_plan_authority(session, source_plan)
        source_payload = json.loads(source_revision.canonical_payload)
        validate_workflow_payload_for_plan(source_payload, source_contract)
        requested_settings = source_payload.get("parameters")
        effective_settings = json.loads(source_preparation.scheduler_payload_json).get("params")
        if not isinstance(requested_settings, dict) or not isinstance(effective_settings, dict):
            raise ValidationFailure("source preparation lacks complete requested/effective settings")
        copied_payload_sha256 = hashlib.sha256(
            source_revision.canonical_payload.encode("utf-8")
        ).hexdigest()
        if copied_payload_sha256 != source_revision.payload_sha256:
            raise ValidationFailure("source Workflow Plan revision payload digest mismatch")

        created_at = datetime.now(timezone.utc).isoformat()
        new_plan = await create_workflow(
            session,
            project_id,
            payload.new_workflow_name,
            source_plan.description,
            experiment_id=domain_id,
        )
        session.add(
            ExperimentWorkflowPlanAuthority(
                workflow_id=new_plan.aggregate_id,
                workspace_id=project_id,
                domain_experiment_id=domain_id,
                expected_domain_revision_id=payload.expected_domain_revision_id,
                capability_contract_json=source_authority.capability_contract_json,
                capability_contract_sha256=source_authority.capability_contract_sha256,
                created_at=created_at,
            )
        )
        new_draft = await session.scalar(
            select(ExperimentWorkflowDraft).where(
                ExperimentWorkflowDraft.workflow_id == new_plan.aggregate_id
            )
        )
        if new_draft is None or new_draft.generation != 0:
            raise ValidationFailure("fresh cloned Plan draft authority is unavailable")
        new_draft.base_revision_id = source_revision.resource_id
        new_draft.canonical_payload = source_revision.canonical_payload
        new_draft.updated_at = created_at

        lineage_edge = ExperimentLineageEdge(
            id=new_id("lineage"),
            workspace_id=project_id,
            source_resource_id=new_draft.resource_id,
            target_resource_id=source_revision.resource_id,
            edge_mode="derived_from",
            edge_key="cloned-plan-intent",
            metadata_json=canonical_json(
                {
                    "operation": "run-clone",
                    "source_run_group_id": run_group_id,
                    "source_run_id": source_run.resource_id,
                    "source_attempt_id": source_attempt.resource_id,
                    "change_summary": payload.change_summary,
                }
            ),
            created_at=created_at,
        )
        session.add(lineage_edge)
        receipt_resource_id = new_id("run-clone-receipt")
        session.add(
            ExperimentResource(
                id=receipt_resource_id,
                kind="run_clone_receipt",
                workspace_id=project_id,
                lifecycle_owner_id=new_plan.aggregate_id,
                created_at=created_at,
            )
        )
        await session.flush()
        receipt = {
            "schema": "bms.run-clone-receipt.v1",
            "clone_receipt_id": receipt_resource_id,
            "project_id": project_id,
            "global_experiment_id": experiment_id,
            "domain_experiment_id": domain_id,
            "domain_experiment_revision_id": payload.expected_domain_revision_id,
            "source_run_group_id": run_group_id,
            "source_run_id": source_run.resource_id,
            "source_attempt_id": source_attempt.resource_id,
            "source_preparation_id": source_preparation.resource_id,
            "source_workflow_plan_id": source_plan.aggregate_id,
            "source_workflow_revision_id": source_revision.resource_id,
            "source_capability_contract_sha256": source_authority.capability_contract_sha256,
            "source_requested_settings_sha256": hashlib.sha256(
                canonical_json(requested_settings).encode("utf-8")
            ).hexdigest(),
            "source_effective_settings_sha256": hashlib.sha256(
                canonical_json(effective_settings).encode("utf-8")
            ).hexdigest(),
            "new_workflow_plan_id": new_plan.aggregate_id,
            "new_draft_id": new_draft.resource_id,
            "new_draft_generation": 0,
            "copied_payload_sha256": copied_payload_sha256,
            "lineage_edge_id": lineage_edge.id,
            "lineage_mode": "derived_from",
            "lineage_source_resource_id": new_draft.resource_id,
            "lineage_target_resource_id": source_revision.resource_id,
            "lineage_edge_key": "cloned-plan-intent",
            "normalized_request_sha256": request_sha256,
            "created_by": actor,
            "created_at": created_at,
        }
        receipt["receipt_sha256"] = hashlib.sha256(
            canonical_json(receipt).encode("utf-8")
        ).hexdigest()
        session.add(
            ExperimentIdempotencyClaim(
                scope=scope,
                idempotency_key=key,
                request_sha256=request_sha256,
                result_resource_id=receipt_resource_id,
                response_json=canonical_json(receipt),
                created_at=created_at,
            )
        )
        add_audit_event(
            session,
            workspace_id=project_id,
            resource_id=domain_id,
            event_type="run_intent_cloned",
            generation=domain.head_generation,
            payload={
                "clone_receipt_id": receipt_resource_id,
                "source_run_group_id": run_group_id,
                "source_run_id": source_run.resource_id,
                "source_attempt_id": source_attempt.resource_id,
                "new_workflow_plan_id": new_plan.aggregate_id,
                "new_draft_id": new_draft.resource_id,
                "lineage_edge_id": lineage_edge.id,
                "change_summary": payload.change_summary,
            },
        )
        await session.commit()
        return receipt
    except ExperimentServiceError as exc:
        await session.rollback()
        raise _service_error(exc) from exc
    except IntegrityError:
        await session.rollback()
        if not scope or not key or not request_sha256:
            raise
        claim = await session.get(ExperimentIdempotencyClaim, (scope, key))
        if claim is None or claim.request_sha256 != request_sha256:
            raise
        receipt_resource = await session.get(ExperimentResource, claim.result_resource_id)
        if receipt_resource is None or receipt_resource.kind != "run_clone_receipt":
            raise ValidationFailure("persisted run clone receipt authority is unavailable")
        return json.loads(claim.response_json)


@router.post("/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/run-groups/{run_group_id}/cancel")
async def cancel_domain_run_group(project_id: str, experiment_id: str, domain_id: str, run_group_id: str, payload: RunGroupCancelRequest, request: Request, session: AsyncSession = Depends(get_experiment_session), core_session: AsyncSession = Depends(get_core_session), domain_session: AsyncSession = Depends(get_molbio_ngs_session)) -> dict:
    try:
        await _require_mutation_owner(request, session, resource_id=project_id)
        _project, _experiment, domain = await _domain_hierarchy(session, project_id, experiment_id, domain_id)
        await _active_ngs_binding(domain_session, project_id=project_id, experiment_id=experiment_id, domain=domain)
        command = await request_run_group_cancellation(
            session,
            workspace_id=project_id,
            run_group_id=run_group_id,
            idempotency_key=_idempotency_key(request),
            expected_generation=payload.expected_run_group_generation,
            reason=payload.reason,
            source_domain_id=domain_id,
        )
        if command.status not in {"applied", "conflicted"}:
            command = await process_run_control_command(
                session,
                core_session,
                command_id=command.command_id,
                worker_id=f"domain-cancel:{uuid.uuid4()}",
            )
        if command.status == "conflicted":
            raise HTTPException(
                409,
                detail={
                    "code": "run_control_conflicted",
                    "command_id": command.command_id,
                    "status": command.status,
                },
            )
        if command.status != "applied":
            raise HTTPException(
                409,
                detail={
                    "code": "run_control_pending",
                    "command_id": command.command_id,
                    "status": command.status,
                },
            )
        return command_document(command)
    except ExperimentServiceError as exc:
        await session.rollback()
        await core_session.rollback()
        raise _service_error(exc) from exc


@router.get("/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/results/{receipt_id}/surface")
async def reopen_domain_result(project_id: str, experiment_id: str, domain_id: str, receipt_id: str, session: AsyncSession = Depends(get_experiment_session)) -> dict:
    try:
        await _domain_hierarchy(session, project_id, experiment_id, domain_id)
        surface = await result_surface_for_receipt(session, project_id=project_id, receipt_id=receipt_id)
        receipt = await session.get(ExperimentResource, receipt_id)
        if receipt is None:
            raise NotFound("Result receipt not found")
        attached = await session.scalar(select(ExperimentLineageEdge).where(ExperimentLineageEdge.source_resource_id == domain_id, ExperimentLineageEdge.target_resource_id == receipt_id))
        if attached is None:
            raise NotFound("Result receipt not attached to Domain")
        return surface
    except ExperimentServiceError as exc:
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
