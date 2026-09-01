"""Exact IUPAC-aware restriction-site and strand-cleavage analysis."""
from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict, defaultdict
from types import MappingProxyType
from typing import Literal, Sequence

import rfc8785
from pydantic import BaseModel, ConfigDict

from services.restriction_catalog import CatalogView, RestrictionRecord

ALGORITHM_ID = "bms-restriction-analysis"
ALGORITHM_VERSION = "2.0.0"
MAX_INLINE_SEQUENCE_LENGTH = 5_000_000
MAX_EXPLICIT_ENZYME_IDS = 256
MAX_REGIONS = 128
MAX_RETURNED_OCCURRENCES = 25_000
MAX_RETURNED_EVENTS = 50_000
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
CACHE_MAX_ENTRIES = 32

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


class DoubleStrandEvent(StrictModel):
    event_ordinal: int
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
    contributor_group_id: str
    activity_assessment: Literal["not_evaluated"] = "not_evaluated"
    methylation_context: Literal["unknown"] = "unknown"


class NickEvent(StrictModel):
    event_ordinal: int
    strand: Literal["top", "bottom"]
    status: Literal["complete", "geometry_out_of_bounds"]
    boundary: int | None
    boundary_unwrapped: int
    winding: int


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


class AnalysisResult(StrictModel):
    algorithm_id: Literal["bms-restriction-analysis"] = ALGORITHM_ID
    algorithm_version: Literal["2.0.0"] = ALGORITHM_VERSION
    source_sha256: str
    topology: Literal["linear", "circular"]
    sequence_length: int
    catalog_sha256: str
    scope_sha256: str
    region_policy_sha256: str
    counts: AnalysisCounts
    occurrences: tuple[AnalysisOccurrence, ...]
    warnings: tuple[str, ...]
    limitations: tuple[str, ...]
    result_sha256: str

    def canonical_result_bytes(self) -> bytes:
        payload = self.model_dump(mode="json")
        payload.pop("result_sha256", None)
        return rfc8785.dumps(payload)


_cache_lock = threading.RLock()
_cache: OrderedDict[tuple[str, ...], AnalysisResult] = OrderedDict()
_compiled_lock = threading.Lock()
_compiled: dict[str, tuple[frozenset[str], ...]] = {}


def reverse_complement(sequence: str) -> str:
    return sequence.translate(_COMPLEMENT)[::-1]


def normalize_dna(sequence: str) -> str:
    if not isinstance(sequence, str) or not sequence or any(c.isspace() for c in sequence):
        raise InvalidDNAError("DNA must be nonempty and contain no whitespace")
    normalized = sequence.upper().replace("U", "T")
    if any(character not in _IUPAC for character in normalized):
        raise InvalidDNAError("DNA contains a symbol outside the IUPAC alphabet")
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


def _scan(sequence: str, pattern: str, topology: str) -> tuple[tuple[int, str, str], ...]:
    length = len(sequence)
    motif = _compile(pattern)
    width = len(motif)
    if width > length:
        return ()
    starts = length if topology == "circular" else length - width + 1
    extended = sequence + sequence[: width - 1] if topology == "circular" and width > 1 else sequence
    matches = []
    for start in range(starts):
        window = extended[start:start + width]
        certainty = _certainty(window, motif)
        if certainty is not None:
            matches.append((start, certainty, window))
    return tuple(matches)


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


def _group_id(source_sha: str, topology: str, top: int, bottom: int) -> str:
    identity = {"source_sha256": source_sha, "topology": topology, "top": top, "bottom": bottom}
    return "sha256:" + hashlib.sha256(rfc8785.dumps(identity)).hexdigest()


def _dsb_events(
    sequence: str, topology: str, source_sha: str, record: RestrictionRecord,
    start: int, orientation: str, certainty: str,
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
        overhang = None
        if complete and certainty == "definite":
            bases = _target_slice(sequence, min(top, bottom), max(top, bottom), topology)
            overhang = reverse_complement(bases) if orientation == "reverse" else bases
        events.append(
            DoubleStrandEvent(
                event_ordinal=ordinal,
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
                contributor_group_id=_group_id(source_sha, topology, top, bottom),
            )
        )
    return tuple(events), tuple(sorted(limitations))


def _nick_events(
    topology: str, record: RestrictionRecord, start: int, orientation: str, length: int
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
            event_ordinal=0,
            strand=strand,
            status="complete" if complete else "geometry_out_of_bounds",
            boundary=boundary % length if topology == "circular" else boundary if complete else None,
            boundary_unwrapped=boundary,
            winding=boundary // length if topology == "circular" else 0,
        ),),
        () if complete else ("geometry_out_of_bounds",),
    )


