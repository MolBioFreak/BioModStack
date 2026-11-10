"""Supplied-parts Golden Gate: a chosen junction label must not form a recognition site with its neighbouring bases."""
import random

import pytest
from services.assembly.golden_gate import resolve_golden_gate_enzyme
from services.assembly.golden_gate_suggest import SuggestRequest, suggest_golden_gate
from services.restriction_catalog import catalog_authority

SITES = ("GGTCTC", "GAGACC")


def _bsai():
    catalog = catalog_authority.require()
    return resolve_golden_gate_enzyme(enzyme_id="BsaI", catalog_id=catalog.catalog_id,
                                      expected_catalog_sha256=catalog.content_sha256)


def _clean(length, seed, head="", tail=""):
    rnd = random.Random(seed)
    while True:
        body = head + "".join(rnd.choice("ACGT") for _ in range(length)) + tail
        if not any(site in body for site in SITES):
            return body


def _request(parts, **extra):
    return SuggestRequest.model_validate(dict(
        enzymes=["BsaI"], circular=True, preparation_kind="pcr", **extra,
        parts=[dict(source=dict(id=f"p{i}", source=dict(kind="inline", sequence=s, topology="linear")), name=f"part{i}")
               for i, s in enumerate(parts)]))


def _ring_has_site(sequence):
    ring = sequence.upper() + sequence.upper()[: len(SITES[0]) - 1]
    return any(site in ring for site in SITES)


def _trap(label):
    """(tail of the left part, head of the right part) that make `label` complete a BsaI site, or None."""
    width = len(label)
    for site in SITES:
        for start in range(-(len(site) - 1), width):
            overlap = range(max(0, start), min(width, start + len(site)))
            if overlap and all(label[p] == site[p - start] for p in overlap):
                tail = site[:-start] if start < 0 else ""
                head = site[width - start:] if start + len(site) > width else ""
                return tail, head
    return None


def test_seam_offenders_flags_a_label_that_completes_a_site_with_both_neighbours():
    from services.assembly.golden_gate_suggest_parts import _seam_offenders
    payloads = ["TTTTTG", "CTTTTT"]
    assert _seam_offenders(_bsai(), payloads, {0: "AAAA", 1: "AGAC"}, True) == {1}
    assert _seam_offenders(_bsai(), payloads, {0: "AAAA", 1: "AACC"}, True) == set()


def test_seam_offenders_flags_a_label_that_completes_a_site_with_one_neighbour():
    from services.assembly.golden_gate_suggest_parts import _seam_offenders
    assert _seam_offenders(_bsai(), ["TTTGAG", "TTTTTT"], {0: "AAAA", 1: "ACCA"}, True) == {1}
    assert _seam_offenders(_bsai(), ["TTTTTT", "TCAAAA"], {0: "AAAA", 1: "GGTC"}, True) == {1}


def test_seam_offenders_open_ends_have_no_outside_neighbour():
    from services.assembly.golden_gate_suggest_parts import _seam_offenders
    # as a ring the end label GGTC would meet the first label TC.. and form a site; as a linear product it does not
    assert _seam_offenders(_bsai(), ["TTTTTT"], {0: "TCAA", 1: "GGTC"}, False) == set()
    assert _seam_offenders(_bsai(), ["TCAAAA"], {0: "GGTC", 1: "AAAA"}, False) == {0}


@pytest.mark.parametrize("count", [4, 5, 6, 8])
def test_chosen_label_that_would_form_a_site_with_a_neighbour_is_replaced(count):
    clean = [_clean(50 + i, 100 + i) for i in range(count)]
    baseline = suggest_golden_gate(_request(clean)).proposals[0].junctions
    slot = next(s for s, label in enumerate(baseline) if _trap(label))
    tail, head = _trap(baseline[slot])
    parts = list(clean)
    left = (slot - 1) % count
    parts[left] = _clean(50 + left, 100 + left, tail=tail)
    parts[slot] = _clean(50 + slot, 100 + slot, head=head) if left != slot else parts[slot]
    result = suggest_golden_gate(_request(parts))
    assert result.proposals, result.enzymes[0].status
    for proposal in result.proposals:
        assert proposal.junctions[slot] != baseline[slot]
        assert not _ring_has_site(proposal.final_sequence)
        assert proposal.final_sequence.upper() in "".join(
            proposal.junctions[i] + parts[i] for i in range(count)).upper() * 2


def test_clean_parts_keep_the_labels_they_always_had():
    result = suggest_golden_gate(_request([_clean(50 + i, 21 + i) for i in range(5)], proposals=1))
    assert result.proposals[0].junctions == ["AAAA", "AACC", "AAGG", "ACAC", "ACCA"]
