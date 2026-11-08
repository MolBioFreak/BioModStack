"""Cloning gaps lane B: common-enzyme ranking, single-enzyme cloning, tail-path primers."""
from __future__ import annotations

import pytest

from routers import molbio_end_operations
from services.assembly.restriction_cloning import suggest_restriction_cloning
from services.molbio_ops import reverse_complement as rc
from test_cloning_connected_api_acceptance import empty_store
from test_molbio_branches import make_client
from test_end_operations_and_cloning import _plasmid, _random_dna, SITES

BASE = "/api/molbio/assembly/restriction-cloning"
COMMON = {"EcoRI", "BamHI", "HindIII", "XhoI", "NcoI", "BglII", "NotI", "KpnI", "SalI", "XbaI", "SpeI", "PstI", "SacI", "NdeI", "SmaI", "EcoRV"}


def circ_eq(a: str, b: str) -> bool:
    return len(a) == len(b) and a in b + b


def test_common_cloning_enzymes_rank_first_and_nothing_is_dropped():
    backbone = _plasmid(SITES["EcoRI"] + "TTTAGC" + SITES["BamHI"], seed=5)
    payload = _random_dna(150, 11, avoid=SITES.values())
    insert = "GCGC" + SITES["EcoRI"] + payload + SITES["BamHI"] + "GCGC"
    body = {"backbone_sequence": backbone, "insert_sequence": insert, "insert_start": 10, "insert_end": 160, "limit": 3}
    result = suggest_restriction_cloning(body)
    top = result["proposals"][0]
    assert set(top["vector_enzymes"]) == {"EcoRI", "BamHI"}, [p["vector_enzymes"] for p in result["proposals"]]
    assert top["directional"]
    assert all(set(p["vector_enzymes"]) <= COMMON for p in result["proposals"][:2])
    wide = suggest_restriction_cloning({**body, "limit": 10})
    assert len(wide["proposals"]) >= len(result["proposals"])  # ranking only; nothing filtered


def test_single_enzyme_search_orders_after_directional_pairs():
    backbone = _plasmid(SITES["EcoRI"] + "TTTAGC" + SITES["BamHI"], seed=5)
    insert = "GCGC" + SITES["EcoRI"] + _random_dna(120, 8, avoid=SITES.values()) + SITES["BamHI"] + "GCGC"
    result = suggest_restriction_cloning({"backbone_sequence": backbone, "insert_sequence": insert,
                                          "enzymes": ["EcoRI", "BamHI"], "limit": 10})
    assert result["proposals"][0]["directional"] and not result["proposals"][0]["single_enzyme"]


async def _chain(client, body, proposal, key):
    sim = await client.post("/api/molbio/assembly/ligation/simulate", json=proposal["assembly_request"])
    assert sim.status_code == 200, sim.text
    assert sim.json()["product"]["sequence"] == proposal["product_sequence"]
    prep = await client.post(BASE + "/prepare", json={"request": body, "selection": proposal["selection"], "name": "single", "idempotency_key": key})
    assert prep.status_code == 200, prep.text
    saved = await client.post("/api/molbio/assembly/ligation/save", json={**prep.json()["assembly_request"], "new_name": key})
    assert saved.status_code == 200, saved.text
    assert saved.json()["saved_sequence"]["sequence"] == proposal["product_sequence"]


@pytest.mark.asyncio
@pytest.mark.parametrize("phosphate", ["unknown", "unphosphorylated"])
async def test_ecori_only_gives_one_proposal_per_orientation(tmp_path, phosphate):
    engine, sessions = await empty_store(tmp_path / "single.db")
    backbone = _plasmid(SITES["EcoRI"], seed=5)
    payload = _random_dna(140, 8, avoid=SITES.values())
    insert = "GCGC" + SITES["EcoRI"] + payload + SITES["EcoRI"] + "GCGC"
    body = {"backbone_sequence": backbone, "insert_sequence": insert, "enzymes": ["EcoRI"], "limit": 3,
            "vector_five_prime_phosphate": phosphate}
    try:
        async with make_client(sessions) as client:
            client._transport.app.include_router(molbio_end_operations.router)
            response = await client.post(BASE + "/suggest", json=body)
            assert response.status_code == 200, response.text
            data = response.json()
            assert not data["no_suitable_pair"]
            proposals = data["proposals"]
            assert sorted(p["insert_orientation"] for p in proposals) == ["forward", "reverse"]
            assert all(not p["directional"] and p["vector_enzymes"][0] == p["vector_enzymes"][1] for p in proposals)
            for p in proposals:
                assert circ_eq(p["product_sequence"], _sticky_total(backbone, payload, p["insert_orientation"]))
                if phosphate == "unphosphorylated":
                    assert p["vector_end_seal"] != "both_strands" and "reduced" in p["vector_self_ligation"]
                await _chain(client, body, p, f"ecori-{phosphate}-{p['insert_orientation']}")
            fwd, rev = proposals
            assert not circ_eq(fwd["product_sequence"], rev["product_sequence"])
    finally:
        await engine.dispose()


