"""Actionable restriction plans: real native HTTP, migrated stores and cold reuse."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import select, text

from molbio_models import MolecularOperation, MolecularOperationInput, MolecularRevision
from routers import molbio_end_operations
from services.assembly import restriction_cloning_persistence as persistence
from services.molbio_persistence import current_molecular_revision
from test_cloning_connected_api_acceptance import empty_store
from test_molbio_branches import make_client, counts
from test_assembly_product_content import seed, feature, extract
from test_end_operations_and_cloning import _plasmid, _random_dna, SITES

BASE = "/api/molbio/assembly/restriction-cloning"


def fixture(tails=False, positive=False):
    names = ("XhoI", "NcoI") if tails else ("EcoRI", "BamHI")
    backbone = _plasmid(SITES[names[0]] + "TTTAGC" + SITES[names[1]], seed=5)
    payload = _random_dna(180 if positive else 120, 2 if positive else 8, avoid=SITES.values())
    donor = "GCGC" + ("" if tails else SITES[names[0]]) + payload + ("" if tails else SITES[names[1]]) + "GCGC"
    start = 4 + (0 if tails else len(SITES[names[0]]))
    return {"backbone_sequence": backbone, "insert_sequence": donor, "enzymes": list(names),
            "insert_start": start, "insert_end": start + len(payload),
            "backbone_features": [feature("backbone tag", 20, 50)],
            "insert_features": [feature("insert tag", start + 20, start + 50), feature("discarded flank", 0, 3)],
            "limit": 3}, payload


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", ["start", "end"])
async def test_a_single_backbone_window_bound_is_not_ignored(tmp_path, bound):
    engine, sessions = await empty_store(tmp_path / "window.db")
    body, _payload = fixture()
    body[f"backbone_region_{bound}"] = body["backbone_sequence"].index(SITES["BamHI" if bound == "start" else "EcoRI"]) + 1
    try:
        async with make_client(sessions) as client:
            client._transport.app.include_router(molbio_end_operations.router)
            result = await client.post(BASE + "/suggest", json=body)
            assert result.status_code == 200, result.text
            assert result.json()["no_suitable_pair"]  # only one of the two cutters is inside the supplied window
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("tails,positive", [(False, False), (True, False), (True, True)])
async def test_suggest_prepare_save_cold_reopen_and_reuse(tmp_path, monkeypatch, tails, positive):
    engine, sessions = await empty_store(tmp_path / "restriction.db")
    body, payload = fixture(tails, positive)
    original = deepcopy(body)
    try:
        async with make_client(sessions) as client:
            client._transport.app.include_router(molbio_end_operations.router)
            before = await counts(sessions)
            response = await client.post(BASE + "/suggest", json=body)
            assert response.status_code == 200, response.text
            assert await counts(sessions) == before  # planning does not save or edit sources
            proposal = response.json()["proposals"][0]
            assert proposal["kind"] == ("primer_tail" if tails else "existing_flank_sites")
            assert len(proposal["assembly_request"]["fragments"]) == 2
            preview = await client.post("/api/molbio/assembly/ligation/simulate", json=proposal["assembly_request"])
            assert preview.status_code == 200, preview.text
            assert preview.json()["product"]["sequence"] == proposal["product_sequence"]
            if positive:
                assert len(proposal["selection"]["primers"]) == 2
            prepare = {"request": body, "selection": proposal["selection"], "name": "Generic restriction recipe", "idempotency_key": "restriction-1"}
            response = await client.post(BASE + "/prepare", json=prepare)
            assert response.status_code == 200, response.text
            prepared = response.json()
            if root := os.environ.get("BMS_RESTRICTION_CLONING_WIRE"):
                Path(root).mkdir(parents=True, exist_ok=True)
                Path(root, f"{'tails-positive' if positive else 'tails' if tails else 'existing'}.json").write_text(json.dumps({
                    "request": body, "suggestion": proposal, "prepare_request": prepare, "prepared": prepared}, indent=2) + "\n")
            assert len(prepared["outputs"]) == (3 if tails else 2)
            assert len(prepared["assembly_request"]["fragments"]) == 2
            for fragment in prepared["assembly_request"]["fragments"]:
                assert fragment["metadata"]["retained_ends"]
                for side in ("left_end", "right_end"):
                    assert fragment[side]["five_prime_phosphate"] == "phosphorylated"
            result = await client.post("/api/molbio/assembly/ligation/save", json={**prepared["assembly_request"], "new_name": "Generic joined construct"})
            assert result.status_code == 200, result.text
            product = result.json()["saved_sequence"]
            assert product["sequence"] == proposal["product_sequence"]
            assert {f["name"]: extract(product["sequence"], f) for f in product["features"]} == {
                "backbone tag": body["backbone_sequence"][20:50], "insert tag": payload[20:50]}
            chooser = (await client.get("/api/molbio/chooser")).json()["items"]
            assert {output["revision_id"] for output in prepared["outputs"]} <= {item["revision_id"] for item in chooser}
            async with sessions() as session:
                inputs = (await session.scalars(select(MolecularOperationInput).where(
                    MolecularOperationInput.operation_id == prepared["operation_id"]))).all()
                originals = [await session.get(MolecularRevision, edge.revision_id) for edge in inputs]
                assert {revision.snapshot["sequence"] for revision in originals} == {body["backbone_sequence"], body["insert_sequence"]}
                operation = await session.get(MolecularOperation, prepared["operation_id"])
                assert "backbone_sequence" not in operation.parameters["source_controls"]
                if tails:
                    output = next(o for o in prepared["outputs"] if o["role"] == "prepared_insert")
                    pcr = await session.get(MolecularRevision, output["revision_id"])
                    assert pcr.snapshot["sequence"] == proposal["prepared_insert_sequence"]
                    assert pcr.provenance["physical_ends"]["left_end"]["five_prime_phosphate"] == "unphosphorylated"
                    assert "discarded flank" not in {f["name"] for f in pcr.snapshot["features"]}
                    for primer in pcr.snapshot["primers"]:
                        assert extract(pcr.snapshot["sequence"], primer) == primer["anneal_sequence"]
                assert not (await session.execute(text("PRAGMA foreign_key_check"))).all()
                assert (await session.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'"))).all()
        await engine.dispose()
        monkeypatch.setattr(persistence, "selected_restriction_cloning", lambda *_: pytest.fail("Cold read/retry must not recompute a preparation"))
        async with make_client(sessions) as client:
            client._transport.app.include_router(molbio_end_operations.router)
            response = await client.get(BASE + "/preparations/" + prepared["operation_id"])
            assert response.status_code == 200, response.text
            assert response.json() == prepared
            before = await counts(sessions)
            replay = await client.post(BASE + "/prepare", json=prepare)
            assert replay.status_code == 200 and replay.json() == prepared
            assert await counts(sessions) == before
            conflict = await client.post(BASE + "/prepare", json={**prepare, "name": "Different recipe"})
            assert conflict.status_code == 409
            reopened = await client.get(f"/api/sequences/{product['id']}")
            assert reopened.status_code == 200 and reopened.json()["sequence"] == product["sequence"]
            joined = await client.post("/api/molbio/assembly/ligation/save", json={**prepared["assembly_request"], "new_name": "Reused fragments"})
            assert joined.status_code == 200, joined.text
            assert joined.json()["saved_sequence"]["sequence"] == product["sequence"]
            parent = prepared["request"]["backbone_revision_id"]
            relatives = await client.get(f"/api/molbio/revisions/{parent}/relatives")
            assert relatives.status_code == 200
            assert all(out["revision_id"] in relatives.text for out in prepared["outputs"])
        assert body == original
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_revision_sources_circular_donor_and_advisory_phosphate(tmp_path):
    engine, sessions = await empty_store(tmp_path / "pinned.db")
    body, payload = fixture()
    try:
        backbone_id = await seed(sessions, body["backbone_sequence"], circular=True, features=body["backbone_features"], name="Backbone fixture")
        donor_id = await seed(sessions, body["insert_sequence"], circular=True, features=body["insert_features"], name="Donor fixture")
        async with sessions() as session:
            backbone = await current_molecular_revision(session, backbone_id)
            donor = await current_molecular_revision(session, donor_id)
        request = {"backbone_revision_id": backbone.id, "insert_revision_id": donor.id,
                   "enzymes": body["enzymes"], "insert_start": body["insert_start"], "insert_end": body["insert_end"],
                   "backbone_topology": "circular", "insert_topology": "circular", "limit": 3,
                   "vector_five_prime_phosphate": "unphosphorylated"}
        async with make_client(sessions) as client:
            client._transport.app.include_router(molbio_end_operations.router)
            response = await client.post(BASE + "/suggest", json=request)
            assert response.status_code == 200, response.text
            suggested = response.json()
            proposal = suggested["proposals"][0]
            assert proposal["kind"] == "existing_flank_sites"
            chooser = (await client.get("/api/molbio/chooser")).json()
            backbone_read = (await client.get(f"/api/sequences/{backbone_id}/revisions/{backbone.id}")).json()
            donor_read = (await client.get(f"/api/sequences/{donor_id}/revisions/{donor.id}")).json()
            prepare_request = {"request": request, "selection": proposal["selection"],
                               "name": "Backbone fixture restriction assembly", "idempotency_key": "pinned-wire"}
            prepared = await client.post(BASE + "/prepare", json=prepare_request)
            assert prepared.status_code == 200, prepared.text
            data = prepared.json()
            simulation = await client.post("/api/molbio/assembly/ligation/simulate", json=data["assembly_request"])
            assert simulation.status_code == 200, simulation.text
            if root := os.environ.get("BMS_RESTRICTION_CLONING_WIRE"):
                Path(root).mkdir(parents=True, exist_ok=True)
                Path(root, "pinned.json").write_text(json.dumps({"request": request, "suggestion": proposal, "suggested": suggested,
                    "prepare_request": prepare_request, "prepared": data, "chooser": chooser,
                    "backbone_revision": backbone_read, "insert_revision": donor_read, "simulation": simulation.json()}, indent=2) + "\n")
            assert data["request"]["backbone_revision_id"] == backbone.id
            assert data["request"]["insert_revision_id"] == donor.id
            assert data["request"]["insert_topology"] == "circular"
            saved = await client.post("/api/molbio/assembly/ligation/save", json=data["assembly_request"])
            assert saved.status_code == 200, saved.text
            assert {j["sealed"] for j in saved.json()["product"]["junctions"]} == {"one_strand"}
            assert {f["name"]: extract(saved.json()["saved_sequence"]["sequence"], f) for f in saved.json()["saved_sequence"]["features"]}["insert tag"] == payload[20:50]
            missing = await client.post(BASE + "/suggest", json={**request, "insert_revision_id": "missing-exact-id"})
            assert missing.status_code == 404
            async with sessions() as session:
                assert (await session.get(MolecularRevision, backbone.id)).snapshot["sequence"] == body["backbone_sequence"]
                assert (await session.get(MolecularRevision, donor.id)).snapshot["sequence"] == body["insert_sequence"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("known_parent", [True, False])
async def test_inline_workspace_parent_and_portable_preparation(tmp_path, known_parent):
    engine, sessions = await empty_store(tmp_path / "workspace.db")
    imported_engine, imported_sessions = await empty_store(tmp_path / "portable.db")
    body, _payload = fixture()
    original_sequence = body["backbone_sequence"]
    try:
        parent_id = await seed(sessions, original_sequence, circular=True, features=body["backbone_features"]) if known_parent else "unavailable-workspace-parent"
        # A dirty workspace is used literally; linking its saved parent never overwrites these bases.
        sequence = original_sequence[:60] + ("G" if original_sequence[60] != "G" else "A") + original_sequence[61:]
        body.update(backbone_sequence=sequence, backbone_source_document_id=parent_id)
        async with make_client(sessions) as client, make_client(imported_sessions) as destination:
            client._transport.app.include_router(molbio_end_operations.router)
            destination._transport.app.include_router(molbio_end_operations.router)
            response = await client.post(BASE + "/suggest", json=body)
            assert response.status_code == 200, response.text
            suggested = response.json()
            proposal = suggested["proposals"][0]
            if known_parent:
                async with sessions() as session:
                    parent = await current_molecular_revision(session, parent_id)
                assert proposal["selection"]["parent_revisions"]["backbone"] == parent.id
            else:
                assert any("could not be linked" in note for note in suggested["notes"])
            response = await client.post(BASE + "/prepare", json={"request": body, "selection": proposal["selection"]})
            assert response.status_code == 200, response.text
            prepared = response.json()
            if known_parent:
                relatives = await client.get(f"/api/molbio/revisions/{parent.id}/relatives")
                assert all(child["revision_id"] in relatives.text for child in prepared["outputs"])
                async with sessions() as session:
                    assert (await session.get(MolecularRevision, parent.id)).snapshot["sequence"] == original_sequence
            result = await client.post("/api/molbio/assembly/ligation/save", json=prepared["assembly_request"])
            assert result.status_code == 200, result.text
            assert result.json()["saved_sequence"]["sequence"] == proposal["product_sequence"]
            exported = await client.post("/api/molbio/branches/export", json={"revision_ids": [o["revision_id"] for o in prepared["outputs"]]})
            assert exported.status_code == 200, exported.text
            response = await destination.post("/api/molbio/branches/import", json={"bundle": exported.json()})
            assert response.status_code == 200, response.text
            ids = response.json()["id_map"]
            reopened = await destination.get(BASE + "/preparations/" + ids["operations"][prepared["operation_id"]])
            assert reopened.status_code == 200, reopened.text
            portable = reopened.json()
            assert {f["source_sequence_id"] for f in portable["assembly_request"]["fragments"]} == {
                ids["documents"][f["source_sequence_id"]] for f in prepared["assembly_request"]["fragments"]}
            if known_parent:
                assert portable["selection"]["parent_revisions"]["backbone"] == ids["revisions"][parent.id]
                assert portable["request"]["backbone_source_document_id"] == ids["documents"][parent_id]
            again = await destination.post("/api/molbio/assembly/ligation/save", json=portable["assembly_request"])
            assert again.status_code == 200, again.text
            assert again.json()["saved_sequence"]["sequence"] == proposal["product_sequence"]
    finally:
        await engine.dispose()
        await imported_engine.dispose()
