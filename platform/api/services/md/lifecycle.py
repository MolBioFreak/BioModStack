from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job
from scripts.bms_md.aggregate_children import publish_json_immutable
from scripts.bms_md.collect_analysis import collect_analysis
from services.md.results import MDResultError, apply_completion_barrier


TERMINAL_SUCCESS = {"completed"}
TERMINAL_FAILURE = {"failed", "cancelled"}
ACTIVE = {"queued", "running", "pending"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _status_token(job: Job) -> str:
    return str(job.status or job.queue_status or "").strip().lower()


async def reconcile_md_analysis_parent(parent_job_id: str, session: AsyncSession) -> dict[str, Any]:
    parent = (await session.execute(select(Job).where(Job.id == parent_job_id))).scalar_one_or_none()
    if parent is None or parent.model_id != "molecular_dynamics" or parent.mode != "simulate":
        raise MDResultError("MD_PARENT_INVALID", "MD analysis parent is unavailable", 404)

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

    parent_root = Path(parent.child_output_dir or parent.output_dir or "").expanduser().resolve()
    aggregate_path = parent_root / "manifest.json"
    if not aggregate_path.is_file():
        raise MDResultError("MD_RESULTS_ABSENT", "MD aggregate manifest is unavailable", 404)
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
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
    orchestration = parent_root / "orchestration" / "analysis_reconciliation"
    orchestration.mkdir(parents=True, exist_ok=True)
    status_path = orchestration / "latest_child_outputs.json"
    status_path.write_text(json.dumps(status_payload, sort_keys=True) + "\n", encoding="utf-8")
    collection = collect_analysis(status_path, aggregate_path, parent_root)

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
            "analysis_manifest_sha256": _sha256(parent_root / "analysis" / "manifest.json")
            if collection["status"] == "completed"
            else None,
        }
    )
    provenance["md"] = md
    parent.provenance = provenance

    if collection["status"] != "completed":
        parent.status = "failed"
        parent.queue_status = "failed"
        parent.current_stage = "MD Analysis Failed"
        parent.error_message = "MD_ANALYSIS_INCOMPLETE"
        return {"status": "partial_failure", "collection": collection}

    analysis_path = parent_root / "analysis" / "manifest.json"
    barrier = {
        "schema": "bms.md.completion-barrier.v1",
        "status": "completed",
        "job_id": str(parent.id),
        "aggregate_manifest_sha256": _sha256(aggregate_path),
        "analysis_manifest_sha256": _sha256(analysis_path),
    }
    publish_json_immutable(barrier, parent_root / "md_completion_barrier.json")
    snapshot = apply_completion_barrier(parent)
    return {"status": "completed", "lifecycle": snapshot}
