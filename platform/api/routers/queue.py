"""
Job Queue Management API router.

Provides endpoints for managing the GPU orchestrator job queue:
- List queued/running jobs
- Pause/resume/cancel jobs
- Pin jobs to specific GPUs
- Set job priority
"""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, ConfigDict, field_serializer
from datetime import datetime, timezone
from pathlib import Path
import time

from database import Job, get_session
from services.gpu_metadata import HARDWARE_LIMITS
from services.gpu_config import read_scheduler_config
from services.gpu_orchestrator import collect_live_vram_by_job, build_queue_scheduler_diagnostics
from services.gpu_stage_activity import job_uses_assigned_gpu
from services.job_control import (
    cancel_job_lineage,
    force_launch_job as force_launch_job_service,
)
from services.workflow_adapter import WorkflowAdapterRequestError, request_via_workflow_adapter, workflow_adapter_enabled
import logging

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/queue", tags=["queue"])

_QUEUE_ENRICHMENT_TTL_SECONDS = 2.5
_queue_enrichment_cache_time = 0.0
_queue_enrichment_cache_signature: tuple = ()
_queue_enrichment_cache_payload: Dict[str, Dict[str, object]] = {
    "live_vram_by_job": {},
    "scheduler_diagnostics": {},
    "stage_progress_by_job": {},
}


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class QueuedJobResponse(BaseModel):
    """Job in queue with relevant orchestrator fields."""
    id: str
    name: str
    model_id: str
    mode: str
    queue_status: str
    paused: bool
    pinned_gpu: Optional[int]
    assigned_gpu: Optional[int]
    display_gpu_ids: Optional[List[int]] = None
    priority: int
    vram_estimate_mb: Optional[int]
    sequence_length: Optional[int]
    batch_id: Optional[str]
    batch_name: Optional[str]
    retry_count: int
    max_retries: int
    created_at: datetime
    started_at: Optional[datetime]
    live_vram_mb: Optional[int] = None
    current_stage: Optional[str] = None  # Current workflow step (rfantibody, fampnn, etc.)
    stage_progress: Optional[str] = None  # Granular progress (e.g., "5/30")
    scheduler_required_mb: Optional[int] = None
    scheduler_candidate_gpus: Optional[List[int]] = None
    scheduler_ready: Optional[bool] = None
    scheduler_blockers: Optional[List[str]] = None

    model_config = ConfigDict(from_attributes=True)

    @field_serializer('created_at', 'started_at')
    @classmethod
    def serialize_datetime(cls, dt: Optional[datetime]) -> Optional[str]:
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _normalize_gpu_id_list(raw_value: Any) -> Optional[List[int]]:
    if raw_value in (None, ""):
        return None
    if isinstance(raw_value, str):
        raw_items: List[Any] = [token.strip() for token in raw_value.split(",") if token.strip()]
    elif isinstance(raw_value, (list, tuple, set)):
        raw_items = list(raw_value)
    else:
        raw_items = [raw_value]

    gpu_ids: List[int] = []
    seen: set[int] = set()
    for raw_item in raw_items:
        try:
            gpu_id = int(str(raw_item).strip())
        except (TypeError, ValueError):
            continue
        if gpu_id < 0 or gpu_id in seen:
            continue
        seen.add(gpu_id)
        gpu_ids.append(gpu_id)
    return gpu_ids or None


def _resolve_display_gpu_ids(job: Job) -> Optional[List[int]]:
    params = getattr(job, "params", None)
    if not isinstance(params, dict):
        params = {}

    for key in ("bcp_gpu_ids", "pinned_gpus", "gpu_ids"):
        gpu_ids = _normalize_gpu_id_list(params.get(key))
        if gpu_ids:
            return gpu_ids

    pinned_gpu = getattr(job, "pinned_gpu", None)
    if isinstance(pinned_gpu, int):
        return [pinned_gpu]

    assigned_gpu = getattr(job, "assigned_gpu", None)
    if isinstance(assigned_gpu, int) and job_uses_assigned_gpu(job):
        return [assigned_gpu]

    return None


