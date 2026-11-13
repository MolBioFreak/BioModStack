"""NEB low-efficiency overhangs: AAAA, CCCC, TAAA and their reverse complements (3 nt analogues for 3 nt enzymes).

BMS avoids them only in junctions it chooses. Authored cuts, supplied overhangs and pre-configured parts are never
moved or refused; a listed overhang that stays is reported in the proposal notes. The preference never refuses a design.
"""
from __future__ import annotations

import itertools
import random

import pytest

from services.assembly.golden_gate_suggest import SuggestRequest, suggest_golden_gate

BAD4 = {"AAAA", "TTTT", "CCCC", "GGGG", "TAAA", "TTTA"}
BAD3 = {"AAA", "TTT", "CCC", "GGG", "TAA", "TTA"}
SITES = ("CACCTGC", "GCAGGTG", "GGTCTC", "GAGACC", "GCTCTTC", "GAAGAGC")


def dna(n: int, seed: int, plant: dict[int, str] | None = None) -> str:
    rng = random.Random(seed)
    while True:
        text = [rng.choice("ACGT") for _ in range(n)]
        for position, word in (plant or {}).items():
            text[position:position + len(word)] = word
        text = "".join(text)
        if not any(site in text for site in SITES):
            return text


def inline(name: str, sequence: str, topology: str = "linear") -> dict:
    return dict(id=name, source=dict(kind="inline", sequence=sequence, topology=topology))


def suggest(**fields):
    return suggest_golden_gate(SuggestRequest.model_validate(fields))


def flagged(proposal) -> list[str]:
    return [note for note in proposal.notes if "Low-efficiency overhang" in note]


# Three cut windows (fragment_count=3 on 1200 bp: starts 0, 300, 700). Listed 4-mers sit at the first positions of each
# window, which is where the unranked search looks first, so the default choice is a listed overhang unless it is avoided.
BAIT = {0: "AAAAAAA", 300: "TAAAAAA", 700: "CCCCCCC", 790: "GGGG"}
TARGET = dna(1200, 5, BAIT)


def test_bait_target_really_offers_listed_overhangs_in_every_cut_window():
    from services.assembly.golden_gate_fidelity import sequence_window_domains
    from services.assembly.golden_gate_suggest import _windows
    request = SuggestRequest.model_validate(dict(target=inline("t", TARGET, "circular"), enzymes=["PaqCI"], fragment_count=3))
    windows, _ = _windows(len(TARGET), True, 4, request, [])
    domains = sequence_window_domains(TARGET, [(w.start, w.end) for w in windows], end_length=4, topology="circular")
    assert len(domains) == 3
    for domain in domains:
        words = {entry["sequence"] for entry in domain}
        assert words & BAD4 and words - BAD4


@pytest.mark.parametrize("avoid_repeats", [True, False])
def test_single_sequence_split_does_not_choose_listed_overhangs(avoid_repeats):
    result = suggest(target=inline("t", TARGET, "circular"), enzymes=["PaqCI"], fragment_count=3,
                     min_fragment_length=10, avoid_repeats=avoid_repeats)
    assert result.proposals
    for proposal in result.proposals:
        assert len(proposal.junctions) == 3
        assert not set(proposal.junctions) & BAD4, proposal.junctions
        assert not flagged(proposal)
        assert proposal.candidate.design.solutions[0].exact_target_match is True
        assert proposal.split_request["excluded"] == []          # a preference, never a hard exclusion


def test_authored_boundary_on_a_listed_overhang_is_kept_and_flagged():
    target = dna(1200, 9, {0: "AAAAAAA", 400: "TAAA", 700: "CCCCCCC"})
    result = suggest(target=inline("t", target, "circular"), enzymes=["PaqCI"], fragment_count=3,
                     min_fragment_length=10, fixed_boundaries=[400], avoid_repeats=False)
    assert result.proposals
    for proposal in result.proposals:
        assert 400 in proposal.cuts and "TAAA" in proposal.junctions
        notes = flagged(proposal)
        assert len(notes) == 1 and "TAAA" in notes[0] and "fixed boundary" in notes[0]
        assert not (set(proposal.junctions) - {"TAAA"}) & BAD4
        assert proposal.candidate.design.solutions[0].exact_target_match is True


def test_terminal_overhang_you_supply_is_kept_and_flagged():
    target = dna(900, 3, {0: "AAAAAAA", 300: "CCCCCCC"})
    result = suggest(target=inline("t", target, "linear"), enzymes=["PaqCI"], fragment_count=3,
                     min_fragment_length=10, terminal_right_fusion="TTTA", avoid_repeats=False)
    assert result.proposals
    for proposal in result.proposals:
        assert "TTTA" in proposal.junctions
        notes = flagged(proposal)
        assert len(notes) == 1 and "TTTA" in notes[0] and "terminal" in notes[0]
        assert not (set(proposal.junctions) - {"TTTA"}) & BAD4


def test_window_with_no_alternative_still_yields_a_proposal_and_says_so():
    target = dna(300, 4, {110: "A" * 80})          # the second cut window lies wholly inside the A tract
    result = suggest(target=inline("t", target, "circular"), enzymes=["PaqCI"], fragment_count=2,
                     min_fragment_length=10, avoid_repeats=False)
    assert result.proposals
    proposal = result.proposals[0]
    assert "AAAA" in proposal.junctions
    notes = flagged(proposal)
    assert len(notes) == 1 and "AAAA" in notes[0] and "no other choice" in notes[0]
    assert not (set(proposal.junctions) - {"AAAA"}) & BAD4
    assert proposal.candidate.design.solutions[0].exact_target_match is True


