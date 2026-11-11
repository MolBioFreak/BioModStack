"""Supplied Golden Gate parts: bare DNA, pre-configured Type IIS fragments, mixtures, ordering and a 10 kb split."""
from __future__ import annotations

import random

import pytest

from services.assembly.common import reverse_complement
from services.assembly.golden_gate_suggest import SuggestRequest, suggest_golden_gate

SITES = ("GGTCTC", "GAGACC")


def dna(n: int, seed: int) -> str:
    rng = random.Random(seed)
    while True:
        text = "".join(rng.choice("ACGT") for _ in range(n))
        if not any(site in text for site in SITES):
            return text


def inline(name: str, sequence: str, topology: str = "linear") -> dict:
    return dict(id=name, source=dict(kind="inline", sequence=sequence, topology=topology))


def prepared(payload: str, left: str, right: str, clamp: str = "TT") -> str:
    return clamp + "GGTCTC" + "A" + left + payload + right + "T" + "GAGACC" + clamp


def suggest(**fields):
    return suggest_golden_gate(SuggestRequest.model_validate(dict(enzymes=["BsaI"], **fields)))


A, B, C = dna(300, 1), dna(400, 2), dna(350, 3)
PRE_A, PRE_B, PRE_C = prepared(A, "AATG", "GCTT"), prepared(B, "GCTT", "CGAA"), prepared(C, "CGAA", "AATG")


def part(name: str, sequence: str, **extra) -> dict:
    return dict(source=inline(name, sequence), name=name, **extra)


def rotations(text: str) -> set[str]:
    return {text[i:] + text[:i] for i in range(len(text))}


def test_prepared_parts_are_used_exactly_as_supplied():
    result = suggest(circular=True, parts=[part("A", PRE_A), part("B", PRE_B), part("C", PRE_C)])
    assert [r.kind for r in result.part_reports] == ["prepared"] * 3
    assert [(r.left_overhang, r.right_overhang) for r in result.part_reports] == [("AATG", "GCTT"), ("GCTT", "CGAA"), ("CGAA", "AATG")]
    proposal = result.proposals[0]
    assert proposal.final_sequence == "AATG" + A + "GCTT" + B + "CGAA" + C
    assert proposal.junctions == ["AATG", "GCTT", "CGAA"]
    assert proposal.junction_mode == "imposed"
    assert proposal.part_order == [0, 1, 2]
    assert proposal.order_summary["prepared_parts"] == 3 and proposal.order_summary["bare_parts"] == 0
    assert [row["part_kind"] for row in proposal.prepared_fragments] == ["prepared"] * 3
    assert all(row["order_sequence"] is None for row in proposal.prepared_fragments)


def test_given_order_that_does_not_chain_names_the_mismatch():
    result = suggest(circular=True, parts=[part("A", PRE_A), part("C", PRE_C), part("B", PRE_B)])
    assert not result.proposals
    status = result.enzymes[0].status
    assert "A ends with GCTT but C starts with CGAA" in status


def test_auto_order_arranges_prepared_parts_by_overhangs():
    result = suggest(circular=True, order="auto", parts=[part("C", PRE_C), part("B", PRE_B), part("A", PRE_A)])
    proposal = result.proposals[0]
    assert proposal.part_order == [0, 2, 1]                      # C stays first, then A, then B
    assert proposal.final_sequence in rotations("AATG" + A + "GCTT" + B + "CGAA" + C)
    assert proposal.final_sequence == "CGAA" + C + "AATG" + A + "GCTT" + B


def test_auto_order_flips_a_part_supplied_in_reverse():
    flipped = reverse_complement(PRE_C)
    result = suggest(circular=True, order="auto", parts=[part("A", PRE_A), part("B", PRE_B), part("C", flipped)])
    proposal = result.proposals[0]
    assert proposal.final_sequence == "AATG" + A + "GCTT" + B + "CGAA" + C
    assert any("C was used as its reverse complement" in note for note in proposal.notes)


def test_auto_order_names_the_part_that_cannot_chain():
    stray = prepared(dna(200, 9), "TTTT", "CCCC")
    result = suggest(circular=True, order="auto", parts=[part("A", PRE_A), part("B", PRE_B), part("stray", stray)])
    assert not result.proposals
    assert "do not form a chain" in result.enzymes[0].status


