"""
Designs API router - Query and manage protein designs.

Provides endpoints for listing, filtering, and managing designs
stored in the SQLite database after pipeline ingestion.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime
from pathlib import Path

from database import get_session, Design, Job


router = APIRouter()


# --- Pydantic Schemas ---

class DesignResponse(BaseModel):
    id: str
    job_id: str
    name: str
    pdb_path: Optional[str]
    
    # Structural metrics
    num_helices: Optional[int]
    num_strands: Optional[int]
    rog: Optional[float]
    
    # Sequence metrics
    mpnn_score: Optional[float]
    fampnn_psce: Optional[float]
    
    # Prediction metrics
    plddt_overall: Optional[float]
    plddt_binder: Optional[float]
    pae_interaction: Optional[float]
    pae_overall: Optional[float]
    rmsd_binder: Optional[float]
    
    # Boltz-2 specific
    conf_score: Optional[float]
    ptm: Optional[float]
    
    # User annotations
    is_favorite: bool
    notes: Optional[str]
    
    created_at: datetime
    
    class Config:
        from_attributes = True


class DesignList(BaseModel):
    designs: List[DesignResponse]
    total: int


class FavoriteUpdate(BaseModel):
    is_favorite: bool


class NotesUpdate(BaseModel):
    notes: str


# --- Endpoints ---

@router.get("", response_model=DesignList)
async def list_designs(
    job_id: Optional[str] = None,
    plddt_min: Optional[float] = Query(None, description="Minimum pLDDT score"),
    pae_max: Optional[float] = Query(None, description="Maximum pAE score"),
    favorites_only: bool = Query(False, description="Show only favorites"),
    limit: int = Query(100, le=1000),
    offset: int = Query(0),
    session: AsyncSession = Depends(get_session)
):
    """
    List designs with optional filtering.
    
    Filters:
    - job_id: Filter by specific job
    - plddt_min: Minimum pLDDT threshold
    - pae_max: Maximum pAE threshold
    - favorites_only: Show only favorited designs
    """
    query = select(Design).order_by(Design.created_at.desc())
    
    # Apply filters
    conditions = []
    if job_id:
        conditions.append(Design.job_id == job_id)
    if plddt_min is not None:
        conditions.append(Design.plddt_overall >= plddt_min)
    if pae_max is not None:
        conditions.append(Design.pae_overall <= pae_max)
    if favorites_only:
        conditions.append(Design.is_favorite == True)
    
    if conditions:
        query = query.where(and_(*conditions))
    
    # Get total count
    count_query = select(func.count(Design.id))
    if conditions:
        count_query = count_query.where(and_(*conditions))
    total = (await session.execute(count_query)).scalar()
    
    # Apply pagination
    query = query.limit(limit).offset(offset)
    result = await session.execute(query)
    designs = result.scalars().all()
    
    return DesignList(
        designs=[DesignResponse.model_validate(d) for d in designs],
        total=total
    )


@router.get("/{design_id}", response_model=DesignResponse)
async def get_design(
    design_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get a specific design by ID."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    return DesignResponse.model_validate(design)


@router.get("/{design_id}/pdb")
async def get_design_pdb(
    design_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Download the PDB file for a design."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    if not design.pdb_path:
        raise HTTPException(status_code=404, detail="No PDB file for this design")
    
    pdb_path = Path(design.pdb_path)
    if not pdb_path.exists():
        raise HTTPException(status_code=404, detail="PDB file not found on disk")
    
    return FileResponse(
        path=pdb_path,
        filename=f"{design.name}.pdb",
        media_type="chemical/x-pdb"
    )


@router.post("/{design_id}/favorite")
async def toggle_favorite(
    design_id: str,
    update: FavoriteUpdate,
    session: AsyncSession = Depends(get_session)
):
    """Toggle favorite status for a design."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    design.is_favorite = update.is_favorite
    await session.commit()
    
    return {"message": "Favorite updated", "is_favorite": design.is_favorite}


@router.patch("/{design_id}/notes")
async def update_notes(
    design_id: str,
    update: NotesUpdate,
    session: AsyncSession = Depends(get_session)
):
    """Update notes for a design."""
    result = await session.execute(select(Design).where(Design.id == design_id))
    design = result.scalar_one_or_none()
    
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    
    design.notes = update.notes
    await session.commit()
    
    return {"message": "Notes updated", "notes": design.notes}


@router.get("/by-job/{job_id}", response_model=DesignList)
async def get_designs_for_job(
    job_id: str,
    limit: int = Query(100, le=1000),
    offset: int = Query(0),
    session: AsyncSession = Depends(get_session)
):
    """Get all designs for a specific job."""
    # Verify job exists
    job_result = await session.execute(select(Job).where(Job.id == job_id))
    if not job_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Job not found")
    
    query = select(Design).where(Design.job_id == job_id).order_by(Design.name)
    query = query.limit(limit).offset(offset)
    
    result = await session.execute(query)
    designs = result.scalars().all()
    
    # Count total
    count_query = select(func.count(Design.id)).where(Design.job_id == job_id)
    total = (await session.execute(count_query)).scalar()
    
    return DesignList(
        designs=[DesignResponse.model_validate(d) for d in designs],
        total=total
    )
