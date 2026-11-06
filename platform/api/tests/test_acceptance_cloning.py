"""Acceptance cases not covered elsewhere (real routes, scratch SQLite, hand-built generic sequences)."""
from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import select

from molbio_models import MolecularOperationInput, MolecularOperationOutput, MolecularRevision
from routers import molbio_ops
from test_assembly_product_content import client_with_store, dna, extract, feature, ligate, rc, seed, source_fragment


def sticky(strand):
    return {"type": "sticky_5", "overhang": "GATC", "protruding_strand": strand}


def hybrid_fragments(metadata=True):
    ends = {"left": ["BamHI"], "right": ["BamHI"]}, {"left": ["BglII"], "right": ["BglII"]}
    return [
        dict(id="v", name="vector", sequence="GATC" + "CAAAAGGTACCTTTT" + "G", left_end=sticky("top"), right_end=sticky("bottom"),
             **({"metadata": {"end_enzymes": ends[0]}} if metadata else {})),
        dict(id="i", name="insert", sequence="GATC" + "TTTACCATGGTTT" + "A", left_end=sticky("top"), right_end=sticky("bottom"),
             **({"metadata": {"end_enzymes": ends[1]}} if metadata else {})),
    ]


# ── A4d through the real ligation route ──────────────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["simulate", "save"])
async def test_a4d_route_reports_hybrid_junction_not_cleavable(tmp_path, route):
    async with client_with_store(tmp_path) as (client, _):
        body = dict(fragments=hybrid_fragments(), circular=False, observe_enzymes=["BamHI", "BglII"])
        response = await client.post(f"/api/molbio/assembly/ligation/{route}", json=body)
        assert response.status_code == 200, response.text
        assert "not cleavable by either enzyme" in response.json()["junction_sites"][0]["note"]


@pytest.mark.asyncio
async def test_a4d_observation_is_omitted_when_not_requested_and_never_changes_the_product(tmp_path):
    async with client_with_store(tmp_path) as (client, _):
        plain = await client.post("/api/molbio/assembly/ligation/simulate", json=dict(fragments=hybrid_fragments(), circular=False))
        observed = await client.post("/api/molbio/assembly/ligation/simulate",
                                     json=dict(fragments=hybrid_fragments(), circular=False, observe_enzymes=["BamHI"]))
        assert "junction_sites" not in plain.json() and observed.json()["product"] == plain.json()["product"]


# ── A1a: marker swap, plain cassette, through the real design route, Save, reopen ─
BB, OLD_MARKER, CASSETTE = dna(11, 1200), dna(12, 600), dna(13, 500)


async def design_and_save(client, payload, name="Swap"):
    design = await client.post("/api/molbio/assembly/gibson/design", json=payload)
    assert design.status_code == 200, design.text
    body = design.json()
    saved = await client.post("/api/molbio/assembly/gibson/design/save", json={
        **payload, "computation_id": body["computation_id"],
        "selected_candidate_checksum": body["selected_candidate_checksum"], "new_name": name})
    assert saved.status_code == 200, saved.text
    return body, saved.json()["saved_sequence"]


async def marker_swap(client, sessions):
    recipient = BB + OLD_MARKER
    features = [feature("ori", 100, 400), feature("old marker", 1300, 1500)]
    recipient_id = await seed(sessions, recipient, circular=True, features=features, name="recipient")
    payload = {"fragments": [
        {"id": "bb", "name": "backbone", "sequence": BB, "preparation": "pcr",
         "source_sequence_id": recipient_id, "source_revision": 1, "source_start": 0, "source_end": 1200},
        {"id": "cas", "name": "cassette", "sequence": CASSETTE, "preparation": "ready_linear"}],
        "circular": True, "overlap": 30, "target_tm": 60.0, "min_anneal": 13}
    return recipient_id, payload


@pytest.mark.asyncio
async def test_a1a_product_is_backbone_plus_cassette_with_backbone_primers(tmp_path):
    async with client_with_store(tmp_path) as (client, sessions):
        _id, payload = await marker_swap(client, sessions)
        body, saved = await design_and_save(client, payload)
        assert (BB + CASSETTE) in saved["sequence"] * 2 and len(saved["sequence"]) == len(BB + CASSETTE)
        assert {p["fragment_id"] for p in body["primers"]} == {"bb"}  # the plain cassette needs no primers


@pytest.mark.asyncio
async def test_a1a_saved_product_keeps_the_recipients_retained_features_on_the_backbone(tmp_path):
    async with client_with_store(tmp_path) as (client, sessions):
        _id, payload = await marker_swap(client, sessions)
        _body, saved = await design_and_save(client, payload)
        reopened = (await client.get(f"/api/sequences/{saved['id']}")).json()
        names = [f["name"] for f in reopened["features"]]
        assert names == ["ori"] and extract(reopened["sequence"], reopened["features"][0]) == BB[100:400]