def test_bare_and_prepared_mix_keeps_prepared_overhangs_and_chooses_the_rest():
    result = suggest(circular=True, parts=[part("A", PRE_A), part("b", B), part("c", C)])
    assert [r.kind for r in result.part_reports] == ["prepared", "bare", "bare"]
    proposal = result.proposals[0]
    assert proposal.junctions[:2] == ["AATG", "GCTT"]
    assert [s["origin"] for s in proposal.junction_slots][:2] == ["prepared", "prepared"]
    assert proposal.junction_slots[2]["origin"] == "optimizer"
    assert proposal.order_summary["bare_parts"] == 2 and proposal.order_summary["prepared_parts"] == 1
    rows = {row["name"]: row for row in proposal.prepared_fragments}
    assert rows["A"]["part_kind"] == "prepared" and rows["A"]["order_sequence"] is None
    assert rows["b"]["part_kind"] == "bare" and rows["b"]["tailed_length"] > len(B)
    assert proposal.order_summary["order_total_bp"] == rows["b"]["tailed_length"] + rows["c"]["tailed_length"]
    assert proposal.final_sequence.startswith("AATG" + A + "GCTT")
    assert B in proposal.final_sequence and C in proposal.final_sequence


def test_bare_parts_report_no_site_and_get_overhangs_added():
    result = suggest(circular=True, parts=[part("a", A), part("b", B)])
    assert [r.kind for r in result.part_reports] == ["bare", "bare"]
    assert all(r.site_count == 0 for r in result.part_reports)
    assert all(row["part_kind"] == "bare" and row["order_sequence"] for row in result.proposals[0].prepared_fragments)


def test_explicit_bare_on_a_site_carrying_part_is_reported_not_blocked():
    result = suggest(circular=True, parts=[part("A", PRE_A, kind="bare"), part("b", B)])
    report = result.part_reports[0]
    assert report.kind == "bare" and report.site_count == 2
    assert "already inside it" in report.message


def test_forcing_prepared_on_bare_dna_is_invalid_with_a_reason():
    result = suggest(circular=True, parts=[part("a", A, kind="prepared"), part("b", B)])
    assert not result.proposals
    assert result.part_reports[0].kind == "invalid"
    assert "No BsaI site" in result.part_reports[0].message


def test_overhang_given_against_the_part_is_rejected_with_both_values():
    result = suggest(circular=True, parts=[part("A", PRE_A, left_overhang="TTTT"), part("b", B)])
    assert not result.proposals
    assert "given as TTTT but the part carries AATG" in result.part_reports[0].message


def test_region_on_a_prepared_part_is_rejected():
    region = dict(start=0, end=100)
    result = suggest(circular=True, parts=[part("A", PRE_A, region=region), part("b", B)])
    assert not result.proposals
    assert "used whole" in result.part_reports[0].message


def test_part_with_several_candidate_fragments_asks_which_to_keep():
    second = prepared(dna(120, 5), "AAAA", "CCCC")
    both = PRE_A + second
    result = suggest(circular=True, parts=[part("two", both), part("b", B)])
    report = result.part_reports[0]
    assert report.kind == "ambiguous" and len(report.candidates) >= 2
    assert not result.proposals
    keep = next(c for c in report.candidates if c.length == len("AATG") + len(A))
    again = suggest(circular=True, parts=[part("two", both, retained_fragment_index=keep.fragment_index), part("b", B)])
    assert again.part_reports[0].kind == "prepared" and again.part_reports[0].payload_length == len(A)
    assert again.proposals


def test_vector_with_prepared_and_bare_parts():
    vector = "TT" + "GGTCTC" + "A" + "GGAG" + "ACGTACGTACGTAC" + "TACT" + "A" + "GAGACC" + "AA" + dna(500, 11)
    result = suggest(vector=inline("v", vector, "circular"),
                     parts=[part("A", prepared(A, "GGAG", "GCTT")), part("b", B), part("c", C)])
    proposal = result.proposals[0]
    assert proposal.junctions[0] == "GGAG" and proposal.junctions[-1] == "TACT"
    assert [s["origin"] for s in proposal.junction_slots] == ["vector", "prepared", "optimizer", "vector"]


def test_ten_kb_plasmid_splits_into_chosen_size_pieces_and_reassembles():
    plasmid = dna(10_000, 21)
    result = suggest(target=inline("plasmid-A", plasmid, "circular"), fragment_count=8,
                     min_fragment_length=900, max_fragment_length=1_600)
    proposal = result.proposals[0]
    assert len(proposal.fragment_lengths) == 8
    assert all(900 <= n <= 1_600 for n in proposal.fragment_lengths)
    assert sum(proposal.fragment_lengths) == 10_000
    assert proposal.final_sequence in rotations(plasmid)
    assert all(row["order_sequence"] for row in proposal.prepared_fragments)
    assert proposal.order_summary["bare_parts"] == 8
