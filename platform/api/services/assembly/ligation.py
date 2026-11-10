"""Validated ligation and sticky-end assembly helpers."""
from __future__ import annotations

from .common import orient_fragment, overhangs_compatible, require_declared_ends_match_sequence
from .types import (
    AssemblyError, AssemblyFragment, AssemblyJunction, AssemblyProduct, FragmentEnd, OrientedFragment, SealedState,
)

UNKNOWN_SEAL_NOTE = "joined, ligation chemistry not established"


def junction_seal(left: FragmentEnd | None, right: FragmentEnd | None) -> tuple[SealedState, list[str]]:
    """Label only. A strand is sealed when the downstream strand's 5' end at the junction is phosphorylated.

    The two downstream 5' termini at a junction are the 5' end of each fragment's own end record
    (the left fragment's right end and the right fragment's left end). Never affects whether fragments join.
    """
    states = [end.five_prime_phosphate if end is not None else "unknown" for end in (left, right)]
    if "unknown" in states:
        return "unknown", [UNKNOWN_SEAL_NOTE]
    count = states.count("phosphorylated")
    return ("both", "one_strand", "none")[2 - count], (
        [] if count == 2 else ["joined, nicked"] if count == 1 else ["joined, not sealed on either strand"]
    )


def simulate_ligation(
    fragments: list[AssemblyFragment],
    *,
    circular: bool,
    mode: str = "ligation",
) -> AssemblyProduct:
    if len(fragments) == 0:
        raise AssemblyError("At least one fragment is required for ligation")

    product = _ligate_oriented_fragments(
        [orient_fragment(fragment) for fragment in fragments], circular=circular, mode=mode,
    )
    for fragment in fragments:
        require_declared_ends_match_sequence(fragment)
    return product


def _ligate_oriented_fragments(
    oriented: list[OrientedFragment], *, circular: bool, mode: str = "ligation",
) -> AssemblyProduct:
    """Join already-oriented physical spans; callers own orientation/validation."""
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
        sealed, seal_notes = junction_seal(left.right_end, right.left_end)
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
                notes=[*notes, *seal_notes],
                sealed=sealed,
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
        sealed, seal_notes = junction_seal(last.right_end, first.left_end)
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
                notes=[*notes, *seal_notes],
                sealed=sealed,
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
