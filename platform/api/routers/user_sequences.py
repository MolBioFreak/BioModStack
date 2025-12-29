"""
User Sequences API router - CRUD operations for user-defined sequences.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
import uuid

from database import get_session, UserSequence


router = APIRouter()


# --- Schemas ---

class UserSequenceCreate(BaseModel):
    """Request schema for creating a user sequence."""
    name: str = Field(..., min_length=1, max_length=255)
    sequence: str = Field(..., min_length=1)
    description: Optional[str] = Field(None, max_length=500)
    organism: Optional[str] = Field(None, max_length=255)
    uniprot_id: Optional[str] = Field(None, max_length=50)
    ncbi_id: Optional[str] = Field(None, max_length=50)


class UserSequenceUpdate(BaseModel):
    """Request schema for updating a user sequence."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    sequence: Optional[str] = Field(None, min_length=1)
    description: Optional[str] = Field(None, max_length=500)
    organism: Optional[str] = Field(None, max_length=255)
    uniprot_id: Optional[str] = Field(None, max_length=50)
    ncbi_id: Optional[str] = Field(None, max_length=50)


class UserSequenceResponse(BaseModel):
    """Response schema for a user sequence."""
    id: str
    name: str
    sequence: str
    description: Optional[str]
    length: int
    organism: Optional[str]
    uniprot_id: Optional[str]
    ncbi_id: Optional[str]
    is_preset: bool = False
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


# --- Endpoints ---

@router.get("", response_model=List[UserSequenceResponse])
async def list_user_sequences(
    search: Optional[str] = Query(None, description="Search by name or description"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session)
):
    """List all user-defined sequences."""
    query = select(UserSequence).order_by(desc(UserSequence.created_at))
    
    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            UserSequence.name.ilike(search_pattern) | 
            UserSequence.description.ilike(search_pattern)
        )
    
    query = query.limit(limit).offset(offset)
    result = await session.execute(query)
    sequences = result.scalars().all()
    
    return sequences


@router.post("", response_model=UserSequenceResponse, status_code=201)
async def create_user_sequence(
    data: UserSequenceCreate,
    session: AsyncSession = Depends(get_session)
):
    """Create a new user-defined sequence."""
    # Clean and validate sequence
    clean_sequence = data.sequence.upper().replace(" ", "").replace("\n", "")
    valid_aa = set("ACDEFGHIKLMNPQRSTVWY")
    invalid_chars = set(clean_sequence) - valid_aa
    
    if invalid_chars:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid amino acid characters: {', '.join(sorted(invalid_chars))}"
        )
    
    # Check for duplicate name
    existing = await session.execute(
        select(UserSequence).where(UserSequence.name == data.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Sequence with name '{data.name}' already exists")
    
    sequence = UserSequence(
        id=str(uuid.uuid4()),
        name=data.name,
        sequence=clean_sequence,
        description=data.description,
        length=len(clean_sequence),
        organism=data.organism,
        uniprot_id=data.uniprot_id,
        ncbi_id=data.ncbi_id,
    )
    
    session.add(sequence)
    await session.commit()
    await session.refresh(sequence)
    
    return sequence


@router.get("/{sequence_id}", response_model=UserSequenceResponse)
async def get_user_sequence(
    sequence_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get a specific user sequence by ID."""
    result = await session.execute(
        select(UserSequence).where(UserSequence.id == sequence_id)
    )
    sequence = result.scalar_one_or_none()
    
    if not sequence:
        raise HTTPException(status_code=404, detail="Sequence not found")
    
    return sequence


@router.put("/{sequence_id}", response_model=UserSequenceResponse)
async def update_user_sequence(
    sequence_id: str,
    data: UserSequenceUpdate,
    session: AsyncSession = Depends(get_session)
):
    """Update a user sequence."""
    result = await session.execute(
        select(UserSequence).where(UserSequence.id == sequence_id)
    )
    sequence = result.scalar_one_or_none()
    
    if not sequence:
        raise HTTPException(status_code=404, detail="Sequence not found")
    
    # Update fields if provided
    if data.name is not None:
        # Check for duplicate name
        existing = await session.execute(
            select(UserSequence).where(
                UserSequence.name == data.name,
                UserSequence.id != sequence_id
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"Sequence with name '{data.name}' already exists")
        sequence.name = data.name
    
    if data.sequence is not None:
        clean_sequence = data.sequence.upper().replace(" ", "").replace("\n", "")
        valid_aa = set("ACDEFGHIKLMNPQRSTVWY")
        invalid_chars = set(clean_sequence) - valid_aa
        
        if invalid_chars:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid amino acid characters: {', '.join(sorted(invalid_chars))}"
            )
        sequence.sequence = clean_sequence
        sequence.length = len(clean_sequence)
    
    if data.description is not None:
        sequence.description = data.description
    if data.organism is not None:
        sequence.organism = data.organism
    if data.uniprot_id is not None:
        sequence.uniprot_id = data.uniprot_id
    if data.ncbi_id is not None:
        sequence.ncbi_id = data.ncbi_id
    
    await session.commit()
    await session.refresh(sequence)
    
    return sequence


@router.delete("/{sequence_id}", status_code=204)
async def delete_user_sequence(
    sequence_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Delete a user sequence."""
    result = await session.execute(
        select(UserSequence).where(UserSequence.id == sequence_id)
    )
    sequence = result.scalar_one_or_none()
    
    if not sequence:
        raise HTTPException(status_code=404, detail="Sequence not found")
    
    await session.delete(sequence)
    await session.commit()


@router.post("/import-presets", response_model=dict)
async def import_preset_sequences(
    session: AsyncSession = Depends(get_session)
):
    """Import preset sequences from YAML config into the database.
    
    This allows preset sequences to be editable alongside user sequences.
    Skips sequences that already exist by name.
    """
    import yaml
    from pathlib import Path
    
    config_path = Path(__file__).parent.parent / "config" / "inputs.yaml"
    
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="inputs.yaml not found")
    
    with open(config_path) as f:
        config = yaml.safe_load(f)
    
    # The key is "preset_sequences" not "sequences"
    sequences = config.get("preset_sequences", [])
    imported = 0
    skipped = 0
    
    for seq_data in sequences:
        # Check if already exists
        existing = await session.execute(
            select(UserSequence).where(UserSequence.name == seq_data.get("name", ""))
        )
        if existing.scalar_one_or_none():
            skipped += 1
            continue
        
        # Clean sequence (may be multiline YAML literal)
        raw_seq = seq_data.get("sequence", "")
        clean_seq = raw_seq.upper().replace(" ", "").replace("\n", "")
        
        if not clean_seq:
            continue
        
        sequence = UserSequence(
            id=str(uuid.uuid4()),
            name=seq_data.get("name", f"Imported-{imported}"),
            sequence=clean_seq,
            description=seq_data.get("description"),
            length=len(clean_seq),
            organism=seq_data.get("organism"),
            # YAML uses "uniprot" not "uniprot_id"
            uniprot_id=seq_data.get("uniprot") or seq_data.get("uniprot_id"),
            ncbi_id=seq_data.get("ncbi_id"),
            is_preset=True,
        )
        
        session.add(sequence)
        imported += 1
    
    await session.commit()
    
    return {
        "imported": imported,
        "skipped": skipped,
        "message": f"Imported {imported} sequences, skipped {skipped} (already exist)"
    }
