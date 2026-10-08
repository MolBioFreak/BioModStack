"""
Molecular biology operations API.
Provides digest, PCR, ligation, mutagenesis, Gibson, and Golden Gate workflows.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
import uuid

from database import NucleotideSequence, get_session
from services.molbio_ops import (
    DigestEnzyme,
    digest_sequence,
    pcr_product,
    ligate_fragments,
    apply_mutations,
    gibson_assembly,
    golden_gate_assembly,
)


router = APIRouter(prefix="/api/molbio", tags=["molbio"])


class SequenceInput(BaseModel):
    sequence_id: Optional[str] = None
    name: Optional[str] = None
    sequence: Optional[str] = None
    is_circular: bool = False


class EnzymeSchema(BaseModel):
    name: str
    site: str
    cut_index: Optional[int] = None


class DigestRequest(SequenceInput):
    enzymes: List[EnzymeSchema]
    save: bool = False
    new_name: Optional[str] = None


class PCRRequest(SequenceInput):
    primer_fwd: str
    primer_rev: str
    save: bool = True
    new_name: Optional[str] = None


class LigationRequest(BaseModel):
    fragments: List[str]
    circular: bool = True
    parent_id: Optional[str] = None
    save: bool = True
    new_name: Optional[str] = None


class MutationSchema(BaseModel):
    pos: int
    to: str
    from_base: Optional[str] = Field(default=None, alias="from")

    class Config:
        populate_by_name = True


class MutagenesisRequest(SequenceInput):
    mutations: List[MutationSchema]
    save: bool = True
    new_name: Optional[str] = None


class GibsonRequest(BaseModel):
    fragments: List[str]
    overlap_length: int = 20
    circular: bool = True
    parent_id: Optional[str] = None
    save: bool = True
    new_name: Optional[str] = None


class GoldenGateRequest(BaseModel):
    fragments: List[str]
    enzymes: List[EnzymeSchema]
    circular: bool = True
    parent_id: Optional[str] = None
    save: bool = True
    new_name: Optional[str] = None


class NucleotideSequenceResponse(BaseModel):
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
    version: int
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class DigestFragmentResponse(BaseModel):
    sequence: str
    start: int
    end: int


class MolbioOperationResponse(BaseModel):
    sequence: Optional[NucleotideSequenceResponse] = None
    fragments: Optional[List[DigestFragmentResponse]] = None
    message: str


async def resolve_sequence(data: SequenceInput, session: AsyncSession) -> NucleotideSequence:
    if data.sequence_id:
        result = await session.execute(
            select(NucleotideSequence).where(NucleotideSequence.id == data.sequence_id)
        )
        seq = result.scalar_one_or_none()
        if not seq:
            raise HTTPException(status_code=404, detail="Sequence not found")
        return seq
    if not data.sequence:
        raise HTTPException(status_code=400, detail="Sequence or sequence_id is required")
    seq_clean = data.sequence
    gc = 0.0
    if seq_clean:
        gc = round(((seq_clean.count('G') + seq_clean.count('C')) / len(seq_clean)) * 100, 2)
    # Construct a temporary sequence object
    return NucleotideSequence(
        id=str(uuid.uuid4()),
        name=data.name or "Unnamed Sequence",
        description=None,
        sequence=data.sequence,
        sequence_type="dna",
        is_circular=data.is_circular,
        length=len(data.sequence),
        features=[],
        primers=[],
        organism=None,
        accession=None,
        source_file=None,
        gc_content=gc,
        parent_id=None,
        operation=None,
        operation_params=None,
        version=1,
    )


def create_child_sequence(
    parent: Optional[NucleotideSequence],
    sequence: str,
    name: str,
    circular: bool,
    operation: str,
    operation_params: dict,
) -> NucleotideSequence:
    def calc_gc(seq: str) -> float:
        if not seq:
            return 0.0
        gc = seq.count('G') + seq.count('C')
        return round((gc / len(seq)) * 100, 2)

    parent_id = parent.id if parent else None
    version = (parent.version + 1) if parent and parent.version else 1
    return NucleotideSequence(
        id=str(uuid.uuid4()),
        name=name,
        description=None,
        sequence=sequence,
        sequence_type="dna",
        is_circular=circular,
        length=len(sequence),
        features=[],
        primers=[],
        organism=parent.organism if parent else None,
        accession=None,
        source_file=None,
        gc_content=calc_gc(sequence),
        parent_id=parent_id,
        operation=operation,
        operation_params=operation_params,
        version=version,
    )


@router.post("/digest", response_model=MolbioOperationResponse)
async def digest(
    request: DigestRequest,
    session: AsyncSession = Depends(get_session)
):
    parent = await resolve_sequence(request, session)
    enzymes = [DigestEnzyme(name=e.name, site=e.site, cut_index=e.cut_index) for e in request.enzymes]
    fragments = digest_sequence(parent.sequence, enzymes, circular=parent.is_circular)
    fragment_payload = [DigestFragmentResponse(sequence=f.sequence, start=f.start, end=f.end) for f in fragments]

    if not request.save:
        return MolbioOperationResponse(
            fragments=fragment_payload,
            message=f"Digest produced {len(fragment_payload)} fragments"
        )

    new_name = request.new_name or f"{parent.name}_digest"
    seq_obj = create_child_sequence(
        parent=parent if request.sequence_id else None,
        sequence="".join(f.sequence for f in fragments),
        name=new_name,
        circular=False,
        operation="digest",
        operation_params={"enzymes": [e.dict() for e in request.enzymes]},
    )
    session.add(seq_obj)
    await session.commit()
    await session.refresh(seq_obj)
    return MolbioOperationResponse(
        sequence=seq_obj,
        fragments=fragment_payload,
        message="Digest complete"
    )


@router.post("/pcr", response_model=MolbioOperationResponse)
async def pcr(
    request: PCRRequest,
    session: AsyncSession = Depends(get_session)
):
    parent = await resolve_sequence(request, session)
    product = pcr_product(parent.sequence, request.primer_fwd, request.primer_rev)
    if not request.save:
        return MolbioOperationResponse(message="PCR complete")

    new_name = request.new_name or f"{parent.name}_PCR"
    seq_obj = create_child_sequence(
        parent=parent if request.sequence_id else None,
        sequence=product,
        name=new_name,
        circular=False,
        operation="pcr",
        operation_params={"primer_fwd": request.primer_fwd, "primer_rev": request.primer_rev},
    )
    session.add(seq_obj)
    await session.commit()
    await session.refresh(seq_obj)
    return MolbioOperationResponse(sequence=seq_obj, message="PCR complete")


@router.post("/ligate", response_model=MolbioOperationResponse)
async def ligate(
    request: LigationRequest,
    session: AsyncSession = Depends(get_session)
):
    parent = None
    if request.parent_id:
        result = await session.execute(
            select(NucleotideSequence).where(NucleotideSequence.id == request.parent_id)
        )
        parent = result.scalar_one_or_none()

    ligated = ligate_fragments(request.fragments, circular=request.circular)
    if not request.save:
        return MolbioOperationResponse(message="Ligation complete")

    new_name = request.new_name or "Ligation_Product"
    seq_obj = create_child_sequence(
        parent=parent,
        sequence=ligated,
        name=new_name,
        circular=request.circular,
        operation="ligate",
        operation_params={"fragment_count": len(request.fragments)},
    )
    session.add(seq_obj)
    await session.commit()
    await session.refresh(seq_obj)
    return MolbioOperationResponse(sequence=seq_obj, message="Ligation complete")


@router.post("/mutagenesis", response_model=MolbioOperationResponse)
async def mutagenesis(
    request: MutagenesisRequest,
    session: AsyncSession = Depends(get_session)
):
    parent = await resolve_sequence(request, session)
    mutations = [
        {"pos": m.pos, "from": m.from_base, "to": m.to} for m in request.mutations
    ]
    mutated = apply_mutations(parent.sequence, mutations)
    if not request.save:
        return MolbioOperationResponse(message="Mutagenesis complete")

    new_name = request.new_name or f"{parent.name}_mut"
    seq_obj = create_child_sequence(
        parent=parent if request.sequence_id else None,
        sequence=mutated,
        name=new_name,
        circular=parent.is_circular,
        operation="mutagenesis",
        operation_params={"mutations": mutations},
    )
    session.add(seq_obj)
    await session.commit()
    await session.refresh(seq_obj)
    return MolbioOperationResponse(sequence=seq_obj, message="Mutagenesis complete")


@router.post("/gibson", response_model=MolbioOperationResponse)
async def gibson(
    request: GibsonRequest,
    session: AsyncSession = Depends(get_session)
):
    parent = None
    if request.parent_id:
        result = await session.execute(
            select(NucleotideSequence).where(NucleotideSequence.id == request.parent_id)
        )
        parent = result.scalar_one_or_none()

    assembled = gibson_assembly(request.fragments, overlap_length=request.overlap_length)
    if not request.save:
        return MolbioOperationResponse(message="Gibson assembly complete")

    new_name = request.new_name or "Gibson_Assembly"
    seq_obj = create_child_sequence(
        parent=parent,
        sequence=assembled,
        name=new_name,
        circular=request.circular,
        operation="gibson",
        operation_params={"overlap_length": request.overlap_length, "fragment_count": len(request.fragments)},
    )
    session.add(seq_obj)
    await session.commit()
    await session.refresh(seq_obj)
    return MolbioOperationResponse(sequence=seq_obj, message="Gibson assembly complete")


@router.post("/golden-gate", response_model=MolbioOperationResponse)
async def golden_gate(
    request: GoldenGateRequest,
    session: AsyncSession = Depends(get_session)
):
    parent = None
    if request.parent_id:
        result = await session.execute(
            select(NucleotideSequence).where(NucleotideSequence.id == request.parent_id)
        )
        parent = result.scalar_one_or_none()

    enzymes = [DigestEnzyme(name=e.name, site=e.site, cut_index=e.cut_index) for e in request.enzymes]
    assembled = golden_gate_assembly(request.fragments, enzymes)
    if not request.save:
        return MolbioOperationResponse(message="Golden Gate assembly complete")

    new_name = request.new_name or "GoldenGate_Assembly"
    seq_obj = create_child_sequence(
        parent=parent,
        sequence=assembled,
        name=new_name,
        circular=request.circular,
        operation="golden_gate",
        operation_params={"enzymes": [e.dict() for e in request.enzymes], "fragment_count": len(request.fragments)},
    )
    session.add(seq_obj)
    await session.commit()
    await session.refresh(seq_obj)
    return MolbioOperationResponse(sequence=seq_obj, message="Golden Gate assembly complete")


# ============================================================================
# Auto-Annotation using pLannotate
# ============================================================================

class AutoAnnotateRequest(BaseModel):
    """Request for automatic feature detection using pLannotate."""
    sequence: str = Field(..., description="DNA sequence to annotate")
    is_linear: bool = Field(False, description="Whether the sequence is linear (default: circular)")
    detailed: bool = Field(False, description="Use detailed search mode (more hits, more false positives)")
    min_identity: float = Field(50.0, description="Minimum percent identity threshold for features")


class DetectedFeature(BaseModel):
    """A feature detected by pLannotate."""
    name: str
    type: str
    start: int
    end: int
    strand: int  # 1 or -1
    identity_pct: float
    match_length_pct: float
    is_fragment: bool
    database: str
    description: str


class AutoAnnotateResponse(BaseModel):
    """Response from auto-annotation."""
    features: List[DetectedFeature]
    message: str


@router.post("/auto-annotate", response_model=AutoAnnotateResponse)
async def auto_annotate(request: AutoAnnotateRequest):
    """
    Auto-detect plasmid features using pLannotate.
    
    Uses BLAST-based detection to identify common plasmid components like:
    - Origins of replication (ori, ColE1, etc.)
    - Antibiotic resistance genes (KanR, AmpR, CmR, etc.)
    - Promoters and terminators
    - Common tags and reporters
    
    Requires pLannotate to be installed via micromamba.
    """
    import subprocess
    import tempfile
    import csv
    import os
    
    # Validate sequence
    sequence = request.sequence.upper().replace(" ", "").replace("\n", "")
    if not sequence:
        raise HTTPException(status_code=400, detail="Empty sequence provided")
    
    if not all(c in "ATGCNRYSWKMBDHV" for c in sequence):
        raise HTTPException(status_code=400, detail="Invalid DNA sequence characters")
    
    # Create temporary files for input/output
    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = os.path.join(tmpdir, "input.fasta")
        output_dir = tmpdir
        
        # Write input FASTA
        with open(input_file, "w") as f:
            f.write(">input_sequence\n")
            # Write sequence in 60-char lines
            for i in range(0, len(sequence), 60):
                f.write(sequence[i:i+60] + "\n")
        
        # Build plannotate command
        cmd = [
            "/home/dalab/bin/micromamba",
            "run", "-n", "plannotate", "--root-prefix", "/home/dalab/micromamba",
            "plannotate", "batch",
            "-i", input_file,
            "-o", output_dir,
            "--csv"
        ]
        
        if request.is_linear:
            cmd.append("-l")  # --linear flag
        
        if request.detailed:
            cmd.append("-d")  # --detailed flag
        
        # Run pLannotate
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120  # 2 minute timeout
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="pLannotate timed out")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"pLannotate execution failed: {str(e)}")
        
        # Find and parse CSV output
        csv_files = [f for f in os.listdir(output_dir) if f.endswith("_pLann.csv")]
        if not csv_files:
            # pLannotate ran but found no features
            return AutoAnnotateResponse(features=[], message="No features detected")
        
        csv_path = os.path.join(output_dir, csv_files[0])
        features = []
        
        try:
            with open(csv_path, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    identity = float(row.get("percent identity", 0))
                    
                    # Skip features below identity threshold
                    if identity < request.min_identity:
                        continue
                    
                    strand_str = row.get("strand", "+")
                    strand = 1 if strand_str == "1" or strand_str == "+" else -1
                    
                    is_fragment = row.get("fragment", "False").lower() == "true"
                    
                    features.append(DetectedFeature(
                        name=row.get("Feature", "Unknown"),
                        type=row.get("Type", "misc_feature"),
                        start=int(row.get("start location", 0)),
                        end=int(row.get("end location", 0)),
                        strand=strand,
                        identity_pct=identity,
                        match_length_pct=float(row.get("percent match length", 0)),
                        is_fragment=is_fragment,
                        database=row.get("database", "unknown"),
                        description=row.get("Description", "")[:500]  # Truncate long descriptions
                    ))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to parse pLannotate output: {str(e)}")
        
        # Sort by start position
        features.sort(key=lambda f: f.start)
        
        return AutoAnnotateResponse(
            features=features,
            message=f"Detected {len(features)} features"
        )

