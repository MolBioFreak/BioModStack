"""Repeat-aware breakpoints for Gibson/HiFi: junctions stay out of repeated sequence, copies of a repeat share a fragment when a layout
allows it, and the advice given when it does not is exact. Judged by brute force and by the independent design route, not by the module."""
from __future__ import annotations

import random

import numpy as np
import pytest

from services.assembly.dnaweaver_gibson import plan_vendor_gibson
from services.assembly.gibson_suggest import SuggestRequest, suggest_gibson
from services.assembly.pydna_gibson import design_gibson
from services.assembly.repeat_breakpoints import RepeatAnalysis, choose_boundaries
from services.assembly.types import AssemblyError, AssemblyFragment
from services.molbio_ops import reverse_complement as rc


def dna(seed: int, n: int) -> str:
    rnd = random.Random(seed)
    return "".join(rnd.choice("ACGT") for _ in range(n))


def circ_eq(a: str, b: str) -> bool:
    return len(a) == len(b) and (a in b + b or rc(a) in b + b)


def brute_repeated_bases(seq: str, k: int, circular: bool = True) -> set[int]:
    n = len(seq)
    t = seq + seq[: k - 1] if circular else seq
    seen: dict[str, set[int]] = {}
    for i in range(len(t) - k + 1):
        m = t[i : i + k]
        seen.setdefault(m, set()).add(i)
        seen.setdefault(rc(m), set()).add(i)
    out: set[int] = set()
    for starts in seen.values():
        if len(starts) > 1:
            for s in starts:
                out.update((s + j) % n for j in range(k))
    return out


def with_repeat(total: int, rep_len: int, first: int, second: int, seed: int = 1, flip_second: bool = False) -> tuple[str, str]:
    """A circular sequence of `total` bp holding two copies of one repeat (the second optionally reverse-complemented)."""
    rep = dna(seed + 1000, rep_len)
    body = dna(seed, total - 2 * rep_len)
    second_copy = rc(rep) if flip_second else rep
    seq = body[:first] + rep + body[first : second - rep_len] + second_copy + body[second - rep_len :]
    assert len(seq) == total
    return seq, rep


def covers(core: tuple[int, int], start: int, end: int, n: int) -> bool:
    """Does the circular core [s, e) hold every base of [start, end)?"""
    s, e = core
    inside = (lambda p: s <= p < e) if s < e else (lambda p: p >= s or p < e)
    return all(inside(p % n) for p in range(start, end))


# ---------------------------------------------------------------- analysis
def test_target_without_repeats_is_never_restricted() -> None:
    seq = dna(5, 8000)
    ra = RepeatAnalysis(seq, circular=True, overlap=30, min_anneal=18)
    assert not ra.has_repeats and ra.families == []
    plan = ra.plan(min_len=500, max_len=3000)
    assert plan.valid is None and not plan.restricts


@pytest.mark.parametrize("flip", [False, True])
@pytest.mark.parametrize("shift", [0, 777])
def test_repeated_base_mask_equals_brute_force_including_reverse_complement_and_origin_crossing(flip: bool, shift: int) -> None:
    seq, _ = with_repeat(6000, 250, 900, 4100, seed=3, flip_second=flip)
    seq = seq[shift:] + seq[:shift]  # moves a copy across the origin when shift cuts through it
    ra = RepeatAnalysis(seq, circular=True, overlap=30, min_anneal=18)
    expected = brute_repeated_bases(seq, ra.k_clean)
    assert set(map(int, np.flatnonzero(ra.rep_mask))) == expected
    assert len(expected) >= 2 * 250


def test_two_copies_form_one_family_with_the_shortest_covering_arc() -> None:
    seq, _ = with_repeat(7000, 300, 1000, 4000, seed=7)
    ra = RepeatAnalysis(seq, circular=True, overlap=30, min_anneal=18)
    fam = next(f for f in ra.families if f.longest >= 250)
    assert len(fam.copies) == 2 and fam.arc is not None
    assert fam.arc_len < 7000 // 2 + 300  # the short way round the circle, not the long way


def test_plan_allows_no_cut_near_a_repeated_base_and_none_inside_a_containable_repeat_span() -> None:
    seq, _ = with_repeat(8000, 300, 1200, 4200, seed=9)
    ra = RepeatAnalysis(seq, circular=True, overlap=30, min_anneal=18)
    plan = ra.plan(min_len=500, max_len=3800)
    assert plan.restricts and plan.contained and not plan.uncontained
    rep = brute_repeated_bases(seq, ra.k_clean)
    n = len(seq)
    allowed = [c for c in range(n) if plan.valid[(c - plan.origin) % n]]  # original-frame cut c is plan cut (c - origin) % n
    assert allowed
    for c in allowed:
        assert not any(((c + d) % n) in rep for d in range(-ra.half, ra.half)), c
    fam = plan.contained[0]
    arc = fam.arc
    inside = {c % n for c in range(arc[0] + 1, arc[1])}
    assert not (inside & set(allowed))
    # the witness layout uses allowed cuts only and respects the piece limits
    cuts = [0, *plan.witness, n]
    assert all(plan.valid[c] for c in plan.witness)
    assert all(500 <= b - a <= 3800 for a, b in zip(cuts, cuts[1:]))


def test_repeat_that_cannot_fit_is_reported_and_the_advised_maximum_is_exact() -> None:
    seq, _ = with_repeat(8000, 300, 1200, 5200, seed=11)
    ra = RepeatAnalysis(seq, circular=True, overlap=30, min_anneal=18)
    plan = ra.plan(min_len=500, max_len=3000)
    assert plan.uncontained and plan.needed > 3000 and any("split across fragments" in n for n in plan.notes)
    exact = ra.smallest_max_len_keeping_repeats_whole(min_len=500, start=3001)
    assert exact is not None and exact > 3000
    whole = ra.plan(min_len=500, max_len=exact)
    assert whole.restricts and not whole.uncontained
    assert ra.plan(min_len=500, max_len=exact - 1).uncontained  # one base less: still split


