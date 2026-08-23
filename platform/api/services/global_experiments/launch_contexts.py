"""Durable opaque handoffs from global Domain Experiments to typed launchers."""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence
from urllib.parse import parse_qs, unquote, urlsplit

from sqlalchemy import case, exists, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import (
    ExperimentAggregateHead,
    ExperimentAuditEvent,
    ExperimentLaunchContext,
    ExperimentResource,
    ExperimentRevision,
    ExperimentRunAttempt,
    ExperimentRunControlCommand,
    ExperimentRunGroup,
    ExperimentValidation,
    ExperimentWorkflowPreparation,
    ExperimentWorkflowRun,
)
from services.rfd3_local_redesign import (
    local_redesign_requests_semantically_equal,
    prepare_local_redesign_scheduler_params,
)


LAUNCH_CONTEXT_SCHEMA = "bms.launch-context.v1"
PREPARED_LAUNCH_CONTEXT_SCHEMA = "bms.launch-context.v2"
BINDING_RECEIPT_SCHEMA = "bms.launch-context-binding.v1"
DEFAULT_TTL = timedelta(minutes=15)
MAX_STALE_CLAIM_RECOVERY_ROWS = 100
ACTIVE_CANCELLATION_COMMAND_STATES = {"pending", "leased", "retryable", "applied", "conflicted"}
DOMAIN_JOB_MODELS = {
    "protein_in_silico": {
        "boltz2", "boltz_cp_experimental", "boltzgen", "esmfold2", "molecular_dynamics",
        "ppiflow", "protein_local_redesign", "protein_modification_experimental", "protenix", "rf3",
        "template_antibody_denovo",
    },
    "ngs_molbio": {"nanopore", "ngs_alignment", "ont_fastq_qc", "sequence_qc", "oligo_builder", "oligo_design"},
}


