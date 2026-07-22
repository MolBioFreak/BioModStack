from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from services.bioxp.runtime import BioXpRuntime

from .dependencies import get_bioxp_runtime, require_bioxp_mutation_access

router = APIRouter(dependencies=[Depends(require_bioxp_mutation_access)])


@router.get("/jobs")
async def list_jobs(
    limit: int = Query(default=100, ge=1, le=500),
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    jobs = runtime.jobs.list(limit=limit)
    return {"jobs": [job.model_dump(mode="json") for job in jobs]}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> dict[str, Any]:
    job = runtime.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="BioXP job not found")
    return {
        "job": job.model_dump(mode="json"),
        "events": [event.model_dump(mode="json") for event in runtime.jobs.events(job_id)],
    }
