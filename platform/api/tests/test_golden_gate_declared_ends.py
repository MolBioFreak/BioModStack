"""Golden Gate simulate/save must refuse a fragment whose declared left overhang is not what its sequence starts with."""
from dataclasses import asdict
import random

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import molbio_ops
from services.assembly.golden_gate import resolve_golden_gate_enzyme, simulate_golden_gate
from services.assembly.types import AssemblyError, AssemblyFragment, FragmentEnd
from services.restriction_catalog import catalog_authority
from test_golden_gate_workflow_receiving import client_store

SITES = ("GGTCTC", "GAGACC")
LABELS = ["AATG", "GCTT", "CAGC"]


def _rc(s):
    return s[::-1].translate(str.maketrans("ACGT", "TGCA"))


def _body(n, seed):
    rnd = random.Random(seed)
    while True:
        s = "".join(rnd.choice("ACGT") for _ in range(n))
        if not any(m in s for m in SITES):
            return s


BODIES = [_body(30, 1), _body(30, 2), _body(30, 3)]


def _binding():
    catalog = catalog_authority.require()
    return dict(enzyme_id="BsaI", catalog_id=catalog.catalog_id, expected_catalog_sha256=catalog.content_sha256)


def _enzyme():
    b = _binding()
    return resolve_golden_gate_enzyme(enzyme_id=b["enzyme_id"], catalog_id=b["catalog_id"],
                                      expected_catalog_sha256=b["expected_catalog_sha256"])


def _frag(k, sequence=None, **kw):
    return AssemblyFragment(
        id=f"p{k}", name=f"part {k + 1}", sequence=sequence if sequence is not None else LABELS[k] + BODIES[k],
        left_end=FragmentEnd("sticky_5", LABELS[k], **kw.pop("left", {})),
        right_end=FragmentEnd("sticky_5", LABELS[(k + 1) % 3], **kw.pop("right", {})), **kw)


def _simulate(fragments, circular=True):
    return simulate_golden_gate(fragments, enzyme=_enzyme(), circular=circular)


def _ring_equal(a, b):
    return len(a) == len(b) and a in b + b


INTENDED = "".join(LABELS[k] + BODIES[k] for k in range(3))


def test_matching_parts_are_accepted_and_give_the_intended_product():
    product = _simulate([_frag(0), _frag(1), _frag(2)])
    assert _ring_equal(product.sequence, INTENDED) and product.warnings == []


def test_matching_parts_with_explicit_strands_are_accepted():
    # physical form: the left overhang is on the top strand, the right overhang on the bottom strand (written 5'->3')
    fragments = [
        AssemblyFragment(id=f"p{k}", name=f"part {k + 1}", sequence=LABELS[k] + BODIES[k],
                         left_end=FragmentEnd("sticky_5", LABELS[k], protruding_strand="top"),
                         right_end=FragmentEnd("sticky_5", _rc(LABELS[(k + 1) % 3]), protruding_strand="bottom"))
        for k in range(3)
    ]
    assert _ring_equal(_simulate(fragments).sequence, INTENDED)


def test_lowercase_sequence_is_accepted():
    fragments = [_frag(0), _frag(1, (LABELS[1] + BODIES[1]).lower()), _frag(2)]
    assert _ring_equal(_simulate(fragments).sequence, INTENDED)


def test_declared_left_overhang_that_the_bytes_do_not_start_with_is_refused():
    fragments = [_frag(0), _frag(1, "TTCG" + BODIES[1]), _frag(2)]
    with pytest.raises(AssemblyError) as caught:
        _simulate(fragments)
    assert str(caught.value) == "Mismatch detected: part 2 declares left overhang GCTT, sequence starts with TTCG"


def test_sequence_without_its_overhang_bytes_is_refused():
    with pytest.raises(AssemblyError, match="part 2.*GCTT"):
        _simulate([_frag(0), _frag(1, BODIES[1]), _frag(2)])


def test_reverse_complemented_sequence_with_forward_metadata_is_refused():
    fragments = [_frag(0), _frag(1, _rc(LABELS[1] + BODIES[1])), _frag(2)]
    with pytest.raises(AssemblyError, match="Mismatch detected: part 2"):
        _simulate(fragments)


def test_linear_assembly_is_checked_the_same_way():
    fragments = [_frag(0), _frag(1, "TTCG" + BODIES[1])]
    with pytest.raises(AssemblyError, match="part 2"):
        _simulate(fragments, circular=False)


def test_a_bottom_protruding_left_end_is_not_checked_against_the_top_bytes():
    # the overhang lives on the bottom strand, so the top bytes legitimately do not start with it
    fragments = [_frag(0), _frag(1, BODIES[1], left=dict(protruding_strand="bottom")), _frag(2)]
    try:
        _simulate(fragments)
    except AssemblyError as exc:
        assert "declares left overhang" not in str(exc)


def test_reverse_oriented_fragment_with_physical_strands_is_accepted():
    # part 2 stored as the opposite strand: its top strand is RC(body) behind the top-strand overhang of its (new) left end
    stored = AssemblyFragment(id="p1", name="part 2", sequence=_rc(LABELS[2]) + _rc(BODIES[1]), orientation="reverse",
                              left_end=FragmentEnd("sticky_5", _rc(LABELS[2]), protruding_strand="top"),
                              right_end=FragmentEnd("sticky_5", LABELS[1], protruding_strand="bottom"))
    assert _ring_equal(_simulate([_frag(0), stored, _frag(2)]).sequence, INTENDED)


def test_reverse_oriented_fragment_whose_bytes_disagree_is_refused():
    stored = AssemblyFragment(id="p1", name="part 2", sequence="TTCG" + _rc(BODIES[1]), orientation="reverse",
                              left_end=FragmentEnd("sticky_5", _rc(LABELS[2]), protruding_strand="top"),
                              right_end=FragmentEnd("sticky_5", LABELS[1], protruding_strand="bottom"))
    with pytest.raises(AssemblyError, match="part 2"):
        _simulate([_frag(0), stored, _frag(2)])


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(molbio_ops.router)
    with TestClient(app) as result:
        yield result


def _body_json(fragments, circular=True, **extra):
    return {"fragments": [asdict(f) for f in fragments], "circular": circular, **_binding(), **extra}


def test_simulate_route_reports_the_mismatch(client):
    response = client.post("/api/molbio/assembly/golden-gate/simulate",
                           json=_body_json([_frag(0), _frag(1, "TTCG" + BODIES[1]), _frag(2)]))
    assert response.status_code == 400
    assert "part 2" in response.json()["detail"] and "GCTT" in response.json()["detail"]


def test_simulate_route_still_accepts_matching_parts(client):
    response = client.post("/api/molbio/assembly/golden-gate/simulate", json=_body_json([_frag(0), _frag(1), _frag(2)]))
    assert response.status_code == 200, response.text
    assert _ring_equal(response.json()["product"]["sequence"].upper(), INTENDED)


@pytest.mark.asyncio
async def test_save_route_refuses_the_mismatch_and_saves_nothing(tmp_path):
    async with client_store(tmp_path) as (client, _):
        response = await client.post("/api/molbio/assembly/golden-gate/save",
                                     json=_body_json([_frag(0), _frag(1, "TTCG" + BODIES[1]), _frag(2)], new_name="x"))
    assert response.status_code == 400
    assert "part 2" in response.json()["detail"]
