"""Displayed lengths of restriction-cloning proposals must add up, judged by an oracle that shares no code with the assembler.

Oracle: Biopython cut positions. A cut is (top index t, bottom index b = t - ovhg). The fragment between consecutive cuts has top strand
ref[Lt:Rt] and bottom strand revcomp(ref[Lb:Rb]). A forward insert contributes ref[Lt:Rt]; a reversed insert is the rotated duplex and
contributes revcomp(ref[Lb:Rb]) as its top strand. Product = vector top strand + that contribution.
"""
from __future__ import annotations

import random

import pytest
import Bio.Restriction as BR
from Bio.Seq import Seq

from services.assembly.restriction_cloning import suggest_restriction_cloning
from services.molbio_ops import reverse_complement as rc

PANEL = ["EcoRI", "BamHI", "HindIII", "XbaI", "NdeI", "SalI", "XhoI", "NcoI", "SphI", "SacI", "KpnI", "PstI", "SpeI", "NheI", "BglII", "ClaI"]


def _cuts(seq, names, circular):
    out = set()
    for n in set(names):
        e = getattr(BR, n)
        for pos in e.search(Seq(seq), linear=not circular):
            out.add((pos - 1, pos - 1 - e.ovhg))
    return sorted(out)


def _arcs(seq, cs, circular):
    ext = seq + seq if circular else seq
    n = len(seq)
    for i, (lt, lb) in enumerate(cs):
        if i + 1 < len(cs):
            rt, rb = cs[i + 1]
        elif circular:
            rt, rb = cs[0][0] + n, cs[0][1] + n
        else:
            continue
        yield ext[lt:rt], ext[lb:rb]


def _circ_eq(a, b):
    return len(a) == len(b) and (a in b + b or rc(a) in b + b)


def _dna(rnd, n, avoid):
    while True:
        s = "".join(rnd.choice("ACGT") for _ in range(n))
        if not any(a in s or a in rc(s) or a in s[-5:] + s[:5] for a in avoid):
            return s


def _case(seed):
    rnd = random.Random(seed)
    pv = rnd.sample(PANEL, 2)
    pi = pv if seed % 3 else rnd.sample(PANEL, 2)
    sites = {n: str(getattr(BR, n).site) for n in set(pv + pi)}
    avoid = [str(getattr(BR, n).site) for n in PANEL]
    vec = _dna(rnd, rnd.randrange(300, 600), avoid) + sites[pv[0]] + _dna(rnd, rnd.randrange(6, 20), avoid) + sites[pv[1]] + _dna(rnd, rnd.randrange(300, 600), avoid)
    left, right = sites[pi[0]], sites[pi[1]]
    if seed % 4 == 0:
        left, right = rc(left), rc(right)
    ins = "GCGCGC" + left + _dna(rnd, rnd.randrange(60, 200), avoid) + right + "GCGCGC"
    return vec, ins, pv, pi


def _verify(seed):
    """Check every proposal of one random case against the independent build; return (checked, reverse_checked, payload_differs)."""
    vec, ins, pv, pi = _case(seed)
    body = {"backbone_sequence": vec, "backbone_topology": "circular", "insert_sequence": ins, "insert_topology": "linear",
            "enzymes": sorted(set(pv + pi)), "limit": 10}
    checked = reverse = differs = 0
    for p in suggest_restriction_cloning(body)["proposals"]:
        names = p["vector_enzymes"] + p["insert_enzymes"]
        if any(str(getattr(BR, n).site) != rc(str(getattr(BR, n).site)) for n in names):
            continue
        prepared = (p.get("prepared_insert_sequence") or ins).upper()
        built = []
        for vtop, _ in _arcs(vec.upper(), _cuts(vec.upper(), p["vector_enzymes"], True), True):
            for itop, ibot_ref in _arcs(prepared, _cuts(prepared, p["insert_enzymes"], False), False):
                built += [("forward", vtop, itop), ("reverse", vtop, rc(ibot_ref))]
        match = [b for b in built if _circ_eq(b[1] + b[2], p["product_sequence"].upper())]
        assert match, ("product is not physically buildable", p["vector_enzymes"], p["insert_enzymes"], p["insert_orientation"])
        _orient, vtop, contribution = match[0]
        assert p["vector_length"] == len(vtop)
        assert p["insert_contributed_length"] == len(contribution)
        assert p["vector_length"] + p["insert_contributed_length"] == p["product_length"] == len(p["product_sequence"])
        checked += 1
        reverse += int(p["insert_orientation"] == "reverse")
        differs += int(p["insert_payload_length"] != p["insert_contributed_length"])
    return checked, reverse, differs


@pytest.mark.parametrize("seed", range(40))
def test_displayed_lengths_add_up_and_match_an_independent_build(seed):
    # Seeds whose insert uses a different enzyme pair than the vector can legitimately have no compatible proposal.
    _verify(seed)


def test_the_random_corpus_really_exercises_reverse_inserts_and_the_strand_length_difference():
    totals = [sum(x) for x in zip(*(_verify(seed) for seed in range(40)))]
    checked, reverse, differs = totals
    assert checked >= 40, totals  # deterministic corpus floor (seeds 0-39 give 45)
    assert reverse >= 8, totals
    assert differs >= 4, totals


def test_reverse_proposal_is_where_the_old_field_disagrees_and_the_new_one_does_not():
    """NdeI (2 nt 5' overhang) on the vector, XbaI (4 nt) on the insert side: a reversed insert contributes a different strand length."""
    rnd = random.Random(5)
    avoid = [str(getattr(BR, n).site) for n in PANEL]
    vec = _dna(rnd, 400, avoid) + "CATATG" + _dna(rnd, 8, avoid) + "TCTAGA" + _dna(rnd, 500, avoid)
    ins = "GCGCGC" + "TCTAGA" + _dna(rnd, 90, avoid) + "CATATG" + "GCGCGC"  # insert carries the pair in the opposite order
    res = suggest_restriction_cloning({"backbone_sequence": vec, "backbone_topology": "circular", "insert_sequence": ins,
                                       "insert_topology": "linear", "enzymes": ["NdeI", "XbaI"], "limit": 10})
    rows = [(p["insert_orientation"], p["insert_payload_length"], p["insert_contributed_length"], p["vector_length"], p["product_length"]) for p in res["proposals"]]
    assert rows, "expected proposals"
    for orientation, payload, contributed, vector, product in rows:
        assert vector + contributed == product
    assert any(payload != contributed for _o, payload, contributed, _v, _p in rows), "this fixture must exercise the strand-length difference"
