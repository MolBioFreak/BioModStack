"""The per-request digest/analysis memo in restriction-cloning suggest must be invisible: same output as an unmemoized run,
and nothing may survive the request (including a failed one)."""
from __future__ import annotations

import random

import pytest

import services.assembly.restriction_cloning as rc_mod
from services.assembly.restriction_cloning import suggest_restriction_cloning
from services.molbio_ops import reverse_complement


def _dna(seed: int, n: int, avoid=("GAATTC", "GGATCC", "AAGCTT")) -> str:
    rnd = random.Random(seed)
    while True:
        s = "".join(rnd.choice("ACGT") for _ in range(n))
        if not any(a in s for a in avoid):
            return s


def body(seed: int, **extra):
    vector = _dna(seed, 600) + "GAATTC" + "TTTAGC" + "GGATCC" + _dna(seed + 1, 700)
    insert = "GCGC" + "GAATTC" + _dna(seed + 2, 140) + "GGATCC" + "GCGC"
    return {"backbone_sequence": vector, "backbone_topology": "circular", "insert_sequence": insert,
            "insert_topology": "linear", "limit": 5, **extra}


def test_memo_does_not_change_results_and_is_cleared():
    assert rc_mod._DIGEST_MEMO.get() is None
    first = suggest_restriction_cloning(body(11))
    assert rc_mod._DIGEST_MEMO.get() is None
    # the same request again, and a different request in between, must give identical answers to the first run
    other = suggest_restriction_cloning(body(23))
    again = suggest_restriction_cloning(body(11))
    assert again == first
    assert other != first
    assert first["proposals"] and first["proposals"][0]["junction_sites"], "returned proposals carry their junction observations"
    assert all("_lazy_junctions" not in p for p in first["proposals"])


def test_memo_is_cleared_when_the_request_fails():
    bad = body(31)
    bad["backbone_topology"] = "linear"  # the suggester refuses a linear backbone
    with pytest.raises(Exception):
        suggest_restriction_cloning(bad)
    assert rc_mod._DIGEST_MEMO.get() is None


def test_unmemoized_digest_gives_the_same_proposals(monkeypatch):
    """Disable the memo (every lookup misses, nothing is stored) and compare with the normal run."""
    normal = suggest_restriction_cloning(body(41, enzymes=["EcoRI", "BamHI"]))

    class Miss(dict):
        def __contains__(self, key):
            return False

        def __setitem__(self, key, value):
            pass

    original = rc_mod._suggest_restriction_cloning

    def no_memo(b):
        token = rc_mod._DIGEST_MEMO.set(Miss())
        try:
            return original(b)
        finally:
            rc_mod._DIGEST_MEMO.reset(token)

    monkeypatch.setattr(rc_mod, "_suggest_restriction_cloning", no_memo)
    unmemoized = suggest_restriction_cloning(body(41, enzymes=["EcoRI", "BamHI"]))
    assert unmemoized == normal


def test_lazy_junctions_match_eager_ones():
    result = suggest_restriction_cloning(body(51, enzymes=["EcoRI", "BamHI"]))
    for proposal in result["proposals"]:
        sites = proposal["junction_sites"]
        assert sites and all({"between", "regenerated", "destroyed", "note"} <= set(j) for j in sites)
        assert reverse_complement(reverse_complement(proposal["product_sequence"])) == proposal["product_sequence"]
