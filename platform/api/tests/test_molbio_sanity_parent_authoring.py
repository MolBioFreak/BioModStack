"""Shared reference authoring and historical-public-writer retirement."""
from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select

from molbio_database import create_molbio_engine, make_molbio_session_factory, run_molbio_migrations
from molbio_models import MolecularRevision
from routers import molbio_ngs_experiments, molbio_ops, nucleotide_sequences


@pytest_asyncio.fixture
async def authoring_client(tmp_path):
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'molbio.db'}")
    await run_molbio_migrations(engine=engine)
    factory = make_molbio_session_factory(engine)
    app = FastAPI()
    app.include_router(molbio_ngs_experiments.router)
    app.include_router(nucleotide_sequences.router)
    app.include_router(molbio_ops.router)

    async def session():
        async with factory() as value:
            yield value

    async def forbidden_domain_write():
        pytest.fail("retired authoring opened the Domain store")
        yield None

    app.dependency_overrides[nucleotide_sequences.get_molbio_session] = session
    app.dependency_overrides[molbio_ops.get_molbio_session] = session
    app.dependency_overrides[molbio_ngs_experiments.get_molbio_ngs_session] = forbidden_domain_write
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client, factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_retired_domain_writers_have_no_store_side_effect(authoring_client):
    client, factory = authoring_client
    for path in (
        "/api/molbio-ngs/references",
        "/api/molbio-ngs/references/from-molbio-revision",
        "/api/molbio-ngs/references/import-browser-entry",
        "/api/molbio-ngs/references/historical-reference/revisions",
    ):
        response = await client.post(path, json={"fasta": ">fixture\nACGT\n"})
        assert response.status_code == 410
        assert response.json()["detail"]["code"] == "domain_reference_authoring_retired"
        assert response.json()["detail"]["dna_import"] == "/api/molbio/sequences/import/commit"
    async with factory() as session:
        assert (await session.execute(select(func.count()).select_from(MolecularRevision))).scalar_one() == 0


@pytest.mark.asyncio
async def test_shared_dna_import_retains_every_record_and_exact_replay(authoring_client):
    client, factory = authoring_client
    payload = {
        "source_format": "fasta", "source_text": ">dna-one\nATCG\n>dna-two\nGGCC\n",
        "topology_default": "linear", "idempotency_key": "ngs-reference-import",
        "origin_surface": "ngs", "source_provider": "paste",
    }
    first = await client.post("/api/molbio/sequences/import/commit", json=payload)
    assert first.status_code == 201, first.text
    records = first.json()["records"]
    assert len(records) == 2
    assert {r["sequence"] for r in records} == {"ATCG", "GGCC"}
    assert all(r["revision_id"] and r["topology"] == "linear" for r in records)
    second = await client.post("/api/molbio/sequences/import/commit", json=payload)
    assert second.status_code == 201, second.text
    assert second.json()["records"] == records
    async with factory() as session:
        assert (await session.execute(select(func.count()).select_from(MolecularRevision))).scalar_one() == 2


@pytest.mark.asyncio
async def test_shared_rna_creation_preserves_uracil_and_topology(authoring_client):
    client, factory = authoring_client
    result = await client.post("/api/sequences/", json={
        "name": "RNA reference", "sequence": "AUGCUN", "sequence_type": "rna", "is_circular": True,
    })
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["sequence"] == "AUGCUN"
    assert body["sequence_type"] == "rna"
    assert body["is_circular"] is True
    reopened = await client.get(f"/api/sequences/{body['id']}")
    assert reopened.status_code == 200
    assert reopened.json()["sequence"] == "AUGCUN"
    async with factory() as session:
        revision = (await session.execute(select(MolecularRevision))).scalar_one()
        assert revision.snapshot["sequence"] == "AUGCUN"
        assert revision.snapshot["sequence_type"] == "rna"
        assert revision.snapshot["is_circular"] is True
