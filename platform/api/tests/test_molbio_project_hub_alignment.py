from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select

from molbio_database import create_molbio_engine, get_molbio_session, make_molbio_session_factory
from molbio_models import MolBioBase, MolecularOperation, MolecularOperationInput, NucleotideSequence
from routers.molbio_ops import router
from services.molbio_persistence import record_sequence_revision


@pytest_asyncio.fixture
async def alignment_store(tmp_path):
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'molbio.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(MolBioBase.metadata.create_all)
    factory = make_molbio_session_factory(engine)
    revision_ids = {}
    async with factory() as session:
        for sequence_id, name, sequence in (
            ("reference-sequence", "Reference", "AACCGGTT"),
            ("query-sequence", "Query", "AACCGATT"),
        ):
            row = NucleotideSequence(
                id=sequence_id,
                name=name,
                description=None,
                sequence=sequence,
                sequence_type="dna",
                molecule_strandedness="double",
                molecule_orientation="forward",
                is_circular=False,
                length=len(sequence),
                features=[],
                primers=[],
                analysis_tracks=[],
                version=1,
            )
            session.add(row)
            revision = await record_sequence_revision(session, row, change_kind="create")
            revision_ids[sequence_id] = revision.id
        await session.commit()
    try:
        yield factory, revision_ids
    finally:
        await engine.dispose()


def _app(factory) -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_molbio_session] = override_session
    return app


@pytest.mark.asyncio
async def test_alignment_is_stateless_by_default_and_persists_exact_lineage_only_on_explicit_save(alignment_store):
    factory, revision_ids = alignment_store
    app = _app(factory)
    stateless_request = {
        "reference_name": "Reference",
        "reference_sequence": "AACCGGTT",
        "query_name": "Query",
        "query_sequence": "AACCGATT",
        "settings": {"mode": "global", "strand": "forward"},
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        stateless = await client.post("/api/molbio/alignment", json=stateless_request)
        saved = await client.post(
            "/api/molbio/alignment/save",
            json={
                "title": "Reference vs Query",
                "reference_sequence_id": "reference-sequence",
                "reference_revision_id": revision_ids["reference-sequence"],
                "query_sequence_id": "query-sequence",
                "query_revision_id": revision_ids["query-sequence"],
                "settings": {"mode": "global", "strand": "forward"},
                "idempotency_key": "alignment-save-1",
            },
        )

    assert stateless.status_code == 200, stateless.text
    assert saved.status_code == 201, saved.text
    payload = saved.json()
    assert payload["persistence"] == "saved"
    assert payload["operation_kind"] == "alignment"
    assert payload["variant_count"] == 1
    assert payload["reopen_href"].endswith(f"molbio_operation_id={payload['operation_id']}")
    assert "reference_aligned" not in payload
    assert "query_aligned" not in payload

    async with factory() as session:
        operations = list((await session.scalars(select(MolecularOperation))).all())
        inputs = list((await session.scalars(select(MolecularOperationInput).order_by(MolecularOperationInput.position))).all())
        assert len(operations) == 1
        assert operations[0].id == payload["operation_id"]
        assert [item.role for item in inputs] == ["reference", "query"]
        assert await session.scalar(select(func.count()).select_from(MolecularOperation)) == 1


@pytest.mark.asyncio
async def test_saved_alignment_idempotency_replays_and_conflicting_payload_is_rejected(alignment_store):
    factory, revision_ids = alignment_store
    app = _app(factory)
    request = {
        "title": "Reference vs Query",
        "reference_sequence_id": "reference-sequence",
        "reference_revision_id": revision_ids["reference-sequence"],
        "query_sequence_id": "query-sequence",
        "query_revision_id": revision_ids["query-sequence"],
        "settings": {"mode": "global", "strand": "forward"},
        "idempotency_key": "alignment-save-replay",
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/molbio/alignment/save", json=request)
        replay = await client.post("/api/molbio/alignment/save", json=request)
        conflict = await client.post("/api/molbio/alignment/save", json={**request, "title": "Different title"})

    assert first.status_code == 201, first.text
    assert replay.status_code == 201, replay.text
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(MolecularOperation)) == 1
