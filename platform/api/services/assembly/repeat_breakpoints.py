"""Repeat-aware breakpoints for overlap (Gibson / HiFi) assemblies.

An experienced designer never puts a junction inside a repeated sequence and keeps the copies of a repeat inside one fragment
whenever a layout allows it. This module finds the repeated sequence of a target and turns that into two rules for choosing
breakpoints:

* clean breakpoint: the zone around a cut (the overlap and its flanks) contains no base covered by a repeated k-mer on either
  strand, so every overlap and primer site is unique;
* containment: every copy of a repeat of at least ``overlap`` bases lies in the same fragment, i.e. no cut falls inside the
  shortest arc that covers all copies.

The rules are applied as far as a feasible fragment layout exists. Containment is attempted repeat by repeat, shortest arc
first, and a repeat is given up (and reported, with the fragment length it would need) when it cannot be kept in one fragment or
when keeping it would leave no layout for the others. When even the clean-breakpoint rule leaves no layout, no restriction is
applied, which is the behaviour before this module existed. A target without repeated k-mers is never restricted.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

CLEAN_K_MIN = 20
MAX_K = 31  # 2 bits per base must stay below the sentinel bit of a uint64
MAX_ANALYSED_BP = 400_000
MAX_FAMILIES = 40
MAX_ORIGIN_TRIES = 12
_SENTINEL = np.uint64(1) << np.uint64(63)

_LUT = np.full(256, 255, dtype=np.uint8)
for _i, _ch in enumerate("ACGT"):
    _LUT[ord(_ch)] = _i
    _LUT[ord(_ch.lower())] = _i


def _codes(seq: str, k: int, circular: bool) -> np.ndarray:
    """Canonical (strand-independent) 2-bit k-mer code per start; a window containing a non-ACGT base gets a unique code."""
    arr = _LUT[np.frombuffer(seq.encode("ascii", "replace"), dtype=np.uint8)]
    n = len(arr)
    ext = np.concatenate([arr, arr[: k - 1]]) if circular and n >= k else arr
    m = len(ext) - k + 1
    if m <= 0:
        return np.zeros(0, dtype=np.uint64)
    fwd = np.zeros(m, dtype=np.uint64)
    rev = np.zeros(m, dtype=np.uint64)
    bad = np.zeros(m, dtype=bool)
    for j in range(k):
        col = ext[j : j + m]
        bad |= col == 255
        col64 = np.where(col == 255, 0, col).astype(np.uint64)
        fwd = (fwd << np.uint64(2)) | col64
        rev |= (np.uint64(3) - col64) << np.uint64(2 * j)
    codes = np.minimum(fwd, rev)
    if bad.any():
        codes = np.where(bad, _SENTINEL + np.arange(m, dtype=np.uint64), codes)
    return codes


def _repeated_starts(codes: np.ndarray):
    """(mask of starts whose canonical k-mer occurs at another start, groups of starts sharing one k-mer)."""
    m = len(codes)
    if m == 0:
        return np.zeros(0, dtype=bool), []
    order = np.argsort(codes, kind="stable")
    ordered = codes[order]
    cut = np.flatnonzero(ordered[1:] != ordered[:-1]) + 1
    bounds = np.concatenate([[0], cut, [m]])
    mask = np.zeros(m, dtype=bool)
    groups = []
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        if hi - lo > 1:
            members = order[lo:hi]
            mask[members] = True
            groups.append(members)
    return mask, groups


def _cover(starts_mask: np.ndarray, k: int, n: int, circular: bool) -> np.ndarray:
    """Bases covered by any repeated k-mer."""
    diff = np.zeros(n + k + 1, dtype=np.int32)
    idx = np.flatnonzero(starts_mask)
    np.add.at(diff, idx, 1)
    np.add.at(diff, idx + k, -1)
    cov = np.cumsum(diff)[: n + k - 1] > 0
    out = cov[:n].copy()
    if circular and len(cov) > n:
        out[: len(cov) - n] |= cov[n:]
    return out


def _runs(mask: np.ndarray, circular: bool) -> list[tuple[int, int]]:
    """Maximal runs of True as (start, end); a run that crosses the origin of a circular mask has end > len(mask)."""
    n = len(mask)
    if n == 0 or not mask.any():
        return []
    if mask.all():
        return [(0, n)]
    padded = np.concatenate([[False], mask, [False]])
    change = np.flatnonzero(padded[1:] != padded[:-1])
    runs = [(int(a), int(b)) for a, b in zip(change[0::2], change[1::2])]
    if circular and len(runs) > 1 and runs[0][0] == 0 and runs[-1][1] == n:
        first, last = runs[0], runs[-1]
        runs = [(last[0], first[1] + n)] + runs[1:-1]
    return runs


@dataclass
class Family:
    copies: list[tuple[int, int]]  # [start, end); end may exceed the length when a copy crosses the origin
    arc: tuple[int, int] | None  # shortest arc covering every copy (start, end); None for a single run
    arc_len: int
    longest: int


@dataclass
class RegionPlan:
    """Allowed cut positions for one region, in the frame of the array they were computed for."""

    valid: np.ndarray | None  # boolean per cut position; None = unrestricted
    contained: list[Family] = field(default_factory=list)
    uncontained: list[Family] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    needed: int = 0  # longest fragment (bp) that would keep every given-up repeat whole
    unrestricted_reason: str | None = None


@dataclass
class CutPlan(RegionPlan):
    origin: int = 0  # circular targets: cut position (original frame) at which the decomposition starts
    witness: list[int] = field(default_factory=list)  # interior cuts of one feasible layout, in the plan's frame

    @property
    def restricts(self) -> bool:
        return self.valid is not None


def _fmt(copies: list[tuple[int, int]], n: int) -> str:
    return ", ".join(f"{a % n + 1}-{(b - 1) % n + 1}" for a, b in copies)


class RepeatAnalysis:
    def __init__(self, seq: str, *, circular: bool, overlap: int, min_anneal: int = 15):
        self.seq = seq.upper()
        self.n = len(seq)
        self.circular = circular
        self.overlap = overlap
        self.k_clean = max(CLEAN_K_MIN, min(MAX_K, min_anneal + 2))
        self.k_family = max(CLEAN_K_MIN, min(MAX_K, overlap))
        self.half = max(overlap // 2 + 4, self.k_clean + 8)
        self.rep_mask = np.zeros(self.n, dtype=bool)
        self.families: list[Family] = []
        self._clean_cache: np.ndarray | None = None
        if self.n < 2 * self.k_clean or self.n > MAX_ANALYSED_BP:
            return
        codes = _codes(self.seq, self.k_clean, circular)
        starts, _ = _repeated_starts(codes)
        self.rep_mask = _cover(starts, self.k_clean, self.n, circular)
        if self.rep_mask.any():
            self._find_families(self.k_family)

    @property
    def has_repeats(self) -> bool:
        return bool(self.rep_mask.any())

    # ---- repeat families: runs of repeated sequence whose copies share k-mers
    def _find_families(self, k: int) -> None:
        if self.n < k:
            return
        codes = _codes(self.seq, k, self.circular)
        starts, groups = _repeated_starts(codes)
        if not starts.any():
            return
        runs = _runs(_cover(starts, k, self.n, self.circular), self.circular)
        run_of = np.full(self.n, -1, dtype=np.int64)
        for rid, (a, b) in enumerate(runs):
            run_of[np.arange(a, b) % self.n] = rid
        parent = list(range(len(runs)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for members in groups:
            ids = sorted({int(run_of[int(p) % self.n]) for p in members} - {-1})
            for other in ids[1:]:
                parent[find(other)] = find(ids[0])
        by_root: dict[int, list[tuple[int, int]]] = {}
        for rid, run in enumerate(runs):
            by_root.setdefault(find(rid), []).append(run)
        families = []
        for copies in by_root.values():
            copies = sorted(copies)
            arc = self._arc(copies)
            families.append(Family(copies=copies, arc=arc, arc_len=(arc[1] - arc[0]) if arc else 0, longest=max(b - a for a, b in copies)))
        families.sort(key=lambda f: -f.longest)
        self.families = families[:MAX_FAMILIES]

    def _arc(self, copies: list[tuple[int, int]]) -> tuple[int, int] | None:
        if len(copies) < 2:
            return None  # one run on its own (tandem or low-complexity): only the clean-breakpoint rule applies
        n = self.n
        if not self.circular:
            return (min(a for a, _ in copies), max(b for _, b in copies))
        spans = sorted(((a % n), (a % n) + (b - a)) for a, b in copies)
        best_gap, best_after = -(10**12), 0
        for i, (_, end) in enumerate(spans):
            j = (i + 1) % len(spans)
            nxt = spans[j][0] + (n if j == 0 else 0)
            if nxt - end > best_gap:
                best_gap, best_after = nxt - end, j
        start = spans[best_after][0]
        return (start, start + n - max(best_gap, 0))

    # ---- allowed cuts
    def clean_cuts(self) -> np.ndarray:
        """Cut positions 0..n whose +-half zone holds no repeated base. Positions 0 and n are the same cut when circular."""
        if self._clean_cache is not None:
            return self._clean_cache
        n, half = self.n, self.half
        valid = np.ones(n + 1, dtype=bool)
        if self.rep_mask.any():
            rep = self.rep_mask.astype(np.int32)
            pos = np.arange(n + 1)
            if self.circular:
                pre = np.concatenate([[0], np.cumsum(np.concatenate([rep, rep, rep]))])
                valid = (pre[pos + n + half] - pre[pos + n - half]) == 0
                valid[n] = valid[0]
            else:
                pre = np.concatenate([[0], np.cumsum(np.concatenate([np.zeros(half, np.int32), rep, np.zeros(half, np.int32)]))])
                valid = (pre[pos + 2 * half] - pre[pos]) == 0
                valid[0] = valid[n] = True  # the two ends of a linear target are not junctions
        self._clean_cache = valid
        return valid

    @staticmethod
    def region_has_layout(valid: np.ndarray, a: int, b: int, min_len: int, max_len: int) -> bool:
        """Can [a, b) be cut into pieces of min_len..max_len using only allowed interior cuts (a and b are fixed ends)?"""
        if b - a <= max_len:
            return b - a >= min(min_len, b - a)
        interior = np.flatnonzero(valid[a + 1 : b]) + a + 1
        reach = [a]
        j = 0  # index of the largest reachable position <= q - min_len
        for q in list(interior) + [b]:
            while j + 1 < len(reach) and reach[j + 1] <= q - min_len:
                j += 1
            if reach[j] <= q - min_len and reach[j] >= q - max_len:
                if q == b:
                    return True
                reach.append(int(q))
        return False

    @staticmethod
    def layout(valid: np.ndarray, a: int, b: int, min_len: int, max_len: int) -> list[int] | None:
        """Interior cuts of a layout of [a, b) into pieces of min_len..max_len using only allowed cuts, with the fewest pieces
        (balanced when the balanced search succeeds). None when there is none."""
        if b - a <= max_len:
            return [] if b - a >= min(min_len, b - a) else None
        got = choose_boundaries(valid, a, b, max_len, min_piece=min_len)
        if got is not None:
            return got
        pos = [a] + [int(q) for q in np.flatnonzero(valid[a + 1 : b]) + a + 1] + [b]
        arr = np.array(pos)
        best = np.full(len(pos), 10**9, dtype=np.int64)
        par = np.full(len(pos), -1, dtype=np.int64)
        best[0] = 0
        for t in range(1, len(pos)):
            lo = int(np.searchsorted(arr, arr[t] - max_len, side="left"))
            hi = int(np.searchsorted(arr, arr[t] - min_len, side="right"))
            if hi > lo:
                j = lo + int(np.argmin(best[lo:hi]))
                if best[j] < 10**9:
                    best[t], par[t] = best[j] + 1, j
        if best[-1] >= 10**9:
            return None
        out, t = [], len(pos) - 1
        while par[t] >= 0:
            t = int(par[t])
            if t:
                out.append(int(arr[t]))
        return out[::-1]

    def _arcs_in(self, a: int, b: int, shift: int = 0, *, whole: bool = False):
        """Repeat families as (family, arc) with the arc in the frame that starts at cut ``shift``: from the first copy's start to
        the last copy's end, the only arc that does not cross the frame start. Families with a copy outside [a, b) are skipped
        (they keep only the clean-breakpoint rule); with ``whole`` a copy crossing the frame start is returned separately."""
        n = self.n
        inside, straddling = [], []
        for fam in self.families:
            if fam.arc is None or fam.longest < self.k_family:
                continue
            spans = []
            for s, e in fam.copies:
                fs = (s - shift) % n if self.circular else s
                spans.append((fs, fs + (e - s)))
            if self.circular and any(e > n for _, e in spans):
                if whole:
                    straddling.append(fam)
                continue
            lo, hi = min(s for s, _ in spans), max(e for _, e in spans)
            if a <= lo and hi <= b:
                inside.append((fam, (lo, hi)))
        return sorted(inside, key=lambda item: item[1][1] - item[1][0]), straddling

    def _forbid(self, valid: np.ndarray, arc: tuple[int, int]) -> np.ndarray:
        out = valid.copy()
        out[arc[0] + 1 : arc[1]] = False
        return out

    def _plan_region(self, valid: np.ndarray, a: int, b: int, min_len: int, max_len: int, arcs, straddling=(), shown_max: int | None = None) -> RegionPlan:
        shown_max = shown_max or max_len
        plan = RegionPlan(valid=valid)
        for fam in straddling:
            plan.uncontained.append(fam)
            plan.needed = max(plan.needed, fam.arc_len + 2 * self.half)
            plan.notes.append(f"The {fam.longest} bp repeat at {_fmt(fam.copies, self.n)} crosses the start of the construct, so its copies are split across fragments.")
        for fam, arc in arcs:
            need = (arc[1] - arc[0]) + 2 * self.half
            if need > max_len:
                plan.uncontained.append(fam)
                plan.needed = max(plan.needed, need)
                plan.notes.append(
                    f"The {fam.longest} bp repeat at {_fmt(fam.copies, self.n)} needs one {need} bp fragment to stay whole but fragments are limited to {shown_max} bp; its copies are split across fragments."
                )
                continue
            trial = self._forbid(plan.valid, arc)
            if self.region_has_layout(trial, a, b, min_len, max_len):
                plan.valid = trial
                plan.contained.append(fam)
            else:
                plan.uncontained.append(fam)
                plan.needed = max(plan.needed, need)
                plan.notes.append(
                    f"The {fam.longest} bp repeat at {_fmt(fam.copies, self.n)} could not be kept in one fragment together with the other repeats; its copies are split across fragments."
                )
        if plan.contained:
            lengths = [f.longest for f in plan.contained]
            span = f"{min(lengths)} bp" if min(lengths) == max(lengths) else f"{min(lengths)}-{max(lengths)} bp"
            plan.notes.insert(0, f"Breakpoints avoid repeated sequence and keep {len(plan.contained)} repeat group(s) of {span} inside single fragments.")
        elif self.has_repeats:
            plan.notes.insert(0, "Breakpoints avoid repeated sequence.")
        return plan

    # ---- decomposition of the whole target (circular targets choose their own start)
    def _origin_candidates(self, clean: np.ndarray, max_len: int) -> list[int]:
        """Start cuts worth trying for a circular target: next to the origin, just before each repeat that could be kept whole
        (so its fragment can begin there) and a spread over the rest of the construct."""
        n = self.n
        pos = np.flatnonzero(clean[:n])
        if not len(pos):
            return []
        out: list[int] = [int(pos[np.argmin(np.minimum(pos, n - pos))])]
        for fam in self.families:
            if fam.arc is None or fam.longest < self.k_family or fam.arc_len + 2 * self.half > max_len:
                continue
            for copy_start, _ in fam.copies:
                before = pos[pos <= copy_start % n]
                out.append(int(before[-1]) if len(before) else int(pos[-1]))
        spread = 2 if n > 100_000 else 12
        out += [int(pos[int(i)]) for i in np.linspace(0, len(pos) - 1, spread)]
        seen: set[int] = set()
        return [c for c in out if not (c in seen or seen.add(c))][: 3 + 2 * len(self.families) + spread]

    def _plan_from(self, clean: np.ndarray, origin: int, min_len: int, max_len: int, shown_max: int | None = None) -> CutPlan | None:
        n = self.n
        idx = (np.arange(n + 1) + origin) % n if self.circular else np.arange(n + 1)
        valid = clean[idx].copy()
        valid[0] = valid[n] = True
        if not self.region_has_layout(valid, 0, n, min_len, max_len):
            return None
        inside, straddling = self._arcs_in(0, n, shift=origin, whole=True)
        region = self._plan_region(valid, 0, n, min_len, max_len, inside, straddling, shown_max)
        return CutPlan(valid=region.valid, contained=region.contained, uncontained=region.uncontained, notes=region.notes, needed=region.needed, origin=origin)

    def plan(self, *, min_len: int, max_len: int, shown_max: int | None = None) -> CutPlan:
        if not self.has_repeats:
            return CutPlan(valid=None)
        clean = self.clean_cuts()
        n = self.n
        if not self.circular:
            plans = [self._plan_from(clean, 0, min_len, max_len, shown_max)]
        else:
            plans = [self._plan_from(clean, c, min_len, max_len, shown_max) for c in self._origin_candidates(clean, max_len)]
        plans = [p for p in plans if p is not None]
        if not plans:
            return CutPlan(valid=None, unrestricted_reason="repeated sequence sits at every possible breakpoint")
        # keep the most repeat groups whole, then the fewest split, then the start closest to the submitted origin
        best = min(plans, key=lambda p: (-len(p.contained), len(p.uncontained), min(p.origin, n - p.origin)))
        best.witness = self.layout(best.valid, 0, n, min_len, max_len) or []
        return best

    # ---- one matched region of a target whose frame is fixed (splitting a PCR source piece)
    def plan_segment(self, a: int, b: int, *, min_len: int, max_len: int) -> RegionPlan:
        if not self.has_repeats or b - a <= max_len:
            return RegionPlan(valid=None)
        valid = self.clean_cuts().copy()
        valid[a] = valid[b] = True
        if not self.region_has_layout(valid, a, b, min_len, max_len):
            return RegionPlan(valid=None, unrestricted_reason="repeated sequence sits at every possible breakpoint")
        whole = self.circular and a == 0 and b == self.n  # a whole circular target cut into pieces: its start is a junction too
        inside, straddling = self._arcs_in(a, b, whole=whole)
        return self._plan_region(valid, a, b, min_len, max_len, inside, straddling)

    def smallest_max_len_keeping_repeats_whole(self, *, min_len: int, start: int, limit: int | None = None,
                                               segment: tuple[int, int] | None = None) -> int | None:
        """Smallest maximum piece length at or above ``start`` for which the plan keeps every repeat whole; None if there is none
        up to ``limit``. ``segment`` asks the question for a fixed region (a PCR source piece) instead of a target whose start is
        free. More room never makes a layout harder, so a binary search gives an exact figure."""
        limit = limit or max(start * 2, start + 2000)

        def whole(max_len: int) -> bool:
            plan = self.plan(min_len=min_len, max_len=max_len) if segment is None else self.plan_segment(segment[0], segment[1], min_len=min_len, max_len=max_len)
            return plan.valid is not None and not plan.uncontained

        lo = max(start, min_len + 1)
        if whole(lo):
            return lo
        if not whole(limit):
            return None
        hi = limit
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if whole(mid):
                hi = mid
            else:
                lo = mid
        return hi

    def suggested_cuts(self, valid: np.ndarray, limit: int = 2000) -> list[int]:
        """Edges and middles of every run of allowed cuts, so a coarse-grained planner always sees a feasible cut."""
        runs = _runs(valid[: self.n] if self.circular else valid, False)
        out: list[int] = []
        for a, b in runs:
            out += [a, (a + b - 1) // 2, b - 1]
        out = sorted(set(out))
        if len(out) > limit:
            out = out[:: len(out) // limit + 1]
        return out


def choose_boundaries(valid: np.ndarray, x: int, y: int, cap: int, *, min_piece: int = 100, extra: int = 2) -> list[int] | None:
    """Split [x, y) into the fewest pieces of at most ``cap`` bases whose interior boundaries are allowed cuts, as close to equal
    as possible. Returns the interior boundaries ([] when one piece is enough), or None when no layout exists."""
    total = y - x
    if cap <= 0 or total <= cap:
        return []
    base = -(-total // cap)
    for count in range(base, base + extra + 1):
        got = _boundaries(valid, x, y, cap, count, min_piece)
        if got is not None:
            return got
    return None


def _boundaries(valid: np.ndarray, x: int, y: int, cap: int, count: int, min_piece: int) -> list[int] | None:
    total = y - x
    if count < 2 or count * cap < total:
        return None
    ideal = [x + total * i / count for i in range(1, count)]
    cands: list[np.ndarray] = []
    for i in range(1, count):
        lo = max(x + min_piece, y - (count - i) * cap)
        hi = min(y - min_piece, x + i * cap)
        if hi < lo:
            return None
        pos = np.arange(lo, hi + 1)
        pos = pos[valid[pos]]
        if len(pos) == 0:
            return None
        cands.append(pos)
    cost_prev = np.array([0.0])
    pos_prev = np.array([x])
    parents: list[np.ndarray] = []
    for i, pos in enumerate(cands):
        # cost of reaching each candidate from the previous boundary set (piece size within [min_piece, cap])
        cost = np.full(len(pos), np.inf)
        par = np.full(len(pos), -1, dtype=np.int64)
        lo_idx = np.searchsorted(pos_prev, pos - cap, side="left")
        hi_idx = np.searchsorted(pos_prev, pos - min_piece, side="right")
        for t in range(len(pos)):
            lo, hi = int(lo_idx[t]), int(hi_idx[t])
            if hi > lo:
                best = lo + int(np.argmin(cost_prev[lo:hi]))
                if np.isfinite(cost_prev[best]):
                    par[t] = best
                    cost[t] = cost_prev[best] + abs(pos[t] - ideal[i])
        parents.append(par)
        cost_prev, pos_prev = cost, pos
    final = [t for t, p in enumerate(pos_prev) if min_piece <= y - p <= cap and np.isfinite(cost_prev[t])]
    if not final:
        return None
    t = min(final, key=lambda s: cost_prev[s])
    out = []
    for i in range(len(cands) - 1, -1, -1):
        out.append(int(cands[i][t]))
        t = int(parents[i][t])
    return out[::-1]