def bare_parts(count: int = 3, length: int = 300):
    return [dict(source=inline(f"p{i}", dna(length + 40 * i, 20 + i)), name=f"p{i}") for i in range(count)]


def test_parts_mode_does_not_choose_listed_overhangs():
    result = suggest(enzymes=["PaqCI"], circular=True, parts=bare_parts())
    assert result.proposals
    for proposal in result.proposals:
        assert proposal.junction_mode == "chosen" and len(proposal.junctions) == 3
        assert not set(proposal.junctions) & BAD4, proposal.junctions
        assert not flagged(proposal)


def test_standard_overhang_you_impose_is_kept_and_flagged_while_free_junctions_avoid_the_list():
    result = suggest(enzymes=["PaqCI"], circular=True, parts=bare_parts(), standard_overhangs=["AAAA"])
    assert result.proposals
    for proposal in result.proposals:
        assert proposal.junctions[0] == "AAAA"
        notes = flagged(proposal)
        assert len(notes) == 1 and "AAAA" in notes[0] and "standard" in notes[0]
        assert not set(proposal.junctions[1:]) & BAD4


def prepared(payload: str, left: str, right: str, clamp: str = "TT") -> str:
    return clamp + "GGTCTC" + "A" + left + payload + right + "T" + "GAGACC" + clamp


def test_preconfigured_part_with_a_listed_overhang_is_used_as_supplied_and_flagged():
    a, b, c = dna(300, 1), dna(400, 2), dna(350, 3)
    parts = [dict(source=inline("A", prepared(a, "TAAA", "GCTT")), name="A"),
             dict(source=inline("B", prepared(b, "GCTT", "CGAA")), name="B"),
             dict(source=inline("C", prepared(c, "CGAA", "TAAA")), name="C")]
    result = suggest(enzymes=["BsaI"], circular=True, parts=parts)
    proposal = result.proposals[0]
    assert proposal.junctions == ["TAAA", "GCTT", "CGAA"]
    assert proposal.final_sequence == "TAAA" + a + "GCTT" + b + "CGAA" + c
    notes = flagged(proposal)
    assert len(notes) == 1 and "TAAA" in notes[0] and "pre-configured" in notes[0]


def test_authored_part_overhang_is_kept_and_flagged_while_other_junctions_are_chosen():
    parts = bare_parts()
    parts[0]["left_overhang"] = "CCCC"
    result = suggest(enzymes=["PaqCI"], circular=True, parts=parts)
    assert result.proposals
    for proposal in result.proposals:
        assert proposal.junctions[0] == "CCCC" and proposal.junction_slots[0]["origin"] == "user"
        notes = flagged(proposal)
        assert len(notes) == 1 and "CCCC" in notes[0] and "supplied" in notes[0]
        assert not set(proposal.junctions[1:]) & BAD4


def test_preference_never_refuses_when_the_list_cannot_be_avoided():
    # 30 junctions of a 3 nt enzyme need 30 of the 32 reverse-complement classes; only 29 remain once the list is avoided
    result = suggest(enzymes=["SapI"], circular=True, parts=bare_parts(30, 60))
    assert result.proposals
    proposal = result.proposals[0]
    listed = [j for j in proposal.junctions if j in BAD3]
    assert len(proposal.junctions) == 30 and len(set(proposal.junctions)) == 30 and listed
    notes = flagged(proposal)
    assert len(notes) == len(listed)
    assert all("no other choice" in note for note in notes)


def test_rule_module_matches_neb_list_for_three_and_four_nt():
    from services.assembly.golden_gate_neb_overhangs import is_low_efficiency, listed_overhangs
    four = {"".join(x) for x in itertools.product("ACGT", repeat=4)}
    three = {"".join(x) for x in itertools.product("ACGT", repeat=3)}
    assert {x for x in four if is_low_efficiency(x)} == BAD4
    assert {x for x in three if is_low_efficiency(x)} == BAD3
    assert set(listed_overhangs(4)) == BAD4 and set(listed_overhangs(3)) == BAD3
    assert listed_overhangs(5) == [] and listed_overhangs(2) == []
    assert not is_low_efficiency("AAAAA") and not is_low_efficiency("AC") and not is_low_efficiency("")
    assert is_low_efficiency("taaa")


def test_optimizer_preference_yields_to_fixed_overhangs_and_to_scarcity():
    from services.assembly import golden_gate_fidelity as fidelity
    domain = ["".join(x) for x in itertools.product("ACGT", repeat=3)]
    settings = fidelity.SearchSettings(ranking_mode="lexicographic")
    avoid = sorted(BAD3)
    plain = fidelity.optimize_overhangs(domain, 3, None, end_length=3, avoided=avoid, settings=settings)
    assert not set(plain["solutions"][0]["junctions"]) & BAD3
    # a fixed overhang on the list is kept (it is not a conflict) and its class is not picked again
    kept = fidelity.optimize_overhangs(domain, 3, None, end_length=3, fixed=["TAA"], avoided=avoid, settings=settings)
    assert kept["solutions"][0]["junctions"][0] == "TAA" and not set(kept["solutions"][0]["junctions"][1:]) & BAD3
    # 3 nt overhangs have 32 reverse-complement classes, 3 of them listed: 31 junctions must use 2 listed ones.
    # A hard exclusion refuses that request, the preference does not.
    scarce = fidelity.optimize_overhangs(domain, 31, None, end_length=3, avoided=avoid, settings=settings)
    assert scarce["solutions"] and len(set(scarce["solutions"][0]["junctions"])) == 31
    assert len(set(scarce["solutions"][0]["junctions"]) & BAD3) == 2
    hard = fidelity.optimize_overhangs(domain, 31, None, end_length=3, excluded=avoid, settings=settings)
    assert not hard["solutions"]
