"""Pydantic request schemas for ligation and assembly fragments."""
from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, model_serializer


class AssemblyFragmentEndSchema(BaseModel):
    type: Literal["blunt", "sticky_5", "sticky_3"]
    overhang: str = ""
    label: Optional[str] = None
    protruding_strand: Optional[Literal["top", "bottom"]] = Field(
        default=None,
        description="Physical overhang strand; overhang is that strand 5′→3′ and sequence is the top span. Omit for historical unspecified notation.",
    )
    five_prime_phosphate: Literal["unknown", "phosphorylated", "unphosphorylated"] = Field(
        default="unknown",
        description="State of this end's 5′ terminus. Omitted from serialization while unknown; never decides whether fragments join.",
    )

    @model_serializer(mode="wrap")
    def _omit_unknown_phosphate(self, handler):
        data = handler(self)
        if isinstance(data, dict) and data.get("five_prime_phosphate") == "unknown":
            data.pop("five_prime_phosphate")
        return data


class AssemblyFragmentSchema(BaseModel):
    id: str
    name: str
    sequence: str
    sequence_sha256: Optional[str] = None
    orientation: Literal["forward", "reverse"] = "forward"
    circular: bool = False
    role: Optional[str] = None
    source_sequence_id: Optional[str] = None
    source_name: Optional[str] = None
    source_revision: Optional[int] = None
    source_start: Optional[int] = None
    source_end: Optional[int] = None
    source_wraps_origin: bool = False
    left_end: Optional[AssemblyFragmentEndSchema] = None
    right_end: Optional[AssemblyFragmentEndSchema] = None
    metadata: Optional[dict[str, Any]] = None
    # Inline fragments only: stored features in fragment-local top-strand coordinates.
    # Ignored when the fragment's source is a saved revision.
    features: Optional[list[dict[str, Any]]] = Field(default=None, exclude_if=lambda value: value is None)


class LigationAssemblyRequest(BaseModel):
    fragments: List[AssemblyFragmentSchema]
    circular: bool = True
    new_name: Optional[str] = None
    save_description: Optional[str] = None
    observe_enzymes: Optional[List[str]] = None  # report whether junctions regenerate these sites; never blocks a join

    @model_serializer(mode="wrap")
    def _omit_absent_observe_enzymes(self, handler):
        data = handler(self)
        if isinstance(data, dict) and data.get("observe_enzymes") is None:
            data.pop("observe_enzymes", None)
        return data
