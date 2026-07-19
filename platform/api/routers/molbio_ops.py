"""
Molecular biology operations API.
Provides digest, PCR, ligation, mutagenesis, Gibson, and Golden Gate workflows.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Any, List, Literal, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from datetime import datetime
import asyncio
import hashlib
import uuid
from Bio.SeqUtils import MeltingTemp as mt

from molbio_database import get_molbio_session
from molbio_models import NucleotideSequence, PCRExperiment, PCRExperimentRevision, Primer
from services.molbio_persistence import (
    IdempotencyConflictError,
    begin_immediate_molbio_write,
    canonical_request_fingerprint,
    current_molecular_revision,
    get_pcr_by_idempotency_key,
    persist_pcr_experiment,
    record_generated_sequence,
    record_primer_revision,
    revise_pcr_review_state,
    sequence_snapshot,
    tm_model_revision_identity,
)
from services.assembly.common import fragment_provenance_payload
from services.assembly.gibson import simulate_gibson
from services.assembly.golden_gate import TYPE_IIS_ENZYMES, get_type_iis_enzyme, simulate_golden_gate
from services.assembly.ligation import simulate_ligation
from services.assembly.types import (
    AssemblyError,
    AssemblyFragment,
    AssemblyJunction,
    AssemblyProduct,
    FragmentEnd,
)
from services.molbio_ops import (
    DigestEnzyme,
    digest_sequence,
    pcr_product,
    apply_mutations,
    reverse_complement,
)
from services.primer_qc import evaluate_primer_pair_qc, evaluate_primer_qc
from services.nucleotide_validation import canonicalize_nucleotide_sequence
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
    new_name: Optional[str] = None
    save: bool = True
    persist_experiment: bool = True
    idempotency_key: Optional[str] = Field(default=None, max_length=255)
    tm_settings: dict[str, Any] = Field(default_factory=dict)
    polymerase_preset_revision_id: Optional[str] = None
    reaction_settings: dict[str, Any] = Field(default_factory=dict)
    cycling_assumptions: dict[str, Any] = Field(default_factory=dict)
    notes: Optional[str] = None
    review_state: str = Field(default="draft", pattern="^(draft|in_review|approved|rejected)$")
    provenance: dict[str, Any] = Field(default_factory=dict)


class PCRReviewStateRequest(BaseModel):
    review_state: str = Field(pattern="^(draft|in_review|approved|rejected)$")
    notes: Optional[str] = None
    actor: Optional[str] = None
    provenance: dict[str, Any] = Field(default_factory=dict)


async def authenticated_molbio_reviewer(request: Request) -> Optional[str]:
    """Resolve only server-authenticated reviewer/admin identities from middleware state."""

    principal = getattr(request.state, "authenticated_principal", None)
    if principal is None:
        return None
    if isinstance(principal, dict):
        actor = principal.get("id") or principal.get("subject")
        roles = principal.get("roles") or []
    else:
        actor = getattr(principal, "id", None) or getattr(principal, "subject", None)
        roles = getattr(principal, "roles", [])
    normalized_roles = {str(role).strip().lower() for role in roles}
    if not actor or not normalized_roles.intersection({"reviewer", "admin"}):
        return None
    return str(actor)


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

    model_config = ConfigDict(populate_by_name=True)


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

    model_config = ConfigDict(from_attributes=True)


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
    experiment_id: Optional[str] = None
    experiment_revision_id: Optional[str] = None
    operation_id: Optional[str] = None
    reused: bool = False


def _pcr_replay_response(existing: Any) -> MolbioOperationResponse:
    if not existing.product_snapshot:
        raise HTTPException(
            status_code=409,
            detail="Idempotent PCR record has no immutable product snapshot",
        )
    saved_sequence = (
        NucleotideSequenceResponse.model_validate(existing.product_sequence_snapshot)
        if existing.product_sequence_snapshot
        else None
    )
    return MolbioOperationResponse(
        sequence=saved_sequence,
        product=PCRProductResponse.model_validate(existing.product_snapshot),
        message="PCR complete (idempotent replay)",
        experiment_id=existing.experiment_id,
        experiment_revision_id=existing.experiment_revision_id,
        operation_id=existing.operation_id,
        reused=True,
    )


def _is_pcr_idempotency_integrity_error(exc: IntegrityError) -> bool:
    message = str(exc)
    return (
        "molecular_operations.idempotency_key" in message
        or "pcr_experiment_revisions.idempotency_key" in message
    )


class AssemblyFragmentEndSchema(BaseModel):
    type: Literal["blunt", "sticky_5", "sticky_3"]
    overhang: str = ""
    label: Optional[str] = None


class AssemblyFragmentSchema(BaseModel):
    id: str
    name: str
    sequence: str
    orientation: Literal["forward", "reverse"] = "forward"
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
    match_score: float = Field(default=2.0, gt=0, allow_inf_nan=False)
    mismatch_score: float = Field(default=-1.0, le=0, allow_inf_nan=False)
    gap_open_score: float = Field(default=-6.0, lt=0, allow_inf_nan=False)
    gap_extend_score: float = Field(default=-1.0, le=0, allow_inf_nan=False)


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
    try:
        return canonicalize_nucleotide_sequence(sequence, sequence_type, allow_empty=True)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


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
        molecule_strandedness=(parent.molecule_strandedness if parent else "unknown"),
        molecule_orientation=(parent.molecule_orientation if parent else "unknown"),
        is_circular=circular,
        length=len(sequence),
        features=[],
        primers=[],
        analysis_tracks=[],
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
    await begin_immediate_molbio_write(session)
    source_ids = [
        fragment.source_sequence_id
        for fragment in product.fragments
        if fragment.source_sequence_id
    ]
    distinct_source_ids = sorted(set(source_ids))
    source_rows: dict[str, NucleotideSequence] = {}
    for source_id in distinct_source_ids:
        source = await session.get(NucleotideSequence, source_id)
        if source is None:
            raise HTTPException(
                status_code=409,
                detail=f"Assembly source projection is missing: {source_id}",
            )
        source_rows[source_id] = source

    parent: Optional[NucleotideSequence] = (
        source_rows[distinct_source_ids[0]] if len(distinct_source_ids) == 1 else None
    )

    input_revisions = []
    inline_inputs = []
    for fragment in product.fragments:
        fragment_snapshot = {
            "fragment": {
                "id": fragment.id,
                "name": fragment.name,
                "role": fragment.role,
                "orientation": fragment.orientation,
                "sequence_sha256": hashlib.sha256(fragment.sequence.encode("utf-8")).hexdigest(),
                "sequence_length": len(fragment.sequence),
                "source_sequence_id": fragment.source_sequence_id,
                "source_start": fragment.source_start,
                "source_end": fragment.source_end,
                "source_wraps_origin": fragment.source_wraps_origin,
                "metadata": fragment.metadata or {},
            }
        }
        if fragment.source_sequence_id:
            source = source_rows[fragment.source_sequence_id]
            start = fragment.source_start
            end = fragment.source_end
            if (start is None) != (end is None):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Assembly fragment '{fragment.name}' must provide both source_start "
                        "and source_end"
                    ),
                )
            if start is None and end is None:
                if fragment.source_wraps_origin:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Assembly fragment '{fragment.name}' claims a wrapped full-source slice",
                    )
                expected_sequence = source.sequence
            elif fragment.source_wraps_origin:
                assert start is not None and end is not None
                if (
                    not source.is_circular
                    or start < 0
                    or end < 0
                    or start >= source.length
                    or end >= source.length
                    or start <= end
                ):
                    raise HTTPException(
                        status_code=409,
                        detail=f"Assembly fragment '{fragment.name}' has invalid wrapped source geometry",
                    )
                expected_sequence = source.sequence[start:] + source.sequence[:end]
            else:
                assert start is not None and end is not None
                if start < 0 or end > source.length or start >= end:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Assembly fragment '{fragment.name}' has invalid linear source geometry",
                    )
                expected_sequence = source.sequence[start:end]

            if fragment.orientation == "reverse":
                expected_sequence = reverse_complement(
                    expected_sequence,
                    source.sequence_type or "dna",
                )
            if fragment.sequence != expected_sequence:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Assembly fragment '{fragment.name}' does not match the attested source slice"
                    ),
                )
            if fragment.source_name is not None and fragment.source_name != source.name:
                raise HTTPException(
                    status_code=409,
                    detail=f"Assembly fragment '{fragment.name}' source name does not match its source ID",
                )
            fragment.source_name = source.name

            source_revision = await current_molecular_revision(session, fragment.source_sequence_id)
            current_hash = hashlib.sha256(source.sequence.encode("utf-8")).hexdigest()
            if source_revision is not None and source_revision.content_sha256 != current_hash:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Assembly source projection does not match its immutable head revision: "
                        f"{fragment.source_sequence_id}"
                    ),
                )
            if source_revision is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Assembly source has no immutable molecular revision: "
                        f"{fragment.source_sequence_id}"
                    ),
                )
            input_revisions.append((source_revision, "fragment", fragment_snapshot))
        else:
            inline_fragment = NucleotideSequence(
                id=str(uuid.uuid4()),
                name=fragment.name,
                description=None,
                sequence=fragment.sequence,
                sequence_type="dna",
                is_circular=False,
                length=len(fragment.sequence),
                features=[],
                primers=[],
                organism=None,
                accession=None,
                source_file=None,
                gc_content=round(
                    (
                        (fragment.sequence.count("G") + fragment.sequence.count("C"))
                        / max(len(fragment.sequence), 1)
                    )
                    * 100,
                    2,
                ),
                parent_id=None,
                operation=None,
                operation_params=None,
                version=1,
            )
            inline_inputs.append((inline_fragment, "fragment", fragment_snapshot))

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

    await record_generated_sequence(
        session,
        sequence_row,
        parent=parent,
        operation_kind=product.mode,
        implementation="services.assembly",
        parameters=operation_params,
        warnings=list(product.warnings),
        provenance={"source": "api", "validated": True},
        input_revisions=input_revisions,
        inline_inputs=inline_inputs,
    )
    await session.commit()
    await session.refresh(sequence_row)
    return sequence_row


@router.post("/digest", response_model=MolbioOperationResponse)
async def digest(
    request: DigestRequest,
    session: AsyncSession = Depends(get_molbio_session)
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
        operation_params={"enzymes": [e.model_dump() for e in request.enzymes]},
    )
    await record_generated_sequence(
        session,
        seq_obj,
        parent=parent if request.sequence_id else None,
        operation_kind="digest",
        implementation="services.molbio_ops.digest_sequence",
        parameters={"enzymes": [e.model_dump() for e in request.enzymes]},
        provenance={"source": "api"},
        inline_inputs=(
            [
                (
                    parent,
                    "template",
                    {
                        "request_source": "inline",
                        "declared_name": request.name,
                        "declared_sequence_type": request.sequence_type,
                        "declared_circular": request.is_circular,
                    },
                )
            ]
            if request.sequence_id is None
            else []
        ),
    )
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
    session: AsyncSession = Depends(get_molbio_session)
):
    parent = await resolve_sequence(request, session)
    forward_primer, _ = normalize_primer_sequence(request.primer_fwd, "dna")
    reverse_primer, _ = normalize_primer_sequence(request.primer_rev, "dna")
    try:
        resolved_tm_settings = (
            PrimerTmSettings(**request.tm_settings)
            if request.tm_settings
            else default_tm_settings_for_sequence_type("dna")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid Tm settings: {exc}") from exc

    forward_tm = calculate_primer_tm_result(
        forward_primer,
        sequence_type="dna",
        settings=resolved_tm_settings,
    )
    reverse_tm = calculate_primer_tm_result(
        reverse_primer,
        sequence_type="dna",
        settings=resolved_tm_settings,
    )
    forward_snapshot = {
        "sequence": forward_primer,
        "sha256": hashlib.sha256(forward_primer.encode("utf-8")).hexdigest(),
        "tm": forward_tm.model_dump(),
    }
    reverse_snapshot = {
        "sequence": reverse_primer,
        "sha256": hashlib.sha256(reverse_primer.encode("utf-8")).hexdigest(),
        "tm": reverse_tm.model_dump(),
    }
    tm_snapshot = {
        "settings": resolved_tm_settings.model_dump(),
        "forward": forward_tm.model_dump(),
        "reverse": reverse_tm.model_dump(),
        "algorithm_definition": TM_ALGORITHM_DEFS.get(resolved_tm_settings.algorithm),
        "salt_correction_definition": TM_SALT_CORRECTION_DEFS.get(resolved_tm_settings.salt_correction),
    }
    provenance = dict(request.provenance)
    provenance.update(
        {
            "source": "api",
            "endpoint": "POST /api/molbio/pcr",
            "template_input": "stored_sequence" if request.sequence_id else "inline_sequence",
        }
    )

    template_revision = (
        await current_molecular_revision(session, parent.id) if request.sequence_id else None
    )
    template_projection_snapshot = sequence_snapshot(parent)
    if not request.sequence_id:
        # The generated ORM identity is not part of an inline request.
        template_projection_snapshot.pop("id", None)
        template_projection_snapshot.pop("created_at", None)
        template_projection_snapshot.pop("updated_at", None)
    request_fingerprint = canonical_request_fingerprint(
        {
            "schema": "pcr-request-v1",
            "template": {
                "document_id": request.sequence_id,
                "revision_id": template_revision.id if template_revision else None,
                "revision_sha256": template_revision.content_sha256 if template_revision else None,
                "revision_snapshot": template_revision.snapshot if template_revision else None,
                "projection_sha256": hashlib.sha256(parent.sequence.encode("utf-8")).hexdigest(),
                "projection_snapshot": template_projection_snapshot,
            },
            "forward_primer_snapshot": forward_snapshot,
            "reverse_primer_snapshot": reverse_snapshot,
            "tm_snapshot": tm_snapshot,
            "tm_model_revision": tm_model_revision_identity(tm_snapshot),
            "polymerase_preset_revision_id": request.polymerase_preset_revision_id,
            "reaction_settings": request.reaction_settings,
            "cycling_assumptions": request.cycling_assumptions,
            "save_intent": {
                "save": request.save,
                "persist_experiment": request.persist_experiment,
                "new_name": request.new_name,
            },
            "experiment": {
                "notes": request.notes,
                "review_state": request.review_state,
            },
            "provenance": provenance,
            "implementation": "services.molbio_ops.pcr_product:v1",
        }
    )

    if request.persist_experiment and request.idempotency_key:
        try:
            existing = await get_pcr_by_idempotency_key(
                session,
                request.idempotency_key,
                request_fingerprint=request_fingerprint,
            )
        except IdempotencyConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if existing is not None:
            return _pcr_replay_response(existing)
        # Release the read snapshot before persistence acquires SQLite's writer
        # lock. Loaded immutable identity data remains available because the
        # Mol Bio session factory uses expire_on_commit=False.
        await session.commit()

    try:
        product = pcr_product(
            parent.sequence,
            forward_primer,
            reverse_primer,
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
    warnings = list(forward_tm.warnings) + list(reverse_tm.warnings)
    if product.wraps_origin:
        warnings.append("PCR product crosses the circular sequence origin.")
    product_snapshot = {
        **product_payload.model_dump(),
        "sha256": hashlib.sha256(product.sequence.encode("utf-8")).hexdigest(),
    }

    saved_sequence = None
    operation_id = None
    if request.save:
        saved_sequence = create_child_sequence(
            parent=parent if request.sequence_id else None,
            sequence=product.sequence,
            name=request.new_name or f"{parent.name}_PCR",
            circular=False,
            operation="pcr",
            operation_params={
                "primer_fwd": forward_primer,
                "primer_rev": reverse_primer,
                "tm_settings": resolved_tm_settings.model_dump(),
            },
        )

    if request.persist_experiment:
        try:
            persisted = await persist_pcr_experiment(
                session,
                template=parent,
                template_was_persisted=bool(request.sequence_id),
                forward_primer_snapshot=forward_snapshot,
                reverse_primer_snapshot=reverse_snapshot,
                tm_snapshot=tm_snapshot,
                polymerase_preset_revision_id=request.polymerase_preset_revision_id,
                reaction_settings=request.reaction_settings,
                cycling_assumptions=request.cycling_assumptions,
                product_snapshot=product_snapshot,
                warnings=warnings,
                notes=request.notes,
                review_state=request.review_state,
                provenance=provenance,
                idempotency_key=request.idempotency_key,
                request_fingerprint=request_fingerprint,
                product_sequence=saved_sequence,
            )
            await session.commit()
        except IdempotencyConflictError as exc:
            await session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except IntegrityError as exc:
            await session.rollback()
            if not request.idempotency_key or not _is_pcr_idempotency_integrity_error(exc):
                raise
            try:
                raced = await get_pcr_by_idempotency_key(
                    session,
                    request.idempotency_key,
                    request_fingerprint=request_fingerprint,
                )
            except IdempotencyConflictError as conflict:
                raise HTTPException(status_code=409, detail=str(conflict)) from conflict
            if raced is None:
                raise
            return _pcr_replay_response(raced)
        except ValueError as exc:
            await session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if persisted.reused:
            return _pcr_replay_response(persisted)
        if saved_sequence is not None:
            await session.refresh(saved_sequence)
        return MolbioOperationResponse(
            sequence=saved_sequence,
            product=product_payload,
            message="PCR complete",
            experiment_id=persisted.experiment_id,
            experiment_revision_id=persisted.experiment_revision_id,
            operation_id=persisted.operation_id,
            reused=persisted.reused,
        )

    if saved_sequence is not None:
        operation, _ = await record_generated_sequence(
            session,
            saved_sequence,
            parent=parent if request.sequence_id else None,
            operation_kind="pcr",
            implementation="services.molbio_ops.pcr_product",
            parameters={
                "primer_fwd": forward_primer,
                "primer_rev": reverse_primer,
                "tm_settings": resolved_tm_settings.model_dump(),
            },
            warnings=warnings,
            provenance=provenance,
            inline_inputs=(
                [
                    (
                        parent,
                        "template",
                        {
                            "request_source": "inline",
                            "declared_name": request.name,
                            "declared_sequence_type": request.sequence_type,
                            "declared_circular": request.is_circular,
                        },
                    )
                ]
                if request.sequence_id is None
                else []
            ),
        )
        operation_id = operation.id
        await session.commit()
        await session.refresh(saved_sequence)

    return MolbioOperationResponse(
        sequence=saved_sequence,
        product=product_payload,
        message="PCR complete",
        operation_id=operation_id,
    )


def _pcr_revision_payload(revision: PCRExperimentRevision) -> dict[str, Any]:
    return {
        "id": revision.id,
        "experiment_id": revision.experiment_id,
        "revision_number": revision.revision_number,
        "operation_id": revision.operation_id,
        "template_document_id": revision.template_document_id,
        "template_revision_id": revision.template_revision_id,
        "template_sha256": revision.template_sha256,
        "template_snapshot": revision.template_snapshot,
        "forward_primer_snapshot": revision.forward_primer_snapshot,
        "reverse_primer_snapshot": revision.reverse_primer_snapshot,
        "tm_model_revision_id": revision.tm_model_revision_id,
        "tm_snapshot": revision.tm_snapshot,
        "polymerase_preset_revision_id": revision.polymerase_preset_revision_id,
        "polymerase_snapshot": revision.polymerase_snapshot,
        "reaction_settings": revision.reaction_settings,
        "cycling_assumptions": revision.cycling_assumptions,
        "product_document_id": revision.product_document_id,
        "product_revision_id": revision.product_revision_id,
        "product_snapshot": revision.product_snapshot,
        "warnings": revision.warnings,
        "notes": revision.notes,
        "review_state": revision.review_state,
        "provenance": revision.provenance,
        "created_by": revision.created_by,
        "created_at": revision.created_at,
    }


@router.get("/pcr-experiments")
async def list_pcr_experiments(
    limit: int = 100,
    session: AsyncSession = Depends(get_molbio_session),
):
    bounded_limit = max(1, min(limit, 500))
    experiments = (
        await session.execute(
            select(PCRExperiment).order_by(PCRExperiment.updated_at.desc()).limit(bounded_limit)
        )
    ).scalars().all()
    items = []
    for experiment in experiments:
        current = (
            await session.get(PCRExperimentRevision, experiment.current_revision_id)
            if experiment.current_revision_id
            else None
        )
        items.append(
            {
                "id": experiment.id,
                "name": experiment.name,
                "review_state": experiment.review_state,
                "current_revision_id": experiment.current_revision_id,
                "created_at": experiment.created_at,
                "updated_at": experiment.updated_at,
                "current_revision": _pcr_revision_payload(current) if current else None,
            }
        )
    return {"items": items, "count": len(items), "limit": bounded_limit}


@router.get("/pcr-experiments/{experiment_id}")
async def get_pcr_experiment(
    experiment_id: str,
    session: AsyncSession = Depends(get_molbio_session),
):
    experiment = await session.get(PCRExperiment, experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail="PCR experiment not found")
    revisions = (
        await session.execute(
            select(PCRExperimentRevision)
            .where(PCRExperimentRevision.experiment_id == experiment.id)
            .order_by(PCRExperimentRevision.revision_number.desc())
        )
    ).scalars().all()
    return {
        "id": experiment.id,
        "name": experiment.name,
        "review_state": experiment.review_state,
        "current_revision_id": experiment.current_revision_id,
        "created_at": experiment.created_at,
        "updated_at": experiment.updated_at,
        "revisions": [_pcr_revision_payload(revision) for revision in revisions],
    }


@router.patch("/pcr-experiments/{experiment_id}/review-state")
async def update_pcr_experiment_review_state(
    experiment_id: str,
    request: PCRReviewStateRequest,
    session: AsyncSession = Depends(get_molbio_session),
    authenticated_actor: Optional[str] = Depends(authenticated_molbio_reviewer),
):
    trusted_actor = authenticated_actor if isinstance(authenticated_actor, str) else None
    if request.review_state in {"approved", "rejected"} and trusted_actor is None:
        raise HTTPException(
            status_code=403,
            detail="An authenticated reviewer or administrator is required for this decision",
        )
    try:
        await begin_immediate_molbio_write(session)
        review_provenance = dict(request.provenance)
        if request.actor is not None:
            review_provenance["client_actor_claim"] = {
                "value": request.actor,
                "verification": "unverified",
            }
        revision = await revise_pcr_review_state(
            session,
            experiment_id=experiment_id,
            review_state=request.review_state,
            notes=request.notes,
            actor=trusted_actor or "system:molbio-api",
            provenance=review_provenance,
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        status = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return _pcr_revision_payload(revision)


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
    session: AsyncSession = Depends(get_molbio_session),
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
    session: AsyncSession = Depends(get_molbio_session)
):
    parent = await resolve_sequence(request, session)
    sequence_type = normalize_sequence_type(parent.sequence_type, parent.sequence)
    mutations: list[dict[str, Any]] = []
    for mutation in request.mutations:
        try:
            to_residue = canonicalize_nucleotide_sequence(
                mutation.to,
                sequence_type,
                allow_empty=False,
            )
            from_residue = (
                canonicalize_nucleotide_sequence(
                    mutation.from_base,
                    sequence_type,
                    allow_empty=False,
                )
                if mutation.from_base is not None
                else None
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if len(to_residue) != 1 or (from_residue is not None and len(from_residue) != 1):
            raise HTTPException(
                status_code=400,
                detail="Mutations require exactly one source and one replacement residue",
            )
        mutations.append(
            {"pos": mutation.pos, "from": from_residue, "to": to_residue}
        )
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
    await record_generated_sequence(
        session,
        seq_obj,
        parent=parent if request.sequence_id else None,
        operation_kind="mutagenesis",
        implementation="services.molbio_ops.apply_mutations",
        parameters={"mutations": mutations},
        provenance={"source": "api"},
        inline_inputs=(
            [
                (
                    parent,
                    "template",
                    {
                        "request_source": "inline",
                        "declared_name": request.name,
                        "declared_sequence_type": request.sequence_type,
                        "declared_circular": request.is_circular,
                    },
                )
            ]
            if request.sequence_id is None
            else []
        ),
    )
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
    session: AsyncSession = Depends(get_molbio_session),
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
    session: AsyncSession = Depends(get_molbio_session),
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
    session: AsyncSession = Depends(get_molbio_session)
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
    session: AsyncSession = Depends(get_molbio_session)
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
    session: AsyncSession = Depends(get_molbio_session)
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
        result = await asyncio.to_thread(
            align_sequences,
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


def _resolve_plannotate_executable(configured: str | None = None) -> str | None:
    """Return an executable path/name when pLannotate tooling is available."""
    import os
    import shutil

    if not configured:
        return None
    if os.path.sep in configured:
        return configured if os.path.exists(configured) else None
    return shutil.which(configured) or None


def _build_plannotate_command(
    *,
    input_file: str,
    output_dir: str,
    is_linear: bool,
    detailed: bool,
) -> list[str]:
    """Build the pLannotate command for either micromamba or direct installs."""
    import os
    import shutil
    from pathlib import Path

    sensitive_yaml = os.getenv(
        "BMS_PLANNOTATE_SENSITIVE_YAML",
        str(Path.home() / ".plannotate_sensitive.yml"),
    )
    plannotate_bin = _resolve_plannotate_executable(os.getenv("BMS_PLANNOTATE_BIN"))
    micromamba_bin = _resolve_plannotate_executable(os.getenv("BMS_MICROMAMBA_BIN")) or shutil.which("micromamba")
    micromamba_root_prefix = os.getenv("BMS_MICROMAMBA_ROOT_PREFIX")
    plannotate_env = os.getenv("BMS_PLANNOTATE_ENV", "plannotate")

    if micromamba_bin:
        cmd = [micromamba_bin, "run"]
        if micromamba_root_prefix:
            cmd.extend(["--root-prefix", micromamba_root_prefix])
        cmd.extend(["-n", plannotate_env, "plannotate"])
    else:
        plannotate_bin = plannotate_bin or shutil.which("plannotate")
        if not plannotate_bin:
            raise HTTPException(
                status_code=503,
                detail=(
                    "pLannotate is not available in this runtime: neither micromamba nor "
                    "plannotate is on PATH. Rebuild bms-api with pLannotate support, or set "
                    "BMS_MICROMAMBA_BIN/BMS_PLANNOTATE_BIN and BMS_PLANNOTATE_ENV."
                ),
            )
        cmd = [plannotate_bin]

    cmd.extend([
        "batch",
        "-i", input_file,
        "-o", output_dir,
        "--csv",
    ])
    if os.path.exists(sensitive_yaml):
        cmd.extend(["-y", sensitive_yaml])
    if is_linear:
        cmd.append("-l")
    if detailed:
        cmd.append("-d")
    return cmd


def _plannotate_error_means_no_features(stderr: str, stdout: str) -> bool:
    """pLannotate can exit non-zero on valid non-plasmid/no-hit inputs."""
    combined = f"{stderr}\n{stdout}"
    no_feature_markers = [
        "Cannot set a DataFrame without columns to the column feat loc",
        "no features",
        "no annotations",
    ]
    return any(marker.lower() in combined.lower() for marker in no_feature_markers)


@router.post("/auto-annotate", response_model=AutoAnnotateResponse)
async def auto_annotate(request: AutoAnnotateRequest):
    """
    Auto-detect plasmid features using pLannotate.
    
    Uses BLAST-based detection to identify common plasmid components like:
    - Origins of replication (ori, ColE1, etc.)
    - Antibiotic resistance genes (KanR, AmpR, CmR, etc.)
    - Promoters and terminators
    - Common tags and reporters
    
    Requires pLannotate to be available directly or through the configured micromamba env.
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
        
        # Build pLannotate command with optional sensitive search config.
        cmd = _build_plannotate_command(
            input_file=input_file,
            output_dir=output_dir,
            is_linear=request.is_linear,
            detailed=request.detailed,
        )
        
        # Run pLannotate
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=120,  # 2 minute timeout
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="pLannotate timed out")
        except FileNotFoundError as e:
            raise HTTPException(
                status_code=503,
                detail=f"pLannotate runtime unavailable: {str(e)}",
            ) from e
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"pLannotate execution failed: {str(e)}")

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            if _plannotate_error_means_no_features(stderr, stdout):
                return AutoAnnotateResponse(features=[], message="No features detected")
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
    primer_concentration_nM: float = Field(default=250.0, gt=0, allow_inf_nan=False)
    template_concentration_nM: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    na_mM: float = Field(default=50.0, ge=0, allow_inf_nan=False)
    k_mM: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    tris_mM: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    mg_mM: float = Field(default=1.5, ge=0, allow_inf_nan=False)
    dntps_mM: float = Field(default=0.6, ge=0, allow_inf_nan=False)
    dmso_percent: float = Field(default=0.0, ge=0, le=100, allow_inf_nan=False)
    formamide_percent: float = Field(default=0.0, ge=0, le=100, allow_inf_nan=False)
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