@pytest.mark.asyncio
async def test_a1a_primers_have_mapped_sites_or_need_review(tmp_path):
    async with client_with_store(tmp_path) as (client, sessions):
        _id, payload = await marker_swap(client, sessions)
        _body, saved = await design_and_save(client, payload)
        assert all(p["end"] > p["start"] or p.get("binding_status") == "needs_review" for p in saved["primers"])


@pytest.mark.asyncio
async def test_a1a_amplicon_child_is_a_usable_source(tmp_path):
    async with client_with_store(tmp_path) as (client, sessions):
        _id, payload = await marker_swap(client, sessions)
        body, saved = await design_and_save(client, payload)
        amplicon = next(d for d in body["designed_fragments"] if d["id"] == "bb")["sequence"]
        async with sessions() as session:
            output = (await session.execute(select(MolecularOperationOutput).where(
                MolecularOperationOutput.role == "designed_fragment"))).scalars().one()
        child_id = output.snapshot["document_id"]
        response = await ligate(client, [source_fragment("a", child_id, amplicon, 0, len(amplicon))])
        assert response.status_code == 200 and response.json()["saved_sequence"]["sequence"] == amplicon


@pytest.mark.asyncio
async def test_a1a_cassette_is_a_usable_source_child(tmp_path):
    async with client_with_store(tmp_path) as (client, sessions):
        _id, payload = await marker_swap(client, sessions)
        await design_and_save(client, payload)
        async with sessions() as session:
            revision = (await session.execute(select(MolecularRevision).where(
                MolecularRevision.content_sha256 == hashlib.sha256(CASSETTE.encode()).hexdigest()))).scalars().first()
        response = await ligate(client, [source_fragment("c", revision.document_id, CASSETTE, 0, len(CASSETTE))])
        assert response.status_code == 200 and response.json()["saved_sequence"]["sequence"] == CASSETTE


# ── A1e: recalculating makes a new result and leaves the retained one unchanged ─
@pytest.mark.asyncio
async def test_a1e_recalculating_makes_a_new_result_and_leaves_the_saved_one_unchanged(tmp_path):
    async with client_with_store(tmp_path) as (client, sessions):
        _id, payload = await marker_swap(client, sessions)
        first, saved = await design_and_save(client, payload)
        before = (await client.get(f"/api/sequences/{saved['id']}")).json()
        second = (await client.post("/api/molbio/assembly/gibson/design", json={**payload, "overlap": 25})).json()
        after = (await client.get(f"/api/sequences/{saved['id']}")).json()
        assert second["computation_id"] != first["computation_id"] and after == before


# ── A3c: a retained child plus another source, Save, reopen ──────────────────
@pytest.mark.asyncio
async def test_a3c_child_plus_other_source_keeps_bases_annotations_and_both_parent_revisions(tmp_path):
    async with client_with_store(tmp_path) as (client, sessions):
        child_seq, other_seq = dna(21, 120), dna(22, 90)
        child_id = await seed(sessions, child_seq, features=[feature("tag", 20, 50, strand=-1)], shelf=False, name="digest child")
        other_id = await seed(sessions, other_seq, features=[feature("gene", 10, 40)], name="other")
        response = await ligate(client, [source_fragment("c", child_id, child_seq, 0, 120), source_fragment("o", other_id, other_seq, 0, 90)])
        assert response.status_code == 200, response.text
        saved = response.json()["saved_sequence"]
        reopened = (await client.get(f"/api/sequences/{saved['id']}")).json()
        product = child_seq + other_seq
        assert reopened["sequence"] == product
        assert {f["name"]: extract(product, f) for f in reopened["features"]} == {"tag": extract(child_seq, feature("tag", 20, 50, strand=-1)), "gene": other_seq[10:40]}
        async with sessions() as session:
            parents = (await session.execute(select(MolecularRevision.document_id).join(
                MolecularOperationInput, MolecularOperationInput.revision_id == MolecularRevision.id).where(
                MolecularOperationInput.role == "fragment"))).scalars().all()
        assert sorted(parents) == sorted([child_id, other_id])


# ── A5e: tailed versus payload PCR length, against the part's own region ─────
def test_a5e_pcr_lengths_are_payload_region_and_tailed_product():
    from test_golden_gate_suggest import req, suggest_golden_gate
    proposal = suggest_golden_gate(req(preparation_kind="pcr", enzymes=["BsaI"])).proposals[0]
    lengths = {x["part_id"]: x for x in proposal.pcr_products}
    prepared = {o.part_id: o for o in proposal.candidate.design.preparations}
    materials = {m.id: m for m in proposal.candidate.design.materials}
    assert all(x["tailed_length"] == len(materials[prepared[pid].prepared_material_id].sequence) and x["tailed_length"] > x["payload_length"]
               for pid, x in lengths.items()) and lengths


