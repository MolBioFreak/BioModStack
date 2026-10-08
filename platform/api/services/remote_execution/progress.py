"""Durable, attempt-fenced remote artifact activity (never estimated percent)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from sqlalchemy import func, update
from database import ExecutionTarget, Job

PRELOAD_ACTIVE_PHASES = ("checking", "transferring", "verifying", "cancelling", "recovery_blocked")

PROGRESS_DEADLINE_ENV = "BMS_REMOTE_ATTEMPT_PROGRESS_DEADLINE_SECONDS"
# The pre-existing staging grace stays honoured so an operator who already tuned
# it does not silently get a second, different deadline.
LEGACY_PROGRESS_DEADLINE_ENV = "BMS_REMOTE_STAGING_RECOVERY_GRACE_SECONDS"
DEFAULT_PROGRESS_DEADLINE_SECONDS = 900.0

# The deadline and its evidence live in the Job's own durable state, not only in
# a log line, so an operator can see why an attempt is about to be abandoned.
RECOVERY_PROVENANCE_KEY = "remote_attempt_recovery"
RECOVERY_SCHEMA = "bms.remote-attempt-recovery.v1"
RECOVERY_RESUMED_STATES = frozenset({"awaiting_progress", "expired", "abandoned"})
RECOVERY_VOLATILE_FIELDS = ("observed_at", "observed_error")
STALL_CONFIRMATION_ENV = "BMS_REMOTE_ATTEMPT_STALL_CONFIRMATION_SECONDS"
DEFAULT_STALL_CONFIRMATION_SECONDS = 60.0


def preload_idle_clause():
    """Use in the SAME target UPDATE that reserves scientific work/attachment."""
    return func.coalesce(ExecutionTarget.provider_metadata["preload"]["phase"].as_string(), "").notin_(PRELOAD_ACTIVE_PHASES)


def preload_active(target):
    return (target.provider_metadata or {}).get("preload", {}).get("phase") in PRELOAD_ACTIVE_PHASES


def progress_deadline_seconds() -> float:
    """Bounded window in which an owned attempt must show forward progress."""
    for name in (PROGRESS_DEADLINE_ENV, LEGACY_PROGRESS_DEADLINE_ENV):
        raw = os.environ.get(name)
        if raw is None or not str(raw).strip():
            continue
        try:
            return max(0.0, float(str(raw).strip()))
        except ValueError:
            continue
    return DEFAULT_PROGRESS_DEADLINE_SECONDS


def _timestamp(value) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is not None else value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def attempt_progress_activity(job, target=None) -> dict | None:
    """This attempt's own last published activity, never a predecessor's."""
    if target is None or not job.remote_attempt_id:
        return None
    record = (getattr(target, "provider_metadata", None) or {}).get("progress")
    if not isinstance(record, dict) or str(record.get("operation_id") or "") != str(job.remote_attempt_id):
        return None
    return record


def progress_anchor(job, target=None) -> tuple[datetime | None, str]:
    """Durable, non-sliding origin for this attempt's progress deadline.

    Real published activity outranks the claim timestamp, so a healthy attempt
    keeps its window open while a quiet one cannot reset it by being polled.
    """
    activity = attempt_progress_activity(job, target)
    if activity is not None:
        observed = _timestamp(activity.get("updated_at"))
        if observed is not None:
            return observed, "attempt_activity"
    assignment = dict((job.provenance or {}).get("remote_execution_assignment") or {})
    claimed = _timestamp(assignment.get("claimed_at"))
    if claimed is not None:
        return claimed, "attempt_claim"
    receipt = dict((job.provenance or {}).get("remote_execution_receipt") or {})
    started = _timestamp(receipt.get("started_at")) or _timestamp(getattr(job, "started_at", None))
    if started is not None:
        return started, "attempt_started"
    created = _timestamp(getattr(job, "created_at", None))
    if created is not None:
        return created, "job_created"
    return None, "unknown"


def progress_deadline(job, *, target=None, now: datetime | None = None) -> dict:
    """When this attempt must show forward progress, and what proves it."""
    now = now or datetime.utcnow()
    anchor, source = progress_anchor(job, target)
    seconds = progress_deadline_seconds()
    deadline = None if anchor is None else anchor + timedelta(seconds=seconds)
    activity = attempt_progress_activity(job, target) or {}
    return {
        "anchor_at": None if anchor is None else anchor.isoformat(),
        "anchor_source": source,
        "deadline_seconds": seconds,
        "deadline_at": None if deadline is None else deadline.isoformat(),
        # An attempt with no durable origin at all has nothing left to wait for.
        "expired": True if deadline is None else now >= deadline,
        "last_progress": (
            {key: activity.get(key) for key in ("phase", "artifact", "message", "updated_at")}
            if activity
            else None
        ),
    }


def attempt_recovery_record(
    job, *, state: str, target=None, observed_error: str | None = None,
    observed_at: datetime | None = None, detail: str | None = None,
) -> dict:
    """The Job-visible explanation of what recovery observed and what it will do."""
    observed_at = observed_at or datetime.utcnow()
    record = {
        "schema": RECOVERY_SCHEMA,
        "attempt_id": str(job.remote_attempt_id or "") or None,
        "execution_target_id": str(job.execution_target_id or "") or None,
        "remote_state": str(job.remote_state or "") or None,
        "state": str(state),
        "resume_lane": "worker_prepare_over_content_addressed_materialization",
        **progress_deadline(job, target=target, now=observed_at),
        "observed_at": observed_at.isoformat(),
        "observed_error": None if observed_error is None else str(observed_error)[:400],
    }
    if detail is not None:
        record["detail"] = str(detail)[:400]
    return record


def recovery_record_changed(previous, current) -> bool:
    """Only material recovery changes republish; observation timestamps never do."""
    def material(record):
        if not isinstance(record, dict):
            return None
        return {key: value for key, value in record.items() if key not in RECOVERY_VOLATILE_FIELDS}
    return material(previous) != material(current)


def stall_confirmation_seconds() -> float:
    raw = os.environ.get(STALL_CONFIRMATION_ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT_STALL_CONFIRMATION_SECONDS
    try:
        return max(0.0, float(str(raw).strip()))
    except ValueError:
        return DEFAULT_STALL_CONFIRMATION_SECONDS


def attempt_stall_confirmed(job, target=None, *, now: datetime | None = None) -> bool:
    """True once the expired deadline has also outlived the confirmation window.

    The confirmation window is the difference between "quiet past its deadline"
    and "provably not writing": the worker's own resume probe has already failed,
    and the attempt has had a full extra window to publish any forward progress
    that would have pushed the deadline out. Inside that window the stall is
    recorded (state ``expired``) and nothing terminal happens; past it the
    attempt is abandoned with the same evidence.
    """
    now = now or datetime.utcnow()
    current = progress_deadline(job, target=target, now=now)
    if not current["expired"] or not current["deadline_at"]:
        return False
    deadline = _timestamp(current["deadline_at"])
    return deadline is not None and (now - deadline).total_seconds() >= stall_confirmation_seconds()


async def publish_job_progress(session, job, *, phase, artifact, message, activity=None):
    """Publish only the currently leased attempt; no Job mutation or secrets."""
    from .contracts import RemoteArtifactProgress
    progress = RemoteArtifactProgress(
        operation_id=str(job.remote_attempt_id), job_id=str(job.id),
        phase=phase, artifact=artifact, message=message, activity=activity, updated_at=datetime.utcnow(),
    ).model_dump(mode="json")
    identity = Job.id == str(job.id)
    from sqlalchemy import select
    owns = select(Job.id).where(identity,
        Job.remote_attempt_id == str(job.remote_attempt_id),
        Job.execution_target_id == str(job.execution_target_id),
        Job.status.in_(("queued", "running")),
        func.coalesce(Job.remote_state, "").notin_((
            "succeeded", "failed", "cancelled", "lost", "results_available", "result_pull_failed", "returning",
        )),
    ).exists()
    result = await session.execute(update(ExecutionTarget).where(
        ExecutionTarget.id == str(job.execution_target_id),
        ExecutionTarget.leased_job_id == str(job.id), owns,
        # A delayed callback must not regress this attempt's terminal projection.
        ~((func.coalesce(ExecutionTarget.provider_metadata["progress"]["operation_id"].as_string(), "") == str(job.remote_attempt_id))
          & func.coalesce(ExecutionTarget.provider_metadata["progress"]["phase"].as_string(), "").in_(("completed", "failed"))),
    ).values(provider_metadata=func.json_set(ExecutionTarget.provider_metadata,
        "$.progress", func.json(json.dumps(progress)))).execution_options(synchronize_session=False))
    await session.commit()
    return result.rowcount == 1
