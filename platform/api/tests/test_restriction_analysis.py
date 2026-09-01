from __future__ import annotations

import hashlib

import pytest

from services.restriction_catalog import catalog_authority
from services.restriction_analysis import (
    AnalysisLimitError,
    InvalidDNAError,
    analyze_sequence,
    normalize_dna,
    reverse_complement,
)


def _analyze(sequence: str, enzymes: list[str], *, topology: str = "linear", possible: bool = True):
    view = catalog_authority.require()
    return analyze_sequence(
        sequence=sequence,
        topology=topology,
        catalog=view,
        records=tuple(view.by_id[name] for name in enzymes),
        include_possible_sites=possible,
    )


def _occ(result, enzyme: str):
    return [row for row in result.occurrences if row.enzyme_id == enzyme]


def test_ecori_linear_reports_exact_site_and_five_prime_overhang() -> None:
    result = _analyze("TTGAATTCAA", ["EcoRI"])
    occurrence = _occ(result, "EcoRI")[0]
    assert (occurrence.site_start, occurrence.site_end_unwrapped) == (2, 8)
    assert occurrence.orientation == "forward"
    assert occurrence.site_segments == ((2, 8),)
    event = occurrence.double_strand_events[0]
    assert (event.top_boundary_unwrapped, event.bottom_boundary_unwrapped) == (3, 7)
    assert (event.top_boundary, event.bottom_boundary) == (3, 7)
    assert (event.overhang_kind, event.overhang_length_nt, event.overhang_sequence_5to3) == (
        "five_prime", 4, "AATT"
    )
    assert result.counts.model_dump() == {
        "recognition_site_count_definite": 1,
        "recognition_site_count_possible": 0,
        "double_strand_break_count": 1,
        "nick_count": 0,
    }


def test_nonpalindromic_type_iis_forward_and_reverse_mirror_and_swap() -> None:
    forward = _occ(_analyze("AAGGTCTCAAAAAAAAAAAAA", ["BsaI"]), "BsaI")[0]
    reverse = _occ(_analyze("AAGAGACCAAAAAAAAAAAAA", ["BsaI"]), "BsaI")[0]
    assert (forward.orientation, forward.double_strand_events[0].top_boundary_unwrapped,
            forward.double_strand_events[0].bottom_boundary_unwrapped) == ("forward", 9, 13)
    assert (reverse.orientation, reverse.double_strand_events[0].top_boundary_unwrapped,
            reverse.double_strand_events[0].bottom_boundary_unwrapped) == ("reverse", -3, 1)


def test_overhang_sequence_and_protruding_strand_follow_common_axis_not_motif_orientation() -> None:
    forward = _occ(_analyze("AAGGTCTCAGTACAAAA", ["BsaI"]), "BsaI")[0]
    reverse = _occ(_analyze("ACGTACGTAAGAGACCAAAA", ["BsaI"]), "BsaI")[0]
    assert (
        forward.double_strand_events[0].overhang_kind,
        forward.double_strand_events[0].overhang_sequence_5to3,
        forward.double_strand_events[0].overhang_source_strand,
        forward.double_strand_events[0].protruding_strand,
    ) == ("five_prime", "GTAC", "top", "top")
    assert (
        reverse.double_strand_events[0].overhang_kind,
        reverse.double_strand_events[0].overhang_sequence_5to3,
        reverse.double_strand_events[0].overhang_source_strand,
        reverse.double_strand_events[0].protruding_strand,
    ) == ("five_prime", "CGTA", "top", "top")

    three_cases = (
        ("AACTGAAG" + "A" * 14 + "GCAA", "forward", "GC"),
        ("AAAATC" + "A" * 14 + "CTTCAGAA", "reverse", "GA"),
    )
    for sequence, orientation, expected in three_cases:
        occurrence = next(row for row in _occ(_analyze(sequence, ["AcuI"]), "AcuI") if row.orientation == orientation)
        event = occurrence.double_strand_events[0]
        assert event.overhang_kind == "three_prime"
        assert event.overhang_source_strand == event.protruding_strand == "bottom"
        assert event.overhang_sequence_5to3 == expected