def normalize_primer_sequence(sequence: str, sequence_type: Optional[str] = None) -> tuple[str, str]:
    resolved_type = normalize_sequence_type(
        sequence_type or infer_primer_sequence_type(sequence),
        sequence,
    )
    try:
        normalized = canonicalize_nucleotide_sequence(
            sequence,
            resolved_type,
            allow_empty=False,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return normalized, resolved_type


async def validate_primer_binding_geometry(
    session: AsyncSession,
    *,
    target_sequence_id: Optional[str],
    binding_start: Optional[int],
    binding_end: Optional[int],
    binding_strand: int,
) -> None:
    if binding_strand not in (-1, 1):
        raise HTTPException(status_code=400, detail="binding_strand must be -1 or 1")
    if target_sequence_id is None:
        if binding_start is not None or binding_end is not None:
            raise HTTPException(
                status_code=400,
                detail="Binding coordinates require target_sequence_id",
            )
        return
    if (binding_start is None) != (binding_end is None):
        raise HTTPException(
            status_code=400,
            detail="binding_start and binding_end must be provided together",
        )

    target = await session.get(NucleotideSequence, target_sequence_id)
    if target is None:
        raise HTTPException(status_code=400, detail="Target sequence not found")
    if binding_start is None or binding_end is None:
        return
    if not (0 <= binding_start < target.length and 0 <= binding_end <= target.length):
        raise HTTPException(
            status_code=400,
            detail=f"Binding coordinates must be within target length {target.length}",
        )
    if target.is_circular:
        if binding_start == binding_end:
            raise HTTPException(status_code=400, detail="Circular binding geometry cannot be empty")
    elif binding_start >= binding_end:
        raise HTTPException(
            status_code=400,
            detail="Linear binding geometry requires binding_start < binding_end",
        )


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
    session: AsyncSession = Depends(get_molbio_session),
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
    session: AsyncSession = Depends(get_molbio_session)
):
    """List all primers with optional filtering."""
    query = (
        select(Primer)
        .where(Primer.deleted_at.is_(None))
        .order_by(Primer.created_at.desc())
    )
    
    if favorites_only:
        query = query.where(Primer.is_favorite.is_(True))
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
    session: AsyncSession = Depends(get_molbio_session)
):
    """Create a new primer in the library."""
    await begin_immediate_molbio_write(session)
    # Validate and canonicalize sequence and placement before persistence.
    sequence, sequence_type = normalize_primer_sequence(
        request.sequence,
        request.sequence_type,
    )
    await validate_primer_binding_geometry(
        session,
        target_sequence_id=request.target_sequence_id,
        binding_start=request.binding_start,
        binding_end=request.binding_end,
        binding_strand=request.binding_strand,
    )
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
    await record_primer_revision(
        session,
        primer,
        change_kind="create",
        provenance={"source": "api", "endpoint": "POST /api/molbio/primers"},
    )
    await session.commit()
    await session.refresh(primer)
    
    return build_primer_response(primer)


