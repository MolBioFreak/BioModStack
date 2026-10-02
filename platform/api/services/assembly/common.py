"""Shared helpers for validated assembly workflows."""
from __future__ import annotations

from typing import Iterable

from services.molbio_ops import clean_sequence, reverse_complement

from .types import (
    AssemblyError,
    AssemblyFragment,
    FragmentEnd,
    OrientedFragment,
)


def normalize_sequence(sequence: str) -> str:
    normalized = clean_sequence(sequence or "")
    if not normalized:
        raise AssemblyError("Assembly fragments must contain valid nucleotide sequence")
    return normalized


def normalize_overhang(overhang: str | None) -> str:
    if not overhang:
        return ""
    normalized = clean_sequence(overhang)
    if not normalized:
        raise AssemblyError("Overhang contains no valid nucleotide characters")
    return normalized


def normalize_end(end: FragmentEnd | None) -> FragmentEnd | None:
    if end is None:
        return None
    overhang = normalize_overhang(end.overhang)
    if end.type == "blunt":
        if overhang:
            raise AssemblyError("Blunt ends must not define an overhang sequence")
        return FragmentEnd(type="blunt", overhang="", label=end.label)
    if not overhang:
        raise AssemblyError(f"{end.type} ends require an overhang sequence")
    return FragmentEnd(type=end.type, overhang=overhang, label=end.label)


def reverse_complement_end(end: FragmentEnd | None) -> FragmentEnd | None:
    if end is None:
        return None
    if end.type == "blunt":
        return FragmentEnd(type="blunt", overhang="", label=end.label)
    return FragmentEnd(
        type=end.type,
        overhang=reverse_complement(end.overhang),
        label=end.label,
    )


def orient_fragment(fragment: AssemblyFragment) -> OrientedFragment:
    sequence = normalize_sequence(fragment.sequence)
    left_end = normalize_end(fragment.left_end)
    right_end = normalize_end(fragment.right_end)
    orientation = fragment.orientation or "forward"

    if orientation not in {"forward", "reverse"}:
        raise AssemblyError(f"Unsupported fragment orientation '{orientation}'")

    if orientation == "reverse":
        sequence = reverse_complement(sequence)
        left_end, right_end = reverse_complement_end(right_end), reverse_complement_end(left_end)

    return OrientedFragment(
        id=fragment.id,
        name=fragment.name,
        sequence=sequence,
        orientation=orientation,
        role=fragment.role,
        left_end=left_end,
        right_end=right_end,
        source_sequence_id=fragment.source_sequence_id,
        source_name=fragment.source_name,
        source_revision=fragment.source_revision,
        source_start=fragment.source_start,
        source_end=fragment.source_end,
        source_wraps_origin=fragment.source_wraps_origin,
        metadata=dict(fragment.metadata or {}),
    )


def overhangs_compatible(left: FragmentEnd | None, right: FragmentEnd | None) -> tuple[bool, list[str]]:
    """Check two facing ends written in top-strand junction notation.

    Both ends of a junction name the same top-strand bases (for example ``AATG``
    on both Golden Gate partners), so compatible sticky ends are identical.
    ``orient_fragment`` reverse-complements overhangs when it flips a fragment,
    which keeps this notation valid for reverse-oriented parts.
    """
    if left is None or right is None:
        return False, ["Both fragment ends must be specified for explicit-end ligation"]

    if left.type == "blunt" or right.type == "blunt":
        if left.type == right.type == "blunt":
            return True, []
        return False, ["Blunt ends cannot ligate to sticky ends without end-repair"]

    if left.type != right.type:
        return False, [f"Incompatible sticky-end polarities: {left.type} vs {right.type}"]

    if len(left.overhang) != len(right.overhang):
        return False, [
            f"Sticky-end lengths differ: {len(left.overhang)} vs {len(right.overhang)} nt"
        ]

    if left.overhang == right.overhang:
        return True, []
    return False, [
        f"Sticky ends are not compatible: {left.overhang} vs {right.overhang} "
        "(both ends of a junction must name the same top-strand bases)"
    ]


def junction_sequence_error(left: OrientedFragment, right: OrientedFragment) -> str | None:
    """Return why the fragment sequences do not carry the junction overhang exactly once.

    Top-strand sequences follow the digest convention: a 5' overhang belongs to
    the top strand of the right-hand fragment, and a 3' overhang to the top
    strand of the left-hand fragment. Concatenating the two top strands then
    reproduces the junction without duplicating or dropping overhang bases.
    """
    end = left.right_end
    if end is None or end.type == "blunt":
        return None
    if end.type == "sticky_5" and not right.sequence.startswith(end.overhang):
        return (
            f"'{right.name}' must begin with its 5' overhang {end.overhang}; "
            "the top strand of a 5'-overhang end includes the protruding bases"
        )
    if end.type == "sticky_3" and not left.sequence.endswith(end.overhang):
        return (
            f"'{left.name}' must end with its 3' overhang {end.overhang}; "
            "the top strand of a 3'-overhang end includes the protruding bases"
        )
    return None


def fragment_provenance_payload(fragments: Iterable[OrientedFragment]) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for fragment in fragments:
        payload.append({
            "fragment_id": fragment.id,
            "name": fragment.name,
            "orientation": fragment.orientation,
            "role": fragment.role,
            "source_sequence_id": fragment.source_sequence_id,
            "source_name": fragment.source_name,
            "source_revision": fragment.source_revision,
            "source_start": fragment.source_start,
            "source_end": fragment.source_end,
            "source_wraps_origin": fragment.source_wraps_origin,
            "left_end": None if fragment.left_end is None else {
                "type": fragment.left_end.type,
                "overhang": fragment.left_end.overhang,
                "label": fragment.left_end.label,
            },
            "right_end": None if fragment.right_end is None else {
                "type": fragment.right_end.type,
                "overhang": fragment.right_end.overhang,
                "label": fragment.right_end.label,
            },
            "metadata": fragment.metadata or None,
        })
    return payload

