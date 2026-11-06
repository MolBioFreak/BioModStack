"""Connected cloning acceptance: real computation/routes, migrated disposable stores.

All DNA is generic seeded synthetic fixture material, never live records.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select, text

from molbio_database import create_molbio_engine, init_molbio_db, make_molbio_session_factory
from molbio_models import MolecularOperationInput, MolecularOperationOutput, MolecularRevision
from routers import molbio_end_operations
from test_acceptance_cloning import BB, CASSETTE, design_and_save, marker_swap
from test_assembly_product_content import extract, feature, ligate, seed, source_fragment
from test_molbio_branches import SOURCE, counts, fragment_from_item, make_client, sticky_children
from test_restriction_digest_persistence import _preview_request, _store


async def empty_store(path):
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{path}")
    await init_molbio_db(engine=engine)
    return engine, make_molbio_session_factory(engine)


@pytest.mark.asyncio
@pytest.mark.parametrize("preoverlapped", [False, True])
async def test_mixed_gibson_reconnect_children_and_reuse(tmp_path, preoverlapped):
    engine, sessions = await empty_store(tmp_path / "mixed.db")
    try:
        async with make_client(sessions) as client:
            _, payload = await marker_swap(client, sessions)
            if preoverlapped:
                payload["fragments"][1]["sequence"] = BB[-30:] + CASSETTE + BB[:30]
            offset = 30 if preoverlapped else 0
            payload["fragments"][1]["features"] = [feature("synthetic tag", offset + 40, offset + 80)]
            body, saved = await design_and_save(client, payload)
            assert len(saved["sequence"]) == len(BB + CASSETTE)
            assert BB + CASSETTE in saved["sequence"] * 2
            assert {f["name"]: extract(saved["sequence"], f) for f in saved["features"]} == {
                "ori": BB[100:400], "synthetic tag": CASSETTE[40:80]}
            assert len(saved["primers"]) == 2
            for primer in saved["primers"]:
                site = extract(saved["sequence"], primer)
                assert site == primer["notes"]["annealing_sequence"]
        await engine.dispose()
        async with make_client(sessions) as client:
            reopened = await client.get(f"/api/sequences/{saved['id']}")
            assert reopened.status_code == 200 and reopened.json()["sequence"] == saved["sequence"]
            async with sessions() as session:
                outputs = (await session.scalars(select(MolecularOperationOutput).where(
                    MolecularOperationOutput.role == "designed_fragment"))).all()
                children = [await session.get(MolecularRevision, o.revision_id) for o in outputs]
                product = (await session.scalars(select(MolecularRevision).where(
                    MolecularRevision.document_id == saved["id"]))).one()
            assert {r.provenance["preparation"] for r in children} == {"pcr", "ready_linear"}
            assert {r.snapshot["sequence"] for r in children} == {d["sequence"] for d in body["designed_fragments"]}
            chooser = (await client.get("/api/molbio/chooser")).json()["items"]
            assert {r.id for r in children} <= {i["revision_id"] for i in chooser}
            relatives = await client.get(f"/api/molbio/revisions/{product.id}/relatives")
            assert relatives.status_code == 200
            assert all(r.id in relatives.text for r in children)
            for child in children:
                exact = await client.get(f"/api/sequences/{child.document_id}/revisions/{child.id}")
                assert exact.status_code == 200
                sequence = exact.json()["snapshot"]["sequence"]
                expected = ({"ori": BB[100:400]} if child.provenance["preparation"] == "pcr"
                            else {"synthetic tag": CASSETTE[40:80]})
                assert {f["name"]: extract(sequence, f) for f in child.snapshot["features"]} == expected
                response = await ligate(client, [source_fragment("reuse", child.document_id, sequence, 0, len(sequence))])
                assert response.status_code == 200, response.text
                assert response.json()["saved_sequence"]["sequence"] == sequence
                assert {f["name"]: extract(sequence, f) for f in response.json()["saved_sequence"]["features"]} == expected
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_applied_digest_phosphate_and_portable_assembly(tmp_path):
    snapshot = {"sequence_type": "dna", "sequence": SOURCE, "is_circular": False,
                "molecule_strandedness": "double", "features": [feature("generic tag", 3, 14)]}
    engine, sessions, digest = await _store(tmp_path, sequence=SOURCE, source_snapshot=snapshot)
    target_engine, target = await empty_store(tmp_path / "second.db")
    try:
        async with make_client(sessions) as client, make_client(target) as dst:
            client._transport.app.include_router(molbio_end_operations.router)
            preview = await client.post("/api/molbio/restriction/digests/simulate", json=_preview_request(digest))
            saved = await client.post("/api/molbio/restriction/digests", json={
                **_preview_request(digest), "schema": "bms.molbio.restriction-digest-save-request.v1",
                "simulation_sha256": preview.json()["simulation_sha256"], "idempotency_key": "connected-digest",
                "persistence_mode": "operation_and_fragments", "fragment_name_prefix": "Generic"})
            assert saved.status_code == 200, saved.text
            children = await sticky_children(client)
            original = await fragment_from_item(client, children[0], "a")
            previous = children[0]["revision_id"]
            for operation, state in [("dephosphorylate", "unphosphorylated"), ("phosphorylate", "phosphorylated")]:
                response = await client.post("/api/molbio/end-operations/apply", json={
                    "source": {"kind": "molecular_revision", "revision_id": previous}, "operation": operation})
                assert response.status_code == 200, response.text
                treated = response.json()
                assert treated["sequence"] == original["sequence"]
                for side in ("left_end", "right_end"):
                    assert treated[side]["five_prime_phosphate"] == state
                    assert {k: treated[side][k] for k in original[side]} == original[side]
                exact = await client.get(f"/api/sequences/{treated['document_id']}/revisions/{treated['revision_id']}")
                assert exact.status_code == 200
                assert exact.json()["snapshot"]["sequence"] == original["sequence"]
                assert exact.json()["provenance"]["source_revision_id"] == previous
                for side in ("left_end", "right_end"):
                    physical = exact.json()["provenance"]["physical_ends"][side]
                    assert physical["five_prime_phosphate"] == state
                    assert (physical["overhang_sequence_5to3"] or "") == treated[side]["overhang"]
                    assert physical["protruding_strand"] == treated[side]["protruding_strand"]
                async with sessions() as session:
                    edge = (await session.scalars(select(MolecularOperationInput).where(
                        MolecularOperationInput.operation_id == treated["operation_id"]))).one()
                    assert edge.revision_id == previous
                previous = treated["revision_id"]
                # Both states save: phosphate labels never become an admission gate.
                fragment = source_fragment("treated", treated["document_id"], treated["sequence"])
                fragment.update(left_end=treated["left_end"], right_end=treated["right_end"])
                joined = await ligate(client, [fragment, await fragment_from_item(client, children[1], "b")])
                assert joined.status_code == 200, joined.text
                assert joined.json()["saved_sequence"]["sequence"] == SOURCE
            product_id = joined.json()["saved_sequence"]["id"]
            async with sessions() as session:
                product = (await session.scalars(select(MolecularRevision).where(MolecularRevision.document_id == product_id))).one()
                triggers = (await session.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'"))).scalars().all()
                assert triggers
            exported = await client.post("/api/molbio/branches/export", json={"revision_ids": [product.id]})
            assert exported.status_code == 200, exported.text
            bundle = exported.json()
            imported = await dst.post("/api/molbio/branches/import", json={"bundle": bundle})
            assert imported.status_code == 200, imported.text
            ids = imported.json()["id_map"]
            async with target() as session:
                for row in bundle["revisions"]:
                    revision = await session.get(MolecularRevision, ids["revisions"][row["id"]])
                    assert revision.snapshot["sequence"] == row["snapshot"]["sequence"]
                    assert revision.snapshot.get("features") == row["snapshot"].get("features")
                    assert revision.provenance.get("physical_ends") == row["provenance"].get("physical_ends")
                edges = (await session.scalars(select(MolecularOperationInput))).all()
                assert {(e.operation_id, e.revision_id, e.role, e.position) for e in edges} == {
                    (ids["operations"][e["operation_id"]], ids["revisions"][e["revision_id"]], e["role"], e["ordinal"]) for e in bundle["inputs"]}
            imported_children = await sticky_children(dst)
            rebuilt = await ligate(dst, [await fragment_from_item(dst, c, str(i)) for i, c in enumerate(imported_children)])
            assert rebuilt.status_code == 200, rebuilt.text
            reopened = await dst.get(f"/api/sequences/{rebuilt.json()['saved_sequence']['id']}")
            assert reopened.json()["sequence"] == SOURCE
            assert extract(SOURCE, reopened.json()["features"][0]) == SOURCE[3:14]
            before = await counts(target)
            replay = await dst.post("/api/molbio/branches/import", json={"bundle": bundle})
            assert replay.status_code == 200 and replay.json()["already_imported"]
            assert await counts(target) == before
    finally:
        await engine.dispose()
        await target_engine.dispose()


@pytest.mark.asyncio
async def test_rotated_circular_annotation_update_then_save(tmp_path):
    engine, sessions = await empty_store(tmp_path / "rotated.db")
    try:
        async with make_client(sessions) as client:
            sequence = "AACCGGTTACGT"
            sid = await seed(sessions, sequence, circular=True, features=[feature("tag", 2, 6)])
            rotated = sequence[4:] + sequence[:4]
            annotation = feature("tag", 10, 2, segments=[{"start": 10, "end": 12}, {"start": 0, "end": 2}])
            updated = await client.put(f"/api/sequences/{sid}", json={"sequence": rotated, "features": [annotation]})
            assert updated.status_code == 200, updated.text
            current = await client.get(f"/api/sequences/{sid}")
            assert current.json()["sequence"] == rotated
            assert extract(rotated, current.json()["features"][0]) == sequence[2:6]
            fragment = source_fragment("circle", sid, rotated, 0, 0, wraps=True)
            fragment["source_revision"] = 2
            saved = await ligate(client, [fragment], circular=True)
            assert saved.status_code == 200, saved.text
            assert saved.json()["saved_sequence"]["sequence"] == rotated
            assert extract(rotated, saved.json()["saved_sequence"]["features"][0]) == sequence[2:6]
    finally:
        await engine.dispose()
