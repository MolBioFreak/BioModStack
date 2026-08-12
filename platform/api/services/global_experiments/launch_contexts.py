"""Durable opaque handoffs from global Domain Experiments to typed launchers."""
from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import (
    ExperimentAggregateHead,
    ExperimentLaunchContext,
    ExperimentRevision,
)


LAUNCH_CONTEXT_SCHEMA = "bms.launch-context.v1"
BINDING_RECEIPT_SCHEMA = "bms.launch-context-binding.v1"
DEFAULT_TTL = timedelta(minutes=15)
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
    if context.state == "claimed":
        return LaunchContextError(
            "launch_context_claimed",
            "Launch context is already claimed by another submission.",
            status_code=409,
        )
    return LaunchContextError(
        "launch_context_invalid",
        "Launch context is in an unsupported state.",
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


def context_document(context: ExperimentLaunchContext) -> dict[str, Any]:
    """Return the closed public launch-context-v1 document."""
    return {
        "schema": LAUNCH_CONTEXT_SCHEMA,
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
    await _validate_hierarchy(
        session,
        project_id=project_id,
        global_experiment_id=global_experiment_id,
        domain_experiment_id=domain_experiment_id,
        workflow_id=workflow_id,
        workflow_revision_id=workflow_revision_id,
    )
    validated_return_uri = _validate_return_uri(
        return_uri,
        project_id=project_id,
        global_experiment_id=global_experiment_id,
        domain_experiment_id=domain_experiment_id,
        workflow_id=workflow_id,
    )
    selected_node_key = parse_qs(urlsplit(validated_return_uri).query, strict_parsing=True)["selected"][0]
    await _validate_return_selection(
        session,
        project_id=project_id,
        global_experiment_id=global_experiment_id,
        selected_node_key=selected_node_key,
    )
    issued = _now()
    domain_head = await session.get(ExperimentAggregateHead, domain_experiment_id)
    source_receipt_id = workflow_revision_id or (domain_head.current_revision_id if domain_head is not None else None)
    if not source_receipt_id:
        raise LaunchContextError("launch_context_source_unavailable", "Launch source revision authority is unavailable.", status_code=409)
    context = ExperimentLaunchContext(
        launch_context_id=f"launch-context:{uuid.uuid4()}",
        project_id=project_id,
        global_experiment_id=global_experiment_id,
        domain_experiment_id=domain_experiment_id,
        workflow_id=workflow_id,
        workflow_revision_id=workflow_revision_id,
        source_receipt_id=source_receipt_id,
        return_uri=validated_return_uri,
        state="issued",
        issued_at=_timestamp(issued),
        expires_at=_timestamp(issued + DEFAULT_TTL),
    )
    session.add(context)
    await session.flush()
    return context


async def resolve_launch_context(session: AsyncSession, launch_context_id: str) -> ExperimentLaunchContext:
    context = await session.get(ExperimentLaunchContext, launch_context_id)
    if context is None:
        raise LaunchContextError(
            "launch_context_unknown",
            "Launch context is unknown.",
            status_code=404,
        )
    _ensure_live(context)
    if context.state != "issued":
        raise _error_for_state(context)
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
    if context.state == "issued":
        return await resolve_launch_context(session, launch_context_id)
    if context.state == "claimed":
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
    if context.workflow_revision_id:
        revision = await session.get(ExperimentRevision, context.workflow_revision_id)
        decoded_payload = json.loads(revision.canonical_payload) if revision is not None else {}
        payload = decoded_payload if isinstance(decoded_payload, dict) else {}
        raw_scheduler = payload.get("scheduler")
        scheduler: dict[str, Any] = raw_scheduler if isinstance(raw_scheduler, dict) else {}
        expected_adapter = payload.get("adapter_id")
        raw_job_params = job.params
        job_params: dict[str, Any] = raw_job_params if isinstance(raw_job_params, dict) else {}
        job_adapter = job_params.get("workflow_adapter")
        raw_expected_params = scheduler.get("params")
        expected_params: dict[str, Any] = raw_expected_params if isinstance(raw_expected_params, dict) else {}
        expected_job_params = dict(expected_params)
        if expected_adapter is not None:
            expected_job_params["workflow_adapter"] = expected_adapter
        params_match = json.dumps(job_params, sort_keys=True, separators=(",", ":")) == json.dumps(
            expected_job_params, sort_keys=True, separators=(",", ":")
        )
        if (
            scheduler.get("model_id") != job.model_id
            or scheduler.get("mode") != job.mode
            or expected_adapter != job_adapter
            or not params_match
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
            ExperimentLaunchContext.state == "issued",
            ExperimentLaunchContext.claim_token.is_(None),
        )
        .values(state="claimed", claim_token=claim_token, claimed_at=claimed_at)
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
    await session.execute(
        update(ExperimentLaunchContext)
        .where(
            ExperimentLaunchContext.launch_context_id == launch_context_id,
            ExperimentLaunchContext.state == "claimed",
            ExperimentLaunchContext.claim_token == claim_token,
        )
        .values(state="issued", claim_token=None, claimed_at=None)
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
) -> tuple[ExperimentLaunchContext, dict[str, Any]]:
    context = await session.get(ExperimentLaunchContext, launch_context_id)
    if context is None:
        raise LaunchContextError("launch_context_unknown", "Launch context is unknown.", status_code=404)
    _ensure_live(context)
    if context.state != "claimed" or not secrets.compare_digest(context.claim_token or "", claim_token):
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
    result = await session.execute(
        update(ExperimentLaunchContext)
        .where(
            ExperimentLaunchContext.launch_context_id == launch_context_id,
            ExperimentLaunchContext.state == "claimed",
            ExperimentLaunchContext.claim_token == claim_token,
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


__all__ = [
    "LaunchContextError",
    "claim_launch_context",
    "consume_launch_context",
    "context_document",
    "create_launch_context",
    "release_launch_context_claim",
    "resolve_launch_context",
    "resolve_launch_context_for_display",
    "validate_bound_job",
]
