"""Validated exact-overlap Gibson assembly helpers."""
from __future__ import annotations

from .common import orient_fragment
from .types import AssemblyError, AssemblyFragment, AssemblyJunction, AssemblyProduct


def _longest_exact_overlap(left: str, right: str, minimum: int, maximum: int | None = None) -> tuple[str, int]:
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
    if len(fragments) < 2:
        raise AssemblyError("Gibson assembly requires at least two fragments")
    if minimum_overlap < 12:
        raise AssemblyError("Gibson minimum overlap must be at least 12 nt")

    oriented = [orient_fragment(fragment) for fragment in fragments]
    sequence = oriented[0].sequence
    junctions: list[AssemblyJunction] = []
    warnings: list[str] = []

    for left, right in zip(oriented, oriented[1:]):
        overlap, overlap_length = _longest_exact_overlap(
            left.sequence,
            right.sequence,
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
        sequence += right.sequence[overlap_length:]
        junctions.append(
            AssemblyJunction(
                left_fragment_id=left.id,
                right_fragment_id=right.id,
                left_fragment_name=left.name,
                right_fragment_name=right.name,
                mode="gibson",
                overlap_sequence=overlap,
                overlap_length=overlap_length,
                junction_sequence=left.sequence[-12:] + right.sequence[:12],
            )
        )

    if circular:
        first = oriented[0]
        last = oriented[-1]
        overlap, overlap_length = _longest_exact_overlap(
            last.sequence,
            first.sequence,
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
                junction_sequence=last.sequence[-12:] + first.sequence[:12],
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