@router.get("/primers/{primer_id}", response_model=PrimerResponse)
async def get_primer(
    primer_id: str,
    session: AsyncSession = Depends(get_molbio_session)
):
    """Get a specific primer by ID."""
    result = await session.execute(select(Primer).where(Primer.id == primer_id, Primer.deleted_at.is_(None)))
    primer = result.scalar_one_or_none()
    
    if not primer:
        raise HTTPException(status_code=404, detail="Primer not found")
    
    return build_primer_response(primer)


@router.patch("/primers/{primer_id}", response_model=PrimerResponse)
async def update_primer(
    primer_id: str,
    request: PrimerUpdate,
    session: AsyncSession = Depends(get_molbio_session)
):
    """Update an existing primer with field-presence and geometry validation."""
    await begin_immediate_molbio_write(session)
    result = await session.execute(
        select(Primer).where(Primer.id == primer_id, Primer.deleted_at.is_(None))
    )
    primer = result.scalar_one_or_none()

    if not primer:
        raise HTTPException(status_code=404, detail="Primer not found")

    provided_fields = request.model_fields_set
    if "name" in provided_fields and request.name is None:
        raise HTTPException(status_code=400, detail="Primer name cannot be null")
    if "sequence" in provided_fields and request.sequence is None:
        raise HTTPException(status_code=400, detail="Primer sequence cannot be null")
    if "primer_type" in provided_fields and request.primer_type is None:
        raise HTTPException(status_code=400, detail="Primer type cannot be null")

    requested_sequence = (
        request.sequence if "sequence" in provided_fields else primer.sequence
    )
    requested_sequence_type = (
        request.sequence_type
        if "sequence_type" in provided_fields
        else primer.sequence_type
    )
    normalized_sequence, normalized_sequence_type = normalize_primer_sequence(
        requested_sequence or "",
        requested_sequence_type,
    )

    target_was_cleared = (
        "target_sequence_id" in provided_fields
        and request.target_sequence_id is None
    )
    next_target_sequence_id = (
        request.target_sequence_id
        if "target_sequence_id" in provided_fields
        else primer.target_sequence_id
    )
    next_binding_start = (
        request.binding_start
        if "binding_start" in provided_fields
        else None
        if target_was_cleared
        else primer.binding_start
    )
    next_binding_end = (
        request.binding_end
        if "binding_end" in provided_fields
        else None
        if target_was_cleared
        else primer.binding_end
    )
    next_binding_strand = (
        request.binding_strand
        if "binding_strand" in provided_fields and request.binding_strand is not None
        else primer.binding_strand or 1
    )
    await validate_primer_binding_geometry(
        session,
        target_sequence_id=next_target_sequence_id,
        binding_start=next_binding_start,
        binding_end=next_binding_end,
        binding_strand=next_binding_strand,
    )

    if "name" in provided_fields:
        primer.name = request.name
    if "primer_type" in provided_fields:
        primer.primer_type = request.primer_type
    if "description" in provided_fields:
        primer.description = request.description
    if "tags" in provided_fields:
        primer.tags = request.tags
    if "is_favorite" in provided_fields and request.is_favorite is not None:
        primer.is_favorite = request.is_favorite

    primer.target_sequence_id = next_target_sequence_id
    primer.binding_start = next_binding_start
    primer.binding_end = next_binding_end
    primer.binding_strand = next_binding_strand

    recalculate_tm = bool(
        {"sequence", "sequence_type", "tm_settings"}.intersection(provided_fields)
    )
    if recalculate_tm:
        tm_settings = (
            request.tm_settings
            if request.tm_settings is not None
            else default_tm_settings_for_sequence_type(normalized_sequence_type)
            if "tm_settings" in provided_fields
            else PrimerTmSettings(**primer.tm_settings)
            if primer.tm_settings
            else default_tm_settings_for_sequence_type(normalized_sequence_type)
        )
        tm_result = calculate_primer_tm_result(
            normalized_sequence,
            sequence_type=normalized_sequence_type,
            settings=tm_settings,
        )
        primer.sequence = normalized_sequence
        primer.sequence_type = normalized_sequence_type
        primer.length = len(normalized_sequence)
        primer.tm = tm_result.tm
        primer.gc_percent = tm_result.gc_percent
        primer.tm_algorithm = tm_result.algorithm
        primer.tm_salt_correction = tm_result.salt_correction
        primer.tm_settings = tm_settings.model_dump()

    primer.updated_at = datetime.utcnow()
    await record_primer_revision(
        session,
        primer,
        change_kind="update",
        provenance={"source": "api", "fields": sorted(provided_fields)},
    )
    await session.commit()
    await session.refresh(primer)

    return build_primer_response(primer)