def _valid_queue_gpu_indices() -> List[int]:
    """Return GPU indices that queue mutating endpoints may target.

    In core-runtime deployments the API container may not have nvidia-smi even
    though host GPU telemetry is available through the workflow adapter.  Do not
    validate force-launch/pin requests against the API container's local, empty
    HARDWARE_LIMITS table in that mode; ask the same live adapter-backed GPU
    status path used by the UI/orchestrator.
    """
    if HARDWARE_LIMITS:
        return sorted(int(idx) for idx in HARDWARE_LIMITS.keys())

    if workflow_adapter_enabled():
        try:
            payload = request_via_workflow_adapter("GET", "/api/gpu/status")
        except (WorkflowAdapterRequestError, RuntimeError) as exc:
            logger.warning("[QUEUE] Failed to resolve GPU indices via workflow adapter: %s", exc)
        else:
            gpus = payload.get("gpus") if isinstance(payload, dict) else None
            indices: List[int] = []
            if isinstance(gpus, list):
                for gpu in gpus:
                    if not isinstance(gpu, dict):
                        continue
                    try:
                        index = int(gpu.get("index"))
                    except (TypeError, ValueError):
                        continue
                    if index >= 0:
                        indices.append(index)
            if indices:
                return sorted(set(indices))

    return []


def _validate_queue_gpu_index(gpu_id: int) -> None:
    valid_indices = _valid_queue_gpu_indices()
    if gpu_id not in valid_indices:
        valid = ",".join(str(idx) for idx in valid_indices)
        raise HTTPException(status_code=400, detail=f"Invalid GPU index (valid: {valid})")


def _parse_pid(raw_pid: Optional[str]) -> Optional[int]:
    try:
        return int(str(raw_pid).strip())
    except (TypeError, ValueError):
        return None


def _get_process_ancestor_pids(pid: int, cache: Dict[int, set[int]]) -> set[int]:
    if pid in cache:
        return cache[pid]
    ancestors: set[int] = set()
    try:
        import psutil

        proc = psutil.Process(pid)
        while proc is not None:
            ancestors.add(proc.pid)
            proc = proc.parent()
    except Exception:
        pass
    cache[pid] = ancestors
    return ancestors


def _get_process_cmdline(pid: int, cache: Dict[int, str]) -> str:
    if pid in cache:
        return cache[pid]
    cmdline = ""
    try:
        import psutil

        cmdline = " ".join(psutil.Process(pid).cmdline()).lower()
    except Exception:
        cmdline = ""
    cache[pid] = cmdline
    return cmdline


def _collect_live_vram_by_job(jobs: List[Job]) -> Dict[str, int]:
    running_jobs = [job for job in jobs if job.queue_status == "running" and job_uses_assigned_gpu(job)]
    if not running_jobs:
        return {}
    try:
        from routers.gpu import get_gpu_stats_with_error
        gpu_stats, _error = get_gpu_stats_with_error(force_refresh=False)
    except Exception as exc:
        logger.debug("Queue VRAM enrichment unavailable: %s", exc)
        return {}
    if not gpu_stats:
        return {}
    return collect_live_vram_by_job(running_jobs, gpu_stats)


def _resolve_task_work_dir(task_bucket: str, task_prefix: str) -> Optional[str]:
    try:
        from paths import get_code_root, get_work_dir
    except Exception:
        return None
    work_roots = [Path(get_work_dir()), Path(get_code_root()) / "work"]
    seen_roots = set()
    for work_root in work_roots:
        root_key = str(work_root)
        if root_key in seen_roots:
            continue
        seen_roots.add(root_key)
        bucket_dir = work_root / task_bucket
        if not bucket_dir.exists():
            continue
        try:
            matches = sorted(bucket_dir.glob(f"{task_prefix}*"))
        except Exception:
            continue
        for candidate in matches:
            if candidate.is_dir():
                return str(candidate)
    return None