# ── A7a: deriving from an older revision leaves the parent unchanged; every source is discoverable ─
@pytest.mark.asyncio
async def test_a7a_derive_from_older_revision_leaves_parent_and_lists_every_source(tmp_path):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from routers import molbio_relatives
    async with client_with_store(tmp_path) as (client, sessions):
        old, new, other = dna(31, 100), dna(32, 100), dna(33, 80)
        parent_id = await seed(sessions, old, name="parent")
        await client.put(f"/api/sequences/{parent_id}", json={"sequence": new})
        other_id = await seed(sessions, other, name="other")
        response = await ligate(client, [source_fragment("p", parent_id, old[:60], 0, 60), source_fragment("o", other_id, other, 0, 80)])
        assert response.status_code == 200, response.text
        saved = response.json()["saved_sequence"]
        assert saved["sequence"] == old[:60] + other
        current = (await client.get(f"/api/sequences/{parent_id}")).json()
        async with sessions() as session:
            revisions = (await session.execute(select(MolecularRevision).where(MolecularRevision.document_id == parent_id))).scalars().all()
            product_revision = (await session.execute(select(MolecularRevision).where(MolecularRevision.document_id == saved["id"]))).scalars().first()
            edges = (await session.execute(select(MolecularOperationInput.revision_id).where(MolecularOperationInput.role == "fragment"))).scalars().all()
        older = next(r for r in revisions if r.revision_number == 1)
        assert current["sequence"] == new and len(revisions) == 2 and older.id in edges
        from fastapi import FastAPI
        app = FastAPI(); app.include_router(molbio_relatives.router)
        from molbio_database import get_molbio_session
        async def dep():
            async with sessions() as s:
                yield s
        app.dependency_overrides[get_molbio_session] = dep
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as relatives:
            body = (await relatives.get(f"/api/molbio/revisions/{product_revision.id}/relatives")).json()
        assert sorted(e["document_id"] for e in body["sources"][0]["inputs"]) == sorted([parent_id, other_id])


# ── A8a: inline_dna and molecular_revision both work (digest and PCR) ────────
@pytest.mark.asyncio
async def test_a8a_digest_inline_and_revision_sources_give_the_same_fragments(tmp_path):
    from test_restriction_digest_persistence import _client, _preview_request, _store
    sequence = "TTGAATTCAAGGATCCTTGAATTCAAA"
    engine, sessions, digest = await _store(tmp_path, sequence=sequence)
    try:
        async with await _client(sessions) as client:
            by_revision = _preview_request(digest)
            inline = {**by_revision, "source": {"kind": "inline_dna", "name": "inline", "dna": sequence, "topology": "linear"}}
            first = await client.post("/api/molbio/restriction/digests/simulate", json=by_revision)
            second = await client.post("/api/molbio/restriction/digests/simulate", json=inline)
            assert first.status_code == 200 and second.status_code == 200, second.text
            assert [f["top_strand_sequence"] for f in first.json()["fragments"]] == [f["top_strand_sequence"] for f in second.json()["fragments"]]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a8a_pcr_inline_and_saved_template_give_the_same_product(tmp_path):
    async with client_with_store(tmp_path) as (client, sessions):
        template = dna(41, 300)
        template_id = await seed(sessions, template, name="template")
        primers = dict(primer_fwd=template[50:75], primer_rev=rc(template[200:225]), save=False, persist_experiment=False)
        saved = await client.post("/api/molbio/pcr", json={"sequence_id": template_id, **primers})
        inline = await client.post("/api/molbio/pcr", json={"sequence": template, "sequence_type": "dna", **primers})
        assert saved.status_code == 200 and inline.status_code == 200, inline.text
        assert saved.json()["product"]["sequence"] == inline.json()["product"]["sequence"] == template[50:225]


# ── A8c: Save and reopen do not re-run the planner ───────────────────────────
@pytest.mark.asyncio
async def test_a8c_gibson_save_and_reopen_do_not_rerun_the_planner(tmp_path, monkeypatch):
    async with client_with_store(tmp_path) as (client, sessions):
        _id, payload = await marker_swap(client, sessions)
        design = (await client.post("/api/molbio/assembly/gibson/design", json=payload)).json()
        def forbidden(*args, **kwargs):
            raise AssertionError("planner re-run")
        monkeypatch.setattr(molbio_ops, "_execute_gibson_design", forbidden)
        monkeypatch.setattr(molbio_ops, "design_gibson", forbidden)
        saved = await client.post("/api/molbio/assembly/gibson/design/save", json={
            **payload, "computation_id": design["computation_id"], "selected_candidate_checksum": design["selected_candidate_checksum"]})
        assert saved.status_code == 200, saved.text
        reopened = await client.get(f"/api/sequences/{saved.json()['saved_sequence']['id']}")
        assert reopened.status_code == 200 and reopened.json()["sequence"] == saved.json()["saved_sequence"]["sequence"]
