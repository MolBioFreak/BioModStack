"""Receiving HTTP/scratch-store regressions for representation-only traffic repairs."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from molbio_database import get_molbio_session
from molbio_models import MolBioBase, MolecularOperationInput, MolecularRevision, NucleotideSequence
from routers import molbio_ops, molbio_restriction, nucleotide_sequences
from services.assembly import golden_gate, ligation
from services.assembly.types import AssemblyFragment, FragmentEnd
from services.restriction_catalog import catalog_authority
from test_restriction_digest_persistence import _store, _client, _preview_request


@asynccontextmanager
async def assembly_client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'assembly.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(MolBioBase.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async def dependency():
        async with sessions() as session:
            yield session
    app = FastAPI()
    app.include_router(molbio_ops.router)
    app.include_router(nucleotide_sequences.router)
    app.dependency_overrides[get_molbio_session] = dependency
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client, sessions, engine
    finally:
        await engine.dispose()


def gg_request() -> dict:
    catalog = catalog_authority.require()
    return dict(
        enzyme_id="BsaI", catalog_id=catalog.catalog_id, expected_catalog_sha256=catalog.content_sha256,
        circular=False, fragments=[asdict(AssemblyFragment(
            str(i), "prepared insert", sequence, orientation="reverse",
            left_end=FragmentEnd("sticky_5", left, protruding_strand="top"),
            right_end=FragmentEnd("sticky_5", right, protruding_strand="bottom"),
        )) for i, (sequence, left, right) in enumerate([
            ("GGAGTTTTT", "GGAG", "CATT"), ("AATGCCCCC", "AATG", "CTCC"),
        ])],
    )


def test_public_query_contracts_are_explicit_opt_in():
    app = FastAPI()
    for router in (molbio_ops.router, molbio_restriction.router, nucleotide_sequences.router):
        app.include_router(router)
    paths = app.openapi()["paths"]
    catalog = paths["/api/molbio/restriction/catalog"]["get"]
    assert {p["name"] for p in catalog["parameters"]} == {
        "response_view", "enzyme_ids", "query", "geometry_status", "commercial", "supplier_code",
        "enzyme_kind", "overhang_kind", "palindromic", "limit", "cursor",
    }
    for path in ["/api/molbio/restriction/catalog", "/api/molbio/restriction/digests",
                 *[f"/api/molbio/assembly/{mode}/save" for mode in ("ligation", "gibson", "golden-gate", "gibson/design")]]:
        operation = paths[path]["get" if path.endswith("catalog") else "post"]
        parameter = next(p for p in operation["parameters"] if p["name"] == "response_view")
        assert parameter["in"] == "query"
        assert parameter["schema"]["enum"] == ["full", "compact"]
        assert parameter["schema"]["default"] == "full"
    shelf = {p["name"]: p["schema"] for p in paths["/api/sequences/assembly-workups"]["get"]["parameters"]}
    assert shelf["offset"]["default"] == 0
    assert shelf["offset"]["minimum"] == 0
    assert shelf["limit"]["default"] == 100


def test_catalog_complete_compact_inventory_matches_full_filters_and_details(record_property):
    app = FastAPI()
    app.include_router(molbio_restriction.router)
    measurements = {}
    with TestClient(app) as client:
        inventories = {}
        for view in ("full", "compact"):
            items, total_bytes, requests, cursor = [], 0, 0, None
            while True:
                params = dict(limit=200, response_view=view)
                if cursor:
                    params["cursor"] = cursor
                response = client.get("/api/molbio/restriction/catalog", params=params)
                assert response.status_code == 200, response.text
                page = response.json()
                items.extend(page["items"])
                total_bytes += len(response.content)
                requests += 1
                cursor = page["next_cursor"]
                if cursor is None:
                    break
            assert len(items) == page["catalog"]["counts"]["total"]
            assert len({item["enzyme_id"] for item in items}) == len(items)
            inventories[view] = items
            measurements[view] = dict(requests=requests, decoded_bytes=total_bytes, count=len(items))
        assert inventories["compact"] == [
            molbio_restriction.CatalogBrowseItem.from_record(
                molbio_restriction.RestrictionRecord.model_validate(item)
            ).model_dump(mode="json") for item in inventories["full"]
        ]
        assert measurements["compact"]["decoded_bytes"] < measurements["full"]["decoded_bytes"]
        for filters in [dict(query="BsaI"), dict(geometry_status="unknown"), dict(commercial="reported"),
                        dict(supplier_code="N"), dict(enzyme_kind="nicking_endonuclease"),
                        dict(overhang_kind="three_prime"), dict(palindromic="true")]:
            full = client.get("/api/molbio/restriction/catalog", params={**filters, "limit": 250}).json()
            compact = client.get("/api/molbio/restriction/catalog", params={**filters, "limit": 250, "response_view": "compact"}).json()
            assert [x["enzyme_id"] for x in full["items"]] == [x["enzyme_id"] for x in compact["items"]]
            assert full["next_cursor"] == compact["next_cursor"]
        # Full selected details are batchable through the same paged owner.
        selected = [item["enzyme_id"] for item in inventories["full"][:260]]
        batch, cursor, count = [], None, 0
        while True:
            params = [("enzyme_ids", item) for item in selected] + [("limit", "200")]
            if cursor:
                params.append(("cursor", cursor))
            response = client.get("/api/molbio/restriction/catalog", params=params)
            assert response.status_code == 200, response.text
            count += 1
            page = response.json()
            batch.extend(page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert batch == inventories["full"][:260]
        assert count == 2
        detail = client.get("/api/molbio/restriction/catalog/BsaI").json()["record"]
        assert detail == next(item for item in inventories["full"] if item["enzyme_id"] == "BsaI")
    record_property("catalog_counters", json.dumps(measurements))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["operation_only", "operation_and_fragments"])
async def test_compact_digest_save_retry_concurrent_and_full_readback(tmp_path, mode, record_property):
    engine, sessions, digest = await _store(tmp_path, sequence="ACGT" * 2500 + "GAATTC" + "TGCA" * 2500)
    try:
        async with await _client(sessions) as client:
            request = {**_preview_request(digest), "schema": "bms.molbio.restriction-digest-save-request.v1",
                       "idempotency_key": "traffic", "persistence_mode": mode}
            first, second = await asyncio.gather(*[
                client.post("/api/molbio/restriction/digests?response_view=compact", json=request) for _ in range(2)
            ])
            assert first.status_code == second.status_code == 200, (first.text, second.text)
            assert first.json() == second.json()
            compact = first.json()
            assert "simulation" not in compact
            full = await client.post("/api/molbio/restriction/digests", json=request)
            detail = await client.get(f"/api/molbio/restriction/digests/{compact['operation_id']}")
            assert full.status_code == detail.status_code == 200
            assert full.content == detail.content
            expected = full.json()
            simulation = expected.pop("simulation")
            expected["schema"] = "bms.molbio.restriction-digest-saved-ack.v1"
            assert compact == expected
            assert "".join(f["top_strand_sequence"] for f in simulation["fragments"]) == "ACGT" * 2500 + "GAATTC" + "TGCA" * 2500
            assert len(compact["outputs"]) == (2 if mode == "operation_and_fragments" else 0)
            conflict = await client.post("/api/molbio/restriction/digests?response_view=compact", json={**request, "enzyme_ids": ["BamHI"]})
            assert conflict.status_code == 409
            record_property("digest_payload_bytes", json.dumps(dict(request=len(first.request.content), full=len(full.content), compact=len(first.content))))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ligation", "gibson", "golden-gate"])
async def test_compact_assembly_save_retains_product_and_historical_replay(tmp_path, mode, record_property):
    payload = gg_request() if mode == "golden-gate" else {
        "fragments": [dict(id="one", name="one", sequence="ACGT" * 2500)], "circular": False,
    }
    if mode == "gibson":
        payload = dict(circular=False, minimum_overlap=28, preferred_overlap=28, maximum_overlap=28,
                       fragments=[dict(id="a", name="a", sequence="A" * 1000 + "ACGT" * 7),
                                  dict(id="b", name="b", sequence="ACGT" * 7 + "T" * 1000)])
    async with assembly_client(tmp_path) as (client, sessions, _):
        full = await client.post(f"/api/molbio/assembly/{mode}/save", json=payload)
        compact = await client.post(f"/api/molbio/assembly/{mode}/save?response_view=compact", json=payload)
        assert full.status_code == compact.status_code == 200, (full.text, compact.text)
        a, b = full.json(), compact.json()
        assert a["product"] == b["product"]
        assert "sequence" not in b["saved_sequence"]
        assert set(a["saved_sequence"]) - set(b["saved_sequence"]) == {"sequence"}
        # Fresh receiving session, not the save route's identity map.
        async with sessions() as session:
            row = await session.get(NucleotideSequence, b["saved_sequence"]["id"])
            assert row.sequence == a["product"]["sequence"]
            assert row.operation_params == b["saved_sequence"]["operation_params"]
            if mode != "gibson":
                replay = row.operation_params["assembly_request"]
                assert replay["fragments"] == b["saved_sequence"]["operation_params"]["assembly_request"]["fragments"]
        if mode != "gibson":
            reopened = await client.post(f"/api/molbio/assembly/{mode}/simulate", json=replay)
            assert reopened.status_code == 200, reopened.text
            assert reopened.json()["product"] == b["product"]
        detail = await client.get(f"/api/sequences/{b['saved_sequence']['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["sequence"] == b["product"]["sequence"]
        assert len(compact.content) < len(full.content)
        record_property("assembly_payload_bytes", json.dumps(dict(request=len(full.request.content), full=len(full.content), compact=len(compact.content))))


@pytest.mark.asyncio
async def test_compact_retained_gibson_design_save_keeps_science_without_replanning(tmp_path, monkeypatch):
    from test_pydna_gibson import _design_payload
    async with assembly_client(tmp_path) as (client, sessions, _):
        payload = _design_payload(inline=True)
        preview = await client.post("/api/molbio/assembly/gibson/design", json=payload)
        assert preview.status_code == 200, preview.text
        payload.update(computation_id=preview.json()["computation_id"],
                       selected_candidate_checksum=preview.json()["selected_candidate_checksum"])
        def forbidden(*args, **kwargs):
            raise AssertionError("Save must use retained computation, not replan")
        monkeypatch.setattr(molbio_ops, "_execute_gibson_design", forbidden)
        response = await client.post("/api/molbio/assembly/gibson/design/save?response_view=compact", json=payload)
        assert response.status_code == 200, response.text
        saved = response.json()
        assert "sequence" not in saved["saved_sequence"]
        assert saved["selected_product"] == preview.json()["selected_product"]
        assert saved["primers"] == preview.json()["primers"]
        async with sessions() as session:
            row = await session.get(NucleotideSequence, saved["saved_sequence"]["id"])
            assert row.sequence == saved["selected_product"]["sequence"]


@pytest.mark.asyncio
async def test_shelf_sql_summary_all_pages_ties_and_malformed_fallbacks(tmp_path, record_property):
    cases = [None, [], "bad", {}, {"fragments": [1, 2]}, {"source_fragments": [1], "fragments": [1, 2]},
             {"ordered_fragments": None, "source_fragments": [1]}, {"ordered_fragments": "bad", "fragments": [1]},
             {"ordered_fragments": [1, 2, 3], "primers": [1], "engine": "pydna", "engine_version": "v1"},
             {"engine": 1, "engine_version": False, "primers": {}}, {"engine": "", "primers": None}]
    async with assembly_client(tmp_path) as (client, sessions, engine):
        async with sessions.begin() as session:
            for i in range(123):
                session.add(NucleotideSequence(id=f"s{i:03}", name=f"row {i}", sequence="ACGT", length=4,
                    sequence_type="dna", is_circular=False, operation="gibson", operation_params=cases[i % len(cases)],
                    features=[], primers=[], version=1, created_at=datetime(2020, 1, 1), updated_at=datetime(2020, 1, 1)))
        statements = []
        def capture(_conn, _cursor, sql, _params, _context, _many):
            if sql.lstrip().upper().startswith("SELECT"):
                statements.append(sql)
        event.listen(engine.sync_engine, "before_cursor_execute", capture)
        pages = []
        for offset in (0, 50, 100, 150):
            response = await client.get("/api/sequences/assembly-workups", params=dict(limit=50, offset=offset))
            assert response.status_code == 200, response.text
            pages.append(response.json())
        rows = [row for page in pages for row in page]
        assert [row["id"] for row in rows] == [f"s{i:03}" for i in range(123)]
        assert len(statements) == 4
        assert all("nucleotide_sequences.sequence," not in sql and "nucleotide_sequences.features" not in sql for sql in statements)
        assert all("nucleotide_sequences.operation_params," not in sql for sql in statements)
        for i, row in enumerate(rows):
            params = cases[i % len(cases)]
            params = params if isinstance(params, dict) else {}
            fragments = params.get("ordered_fragments", params.get("source_fragments", params.get("fragments", [])))
            primers = params.get("primers", [])
            assert row["fragment_count"] == (len(fragments) if isinstance(fragments, list) else 0)
            assert row["primer_count"] == (len(primers) if isinstance(primers, list) else 0)
            assert row["engine"] == (params.get("engine") if isinstance(params.get("engine"), str) else None)
            assert row["engine_version"] == (params.get("engine_version") if isinstance(params.get("engine_version"), str) else None)
        before = await client.get("/api/sequences/assembly-workups?limit=1")
        async with sessions.begin() as session:
            await session.execute(update(NucleotideSequence).where(NucleotideSequence.id == "s000").values(
                sequence="ACGT" * 100000, operation_params={"unused": "ACGT" * 100000},
                updated_at=datetime(2020, 1, 1)))
        after = await client.get("/api/sequences/assembly-workups?limit=1")
        assert before.content == after.content
        record_property("shelf_counters", json.dumps(dict(rows=len(rows), requests=4, page_lengths=list(map(len, pages)), small_bytes=len(before.content), large_bytes=len(after.content))))


@pytest.mark.asyncio
async def test_transaction_source_resolution_hash_once_and_every_slice_checked(tmp_path, monkeypatch, record_property):
    async with assembly_client(tmp_path) as (client, sessions, _):
        source_dna = "ACGT" * 25000
        seed = await client.post("/api/molbio/assembly/ligation/save", json={"fragments": [dict(id="s", name="s", sequence=source_dna)], "circular": False})
        assert seed.status_code == 200, seed.text
        source_id = seed.json()["saved_sequence"]["id"]
        calls, hashed = [], []
        owner = molbio_ops._assembly_source_revision
        sha256 = hashlib.sha256
        def count_hash(data):
            if data == source_dna.encode():
                hashed.append(len(data))
            return sha256(data)
        async def count_resolve(session, sequence_id, number):
            calls.append((sequence_id, number))
            return await owner(session, sequence_id, number)
        monkeypatch.setattr(molbio_ops, "hashlib", SimpleNamespace(sha256=count_hash))
        monkeypatch.setattr(molbio_ops, "_assembly_source_revision", count_resolve)
        fragments = [dict(id=str(i), name=str(i), sequence="ACGT", source_sequence_id=source_id,
                          source_revision=1 if i % 2 == 0 else None, source_start=0, source_end=4,
                          left_end={"type": "blunt"}, right_end={"type": "blunt"}) for i in range(12)]
        result = await client.post("/api/molbio/assembly/ligation/save?response_view=compact", json=dict(fragments=fragments, circular=False))
        assert result.status_code == 200, result.text
        assert len(calls) == len(hashed) == 1
        assert result.json()["product"]["sequence"] == "ACGT" * 12
        async with sessions() as session:
            edges = (await session.execute(select(MolecularOperationInput).where(MolecularOperationInput.role == "fragment"))).scalars().all()
            assert sum(edge.snapshot.get("fragment", {}).get("source_sequence_id") == source_id for edge in edges) == 12
        record_property("source_counters", json.dumps(dict(parts=12, resolutions=len(calls), whole_source_hashes=len(hashed), bytes_hashed=sum(hashed))))
        # A late mismatching instance must still fail despite sharing authority.
        fragments[-1]["sequence"] = "AAAA"
        bad = await client.post("/api/molbio/assembly/ligation/save?response_view=compact", json=dict(fragments=fragments, circular=False))
        assert bad.status_code == 409
        assert "attested source slice" in bad.text


@pytest.mark.asyncio
async def test_distinct_historical_revisions_and_tampered_authority(tmp_path, monkeypatch):
    from services.molbio_persistence import record_sequence_revision
    async with assembly_client(tmp_path) as (client, sessions, _):
        seed = await client.post("/api/molbio/assembly/ligation/save", json={
            "fragments": [dict(id="s", name="s", sequence="ACGT")], "circular": False})
        assert seed.status_code == 200, seed.text
        source_id = seed.json()["saved_sequence"]["id"]
        async with sessions.begin() as session:
            row = await session.get(NucleotideSequence, source_id)
            row.sequence = "TGCA"
            await record_sequence_revision(session, row, change_kind="edited")
        calls = []
        resolve = molbio_ops._assembly_source_revision
        async def counted(session, source_id, number):
            calls.append(number)
            return await resolve(session, source_id, number)
        monkeypatch.setattr(molbio_ops, "_assembly_source_revision", counted)
        fragments = [dict(id=str(i), name=str(i), sequence=dna, source_sequence_id=source_id,
                          source_revision=revision, left_end={"type": "blunt"}, right_end={"type": "blunt"})
                     for i, (revision, dna) in enumerate([(1, "ACGT"), (2, "TGCA"), (None, "TGCA"), (1, "ACGT")])]
        response = await client.post("/api/molbio/assembly/ligation/save?response_view=compact", json=dict(fragments=fragments, circular=False))
        assert response.status_code == 200, response.text
        assert calls == [None, 1]
        assert response.json()["product"]["sequence"] == "ACGTTGCATGCAACGT"
        # This store deliberately has no migration immutability triggers so a
        # corrupt retained authority can reach the unchanged reader check.
        async with sessions.begin() as session:
            await session.execute(update(MolecularRevision).where(
                MolecularRevision.document_id == source_id, MolecularRevision.revision_number == 1,
            ).values(content_sha256="0" * 64))
        bad = await client.post("/api/molbio/assembly/ligation/save?response_view=compact", json=dict(fragments=fragments, circular=False))
        assert bad.status_code == 409
        assert "immutable sequence is invalid" in bad.text


def test_golden_gate_orients_once_and_keeps_fragment_and_product_scans(monkeypatch, record_property):
    payload = gg_request()
    fragments = [molbio_ops.build_assembly_fragment(molbio_ops.AssemblyFragmentSchema(**f)) for f in payload["fragments"]]
    oriented, scanned = [], []
    orient = golden_gate.orient_fragment
    scan = golden_gate._site_count
    def counted_orient(fragment):
        oriented.append(fragment.id)
        return orient(fragment)
    def counted_scan(sequence, **kwargs):
        scanned.append(sequence)
        return scan(sequence, **kwargs)
    monkeypatch.setattr(golden_gate, "orient_fragment", counted_orient)
    monkeypatch.setattr(ligation, "orient_fragment", counted_orient)
    monkeypatch.setattr(golden_gate, "_site_count", counted_scan)
    product = golden_gate.simulate_golden_gate(fragments, circular=False, enzyme=golden_gate.resolve_golden_gate_enzyme(
        enzyme_id=payload["enzyme_id"], catalog_id=payload["catalog_id"], expected_catalog_sha256=payload["expected_catalog_sha256"]))
    assert product.sequence == "CATTAAAAACTCCGGGGG"
    assert len(oriented) == 2
    assert len(scanned) == 3
    assert scanned[-1] == product.sequence
    record_property("orientation_counters", json.dumps(dict(parts=2, orientations=len(oriented), scientific_scans=len(scanned))))
