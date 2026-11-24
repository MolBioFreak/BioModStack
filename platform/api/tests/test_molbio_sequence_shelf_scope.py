"""Sequence-shelf scope uses current membership, before recent-list pagination."""
import httpx
import pytest

from molbio_models import NucleotideSequence
from molbio_ngs_models import MolBioNGSDomainState, MolBioNGSDomainStateMember, MolBioNGSDomainStateRevision
from routers.nucleotide_sequences import router as sequences_router
from services.molbio_ngs_member_receipts import persist_member_receipt, resolve_molecular_revision_receipt
from services.molbio_persistence import record_sequence_revision
from tests.test_molbio_project_hub_read_model import hub_stores, _app


@pytest.mark.asyncio
async def test_unassigned_scope_filters_before_paging_across_candidate_batches(hub_stores):
    experiments, native, molbio, _project, _experiment, _domain, _revision = hub_stores
    assigned_ids = [f"assigned-{index:03}" for index in range(101)]
    async with molbio() as session:
        for sequence_id in [*assigned_ids, "orphan-0", "orphan-1", "orphan-2"]:
            sequence = NucleotideSequence(
                id=sequence_id, name=f"{'A' if sequence_id in assigned_ids else 'Z'}-{sequence_id}",
                sequence="ACGT", sequence_type="dna", is_circular=False, length=4,
                features=[], primers=[], analysis_tracks=[], version=1,
            )
            session.add(sequence)
            await record_sequence_revision(session, sequence, change_kind="create")
        # A Project's attached revision remains authoritative for membership even
        # after the editable sequence advances to a different revision.
        project_sequence = await session.get(NucleotideSequence, "sequence-pl1480")
        project_sequence.name = "Updated project sequence"
        await record_sequence_revision(session, project_sequence, change_kind="update")
        await session.commit()
    async with native() as native_session, molbio() as session:
        from molbio_models import MolecularDocument
        for ordinal, sequence_id in enumerate(assigned_ids, start=1):
            document = await session.get(MolecularDocument, sequence_id)
            resolved = await resolve_molecular_revision_receipt(session, sequence_id=sequence_id, revision_id=document.current_revision_id)
            receipt = await persist_member_receipt(native_session, resolved)
            native_session.add(MolBioNGSDomainStateMember(
                state_revision_id="state-current", receipt_id=receipt.receipt_id,
                role="molecular_expected_construct", ordinal=ordinal,
            ))
        await native_session.commit()
    app = _app(experiments, native, molbio)
    app.include_router(sequences_router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        params = {"project_membership": "unassigned", "sort_by": "name", "sort_desc": False}
        response = await client.get("/api/sequences/", params={**params, "limit": 1, "offset": 1})
        assert response.status_code == 200
        assert response.headers["x-bns-sequence-scope"] == "unassigned"
        assert [item["id"] for item in response.json()] == ["orphan-1"]
        response = await client.get("/api/sequences/", params={**params, "sequence_ids": "sequence-pl1480,orphan-2"})
        assert response.status_code == 200
        assert [item["id"] for item in response.json()] == ["orphan-2"]
        response = await client.get("/api/sequences/", params={"sequence_ids": "sequence-pl1480"})
        assert [item["id"] for item in response.json()] == ["sequence-pl1480"]


@pytest.mark.asyncio
async def test_historical_membership_does_not_hide_a_now_unassigned_sequence(hub_stores):
    experiments, native, molbio, _project, _experiment, domain, _revision = hub_stores
    async with native() as session:
        current = await session.get(MolBioNGSDomainStateRevision, "state-current")
        values = {column.name: getattr(current, column.name) for column in current.__table__.columns}
        values.update(id="state-detached", revision_number=2, parent_revision_id=current.id, membership_graph_sha256="n" * 64)
        session.add(MolBioNGSDomainStateRevision(**values))
        await session.flush()
        head = await session.get(MolBioNGSDomainState, domain.id)
        head.current_state_revision_id = "state-detached"
        await session.commit()
    app = _app(experiments, native, molbio)
    app.include_router(sequences_router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/sequences/", params={"project_membership": "unassigned"})
        assert response.status_code == 200
        assert [item["id"] for item in response.json()] == ["sequence-pl1480"]
        assert (await client.get("/api/sequences/", params={"project_membership": "unknown"})).status_code == 422
