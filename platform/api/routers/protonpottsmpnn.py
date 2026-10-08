from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_session
from services.protonpottsmpnn_design import read_result
router = APIRouter()

@router.get('/{job_id}/protonpottsmpnn/results')
async def get_results(job_id: str, session: AsyncSession = Depends(get_session)):
    try:
        return await read_result(session, job_id)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise HTTPException(409, str(exc)) from exc
