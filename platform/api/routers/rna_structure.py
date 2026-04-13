"""RNA secondary structure API backed by ViennaRNA."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import NucleotideSequence, get_session
from services.rna_structure import (
    RnaStructureError,
    RnaStructureSettings,
    analyze_rna_structure,
    default_structure_settings,
    structure_limits,
)


router = APIRouter(prefix="/api/molbio/rna-structure", tags=["rna-structure"])


class RnaStructureSettingsSchema(BaseModel):
    temperature_c: float = Field(default=37.0, ge=0.0, le=100.0)
    no_lonely_pairs: bool = False
    dangles: int = Field(default=2, ge=0, le=3)
    circular: Optional[bool] = None
    max_bp_span: Optional[int] = Field(default=None, ge=2)
    gamma: float = Field(default=1.0, gt=0.0, le=10.0)
    probability_cutoff: float = Field(default=0.02, gt=0.0, lt=1.0)
    max_pairs: int = Field(default=800, ge=10, le=5000)


class RnaStructureRequest(BaseModel):
    sequence_id: Optional[str] = None
    name: Optional[str] = None
    sequence: Optional[str] = None
    is_circular: Optional[bool] = None
    settings: RnaStructureSettingsSchema = Field(default_factory=RnaStructureSettingsSchema)


class RnaFoldRequest(RnaStructureRequest):
    include_partition: bool = True


class RnaStructurePredictionResponse(BaseModel):
    dot_bracket: str
    energy_kcal_mol: Optional[float] = None
    score: Optional[float] = None
    distance: Optional[float] = None
    paired_count: int


class RnaPartitionResponse(BaseModel):
    dot_bracket: str
    ensemble_free_energy_kcal_mol: float
    mean_bp_distance: float
    probability_cutoff: float
    pair_count: int
    truncated: bool


class RnaPairProbabilityResponse(BaseModel):
    i: int
    j: int
    probability: float


class RnaBaseProbabilityResponse(BaseModel):
    index: int
    base: str
    paired_probability: float
    unpaired_probability: float
    positional_entropy: Optional[float] = None


class RnaStructureResponse(BaseModel):
    source_sequence_id: Optional[str] = None
    name: Optional[str] = None
    sequence: str
    length: int
    circular: bool
    settings: RnaStructureSettingsSchema
    mfe: RnaStructurePredictionResponse
    centroid: Optional[RnaStructurePredictionResponse] = None
    mea: Optional[RnaStructurePredictionResponse] = None
    partition: Optional[RnaPartitionResponse] = None
    pair_probabilities: list[RnaPairProbabilityResponse] = Field(default_factory=list)
    bases: list[RnaBaseProbabilityResponse] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RnaStructureOptionsResponse(BaseModel):
    defaults: RnaStructureSettingsSchema
    limits: dict[str, int]


def _schema_to_settings(schema: RnaStructureSettingsSchema, circular: bool) -> RnaStructureSettings:
    return RnaStructureSettings(
        temperature_c=schema.temperature_c,
        no_lonely_pairs=schema.no_lonely_pairs,
        dangles=schema.dangles,
        circular=circular if schema.circular is None else schema.circular,
        max_bp_span=schema.max_bp_span,
        gamma=schema.gamma,
        probability_cutoff=schema.probability_cutoff,
        max_pairs=schema.max_pairs,
    )


async def _resolve_rna_request(
    data: RnaStructureRequest,
    session: AsyncSession,
) -> tuple[Optional[str], Optional[str], str, bool]:
    if data.sequence_id:
        result = await session.execute(
            select(NucleotideSequence).where(NucleotideSequence.id == data.sequence_id)
        )
        seq = result.scalar_one_or_none()
        if not seq:
            raise HTTPException(status_code=404, detail="Sequence not found")
        if seq.sequence_type != "rna":
            raise HTTPException(status_code=400, detail="RNA structure analysis only supports RNA sequences")
        return seq.id, seq.name, seq.sequence, bool(seq.is_circular)

    if not data.sequence:
        raise HTTPException(status_code=400, detail="Provide either sequence_id or an inline RNA sequence")

    return None, data.name, data.sequence, bool(data.is_circular)


def _run_structure_analysis(
    name: Optional[str],
    source_sequence_id: Optional[str],
    sequence: str,
    settings: RnaStructureSettings,
    include_partition: bool,
) -> RnaStructureResponse:
    try:
        result = analyze_rna_structure(sequence, settings, include_partition=include_partition)
    except RnaStructureError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return RnaStructureResponse(
        source_sequence_id=source_sequence_id,
        name=name,
        **result,
    )


@router.get("/options", response_model=RnaStructureOptionsResponse)
async def get_rna_structure_options():
    """Return default RNA folding settings and hard limits."""
    defaults = default_structure_settings()
    return RnaStructureOptionsResponse(
        defaults=RnaStructureSettingsSchema(
            temperature_c=defaults.temperature_c,
            no_lonely_pairs=defaults.no_lonely_pairs,
            dangles=defaults.dangles,
            circular=defaults.circular,
            max_bp_span=defaults.max_bp_span,
            gamma=defaults.gamma,
            probability_cutoff=defaults.probability_cutoff,
            max_pairs=defaults.max_pairs,
        ),
        limits=structure_limits(),
    )


@router.post("/fold", response_model=RnaStructureResponse)
async def fold_rna(
    data: RnaFoldRequest,
    session: AsyncSession = Depends(get_session),
):
    """Fold an RNA sequence and optionally compute ensemble statistics."""
    source_sequence_id, name, sequence, circular = await _resolve_rna_request(data, session)
    settings = _schema_to_settings(data.settings, circular)
    return _run_structure_analysis(name, source_sequence_id, sequence, settings, data.include_partition)


@router.post("/partition", response_model=RnaStructureResponse)
async def partition_rna(
    data: RnaStructureRequest,
    session: AsyncSession = Depends(get_session),
):
    """Compute RNA partition-function ensemble statistics and pair probabilities."""
    source_sequence_id, name, sequence, circular = await _resolve_rna_request(data, session)
    settings = _schema_to_settings(data.settings, circular)
    return _run_structure_analysis(name, source_sequence_id, sequence, settings, True)
