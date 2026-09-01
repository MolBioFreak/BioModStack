"""Exact, bounded, IUPAC-aware restriction-site and cleavage analysis."""
from __future__ import annotations

import hashlib
import sys
import threading
from collections import OrderedDict, defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Sequence

import rfc8785
from pydantic import BaseModel, ConfigDict, Field

from services.restriction_catalog import (
    ANALYSIS_CACHE_MAXIMUM_ENTRIES,
    ANALYSIS_CACHE_MAXIMUM_RESULT_WEIGHT_BYTES,
    ANALYSIS_CACHE_MAXIMUM_TOTAL_WEIGHT_BYTES,
    ANALYSIS_EVENT_MAXIMUM,
    ANALYSIS_EXPLICIT_ENZYME_MAXIMUM,
    ANALYSIS_INLINE_SEQUENCE_MAX_LENGTH,
    ANALYSIS_OCCURRENCE_MAXIMUM,
    ANALYSIS_PATTERN_MAXIMUM,
    ANALYSIS_REGION_MAXIMUM,
    ANALYSIS_RESPONSE_MAXIMUM_BYTES,
    ANALYSIS_SCAN_WORK_MAXIMUM,
    ANALYSIS_CANCELLATION_POLICY,
    ANALYSIS_QUEUE_POLICY,
    ANALYSIS_TIMEOUT_SECONDS,
    ANALYSIS_WORKER_CONCURRENCY,
    ANALYSIS_RESPONSE_BASE_BUDGET_BYTES,
    ANALYSIS_RESPONSE_EVENT_BUDGET_BYTES,
    ANALYSIS_RESPONSE_OCCURRENCE_BUDGET_BYTES,
    ANALYSIS_SCAN_WORK_FORMULA_ID,
    ANALYSIS_SCAN_WORK_FORMULA_VERSION,
    CatalogView,
    RestrictionRecord,
)

ALGORITHM_ID = "bms-restriction-analysis"
ALGORITHM_VERSION = "2.1.0"
SCAN_WORK_FORMULA_ID = ANALYSIS_SCAN_WORK_FORMULA_ID
SCAN_WORK_FORMULA_VERSION = ANALYSIS_SCAN_WORK_FORMULA_VERSION
MAX_INLINE_SEQUENCE_LENGTH = ANALYSIS_INLINE_SEQUENCE_MAX_LENGTH
MAX_EXPLICIT_ENZYME_IDS = ANALYSIS_EXPLICIT_ENZYME_MAXIMUM
MAX_REGIONS = ANALYSIS_REGION_MAXIMUM
MAX_ANALYSIS_PATTERNS = ANALYSIS_PATTERN_MAXIMUM
MAX_SCAN_WORK = ANALYSIS_SCAN_WORK_MAXIMUM
MAX_RETURNED_OCCURRENCES = ANALYSIS_OCCURRENCE_MAXIMUM
MAX_RETURNED_EVENTS = ANALYSIS_EVENT_MAXIMUM
MAX_RESPONSE_BYTES = ANALYSIS_RESPONSE_MAXIMUM_BYTES
CACHE_MAX_ENTRIES = ANALYSIS_CACHE_MAXIMUM_ENTRIES
CACHE_MAX_TOTAL_WEIGHT_BYTES = ANALYSIS_CACHE_MAXIMUM_TOTAL_WEIGHT_BYTES
CACHE_MAX_RESULT_WEIGHT_BYTES = ANALYSIS_CACHE_MAXIMUM_RESULT_WEIGHT_BYTES
_RESPONSE_BASE_BUDGET = ANALYSIS_RESPONSE_BASE_BUDGET_BYTES
_RESPONSE_OCCURRENCE_BUDGET = ANALYSIS_RESPONSE_OCCURRENCE_BUDGET_BYTES
_RESPONSE_EVENT_BUDGET = ANALYSIS_RESPONSE_EVENT_BUDGET_BYTES

_IUPAC = MappingProxyType(
    {
        "A": frozenset("A"), "C": frozenset("C"), "G": frozenset("G"),
        "T": frozenset("T"), "R": frozenset("AG"), "Y": frozenset("CT"),
        "S": frozenset("CG"), "W": frozenset("AT"), "K": frozenset("GT"),
        "M": frozenset("AC"), "B": frozenset("CGT"), "D": frozenset("AGT"),
        "H": frozenset("ACT"), "V": frozenset("ACG"), "N": frozenset("ACGT"),
    }
)
_COMPLEMENT = str.maketrans("ACGTRYSWKMBDHVN", "TGCAYRSWMKVHDBN")


