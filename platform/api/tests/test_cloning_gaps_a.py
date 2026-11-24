"""Lane A: saved assembly products are digestable; GenBank import honours LOCUS topology.

Real HTTP (ASGI) against migrated scratch SQLite; expected values are recomputed from raw inputs.
"""
from __future__ import annotations

import re
import uuid

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select, text

from molbio_database import get_molbio_session
from molbio_models import MolecularRevision, NucleotideSequence
from routers import molbio_ops, nucleotide_sequences
from services.molbio_persistence import record_sequence_revision
from test_acceptance_cloning import CASSETTE, design_and_save, marker_swap
from test_assembly_product_content import dna, seed, source_fragment
from test_cloning_connected_api_acceptance import empty_store
from test_molbio_branches import make_client
from test_restriction_digest_persistence import CATALOG_ID, CATALOG_SHA

ECORI = "GAATTC"


def site_starts(sequence: str, motif: str, circular: bool) -> list[int]:
    haystack = sequence + sequence[: len(motif) - 1] if circular else sequence
    return [m.start() for m in re.finditer(f"(?={motif})", haystack) if m.start() < len(sequence)]


async def digest_saved(client, sessions, document_id: str, enzyme: str = "EcoRI"):
    """Digest exactly as the panel does: by the saved product's source reference."""
    async with sessions() as session:
        revision = (await session.scalars(select(MolecularRevision).where(
            MolecularRevision.document_id == document_id))).first()
        stored = (await session.execute(text(
            "SELECT molecule_strandedness FROM nucleotide_sequences WHERE id = :i"), {"i": document_id})).scalar_one()
    response = await client.post("/api/molbio/restriction/digests/simulate", json={
        "schema": "bms.molbio.restriction-digest-simulation-request.v1",
        "source": {"kind": "molecular_revision", "sequence_id": document_id, "revision_id": revision.id,
                   "expected_content_sha256": revision.content_sha256},
        "catalog": {"catalog_id": CATALOG_ID, "expected_catalog_sha256": CATALOG_SHA},
        "enzyme_ids": [enzyme]})
    return response, stored, revision


def assert_digest(response, sequence: str, motif: str, circular: bool) -> None:
    assert response.status_code == 200, response.text
    sites = site_starts(sequence, motif, circular)
    assert sites, "fixture product must contain the recognition site"
    fragments = response.json()["fragments"]
    assert len(fragments) == (len(sites) if circular else len(sites) + 1)
    # Cut G^AATTC: top-strand cut is one base after each site start.
    lengths = sorted(len(f["top_strand_sequence"]) for f in fragments)
    cuts = sorted(s + 1 for s in sites)
    if circular and len(cuts) == 1:
        expected = [len(sequence)]
    elif circular:
        expected = sorted([(b - a) for a, b in zip(cuts, cuts[1:])] + [len(sequence) - cuts[-1] + cuts[0]])
    else:
        expected = sorted(b - a for a, b in zip([0] + cuts, cuts + [len(sequence)]))
    assert lengths == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("circular", [False, True])
