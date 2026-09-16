"""Isolated Lane G pagination and SQL-projection regressions."""
import json

import httpx
import pytest
from sqlalchemy import event, select
from molbio_ngs_models import MolBioNGSMemberReceipt, MolBioNGSDomainStateMember, MolBioNGSDomainStateRevision
from experiment_models import ExperimentExternalEntityReceipt, ExperimentLineageEdge, ExperimentResource, ExperimentAuditEvent
from tests.test_molbio_project_hub_read_model import hub_stores, _app


@pytest.mark.asyncio
async def test_hub_pages_over_100_members_without_loss(hub_stores):
    ef, nf, mf, project, experiment, domain, revision = hub_stores
    # Retained unavailable native members are valid shelf entries, not a reason
    # to reject the whole hub. No scientific records or live stores are touched.
    async with nf() as session:
        original = await session.scalar(select(MolBioNGSMemberReceipt))
        values = {column.name: getattr(original, column.name) for column in original.__table__.columns}
        for index in range(1, 105):
            session.add(MolBioNGSMemberReceipt(**{**values, "receipt_id": f"retained-{index:03}",
                "entity_id": f"missing-{index:03}", "availability": "unavailable",
                "reopen_destination": json.dumps({"surface": "molbio-sequence-revision", "params": {"sequence_id": f"missing-seq-{index:03}", "revision_id": f"missing-{index:03}"}})}))
        await session.flush()
        session.add_all([MolBioNGSDomainStateMember(state_revision_id="state-current", receipt_id=f"retained-{i:03}", role="molecular_expected_construct", ordinal=i) for i in range(1, 105)])
        state = await session.get(MolBioNGSDomainStateRevision, "state-current")
        values = {column.name: getattr(state, column.name) for column in state.__table__.columns}
        session.add(MolBioNGSDomainStateRevision(**{**values, "id": "state-other", "revision_number": 2, "membership_graph_sha256": "b" * 64}))
        await session.commit()
    app = _app(ef, nf, mf)
    url = f"/api/projects/{project.id}/experiments/{experiment.id}/domains/{domain.id}/project-hub"
    seen, sizes, cursor = [], [], None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        while True:
            params = {"state_revision_id": "state-current"}
            if cursor: params["members_cursor"] = cursor
            response = await client.get(url, params=params)
            assert response.status_code == 200, response.text
            model = response.json()
            assert model["project"]["plasmid_count"] == 105
            assert model["pages"]["members"]["total_count"] == 105
            sizes.append(len(model["plasmids"]))
            seen.extend(row["receipt_id"] for row in model["plasmids"])
            cursor = model["pages"]["members"]["next_cursor"]
            if cursor:
                wrong_state = await client.get(url, params={"state_revision_id": "state-other", "members_cursor": cursor})
                assert wrong_state.status_code == 422
            if not cursor: break
    assert sizes == [50, 50, 5]
    assert len(seen) == len(set(seen)) == 105


@pytest.mark.asyncio
async def test_hub_receipt_activity_continuation_and_scalar_sql(hub_stores):
    ef, nf, mf, project, experiment, domain, revision = hub_stores
    async with ef() as session:
        for i in range(105):
            rid = f"page-op-{i:03}"
            session.add(ExperimentResource(id=rid, kind="external_entity_receipt", workspace_id=project.id, lifecycle_owner_id=project.id))
        await session.flush()
        for i in range(105):
            rid = f"page-op-{i:03}"
            session.add(ExperimentExternalEntityReceipt(id=rid, workspace_id=project.id, resource_id=rid, store_id="molbio", entity_kind="molecular_operation", entity_id=f"op-{i:03}", generation_or_revision="1", content_digest="a"*64, availability="available", verification_authority="test", acknowledgement_json='{}'))
            session.add(ExperimentLineageEdge(id=f"edge-{i}", workspace_id=project.id, source_resource_id=domain.id, target_resource_id=rid, edge_mode="attached", edge_key=f"page-{i}"))
            session.add(ExperimentAuditEvent(id=f"page-event-{i}", workspace_id=project.id, resource_id=domain.id, event_type="test", generation=1, payload_json='{}'))
        await session.commit()
    sql = []
    async with mf() as session:
        engine = session.bind.sync_engine
    def capture(_conn, _cursor, statement, _params, _context, _many): sql.append(statement)
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        app = _app(ef, nf, mf)
        url = f"/api/projects/{project.id}/experiments/{experiment.id}/domains/{domain.id}/project-hub"
        seen, activity, cursor, acursor = [], [], None, None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            while True:
                params = {"state_revision_id": "state-current"}
                if cursor: params['operations_cursor'] = cursor
                if acursor: params['activity_cursor'] = acursor
                response = await client.get(url, params=params)
                assert response.status_code == 200, response.text
                data = response.json()
                assert data['pages']['operations']['total_count'] == 107
                assert data['pages']['activity']['total_count'] == 107
                assert data['plasmids'][0]['saved_experiment_count_complete'] is False
                seen.extend(row['id'] for row in data['experiments'])
                activity.extend(row['id'] for row in data['activity'])
                cursor = data['pages']['operations']['next_cursor']
                acursor = data['pages']['activity']['next_cursor']
                if not cursor: break
        assert len(seen) == len(set(seen)) == 107
        assert len(activity) == len(set(activity)) == 107
        summary_queries = sum('json_object(' in statement for statement in sql)
        assert summary_queries == 3  # one shared attached/current summary per page, not two
        print(f'G summary queries: {summary_queries} across 3 hub pages (original overlap path: 6)')
        assert all('input_snapshot' not in statement for statement in sql)
        # Full snapshot column is only an argument to SQL JSON extraction; the
        # ORM never selects full snapshot/provenance/input content for display.
        assert all('molecular_revisions.snapshot AS molecular_revisions_snapshot' not in statement for statement in sql)
        assert all('molecular_operations.provenance' not in statement for statement in sql)
    finally:
        event.remove(engine, 'before_cursor_execute', capture)


@pytest.mark.asyncio
async def test_summary_excludes_large_sequence_before_python_hydration(hub_stores):
    from molbio_models import NucleotideSequence, MolecularRevision
    from services.molbio_persistence import record_sequence_revision
    from routers.ngs_molbio_n5 import _hub_revision_rows
    _ef, _nf, mf, _project, _experiment, _domain, _revision = hub_stores
    async with mf() as session:
        sequence = await session.get(NucleotideSequence, "sequence-pl1480")
        sequence.sequence = "ACGT" * 25000
        sequence.length = 100000
        sequence.version += 1
        revision = await record_sequence_revision(session, sequence, change_kind="edit")
        await session.commit()
        # Original hub hydrated this entire JSON column. Compare real bytes,
        # retaining the database-owned scientific record unchanged.
        old_snapshot = await session.scalar(select(MolecularRevision.snapshot).where(MolecularRevision.id == revision.id))
        summary = (await _hub_revision_rows(session, [revision.id]))[revision.id].snapshot
        old_bytes = len(json.dumps(old_snapshot).encode())
        summary_bytes = len(json.dumps(summary).encode())
        assert summary["features"] == old_snapshot["features"]
        assert summary["name"] == old_snapshot["name"]
        assert "sequence" not in summary
        assert summary_bytes < old_bytes / 100
        print(f"G projection bytes: original={old_bytes} summary={summary_bytes}; sequence_bp=100000")
