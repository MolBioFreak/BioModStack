"""Validated exact-overlap Gibson assembly helpers."""

from __future__ import annotations

from services.molbio_ops import reverse_complement

from .common import orient_fragment
from .gibson_tm import TM_MODEL, primer_tm, rule_2_4
from .types import (
    AssemblyError, AssemblyFragment, AssemblyJunction, AssemblyProduct, OrientedFragment,
)


def effective_sequence(fragment: OrientedFragment) -> tuple[str, list[str]]:
    """Top-strand string that takes part in overlap evaluation.

    Only ends with an explicit protruding strand are interpreted. A 5' protrusion
    on the bottom strand at the right end is single-stranded territory that may
    serve as homology; a 3' protrusion on the top strand at the right end is an
    unpaired flap and is not counted. At the left end a top 5' protrusion is part
    of the top span already and a bottom 3' protrusion is a flap outside it.
    """
    sequence, notes = fragment.sequence, []
    right = fragment.right_end
    if right is not None and right.protruding_strand and right.overhang:
        if right.type == "sticky_5" and right.protruding_strand == "bottom":
            sequence += reverse_complement(right.overhang)
            notes.append(f"{fragment.name}: right 5' protrusion {right.overhang} counted as homology")
        elif right.type == "sticky_3" and right.protruding_strand == "top" and sequence.endswith(right.overhang):
            sequence = sequence[: -len(right.overhang)]
            notes.append(f"{fragment.name}: right 3' protrusion {right.overhang} is an unpaired flap, not counted")
    left = fragment.left_end
    if left is not None and left.protruding_strand and left.type == "sticky_3" and left.overhang:
        notes.append(f"{fragment.name}: left 3' protrusion {left.overhang} is an unpaired flap, not counted")
    return sequence, notes


def _tm_fields(overlap: str) -> dict:
    return {"overlap_tm": primer_tm(overlap), "overlap_tm_2_4": rule_2_4(overlap), "overlap_tm_model": TM_MODEL}


def _longest_exact_overlap(
    left: str, right: str, minimum: int, maximum: int | None = None
) -> tuple[str, int]:
    max_allowed = min(len(left), len(right)) - 1
    if maximum is not None:
        max_allowed = min(max_allowed, maximum)
    for overlap_length in range(max_allowed, minimum - 1, -1):
        overlap = left[-overlap_length:]
        if overlap == right[:overlap_length]:
            return overlap, overlap_length
    return "", 0


def simulate_gibson(
    fragments: list[AssemblyFragment],
    *,
    circular: bool,
    minimum_overlap: int,
    preferred_overlap: int | None = None,
    maximum_overlap: int | None = 80,
) -> AssemblyProduct:
    if not fragments or (len(fragments) < 2 and not circular):
        raise AssemblyError("Gibson assembly requires at least two fragments, or one fragment to circularize")
    if minimum_overlap < 12:
        raise AssemblyError("Gibson minimum overlap must be at least 12 nt")

    oriented = [orient_fragment(fragment) for fragment in fragments]
    effective = {}
    warnings: list[str] = []
    for item in oriented:
        effective[item.id], notes = effective_sequence(item)
        warnings.extend(notes)
    sequence = effective[oriented[0].id]
    junctions: list[AssemblyJunction] = []

    for left, right in zip(oriented, oriented[1:]):
        overlap, overlap_length = _longest_exact_overlap(
            effective[left.id],
            effective[right.id],
            minimum_overlap,
            maximum_overlap,
        )
        if overlap_length == 0:
            raise AssemblyError(
                f"Gibson overlap validation failed between '{left.name}' and '{right.name}'; "
                f"no exact terminal overlap >= {minimum_overlap} nt was found"
            )
        if preferred_overlap is not None and overlap_length < preferred_overlap:
            warnings.append(
                f"Overlap {left.name}→{right.name} is {overlap_length} nt; below preferred {preferred_overlap} nt"
            )
        sequence += effective[right.id][overlap_length:]
        junctions.append(
            AssemblyJunction(
                left_fragment_id=left.id,
                right_fragment_id=right.id,
                left_fragment_name=left.name,
                right_fragment_name=right.name,
                mode="gibson",
                overlap_sequence=overlap,
                overlap_length=overlap_length,
                junction_sequence=overlap,
                **_tm_fields(overlap),
            )
        )

    if circular:
        first = oriented[0]
        last = oriented[-1]
        overlap, overlap_length = _longest_exact_overlap(
            effective[last.id],
            effective[first.id],
            minimum_overlap,
            maximum_overlap,
        )
        if overlap_length == 0:
            raise AssemblyError(
                f"Gibson circularization failed between '{last.name}' and '{first.name}'; "
                f"no exact terminal overlap >= {minimum_overlap} nt was found"
            )
        if preferred_overlap is not None and overlap_length < preferred_overlap:
            warnings.append(
                f"Circularization overlap {last.name}→{first.name} is {overlap_length} nt; below preferred {preferred_overlap} nt"
            )
        sequence = sequence[:-overlap_length]
        junctions.append(
            AssemblyJunction(
                left_fragment_id=last.id,
                right_fragment_id=first.id,
                left_fragment_name=last.name,
                right_fragment_name=first.name,
                mode="gibson",
                overlap_sequence=overlap,
                overlap_length=overlap_length,
                junction_sequence=overlap,
                **_tm_fields(overlap),
            )
        )

    return AssemblyProduct(
        mode="gibson",
        sequence=sequence,
        circular=circular,
        fragments=oriented,
        junctions=junctions,
        warnings=list(dict.fromkeys(warnings)),
        validation_notes=[],
    )
