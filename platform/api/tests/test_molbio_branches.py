"""Portable branch export/import over real routes with two scratch SQLite stores."""
from __future__ import annotations

import copy
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, text

from molbio_database import create_molbio_engine, get_molbio_session, init_molbio_db, make_molbio_session_factory
from molbio_models import (
    MolecularDocument, MolecularImportBatch, MolecularOperation, MolecularOperationInput,
    MolecularOperationOutput, MolecularRevision, NucleotideSequence,
)
from routers import molbio_branches, molbio_ops, molbio_relatives, molbio_restriction, nucleotide_sequences
from services.restriction_catalog import CatalogAuthority
from test_restriction_digest_persistence import CATALOG, MANIFEST, SCHEMA, _preview_request, _store

# One EcoRI site (G^AATTC), flanked by random-looking synthetic DNA.
SOURCE = "ACGTTGCATCGGATCCTAGCATCGACTTGAATTCAGCTAGGCTAACGTTCGATCAGTCCATG"
TABLES = (MolecularDocument, MolecularRevision, MolecularOperation, MolecularOperationInput,
          MolecularOperationOutput, MolecularImportBatch, NucleotideSequence)


def make_client(sessions):
    app = FastAPI()
    for module in (molbio_ops, molbio_restriction, molbio_relatives, nucleotide_sequences, molbio_branches):
        app.include_router(module.router)
    authority = CatalogAuthority(CATALOG, MANIFEST, SCHEMA)
    app.dependency_overrides[molbio_restriction.get_catalog_authority] = lambda: authority

    async def dependency():
        async with sessions() as session:
            yield session

    app.dependency_overrides[molbio_restriction.get_molbio_session] = dependency
    app.dependency_overrides[get_molbio_session] = dependency
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def counts(sessions):
    async with sessions() as session:
        return [(await session.execute(select(func.count()).select_from(model))).scalar_one() for model in TABLES]


async def revision_hashes(sessions):
    async with sessions() as session:
        return sorted((r.id, r.content_sha256, str(r.snapshot), str(r.provenance))
                      for r in (await session.scalars(select(MolecularRevision))).all())


def end_schema(end):
    kind = {"blunt": "blunt", "five_prime_overhang": "sticky_5", "three_prime_overhang": "sticky_3"}[end["kind"]]
    return {"type": kind, "overhang": end["overhang_sequence_5to3"] or "", "protruding_strand": end["protruding_strand"]}


async def fragment_from_item(client, item, label):
    revision = await client.get(f"/api/sequences/{item['document_id']}/revisions/{item['revision_id']}")
    return dict(id=label, name=item["name"], sequence=revision.json()["snapshot"]["sequence"],
                source_sequence_id=item["document_id"], source_revision=item["revision_number"],
                left_end=end_schema(item["left_end"]), right_end=end_schema(item["right_end"]))


async def sticky_children(client):
    items = (await client.get("/api/molbio/chooser", params={"kinds": "restriction_digest"})).json()["items"]
    return sorted(items, key=lambda item: item["name"])


