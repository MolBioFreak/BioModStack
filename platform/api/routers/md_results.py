from __future__ import annotations

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
from services.md.results import MDResultError, analysis_report, artifact_inventory, open_verified_artifact, summary

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
        return summary(await _job(job_id, session))
    except MDResultError as exc:
        _raise(exc)


@router.get("/{job_id}/md/artifacts", description=f"Authorization scope: {MD_AUTHORIZATION_SCOPE}")
async def get_md_artifacts(job_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    try:
        return artifact_inventory(await _job(job_id, session))
    except MDResultError as exc:
        _raise(exc)


@router.get("/{job_id}/md/analysis", description=f"Authorization scope: {MD_AUTHORIZATION_SCOPE}")
async def get_md_analysis(job_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    try:
        job = await _job(job_id, session)
        report = analysis_report(job)
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
        lifecycle_retrying = isinstance(md, dict) and md.get("analysis_state") == "retrying"
        generation_matches = _generation_matches_accepted(job)
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


@router.post("/{job_id}/md/analysis/retry", description=f"Authorization scope: {MD_AUTHORIZATION_SCOPE}")
async def retry_md_analysis(job_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    parent = (
        await session.execute(select(Job).where(Job.id == job_id).with_for_update())
    ).scalar_one_or_none()
    if parent is None:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        summary(parent)
    except MDResultError as exc:
        _raise(exc)
    if parent.model_id != "molecular_dynamics" or parent.mode != "simulate" or parent.parent_job_id is not None:
        raise HTTPException(status_code=409, detail={"code": "MD_PARENT_REQUIRED", "message": "MD parent job is required"})
    current_md = (parent.provenance or {}).get("md") if isinstance(parent.provenance, dict) else None
    if isinstance(current_md, dict) and current_md.get("analysis_state") == "retrying":
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
    if active:
        raise HTTPException(
            status_code=409,
            detail={"code": "MD_ANALYSIS_RETRY_ACTIVE", "message": "An MD analysis retry is already active"},
        )

    try:
        parent_root, aggregate, manifest_records, current_aggregate_sha256, manifest_set_sha256 = _current_dynamics_generation(parent)
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
    if not retry_indices:
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
        not _generation_matches_accepted(parent)
        or len(accepted_sets) != 1
        or manifest_set_sha256 not in accepted_sets
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "MD_DYNAMICS_GENERATION_CHANGED", "message": "Analysis retry must reuse the accepted immutable dynamics generation"},
        )

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
            publish_json_immutable(work_item, work_item_path)
            response = await create_job(
                JobCreate(
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
                ),
                BackgroundTasks(),
                session,
            )
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
        artifact, handle = open_verified_artifact(await _job(job_id, session), artifact_id)
        return _stream_verified_artifact(handle, name=artifact.name, size=artifact.bytes, request=request)
    except MDResultError as exc:
        _raise(exc)
