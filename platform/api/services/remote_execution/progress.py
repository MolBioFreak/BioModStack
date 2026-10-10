"""Durable, attempt-fenced remote artifact activity (never estimated percent)."""
from __future__ import annotations

import json
from datetime import datetime
from sqlalchemy import func, update
from database import ExecutionTarget, Job

PRELOAD_ACTIVE_PHASES = ("checking", "transferring", "verifying", "cancelling", "recovery_blocked")


def preload_idle_clause():
    """Serialize provisioning and attachment changes, not scientific work."""
    return func.coalesce(ExecutionTarget.provider_metadata["preload"]["phase"].as_string(), "").notin_(PRELOAD_ACTIVE_PHASES)


async def publish_job_progress(session, job, *, phase, artifact, message, activity=None):
    """Publish this owned attempt without replacing a sibling's activity."""
    from .contracts import RemoteArtifactProgress
    progress = RemoteArtifactProgress(
        operation_id=str(job.remote_attempt_id), job_id=str(job.id),
        phase=phase, artifact=artifact, message=message, activity=activity, updated_at=datetime.utcnow(),
    ).model_dump(mode="json")
    identity = Job.id == str(job.id)
    from sqlalchemy import case, select
    from .claims import job_claim_authority
    owns = select(Job.id).where(identity, job_claim_authority(job),
        Job.remote_attempt_id == str(job.remote_attempt_id),
        Job.execution_target_id == str(job.execution_target_id),
        Job.status.in_(("queued", "running")),
        func.coalesce(Job.remote_state, "").notin_((
            "succeeded", "failed", "cancelled", "lost", "results_available", "result_pull_failed", "returning",
        )),
    ).exists()
    metadata = ExecutionTarget.provider_metadata
    legacy = ExecutionTarget.leased_job_id == str(job.id)
    prior = metadata["job_progress"][str(job.id)]
    operation = func.coalesce(case((legacy, metadata["progress"]["operation_id"].as_string())),
        prior["operation_id"].as_string(), "")
    prior_phase = func.coalesce(case((legacy, metadata["progress"]["phase"].as_string())),
        prior["phase"].as_string(), "")
    payload = func.json(json.dumps(progress))
    updated = func.json_set(metadata, "$.job_progress." + json.dumps(str(job.id)), payload)
    result = await session.execute(update(ExecutionTarget).where(
        ExecutionTarget.id == str(job.execution_target_id), owns,
        # A delayed callback must not regress this attempt's terminal projection.
        ~((operation == str(job.remote_attempt_id)) & prior_phase.in_(("completed", "failed"))),
    ).values(provider_metadata=case((legacy, func.json_set(updated, "$.progress", payload)),
        else_=updated)).execution_options(synchronize_session=False))
    await session.commit()
    return result.rowcount == 1