def test_three_state_iupac_matching_definite_possible_and_none() -> None:
    definite = _analyze("GGCCNNNNNGGCC", ["SfiI"])
    possible = _analyze("GGCCRAAAAGGCC", ["SfiI"])
    impossible = _analyze("GGCCTAAAATGCC", ["SfiI"])
    assert _occ(definite, "SfiI")[0].certainty == "definite"
    assert _occ(possible, "SfiI")[0].certainty == "definite"  # motif N accepts every DNA IUPAC set
    assert not _occ(impossible, "SfiI")

    uncertain = _analyze("GAR TTC".replace(" ", ""), ["EcoRI"])
    assert _occ(uncertain, "EcoRI")[0].certainty == "possible"
    assert not _occ(_analyze("GACTTC", ["EcoRI"]), "EcoRI")
    assert not _occ(_analyze("GARTTC", ["EcoRI"], possible=False), "EcoRI")


def test_palindrome_not_duplicated_and_overlaps_are_retained() -> None:
    assert len(_occ(_analyze("GAATTC", ["EcoRI"]), "EcoRI")) == 1
    overlaps = _analyze("GATCGATC", ["DpnI", "MboI"])
    assert [(o.enzyme_id, o.site_start) for o in overlaps.occurrences] == [
        ("DpnI", 0), ("DpnI", 4), ("MboI", 0), ("MboI", 4)
    ]


def test_circular_origin_site_and_type_iis_cut_keep_unwrapped_geometry() -> None:
    circular = _occ(_analyze("AATTCCCG", ["EcoRI"], topology="circular"), "EcoRI")[0]
    assert (circular.site_start, circular.site_end_unwrapped, circular.wraps_origin) == (7, 13, True)
    assert circular.site_segments == ((7, 8), (0, 5))
    assert circular.matched_reference_sequence == "GAATTC"
    event = circular.double_strand_events[0]
    assert (event.top_boundary_unwrapped, event.bottom_boundary_unwrapped) == (8, 12)
    assert (event.top_boundary, event.bottom_boundary) == (0, 4)

    type_iis = _occ(_analyze("GGTCTCAA", ["BsaI"], topology="circular"), "BsaI")[0]
    cut = type_iis.double_strand_events[0]
    assert (cut.top_boundary_unwrapped, cut.bottom_boundary_unwrapped) == (7, 11)
    assert (cut.top_boundary, cut.bottom_boundary) == (7, 3)
    assert cut.overhang_sequence_5to3 == "AGGT"

    reverse_wrap = _occ(_analyze("ACCAGAG", ["BsaI"], topology="circular"), "BsaI")[0]
    reverse_cut = reverse_wrap.double_strand_events[0]
    assert reverse_wrap.orientation == "reverse"
    assert reverse_cut.overhang_sequence_5to3 == "GACC"
    assert reverse_cut.overhang_source_strand == reverse_cut.protruding_strand == "top"


def test_linear_off_end_site_is_retained_with_typed_geometry_limitation() -> None:
    occurrence = _occ(_analyze("GGTCTC", ["BsaI"]), "BsaI")[0]
    assert occurrence.double_strand_events[0].status == "geometry_out_of_bounds"
    assert occurrence.limitations == ("geometry_out_of_bounds",)
    assert _analyze("GGTCTC", ["BsaI"]).counts.double_strand_break_count == 0


def test_same_site_records_stay_distinct_and_duplicate_geometry_has_group_identity() -> None:
    result = _analyze("GATC", ["DpnI", "MboI"])
    assert {o.enzyme_id for o in result.occurrences} == {"DpnI", "MboI"}
    assert all(o.activity_assessment == "not_evaluated" for o in result.occurrences)
    assert all(o.methylation_context == "unknown" for o in result.occurrences)

    duplicate = _analyze("TTATAA", ["AanI", "PsiI"])
    events = [event for row in duplicate.occurrences for event in row.double_strand_events]
    assert len(events) == 2
    assert events[0].contributor_group_id == events[1].contributor_group_id
    assert len(duplicate.grouped_cleavages) == 1
    group = duplicate.grouped_cleavages[0]
    assert group.contributing_enzyme_ids == ("AanI", "PsiI")
    assert [(ref.enzyme_id, ref.event_ordinal) for ref in group.contributors] == [
        ("AanI", 0), ("PsiI", 0)
    ]
    for occurrence in duplicate.occurrences:
        event = occurrence.double_strand_events[0]
        assert event.enzyme_id == occurrence.enzyme_id
        assert event.occurrence_id == occurrence.occurrence_id
        assert event.orientation == occurrence.orientation
        assert event.activity_assessment == "not_evaluated"


