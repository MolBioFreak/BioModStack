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
from Bio.SeqUtils import MeltingTemp as mt

from database import NucleotideSequence, get_session
from services.assembly.common import fragment_provenance_payload
from services.assembly.gibson import simulate_gibson
from services.assembly.golden_gate import TYPE_IIS_ENZYMES, get_type_iis_enzyme, simulate_golden_gate
from services.assembly.ligation import simulate_ligation
from services.assembly.types import (
    AssemblyError,
    AssemblyFragment,
    AssemblyJunction,
    FragmentEnd,
)
from services.molbio_ops import (
    DigestEnzyme,
    clean_sequence,
    digest_sequence,
    pcr_product,
    apply_mutations,
    reverse_complement,
)
from services.primer_qc import evaluate_primer_pair_qc, evaluate_primer_qc
from services.sequence_alignment import (
    AlignmentSettings,
    SequenceAlignmentError,
    align_sequences,
)


router = APIRouter(prefix="/api/molbio", tags=["molbio"])


class SequenceInput(BaseModel):
    sequence_id: Optional[str] = None
    name: Optional[str] = None
    sequence: Optional[str] = None
    sequence_type: Optional[str] = None
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
    length: int
    wraps_origin: bool


class PCRProductResponse(BaseModel):
    sequence: str
    start: int
    end: int
    length: int
    wraps_origin: bool


class MolbioOperationResponse(BaseModel):
    sequence: Optional[NucleotideSequenceResponse] = None
    fragments: Optional[List[DigestFragmentResponse]] = None
    product: Optional[PCRProductResponse] = None
    message: str


class AssemblyFragmentEndSchema(BaseModel):
    type: str
    overhang: str = ""
    label: Optional[str] = None


class AssemblyFragmentSchema(BaseModel):
    id: str
    name: str
    sequence: str
    orientation: str = "forward"
    circular: bool = False
    role: Optional[str] = None
    source_sequence_id: Optional[str] = None
    source_name: Optional[str] = None
    source_start: Optional[int] = None
    source_end: Optional[int] = None
    source_wraps_origin: bool = False
    left_end: Optional[AssemblyFragmentEndSchema] = None
    right_end: Optional[AssemblyFragmentEndSchema] = None
    metadata: Optional[dict[str, Any]] = None


class AssemblyFragmentResponse(BaseModel):
    id: str
    name: str
    orientation: str
    role: Optional[str] = None
    source_sequence_id: Optional[str] = None
    source_name: Optional[str] = None
    source_start: Optional[int] = None
    source_end: Optional[int] = None
    source_wraps_origin: bool = False
    left_end: Optional[AssemblyFragmentEndSchema] = None
    right_end: Optional[AssemblyFragmentEndSchema] = None
    metadata: Optional[dict[str, Any]] = None


class AssemblyJunctionResponse(BaseModel):
    left_fragment_id: str
    right_fragment_id: str
    left_fragment_name: str
    right_fragment_name: str
    mode: str
    left_end_type: Optional[str] = None
    right_end_type: Optional[str] = None
    overhang_sequence: Optional[str] = None
    overlap_sequence: Optional[str] = None
    overlap_length: int = 0
    junction_sequence: str
    validation: str = "validated"
    notes: List[str] = Field(default_factory=list)


class AssemblyProductResponse(BaseModel):
    sequence: str
    circular: bool
    length: int
    mode: str
    fragments: List[AssemblyFragmentResponse]
    junctions: List[AssemblyJunctionResponse]
    warnings: List[str] = Field(default_factory=list)
    validation_notes: List[str] = Field(default_factory=list)


class AssemblyOperationResponse(BaseModel):
    product: AssemblyProductResponse
    saved_sequence: Optional[NucleotideSequenceResponse] = None
    message: str


class LigationAssemblyRequest(BaseModel):
    fragments: List[AssemblyFragmentSchema]
    circular: bool = True
    new_name: Optional[str] = None
    save_description: Optional[str] = None


class GibsonAssemblyRequest(BaseModel):
    fragments: List[AssemblyFragmentSchema]
    circular: bool = True
    minimum_overlap: int = 20
    preferred_overlap: Optional[int] = 28
    maximum_overlap: Optional[int] = 80
    new_name: Optional[str] = None
    save_description: Optional[str] = None


class GoldenGateAssemblyRequest(BaseModel):
    fragments: List[AssemblyFragmentSchema]
    circular: bool = True
    enzyme_name: str = "BsaI"
    new_name: Optional[str] = None
    save_description: Optional[str] = None


class AlignmentSettingsSchema(BaseModel):
    mode: str = "placement"
    strand: str = "auto"
    reference_is_circular: bool = False
    match_score: float = 2.0
    mismatch_score: float = -1.0
    gap_open_score: float = -6.0
    gap_extend_score: float = -1.0


class SequenceAlignmentRequest(BaseModel):
    reference_name: Optional[str] = None
    reference_sequence: str
    query_name: Optional[str] = None
    query_sequence: str
    settings: AlignmentSettingsSchema = Field(default_factory=AlignmentSettingsSchema)


class AlignmentVariantResponse(BaseModel):
    type: str
    start: int
    end: int
    reference_wraps_origin: bool = False
    query_start: int
    query_end: int
    reference: str
    query: str
    label: str
    length: int


class SequenceAlignmentResponse(BaseModel):
    reference_name: Optional[str] = None
    query_name: Optional[str] = None
    reference_sequence: str
    query_sequence: str
    reference_aligned: str
    query_aligned: str
    midline: str
    score: float
    mode: str
    strand: str
    reference_start: int
    reference_end: int
    reference_wraps_origin: bool
    query_start: int
    query_end: int
    query_soft_clip_left: int = 0
    query_soft_clip_right: int = 0
    reference_flank_left: int = 0
    reference_flank_right: int = 0
    alignment_length: int
    matches: int
    mismatches: int
    gap_columns: int
    aligned_columns: int
    reference_aligned_bases: int
    query_aligned_bases: int
    identity_pct: float
    ungapped_identity: float
    reference_coverage: float
    query_coverage: float
    variants: List[AlignmentVariantResponse] = Field(default_factory=list)


def normalize_sequence_type(sequence_type: Optional[str], sequence: Optional[str]) -> str:
    normalized = (sequence_type or "").strip().lower()
    if normalized in {"dna", "rna"}:
        return normalized

    sequence_text = (sequence or "").upper()
    if "U" in sequence_text and "T" not in sequence_text:
        return "rna"
    return "dna"


def clean_inline_sequence(sequence: str, sequence_type: str) -> str:
    upper = sequence.upper().replace(" ", "").replace("\n", "").replace("\r", "")
    valid_chars = set("ATCGNRYMKSWHBVD") if sequence_type == "dna" else set("AUCGNRYMKSWHBVD")
    return "".join(char for char in upper if char in valid_chars)


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
    sequence_type = normalize_sequence_type(data.sequence_type, data.sequence)
    seq_clean = clean_inline_sequence(data.sequence, sequence_type)
    if not seq_clean:
        raise HTTPException(status_code=400, detail="Sequence contains no valid nucleotides")
    gc = 0.0
    if seq_clean:
        gc = round(((seq_clean.count('G') + seq_clean.count('C')) / len(seq_clean)) * 100, 2)
    # Construct a temporary sequence object
    return NucleotideSequence(
        id=str(uuid.uuid4()),
        name=data.name or "Unnamed Sequence",
        description=None,
        sequence=seq_clean,
        sequence_type=sequence_type,
        is_circular=data.is_circular,
        length=len(seq_clean),
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
    sequence_type: Optional[str] = None,
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
        description=parent.description if parent else None,
        sequence=sequence,
        sequence_type=normalize_sequence_type(sequence_type or (parent.sequence_type if parent else None), sequence),
        is_circular=circular,
        length=len(sequence),
        features=[],
        primers=[],
        organism=parent.organism if parent else None,
        accession=parent.accession if parent else None,
        source_file=None,
        gc_content=calc_gc(sequence),
        parent_id=parent_id,
        operation=operation,
        operation_params=operation_params,
        version=version,
    )


