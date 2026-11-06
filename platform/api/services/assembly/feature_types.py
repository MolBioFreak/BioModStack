"""Light shared feature/material types for assembly mapping.

Kept free of router and primer-service imports so conventional assembly,
digest, PCR and Golden Gate can all share one coordinate mapper without a cycle.
Golden Gate re-exports these names; class identity is unchanged.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.nucleotide_validation import canonicalize_nucleotide_sequence


class Closed(BaseModel):
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


class Mapping(Closed):
    """Exact reference-axis interval in a parent material, not ancestry fiction."""
    output_start: int
    output_end: int
    parent_id: str
    parent_start: int
    parent_end: int
    strand: Literal[-1, 1]
