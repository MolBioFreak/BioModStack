"""Exact duplex restriction-digest simulation over Phase 2 analysis authority."""
from __future__ import annotations

import hashlib
from typing import Any, Literal, Sequence

import rfc8785
from pydantic import BaseModel, ConfigDict, Field

from services.restriction_analysis import (
    ALGORITHM_ID as ANALYSIS_ALGORITHM_ID,
    ALGORITHM_VERSION as ANALYSIS_ALGORITHM_VERSION,
    AnalysisOccurrence,
    CleavageContributor,
    ResourcePolicyReceipt,
    analyze_sequence,
    reverse_complement,
)
from services.restriction_catalog import CatalogView, RestrictionRecord

DIGEST_ALGORITHM_ID = "bms-restriction-duplex-digest"
DIGEST_ALGORITHM_VERSION = "1.0.0"
MAX_SELECTED_ENZYMES = 64
MAX_PHYSICAL_CUTS = 4096
MAX_FRAGMENTS = 4097
MAX_TOTAL_FRAGMENT_BASES = 10_000_000
MAX_SIMULATION_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_SAVED_OUTPUTS = 4097


class DigestGeometryError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DigestLimitError(ValueError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class DigestResourcePolicy(StrictModel):
    schema_: Literal["bms.molbio.restriction-digest-resource-policy.v1"] = Field(alias="schema")
    policy_version: Literal["1.0.0"] = "1.0.0"
    selected_enzyme_maximum: int = MAX_SELECTED_ENZYMES
    physical_cut_maximum: int = MAX_PHYSICAL_CUTS
    fragment_maximum: int = MAX_FRAGMENTS
    total_fragment_bases_maximum: int = MAX_TOTAL_FRAGMENT_BASES
    simulation_response_maximum_bytes: int = MAX_SIMULATION_RESPONSE_BYTES
    saved_output_maximum: int = MAX_SAVED_OUTPUTS
    worker_concurrency: Literal[2] = 2
    queue_policy: Literal["reject_when_all_workers_busy"] = "reject_when_all_workers_busy"
    cancellation_policy: Literal[
        "worker_continues_and_capacity_is_retained_until_completion"
    ] = "worker_continues_and_capacity_is_retained_until_completion"


class DigestEnd(StrictModel):
    kind: Literal[
        "natural", "blunt", "five_prime_overhang", "three_prime_overhang",
        "no_cut_circular",
    ]
    enzyme_created: bool
    side: Literal["left", "right"]
    protruding_strand: Literal["top", "bottom"] | None
    overhang_sequence_5to3: str | None
    length_nt: int
    top_boundary: int | None
    bottom_boundary: int | None
    top_boundary_unwrapped: int | None
    bottom_boundary_unwrapped: int | None
    top_winding: int | None
    bottom_winding: int | None
    contributing_enzyme_ids: tuple[str, ...]
    contributors: tuple[CleavageContributor, ...]
    contributor_group_id: str | None


class PhysicalCleavage(StrictModel):
    cleavage_index: int
    contributor_group_id: str
    top_boundary: int
    bottom_boundary: int
    top_boundary_unwrapped: int
    bottom_boundary_unwrapped: int
    top_winding: int
    bottom_winding: int
    overhang_kind: Literal["blunt", "five_prime", "three_prime"]
    overhang_length_nt: int
    contributing_enzyme_ids: tuple[str, ...]
    contributors: tuple[CleavageContributor, ...]


class DigestFragment(StrictModel):
    fragment_index: int
    topology: Literal["linear", "circular"]
    top_strand_sequence: str
    reference_span_bp: int
    source_segments: tuple[tuple[int, int], ...]
    top_start_boundary: int
    top_end_boundary: int
    bottom_start_boundary: int
    bottom_end_boundary: int
    top_start_boundary_normalized: int
    top_end_boundary_normalized: int
    bottom_start_boundary_normalized: int
    bottom_end_boundary_normalized: int
    top_start_winding: int
    top_end_winding: int
    bottom_start_winding: int
    bottom_end_winding: int
    wraps_origin: bool
    left_end: DigestEnd
    right_end: DigestEnd
    lineage_cleavage_group_ids: tuple[str, ...]
    contributing_enzyme_ids: tuple[str, ...]


class DigestSourceReceipt(StrictModel):
    kind: Literal["inline_dna", "molecular_revision"]
    name: str | None
    sequence_id: str | None
    revision_id: str | None
    revision_number: int | None
    content_sha256: str
    content_length: int
    topology: Literal["linear", "circular"]


class DigestCatalogCounts(StrictModel):
    total: int
    geometry_ready: int
    commercial_geometry_ready: int
    unknown_geometry: int
    nicking: int
    two_event_double_strand: int


class DigestCatalogBounds(StrictModel):
    default_limit: int
    maximum_limit: int
    query_max_length: int
    analysis_inline_sequence_max_length: int
    analysis_explicit_enzyme_maximum: int
    analysis_region_maximum: int
    analysis_scan_pattern_maximum: int
    analysis_scan_work_maximum: int
    analysis_occurrence_maximum: int
    analysis_event_maximum: int
    analysis_response_maximum_bytes: int
    analysis_cache_maximum_entries: int
    analysis_cache_maximum_total_weight_bytes: int
    analysis_cache_maximum_result_weight_bytes: int


class DigestCatalogReceipt(StrictModel):
    catalog_id: str
    catalog_sha256: str
    source_release: str
    counts: DigestCatalogCounts
    source_year: int
    source_age_years: int
    source_age_notice: str
    supplier_code_notice: str
    bounds: DigestCatalogBounds
    resource_policy: ResourcePolicyReceipt
    resource_policy_sha256: str
    analysis_enabled: Literal[True]
    digest_enabled: Literal[True]


class DigestSimulation(StrictModel):
    schema_: Literal["bms.molbio.restriction-digest-simulation.v1"] = Field(alias="schema")
    cleavage_state: Literal["uncut", "linearized", "fragmented"]
    activity_assessment: Literal["not_evaluated"] = "not_evaluated"
    source: DigestSourceReceipt
    catalog: DigestCatalogReceipt
    selected_enzyme_ids: tuple[str, ...]
    selected_enzymes: tuple[RestrictionRecord, ...]
    analysis_algorithm_id: Literal["bms-restriction-analysis"] = ANALYSIS_ALGORITHM_ID
    analysis_algorithm_version: str = ANALYSIS_ALGORITHM_VERSION
    analysis_result_sha256: str
    digest_algorithm_id: Literal["bms-restriction-duplex-digest"] = DIGEST_ALGORITHM_ID
    digest_algorithm_version: Literal["1.0.0"] = DIGEST_ALGORITHM_VERSION
    resource_policy: DigestResourcePolicy
    resource_policy_sha256: str
    request_sha256: str
    occurrences: tuple[AnalysisOccurrence, ...]
    cleavages: tuple[PhysicalCleavage, ...]
    fragments: tuple[DigestFragment, ...]
    warnings: tuple[str, ...]
    limitations: tuple[str, ...]
    simulation_sha256: str

    def canonical_unsigned_bytes(self) -> bytes:
        payload = self.model_dump(mode="json", by_alias=True)
        payload.pop("simulation_sha256", None)
        return rfc8785.dumps(payload)

    def canonical_bytes(self) -> bytes:
        return rfc8785.dumps(self.model_dump(mode="json", by_alias=True))


def resource_policy_receipt() -> DigestResourcePolicy:
    return DigestResourcePolicy(schema="bms.molbio.restriction-digest-resource-policy.v1")


def _circular_slice(sequence: str, start: int, end: int) -> str:
    length = len(sequence)
    return "".join(sequence[index % length] for index in range(start, end))


def _segments(start: int, end: int, length: int, topology: str) -> tuple[tuple[int, int], ...]:
    if topology == "linear":
        return ((start, end),)
    span = end - start
    if span == length:
        return ((0, length),)
    normalized = start % length
    stop = normalized + span
    if stop <= length:
        return ((normalized, stop),)
    return ((normalized, length), (0, stop % length))


def _natural(side: Literal["left", "right"], boundary: int) -> DigestEnd:
    return DigestEnd(
        kind="natural", enzyme_created=False, side=side, protruding_strand=None,
        overhang_sequence_5to3=None, length_nt=0,
        top_boundary=boundary, bottom_boundary=boundary,
        top_boundary_unwrapped=boundary, bottom_boundary_unwrapped=boundary,
        top_winding=0, bottom_winding=0, contributing_enzyme_ids=(), contributors=(),
        contributor_group_id=None,
    )


def _no_cut_circular(side: Literal["left", "right"]) -> DigestEnd:
    return DigestEnd(
        kind="no_cut_circular", enzyme_created=False, side=side, protruding_strand=None,
        overhang_sequence_5to3=None, length_nt=0, top_boundary=None, bottom_boundary=None,
        top_boundary_unwrapped=None, bottom_boundary_unwrapped=None,
        top_winding=None, bottom_winding=None, contributing_enzyme_ids=(), contributors=(),
        contributor_group_id=None,
    )


def _cut_end(
    sequence: str,
    topology: str,
    cut: PhysicalCleavage,
    side: Literal["left", "right"],
) -> DigestEnd:
    top = cut.top_boundary_unwrapped
    bottom = cut.bottom_boundary_unwrapped
    delta = bottom - top
    if delta == 0:
        kind = "blunt"
        strand = None
        overhang = None
    elif delta > 0:
        kind = "five_prime_overhang"
        strand = "top" if side == "left" else "bottom"
        interval = sequence[top:bottom] if topology == "linear" else _circular_slice(sequence, top, bottom)
        overhang = interval if side == "left" else reverse_complement(interval)
    else:
        kind = "three_prime_overhang"
        strand = "bottom" if side == "left" else "top"
        interval = sequence[bottom:top] if topology == "linear" else _circular_slice(sequence, bottom, top)
        overhang = reverse_complement(interval) if side == "left" else interval
    return DigestEnd(
        kind=kind, enzyme_created=True, side=side, protruding_strand=strand,
        overhang_sequence_5to3=overhang, length_nt=abs(delta),
        top_boundary=cut.top_boundary, bottom_boundary=cut.bottom_boundary,
        top_boundary_unwrapped=top, bottom_boundary_unwrapped=bottom,
        top_winding=cut.top_winding, bottom_winding=cut.bottom_winding,
        contributing_enzyme_ids=cut.contributing_enzyme_ids,
        contributors=cut.contributors, contributor_group_id=cut.contributor_group_id,
    )


def _physical_cuts(analysis, sequence_length: int, topology: str) -> tuple[PhysicalCleavage, ...]:
    event_by_group: dict[str, list[Any]] = {}
    definite_occurrences = [row for row in analysis.occurrences if row.certainty == "definite"]
    for occurrence in definite_occurrences:
        for event in occurrence.double_strand_events:
            if event.status == "complete":
                event_by_group.setdefault(event.contributor_group_id, []).append(event)
    raw: list[tuple[int, int, int, int, str, tuple[Any, ...]]] = []
    for group_id, events in event_by_group.items():
        ordered = tuple(sorted(events, key=lambda row: (
            row.enzyme_id.casefold(), row.occurrence_id, row.event_ordinal,
        )))
        first = ordered[0]
        if topology == "linear":
            top = int(first.top_boundary_unwrapped)
            bottom = int(first.bottom_boundary_unwrapped)
            top_normal = top
            bottom_normal = bottom
        else:
            top_normal = int(first.top_boundary)
            bottom_normal = int(first.bottom_boundary)
            delta = int(first.bottom_boundary_unwrapped) - int(first.top_boundary_unwrapped)
            top = top_normal
            bottom = top + delta
        raw.append((top_normal, bottom_normal, top, bottom, group_id, ordered))
    raw.sort(key=lambda row: (row[0], row[1], row[4]))
    if len(raw) > MAX_PHYSICAL_CUTS:
        raise DigestLimitError("physical cut count exceeds digest limit")

    for left, right in zip(raw, raw[1:]):
        if left[0] == right[0] or left[1] == right[1]:
            raise DigestGeometryError(
                "unsupported_crossing_cleavage_geometry",
                "selected cleavages share one strand boundary without an identical duplex cut",
            )
        if left[1] > right[1]:
            raise DigestGeometryError(
                "unsupported_crossing_cleavage_geometry",
                "selected top- and bottom-strand cut orders cross",
            )
        left_interval = (min(left[2], left[3]), max(left[2], left[3]))
        right_interval = (min(right[2], right[3]), max(right[2], right[3]))
        if max(left_interval[0], right_interval[0]) < min(left_interval[1], right_interval[1]):
            raise DigestGeometryError(
                "overlapping_cleavage_geometry",
                "selected stagger intervals overlap",
            )
    if topology == "circular" and len(raw) > 1:
        bottoms = [row[1] for row in raw]
        rotations = sum(bottoms[index] > bottoms[(index + 1) % len(bottoms)] for index in range(len(bottoms)))
        if rotations != 1:
            raise DigestGeometryError(
                "unsupported_crossing_cleavage_geometry",
                "selected circular cleavages have no consistent duplex cycle",
            )

    cuts = []
    for index, (top_normal, bottom_normal, top, bottom, group_id, events) in enumerate(raw):
        first = events[0]
        cuts.append(PhysicalCleavage(
            cleavage_index=index, contributor_group_id=group_id,
            top_boundary=top_normal, bottom_boundary=bottom_normal,
            top_boundary_unwrapped=top, bottom_boundary_unwrapped=bottom,
            top_winding=top // sequence_length if topology == "circular" else 0,
            bottom_winding=bottom // sequence_length if topology == "circular" else 0,
            overhang_kind=first.overhang_kind, overhang_length_nt=first.overhang_length_nt,
            contributing_enzyme_ids=tuple(sorted({row.enzyme_id for row in events}, key=str.casefold)),
            contributors=tuple(CleavageContributor(
                enzyme_id=row.enzyme_id, occurrence_id=row.occurrence_id,
                event_ordinal=row.event_ordinal, orientation=row.orientation,
            ) for row in events),
        ))
    return tuple(cuts)


def _fragment(
    *, sequence: str, source_topology: str, index: int, start_top: int, end_top: int,
    start_bottom: int, end_bottom: int, left: DigestEnd, right: DigestEnd,
    lineage: tuple[str, ...], enzyme_ids: tuple[str, ...], topology: str,
) -> DigestFragment:
    length = len(sequence)
    top_sequence = sequence[start_top:end_top] if source_topology == "linear" else _circular_slice(sequence, start_top, end_top)
    if not top_sequence:
        raise DigestGeometryError(
            "overlapping_cleavage_geometry", "selected cleavages create an empty physical fragment",
        )
    return DigestFragment(
        fragment_index=index, topology=topology, top_strand_sequence=top_sequence,
        reference_span_bp=end_top - start_top,
        source_segments=_segments(start_top, end_top, length, source_topology),
        top_start_boundary=start_top, top_end_boundary=end_top,
        bottom_start_boundary=start_bottom, bottom_end_boundary=end_bottom,
        top_start_boundary_normalized=start_top % length,
        top_end_boundary_normalized=end_top % length,
        bottom_start_boundary_normalized=start_bottom % length,
        bottom_end_boundary_normalized=end_bottom % length,
        top_start_winding=start_top // length if source_topology == "circular" else 0,
        top_end_winding=end_top // length if source_topology == "circular" else 0,
        bottom_start_winding=start_bottom // length if source_topology == "circular" else 0,
        bottom_end_winding=end_bottom // length if source_topology == "circular" else 0,
        wraps_origin=(
            source_topology == "circular"
            and (topology == "linear" or end_top > length or start_top != 0)
        ),
        left_end=left, right_end=right, lineage_cleavage_group_ids=lineage,
        contributing_enzyme_ids=enzyme_ids,
    )


def _construct_fragments(sequence: str, topology: str, cuts: tuple[PhysicalCleavage, ...]) -> tuple[DigestFragment, ...]:
    length = len(sequence)
    if not cuts:
        if topology == "linear":
            return (_fragment(
                sequence=sequence, source_topology="linear", index=0,
                start_top=0, end_top=length, start_bottom=0, end_bottom=length,
                left=_natural("left", 0), right=_natural("right", length),
                lineage=(), enzyme_ids=(), topology="linear",
            ),)
        end = _no_cut_circular("right")
        return (_fragment(
            sequence=sequence, source_topology="circular", index=0,
            start_top=0, end_top=length, start_bottom=0, end_bottom=length,
            left=_no_cut_circular("left"), right=end, lineage=(), enzyme_ids=(), topology="circular",
        ),)

    fragments: list[DigestFragment] = []
    if topology == "linear":
        if any(cut.top_boundary in {0, length} or cut.bottom_boundary in {0, length} for cut in cuts):
            raise DigestGeometryError(
                "overlapping_cleavage_geometry", "cut-at-terminus geometry is unsupported in digest v1",
            )
        boundaries: list[PhysicalCleavage | None] = [None, *cuts, None]
        for index, (left_cut, right_cut) in enumerate(zip(boundaries, boundaries[1:])):
            start_top = 0 if left_cut is None else left_cut.top_boundary_unwrapped
            start_bottom = 0 if left_cut is None else left_cut.bottom_boundary_unwrapped
            end_top = length if right_cut is None else right_cut.top_boundary_unwrapped
            end_bottom = length if right_cut is None else right_cut.bottom_boundary_unwrapped
            left_end = _natural("left", 0) if left_cut is None else _cut_end(sequence, topology, left_cut, "left")
            right_end = _natural("right", length) if right_cut is None else _cut_end(sequence, topology, right_cut, "right")
            adjacent = tuple(cut for cut in (left_cut, right_cut) if cut is not None)
            fragments.append(_fragment(
                sequence=sequence, source_topology=topology, index=index,
                start_top=start_top, end_top=end_top, start_bottom=start_bottom, end_bottom=end_bottom,
                left=left_end, right=right_end,
                lineage=tuple(cut.contributor_group_id for cut in adjacent),
                enzyme_ids=tuple(sorted({enzyme for cut in adjacent for enzyme in cut.contributing_enzyme_ids}, key=str.casefold)),
                topology="linear",
            ))
    else:
        ordered = cuts
        for index, left_cut in enumerate(ordered):
            right_cut = ordered[(index + 1) % len(ordered)]
            end_top = right_cut.top_boundary_unwrapped + (length if index == len(ordered) - 1 else 0)
            end_bottom = right_cut.bottom_boundary_unwrapped + (length if index == len(ordered) - 1 else 0)
            if len(ordered) == 1:
                end_top = left_cut.top_boundary_unwrapped + length
                end_bottom = left_cut.bottom_boundary_unwrapped + length
                right_cut = left_cut
            adjacent = (left_cut, right_cut)
            fragments.append(_fragment(
                sequence=sequence, source_topology=topology, index=index,
                start_top=left_cut.top_boundary_unwrapped, end_top=end_top,
                start_bottom=left_cut.bottom_boundary_unwrapped, end_bottom=end_bottom,
                left=_cut_end(sequence, topology, left_cut, "left"),
                right=_cut_end(sequence, topology, right_cut, "right"),
                lineage=tuple(cut.contributor_group_id for cut in adjacent),
                enzyme_ids=tuple(sorted({enzyme for cut in adjacent for enzyme in cut.contributing_enzyme_ids}, key=str.casefold)),
                topology="linear",
            ))
    if len(fragments) > MAX_FRAGMENTS or sum(len(row.top_strand_sequence) for row in fragments) > MAX_TOTAL_FRAGMENT_BASES:
        raise DigestLimitError("fragment output exceeds digest limit")
    return tuple(fragments)


def simulate_digest(
    *,
    sequence: str,
    topology: Literal["linear", "circular"],
    catalog: CatalogView,
    records: Sequence[RestrictionRecord],
    selected_enzyme_ids: Sequence[str],
    source_receipt: dict[str, Any],
    catalog_receipt: dict[str, Any],
) -> DigestSimulation:
    selected_ids = tuple(selected_enzyme_ids)
    if not selected_ids or len(selected_ids) > MAX_SELECTED_ENZYMES or len(set(selected_ids)) != len(selected_ids):
        raise DigestLimitError("selected enzyme list is empty, duplicate, or oversized")
    by_id = {record.enzyme_id: record for record in records}
    selected = tuple(by_id[item] for item in selected_ids)
    for record in selected:
        if record.cleavage.status == "unknown":
            raise DigestGeometryError("enzyme_geometry_unavailable", "selected enzyme has no complete DSB geometry")
        if record.cleavage.status == "known_single_strand_nick":
            raise DigestGeometryError("nicking_enzyme_not_digestible", "selected nicking enzyme cannot produce digest fragments")
    analysis = analyze_sequence(
        sequence=sequence, topology=topology, catalog=catalog, records=selected,
        include_possible_sites=True,
    )
    if any(row.certainty == "possible" for row in analysis.occurrences):
        raise DigestGeometryError("possible_site_not_digestible", "selected enzyme has a possible-only recognition site")
    if topology == "linear" and any(
        row.certainty == "definite" and event.status == "geometry_out_of_bounds"
        for row in analysis.occurrences for event in row.double_strand_events
    ):
        raise DigestGeometryError("linear_cut_out_of_bounds", "selected enzyme has incomplete off-molecule DSB geometry")
    cuts = _physical_cuts(analysis, len(sequence), topology)
    fragments = _construct_fragments(sequence, topology, cuts)
    state = "uncut" if not cuts else "linearized" if topology == "circular" and len(cuts) == 1 else "fragmented"
    policy = resource_policy_receipt()
    policy_sha = hashlib.sha256(rfc8785.dumps(policy.model_dump(mode="json", by_alias=True))).hexdigest()
    request_authority = {
        "source": source_receipt, "catalog_id": catalog.catalog_id,
        "catalog_sha256": catalog.content_sha256, "selected_enzyme_ids": selected_ids,
        "analysis_algorithm_id": ANALYSIS_ALGORITHM_ID,
        "analysis_algorithm_version": ANALYSIS_ALGORITHM_VERSION,
        "analysis_result_sha256": analysis.result_sha256,
        "digest_algorithm_id": DIGEST_ALGORITHM_ID,
        "digest_algorithm_version": DIGEST_ALGORITHM_VERSION,
        "resource_policy_sha256": policy_sha,
        "activity_assessment": "not_evaluated",
    }
    request_sha = hashlib.sha256(rfc8785.dumps(request_authority)).hexdigest()
    occurrences = tuple(row for row in analysis.occurrences if row.certainty == "definite")
    payload = {
        "schema": "bms.molbio.restriction-digest-simulation.v1",
        "cleavage_state": state, "activity_assessment": "not_evaluated",
        "source": source_receipt, "catalog": catalog_receipt,
        "selected_enzyme_ids": selected_ids, "selected_enzymes": selected,
        "analysis_algorithm_id": ANALYSIS_ALGORITHM_ID,
        "analysis_algorithm_version": ANALYSIS_ALGORITHM_VERSION,
        "analysis_result_sha256": analysis.result_sha256,
        "digest_algorithm_id": DIGEST_ALGORITHM_ID,
        "digest_algorithm_version": DIGEST_ALGORITHM_VERSION,
        "resource_policy": policy, "resource_policy_sha256": policy_sha,
        "request_sha256": request_sha, "occurrences": occurrences,
        "cleavages": cuts, "fragments": fragments,
        "warnings": tuple(analysis.warnings), "limitations": (),
    }
    unsigned = DigestSimulation.model_validate({**payload, "simulation_sha256": "0" * 64})
    result = DigestSimulation.model_validate({
        **payload,
        "simulation_sha256": hashlib.sha256(unsigned.canonical_unsigned_bytes()).hexdigest(),
    })
    if len(result.canonical_bytes()) > MAX_SIMULATION_RESPONSE_BYTES:
        raise DigestLimitError("simulation response exceeds digest byte limit")
    return result


__all__ = [
    "DIGEST_ALGORITHM_ID", "DIGEST_ALGORITHM_VERSION", "DigestGeometryError",
    "DigestLimitError", "DigestSimulation", "MAX_FRAGMENTS", "MAX_PHYSICAL_CUTS",
    "MAX_SAVED_OUTPUTS", "MAX_SELECTED_ENZYMES", "MAX_SIMULATION_RESPONSE_BYTES",
    "MAX_TOTAL_FRAGMENT_BASES", "simulate_digest",
]
