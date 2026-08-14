"""Durable, recoverable cancellation authority for global experiment run groups.

The experiment store owns command admission, target snapshots, saga progress, and
source publication.  The core store owns native Job cancellation receipts.  No
transaction in this module is represented as atomic across those stores.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import (
    ExperimentDispatchOutbox,
    ExperimentLaunchContext,
    ExperimentRunAttempt,
    ExperimentRunControlCommand,
    ExperimentRunGroup,
    ExperimentWorkflowPreparation,
    ExperimentWorkflowRun,
)
from experiment_services import (
    DispatchFailure,
    IdempotencyConflict,
    NotFound,
    RevisionConflict,
    ValidationFailure,
    _preparation_plan_scope,
    add_audit_event,
    canonical_json,
    now,
    sha256_text,
)
from services.job_control import cancel_job_lineage
from services.ngs_molbio_n5 import (
    ResourceUsageEvidenceUnavailable,
    persist_never_launched_resource_usage_evidence,
    persist_producer_resource_usage_evidence,
)
from services.resource_usage_evidence import (
    ResourceUsageEvidenceError,
    attach_cancelled_resource_receipt_from_checkpoint,
)


MAX_COMMAND_ROWS = 2048
MAX_COMMAND_BYTES = 1_000_000
LEASE_SECONDS = 300
ACTIVE_COMMAND_STATUSES = frozenset({"pending", "leased", "retryable", "applied", "conflicted"})
ACTIVE_ATTEMPT_STATES = frozenset({"pending", "dispatched", "queued", "running"})
TERMINAL_ATTEMPT_STATES = frozenset({"completed", "failed", "cancelled"})
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}")


class _Retryable(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class _Conflict(RuntimeError):
    def __init__(self, code: str, message: str, *, attempt_id: str | None = None) -> None:
        self.code = code
        self.message = message
        self.attempt_id = attempt_id
        super().__init__(message)


def _bounded_json(value: Any, *, label: str) -> str:
    encoded = canonical_json(value)
    if len(encoded.encode("utf-8")) > MAX_COMMAND_BYTES:
        raise ValidationFailure(f"{label} exceeds the bounded durable authority limit")
    return encoded


def _digest_or_none(value: str | None) -> str | None:
    return sha256_text(value) if isinstance(value, str) else None


def _command_scope(run_group_id: str) -> str:
    return f"run-group-cancel:{sha256_text(run_group_id)}"


def command_document(command: ExperimentRunControlCommand) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": "bms.run-control-command.v1",
        "command_id": command.command_id,
        "command_type": command.command_type,
        "workspace_id": command.workspace_id,
        "run_group_id": command.run_group_id,
        "expected_generation": int(command.expected_generation),
        "status": command.status,
        "attempt_count": int(command.attempt_count),
        "created_at": command.created_at,
        "updated_at": command.updated_at,
        "applied_at": command.applied_at,
    }
    if command.status == "applied" and command.acknowledgement_json:
        document["acknowledgement"] = json.loads(command.acknowledgement_json)
    elif command.status == "conflicted" and command.conflict_json:
        document["conflict"] = json.loads(command.conflict_json)
    return document


async def blocking_cancellation_command(
    session: AsyncSession,
    run_group_id: str,
) -> ExperimentRunControlCommand | None:
    return await session.scalar(
        select(ExperimentRunControlCommand)
        .where(
            ExperimentRunControlCommand.run_group_id == run_group_id,
            ExperimentRunControlCommand.command_type == "cancel",
            ExperimentRunControlCommand.status.in_(ACTIVE_COMMAND_STATUSES),
        )
        .limit(1)
    )


async def require_launch_not_fenced(session: AsyncSession, run_group_id: str) -> None:
    if await blocking_cancellation_command(session, run_group_id) is not None:
        raise ValidationFailure("run-group launch is permanently fenced by cancellation command")


async def _preparation_snapshot(
    session: AsyncSession,
    preparation: ExperimentWorkflowPreparation,
    *,
    workspace_id: str,
    source_domain_id: str | None,
) -> dict[str, Any]:
    domain_id, global_experiment_id, revision, plan = await _preparation_plan_scope(
        session,
        preparation,
        workspace_id=workspace_id,
    )
    if source_domain_id is not None and domain_id != source_domain_id:
        raise NotFound("run group not found in the exact source Domain")
    return {
        "preparation_id": preparation.resource_id,
        "workflow_revision_id": preparation.workflow_revision_id,
        "normalized_request_sha256": preparation.normalized_request_sha256,
        "validation_resource_id": preparation.validation_resource_id,
        "validation_receipt_sha256": sha256_text(preparation.validation_receipt_json),
        "scheduler_payload_sha256": sha256_text(preparation.scheduler_payload_json),
        "domain_id": domain_id,
        "global_experiment_id": global_experiment_id,
        "plan_id": plan.aggregate_id,
        "revision_id": revision.resource_id,
    }


def _launch_context_snapshot(context: ExperimentLaunchContext | None) -> dict[str, Any] | None:
    if context is None:
        return None
    return {
        "launch_context_id": context.launch_context_id,
        "project_id": context.project_id,
        "global_experiment_id": context.global_experiment_id,
        "domain_experiment_id": context.domain_experiment_id,
        "workflow_id": context.workflow_id,
        "workflow_revision_id": context.workflow_revision_id,
        "preparation_id": context.preparation_id,
        "run_attempt_id": context.run_attempt_id,
        "contract_version": context.contract_version,
        "normalized_request_sha256": context.normalized_request_sha256,
        "validation_receipt_id": context.validation_receipt_id,
        "validation_receipt_sha256": context.validation_receipt_sha256,
        "source_receipt_id": context.source_receipt_id,
        "state": context.state,
        "canonical_job_id": context.canonical_job_id,
        "binding_receipt_sha256": _digest_or_none(context.binding_receipt_json),
    }


def _outbox_snapshot(row: ExperimentDispatchOutbox | None) -> dict[str, Any] | None:
    if row is None:
        return None
    try:
        payload = json.loads(row.payload_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValidationFailure("dispatch outbox payload is malformed") from exc
    if (
        not isinstance(payload, dict)
        or canonical_json(payload) != row.payload_json
        or sha256_text(row.payload_json) != row.payload_sha256
    ):
        raise ValidationFailure("dispatch outbox payload is not canonical durable authority")
    return {
        "outbox_id": row.id,
        "event_type": row.event_type,
        "payload_sha256": row.payload_sha256,
        "status": row.status,
        "dispatch_attempts": int(row.dispatch_attempts),
        "acknowledgement_sha256": _digest_or_none(row.acknowledgement_json),
        "payload_identity": {
            "run_group_id": payload.get("run_group_id"),
            "workflow_run_id": payload.get("workflow_run_id"),
            "attempt_id": payload.get("attempt_id"),
            "scheduler_job_id": payload.get("scheduler_job_id"),
            "workflow_revision_id": payload.get("workflow_revision_id"),
            "scheduler_sha256": sha256_text(canonical_json(payload.get("scheduler"))),
        },
    }


async def _target_snapshot(
    session: AsyncSession,
    *,
    group: ExperimentRunGroup,
    source_domain_id: str | None,
) -> dict[str, Any]:
    runs = list(
        (
            await session.execute(
                select(ExperimentWorkflowRun)
                .where(ExperimentWorkflowRun.run_group_id == group.resource_id)
                .order_by(ExperimentWorkflowRun.created_at, ExperimentWorkflowRun.resource_id)
            )
        ).scalars().all()
    )
    if not runs:
        raise ValidationFailure("run group has no durable run authority")
    run_documents: list[dict[str, Any]] = []
    row_count = len(runs)
    observed_domains: set[str] = set()
    for run in runs:
        run_preparation = await session.get(ExperimentWorkflowPreparation, run.preparation_id)
        if run_preparation is None:
            raise ValidationFailure("run preparation authority is unavailable")
        run_preparation_document = await _preparation_snapshot(
            session,
            run_preparation,
            workspace_id=group.workspace_id,
            source_domain_id=source_domain_id,
        )
        observed_domains.add(str(run_preparation_document["domain_id"]))
        attempts = list(
            (
                await session.execute(
                    select(ExperimentRunAttempt)
                    .where(ExperimentRunAttempt.workflow_run_id == run.resource_id)
                    .order_by(ExperimentRunAttempt.attempt_number, ExperimentRunAttempt.resource_id)
                )
            ).scalars().all()
        )
        if not attempts:
            raise ValidationFailure("run has no durable attempt authority")
        row_count += len(attempts)
        attempt_documents: list[dict[str, Any]] = []
        for attempt in attempts:
            preparation = await session.get(ExperimentWorkflowPreparation, attempt.preparation_id)
            if preparation is None:
                raise ValidationFailure("attempt preparation authority is unavailable")
            preparation_document = await _preparation_snapshot(
                session,
                preparation,
                workspace_id=group.workspace_id,
                source_domain_id=source_domain_id,
            )
            observed_domains.add(str(preparation_document["domain_id"]))
            contexts = list(
                (
                    await session.execute(
                        select(ExperimentLaunchContext).where(
                            ExperimentLaunchContext.run_attempt_id == attempt.resource_id
                        )
                    )
                ).scalars().all()
            )
            outboxes = list(
                (
                    await session.execute(
                        select(ExperimentDispatchOutbox).where(
                            ExperimentDispatchOutbox.run_attempt_id == attempt.resource_id,
                            ExperimentDispatchOutbox.event_type == "materialize_scheduler_job",
                        )
                    )
                ).scalars().all()
            )
            if len(contexts) > 1 or len(outboxes) > 1 or bool(contexts) == bool(outboxes):
                raise ValidationFailure("attempt has no exact single launch authority")
            attempt_documents.append(
                {
                    "attempt_id": attempt.resource_id,
                    "workflow_run_id": attempt.workflow_run_id,
                    "attempt_number": int(attempt.attempt_number),
                    "state": attempt.state,
                    "scheduler_job_id": attempt.scheduler_job_id,
                    "external_binding_receipt_sha256": _digest_or_none(
                        attempt.external_binding_receipt_json
                    ),
                    "runtime_identity_sha256": _digest_or_none(attempt.runtime_identity_json),
                    "terminal_receipt_sha256": attempt.terminal_receipt_sha256,
                    "created_at": attempt.created_at,
                    "preparation": preparation_document,
                    "launch_context": _launch_context_snapshot(contexts[0] if contexts else None),
                    "outbox": _outbox_snapshot(outboxes[0] if outboxes else None),
                }
            )
        run_documents.append(
            {
                "run_id": run.resource_id,
                "preparation_id": run.preparation_id,
                "node_id": run.node_id,
                "requiredness": run.requiredness,
                "state": run.state,
                "generation": int(run.generation),
                "created_at": run.created_at,
                "preparation": run_preparation_document,
                "attempts": attempt_documents,
            }
        )
    if row_count > MAX_COMMAND_ROWS:
        raise ValidationFailure("cancellation target exceeds the bounded durable authority limit")
    if source_domain_id is not None and observed_domains != {source_domain_id}:
        raise NotFound("run group not found in the exact source Domain")
    return {
        "schema": "bms.run-control-target-snapshot.v1",
        "workspace_id": group.workspace_id,
        "run_group_id": group.resource_id,
        "group_state": group.state,
        "group_generation": int(group.generation),
        "group_request_sha256": group.request_sha256,
        "source_domain_id": source_domain_id,
        "runs": run_documents,
    }


async def request_run_group_cancellation(
    session: AsyncSession,
    *,
    workspace_id: str,
    run_group_id: str,
    idempotency_key: str,
    expected_generation: int,
    reason: str,
    source_domain_id: str | None = None,
) -> ExperimentRunControlCommand:
    if (
        not isinstance(idempotency_key, str)
        or not 1 <= len(idempotency_key) <= 255
        or any(ord(character) < 33 or ord(character) > 126 for character in idempotency_key)
    ):
        raise ValidationFailure("cancellation idempotency key is invalid")
    if (
        isinstance(expected_generation, bool)
        or not isinstance(expected_generation, int)
        or expected_generation < 0
    ):
        raise ValidationFailure("cancellation requires an explicit expected run-group generation")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1024:
        raise ValidationFailure("cancellation reason must contain 1..1024 characters")
    bounded_reason = reason.strip()
    request_scope = _command_scope(run_group_id)
    request_document = {
        "schema": "bms.run-control-cancel-request.v1",
        "workspace_id": workspace_id,
        "run_group_id": run_group_id,
        "expected_generation": expected_generation,
        "reason": bounded_reason,
        "source_domain_id": source_domain_id,
    }
    request_json = _bounded_json(request_document, label="cancellation request")
    request_sha256 = sha256_text(request_json)
    replay = await session.scalar(
        select(ExperimentRunControlCommand).where(
            ExperimentRunControlCommand.request_scope == request_scope,
            ExperimentRunControlCommand.idempotency_key == idempotency_key,
        )
    )
    if replay is not None:
        if (
            replay.request_sha256 == request_sha256
            and replay.request_json == request_json
            and replay.workspace_id == workspace_id
            and replay.run_group_id == run_group_id
            and replay.expected_generation == expected_generation
        ):
            return replay
        raise IdempotencyConflict("cancellation idempotency key has different canonical authority")
    existing = await session.scalar(
        select(ExperimentRunControlCommand)
        .where(
            ExperimentRunControlCommand.run_group_id == run_group_id,
            ExperimentRunControlCommand.command_type == "cancel",
            ExperimentRunControlCommand.status.in_(("pending", "leased", "retryable", "applied")),
        )
        .order_by(
            ExperimentRunControlCommand.created_at.desc(),
            ExperimentRunControlCommand.command_id.desc(),
        )
        .limit(1)
    )
    if existing is not None:
        raise IdempotencyConflict("run group already has active canonical cancellation authority")
    group = await session.get(ExperimentRunGroup, run_group_id)
    if group is None or group.workspace_id != workspace_id:
        raise NotFound("run group not found")
    if int(group.generation) != expected_generation:
        raise RevisionConflict("run group generation changed")
    snapshot = await _target_snapshot(
        session,
        group=group,
        source_domain_id=source_domain_id,
    )
    snapshot_json = _bounded_json(snapshot, label="cancellation target snapshot")
    progress_json = canonical_json(
        {"schema": "bms.run-control-progress.v1", "targets": {}}
    )
    timestamp = now()
    command = ExperimentRunControlCommand(
        command_id=f"run-control-{uuid.uuid4()}",
        request_scope=request_scope,
        idempotency_key=idempotency_key,
        command_type="cancel",
        workspace_id=workspace_id,
        run_group_id=run_group_id,
        expected_generation=expected_generation,
        request_json=request_json,
        request_sha256=request_sha256,
        target_snapshot_json=snapshot_json,
        target_snapshot_sha256=sha256_text(snapshot_json),
        status="pending",
        attempt_count=0,
        progress_json=progress_json,
        progress_sha256=sha256_text(progress_json),
        created_at=timestamp,
        updated_at=timestamp,
    )
    session.add(command)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raced = await session.scalar(
            select(ExperimentRunControlCommand).where(
                ExperimentRunControlCommand.request_scope == request_scope,
                ExperimentRunControlCommand.idempotency_key == idempotency_key,
            )
        )
        if (
            raced is not None
            and raced.request_sha256 == request_sha256
            and raced.request_json == request_json
            and raced.workspace_id == workspace_id
            and raced.run_group_id == run_group_id
            and raced.expected_generation == expected_generation
        ):
            return raced
        raise IdempotencyConflict(
            "run group cancellation admission raced with different authority"
        ) from exc
    await session.refresh(command)
    return command


def _decode_command(command: ExperimentRunControlCommand) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        sha256_text(command.request_json) != command.request_sha256
        or sha256_text(command.target_snapshot_json) != command.target_snapshot_sha256
        or sha256_text(command.progress_json) != command.progress_sha256
        or len(command.target_snapshot_json.encode("utf-8")) > MAX_COMMAND_BYTES
    ):
        raise _Conflict("command_authority_invalid", "durable cancellation authority is invalid")
    try:
        snapshot = json.loads(command.target_snapshot_json)
        progress = json.loads(command.progress_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise _Conflict("command_authority_invalid", "durable cancellation authority is malformed") from exc
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("schema") != "bms.run-control-target-snapshot.v1"
        or canonical_json(snapshot) != command.target_snapshot_json
        or snapshot.get("workspace_id") != command.workspace_id
        or snapshot.get("run_group_id") != command.run_group_id
        or snapshot.get("group_generation") != int(command.expected_generation)
        or not isinstance(snapshot.get("runs"), list)
        or not isinstance(progress, dict)
        or progress.get("schema") != "bms.run-control-progress.v1"
        or not isinstance(progress.get("targets"), dict)
        or canonical_json(progress) != command.progress_json
    ):
        raise _Conflict("command_authority_invalid", "durable cancellation authority is inconsistent")
    return snapshot, progress


async def _claim_command(
    session: AsyncSession,
    *,
    command_id: str | None,
    worker_id: str,
) -> ExperimentRunControlCommand | None:
    timestamp = now()
    ready = or_(
        ExperimentRunControlCommand.status == "pending",
        and_(
            ExperimentRunControlCommand.status == "retryable",
            or_(
                ExperimentRunControlCommand.next_retry_at.is_(None),
                ExperimentRunControlCommand.next_retry_at <= timestamp,
            ),
        ),
        and_(
            ExperimentRunControlCommand.status == "leased",
            ExperimentRunControlCommand.lease_expires_at <= timestamp,
        ),
    )
    statement = (
        select(ExperimentRunControlCommand)
        .where(ready)
        .order_by(
            ExperimentRunControlCommand.created_at,
            ExperimentRunControlCommand.command_id,
        )
        .limit(1)
    )
    if command_id is not None:
        statement = statement.where(ExperimentRunControlCommand.command_id == command_id)
    candidate = await session.scalar(statement)
    if candidate is None:
        return await session.get(ExperimentRunControlCommand, command_id) if command_id else None
    lease_token = f"run-control-lease-{uuid.uuid4()}"
    claimed = await session.execute(
        update(ExperimentRunControlCommand)
        .where(
            ExperimentRunControlCommand.command_id == candidate.command_id,
            ready,
        )
        .values(
            status="leased",
            lease_owner=worker_id[:255],
            lease_token=lease_token,
            lease_expires_at=(datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS)).isoformat(),
            attempt_count=ExperimentRunControlCommand.attempt_count + 1,
            next_retry_at=None,
            last_error_code=None,
            last_error_message=None,
            updated_at=timestamp,
        )
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        await session.rollback()
        return None
    await session.commit()
    return await session.get(ExperimentRunControlCommand, candidate.command_id)


async def _core_lineage(core_session: AsyncSession, root_job: Any) -> list[Any]:
    from database import Job

    jobs: dict[str, Any] = {str(root_job.id): root_job}
    frontier = [str(root_job.id)]
    while frontier:
        children = list(
            (
                await core_session.execute(select(Job).where(Job.parent_job_id.in_(frontier)))
            ).scalars().all()
        )
        frontier = []
        for child in children:
            child_id = str(child.id)
            if child_id in jobs:
                continue
            jobs[child_id] = child
            frontier.append(child_id)
            if len(jobs) > MAX_COMMAND_ROWS:
                raise _Conflict(
                    "core_lineage_unbounded",
                    "canonical Job lineage exceeds the cancellation authority limit",
                )
    return [jobs[job_id] for job_id in sorted(jobs)]


def _native_cancellation_receipt(job: Any) -> dict[str, Any] | None:
    params = job.params
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except json.JSONDecodeError:
            return None
    if not isinstance(params, dict):
        return None
    receipt = params.get("cancellation_receipt")
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema") != "bms.workflow-cancellation.v1"
        or receipt.get("state") != "completed"
        or not isinstance(receipt.get("completed_at"), str)
        or not isinstance(receipt.get("run_identity"), str)
    ):
        return None
    return {
        "schema": receipt["schema"],
        "state": receipt["state"],
        "completed_at": receipt["completed_at"],
        "run_identity": receipt["run_identity"],
    }


async def _core_cancelled_receipt(
    core_session: AsyncSession,
    *,
    root_job: Any,
    command_id: str,
    attempt_id: str,
) -> dict[str, Any]:
    lineage = await _core_lineage(core_session, root_job)
    jobs: list[dict[str, Any]] = []
    for job in lineage:
        status = str(job.status or "").strip().lower()
        queue_status = str(job.queue_status or "").strip().lower()
        native = _native_cancellation_receipt(job)
        if status not in {"cancelled", "canceled"} or queue_status != "cancelled" or native is None:
            raise _Retryable(
                "core_cancellation_receipt_pending",
                "canonical Job cancellation receipt is not yet complete",
            )
        jobs.append(
            {
                "job_id": str(job.id),
                "parent_job_id": str(job.parent_job_id) if job.parent_job_id else None,
                "status": status,
                "queue_status": queue_status,
                "completed_at": str(job.completed_at) if job.completed_at is not None else None,
                "native_receipt": native,
            }
        )
    receipt = {
        "schema": "bms.run-control-core-cancellation.v1",
        "command_id": command_id,
        "attempt_id": attempt_id,
        "root_job_id": str(root_job.id),
        "jobs": jobs,
    }
    receipt_json = _bounded_json(receipt, label="core cancellation receipt")
    return {"receipt": receipt, "receipt_sha256": sha256_text(receipt_json)}


async def _live_launch_authority(
    session: AsyncSession,
    target: dict[str, Any],
) -> tuple[str, ExperimentLaunchContext | ExperimentDispatchOutbox, bool]:
    attempt_id = str(target.get("attempt_id") or "")
    scheduler_job_id = str(target.get("scheduler_job_id") or "")
    context_snapshot = target.get("launch_context")
    outbox_snapshot = target.get("outbox")
    if isinstance(context_snapshot, dict) and outbox_snapshot is None:
        context = await session.get(
            ExperimentLaunchContext,
            str(context_snapshot.get("launch_context_id") or ""),
        )
        immutable_fields = (
            "launch_context_id",
            "project_id",
            "global_experiment_id",
            "domain_experiment_id",
            "workflow_id",
            "workflow_revision_id",
            "preparation_id",
            "run_attempt_id",
            "contract_version",
            "normalized_request_sha256",
            "validation_receipt_id",
            "validation_receipt_sha256",
            "source_receipt_id",
        )
        current = _launch_context_snapshot(context)
        if (
            context is None
            or current is None
            or any(current.get(field) != context_snapshot.get(field) for field in immutable_fields)
            or context.run_attempt_id != attempt_id
        ):
            raise _Conflict(
                "typed_launch_authority_changed",
                "typed launch-context identity no longer matches the cancellation target",
                attempt_id=attempt_id,
            )
        launch_proven = bool(
            context.state == "consumed"
            or context.canonical_job_id is not None
            or context.binding_receipt_json is not None
            or target.get("external_binding_receipt_sha256") is not None
        )
        if context.canonical_job_id is not None and context.canonical_job_id != scheduler_job_id:
            raise _Conflict(
                "typed_job_identity_conflict",
                "typed launch context names a different canonical Job",
                attempt_id=attempt_id,
            )
        return "typed", context, launch_proven
    if isinstance(outbox_snapshot, dict) and context_snapshot is None:
        outbox = await session.get(
            ExperimentDispatchOutbox,
            str(outbox_snapshot.get("outbox_id") or ""),
        )
        if outbox is None or outbox.run_attempt_id != attempt_id:
            raise _Conflict(
                "managed_dispatch_authority_changed",
                "managed dispatch authority no longer matches the cancellation target",
                attempt_id=attempt_id,
            )
        current = _outbox_snapshot(outbox)
        identity = current.get("payload_identity") if isinstance(current, dict) else None
        expected_identity = outbox_snapshot.get("payload_identity")
        if (
            current is None
            or current.get("event_type") != outbox_snapshot.get("event_type")
            or current.get("payload_sha256") != outbox_snapshot.get("payload_sha256")
            or identity != expected_identity
            or not isinstance(identity, dict)
            or identity.get("attempt_id") != attempt_id
            or identity.get("scheduler_job_id") != scheduler_job_id
        ):
            raise _Conflict(
                "managed_dispatch_authority_changed",
                "managed dispatch payload no longer matches the cancellation target",
                attempt_id=attempt_id,
            )
        launch_proven = bool(
            outbox.status == "acknowledged"
            or outbox.acknowledgement_json is not None
            or target.get("external_binding_receipt_sha256") is not None
        )
        return "managed", outbox, launch_proven
    raise _Conflict(
        "launch_authority_invalid",
        "cancellation target has no exact single launch authority",
        attempt_id=attempt_id,
    )


def _job_typed_ownership(job: Any, context: ExperimentLaunchContext) -> bool:
    provenance = job.provenance
    if isinstance(provenance, str):
        try:
            provenance = json.loads(provenance)
        except json.JSONDecodeError:
            provenance = {}
    return bool(
        context.canonical_job_id == str(job.id)
        or isinstance(provenance, dict)
        and provenance.get("launch_context_id") == context.launch_context_id
    )


async def _observe_target(
    session: AsyncSession,
    core_session: AsyncSession,
    target: dict[str, Any],
) -> tuple[Any | None, str, bool]:
    from database import Job

    attempt_id = str(target.get("attempt_id") or "")
    scheduler_job_id = str(target.get("scheduler_job_id") or "")
    attempt = await session.get(ExperimentRunAttempt, attempt_id)
    if (
        attempt is None
        or attempt.workflow_run_id != target.get("workflow_run_id")
        or attempt.preparation_id != target.get("preparation", {}).get("preparation_id")
        or attempt.scheduler_job_id != scheduler_job_id
        or int(attempt.attempt_number) != target.get("attempt_number")
    ):
        raise _Conflict(
            "source_target_identity_changed",
            "source attempt identity no longer matches the cancellation target",
            attempt_id=attempt_id,
        )
    launch_kind, authority, launch_proven = await _live_launch_authority(session, target)
    job = await core_session.get(Job, scheduler_job_id)
    if job is not None:
        status = str(job.status or "").strip().lower()
        if status in {"completed", "succeeded", "failed"}:
            raise _Conflict(
                "external_terminal_outcome",
                "canonical Job reached a contradictory terminal outcome",
                attempt_id=attempt_id,
            )
        if launch_kind == "typed" and not _job_typed_ownership(job, authority):
            raise _Conflict(
                "core_job_ownership_invalid",
                "canonical Job does not carry exact typed launch ownership",
                attempt_id=attempt_id,
            )
    return job, launch_kind, launch_proven


async def _persist_progress(
    session: AsyncSession,
    *,
    command_id: str,
    lease_token: str,
    progress: dict[str, Any],
) -> None:
    progress_json = _bounded_json(progress, label="cancellation progress")
    updated = await session.execute(
        update(ExperimentRunControlCommand)
        .where(
            ExperimentRunControlCommand.command_id == command_id,
            ExperimentRunControlCommand.status == "leased",
            ExperimentRunControlCommand.lease_token == lease_token,
        )
        .values(
            progress_json=progress_json,
            progress_sha256=sha256_text(progress_json),
            lease_expires_at=(datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS)).isoformat(),
            updated_at=now(),
        )
        .execution_options(synchronize_session=False)
    )
    if updated.rowcount != 1:
        await session.rollback()
        raise DispatchFailure("cancellation command lease changed while recording progress")
    await session.commit()


async def _process_target(
    session: AsyncSession,
    core_session: AsyncSession,
    *,
    command: ExperimentRunControlCommand,
    target: dict[str, Any],
    progress: dict[str, Any],
    reason: str,
) -> None:
    attempt_id = str(target.get("attempt_id") or "")
    target_progress = progress["targets"].get(attempt_id)
    snapshot_state = str(target.get("state") or "")
    if snapshot_state in TERMINAL_ATTEMPT_STATES:
        preserved = {
            "outcome": "source_terminal_preserved",
            "source_state": snapshot_state,
            "terminal_receipt_sha256": target.get("terminal_receipt_sha256"),
        }
        if target_progress != preserved:
            progress["targets"][attempt_id] = preserved
            await _persist_progress(
                session,
                command_id=command.command_id,
                lease_token=str(command.lease_token),
                progress=progress,
            )
        return
    if snapshot_state not in ACTIVE_ATTEMPT_STATES:
        raise _Conflict(
            "source_state_invalid",
            "cancellation target has an unsupported source state",
            attempt_id=attempt_id,
        )
    job, launch_kind, launch_proven = await _observe_target(
        session,
        core_session,
        target,
    )
    if job is None:
        if launch_proven:
            raise _Retryable(
                "core_job_missing_after_launch",
                "canonical Job is unavailable after durable launch evidence",
            )
        absent = {
            "outcome": "launch_fenced_absent",
            "launch_kind": launch_kind,
            "scheduler_job_id": target["scheduler_job_id"],
        }
        if target_progress != absent:
            progress["targets"][attempt_id] = absent
            await _persist_progress(
                session,
                command_id=command.command_id,
                lease_token=str(command.lease_token),
                progress=progress,
            )
        return
    if isinstance(target_progress, dict) and target_progress.get("outcome") == "core_cancelled":
        revalidated = await _core_cancelled_receipt(
            core_session,
            root_job=job,
            command_id=command.command_id,
            attempt_id=attempt_id,
        )
        if (
            target_progress.get("receipt") != revalidated["receipt"]
            or target_progress.get("receipt_sha256") != revalidated["receipt_sha256"]
        ):
            raise _Conflict(
                "core_cancellation_receipt_changed",
                "native core cancellation receipt changed during replay",
                attempt_id=attempt_id,
            )
        return
    try:
        lineage_before = await _core_lineage(core_session, job)
        if any(
            str(lineage_job.status or "").strip().lower()
            in {"completed", "succeeded", "failed"}
            for lineage_job in lineage_before
        ):
            raise _Conflict(
                "external_terminal_outcome",
                "canonical Job lineage reached a contradictory terminal outcome",
                attempt_id=attempt_id,
            )
        root, _lineage = await cancel_job_lineage(
            str(target["scheduler_job_id"]),
            core_session,
            error_message=reason,
            commit=False,
        )
        receipt_authority = await _core_cancelled_receipt(
            core_session,
            root_job=root,
            command_id=command.command_id,
            attempt_id=attempt_id,
        )
        await core_session.commit()
    except _Conflict:
        await core_session.rollback()
        raise
    except _Retryable:
        await core_session.rollback()
        raise
    except HTTPException as exc:
        await core_session.rollback()
        code = "core_cancellation_pending"
        if isinstance(exc.detail, dict) and exc.detail.get("code") == "CANCELLATION_INCOMPLETE":
            code = "core_cancellation_incomplete"
        raise _Retryable(code, "canonical Job cancellation remains pending") from exc
    except Exception as exc:
        await core_session.rollback()
        raise _Retryable(
            "core_cancellation_unavailable",
            "canonical Job cancellation is temporarily unavailable",
        ) from exc
    progress["targets"][attempt_id] = {
        "outcome": "core_cancelled",
        "receipt": receipt_authority["receipt"],
        "receipt_sha256": receipt_authority["receipt_sha256"],
    }
    await _persist_progress(
        session,
        command_id=command.command_id,
        lease_token=str(command.lease_token),
        progress=progress,
    )


async def _set_retryable(
    session: AsyncSession,
    *,
    command_id: str,
    lease_token: str,
    code: str,
    message: str,
) -> None:
    current = await session.get(ExperimentRunControlCommand, command_id)
    attempt_count = int(current.attempt_count) if current is not None else 1
    delay = min(300, 2 ** min(max(attempt_count, 1), 8))
    updated = await session.execute(
        update(ExperimentRunControlCommand)
        .where(
            ExperimentRunControlCommand.command_id == command_id,
            ExperimentRunControlCommand.status == "leased",
            ExperimentRunControlCommand.lease_token == lease_token,
        )
        .values(
            status="retryable",
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            next_retry_at=(datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(),
            last_error_code=code[:128],
            last_error_message=message[:2000],
            updated_at=now(),
        )
        .execution_options(synchronize_session=False)
    )
    if updated.rowcount == 1:
        await session.commit()
    else:
        await session.rollback()


async def _set_conflicted(
    session: AsyncSession,
    *,
    command_id: str,
    lease_token: str,
    conflict: _Conflict,
) -> None:
    document = {
        "schema": "bms.run-control-conflict.v1",
        "command_id": command_id,
        "code": conflict.code,
        "attempt_id": conflict.attempt_id,
        "message": conflict.message,
    }
    conflict_json = _bounded_json(document, label="cancellation conflict")
    updated = await session.execute(
        update(ExperimentRunControlCommand)
        .where(
            ExperimentRunControlCommand.command_id == command_id,
            ExperimentRunControlCommand.status == "leased",
            ExperimentRunControlCommand.lease_token == lease_token,
        )
        .values(
            status="conflicted",
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            next_retry_at=None,
            conflict_json=conflict_json,
            conflict_sha256=sha256_text(conflict_json),
            last_error_code=conflict.code[:128],
            last_error_message=conflict.message[:2000],
            updated_at=now(),
        )
        .execution_options(synchronize_session=False)
    )
    if updated.rowcount == 1:
        await session.commit()
    else:
        await session.rollback()


def _flatten_targets(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for run in snapshot["runs"]:
        if not isinstance(run, dict) or not isinstance(run.get("attempts"), list):
            raise _Conflict("command_authority_invalid", "cancellation run snapshot is malformed")
        for attempt in run["attempts"]:
            if not isinstance(attempt, dict):
                raise _Conflict("command_authority_invalid", "cancellation attempt snapshot is malformed")
            targets.append(attempt)
    if len(snapshot["runs"]) + len(targets) > MAX_COMMAND_ROWS:
        raise _Conflict("command_authority_invalid", "cancellation target exceeds its row limit")
    return targets


async def _revalidate_progress(
    session: AsyncSession,
    core_session: AsyncSession,
    *,
    command: ExperimentRunControlCommand,
    targets: list[dict[str, Any]],
    progress: dict[str, Any],
) -> None:
    for target in targets:
        attempt_id = str(target.get("attempt_id") or "")
        outcome = progress["targets"].get(attempt_id)
        if not isinstance(outcome, dict):
            raise _Retryable("target_progress_incomplete", "cancellation target progress is incomplete")
        if outcome.get("outcome") == "source_terminal_preserved":
            continue
        job, launch_kind, launch_proven = await _observe_target(
            session,
            core_session,
            target,
        )
        if outcome.get("outcome") == "launch_fenced_absent":
            if job is not None or launch_proven or outcome.get("launch_kind") != launch_kind:
                raise _Retryable(
                    "launch_fence_revalidation_changed",
                    "launch-fenced absence requires cancellation reprocessing",
                )
        elif outcome.get("outcome") == "core_cancelled":
            if job is None:
                raise _Retryable(
                    "cancelled_core_job_unavailable",
                    "cancelled canonical Job receipt is temporarily unavailable",
                )
            receipt = await _core_cancelled_receipt(
                core_session,
                root_job=job,
                command_id=command.command_id,
                attempt_id=attempt_id,
            )
            if (
                receipt["receipt"] != outcome.get("receipt")
                or receipt["receipt_sha256"] != outcome.get("receipt_sha256")
            ):
                raise _Conflict(
                    "core_cancellation_receipt_changed",
                    "native core cancellation receipt changed before source publication",
                    attempt_id=attempt_id,
                )
        else:
            raise _Conflict(
                "target_progress_invalid",
                "cancellation target progress has an unsupported outcome",
                attempt_id=attempt_id,
            )


def _cancelled_finished_at(outcome: dict[str, Any], scheduler_job_id: str) -> str:
    receipt = outcome.get("receipt")
    jobs = receipt.get("jobs") if isinstance(receipt, dict) else None
    if not isinstance(jobs, list):
        raise _Conflict(
            "core_cancellation_receipt_invalid",
            "native core cancellation receipt has no bounded Job evidence",
        )
    root_rows = [
        row
        for row in jobs
        if isinstance(row, dict) and row.get("job_id") == scheduler_job_id
    ]
    native = root_rows[0].get("native_receipt") if len(root_rows) == 1 else None
    finished_at = native.get("completed_at") if isinstance(native, dict) else None
    if not isinstance(finished_at, str) or not finished_at or len(finished_at) > 64:
        raise _Conflict(
            "core_cancellation_receipt_invalid",
            "native core cancellation receipt has no canonical completion time",
        )
    return finished_at


async def _persist_cancellation_resource_evidence(
    session: AsyncSession,
    core_session: AsyncSession,
    *,
    command: ExperimentRunControlCommand,
    attempt: ExperimentRunAttempt,
    outcome: dict[str, Any],
) -> None:
    outcome_kind = outcome.get("outcome")
    if outcome_kind == "launch_fenced_absent":
        try:
            await persist_never_launched_resource_usage_evidence(
                session,
                run_attempt_id=attempt.resource_id,
                command_id=command.command_id,
                request_sha256=command.request_sha256,
                launch_kind=str(outcome.get("launch_kind") or ""),
                sealed_at=now(),
            )
        except (ResourceUsageEvidenceError, ResourceUsageEvidenceUnavailable) as exc:
            raise _Conflict(
                "never_launched_resource_evidence_invalid",
                "launch-fenced absence could not seal canonical zero-use evidence",
                attempt_id=attempt.resource_id,
            ) from exc
        return
    if outcome_kind != "core_cancelled":
        raise _Conflict(
            "target_progress_invalid",
            "cancellation target has no resource-evidence outcome",
            attempt_id=attempt.resource_id,
        )

    from database import Job

    core_job = await core_session.get(Job, attempt.scheduler_job_id)
    if core_job is None:
        raise _Retryable(
            "cancelled_core_job_unavailable",
            "cancelled canonical Job is unavailable for resource-evidence recovery",
        )
    try:
        recovered_params = attach_cancelled_resource_receipt_from_checkpoint(
            core_job,
            finished_at=_cancelled_finished_at(outcome, attempt.scheduler_job_id),
        )
    except ResourceUsageEvidenceError:
        return
    if recovered_params is not None:
        core_job.params = recovered_params
        await core_session.commit()
        await core_session.refresh(core_job)
    try:
        await persist_producer_resource_usage_evidence(
            session,
            core_job=core_job,
            run_attempt_id=attempt.resource_id,
        )
    except ResourceUsageEvidenceUnavailable:
        # Cancellation remains valid, but admission stays active until exact
        # producer evidence can be recovered and accepted.
        return


async def _finalize(
    session: AsyncSession,
    core_session: AsyncSession,
    *,
    command: ExperimentRunControlCommand,
    snapshot: dict[str, Any],
    progress: dict[str, Any],
    reason: str,
) -> None:
    targets = _flatten_targets(snapshot)
    await _revalidate_progress(
        session,
        core_session,
        command=command,
        targets=targets,
        progress=progress,
    )
    locked = await session.execute(
        update(ExperimentRunControlCommand)
        .where(
            ExperimentRunControlCommand.command_id == command.command_id,
            ExperimentRunControlCommand.status == "leased",
            ExperimentRunControlCommand.lease_token == command.lease_token,
        )
        .values(updated_at=ExperimentRunControlCommand.updated_at)
        .execution_options(synchronize_session=False)
    )
    if locked.rowcount != 1:
        raise DispatchFailure("cancellation command lease changed before publication")
    current_command = await session.get(ExperimentRunControlCommand, command.command_id)
    if current_command is not None:
        await session.refresh(current_command)
    group = await session.get(ExperimentRunGroup, command.run_group_id)
    if (
        current_command is None
        or current_command.progress_json != canonical_json(progress)
        or current_command.progress_sha256 != sha256_text(current_command.progress_json)
        or group is None
        or group.workspace_id != command.workspace_id
        or group.request_sha256 != snapshot.get("group_request_sha256")
    ):
        raise _Conflict("source_authority_changed", "source cancellation authority changed before publication")
    current_runs = list(
        (
            await session.execute(
                select(ExperimentWorkflowRun)
                .where(ExperimentWorkflowRun.run_group_id == command.run_group_id)
                .order_by(ExperimentWorkflowRun.created_at, ExperimentWorkflowRun.resource_id)
            )
        ).scalars().all()
    )
    snapshot_runs = snapshot["runs"]
    snapshot_runs_by_id = {item.get("run_id"): item for item in snapshot_runs if isinstance(item, dict)}
    if (
        len(snapshot_runs_by_id) != len(snapshot_runs)
        or {run.resource_id for run in current_runs} != set(snapshot_runs_by_id)
    ):
        raise _Conflict("source_run_set_changed", "run-group membership changed after cancellation admission")
    cancelled_attempt_ids: list[str] = []
    preserved_attempt_ids: list[str] = []
    for run in current_runs:
        stored_run = snapshot_runs_by_id[run.resource_id]
        attempts = list(
            (
                await session.execute(
                    select(ExperimentRunAttempt)
                    .where(ExperimentRunAttempt.workflow_run_id == run.resource_id)
                    .order_by(ExperimentRunAttempt.attempt_number, ExperimentRunAttempt.resource_id)
                )
            ).scalars().all()
        )
        stored_attempts = stored_run.get("attempts")
        stored_by_id = {
            item.get("attempt_id"): item for item in stored_attempts if isinstance(item, dict)
        } if isinstance(stored_attempts, list) else {}
        if len(stored_by_id) != len(stored_attempts or []) or {row.resource_id for row in attempts} != set(stored_by_id):
            raise _Conflict(
                "source_attempt_set_changed",
                "run attempt membership changed after cancellation admission",
            )
        run_had_active_target = False
        for attempt in attempts:
            stored = stored_by_id[attempt.resource_id]
            stored_state = str(stored.get("state") or "")
            outcome = progress["targets"][attempt.resource_id]
            if (
                attempt.preparation_id != stored.get("preparation", {}).get("preparation_id")
                or attempt.scheduler_job_id != stored.get("scheduler_job_id")
                or int(attempt.attempt_number) != stored.get("attempt_number")
            ):
                raise _Conflict(
                    "source_target_identity_changed",
                    "source attempt identity changed before cancellation publication",
                    attempt_id=attempt.resource_id,
                )
            if stored_state in TERMINAL_ATTEMPT_STATES:
                if (
                    attempt.state != stored_state
                    or attempt.terminal_receipt_sha256 != stored.get("terminal_receipt_sha256")
                ):
                    raise _Conflict(
                        "source_terminal_outcome_changed",
                        "historical terminal source outcome changed after cancellation admission",
                        attempt_id=attempt.resource_id,
                    )
                preserved_attempt_ids.append(attempt.resource_id)
                continue
            if attempt.state in {"completed", "failed"}:
                raise _Conflict(
                    "source_terminal_outcome_changed",
                    "active cancellation target reached a contradictory source terminal outcome",
                    attempt_id=attempt.resource_id,
                )
            if attempt.state not in ACTIVE_ATTEMPT_STATES | {"cancelled"}:
                raise _Conflict(
                    "source_state_invalid",
                    "active cancellation target has an unsupported current source state",
                    attempt_id=attempt.resource_id,
                )
            terminal_receipt: dict[str, Any] = {
                "schema": "bms.experiment.cancellation-terminal-receipt.v1",
                "command_id": command.command_id,
                "request_sha256": command.request_sha256,
                "attempt_id": attempt.resource_id,
                "scheduler_job_id": attempt.scheduler_job_id,
                "outcome": outcome["outcome"],
            }
            if outcome["outcome"] == "core_cancelled":
                terminal_receipt["core_receipt"] = outcome["receipt"]
                terminal_receipt["core_receipt_sha256"] = outcome["receipt_sha256"]
            else:
                terminal_receipt["launch_fence"] = {
                    "state": "absent",
                    "launch_kind": outcome["launch_kind"],
                }
            terminal_json = _bounded_json(terminal_receipt, label="cancellation terminal receipt")
            attempt_update = await session.execute(
                update(ExperimentRunAttempt)
                .where(
                    ExperimentRunAttempt.resource_id == attempt.resource_id,
                    ExperimentRunAttempt.workflow_run_id == run.resource_id,
                    ExperimentRunAttempt.preparation_id == attempt.preparation_id,
                    ExperimentRunAttempt.scheduler_job_id == attempt.scheduler_job_id,
                    ExperimentRunAttempt.state == attempt.state,
                )
                .values(
                    state="cancelled",
                    terminal_receipt_json=terminal_json,
                    terminal_receipt_sha256=sha256_text(terminal_json),
                )
                .execution_options(synchronize_session=False)
            )
            if attempt_update.rowcount != 1:
                raise _Conflict(
                    "source_attempt_cas_failed",
                    "source attempt changed during cancellation publication",
                    attempt_id=attempt.resource_id,
                )
            await session.refresh(attempt)
            await _persist_cancellation_resource_evidence(
                session,
                core_session,
                command=command,
                attempt=attempt,
                outcome=outcome,
            )
            cancelled_attempt_ids.append(attempt.resource_id)
            run_had_active_target = True
            outbox = await session.scalar(
                select(ExperimentDispatchOutbox).where(
                    ExperimentDispatchOutbox.run_attempt_id == attempt.resource_id,
                    ExperimentDispatchOutbox.event_type == "materialize_scheduler_job",
                )
            )
            if outbox is not None and outbox.status in {"pending", "dispatching"}:
                outbox.status = "failed"
                outbox.lease_token = None
                outbox.lease_owner = None
                outbox.lease_acquired_at = None
                outbox.lease_expires_at = None
                outbox.last_error = "cancelled_by_run_control_command"
                outbox.updated_at = now()
            context = await session.scalar(
                select(ExperimentLaunchContext).where(
                    ExperimentLaunchContext.run_attempt_id == attempt.resource_id
                )
            )
            if context is not None and context.state == "reserved":
                context.claim_token = f"cancel-fence:{command.command_id}"[:128]
                context.claimed_at = context.claimed_at or now()
        if run_had_active_target:
            run_generation = int(run.generation)
            run_update = await session.execute(
                update(ExperimentWorkflowRun)
                .where(
                    ExperimentWorkflowRun.resource_id == run.resource_id,
                    ExperimentWorkflowRun.run_group_id == group.resource_id,
                    ExperimentWorkflowRun.state == run.state,
                    ExperimentWorkflowRun.generation == run_generation,
                )
                .values(state="cancelled", generation=run_generation + 1)
                .execution_options(synchronize_session=False)
            )
            if run_update.rowcount != 1:
                raise _Conflict(
                    "source_run_cas_failed",
                    "source run changed during cancellation publication",
                )
    group_generation = int(group.generation)
    group_update = await session.execute(
        update(ExperimentRunGroup)
        .where(
            ExperimentRunGroup.resource_id == group.resource_id,
            ExperimentRunGroup.workspace_id == command.workspace_id,
            ExperimentRunGroup.state == group.state,
            ExperimentRunGroup.generation == group_generation,
        )
        .values(
            state="cancelled",
            generation=group_generation + 1,
            updated_at=now(),
        )
        .execution_options(synchronize_session=False)
    )
    if group_update.rowcount != 1:
        raise _Conflict(
            "source_group_cas_failed",
            "run group changed during cancellation publication",
        )
    acknowledgement = {
        "schema": "bms.run-control-cancellation-acknowledgement.v1",
        "command_id": command.command_id,
        "workspace_id": command.workspace_id,
        "run_group_id": command.run_group_id,
        "request_sha256": command.request_sha256,
        "target_snapshot_sha256": command.target_snapshot_sha256,
        "progress_sha256": sha256_text(canonical_json(progress)),
        "resulting_generation": group_generation + 1,
        "cancelled_attempt_ids": sorted(cancelled_attempt_ids),
        "preserved_terminal_attempt_ids": sorted(preserved_attempt_ids),
    }
    acknowledgement_json = _bounded_json(
        acknowledgement,
        label="cancellation acknowledgement",
    )
    add_audit_event(
        session,
        workspace_id=command.workspace_id,
        resource_id=command.run_group_id,
        event_type="run_group_cancelled",
        generation=group_generation + 1,
        payload={
            "schema": "bms.run-control-cancellation-audit.v1",
            "command_id": command.command_id,
            "request_sha256": command.request_sha256,
            "acknowledgement_sha256": sha256_text(acknowledgement_json),
            "cancelled_attempt_count": len(cancelled_attempt_ids),
            "preserved_terminal_attempt_count": len(preserved_attempt_ids),
        },
    )
    current_command.status = "applied"
    current_command.lease_owner = None
    current_command.lease_token = None
    current_command.lease_expires_at = None
    current_command.next_retry_at = None
    current_command.acknowledgement_json = acknowledgement_json
    current_command.acknowledgement_sha256 = sha256_text(acknowledgement_json)
    current_command.last_error_code = None
    current_command.last_error_message = None
    current_command.updated_at = now()
    current_command.applied_at = now()
    await session.commit()


async def process_run_control_command(
    session: AsyncSession,
    core_session: AsyncSession,
    *,
    command_id: str,
    worker_id: str,
) -> ExperimentRunControlCommand:
    command = await _claim_command(
        session,
        command_id=command_id,
        worker_id=worker_id,
    )
    if command is None:
        raise NotFound("run control command not found")
    if command.status in {"applied", "conflicted"}:
        return command
    if command.status != "leased" or command.lease_owner != worker_id or not command.lease_token:
        return command
    lease_token = str(command.lease_token)
    try:
        snapshot, progress = _decode_command(command)
        request_document = json.loads(command.request_json)
        reason = str(request_document.get("reason") or "")
        for target in _flatten_targets(snapshot):
            await _process_target(
                session,
                core_session,
                command=command,
                target=target,
                progress=progress,
                reason=reason,
            )
        await _finalize(
            session,
            core_session,
            command=command,
            snapshot=snapshot,
            progress=progress,
            reason=reason,
        )
    except _Conflict as conflict:
        await session.rollback()
        await core_session.rollback()
        await _set_conflicted(
            session,
            command_id=command.command_id,
            lease_token=lease_token,
            conflict=conflict,
        )
    except _Retryable as retryable:
        await session.rollback()
        await core_session.rollback()
        await _set_retryable(
            session,
            command_id=command.command_id,
            lease_token=lease_token,
            code=retryable.code,
            message=retryable.message,
        )
    except Exception:
        await session.rollback()
        await core_session.rollback()
        await _set_retryable(
            session,
            command_id=command.command_id,
            lease_token=lease_token,
            code="run_control_processing_unavailable",
            message="durable cancellation processing is temporarily unavailable",
        )
    refreshed = await session.get(ExperimentRunControlCommand, command.command_id)
    if refreshed is None:
        raise DispatchFailure("run control command disappeared after processing")
    return refreshed


async def process_run_control_command_once(
    session: AsyncSession,
    core_session: AsyncSession,
    *,
    worker_id: str,
) -> int:
    command = await _claim_command(session, command_id=None, worker_id=worker_id)
    if command is None:
        return 0
    if command.status != "leased" or command.lease_owner != worker_id:
        return 0
    # process_run_control_command reuses the live lease because it is not ready
    # for a second claim; execute the leased command directly through a private
    # worker identity-preserving path.
    lease_token = str(command.lease_token or "")
    try:
        snapshot, progress = _decode_command(command)
        request_document = json.loads(command.request_json)
        reason = str(request_document.get("reason") or "")
        for target in _flatten_targets(snapshot):
            await _process_target(
                session,
                core_session,
                command=command,
                target=target,
                progress=progress,
                reason=reason,
            )
        await _finalize(
            session,
            core_session,
            command=command,
            snapshot=snapshot,
            progress=progress,
            reason=reason,
        )
    except _Conflict as conflict:
        await session.rollback()
        await core_session.rollback()
        await _set_conflicted(
            session,
            command_id=command.command_id,
            lease_token=lease_token,
            conflict=conflict,
        )
    except _Retryable as retryable:
        await session.rollback()
        await core_session.rollback()
        await _set_retryable(
            session,
            command_id=command.command_id,
            lease_token=lease_token,
            code=retryable.code,
            message=retryable.message,
        )
    except Exception:
        await session.rollback()
        await core_session.rollback()
        await _set_retryable(
            session,
            command_id=command.command_id,
            lease_token=lease_token,
            code="run_control_processing_unavailable",
            message="durable cancellation processing is temporarily unavailable",
        )
    return 1


__all__ = [
    "ACTIVE_COMMAND_STATUSES",
    "blocking_cancellation_command",
    "command_document",
    "process_run_control_command",
    "process_run_control_command_once",
    "request_run_group_cancellation",
    "require_launch_not_fenced",
]
