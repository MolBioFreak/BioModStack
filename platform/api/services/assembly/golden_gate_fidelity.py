"""Offline Pryor observation authority; independent of assembly DTOs/physical simulation.

One input string denotes one *intended junction*, not one physical end. Callers
must retain both physical end instances separately and convert their convention
before calling. Missing evidence never denies physical assembly or saving.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
import gzip
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
from typing import Callable, Sequence

ASSET_ROOT = Path(__file__).resolve().parents[2] / "config" / "golden_gate"
METRIC_VERSION = "pryor-pair-pooled-v1"
SCORE_LABEL = "Estimated junction-set correctness (empirical ligation model)"
CAVEATS = [
    "Idealized intended unique-junction set, not mass yield or colony success.",
    "Zero observations are unobserved events, not impossible chemistry.",
    "Background, concentration, recutting, depletion and nick closure are unmodeled.",
]


def reverse_complement(sequence: str) -> str:
    return sequence.translate(str.maketrans("ACGT", "TGCA"))[::-1]


def _dna(sequence: str, length: int | None = None) -> str:
    # Do not silently strip, uppercase, truncate or otherwise repair source tokens.
    if not sequence or set(sequence) - set("ACGT") or (length is not None and len(sequence) != length):
        raise ValueError(f"Expected explicit uppercase ACGT sequence of length {length}: {sequence!r}")
    return sequence


def _class(sequence: str) -> str:
    return min(sequence, reverse_complement(sequence))


@dataclass(frozen=True)
class EndInstance:
    """Caller-supplied physical inventory; no fabricated chemistry or concentration."""
    instance_id: str
    sequence: str | None
    role: str  # vector, insert, closure, dropout, donor, terminal, adapter, contaminant
    intended_junction_id: str | None = None
    polarity: str | None = None
    removed: bool = False
    phosphorylation: str | None = None


@dataclass(frozen=True)
class ObservationMatrix:
    manifest: dict
    row_labels: tuple[str, ...]
    column_labels: tuple[str, ...]
    observations: tuple[tuple[int, ...], ...]
    _rows: dict[str, int] = field(init=False, repr=False, compare=False)
    _columns: dict[str, int] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        length = self.manifest["end_length"]
        expected = {"".join(x) for x in itertools.product("ACGT", repeat=length)}
        if (set(self.row_labels) != expected or set(self.column_labels) != expected
                or len(self.row_labels) != len(expected) or len(self.column_labels) != len(expected)
                or len(self.observations) != len(expected)
                or any(len(row) != len(expected) for row in self.observations)
                or any(type(x) is not int or x < 0 for row in self.observations for x in row)):
            raise ValueError("Invalid observation dimensions, labels or values")
        object.__setattr__(self, "_rows", {x: i for i, x in enumerate(self.row_labels)})
        object.__setattr__(self, "_columns", {x: i for i, x in enumerate(self.column_labels)})

    def count(self, a: str, b: str) -> int:
        return self.observations[self._rows[a]][self._columns[b]]


def discover_datasets() -> list[dict]:
    """Metadata only. Does not load matrices, download files or initialize caches."""
    try:
        return json.loads((ASSET_ROOT / "manifest.json").read_text())
    except (OSError, ValueError):
        return []


def load_dataset(dataset_id: str) -> ObservationMatrix:
    """Load and verify one packaged dataset. Errors are evidence, not workflow gates.

    Low-level function raises ValueError/OSError; evaluate/optimize return unavailable.
    No import-time I/O and no user cache or network dependency.
    """
    item = next((x for x in discover_datasets() if x["id"] == dataset_id), None)
    if item is None:
        raise ValueError(f"Unknown or unavailable dataset: {dataset_id}")
    original = (ASSET_ROOT / item["file_name"]).read_bytes()
    payload = (ASSET_ROOT / item["parsed_file"]).read_bytes()
    if (hashlib.sha256(original).hexdigest() != item["sha256"]
            or hashlib.sha256(payload).hexdigest() != item["parsed_sha256"]):
        raise ValueError(f"Dataset checksum mismatch: {dataset_id}")
    parsed = json.loads(gzip.decompress(payload))
    if (parsed["source_sha256"] != item["sha256"] or parsed["worksheet"] != item["worksheet"]
            or parsed["conversion_version"] != item["conversion_version"]):
        raise ValueError("Conversion provenance mismatch")
    return ObservationMatrix(item, tuple(parsed["row_labels"]), tuple(parsed["column_labels"]),
                             tuple(tuple(row) for row in parsed["observations"]))


def _probabilities(matrix: ObservationMatrix, junctions: Sequence[str]) -> list[dict]:
    classes = sorted({_class(x) for x in junctions})
    universe = sorted(set(classes) | {reverse_complement(x) for x in classes})
    values = []
    for a in classes:
        b = reverse_complement(a)
        correct = matrix.count(a, b) + matrix.count(b, a)
        total = sum(matrix.count(a, x) + matrix.count(b, x) for x in universe)
        values.append({"representative": a, "complement": b, "correct_observations": correct,
                       "total_observations": total, "probability": correct / total if total else None})
    return values


def _objective(matrix: ObservationMatrix, junctions: Sequence[str]) -> tuple[float | None, float | None]:
    if (not junctions or any(x not in matrix._rows for x in junctions)
            or len({_class(x) for x in junctions}) != len(junctions) or any(
                x == reverse_complement(x) for x in junctions)):
        return None, None
    probabilities = [x["probability"] for x in _probabilities(matrix, junctions)]
    if any(p is None for p in probabilities):
        return None, None
    if any(p == 0 for p in probabilities):
        return 0.0, None
    log_value = math.fsum(math.log(p) for p in probabilities)
    return math.exp(log_value), log_value


def score_matrix(matrix: ObservationMatrix, junctions: Sequence[str], *,
                 include_pair_observations: bool = False) -> dict:
    """Pure numerical leaf, also usable with explicitly labeled synthetic fixtures."""
    length = matrix.manifest["end_length"]
    reasons = []
    if any(not x or set(x) - set("ACGT") or len(x) != length for x in junctions):
        return {"status": "unavailable", "f_set": None, "log_f_set": None,
                "reasons": ["unsupported_end_sequence_or_length"], "per_junction": [],
                "joining_bias": [], "pair_observations": []}
    counts = Counter(_class(x) for x in junctions)
    repeated = sorted(x for x, n in counts.items() if n > 1)
    palindromes = sorted(x for x in counts if x == reverse_complement(x))
    if not junctions:
        reasons.append("empty_junction_set")
    if repeated:
        reasons.append("repeated_junction_classes")
    if palindromes:
        reasons.append("palindromic_junctions")
    values = _probabilities(matrix, junctions)
    if any(x["probability"] is None for x in values):
        reasons.append("zero_denominator")
    score, log_score = _objective(matrix, junctions)
    max_wc = max(matrix.count(a, reverse_complement(a)) + matrix.count(reverse_complement(a), a)
                 for a in matrix.row_labels)
    universe = sorted(set(junctions) | {reverse_complement(x) for x in junctions})
    pairs = [{"a": a, "b": b, "observations": matrix.count(a, b),
              "watson_crick": b == reverse_complement(a)} for a in universe for b in universe
             ] if include_pair_observations else []
    # Per-class chemical diagnostics remain available for structurally ambiguous designs;
    # they must not be advertised as physical-fragment assignment probabilities.
    return {"status": "unavailable" if reasons else "available", "f_set": score,
            "log_f_set": log_score, "exact_zero_observed_correct": any(
                x["correct_observations"] == 0 and x["total_observations"] > 0 for x in values),
            "reasons": reasons, "repeated_classes": repeated, "palindromes": palindromes,
            "per_junction": values, "joining_bias": [
                {"representative": x["representative"], "pooled_wc_observations": x["correct_observations"],
                 "relative_to_dataset_max_pooled_wc": x["correct_observations"] / max_wc if max_wc else None}
                for x in values], "pair_observations": pairs}


def evaluate_overhangs(junctions: Sequence[str], dataset_id: str | None, *,
                       condition_use: str = "reference", inventory: Sequence[EndInstance] = (),
                       inventory_complete: bool = False,
                       include_pair_observations: bool = False) -> dict:
    """Score intended junction instances. RC duplicates are structural ambiguity.

    condition_use: 'reference' (selected assay only), or 'explicit_proxy' (caller
    explicitly applies it to another chemistry). Never infers a condition match.
    Inventory is preserved verbatim and not concentration-weighted or scored.
    """
    if condition_use not in ("reference", "explicit_proxy"):
        raise ValueError("condition_use must be reference or explicit_proxy")
    base = {"dataset_id": dataset_id, "metric_version": METRIC_VERSION, "label": SCORE_LABEL,
            "condition_use": condition_use, "junctions": list(junctions),
            "inventory": [asdict(x) for x in inventory], "inventory_complete": inventory_complete,
            "unmodeled_inventory_instance_ids": [x.instance_id for x in inventory
                                                  if not x.removed and x.intended_junction_id is None],
            "scope": "idealized_intended_unique_junction_set", "caveats": list(CAVEATS),
            "dataset": None}
    try:
        if dataset_id is None:
            raise ValueError("No reference dataset selected")
        matrix = load_dataset(dataset_id)
    except (OSError, ValueError, KeyError) as error:
        return {**base, "status": "unavailable", "f_set": None, "log_f_set": None,
                "reasons": [str(error)], "per_junction": [], "joining_bias": [], "pair_observations": []}
    return {**base, "dataset": matrix.manifest,
            **score_matrix(matrix, junctions, include_pair_observations=include_pair_observations)}


@dataclass(frozen=True)
class SearchSettings:
    """All output-affecting budgets. budget counts attempted assignments, including invalid ones."""
    seed: int = 0
    evaluation_budget: int = 10000
    restarts: int = 8
    exact_limit: int = 10000
    alternatives: int = 5
    unique_classes: bool = True
    exclude_palindromes: bool = True
    ranking_mode: str = "empirical"  # explicit 'lexicographic' permits unscored suggestions

    def __post_init__(self) -> None:
        for name in ("evaluation_budget", "restarts", "exact_limit", "alternatives"):
            if type(getattr(self, name)) is not int or getattr(self, name) < (1 if name in ("restarts", "alternatives") else 0):
                raise ValueError(f"Invalid search setting: {name}")
        if self.ranking_mode not in ("empirical", "lexicographic"):
            raise ValueError("Unknown ranking mode")


def _search(*, domain_size: int, enumeration: Callable, random_start: Callable,
            neighbor: Callable, describe: Callable, feasible: Callable,
            dataset_id: str | None, end_length: int, condition_use: str, settings: SearchSettings) -> dict:
    if condition_use not in ("reference", "explicit_proxy"):
        raise ValueError("condition_use must be reference or explicit_proxy")
    matrix = None
    unavailable = None
    try:
        if dataset_id is not None:
            matrix = load_dataset(dataset_id)
            if matrix.manifest["end_length"] != end_length:
                matrix = None
                unavailable = "Selected dataset does not cover the requested end length"
        else:
            unavailable = "No reference dataset selected"
    except (OSError, ValueError, KeyError) as error:
        unavailable = str(error)
    exact = domain_size <= settings.exact_limit and domain_size <= settings.evaluation_budget
    result = {"dataset_id": dataset_id, "metric_version": METRIC_VERSION,
              "dataset": matrix.manifest if matrix is not None else None,
              "condition_use": condition_use, "scope": "idealized_intended_unique_junction_set",
              "settings": asdict(settings), "solutions": [], "diagnostics": [],
              "search_scope": {"algorithm": "exhaustive" if exact else "seeded_greedy_coordinate_restarts",
                  "domain_size": domain_size, "attempted": 0, "scored": 0,
                  "complete": False, "optimality_proven": False,
                  "tie_break": "descending log F_set, then size penalty, then explicit choices",
                  "objective": settings.ranking_mode}}
    if unavailable:
        result["diagnostics"].append(unavailable)
    if matrix is None and settings.ranking_mode == "empirical":
        result["status"] = "unavailable_objective"
        return result
    rng = random.Random(settings.seed)
    best: dict[tuple, tuple] = {}
    scope = result["search_scope"]

    def visit(choice):
        scope["attempted"] += 1
        if not feasible(choice):
            return None
        candidate = describe(choice)
        junctions = candidate["junctions"]
        if settings.unique_classes and len({_class(x) for x in junctions}) != len(junctions):
            return None
        if settings.exclude_palindromes and any(x == reverse_complement(x) for x in junctions):
            return None
        value, log_value = (None, None) if matrix is None else _objective(matrix, junctions)
        if settings.ranking_mode == "empirical" and value is None:
            return None
        scope["scored"] += 1
        # Log objective preserves ordering even if representable F_set underflows.
        objective = (-(log_value if log_value is not None else -math.inf)
                     if settings.ranking_mode == "empirical" else 0)
        key = (objective, candidate.get("size_penalty", 0), tuple(choice))
        candidate.update(f_set=value, log_f_set=log_value)
        best[tuple(choice)] = (key, candidate)
        if len(best) > settings.alternatives:
            del best[max(best, key=lambda k: best[k][0])]
        return key

    if exact:
        for choice in enumeration():
            visit(choice)
        scope["complete"] = True
    elif domain_size and settings.evaluation_budget:
        # Each restart gets an explicit equal share (remainder goes to early restarts).
        for restart in range(min(settings.restarts, settings.evaluation_budget)):
            share = settings.evaluation_budget // settings.restarts + (restart < settings.evaluation_budget % settings.restarts)
            current = random_start(rng)
            current_key = visit(current)
            for _ in range(share - 1):
                proposed = neighbor(current, rng)
                proposed_key = visit(proposed)
                if proposed_key is not None and (current_key is None or proposed_key < current_key):
                    current, current_key = proposed, proposed_key
    result["solutions"] = [x[1] for x in sorted(best.values(), key=lambda x: x[0])]
    scope["optimality_proven"] = scope["complete"] and bool(best)
    result["status"] = "solutions" if best else ("no_feasible_scored_solution" if exact else "no_solution_found_within_budget")
    if not exact:
        result["diagnostics"].append("Bounded best-found search; neither optimality nor infeasibility is proven.")
    return result


def optimize_overhangs(candidate_domain: Sequence[str], junction_count: int, dataset_id: str | None, *,
                       end_length: int, required: Sequence[str] = (), fixed: Sequence[str] = (),
                       excluded: Sequence[str] = (), condition_use: str = "reference",
                       settings: SearchSettings = SearchSettings()) -> dict:
    """Find a set, including every required/fixed class. Exclusions include RC classes.

    Fixed strings preserve their supplied orientation; other classes use the
    lexicographically first explicitly supplied representative. No hidden full domain.
    """
    if type(junction_count) is not int or junction_count < 1:
        raise ValueError("junction_count must be positive")
    if end_length not in (3, 4):
        raise ValueError("end_length must be 3 or 4")
    for x in itertools.chain(candidate_domain, required, fixed, excluded):
        _dna(x, end_length)
    forbidden = {_class(x) for x in excluded}
    locked = list(fixed)
    for x in required:
        if _class(x) not in {_class(y) for y in locked}:
            locked.append(x)
    representatives = {}
    for x in sorted(set(candidate_domain)):
        if _class(x) not in forbidden and (not settings.unique_classes or _class(x) not in {_class(y) for y in locked}):
            if not settings.exclude_palindromes or x != reverse_complement(x):
                representatives.setdefault(_class(x), x)
    pool = sorted(representatives.values())
    need = junction_count - len(locked)
    conflict = need < 0 or any(_class(x) in forbidden for x in locked)
    if conflict or (need and not pool) or (settings.unique_classes and need > len(pool)):
        size = 0
    else:
        size = math.comb(len(pool), need) if settings.unique_classes else math.comb(len(pool) + need - 1, need) if need else 1

    def enumeration():
        if not size:
            return iter(())
        combinations = itertools.combinations if settings.unique_classes else itertools.combinations_with_replacement
        return combinations(range(len(pool)), need)

    def start(rng):
        if settings.unique_classes:
            return tuple(sorted(rng.sample(range(len(pool)), need)))
        return tuple(sorted(rng.randrange(len(pool)) for _ in range(need)))

    def neighbor(choice, rng):
        remaining = sorted(set(range(len(pool))) - set(choice)) if settings.unique_classes else list(range(len(pool)))
        if not choice or not remaining:
            return choice
        out = list(choice)
        out[rng.randrange(len(out))] = rng.choice(remaining)
        return tuple(sorted(out))

    result = _search(domain_size=size, enumeration=enumeration, random_start=start, neighbor=neighbor,
                     describe=lambda c: {"junctions": locked + [pool[i] for i in c], "choices": list(c)},
                     feasible=lambda c: True, dataset_id=dataset_id, end_length=end_length,
                     condition_use=condition_use, settings=settings)
    result["constraints"] = {"candidate_domain": list(candidate_domain), "effective_candidate_domain": pool,
                             "junction_count": junction_count, "end_length": end_length,
                             "required": list(required), "fixed": list(fixed), "excluded": list(excluded)}
    return result


def sequence_window_domains(sequence: str, windows: Sequence[tuple[int, int]], *,
                            end_length: int, topology: str = "linear",
                            protected_regions: Sequence[tuple[int, int]] = (),
                            frame_constraints: Sequence[tuple[int, int, int, int]] = ()) -> list[list[dict]]:
    """Enumerate unchanged sequence-derived fusions; no primer/digest simulation.

    Windows are half-open ranges of permitted zero-based fusion START boundaries.
    Protected intervals exclude any overlapping fusion. On circular targets a
    start greater than end wraps through origin, within the SAME window/slot.
    Frame tuples are (start,end,origin,phase), admitting (position-origin)%3==phase
    within that region. They constrain cut placement only, not translation or edits.
    Circular domains and fusion bases can wrap across origin. Frame regions are
    nonwrapping reference-coordinate intervals; no CDS translation claim is made.
    """
    _dna(sequence)
    if end_length not in (3, 4) or topology not in ("linear", "circular"):
        raise ValueError("Unsupported length/topology")
    n = len(sequence)
    if n < end_length:
        raise ValueError("Target shorter than fusion")
    for start, end in itertools.chain(windows, protected_regions):
        if not (0 <= start <= n and 0 <= end <= n) or (topology == "linear" and start > end):
            raise ValueError("Intervals must be 0-based half-open target coordinates; only circular intervals wrap")
    for start, end, origin, phase in frame_constraints:
        if not 0 <= start <= end <= n or not 0 <= origin <= n or phase not in (0, 1, 2):
            raise ValueError("Invalid frame constraint")
    domains = []
    for start, end in windows:
        domain = []
        positions = range(start, end) if start <= end else itertools.chain(range(start, n), range(end))
        for p in positions:
            if topology == "linear" and (p == 0 or p + end_length > n):
                continue
            covered = {(p + i) % n for i in range(end_length)}
            if any(any((a <= x < b if a <= b else x >= a or x < b) for x in covered)
                   for a, b in protected_regions):
                continue
            if any(a <= p < b and (p - origin) % 3 != phase for a, b, origin, phase in frame_constraints):
                continue
            domain.append({"position": p, "sequence": (sequence + sequence[:end_length - 1])[p:p + end_length]})
        domains.append(domain)
    return domains


def optimize_sequence_windows(sequence: str, windows: Sequence[tuple[int, int]], dataset_id: str | None, *,
                              end_length: int, topology: str = "linear",
                              fixed_positions: Sequence[int | None] = (),
                              fixed_overhangs: Sequence[str] = (), required: Sequence[str] = (),
                              excluded: Sequence[str] = (), protected_regions: Sequence[tuple[int, int]] = (),
                              frame_constraints: Sequence[tuple[int, int, int, int]] = (),
                              min_fragment_length: int = 1, max_fragment_length: int | None = None,
                              target_fragment_length: int | None = None,
                              condition_use: str = "reference",
                              settings: SearchSettings = SearchSettings()) -> dict:
    """Joint full-set optimization of one real fusion per ordered target window.

    Segment lengths are distances between fusion START boundaries, not PCR product
    lengths. Circular final segment crosses the origin. Fixed vector/closure ends
    participate in the objective without pretending to be target cut positions.
    The assembly owner must verify physical fragments/primers and coding biology.
    """
    domains = sequence_window_domains(sequence, windows, end_length=end_length, topology=topology,
                                     protected_regions=protected_regions, frame_constraints=frame_constraints)
    if fixed_positions and len(fixed_positions) != len(domains):
        raise ValueError("fixed_positions must align with windows")
    if (min_fragment_length < 1 or (max_fragment_length is not None and max_fragment_length < min_fragment_length)
            or (target_fragment_length is not None and target_fragment_length < 1)):
        raise ValueError("Invalid fragment length goal")
    for x in itertools.chain(fixed_overhangs, required, excluded):
        _dna(x, end_length)
    forbidden = {_class(x) for x in excluded}
    for i, domain in enumerate(domains):
        domains[i] = [x for x in domain if _class(x["sequence"]) not in forbidden and
                      (not fixed_positions or fixed_positions[i] is None or x["position"] == fixed_positions[i])]
    size = math.prod(len(x) for x in domains)
    if not domains:
        size = 0

    def describe(choice):
        selected = [domains[i][j] for i, j in enumerate(choice)]
        positions = [x["position"] for x in selected]
        if topology == "linear":
            boundaries = [0, *positions, len(sequence)]
            lengths = [b - a for a, b in zip(boundaries, boundaries[1:])]
        else:
            lengths = ([(b - a) % len(sequence) for a, b in zip(positions, positions[1:] + positions[:1])]
                       if len(positions) > 1 else [len(sequence)])
        return {"junctions": list(fixed_overhangs) + [x["sequence"] for x in selected],
                "choices": list(choice), "cuts": selected, "fragment_lengths": lengths,
                "size_penalty": sum(abs(x - target_fragment_length) for x in lengths) if target_fragment_length else 0}

    def feasible(choice):
        candidate = describe(choice)
        positions = [x["position"] for x in candidate["cuts"]]
        geometry_valid = (all(a + end_length <= b for a, b in zip(positions, positions[1:]))
                          if topology == "linear" else
                          sum(candidate["fragment_lengths"]) == len(sequence)
                          and all(x >= end_length for x in candidate["fragment_lengths"]))
        return (geometry_valid
                and all(x >= min_fragment_length and (max_fragment_length is None or x <= max_fragment_length)
                        for x in candidate["fragment_lengths"])
                and not any(_class(x) in forbidden for x in candidate["junctions"])
                and {_class(x) for x in required} <= {_class(x) for x in candidate["junctions"]})

    def neighbor(choice, rng):
        out = list(choice)
        slot = rng.randrange(len(domains))
        out[slot] = rng.randrange(len(domains[slot]))
        return tuple(out)

    result = _search(domain_size=size, enumeration=lambda: itertools.product(*(range(len(d)) for d in domains)) if size else iter(()),
                     random_start=lambda rng: tuple(rng.randrange(len(d)) for d in domains),
                     neighbor=neighbor, describe=describe, feasible=feasible, dataset_id=dataset_id,
                     end_length=end_length, condition_use=condition_use, settings=settings)
    result["constraints"] = {"end_length": end_length, "topology": topology, "windows": [list(x) for x in windows],
        "coordinate_convention": "zero_based_half_open_fusion_start_windows",
        "fragment_length_basis": "distance_between_fusion_starts", "fusion_nonoverlap": True,
        "fixed_positions": list(fixed_positions), "fixed_overhangs": list(fixed_overhangs), "required": list(required),
        "excluded": list(excluded), "protected_regions": [list(x) for x in protected_regions],
        "frame_constraints": [list(x) for x in frame_constraints], "min_fragment_length": min_fragment_length,
        "max_fragment_length": max_fragment_length, "target_fragment_length": target_fragment_length}
    result["candidate_domains"] = domains
    result["diagnostics"].append("Sequence-window assignment only; physical preparation, primer tradeoffs and translation verification belong to the design owner. Target DNA is unchanged.")
    return result
