"""Shared assembly types for ligation, Gibson, and Golden Gate workflows."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


EndType = Literal["blunt", "sticky_5", "sticky_3"]
AssemblyMode = Literal["ligation", "gibson", "golden_gate"]
FragmentOrientation = Literal["forward", "reverse"]
FragmentPreparation = Literal["pcr", "ready_linear"]
PrimerDirection = Literal["forward", "reverse"]


class AssemblyError(ValueError):
    """Raised when an assembly request is invalid or ambiguous."""


@dataclass(slots=True)
class FragmentEnd:
    type: EndType
    overhang: str = ""
    label: Optional[str] = None


@dataclass(slots=True)
class AssemblyFragment:
    id: str
    name: str
    sequence: str
    orientation: FragmentOrientation = "forward"
    circular: bool = False
    role: Optional[str] = None
    source_sequence_id: Optional[str] = None
    source_name: Optional[str] = None
    source_revision: Optional[int] = None
    source_start: Optional[int] = None
    source_end: Optional[int] = None
    source_wraps_origin: bool = False
    left_end: Optional[FragmentEnd] = None
    right_end: Optional[FragmentEnd] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OrientedFragment:
    id: str
    name: str
    sequence: str
    orientation: FragmentOrientation
    role: Optional[str]
    left_end: Optional[FragmentEnd]
    right_end: Optional[FragmentEnd]
    source_sequence_id: Optional[str]
    source_name: Optional[str]
    source_revision: Optional[int]
    source_start: Optional[int]
    source_end: Optional[int]
    source_wraps_origin: bool
    metadata: dict[str, Any]


@dataclass(slots=True)
class AssemblyJunction:
    left_fragment_id: str
    right_fragment_id: str
    left_fragment_name: str
    right_fragment_name: str
    mode: AssemblyMode
    left_end_type: Optional[EndType] = None
    right_end_type: Optional[EndType] = None
    overhang_sequence: Optional[str] = None
    overlap_sequence: Optional[str] = None
    overlap_length: int = 0
    junction_sequence: str = ""
    validation: str = "validated"
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class GoldenGateCatalogAuthority:
    enzyme_id: str
    catalog_id: str
    catalog_sha256: str


@dataclass(slots=True)
class AssemblyProduct:
    mode: AssemblyMode
    sequence: str
    circular: bool
    fragments: list[OrientedFragment]
    junctions: list[AssemblyJunction]
    warnings: list[str] = field(default_factory=list)
    validation_notes: list[str] = field(default_factory=list)
    golden_gate_authority: Optional[GoldenGateCatalogAuthority] = None


@dataclass(slots=True)
class GeneratedPrimer:
    id: str
    fragment_id: str
    fragment_name: str
    direction: PrimerDirection
    full_sequence: str
    annealing_sequence: str
    tail_sequence: str
    tm: float
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DesignedFragment:
    id: str
    name: str
    preparation: FragmentPreparation
    sequence: str
    checksum: str
    primer_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class GibsonCandidate:
    checksum: str
    product: AssemblyProduct
    exact_match: bool

    @property
    def sequence(self) -> str:
        return self.product.sequence

    @property
    def circular(self) -> bool:
        return self.product.circular

    @property
    def junctions(self) -> list[AssemblyJunction]:
        return self.product.junctions


@dataclass(slots=True)
class GibsonDesignResult:
    engine: str
    engine_version: str
    circular: bool
    overlap: int
    target_tm: float
    min_anneal: int
    primers: list[GeneratedPrimer]
    designed_fragments: list[DesignedFragment]
    candidates: list[GibsonCandidate]
    selected_candidate_checksum: Optional[str]
    warnings: list[str]
    source_provenance: list[dict[str, Any]]