async def test_ligation_save_is_digestable(tmp_path, circular):
    engine, sessions = await empty_store(tmp_path / "lig.db")
    try:
        a = dna(51, 80) + ECORI + dna(52, 60)
        b = dna(53, 90)
        async with make_client(sessions) as client:
            a_id, b_id = await seed(sessions, a, name="a"), await seed(sessions, b, name="b")
            saved = await client.post("/api/molbio/assembly/ligation/save", json=dict(circular=circular, fragments=[
                source_fragment("a", a_id, a, 0, len(a)), source_fragment("b", b_id, b, 0, len(b))]))
            assert saved.status_code == 200, saved.text
            product = saved.json()["saved_sequence"]
            assert product["sequence"] == a + b
            response, stored, _ = await digest_saved(client, sessions, product["id"])
            assert stored == "double"
            assert_digest(response, a + b, ECORI, circular)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_gibson_save_is_digestable(tmp_path):
    engine, sessions = await empty_store(tmp_path / "gib.db")
    try:
        overlap = dna(61, 30)
        left, right = dna(62, 100) + ECORI + overlap, overlap + dna(63, 100)
        async with make_client(sessions) as client:
            saved = await client.post("/api/molbio/assembly/gibson/save", json=dict(circular=False, fragments=[
                dict(id="l", name="l", sequence=left), dict(id="r", name="r", sequence=right)]))
            assert saved.status_code == 200, saved.text
            product = saved.json()["saved_sequence"]
            assert product["sequence"] == left + right[len(overlap):]
            response, stored, _ = await digest_saved(client, sessions, product["id"])
            assert stored == "double"
            assert_digest(response, product["sequence"], ECORI, False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_gibson_design_save_is_digestable(tmp_path):
    engine, sessions = await empty_store(tmp_path / "gd.db")
    try:
        async with make_client(sessions) as client:
            _, payload = await marker_swap(client, sessions)
            payload["fragments"][1]["sequence"] = CASSETTE[:200] + ECORI + CASSETTE[200:]
            _, saved = await design_and_save(client, payload)
            response, stored, _ = await digest_saved(client, sessions, saved["id"])
            assert stored == "double"
            assert_digest(response, saved["sequence"], ECORI, True)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_single_stranded_input_is_not_claimed_double(tmp_path):
    engine, sessions = await empty_store(tmp_path / "ss.db")
    try:
        seq = dna(71, 100)
        row = NucleotideSequence(id=str(uuid.uuid4()), name="ss", sequence=seq, sequence_type="dna",
                                 molecule_strandedness="single", is_circular=False, length=100,
                                 features=[], primers=[], version=1)
        async with sessions.begin() as session:
            session.add(row)
            await record_sequence_revision(session, row, change_kind="created")
        async with make_client(sessions) as client:
            saved = await client.post("/api/molbio/assembly/ligation/save", json=dict(circular=False, fragments=[
                source_fragment("s", row.id, seq, 0, 100)]))
            assert saved.status_code == 200, saved.text
            async with sessions() as session:
                stored = (await session.execute(text(
                    "SELECT molecule_strandedness FROM nucleotide_sequences WHERE id=:i"),
                    {"i": saved.json()["saved_sequence"]["id"]})).scalar_one()
            assert stored == "single"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_golden_gate_design_save_is_digestable(tmp_path):
    from test_golden_gate_append_only_save import BASE, native_store
    from test_golden_gate_wire_receiving import fixture
    from routers import molbio_restriction
    from services.assembly.golden_gate_workflow import freeze_selection
    from services.assembly.golden_gate_workflow_types import SaveDesignRequest, WorkflowResult
    from services.restriction_catalog import CatalogAuthority
    from test_restriction_digest_persistence import CATALOG, MANIFEST, SCHEMA
    async with native_store(tmp_path / "gg.db", initialize=True) as (client, sessions):
        app = client._transport.app
        app.include_router(molbio_restriction.router)
        authority = CatalogAuthority(CATALOG, MANIFEST, SCHEMA)
        app.dependency_overrides[molbio_restriction.get_catalog_authority] = lambda: authority
        app.dependency_overrides[molbio_restriction.get_molbio_session] = app.dependency_overrides[get_molbio_session]
        preview = await client.post(BASE, json=fixture("split"))
        assert preview.status_code == 200, preview.text
        result = WorkflowResult.model_validate(preview.json())
        save = SaveDesignRequest(selection=freeze_selection(result, result.selected_solution_id),
                                 name="gg-digest", idempotency_key="gg-digest").model_dump(mode="json")
        saved = await client.post(BASE + "/save", json=save)
        assert saved.status_code == 200, saved.text
        product_id = saved.json()["product_document_id"]
        _, stored, revision = await digest_saved(client, sessions, product_id, "EcoRI")
        assert stored == "double" and revision.snapshot["molecule_strandedness"] == "double"
        sequence, circular = revision.snapshot["sequence"], bool(revision.snapshot["is_circular"])
        # Golden Gate products have their BsaI sites consumed; digest with a palindromic
        # enzyme whose site really occurs in the product (found by plain string search).
        palindromes = {"EcoRI": "GAATTC", "BamHI": "GGATCC", "HindIII": "AAGCTT", "XhoI": "CTCGAG",
                       "NcoI": "CCATGG", "KpnI": "GGTACC", "SacI": "GAGCTC", "PstI": "CTGCAG",
                       "SalI": "GTCGAC", "XbaI": "TCTAGA", "NdeI": "CATATG", "SpeI": "ACTAGT"}
        enzyme, motif = next(((e, m) for e, m in palindromes.items() if site_starts(sequence, m, circular)), (None, None))
        assert enzyme, "fixture product has no usable palindromic site"
        response, _, _ = await digest_saved(client, sessions, product_id, enzyme)
        assert response.status_code == 200, response.text
        sites = site_starts(sequence, motif, circular)
        assert len(response.json()["fragments"]) == (len(sites) if circular else len(sites) + 1)


# ── Item 2: GenBank LOCUS topology ──────────────────────────────────────────
def genbank(name, sequence, topology, feature=None):
    rows = "".join(f"{i + 1:>9} " + " ".join(sequence[i:i + 60][j:j + 10] for j in range(0, 60, 10)).lower() + "\n"
                   for i in range(0, len(sequence), 60))
    feats = (f"FEATURES             Location/Qualifiers\n     misc_feature    {feature}\n"
             "                     /label=wrap\n") if feature else ""
    return (f"LOCUS       {name:<16} {len(sequence)} bp    DNA     {topology:<8} SYN 01-JAN-2000\n"
            f"DEFINITION  synthetic.\nACCESSION   {name}\nVERSION     {name}.1\n{feats}ORIGIN\n{rows}//\n")


async def run_import(client, source, fmt, default, key="k", overrides=None):
    payload = {"source_format": fmt, "source_text": source, "topology_default": default,
               "origin_surface": "ngs", "source_provider": "paste"}
    if overrides:
        payload["topology_overrides"] = overrides
    preview = await client.post("/api/molbio/sequences/import/preview", json=payload)
    assert preview.status_code == 200, preview.text
    commit = await client.post("/api/molbio/sequences/import/commit",
                               json={**payload, "idempotency_key": key})
    assert commit.status_code == 201, commit.text
    return preview.json(), commit.json()


@pytest_asyncio.fixture
async def import_client(tmp_path):
    engine, sessions = await empty_store(tmp_path / "imp.db")
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


@pytest.mark.asyncio
@pytest.mark.parametrize("locus,default", [
    ("circular", "linear"), ("linear", "circular"), ("circular", "circular"), ("linear", "linear")])
async def test_genbank_locus_topology_wins_over_default(import_client, locus, default):
    seq = dna(81, 420)
    preview, commit = await run_import(import_client, genbank("LOC1", seq, locus), "genbank", default)
    assert [r["topology"] for r in preview["records"]] == [locus]
    assert preview["records"][0]["topology_source"] == "file"
    assert [r["topology"] for r in commit["records"]] == [locus]
    assert commit["records"][0]["sequence"] == seq


@pytest.mark.asyncio
@pytest.mark.parametrize("default", ["linear", "circular"])
async def test_fasta_uses_the_default_topology(import_client, default):
    seq = dna(82, 100)
    preview, commit = await run_import(import_client, f">f\n{seq}\n", "fasta", default)
    assert preview["records"][0]["topology"] == default
    assert preview["records"][0]["topology_source"] == "default"
    assert commit["records"][0]["topology"] == default and commit["records"][0]["sequence"] == seq


@pytest.mark.asyncio
async def test_mixed_multi_record_file_and_explicit_override_wins(import_client):
    a, b = dna(83, 120), dna(84, 130)
    source = genbank("MIXA", a, "circular") + genbank("MIXB", b, "linear")
    _, commit = await run_import(import_client, source, "genbank", "linear")
    assert [r["topology"] for r in commit["records"]] == ["circular", "linear"]
    assert [r["sequence"] for r in commit["records"]] == [a, b]
    over, _ = await run_import(import_client, source, "genbank", "linear", key="k2",
                               overrides=[{"record_ordinal": 1, "topology": "linear"}, {"record_ordinal": 2, "topology": "circular"}])
    assert [(r["topology"], r["topology_source"]) for r in over["records"]] == [
        ("linear", "override"), ("circular", "override")]


@pytest.mark.asyncio
async def test_wraparound_feature_stays_exact_on_circular_locus(import_client):
    seq = dna(85, 418)
    _, commit = await run_import(import_client, genbank("WRAP", seq, "circular", "join(397..418,1..15)"),
                                 "genbank", "linear")
    record = commit["records"][0]
    assert record["topology"] == "circular" and record["sequence"] == seq
    segments = record["features"][0]["segments"]
    assert [(s["start"], s["end"]) for s in segments] == [(396, 418), (0, 15)]
