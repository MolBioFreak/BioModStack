"""Lane G: browser-shaped import overrides, classic-enzyme ranking, duplicate restriction-cloning proposals.

Import tests go through the real import router over a migrated scratch SQLite store; expected values are
recomputed from the raw inputs.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from molbio_database import get_molbio_session
from routers import molbio_end_operations, molbio_ops, nucleotide_sequences
from services.assembly.restriction_cloning import suggest_restriction_cloning
from services.molbio_ops import reverse_complement as rc
from test_assembly_product_content import dna
from test_cloning_connected_api_acceptance import empty_store
from test_cloning_gaps_a import genbank
from test_end_operations_and_cloning import SITES, _plasmid, _random_dna
from test_molbio_branches import make_client

BASE = "/api/molbio/assembly/restriction-cloning"
IMPORT = "/api/molbio/sequences/import"


# ── Item 1: string-keyed topology_overrides ─────────────────────────────────
@pytest_asyncio.fixture
async def import_client(tmp_path):
    engine, sessions = await empty_store(tmp_path / "g.db")
    app = FastAPI()
    app.include_router(molbio_ops.router)
    app.include_router(nucleotide_sequences.router)

    async def dep():
        async with sessions() as session:
            yield session
    app.dependency_overrides[get_molbio_session] = dep
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            yield client
    finally:
        await engine.dispose()


def browser_body(source: str, fmt: str, default: str, overrides) -> dict:
    # Exactly what utils/serverImport.ts sends: overrides keyed by record ordinal (JSON keys are strings).
    return {"source_format": fmt, "source_text": source, "topology_default": default,
            "topology_overrides": overrides, "origin_surface": "molbio", "source_provider": "upload"}


async def preview_commit(client, body, key="g1"):
    preview = await client.post(IMPORT + "/preview", content=json.dumps(body), headers={"content-type": "application/json"})
    commit = await client.post(IMPORT + "/commit", content=json.dumps({**body, "idempotency_key": key}),
                               headers={"content-type": "application/json", "Idempotency-Key": key})
    return preview, commit


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["fasta", "genbank"])
async def test_string_key_override_preview_and_commit_return_ok(import_client, fmt):
    seq = dna(91, 300)
    source = f">r\n{seq}\n" if fmt == "fasta" else genbank("OV1", seq, "linear")
    preview, commit = await preview_commit(import_client, browser_body(source, fmt, "linear", {"1": "circular"}))
    assert preview.status_code == 200, preview.text
    assert commit.status_code in (200, 201), commit.text
    rec = preview.json()["records"][0]
    assert (rec["topology"], rec["topology_source"]) == ("circular", "override")
    assert commit.json()["records"][0]["topology"] == "circular"
    assert commit.json()["records"][0]["sequence"] == seq


@pytest.mark.asyncio
async def test_browser_genbank_circular_locus_imports_circular_with_bytes_unchanged(import_client):
    seq = dna(92, 420)
    body = browser_body(genbank("LOC9", seq, "circular"), "genbank", "linear", {"1": "circular"})
    preview, commit = await preview_commit(import_client, body)
    assert preview.status_code == 200 and commit.status_code in (200, 201), (preview.text, commit.text)
    assert commit.json()["records"][0]["topology"] == "circular"
    assert commit.json()["records"][0]["sequence"] == seq


@pytest.mark.asyncio
async def test_forced_linear_override_on_circular_locus(import_client):
    seq = dna(93, 380)
    body = browser_body(genbank("LOC8", seq, "circular"), "genbank", "circular", {"1": "linear"})
    preview, commit = await preview_commit(import_client, body)
    assert preview.status_code == 200 and commit.status_code in (200, 201), (preview.text, commit.text)
    assert preview.json()["records"][0]["topology"] == "linear"
    assert commit.json()["records"][0]["topology"] == "linear"
    assert commit.json()["records"][0]["sequence"] == seq


@pytest.mark.asyncio
async def test_non_digit_override_key_is_still_rejected(import_client):
    body = browser_body(f">r\n{dna(94, 100)}\n", "fasta", "linear", {"x": "circular"})
    preview, commit = await preview_commit(import_client, body, key="g4")
    assert preview.status_code == 422 and commit.status_code == 422


@pytest.mark.asyncio
async def test_list_form_of_overrides_still_works(import_client):
    seq = dna(95, 150)
    body = browser_body(f">r\n{seq}\n", "fasta", "linear", [{"record_ordinal": 1, "topology": "circular"}])
    preview, commit = await preview_commit(import_client, body, key="g5")
    assert preview.status_code == 200 and commit.status_code in (200, 201), (preview.text, commit.text)
    assert commit.json()["records"][0]["topology"] == "circular"


# ── Items 2 and 3: ranking and duplicates ───────────────────────────────────
def eight_plasmids():
    out = []
    for seed in range(1, 9):
        rng = random.Random(1000 + seed)
        n = rng.randint(1500, 2500)
        filler = _random_dna(n, 2000 + seed, avoid=SITES.values())
        cut = rng.randint(300, 600)
        spacer = rng.randint(30, 80)
        backbone = filler[:cut] + SITES["EcoRI"] + filler[cut:cut + spacer] + SITES["BamHI"] + filler[cut + spacer:]
        payload = _random_dna(150, 3000 + seed, avoid=SITES.values())
        insert = "GCGC" + SITES["EcoRI"] + payload + SITES["BamHI"] + "GCGC"
        out.append((seed, backbone, insert, payload))
    return out


def _record_by_name():
    from services.restriction_catalog import catalog_authority
    cat = catalog_authority.require()
    return {r.canonical_name: r for r in cat.ordered_records}


@pytest.mark.parametrize("seed,backbone,insert,payload", eight_plasmids())
def test_top_three_use_only_six_plus_base_cohesive_enzymes(seed, backbone, insert, payload):
    names = _record_by_name()
    body = {"backbone_sequence": backbone, "insert_sequence": insert, "insert_start": 10, "insert_end": 160}
    top = suggest_restriction_cloning({**body, "limit": 3})["proposals"]
    assert len(top) == 3
    for proposal in top:
        for name in proposal["vector_enzymes"] + proposal["insert_enzymes"]:
            record = names[name]
            site = record.recognition.site_iupac
            assert len(site) >= 6 and set(site) <= set("ACGT"), (seed, name, site)  # independent of tier numbers
            assert all(e.overhang_kind != "blunt" for e in record.cleavage.events), (seed, name)
    # Item 3 dedupes the swapped-order twin, so the EcoRI/BamHI cloning appears once and leads.
    assert set(top[0]["vector_enzymes"]) == {"BamHI", "EcoRI"}
    wide = suggest_restriction_cloning({**body, "limit": 10})["proposals"]
    assert len(wide) >= len(top)


def test_auto_search_terminus_request_still_ok_and_classic_first():
    body = json.loads((Path(__file__).parent / "cloning_gaps_auto_search_terminus_request.json").read_text())
    names = _record_by_name()
    for limit in (3, 10):
        proposals = suggest_restriction_cloning({**body, "limit": limit})["proposals"]
        assert len(proposals) >= 3
        for proposal in proposals[:3]:
            for name in proposal["vector_enzymes"] + proposal["insert_enzymes"]:
                assert len(names[name].recognition.site_iupac) >= 6


def circ_eq(a: str, b: str) -> bool:
    return len(a) == len(b) and (a in b + b or rc(a) in b + b)


def test_no_duplicate_proposals_for_ecori_bamhi():
    backbone = _plasmid(SITES["EcoRI"] + "TTTAGC" + SITES["BamHI"], seed=5)
    payload = _random_dna(150, 11, avoid=SITES.values())
    insert = "GCGC" + SITES["EcoRI"] + payload + SITES["BamHI"] + "GCGC"
    body = {"backbone_sequence": backbone, "insert_sequence": insert, "insert_start": 10, "insert_end": 160}
    for limit in (3, 10):
        proposals = suggest_restriction_cloning({**body, "limit": limit})["proposals"]
        for i, a in enumerate(proposals):
            for b in proposals[i + 1:]:
                same_sets = (set(a["vector_enzymes"]) == set(b["vector_enzymes"])
                             and set(a["insert_enzymes"]) == set(b["insert_enzymes"]))
                assert not (same_sets and circ_eq(a["product_sequence"], b["product_sequence"])), (limit, a["vector_enzymes"], b["vector_enzymes"])
        first_two = [set(p["vector_enzymes"]) for p in proposals[:2]]
        assert first_two[0] == {"EcoRI", "BamHI"}
        assert first_two[1] != {"EcoRI", "BamHI"} or not circ_eq(proposals[0]["product_sequence"], proposals[1]["product_sequence"])


@pytest.mark.asyncio
async def test_single_enzyme_is_exposed_over_http(tmp_path):
    engine, sessions = await empty_store(tmp_path / "se.db")
    try:
        backbone = _plasmid(SITES["EcoRI"], seed=5)
        insert = "GCGC" + SITES["EcoRI"] + _random_dna(120, 8, avoid=SITES.values()) + SITES["EcoRI"] + "GCGC"
        async with make_client(sessions) as client:
            client._transport.app.include_router(molbio_end_operations.router)
            response = await client.post(BASE + "/suggest", json={
                "backbone_sequence": backbone, "insert_sequence": insert, "insert_start": 10, "insert_end": 130,
                "enzymes": ["EcoRI"], "limit": 5})
            assert response.status_code == 200, response.text
            proposals = response.json()["proposals"]
            assert proposals and all(p["single_enzyme"] is True for p in proposals)
    finally:
        await engine.dispose()
