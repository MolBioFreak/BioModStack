"""Lane D SQL projection/page and immutable inline-PCR integration tests."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
import threading

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from routers import molbio_ops as api
from services.molbio_ops import reverse_complement
from molbio_database import create_molbio_engine, init_molbio_db, make_molbio_session_factory
from molbio_models import MolecularRevision
from database import Base, Job, MolBioNgsReceipt


@pytest.mark.asyncio
async def test_pcr_summary_pages_join_current_and_preserve_exact_reopening(tmp_path):
    engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'pcr.db'}")
    await init_molbio_db(engine=engine)
    sessions = make_molbio_session_factory(engine)
    sequence = "ATGCGTACGTTAGCTAGCTAGGCTAACCGGTTACGATCGATCGTACGTTAGC"
    ids = []
    try:
        async with sessions() as session:
            for i in range(3):
                request = api.PCRRequest(sequence=sequence, name=f"edited inline {i}", sequence_type="dna",
                    primer_fwd=sequence[:12], primer_rev=reverse_complement(sequence[-12:]),
                    save=False, persist_experiment=True, idempotency_key=f"inline-{i}")
                result = await api.pcr(request, session)
                ids.append(result.experiment_id)
            await api.update_pcr_experiment_review_state(ids[0], api.PCRReviewStateRequest(review_state="in_review"), session)
        queries = []
        def capture(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                queries.append(statement)
        event.listen(engine.sync_engine, "before_cursor_execute", capture)
        async with sessions() as session:
            first = await api.list_pcr_experiments(2, session, summary=True)
            assert len(queries) == 1
            assert "template_snapshot" not in queries[0]
            assert "product_snapshot" not in queries[0]
            assert first["has_more"] and first["next_offset"] == 2
            second = await api.list_pcr_experiments(2, session, offset=2, summary=True)
            assert not second["has_more"]
            assert {x["id"] for x in first["items"] + second["items"]} == set(ids)
            current = first["items"][0]["current_revision"]
            assert "template_snapshot" not in current and "payload_sha256" not in current
            history1 = await api.get_pcr_experiment(ids[0], session, limit=1, summary=True)
            history2 = await api.get_pcr_experiment(ids[0], session, limit=1, offset=1, summary=True)
            assert history1["next_offset"] == 1 and not history2["has_more"]
            assert [history1["revisions"][0]["revision_number"], history2["revisions"][0]["revision_number"]] == [2, 1]
        # A new session prevents identity-map snapshots masking projection loads.
        async with sessions() as session:
            page1 = await api.list_pcr_experiment_revisions(ids[0], 1, session)
            page2 = await api.list_pcr_experiment_revisions(ids[0], 1, session, before_revision=page1[0].revision_number)
            assert page1[0].parent_revision_id == page2[0].id
            exact = await api.get_pcr_experiment_revision(ids[0], page2[0].id, session)
            assert exact.template_snapshot["sequence"] == sequence
            template_id = exact.template_document_id
            # Inline private template has immutable authority but no mutable shelf row.
            loaded = []
            def on_load(target, context):
                loaded.append(target.id)
            event.listen(MolecularRevision, "load", on_load)
            try:
                summary = await api.list_sequence_revisions(template_id, session, limit=1)
                assert loaded == []
                assert summary[0]["topology"] == "linear"
                detail = await api.get_sequence_revision(template_id, summary[0]["revision_id"], session)
                assert detail["snapshot"]["sequence"] == sequence
            finally:
                event.remove(MolecularRevision, "load", on_load)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_workup_sql_limits_related_receipts_and_advances_invalid_page(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'core.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: Base.metadata.create_all(conn, tables=[Job.__table__, MolBioNgsReceipt.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            for i in range(100):
                session.add(Job(id=f"unrelated-{i}", name="unrelated", model_id="nanopore", mode="test", params={}))
            for job_id in ["a-invalid", "b-valid", "c-valid"]:
                binding = dict(receipt_id=job_id, sequence_id="seq", revision_id="rev", revision_sha256="a" * 64,
                               reference_snapshot_sha256="b" * 64)
                session.add(Job(id=job_id, name=job_id, model_id="nanopore", mode="test", params={"molbio_revision_binding": binding}))
                session.add(MolBioNgsReceipt(id=job_id, consumed_job_id=job_id, sequence_id="seq", revision_id="wrong" if job_id == "a-invalid" else "rev",
                    revision_sha256="a" * 64, reference_snapshot_path="unused", reference_snapshot_sha256="b" * 64, expires_at=datetime(2030, 1, 1)))
            await session.commit()
        revision = SimpleNamespace(id="rev", content_sha256="a" * 64)
        molbio = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id="seq")))
        monkeypatch.setattr(api, "current_molecular_revision", AsyncMock(return_value=revision))
        projections = []
        main_thread = threading.get_ident()
        def project(job, revision, panel):
            assert threading.get_ident() != main_thread
            projections.append(job.id)
            return {"job_id": job.id}
        monkeypatch.setattr(api, "_project_job_ngs_workup", project)
        loaded = []
        def on_load(target, context):
            loaded.append(target.id)
        event.listen(Job, "load", on_load)
        try:
            async with sessions() as session:
                first = await api.get_sequence_ngs_workup("seq", molbio, session, limit=1)
                assert first["workups"] == []
                assert first["has_more"] and first["next_offset"] == 1
                assert set(loaded) == {"a-invalid", "b-valid"}
                second = await api.get_sequence_ngs_workup("seq", molbio, session, limit=1, offset=first["next_offset"])
                third = await api.get_sequence_ngs_workup("seq", molbio, session, limit=1, offset=second["next_offset"])
                assert not third["has_more"] and third["next_offset"] is None
                assert projections == ["b-valid", "c-valid"]
                assert all(not item.startswith("unrelated") for item in loaded)
        finally:
            event.remove(Job, "load", on_load)
    finally:
        await engine.dispose()
