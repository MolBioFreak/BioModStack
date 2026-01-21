"""
Nucleotide Sequences API for BioDesigner.

Provides CRUD operations for DNA/RNA sequences with feature annotations.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from datetime import datetime
import uuid

from database import NucleotideSequence, get_session


router = APIRouter(prefix="/api/sequences", tags=["sequences"])


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class FeatureSchema(BaseModel):
    """Feature annotation on a sequence."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    type: str = "misc_feature"
    start: int
    end: int
    strand: int = 1  # 1 for forward, -1 for reverse
    color: Optional[str] = None
    notes: Optional[dict] = None


class PrimerSchema(BaseModel):
    """Primer associated with a sequence."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    sequence: str
    start: int
    end: int
    tm: Optional[float] = None
    gc_percent: Optional[float] = None


class NucleotideSequenceCreate(BaseModel):
    """Schema for creating a new sequence."""
    name: str
    description: Optional[str] = None
    sequence: str
    sequence_type: str = "dna"
    is_circular: bool = False
    features: Optional[List[FeatureSchema]] = None
    primers: Optional[List[PrimerSchema]] = None
    organism: Optional[str] = None
    accession: Optional[str] = None
    source_file: Optional[str] = None


class NucleotideSequenceUpdate(BaseModel):
    """Schema for updating an existing sequence."""
    name: Optional[str] = None
    description: Optional[str] = None
    sequence: Optional[str] = None
    sequence_type: Optional[str] = None
    is_circular: Optional[bool] = None
    features: Optional[List[FeatureSchema]] = None
    primers: Optional[List[PrimerSchema]] = None
    organism: Optional[str] = None
    accession: Optional[str] = None


class NucleotideSequenceResponse(BaseModel):
    """Schema for sequence response."""
    id: str
    name: str
    description: Optional[str]
    sequence: str
    sequence_type: str
    is_circular: bool
    length: int
    features: Optional[List[Any]]
    primers: Optional[List[Any]]
    organism: Optional[str]
    accession: Optional[str]
    source_file: Optional[str]
    gc_content: Optional[float]
    parent_id: Optional[str]
    operation: Optional[str]
    operation_params: Optional[dict]
    version: Optional[int]
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class NucleotideSequenceListItem(BaseModel):
    """Schema for sequence list response (lighter)."""
    id: str
    name: str
    description: Optional[str]
    sequence_type: str
    is_circular: bool
    length: int
    gc_content: Optional[float]
    feature_count: int
    created_at: datetime


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_gc_content(sequence: str) -> float:
    """Calculate GC content percentage."""
    seq = sequence.upper()
    gc_count = seq.count('G') + seq.count('C')
    total = len(seq)
    if total == 0:
        return 0.0
    return round((gc_count / total) * 100, 2)


def clean_sequence(sequence: str, seq_type: str = "dna") -> str:
    """Clean and validate nucleotide sequence."""
    seq = sequence.upper().replace(" ", "").replace("\n", "").replace("\r", "")
    valid_chars = set("ATCGN") if seq_type == "dna" else set("AUCGN")
    cleaned = "".join(c for c in seq if c in valid_chars)
    return cleaned


# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/", response_model=List[NucleotideSequenceListItem])
async def list_sequences(
    session: AsyncSession = Depends(get_session),
    limit: int = 100,
    offset: int = 0
):
    """List all nucleotide sequences."""
    result = await session.execute(
        select(NucleotideSequence)
        .order_by(NucleotideSequence.updated_at.desc().nullsfirst(), NucleotideSequence.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    sequences = result.scalars().all()
    
    return [
        NucleotideSequenceListItem(
            id=seq.id,
            name=seq.name,
            description=seq.description,
            sequence_type=seq.sequence_type,
            is_circular=seq.is_circular,
            length=seq.length,
            gc_content=seq.gc_content,
            feature_count=len(seq.features) if seq.features else 0,
            created_at=seq.created_at
        )
        for seq in sequences
    ]


@router.post("/", response_model=NucleotideSequenceResponse)
async def create_sequence(
    data: NucleotideSequenceCreate,
    session: AsyncSession = Depends(get_session)
):
    """Create a new nucleotide sequence."""
    # Clean and validate sequence
    cleaned_seq = clean_sequence(data.sequence, data.sequence_type)
    if not cleaned_seq:
        raise HTTPException(status_code=400, detail="Invalid sequence: no valid nucleotides found")
    
    # Create new sequence record
    seq_id = str(uuid.uuid4())
    seq = NucleotideSequence(
        id=seq_id,
        name=data.name,
        description=data.description,
        sequence=cleaned_seq,
        sequence_type=data.sequence_type,
        is_circular=data.is_circular,
        length=len(cleaned_seq),
        features=[f.model_dump() for f in data.features] if data.features else [],
        primers=[p.model_dump() for p in data.primers] if data.primers else [],
        organism=data.organism,
        accession=data.accession,
        source_file=data.source_file,
        gc_content=calculate_gc_content(cleaned_seq)
    )
    
    session.add(seq)
    await session.commit()
    await session.refresh(seq)
    
    return seq


@router.get("/{sequence_id}", response_model=NucleotideSequenceResponse)
async def get_sequence(
    sequence_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get a specific sequence by ID."""
    result = await session.execute(
        select(NucleotideSequence).where(NucleotideSequence.id == sequence_id)
    )
    seq = result.scalar_one_or_none()
    
    if not seq:
        raise HTTPException(status_code=404, detail="Sequence not found")
    
    return seq