def _infer_stage_work_dir(job: Job) -> Optional[str]:
    output_dir = getattr(job, "output_dir", None)
    current_stage = str(getattr(job, "current_stage", "") or "").strip().lower()
    if not output_dir or not current_stage:
        return None

    log_candidates = [Path(output_dir) / "nextflow.log", Path(output_dir) / ".nextflow.log"]
    log_path = next((candidate for candidate in log_candidates if candidate.exists()), None)
    if log_path is None:
        return None

    try:
        lines = log_path.read_text(errors="replace").splitlines()[-400:]
    except Exception:
        return None

    stage_markers = {
        "rfantibody": "rfantibody",
        "fampnn": "fampnn",
        "waitforchildren": "waitforchildren",
        "waitforfampnnchildren": "waitforfampnnchildren",
        "waitformaturationchildren": "waitformaturationchildren",
        "waitforppiflowbackbonechildren": "waitforppiflowbackbonechildren",
        "waitforppiflowmaturationchildren": "waitforppiflowmaturationchildren",
        "spawnrfantibodyjobs": "spawnrfantibodyjobs",
        "spawnfampnnjobs": "spawnfampnnjobs",
        "spawnmaturationjobs": "spawnmaturationjobs",
        "spawnppiflowbackbonejobs": "spawnppiflowbackbonejobs",
        "spawnppiflowmaturationjobs": "spawnppiflowmaturationjobs",
        "spawnchildjobs": "spawnchildjobs",
        "waitandaggregatechildresults": "waitandaggregatechildresults",
        "backbone_refine": "ppiflow",
        "maturation": "ppiflow",
        "ppiflow_backbone": "ppiflow",
        "ppiflow_maturation": "ppiflow",
        "ppiflow_post_validation": "ppiflow",
        "protenix": "protenix",
        "boltz2": "boltz",
    }
    stage_marker = stage_markers.get(current_stage, current_stage)

    import re

    for line in reversed(lines):
        if stage_marker not in line.lower():
            continue
        match = re.search(r'^\[([0-9a-f]{2})/([0-9a-f]+)\]\s+Submitted process >', line, re.IGNORECASE)
        if match:
            return _resolve_task_work_dir(match.group(1), match.group(2))
    return None


def _collect_stage_progress_by_job(jobs: List[Job]) -> Dict[str, str]:
    try:
        from services.nextflow import parse_stage_progress
    except Exception as exc:
        logger.debug("Queue progress enrichment unavailable: %s", exc)
        return {}

    progress_by_job: Dict[str, str] = {}
    for job in jobs:
        if job.queue_status != "running" or not job.current_stage:
            continue
        work_dir = job.stage_work_dir or _infer_stage_work_dir(job)
        if not work_dir:
            continue
        total_designs = None
        params = job.params if isinstance(job.params, dict) else {}
        total_designs = params.get("rfantibody_num_designs") or params.get("num_designs")
        progress = parse_stage_progress(work_dir, job.current_stage, total_designs)
        if progress:
            progress_by_job[job.id] = progress
    return progress_by_job


def _queue_enrichment_signature(jobs: List[Job]) -> Tuple[Tuple[object, ...], ...]:
    signature_rows: List[Tuple[object, ...]] = []
    for job in jobs:
        signature_rows.append((
            job.id,
            job.queue_status,
            bool(job.paused),
            job.assigned_gpu,
            job.pinned_gpu,
            job.priority,
            str(job.current_stage or ""),
            str(job.stage_progress or ""),
            str(job.stage_work_dir or ""),
            int(job.vram_estimate_mb or 0),
            job.started_at.isoformat() if job.started_at else None,
        ))
    return tuple(signature_rows)


def _get_queue_enrichment(jobs: List[Job]) -> Dict[str, Dict[str, object]]:
    global _queue_enrichment_cache_time
    global _queue_enrichment_cache_signature
    global _queue_enrichment_cache_payload

    now = time.time()
    signature = _queue_enrichment_signature(jobs)
    if (
        signature == _queue_enrichment_cache_signature
        and (now - _queue_enrichment_cache_time) <= _QUEUE_ENRICHMENT_TTL_SECONDS
    ):
        return _queue_enrichment_cache_payload

    live_vram_by_job: Dict[str, int] = {}
    scheduler_diagnostics: Dict[str, Dict[str, object]] = {}
    stage_progress_by_job = _collect_stage_progress_by_job(jobs)

    running_jobs = [job for job in jobs if job.queue_status == "running"]
    queued_jobs = [job for job in jobs if job.queue_status == "queued"]

    try:
        from routers.gpu import get_gpu_stats_with_error

        gpu_stats, _gpu_error = get_gpu_stats_with_error(force_refresh=False)
        if gpu_stats:
            live_vram_by_job = collect_live_vram_by_job(running_jobs, gpu_stats)
            if queued_jobs:
                scheduler_diagnostics = build_queue_scheduler_diagnostics(
                    queued_jobs,
                    running_jobs,
                    gpu_stats,
                    read_scheduler_config(),
                )
    except Exception as exc:
        logger.debug("Queue scheduler diagnostics unavailable: %s", exc)

    _queue_enrichment_cache_time = now
    _queue_enrichment_cache_signature = signature
    _queue_enrichment_cache_payload = {
        "live_vram_by_job": live_vram_by_job,
        "scheduler_diagnostics": scheduler_diagnostics,
        "stage_progress_by_job": stage_progress_by_job,
    }
    return _queue_enrichment_cache_payload


