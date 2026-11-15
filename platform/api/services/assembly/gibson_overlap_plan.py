"""Where to cut a synthetic stretch into overlapping fragments, and how long to make each overlap.

Both choices are scored with the same observations the design reports (Tm by the NEB 2/4 rule, GC,
low-complexity 5' end, copies elsewhere in the product), so a suggestion does not offer an overlap
its own design would flag when a clean one is nearby. Nothing here refuses a design: if no clean
window exists the best one is used and the design's own notes say what is weak about it.
"""
from __future__ import annotations

from dataclasses import dataclass

from .gibson_tm import overlap_observations

MAX_OVERLAP = 80
LENGTH_STEPS = (0, 2, 4, 6, 8, 12, 16, 20)
SEARCH_FRACTION = 0.15  # how far from an even split a cut may move, as a share of the fragment length
MIN_SYNTHESIS_FRAGMENT = 125
EDGE_MARGIN = 30


@dataclass(frozen=True)
class Cut:
    start: int  # first base of the shared window in target coordinates (negative = before the origin of a circle)
    length: int


def window(target: str, circular: bool, start: int, length: int) -> str | None:
    n = len(target)
    if length <= 0:
        return None
    if circular:
        if length > n:
            return None
        begin = start % n
        return (target + target)[begin:begin + length]
    if start < 0 or start + length > n:
        return None
    return target[start:start + length]


def _observation_count(target: str, circular: bool, start: int, length: int) -> int | None:
    sequence = window(target, circular, start, length)
    if sequence is None:
        return None
    return len(overlap_observations(sequence, target, circular))


def _lengths(base: int) -> list[int]:
    return sorted({min(MAX_OVERLAP, base + step) for step in LENGTH_STEPS})


def best_cut(target: str, circular: bool, ideal: int, base_length: int, low: int, high: int) -> Cut | None:
    """The window starting in [low, high] with the fewest observations, then nearest the ideal start, then shortest."""
    best: tuple[tuple[int, int, int], Cut] | None = None
    for position, length in enumerate(_lengths(base_length)):
        for start in range(low, high + 1):
            count = _observation_count(target, circular, start, length)
            if count is None:
                continue
            key = (count, abs(start - ideal), length)
            if best is None or key < best[0]:
                best = (key, Cut(start, length))
        if position == 0 and best is not None and best[0][0] == 0:
            break
    return best[1] if best else None


def minimum_count(span: int, closed: bool, max_length: int, overlap: int) -> int:
    """Fewest fragments that keep every one under max_length even after cuts move off an even split."""
    count = 2 if closed else 1
    while (span / count) * (1 + 2 * SEARCH_FRACTION) + overlap + LENGTH_STEPS[-1] > max_length:
        count += 1
        if count > max(2, span // MIN_SYNTHESIS_FRAGMENT):
            break
    return count


def maximum_count(span: int, closed: bool) -> int:
    return max(2 if closed else 1, span // MIN_SYNTHESIS_FRAGMENT)


def cut_stretch(
    target: str, circular: bool, low: int, high: int, count: int, overlap: int, *, closed: bool = False
) -> list[Cut] | None:
    """Overlap windows for a stretch [low, high) cut into `count` fragments.

    A closed stretch is the whole circle (count windows, one of them at the origin);
    an open one needs count - 1 interior windows.
    """
    span = high - low
    radius = max(10, int(SEARCH_FRACTION * span / count))
    steps = range(count) if closed else range(1, count)
    cuts: list[Cut] = []
    for step in steps:
        ideal = low + round(step * span / count) + (radius if closed else -(overlap // 2))
        first, last = ideal - radius, ideal + radius
        if not closed:
            first = max(first, low + EDGE_MARGIN)
            last = min(last, high - EDGE_MARGIN - overlap - LENGTH_STEPS[-1])
        if first > last:
            return None
        cut = best_cut(target, circular, ideal, overlap, first, last)
        if cut is None:
            return None
        cuts.append(cut)
    return cuts


def fragment_spans(cuts: list[Cut], low: int, high: int, *, closed: bool, size: int) -> list[tuple[int, int]]:
    """(start, end) of every fragment so that neighbours share exactly their cut window."""
    if closed:
        count = len(cuts)
        spans = []
        for index, cut in enumerate(cuts):
            following = cuts[(index + 1) % count]
            end = following.start + following.length + (size if index + 1 == count else 0)
            spans.append((cut.start, end))
        return spans
    starts = [low] + [cut.start for cut in cuts]
    ends = [cut.start + cut.length for cut in cuts] + [high]
    return list(zip(starts, ends))


def tune_primer_overlap(target: str, circular: bool, boundary: int, base_length: int, distribution: str) -> int:
    """Overlap length for a join whose overlap rides on primers; the join stays where the match ends."""
    best: tuple[int, int] | None = None
    for length in _lengths(base_length):
        if distribution == "split":
            start = boundary - length // 2
        elif distribution == "right":
            start = boundary - length
        else:
            start = boundary
        count = _observation_count(target, circular, start, length)
        if count is None:
            continue
        if count == 0:
            return length
        if best is None or (count, length) < best:
            best = (count, length)
    return best[1] if best else base_length
