"""
Nucleotide Sequences API for BioDesigner.

Provides CRUD operations for DNA/RNA sequences with feature annotations.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, or_
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
    description: Optional[str] = None
    notes: Optional[dict] = None


class PrimerSchema(BaseModel):
    """Primer associated with a sequence."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    sequence: str
    sequence_type: Optional[str] = None
    start: int
    end: int
    strand: int = 1
    tm: Optional[float] = None
    gc_percent: Optional[float] = None
    tm_algorithm: Optional[str] = None
    tm_salt_correction: Optional[str] = None
    tm_settings: Optional[dict] = None


class AnalysisTrackSchema(BaseModel):
    """Analysis/evidence track aligned to a nucleotide sequence."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    kind: str = "custom"
    description: Optional[str] = None
    color: Optional[str] = None
    source_format: Optional[str] = None
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    normalization: Optional[str] = None
    values: List[Optional[float]] = Field(default_factory=list)
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    created_at: Optional[str] = None


class NucleotideSequenceCreate(BaseModel):
    """Schema for creating a new sequence."""
    name: str
    description: Optional[str] = None
    sequence: str
    sequence_type: str = "dna"
    is_circular: bool = False
    features: Optional[List[FeatureSchema]] = None
    primers: Optional[List[PrimerSchema]] = None
    analysis_tracks: Optional[List[AnalysisTrackSchema]] = None
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
    analysis_tracks: Optional[List[AnalysisTrackSchema]] = None
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
    analysis_tracks: Optional[List[Any]]
    organism: Optional[str]
    accession: Optional[str]
    source_file: Optional[str]
    gc_content: Optional[float]
    parent_id: Optional[str]
    operation: Optional[str]
    operation_params: Optional[dict]
    version: Optional[int]
    entity_kind: str
    topology: str
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
    organism: Optional[str]
    accession: Optional[str]
    source_file: Optional[str]
    entity_kind: str
    topology: str
    created_at: datetime
    updated_at: Optional[datetime]


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
    valid_chars = set("ATCGNRYMKSWHBVD") if seq_type == "dna" else set("AUCGNRYMKSWHBVD")
    cleaned = "".join(c for c in seq if c in valid_chars)
    return cleaned


def normalize_sequence_type(sequence_type: Optional[str], sequence: str) -> str:
    """Normalize a requested sequence type or infer one from sequence content."""
    normalized = (sequence_type or "").strip().lower()
    if normalized in {"dna", "rna"}:
        return normalized

    upper = sequence.upper()
    if "U" in upper and "T" not in upper:
        return "rna"
    return "dna"


def entity_kind_for(sequence_type: str, is_circular: bool) -> str:
    """Derive a UI-friendly construct kind without requiring a DB migration."""
    if sequence_type == "rna":
        return "circular_rna" if is_circular else "rna"
    return "plasmid" if is_circular else "dna"


def topology_for(is_circular: bool) -> str:
    return "circular" if is_circular else "linear"


def serialize_sequence(seq: NucleotideSequence) -> NucleotideSequenceResponse:
    """Serialize a DB sequence row with computed frontend-facing metadata."""
    return NucleotideSequenceResponse(
        id=seq.id,
        name=seq.name,
        description=seq.description,
        sequence=seq.sequence,
        sequence_type=seq.sequence_type,
        is_circular=seq.is_circular,
        length=seq.length,
        features=seq.features,
        primers=seq.primers,
        analysis_tracks=seq.analysis_tracks,
        organism=seq.organism,
        accession=seq.accession,
        source_file=seq.source_file,
        gc_content=seq.gc_content,
        parent_id=seq.parent_id,
        operation=seq.operation,
        operation_params=seq.operation_params,
        version=seq.version,
        entity_kind=entity_kind_for(seq.sequence_type, seq.is_circular),
        topology=topology_for(seq.is_circular),
        created_at=seq.created_at,
        updated_at=seq.updated_at,
    )


def normalize_analysis_tracks(
    tracks: Optional[List[AnalysisTrackSchema]],
    sequence_length: int,
) -> List[dict]:
    """Validate and serialize aligned analysis/evidence tracks."""
    if not tracks:
        return []

    normalized: List[dict] = []
    for track in tracks:
        payload = track.model_dump()
        values = payload.get("values") or []
        if len(values) != sequence_length:
            raise HTTPException(
                status_code=400,
                detail=f"Analysis track '{payload.get('name', 'unnamed')}' has {len(values)} values but sequence length is {sequence_length}",
            )

        numeric_values = [value for value in values if value is not None]
        if numeric_values:
            payload["min_value"] = min(numeric_values) if payload.get("min_value") is None else payload["min_value"]
            payload["max_value"] = max(numeric_values) if payload.get("max_value") is None else payload["max_value"]

        normalized.append(payload)

    return normalized


# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/", response_model=List[NucleotideSequenceListItem])
async def list_sequences(
    session: AsyncSession = Depends(get_session),
    search: Optional[str] = Query(None, description="Search by name, description, accession, organism, or source file"),
    sequence_type: Optional[str] = Query(None, description="Filter by polymer type: dna or rna"),
    topology: str = Query("all", description="Filter by topology: all, circular, linear"),
    sort_by: str = Query("updated_at", description="Sort by updated_at, created_at, name, length, gc_content, or feature_count"),
    sort_desc: bool = Query(True, description="Sort descending"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List all nucleotide sequences."""
    query = select(NucleotideSequence)

    normalized_type = sequence_type.strip().lower() if sequence_type else None
    if normalized_type in {"dna", "rna"}:
        query = query.where(NucleotideSequence.sequence_type == normalized_type)

    if topology == "circular":
        query = query.where(NucleotideSequence.is_circular.is_(True))
    elif topology == "linear":
        query = query.where(NucleotideSequence.is_circular.is_(False))

    if search:
        search_pattern = f"%{search.strip()}%"
        query = query.where(or_(
            NucleotideSequence.name.ilike(search_pattern),
            NucleotideSequence.description.ilike(search_pattern),
            NucleotideSequence.organism.ilike(search_pattern),
            NucleotideSequence.accession.ilike(search_pattern),
            NucleotideSequence.source_file.ilike(search_pattern),
        ))

    result = await session.execute(query)
    sequences = result.scalars().all()

    def sort_value(seq: NucleotideSequence):
        if sort_by == "name":
            return (seq.name or "").lower()
        if sort_by == "length":
            return seq.length or 0
        if sort_by == "gc_content":
            return seq.gc_content or 0.0
        if sort_by == "feature_count":
            return len(seq.features) if seq.features else 0
        if sort_by == "created_at":
            return seq.created_at or datetime.min
        return seq.updated_at or seq.created_at or datetime.min

    sequences = sorted(sequences, key=sort_value, reverse=sort_desc)
    paginated = sequences[offset:offset + limit]
    
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
            organism=seq.organism,
            accession=seq.accession,
            source_file=seq.source_file,
            entity_kind=entity_kind_for(seq.sequence_type, seq.is_circular),
            topology=topology_for(seq.is_circular),
            created_at=seq.created_at,
            updated_at=seq.updated_at,
        )
        for seq in paginated
    ]