@router.delete("/primers/{primer_id}")
async def delete_primer(
    primer_id: str,
    session: AsyncSession = Depends(get_molbio_session)
):
    """Soft-delete a primer while preserving its immutable revision history."""
    await begin_immediate_molbio_write(session)
    result = await session.execute(select(Primer).where(Primer.id == primer_id, Primer.deleted_at.is_(None)))
    primer = result.scalar_one_or_none()
    
    if not primer:
        raise HTTPException(status_code=404, detail="Primer not found")

    primer.deleted_at = datetime.utcnow()
    primer.updated_at = primer.deleted_at
    await record_primer_revision(
        session,
        primer,
        change_kind="delete",
        provenance={"source": "api", "endpoint": "DELETE /api/molbio/primers/{primer_id}"},
    )
    await session.commit()
    
    return {"message": f"Primer '{primer.name}' deleted"}


@router.post("/primers/{primer_id}/toggle-favorite", response_model=PrimerResponse)
async def toggle_primer_favorite(
    primer_id: str,
    session: AsyncSession = Depends(get_molbio_session)
):
    """Toggle favorite status for a primer."""
    await begin_immediate_molbio_write(session)
    result = await session.execute(select(Primer).where(Primer.id == primer_id, Primer.deleted_at.is_(None)))
    primer = result.scalar_one_or_none()
    
    if not primer:
        raise HTTPException(status_code=404, detail="Primer not found")
    
    primer.is_favorite = not primer.is_favorite
    primer.updated_at = datetime.utcnow()
    await record_primer_revision(
        session,
        primer,
        change_kind="favorite_toggle",
        provenance={"source": "api", "is_favorite": primer.is_favorite},
    )
    await session.commit()
    await session.refresh(primer)
    
    return build_primer_response(primer)
