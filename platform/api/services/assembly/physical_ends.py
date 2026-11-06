"""Read/write retained physical ends on a revision's provenance.

A saved child whose ends are not blunt (end-operation products, linear assembly
products with sticky external ends, imported fragments) keeps them under the
provenance key ``physical_ends``. Digest children already keep the same facts under
their digest-fragment ``geometry``; ``read_physical_ends`` understands both so every
consumer asks one function. The optional ``five_prime_phosphate`` field is carried
when the end type has it and is left out while unknown.

5' phosphate state by product type (textbook chemistry; a label only, it never gates a join):

* restriction-enzyme cut end ........ phosphorylated (5'-P, 3'-OH)
* PCR product, unmodified primers ... unphosphorylated (oligos start with 5'-OH)
* chemically synthesized dsDNA ...... unphosphorylated unless ordered with a 5' phosphate
* T4 PNK / phosphatase (CIP, rSAP) .. phosphorylated / unphosphorylated
* fill-in, 3'->5' exonuclease ....... the 5' terminus is untouched, state unchanged
* mung bean / S1 on a 5' overhang ... new 5' terminus is phosphorylated
* ligation .......................... joined ends are consumed, outer ends keep their state
* uncut source ends, pasted DNA ...... unknown until stated
"""
from __future__ import annotations

import dataclasses
from typing import Any, Mapping

from .types import AssemblyError, EndType, FragmentEnd

_KIND_BY_TYPE = {"blunt": "blunt", "sticky_5": "five_prime_overhang", "sticky_3": "three_prime_overhang"}
_TYPE_BY_KIND: dict[str, EndType] = {
    "natural": "blunt", "blunt": "blunt",
    "five_prime_overhang": "sticky_5", "three_prime_overhang": "sticky_3",
}
_FIELDS = {f.name for f in dataclasses.fields(FragmentEnd)}


def end_to_record(end: FragmentEnd | None) -> dict[str, Any] | None:
    if end is None:
        return None
    overhang = end.overhang or ""
    record: dict[str, Any] = {
        "kind": _KIND_BY_TYPE.get(end.type, "blunt"),
        "overhang_sequence_5to3": overhang or None,
        "protruding_strand": end.protruding_strand,
        "length_nt": len(overhang),
    }
    phosphate = getattr(end, "five_prime_phosphate", "unknown")
    if phosphate and phosphate != "unknown":
        record["five_prime_phosphate"] = phosphate
    return record


def record_to_end(record: Mapping[str, Any] | None) -> FragmentEnd | None:
    if not isinstance(record, Mapping):
        return None
    kind = record.get("kind")
    if kind == "no_cut_circular" or kind not in _TYPE_BY_KIND:
        return None
    kwargs: dict[str, Any] = {
        "type": _TYPE_BY_KIND[kind],
        "overhang": record.get("overhang_sequence_5to3") or "",
        "protruding_strand": record.get("protruding_strand"),
    }
    stated = record.get("five_prime_phosphate")
    if not stated or stated == "unknown":
        # Restriction-enzyme cut ends carry a 5' phosphate; a saved digest does not store that default.
        stated = "phosphorylated" if record.get("enzyme_created") else "unknown"
    kwargs["five_prime_phosphate"] = stated
    return FragmentEnd(**kwargs)


def write_physical_ends(provenance: Mapping[str, Any] | None, left: FragmentEnd | None,
                        right: FragmentEnd | None, source: str) -> dict[str, Any]:
    """Return a copy of ``provenance`` carrying a ``physical_ends`` record."""
    merged = dict(provenance or {})
    merged["physical_ends"] = {"left_end": end_to_record(left), "right_end": end_to_record(right), "source": source}
    return merged


def read_physical_ends(provenance: Mapping[str, Any] | None) -> tuple[FragmentEnd | None, FragmentEnd | None]:
    """Ends from ``physical_ends`` or, failing that, a digest-fragment ``geometry``."""
    if not isinstance(provenance, Mapping):
        return None, None
    record = provenance.get("physical_ends")
    if isinstance(record, Mapping):
        return record_to_end(record.get("left_end")), record_to_end(record.get("right_end"))
    geometry = provenance.get("geometry")
    if isinstance(geometry, Mapping):
        return record_to_end(geometry.get("left_end")), record_to_end(geometry.get("right_end"))
    return None, None


def with_unphosphorylated_blunt_ends(provenance: Mapping[str, Any] | None, source: str) -> dict[str, Any]:
    """Provenance for a PCR or synthesized product: blunt ends with 5'-OH (unmodified primers/oligos)."""
    end = FragmentEnd(type="blunt", five_prime_phosphate="unphosphorylated")
    return write_physical_ends(provenance, end, end, source)


def digest_end_to_fragment_end(value: Any) -> FragmentEnd:
    """Digest end -> assembly end. Enzyme-created ends are 5'-phosphorylated; a digest end
    that states its own phosphate state wins; natural ends stay unknown."""
    if value.kind == "no_cut_circular":
        raise AssemblyError("An uncut circular molecule has no ligatable ends")
    kind: EndType = ("sticky_5" if value.kind == "five_prime_overhang"
                     else "sticky_3" if value.kind == "three_prime_overhang" else "blunt")
    stated = getattr(value, "five_prime_phosphate", None)
    if not stated or stated == "unknown":
        stated = "phosphorylated" if value.enzyme_created else "unknown"
    phosphate = stated
    return FragmentEnd(type=kind, overhang=value.overhang_sequence_5to3 or "",
                       protruding_strand=value.protruding_strand, five_prime_phosphate=phosphate)