def build_assembly_fragment(fragment: AssemblyFragmentSchema) -> AssemblyFragment:
    return AssemblyFragment(
        id=fragment.id,
        name=fragment.name,
        sequence=fragment.sequence,
        orientation=fragment.orientation,  # type: ignore[arg-type]
        circular=fragment.circular,
        role=fragment.role,
        source_sequence_id=fragment.source_sequence_id,
        source_name=fragment.source_name,
        source_start=fragment.source_start,
        source_end=fragment.source_end,
        source_wraps_origin=fragment.source_wraps_origin,
        left_end=None if fragment.left_end is None else FragmentEnd(
            type=fragment.left_end.type,  # type: ignore[arg-type]
            overhang=fragment.left_end.overhang,
            label=fragment.left_end.label,
        ),
        right_end=None if fragment.right_end is None else FragmentEnd(
            type=fragment.right_end.type,  # type: ignore[arg-type]
            overhang=fragment.right_end.overhang,
            label=fragment.right_end.label,
        ),
        metadata=fragment.metadata or {},
    )


def assembly_junction_to_response(junction: AssemblyJunction) -> AssemblyJunctionResponse:
    return AssemblyJunctionResponse(
        left_fragment_id=junction.left_fragment_id,
        right_fragment_id=junction.right_fragment_id,
        left_fragment_name=junction.left_fragment_name,
        right_fragment_name=junction.right_fragment_name,
        mode=junction.mode,
        left_end_type=junction.left_end_type,
        right_end_type=junction.right_end_type,
        overhang_sequence=junction.overhang_sequence,
        overlap_sequence=junction.overlap_sequence,
        overlap_length=junction.overlap_length,
        junction_sequence=junction.junction_sequence,
        validation=junction.validation,
        notes=junction.notes,
    )


def assembly_product_to_response(product: "AssemblyProduct") -> AssemblyProductResponse:
    return AssemblyProductResponse(
        sequence=product.sequence,
        circular=product.circular,
        length=len(product.sequence),
        mode=product.mode,
        fragments=[
            AssemblyFragmentResponse(
                id=fragment.id,
                name=fragment.name,
                orientation=fragment.orientation,
                role=fragment.role,
                source_sequence_id=fragment.source_sequence_id,
                source_name=fragment.source_name,
                source_start=fragment.source_start,
                source_end=fragment.source_end,
                source_wraps_origin=fragment.source_wraps_origin,
                left_end=None if fragment.left_end is None else AssemblyFragmentEndSchema(
                    type=fragment.left_end.type,
                    overhang=fragment.left_end.overhang,
                    label=fragment.left_end.label,
                ),
                right_end=None if fragment.right_end is None else AssemblyFragmentEndSchema(
                    type=fragment.right_end.type,
                    overhang=fragment.right_end.overhang,
                    label=fragment.right_end.label,
                ),
                metadata=fragment.metadata or None,
            )
            for fragment in product.fragments
        ],
        junctions=[assembly_junction_to_response(junction) for junction in product.junctions],
        warnings=product.warnings,
        validation_notes=product.validation_notes,
    )


async def persist_assembly_product(
    session: AsyncSession,
    *,
    product: "AssemblyProduct",
    name: Optional[str],
    save_description: Optional[str],
):
    source_ids = [fragment.source_sequence_id for fragment in product.fragments if fragment.source_sequence_id]
    distinct_source_ids = sorted(set(source_ids))
    parent: Optional[NucleotideSequence] = None
    if len(distinct_source_ids) == 1:
        result = await session.execute(
            select(NucleotideSequence).where(NucleotideSequence.id == distinct_source_ids[0])
        )
        parent = result.scalar_one_or_none()

    operation_params = {
        "mode": product.mode,
        "fragments": fragment_provenance_payload(product.fragments),
        "junctions": [junction.model_dump() for junction in [assembly_junction_to_response(item) for item in product.junctions]],
        "warnings": product.warnings,
        "validation_notes": product.validation_notes,
        "topology": "circular" if product.circular else "linear",
    }

    sequence_name = (name or "").strip() or f"{product.mode.replace('_', ' ').title()} product"
    if parent is not None:
        sequence_row = create_child_sequence(
            parent=parent,
            sequence=product.sequence,
            name=sequence_name,
            circular=product.circular,
            operation=product.mode,
            operation_params=operation_params,
        )
        if save_description is not None:
            sequence_row.description = save_description
    else:
        sequence_row = NucleotideSequence(
            id=str(uuid.uuid4()),
            name=sequence_name,
            description=save_description,
            sequence=product.sequence,
            sequence_type="dna",
            is_circular=product.circular,
            length=len(product.sequence),
            features=[],
            primers=[],
            organism=None,
            accession=None,
            source_file=None,
            gc_content=round(((product.sequence.count("G") + product.sequence.count("C")) / max(len(product.sequence), 1)) * 100, 2),
            parent_id=None,
            operation=product.mode,
            operation_params=operation_params,
            version=1,
        )

    session.add(sequence_row)
    await session.commit()
    await session.refresh(sequence_row)
    return sequence_row