@router.put("/{sequence_id}", response_model=NucleotideSequenceResponse)
async def update_sequence(
    sequence_id: str,
    data: NucleotideSequenceUpdate,
    session: AsyncSession = Depends(get_session)
):
    """Update an existing sequence."""
    result = await session.execute(
        select(NucleotideSequence).where(NucleotideSequence.id == sequence_id)
    )
    seq = result.scalar_one_or_none()
    
    if not seq:
        raise HTTPException(status_code=404, detail="Sequence not found")
    
    # Update fields if provided
    if data.name is not None:
        seq.name = data.name
    if data.description is not None:
        seq.description = data.description
    if data.sequence is not None:
        cleaned_seq = clean_sequence(data.sequence, data.sequence_type or seq.sequence_type)
        seq.sequence = cleaned_seq
        seq.length = len(cleaned_seq)
        seq.gc_content = calculate_gc_content(cleaned_seq)
    if data.sequence_type is not None:
        seq.sequence_type = data.sequence_type
    if data.is_circular is not None:
        seq.is_circular = data.is_circular
    if data.features is not None:
        seq.features = [f.model_dump() for f in data.features]
    if data.primers is not None:
        seq.primers = [p.model_dump() for p in data.primers]
    if data.organism is not None:
        seq.organism = data.organism
    if data.accession is not None:
        seq.accession = data.accession
    
    await session.commit()
    await session.refresh(seq)
    
    return seq


@router.delete("/{sequence_id}")
async def delete_sequence(
    sequence_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Delete a sequence."""
    result = await session.execute(
        select(NucleotideSequence).where(NucleotideSequence.id == sequence_id)
    )
    seq = result.scalar_one_or_none()
    
    if not seq:
        raise HTTPException(status_code=404, detail="Sequence not found")
    
    await session.delete(seq)
    await session.commit()
    
    return {"status": "deleted", "id": sequence_id}


@router.post("/{sequence_id}/features", response_model=NucleotideSequenceResponse)
async def add_feature(
    sequence_id: str,
    feature: FeatureSchema,
    session: AsyncSession = Depends(get_session)
):
    """Add a feature to a sequence."""
    result = await session.execute(
        select(NucleotideSequence).where(NucleotideSequence.id == sequence_id)
    )
    seq = result.scalar_one_or_none()
    
    if not seq:
        raise HTTPException(status_code=404, detail="Sequence not found")
    
    # Initialize features if None
    if seq.features is None:
        seq.features = []
    
    # Add the new feature
    features = list(seq.features)
    features.append(feature.model_dump())
    seq.features = features
    
    await session.commit()
    await session.refresh(seq)
    
    return seq


@router.delete("/{sequence_id}/features/{feature_id}")
async def delete_feature(
    sequence_id: str,
    feature_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Delete a feature from a sequence."""
    result = await session.execute(
        select(NucleotideSequence).where(NucleotideSequence.id == sequence_id)
    )
    seq = result.scalar_one_or_none()
    
    if not seq:
        raise HTTPException(status_code=404, detail="Sequence not found")
    
    if seq.features:
        seq.features = [f for f in seq.features if f.get("id") != feature_id]
        await session.commit()
    
    return {"status": "deleted", "feature_id": feature_id}