class LaunchContextError(RuntimeError):
    """Fail-closed launch-context error with a stable API code."""

    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class _StaleClaimConflict(RuntimeError):
    """Internal fixed-code conflict used to roll back one recovery savepoint."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise LaunchContextError(
            "launch_context_invalid",
            "Launch context has an invalid server timestamp.",
            status_code=409,
        ) from exc
    if parsed.tzinfo is None:
        raise LaunchContextError(
            "launch_context_invalid",
            "Launch context has a timezone-naive server timestamp.",
            status_code=409,
        )
    return parsed.astimezone(timezone.utc)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _error_for_state(context: ExperimentLaunchContext) -> LaunchContextError:
    if context.state == "consumed":
        return LaunchContextError(
            "launch_context_consumed",
            "Launch context has already been consumed by a canonical Job submission.",
            status_code=409,
        )
    if context.state == "claimed" or (
        context.contract_version == "2" and context.state == "reserved" and context.claim_token
    ):
        return LaunchContextError(
            "launch_context_claimed",
            "Launch context is already claimed by another submission.",
            status_code=409,
        )
    return LaunchContextError(
        "launch_context_invalid_state",
        f"Launch context cannot be used from state {context.state!r}.",
        status_code=409,
    )


def _require_v2_context(context: ExperimentLaunchContext, *, operation: str) -> None:
    if context.contract_version != "2":
        raise LaunchContextError(
            "launch_context_version_read_only",
            f"Historical v1 launch contexts are display-only and cannot be {operation}.",
            status_code=409,
        )


def _ensure_live(context: ExperimentLaunchContext, *, at: datetime | None = None) -> None:
    if _parse_timestamp(context.expires_at) <= (at or _now()):
        raise LaunchContextError(
            "launch_context_expired",
            "Launch context has expired.",
            status_code=410,
        )


def _validate_return_uri(
    return_uri: str,
    *,
    project_id: str,
    global_experiment_id: str,
    domain_experiment_id: str,
    workflow_id: str | None,
) -> str:
    if not return_uri or len(return_uri) > 1000:
        raise LaunchContextError(
            "launch_context_return_uri_invalid",
            "Return URI must be a bounded internal Project route.",
            status_code=422,
        )
    split = urlsplit(return_uri)
    if split.scheme or split.netloc or split.fragment or not split.path.startswith("/") or split.path.startswith("//"):
        raise LaunchContextError(
            "launch_context_return_uri_invalid",
            "Return URI must be a same-origin Project route without a fragment.",
            status_code=422,
        )
    if unquote(split.path) != f"/projects/{project_id}":
        raise LaunchContextError(
            "launch_context_return_uri_mismatch",
            "Return URI does not target the launch context Project.",
            status_code=409,
        )
    try:
        query = parse_qs(split.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise LaunchContextError(
            "launch_context_return_uri_invalid",
            "Return URI query is malformed.",
            status_code=422,
        ) from exc
    if set(query) != {"focus", "selected"} or any(len(values) != 1 for values in query.values()):
        raise LaunchContextError(
            "launch_context_return_uri_invalid",
            "Return URI must contain exactly one focus and selected Project context.",
            status_code=422,
        )
    if query["focus"][0] != global_experiment_id:
        raise LaunchContextError(
            "launch_context_return_uri_mismatch",
            "Return URI focus does not match the Global Experiment.",
            status_code=409,
        )
    selected = query["selected"][0]
    if not selected or len(selected) > 255 or ":" not in selected:
        raise LaunchContextError(
            "launch_context_return_uri_invalid",
            "Return URI selection is malformed.",
            status_code=422,
        )
    return return_uri


async def _validate_return_selection(
    session: AsyncSession,
    *,
    project_id: str,
    global_experiment_id: str,
    selected_node_key: str,
) -> None:
    from services.global_experiments.read_models import build_project_manager_read_model

    summary = await build_project_manager_read_model(
        session,
        project_id=project_id,
        focus_id=global_experiment_id,
        selected_node_key=selected_node_key,
        run_limit=1,
        map_limit=1,
    )
    if summary["selection"]["node_key"] != selected_node_key:
        raise LaunchContextError(
            "launch_context_return_uri_mismatch",
            "Return URI selection is not owned by the bound Project context.",
            status_code=409,
        )


async def _validate_hierarchy(
    session: AsyncSession,
    *,
    project_id: str,
    global_experiment_id: str,
    domain_experiment_id: str,
    workflow_id: str | None,
    workflow_revision_id: str | None,
) -> None:
    project = await session.get(ExperimentAggregateHead, project_id)
    experiment = await session.get(ExperimentAggregateHead, global_experiment_id)
    domain = await session.get(ExperimentAggregateHead, domain_experiment_id)
    if project is None or project.aggregate_kind != "workspace":
        raise LaunchContextError("launch_context_hierarchy_mismatch", "Project is unavailable.", status_code=409)
    if (
        experiment is None
        or experiment.aggregate_kind != "experiment"
        or experiment.workspace_id != project_id
        or experiment.parent_id != project_id
    ):
        raise LaunchContextError(
            "launch_context_hierarchy_mismatch",
            "Global Experiment does not belong to the Project.",
            status_code=409,
        )
    if (
        domain is None
        or domain.aggregate_kind != "domain_experiment"
        or domain.workspace_id != project_id
        or domain.parent_id != global_experiment_id
    ):
        raise LaunchContextError(
            "launch_context_hierarchy_mismatch",
            "Domain Experiment does not belong to the Global Experiment.",
            status_code=409,
        )
    if any(head.lifecycle_state == "archived" for head in (project, experiment, domain)):
        raise LaunchContextError(
            "launch_context_hierarchy_unavailable",
            "Archived Project hierarchy cannot issue or consume launch contexts.",
            status_code=409,
        )
    if workflow_revision_id is not None and workflow_id is None:
        raise LaunchContextError(
            "launch_context_revision_mismatch",
            "Workflow Revision requires a Workflow identity.",
            status_code=409,
        )
    if workflow_id is None:
        return
    workflow = await session.get(ExperimentAggregateHead, workflow_id)
    if (
        workflow is None
        or workflow.aggregate_kind != "workflow"
        or workflow.workspace_id != project_id
        or workflow.parent_id != domain_experiment_id
        or workflow.lifecycle_state == "archived"
    ):
        raise LaunchContextError(
            "launch_context_hierarchy_mismatch",
            "Workflow does not belong to the Domain Experiment.",
            status_code=409,
        )
    if workflow_revision_id is not None:
        revision = await session.get(ExperimentRevision, workflow_revision_id)
        if revision is None or revision.subject_id != workflow_id:
            raise LaunchContextError(
                "launch_context_revision_mismatch",
                "Workflow Revision does not belong to the Workflow.",
                status_code=409,
            )


async def workflow_pinned_gpu(
    session: AsyncSession,
    context: ExperimentLaunchContext,
) -> int | None:
    """Resolve an immutable native RFD3 GPU pin from the bound Workflow Revision."""
    if context.workflow_revision_id is None:
        return None
    revision = await session.get(ExperimentRevision, context.workflow_revision_id)
    if revision is None or revision.subject_id != context.workflow_id:
        raise LaunchContextError(
            "launch_context_revision_mismatch",
            "Workflow Revision does not belong to the Workflow.",
            status_code=409,
        )
    decoded_payload = json.loads(revision.canonical_payload)
    payload = decoded_payload if isinstance(decoded_payload, dict) else {}
    raw_scheduler = payload.get("scheduler")
    scheduler: dict[str, Any] = raw_scheduler if isinstance(raw_scheduler, dict) else {}
    if scheduler.get("model_id") != "protein_local_redesign":
        return None
    raw_resources = scheduler.get("resources")
    resources: dict[str, Any] = raw_resources if isinstance(raw_resources, dict) else {}
    pinned_gpu = resources.get("pinned_gpu")
    if isinstance(pinned_gpu, bool) or not isinstance(pinned_gpu, int) or pinned_gpu < 0:
        raise LaunchContextError(
            "launch_context_workflow_invalid",
            "Native RFD3 Workflow Revision has no authoritative pinned GPU.",
            status_code=409,
        )
    return pinned_gpu


async def _attach_typed_resource_authority(
    session: AsyncSession,
    context: ExperimentLaunchContext,
    params: dict[str, Any],
    pinned_gpu: int | None,
) -> dict[str, Any]:
    if not context.run_attempt_id or not context.normalized_request_sha256:
        raise LaunchContextError(
            "launch_context_resource_authority_missing",
            "Prepared launch context has no resource authority.",
            status_code=409,
        )
    attempt = await session.get(ExperimentRunAttempt, context.run_attempt_id)
    if attempt is None:
        raise LaunchContextError(
            "launch_context_resource_authority_missing",
            "Prepared launch attempt is unavailable.",
            status_code=409,
        )
    from experiment_services import ExperimentServiceError
    from services.ngs_molbio_n5 import resource_admission_handoff_for_attempt
    from services.resource_usage_evidence import (
        GLOBAL_DISPATCH_AUTHORITY_PARAM,
        GLOBAL_RESOURCE_ADMISSION_PARAM,
        RESOURCE_USAGE_RECEIPTS_PARAM,
        ResourceUsageEvidenceError,
        attach_dispatch_materialization_authority,
        attach_resource_admission_handoff,
        build_dispatch_materialization_authority,
    )

    if any(
        key in params
        for key in (
            GLOBAL_RESOURCE_ADMISSION_PARAM,
            GLOBAL_DISPATCH_AUTHORITY_PARAM,
            RESOURCE_USAGE_RECEIPTS_PARAM,
        )
    ):
        raise LaunchContextError(
            "launch_context_resource_authority_conflict",
            "Resource authority fields are server-owned.",
            status_code=409,
        )
    try:
        handoff = await resource_admission_handoff_for_attempt(
            session,
            run_attempt_id=attempt.resource_id,
            canonical_job_id=attempt.scheduler_job_id,
        )
        if handoff["gpu_index"] != pinned_gpu:
            raise ResourceUsageEvidenceError("pinned GPU differs from resource admission")
        dispatch = build_dispatch_materialization_authority(
            payload_sha256=context.normalized_request_sha256,
            handoff=handoff,
        )
        prepared = attach_resource_admission_handoff(params, handoff)
        return attach_dispatch_materialization_authority(prepared, dispatch)
    except (ExperimentServiceError, ResourceUsageEvidenceError) as exc:
        raise LaunchContextError(
            "launch_context_resource_authority_invalid",
            "Prepared launch resource authority is invalid.",
            status_code=409,
        ) from exc


async def publish_launch_context_binding(
    core_session: AsyncSession,
    *,
    context: ExperimentLaunchContext,
    job: Any,
    binding: dict[str, Any],
) -> None:
    """Publish the core scheduler gate only after the source binding commits."""
    from database import (
        Job,
        LAUNCH_CONTEXT_BINDING_PROVENANCE_KEY,
        LAUNCH_CONTEXT_BINDING_PROVENANCE_SCHEMA,
    )

    if (
        binding.get("schema") != "bms.launch-context-binding.v2"
        or binding.get("launch_context_id") != context.launch_context_id
        or binding.get("run_attempt_id") != context.run_attempt_id
        or binding.get("canonical_job_id") != str(job.id)
    ):
        raise LaunchContextError(
            "launch_context_binding_invalid",
            "Launch binding does not match its prepared context and canonical Job.",
            status_code=409,
        )
    marker = {
        "schema": LAUNCH_CONTEXT_BINDING_PROVENANCE_SCHEMA,
        "launch_context_id": context.launch_context_id,
        "run_attempt_id": context.run_attempt_id,
        "canonical_job_id": str(job.id),
        "binding_receipt_sha256": hashlib.sha256(
            _canonical_json(binding).encode("utf-8")
        ).hexdigest(),
    }
    provenance = dict(getattr(job, "provenance", None) or {})
    if provenance.get("launch_context_id") != context.launch_context_id:
        raise LaunchContextError(
            "launch_context_job_provenance_mismatch",
            "Job launch-context provenance changed before binding publication.",
            status_code=409,
        )
    existing = provenance.get(LAUNCH_CONTEXT_BINDING_PROVENANCE_KEY)
    if existing is not None:
        if existing != marker:
            raise LaunchContextError(
                "launch_context_binding_conflict",
                "Job has a different durable launch binding.",
                status_code=409,
            )
        return
    observed_provenance = dict(provenance)
    provenance[LAUNCH_CONTEXT_BINDING_PROVENANCE_KEY] = marker
    result = await core_session.execute(
        update(Job)
        .where(Job.id == job.id, Job.provenance == observed_provenance)
        .values(provenance=provenance)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await core_session.rollback()
        current = await core_session.get(Job, job.id)
        if current is None or dict(current.provenance or {}).get(
            LAUNCH_CONTEXT_BINDING_PROVENANCE_KEY
        ) != marker:
            raise LaunchContextError(
                "launch_context_binding_conflict",
                "Job changed before durable launch binding publication.",
                status_code=409,
            )
        return
    await core_session.commit()
    await core_session.refresh(job)


def normalize_bound_job_params(
    *,
    supplied_params: dict[str, Any],
    expected_params: dict[str, Any],
) -> dict[str, Any]:
    expected_adapter = expected_params.get("workflow_adapter")
    supplied_adapter = supplied_params.get("workflow_adapter")
    if not isinstance(expected_adapter, str) or not expected_adapter:
        raise LaunchContextError(
            "launch_context_workflow_mismatch",
            "Bound Workflow Revision has no workflow adapter authority.",
            status_code=409,
        )
    if supplied_adapter not in (None, expected_adapter):
        raise LaunchContextError(
            "launch_context_workflow_mismatch",
            "Native workflow adapter does not match the bound Workflow Revision.",
            status_code=409,
        )
    return {**supplied_params, "workflow_adapter": expected_adapter}


async def validate_bound_job_request(
    session: AsyncSession,
    context: ExperimentLaunchContext,
    *,
    job_name: str,
    model_id: str,
    mode: str,
    params: dict[str, Any],
    pinned_gpu: int | None,
) -> dict[str, Any]:
    """Validate immutable Workflow Revision authority before any Job transaction."""
    _require_v2_context(context, operation="used for Job submission")
    domain = await session.get(ExperimentAggregateHead, context.domain_experiment_id)
    if domain is None or domain.current_revision_id is None:
        raise LaunchContextError(
            "launch_context_domain_unavailable",
            "Domain Experiment authority is unavailable.",
            status_code=409,
        )
    domain_revision = await session.get(ExperimentRevision, domain.current_revision_id)
    decoded_domain_payload = json.loads(domain_revision.canonical_payload) if domain_revision is not None else {}
    domain_payload = decoded_domain_payload if isinstance(decoded_domain_payload, dict) else {}
    domain_kind = str(domain_payload.get("domain_kind") or "")
    if model_id not in DOMAIN_JOB_MODELS.get(domain_kind, set()):
        raise LaunchContextError(
            "launch_context_model_mismatch",
            "Job model is outside the bound Domain capability.",
            status_code=409,
        )
    if context.workflow_revision_id is None:
        if model_id == "protein_local_redesign" and "workflow_adapter" in params:
            raise LaunchContextError(
                "launch_context_workflow_mismatch",
                "Native RFD3 workflow_adapter is server-owned.",
                status_code=409,
            )
        return await _attach_typed_resource_authority(
            session,
            context,
            dict(params),
            pinned_gpu,
        )

    revision = await session.get(ExperimentRevision, context.workflow_revision_id)
    decoded_payload = json.loads(revision.canonical_payload) if revision is not None else {}
    payload = decoded_payload if isinstance(decoded_payload, dict) else {}
    raw_scheduler = payload.get("scheduler")
    scheduler: dict[str, Any] = raw_scheduler if isinstance(raw_scheduler, dict) else {}
    expected_adapter = str(payload.get("adapter_id") or "")
    raw_expected_params = scheduler.get("params")
    expected_params: dict[str, Any] = dict(raw_expected_params) if isinstance(raw_expected_params, dict) else {}
    if expected_adapter:
        expected_params.setdefault("workflow_adapter", expected_adapter)
    if scheduler.get("model_id") != model_id or scheduler.get("mode") != mode:
        raise LaunchContextError(
            "launch_context_workflow_mismatch",
            "Job model or mode does not match the bound Workflow Revision.",
            status_code=409,
        )
    prepared_params = normalize_bound_job_params(
        supplied_params=params,
        expected_params=expected_params,
    )
    params_match = _canonical_json(prepared_params) == _canonical_json(expected_params)
    if model_id == "nanopore":
        params_match = all(
            key in prepared_params and prepared_params[key] == value
            for key, value in expected_params.items()
        )
    if model_id == "protein_local_redesign":
        supplied_adapter = params.get("workflow_adapter")
        if supplied_adapter not in (None, expected_adapter):
            raise LaunchContextError(
                "launch_context_workflow_mismatch",
                "Native RFD3 workflow_adapter does not match the bound Workflow Revision.",
                status_code=409,
            )
        try:
            actual_params = prepare_local_redesign_scheduler_params(
                {**params, "workflow_adapter": expected_adapter},
                job_name=job_name,
                expected_adapter_id=expected_adapter,
            )
            expected_native_params = prepare_local_redesign_scheduler_params(
                expected_params,
                job_name=str(scheduler.get("name") or ""),
                expected_adapter_id=expected_adapter,
            )
        except Exception as exc:
            raise LaunchContextError(
                "launch_context_workflow_mismatch",
                "Job request does not satisfy the bound native RFD3 Workflow Revision.",
                status_code=409,
            ) from exc
        actual_request = actual_params.get("rfd3_request")
        expected_request = expected_native_params.get("rfd3_request")
        params_match = (
            isinstance(actual_request, dict)
            and isinstance(expected_request, dict)
            and local_redesign_requests_semantically_equal(actual_request, expected_request)
        )
        prepared_params = {**params, "workflow_adapter": expected_adapter}
    resources = scheduler.get("resources")
    expected_pinned_gpu = resources.get("pinned_gpu") if isinstance(resources, dict) else None
    if (
        scheduler.get("model_id") != model_id
        or scheduler.get("mode") != mode
        or not params_match
        or (model_id == "protein_local_redesign" and expected_pinned_gpu != pinned_gpu)
    ):
        mismatched_params = sorted(
            key for key, value in expected_params.items()
            if key not in params or params.get(key) != value
        )
        detail = (
            "Job request does not match the bound Workflow Revision: "
            f"expected model/mode {scheduler.get('model_id')}/{scheduler.get('mode')}, "
            f"received {model_id}/{mode}, mismatched params={mismatched_params}."
        )
        raise LaunchContextError(
            "launch_context_workflow_mismatch",
            detail,
            status_code=409,
        )
    return await _attach_typed_resource_authority(
        session,
        context,
        prepared_params,
        pinned_gpu,
    )


def context_document(context: ExperimentLaunchContext) -> dict[str, Any]:
    """Return the closed public document for the persisted contract version."""
    document = {
        "schema": PREPARED_LAUNCH_CONTEXT_SCHEMA if context.contract_version == "2" else LAUNCH_CONTEXT_SCHEMA,
        "launch_context_id": context.launch_context_id,
        "project_id": context.project_id,
        "global_experiment_id": context.global_experiment_id,
        "domain_experiment_id": context.domain_experiment_id,
        "workflow_id": context.workflow_id,
        "workflow_revision_id": context.workflow_revision_id,
        "return_uri": context.return_uri,
        "source_receipt_id": context.source_receipt_id,
        "state": context.state,
        "canonical_job_id": context.canonical_job_id,
        "binding_receipt": json.loads(context.binding_receipt_json) if context.binding_receipt_json else None,
        "issued_at": context.issued_at,
        "expires_at": context.expires_at,
    }
    if context.contract_version == "2":
        document.update(
            preparation_id=context.preparation_id,
            run_attempt_id=context.run_attempt_id,
            normalized_request_sha256=context.normalized_request_sha256,
            validation_receipt_id=context.validation_receipt_id,
            validation_receipt_sha256=context.validation_receipt_sha256,
        )
    return document


async def create_prepared_launch_context(
    session: AsyncSession,
    *,
    project_id: str,
    global_experiment_id: str,
    domain_experiment_id: str,
    preparation_id: str,
    return_uri: str,
) -> ExperimentLaunchContext:
    """Issue v2 handoff authority bound to one immutable valid preparation."""
    preparation = await session.get(ExperimentWorkflowPreparation, preparation_id)
    if preparation is None or preparation.workspace_id != project_id:
        raise LaunchContextError("launch_context_preparation_unknown", "Preparation is unavailable.", status_code=404)
    revision = await session.get(ExperimentRevision, preparation.workflow_revision_id)
    workflow = await session.get(ExperimentAggregateHead, revision.subject_id if revision else "")
    if (
        revision is None
        or workflow is None
        or workflow.aggregate_kind != "workflow"
        or workflow.workspace_id != project_id
        or workflow.parent_id != domain_experiment_id
        or preparation.validation_status != "valid"
        or not preparation.validation_resource_id
    ):
        raise LaunchContextError("launch_context_preparation_invalid", "Preparation is not valid in this Domain.", status_code=409)
    validation = await session.get(ExperimentValidation, preparation.validation_resource_id)
    if (
        validation is None
        or validation.subject_resource_id != preparation_id
        or validation.outcome != "valid"
        or validation.receipt_json != preparation.validation_receipt_json
    ):
        raise LaunchContextError("launch_context_preparation_invalid", "Preparation validation authority is unavailable.", status_code=409)
    await _validate_hierarchy(
        session,
        project_id=project_id,
        global_experiment_id=global_experiment_id,
        domain_experiment_id=domain_experiment_id,
        workflow_id=workflow.aggregate_id,
        workflow_revision_id=revision.resource_id,
    )
    validated_return_uri = _validate_return_uri(
        return_uri,
        project_id=project_id,
        global_experiment_id=global_experiment_id,
        domain_experiment_id=domain_experiment_id,
        workflow_id=workflow.aggregate_id,
    )
    selected = parse_qs(urlsplit(validated_return_uri).query, strict_parsing=True)["selected"][0]
    await _validate_return_selection(
        session, project_id=project_id, global_experiment_id=global_experiment_id,
        selected_node_key=selected,
    )
    issued = _now()
    launch_context_id = f"launch-context:{uuid.uuid4()}"
    session.add(ExperimentResource(
        id=launch_context_id, kind="launch_context", workspace_id=project_id,
        lifecycle_owner_id=preparation_id, created_at=_timestamp(issued),
    ))
    await session.flush()
    context = ExperimentLaunchContext(
        launch_context_id=launch_context_id,
        project_id=project_id,
        global_experiment_id=global_experiment_id,
        domain_experiment_id=domain_experiment_id,
        workflow_id=workflow.aggregate_id,
        workflow_revision_id=revision.resource_id,
        preparation_id=preparation_id,
        contract_version="2",
        normalized_request_sha256=preparation.normalized_request_sha256,
        validation_receipt_id=validation.resource_id,
        validation_receipt_sha256=validation.receipt_sha256,
        source_receipt_id=revision.resource_id,
        return_uri=validated_return_uri,
        state="issued",
        issued_at=_timestamp(issued),
        expires_at=_timestamp(issued + DEFAULT_TTL),
    )
    session.add(context)
    await session.flush()
    return context


async def create_launch_context(
    session: AsyncSession,
    *,
    project_id: str,
    global_experiment_id: str,
    domain_experiment_id: str,
    workflow_id: str | None,
    workflow_revision_id: str | None,
    return_uri: str,
) -> ExperimentLaunchContext:
    raise LaunchContextError(
        "launch_context_version_read_only",
        "Historical v1 launch contexts are display-only and new v1 contexts cannot be issued.",
        status_code=409,
    )


async def resolve_launch_context(session: AsyncSession, launch_context_id: str) -> ExperimentLaunchContext:
    context = await session.get(ExperimentLaunchContext, launch_context_id)
    if context is None:
        raise LaunchContextError(
            "launch_context_unknown",
            "Launch context is unknown.",
            status_code=404,
        )
    _require_v2_context(context, operation="claimed or dispatched")
    _ensure_live(context)
    if context.state != "reserved":
        raise _error_for_state(context)
    preparation = await session.get(ExperimentWorkflowPreparation, context.preparation_id)
    attempt = await session.get(ExperimentRunAttempt, context.run_attempt_id)
    validation = await session.get(ExperimentValidation, context.validation_receipt_id)
    if (
        preparation is None or attempt is None or validation is None
        or attempt.preparation_id != preparation.resource_id
        or attempt.resource_id != context.run_attempt_id
        or attempt.workspace_id != context.project_id
        or preparation.workspace_id != context.project_id
        or attempt.state != "pending"
        or attempt.external_binding_receipt_json is not None
        or attempt.terminal_receipt_json is not None
        or attempt.terminal_receipt_sha256 is not None
        or preparation.normalized_request_sha256 != context.normalized_request_sha256
        or validation.subject_resource_id != preparation.resource_id
        or validation.outcome != "valid"
        or validation.receipt_json != preparation.validation_receipt_json
        or validation.receipt_sha256 != context.validation_receipt_sha256
    ):
        raise LaunchContextError("launch_context_binding_invalid", "Prepared launch authority is stale or inconsistent.", status_code=409)
    run = await session.get(ExperimentWorkflowRun, attempt.workflow_run_id)
    group = await session.get(
        ExperimentRunGroup,
        run.run_group_id if run is not None else "",
    )
    if (
        run is None
        or group is None
        or run.workspace_id != context.project_id
        or group.workspace_id != context.project_id
        or run.preparation_id != preparation.resource_id
        or run.state != "dispatch_pending"
    ):
        raise LaunchContextError(
            "launch_context_attempt_unavailable",
            "Prepared attempt is no longer admitted for launch.",
            status_code=409,
        )
    cancellation_command = await session.scalar(
        select(ExperimentRunControlCommand.command_id)
        .where(
            ExperimentRunControlCommand.run_group_id == group.resource_id,
            ExperimentRunControlCommand.command_type == "cancel",
            ExperimentRunControlCommand.status.in_(ACTIVE_CANCELLATION_COMMAND_STATES),
        )
        .limit(1)
    )
    if cancellation_command is not None:
        raise LaunchContextError(
            "launch_context_cancellation_pending",
            "Prepared attempt is closed by durable cancellation authority.",
            status_code=409,
        )
    await _validate_hierarchy(
        session,
        project_id=context.project_id,
        global_experiment_id=context.global_experiment_id,
        domain_experiment_id=context.domain_experiment_id,
        workflow_id=context.workflow_id,
        workflow_revision_id=context.workflow_revision_id,
    )
    _validate_return_uri(
        context.return_uri,
        project_id=context.project_id,
        global_experiment_id=context.global_experiment_id,
        domain_experiment_id=context.domain_experiment_id,
        workflow_id=context.workflow_id,
    )
    return context


async def resolve_launch_context_for_display(
    session: AsyncSession,
    launch_context_id: str,
) -> ExperimentLaunchContext:
    context = await session.get(ExperimentLaunchContext, launch_context_id)
    if context is None:
        raise LaunchContextError("launch_context_unknown", "Launch context is unknown.", status_code=404)
    if context.state in {"issued", "claimed", "reserved"}:
        if context.state == "issued":
            _ensure_live(context)
        await _validate_hierarchy(
            session,
            project_id=context.project_id,
            global_experiment_id=context.global_experiment_id,
            domain_experiment_id=context.domain_experiment_id,
            workflow_id=context.workflow_id,
            workflow_revision_id=context.workflow_revision_id,
        )
        _validate_return_uri(
            context.return_uri,
            project_id=context.project_id,
            global_experiment_id=context.global_experiment_id,
            domain_experiment_id=context.domain_experiment_id,
            workflow_id=context.workflow_id,
        )
        return context
    if context.state != "consumed" or not context.binding_receipt_json:
        raise _error_for_state(context)
    try:
        receipt = json.loads(context.binding_receipt_json)
    except (TypeError, ValueError) as exc:
        raise LaunchContextError(
            "launch_context_binding_invalid",
            "Launch context binding receipt is invalid.",
            status_code=409,
        ) from exc
    expected = {
        "launch_context_id": context.launch_context_id,
        "canonical_job_id": context.canonical_job_id,
        "project_id": context.project_id,
        "global_experiment_id": context.global_experiment_id,
        "domain_experiment_id": context.domain_experiment_id,
        "workflow_id": context.workflow_id,
        "workflow_revision_id": context.workflow_revision_id,
        "return_uri": context.return_uri,
        "verified": True,
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise LaunchContextError(
            "launch_context_binding_invalid",
            "Launch context binding receipt does not match persisted identity.",
            status_code=409,
        )
    await _validate_hierarchy(
        session,
        project_id=context.project_id,
        global_experiment_id=context.global_experiment_id,
        domain_experiment_id=context.domain_experiment_id,
        workflow_id=context.workflow_id,
        workflow_revision_id=context.workflow_revision_id,
    )
    _validate_return_uri(
        context.return_uri,
        project_id=context.project_id,
        global_experiment_id=context.global_experiment_id,
        domain_experiment_id=context.domain_experiment_id,
        workflow_id=context.workflow_id,
    )
    return context


async def validate_bound_job(
    session: AsyncSession,
    context: ExperimentLaunchContext,
    job: Any,
) -> None:
    _require_v2_context(context, operation="bound to a Job")
    await _validate_hierarchy(
        session,
        project_id=context.project_id,
        global_experiment_id=context.global_experiment_id,
        domain_experiment_id=context.domain_experiment_id,
        workflow_id=context.workflow_id,
        workflow_revision_id=context.workflow_revision_id,
    )
    provenance = dict(job.provenance or {})
    if provenance.get("launch_context_id") != context.launch_context_id:
        raise LaunchContextError("launch_context_job_provenance_mismatch", "Job was not created for this launch context.", status_code=409)
    domain = await session.get(ExperimentAggregateHead, context.domain_experiment_id)
    if domain is None or domain.current_revision_id is None:
        raise LaunchContextError("launch_context_domain_unavailable", "Domain Experiment authority is unavailable.", status_code=409)
    domain_revision = await session.get(ExperimentRevision, domain.current_revision_id)
    decoded_domain_payload = json.loads(domain_revision.canonical_payload) if domain_revision is not None else {}
    domain_payload = decoded_domain_payload if isinstance(decoded_domain_payload, dict) else {}
    domain_kind = str(domain_payload.get("domain_kind") or "")
    if str(job.model_id) not in DOMAIN_JOB_MODELS.get(domain_kind, set()):
        raise LaunchContextError("launch_context_model_mismatch", "Job model is outside the bound Domain capability.", status_code=409)
    from services.execution_ownership import strip_execution_metadata
    from services.resource_usage_evidence import (
        RESOURCE_USAGE_RECEIPTS_PARAM,
        ResourceUsageEvidenceError,
        strip_resource_execution_metadata,
    )

    raw_job_params = job.params
    job_params: dict[str, Any] = raw_job_params if isinstance(raw_job_params, dict) else {}
    base_job_params = strip_execution_metadata(strip_resource_execution_metadata(job_params))
    try:
        expected_authoritative_params = await _attach_typed_resource_authority(
            session,
            context,
            base_job_params,
            job.pinned_gpu,
        )
        actual_authoritative_params = strip_execution_metadata(job_params)
        actual_authoritative_params.pop(RESOURCE_USAGE_RECEIPTS_PARAM, None)
        resource_authority_matches = actual_authoritative_params == expected_authoritative_params
    except (LaunchContextError, ResourceUsageEvidenceError):
        resource_authority_matches = False
    if not resource_authority_matches:
        raise LaunchContextError(
            "launch_context_resource_authority_mismatch",
            "Job resource authority differs from the reserved attempt.",
            status_code=409,
        )
    if context.workflow_revision_id:
        revision = await session.get(ExperimentRevision, context.workflow_revision_id)
        decoded_payload = json.loads(revision.canonical_payload) if revision is not None else {}
        payload = decoded_payload if isinstance(decoded_payload, dict) else {}
        raw_scheduler = payload.get("scheduler")
        scheduler: dict[str, Any] = raw_scheduler if isinstance(raw_scheduler, dict) else {}
        expected_adapter = payload.get("adapter_id")
        job_adapter = base_job_params.get("workflow_adapter")
        raw_expected_params = scheduler.get("params")
        expected_params: dict[str, Any] = raw_expected_params if isinstance(raw_expected_params, dict) else {}
        expected_job_params = dict(expected_params)
        if expected_adapter is not None:
            expected_job_params["workflow_adapter"] = expected_adapter
        raw_resources = scheduler.get("resources")
        resources: dict[str, Any] = raw_resources if isinstance(raw_resources, dict) else {}
        expected_pinned_gpu = resources.get("pinned_gpu")
        params_match = all(
            base_job_params.get(key, 1 if key == "num_parallel_jobs" else object()) == value
            for key, value in expected_job_params.items()
        )
        if job.model_id == "protein_local_redesign":
            try:
                expected_native_params = prepare_local_redesign_scheduler_params(
                    expected_job_params,
                    job_name=str(scheduler.get("name") or ""),
                    expected_adapter_id=str(expected_adapter or ""),
                )
            except Exception:
                expected_native_params = {}
            expected_request = expected_native_params.get("rfd3_request")
            observed_request = base_job_params.get("rfd3_request")
            params_match = (
                job_adapter == expected_adapter
                and isinstance(expected_request, dict)
                and isinstance(observed_request, dict)
                and local_redesign_requests_semantically_equal(observed_request, expected_request)
            )
        if (
            scheduler.get("model_id") != job.model_id
            or scheduler.get("mode") != job.mode
            or expected_adapter != job_adapter
            or not params_match
            or (
                job.model_id == "protein_local_redesign"
                and expected_pinned_gpu != job.pinned_gpu
            )
        ):
            raise LaunchContextError("launch_context_workflow_mismatch", "Job does not match the bound Workflow Revision.", status_code=409)


async def claim_launch_context(session: AsyncSession, launch_context_id: str) -> tuple[ExperimentLaunchContext, str]:
    context = await resolve_launch_context(session, launch_context_id)
    claimed_at = _timestamp(_now())
    claim_token = secrets.token_urlsafe(32)
    result = await session.execute(
        update(ExperimentLaunchContext)
        .where(
            ExperimentLaunchContext.launch_context_id == launch_context_id,
            ExperimentLaunchContext.contract_version == "2",
            ExperimentLaunchContext.state == "reserved",
            ExperimentLaunchContext.claim_token.is_(None),
        )
        .values(claim_token=claim_token, claimed_at=claimed_at)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.expire(context)
        current = await session.get(ExperimentLaunchContext, launch_context_id)
        if current is None:
            raise LaunchContextError("launch_context_unknown", "Launch context is unknown.", status_code=404)
        raise _error_for_state(current)
    await session.flush()
    await session.refresh(context)
    return context, claim_token


async def release_launch_context_claim(
    session: AsyncSession,
    *,
    launch_context_id: str,
    claim_token: str,
) -> None:
    """Release only this caller's unconsumed claim after a handled submission failure."""
    context = await session.get(ExperimentLaunchContext, launch_context_id)
    if context is None:
        raise LaunchContextError("launch_context_unknown", "Launch context is unknown.", status_code=404)
    _require_v2_context(context, operation="released")
    await session.execute(
        update(ExperimentLaunchContext)
        .where(
            ExperimentLaunchContext.launch_context_id == launch_context_id,
            ExperimentLaunchContext.contract_version == "2",
            ExperimentLaunchContext.state == "reserved",
            ExperimentLaunchContext.claim_token == claim_token,
        )
        .values(claim_token=None, claimed_at=None)
        .execution_options(synchronize_session=False)
    )
    await session.flush()


