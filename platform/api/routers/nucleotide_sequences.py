"""
Nucleotide Sequences API for BioDesigner.

Provides CRUD operations for DNA/RNA sequences with feature annotations.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from datetime import datetime, timezone
import uuid

from molbio_database import get_molbio_session
from molbio_models import NucleotideSequence
from services.molbio_persistence import (
    begin_immediate_molbio_write,
    record_sequence_deletion,
    record_sequence_revision,
)


router = APIRouter(prefix="/api/sequences", tags=["sequences"])


def _sortable_timestamp(value: Optional[datetime]) -> datetime:
    """Return one UTC-aware value so mixed legacy timestamps remain sortable."""
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class FeatureSchema(BaseModel):
    """Feature annotation on a sequence."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    type: str = "misc_feature"
    start: Optional[int] = None
    end: Optional[int] = None
    strand: int = 1  # 1 for forward, -1 for reverse
    color: Optional[str] = None
    description: Optional[str] = None
    notes: Optional[dict] = None
    qualifiers: Optional[dict] = None
    provenance: Optional[dict] = None
    segments: Optional[List[dict[str, int]]] = None


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
    notes: Optional[dict] = None
    provenance: Optional[dict] = None
    sites: Optional[List[dict[str, Any]]] = None


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
    molecule_strandedness: Optional[str] = None
    molecule_orientation: Optional[str] = None
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
    molecule_strandedness: Optional[str] = None
    molecule_orientation: Optional[str] = None
    is_circular: Optional[bool] = None
    features: Optional[List[FeatureSchema]] = None
    primers: Optional[List[PrimerSchema]] = None
    analysis_tracks: Optional[List[AnalysisTrackSchema]] = None
    organism: Optional[str] = None
    accession: Optional[str] = None
    source_file: Optional[str] = None


class NucleotideSequenceResponse(BaseModel):
    """Schema for sequence response."""
    id: str
    name: str
    description: Optional[str]
    sequence: str
    sequence_type: str
    molecule_strandedness: str
    molecule_orientation: str
    molecule_label: str
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

    model_config = ConfigDict(from_attributes=True)


class NucleotideSequenceListItem(BaseModel):
    """Schema for sequence list response (lighter)."""
    id: str
    name: str
    description: Optional[str]
    sequence_type: str
    molecule_strandedness: str
    molecule_orientation: str
    molecule_label: str
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
    """Clean, canonicalize, and validate a DNA/RNA nucleotide sequence."""
    seq = sequence.upper().replace(" ", "").replace("\n", "").replace("\r", "")
    if seq_type == "rna":
        seq = seq.replace("T", "U")
        valid_chars = set("AUCGNRYMKSWHBVD")
    else:
        seq = seq.replace("U", "T")
        valid_chars = set("ATCGNRYMKSWHBVD")
    cleaned = "".join(c for c in seq if c in valid_chars)
    return cleaned


def normalize_sequence_type(sequence_type: Optional[str], sequence: str) -> str:
    """Normalize a requested sequence type or infer one from sequence content."""
    normalized = (sequence_type or "").strip().lower()
    if normalized in {"dna", "rna"}:
        return normalized
    if "rna" in normalized:
        return "rna"
    if "dna" in normalized:
        return "dna"

    upper = sequence.upper()
    if "U" in upper and "T" not in upper:
        return "rna"
    return "dna"


def normalize_molecule_strandedness(strandedness: Optional[str], sequence_type: str) -> str:
    """Normalize molecule strandedness, preserving explicit ss/ds metadata when present."""
    token = (strandedness or "").strip().lower().replace("_", "-").replace(" ", "-")
    if token in {
        "ss",
        "single",
        "single-stranded",
        "single-strand",
        "single-stranded-dna",
        "single-stranded-rna",
        "ss-dna",
        "ss-rna",
        "ssdna",
        "ssrna",
    } or "single-strand" in token or "ssrna" in token or "ssdna" in token or "ss-rna" in token or "ss-dna" in token:
        return "single"
    if token in {
        "ds",
        "double",
        "double-stranded",
        "double-strand",
        "double-stranded-dna",
        "double-stranded-rna",
        "ds-dna",
        "ds-rna",
        "dsdna",
        "dsrna",
    } or "double-strand" in token or "dsrna" in token or "dsdna" in token or "ds-rna" in token or "ds-dna" in token:
        return "double"
    if token in {"unknown", "not-known", "na", "n/a"}:
        return "unknown"

    # Legacy imports did not carry strandedness. These defaults match the common
    # construct assumptions while still allowing ssDNA/dsRNA to be explicit.
    return "single" if sequence_type == "rna" else "double"


