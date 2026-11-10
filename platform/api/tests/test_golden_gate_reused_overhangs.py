"""Golden Gate simulate/save must refuse an assembly that reuses a junction overhang class."""
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


def _body(n, seed):
    rnd = random.Random(seed)
    while True:
        s = "".join(rnd.choice("ACGT") for _ in range(n))
        if not any(m in s for m in SITES):
            return s


def _binding():
    catalog = catalog_authority.require()
    return dict(enzyme_id="BsaI", catalog_id=catalog.catalog_id, expected_catalog_sha256=catalog.content_sha256)


def _enzyme():
    return resolve_golden_gate_enzyme(**_binding())


def _ring(labels, circular=True):
    """Part k starts with labels[k] and ends where labels[k + 1] begins; a linear set has len(labels) + 1 parts."""
    count = len(labels) if circular else len(labels) + 1
    lefts = [labels[k - 1] if (k or circular) else "ACGA" for k in range(count)]
    rights = [labels[k] if k < len(labels) else "TCGA" for k in range(count)]
    return [
        AssemblyFragment(id=f"p{k}", name=f"part {k + 1}", sequence=lefts[k] + _body(30, k + 1),
                         left_end=FragmentEnd("sticky_5", lefts[k]), right_end=FragmentEnd("sticky_5", rights[k]))
        for k in range(count)
    ]


def _simulate(labels, circular=True):
    return simulate_golden_gate(_ring(labels, circular), enzyme=_enzyme(), circular=circular)


def test_distinct_overhangs_are_accepted_without_warnings():
    product = _simulate(["AATG", "GCTT", "CAGC", "TACG"])
    assert product.warnings == []


def test_a_reused_overhang_is_refused_in_a_ring():
    with pytest.raises(AssemblyError) as caught:
        _simulate(["AATG", "GCTT", "AATG", "CAGC"])
    assert str(caught.value) == "Duplicate overhang detected: AATG/CATT used at 2 junctions"


def test_a_reused_overhang_is_refused_in_a_linear_assembly():
    with pytest.raises(AssemblyError, match="Duplicate overhang detected: AATG/CATT used at 2 junctions"):
        _simulate(["AATG", "GCTT", "AATG"], circular=False)


def test_an_overhang_and_its_reverse_complement_count_as_one_class():
    with pytest.raises(AssemblyError, match="Duplicate overhang detected: AATG/CATT used at 2 junctions"):
        _simulate(["AATG", "GCTT", "CATT", "CAGC"])


def test_three_uses_are_counted():
    with pytest.raises(AssemblyError, match="used at 3 junctions"):
        _simulate(["AATG", "GCTT", "AATG", "CAGC", "AATG"])


def test_a_single_palindromic_junction_is_unchanged_and_still_warns():
    product = _simulate(["ATAT", "AGAC"], circular=False)
    assert sum("is palindromic" in warning for warning in product.warnings) == 1


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(molbio_ops.router)
    with TestClient(app) as result:
        yield result


def _json(labels, **extra):
    return {"fragments": [asdict(f) for f in _ring(labels)], "circular": True, **_binding(), **extra}


def test_simulate_route_reports_the_duplicate(client):
    response = client.post("/api/molbio/assembly/golden-gate/simulate", json=_json(["AATG", "GCTT", "AATG", "CAGC"]))
    assert response.status_code == 400
    assert response.json()["detail"] == "Duplicate overhang detected: AATG/CATT used at 2 junctions"


def test_simulate_route_still_accepts_distinct_overhangs(client):
    response = client.post("/api/molbio/assembly/golden-gate/simulate", json=_json(["AATG", "GCTT", "CAGC", "TACG"]))
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_save_route_refuses_the_duplicate_and_saves_nothing(tmp_path):
    async with client_store(tmp_path) as (client, _):
        response = await client.post("/api/molbio/assembly/golden-gate/save",
                                     json=_json(["AATG", "GCTT", "AATG", "CAGC"], new_name="x"))
    assert response.status_code == 400
    assert "Duplicate overhang detected" in response.json()["detail"]