def test_bcgi_has_two_deterministically_ordered_dsb_events() -> None:
    occurrence = _occ(_analyze("A" * 12 + "CGAAAAAAATGC" + "A" * 30, ["BcgI"]), "BcgI")[0]
    assert [(e.event_ordinal, e.top_boundary_unwrapped, e.bottom_boundary_unwrapped) for e in occurrence.double_strand_events] == [
        (0, 2, 0), (1, 36, 34)
    ]


def test_unknown_geometry_is_discoverable_without_fabricated_cut() -> None:
    occurrence = _occ(_analyze("GCAAAC", ["Aba13301I"]), "Aba13301I")[0]
    assert occurrence.double_strand_events == ()
    assert occurrence.nicks == ()
    assert occurrence.limitations == ("enzyme_geometry_unavailable",)


@pytest.mark.parametrize(
    "enzyme,site,forward_strand,forward_boundary,reverse_strand,reverse_boundary",
    [
        ("Nt.BbvCI", "CCTCAGC", "top", 2, "bottom", 5),
        ("Nb.BbvCI", "CCTCAGC", "bottom", 5, "top", 2),
        ("Nt.BspQI", "GCTCTTC", "top", 8, "bottom", -1),
        ("Nb.BssSI", "CACGAG", "bottom", 5, "top", 1),
    ],
)
def test_all_curated_nickases_forward_and_reverse(
    enzyme, site, forward_strand, forward_boundary, reverse_strand, reverse_boundary
) -> None:
    from services.restriction_analysis import reverse_complement

    forward = _occ(_analyze("AA" + site + "AA", [enzyme]), enzyme)[0]
    reverse = _occ(_analyze("AA" + reverse_complement(site) + "AA", [enzyme]), enzyme)[0]
    assert [(n.strand, n.boundary_unwrapped) for n in forward.nicks] == [
        (forward_strand, 2 + forward_boundary)
    ]
    assert [(n.strand, n.boundary_unwrapped) for n in reverse.nicks] == [
        (reverse_strand, 2 + reverse_boundary)
    ]
    assert forward.double_strand_events == reverse.double_strand_events == ()


def test_motif_longer_than_molecule_is_not_scanned_and_has_grouped_typed_limitation() -> None:
    view = catalog_authority.require()
    duplicate = view.by_id["EcoRI"].model_copy(update={
        "enzyme_id": "EcoRI-duplicate", "canonical_name": "EcoRI duplicate",
    })
    result = analyze_sequence(
        sequence="GAA", topology="circular", catalog=view,
        records=(view.by_id["EcoRI"], duplicate),
    )
    assert not result.occurrences
    assert [item.code for item in result.limitations] == ["recognition_motif_longer_than_molecule"]
    limitation = result.limitations[0]
    assert limitation.motif == "GAATTC"
    assert limitation.molecule_length_bp == 3
    assert limitation.motif_length_bp == 6
    assert limitation.enzyme_ids == ("EcoRI", "EcoRI-duplicate")
    assert all(
        summary.limitations == ("recognition_motif_longer_than_molecule",)
        for summary in result.enzyme_summaries
    )

    origin = _analyze("AATTCCCG", ["EcoRI"], topology="circular")
    assert len(origin.occurrences) == 1
    assert not origin.limitations


def test_invalid_dna_and_output_bound_fail_closed() -> None:
    for invalid in ("GA ATTC", "GA-ATTC", "", "GZATTC", "GAUUUC"):
        with pytest.raises(InvalidDNAError):
            _analyze(invalid, ["EcoRI"])
    with pytest.raises(InvalidDNAError):
        normalize_dna("augu")
    with pytest.raises(AnalysisLimitError):
        _analyze("GATC" * 25_001, ["DpnI"])


def test_analysis_is_deterministic_and_cache_separates_authorities() -> None:
    first = _analyze("GAATTC", ["EcoRI"])
    second = _analyze("GAATTC", ["EcoRI"])
    other = _analyze("GAATTCAA", ["EcoRI"])
    assert first is second
    assert first.result_sha256 == second.result_sha256
    assert first.result_sha256 != other.result_sha256
    assert first.result_sha256 == hashlib.sha256(first.canonical_result_bytes()).hexdigest()