def _sticky_total(backbone: str, payload: str, orientation: str) -> str:
    """Independent construction: vector with its EcoRI site opened + insert between sites, both junction sites regenerated."""
    i = backbone.index(SITES["EcoRI"])
    left, right = backbone[:i], backbone[i + 6:]
    mid = payload if orientation == "forward" else rc(payload)
    return right + left + SITES["EcoRI"] + mid + SITES["EcoRI"]


@pytest.mark.asyncio
async def test_bamhi_vector_bglii_insert_compatible_ends_both_orientations(tmp_path):
    engine, sessions = await empty_store(tmp_path / "compat.db")
    backbone = _plasmid(SITES["BamHI"], seed=5)
    payload = _random_dna(130, 9, avoid=SITES.values())
    insert = "GCGC" + SITES["BglII"] + payload + SITES["BglII"] + "GCGC"
    body = {"backbone_sequence": backbone, "insert_sequence": insert, "enzymes": ["BamHI", "BglII"], "limit": 5}
    try:
        async with make_client(sessions) as client:
            client._transport.app.include_router(molbio_end_operations.router)
            response = await client.post(BASE + "/suggest", json=body)
            assert response.status_code == 200, response.text
            proposals = [p for p in response.json()["proposals"] if p["vector_enzymes"] == ["BamHI", "BamHI"] and p["insert_enzymes"] == ["BglII", "BglII"]]
            assert sorted(p["insert_orientation"] for p in proposals) == ["forward", "reverse"]
            i = backbone.index(SITES["BamHI"])
            for p in proposals:
                mid = payload if p["insert_orientation"] == "forward" else rc(payload)
                # hybrid junctions: BamHI half + BglII half = GGATCT / AGATCC, neither cleavable by BamHI nor BglII
                expected = backbone[i + 6:] + backbone[:i] + "GGATCT" + mid + "AGATCC"
                assert circ_eq(p["product_sequence"], expected)
                doubled = p["product_sequence"] + p["product_sequence"][:5]
                assert doubled.count(SITES["BamHI"]) == 0 and doubled.count(SITES["BglII"]) == 0
                await _chain(client, body, p, f"compat-{p['insert_orientation']}")
    finally:
        await engine.dispose()


@pytest.mark.parametrize("n", [150, 400])
@pytest.mark.parametrize("seed", range(20, 32))
def test_tail_path_always_returns_primers_that_amplify_the_prepared_insert(seed, n):
    backbone = _plasmid(SITES["XhoI"] + "TTTAGC" + SITES["NcoI"], seed=5)
    payload = _random_dna(n, seed, avoid=SITES.values())
    body = {"backbone_sequence": backbone, "insert_sequence": payload, "enzymes": ["XhoI", "NcoI"], "limit": 3}
    result = suggest_restriction_cloning(body)
    tail = next(p for p in result["proposals"] if p["kind"] == "primer_tail")
    primers = tail["selection"]["primers"]
    assert len(primers) == 2, tail["selection"]["primer_notes"]
    fwd, rev = primers
    tails = tail["primer_tails"]
    assert fwd["sequence"] == tails["forward_primer_5prime_tail"] + fwd["anneal_sequence"]
    assert rev["sequence"] == tails["reverse_primer_5prime_tail"] + rev["anneal_sequence"]
    assert fwd["anneal_sequence"] == payload[:fwd["anneal_length"]]
    assert rev["anneal_sequence"] == rc(payload[-rev["anneal_length"]:])
    left, right = (SITES[name] for name in tail["insert_enzymes"])
    assert fwd["sequence"].startswith("GCGCGC" + left) and rev["sequence"].startswith("GCGCGC" + rc(right))
    assert 18 <= fwd["anneal_length"] <= 35 and 18 <= rev["anneal_length"] <= 35
    assert abs(fwd["tm"] - rev["tm"]) <= 5.0 or any("Tm differs" in note for note in tail["selection"]["primer_notes"])
    product = fwd["sequence"] + payload[fwd["anneal_length"]:n - rev["anneal_length"]] + rc(rev["sequence"])
    assert product == tail["prepared_insert_sequence"]


def test_tail_path_short_payload_reports_note_not_a_silent_empty_result():
    backbone = _plasmid(SITES["XhoI"] + "TTTAGC" + SITES["NcoI"], seed=5)
    payload = _random_dna(30, 4, avoid=SITES.values())
    result = suggest_restriction_cloning({"backbone_sequence": backbone, "insert_sequence": payload, "enzymes": ["XhoI", "NcoI"]})
    tail = next(p for p in result["proposals"] if p["kind"] == "primer_tail")
    assert tail["selection"]["primers"] == [] and tail["selection"]["primer_notes"]