def test_choose_boundaries_respects_allowed_cuts_cap_and_piece_minimum() -> None:
    valid = np.ones(10_001, dtype=bool)
    valid[3000:6000] = False  # a forbidden stretch in the middle
    got = choose_boundaries(valid, 0, 10_000, 3500, min_piece=100)
    assert got is not None
    bounds = [0, *got, 10_000]
    assert all(valid[b] for b in got)
    assert all(100 <= b - a <= 3500 for a, b in zip(bounds, bounds[1:]))
    assert len(bounds) - 1 == 4  # three pieces cannot avoid 3000-6000 (the first cut must be <3000, the second >=6500), so four is the minimum
    from services.assembly.repeat_breakpoints import _boundaries
    assert _boundaries(valid, 0, 10_000, 3500, 3, 100) is None
    assert choose_boundaries(np.ones(10_001, dtype=bool), 0, 10_000, 20_000) == []
    impossible = np.zeros(10_001, dtype=bool)
    assert choose_boundaries(impossible, 0, 10_000, 3500) is None


# ---------------------------------------------------------------- vendor planner (DNA Weaver + pydna)
def test_vendor_plan_keeps_repeat_copies_in_one_fragment_and_junctions_out_of_repeats() -> None:
    seq, rep = with_repeat(6000, 120, 1000, 3600, seed=21)
    plan = plan_vendor_gibson(seq, circular=True, min_fragment_length=500, max_fragment_length=3500, overlap_length=30)
    assert plan.pydna_exact_candidate_count == 1
    n = len(seq)
    cores = [(iv["target_start"], iv["target_end"]) if "target_start" in iv else (iv["start"], iv["end"]) for iv in plan.source_intervals]
    copy_a, copy_b = [i for i in range(n) if seq.startswith(rep, i)][:1] + [i for i in range(n) if seq.startswith(rep, i)][1:2]
    holder_a = [c for c in cores if covers(c, copy_a, copy_a + 120, n)]
    holder_b = [c for c in cores if covers(c, copy_b, copy_b + 120, n)]
    assert holder_a and holder_a == holder_b, "both copies must lie inside one fragment core"
    rep_bases = brute_repeated_bases(seq, 20)
    for fragment in plan.product.fragments:
        overlap = fragment.sequence[-30:]
        start = (seq + seq).index(overlap)
        assert not any(((start + j) % n) in rep_bases for j in range(30)), "junction overlap sits on repeated sequence"
    assert any(c["check_id"] == "repeat_breakpoints" for c in plan.quality_checks)


def test_vendor_plan_for_a_target_without_repeats_has_no_repeat_note() -> None:
    plan = plan_vendor_gibson(dna(31, 4000), circular=True, min_fragment_length=500, max_fragment_length=1500, overlap_length=30)
    assert not any(c["check_id"] == "repeat_breakpoints" for c in plan.quality_checks)
    assert plan.order_ready


def test_vendor_plan_refusal_names_a_maximum_that_then_works() -> None:
    seq, _ = with_repeat(7000, 300, 1000, 4700, seed=41)
    with pytest.raises(AssemblyError) as caught:
        plan_vendor_gibson(seq, circular=True, min_fragment_length=500, max_fragment_length=3000, overlap_length=30)
    text = str(caught.value)
    assert "split across fragments" in text and "maximum fragment length to" in text
    advised = int(text.split("maximum fragment length to ")[1].split(" bp")[0])
    assert advised > 3000
    plan = plan_vendor_gibson(seq, circular=True, min_fragment_length=500, max_fragment_length=advised, overlap_length=30)
    assert plan.pydna_exact_candidate_count == 1


# ---------------------------------------------------------------- Gibson / HiFi suggest from a source plasmid
def suggest_for(seq: str, cap: int):
    return suggest_gibson(SuggestRequest.model_validate({
        "target": {"name": "t", "sequence": seq, "circular": True},
        "sources": [{"id": "self", "name": "plasmid", "sequence": seq, "circular": True}],
        "max_pcr_length": cap, "limit": 3}))


def test_suggest_splits_a_repeat_plasmid_so_the_design_route_assembles_it() -> None:
    seq, _ = with_repeat(7000, 200, 800, 3300, seed=51)
    result = suggest_for(seq, 3500)
    assert result.proposals, result.diagnostics
    proposal = result.proposals[0]
    fragments = [AssemblyFragment(id=f["id"], name=f["name"], sequence=f["sequence"], orientation=f["orientation"]) for f in proposal.design_request["fragments"]]
    design = design_gibson(fragments, preparations=[f["preparation"] for f in proposal.design_request["fragments"]], circular=True,
                           overlap=proposal.design_request["overlap"], target_tm=proposal.design_request["target_tm"], min_anneal=proposal.design_request["min_anneal"])
    assert circ_eq(design.candidates[0].product.sequence.upper(), seq.upper())
    assert any("repeat" in note for note in proposal.notes)


def test_suggest_when_the_cap_is_too_small_names_a_cap_that_then_works() -> None:
    seq, _ = with_repeat(8000, 300, 1200, 5200, seed=61)
    refused = suggest_for(seq, 3000)
    assert not refused.proposals
    text = " ".join(refused.diagnostics)
    assert "Raise the PCR length limit to" in text
    advised = int(text.split("Raise the PCR length limit to ")[1].split(" bp")[0])
    assert suggest_for(seq, advised).proposals
    assert not suggest_for(seq, advised - 1).proposals
