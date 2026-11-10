"""Explicit-end ligation must refuse a fragment whose declared top-strand overhang is not in its sequence."""
from dataclasses import asdict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import molbio_ops
from services.assembly.ligation import simulate_ligation
from services.assembly.types import AssemblyError, AssemblyFragment, FragmentEnd

BODY = "ACGTTGCAAGCTTGACCATGCAAGT"


def _frag(i, sequence, left, right, left_strand=None, right_strand=None, kind="sticky_5", orientation="forward"):
    return AssemblyFragment(
        id=str(i), name=f"part {i + 1}", sequence=sequence, orientation=orientation,
        left_end=FragmentEnd(kind, left, protruding_strand=left_strand),
        right_end=FragmentEnd(kind, right, protruding_strand=right_strand),
    )


def _pair(second_sequence):
    return [_frag(0, "AATT" + BODY, "AATT", "GGCC"), _frag(1, second_sequence, "GGCC", "AATT")]


def test_matching_bytes_are_accepted():
    product = simulate_ligation(_pair("GGCC" + BODY), circular=True)
    assert product.sequence == "AATT" + BODY + "GGCC" + BODY and product.warnings == []


def test_left_overhang_not_in_the_bytes_is_refused():
    with pytest.raises(AssemblyError) as caught:
        simulate_ligation(_pair("TTCG" + BODY), circular=True)
    assert str(caught.value) == "Mismatch detected: part 2 declares left overhang GGCC, sequence starts with TTCG"


def test_linear_ligation_is_checked_too():
    with pytest.raises(AssemblyError, match="Mismatch detected: part 2"):
        simulate_ligation(_pair("TTCG" + BODY), circular=False)


def test_top_protruding_right_end_must_be_the_last_bytes():
    # 3' overhangs: the right end protrudes on the top strand, so the top bytes end with it
    ok = [
        _frag(0, BODY + "GTAC", "ATGC", "GTAC", left_strand="bottom", right_strand="top", kind="sticky_3"),
        _frag(1, BODY, "GTAC", "ATGC", left_strand="bottom", right_strand="top", kind="sticky_3"),
    ]
    ok[1] = _frag(1, BODY + "ATGC", "GTAC", "ATGC", left_strand="bottom", right_strand="top", kind="sticky_3")
    simulate_ligation(ok, circular=False)
    bad = [
        _frag(0, BODY + "TTTT", "ATGC", "GTAC", left_strand="bottom", right_strand="top", kind="sticky_3"),
        ok[1],
    ]
    with pytest.raises(AssemblyError, match="Mismatch detected: part 1 declares right overhang GTAC, sequence ends with TTTT"):
        simulate_ligation(bad, circular=False)


def test_bottom_protruding_ends_are_not_checked_against_the_top_bytes():
    fragments = [
        _frag(0, BODY, "AATT", "AATT", left_strand="bottom", right_strand="bottom"),
        _frag(1, BODY, "AATT", "AATT", left_strand="bottom", right_strand="bottom"),
    ]
    try:
        simulate_ligation(fragments, circular=False)
    except AssemblyError as exc:
        assert "Mismatch detected" not in str(exc)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(molbio_ops.router)
    with TestClient(app) as result:
        yield result


def test_ligation_route_reports_the_mismatch(client):
    response = client.post("/api/molbio/assembly/ligation/simulate",
                           json={"fragments": [asdict(f) for f in _pair("TTCG" + BODY)], "circular": True})
    assert response.status_code == 400
    assert response.json()["detail"] == "Mismatch detected: part 2 declares left overhang GGCC, sequence starts with TTCG"


def test_ligation_route_accepts_matching_bytes(client):
    response = client.post("/api/molbio/assembly/ligation/simulate",
                           json={"fragments": [asdict(f) for f in _pair("GGCC" + BODY)], "circular": True})
    assert response.status_code == 200, response.text
