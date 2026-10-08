from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Design, Job, get_session
from services.plr_workflow_results import (
    build_protein_local_redesign_result_surface,
    is_protein_local_redesign_job,
    resolve_protein_local_redesign_artifact,
)

router = APIRouter()


async def _load_job_and_designs(job_id: str, session: AsyncSession) -> tuple[Job, list[Design]]:
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if job is None or not is_protein_local_redesign_job(job):
        raise HTTPException(status_code=404, detail="Protein Local Redesign workflow result not found")
    designs = (
        await session.execute(
            select(Design).where(Design.job_id == job_id).order_by(Design.name, Design.id)
        )
    ).scalars().all()
    return job, list(designs)


def _surface_error(exc: ValueError) -> HTTPException:
    message = str(exc)
    status_code = 404 if "unavailable" in message or "not registered" in message else 409
    return HTTPException(
        status_code=status_code,
        detail={
            "schema": "bms.workflow.error.v1",
            "code": "PLR_RESULT_SURFACE_UNAVAILABLE" if status_code == 404 else "PLR_RESULT_ARTIFACT_INTEGRITY_ERROR",
            "message": message,
            "retryable": False,
        },
    )


@router.get("/{job_id}/workflow-results")
async def get_protein_local_redesign_result_surface(
    job_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    job, designs = await _load_job_and_designs(job_id, session)
    try:
        return build_protein_local_redesign_result_surface(job, designs)
    except ValueError as exc:
        raise _surface_error(exc) from exc


@router.get("/{job_id}/workflow-results/artifacts/{artifact_id}")
async def get_protein_local_redesign_result_artifact(
    job_id: str,
    artifact_id: str,
    session: AsyncSession = Depends(get_session),
):
    job, designs = await _load_job_and_designs(job_id, session)
    try:
        surface = build_protein_local_redesign_result_surface(job, designs)
        path, artifact = resolve_protein_local_redesign_artifact(job, surface, artifact_id)
    except ValueError as exc:
        raise _surface_error(exc) from exc

    headers = {"X-Content-Type-Options": "nosniff", "ETag": f'"{artifact["sha256"]}"'}
    if path.name.endswith(".cif.gz"):
        headers["Content-Encoding"] = "gzip"
    media_type = str(artifact.get("media_type") or "application/octet-stream")
    return FileResponse(
        path=Path(path),
        media_type=media_type,
        filename=path.name,
        headers=headers,
    )
