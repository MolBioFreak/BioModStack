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
        return FragmentEnd(type="blunt", overhang="", label=end.label,
                           five_prime_phosphate=end.five_prime_phosphate)
    if not overhang:
        raise AssemblyError(f"{end.type} ends require an overhang sequence")
    return FragmentEnd(type=end.type, overhang=overhang, label=end.label,
                       protruding_strand=end.protruding_strand,
                       five_prime_phosphate=end.five_prime_phosphate)


def reverse_complement_end(end: FragmentEnd | None) -> FragmentEnd | None:
    if end is None:
        return None
    # A rotated end keeps its own phosphate state; only the strand label swaps.
    if end.type == "blunt":
        return FragmentEnd(type="blunt", overhang="", label=end.label,
                           five_prime_phosphate=end.five_prime_phosphate)
    return FragmentEnd(
        type=end.type,
        overhang=end.overhang if end.protruding_strand else reverse_complement(end.overhang),
        label=end.label,
        protruding_strand=("bottom" if end.protruding_strand == "top" else "top")
        if end.protruding_strand else None,
        five_prime_phosphate=end.five_prime_phosphate,
    )


def require_declared_ends_match_sequence(fragment: AssemblyFragment) -> None:
    """A top-strand overhang a fragment declares must be the bases its sequence actually has there.

    Joins are built from the sequence bytes, so a declared end that the bytes contradict builds a different
    junction than the one reported. Runs on the fragment as stored, before any reversal. A bottom-strand
    overhang is not part of the top bytes, so only its partner end can be checked. An end with no strand label
    is read as a top overhang only for a left 5' end on a forward fragment (the legacy meaning).
    """
    sequence = normalize_sequence(fragment.sequence)
    forward = (fragment.orientation or "forward") == "forward"
    left = normalize_end(fragment.left_end)
    right = normalize_end(fragment.right_end)
    if left and left.overhang:
        top = left.protruding_strand == "top" or (left.protruding_strand is None and left.type == "sticky_5" and forward)
        found = sequence[: len(left.overhang)]
        if top and found != left.overhang:
            raise AssemblyError(
                f"Mismatch detected: {fragment.name} declares left overhang {left.overhang}, sequence starts with {found}"
            )
    if right and right.overhang and right.protruding_strand == "top":
        found = sequence[-len(right.overhang):]
        if found != right.overhang:
            raise AssemblyError(
                f"Mismatch detected: {fragment.name} declares right overhang {right.overhang}, sequence ends with {found}"
            )


def orient_fragment(fragment: AssemblyFragment) -> OrientedFragment:
    sequence = normalize_sequence(fragment.sequence)
    left_end = normalize_end(fragment.left_end)
    right_end = normalize_end(fragment.right_end)
    orientation = fragment.orientation or "forward"

    if orientation not in {"forward", "reverse"}:
        raise AssemblyError(f"Unsupported fragment orientation '{orientation}'")

    if orientation == "reverse":
        # Rotate the duplex, not just its top span. Reconstruct the bottom's
        # reference-axis span by removing top protrusions and adding bottom ones.
        # Unspecified historical ends retain their previous RC(top) behavior.
        start = len(left_end.overhang) if left_end and left_end.protruding_strand == "top" else 0
        stop = len(sequence) - (len(right_end.overhang) if right_end and right_end.protruding_strand == "top" else 0)
        sequence = sequence[start:stop]
        if left_end and left_end.protruding_strand == "bottom":
            sequence = reverse_complement(left_end.overhang) + sequence
        if right_end and right_end.protruding_strand == "bottom":
            sequence += reverse_complement(right_end.overhang)
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
        features=fragment.features,
    )


def reverse_complement_match(left: str, right: str) -> bool:
    return reverse_complement(left) == right


def overhangs_compatible(left: FragmentEnd | None, right: FragmentEnd | None) -> tuple[bool, list[str]]:
    if left is None or right is None:
        return False, ["Both fragment ends must be specified for explicit-end ligation"]

    if left.type == "blunt" or right.type == "blunt":
        if left.type == right.type == "blunt":
            return True, []
        return False, ["Blunt ends cannot ligate to sticky ends without end-repair (use Fill in / Remove overhang end operations)"]

    if left.type != right.type:
        return False, [f"Incompatible sticky-end polarities: {left.type} vs {right.type}"]

    if len(left.overhang) != len(right.overhang):
        return False, [
            f"Sticky-end lengths differ: {len(left.overhang)} vs {len(right.overhang)} nt"
        ]

    if left.protruding_strand and right.protruding_strand:
        if left.protruding_strand != right.protruding_strand and reverse_complement_match(left.overhang, right.overhang):
            return True, []
        return False, [f"Physical protruding strands are not complementary: {left.overhang} vs {right.overhang}"]

    if left.overhang == right.overhang:
        return True, []
    if reverse_complement_match(left.overhang, right.overhang):
        return True, ["Matched by reverse-complement overhang normalization"]
    return False, [f"Sticky ends are not compatible: {left.overhang} vs {right.overhang}"]


def _phosphate_item(end: FragmentEnd) -> dict[str, str]:
    """Omitted while unknown so older request records and hashes are unchanged."""
    return {} if end.five_prime_phosphate == "unknown" else {"five_prime_phosphate": end.five_prime_phosphate}


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
                "protruding_strand": fragment.left_end.protruding_strand,
                **_phosphate_item(fragment.left_end),
            },
            "right_end": None if fragment.right_end is None else {
                "type": fragment.right_end.type,
                "overhang": fragment.right_end.overhang,
                "label": fragment.right_end.label,
                "protruding_strand": fragment.right_end.protruding_strand,
                **_phosphate_item(fragment.right_end),
            },
            "metadata": fragment.metadata or None,
        })
    return payload

