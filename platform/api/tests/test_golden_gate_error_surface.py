"""Golden Gate refusals must say why: simulate/save keep the real message, and suggest never 500s."""
from dataclasses import asdict
import random

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import molbio_ops
from services.assembly.golden_gate_suggest import SuggestRequest, suggest_golden_gate
from services.assembly.types import AssemblyFragment, FragmentEnd
from services.restriction_catalog import catalog_authority
from test_golden_gate_workflow_receiving import client_store

BSAI = ("GGTCTC", "GAGACC")


def _binding():
    catalog = catalog_authority.require()
    return dict(enzyme_id="BsaI", catalog_id=catalog.catalog_id, expected_catalog_sha256=catalog.content_sha256)


def _dna(n, seed):
    rnd = random.Random(seed)
    while True:
        s = "".join(rnd.choice("ACGT") for _ in range(n))
        if not any(m in s for m in BSAI):
            return s


def _frag(i, body, left, right):
    return AssemblyFragment(id=f"p{i}", name=f"part {i}", sequence=body,
                            left_end=FragmentEnd("sticky_5", left), right_end=FragmentEnd("sticky_5", right))


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(molbio_ops.router)
    with TestClient(app) as result:
        yield result


def _post(client, route, fragments, circular=False):
    return client.post(f"/api/molbio/assembly/golden-gate/{route}",
                       json={"fragments": [asdict(f) for f in fragments], "circular": circular, **_binding()})


def test_simulate_reports_internal_site_instead_of_generic_400(client):
    body = _dna(40, 1)
    cut = body[:20] + "GGTCTC" + body[20:]
    fragments = [_frag(0, _dna(30, 2), "AATG", "GCTT"), _frag(1, cut, "GCTT", "CAGC"), _frag(2, _dna(30, 3), "CAGC", "TCGA")]
    response = _post(client, "simulate", fragments)
    assert response.status_code == 400
    assert "internal BsaI recognition site" in response.json()["detail"]
    assert "part 1" in response.json()["detail"]


def test_simulate_reports_wrong_overhang_length(client):
    fragments = [_frag(0, _dna(30, 2), "AATG", "GCT"), _frag(1, _dna(30, 4), "GCT", "TCGA")]
    response = _post(client, "simulate", fragments)
    assert response.status_code == 400
    assert "4 nt" in response.json()["detail"]


def test_simulate_reports_non_complementary_join(client):
    fragments = [_frag(0, _dna(30, 2), "AATG", "GCTT"), _frag(1, _dna(30, 4), "CAGC", "TCGA")]
    response = _post(client, "simulate", fragments)
    assert response.status_code == 400
    assert "not compatible" in response.json()["detail"]


@pytest.mark.asyncio
async def test_save_reports_internal_site_instead_of_generic_400(tmp_path):
    body = _dna(40, 1)
    cut = body[:20] + "GGTCTC" + body[20:]
    fragments = [_frag(0, _dna(30, 2), "AATG", "GCTT"), _frag(1, cut, "GCTT", "CAGC"), _frag(2, _dna(30, 3), "CAGC", "TCGA")]
    async with client_store(tmp_path) as (client, _):
        response = await client.post("/api/molbio/assembly/golden-gate/save",
                                     json={"fragments": [asdict(f) for f in fragments], "circular": False, "new_name": "x", **_binding()})
    assert response.status_code == 400
    assert "internal BsaI recognition site" in response.json()["detail"]


def _suggest(parts):
    return SuggestRequest.model_validate(dict(
        enzymes=["BsaI"], circular=True, preparation_kind="pcr",
        parts=[dict(source=dict(id=f"p{i}", source=dict(kind="inline", sequence=s, topology="linear")), name=f"part{i}")
               for i, s in enumerate(parts)]))


def test_suggest_with_site_inside_a_part_is_a_reported_refusal_not_a_crash():
    parts = [_dna(500, 10 + i) for i in range(4)]
    parts[1] = parts[1][:250] + "GGTCTC" + parts[1][256:]
    result = suggest_golden_gate(_suggest(parts))
    assert result.proposals == []
    report = result.enzymes[0]
    assert report.internal_sites == 1
    assert report.status.startswith("no proposal:") and report.status != "no proposal: "
    assert result.diagnostics


def test_suggest_without_sites_still_designs():
    parts = [_dna(500, 20 + i) for i in range(4)]
    result = suggest_golden_gate(_suggest(parts))
    assert result.proposals and result.enzymes[0].internal_sites == 0


@pytest.mark.asyncio
async def test_suggest_route_returns_200_with_reason_for_site_inside_a_part(tmp_path):
    parts = [_dna(500, 30 + i) for i in range(4)]
    parts[2] = parts[2][:100] + "GAGACC" + parts[2][106:]
    async with client_store(tmp_path) as (client, _):
        response = await client.post("/api/molbio/assembly/golden-gate/suggest", json=_suggest(parts).model_dump(mode="json"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["proposals"] == []
    assert body["enzymes"][0]["status"].startswith("no proposal:")