def normalize_molecule_orientation(orientation: Optional[str], strandedness: str) -> str:
    """Normalize single-strand sense/orientation metadata."""
    if strandedness == "double":
        return "not_applicable"

    token = (orientation or "").strip().lower().replace("_", "-").replace(" ", "-")
    if token in {"+", "plus", "positive", "positive-sense", "positive-strand", "plus-sense", "plus-strand", "+sense", "sense", "coding"}:
        return "positive"
    if token in {"-", "minus", "negative", "negative-sense", "negative-strand", "minus-sense", "minus-strand", "-sense", "antisense", "anti-sense"}:
        return "negative"
    if token in {"ambisense", "ambi-sense", "+/-", "-/+"}:
        return "ambisense"
    if token in {"not-applicable", "not_applicable", "n/a", "na"}:
        return "not_applicable"
    return "unknown"


def molecule_label_for(sequence_type: str, strandedness: str, orientation: str) -> str:
    polymer = "RNA" if sequence_type == "rna" else "DNA"
    if strandedness == "double":
        return f"ds{polymer}"
    if strandedness == "single":
        if orientation == "positive":
            return f"(+)ss{polymer}"
        if orientation == "negative":
            return f"(-)ss{polymer}"
        if orientation == "ambisense":
            return f"ambisense ss{polymer}"
        return f"ss{polymer}"
    return polymer


def entity_kind_for(sequence_type: str, is_circular: bool, strandedness: str = "unknown", orientation: str = "unknown") -> str:
    """Derive a UI-friendly construct kind without requiring consumers to infer labels."""
    if sequence_type == "dna" and is_circular and strandedness in {"unknown", "double"}:
        return "plasmid"
    label = molecule_label_for(sequence_type, strandedness, orientation).lower()
    label = label.replace("(+)", "positive_").replace("(-)", "negative_").replace(" ", "_")
    return f"circular_{label}" if is_circular else label


def topology_for(is_circular: bool) -> str:
    return "circular" if is_circular else "linear"


