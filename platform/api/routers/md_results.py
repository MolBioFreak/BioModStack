from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, BinaryIO, Iterator

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job, get_session
from routers.files import _guess_media_type, _parse_byte_range
from schemas import JobCreate
from scripts.bms_md.aggregate_children import publish_json_immutable
from scripts.bms_md.spawn_analysis import QUALIFIED_RUNTIME_SHA256
from services.md.lifecycle import reconcile_md_analysis_parent
from services.md.results import MDResultError, analysis_report, artifact_inventory, open_verified_artifact, result_record, summary

from services.remote_execution.executor import _joined_thread, _join_mutation

router = APIRouter()
MD_AUTHORIZATION_SCOPE = "job-bound/no-authenticated-principal"


async def _job(job_id: str, session: AsyncSession) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _current_dynamics_generation(job: Job) -> tuple[Path, dict[str, Any], list[tuple[int, str, Path]], str, str]:
    parent_root = Path(job.child_output_dir or job.output_dir or "").expanduser().resolve()
    aggregate_path = parent_root / "manifest.json"
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    replica_indices = sorted(int(item["replica_index"]) for item in aggregate.get("replicas") or [])
    manifest_records: list[tuple[int, str, Path]] = []
    for replica in replica_indices:
        manifest_path = parent_root / "replicas" / f"replica_{replica}" / "manifest.json"
        json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_records.append((replica, hashlib.sha256(manifest_path.read_bytes()).hexdigest(), manifest_path))
    aggregate_sha256 = hashlib.sha256(aggregate_path.read_bytes()).hexdigest()
    manifest_set_sha256 = hashlib.sha256(
        json.dumps([(replica, digest) for replica, digest, _manifest in manifest_records], separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return parent_root, aggregate, manifest_records, aggregate_sha256, manifest_set_sha256


def _generation_matches_accepted(job: Job) -> bool:
    md = (job.provenance or {}).get("md") if isinstance(job.provenance, dict) else None
    if not isinstance(md, dict):
        return False
    try:
        _root, _aggregate, _records, aggregate_sha256, manifest_set_sha256 = _current_dynamics_generation(job)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    return (
        md.get("aggregate_manifest_sha256") == aggregate_sha256
        and md.get("replica_manifest_set_sha256") == manifest_set_sha256
    )


def _raise(error: MDResultError) -> None:
    raise HTTPException(status_code=error.status_code, detail={"code": error.code, "message": str(error)})


@router.get("/{job_id}/md/summary", description=f"Authorization scope: {MD_AUTHORIZATION_SCOPE}")
async def get_md_summary(job_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    try:
        return await _joined_thread(summary, result_record(await _job(job_id, session)))
    except MDResultError as exc:
        _raise(exc)


@router.get("/{job_id}/md/artifacts", description=f"Authorization scope: {MD_AUTHORIZATION_SCOPE}")
async def get_md_artifacts(job_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    try:
        return await _joined_thread(artifact_inventory, result_record(await _job(job_id, session)))
    except MDResultError as exc:
        _raise(exc)


@router.get("/{job_id}/md/analysis", description=f"Authorization scope: {MD_AUTHORIZATION_SCOPE}")
async def get_md_analysis(job_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    try:
        job = await _job(job_id, session)
        report = await _joined_thread(analysis_report, result_record(job))
        children = list(
            (
                await session.execute(
                    select(Job).where(
                        Job.parent_job_id == job_id,
                        Job.model_id == "molecular_dynamics",
                        Job.mode == "analyze",
                        Job.child_stage == "md_analysis",
                    )
                )
            ).scalars()
        )
        latest: dict[int, Job] = {}
        for child in children:
            params = child.params if isinstance(child.params, dict) else {}
            replica = params.get("md_replica_index")
            if type(replica) is not int:
                continue
            current = latest.get(replica)
            if current is None or (child.created_at, str(child.id)) > (current.created_at, str(current.id)):
                latest[replica] = child
        active = any(str(child.status or child.queue_status).lower() in {"queued", "running", "pending"} for child in children)
        raw_states = report.get("replica_states")
        states: list[Any] = raw_states if isinstance(raw_states, list) else []
        failed_or_missing = any(
            isinstance(state, dict) and state.get("status") in {"failed", "absent"}
            for state in states
        )
        md = (job.provenance or {}).get("md") if isinstance(job.provenance, dict) else None
        accepted_set = md.get("replica_manifest_set_sha256") if isinstance(md, dict) else None
        lifecycle_retrying = isinstance(md, dict) and md.get("analysis_state") == "retrying" and job.status != "failed"
        generation_matches = await _joined_thread(_generation_matches_accepted, result_record(job))
        eligible = bool(
            report.get("status") != "completed"
            and failed_or_missing
            and not active
            and not lifecycle_retrying
            and isinstance(accepted_set, str)
            and generation_matches
        )
        report["retry"] = {
            "eligible": eligible,
            "active": active or lifecycle_retrying,
            "reason": (
                "failed_or_missing_analysis" if eligible
                else "dynamics_generation_changed" if failed_or_missing and not generation_matches
                else "analysis_not_retryable"
            ),
        }
        return report
    except MDResultError as exc:
        _raise(exc)


def _retained_analysis_roster(provenance: dict, root_job_id: str, target_id: str,
                              parent_root: Path) -> dict[int, dict]:
    """Resolve exact current requests, including retained but unprojected lanes."""
    context_path = provenance.get("component_context_path")
    if context_path:
        from scripts.lib.component_adapter import runtime_from_environment
        runtime = runtime_from_environment(Path(context_path))
        if runtime.root_job_id != root_job_id or runtime.target_id != target_id:
            raise ValueError("Retained analysis roster root/target conflicts")
        rows = runtime.children(root_job_id, "md_analysis")
    else:
        # Remote terminal return retains the real ledger snapshot even for lanes
        # the host importer did not project. Use its issued attempt identities.
        path = parent_root / ".bms-components.json"
        if not path.exists():
            return {}
        if path.is_symlink() or not path.is_file():
            raise ValueError("Retained analysis roster must be a regular publication")
        envelope = json.loads(path.read_bytes())
        receipt = provenance.get("remote_execution_receipt") or {}
        expected = receipt.get("component_context_identity") or provenance.get("assignment_context") or {}
        for key in ("root_job_id", "attempt_id", "target_id", "lease_id", "source_identity", "plan_sha256"):
            if envelope.get(key) != expected.get(key) or key not in expected:
                raise ValueError("Retained analysis roster attempt identity conflicts")
        if (envelope.get("schema_name") != "bms.component-projection.v1"
                or envelope.get("generation") != receipt.get("generation", 0)
                or envelope.get("current_plan_sha256") != receipt.get("plan_sha256", expected["plan_sha256"])):
            raise ValueError("Retained analysis roster generation conflicts")
        from component_runtime import ComponentRequest
        replaced = {row["original"] for row in envelope.get("replacements", [])}
        rows = []
        for row in [*envelope.get("components", []), *envelope.get("unprojected_components", [])]:
            request = ComponentRequest.capture(**row["request"])
            if request.component_id != row["component_id"]:
                raise ValueError("Retained analysis component request identity conflicts")
            if request.component_id not in replaced and request.stage == "md_analysis":
                rows.append(dict(id=request.component_id, status=row["state"],
                                 required=request.required, params=request.payload["params"]))
    roster = {}
    for row in rows:
        if not row["required"]:
            continue
        index = row["params"]["md_replica_index"]
        if type(index) is not int or index < 0 or index in roster:
            raise ValueError("Retained analysis roster replica identity conflicts")
        roster[index] = row
    return roster


@router.post("/{job_id}/md/analysis/retry", description=f"Authorization scope: {MD_AUTHORIZATION_SCOPE}")
async def retry_md_analysis(job_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    parent = (
        await session.execute(select(Job).where(Job.id == job_id).with_for_update())
    ).scalar_one_or_none()
    if parent is None:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        await _joined_thread(summary, result_record(parent))
    except MDResultError as exc:
        _raise(exc)
    if parent.model_id != "molecular_dynamics" or parent.mode != "simulate" or parent.parent_job_id is not None:
        raise HTTPException(status_code=409, detail={"code": "MD_PARENT_REQUIRED", "message": "MD parent job is required"})
    await session.refresh(parent)
    if parent.status == "cancelled" or parent.awaiting_input:
        raise HTTPException(status_code=409, detail={"code": "MD_ANALYSIS_RETRY_NOT_ELIGIBLE",
            "message": "MD cancellation or review owns the parent"})
    current_md = (parent.provenance or {}).get("md") if isinstance(parent.provenance, dict) else None
    retained_intent = (parent.provenance or {}).get("component_retry") or {}
    recovering = (retained_intent.get("actor") == "md-analysis-retry:" + job_id
                  and retained_intent.get("state") in {"pending", "requested", "uncertain"})
    if (isinstance(current_md, dict) and current_md.get("analysis_state") == "retrying"
            and parent.status != "failed" and not recovering):
        raise HTTPException(
            status_code=409,
            detail={"code": "MD_ANALYSIS_RETRY_ACTIVE", "message": "An MD analysis retry is already active"},
        )

    children = list(
        (
            await session.execute(
                select(Job).where(
                    Job.parent_job_id == job_id,
                    Job.model_id == "molecular_dynamics",
                    Job.mode == "analyze",
                    Job.child_stage == "md_analysis",
                )
            )
        ).scalars()
    )
    latest: dict[int, Job] = {}
    for child in children:
        params = child.params if isinstance(child.params, dict) else {}
        replica = params.get("md_replica_index")
        if type(replica) is not int or replica < 0:
            continue
        current = latest.get(replica)
        if current is None or (child.created_at, str(child.id)) > (current.created_at, str(current.id)):
            latest[replica] = child
    active = [child for child in children if str(child.status or child.queue_status).lower() in {"queued", "running", "pending"}]
    if active and not recovering:
        raise HTTPException(
            status_code=409,
            detail={"code": "MD_ANALYSIS_RETRY_ACTIVE", "message": "An MD analysis retry is already active"},
        )

    try:
        parent_root, aggregate, manifest_records, current_aggregate_sha256, manifest_set_sha256 = await _joined_thread(
            _current_dynamics_generation, result_record(parent))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "MD_DYNAMICS_GENERATION_INVALID", "message": "Accepted dynamics generation is unavailable or invalid"},
        ) from exc
    replica_indices = [replica for replica, _digest, _manifest in manifest_records]
    retry_indices = [
        replica
        for replica in replica_indices
        if replica not in latest or str(latest[replica].status or latest[replica].queue_status).lower() != "completed"
    ]
    if not retry_indices and not (parent.execution_target_id or (parent.provenance or {}).get("component_context_path")):
        raise HTTPException(
            status_code=409,
            detail={"code": "MD_ANALYSIS_RETRY_NOT_ELIGIBLE", "message": "No failed or missing MD analysis lane is retryable"},
        )

    accepted_sets = {
        value
        for child in children
        if isinstance(child.params, dict)
        for value in [child.params.get("md_replica_manifest_set_sha256")]
        if isinstance(value, str)
    }
    if isinstance(current_md, dict) and isinstance(current_md.get("replica_manifest_set_sha256"), str):
        accepted_sets.add(current_md["replica_manifest_set_sha256"])
    if (
        not isinstance(current_md, dict)
        or current_md.get("aggregate_manifest_sha256") != current_aggregate_sha256
        or current_md.get("replica_manifest_set_sha256") != manifest_set_sha256
        or len(accepted_sets) != 1
        or manifest_set_sha256 not in accepted_sets
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "MD_DYNAMICS_GENERATION_CHANGED", "message": "Analysis retry must reuse the accepted immutable dynamics generation"},
        )

    approved = None
    if parent.execution_target_id:
        approved = (parent.provenance or {}).get('execution_plan_approval') or {}
        plan = approved.get('plan') or {}
        templates = (plan.get('metadata') or {}).get('dynamic_templates') or []
        if (not approved.get('approval_digest') or plan.get('complete') is not True
                or not any((row.get('expansion_json') or {}).get('child_model') == 'molecular_dynamics'
                    and (row.get('expansion_json') or {}).get('child_mode') == 'analyze'
                    and (row.get('expansion_json') or {}).get('child_stage') == 'md_analysis'
                    for row in templates)):
            raise HTTPException(status_code=409, detail='Remote MD analysis retry requires its retained approved analysis expansion')

    shared_context = (parent.provenance or {}).get("component_context_path")
    if approved is not None or shared_context:
        from services.remote_execution.executor import retry_component_execution
        # The retained shared owner persists its own exact-operation intent and
        # reacquires the original target. Never submit a scheduler-visible leaf.
        try:
            roster = (await _joined_thread(_retained_analysis_roster, dict(parent.provenance or {}),
                str(parent.id), parent.execution_target_id or "local", parent_root) if not recovering else {})
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise HTTPException(status_code=409, detail={"code": "MD_ANALYSIS_RETRY_ACTUATION_UNCERTAIN",
                "message": "Retained runtime analysis roster requires same-attempt recovery"}) from exc
        # The existing authority replaces one exact failed component per operation.
        # A later explicit operator request addresses the next failed lane; never
        # launch a background roster retry or call one replacement roster success.
        if roster:
            retry_indices = sorted(index for index, row in roster.items()
                                   if row["status"] != "completed")
            if any(row["status"] in {"queued", "running", "uncertain"} for row in roster.values()):
                raise HTTPException(status_code=409, detail={"code": "MD_ANALYSIS_RETRY_ACTIVE",
                    "message": "Retained analysis components are still active"})
        if recovering and not retry_indices:
            retry_indices = replica_indices
        if not retry_indices:
            raise HTTPException(status_code=409, detail={"code": "MD_ANALYSIS_RETRY_NOT_ELIGIBLE",
                "message": "No failed or missing MD analysis lane is retryable"})
        selected_index = retry_indices[0]
        source = latest.get(selected_index)
        component_id = (roster[selected_index]["id"] if selected_index in roster else
                        str((source.provenance or {}).get("component_id") or source.id) if source else None)
        if recovering:
            component_id = retained_intent["component_id"]
        if component_id is None:
            raise HTTPException(status_code=409, detail={"code": "MD_ANALYSIS_RETRY_ACTUATION_UNCERTAIN",
                "message": "Retained runtime analysis roster is unavailable; no component identity was invented"})
        operation_id = "md-analysis:" + hashlib.sha256(json.dumps(
            [job_id, parent.remote_attempt_id, manifest_set_sha256, selected_index, component_id],
            separators=(",", ":"),
        ).encode()).hexdigest()
        if recovering:
            operation_id = retained_intent["operation_id"]
        try:
            receipt = await retry_component_execution(session, parent,
                component_id=component_id, operation_id=operation_id,
                actor="md-analysis-retry:" + job_id, failure_code="execution_failed")
        except Exception as exc:
            # The shared outbox remains the uncertainty owner. No replacement
            # Jobs, fallback leaf admission or automatic replay is introduced.
            raise HTTPException(status_code=409, detail={
                "code": "MD_ANALYSIS_RETRY_ACTUATION_UNCERTAIN",
                "message": "Retained shared analysis retry requires explicit same-operation recovery",
            }) from exc
        await session.refresh(parent)
        if parent.status == "cancelled" or parent.awaiting_input:
            return {"schema": "bms.md.analysis-retry.v1", "status": "cancelled",
                "created_child_ids": []}
        provenance = dict(parent.provenance or {})
        md = dict(provenance.get("md") or {})
        md.update(schema="bms.md.lifecycle.v1", dynamics_state="completed",
            analysis_state="retrying", result_state="partial",
            aggregate_manifest_sha256=current_aggregate_sha256,
            replica_manifest_set_sha256=manifest_set_sha256)
        provenance["md"] = md
        from services.remote_execution.executor import _publish_remote_transition
        if not await _publish_remote_transition(session, parent, {"provenance": provenance}, require_lease=False):
            await session.refresh(parent)
            return {"schema": "bms.md.analysis-retry.v1", "status": "cancelled" if parent.status == "cancelled" else "ownership_changed",
                "created_child_ids": []}
        child_id = receipt.get("child_job_id") or receipt.get("component_id")
        selected_index = ((receipt.get("replacement") or {}).get("payload") or {}).get("params", {}).get("md_replica_index", selected_index)
        return {"schema": "bms.md.analysis-retry.v1", "status": "scheduled",
            "created_child_ids": [str(child_id)] if child_id else [],
            "scheduled_replica_indices": [selected_index],
            "remaining_replica_indices": [index for index in retry_indices if index != selected_index]}

    provenance = dict(parent.provenance or {})
    md = dict(provenance.get("md") or {})
    md.update(
        {
            "schema": "bms.md.lifecycle.v1",
            "dynamics_state": "completed",
            "analysis_state": "retrying",
            "result_state": "partial",
            "aggregate_manifest_sha256": current_aggregate_sha256,
            "replica_manifest_set_sha256": manifest_set_sha256,
        }
    )
    provenance["md"] = md
    parent.provenance = provenance
    parent.status = "running"
    parent.queue_status = "running"
    parent.completed_at = None
    parent.error_message = None
    await session.commit()

    from routers.jobs import create_job

    created_ids: list[str] = []
    work_item_dir = parent_root / "orchestration" / "analysis_retry_work_items"
    try:
        for replica, digest, manifest in manifest_records:
            if replica not in retry_indices:
                continue
            work_item = {
                "schema": "bms.md.analysis-work-item.v1",
                "job_id": job_id,
                "replica_index": replica,
                "manifest": str(manifest),
                "manifest_sha256": digest,
                "replica_manifest_set_sha256": manifest_set_sha256,
            }
            work_item_path = work_item_dir / f"replica_{replica}.json"
            await _joined_thread(publish_json_immutable, work_item, work_item_path)
            child_request = JobCreate(
                    name=f"{parent.name} - MD analysis retry replica {replica}",
                    model_id="molecular_dynamics",
                    mode="analyze",
                    params={
                        "md_analysis_work_item": str(work_item_path),
                        "md_analysis_sif_sha256": QUALIFIED_RUNTIME_SHA256,
                        "md_replica_index": replica,
                        "md_replica_manifest_sha256": digest,
                        "md_replica_manifest_set_sha256": manifest_set_sha256,
                        "lineage_root_job_id": job_id,
                    },
                    parent_job_id=job_id,
                    batch_id=job_id,
                    batch_name=parent.name,
                    child_stage="md_analysis",
                    pinned_gpu=None,
                    sequence_length=None,
                )
            response = await create_job(child_request, BackgroundTasks(), session)
            created_ids.append(str(response.id))
    except Exception as exc:
        await session.rollback()
        from services.job_control import cancel_job_lineage

        for created_id in created_ids:
            try:
                await cancel_job_lineage(
                    created_id,
                    session,
                    error_message="Cancelled after incomplete MD analysis retry admission",
                )
            except Exception:
                await session.rollback()
                partial_child = await session.get(Job, created_id)
                if partial_child is not None:
                    partial_child.status = "cancelled"
                    partial_child.queue_status = "cancelled"
                    partial_child.error_message = "MD_ANALYSIS_RETRY_ADMISSION_ROLLED_BACK"
                    await session.commit()
        failed_parent = await _job(job_id, session)
        failed_provenance = dict(failed_parent.provenance or {})
        failed_md = dict(failed_provenance.get("md") or {})
        failed_md.update(
            {
                "schema": "bms.md.lifecycle.v1",
                "dynamics_state": "completed",
                "analysis_state": "failed",
                "result_state": "partial",
                "aggregate_manifest_sha256": current_aggregate_sha256,
                "replica_manifest_set_sha256": manifest_set_sha256,
                "analysis_child_ids": created_ids,
            }
        )
        failed_provenance["md"] = failed_md
        failed_parent.provenance = failed_provenance
        failed_parent.status = "failed"
        failed_parent.queue_status = "failed"
        failed_parent.current_stage = "MD Analysis Retry Scheduling Failed"
        failed_parent.error_message = "MD_ANALYSIS_RETRY_SCHEDULING_FAILED"
        await session.commit()
        raise HTTPException(
            status_code=500,
            detail={
                "code": "MD_ANALYSIS_RETRY_SCHEDULING_FAILED",
                "message": "MD analysis retry scheduling failed; any partially created children were cancelled and remain recorded for reconciliation",
            },
        ) from exc
    return {"schema": "bms.md.analysis-retry.v1", "status": "scheduled", "created_child_ids": created_ids}


def _iter_verified_handle(handle: BinaryIO, start: int, end: int) -> Iterator[bytes]:
    remaining = end - start + 1
    try:
        handle.seek(start)
        while remaining:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        handle.close()


def _stream_verified_artifact(handle: BinaryIO, *, name: str, size: int, request: Request) -> Response:
    range_header = request.headers.get("range")
    status_code = 200
    start, end = 0, size - 1
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    if range_header:
        try:
            start, end = _parse_byte_range(range_header, size)
        except ValueError:
            handle.close()
            return Response(
                status_code=416,
                headers={**headers, "Content-Range": f"bytes */{size}", "Content-Length": "0"},
            )
        status_code = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(end - start + 1)
    return StreamingResponse(
        _iter_verified_handle(handle, start, end),
        status_code=status_code,
        headers=headers,
        media_type=_guess_media_type(Path(name)),
    )


@router.get("/{job_id}/md/artifacts/{artifact_id}/content", description=f"Authorization scope: {MD_AUTHORIZATION_SCOPE}")
async def get_md_artifact_content(job_id: str, artifact_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    try:
        record = result_record(await _job(job_id, session))
        task = asyncio.create_task(asyncio.to_thread(open_verified_artifact, record, artifact_id))
        try:
            artifact, handle = await asyncio.shield(task)
        except asyncio.CancelledError:
            await _join_mutation(task)
            if not task.cancelled() and task.exception() is None:
                task.result()[1].close()
            raise
        return _stream_verified_artifact(handle, name=artifact.name, size=artifact.bytes, request=request)
    except MDResultError as exc:
        _raise(exc)
