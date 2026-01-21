"""
MSA job submission API for nucleotide/protein sequences.
Creates a GPU-backed msa_batch job using existing orchestrator pipeline.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
from pathlib import Path
import uuid
import os

from database import Job, NucleotideSequence, get_session
from services.gpu_orchestrator import estimate_vram


router = APIRouter(prefix="/api/msa", tags=["msa"])

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


class MSASequence(BaseModel):
    name: str
    sequence: str


class MSARequest(BaseModel):
    name: str
    sequence: Optional[str] = None
    sequence_id: Optional[str] = None
    reference_sequence: Optional[str] = None
    sequences: Optional[List[MSASequence]] = None


class MSAResponse(BaseModel):
    job_id: str
    status: str
    output_dir: str
    created_at: datetime


@router.post("", response_model=MSAResponse)
async def create_msa_job(request: MSARequest, session: AsyncSession = Depends(get_session)):
    sequences = []

    if request.sequences:
        sequences = [{"name": s.name, "sequence": s.sequence} for s in request.sequences]
    elif request.sequence_id:
        result = await session.execute(
            select(NucleotideSequence).where(NucleotideSequence.id == request.sequence_id)
        )
        seq = result.scalar_one_or_none()
        if not seq:
            raise HTTPException(status_code=404, detail="Sequence not found")
        sequences = [{"name": seq.name, "sequence": seq.sequence}]
    elif request.sequence:
        sequences = [{"name": request.name, "sequence": request.sequence}]
    else:
        raise HTTPException(status_code=400, detail="Provide sequence, sequence_id, or sequences list")

    job_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    base_output_dir = str(PROJECT_ROOT / "pdj_results" / f"msa_{request.name}_{timestamp}")
    os.makedirs(base_output_dir, exist_ok=True)

    seq_len = len(sequences[0]["sequence"]) if sequences else 300
    vram = estimate_vram("msa_batch", seq_len)

    job = Job(
        id=job_id,
        name=f"{request.name}_msa",
        status="queued",
        model_id="msa_batch",
        mode="msa_generation",
        params={
            "sequences": sequences,
            "sequences_json": sequences,
            "reference_sequence": request.reference_sequence,
        },
        output_dir=base_output_dir,
        created_at=datetime.utcnow(),
        queue_status="queued",
        vram_estimate_mb=vram,
        sequence_length=seq_len,
        job_phase="msa_generation",
        msa_sequences=sequences,
    )

    session.add(job)
    await session.commit()
    await session.refresh(job)

    return MSAResponse(
        job_id=job.id,
        status=job.status,
        output_dir=job.output_dir or "",
        created_at=job.created_at
    )