def _region_contains(start: int, regions: tuple[tuple[int, int], ...]) -> bool:
    return not regions or any(region_start <= start < region_end for region_start, region_end in regions)


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

    source_sha = hashlib.sha256(normalized.encode("ascii")).hexdigest()
    record_ids = tuple(sorted({record.enzyme_id for record in records}))
    scope_sha = hashlib.sha256(rfc8785.dumps({"enzyme_ids": record_ids})).hexdigest()
    region_sha = hashlib.sha256(rfc8785.dumps({"regions": ordered_regions, "include_possible_sites": include_possible_sites})).hexdigest()
    cache_key = (source_sha, topology, catalog.content_sha256, scope_sha, region_sha, ALGORITHM_VERSION)
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached is not None:
            _cache.move_to_end(cache_key)
            return cached

    selected = {record.enzyme_id: record for record in records}
    scan_jobs: dict[str, list[tuple[RestrictionRecord, str]]] = defaultdict(list)
    for record in selected.values():
        for pattern in record.recognition.site_alternatives_iupac:
            scan_jobs[pattern].append((record, "forward"))
        if not record.recognition.palindromic:
            for pattern in record.recognition.reverse_complement_alternatives_iupac:
                scan_jobs[pattern].append((record, "reverse"))

    raw_occurrences: list[tuple[RestrictionRecord, int, str, str, str, str]] = []
    seen: set[tuple[str, int, str]] = set()
    for pattern, consumers in scan_jobs.items():
        for start, certainty, window in _scan(normalized, pattern, topology):
            if certainty == "possible" and not include_possible_sites or not _region_contains(start, ordered_regions):
                continue
            for record, orientation in consumers:
                key = (record.enzyme_id, start, orientation)
                if key not in seen:
                    seen.add(key)
                    raw_occurrences.append((record, start, orientation, certainty, window, pattern))
            if len(raw_occurrences) > MAX_RETURNED_OCCURRENCES:
                raise AnalysisLimitError("returned occurrences exceed analysis limit")

    raw_occurrences.sort(key=lambda row: (
        row[0].canonical_name.casefold(), row[0].enzyme_id.casefold(), row[1],
        0 if row[2] == "forward" else 1,
    ))
    per_enzyme: dict[str, int] = defaultdict(int)
    occurrences: list[AnalysisOccurrence] = []
    event_count = 0
    for record, start, orientation, certainty, window, pattern in raw_occurrences:
        ordinal = per_enzyme[record.enzyme_id]
        per_enzyme[record.enzyme_id] += 1
        limitations: set[str] = set()
        dsb: tuple[DoubleStrandEvent, ...] = ()
        nicks: tuple[NickEvent, ...] = ()
        if record.cleavage.status == "known_double_strand":
            dsb, event_limitations = _dsb_events(
                normalized, topology, source_sha, record, start, orientation, certainty
            )
            limitations.update(event_limitations)
        elif record.cleavage.status == "known_single_strand_nick":
            nicks, nick_limitations = _nick_events(topology, record, start, orientation, len(normalized))
            limitations.update(nick_limitations)
        else:
            limitations.add("enzyme_geometry_unavailable")
        event_count += len(dsb) + len(nicks)
        if event_count > MAX_RETURNED_EVENTS:
            raise AnalysisLimitError("returned events exceed analysis limit")
        end = start + len(pattern)
        occurrence_identity = {
            "enzyme_id": record.enzyme_id, "site_start": start,
            "orientation": orientation, "ordinal": ordinal,
        }
        occurrences.append(AnalysisOccurrence(
            occurrence_id="sha256:" + hashlib.sha256(rfc8785.dumps(occurrence_identity)).hexdigest(),
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
    warnings = ("possible_recognition_sites_present",) if possible else ()
    limitations = tuple(sorted({item for row in occurrences for item in row.limitations}))
    payload = {
        "algorithm_id": ALGORITHM_ID, "algorithm_version": ALGORITHM_VERSION,
        "source_sha256": source_sha, "topology": topology, "sequence_length": len(normalized),
        "catalog_sha256": catalog.content_sha256, "scope_sha256": scope_sha,
        "region_policy_sha256": region_sha,
        "counts": AnalysisCounts(
            recognition_site_count_definite=definite,
            recognition_site_count_possible=possible,
            double_strand_break_count=complete_dsb,
            nick_count=complete_nicks,
        ),
        "occurrences": tuple(occurrences), "warnings": warnings, "limitations": limitations,
    }
    digest_payload = AnalysisResult.model_validate({**payload, "result_sha256": "0" * 64})
    result = AnalysisResult.model_validate({
        **payload,
        "result_sha256": hashlib.sha256(digest_payload.canonical_result_bytes()).hexdigest(),
    })
    if len(rfc8785.dumps(result.model_dump(mode="json"))) > MAX_RESPONSE_BYTES:
        raise AnalysisLimitError("analysis response exceeds byte limit")
    with _cache_lock:
        _cache[cache_key] = result
        _cache.move_to_end(cache_key)
        while len(_cache) > CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)
    return result


__all__ = [
    "ALGORITHM_ID", "ALGORITHM_VERSION", "AnalysisLimitError", "AnalysisResult",
    "InvalidDNAError", "MAX_EXPLICIT_ENZYME_IDS", "MAX_INLINE_SEQUENCE_LENGTH",
    "MAX_REGIONS", "MAX_RESPONSE_BYTES", "MAX_RETURNED_EVENTS",
    "MAX_RETURNED_OCCURRENCES", "analyze_sequence", "normalize_dna", "reverse_complement",
]