@router.post("/", response_model=NucleotideSequenceResponse)
async def create_sequence(
    data: NucleotideSequenceCreate,
    session: AsyncSession = Depends(get_session)
):
    """Create a new nucleotide sequence."""
    # Clean and validate sequence
    normalized_type = normalize_sequence_type(data.sequence_type, data.sequence)
    cleaned_seq = clean_sequence(data.sequence, normalized_type)
    if not cleaned_seq:
        raise HTTPException(status_code=400, detail="Invalid sequence: no valid nucleotides found")
    
    # Create new sequence record
    seq_id = str(uuid.uuid4())
    seq = NucleotideSequence(
        id=seq_id,
        name=data.name,
        description=data.description,
        sequence=cleaned_seq,
        sequence_type=normalized_type,
        is_circular=data.is_circular,
        length=len(cleaned_seq),
        features=[f.model_dump() for f in data.features] if data.features else [],
        primers=[p.model_dump() for p in data.primers] if data.primers else [],
        analysis_tracks=normalize_analysis_tracks(data.analysis_tracks, len(cleaned_seq)),
        organism=data.organism,
        accession=data.accession,
        source_file=data.source_file,
        gc_content=calculate_gc_content(cleaned_seq)
    )
    
    session.add(seq)
    await session.commit()
    await session.refresh(seq)
    
    return serialize_sequence(seq)


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
    
    return serialize_sequence(seq)


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
    next_sequence_type = normalize_sequence_type(data.sequence_type or seq.sequence_type, data.sequence or seq.sequence)
    if data.sequence is not None:
        cleaned_seq = clean_sequence(data.sequence, next_sequence_type)
        seq.sequence = cleaned_seq
        seq.length = len(cleaned_seq)
        seq.gc_content = calculate_gc_content(cleaned_seq)
        if data.analysis_tracks is None:
            seq.analysis_tracks = []
    if data.sequence_type is not None:
        seq.sequence_type = next_sequence_type
    if data.is_circular is not None:
        seq.is_circular = data.is_circular
    if data.features is not None:
        seq.features = [f.model_dump() for f in data.features]
    if data.primers is not None:
        seq.primers = [p.model_dump() for p in data.primers]
    if data.analysis_tracks is not None:
        seq.analysis_tracks = normalize_analysis_tracks(data.analysis_tracks, seq.length)
    if data.organism is not None:
        seq.organism = data.organism
    if data.accession is not None:
        seq.accession = data.accession
    
    await session.commit()
    await session.refresh(seq)
    
    return serialize_sequence(seq)


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
    
    return serialize_sequence(seq)


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
