"""Durable, attempt-fenced remote artifact activity (never estimated percent)."""
from __future__ import annotations

import json
from datetime import datetime
from sqlalchemy import func, update
from database import ExecutionTarget, Job

PRELOAD_ACTIVE_PHASES = ("checking", "transferring", "verifying")


def preload_idle_clause():
    """Use in the SAME target UPDATE that reserves scientific work/attachment."""
    return func.coalesce(ExecutionTarget.provider_metadata["preload"]["phase"].as_string(), "").notin_(PRELOAD_ACTIVE_PHASES)


def preload_active(target):
    return (target.provider_metadata or {}).get("preload", {}).get("phase") in PRELOAD_ACTIVE_PHASES


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
