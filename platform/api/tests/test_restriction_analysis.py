from __future__ import annotations

import hashlib

import pytest

from services.restriction_catalog import catalog_authority
from services.restriction_analysis import (
    AnalysisLimitError,
    InvalidDNAError,
    analyze_sequence,
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


def test_motif_longer_than_molecule_is_not_scanned_even_for_circular() -> None:
    assert not _analyze("GAA", ["EcoRI"], topology="circular").occurrences


def test_invalid_dna_and_output_bound_fail_closed() -> None:
    for invalid in ("GA ATTC", "GA-ATTC", "", "GZATTC"):
        with pytest.raises(InvalidDNAError):
            _analyze(invalid, ["EcoRI"])
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


def test_every_geometry_ready_enzyme_matches_independent_biopython_cut_oracle() -> None:
    from Bio.Restriction import AllEnzymes
    from Bio.Seq import Seq

    view = catalog_authority.require()
    oracle = {str(enzyme): enzyme for enzyme in AllEnzymes}
    records = view.by_capability["digest_simulation"]
    assert len(records) == 754
    for record in records:
        concrete = "".join({
            "A": "A", "C": "C", "G": "G", "T": "T", "R": "A", "Y": "C",
            "S": "C", "W": "A", "K": "G", "M": "A", "B": "C", "D": "A",
            "H": "A", "V": "A", "N": "A",
        }[base] for base in record.recognition.site_iupac)
        filler = next(
            base for base in "ACGT"
            if not analyze_sequence(
                sequence=base * 220, topology="linear", catalog=view, records=(record,),
                include_possible_sites=False,
            ).occurrences
        )
        sequence = filler * 100 + concrete + filler * 100
        result = analyze_sequence(
            sequence=sequence, topology="linear", catalog=view, records=(record,),
            include_possible_sites=False,
        )
        actual_top = sorted(
            event.top_boundary_unwrapped
            for occurrence in result.occurrences
            for event in occurrence.double_strand_events
            if event.status == "complete"
        )
        actual_bottom = sorted(
            event.bottom_boundary_unwrapped
            for occurrence in result.occurrences
            for event in occurrence.double_strand_events
            if event.status == "complete"
        )
        enzyme = oracle[record.enzyme_id]
        expected_top = sorted(position - 1 for position in enzyme.search(Seq(sequence), linear=True))
        expected_pairs = []
        source_pairs = [(enzyme.fst5, enzyme.fst3)]
        if enzyme.scd5 is not None and enzyme.scd3 is not None:
            source_pairs.append((enzyme.scd5, enzyme.scd3))
        for occurrence in result.occurrences:
            for top_source, bottom_source_from_end in source_pairs:
                if occurrence.orientation == "forward":
                    expected_pairs.append((
                        occurrence.site_start + top_source,
                        occurrence.site_start + record.recognition.length_bp + bottom_source_from_end,
                    ))
                else:
                    expected_pairs.append((
                        occurrence.site_start - bottom_source_from_end,
                        occurrence.site_start + record.recognition.length_bp - top_source,
                    ))
        actual_pairs = [
            (event.top_boundary_unwrapped, event.bottom_boundary_unwrapped)
            for occurrence in result.occurrences
            for event in occurrence.double_strand_events
            if event.status == "complete"
        ]
        assert sorted(set(actual_top)) == sorted(set(expected_top)), record.enzyme_id
        assert sorted(actual_pairs) == sorted(expected_pairs), record.enzyme_id