class PinGPURequest(BaseModel):
    """Request to pin job to specific GPU."""
    gpu_id: Optional[int]  # None = auto-assign


class PriorityRequest(BaseModel):
    """Request to set job priority."""
    priority: int  # Higher = runs first


class QueueStatsResponse(BaseModel):
    """Queue statistics."""
    queued: int
    running: int
    paused: int
    total: int


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("", response_model=List[QueuedJobResponse])
async def list_queue(
    status: Optional[str] = None,
    session: AsyncSession = Depends(get_session)
):
    """
    List all queued/running jobs.
    
    Query params:
    - status: Filter by queue_status (queued, running, paused)
    """
    # Auto-sync: Fix queue_status for jobs that have actually completed
    # This catches cases where the job finished but queue_status wasn't updated
    sync_result = await session.execute(
        select(Job).where(
            Job.queue_status.in_(['running', 'queued', 'paused']),
            (
                Job.status.in_(['completed', 'failed', 'cancelled', 'awaiting_input'])
                | ((Job.queue_status == 'paused') & (Job.paused == False))
            )
        )
    )
    stale_jobs = sync_result.scalars().all()
    for job in stale_jobs:
        if job.status == 'awaiting_input':
            job.queue_status = 'completed'
            job.paused = False
            job.assigned_gpu = None
        elif job.status == 'completed':
            job.queue_status = 'completed'
            job.paused = False
            job.assigned_gpu = None
        elif job.queue_status == 'paused':
            job.paused = True
        else:
            job.queue_status = 'failed'
            job.paused = False
            if job.status in ['failed', 'cancelled']:
                job.assigned_gpu = None
    if stale_jobs:
        await session.commit()
    
    # Only show jobs that went through the new orchestrator (have vram_estimate_mb set)
    query = select(Job).where(
        Job.queue_status.in_(['queued', 'running', 'paused']),
        Job.awaiting_input == False,
        Job.vram_estimate_mb.isnot(None)
    ).order_by(
        Job.priority.desc(),
        Job.created_at
    )
    
    if status:
        query = select(Job).where(
            Job.queue_status == status
        ).order_by(Job.priority.desc(), Job.created_at)
    
    result = await session.execute(query)
    jobs = result.scalars().all()
    enrichment = _get_queue_enrichment(jobs)
    live_vram_by_job = enrichment.get("live_vram_by_job", {})
    scheduler_diagnostics = enrichment.get("scheduler_diagnostics", {})
    stage_progress_by_job = enrichment.get("stage_progress_by_job", {})

    return [
        QueuedJobResponse(
            id=job.id,
            name=job.name,
            model_id=job.model_id,
            mode=job.mode,
            queue_status=job.queue_status,
            paused=job.paused,
            pinned_gpu=job.pinned_gpu,
            assigned_gpu=job.assigned_gpu if job_uses_assigned_gpu(job) else None,
            display_gpu_ids=_resolve_display_gpu_ids(job),
            priority=job.priority,
            vram_estimate_mb=job.vram_estimate_mb,
            sequence_length=job.sequence_length,
            batch_id=job.batch_id,
            batch_name=job.batch_name,
            retry_count=job.retry_count,
            max_retries=job.max_retries,
            created_at=job.created_at,
            started_at=job.started_at,
            live_vram_mb=live_vram_by_job.get(job.id) if job_uses_assigned_gpu(job) else None,
            current_stage=job.current_stage,
            stage_progress=stage_progress_by_job.get(job.id, job.stage_progress),
            scheduler_required_mb=scheduler_diagnostics.get(job.id, {}).get("scheduler_required_mb"),
            scheduler_candidate_gpus=scheduler_diagnostics.get(job.id, {}).get("scheduler_candidate_gpus"),
            scheduler_ready=scheduler_diagnostics.get(job.id, {}).get("scheduler_ready"),
            scheduler_blockers=scheduler_diagnostics.get(job.id, {}).get("scheduler_blockers"),
        )
        for job in jobs
    ]


