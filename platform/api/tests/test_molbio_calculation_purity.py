"""Calculation previews do not prepare persistence-only identities."""
from types import SimpleNamespace

import httpx
import pytest
from Bio.Seq import Seq
from fastapi import FastAPI
from sqlalchemy import event, select

from molbio_database import create_molbio_engine, get_molbio_session, init_molbio_db, make_molbio_session_factory
from molbio_models import NucleotideSequence, PCRExperimentRevision
from routers import molbio_ops
from services.molbio_persistence import record_sequence_revision

TEMPLATE = "ATGCGTACGTTAGCTAGCTAGGCTAACCGGTTACGATCGATCGTACGTTAGC"


def forbidden(*args, **kwargs):
    raise AssertionError("Calculation prepared an unused persistence identity")


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", [False, True])
@pytest.mark.parametrize("save", [False, True])
async def test_pcr_without_experiment_skips_receipt_work(tmp_path, monkeypatch, stored, save):
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'molbio.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    if stored:
        async with sessions() as session:
            template = NucleotideSequence(
                id="template", name="Template", sequence=TEMPLATE, sequence_type="dna",
                is_circular=False, length=len(TEMPLATE), features=[], primers=[],
            )
            session.add(template)
            await record_sequence_revision(session, template, change_kind="create", provenance={})
            await session.commit()

    async def session_override():
        async with sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(molbio_ops.router)
    app.dependency_overrides[get_molbio_session] = session_override
    for name in ("current_molecular_revision", "sequence_snapshot", "canonical_request_fingerprint", "tm_model_revision_identity", "persist_pcr_experiment"):
        monkeypatch.setattr(molbio_ops, name, forbidden)
    # Replace only the router's namespace, not hashlib used by real save owners.
    monkeypatch.setattr(molbio_ops, "hashlib", SimpleNamespace(sha256=forbidden))
    statements = []
    event.listen(engine.sync_engine, "before_cursor_execute", lambda conn, cursor, statement, parameters, context, many: statements.append(statement))
    payload = {
        **({"sequence_id": "template"} if stored else {"sequence": TEMPLATE, "sequence_type": "dna"}),
        "primer_fwd": TEMPLATE[:12], "primer_rev": str(Seq(TEMPLATE[-12:]).reverse_complement()),
        "save": save, "persist_experiment": False, "new_name": "Product",
    }
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
            response = await client.post("/api/molbio/pcr", json=payload)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["product"]["sequence"] == TEMPLATE
        assert result["product"]["length"] == len(TEMPLATE)
        assert result["experiment_id"] is None
        assert not any("pcr_experiment" in statement.lower() for statement in statements)
        if save:
            assert result["sequence"]["sequence"] == TEMPLATE
            async with sessions() as session:
                saved = await session.get(NucleotideSequence, result["sequence"]["id"])
                assert saved is not None and saved.sequence == TEMPLATE
                assert not (await session.scalars(select(PCRExperimentRevision))).all()
        else:
            assert result["sequence"] is None and result["operation_id"] is None
            assert not any(statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for statement in statements)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_gibson_simulation_remains_a_pure_calculation(monkeypatch):
    for name in ("canonical_request_fingerprint", "_retain_assembly_result", "create_operation"):
        monkeypatch.setattr(molbio_ops, name, forbidden)
    monkeypatch.setattr(molbio_ops, "hashlib", SimpleNamespace(sha256=forbidden))
    app = FastAPI()
    app.include_router(molbio_ops.router)
    overlap = "ACGTTAGCTAGGCTAACCTGATCGG"
    payload = {
        "fragments": [
            {"id": "left", "name": "Left", "sequence": "AAAA" + overlap},
            {"id": "right", "name": "Right", "sequence": overlap + "GGGG"},
        ],
        "circular": False, "minimum_overlap": 16,
        "preferred_overlap": len(overlap), "maximum_overlap": len(overlap),
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        response = await client.post("/api/molbio/assembly/gibson/simulate", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["product"]["sequence"] == "AAAA" + overlap + "GGGG"
