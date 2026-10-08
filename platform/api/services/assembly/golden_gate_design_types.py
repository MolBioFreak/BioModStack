"""Closed v1 raw-part preparation contract (not the legacy prepared request).

Coordinates are zero-based half-open. Fusion labels use the assembled top
reference axis; physical end strings in results are individually 5′→3′.
The route owner must import this service lazily: the shared Tm DTO currently
lives in routers.molbio_ops, and remains the single chemistry authority.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from routers.molbio_ops import PrimerTmSettings, PrimerTmResult
from services.primer_qc import PrimerQcMetrics, PrimerPairQcMetrics
from services.assembly.types import AssemblyJunction, FragmentEnd
from services.nucleotide_validation import canonicalize_nucleotide_sequence
from services.restriction_digest import DigestFragment, PhysicalCleavage
from services.restriction_analysis import AnalysisOccurrence


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Thermodynamics(PrimerTmSettings):
    """Same fields/defaults/ranges as the global Tm contract; reject typos."""
    model_config = ConfigDict(extra="forbid", frozen=True)


class Region(Closed):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    wraps_origin: bool = False


class Feature(Closed):
    id: str
    type: str
    name: str = ""
    segments: list[Region]
    strand: Literal[-1, 0, 1] = 1
    qualifiers: dict[str, Any] = Field(default_factory=dict)
    codon_start: Literal[1, 2, 3] | None = None
    status: Literal["intact", "truncated", "disrupted"] = "intact"
    frame_preserved: bool | None = None


class Material(Closed):
    sequence: str
    topology: Literal["linear", "circular"]
    features: list[Feature] = Field(default_factory=list)

    @field_validator("sequence")
    @classmethod
    def dna(cls, value: str) -> str:
        return canonicalize_nucleotide_sequence(value, "dna", allow_empty=False)


class InlineSource(Material):
    kind: Literal["inline"] = "inline"


class RevisionSource(Closed):
    kind: Literal["molecular_revision"] = "molecular_revision"
    revision_id: str = Field(min_length=1)


class Source(Closed):
    id: str = Field(min_length=1)
    source: Annotated[InlineSource | RevisionSource, Field(discriminator="kind")]


class Tail(Closed):
    # Explicit choices, including empty clamp. No vendor-efficiency gate.
    clamp: str
    spacer: str
    fusion: str

    @field_validator("clamp", "spacer", "fusion")
    @classmethod
    def dna(cls, value: str) -> str:
        return canonicalize_nucleotide_sequence(value, "dna", allow_empty=True)


class SynthesisPreparation(Closed):
    kind: Literal["synthesis"] = "synthesis"
    region: Region
    left: Tail
    right: Tail
    removed_fragment_indices: list[int] = Field(default_factory=list)


class PCRPreparation(Closed):
    kind: Literal["pcr"] = "pcr"
    region: Region
    left: Tail
    right: Tail
    forward_anneal_length: int = Field(ge=8)
    reverse_anneal_length: int = Field(ge=8)
    qc_min_binding_anneal_length: int = Field(default=12, ge=1)
    removed_fragment_indices: list[int] = Field(default_factory=list)


class DonorPreparation(Closed):
    kind: Literal["donor"] = "donor"
    retained_fragment_index: int = Field(ge=0)
    removed_fragment_indices: list[int]


class PreparedPreparation(Closed):
    kind: Literal["prepared"] = "prepared"
    left_end: FragmentEnd
    right_end: FragmentEnd


class Part(Closed):
    id: str = Field(min_length=1)
    source_id: str
    name: str
    role: str | None = None
    slot: str | None = None
    orientation: Literal["forward", "reverse"] = "forward"
    preparation: Annotated[
        PCRPreparation | SynthesisPreparation | DonorPreparation | PreparedPreparation,
        Field(discriminator="kind"),
    ]


class EnzymeBinding(Closed):
    enzyme_id: str
    catalog_id: str
    catalog_sha256: str


class Target(Closed):
    topology: Literal["linear", "circular"]
    display_origin: int = Field(default=0, ge=0)
    exact_sequence: str | None = None

    @field_validator("exact_sequence")
    @classmethod
    def dna(cls, value: str | None) -> str | None:
        return None if value is None else canonicalize_nucleotide_sequence(value, "dna", allow_empty=False)


class GoldenGateDesignRequest(Closed):
    schema_version: Literal["bms.golden-gate-design.v1"] = "bms.golden-gate-design.v1"
    task: Literal["assemble_parts"] = "assemble_parts"
    sources: list[Source]
    parts: list[Part]
    target: Target
    enzyme: EnzymeBinding
    primer_settings: Thermodynamics = Field(default_factory=Thermodynamics)


class Geometry(Closed):
    site: str
    top_offset: int
    bottom_offset: int
    spacer_length: int
    overhang_length: int
    polarity: Literal["five_prime"] = "five_prime"


class Mapping(Closed):
    """Exact reference-axis interval in a parent material, not ancestry fiction."""
    output_start: int
    output_end: int
    parent_id: str
    parent_start: int
    parent_end: int
    strand: Literal[-1, 1]


class DesignMaterial(Material):
    id: str
    stage: Literal["source", "prepared", "digest"]
    parent_id: str | None = None
    transformation: Literal["source", "pcr", "synthesis", "donor", "prepared", "digest"]
    mappings: list[Mapping] = Field(default_factory=list)
    left_end: FragmentEnd | None = None
    right_end: FragmentEnd | None = None


class Primer(Closed):
    id: str
    part_id: str
    direction: Literal["forward", "reverse"]
    full_sequence: str
    clamp: str
    recognition_site: str
    recognition_orientation: Literal["forward"] = "forward"
    spacer: str
    fusion: str
    annealing_sequence: str
    # Numerical/QC result types remain owned by the existing primer services.
    tm: PrimerTmResult
    qc: PrimerQcMetrics
    qc_template_orientation: Literal["forward", "reverse"]
    footprint_mappings: list[Mapping]


class DigestOutcome(Closed):
    part_id: str
    input_material_id: str
    fragment_material_ids: list[str]
    fragments: list[DigestFragment]
    occurrences: list[AnalysisOccurrence]
    cleavages: list[PhysicalCleavage]
    retained_fragment_index: int | None
    removed_fragment_indices: list[int]
    background_fragment_indices: list[int]
    warnings: list[str]


class PreparationOutcome(Closed):
    part_id: str
    source_material_id: str
    prepared_material_id: str
    retained_material_id: str | None
    primer_ids: list[str] = Field(default_factory=list)
    pcr_verification: Literal["not_applicable", "verified", "ambiguous", "mismatch"] = "not_applicable"
    pcr_diagnostics: list[str] = Field(default_factory=list)
    pair_qc: PrimerPairQcMetrics | None = None


class SourceScreen(Closed):
    source_material_id: str
    occurrences: list[AnalysisOccurrence]
    warnings: list[str]


class Solution(Closed):
    id: str
    sequence: str
    topology: Literal["linear", "circular"]
    display_origin: int
    part_material_ids: list[str]
    mappings: list[Mapping]
    features: list[Feature]
    junctions: list[AssemblyJunction]
    occurrences: list[AnalysisOccurrence]
    exact_target_match: bool | None
    warnings: list[str]


class GoldenGateDesignResult(Closed):
    schema_version: Literal["bms.golden-gate-design-result.v1"] = "bms.golden-gate-design-result.v1"
    enzyme: EnzymeBinding
    geometry: Geometry
    primer_settings: Thermodynamics
    materials: list[DesignMaterial]
    source_screens: list[SourceScreen]
    preparations: list[PreparationOutcome]
    primers: list[Primer]
    digests: list[DigestOutcome]
    solutions: list[Solution]
    selected_solution_id: str | None
    search_scope: Literal["fixed_order_and_explicit_preparations_only"] = "fixed_order_and_explicit_preparations_only"
    diagnostics: list[str]
    limitations: list[str]