def serialize_sequence(seq: NucleotideSequence) -> NucleotideSequenceResponse:
    """Serialize a DB sequence row with computed frontend-facing metadata."""
    strandedness = normalize_molecule_strandedness(getattr(seq, "molecule_strandedness", None), seq.sequence_type)
    orientation = normalize_molecule_orientation(getattr(seq, "molecule_orientation", None), strandedness)
    molecule_label = molecule_label_for(seq.sequence_type, strandedness, orientation)
    return NucleotideSequenceResponse(
        id=seq.id,
        name=seq.name,
        description=seq.description,
        sequence=seq.sequence,
        sequence_type=seq.sequence_type,
        molecule_strandedness=strandedness,
        molecule_orientation=orientation,
        molecule_label=molecule_label,
        is_circular=seq.is_circular,
        length=seq.length,
        features=normalize_feature_payloads(seq.features, seq.length),
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
        entity_kind=entity_kind_for(seq.sequence_type, seq.is_circular, strandedness, orientation),
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


def normalize_feature_payloads(
    features: Optional[List[Any]],
    sequence_length: Optional[int] = None,
) -> List[dict]:
    if not features:
        return []

    normalized: List[dict] = []
    for raw_feature in features:
        payload = raw_feature.model_dump() if isinstance(raw_feature, BaseModel) else dict(raw_feature)
        segments = payload.get("segments") or []
        if not segments:
            start = payload.get("start")
            end = payload.get("end")
            if start is None or end is None:
                raise HTTPException(status_code=400, detail=f"Feature '{payload.get('name', 'unnamed')}' is missing both segments and start/end coordinates")
            segments = [{"start": int(start), "end": int(end)}]

        normalized_segments: List[dict[str, int]] = []
        for segment in segments:
            start = int(segment.get("start"))
            end = int(segment.get("end"))
            if end <= start:
                raise HTTPException(
                    status_code=400,
                    detail=f"Feature '{payload.get('name', 'unnamed')}' contains an invalid segment {start}-{end}",
                )
            if sequence_length is not None and (start < 0 or end > sequence_length):
                raise HTTPException(
                    status_code=400,
                    detail=f"Feature '{payload.get('name', 'unnamed')}' segment {start}-{end} exceeds sequence length {sequence_length}",
                )
            normalized_segments.append({"start": start, "end": end})

        normalized_segments.sort(key=lambda item: (item["start"], item["end"]))
        payload["segments"] = normalized_segments
        payload["start"] = min(segment["start"] for segment in normalized_segments)
        payload["end"] = max(segment["end"] for segment in normalized_segments)
        payload["strand"] = -1 if int(payload.get("strand", 1)) == -1 else 1
        if payload.get("qualifiers") is None and payload.get("notes") is not None:
            payload["qualifiers"] = payload["notes"]
        if payload.get("notes") is None and payload.get("qualifiers") is not None:
            payload["notes"] = payload["qualifiers"]
        normalized.append(payload)

    return normalized


# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/", response_model=List[NucleotideSequenceListItem])
async def list_sequences(
    session: AsyncSession = Depends(get_molbio_session),
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
            return _sortable_timestamp(seq.created_at)
        return _sortable_timestamp(seq.updated_at or seq.created_at)

    sequences = sorted(sequences, key=sort_value, reverse=sort_desc)
    paginated = sequences[offset:offset + limit]
    
    return [
        NucleotideSequenceListItem(
            id=seq.id,
            name=seq.name,
            description=seq.description,
            sequence_type=seq.sequence_type,
            molecule_strandedness=(strandedness := normalize_molecule_strandedness(getattr(seq, "molecule_strandedness", None), seq.sequence_type)),
            molecule_orientation=(orientation := normalize_molecule_orientation(getattr(seq, "molecule_orientation", None), strandedness)),
            molecule_label=molecule_label_for(seq.sequence_type, strandedness, orientation),
            is_circular=seq.is_circular,
            length=seq.length,
            gc_content=seq.gc_content,
            feature_count=len(seq.features) if seq.features else 0,
            organism=seq.organism,
            accession=seq.accession,
            source_file=seq.source_file,
            entity_kind=entity_kind_for(seq.sequence_type, seq.is_circular, strandedness, orientation),
            topology=topology_for(seq.is_circular),
            created_at=seq.created_at,
            updated_at=seq.updated_at,
        )
        for seq in paginated
    ]


@router.post("/", response_model=NucleotideSequenceResponse)
async def create_sequence(
    data: NucleotideSequenceCreate,
    session: AsyncSession = Depends(get_molbio_session)
):
    """Create a new nucleotide sequence."""
    # Clean and validate sequence
    normalized_type = normalize_sequence_type(data.sequence_type, data.sequence)
    cleaned_seq = clean_sequence(data.sequence, normalized_type)
    if not cleaned_seq:
        raise HTTPException(status_code=400, detail="Invalid sequence: no valid nucleotides found")
    
    # Create new sequence record
    strandedness = normalize_molecule_strandedness(data.molecule_strandedness, normalized_type)
    orientation = normalize_molecule_orientation(data.molecule_orientation, strandedness)
    seq_id = str(uuid.uuid4())
    seq = NucleotideSequence(
        id=seq_id,
        name=data.name,
        description=data.description,
        sequence=cleaned_seq,
        sequence_type=normalized_type,
        molecule_strandedness=strandedness,
        molecule_orientation=orientation,
        is_circular=data.is_circular,
        length=len(cleaned_seq),
        features=normalize_feature_payloads(data.features, len(cleaned_seq)),
        primers=[p.model_dump() for p in data.primers] if data.primers else [],
        analysis_tracks=normalize_analysis_tracks(data.analysis_tracks, len(cleaned_seq)),
        organism=data.organism,
        accession=data.accession,
        source_file=data.source_file,
        gc_content=calculate_gc_content(cleaned_seq)
    )
    
    session.add(seq)
    await record_sequence_revision(
        session,
        seq,
        change_kind="create",
        provenance={"source": "api", "endpoint": "POST /api/sequences/"},
    )
    await session.commit()
    await session.refresh(seq)
    
    return serialize_sequence(seq)


@router.get("/{sequence_id}", response_model=NucleotideSequenceResponse)
async def get_sequence(
    sequence_id: str,
    session: AsyncSession = Depends(get_molbio_session)
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
    session: AsyncSession = Depends(get_molbio_session)
):
    """Update an existing sequence."""
    await begin_immediate_molbio_write(session)
    result = await session.execute(
        select(NucleotideSequence).where(NucleotideSequence.id == sequence_id)
    )
    seq = result.scalar_one_or_none()
    
    if not seq:
        raise HTTPException(status_code=404, detail="Sequence not found")

    changed = False

    # Update fields if provided
    if data.name is not None:
        seq.name = data.name
        changed = True
    if data.description is not None:
        seq.description = data.description
        changed = True
    next_sequence_type = normalize_sequence_type(data.sequence_type or seq.sequence_type, data.sequence or seq.sequence)
    if data.sequence is not None:
        cleaned_seq = clean_sequence(data.sequence, next_sequence_type)
        seq.sequence = cleaned_seq
        seq.length = len(cleaned_seq)
        seq.gc_content = calculate_gc_content(cleaned_seq)
        if data.analysis_tracks is None:
            seq.analysis_tracks = []
        changed = True
    if data.sequence_type is not None:
        seq.sequence_type = next_sequence_type
        changed = True

    if data.molecule_strandedness is not None:
        seq.molecule_strandedness = normalize_molecule_strandedness(data.molecule_strandedness, next_sequence_type)
        changed = True
    elif getattr(seq, "molecule_strandedness", None) in {None, ""}:
        seq.molecule_strandedness = normalize_molecule_strandedness(None, next_sequence_type)
        changed = True

    if data.molecule_orientation is not None or data.molecule_strandedness is not None:
        seq.molecule_orientation = normalize_molecule_orientation(
            data.molecule_orientation if data.molecule_orientation is not None else getattr(seq, "molecule_orientation", None),
            normalize_molecule_strandedness(getattr(seq, "molecule_strandedness", None), next_sequence_type),
        )
        changed = True
    elif getattr(seq, "molecule_orientation", None) in {None, ""}:
        seq.molecule_orientation = normalize_molecule_orientation(None, normalize_molecule_strandedness(getattr(seq, "molecule_strandedness", None), next_sequence_type))
        changed = True

    if data.is_circular is not None:
        seq.is_circular = data.is_circular
        changed = True
    if data.features is not None:
        seq.features = normalize_feature_payloads(data.features, seq.length)
        changed = True
    if data.primers is not None:
        seq.primers = [p.model_dump() for p in data.primers]
        changed = True
    if data.analysis_tracks is not None:
        seq.analysis_tracks = normalize_analysis_tracks(data.analysis_tracks, seq.length)
        changed = True
    if data.organism is not None:
        seq.organism = data.organism
        changed = True
    if data.accession is not None:
        seq.accession = data.accession
        changed = True
    if data.source_file is not None:
        seq.source_file = data.source_file
        changed = True

    if changed:
        seq.version = (seq.version or 1) + 1
        seq.updated_at = datetime.utcnow()
        await record_sequence_revision(
            session,
            seq,
            change_kind="update",
            provenance={"source": "api", "endpoint": "PUT /api/sequences/{sequence_id}"},
        )

    await session.commit()
    await session.refresh(seq)
    
    return serialize_sequence(seq)


@router.delete("/{sequence_id}")
async def delete_sequence(
    sequence_id: str,
    session: AsyncSession = Depends(get_molbio_session)
):
    """Delete a sequence."""
    await begin_immediate_molbio_write(session)
    result = await session.execute(
        select(NucleotideSequence).where(NucleotideSequence.id == sequence_id)
    )
    seq = result.scalar_one_or_none()
    
    if not seq:
        raise HTTPException(status_code=404, detail="Sequence not found")
    
    await record_sequence_deletion(
        session,
        seq,
        provenance={"source": "api", "endpoint": "DELETE /api/sequences/{sequence_id}"},
    )
    await session.delete(seq)
    await session.commit()
    
    return {"status": "deleted", "id": sequence_id}


@router.post("/{sequence_id}/features", response_model=NucleotideSequenceResponse)
async def add_feature(
    sequence_id: str,
    feature: FeatureSchema,
    session: AsyncSession = Depends(get_molbio_session)
):
    """Add a feature to a sequence."""
    await begin_immediate_molbio_write(session)
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
    features.append(feature)
    seq.features = normalize_feature_payloads(features, seq.length)
    seq.version = (seq.version or 1) + 1
    seq.updated_at = datetime.utcnow()
    await record_sequence_revision(
        session,
        seq,
        change_kind="feature_add",
        provenance={"source": "api", "feature_id": feature.id},
    )

    await session.commit()
    await session.refresh(seq)
    
    return serialize_sequence(seq)


@router.delete("/{sequence_id}/features/{feature_id}")
async def delete_feature(
    sequence_id: str,
    feature_id: str,
    session: AsyncSession = Depends(get_molbio_session)
):
    """Delete a feature from a sequence."""
    await begin_immediate_molbio_write(session)
    result = await session.execute(
        select(NucleotideSequence).where(NucleotideSequence.id == sequence_id)
    )
    seq = result.scalar_one_or_none()
    
    if not seq:
        raise HTTPException(status_code=404, detail="Sequence not found")
    
    if seq.features:
        previous_count = len(seq.features)
        seq.features = [f for f in seq.features if f.get("id") != feature_id]
        if len(seq.features) != previous_count:
            seq.version = (seq.version or 1) + 1
            seq.updated_at = datetime.utcnow()
            await record_sequence_revision(
                session,
                seq,
                change_kind="feature_delete",
                provenance={"source": "api", "feature_id": feature_id},
            )
            await session.commit()
    
    return {"status": "deleted", "feature_id": feature_id}