class InvalidDNAError(ValueError):
    pass


class AnalysisLimitError(ValueError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AnalysisCounts(StrictModel):
    recognition_site_count_definite: int
    recognition_site_count_possible: int
    double_strand_break_count: int
    nick_count: int


class AnalysisLimitation(StrictModel):
    code: Literal["recognition_motif_longer_than_molecule"]
    motif: str
    motif_length_bp: int
    molecule_length_bp: int
    enzyme_ids: tuple[str, ...]


class DoubleStrandEvent(StrictModel):
    enzyme_id: str
    occurrence_id: str
    event_ordinal: int
    orientation: Literal["forward", "reverse"]
    status: Literal["complete", "geometry_out_of_bounds"]
    top_boundary: int | None
    bottom_boundary: int | None
    top_boundary_unwrapped: int
    bottom_boundary_unwrapped: int
    top_winding: int
    bottom_winding: int
    overhang_kind: Literal["blunt", "five_prime", "three_prime"]
    overhang_length_nt: int
    overhang_sequence_5to3: str | None
    overhang_source_strand: Literal["top", "bottom"] | None
    protruding_strand: Literal["top", "bottom"] | None
    contributor_group_id: str
    activity_assessment: Literal["not_evaluated"] = "not_evaluated"
    methylation_context: Literal["unknown"] = "unknown"


class NickEvent(StrictModel):
    enzyme_id: str
    occurrence_id: str
    event_ordinal: int
    orientation: Literal["forward", "reverse"]
    strand: Literal["top", "bottom"]
    status: Literal["complete", "geometry_out_of_bounds"]
    boundary: int | None
    boundary_unwrapped: int
    winding: int
    contributor_group_id: str
    activity_assessment: Literal["not_evaluated"] = "not_evaluated"


class AnalysisOccurrence(StrictModel):
    occurrence_id: str
    occurrence_ordinal: int
    enzyme_id: str
    canonical_name: str
    orientation: Literal["forward", "reverse"]
    certainty: Literal["definite", "possible"]
    recognition_pattern: str
    site_start: int
    site_end_unwrapped: int
    site_segments: tuple[tuple[int, int], ...]
    wraps_origin: bool
    matched_reference_sequence: str
    double_strand_events: tuple[DoubleStrandEvent, ...]
    nicks: tuple[NickEvent, ...]
    limitations: tuple[str, ...]
    activity_assessment: Literal["not_evaluated"] = "not_evaluated"
    methylation_context: Literal["unknown"] = "unknown"


class EnzymeSummary(StrictModel):
    enzyme_id: str
    canonical_name: str
    analysis_capability: Literal["digest_simulation", "nicking_analysis", "recognition_only"]
    cleavage_status: Literal["known_double_strand", "known_single_strand_nick", "unknown"]
    recognition_site_count_definite: int
    recognition_site_count_possible: int
    double_strand_break_count: int
    nick_count: int
    limitations: tuple[str, ...]


class CleavageContributor(StrictModel):
    enzyme_id: str
    occurrence_id: str
    event_ordinal: int
    orientation: Literal["forward", "reverse"]


class GroupedCleavage(StrictModel):
    contributor_group_id: str
    status: Literal["complete", "geometry_out_of_bounds"]
    top_boundary: int | None
    bottom_boundary: int | None
    overhang_kind: Literal["blunt", "five_prime", "three_prime"]
    overhang_length_nt: int
    overhang_sequence_5to3: str | None
    overhang_source_strand: Literal["top", "bottom"] | None
    protruding_strand: Literal["top", "bottom"] | None
    contributing_enzyme_ids: tuple[str, ...]
    contributors: tuple[CleavageContributor, ...]


_RESOURCE_POLICY_OPENAPI_EXAMPLE = {
    "schema": "bms.molbio.restriction-analysis-resource-policy.v1",
    "policy_version": "1.1.0",
    "scan_work_formula_id": "candidate-starts-times-motif-width",
    "scan_work_formula_version": "1.0.0",
    "sequence_length_maximum": 5_000_000,
    "explicit_enzyme_maximum": 256,
    "region_maximum": 128,
    "actual_scan_pattern_maximum": 1_056,
    "scan_work_maximum": 32_000_000,
    "occurrence_maximum": 25_000,
    "event_maximum": 50_000,
    "response_maximum_bytes": 32 * 1024 * 1024,
    "response_base_budget_bytes": 64 * 1024,
    "response_occurrence_budget_bytes": 2_048,
    "response_event_budget_bytes": 1_024,
    "worker_concurrency": 2,
    "queue_policy": "reject_when_all_workers_busy",
    "timeout_seconds": 60,
    "cancellation_policy": "worker_continues_and_capacity_is_retained_until_completion",
    "cache_entry_maximum": 32,
    "cache_total_weight_maximum_bytes": 64 * 1024 * 1024,
    "cache_result_weight_maximum_bytes": 8 * 1024 * 1024,
    "cache_weight_formula_id": "canonical-json-entry-and-complete-cache-graph",
    "cache_weight_formula_version": "2.0.0",
}


class ResourcePolicyReceipt(StrictModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True,
        json_schema_extra={"examples": [_RESOURCE_POLICY_OPENAPI_EXAMPLE]},
    )
    schema_: Literal["bms.molbio.restriction-analysis-resource-policy.v1"] = Field(alias="schema")
    policy_version: Literal["1.1.0"]
    scan_work_formula_id: Literal["candidate-starts-times-motif-width"]
    scan_work_formula_version: Literal["1.0.0"]
    sequence_length_maximum: int
    explicit_enzyme_maximum: int
    region_maximum: int
    actual_scan_pattern_maximum: int
    scan_work_maximum: int
    occurrence_maximum: int
    event_maximum: int
    response_maximum_bytes: int
    response_base_budget_bytes: int
    response_occurrence_budget_bytes: int
    response_event_budget_bytes: int
    worker_concurrency: int
    queue_policy: Literal["reject_when_all_workers_busy"]
    timeout_seconds: int
    cancellation_policy: Literal["worker_continues_and_capacity_is_retained_until_completion"]
    cache_entry_maximum: int
    cache_total_weight_maximum_bytes: int
    cache_result_weight_maximum_bytes: int
    cache_weight_formula_id: Literal["canonical-json-entry-and-complete-cache-graph"]
    cache_weight_formula_version: Literal["2.0.0"]


def resource_policy_receipt() -> ResourcePolicyReceipt:
    return ResourcePolicyReceipt(
        schema="bms.molbio.restriction-analysis-resource-policy.v1",
        policy_version="1.1.0",
        scan_work_formula_id=SCAN_WORK_FORMULA_ID,
        scan_work_formula_version=SCAN_WORK_FORMULA_VERSION,
        sequence_length_maximum=MAX_INLINE_SEQUENCE_LENGTH,
        explicit_enzyme_maximum=MAX_EXPLICIT_ENZYME_IDS,
        region_maximum=MAX_REGIONS,
        actual_scan_pattern_maximum=MAX_ANALYSIS_PATTERNS,
        scan_work_maximum=MAX_SCAN_WORK,
        occurrence_maximum=MAX_RETURNED_OCCURRENCES,
        event_maximum=MAX_RETURNED_EVENTS,
        response_maximum_bytes=MAX_RESPONSE_BYTES,
        response_base_budget_bytes=_RESPONSE_BASE_BUDGET,
        response_occurrence_budget_bytes=_RESPONSE_OCCURRENCE_BUDGET,
        response_event_budget_bytes=_RESPONSE_EVENT_BUDGET,
        worker_concurrency=ANALYSIS_WORKER_CONCURRENCY,
        queue_policy=ANALYSIS_QUEUE_POLICY,
        timeout_seconds=ANALYSIS_TIMEOUT_SECONDS,
        cancellation_policy=ANALYSIS_CANCELLATION_POLICY,
        cache_entry_maximum=CACHE_MAX_ENTRIES,
        cache_total_weight_maximum_bytes=CACHE_MAX_TOTAL_WEIGHT_BYTES,
        cache_result_weight_maximum_bytes=CACHE_MAX_RESULT_WEIGHT_BYTES,
        cache_weight_formula_id="canonical-json-entry-and-complete-cache-graph",
        cache_weight_formula_version="2.0.0",
    )


class AnalysisResult(StrictModel):
    algorithm_id: Literal["bms-restriction-analysis"] = ALGORITHM_ID
    algorithm_version: Literal["2.1.0"] = ALGORITHM_VERSION
    source_sha256: str
    topology: Literal["linear", "circular"]
    sequence_length: int
    catalog_sha256: str
    scope_sha256: str
    region_policy_sha256: str
    resource_policy_receipt: ResourcePolicyReceipt
    resource_policy_sha256: str
    counts: AnalysisCounts
    enzyme_summaries: tuple[EnzymeSummary, ...]
    occurrences: tuple[AnalysisOccurrence, ...]
    grouped_cleavages: tuple[GroupedCleavage, ...]
    warnings: tuple[str, ...]
    limitations: tuple[AnalysisLimitation, ...]
    result_sha256: str

    def canonical_result_bytes(self) -> bytes:
        payload = self.model_dump(mode="json", by_alias=True)
        payload.pop("result_sha256", None)
        return rfc8785.dumps(payload)


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    key: tuple[str, ...]
    canonical_result: bytes


_cache_lock = threading.RLock()
_cache: OrderedDict[tuple[str, ...], _CacheEntry] = OrderedDict()
_compiled_lock = threading.Lock()
_compiled: dict[str, tuple[frozenset[str], ...]] = {}


def reverse_complement(sequence: str) -> str:
    return sequence.translate(_COMPLEMENT)[::-1]


def _retained_weight(value: object, seen: set[int] | None = None) -> int:
    """Count the complete supported retained graph once by object identity."""
    if seen is None:
        seen = set()
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    size = sys.getsizeof(value)
    if isinstance(value, BaseModel):
        return size + _retained_weight(value.__dict__, seen)
    if isinstance(value, Mapping):
        return size + sum(
            _retained_weight(key, seen) + _retained_weight(item, seen)
            for key, item in value.items()
        )
    if isinstance(value, (tuple, list, set, frozenset)):
        return size + sum(_retained_weight(item, seen) for item in value)
    slots = getattr(type(value), "__slots__", ())
    if isinstance(slots, str):
        slots = (slots,)
    return size + sum(
        _retained_weight(getattr(value, slot), seen)
        for slot in slots
        if slot not in {"__weakref__", "__dict__"} and hasattr(value, slot)
    )


def _clear_cache_for_testing() -> None:
    with _cache_lock:
        _cache.clear()


def _cache_snapshot_for_testing() -> tuple[int, int]:
    with _cache_lock:
        return len(_cache), _retained_weight(_cache)


def normalize_dna(sequence: str) -> str:
    if not isinstance(sequence, str) or not sequence or any(c.isspace() for c in sequence):
        raise InvalidDNAError("DNA must be nonempty and contain no whitespace")
    normalized = sequence.upper()
    if any(character not in _IUPAC for character in normalized):
        raise InvalidDNAError("DNA contains a symbol outside the DNA IUPAC alphabet")
    return normalized


def _compile(pattern: str) -> tuple[frozenset[str], ...]:
    with _compiled_lock:
        value = _compiled.get(pattern)
        if value is None:
            value = tuple(_IUPAC[character] for character in pattern)
            _compiled[pattern] = value
        return value


def _certainty(window: str, motif: tuple[frozenset[str], ...]) -> str | None:
    definite = True
    for source_symbol, motif_set in zip(window, motif, strict=True):
        source_set = _IUPAC[source_symbol]
        if source_set.isdisjoint(motif_set):
            return None
        if not source_set.issubset(motif_set):
            definite = False
    return "definite" if definite else "possible"


def _scan(sequence: str, pattern: str, topology: str) -> Iterator[tuple[int, str, str]]:
    """Yield matches incrementally; a circular molecule never reuses physical bases."""
    length = len(sequence)
    motif = _compile(pattern)
    width = len(motif)
    if width > length:
        return
    starts = length if topology == "circular" else length - width + 1
    extended = sequence + sequence[: width - 1] if topology == "circular" and width > 1 else sequence
    for start in range(starts):
        window = extended[start:start + width]
        certainty = _certainty(window, motif)
        if certainty is not None:
            yield start, certainty, window


def _segments(start: int, end: int, length: int, topology: str) -> tuple[tuple[int, int], ...]:
    if topology == "linear" or end <= length:
        return ((start, end),)
    return ((start, length), (0, end % length))


def _target_slice(sequence: str, start: int, end: int, topology: str) -> str:
    if start == end:
        return ""
    if topology == "linear":
        return sequence[start:end]
    length = len(sequence)
    return "".join(sequence[index % length] for index in range(start, end))


def _group_id(source_sha: str, topology: str, top: int, bottom: int, length: int) -> str:
    physical_top = top % length if topology == "circular" else top
    physical_bottom = bottom % length if topology == "circular" else bottom
    identity = {
        "source_sha256": source_sha, "topology": topology,
        "top_boundary": physical_top, "bottom_boundary": physical_bottom,
    }
    return "sha256:" + hashlib.sha256(rfc8785.dumps(identity)).hexdigest()


def _nick_group_id(source_sha: str, topology: str, strand: str, boundary: int, length: int) -> str:
    physical = boundary % length if topology == "circular" else boundary
    identity = {
        "source_sha256": source_sha, "topology": topology,
        "strand": strand, "boundary": physical,
    }
    return "sha256:" + hashlib.sha256(rfc8785.dumps(identity)).hexdigest()


def _dsb_events(
    sequence: str, topology: str, source_sha: str, record: RestrictionRecord,
    start: int, orientation: str, certainty: str, occurrence_id: str,
) -> tuple[tuple[DoubleStrandEvent, ...], tuple[str, ...]]:
    length = len(sequence)
    width = record.recognition.length_bp
    events: list[DoubleStrandEvent] = []
    limitations: set[str] = set()
    for ordinal, source_event in enumerate(record.cleavage.events):
        if orientation == "forward":
            top = start + source_event.top_offset
            bottom = start + source_event.bottom_offset
        else:
            top = start + (width - source_event.bottom_offset)
            bottom = start + (width - source_event.top_offset)
        complete = topology == "circular" or (0 <= top <= length and 0 <= bottom <= length)
        if not complete:
            limitations.add("geometry_out_of_bounds")
        delta = bottom - top
        kind = "blunt" if delta == 0 else "five_prime" if delta > 0 else "three_prime"
        source_strand: Literal["top", "bottom"] | None = None
        overhang: str | None = None
        if delta > 0:
            source_strand = "top"
            if complete and certainty == "definite":
                overhang = _target_slice(sequence, top, bottom, topology)
        elif delta < 0:
            source_strand = "bottom"
            if complete and certainty == "definite":
                overhang = reverse_complement(_target_slice(sequence, bottom, top, topology))
        events.append(DoubleStrandEvent(
            enzyme_id=record.enzyme_id,
            occurrence_id=occurrence_id,
            event_ordinal=ordinal,
            orientation=orientation,
            status="complete" if complete else "geometry_out_of_bounds",
            top_boundary=top % length if topology == "circular" else top if complete else None,
            bottom_boundary=bottom % length if topology == "circular" else bottom if complete else None,
            top_boundary_unwrapped=top,
            bottom_boundary_unwrapped=bottom,
            top_winding=top // length if topology == "circular" else 0,
            bottom_winding=bottom // length if topology == "circular" else 0,
            overhang_kind=kind,
            overhang_length_nt=abs(delta),
            overhang_sequence_5to3=overhang,
            overhang_source_strand=source_strand,
            protruding_strand=source_strand,
            contributor_group_id=_group_id(source_sha, topology, top, bottom, length),
        ))
    return tuple(events), tuple(sorted(limitations))


def _nick_events(
    topology: str, source_sha: str, record: RestrictionRecord, start: int,
    orientation: str, length: int, occurrence_id: str,
) -> tuple[tuple[NickEvent, ...], tuple[str, ...]]:
    nick = record.cleavage.nick
    if nick is None:
        return (), ()
    if orientation == "forward":
        strand, offset = nick.strand, nick.boundary_offset
    else:
        strand = nick.reverse_orientation.strand
        offset = nick.reverse_orientation.boundary_offset
    boundary = start + offset
    complete = topology == "circular" or 0 <= boundary <= length
    return (
        (NickEvent(
            enzyme_id=record.enzyme_id,
            occurrence_id=occurrence_id,
            event_ordinal=0,
            orientation=orientation,
            strand=strand,
            status="complete" if complete else "geometry_out_of_bounds",
            boundary=boundary % length if topology == "circular" else boundary if complete else None,
            boundary_unwrapped=boundary,
            winding=boundary // length if topology == "circular" else 0,
            contributor_group_id=_nick_group_id(source_sha, topology, strand, boundary, length),
        ),),
        () if complete else ("geometry_out_of_bounds",),
    )


def _region_contains(start: int, regions: tuple[tuple[int, int], ...]) -> bool:
    return not regions or any(region_start <= start < region_end for region_start, region_end in regions)


def _event_cardinality(record: RestrictionRecord) -> int:
    if record.cleavage.status == "known_double_strand":
        return len(record.cleavage.events)
    if record.cleavage.status == "known_single_strand_nick":
        return 1
    return 0


def _candidate_starts(sequence_length: int, motif_length: int, topology: str) -> int:
    if topology == "circular":
        return sequence_length if motif_length <= sequence_length else 0
    return max(sequence_length - motif_length + 1, 0)


def _build_scan_plan(
    records: Sequence[RestrictionRecord], sequence_length: int, topology: str,
) -> tuple[
    tuple[tuple[str, tuple[tuple[RestrictionRecord, str], ...]], ...],
    dict[str, set[str]],
    int,
]:
    """Build the one exact, deduplicated plan shared by admission and scanning."""
    long_motifs: dict[str, set[str]] = defaultdict(set)
    jobs: dict[str, dict[tuple[str, str], RestrictionRecord]] = defaultdict(dict)
    for record in records:
        for motif in record.recognition.site_alternatives_iupac:
            if len(motif) > sequence_length:
                long_motifs[motif].add(record.enzyme_id)
            else:
                jobs[motif][(record.enzyme_id, "forward")] = record
        if not record.recognition.palindromic:
            for motif in record.recognition.reverse_complement_alternatives_iupac:
                if len(motif) <= sequence_length:
                    jobs[motif][(record.enzyme_id, "reverse")] = record

    plan = tuple(
        (
            motif,
            tuple(
                (consumers[key], key[1])
                for key in sorted(consumers, key=lambda item: (item[0].casefold(), item[1]))
            ),
        )
        for motif, consumers in sorted(jobs.items())
    )
    charged_work = sum(
        _candidate_starts(sequence_length, len(motif), topology) * len(motif)
        for motif, _consumers in plan
    )
    return plan, long_motifs, charged_work


def analyze_sequence(
    *,
    sequence: str,
    topology: Literal["linear", "circular"],
    catalog: CatalogView,
    records: Sequence[RestrictionRecord],
    include_possible_sites: bool = True,
    regions: tuple[tuple[int, int], ...] = (),
) -> AnalysisResult:
    normalized = normalize_dna(sequence)
    if topology not in {"linear", "circular"}:
        raise InvalidDNAError("topology must be linear or circular")
    if len(normalized) > MAX_INLINE_SEQUENCE_LENGTH:
        raise AnalysisLimitError("sequence exceeds analysis limit")
    if len(regions) > MAX_REGIONS:
        raise AnalysisLimitError("request exceeds analysis limit")
    if any(start < 0 or end <= start or end > len(normalized) for start, end in regions):
        raise InvalidDNAError("analysis region is invalid")
    ordered_regions = tuple(sorted(regions))
    if any(a[1] > b[0] for a, b in zip(ordered_regions, ordered_regions[1:])):
        raise InvalidDNAError("analysis regions overlap")

    selected = {record.enzyme_id: record for record in records}
    policy_receipt = resource_policy_receipt()
    policy_sha256 = hashlib.sha256(
        rfc8785.dumps(policy_receipt.model_dump(mode="json", by_alias=True))
    ).hexdigest()
    scan_plan, long_motifs, scan_work = _build_scan_plan(
        tuple(selected.values()), len(normalized), topology,
    )
    pattern_count = len(scan_plan)
    if pattern_count > MAX_ANALYSIS_PATTERNS:
        raise AnalysisLimitError("pattern count exceeds analysis limit")
    if scan_work > MAX_SCAN_WORK:
        raise AnalysisLimitError("scan work exceeds analysis limit")

    source_sha = hashlib.sha256(normalized.encode("ascii")).hexdigest()
    record_ids = tuple(sorted(selected))
    scope_sha = hashlib.sha256(rfc8785.dumps({"enzyme_ids": record_ids})).hexdigest()
    region_sha = hashlib.sha256(rfc8785.dumps({
        "regions": ordered_regions, "include_possible_sites": include_possible_sites,
    })).hexdigest()
    cache_key = tuple(str(item) for item in (
        source_sha, topology, catalog.content_sha256, scope_sha, region_sha, ALGORITHM_VERSION,
        policy_sha256,
    ))
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached is not None:
            _cache.move_to_end(cache_key)
            return AnalysisResult.model_validate_json(cached.canonical_result, strict=True)

    typed_limitations = tuple(
        AnalysisLimitation(
            code="recognition_motif_longer_than_molecule",
            motif=motif,
            motif_length_bp=len(motif),
            molecule_length_bp=len(normalized),
            enzyme_ids=tuple(sorted(enzyme_ids, key=str.casefold)),
        )
        for motif, enzyme_ids in sorted(long_motifs.items())
    )
    long_limited_enzymes = {enzyme_id for ids in long_motifs.values() for enzyme_id in ids}

    raw_occurrences: list[tuple[RestrictionRecord, int, str, str, str, str]] = []
    seen: set[tuple[str, int, str]] = set()
    event_count = 0
    encoded_budget = _RESPONSE_BASE_BUDGET
    for pattern, consumers in scan_plan:
        for start, certainty, window in _scan(normalized, pattern, topology):
            if (certainty == "possible" and not include_possible_sites) or not _region_contains(start, ordered_regions):
                continue
            for record, orientation in consumers:
                key = (record.enzyme_id, start, orientation)
                if key in seen:
                    continue
                next_events = _event_cardinality(record)
                if len(raw_occurrences) >= MAX_RETURNED_OCCURRENCES:
                    raise AnalysisLimitError("returned occurrences exceed analysis limit")
                if event_count + next_events > MAX_RETURNED_EVENTS:
                    raise AnalysisLimitError("returned events exceed analysis limit")
                next_budget = (
                    encoded_budget + _RESPONSE_OCCURRENCE_BUDGET
                    + next_events * _RESPONSE_EVENT_BUDGET
                )
                if next_budget > MAX_RESPONSE_BYTES:
                    raise AnalysisLimitError("analysis response exceeds byte limit")
                seen.add(key)
                raw_occurrences.append((record, start, orientation, certainty, window, pattern))
                event_count += next_events
                encoded_budget = next_budget

    raw_occurrences.sort(key=lambda row: (
        row[0].canonical_name.casefold(), row[0].enzyme_id.casefold(), row[1],
        0 if row[2] == "forward" else 1,
    ))
    per_enzyme: dict[str, int] = defaultdict(int)
    occurrences: list[AnalysisOccurrence] = []
    for record, start, orientation, certainty, window, pattern in raw_occurrences:
        ordinal = per_enzyme[record.enzyme_id]
        per_enzyme[record.enzyme_id] += 1
        occurrence_identity = {
            "enzyme_id": record.enzyme_id, "site_start": start,
            "orientation": orientation, "ordinal": ordinal,
        }
        occurrence_id = "sha256:" + hashlib.sha256(rfc8785.dumps(occurrence_identity)).hexdigest()
        limitations: set[str] = set()
        dsb: tuple[DoubleStrandEvent, ...] = ()
        nicks: tuple[NickEvent, ...] = ()
        if record.cleavage.status == "known_double_strand":
            dsb, event_limitations = _dsb_events(
                normalized, topology, source_sha, record, start, orientation, certainty, occurrence_id,
            )
            limitations.update(event_limitations)
        elif record.cleavage.status == "known_single_strand_nick":
            nicks, nick_limitations = _nick_events(
                topology, source_sha, record, start, orientation, len(normalized), occurrence_id,
            )
            limitations.update(nick_limitations)
        else:
            limitations.add("enzyme_geometry_unavailable")
        end = start + len(pattern)
        occurrences.append(AnalysisOccurrence(
            occurrence_id=occurrence_id,
            occurrence_ordinal=ordinal,
            enzyme_id=record.enzyme_id,
            canonical_name=record.canonical_name,
            orientation=orientation,
            certainty=certainty,
            recognition_pattern=pattern,
            site_start=start,
            site_end_unwrapped=end,
            site_segments=_segments(start, end, len(normalized), topology),
            wraps_origin=topology == "circular" and end > len(normalized),
            matched_reference_sequence=window,
            double_strand_events=dsb,
            nicks=nicks,
            limitations=tuple(sorted(limitations)),
        ))

    definite = sum(row.certainty == "definite" for row in occurrences)
    possible = len(occurrences) - definite
    complete_dsb = sum(
        event.status == "complete" and row.certainty == "definite"
        for row in occurrences for event in row.double_strand_events
    )
    complete_nicks = sum(
        nick.status == "complete" and row.certainty == "definite"
        for row in occurrences for nick in row.nicks
    )

    occurrence_by_enzyme: dict[str, list[AnalysisOccurrence]] = defaultdict(list)
    for occurrence in occurrences:
        occurrence_by_enzyme[occurrence.enzyme_id].append(occurrence)
    enzyme_summaries = []
    for record in sorted(selected.values(), key=lambda row: (
        row.canonical_name.casefold(), row.enzyme_id.casefold(),
    )):
        rows = occurrence_by_enzyme[record.enzyme_id]
        summary_limitations = {item for row in rows for item in row.limitations}
        if record.enzyme_id in long_limited_enzymes:
            summary_limitations.add("recognition_motif_longer_than_molecule")
        enzyme_summaries.append(EnzymeSummary(
            enzyme_id=record.enzyme_id,
            canonical_name=record.canonical_name,
            analysis_capability=record.analysis_capability,
            cleavage_status=record.cleavage.status,
            recognition_site_count_definite=sum(row.certainty == "definite" for row in rows),
            recognition_site_count_possible=sum(row.certainty == "possible" for row in rows),
            double_strand_break_count=sum(
                event.status == "complete" and row.certainty == "definite"
                for row in rows for event in row.double_strand_events
            ),
            nick_count=sum(
                nick.status == "complete" and row.certainty == "definite"
                for row in rows for nick in row.nicks
            ),
            limitations=tuple(sorted(summary_limitations)),
        ))

    groups: dict[str, list[DoubleStrandEvent]] = defaultdict(list)
    for row in occurrences:
        for event in row.double_strand_events:
            groups[event.contributor_group_id].append(event)
    grouped_cleavages = []
    for group_id, events in sorted(groups.items()):
        ordered_events = sorted(events, key=lambda event: (
            event.enzyme_id.casefold(), event.occurrence_id, event.event_ordinal,
        ))
        first = ordered_events[0]
        grouped_cleavages.append(GroupedCleavage(
            contributor_group_id=group_id,
            status=first.status,
            top_boundary=first.top_boundary,
            bottom_boundary=first.bottom_boundary,
            overhang_kind=first.overhang_kind,
            overhang_length_nt=first.overhang_length_nt,
            overhang_sequence_5to3=first.overhang_sequence_5to3,
            overhang_source_strand=first.overhang_source_strand,
            protruding_strand=first.protruding_strand,
            contributing_enzyme_ids=tuple(sorted({event.enzyme_id for event in ordered_events}, key=str.casefold)),
            contributors=tuple(CleavageContributor(
                enzyme_id=event.enzyme_id,
                occurrence_id=event.occurrence_id,
                event_ordinal=event.event_ordinal,
                orientation=event.orientation,
            ) for event in ordered_events),
        ))

    warnings = ("possible_recognition_sites_present",) if possible else ()
    payload = {
        "algorithm_id": ALGORITHM_ID,
        "algorithm_version": ALGORITHM_VERSION,
        "source_sha256": source_sha,
        "topology": topology,
        "sequence_length": len(normalized),
        "catalog_sha256": catalog.content_sha256,
        "scope_sha256": scope_sha,
        "region_policy_sha256": region_sha,
        "resource_policy_receipt": policy_receipt,
        "resource_policy_sha256": policy_sha256,
        "counts": AnalysisCounts(
            recognition_site_count_definite=definite,
            recognition_site_count_possible=possible,
            double_strand_break_count=complete_dsb,
            nick_count=complete_nicks,
        ),
        "enzyme_summaries": tuple(enzyme_summaries),
        "occurrences": tuple(occurrences),
        "grouped_cleavages": tuple(grouped_cleavages),
        "warnings": warnings,
        "limitations": typed_limitations,
    }
    digest_payload = AnalysisResult.model_validate({**payload, "result_sha256": "0" * 64})
    result = AnalysisResult.model_validate({
        **payload,
        "result_sha256": hashlib.sha256(digest_payload.canonical_result_bytes()).hexdigest(),
    })
    canonical_result = rfc8785.dumps(result.model_dump(mode="json", by_alias=True))
    if len(canonical_result) > MAX_RESPONSE_BYTES:
        raise AnalysisLimitError("analysis response exceeds byte limit")
    entry = _CacheEntry(key=cache_key, canonical_result=canonical_result)
    if _retained_weight(entry) <= CACHE_MAX_RESULT_WEIGHT_BYTES:
        with _cache_lock:
            concurrent = _cache.get(cache_key)
            if concurrent is not None:
                _cache.move_to_end(cache_key)
                return AnalysisResult.model_validate_json(
                    concurrent.canonical_result, strict=True
                )
            _cache[cache_key] = entry
            _cache.move_to_end(cache_key)
            while (
                len(_cache) > CACHE_MAX_ENTRIES
                or _retained_weight(_cache) > CACHE_MAX_TOTAL_WEIGHT_BYTES
            ):
                _cache.popitem(last=False)
    return result


__all__ = [
    "ALGORITHM_ID", "ALGORITHM_VERSION", "AnalysisLimitError", "AnalysisResult",
    "CACHE_MAX_ENTRIES", "InvalidDNAError", "MAX_ANALYSIS_PATTERNS",
    "MAX_EXPLICIT_ENZYME_IDS", "MAX_INLINE_SEQUENCE_LENGTH", "MAX_REGIONS",
    "MAX_RESPONSE_BYTES", "MAX_RETURNED_EVENTS", "MAX_RETURNED_OCCURRENCES",
    "MAX_SCAN_WORK", "analyze_sequence", "normalize_dna", "reverse_complement",
]
