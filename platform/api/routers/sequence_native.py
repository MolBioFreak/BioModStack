"""Model-owned standalone sequence-design and packing result readback."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job, get_session
from services.caliby_native import SUPPORTED_MODES
from services.ligandmpnn_design import MODES
from services import caliby_native_publication, ligandmpnn_design_publication

router = APIRouter(prefix="/jobs", tags=["sequence-native"])


async def _read(job_id, session, model_id, modes, owner):
    job = await session.get(Job, job_id)
    if job is None or job.model_id != model_id or job.mode not in modes:
        raise HTTPException(404, "Native result Job not found")
    try:
        return await owner.read_published_native_results(job, session)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{job_id}/caliby-native-results")
async def get_caliby_native_results(job_id: str, session: AsyncSession = Depends(get_session)):
    return await _read(job_id, session, "caliby_experimental", SUPPORTED_MODES, caliby_native_publication)


@router.get("/{job_id}/ligandmpnn-design-results")
async def get_ligandmpnn_design_results(job_id: str, session: AsyncSession = Depends(get_session)):
    return await _read(job_id, session, "ligandmpnn", MODES, ligandmpnn_design_publication)
