"""Exact-match region finder: cover a target with supplied-source regions and new DNA.

Only the supplied sources are searched. A source region matches the target exactly,
in either orientation, and (for circular sources) across the origin. Coordinates
follow the assembly slices: half-open, ``wraps_origin`` when the run crosses the
source origin (then ``end <= start``).

Algorithm: a k-mer index over both strands of every source, then for each target
position the longest exact extension among the k-mer hits. After a match the scan
jumps to its end, so the work is near-linear in target plus matched length.
Taking the longest match at each position keeps the number of matched segments low.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

COMP = str.maketrans("ACGT", "TGCA")


def reverse_complement(sequence: str) -> str:
    return sequence.translate(COMP)[::-1]


@dataclass(frozen=True)
class Source:
    id: str
    sequence: str
    circular: bool = False


@dataclass(frozen=True)
class Piece:
    """One target interval: a source match (``source`` set) or new DNA (``sequence`` set)."""
    start: int
    end: int
    source: str | None = None
    strand: int = 0
    anchor: int = 0  # source position of target[start]; moves -1 per base on strand -1
    n: int = 0  # length of the source
    sequence: str | None = None

    @property
    def is_match(self) -> bool:
        return self.source is not None

    def pos(self, t: int) -> int:
        return (self.anchor + self.strand * (t - self.start)) % self.n

    def sub(self, a: int, b: int) -> "Piece":
        if self.is_match:
            return replace(self, start=a, end=b, anchor=self.pos(a))
        return replace(self, start=a, end=b, sequence=(self.sequence or "")[a - self.start:b - self.start])

    def region(self) -> tuple[int, int, bool]:
        """Forward-strand source (start, end, wraps_origin) of a match."""
        length = self.end - self.start
        first = self.pos(self.start) if self.strand == 1 else self.pos(self.end - 1)
        wraps = first + length > self.n
        return first, (first + length - self.n if wraps else first + length), wraps

    def forward_positions(self) -> list[int]:
        """Source positions of the matched run in forward-strand order."""
        positions = [self.pos(t) for t in range(self.start, self.end)]
        return positions if self.strand == 1 else positions[::-1]


@dataclass(frozen=True)
class Cover:
    rotation: int  # rotated[t] == target[(t + rotation) % len(target)]
    pieces: list[Piece]


def _common_prefix(a: str, ai: int, b: str, bi: int, limit: int) -> int:
    n = 0
    while n < limit:
        step = min(512, limit - n)
        if a[ai + n:ai + n + step] == b[bi + n:bi + n + step]:
            n += step
            continue
        while a[ai + n] == b[bi + n]:
            n += 1
        return n
    return n


class Matcher:
    def __init__(self, sources: list[Source], min_match: int = 30):
        self.sources = sources
        self.min_match = min_match
        self.k = min(min_match, 24)
        self._strands: dict[tuple[int, int], str] = {}
        self._index: dict[str, list[tuple[int, int, int]]] = {}
        for si, source in enumerate(sources):
            n = len(source.sequence)
            if n < min_match:
                continue
            for strand, seq in ((1, source.sequence), (-1, reverse_complement(source.sequence))):
                text = seq + seq if source.circular else seq
                self._strands[(si, strand)] = text
                for p in range(n if source.circular else n - self.k + 1):
                    self._index.setdefault(text[p:p + self.k], []).append((si, strand, p))

    def longest(self, target: str, i: int) -> Piece | None:
        """Longest exact match starting at target[i] (>= min_match), or None."""
        hits = self._index.get(target[i:i + self.k])
        if not hits:
            return None
        best, best_len = None, self.min_match - 1
        for si, strand, p in hits:
            source = self.sources[si]
            n = len(source.sequence)
            room = n if source.circular else n - p
            length = _common_prefix(self._strands[(si, strand)], p, target, i, min(room, len(target) - i))
            if length > best_len:
                best_len, best = length, (si, strand, p)
        if best is None:
            return None
        si, strand, p = best
        n = len(self.sources[si].sequence)
        anchor = p % n if strand == 1 else (n - 1 - p) % n
        return Piece(i, i + best_len, self.sources[si].id, strand, anchor, n)  # noqa

    def cover(self, target: str) -> list[Piece]:
        pieces: list[Piece] = []
        gap = 0
        i = 0
        while i < len(target):
            match = self.longest(target, i)
            if match is None:
                i += 1
                continue
            if i > gap:
                pieces.append(Piece(gap, i, sequence=target[gap:i]))
            pieces.append(match)
            i = gap = match.end
        if gap < len(target):
            pieces.append(Piece(gap, len(target), sequence=target[gap:]))
        return pieces


def cover_target(target: str, sources: list[Source], *, min_match: int = 30, circular: bool = False,
                 origin: int | None = None, matcher: Matcher | None = None) -> Cover:
    """Cover ``target`` by source regions and new DNA.

    A circular target is rotated to ``origin`` when given; otherwise to the start of
    the match that straddles the target origin, so no match is split by the origin.
    """
    matcher = matcher or Matcher(sources, min_match)
    rotation = (origin or 0) % len(target) if circular else 0
    pieces = matcher.cover(target[rotation:] + target[:rotation])
    if circular and origin is None and len(pieces) > 1:
        # Keep the origin from splitting what is really one piece.
        first, last = pieces[0], pieces[-1]
        joined = (not first.is_match and not last.is_match) or (
            first.is_match and last.is_match and first.source == last.source and first.strand == last.strand
            and first.pos(first.start) == last.pos(last.end))
        if joined:
            rotation = (rotation + last.start) % len(target)
            pieces = matcher.cover(target[rotation:] + target[:rotation])
    return Cover(rotation, pieces)