async def consume_launch_context(
    session: AsyncSession,
    *,
    launch_context_id: str,
    claim_token: str,
    canonical_job_id: str,
    canonical_batch_id: str | None,
    stale_claimed_at: str | None = None,
) -> tuple[ExperimentLaunchContext, dict[str, Any]]:
    context = await session.get(ExperimentLaunchContext, launch_context_id)
    if context is None:
        raise LaunchContextError("launch_context_unknown", "Launch context is unknown.", status_code=404)
    _require_v2_context(context, operation="consumed or dispatched")
    if stale_claimed_at is None:
        _ensure_live(context)
    elif context.claimed_at != stale_claimed_at:
        raise LaunchContextError(
            "launch_context_claim_mismatch",
            "Launch context claim timestamp no longer belongs to this submission.",
            status_code=409,
        )
    expected_state = "reserved"
    if context.state != expected_state or not secrets.compare_digest(context.claim_token or "", claim_token):
        raise LaunchContextError(
            "launch_context_claim_mismatch",
            "Launch context claim no longer belongs to this submission.",
            status_code=409,
        )
    await _validate_hierarchy(
        session,
        project_id=context.project_id,
        global_experiment_id=context.global_experiment_id,
        domain_experiment_id=context.domain_experiment_id,
        workflow_id=context.workflow_id,
        workflow_revision_id=context.workflow_revision_id,
    )
    _validate_return_uri(
        context.return_uri,
        project_id=context.project_id,
        global_experiment_id=context.global_experiment_id,
        domain_experiment_id=context.domain_experiment_id,
        workflow_id=context.workflow_id,
    )
    if not canonical_job_id:
        raise LaunchContextError(
            "launch_context_binding_invalid",
            "Canonical Job identity is required to consume a launch context.",
            status_code=409,
        )
    consumed_at = _timestamp(_now())
    receipt = {
        "schema": BINDING_RECEIPT_SCHEMA,
        "launch_context_id": context.launch_context_id,
        "canonical_store_id": "core.jobs",
        "canonical_job_id": canonical_job_id,
        "canonical_batch_id": canonical_batch_id,
        "project_id": context.project_id,
        "global_experiment_id": context.global_experiment_id,
        "domain_experiment_id": context.domain_experiment_id,
        "workflow_id": context.workflow_id,
        "workflow_revision_id": context.workflow_revision_id,
        "return_uri": context.return_uri,
        "bound_at": consumed_at,
        "verified": True,
    }
    if context.contract_version == "2":
        attempt = await session.get(ExperimentRunAttempt, context.run_attempt_id)
        if attempt is None or attempt.scheduler_job_id != canonical_job_id:
            raise LaunchContextError("launch_context_job_mismatch", "Canonical Job does not match the reserved attempt.", status_code=409)
        receipt.update(
            schema="bms.launch-context-binding.v2",
            preparation_id=context.preparation_id,
            run_attempt_id=context.run_attempt_id,
            normalized_request_sha256=context.normalized_request_sha256,
            validation_receipt_id=context.validation_receipt_id,
            validation_receipt_sha256=context.validation_receipt_sha256,
        )
    result = await session.execute(
        update(ExperimentLaunchContext)
        .where(
            ExperimentLaunchContext.launch_context_id == launch_context_id,
            ExperimentLaunchContext.contract_version == "2",
            ExperimentLaunchContext.state == expected_state,
            ExperimentLaunchContext.claim_token == claim_token,
            ExperimentLaunchContext.claimed_at == context.claimed_at,
            ExperimentLaunchContext.canonical_job_id.is_(None),
        )
        .values(
            state="consumed",
            canonical_job_id=canonical_job_id,
            binding_receipt_json=_canonical_json(receipt),
            consumed_at=consumed_at,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise LaunchContextError(
            "launch_context_claim_mismatch",
            "Launch context could not be consumed by this submission.",
            status_code=409,
        )
    await session.flush()
    await session.refresh(context)
    return context, receipt


def _stale_claim_conflict_audit_id(
    launch_context_id: str,
    claim_token: str,
    claimed_at: str,
) -> str:
    marker = uuid.uuid5(
        uuid.UUID("3b89019e-2755-49e8-ab5a-95e13cfefb6e"),
        f"{launch_context_id}:{claim_token}:{claimed_at}",
    )
    return f"launch-claim-conflict:{marker}"


async def _append_stale_claim_conflict(
    session: AsyncSession,
    *,
    launch_context_id: str,
    workspace_id: str,
    run_attempt_id: str | None,
    claim_token: str,
    claimed_at: str,
    code: str,
) -> str:
    """Append one deterministic, sanitized marker for this exact stale claim."""
    audit_id = _stale_claim_conflict_audit_id(
        launch_context_id,
        claim_token,
        claimed_at,
    )
    if await session.get(ExperimentAuditEvent, audit_id) is None:
        attempt = await session.get(ExperimentRunAttempt, run_attempt_id or "")
        payload = {
            "schema": "bms.launch-context-stale-claim-conflict.v1",
            "launch_context_id": launch_context_id,
            "run_attempt_id": run_attempt_id,
            "scheduler_job_id": attempt.scheduler_job_id if attempt is not None else None,
            "conflict_code": code,
        }
        session.add(
            ExperimentAuditEvent(
                id=audit_id,
                workspace_id=workspace_id,
                resource_id=launch_context_id,
                event_type="launch_context_stale_claim_conflict",
                generation=0,
                payload_json=_canonical_json(payload),
                created_at=_timestamp(_now()),
            )
        )
        await session.flush()
    return audit_id


async def _recover_stale_typed_claim_candidate(
    session: AsyncSession,
    core_session: AsyncSession,
    candidate: ExperimentLaunchContext,
) -> str:
    """Recover one candidate under a savepoint without committing its transaction."""
    from database import Job
    from experiment_services import derive_run_group_state, validate_preparation_authority

    launch_context_id = str(candidate.launch_context_id)
    claim_token = str(candidate.claim_token)
    claimed_at = str(candidate.claimed_at)
    async with session.begin_nested():
        # Acquire and retain source-store write ownership before any core-store
        # observation. Cancellation either committed first and is visible below,
        # or must wait for the caller-owned source transaction to finish.
        ownership = await session.execute(
            update(ExperimentLaunchContext)
            .where(
                ExperimentLaunchContext.launch_context_id == launch_context_id,
                ExperimentLaunchContext.contract_version == "2",
                ExperimentLaunchContext.state == "reserved",
                ExperimentLaunchContext.claim_token == claim_token,
                ExperimentLaunchContext.claimed_at == claimed_at,
            )
            .values(claimed_at=ExperimentLaunchContext.claimed_at)
            .execution_options(synchronize_session=False)
        )
        if ownership.rowcount != 1:
            return "cas_lost"
        await session.refresh(candidate)

        attempt = await session.get(ExperimentRunAttempt, candidate.run_attempt_id or "")
        preparation = await session.get(
            ExperimentWorkflowPreparation,
            candidate.preparation_id or "",
        )
        validation = await session.get(
            ExperimentValidation,
            candidate.validation_receipt_id or "",
        )
        run = await session.get(
            ExperimentWorkflowRun,
            attempt.workflow_run_id if attempt is not None else "",
        )
        group = await session.get(
            ExperimentRunGroup,
            run.run_group_id if run is not None else "",
        )
        if (
            attempt is None
            or preparation is None
            or validation is None
            or run is None
            or group is None
            or attempt.resource_id != candidate.run_attempt_id
            or attempt.workspace_id != candidate.project_id
            or attempt.preparation_id != candidate.preparation_id
            or preparation.resource_id != candidate.preparation_id
            or preparation.workspace_id != candidate.project_id
            or preparation.workflow_revision_id != candidate.workflow_revision_id
            or preparation.normalized_request_sha256 != candidate.normalized_request_sha256
            or validation.resource_id != candidate.validation_receipt_id
            or validation.subject_resource_id != preparation.resource_id
            or validation.outcome != "valid"
            or validation.receipt_json != preparation.validation_receipt_json
            or validation.receipt_sha256 != candidate.validation_receipt_sha256
            or run.resource_id != attempt.workflow_run_id
            or run.workspace_id != candidate.project_id
            or run.preparation_id != preparation.resource_id
            or group.resource_id != run.run_group_id
            or group.workspace_id != candidate.project_id
            or candidate.source_receipt_id != candidate.workflow_revision_id
            or not attempt.scheduler_job_id
        ):
            raise _StaleClaimConflict("source_authority_invalid")

        cancellation_command = await session.scalar(
            select(ExperimentRunControlCommand.command_id)
            .where(
                ExperimentRunControlCommand.run_group_id == group.resource_id,
                ExperimentRunControlCommand.command_type == "cancel",
                ExperimentRunControlCommand.status.in_(ACTIVE_CANCELLATION_COMMAND_STATES),
            )
            .limit(1)
        )
        if cancellation_command is not None or attempt.state in {"completed", "failed", "cancelled"}:
            cleared = await session.execute(
                update(ExperimentLaunchContext)
                .where(
                    ExperimentLaunchContext.launch_context_id == launch_context_id,
                    ExperimentLaunchContext.contract_version == "2",
                    ExperimentLaunchContext.state == "reserved",
                    ExperimentLaunchContext.claim_token == claim_token,
                    ExperimentLaunchContext.claimed_at == claimed_at,
                )
                .values(claim_token=None, claimed_at=None)
                .execution_options(synchronize_session=False)
            )
            if cleared.rowcount != 1:
                raise _StaleClaimConflict("claim_changed_during_blocked_release")
            return "blocked"

        if (
            attempt.state != "pending"
            or attempt.external_binding_receipt_json is not None
            or attempt.terminal_receipt_json is not None
            or attempt.terminal_receipt_sha256 is not None
            or run.state != "dispatch_pending"
            or candidate.canonical_job_id is not None
            or candidate.binding_receipt_json is not None
            or candidate.consumed_at is not None
        ):
            raise _StaleClaimConflict("source_binding_evidence_ambiguous")
        try:
            await validate_preparation_authority(
                session,
                preparation,
                core_session=core_session,
            )
        except Exception as exc:
            raise _StaleClaimConflict("preparation_authority_invalid") from exc

        job = await core_session.get(Job, attempt.scheduler_job_id)
        if job is None:
            tagged_job_ids = list(
                (
                    await core_session.execute(
                        select(Job.id)
                        .where(
                            func.json_extract(Job.provenance, "$.launch_context_id")
                            == launch_context_id
                        )
                        .order_by(Job.id)
                        .limit(2)
                    )
                ).scalars().all()
            )
            if tagged_job_ids:
                raise _StaleClaimConflict("core_job_binding_ambiguous")
            released = await session.execute(
                update(ExperimentLaunchContext)
                .where(
                    ExperimentLaunchContext.launch_context_id == launch_context_id,
                    ExperimentLaunchContext.contract_version == "2",
                    ExperimentLaunchContext.state == "reserved",
                    ExperimentLaunchContext.claim_token == claim_token,
                    ExperimentLaunchContext.claimed_at == claimed_at,
                    ExperimentLaunchContext.canonical_job_id.is_(None),
                    ExperimentLaunchContext.binding_receipt_json.is_(None),
                    ExperimentLaunchContext.consumed_at.is_(None),
                )
                .values(claim_token=None, claimed_at=None)
                .execution_options(synchronize_session=False)
            )
            if released.rowcount != 1:
                raise _StaleClaimConflict("claim_changed_during_retry_release")
            return "released_for_retry"

        try:
            await validate_bound_job(session, candidate, job)
        except Exception as exc:
            raise _StaleClaimConflict("core_job_authority_invalid") from exc

        expected_group_state = await derive_run_group_state(session, group.resource_id)
        if (
            expected_group_state not in {"dispatch_pending", "partially_dispatched"}
            or group.state != expected_group_state
        ):
            raise _StaleClaimConflict("run_group_projection_invalid")
        canonical_batch_id = getattr(job, "batch_id", None)
        try:
            consumed, binding = await consume_launch_context(
                session,
                launch_context_id=launch_context_id,
                claim_token=claim_token,
                canonical_job_id=str(job.id),
                canonical_batch_id=(
                    str(canonical_batch_id) if canonical_batch_id is not None else None
                ),
                stale_claimed_at=claimed_at,
            )
        except Exception as exc:
            raise _StaleClaimConflict("launch_context_consume_conflict") from exc
        binding_json = _canonical_json(binding)
        run_generation = int(run.generation)
        group_generation = int(group.generation)
        attempt_update = await session.execute(
            update(ExperimentRunAttempt)
            .where(
                ExperimentRunAttempt.resource_id == attempt.resource_id,
                ExperimentRunAttempt.workspace_id == candidate.project_id,
                ExperimentRunAttempt.workflow_run_id == run.resource_id,
                ExperimentRunAttempt.preparation_id == preparation.resource_id,
                ExperimentRunAttempt.scheduler_job_id == str(job.id),
                ExperimentRunAttempt.state == "pending",
                ExperimentRunAttempt.external_binding_receipt_json.is_(None),
                ExperimentRunAttempt.terminal_receipt_json.is_(None),
                ExperimentRunAttempt.terminal_receipt_sha256.is_(None),
            )
            .values(
                state="dispatched",
                external_binding_receipt_json=binding_json,
            )
            .execution_options(synchronize_session=False)
        )
        run_update = await session.execute(
            update(ExperimentWorkflowRun)
            .where(
                ExperimentWorkflowRun.resource_id == run.resource_id,
                ExperimentWorkflowRun.workspace_id == candidate.project_id,
                ExperimentWorkflowRun.run_group_id == group.resource_id,
                ExperimentWorkflowRun.preparation_id == preparation.resource_id,
                ExperimentWorkflowRun.state == "dispatch_pending",
                ExperimentWorkflowRun.generation == run_generation,
            )
            .values(state="dispatched", generation=run_generation + 1)
            .execution_options(synchronize_session=False)
        )
        if attempt_update.rowcount != 1 or run_update.rowcount != 1:
            raise _StaleClaimConflict("attempt_projection_conflict")
        projected_group_state = await derive_run_group_state(session, group.resource_id)
        group_update = await session.execute(
            update(ExperimentRunGroup)
            .where(
                ExperimentRunGroup.resource_id == group.resource_id,
                ExperimentRunGroup.workspace_id == candidate.project_id,
                ExperimentRunGroup.state == expected_group_state,
                ExperimentRunGroup.generation == group_generation,
            )
            .values(
                state=projected_group_state,
                generation=group_generation + 1,
                updated_at=_timestamp(_now()),
            )
            .execution_options(synchronize_session=False)
        )
        if group_update.rowcount != 1:
            raise _StaleClaimConflict("run_group_projection_conflict")
        if consumed.binding_receipt_json != binding_json:
            raise _StaleClaimConflict("canonical_binding_receipt_changed")
        return "consumed"


async def publish_consumed_launch_context_bindings(
    session: AsyncSession,
    core_session: AsyncSession,
    *,
    observed_at: datetime | None = None,
    priority_context_ids: Sequence[str] = (),
    limit: int = 100,
) -> dict[str, int]:
    """Bounded repair for consumed typed contexts whose scheduler gate is absent."""
    from database import Job, launch_context_binding_ready

    if limit < 1 or limit > 100:
        raise LaunchContextError("invalid_binding_publication_limit", "Binding publication limit must be between 1 and 100.", status_code=422)
    priority_ids = list(dict.fromkeys(priority_context_ids))
    if len(priority_ids) > limit or any(type(value) is not str or not value for value in priority_ids):
        raise LaunchContextError("invalid_binding_publication_priority", "Binding publication priorities are invalid.", status_code=422)
    conditions = (
        ExperimentLaunchContext.contract_version == "2",
        ExperimentLaunchContext.state == "consumed",
        ExperimentLaunchContext.binding_receipt_json.is_not(None),
        ExperimentLaunchContext.canonical_job_id.is_not(None),
    )
    total = int(await session.scalar(select(func.count()).select_from(ExperimentLaunchContext).where(*conditions)) or 0)
    if total == 0:
        return {"scanned_count": 0, "published_count": 0, "conflict_count": 0}
    instant = observed_at or datetime.now(timezone.utc)
    offset = (int(instant.timestamp() // 60) * limit) % total
    candidates = list((await session.scalars(
        select(ExperimentLaunchContext).where(*conditions)
        .order_by(ExperimentLaunchContext.consumed_at, ExperimentLaunchContext.launch_context_id)
        .offset(offset).limit(limit)
    )).all())
    if len(candidates) < limit and offset:
        candidates.extend(list((await session.scalars(
            select(ExperimentLaunchContext).where(*conditions)
            .order_by(ExperimentLaunchContext.consumed_at, ExperimentLaunchContext.launch_context_id)
            .limit(limit - len(candidates))
        )).all()))
    if priority_ids:
        priority_rows = list((await session.scalars(
            select(ExperimentLaunchContext).where(
                *conditions,
                ExperimentLaunchContext.launch_context_id.in_(priority_ids),
            )
        )).all())
        by_id = {str(row.launch_context_id): row for row in priority_rows}
        ordered_priority = [by_id[value] for value in priority_ids if value in by_id]
        priority_set = set(priority_ids)
        candidates = (
            ordered_priority
            + [row for row in candidates if str(row.launch_context_id) not in priority_set]
        )[:limit]
    published = 0
    conflicts = 0
    for context in candidates:
        job = await core_session.get(Job, context.canonical_job_id)
        if job is None:
            conflicts += 1
            continue
        if launch_context_binding_ready(job):
            continue
        try:
            raw_binding = str(context.binding_receipt_json)
            decoded_binding = json.loads(raw_binding)
            if type(decoded_binding) is not dict or _canonical_json(decoded_binding) != raw_binding:
                raise LaunchContextError(
                    "launch_context_binding_invalid",
                    "Consumed launch context binding receipt is not canonical.",
                    status_code=409,
                )
            binding = decoded_binding
            await publish_launch_context_binding(
                core_session,
                context=context,
                job=job,
                binding=binding,
            )
            published += 1
        except (LaunchContextError, SQLAlchemyError, TypeError, ValueError):
            await core_session.rollback()
            conflicts += 1
    return {"scanned_count": len(candidates), "published_count": published, "conflict_count": conflicts}


async def recover_stale_typed_launch_context_claims(
    session: AsyncSession,
    core_session: AsyncSession,
    *,
    claimed_before: datetime,
    limit: int,
) -> dict[str, Any]:
    """Recover a bounded batch of stale v2 typed claims.

    The caller owns the source transaction and must commit or roll it back. This
    helper deliberately does not commit.
    """
    if (
        not isinstance(claimed_before, datetime)
        or claimed_before.tzinfo is None
        or claimed_before.utcoffset() is None
    ):
        raise ValueError("claimed_before must be a timezone-aware datetime")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_STALE_CLAIM_RECOVERY_ROWS:
        raise ValueError(f"limit must be between 1 and {MAX_STALE_CLAIM_RECOVERY_ROWS}")

    cutoff = _timestamp(claimed_before)
    prior_conflict = exists(
        select(ExperimentAuditEvent.id).where(
            ExperimentAuditEvent.workspace_id == ExperimentLaunchContext.project_id,
            ExperimentAuditEvent.resource_id == ExperimentLaunchContext.launch_context_id,
            ExperimentAuditEvent.event_type == "launch_context_stale_claim_conflict",
        )
    )
    base_conditions = (
        ExperimentLaunchContext.contract_version == "2",
        ExperimentLaunchContext.state == "reserved",
        ExperimentLaunchContext.claim_token.is_not(None),
        ExperimentLaunchContext.claimed_at.is_not(None),
        ExperimentLaunchContext.claimed_at < cutoff,
    )
    conflict_count = int(
        await session.scalar(
            select(func.count())
            .select_from(ExperimentLaunchContext)
            .where(*base_conditions, prior_conflict)
        )
        or 0
    )
    epoch_bucket = int(claimed_before.timestamp() // 60)
    if limit == 1:
        conflict_quota = 1 if conflict_count and epoch_bucket % 2 == 0 else 0
    else:
        conflict_quota = min(conflict_count, max(1, limit // 4))
    fresh_quota = limit - conflict_quota
    candidates = list(
        (
            await session.scalars(
                select(ExperimentLaunchContext)
                .where(*base_conditions, ~prior_conflict)
                .order_by(
                    ExperimentLaunchContext.claimed_at,
                    ExperimentLaunchContext.launch_context_id,
                )
                .limit(fresh_quota)
            )
        ).all()
    )
    conflict_quota = min(conflict_count, limit - len(candidates))
    if conflict_quota:
        offset = (epoch_bucket * conflict_quota) % conflict_count
        conflicted = list(
            (
                await session.scalars(
                    select(ExperimentLaunchContext)
                    .where(*base_conditions, prior_conflict)
                    .order_by(
                        ExperimentLaunchContext.claimed_at,
                        ExperimentLaunchContext.launch_context_id,
                    )
                    .offset(offset)
                    .limit(conflict_quota)
                )
            ).all()
        )
        if len(conflicted) < conflict_quota:
            conflicted.extend(
                list(
                    (
                        await session.scalars(
                            select(ExperimentLaunchContext)
                            .where(*base_conditions, prior_conflict)
                            .order_by(
                                ExperimentLaunchContext.claimed_at,
                                ExperimentLaunchContext.launch_context_id,
                            )
                            .limit(conflict_quota - len(conflicted))
                        )
                    ).all()
                )
            )
        candidates.extend(conflicted)
    if len(candidates) < limit:
        selected_ids = [row.launch_context_id for row in candidates]
        candidates.extend(
            list(
                (
                    await session.scalars(
                        select(ExperimentLaunchContext)
                        .where(
                            *base_conditions,
                            ExperimentLaunchContext.launch_context_id.not_in(selected_ids),
                        )
                        .order_by(
                            case((prior_conflict, 1), else_=0),
                            ExperimentLaunchContext.claimed_at,
                            ExperimentLaunchContext.launch_context_id,
                        )
                        .limit(limit - len(candidates))
                    )
                ).all()
            )
        )
    report: dict[str, Any] = {
        "schema": "bms.launch-context-stale-claim-recovery.v1",
        "claimed_before": cutoff,
        "limit": limit,
        "scanned_count": len(candidates),
        "consumed_count": 0,
        "released_for_retry_count": 0,
        "blocked_count": 0,
        "conflict_count": 0,
        "cas_lost_count": 0,
        "consumed_launch_context_ids": [],
        "released_for_retry_launch_context_ids": [],
        "blocked_launch_context_ids": [],
        "conflict_launch_context_ids": [],
        "cas_lost_launch_context_ids": [],
        "conflict_audit_event_ids": [],
    }
    report_keys = {
        "consumed": ("consumed_count", "consumed_launch_context_ids"),
        "released_for_retry": (
            "released_for_retry_count",
            "released_for_retry_launch_context_ids",
        ),
        "blocked": ("blocked_count", "blocked_launch_context_ids"),
        "cas_lost": ("cas_lost_count", "cas_lost_launch_context_ids"),
    }

    for candidate in candidates:
        launch_context_id = str(candidate.launch_context_id)
        workspace_id = str(candidate.project_id)
        run_attempt_id = (
            str(candidate.run_attempt_id) if candidate.run_attempt_id is not None else None
        )
        claim_token = str(candidate.claim_token)
        claimed_at = str(candidate.claimed_at)
        try:
            outcome = await _recover_stale_typed_claim_candidate(
                session,
                core_session,
                candidate,
            )
        except _StaleClaimConflict as exc:
            report["conflict_count"] += 1
            report["conflict_launch_context_ids"].append(launch_context_id)
            audit_id = await _append_stale_claim_conflict(
                session,
                launch_context_id=launch_context_id,
                workspace_id=workspace_id,
                run_attempt_id=run_attempt_id,
                claim_token=claim_token,
                claimed_at=claimed_at,
                code=exc.code,
            )
            report["conflict_audit_event_ids"].append(audit_id)
            continue
        count_key, ids_key = report_keys[outcome]
        report[count_key] += 1
        report[ids_key].append(launch_context_id)

    await session.flush()
    return report


__all__ = [
    "LaunchContextError",
    "claim_launch_context",
    "consume_launch_context",
    "context_document",
    "create_launch_context",
    "create_prepared_launch_context",
    "publish_launch_context_binding",
    "publish_consumed_launch_context_bindings",
    "recover_stale_typed_launch_context_claims",
    "release_launch_context_claim",
    "resolve_launch_context",
    "resolve_launch_context_for_display",
    "validate_bound_job",
    "validate_bound_job_request",
    "workflow_pinned_gpu",
]