def test_enzyme_summaries_are_complete_ordered_and_separate_counts() -> None:
    result = _analyze("GATCGAATTC", ["MboI", "EcoRI"])
    assert [summary.enzyme_id for summary in result.enzyme_summaries] == ["EcoRI", "MboI"]
    assert result.enzyme_summaries[0].model_dump() == {
        "enzyme_id": "EcoRI", "canonical_name": "EcoRI",
        "analysis_capability": "digest_simulation", "cleavage_status": "known_double_strand",
        "recognition_site_count_definite": 1, "recognition_site_count_possible": 0,
        "double_strand_break_count": 1, "nick_count": 0, "limitations": (),
    }


def test_resource_admission_rejects_before_scan_or_excess_models(monkeypatch) -> None:
    import services.restriction_analysis as module

    view = catalog_authority.require()
    scanned = 0
    original_scan = module._scan

    def counted_scan(*args, **kwargs):
        nonlocal scanned
        scanned += 1
        return original_scan(*args, **kwargs)

    monkeypatch.setattr(module, "_scan", counted_scan)
    with pytest.raises(AnalysisLimitError, match="scan work"):
        analyze_sequence(
            sequence="A" * (module.MAX_SCAN_WORK // 292 + 1), topology="linear",
            catalog=view, records=view.by_capability["digest_simulation"],
        )
    assert scanned == 0

    monkeypatch.setattr(module, "MAX_RETURNED_OCCURRENCES", 2)
    constructed = 0
    original_occurrence = module.AnalysisOccurrence

    class CountedOccurrence(original_occurrence):
        def __init__(self, **data):
            nonlocal constructed
            constructed += 1
            super().__init__(**data)

    monkeypatch.setattr(module, "AnalysisOccurrence", CountedOccurrence)
    with pytest.raises(AnalysisLimitError, match="occurrences"):
        _analyze("GATCGATCGATC", ["DpnI"])
    assert constructed == 0

    monkeypatch.setattr(module, "MAX_RETURNED_OCCURRENCES", 25_000)
    monkeypatch.setattr(module, "MAX_RETURNED_EVENTS", 1)
    event_models = 0
    original_event = module.DoubleStrandEvent

    class CountedEvent(original_event):
        def __init__(self, **data):
            nonlocal event_models
            event_models += 1
            super().__init__(**data)

    monkeypatch.setattr(module, "DoubleStrandEvent", CountedEvent)
    with pytest.raises(AnalysisLimitError, match="events"):
        _analyze("A" * 12 + "CGAAAAAAATGC" + "A" * 30, ["BcgI"])
    assert event_models == 0


def test_every_geometry_ready_enzyme_matches_independent_biopython_cut_oracle() -> None:
    from Bio.Restriction import AllEnzymes

    view = catalog_authority.require()
    oracle = {str(enzyme): enzyme for enzyme in AllEnzymes}
    records = view.by_capability["digest_simulation"]
    assert len(records) == 754
    choices = {
        "A": "A", "C": "C", "G": "G", "T": "T", "R": "AG", "Y": "CT",
        "S": "CG", "W": "AT", "K": "GT", "M": "AC", "B": "CGT",
        "D": "AGT", "H": "ACT", "V": "ACG", "N": "ACGT",
    }
    complement = str.maketrans("ACGTRYSWKMBDHVN", "TGCAYRSWMKVHDBN")

    def independent_rc(value: str) -> str:
        return value.translate(complement)[::-1]

    def exact_matches(sequence: str, pattern: str) -> list[int]:
        allowed = {
            "A": "A", "C": "C", "G": "G", "T": "T", "R": "AG", "Y": "CT",
            "S": "CG", "W": "AT", "K": "GT", "M": "AC", "B": "CGT",
            "D": "AGT", "H": "ACT", "V": "ACG", "N": "ACGT",
        }
        return [
            start for start in range(len(sequence) - len(pattern) + 1)
            if all(base in allowed[symbol] for base, symbol in zip(
                sequence[start:start + len(pattern)], pattern, strict=True
            ))
        ]

    def isolated_fixture(enzyme_id: str, motif: str, orientation: str) -> tuple[str, int]:
        reverse_pattern = independent_rc(motif)
        seed = int(hashlib.sha256(f"{enzyme_id}:{orientation}".encode()).hexdigest(), 16)
        for attempt in range(260):
            concrete_state = seed + attempt
            concrete_bases = []
            for symbol in motif:
                concrete_state = (2862933555777941757 * concrete_state + 3037000493) & ((1 << 64) - 1)
                options = choices[symbol]
                concrete_bases.append(options[concrete_state % len(options)])
            concrete = "".join(concrete_bases)
            if orientation == "reverse":
                concrete = independent_rc(concrete)
            opposite = reverse_pattern if orientation == "forward" else motif
            if motif != reverse_pattern and exact_matches(concrete, opposite):
                continue
            if attempt < 4:
                flank = ["ACGT"[attempt]] * 100
            else:
                state = seed + attempt - 4
                flank = []
                for _ in range(100):
                    state = (6364136223846793005 * state + 1442695040888963407) & ((1 << 64) - 1)
                    flank.append("ACGT"[(state >> 32) & 3])
            sequence = "".join(flank) + concrete + "".join(reversed(flank))
            forward_starts = exact_matches(sequence, motif)
            reverse_starts = [] if motif == reverse_pattern else exact_matches(sequence, reverse_pattern)
            expected = [100]
            if orientation == "forward" and forward_starts == expected and not reverse_starts:
                return sequence, 100
            if orientation == "reverse" and reverse_starts == expected and not forward_starts:
                return sequence, 100
        raise AssertionError(f"could not isolate {enzyme_id} {orientation} fixture")

    for record in records:
        enzyme = oracle[record.enzyme_id]
        source_pairs = [(enzyme.fst5, enzyme.fst3)]
        if enzyme.scd5 is not None and enzyme.scd3 is not None:
            source_pairs.append((enzyme.scd5, enzyme.scd3))
        lanes = ["forward"] if record.recognition.palindromic else ["forward", "reverse"]
        for lane in lanes:
            sequence, site_start = isolated_fixture(
                record.enzyme_id, str(enzyme.site), lane
            )
            result = analyze_sequence(
                sequence=sequence, topology="linear", catalog=view, records=(record,),
                include_possible_sites=False,
            )
            assert [(occurrence.site_start, occurrence.orientation) for occurrence in result.occurrences] == [
                (site_start, lane)
            ], (record.enzyme_id, lane)
            expected_pairs = []
            for top_source, bottom_source_from_end in source_pairs:
                if lane == "forward":
                    expected_pairs.append((
                        site_start + top_source,
                        site_start + len(str(enzyme.site)) + bottom_source_from_end,
                    ))
                else:
                    expected_pairs.append((
                        site_start - bottom_source_from_end,
                        site_start + len(str(enzyme.site)) - top_source,
                    ))
            actual_pairs = [
                (event.top_boundary_unwrapped, event.bottom_boundary_unwrapped)
                for occurrence in result.occurrences
                for event in occurrence.double_strand_events
                if event.status == "complete"
            ]
            assert sorted(actual_pairs) == sorted(expected_pairs), (record.enzyme_id, lane)


def test_independent_circular_winding_and_target_derived_overhang_oracle() -> None:
    from Bio.Restriction import BsaI

    sequence = "GGTCTCAA"
    top_unwrapped = BsaI.fst5
    bottom_unwrapped = len(str(BsaI.site)) + BsaI.fst3
    occurrence = _occ(_analyze(sequence, ["BsaI"], topology="circular"), "BsaI")[0]
    event = occurrence.double_strand_events[0]
    assert (
        event.top_boundary_unwrapped, event.bottom_boundary_unwrapped,
        event.top_boundary, event.bottom_boundary,
        event.top_winding, event.bottom_winding,
    ) == (top_unwrapped, bottom_unwrapped, top_unwrapped % len(sequence),
          bottom_unwrapped % len(sequence), top_unwrapped // len(sequence),
          bottom_unwrapped // len(sequence))
    interval = "".join(sequence[index % len(sequence)] for index in range(top_unwrapped, bottom_unwrapped))
    assert event.overhang_sequence_5to3 == interval