@router.get("/stats", response_model=QueueStatsResponse)
async def get_queue_stats(session: AsyncSession = Depends(get_session)):
    """Get queue statistics."""
    # Only count jobs that went through new orchestrator (have vram_estimate_mb set)
    result = await session.execute(
        select(Job).where(
            Job.queue_status.in_(['queued', 'running', 'paused']),
            Job.awaiting_input == False,
            Job.vram_estimate_mb.isnot(None)
        )
    )
    jobs = result.scalars().all()
    
    queued = sum(1 for j in jobs if j.queue_status == 'queued')
    running = sum(1 for j in jobs if j.queue_status == 'running')
    paused = sum(1 for j in jobs if j.queue_status == 'paused')
    
    return QueueStatsResponse(
        queued=queued,
        running=running,
        paused=paused,
        total=len(jobs)
    )


@router.post("/{job_id}/pause")
async def pause_job(job_id: str, session: AsyncSession = Depends(get_session)):
    """Pause a queued job (won't be scheduled until resumed)."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.queue_status not in ['queued', 'running']:
        raise HTTPException(status_code=400, detail=f"Cannot pause job with status: {job.queue_status}")
    
    job.paused = True
    job.queue_status = 'paused'
    await session.commit()
    
    return {"success": True, "message": f"Job {job.name} paused", "job_id": job_id}


@router.post("/{job_id}/resume")
async def resume_job(job_id: str, session: AsyncSession = Depends(get_session)):
    """Resume a paused job."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if not job.paused:
        raise HTTPException(status_code=400, detail="Job is not paused")
    
    job.paused = False
    job.queue_status = 'queued'
    await session.commit()
    
    return {"success": True, "message": f"Job {job.name} resumed", "job_id": job_id}


