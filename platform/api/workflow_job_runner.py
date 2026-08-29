"""Execute one authoritative workflow attempt inside its transient systemd unit."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import logging
import os
import sys
from typing import Any

import sqlalchemy

import database
from services import nextflow
from services.execution_ownership import (
    AdapterIdentity,
    ExecutionOwnershipError,
    EXECUTION_ATTEMPT_TERMINAL_STATES,
    TRANSIENT_WORKFLOW_OWNER_NONCE_ENV,
    TRANSIENT_WORKFLOW_UNIT_ENV,
    TRANSIENT_WORKFLOW_UNIT_NAME_ENV,
    assert_unit_lane,
    adapter_identity_from_environment,
    cancellation_intent_requested,
    execution_attempt_is_terminal,
    latest_execution_attempt,
    params_mapping,
    release_scheduler_gpu_assignment,
    show_unit_properties,
    strip_execution_metadata,
    update_execution_attempt,
    utc_timestamp,
)
from services.resource_usage_evidence import (
    RESOURCE_USAGE_RECEIPTS_PARAM,
    ResourceUsageEvidenceError,
    WorkflowResourceMonitor,
    attach_resource_usage_receipt,
    strip_resource_execution_metadata,
)


logger = logging.getLogger(__name__)


def _positive_float_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def require_transient_environment(job_id: str, lane: str) -> tuple[AdapterIdentity, str, str, str]:
    """Prove that this process is the exact runner selected by the adapter."""
    if os.getenv(TRANSIENT_WORKFLOW_UNIT_ENV) != "1":
        raise ExecutionOwnershipError(
            f"{TRANSIENT_WORKFLOW_UNIT_ENV}=1 is required for workflow execution"
        )
    identity = adapter_identity_from_environment()
    if identity.lane != str(lane).strip().lower():
        raise ExecutionOwnershipError(
            f"Runner lane {lane!r} does not match adapter lane {identity.lane!r}"
        )
    unit_name = str(os.getenv(TRANSIENT_WORKFLOW_UNIT_NAME_ENV) or "").strip()
    if not unit_name:
        raise ExecutionOwnershipError(
            f"{TRANSIENT_WORKFLOW_UNIT_NAME_ENV} is required for transient workflow execution"
        )
    unit_identity = assert_unit_lane(unit_name, identity.lane)
    if unit_identity.job_id != str(job_id):
        raise ExecutionOwnershipError(
            f"Runner job {job_id!r} does not match transient unit {unit_name!r}"
        )
    owner_nonce = str(os.getenv(TRANSIENT_WORKFLOW_OWNER_NONCE_ENV) or "").strip()
    if not owner_nonce:
        raise ExecutionOwnershipError(
            f"{TRANSIENT_WORKFLOW_OWNER_NONCE_ENV} is required for transient workflow execution"
        )
    invocation_id = str(os.getenv("INVOCATION_ID") or "").strip()
    if not invocation_id:
        raise ExecutionOwnershipError("systemd INVOCATION_ID is required for transient workflow execution")
    return identity, unit_name, owner_nonce, invocation_id


async def _load_authoritative_attempt(
    *,
    job_id: str,
    lane: str,
    unit_name: str,
    owner_nonce: str,
    invocation_id: str,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """Prove the planned receipt and publish the started state from the runner."""
    async with database.async_session() as session:
        await session.execute(sqlalchemy.text("BEGIN IMMEDIATE"))
        result = await session.execute(sqlalchemy.select(database.Job).where(database.Job.id == job_id))
        job = result.scalar_one_or_none()
        if job is None:
            raise ExecutionOwnershipError(f"Authoritative workflow job {job_id} was not found")
        if not database.launch_context_binding_ready(job):
            raise ExecutionOwnershipError("launch-context source binding is not durably published")
        if cancellation_intent_requested(job):
            raise ExecutionOwnershipError("authoritative cancellation intent fences runner start")
        params = params_mapping(getattr(job, "params", {}))
        receipt = latest_execution_attempt(params)
        if receipt is None or str(receipt.get("lane", "")) != lane:
            raise ExecutionOwnershipError("Authoritative planned workflow receipt is missing")
        if str(receipt.get("unit", "")) != unit_name:
            raise ExecutionOwnershipError("Authoritative receipt unit does not match the transient unit")
        if str(receipt.get("owner_nonce", "")) != owner_nonce:
            raise ExecutionOwnershipError("Transient workflow owner nonce does not match the receipt")
        state = str(receipt.get("state", "")).strip().lower()
        if state in EXECUTION_ATTEMPT_TERMINAL_STATES:
            raise ExecutionOwnershipError(f"Transient workflow receipt became terminal before runner start: {state}")
        properties = await asyncio.to_thread(show_unit_properties, unit_name, lane)
        if properties.invocation_id != invocation_id:
            raise ExecutionOwnershipError("systemd InvocationID does not match the transient runner")
        if state == "started":
            if str(receipt.get("invocation_id", "")) != invocation_id:
                raise ExecutionOwnershipError("Started workflow receipt has a different InvocationID")
            return job, params, receipt
        if state != "planned":
            raise ExecutionOwnershipError(f"Transient workflow receipt has invalid startup state: {state}")
        params = update_execution_attempt(
            params,
            lane=lane,
            generation=int(receipt["generation"]),
            attempt=int(receipt["attempt"]),
            unit=unit_name,
            owner_nonce=owner_nonce,
            changes={
                "state": "started",
                "invocation_id": invocation_id,
                "started_at": utc_timestamp(),
            },
        )
        job.params = params
        if str(getattr(job, "status", "") or "").lower() not in {"completed", "failed", "cancelled"}:
            job.status = "running"
            job.queue_status = "running"
            if getattr(job, "started_at", None) is None:
                job.started_at = datetime.utcnow()
        await session.commit()
        return job, params, latest_execution_attempt(params) or receipt


async def _finish_attempt(
    *,
    job_id: str,
    lane: str,
    unit_name: str,
    owner_nonce: str,
    invocation_id: str,
    state: str,
    reason: str | None = None,
) -> str:
    async with database.async_session() as session:
        await session.execute(sqlalchemy.text("BEGIN IMMEDIATE"))
        result = await session.execute(
            sqlalchemy.select(database.Job).where(database.Job.id == job_id)
        )
        job = result.scalar_one_or_none()
        if job is None:
            return state
        params = params_mapping(getattr(job, "params", {}))
        receipt = latest_execution_attempt(params)
        if receipt is None or str(receipt.get("lane", "")) != lane:
            return state
        if str(receipt.get("unit", "")) != unit_name or str(receipt.get("owner_nonce", "")) != owner_nonce:
            return state
        if str(receipt.get("invocation_id", "")) != invocation_id:
            logger.error(
                "Refused terminal workflow receipt for %s: InvocationID mismatch receipt=%r runner=%r",
                job_id,
                receipt.get("invocation_id"),
                invocation_id,
            )
            return state
        # Startup reconciliation can terminalize an ownership record while an
        # old runner is still draining.  That terminal decision is authoritative
        # and must not be replaced by a late completion from the old process.
        if execution_attempt_is_terminal(receipt):
            return state
        try:
            generation = int(receipt["generation"])
            attempt = int(receipt["attempt"])
            params = update_execution_attempt(
                params,
                lane=lane,
                generation=generation,
                attempt=attempt,
                unit=unit_name,
                owner_nonce=owner_nonce,
                changes={
                    "state": state,
                    "terminal_at": utc_timestamp(),
                    "terminal_reason": reason or state,
                    "invocation_id": invocation_id,
                },
            )
        except (KeyError, TypeError, ValueError, ExecutionOwnershipError):
            logger.exception("Could not publish terminal workflow receipt for %s", job_id)
            return state
        job.params = release_scheduler_gpu_assignment(params)
        if state in {"completed", "failed", "interrupted_owner", "cancelled"}:
            job.assigned_gpu = None
        if state in {"failed", "interrupted_owner"} and str(getattr(job, "status", "") or "").lower() not in {
            "completed",
            "cancelled",
            "failed",
        }:
            job.status = "failed"
            job.queue_status = "failed"
            job.error_message = reason or state
            job.completed_at = datetime.utcnow()
        await session.commit()
    return state


async def _persist_resource_usage_receipt(job_id: str, receipt: dict[str, Any]) -> None:
    """Append one producer receipt without replacing another execution identity."""

    async with database.async_session() as session:
        await session.execute(sqlalchemy.text("BEGIN IMMEDIATE"))
        result = await session.execute(
            sqlalchemy.select(database.Job).where(database.Job.id == job_id)
        )
        job = result.scalar_one_or_none()
        if job is None:
            raise ResourceUsageEvidenceError("resource receipt Job disappeared")
        job.params = attach_resource_usage_receipt(job.params, receipt)
        await session.commit()


def _terminal_state(job: Any) -> tuple[str, str | None]:
    status = str(getattr(job, "status", "") or "").strip().lower()
    if status == "cancelled":
        params = params_mapping(getattr(job, "params", {}))
        cancellation = params.get("cancellation_receipt")
        if isinstance(cancellation, dict) and cancellation.get("state") == "completed":
            return "cancelled", "API cancellation receipt"
        return "failed", "CANCELLED_WITHOUT_API_RECEIPT"
    if status == "completed" or status == "awaiting_input":
        return "completed", "job finalization completed"
    if status == "failed":
        return "failed", str(getattr(job, "error_message", "") or "workflow failed")[:2000]
    return "interrupted_owner", "runner returned with nonterminal Job state"


async def run_workflow_job(job_id: str, lane: str) -> int:
    identity, unit_name, owner_nonce, invocation_id = require_transient_environment(job_id, lane)
    job, params, _receipt = await _load_authoritative_attempt(
        job_id=job_id,
        lane=identity.lane,
        unit_name=unit_name,
        owner_nonce=owner_nonce,
        invocation_id=invocation_id,
    )
    monitor: WorkflowResourceMonitor | None = None
    terminal_resource_receipt: dict[str, Any] | None = None

    def finish_success_resource_evidence() -> dict[str, Any]:
        nonlocal terminal_resource_receipt
        if monitor is None:
            raise ResourceUsageEvidenceError("ONT success has no producer resource monitor")
        if terminal_resource_receipt is None:
            terminal_resource_receipt = monitor.finish(outcome="completed")
        if terminal_resource_receipt.get("complete") is not True:
            raise ResourceUsageEvidenceError("producer resource evidence is incomplete")
        return dict(terminal_resource_receipt)

    async def persist_terminal_resource_evidence(*, outcome: str) -> None:
        nonlocal terminal_resource_receipt
        if monitor is None:
            return
        if terminal_resource_receipt is None:
            terminal_resource_receipt = monitor.finish(outcome=outcome)
        await _persist_resource_usage_receipt(job_id, terminal_resource_receipt)

    try:
        monitor = WorkflowResourceMonitor.from_job(job)
        if monitor is not None:
            monitor.start()
        authoritative_model_id = str(job.model_id)
        authoritative_mode = str(job.mode)
        authoritative_output_dir = str(getattr(job, "output_dir", "") or "")
        if not authoritative_output_dir:
            raise ExecutionOwnershipError(
                f"Authoritative Job.output_dir is required for workflow {job_id}"
            )
        execution_params = strip_resource_execution_metadata(strip_execution_metadata(params))
        if authoritative_model_id == "nanopore":
            ngs_runtime_sif = str(os.getenv("BMS_NGS_RUNTIME_SIF") or "").strip()
            if ngs_runtime_sif:
                execution_params["dorado_runtime_sif"] = ngs_runtime_sif
        await nextflow.launch_nextflow_job(
            job_id=job_id,
            model_id=authoritative_model_id,
            mode=authoritative_mode,
            params=execution_params,
            output_dir=authoritative_output_dir,
            allow_running_job=True,
            terminal_resource_receipt_factory=(
                finish_success_resource_evidence if monitor is not None else None
            ),
        )
    except Exception as exc:
        logger.exception("Transient workflow runner failed for %s", job_id)
        if monitor is not None:
            try:
                await persist_terminal_resource_evidence(outcome="failed")
            except Exception:
                logger.exception("Could not persist failed resource-use evidence for %s", job_id)
        await _finish_attempt(
            job_id=job_id,
            lane=identity.lane,
            unit_name=unit_name,
            owner_nonce=owner_nonce,
            invocation_id=invocation_id,
            state="failed",
            reason=str(exc),
        )
        return 1

    try:
        async with database.async_session() as session:
            result = await session.execute(
                sqlalchemy.select(database.Job).where(database.Job.id == job_id)
            )
            final_job = result.scalar_one_or_none()
    except Exception as exc:
        logger.exception("Could not reload the authoritative workflow result for %s", job_id)
        if monitor is not None:
            try:
                await persist_terminal_resource_evidence(outcome="failed")
            except Exception:
                logger.exception("Could not persist failed resource-use evidence for %s", job_id)
        await _finish_attempt(
            job_id=job_id,
            lane=identity.lane,
            unit_name=unit_name,
            owner_nonce=owner_nonce,
            invocation_id=invocation_id,
            state="failed",
            reason=str(exc),
        )
        return 1
    if final_job is None:
        if monitor is not None:
            try:
                await persist_terminal_resource_evidence(outcome="failed")
            except Exception:
                logger.exception("Could not persist failed resource-use evidence for %s", job_id)
        await _finish_attempt(
            job_id=job_id,
            lane=identity.lane,
            unit_name=unit_name,
            owner_nonce=owner_nonce,
            invocation_id=invocation_id,
            state="failed",
            reason="authoritative job disappeared after workflow execution",
        )
        return 1

    state, reason = _terminal_state(final_job)
    if monitor is not None:
        try:
            if terminal_resource_receipt is None:
                await persist_terminal_resource_evidence(outcome=state)
            else:
                persisted_receipts = params_mapping(final_job.params).get(
                    RESOURCE_USAGE_RECEIPTS_PARAM
                )
                if (
                    not isinstance(persisted_receipts, list)
                    or dict(terminal_resource_receipt)
                    not in [dict(item) for item in persisted_receipts if isinstance(item, dict)]
                ):
                    raise ResourceUsageEvidenceError(
                        "terminal Job omitted the producer resource receipt"
                    )
            resource_receipt = terminal_resource_receipt or {}
            if state == "completed" and resource_receipt.get("complete") is not True:
                state = "failed"
                reason = "producer_resource_evidence_incomplete"
        except Exception:
            logger.exception("Could not persist terminal resource-use evidence for %s", job_id)
            state = "failed"
            reason = "producer_resource_evidence_persistence_failed"
    await _finish_attempt(
        job_id=job_id,
        lane=identity.lane,
        unit_name=unit_name,
        owner_nonce=owner_nonce,
        invocation_id=invocation_id,
        state=state,
        reason=reason,
    )
    return 0 if state in {"completed", "cancelled"} else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--lane", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.getenv("BMS_LOG_LEVEL", "INFO"))
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(run_workflow_job(str(args.job_id), str(args.lane)))
    except Exception:
        logger.exception("Transient workflow runner rejected job %s", args.job_id)
        return 78


if __name__ == "__main__":
    sys.exit(main())
