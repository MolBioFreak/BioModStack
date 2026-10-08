"""Fenced single-child lease lending at the terminal FrustraMPNN boundary.

The target always names the *one* GPU owner. A lent parent retains observation /
terminal-publication authority, not launch authority. No schema or local fallback.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import and_, case, or_, select, update
from sqlalchemy.orm import aliased

from database import ExecutionTarget, Job
from services.structure_dataset_fanout import (
    FANOUT_CAPABILITY_CONSUMED_KEY, FANOUT_PROVENANCE_KEY, FANOUT_SCHEMA, _child_id,
)

HANDOFF_KEY = "frustrampnn_remote_handoff_v1"
TERMINAL = ("completed", "failed", "cancelled")
TRIGGER = "parent_workflow_terminal_dataset"
WORKFLOWS = {"structure_prediction", "complex_prediction", "protein_design",
             "antibody_denovo", "conformational_mapping"}


def authorized_parent(child, parent) -> bool:
    """Validate both durable sides; a bare parent_job_id never grants admission."""
    try:
        envelope = child.params["_frustrampnn_child_v1"]
        lineage = child.provenance[FANOUT_PROVENANCE_KEY]
        fanout_id = lineage["fanout_id"]
        contract = parent.provenance[FANOUT_PROVENANCE_KEY][fanout_id]
        plan = contract["plan"]
        ordinal = lineage["batch_ordinal"]
        workflow = plan["workflow_id"].removesuffix(".frustrampnn.v1")
        digest = parent.provenance[FANOUT_CAPABILITY_CONSUMED_KEY]
        members = plan["members"]
        size = plan["effective_structures_per_job"]
        if type(size) is not int or size < 1 or type(ordinal) is not int or ordinal < 0:
            return False
        batches = [members[start:start + size] for start in range(0, len(members), size)]
        expected_ids = [_child_id(fanout_id, index) for index in range(len(batches))]
        batch = batches[ordinal]
        expected_lineage = {
            "schema_name": FANOUT_SCHEMA, "schema_version": 1,
            "fanout_id": fanout_id, "parent_job_id": str(parent.id),
            "batch_ordinal": ordinal,
            "structure_ids": [member["structure_id"] for member in batch],
            "member_lineage": [member["lineage"] for member in batch],
        }
        return bool(
            child.model_id == "frustrampnn" and child.child_stage == "frustrampnn"
            and child.mode == "analyze"
            and child.parent_job_id == str(parent.id)
            and envelope["schema_name"] == "bms.frustrampnn.scheduler-child.v1"
            and envelope["execution_owner_job_id"] == str(child.id)
            and envelope["source_parent_job_id"] == str(parent.id)
            and envelope["trigger"] == TRIGGER
            and plan["request_identity"]["trigger"] == TRIGGER
            and workflow in WORKFLOWS and plan["workflow_id"] == f"{workflow}.frustrampnn.v1"
            and plan["parent_job_id"] == str(parent.id)
            and plan["schema_name"] == contract["schema_name"] == FANOUT_SCHEMA
            and hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":"),
                                          ensure_ascii=False, allow_nan=False).encode()).hexdigest() == fanout_id
            and contract["child_job_ids"] == expected_ids
            and expected_ids[ordinal] == str(child.id) and lineage == expected_lineage
            and len(digest) == 64 and digest == parent.provenance["workflow_stage_report_token_sha256"]
            and parent.status == parent.queue_status == "running" and not parent.paused
            and parent.remote_attempt_id and parent.nextflow_run_id == f"remote:{parent.remote_attempt_id}"
            and parent.execution_target_id and child.execution_target_id == parent.execution_target_id
            and parent.execution_source_revision and child.execution_source_revision == parent.execution_source_revision
            and parent.execution_source_tree and child.execution_source_tree == parent.execution_source_tree
        )
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        return False


async def lending_parent(session, child):
    parent = await session.get(Job, str(child.parent_job_id)) if child.parent_job_id else None
    return parent if parent is not None and authorized_parent(child, parent) else None


def parent_snapshot(parent):
    """SQL CAS of every authority field read during admission."""
    owner = aliased(Job)
    return select(owner.id).where(
        owner.id == str(parent.id), owner.status == "running", owner.queue_status == "running",
        owner.paused.is_(False), owner.provenance == parent.provenance,
        owner.params == parent.params, owner.remote_attempt_id == parent.remote_attempt_id,
        owner.nextflow_run_id == parent.nextflow_run_id,
        owner.remote_state == parent.remote_state,
        owner.execution_target_id == parent.execution_target_id,
        owner.execution_source_revision == parent.execution_source_revision,
        owner.execution_source_tree == parent.execution_source_tree,
    ).exists()


def handoff_receipt(parent):
    return {"parent_job_id": str(parent.id), "parent_attempt_id": parent.remote_attempt_id,
            "parent_run_id": parent.nextflow_run_id}


def lease_authority(job):
    """Exact child lease, or the running parent's own scheduler-fenced loan.

    Only a parent already running may publish through its child. In particular
    this cannot authorize staging/resuming a parent while a child owns the GPU.
    """
    child = aliased(Job)
    loan = child.provenance["remote_execution_assignment"][HANDOFF_KEY]
    borrowed = select(child.id).where(
        child.id == ExecutionTarget.leased_job_id,
        child.parent_job_id == str(job.id), child.execution_target_id == job.execution_target_id,
        child.model_id == "frustrampnn", child.child_stage == "frustrampnn",
        loan["parent_job_id"].as_string() == str(job.id),
        loan["parent_attempt_id"].as_string() == job.remote_attempt_id,
        loan["parent_run_id"].as_string() == job.nextflow_run_id,
        child.execution_source_revision == job.execution_source_revision,
        child.execution_source_tree == job.execution_source_tree,
    ).correlate(ExecutionTarget).exists()
    return select(ExecutionTarget.id).where(
        ExecutionTarget.id == job.execution_target_id,
        or_(ExecutionTarget.leased_job_id == str(job.id),
            and_(job.status == "running", job.queue_status in ("running", "cancelling"), borrowed)),
    ).exists()


def child_launch_parent_authority(job):
    """A claimed loan is not permission to start after parent cancellation."""
    receipt = (job.provenance or {}).get("remote_execution_assignment", {}).get(HANDOFF_KEY, {})
    if not receipt:
        return True
    parent = aliased(Job)
    return select(parent.id).where(
        parent.id == receipt.get("parent_job_id"), parent.id == job.parent_job_id,
        parent.status == "running", parent.queue_status == "running", parent.paused.is_(False),
        parent.execution_target_id == job.execution_target_id,
        parent.remote_attempt_id == receipt.get("parent_attempt_id"),
        parent.nextflow_run_id == receipt.get("parent_run_id"),
        parent.execution_source_revision == job.execution_source_revision,
        parent.execution_source_tree == job.execution_source_tree,
    ).exists()


async def release_lease(session, job):
    """Return a child's lease to its live root, otherwise release exact ownership.

    Parent terminalization while a child runs cannot clear the child's lease.
    Return even to a cancelling parent: its remote process is not yet proven dead.
    """
    if not job.execution_target_id:
        return
    receipt = (job.provenance or {}).get("remote_execution_assignment", {}).get(HANDOFF_KEY, {})
    parent_id = receipt.get("parent_job_id")
    parent = aliased(Job)
    live_parent = select(parent.id).where(
        parent.id == parent_id, parent.id == job.parent_job_id,
        parent.execution_target_id == job.execution_target_id,
        parent.remote_attempt_id == receipt.get("parent_attempt_id"),
        parent.nextflow_run_id == receipt.get("parent_run_id"),
        ~and_(parent.status.in_(TERMINAL), parent.queue_status.in_(TERMINAL)),
    ).exists()
    terminal_owner = select(Job.id).where(
        Job.id == str(job.id), Job.execution_target_id == job.execution_target_id,
        Job.status.in_(TERMINAL), Job.queue_status.in_(TERMINAL),
        Job.remote_attempt_id == job.remote_attempt_id,
        Job.nextflow_run_id == job.nextflow_run_id,
    ).exists()
    await session.execute(update(ExecutionTarget).where(
        ExecutionTarget.id == job.execution_target_id,
        ExecutionTarget.leased_job_id == str(job.id), terminal_owner,
    ).values(
        leased_job_id=case((live_parent, parent_id), else_=None) if parent_id else None,
        lease_acquired_at=case((live_parent, ExecutionTarget.lease_acquired_at), else_=None) if parent_id else None,
        updated_at=datetime.utcnow(),
    ).execution_options(synchronize_session=False))


async def recover_terminal_leases(session):
    """Use the same handback rules after a crash between terminal commit/cleanup."""
    owners = (await session.execute(select(Job).join(
        ExecutionTarget, and_(ExecutionTarget.leased_job_id == Job.id,
                              ExecutionTarget.id == Job.execution_target_id),
    ).where(Job.status.in_(TERMINAL), Job.queue_status.in_(TERMINAL)))).scalars().all()
    for owner in owners:
        await release_lease(session, owner)