@pytest.mark.asyncio
async def test_branch_round_trip_between_two_stores(tmp_path: Path):
    source_engine, source, digest = await _store(tmp_path, sequence=SOURCE)
    target_engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'target.db'}")
    await init_molbio_db(engine=target_engine)
    target = make_molbio_session_factory(target_engine)
    try:
        async with make_client(source) as src, make_client(target) as dst:
            preview = await src.post("/api/molbio/restriction/digests/simulate", json=_preview_request(digest))
            saved = await src.post("/api/molbio/restriction/digests", json={
                **_preview_request(digest), "schema": "bms.molbio.restriction-digest-save-request.v1",
                "simulation_sha256": preview.json()["simulation_sha256"], "idempotency_key": "branch-digest",
                "persistence_mode": "operation_and_fragments", "fragment_name_prefix": "Part"})
            assert saved.status_code == 200, saved.text
            children = await sticky_children(src)
            assert len(children) == 2
            # A ligation of both children, saved through the retained-child path, is the branch's last step.
            joined = await src.post("/api/molbio/assembly/ligation/save", json=dict(
                fragments=[await fragment_from_item(src, children[0], "a"), await fragment_from_item(src, children[1], "b")],
                circular=False, new_name="Rejoined"))
            assert joined.status_code == 200, joined.text
            product_id = joined.json()["saved_sequence"]["id"]
            assert joined.json()["saved_sequence"]["sequence"] == SOURCE
            async with source() as session:
                product_rev = (await session.scalars(select(MolecularRevision).where(
                    MolecularRevision.document_id == product_id))).one().id

            before_counts, before_hashes = await counts(source), await revision_hashes(source)
            exported = await src.post("/api/molbio/branches/export", json={"revision_ids": [product_rev]})
            assert exported.status_code == 200, exported.text
            bundle = exported.json()
            assert await counts(source) == before_counts and await revision_hashes(source) == before_hashes
            assert bundle["format"] == "molbio-branch" and bundle["selected"] == [product_rev]
            assert {op["kind"] for op in bundle["operations"]} == {"restriction_digest", "ligation"}
            assert any(doc["projection"] for doc in bundle["documents"])

            first = await dst.post("/api/molbio/branches/import", json={"bundle": bundle, "idempotency_key": "k1"})
            assert first.status_code == 200, first.text
            result = first.json()
            assert result["already_imported"] is False
            assert result["created"] == {"documents": len(bundle["documents"]), "revisions": len(bundle["revisions"]),
                                         "operations": len(bundle["operations"])}
            rev_map = result["id_map"]["revisions"]
            assert result["selected"] == [rev_map[product_rev]]
            after_import = await counts(target)

            # Children kept sequence, ends, phosphate state and parent edges; originals recorded.
            src_rows, dst_rows = {}, {}
            async with source() as s1, target() as s2:
                for old, new in rev_map.items():
                    src_rows[old] = await s1.get(MolecularRevision, old)
                    dst_rows[old] = await s2.get(MolecularRevision, new)
                for old, row in dst_rows.items():
                    assert row.snapshot["sequence"] == src_rows[old].snapshot["sequence"]
                    assert row.snapshot.get("features") == src_rows[old].snapshot.get("features")
                    assert row.content_sha256 == src_rows[old].content_sha256
                    assert row.provenance["imported_from"]["revision_id"] == old
                    assert {k: v for k, v in row.provenance.items() if k != "imported_from"} == src_rows[old].provenance
                digest_children = [r for r in src_rows.values() if r.provenance.get("geometry")]
                assert digest_children
                # no digest result row is fabricated
                assert (await s2.execute(text("SELECT count(*) FROM restriction_digest_results"))).scalar_one() == 0
                new_edges = (await s2.scalars(select(MolecularOperationInput))).all()
                old_edges = (await s1.scalars(select(MolecularOperationInput))).all()
                inv = {v: k for k, v in rev_map.items()}
                assert sorted((inv[e.revision_id], e.role, e.position) for e in new_edges) == sorted(
                    (e.revision_id, e.role, e.position) for e in old_edges if e.revision_id in rev_map)
                op = (await s2.scalars(select(MolecularOperation).where(MolecularOperation.operation_kind == "ligation"))).one()
                assert op.provenance["imported_from"]["operation_id"] in result["id_map"]["operations"]

            # Chooser lists imported children (retained) and the shelf projection arrives only where it existed.
            imported = await sticky_children(dst)
            assert [i["name"] for i in imported] == [c["name"] for c in children]
            assert [(i["left_end"], i["right_end"]) for i in imported] == [(c["left_end"], c["right_end"]) for c in children]
            assert all(i["origin"] == "retained" and i["sequence_id"] is None for i in imported)
            shelf_names = {i["name"] for i in (await dst.get("/api/molbio/chooser")).json()["items"] if i["origin"] == "shelf"}
            assert shelf_names == {"Rejoined"}

            # An assembly from the imported fragments saves and reopens with the independent expectation.
            again = await dst.post("/api/molbio/assembly/ligation/save", json=dict(
                fragments=[await fragment_from_item(dst, imported[0], "a"), await fragment_from_item(dst, imported[1], "b")],
                circular=False, new_name="Rebuilt"))
            assert again.status_code == 200, again.text
            reopened = await dst.get(f"/api/sequences/{again.json()['saved_sequence']['id']}")
            assert reopened.json()["sequence"] == SOURCE

            # Same bundle again: earlier result, nothing created.
            settled = await counts(target)
            twice = await dst.post("/api/molbio/branches/import", json={"bundle": bundle, "idempotency_key": "k1"})
            other_key = await dst.post("/api/molbio/branches/import", json={"bundle": bundle})
            for response in (twice, other_key):
                assert response.status_code == 200 and response.json()["already_imported"] is True
                assert response.json()["id_map"] == result["id_map"] and response.json()["selected"] == result["selected"]
            assert await counts(target) == settled
            assert settled[5] == after_import[5] == 1

            # A bundle that references a revision it does not carry is rejected and writes nothing.
            broken = copy.deepcopy(bundle)
            broken["revisions"] = [r for r in broken["revisions"] if r["id"] != children[0]["revision_id"]]
            before = await counts(target)
            rejected = await dst.post("/api/molbio/branches/import", json={"bundle": broken})
            assert rejected.status_code == 422 and "not in the bundle" in rejected.json()["detail"]
            assert await counts(target) == before
            missing = await src.post("/api/molbio/branches/export", json={"revision_ids": [str(uuid.uuid4())]})
            assert missing.status_code == 404
    finally:
        await source_engine.dispose()
        await target_engine.dispose()
