"""Apply an overlap the user set on a join between fragments BMS synthesised, by moving their ends.

Neighbouring synthetic fragments carry their shared overlap inside the sequences that get ordered, so
changing the overlap means ordering different sequences. A join is re-cut only when at least one side is
marked as BMS-synthesised ("synthetic_fragment" in the fragment metadata); a supplied fragment is never
rewritten. Growing copies bases from the neighbour's own sequence and trimming drops end bases, so the
assembled product is unchanged, and the design's independent validation still has to reproduce it.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable

from .types import OrientedFragment

SYNTHETIC_FLAG = "synthetic_fragment"
SYNTHESIS_LIMIT = "max_synthesis_length"
PART_MIN = 41  # a shorter piece is carried as a linker on its neighbours' primers, not as a part


@dataclass
class RecutResult:
    fragments: list[OrientedFragment]
    done: dict[int, list[str]] = field(default_factory=dict)  # junction (index of its left fragment) -> notes
    refused: dict[int, str] = field(default_factory=dict)
    changed: list[str] = field(default_factory=list)  # names of fragments whose sequence changed


def is_synthetic(fragment: OrientedFragment) -> bool:
    return fragment.metadata.get(SYNTHETIC_FLAG) is True and fragment.left_end is None and fragment.right_end is None


def _limit(fragment: OrientedFragment) -> int | None:
    value = fragment.metadata.get(SYNTHESIS_LIMIT)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _room(fragment: OrientedFragment, length: int) -> int:
    limit = _limit(fragment)
    return 10**9 if limit is None else max(0, limit - length)


def _allocate(total: int, first: int | None, second: int | None) -> tuple[int, int] | None:
    """Split `total` bases between two sides. A side is None when it cannot move, else the most it can take."""
    if first is None and second is None:
        return None
    if first is None:
        return (0, total) if total <= second else None
    if second is None:
        return (total, 0) if total <= first else None
    x = (total + 1) // 2
    y = total - x
    if x > first:
        x, y = first, total - first
    elif y > second:
        x, y = total - second, second
    return (x, y) if 0 <= x <= first and 0 <= y <= second else None


def recut_synthetic(
    fragments: list[OrientedFragment],
    free: list[bool],
    requests: list[tuple[int, int, int]],
    circular: bool,
    homology: Callable[[str, str], int],
) -> RecutResult:
    """`free[i]` says fragment i may be re-cut; `requests` are (left index, right index, wanted overlap)."""
    seqs = [fragment.sequence for fragment in fragments]
    count = len(fragments)
    result = RecutResult(fragments=list(fragments))
    touched: dict[int, list[int]] = {}
    for a, b, wanted in requests:
        if a == b or not (b == a + 1 or (circular and a == count - 1 and b == 0)):
            continue
        left, right = fragments[a], fragments[b]
        if left.right_end is not None or right.left_end is not None:
            continue  # a retained sticky end is not ours to move
        shared = homology(seqs[a], seqs[b])
        if not shared or shared == wanted:
            continue
        left_free, right_free = free[a], free[b]
        if not (left_free or right_free):
            continue
        total = abs(wanted - shared)
        growing = wanted > shared
        if growing:
            caps = (len(seqs[b]) - shared if left_free else None, len(seqs[a]) - shared if right_free else None)
            limited = tuple(
                None if cap is None else max(0, min(cap, _room(fragment, len(seqs[index]))))
                for cap, fragment, index in ((caps[0], left, a), (caps[1], right, b))
            )
            split = _allocate(total, *limited) or _allocate(total, *caps)
        else:
            caps = tuple(
                None if not free else max(0, len(seqs[index]) - wanted - 1)
                for free, index in ((left_free, a), (right_free, b))
            )
            split = _allocate(total, *caps)
        if split is None:
            result.refused[a] = (
                f"The overlap you set ({wanted} nt) could not be re-cut: {left.name} and {right.name} "
                f"do not have enough sequence to move their ends that far, so the existing {shared} nt overlap is kept"
            )
            continue
        x, y = split
        if growing:
            new_a = seqs[a] + seqs[b][shared:shared + x]
            new_b = seqs[a][len(seqs[a]) - shared - y:len(seqs[a]) - shared] + seqs[b]
        else:
            new_a = seqs[a][:len(seqs[a]) - x]
            new_b = seqs[b][y:]
        if len(new_a) < PART_MIN or len(new_b) < PART_MIN or homology(new_a, new_b) != wanted:
            result.refused[a] = (
                f"The overlap you set ({wanted} nt) could not be re-cut: the new ends of {left.name} and {right.name} "
                f"would not share exactly that overlap, so the existing {shared} nt overlap is kept"
            )
            continue
        sign = 1 if growing else -1
        moved = [f"{name} {sign * amount:+d} nt at its {end} end" for name, amount, end in
                 ((left.name, x, "right"), (right.name, y, "left")) if amount]
        result.done[a] = [f"Overlap re-cut from {shared} nt to {wanted} nt ({'; '.join(moved)})"]
        seqs[a], seqs[b] = new_a, new_b
        touched.setdefault(a, []).append(a)
        touched.setdefault(b, []).append(a)
    for index, junctions in touched.items():
        fragment = fragments[index]
        if seqs[index] != fragment.sequence:
            result.fragments[index] = replace(fragment, sequence=seqs[index])
            result.changed.append(fragment.name)
        limit = _limit(fragment)
        if limit is not None and len(seqs[index]) > limit:
            result.done[junctions[-1]].append(
                f"{fragment.name} is now {len(seqs[index])} nt, over its {limit} nt synthesis limit"
            )
    return result
