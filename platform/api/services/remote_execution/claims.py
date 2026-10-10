"""Remote ownership projection: Jobs own shared claims; old target leases survive.

No status is evidence of release. Only the existing quiescent release owners
mark an assignment released, including terminal and preparing Jobs.
"""
from sqlalchemy import and_, or_, select
from database import ExecutionTarget, Job

POLICY = "vram_packing"
IDENTITY_FIELDS = ("host", "port", "username", "remote_root", "host_key_sha256")


def shared_claim_clause():
    assignment = Job.provenance["remote_execution_assignment"]
    return and_(
        Job.execution_target_id.is_not(None),
        assignment["policy"].as_string() == POLICY,
        assignment["lease_id"].as_string().is_not(None),
        assignment["execution_target_id"].as_string() == Job.execution_target_id,
        assignment["root_job_id"].as_string() == Job.id,
        assignment["released_at"].as_string().is_(None),
    )


def outstanding_claim_clause():
    """Job predicate, deliberately independent of status and queue_status."""
    return or_(shared_claim_clause(), select(ExecutionTarget.id).where(
        ExecutionTarget.id == Job.execution_target_id,
        ExecutionTarget.leased_job_id == Job.id,
    ).correlate(Job).exists())


def target_idle_clause():
    """ExecutionTarget predicate: neither a legacy lease nor shared Job claims."""
    return and_(ExecutionTarget.leased_job_id.is_(None), ~select(Job.id).where(
        Job.execution_target_id == ExecutionTarget.id, shared_claim_clause(),
    ).correlate(ExecutionTarget).exists())


async def has_target_claims(session, target_id):
    return (await session.execute(select(ExecutionTarget.id).where(
        ExecutionTarget.id == str(target_id), ~target_idle_clause(),
    ))).first() is not None


def shared_assignment(job):
    value = (getattr(job, "provenance", None) or {}).get("remote_execution_assignment") or {}
    return value if value.get("policy") == POLICY else {}


def job_has_claim(target, job):
    if target is None or job is None or str(target.id) != str(job.execution_target_id):
        return False
    assignment = shared_assignment(job)
    if assignment:
        return bool(assignment.get("lease_id") and not assignment.get("released_at")
                    and assignment.get("execution_target_id") == str(target.id)
                    and assignment.get("root_job_id") == str(job.id))
    return target.leased_job_id == str(job.id)


def target_claim_authority(job):
    """Exact ExecutionTarget predicate for a captured Job claim.

    Shared authority binds its persisted per-Job lease id, not a sibling's
    target pointer. Legacy authority retains the original receipt epoch.
    Callers still CAS their complete Job/attempt snapshot.
    """
    assignment = shared_assignment(job)
    if assignment:
        return and_(ExecutionTarget.id == job.execution_target_id,
            select(Job.id).where(Job.id == str(job.id),
                Job.execution_target_id == job.execution_target_id,
                shared_claim_clause(),
                Job.provenance["remote_execution_assignment"]["lease_id"].as_string()
                    == assignment.get("lease_id"),
            ).correlate(ExecutionTarget).exists())
    from datetime import datetime
    receipt = (getattr(job, "provenance", None) or {}).get("remote_execution_receipt") or {}
    raw = receipt.get("lease_acquired_at")
    predicates = [ExecutionTarget.id == job.execution_target_id,
                  ExecutionTarget.leased_job_id == str(job.id)]
    if raw is not None:
        predicates.append(ExecutionTarget.lease_acquired_at == datetime.fromisoformat(str(raw)))
    return and_(*predicates)


def job_claim_authority(job):
    """Exact claim authority as a Job UPDATE predicate."""
    # Explicit correlation keeps the inner Job snapshot independent of UPDATE Job.
    return select(ExecutionTarget.id).where(target_claim_authority(job)).correlate(None).exists()
