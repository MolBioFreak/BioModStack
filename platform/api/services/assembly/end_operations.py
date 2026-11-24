"""Idealized end operations on one linear duplex fragment.

The fragment is held as two explicit strands on one reference axis. ``sequence`` is the
TOP span (index 0 = its first base). A top-strand protrusion lies inside that span; a
bottom-strand protrusion lies outside it. Each end operation moves the strand extents at
the selected end only:

* fill-in (5' overhang): the recessed 3' end is extended, the end becomes blunt.
* 3'->5' exonuclease (3' overhang): the protruding 3' strand is trimmed, blunt.
* single-strand nuclease (5' overhang): the protruding 5' strand is removed, blunt, 5' phosphate.
* phosphorylate / dephosphorylate: only the end's 5' phosphate state changes.

Applying an operation to an end it does not describe (fill-in of a 3' overhang, removal of
an overhang that is not there) leaves that end unchanged and says so in ``notes``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from services.molbio_ops import reverse_complement

from .common import normalize_sequence
from .feature_mapping import map_stored_features
from .types import AssemblyError, FragmentEnd

Operation = Literal["fill_in", "remove_3_overhang", "remove_5_overhang_nuclease", "phosphorylate", "dephosphorylate"]
EndSelector = Literal["left", "right", "both"]
OPERATIONS = ("fill_in", "remove_3_overhang", "remove_5_overhang_nuclease", "phosphorylate", "dephosphorylate")
_APPLIES_TO = {"fill_in": "sticky_5", "remove_3_overhang": "sticky_3", "remove_5_overhang_nuclease": "sticky_5"}
_LABEL = {"fill_in": "filled in", "remove_3_overhang": "3' overhang removed",
          "remove_5_overhang_nuclease": "5' overhang removed", "phosphorylate": "5' phosphorylated",
          "dephosphorylate": "5' dephosphorylated"}


@dataclass(slots=True)
class EndOperationResult:
    sequence: str
    left_end: FragmentEnd
    right_end: FragmentEnd
    features: list[dict]
    bases_added: int
    bases_removed: int
    notes: list[str] = field(default_factory=list)
    label: str = ""


def _blunt(end: FragmentEnd | None, phosphate: str | None = None) -> FragmentEnd:
    """Blunt replacement for an end. Fill-in and 3'->5' exonuclease leave the 5' terminus alone;
    pass ``phosphate`` when the operation creates a new 5' terminus."""
    return FragmentEnd(type="blunt", overhang="", label=end.label if end else None,
                       five_prime_phosphate=phosphate or (end.five_prime_phosphate if end else "unknown"))


def _strand(end: FragmentEnd, side: str) -> str:
    """Physical protruding strand. Historical unspecified ends follow the antiparallel geometry."""
    if end.protruding_strand:
        return end.protruding_strand
    if side == "left":
        return "top" if end.type == "sticky_5" else "bottom"
    return "bottom" if end.type == "sticky_5" else "top"


def _extents(sequence: str, left: FragmentEnd, right: FragmentEnd):
    """Return (x, lo, ts, te, bs, be): x[i - lo] is the top-orientation base at reference position i."""
    n = len(sequence)
    ts, te, bs, be = 0, n, 0, n
    lo_bases = hi_bases = ""
    if left.type != "blunt":
        k = len(left.overhang)
        if k > n:
            raise AssemblyError("Left overhang is longer than the fragment")
        if _strand(left, "left") == "top":
            bs = k
        else:
            bs = -k
            lo_bases = reverse_complement(left.overhang)
    if right.type != "blunt":
        k = len(right.overhang)
        if k > n:
            raise AssemblyError("Right overhang is longer than the fragment")
        if _strand(right, "right") == "top":
            be = n - k
        else:
            be = n + k
            hi_bases = reverse_complement(right.overhang)
    return lo_bases + sequence + hi_bases, -len(lo_bases), ts, te, bs, be


def apply_end_operation(
    sequence: str, left_end: FragmentEnd | None, right_end: FragmentEnd | None, *,
    operation: str, end: str, features: list[dict] | None = None,
) -> EndOperationResult:
    if operation not in OPERATIONS:
        raise AssemblyError(f"Unsupported end operation '{operation}'")
    if end not in {"left", "right", "both"}:
        raise AssemblyError(f"Unsupported end selection '{end}'")
    sequence = normalize_sequence(sequence)
    left = left_end or FragmentEnd(type="blunt")
    right = right_end or FragmentEnd(type="blunt")
    sides = ("left", "right") if end == "both" else (end,)
    notes: list[str] = []

    if operation in ("phosphorylate", "dephosphorylate"):
        state = "phosphorylated" if operation == "phosphorylate" else "unphosphorylated"
        new = {"left": left, "right": right}
        for side in sides:
            old = new[side]
            new[side] = FragmentEnd(type=old.type, overhang=old.overhang, label=old.label,
                                    protruding_strand=old.protruding_strand, five_prime_phosphate=state)
        mapped = map_stored_features(features, sequence, "linear", list(range(len(sequence))))
        return EndOperationResult(sequence, new["left"], new["right"], mapped, 0, 0, notes, _LABEL[operation])

    x, lo, ts, te, bs, be = _extents(sequence, left, right)
    applies = _APPLIES_TO[operation]
    new_ts, new_te, new_bs, new_be = ts, te, bs, be
    new_ends = {"left": left, "right": right}
    added = removed = 0
    for side in sides:
        current = new_ends[side]
        if current.type == "blunt":
            notes.append(f"{side.capitalize()} end is already blunt; nothing to {'fill in' if operation == 'fill_in' else 'remove'}.")
            continue
        if current.type != applies:
            what = {"fill_in": "Fill-in does not apply to a 3' overhang",
                    "remove_3_overhang": "This end has a 5' overhang, not a 3' overhang",
                    "remove_5_overhang_nuclease": "This end has a 3' overhang, not a 5' overhang"}[operation]
            notes.append(f"{what} ({side} end); end left unchanged.")
            continue
        if side == "left":
            edge = min(ts, bs) if operation == "fill_in" else max(ts, bs)
            new_ts = new_bs = edge
        else:
            edge = max(te, be) if operation == "fill_in" else min(te, be)
            new_te = new_be = edge
        count = len(current.overhang)
        if operation == "fill_in":
            added += count
        else:
            removed += count
        # Mung bean / S1 cut the protruding strand and leave a 5'-phosphoryl terminus on the duplex.
        new_ends[side] = _blunt(current, "phosphorylated" if operation == "remove_5_overhang_nuclease" else None)
    if new_te <= new_ts:
        raise AssemblyError("The end operation would remove the whole fragment")

    out = x[new_ts - lo:new_te - lo]
    positions = [p if 0 <= p < len(sequence) else None for p in range(new_ts, new_te)]
    mapped = map_stored_features(features, sequence, "linear", positions)
    return EndOperationResult(out, new_ends["left"], new_ends["right"], mapped, added, removed, notes, _LABEL[operation])
