"""
Job control helpers shared across routers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence
import asyncio
import json
import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session, Job
from services.nextflow import launch_nextflow_job

logger = logging.getLogger(__name__)


async def _force_launch_with_session(
    session: AsyncSession,
    job_id: str,
    gpu_id: int,
    allowed_queue_statuses: Sequence[str],
) -> Job:
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    if job.queue_status not in allowed_queue_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Job must be {', '.join(allowed_queue_statuses)} to force-run (current: {job.queue_status})",
        )

    params = json.loads(job.params) if isinstance(job.params, str) else (job.params or {})
    params["gpu_id"] = gpu_id

    job.queue_status = "running"
    job.status = "running"
    job.assigned_gpu = gpu_id
    job.started_at = datetime.utcnow()
    job.paused = False

    await session.commit()

    asyncio.create_task(
        launch_nextflow_job(
            job_id=job.id,
            model_id=job.model_id,
            mode=params.get("mode", job.mode),
            params=params,
            output_dir=job.output_dir,
        )
    )

    return job


async def force_launch_job(
    job_id: str,
    gpu_id: int,
    allowed_queue_statuses: Sequence[str],
    session: Optional[AsyncSession] = None,
) -> Job:
    if session is None:
        async with async_session() as session:
            return await _force_launch_with_session(session, job_id, gpu_id, allowed_queue_statuses)
    return await _force_launch_with_session(session, job_id, gpu_id, allowed_queue_statuses)
