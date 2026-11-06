"""Gibson/HiFi suggestions through the real routes against a scratch store."""
import asyncio
import copy
import random
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

API_DIR = Path(__file__).resolve().parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from molbio_database import create_molbio_engine, get_molbio_session, init_molbio_db, make_molbio_session_factory  # noqa: E402
from routers.molbio_gibson_suggest import router as suggest_router  # noqa: E402
from routers.molbio_ops import router as molbio_router  # noqa: E402
from services.assembly.region_match import reverse_complement  # noqa: E402


def dna(seed, n):
    rng = random.Random(seed)
    return "".join(rng.choice("ACGT") for _ in range(n))


def same_circle(a, b):
    return len(a) == len(b) and a in b + b


@pytest.fixture
def client(tmp_path):
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'w2a.db'}")
    asyncio.run(init_molbio_db(engine=engine))
    sessions = make_molbio_session_factory(engine)

    async def override():
        async with sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(molbio_router)
    app.include_router(suggest_router)
    app.dependency_overrides[get_molbio_session] = override
    with TestClient(app) as test_client:
        yield test_client
    asyncio.run(engine.dispose())


def feat(fid, name, kind, start, end, strand=1):
    return {"id": fid, "name": name, "type": kind, "start": start, "end": end, "strand": strand,
            "segments": [{"start": start, "end": end}]}


