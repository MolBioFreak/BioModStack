from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job, get_session
from routers.files import _serve_file_response
from services.md.results import MDResultError, analysis_report, artifact_inventory, resolve_artifact, summary

router = APIRouter()


async def _job(job_id: str, session: AsyncSession) -> Job:
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _raise(error: MDResultError) -> None:
    raise HTTPException(status_code=error.status_code, detail={"code": error.code, "message": str(error)})


@router.get("/{job_id}/md/summary")
async def get_md_summary(job_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    try:
        return summary(await _job(job_id, session))
    except MDResultError as exc:
        _raise(exc)


@router.get("/{job_id}/md/artifacts")
async def get_md_artifacts(job_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    try:
        return artifact_inventory(await _job(job_id, session))
    except MDResultError as exc:
        _raise(exc)


@router.get("/{job_id}/md/analysis")
async def get_md_analysis(job_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    try:
        return analysis_report(await _job(job_id, session))
    except MDResultError as exc:
        _raise(exc)


@router.get("/{job_id}/md/artifacts/{artifact_id}/content")
async def get_md_artifact_content(job_id: str, artifact_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    try:
        artifact = resolve_artifact(await _job(job_id, session), artifact_id, verify=True)
        return _serve_file_response(artifact.path, request, as_attachment=False)
    except MDResultError as exc:
        _raise(exc)
