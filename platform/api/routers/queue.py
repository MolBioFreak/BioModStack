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
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime

from database import Job, get_session
from services.gpu_metadata import HARDWARE_LIMITS
from services.job_control import force_launch_job as force_launch_job_service
import logging

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/queue", tags=["queue"])


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
    priority: int
    vram_estimate_mb: Optional[int]
    sequence_length: Optional[int]
    batch_id: Optional[str]
    batch_name: Optional[str]
    retry_count: int
    max_retries: int
    created_at: datetime
    started_at: Optional[datetime]
    current_stage: Optional[str] = None  # Current workflow step (rfantibody, fampnn, etc.)
    stage_progress: Optional[str] = None  # Granular progress (e.g., "5/30")
    
    class Config:
        from_attributes = True


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
            Job.queue_status.in_(['running', 'queued']),
            Job.status.in_(['completed', 'failed', 'cancelled'])
        )
    )
    stale_jobs = sync_result.scalars().all()
    for job in stale_jobs:
        job.queue_status = 'completed' if job.status == 'completed' else 'failed'
    if stale_jobs:
        await session.commit()
    
    # Only show jobs that went through the new orchestrator (have vram_estimate_mb set)
    query = select(Job).where(
        Job.queue_status.in_(['queued', 'running', 'paused']),
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
    
    return jobs


@router.get("/stats", response_model=QueueStatsResponse)
async def get_queue_stats(session: AsyncSession = Depends(get_session)):
    """Get queue statistics."""
    # Only count jobs that went through new orchestrator (have vram_estimate_mb set)
    result = await session.execute(
        select(Job).where(
            Job.queue_status.in_(['queued', 'running', 'paused']),
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


@router.delete("/{job_id}")
async def cancel_job(job_id: str, session: AsyncSession = Depends(get_session)):
    """Cancel a queued/paused/running job."""
    from services.nextflow import cancel_nextflow_job
    
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.queue_status == 'running':
        # Actually kill the Nextflow process
        if job.nextflow_run_id:
            try:
                killed = await cancel_nextflow_job(job.nextflow_run_id)
                if killed:
                    logger.info(f"[CANCEL] Killed Nextflow process for job {job.name}")
            except Exception as e:
                logger.warning(f"[CANCEL] Failed to kill process for {job.name}: {e}")
        
        job.queue_status = 'failed'
        job.status = 'cancelled'
        job.error_message = 'Cancelled by user'
    elif job.queue_status in ['queued', 'paused']:
        job.queue_status = 'failed'
        job.status = 'cancelled'
        job.error_message = 'Cancelled by user'
    else:
        raise HTTPException(status_code=400, detail=f"Cannot cancel job with status: {job.queue_status}")
    
    await session.commit()
    
    return {"success": True, "message": f"Job {job.name} cancelled", "job_id": job_id}


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
    
    # Validate GPU index
    if request.gpu_id is not None and request.gpu_id not in HARDWARE_LIMITS:
        valid = ",".join(str(idx) for idx in sorted(HARDWARE_LIMITS.keys()))
        raise HTTPException(status_code=400, detail=f"Invalid GPU index (valid: {valid})")
    
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


@router.delete("/cancel-all")
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
    # Validate GPU index
    if request.gpu_id not in HARDWARE_LIMITS:
        valid = ",".join(str(idx) for idx in sorted(HARDWARE_LIMITS.keys()))
        raise HTTPException(status_code=400, detail=f"Invalid GPU index (valid: {valid})")

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