def suggest(client, **body):
    resp = client.post("/api/molbio/assembly/gibson/suggest", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def design(client, proposal):
    resp = client.post("/api/molbio/assembly/gibson/design", json=proposal["design_request"])
    assert resp.status_code == 200, resp.text
    return resp.json()


P, MARKER, Q = dna(1, 300), dna(2, 800), dna(3, 900)
RECIPIENT = P + MARKER + Q
RECIPIENT_FEATURES = [feat("f-marker", "KanR", "CDS", 300, 1100), feat("f-ori", "ori", "rep_origin", 1500, 1900),
                      feat("f-prom", "P_lac", "promoter", 100, 250)]
CASSETTE = dna(4, 700)


def test_a1a_marker_swap_plain_cassette_through_the_real_design_route(client):
    target = P + CASSETTE + Q
    body = suggest(client, target={"name": "swapped", "sequence": target, "circular": True},
                   sources=[{"id": "rec", "name": "recipient", "sequence": RECIPIENT, "circular": True,
                             "features": RECIPIENT_FEATURES}])
    top = body["proposals"][0]
    prep = {f["preparation"]: f for f in top["fragments"]}
    assert top["counts"] == {"pcr": 1, "synthesis": 1, "existing": 0}
    assert prep["synthesis"]["sequence"] == CASSETTE  # plain order sequence, tails ride on the backbone primers
    assert prep["pcr"]["source"]["wraps_origin"] is True
    result = design(client, top)
    assert same_circle(result["selected_product"]["sequence"], target)
    assert all(p["tail_sequence"] for p in result["primers"])  # both backbone primers carry the overlaps
    assert {k: sorted(v) for k, v in top["features"].items()} == {
        "kept": ["recipient: P_lac (f-prom)", "recipient: ori (f-ori)"],
        "removed": ["recipient: KanR (f-marker)"], "clipped": []}


def test_a1c_replacement_cds_with_promoter_boundaries_from_feature_edges(client):
    cds_unit = dna(5, 600)  # promoter 0-150 and CDS 150-600 of the donor
    donor = dna(6, 200) + cds_unit + dna(7, 250)
    donor_features = [feat("d-prom", "P_new", "promoter", 200, 350), feat("d-cds", "NewR", "CDS", 350, 800),
                      feat("d-tail", "T_end", "terminator", 700, 900)]  # terminator straddles the region end
    target = P + cds_unit + Q
    body = suggest(client, target={"sequence": target, "circular": True},
                   sources=[{"id": "rec", "name": "recipient", "sequence": RECIPIENT, "circular": True,
                             "features": RECIPIENT_FEATURES},
                            {"id": "don", "name": "donor", "sequence": donor, "circular": True,
                             "features": donor_features}])
    top = body["proposals"][0]
    donor_part = next(f for f in top["fragments"] if f["source"] and f["source"]["id"] == "don")
    assert (donor_part["source"]["start"], donor_part["source"]["end"]) == (200, 800)  # = promoter start .. CDS end
    assert same_circle(design(client, top)["selected_product"]["sequence"], target)
    report = top["features"]
    assert "donor: P_new (d-prom)" in report["kept"] and "donor: NewR (d-cds)" in report["kept"]
    assert "donor: T_end (d-tail)" in report["clipped"]
    assert "recipient: KanR (f-marker)" in report["removed"]


def _a2_sources():
    a, c = dna(10, 600), dna(11, 400)
    return a, c


def test_a2a_linear_target_alternatives_are_distinct_ordered_and_assemble(client):
    a, c = _a2_sources()
    new1, new2 = dna(12, 200), dna(13, 300)
    target = new1 + a + c[100:160] + new2
    sources = [{"id": "a", "name": "frag-a", "sequence": a, "circular": False},
               {"id": "c", "name": "plasmid-c", "sequence": c, "circular": True}]
    before = copy.deepcopy(sources)
    body = suggest(client, target={"sequence": target, "circular": False}, sources=sources)
    proposals = body["proposals"]
    assert len(proposals) == 3 and [p["rank"] for p in proposals] == [1, 2, 3]
    keys = {repr(p["design_request"]["fragments"]) for p in proposals}
    assert len(keys) == 3
    synth = [sum(len(f["sequence"]) for f in p["fragments"] if f["preparation"] == "synthesis") for p in proposals]
    assert synth == sorted(synth)
    assert any(p["counts"]["existing"] == 1 for p in proposals)
    for proposal in proposals:
        assert design(client, proposal)["selected_product"]["sequence"] == target
    assert sources == before


def test_a2a_circular_target_with_reverse_oriented_source(client):
    a, c = _a2_sources()
    target = a[:400] + dna(14, 120) + reverse_complement(c[50:350])
    body = suggest(client, target={"sequence": target, "circular": True},
                   sources=[{"id": "a", "name": "frag-a", "sequence": a}, {"id": "c", "name": "c", "sequence": c}])
    top = body["proposals"][0]
    assert any(f["source"] and f["source"]["strand"] == -1 for f in top["fragments"])
    assert same_circle(design(client, top)["selected_product"]["sequence"], target)


def test_fixed_boundary_and_protected_feature(client):
    a = dna(15, 900)
    target = dna(16, 100) + a + dna(17, 100)
    features = [feat("t-1", "site", "misc_feature", 100 + 400, 100 + 460)]
    body = suggest(client, target={"sequence": target, "circular": False, "features": features},
                   sources=[{"id": "a", "name": "a", "sequence": a}], fixed_boundaries=[100 + 300])
    top = body["proposals"][0]
    starts = sorted(f["source"]["start"] for f in top["fragments"] if f["source"])
    assert starts == [0, 300]
    assert design(client, top)["selected_product"]["sequence"] == target


def test_no_matching_source_returns_a_diagnostic_not_a_proposal(client):
    body = suggest(client, target={"sequence": dna(18, 500), "circular": False}, sources=[{"id": "x", "sequence": dna(30, 400)}])
    assert body["proposals"] == [] and "No supplied source matches" in body["diagnostics"][0]


def test_twenty_kb_target_two_ten_kb_sources_in_seconds(client):
    a, b = dna(19, 10000), dna(20, 10000)
    target = a[1000:9000] + dna(21, 300) + reverse_complement(b[500:8000]) + a[:4700]
    start = time.time()
    body = suggest(client, target={"sequence": target, "circular": True},
                   sources=[{"id": "a", "name": "a", "sequence": a, "circular": True},
                            {"id": "b", "name": "b", "sequence": b}])
    assert time.time() - start < 30
    assert body["proposals"]
