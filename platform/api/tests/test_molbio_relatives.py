"""Revision relatives and chooser read routes over real saved digests/assemblies (scratch SQLite)."""
from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from molbio_database import create_molbio_engine, get_molbio_session, init_molbio_db, make_molbio_session_factory
from molbio_models import (
    MolecularDocument,
    MolecularOperation,
    MolecularOperationInput,
    MolecularOperationOutput,
    MolecularRevision,
)
from routers import molbio_ops, molbio_relatives, molbio_restriction, nucleotide_sequences
from services.restriction_catalog import CatalogAuthority
from test_restriction_digest_persistence import CATALOG, MANIFEST, SCHEMA, _preview_request, _store

SEQ_A = "ACGTTGCAAGCTTCGAGGCTAAGTCCATGGACTTCAGGATCCTTAGCAGTCA" * 2
SEQ_B = "TTGACCGGATCGATCGTTAACCGGTTAGCATCGGCATTCGAGCTAAGCTT" * 2


async def _client(sessions):
    app = FastAPI()
    for module in (molbio_ops, molbio_restriction, molbio_relatives, nucleotide_sequences):
        app.include_router(module.router)
    authority = CatalogAuthority(CATALOG, MANIFEST, SCHEMA)
    app.dependency_overrides[molbio_restriction.get_catalog_authority] = lambda: authority

    async def dependency():
        async with sessions() as session:
            yield session

    app.dependency_overrides[molbio_restriction.get_molbio_session] = dependency
    app.dependency_overrides[get_molbio_session] = dependency
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _save_digest(client, digest):
    preview = await client.post("/api/molbio/restriction/digests/simulate", json=_preview_request(digest))
    assert preview.status_code == 200, preview.text
    body = {
        **_preview_request(digest),
        "schema": "bms.molbio.restriction-digest-save-request.v1",
        "simulation_sha256": preview.json()["simulation_sha256"],
        "idempotency_key": "relatives-" + uuid.uuid4().hex,
        "persistence_mode": "operation_and_fragments",
        "fragment_name_prefix": "Rel child",
    }
    saved = await client.post("/api/molbio/restriction/digests", json=body)
    assert saved.status_code == 200, saved.text
    return saved.json()


async def _digest_fixture(tmp_path):
    engine, sessions, digest = await _store(tmp_path)
    async with await _client(sessions) as saver:
        saved = await _save_digest(saver, digest)
    return engine, sessions, await _client(sessions), saved