@router.post("/digest", response_model=MolbioOperationResponse)
async def digest(
    request: DigestRequest,
    session: AsyncSession = Depends(get_session)
):
    parent = await resolve_sequence(request, session)
    enzymes = [DigestEnzyme(name=e.name, site=e.site, cut_index=e.cut_index) for e in request.enzymes]
    fragments = digest_sequence(parent.sequence, enzymes, circular=parent.is_circular)
    fragment_payload = [
        DigestFragmentResponse(
            sequence=f.sequence,
            start=f.start,
            end=f.end,
            length=len(f.sequence),
            wraps_origin=f.start >= f.end,
        )
        for f in fragments
    ]

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
    try:
        product = pcr_product(
            parent.sequence,
            request.primer_fwd,
            request.primer_rev,
            circular=parent.is_circular,
            sequence_type=parent.sequence_type or "dna",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    product_payload = PCRProductResponse(
        sequence=product.sequence,
        start=product.start,
        end=product.end,
        length=product.length,
        wraps_origin=product.wraps_origin,
    )
    if not request.save:
        return MolbioOperationResponse(product=product_payload, message="PCR complete")

    new_name = request.new_name or f"{parent.name}_PCR"
    seq_obj = create_child_sequence(
        parent=parent if request.sequence_id else None,
        sequence=product.sequence,
        name=new_name,
        circular=False,
        operation="pcr",
        operation_params={"primer_fwd": request.primer_fwd, "primer_rev": request.primer_rev},
    )
    session.add(seq_obj)
    await session.commit()
    await session.refresh(seq_obj)
    return MolbioOperationResponse(sequence=seq_obj, product=product_payload, message="PCR complete")


@router.post("/assembly/ligation/simulate", response_model=AssemblyOperationResponse)
async def simulate_ligation_assembly(request: LigationAssemblyRequest):
    try:
        product = simulate_ligation(
            [build_assembly_fragment(fragment) for fragment in request.fragments],
            circular=request.circular,
        )
    except AssemblyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return AssemblyOperationResponse(
        product=assembly_product_to_response(product),
        message=f"Validated ligation across {len(product.fragments)} fragments",
    )


@router.post("/assembly/ligation/save", response_model=AssemblyOperationResponse)
async def save_ligation_assembly(
    request: LigationAssemblyRequest,
    session: AsyncSession = Depends(get_session),
):
    try:
        product = simulate_ligation(
            [build_assembly_fragment(fragment) for fragment in request.fragments],
            circular=request.circular,
        )
    except AssemblyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    saved = await persist_assembly_product(
        session,
        product=product,
        name=request.new_name,
        save_description=request.save_description,
    )
    return AssemblyOperationResponse(
        product=assembly_product_to_response(product),
        saved_sequence=saved,
        message=f"Saved ligation product '{saved.name}'",
    )


@router.post("/mutagenesis", response_model=MolbioOperationResponse)
async def mutagenesis(
    request: MutagenesisRequest,
    session: AsyncSession = Depends(get_session)
):
    parent = await resolve_sequence(request, session)
    mutations = [
        {"pos": m.pos, "from": m.from_base, "to": m.to} for m in request.mutations
    ]
    try:
        mutated = apply_mutations(parent.sequence, mutations)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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


@router.post("/assembly/gibson/simulate", response_model=AssemblyOperationResponse)
async def simulate_gibson_assembly(request: GibsonAssemblyRequest):
    try:
        product = simulate_gibson(
            [build_assembly_fragment(fragment) for fragment in request.fragments],
            circular=request.circular,
            minimum_overlap=request.minimum_overlap,
            preferred_overlap=request.preferred_overlap,
            maximum_overlap=request.maximum_overlap,
        )
    except AssemblyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return AssemblyOperationResponse(
        product=assembly_product_to_response(product),
        message=f"Validated Gibson assembly across {len(product.fragments)} fragments",
    )


@router.post("/assembly/gibson/save", response_model=AssemblyOperationResponse)
async def save_gibson_assembly(
    request: GibsonAssemblyRequest,
    session: AsyncSession = Depends(get_session),
):
    try:
        product = simulate_gibson(
            [build_assembly_fragment(fragment) for fragment in request.fragments],
            circular=request.circular,
            minimum_overlap=request.minimum_overlap,
            preferred_overlap=request.preferred_overlap,
            maximum_overlap=request.maximum_overlap,
        )
    except AssemblyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    saved = await persist_assembly_product(
        session,
        product=product,
        name=request.new_name,
        save_description=request.save_description,
    )
    return AssemblyOperationResponse(
        product=assembly_product_to_response(product),
        saved_sequence=saved,
        message=f"Saved Gibson product '{saved.name}'",
    )


@router.get("/assembly/golden-gate/options")
async def golden_gate_options():
    return {
        "enzymes": [
            {
                "name": enzyme.name,
                "site": enzyme.site,
                "overhang_length": enzyme.overhang_length,
            }
            for enzyme in TYPE_IIS_ENZYMES.values()
        ]
    }


@router.post("/assembly/golden-gate/simulate", response_model=AssemblyOperationResponse)
async def simulate_golden_gate_assembly(request: GoldenGateAssemblyRequest):
    try:
        product = simulate_golden_gate(
            [build_assembly_fragment(fragment) for fragment in request.fragments],
            enzyme_name=request.enzyme_name,
            circular=request.circular,
        )
    except AssemblyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    enzyme = get_type_iis_enzyme(request.enzyme_name)
    return AssemblyOperationResponse(
        product=assembly_product_to_response(product),
        message=f"Validated {enzyme.name} Golden Gate assembly across {len(product.fragments)} fragments",
    )


@router.post("/assembly/golden-gate/save", response_model=AssemblyOperationResponse)
async def save_golden_gate_assembly(
    request: GoldenGateAssemblyRequest,
    session: AsyncSession = Depends(get_session),
):
    try:
        product = simulate_golden_gate(
            [build_assembly_fragment(fragment) for fragment in request.fragments],
            enzyme_name=request.enzyme_name,
            circular=request.circular,
        )
    except AssemblyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    saved = await persist_assembly_product(
        session,
        product=product,
        name=request.new_name,
        save_description=request.save_description,
    )
    return AssemblyOperationResponse(
        product=assembly_product_to_response(product),
        saved_sequence=saved,
        message=f"Saved Golden Gate product '{saved.name}'",
    )


@router.post("/ligate", response_model=MolbioOperationResponse)
async def ligate(
    request: LigationRequest,
    session: AsyncSession = Depends(get_session)
):
    raise HTTPException(
        status_code=400,
        detail=(
            "The legacy /ligate route is deprecated because it does not carry fragment-end metadata. "
            "Use /api/molbio/assembly/ligation/simulate or /save with explicit fragment ends."
        ),
    )


@router.post("/gibson", response_model=MolbioOperationResponse)
async def gibson(
    request: GibsonRequest,
    session: AsyncSession = Depends(get_session)
):
    raise HTTPException(
        status_code=400,
        detail=(
            "The legacy /gibson route is deprecated because it does not carry validated overlap contracts. "
            "Use /api/molbio/assembly/gibson/simulate or /save."
        ),
    )


@router.post("/golden-gate", response_model=MolbioOperationResponse)
async def golden_gate(
    request: GoldenGateRequest,
    session: AsyncSession = Depends(get_session)
):
    raise HTTPException(
        status_code=400,
        detail=(
            "The legacy /golden-gate route is deprecated because it does not carry explicit post-digestion fragment metadata. "
            "Use /api/molbio/assembly/golden-gate/simulate or /save."
        ),
    )


@router.post("/alignment", response_model=SequenceAlignmentResponse)
async def align_molecular_sequences(request: SequenceAlignmentRequest):
    """Align two nucleotide sequences and return rendered alignment plus variant events."""
    try:
        result = align_sequences(
            request.reference_sequence,
            request.query_sequence,
            AlignmentSettings(
                mode=request.settings.mode,
                strand=request.settings.strand,
                reference_is_circular=request.settings.reference_is_circular,
                match_score=request.settings.match_score,
                mismatch_score=request.settings.mismatch_score,
                gap_open_score=request.settings.gap_open_score,
                gap_extend_score=request.settings.gap_extend_score,
            ),
        )
    except SequenceAlignmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return SequenceAlignmentResponse(
        reference_name=request.reference_name,
        query_name=request.query_name,
        **result,
    )


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
    from pathlib import Path
    
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
        
        # Build plannotate command with optional sensitive search config.
        sensitive_yaml = os.getenv(
            "BMS_PLANNOTATE_SENSITIVE_YAML",
            str(Path.home() / ".plannotate_sensitive.yml"),
        )
        micromamba_bin = os.getenv("BMS_MICROMAMBA_BIN", "micromamba")
        micromamba_root_prefix = os.getenv("BMS_MICROMAMBA_ROOT_PREFIX")
        plannotate_env = os.getenv("BMS_PLANNOTATE_ENV", "plannotate")

        cmd = [micromamba_bin, "run", "-n", plannotate_env]
        if micromamba_root_prefix:
            cmd.extend(["--root-prefix", micromamba_root_prefix])
        cmd.extend([
            "plannotate", "batch",
            "-i", input_file,
            "-o", output_dir,
            "--csv",
        ])
        if os.path.exists(sensitive_yaml):
            cmd.extend(["-y", sensitive_yaml])
        
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

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            detail = stderr or stdout or f"pLannotate exited with status {result.returncode}"
            raise HTTPException(status_code=500, detail=detail[:1000])
        
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


# ═══════════════════════════════════════════════════════════════════════════════
# PRIMER LIBRARY API
# ═══════════════════════════════════════════════════════════════════════════════

from database import Primer

TM_ALGORITHM_DEFS = {
    "wallace": {
        "label": "Wallace rule",
        "description": "Rule-of-thumb 2/4 formula for short oligos.",
        "kind": "wallace",
        "sequence_types": ["dna", "rna"],
    },
    "gc_empirical": {
        "label": "GC empirical",
        "description": "Empirical GC-based formula with selectable salt correction.",
        "kind": "gc",
        "sequence_types": ["dna", "rna"],
        "gc_valueset": 7,
    },
    "nn_breslauer_1986": {
        "label": "Nearest-neighbor: Breslauer 1986",
        "description": "Legacy DNA/DNA nearest-neighbor thermodynamic table.",
        "kind": "nn",
        "sequence_types": ["dna"],
        "nn_table_name": "DNA_NN1",
        "polymer_pairing": "dna_dna",
    },
    "nn_sugimoto_1996": {
        "label": "Nearest-neighbor: Sugimoto 1996",
        "description": "DNA/DNA nearest-neighbor parameters from Sugimoto et al.",
        "kind": "nn",
        "sequence_types": ["dna"],
        "nn_table_name": "DNA_NN2",
        "polymer_pairing": "dna_dna",
    },
    "nn_allawi_santalucia_1997": {
        "label": "Nearest-neighbor: Allawi & SantaLucia 1997",
        "description": "DNA/DNA nearest-neighbor table used as Biopython's default NN parameter set.",
        "kind": "nn",
        "sequence_types": ["dna"],
        "nn_table_name": "DNA_NN3",
        "polymer_pairing": "dna_dna",
    },
    "nn_santalucia_hicks_2004": {
        "label": "Nearest-neighbor: SantaLucia & Hicks 2004",
        "description": "Modern DNA/DNA nearest-neighbor parameter refinement.",
        "kind": "nn",
        "sequence_types": ["dna"],
        "nn_table_name": "DNA_NN4",
        "polymer_pairing": "dna_dna",
    },
    "rna_nn_freier_1986": {
        "label": "RNA/RNA NN: Freier 1986",
        "description": "Legacy RNA/RNA nearest-neighbor thermodynamic table.",
        "kind": "nn",
        "sequence_types": ["rna"],
        "nn_table_name": "RNA_NN1",
        "polymer_pairing": "rna_rna",
    },
    "rna_nn_xia_1998": {
        "label": "RNA/RNA NN: Xia 1998",
        "description": "RNA/RNA nearest-neighbor parameters from Xia et al.",
        "kind": "nn",
        "sequence_types": ["rna"],
        "nn_table_name": "RNA_NN2",
        "polymer_pairing": "rna_rna",
    },
    "rna_nn_chen_2012": {
        "label": "RNA/RNA NN: Chen 2012",
        "description": "Modern RNA/RNA nearest-neighbor parameter set.",
        "kind": "nn",
        "sequence_types": ["rna"],
        "nn_table_name": "RNA_NN3",
        "polymer_pairing": "rna_rna",
    },
    "rna_dna_sugimoto_1995": {
        "label": "RNA/DNA hybrid NN: Sugimoto 1995",
        "description": "RNA/DNA hybrid nearest-neighbor table. Sequence must be RNA.",
        "kind": "nn",
        "sequence_types": ["rna"],
        "nn_table_name": "R_DNA_NN1",
        "polymer_pairing": "rna_dna_hybrid",
    },
}

TM_SALT_CORRECTION_DEFS = {
    "none": {
        "label": "None",
        "description": "No salt correction.",
        "method": 0,
    },
    "schildkraut_lifson_1965": {
        "label": "Schildkraut-Lifson 1965",
        "description": "Legacy monovalent salt correction.",
        "method": 1,
    },
    "wetmur_1991": {
        "label": "Wetmur 1991",
        "description": "Monovalent salt correction using the Wetmur formulation.",
        "method": 2,
    },
    "santalucia_1996": {
        "label": "SantaLucia 1996",
        "description": "Monovalent salt correction from SantaLucia et al. 1996.",
        "method": 3,
    },
    "santalucia_1998_tm": {
        "label": "SantaLucia 1998 (Tm)",
        "description": "SantaLucia 1998 salt correction applied directly to Tm.",
        "method": 4,
    },
    "santalucia_1998_entropy": {
        "label": "SantaLucia 1998 (entropy)",
        "description": "SantaLucia 1998 entropy correction. Good general-purpose PCR default.",
        "method": 5,
    },
    "owczarzy_2004": {
        "label": "Owczarzy 2004",
        "description": "GC-aware monovalent salt correction.",
        "method": 6,
    },
    "owczarzy_2008": {
        "label": "Owczarzy 2008",
        "description": "Mg2+/dNTP-aware salt correction for mixed monovalent/divalent PCR conditions.",
        "method": 7,
    },
}

DEFAULT_TM_SETTINGS_BY_SEQUENCE_TYPE = {
    "dna": {
        "algorithm": "nn_santalucia_hicks_2004",
        "salt_correction": "owczarzy_2008",
        "primer_concentration_nM": 250.0,
        "template_concentration_nM": 0.0,
        "na_mM": 50.0,
        "k_mM": 0.0,
        "tris_mM": 0.0,
        "mg_mM": 1.5,
        "dntps_mM": 0.6,
        "dmso_percent": 0.0,
        "formamide_percent": 0.0,
        "self_complementary": False,
    },
    "rna": {
        "algorithm": "rna_nn_chen_2012",
        "salt_correction": "owczarzy_2008",
        "primer_concentration_nM": 250.0,
        "template_concentration_nM": 0.0,
        "na_mM": 50.0,
        "k_mM": 0.0,
        "tris_mM": 0.0,
        "mg_mM": 1.5,
        "dntps_mM": 0.6,
        "dmso_percent": 0.0,
        "formamide_percent": 0.0,
        "self_complementary": False,
    },
}


class PrimerTmSettings(BaseModel):
    algorithm: str = "nn_santalucia_hicks_2004"
    salt_correction: str = "owczarzy_2008"
    primer_concentration_nM: float = 250.0
    template_concentration_nM: float = 0.0
    na_mM: float = 50.0
    k_mM: float = 0.0
    tris_mM: float = 0.0
    mg_mM: float = 1.5
    dntps_mM: float = 0.6
    dmso_percent: float = 0.0
    formamide_percent: float = 0.0
    self_complementary: bool = False


class PrimerTmInput(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    sequence: str
    sequence_type: Optional[str] = None
    complement_sequence: Optional[str] = None
    shift: int = 0


class PrimerTmResult(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    sequence: str
    sequence_type: str
    length: int
    gc_percent: float
    tm: Optional[float]
    algorithm: str
    algorithm_label: str
    salt_correction: str
    salt_correction_label: str
    polymer_pairing: str
    warnings: List[str] = Field(default_factory=list)


class PrimerTmBatchRequest(BaseModel):
    primers: List[PrimerTmInput]
    settings: Optional[PrimerTmSettings] = None


class PrimerTmOption(BaseModel):
    id: str
    label: str
    description: str
    sequence_types: List[str]
    polymer_pairing: Optional[str] = None


class PrimerTmSaltCorrectionOption(BaseModel):
    id: str
    label: str
    description: str


class PrimerTmOptionsResponse(BaseModel):
    algorithms: List[PrimerTmOption]
    salt_corrections: List[PrimerTmSaltCorrectionOption]
    defaults: dict[str, PrimerTmSettings]


def clean_primer_sequence(sequence: str) -> str:
    """Normalize primer sequence text."""
    return (sequence or "").upper().replace(" ", "").replace("\n", "").replace("\r", "")


def infer_primer_sequence_type(sequence: str) -> str:
    upper = clean_primer_sequence(sequence)
    if "U" in upper and "T" not in upper:
        return "rna"
    return "dna"


def calculate_gc_percent(sequence: str) -> float:
    """Calculate GC content percentage."""
    if not sequence or len(sequence) == 0:
        return 0.0
    upper = sequence.upper()
    gc = upper.count('G') + upper.count('C')
    return round((gc / len(sequence)) * 100, 1)


def default_tm_settings_for_sequence_type(sequence_type: str) -> PrimerTmSettings:
    defaults = DEFAULT_TM_SETTINGS_BY_SEQUENCE_TYPE.get(sequence_type, DEFAULT_TM_SETTINGS_BY_SEQUENCE_TYPE["dna"])
    return PrimerTmSettings(**defaults)


def calculate_primer_tm_result(
    sequence: str,
    sequence_type: Optional[str] = None,
    settings: Optional[PrimerTmSettings] = None,
    complement_sequence: Optional[str] = None,
    shift: int = 0,
) -> PrimerTmResult:
    cleaned = clean_primer_sequence(sequence)
    resolved_sequence_type = sequence_type or infer_primer_sequence_type(cleaned)
    resolved_settings = settings or default_tm_settings_for_sequence_type(resolved_sequence_type)
    gc_percent = calculate_gc_percent(cleaned)
    warnings: List[str] = []

    if not cleaned:
        return PrimerTmResult(
            sequence=cleaned,
            sequence_type=resolved_sequence_type,
            length=0,
            gc_percent=0.0,
            tm=None,
            algorithm=resolved_settings.algorithm,
            algorithm_label=resolved_settings.algorithm,
            salt_correction=resolved_settings.salt_correction,
            salt_correction_label=resolved_settings.salt_correction,
            polymer_pairing="unknown",
            warnings=["Primer sequence is empty."],
        )

    if not all(base in "ATCGUMRWSYKVHDBN" for base in cleaned):
        return PrimerTmResult(
            sequence=cleaned,
            sequence_type=resolved_sequence_type,
            length=len(cleaned),
            gc_percent=gc_percent,
            tm=None,
            algorithm=resolved_settings.algorithm,
            algorithm_label=resolved_settings.algorithm,
            salt_correction=resolved_settings.salt_correction,
            salt_correction_label=resolved_settings.salt_correction,
            polymer_pairing="unknown",
            warnings=["Primer contains invalid nucleotide characters."],
        )

    algorithm_def = TM_ALGORITHM_DEFS.get(resolved_settings.algorithm)
    salt_def = TM_SALT_CORRECTION_DEFS.get(resolved_settings.salt_correction)

    if algorithm_def is None:
        return PrimerTmResult(
            sequence=cleaned,
            sequence_type=resolved_sequence_type,
            length=len(cleaned),
            gc_percent=gc_percent,
            tm=None,
            algorithm=resolved_settings.algorithm,
            algorithm_label=resolved_settings.algorithm,
            salt_correction=resolved_settings.salt_correction,
            salt_correction_label=resolved_settings.salt_correction,
            polymer_pairing="unknown",
            warnings=[f"Unknown Tm algorithm '{resolved_settings.algorithm}'."],
        )

    if salt_def is None:
        return PrimerTmResult(
            sequence=cleaned,
            sequence_type=resolved_sequence_type,
            length=len(cleaned),
            gc_percent=gc_percent,
            tm=None,
            algorithm=resolved_settings.algorithm,
            algorithm_label=algorithm_def["label"],
            salt_correction=resolved_settings.salt_correction,
            salt_correction_label=resolved_settings.salt_correction,
            polymer_pairing=algorithm_def.get("polymer_pairing", resolved_sequence_type),
            warnings=[f"Unknown salt correction '{resolved_settings.salt_correction}'."],
        )

    if resolved_sequence_type not in algorithm_def["sequence_types"]:
        return PrimerTmResult(
            sequence=cleaned,
            sequence_type=resolved_sequence_type,
            length=len(cleaned),
            gc_percent=gc_percent,
            tm=None,
            algorithm=resolved_settings.algorithm,
            algorithm_label=algorithm_def["label"],
            salt_correction=resolved_settings.salt_correction,
            salt_correction_label=salt_def["label"],
            polymer_pairing=algorithm_def.get("polymer_pairing", resolved_sequence_type),
            warnings=[f"Algorithm '{algorithm_def['label']}' does not support {resolved_sequence_type.upper()} primers."],
        )

    tm_value: Optional[float] = None
    try:
        if algorithm_def["kind"] == "wallace":
            tm_value = float(mt.Tm_Wallace(cleaned, strict=True))
        elif algorithm_def["kind"] == "gc":
            tm_value = float(mt.Tm_GC(
                cleaned,
                strict=True,
                valueset=algorithm_def.get("gc_valueset", 7),
                Na=resolved_settings.na_mM,
                K=resolved_settings.k_mM,
                Tris=resolved_settings.tris_mM,
                Mg=resolved_settings.mg_mM,
                dNTPs=resolved_settings.dntps_mM,
                saltcorr=salt_def["method"],
            ))
        else:
            tm_kwargs = {
                "nn_table": getattr(mt, algorithm_def["nn_table_name"]),
                "dnac1": resolved_settings.primer_concentration_nM,
                "dnac2": resolved_settings.template_concentration_nM,
                "selfcomp": resolved_settings.self_complementary,
                "Na": resolved_settings.na_mM,
                "K": resolved_settings.k_mM,
                "Tris": resolved_settings.tris_mM,
                "Mg": resolved_settings.mg_mM,
                "dNTPs": resolved_settings.dntps_mM,
                "saltcorr": salt_def["method"],
                "strict": True,
            }
            if complement_sequence:
                tm_kwargs["c_seq"] = clean_primer_sequence(complement_sequence)
                tm_kwargs["shift"] = shift
            tm_value = float(mt.Tm_NN(cleaned, **tm_kwargs))

        if resolved_settings.dmso_percent or resolved_settings.formamide_percent:
            warnings.append("DMSO/formamide corrections are approximate.")
            tm_value = float(mt.chem_correction(
                tm_value,
                DMSO=resolved_settings.dmso_percent,
                fmd=resolved_settings.formamide_percent,
                GC=gc_percent,
            ))
    except Exception as exc:
        warnings.append(str(exc))

    return PrimerTmResult(
        sequence=cleaned,
        sequence_type=resolved_sequence_type,
        length=len(cleaned),
        gc_percent=gc_percent,
        tm=round(tm_value, 2) if tm_value is not None else None,
        algorithm=resolved_settings.algorithm,
        algorithm_label=algorithm_def["label"],
        salt_correction=resolved_settings.salt_correction,
        salt_correction_label=salt_def["label"],
        polymer_pairing=algorithm_def.get("polymer_pairing", resolved_sequence_type),
        warnings=warnings,
    )


class PrimerDesignRequest(SequenceInput):
    target_start: int = 0
    target_end: Optional[int] = None
    primer_min_length: int = 18
    primer_max_length: int = 28
    product_min_length: int = 120
    product_max_length: int = 1500
    flank_search_span: int = 80
    gc_min_percent: float = 35.0
    gc_max_percent: float = 65.0
    tm_target_c: float = 62.0
    tm_max_delta_c: float = 3.0
    gc_clamp_min: int = 1
    max_poly_x: int = 4
    max_pairs: int = 8
    overhang_forward: str = ""
    overhang_reverse: str = ""
    tm_settings: Optional[PrimerTmSettings] = None


class PrimerDesignCandidateResponse(BaseModel):
    sequence: str
    anneal_sequence: str
    start: int
    end: int
    strand: int
    length: int
    anneal_length: int
    overhang_length: int
    tm: float
    gc_percent: float
    gc_clamp: int
    max_homopolymer: int
    max_self_complement: int = 0
    three_prime_self_complement: int = 0
    max_hairpin_stem: int = 0
    hairpin_loop_size: Optional[int] = None
    binding_site_count: Optional[int] = None
    off_target_site_count: Optional[int] = None
    warnings: List[str] = Field(default_factory=list)


class PrimerDesignPairResponse(BaseModel):
    rank: int
    penalty: float
    tm_delta: float
    product_start: int
    product_end: int
    product_length: int
    heterodimer_complement: int = 0
    three_prime_heterodimer: int = 0
    warnings: List[str] = Field(default_factory=list)
    forward: PrimerDesignCandidateResponse
    reverse: PrimerDesignCandidateResponse


class PrimerDesignResponse(BaseModel):
    sequence_name: Optional[str] = None
    sequence_type: str
    target_start: int
    target_end: int
    target_length: int
    pair_count: int
    pairs: List[PrimerDesignPairResponse] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


def _max_homopolymer_run(sequence: str) -> int:
    longest = 0
    current = 0
    previous = None
    for base in sequence:
        if base == previous:
            current += 1
        else:
            current = 1
            previous = base
        longest = max(longest, current)
    return longest


def _gc_clamp(sequence: str, window: int = 5) -> int:
    return sum(1 for base in sequence[-window:] if base in {"G", "C"})


def _linear_segment(sequence: str, start: int, end: int) -> Optional[str]:
    if start < 0 or end > len(sequence) or start >= end:
        return None
    return sequence[start:end]


def _design_candidate(
    anneal_sequence: str,
    start: int,
    end: int,
    strand: int,
    overhang: str,
    sequence_type: str,
    tm_settings: PrimerTmSettings,
    gc_min: float,
    gc_max: float,
    gc_clamp_min: int,
    max_poly_x: int,
    tm_target_c: float,
    tm_max_delta_c: float,
    template_sequence: str,
    circular_template: bool,
) -> Optional[dict[str, Any]]:
    primer_sequence = (overhang + anneal_sequence).upper()
    tm_result = calculate_primer_tm_result(
        anneal_sequence,
        sequence_type=sequence_type,
        settings=tm_settings,
    )
    if tm_result.tm is None:
        return None

    gc_percent = calculate_gc_percent(anneal_sequence)
    if gc_percent < gc_min or gc_percent > gc_max:
        return None

    clamp = _gc_clamp(primer_sequence)
    if clamp < gc_clamp_min:
        return None

    homopolymer = _max_homopolymer_run(primer_sequence)
    if homopolymer > max_poly_x:
        return None

    if abs(tm_result.tm - tm_target_c) > tm_max_delta_c:
        return None

    qc = evaluate_primer_qc(
        primer_sequence,
        sequence_type=sequence_type,  # type: ignore[arg-type]
        template_sequence=template_sequence,
        circular_template=circular_template,
    )

    return {
        "sequence": primer_sequence,
        "anneal_sequence": anneal_sequence,
        "start": start,
        "end": end,
        "strand": strand,
        "length": len(primer_sequence),
        "anneal_length": len(anneal_sequence),
        "overhang_length": len(overhang),
        "tm": round(tm_result.tm, 2),
        "gc_percent": gc_percent,
        "gc_clamp": clamp,
        "max_homopolymer": homopolymer,
        "max_self_complement": qc.max_self_complement,
        "three_prime_self_complement": qc.three_prime_self_complement,
        "max_hairpin_stem": qc.max_hairpin_stem,
        "hairpin_loop_size": qc.hairpin_loop_size,
        "binding_site_count": qc.binding_site_count,
        "off_target_site_count": qc.off_target_site_count,
        "warnings": [*tm_result.warnings, *qc.warnings],
    }


def design_primer_pairs_for_request(request: PrimerDesignRequest, sequence_name: Optional[str]) -> PrimerDesignResponse:
    sequence_type = normalize_sequence_type(request.sequence_type, request.sequence)
    template = clean_inline_sequence(request.sequence or "", sequence_type)
    if not template:
        raise HTTPException(status_code=400, detail="Sequence contains no valid nucleotides")

    sequence_length = len(template)
    target_end = request.target_end if request.target_end is not None else sequence_length
    if request.target_start < 0 or target_end > sequence_length or request.target_start >= target_end:
        raise HTTPException(status_code=400, detail="Target range is invalid for the current sequence")
    if request.primer_min_length < 12 or request.primer_max_length < request.primer_min_length:
        raise HTTPException(status_code=400, detail="Primer length range is invalid")
    if request.product_min_length < 40 or request.product_max_length < request.product_min_length:
        raise HTTPException(status_code=400, detail="Product length range is invalid")

    tm_settings = request.tm_settings or default_tm_settings_for_sequence_type(sequence_type)
    forward_candidates: list[dict[str, Any]] = []
    reverse_candidates: list[dict[str, Any]] = []
    warnings: list[str] = []

    if request.is_circular:
        warnings.append("Primer design currently excludes origin-wrapping candidates on circular templates.")

    forward_start_min = max(0, request.target_start - request.flank_search_span)
    forward_start_max = min(sequence_length, request.target_start + 1)
    reverse_end_min = max(0, target_end)
    reverse_end_max = min(sequence_length, target_end + request.flank_search_span)

    for start in range(forward_start_min, forward_start_max):
        for anneal_length in range(request.primer_min_length, request.primer_max_length + 1):
            end = start + anneal_length
            anneal_sequence = _linear_segment(template, start, end)
            if anneal_sequence is None:
                continue
            if end > request.target_start + 4:
                continue
            candidate = _design_candidate(
                anneal_sequence=anneal_sequence,
                start=start,
                end=end,
                strand=1,
                overhang=clean_primer_sequence(request.overhang_forward),
                sequence_type=sequence_type,
                tm_settings=tm_settings,
                gc_min=request.gc_min_percent,
                gc_max=request.gc_max_percent,
                gc_clamp_min=request.gc_clamp_min,
                max_poly_x=request.max_poly_x,
                tm_target_c=request.tm_target_c,
                tm_max_delta_c=request.tm_max_delta_c,
                template_sequence=template,
                circular_template=request.is_circular,
            )
            if candidate:
                forward_candidates.append(candidate)

    for end in range(reverse_end_min, reverse_end_max + 1):
        for anneal_length in range(request.primer_min_length, request.primer_max_length + 1):
            start = end - anneal_length
            anneal_template = _linear_segment(template, start, end)
            if anneal_template is None:
                continue
            if start < target_end - 4:
                continue
            anneal_sequence = reverse_complement(anneal_template, sequence_type)
            candidate = _design_candidate(
                anneal_sequence=anneal_sequence,
                start=start,
                end=end,
                strand=-1,
                overhang=clean_primer_sequence(request.overhang_reverse),
                sequence_type=sequence_type,
                tm_settings=tm_settings,
                gc_min=request.gc_min_percent,
                gc_max=request.gc_max_percent,
                gc_clamp_min=request.gc_clamp_min,
                max_poly_x=request.max_poly_x,
                tm_target_c=request.tm_target_c,
                tm_max_delta_c=request.tm_max_delta_c,
                template_sequence=template,
                circular_template=request.is_circular,
            )
            if candidate:
                reverse_candidates.append(candidate)

    forward_candidates.sort(key=lambda candidate: (abs(candidate["tm"] - request.tm_target_c), candidate["start"]))
    reverse_candidates.sort(key=lambda candidate: (abs(candidate["tm"] - request.tm_target_c), candidate["start"]))

    pair_candidates: list[dict[str, Any]] = []
    for forward in forward_candidates[:48]:
        for reverse in reverse_candidates[:48]:
            product_length = reverse["end"] - forward["start"]
            if product_length < request.product_min_length or product_length > request.product_max_length:
                continue
            if forward["start"] > request.target_start or reverse["end"] < target_end:
                continue

            tm_delta = abs(forward["tm"] - reverse["tm"])
            pair_qc = evaluate_primer_pair_qc(forward["sequence"], reverse["sequence"])
            penalty = round(
                abs(forward["tm"] - request.tm_target_c)
                + abs(reverse["tm"] - request.tm_target_c)
                + tm_delta * 2.5
                + pair_qc.heterodimer_complement * 1.8
                + pair_qc.three_prime_heterodimer * 2.8
                + max(forward.get("off_target_site_count") or 0, 0) * 1.2
                + max(reverse.get("off_target_site_count") or 0, 0) * 1.2
                + abs(product_length - (target_end - request.target_start)) / max(20.0, request.flank_search_span),
                3,
            )
            pair_candidates.append({
                "penalty": penalty,
                "tm_delta": round(tm_delta, 2),
                "product_start": forward["start"],
                "product_end": reverse["end"],
                "product_length": product_length,
                "heterodimer_complement": pair_qc.heterodimer_complement,
                "three_prime_heterodimer": pair_qc.three_prime_heterodimer,
                "warnings": [*forward["warnings"], *reverse["warnings"], *pair_qc.warnings],
                "forward": forward,
                "reverse": reverse,
            })

    pair_candidates.sort(key=lambda pair: (pair["penalty"], pair["tm_delta"], pair["product_length"]))
    top_pairs = pair_candidates[:request.max_pairs]
    pairs = [
        PrimerDesignPairResponse(
            rank=index + 1,
            penalty=pair["penalty"],
            tm_delta=pair["tm_delta"],
            product_start=pair["product_start"],
            product_end=pair["product_end"],
            product_length=pair["product_length"],
            heterodimer_complement=pair["heterodimer_complement"],
            three_prime_heterodimer=pair["three_prime_heterodimer"],
            warnings=pair["warnings"],
            forward=PrimerDesignCandidateResponse(**pair["forward"]),
            reverse=PrimerDesignCandidateResponse(**pair["reverse"]),
        )
        for index, pair in enumerate(top_pairs)
    ]

    if not pairs:
        warnings.append("No primer pairs met the current GC/Tm/product constraints. Relax the design settings or widen the target flanks.")

    return PrimerDesignResponse(
        sequence_name=sequence_name,
        sequence_type=sequence_type,
        target_start=request.target_start,
        target_end=target_end,
        target_length=target_end - request.target_start,
        pair_count=len(pairs),
        pairs=pairs,
        warnings=warnings,
    )


def build_primer_response(primer: Primer) -> "PrimerResponse":
    return PrimerResponse(
        id=primer.id,
        name=primer.name,
        sequence=primer.sequence,
        sequence_type=primer.sequence_type or infer_primer_sequence_type(primer.sequence),
        length=primer.length,
        tm=primer.tm,
        gc_percent=primer.gc_percent,
        tm_algorithm=primer.tm_algorithm,
        tm_salt_correction=primer.tm_salt_correction,
        tm_settings=primer.tm_settings,
        primer_type=primer.primer_type,
        description=primer.description,
        target_sequence_id=primer.target_sequence_id,
        binding_start=primer.binding_start,
        binding_end=primer.binding_end,
        binding_strand=primer.binding_strand or 1,
        tags=primer.tags,
        is_favorite=primer.is_favorite,
        created_at=primer.created_at,
        updated_at=primer.updated_at,
    )


class PrimerCreate(BaseModel):
    """Request to create a new primer."""
    name: str
    sequence: str
    sequence_type: Optional[str] = None
    primer_type: str = "general"
    description: Optional[str] = None
    target_sequence_id: Optional[str] = None
    binding_start: Optional[int] = None
    binding_end: Optional[int] = None
    binding_strand: int = 1
    tags: Optional[List[str]] = None
    tm_settings: Optional[PrimerTmSettings] = None


class PrimerUpdate(BaseModel):
    """Request to update an existing primer."""
    name: Optional[str] = None
    sequence: Optional[str] = None
    sequence_type: Optional[str] = None
    primer_type: Optional[str] = None
    description: Optional[str] = None
    target_sequence_id: Optional[str] = None
    binding_start: Optional[int] = None
    binding_end: Optional[int] = None
    binding_strand: Optional[int] = None
    tags: Optional[List[str]] = None
    is_favorite: Optional[bool] = None
    tm_settings: Optional[PrimerTmSettings] = None


class PrimerResponse(BaseModel):
    """Primer library entry response."""
    id: str
    name: str
    sequence: str
    sequence_type: str
    length: int
    tm: Optional[float]
    gc_percent: Optional[float]
    tm_algorithm: Optional[str]
    tm_salt_correction: Optional[str]
    tm_settings: Optional[dict[str, Any]]
    primer_type: str
    description: Optional[str]
    target_sequence_id: Optional[str]
    binding_start: Optional[int]
    binding_end: Optional[int]
    binding_strand: int
    tags: Optional[List[str]]
    is_favorite: bool
    created_at: datetime
    updated_at: Optional[datetime]


class PrimerQcPosition(BaseModel):
    start: int
    end: int
    strand: int
    anneal_length: int
    overhang_length: int
    reverse_primer_binding: bool


class PrimerQcResultResponse(BaseModel):
    sequence: str
    sequence_type: str
    length: int
    gc_percent: float
    max_self_complement: int
    three_prime_self_complement: int
    max_hairpin_stem: int
    hairpin_loop_size: Optional[int] = None
    binding_site_count: Optional[int] = None
    off_target_site_count: Optional[int] = None
    binding_positions: List[PrimerQcPosition] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class PrimerPairQcResultResponse(BaseModel):
    heterodimer_complement: int
    three_prime_heterodimer: int
    warnings: List[str] = Field(default_factory=list)


class PrimerQcEntry(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    sequence: str
    sequence_type: Optional[str] = None


class PrimerQcRequest(BaseModel):
    primers: List[PrimerQcEntry]
    template_sequence: Optional[str] = None
    template_sequence_type: Optional[str] = None
    template_is_circular: bool = False
    include_pairwise: bool = True


class PrimerQcEntryResponse(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    qc: PrimerQcResultResponse


class PrimerQcBatchResponse(BaseModel):
    primers: List[PrimerQcEntryResponse]
    pairwise: List[dict[str, Any]] = Field(default_factory=list)


@router.get("/primer-tm/options", response_model=PrimerTmOptionsResponse)
async def primer_tm_options():
    """Return supported Tm algorithms, salt corrections, and default settings."""
    return PrimerTmOptionsResponse(
        algorithms=[
            PrimerTmOption(
                id=option_id,
                label=definition["label"],
                description=definition["description"],
                sequence_types=definition["sequence_types"],
                polymer_pairing=definition.get("polymer_pairing"),
            )
            for option_id, definition in TM_ALGORITHM_DEFS.items()
        ],
        salt_corrections=[
            PrimerTmSaltCorrectionOption(
                id=option_id,
                label=definition["label"],
                description=definition["description"],
            )
            for option_id, definition in TM_SALT_CORRECTION_DEFS.items()
        ],
        defaults={
            sequence_type: PrimerTmSettings(**settings)
            for sequence_type, settings in DEFAULT_TM_SETTINGS_BY_SEQUENCE_TYPE.items()
        },
    )


@router.post("/primer-tm/calculate", response_model=List[PrimerTmResult])
async def calculate_primer_tm_batch(request: PrimerTmBatchRequest):
    """Calculate Tm for one or more primers using selectable thermodynamic models."""
    results: List[PrimerTmResult] = []
    for primer in request.primers:
        resolved_sequence_type = primer.sequence_type or infer_primer_sequence_type(primer.sequence)
        settings = request.settings or default_tm_settings_for_sequence_type(resolved_sequence_type)
        result = calculate_primer_tm_result(
            sequence=primer.sequence,
            sequence_type=resolved_sequence_type,
            settings=settings,
            complement_sequence=primer.complement_sequence,
            shift=primer.shift,
        )
        result.id = primer.id
        result.name = primer.name
        results.append(result)
    return results


@router.post("/primer-qc", response_model=PrimerQcBatchResponse)
async def calculate_primer_qc(request: PrimerQcRequest):
    """Calculate exact complementarity and template-binding QC metrics for primers or oligos."""
    template_sequence = None
    template_sequence_type = normalize_sequence_type(request.template_sequence_type, request.template_sequence or "")
    if request.template_sequence:
        template_sequence = clean_inline_sequence(request.template_sequence, template_sequence_type)

    primer_results: List[PrimerQcEntryResponse] = []
    normalized_sequences: List[tuple[Optional[str], Optional[str], str, str]] = []
    for primer in request.primers:
        sequence_type = primer.sequence_type or infer_primer_sequence_type(primer.sequence)
        qc = evaluate_primer_qc(
            primer.sequence,
            sequence_type=sequence_type,  # type: ignore[arg-type]
            template_sequence=template_sequence,
            circular_template=request.template_is_circular,
        )
        primer_results.append(
            PrimerQcEntryResponse(
                id=primer.id,
                name=primer.name,
                qc=PrimerQcResultResponse(
                    sequence=qc.sequence,
                    sequence_type=qc.sequence_type,
                    length=qc.length,
                    gc_percent=qc.gc_percent,
                    max_self_complement=qc.max_self_complement,
                    three_prime_self_complement=qc.three_prime_self_complement,
                    max_hairpin_stem=qc.max_hairpin_stem,
                    hairpin_loop_size=qc.hairpin_loop_size,
                    binding_site_count=qc.binding_site_count,
                    off_target_site_count=qc.off_target_site_count,
                    binding_positions=[PrimerQcPosition(**position) for position in qc.binding_positions],
                    warnings=qc.warnings,
                ),
            )
        )
        normalized_sequences.append((primer.id, primer.name, qc.sequence, qc.sequence_type))

    pairwise: List[dict[str, Any]] = []
    if request.include_pairwise and len(normalized_sequences) >= 2:
        for index, left in enumerate(normalized_sequences[:-1]):
            for right in normalized_sequences[index + 1:]:
                pair_qc = evaluate_primer_pair_qc(left[2], right[2])
                pairwise.append({
                    "left_id": left[0],
                    "left_name": left[1],
                    "right_id": right[0],
                    "right_name": right[1],
                    "heterodimer_complement": pair_qc.heterodimer_complement,
                    "three_prime_heterodimer": pair_qc.three_prime_heterodimer,
                    "warnings": pair_qc.warnings,
                })

    return PrimerQcBatchResponse(
        primers=primer_results,
        pairwise=pairwise,
    )


@router.post("/primer-design", response_model=PrimerDesignResponse)
async def design_primers(
    request: PrimerDesignRequest,
    session: AsyncSession = Depends(get_session),
):
    """Design PCR primer pairs around a target region using the configured Tm model."""
    sequence_name = request.name
    if request.sequence_id:
        sequence = await resolve_sequence(request, session)
        request = request.model_copy(update={
            "sequence": sequence.sequence,
            "sequence_type": sequence.sequence_type,
            "is_circular": sequence.is_circular,
            "name": sequence.name,
        })
        sequence_name = sequence.name

    return design_primer_pairs_for_request(request, sequence_name)


@router.get("/primers", response_model=List[PrimerResponse])
async def list_primers(
    search: Optional[str] = None,
    primer_type: Optional[str] = None,
    favorites_only: bool = False,
    target_sequence_id: Optional[str] = None,
    session: AsyncSession = Depends(get_session)
):
    """List all primers with optional filtering."""
    query = select(Primer).order_by(Primer.created_at.desc())
    
    if favorites_only:
        query = query.where(Primer.is_favorite == True)
    if primer_type:
        query = query.where(Primer.primer_type == primer_type)
    if target_sequence_id:
        query = query.where(Primer.target_sequence_id == target_sequence_id)
    
    result = await session.execute(query)
    primers = result.scalars().all()
    
    # Filter by search term if provided
    if search:
        search_lower = search.lower()
        primers = [p for p in primers if 
                   search_lower in p.name.lower() or 
                   search_lower in p.sequence.lower() or
                   (p.description and search_lower in p.description.lower())]
    
    return [build_primer_response(p) for p in primers]


@router.post("/primers", response_model=PrimerResponse)
async def create_primer(
    request: PrimerCreate,
    session: AsyncSession = Depends(get_session)
):
    """Create a new primer in the library."""
    # Validate and clean sequence
    sequence = clean_primer_sequence(request.sequence)
    if not all(c in "ATCGUMRWSYKVHDBN" for c in sequence):
        raise HTTPException(status_code=400, detail="Invalid nucleotide sequence")

    sequence_type = request.sequence_type or infer_primer_sequence_type(sequence)
    tm_settings = request.tm_settings or default_tm_settings_for_sequence_type(sequence_type)
    tm_result = calculate_primer_tm_result(sequence, sequence_type=sequence_type, settings=tm_settings)

    primer = Primer(
        id=str(uuid.uuid4()),
        name=request.name,
        sequence=sequence,
        sequence_type=sequence_type,
        length=len(sequence),
        tm=tm_result.tm,
        gc_percent=tm_result.gc_percent,
        tm_algorithm=tm_result.algorithm,
        tm_salt_correction=tm_result.salt_correction,
        tm_settings=tm_settings.model_dump(),
        primer_type=request.primer_type,
        description=request.description,
        target_sequence_id=request.target_sequence_id,
        binding_start=request.binding_start,
        binding_end=request.binding_end,
        binding_strand=request.binding_strand,
        tags=request.tags,
        is_favorite=False,
        created_at=datetime.utcnow()
    )
    
    session.add(primer)
    await session.commit()
    await session.refresh(primer)
    
    return build_primer_response(primer)


@router.get("/primers/{primer_id}", response_model=PrimerResponse)
async def get_primer(
    primer_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get a specific primer by ID."""
    result = await session.execute(select(Primer).where(Primer.id == primer_id))
    primer = result.scalar_one_or_none()
    
    if not primer:
        raise HTTPException(status_code=404, detail="Primer not found")
    
    return build_primer_response(primer)


@router.patch("/primers/{primer_id}", response_model=PrimerResponse)
async def update_primer(
    primer_id: str,
    request: PrimerUpdate,
    session: AsyncSession = Depends(get_session)
):
    """Update an existing primer."""
    result = await session.execute(select(Primer).where(Primer.id == primer_id))
    primer = result.scalar_one_or_none()
    
    if not primer:
        raise HTTPException(status_code=404, detail="Primer not found")
    
    # Update fields if provided
    recalculate_tm = False
    if request.name is not None:
        primer.name = request.name
    if request.sequence is not None:
        primer.sequence = clean_primer_sequence(request.sequence)
        primer.length = len(primer.sequence)
        recalculate_tm = True
    if request.sequence_type is not None:
        primer.sequence_type = request.sequence_type
        recalculate_tm = True
    if request.primer_type is not None:
        primer.primer_type = request.primer_type
    if request.description is not None:
        primer.description = request.description
    if request.target_sequence_id is not None:
        primer.target_sequence_id = request.target_sequence_id
    if request.binding_start is not None:
        primer.binding_start = request.binding_start
    if request.binding_end is not None:
        primer.binding_end = request.binding_end
    if request.binding_strand is not None:
        primer.binding_strand = request.binding_strand
    if request.tags is not None:
        primer.tags = request.tags
    if request.is_favorite is not None:
        primer.is_favorite = request.is_favorite
    if request.tm_settings is not None:
        primer.tm_settings = request.tm_settings.model_dump()
        recalculate_tm = True

    if recalculate_tm:
        sequence_type = primer.sequence_type or infer_primer_sequence_type(primer.sequence)
        if not sequence_type:
            sequence_type = infer_primer_sequence_type(primer.sequence)
            primer.sequence_type = sequence_type
        tm_settings = request.tm_settings or (
            PrimerTmSettings(**primer.tm_settings)
            if primer.tm_settings
            else default_tm_settings_for_sequence_type(sequence_type)
        )
        tm_result = calculate_primer_tm_result(
            primer.sequence,
            sequence_type=sequence_type,
            settings=tm_settings,
        )
        primer.length = len(primer.sequence)
        primer.tm = tm_result.tm
        primer.gc_percent = tm_result.gc_percent
        primer.tm_algorithm = tm_result.algorithm
        primer.tm_salt_correction = tm_result.salt_correction
        primer.tm_settings = tm_settings.model_dump()
    
    primer.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(primer)
    
    return build_primer_response(primer)


@router.delete("/primers/{primer_id}")
async def delete_primer(
    primer_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Delete a primer from the library."""
    result = await session.execute(select(Primer).where(Primer.id == primer_id))
    primer = result.scalar_one_or_none()
    
    if not primer:
        raise HTTPException(status_code=404, detail="Primer not found")
    
    await session.delete(primer)
    await session.commit()
    
    return {"message": f"Primer '{primer.name}' deleted"}


@router.post("/primers/{primer_id}/toggle-favorite", response_model=PrimerResponse)
async def toggle_primer_favorite(
    primer_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Toggle favorite status for a primer."""
    result = await session.execute(select(Primer).where(Primer.id == primer_id))
    primer = result.scalar_one_or_none()
    
    if not primer:
        raise HTTPException(status_code=404, detail="Primer not found")
    
    primer.is_favorite = not primer.is_favorite
    primer.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(primer)
    
    return build_primer_response(primer)
