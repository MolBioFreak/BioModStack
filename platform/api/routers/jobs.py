"""
Jobs API router - Create, list, cancel pipeline jobs.
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional
import uuid
from datetime import datetime

from database import get_session, Job, Design
from schemas import JobCreate, JobResponse, JobList, JobStatus
from services.nextflow import launch_nextflow_job, cancel_nextflow_job

router = APIRouter()


@router.get("", response_model=JobList)
async def list_jobs(
    status: Optional[JobStatus] = None,
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session)
):
    """List all jobs with optional status filter."""
    query = select(Job).order_by(Job.created_at.desc())
    
    if status:
        query = query.where(Job.status == status.value)
    
    query = query.limit(limit).offset(offset)
    result = await session.execute(query)
    jobs = result.scalars().all()
    
    # Get total count
    count_query = select(func.count(Job.id))
    if status:
        count_query = count_query.where(Job.status == status.value)
    total = (await session.execute(count_query)).scalar()
    
    # Get design counts for each job
    job_responses = []
    for job in jobs:
        design_count_query = select(func.count(Design.id)).where(Design.job_id == job.id)
        design_count = (await session.execute(design_count_query)).scalar()
        
        job_responses.append(JobResponse(
            id=job.id,
            name=job.name,
            status=job.status,
            mode=job.mode,
            params=job.params,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            output_dir=job.output_dir,
            error_message=job.error_message,
            design_count=design_count or 0
        ))
    
    return JobList(jobs=job_responses, total=total)


@router.post("", response_model=JobResponse, status_code=201)
async def create_job(
    job_data: JobCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session)
):
    """Create and queue a new pipeline job."""
    job_id = str(uuid.uuid4())
    
    # Create output directory name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = f"pdj_results/{job_data.name}_{timestamp}"
    
    # Create job record
    job = Job(
        id=job_id,
        name=job_data.name,
        status=JobStatus.QUEUED.value,
        mode=job_data.mode.value,
        params=job_data.params,
        output_dir=output_dir
    )
    
    session.add(job)
    await session.commit()
    await session.refresh(job)
    
    # Launch job in background
    background_tasks.add_task(
        launch_nextflow_job,
        job_id=job_id,
        mode=job_data.mode.value,
        params=job_data.params,
        output_dir=output_dir
    )
    
    return JobResponse(
        id=job.id,
        name=job.name,
        status=job.status,
        mode=job.mode,
        params=job.params,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        output_dir=job.output_dir,
        error_message=job.error_message,
        design_count=0
    )


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get details of a specific job."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Get design count
    design_count_query = select(func.count(Design.id)).where(Design.job_id == job.id)
    design_count = (await session.execute(design_count_query)).scalar()
    
    return JobResponse(
        id=job.id,
        name=job.name,
        status=job.status,
        mode=job.mode,
        params=job.params,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        output_dir=job.output_dir,
        error_message=job.error_message,
        design_count=design_count or 0
    )


@router.delete("/{job_id}")
async def cancel_job(
    job_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Cancel a running or queued job."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.status not in [JobStatus.QUEUED.value, JobStatus.RUNNING.value]:
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot cancel job with status: {job.status}"
        )
    
    # Cancel the Nextflow process if running
    if job.nextflow_run_id:
        await cancel_nextflow_job(job.nextflow_run_id)
    
    job.status = JobStatus.CANCELLED.value
    job.completed_at = datetime.utcnow()
    await session.commit()
    
    return {"message": "Job cancelled", "job_id": job_id}


@router.get("/{job_id}/logs")
async def get_job_logs(
    job_id: str,
    tail: int = 100,
    session: AsyncSession = Depends(get_session)
):
    """Get log output for a job."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # TODO: Read from Nextflow log file
    log_path = f"{job.output_dir}/.nextflow.log" if job.output_dir else None
    
    if log_path:
        try:
            with open(log_path, 'r') as f:
                lines = f.readlines()
                return {"logs": "".join(lines[-tail:])}
        except FileNotFoundError:
            return {"logs": "Log file not yet available"}
    
    return {"logs": "No log file path configured"}
