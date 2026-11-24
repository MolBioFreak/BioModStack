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
MIN_RADIUS = 10  # a cut can always move this far from an even split

# Default overlap by fragment count. NEB's NEBuilder HiFi DNA Assembly protocol (E2621/E5520) gives 15-20 nt overlaps
# for 2-3 fragments and 20-30 nt for 4-6 fragments, noting that efficiency falls as the number of fragments rises
# (https://www.neb.com/en/protocols/nebuilder-hifi-dna-assembly-reaction-protocol). The default is the top of each
# range: 20 nt is also the shortest overlap the NEBuilder tool itself read as clean, and the longer end of the range
# suits the larger assembly. It is only a default: an overlap the user sets wins.
FEW_FRAGMENTS = 3
FEW_FRAGMENT_OVERLAP = 20
MANY_FRAGMENT_OVERLAP = 30
DOCUMENTED_FRAGMENTS = 6  # NEB's HiFi guidance (overlaps, DNA amounts) stops at 6 fragments


def scaled_overlap(count: int) -> int:
    """Overlap NEB's guidance gives for `count` fragments in one assembly."""
    return MANY_FRAGMENT_OVERLAP if count > FEW_FRAGMENTS else FEW_FRAGMENT_OVERLAP


def overlap_note(overlap: int, count: int) -> str:
    shown = f"{count} fragment{'s' if count != 1 else ''}"
    guide = "20-30 nt for 4-6 fragments" if overlap == MANY_FRAGMENT_OVERLAP else "15-20 nt for 2-3 fragments"
    return f"Overlap {overlap} nt for {shown}: NEBuilder HiFi guidance is {guide}. Set an overlap to use a different one."


def beyond_guidance_note(count: int) -> str | None:
    if count <= DOCUMENTED_FRAGMENTS:
        return None
    return (f"{count} fragments is more than the 2-6 NEB's HiFi guidance covers; "
            f"{MANY_FRAGMENT_OVERLAP} nt is the top of its 4-6 fragment range, and assembling in two stages is the usual alternative.")


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
    """Fewest fragments that keep every one under max_length with the cuts allowed the least room to move."""
    count = 2 if closed else 1
    while span / count + 2 * MIN_RADIUS + overlap + LENGTH_STEPS[-1] > max_length:
        count += 1
        if count > max(2, span // MIN_SYNTHESIS_FRAGMENT):
            break
    return count


def maximum_count(span: int, closed: bool) -> int:
    return max(2 if closed else 1, span // MIN_SYNTHESIS_FRAGMENT)


def cut_stretch(
    target: str, circular: bool, low: int, high: int, count: int, overlap: int, *, closed: bool = False,
    max_length: int | None = None,
) -> list[Cut] | None:
    """Overlap windows for a stretch [low, high) cut into `count` fragments.

    A closed stretch is the whole circle (count windows, one of them at the origin);
    an open one needs count - 1 interior windows. With max_length, cuts only move as far from an even
    split as keeps every fragment (its overlaps included) within that length.
    """
    span = high - low
    radius = max(MIN_RADIUS, int(SEARCH_FRACTION * span / count))
    if max_length is not None:
        room = int((max_length - overlap - LENGTH_STEPS[-1] - span / count) // 2)
        radius = max(MIN_RADIUS, min(radius, room))
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
