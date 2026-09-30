from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job
from scripts.bms_md.aggregate_children import publish_json_immutable
from scripts.bms_md.collect_analysis import collect_analysis
from services.md.results import MDResultError
from services.md.completion import validate_and_finalize_md_job
from services.remote_execution.executor import _joined_thread


TERMINAL_SUCCESS = {"completed"}
TERMINAL_FAILURE = {"failed", "cancelled"}
ACTIVE = {"queued", "running", "pending"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _status_token(job: Job) -> str:
    return str(job.status or job.queue_status or "").strip().lower()


def _read_aggregate(output_dir):
    root = Path(output_dir).expanduser().resolve()
    path = root / "manifest.json"
    if not path.is_file():
        raise MDResultError("MD_RESULTS_ABSENT", "MD aggregate manifest is unavailable", 404)
    return root, json.loads(path.read_text(encoding="utf-8"))


def _collect(root, aggregate_path, status_payload):
    orchestration = root / "orchestration" / "analysis_reconciliation"
    orchestration.mkdir(parents=True, exist_ok=True)
    status_path = orchestration / "latest_child_outputs.json"
    status_path.write_text(json.dumps(status_payload, sort_keys=True) + "\n", encoding="utf-8")
    return collect_analysis(status_path, aggregate_path, root)


def _publish_barrier(root, job_id):
    barrier = {
        "schema": "bms.md.completion-barrier.v1", "status": "completed", "job_id": job_id,
        "aggregate_manifest_sha256": _sha256(root / "manifest.json"),
        "analysis_manifest_sha256": _sha256(root / "analysis" / "manifest.json"),
    }
    publish_json_immutable(barrier, root / "md_completion_barrier.json")


async def reconcile_md_analysis_parent(parent_job_id: str, session: AsyncSession) -> dict[str, Any]:
    parent = (await session.execute(select(Job).where(Job.id == parent_job_id))).scalar_one_or_none()
    if parent is None or parent.model_id != "molecular_dynamics" or parent.mode != "simulate":
        raise MDResultError("MD_PARENT_INVALID", "MD analysis parent is unavailable", 404)

    await session.refresh(parent)
    if parent.status == "cancelled" or parent.awaiting_input or parent.paused:
        return {"status": "cancelled" if parent.status == "cancelled" else "waiting"}
    status, queue_status = parent.status, parent.queue_status

    children = list(
        (
            await session.execute(
                select(Job).where(
                    Job.parent_job_id == parent_job_id,
                    Job.model_id == "molecular_dynamics",
                    Job.mode == "analyze",
                    Job.child_stage == "md_analysis",
                )
            )
        ).scalars()
    )
    active_children = [child for child in children if _status_token(child) in ACTIVE]
    if active_children:
        return {"status": "waiting", "child_ids": [str(child.id) for child in active_children]}

    latest_by_replica: dict[int, Job] = {}
    for child in children:
        params = child.params if isinstance(child.params, dict) else {}
        replica = params.get("md_replica_index")
        if type(replica) is not int or replica < 0:
            continue
        current = latest_by_replica.get(replica)
        if current is None or (child.created_at, str(child.id)) > (current.created_at, str(current.id)):
            latest_by_replica[replica] = child

    parent_root, aggregate = await _joined_thread(
        _read_aggregate, parent.child_output_dir or parent.output_dir or "")
    aggregate_path = parent_root / "manifest.json"
    expected = sorted(int(item["replica_index"]) for item in aggregate.get("replicas") or [])
    selected = [latest_by_replica[index] for index in expected if index in latest_by_replica]
    active = [child for child in selected if _status_token(child) in ACTIVE]
    if active:
        return {"status": "waiting", "child_ids": [str(child.id) for child in selected]}
    completed = [child for child in selected if _status_token(child) in TERMINAL_SUCCESS]
    failed = [child for child in selected if _status_token(child) in TERMINAL_FAILURE]
    missing = len(expected) - len(selected)

    status_payload = {
        "total": len(expected),
        "completed": len(completed),
        "failed": len(failed) + missing,
        "cancelled": sum(1 for child in failed if _status_token(child) == "cancelled"),
        "child_ids": [str(child.id) for child in selected],
        "child_output_dirs": [str(child.child_output_dir or child.output_dir) for child in completed],
    }
    collection = await _joined_thread(_collect, parent_root, aggregate_path, status_payload)
    await session.refresh(parent)
    if parent.status == "cancelled" or parent.awaiting_input or parent.paused:
        return {"status": "cancelled" if parent.status == "cancelled" else "waiting"}
    if (parent.status, parent.queue_status) != (status, queue_status):
        return {"status": "waiting"}

    provenance = dict(parent.provenance or {})
    md = dict(provenance.get("md") or {})
    current_dynamics = {
        "aggregate_manifest_sha256": collection.get("aggregate_manifest_sha256"),
        "replica_manifest_set_sha256": collection.get("replica_manifest_set_sha256"),
    }
    for key, current_value in current_dynamics.items():
        accepted_value = md.get(key)
        if isinstance(accepted_value, str) and accepted_value != current_value:
            raise MDResultError(
                "MD_DYNAMICS_GENERATION_CHANGED",
                "Analysis collection does not match the accepted immutable dynamics generation",
                409,
            )
    md.update(
        {
            "schema": "bms.md.lifecycle.v1",
            "dynamics_state": "completed",
            "analysis_state": "completed" if collection["status"] == "completed" else "failed",
            "result_state": "completed" if collection["status"] == "completed" else "partial",
            "analysis_child_ids": status_payload["child_ids"],
            "aggregate_manifest_sha256": collection.get("aggregate_manifest_sha256"),
            "replica_manifest_set_sha256": collection.get("replica_manifest_set_sha256"),
            "analysis_manifest_sha256": await _joined_thread(_sha256, parent_root / "analysis" / "manifest.json")
            if collection["status"] == "completed"
            else None,
        }
    )
    if collection["status"] != "completed":
        claimed = await session.execute(update(Job).where(
            Job.id == parent.id, Job.status == status, Job.queue_status == queue_status,
            Job.status != "cancelled", Job.awaiting_input.is_(False), Job.paused.is_(False),
        ).values(status=status).execution_options(synchronize_session=False))
        if claimed.rowcount != 1:
            return {"status": "waiting"}
        provenance["md"] = md
        parent.provenance = provenance
        parent.status = "failed"
        parent.queue_status = "failed"
        parent.current_stage = "MD Analysis Failed"
        parent.error_message = "MD_ANALYSIS_INCOMPLETE"
        return {"status": "partial_failure", "collection": collection}

    await _joined_thread(_publish_barrier, parent_root, str(parent.id))
    snapshot = await validate_and_finalize_md_job(parent, session)
    from services.result_state_integrity import finalize_component_projection
    await finalize_component_projection(parent, session)
    return {"status": "completed", "lifecycle": snapshot}