async def _all_pages(client, url, key):
    items, cursor = [], None
    while True:
        response = await client.get(url, params={"limit": 2, **({"cursor": cursor} if cursor else {})})
        assert response.status_code == 200, response.text
        body = response.json()
        page = key(body)
        items.extend(page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            return items


@pytest.mark.asyncio
async def test_digest_children_in_relatives_chooser_and_sources_lead_back(tmp_path: Path):
    engine, sessions, client, saved = await _digest_fixture(tmp_path)
    try:
        async with client:
            operation_id = saved["operation_id"]
            relatives = await client.get("/api/molbio/revisions/source-revision/relatives")
            assert relatives.status_code == 200, relatives.text
            body = relatives.json()
            assert body["revision"]["document_name"] == "source"
            assert body["sources"] == []
            derived = body["derived"]["items"]
            assert [op["operation_id"] for op in derived] == [operation_id]
            assert derived[0]["operation_kind"] == "restriction_digest"
            assert [edge["role"] for edge in derived[0]["inputs"]] == ["digest_source"]
            assert len(derived[0]["outputs"]) == 2
            child = derived[0]["outputs"][0]
            assert child["role"] == "digest_fragment" and child["document_name"].startswith("Rel child")
            assert child["topology"] == "linear" and child["length"] > 0

            chooser = await client.get("/api/molbio/chooser")
            assert chooser.status_code == 200, chooser.text
            retained = [item for item in chooser.json()["items"] if item["origin"] == "retained"]
            assert {item["revision_id"] for item in retained} == {edge["revision_id"] for edge in derived[0]["outputs"]}
            for item in retained:
                assert item["sequence_id"] is None and item["is_current"] is True
                assert item["produced_by"] == {
                    "operation_id": operation_id, "operation_kind": "restriction_digest", "role": "digest_fragment",
                }
                assert item["left_end"] is not None and item["right_end"] is not None
            only_kind = await client.get("/api/molbio/chooser", params={"kinds": "restriction_digest"})
            assert len(only_kind.json()["items"]) == 2
            other_kind = await client.get("/api/molbio/chooser", params={"kinds": "ligation"})
            assert other_kind.json()["items"] == []
            by_name = await client.get("/api/molbio/chooser", params={"q": "REL CHILD 1"})
            assert [item["name"] for item in by_name.json()["items"]] == ["Rel child 1"]

            back = await client.get(f"/api/molbio/revisions/{child['revision_id']}/relatives")
            sources = back.json()["sources"]
            assert [op["operation_id"] for op in sources] == [operation_id]
            assert sources[0]["inputs"][0]["revision_id"] == "source-revision"
            assert back.json()["derived"] == {"items": [], "next_cursor": None}

            detail = (await client.get(f"/api/molbio/operations/{operation_id}")).json()
            assert [(row["revision_id"], row["document_id"]) for row in detail["outputs"]] == [
                (edge["revision_id"], edge["document_id"]) for edge in derived[0]["outputs"]
            ]
            assert (await client.get("/api/molbio/revisions/nope/relatives")).status_code == 404
            assert (await client.get("/api/molbio/chooser", params={"cursor": "###"})).status_code == 400
    finally:
        await engine.dispose()


async def _seed_shelf(client, name, sequence):
    response = await client.post("/api/sequences/", json={"name": name, "sequence": sequence, "sequence_type": "dna", "is_circular": False})
    assert response.status_code == 200, response.text
    return response.json()["id"]


@pytest.mark.asyncio
async def test_multi_parent_assembly_lists_every_input_and_shelf_origin(tmp_path: Path):
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'asm.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    client = await _client(sessions)
    try:
        async with client:
            a = await _seed_shelf(client, "Parent A", SEQ_A)
            b = await _seed_shelf(client, "Parent B", SEQ_B)
            fragments = [
                dict(id="a", name="A", sequence=SEQ_A, source_sequence_id=a, source_start=0, source_end=len(SEQ_A),
                     left_end={"type": "blunt"}, right_end={"type": "blunt"}),
                dict(id="b", name="B", sequence=SEQ_B, source_sequence_id=b, source_start=0, source_end=len(SEQ_B),
                     left_end={"type": "blunt"}, right_end={"type": "blunt"}),
            ]
            saved = await client.post("/api/molbio/assembly/ligation/save?response_view=compact",
                                      json=dict(fragments=fragments, circular=False, new_name="Joined AB"))
            assert saved.status_code == 200, saved.text
            product_id = saved.json()["saved_sequence"]["id"]
            async with sessions() as session:
                heads = {row.document_id: row.id for row in (await session.scalars(select(MolecularRevision))).all()
                         if row.revision_number == 1}
                product_revision = heads[product_id]
            product = await client.get(f"/api/molbio/revisions/{product_revision}/relatives")
            sources = product.json()["sources"]
            assert len(sources) == 1
            input_documents = {edge["document_id"] for edge in sources[0]["inputs"] if edge["role"] == "fragment"}
            assert input_documents == {a, b}
            assert sources[0]["operation_kind"] == "ligation"
            for parent in (a, b):
                derived = (await client.get(f"/api/molbio/revisions/{heads[parent]}/relatives")).json()["derived"]["items"]
                assert [op["operation_id"] for op in derived] == [sources[0]["operation_id"]]
                assert {edge["document_name"] for edge in derived[0]["inputs"]} >= {"Parent A", "Parent B"}
            shelf = (await client.get("/api/molbio/chooser", params={"q": "parent"})).json()["items"]
            assert {item["name"] for item in shelf} == {"Parent A", "Parent B"}
            assert all(item["origin"] == "shelf" and item["sequence_id"] == item["document_id"] and item["is_current"] for item in shelf)
            assert all(item["left_end"] is None for item in shelf)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cursors_exhaust_sets_larger_than_a_page(tmp_path: Path):
    engine, sessions, client, saved = await _digest_fixture(tmp_path)
    try:
        async with client:
            # Add extra operations that consume the same revision plus extra retained outputs.
            async with sessions.begin() as session:
                revision = await session.get(MolecularRevision, "source-revision")
                added = set()
                for index in range(5):
                    operation_id = str(uuid.uuid4())
                    session.add(MolecularOperation(
                        id=operation_id, operation_kind="end_operation", implementation="test",
                        status="completed", parameters={}, warnings=[], provenance={},
                    ))
                    document_id, revision_id = str(uuid.uuid4()), str(uuid.uuid4())
                    session.add(MolecularDocument(id=document_id, document_kind="dna", name=f"Extra {index}", current_revision_id=None))
                    await session.flush()
                    session.add(MolecularRevision(
                        id=revision_id, document_id=document_id, revision_number=1, change_kind="end_operation",
                        content_sha256=revision.content_sha256, content_length=revision.content_length,
                        snapshot={"sequence": "ACGT", "name": f"Extra {index}", "topology": "linear"}, provenance={},
                        operation_id=operation_id,
                    ))
                    await session.flush()
                    session.add(MolecularOperationInput(id=str(uuid.uuid4()), operation_id=operation_id,
                                                        revision_id="source-revision", role="source", position=0, snapshot={}))
                    session.add(MolecularOperationOutput(id=str(uuid.uuid4()), operation_id=operation_id,
                                                         revision_id=revision_id, role="end_operation_product", position=0, snapshot={}))
                    added.add(operation_id)
            derived = await _all_pages(client, "/api/molbio/revisions/source-revision/relatives", lambda b: b["derived"])
            ids = [op["operation_id"] for op in derived]
            assert len(ids) == len(set(ids)) == 6 and added | {saved["operation_id"]} == set(ids)
            chooser = await _all_pages(client, "/api/molbio/chooser", lambda b: b)
            keys = [item["revision_id"] for item in chooser]
            assert len(keys) == len(set(keys)) == 7  # 2 digest children + 5 extras, all retained
            full = (await client.get("/api/molbio/chooser", params={"limit": 200})).json()
            assert full["next_cursor"] is None and {i["revision_id"] for i in full["items"]} == set(keys)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_deleted_parent_still_displays_by_snapshot_and_older_revision_opens(tmp_path: Path):
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'del.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    client = await _client(sessions)
    try:
        async with client:
            a = await _seed_shelf(client, "Doomed parent", SEQ_A)
            b = await _seed_shelf(client, "Survivor", SEQ_B)
            fragments = [
                dict(id="a", name="A", sequence=SEQ_A, source_sequence_id=a, source_start=0, source_end=len(SEQ_A),
                     left_end={"type": "blunt"}, right_end={"type": "blunt"}),
                dict(id="b", name="B", sequence=SEQ_B, source_sequence_id=b, source_start=0, source_end=len(SEQ_B),
                     left_end={"type": "blunt"}, right_end={"type": "blunt"}),
            ]
            saved = await client.post("/api/molbio/assembly/ligation/save?response_view=compact",
                                      json=dict(fragments=fragments, circular=False, new_name="Joined"))
            assert saved.status_code == 200, saved.text
            product_id = saved.json()["saved_sequence"]["id"]
            async with sessions() as session:
                revisions = {row.document_id: row for row in (await session.scalars(select(MolecularRevision))).all()}
            assert (await client.delete(f"/api/sequences/{a}")).status_code in (200, 204)
            product_revision = revisions[product_id].id
            sources = (await client.get(f"/api/molbio/revisions/{product_revision}/relatives")).json()["sources"]
            names = {edge["document_name"] for edge in sources[0]["inputs"]}
            assert "Doomed parent" in names
            relatives = await client.get(f"/api/molbio/revisions/{revisions[a].id}/relatives")
            assert relatives.status_code == 200
            assert relatives.json()["revision"]["document_name"] == "Doomed parent"
            assert len(relatives.json()["derived"]["items"]) == 1
            reopened = await client.get(f"/api/sequences/{a}/revisions/{revisions[a].id}")
            assert reopened.status_code == 200, reopened.text
            assert reopened.json()["snapshot"]["sequence"] == SEQ_A
            assert "Doomed parent" not in {item["name"] for item in (await client.get("/api/molbio/chooser")).json()["items"]}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_golden_gate_intermediates_without_projection_are_chooser_items(tmp_path: Path):
    from services.assembly.golden_gate_workflow import freeze_selection
    from services.assembly.golden_gate_workflow_types import SaveDesignRequest, WorkflowResult
    from test_golden_gate_design_core import request
    from test_golden_gate_workflow_receiving import client_store

    async with client_store(tmp_path) as (client, sessions):
        response = await client.post("/api/molbio/assembly/golden-gate/design", json=request(kind="synthesis").model_dump(mode="json"))
        assert response.status_code == 200, response.text
        result = WorkflowResult.model_validate(response.json())
        body = SaveDesignRequest(selection=freeze_selection(result, result.selected_solution_id), name="GG rel", idempotency_key="gg-rel")
        saved = await client.post("/api/molbio/assembly/golden-gate/design/save", json=body.model_dump(mode="json"))
        assert saved.status_code == 200, saved.text
        saved = saved.json()
        app = client._transport.app
        app.include_router(molbio_relatives.router)
        async with sessions() as session:
            outputs = (await session.scalars(select(MolecularOperationOutput).where(
                MolecularOperationOutput.operation_id == saved["operation_id"]))).all()
            projected = {row.id for row in (await session.scalars(select(MolecularDocument))).all()
                         if (await session.execute(text("SELECT 1 FROM nucleotide_sequences WHERE id=:i"), {"i": row.id})).first()}
            output_docs = {row.revision_id: row for row in outputs}
            documents = {rev.id: rev.document_id for rev in (await session.scalars(select(MolecularRevision).where(
                MolecularRevision.id.in_(list(output_docs))))).all()}
        retained_expected = {rev for rev, doc in documents.items() if doc not in projected}
        items = (await client.get("/api/molbio/chooser", params={"limit": 200})).json()["items"]
        retained = {item["revision_id"] for item in items if item["origin"] == "retained"}
        assert retained_expected, "the Golden Gate save retained no off-shelf output to check"
        assert retained_expected <= retained
        kinds = {item["produced_by"]["operation_kind"] for item in items if item["revision_id"] in retained_expected}
        assert len(kinds) == 1 and kinds != {None}
        one = next(item for item in items if item["revision_id"] in retained_expected)
        relatives = (await client.get(f"/api/molbio/revisions/{one['revision_id']}/relatives")).json()
        assert relatives["sources"][0]["operation_id"] == saved["operation_id"]
