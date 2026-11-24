"""Duplex substrate for redigesting a retained fragment whose ends are staggered.

The digest engine works on one top-strand string. A retained sticky fragment is that
string plus a bottom strand that starts and ends at different places. A site is cut only
when its whole recognition sequence is paired and every cleavage leaves paired bases on
both sides. Nothing here refuses a digest: sites that cannot be cut are reported back as
``not_cuttable`` rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

UNPAIRED_SITE = "recognition_site_overlaps_unpaired_overhang"
OUTSIDE_DUPLEX = "cleavage_position_outside_duplex"


@dataclass(frozen=True, slots=True)
class SubstrateEnd:
    kind: str  # five_prime_overhang | three_prime_overhang
    overhang_5to3: str
    phosphate: str = "unknown"

    @property
    def offset(self) -> int:
        """Bottom-minus-top boundary at this end: +n for a 5' overhang, -n for a 3' overhang."""
        return len(self.overhang_5to3) if self.kind == "five_prime_overhang" else -len(self.overhang_5to3)


@dataclass(frozen=True, slots=True)
class DuplexSubstrate:
    length: int
    left: SubstrateEnd | None
    right: SubstrateEnd | None

    @property
    def bottom_start(self) -> int:
        return self.left.offset if self.left else 0

    @property
    def bottom_end(self) -> int:
        return self.length + (self.right.offset if self.right else 0)

    @property
    def paired(self) -> tuple[int, int]:
        """Top-axis interval [low, high) where both strands exist."""
        return max(0, self.bottom_start), min(self.length, self.bottom_end)


def _end(end: Any) -> SubstrateEnd | None:
    kind = {"sticky_5": "five_prime_overhang", "sticky_3": "three_prime_overhang"}.get(
        getattr(end, "type", "blunt")
    )
    overhang = (getattr(end, "overhang", "") or "") if kind else ""
    if not kind or not overhang:
        return None
    return SubstrateEnd(kind, overhang, getattr(end, "five_prime_phosphate", "unknown") or "unknown")


def substrate_from_ends(sequence: str, left: Any, right: Any) -> DuplexSubstrate | None:
    """Substrate for retained FragmentEnd-like ends; None when both are blunt/natural."""
    left_end, right_end = _end(left), _end(right)
    if left_end is None and right_end is None:
        return None
    return DuplexSubstrate(len(sequence), left_end, right_end)


def unpaired_reason(substrate: DuplexSubstrate, occurrence: Any) -> str | None:
    """Why this occurrence cannot be cut on the substrate, or None when it can."""
    low, high = substrate.paired
    if occurrence.site_start < low or occurrence.site_end_unwrapped > high:
        return UNPAIRED_SITE
    for event in occurrence.double_strand_events:
        if event.status != "complete":
            return OUTSIDE_DUPLEX
        top, bottom = int(event.top_boundary_unwrapped), int(event.bottom_boundary_unwrapped)
        # Both resulting pieces must keep paired bases next to the cut.
        if not (low < min(top, bottom) and max(top, bottom) < high):
            return OUTSIDE_DUPLEX
    return None


def classify_occurrences(
    substrate: DuplexSubstrate, occurrences: Sequence[Any],
) -> tuple[frozenset[str], list[dict[str, Any]]]:
    """(occurrence ids that are not cut, ``not_cuttable`` rows) for definite occurrences."""
    blocked: set[str] = set()
    rows: list[dict[str, Any]] = []
    for occurrence in occurrences:
        if occurrence.certainty != "definite":
            continue
        reason = unpaired_reason(substrate, occurrence)
        if reason is not None:
            blocked.add(occurrence.occurrence_id)
            rows.append({"enzyme_id": occurrence.enzyme_id,
                         "site_start": occurrence.site_start, "reason": reason})
    return frozenset(blocked), rows


def effective_five_prime_phosphate(end: Mapping[str, Any]) -> str:
    """Phosphate state of a serialized digest end, for assembly/end-operation adapters.

    An explicit state wins. Otherwise a restriction-enzyme cut end is phosphorylated.
    The digest result does not store that default, so existing saved results keep
    their exact bytes and hashes.
    """
    state = end.get("five_prime_phosphate") or "unknown"
    if state != "unknown":
        return str(state)
    if end.get("enzyme_created") and end.get("kind") in {
        "blunt", "five_prime_overhang", "three_prime_overhang",
    }:
        return "phosphorylated"
    return "unknown"