@router.post("/{job_id}/release-gpu")
async def release_job_gpu(job_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    released_gpu = job.assigned_gpu
    job.assigned_gpu = None
    await session.commit()

    return {
        "success": True,
        "job_id": job_id,
        "released_gpu": released_gpu,
        "message": f"Released assigned GPU for job {job.name}",
    }


@router.delete("/{job_id}")
async def cancel_job(job_id: str, session: AsyncSession = Depends(get_session)):
    """Cancel a queued/paused/running job and any active descendant jobs it spawned."""
    job, lineage = await cancel_job_lineage(job_id, session)
    return {
        "success": True,
        "message": f"Job {job.name} cancelled",
        "job_id": job_id,
        "jobs_cancelled": len(lineage),
    }


@router.post("/{job_id}/pin")
async def pin_job_to_gpu(
    job_id: str, 
    request: PinGPURequest,
    session: AsyncSession = Depends(get_session)
):
    """Pin a job to a specific GPU (or set to None for auto-assign)."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.queue_status not in ['queued', 'paused']:
        raise HTTPException(status_code=400, detail="Can only pin queued or paused jobs")
    
    # Validate GPU index against live/proxied GPU status, not only the API
    # container's local nvidia-smi view.
    if request.gpu_id is not None:
        _validate_queue_gpu_index(request.gpu_id)
    
    job.pinned_gpu = request.gpu_id
    await session.commit()
    
    pin_msg = f"GPU {request.gpu_id}" if request.gpu_id is not None else "auto-assign"
    return {"success": True, "message": f"Job {job.name} pinned to {pin_msg}", "job_id": job_id}


@router.post("/{job_id}/priority")
async def set_job_priority(
    job_id: str,
    request: PriorityRequest,
    session: AsyncSession = Depends(get_session)
):
    """Set job priority (higher = runs first)."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.queue_status not in ['queued', 'paused']:
        raise HTTPException(status_code=400, detail="Can only change priority for queued or paused jobs")
    
    job.priority = request.priority
    await session.commit()
    
    return {"success": True, "message": f"Job {job.name} priority set to {request.priority}", "job_id": job_id}


@router.post("/{job_id}/retry")
async def retry_job(job_id: str, session: AsyncSession = Depends(get_session)):
    """Retry a failed job (resets to queued)."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.queue_status != 'failed':
        raise HTTPException(status_code=400, detail="Can only retry failed jobs")
    
    if job.retry_count >= job.max_retries:
        raise HTTPException(status_code=400, detail=f"Max retries ({job.max_retries}) exceeded")
    
    job.retry_count += 1
    job.queue_status = 'queued'
    job.paused = False
    job.error_message = None
    job.assigned_gpu = None
    await session.commit()
    
    return {
        "success": True, 
        "message": f"Job {job.name} requeued (retry {job.retry_count}/{job.max_retries})", 
        "job_id": job_id
    }


@router.delete("/clear-all")
async def cancel_all_queued(session: AsyncSession = Depends(get_session)):
    """Cancel ALL queued/paused jobs (not running jobs)."""
    result = await session.execute(
        select(Job).where(Job.queue_status.in_(['queued', 'paused']))
    )
    jobs = result.scalars().all()
    
    cancelled_count = 0
    cancelled_names = []
    for job in jobs:
        job.queue_status = 'failed'
        job.paused = False
        job.status = 'cancelled'
        job.error_message = 'Bulk cancelled by user'
        cancelled_count += 1
        cancelled_names.append(job.name)
    
    await session.commit()
    
    return {
        "success": True,
        "cancelled_count": cancelled_count,
        "cancelled_jobs": cancelled_names[:10],  # First 10 for display
        "message": f"Cancelled {cancelled_count} jobs"
    }


@router.get("/cancelled", response_model=List[QueuedJobResponse])
async def list_cancelled_jobs(
    limit: int = 20,
    session: AsyncSession = Depends(get_session)
):
    """List recently cancelled jobs (for requeue UI)."""
    result = await session.execute(
        select(Job).where(
            Job.error_message.like('%cancelled%')
        ).order_by(Job.created_at.desc()).limit(limit)
    )
    jobs = result.scalars().all()
    return jobs


@router.post("/kill-active")
async def kill_active_nextflow_jobs():
    """Kill ALL running Nextflow processes (nuclear option)."""
    import subprocess
    import os
    
    # Find and kill Nextflow Java processes
    try:
        # Get PIDs of Nextflow processes
        result = subprocess.run(
            ["pgrep", "-f", "nextflow"],
            capture_output=True,
            text=True
        )
        pids = result.stdout.strip().split('\n')
        pids = [p for p in pids if p]  # Remove empty strings
        
        killed = []
        for pid in pids:
            try:
                os.kill(int(pid), 15)  # SIGTERM
                killed.append(pid)
            except (ProcessLookupError, ValueError):
                pass
        
        return {
            "success": True,
            "killed_pids": killed,
            "message": f"Sent SIGTERM to {len(killed)} Nextflow processes"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "message": "Failed to kill processes"
        }


class ForceLaunchRequest(BaseModel):
    """Request to force-launch a job on a specific GPU."""
    gpu_id: int  # GPU to launch on


@router.post("/{job_id}/force-launch")
async def force_launch_job(
    job_id: str,
    request: ForceLaunchRequest,
    session: AsyncSession = Depends(get_session)
):
    """
    Force-launch a queued job, bypassing VRAM checks.
    
    This directly starts the job on the specified GPU without waiting for
    the orchestrator's bin-packing algorithm. Use when you know there's
    enough VRAM and want to manually control job placement.
    """
    # Validate GPU index against live/proxied GPU status, not only the API
    # container's local nvidia-smi view.
    _validate_queue_gpu_index(request.gpu_id)

    try:
        job = await force_launch_job_service(
            job_id=job_id,
            gpu_id=request.gpu_id,
            allowed_queue_statuses=["queued", "paused"],
            session=session,
        )
        logger.info(f"[FORCE-LAUNCH] {job.name} on GPU {request.gpu_id}")
        return {
            "success": True,
            "message": f"Force-launched {job.name} on GPU {request.gpu_id}",
            "job_id": job_id,
            "gpu_id": request.gpu_id
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[FORCE-LAUNCH FAILED] {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Launch failed: {str(e)}")
