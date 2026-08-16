"""Validated ligation and sticky-end assembly helpers."""
from __future__ import annotations

from .common import orient_fragment, overhangs_compatible
from .types import AssemblyError, AssemblyFragment, AssemblyJunction, AssemblyProduct


def simulate_ligation(
    fragments: list[AssemblyFragment],
    *,
    circular: bool,
    mode: str = "ligation",
) -> AssemblyProduct:
    if len(fragments) == 0:
        raise AssemblyError("At least one fragment is required for ligation")

    oriented = [orient_fragment(fragment) for fragment in fragments]
    if len(oriented) == 1 and circular:
        single = oriented[0]
        compatible, notes = overhangs_compatible(single.right_end, single.left_end)
        if not compatible:
            raise AssemblyError(
                "Single-fragment circularization failed: " + "; ".join(notes)
            )

    junctions: list[AssemblyJunction] = []
    warnings: list[str] = []
    sequence = oriented[0].sequence

    for left, right in zip(oriented, oriented[1:]):
        compatible, notes = overhangs_compatible(left.right_end, right.left_end)
        if not compatible:
            raise AssemblyError(
                f"Ligation failed between '{left.name}' and '{right.name}': " + "; ".join(notes)
            )
        if notes:
            warnings.extend(notes)
        sequence += right.sequence
        junctions.append(
            AssemblyJunction(
                left_fragment_id=left.id,
                right_fragment_id=right.id,
                left_fragment_name=left.name,
                right_fragment_name=right.name,
                mode=mode,  # type: ignore[arg-type]
                left_end_type=left.right_end.type if left.right_end else None,
                right_end_type=right.left_end.type if right.left_end else None,
                overhang_sequence=(left.right_end.overhang if left.right_end and left.right_end.overhang else None),
                junction_sequence=left.sequence[-12:] + right.sequence[:12],
                notes=notes,
            )
        )

    if circular:
        first = oriented[0]
        last = oriented[-1]
        compatible, notes = overhangs_compatible(last.right_end, first.left_end)
        if not compatible:
            raise AssemblyError(
                f"Circularization failed between '{last.name}' and '{first.name}': " + "; ".join(notes)
            )
        if notes:
            warnings.extend(notes)
        junctions.append(
            AssemblyJunction(
                left_fragment_id=last.id,
                right_fragment_id=first.id,
                left_fragment_name=last.name,
                right_fragment_name=first.name,
                mode=mode,  # type: ignore[arg-type]
                left_end_type=last.right_end.type if last.right_end else None,
                right_end_type=first.left_end.type if first.left_end else None,
                overhang_sequence=(last.right_end.overhang if last.right_end and last.right_end.overhang else None),
                junction_sequence=last.sequence[-12:] + first.sequence[:12],
                notes=notes,
            )
        )

    return AssemblyProduct(
        mode=mode,  # type: ignore[arg-type]
        sequence=sequence,
        circular=circular,
        fragments=oriented,
        junctions=junctions,
        warnings=list(dict.fromkeys(warnings)),
        validation_notes=[],
    )
