from __future__ import annotations

import hashlib
import json

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text

from experiment_database import create_experiment_engine, create_experiment_session_factory, get_experiment_session
from experiment_models import ExperimentAuditEvent, ExperimentBase, ExperimentExternalEntityReceipt, ExperimentLineageEdge, ExperimentResource
from experiment_services import create_domain_experiment, create_global_experiment, create_project
from molbio_database import create_molbio_engine, get_molbio_session, make_molbio_session_factory
from molbio_models import MolBioBase, MolecularDocument, MolecularRevision, NucleotideSequence, ProjectPlasmidMetadata
from molbio_ngs_database import create_molbio_ngs_engine, create_molbio_ngs_session_factory, get_molbio_ngs_session
from molbio_ngs_models import MolBioNGSBase, MolBioNGSDomainState, MolBioNGSDomainStateMember, MolBioNGSDomainStateRevision, MolBioNGSGlobalBinding, MolBioNGSMemberReceipt
from routers.ngs_molbio_n5 import router
from services.molbio_ngs_member_receipts import persist_member_receipt, resolve_molecular_revision_receipt
from services.molbio_persistence import add_operation_edges, create_operation, record_sequence_revision


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


@pytest_asyncio.fixture
async def hub_stores(tmp_path):
    experiment_engine = create_experiment_engine(f"sqlite+aiosqlite:///{tmp_path / 'experiment.db'}")
    native_engine = create_molbio_ngs_engine(f"sqlite+aiosqlite:///{tmp_path / 'native.db'}")
    molbio_engine = create_molbio_engine(f"sqlite+aiosqlite:///{tmp_path / 'molbio.db'}")
    async with experiment_engine.begin() as connection:
        await connection.run_sync(ExperimentBase.metadata.create_all)
    async with native_engine.begin() as connection:
        await connection.run_sync(MolBioNGSBase.metadata.create_all)
    async with molbio_engine.begin() as connection:
        await connection.run_sync(MolBioBase.metadata.create_all)
    experiment_factory = create_experiment_session_factory(experiment_engine)
    native_factory = create_molbio_ngs_session_factory(native_engine)
    molbio_factory = make_molbio_session_factory(molbio_engine)

    async with experiment_factory() as experiment_session:
        project = await create_project(experiment_session, {
            "schema": "bms.project.v1", "name": "Syenex New Plasmids", "description": "Routine new plasmid onboarding.",
            "research_objective": "Onboard plasmids", "owner": "operator", "contributors": [], "tags": [], "status": "active", "needs_metadata_review": False,
            "start_date": None, "target_end_date": None, "external_references": [], "created_by": "operator", "change_summary": "created",
        })
        experiment = await create_global_experiment(experiment_session, project.id, {
            "schema": "bms.global-experiment.v1", "name": "Onboarding", "objective": "Onboard plasmids", "scientific_question": "Are plasmids ready?",
            "hypothesis": None, "description": "", "status": "active", "priority": "normal", "tags": [], "shared_source_receipt_ids": [],
            "shared_dataset_ids": [], "comparison_plan": None, "success_criteria": ["Plasmids are ready"], "review_summary": None, "conclusion": None,
            "needs_metadata_review": False,
            "created_by": "operator", "change_summary": "created",
        })
        domain = await create_domain_experiment(experiment_session, project.id, experiment.id, {
            "schema": "bms.domain-experiment.v1", "domain_kind": "ngs_molbio", "domain_contract_version": "1", "name": "Plasmid verification",
            "objective": "Onboard plasmids", "status": "active", "tags": [], "source_receipt_ids": [], "dataset_ids": [], "created_by": "operator",
            "change_summary": "created", "domain_payload": {"schema": "bms.ngs-molbio-experiment.v1"},
        })
        await experiment_session.commit()

    async with molbio_factory() as molbio_session:
        sequence = NucleotideSequence(
            id="sequence-pl1480", name="PL1480", description="Synthetic circular DNA", sequence="AACCGGTT", sequence_type="dna",
            molecule_strandedness="double", molecule_orientation="forward", is_circular=True, length=8,
            features=[{"name": "CMV promoter", "type": "promoter", "start": 0, "end": 4}, {"name": "NeoR/KanR", "type": "CDS", "start": 4, "end": 8}],
            primers=[], analysis_tracks=[], organism=None, version=1, gc_content=50.0,
        )
        molbio_session.add(sequence)
        revision = await record_sequence_revision(molbio_session, sequence, change_kind="create")
        operation = await create_operation(
            molbio_session, operation_kind="alignment", implementation="test", parameters={"summary": {"title": "Saved alignment", "variant_count": 1}},
            provenance={"save_contract": "explicit"}, idempotency_key="saved-op", request_fingerprint="a" * 64,
        )
        await add_operation_edges(molbio_session, operation, input_revisions=[(revision, "reference", None)])
        await molbio_session.commit()

    async with native_factory() as native_session, molbio_factory() as molbio_session:
        resolved = await resolve_molecular_revision_receipt(molbio_session, sequence_id=sequence.id, revision_id=revision.id)
        receipt = await persist_member_receipt(native_session, resolved)
        await native_session.commit()
        receipt_id = receipt.receipt_id

    payload = {"schema": "bms.molbio-ngs.domain-state-revision.v1", "design": {"sample_revision_ids": [], "conditions": [], "replicates": [], "expected_molecule_roles": ["molecular_expected_construct"]}, "reference_policy": {"required_roles": ["molecular_expected_construct"], "coordinate_policy": "exact_revision"}, "acquisition_policy": {"platform": "ont", "required_terminal_manifest": True}, "analysis_policy": {"allowed_workflow_ids": [], "required_manifest_schemas": []}, "assessment_policy": {"rule_id": "server-owned-rule", "completion_is_scientific_pass": False}, "notes": ""}
    async with native_engine.connect() as connection:
        await connection.execute(text("PRAGMA foreign_keys=OFF"))
        await connection.execute(text("INSERT INTO molbio_ngs_domain_states(global_domain_experiment_id,current_state_revision_id,current_binding_revision_id,head_generation,created_at,updated_at) VALUES (:domain,'state-current','binding-1',1,'2026-08-27T12:00:00Z','2026-08-27T12:00:00Z')"), {"domain": domain.id})
        await connection.execute(text("""INSERT INTO molbio_ngs_global_binding_revisions(binding_revision_id,global_domain_experiment_id,revision_number,global_domain_experiment_revision_id,global_domain_experiment_revision_digest,project_id,project_generation,project_digest,project_receipt_id,project_reopen_destination,project_acknowledgement,global_experiment_id,global_experiment_generation,global_experiment_digest,global_experiment_receipt_id,global_experiment_reopen_destination,global_experiment_acknowledgement,binding_state,created_at) VALUES ('binding-1',:domain,1,:domain_revision,:domain_digest,:project,'1',:project_digest,'project-receipt','{}','{}',:experiment,'1',:experiment_digest,'experiment-receipt','{}','{}','acknowledged','2026-08-27T12:00:00Z')"""), {"domain": domain.id, "domain_revision": domain.current_revision_id, "domain_digest": "d" * 64, "project": project.id, "project_digest": "p" * 64, "experiment": experiment.id, "experiment_digest": "e" * 64})
        await connection.execute(text("""INSERT INTO molbio_ngs_domain_state_revisions(id,global_domain_experiment_id,global_domain_experiment_revision_id,binding_revision_id,revision_number,parent_revision_id,schema_name,schema_version,canonical_payload,payload_sha256,membership_graph_sha256,created_at) VALUES ('state-current',:domain,:domain_revision,'binding-1',1,NULL,'bms.molbio-ngs.domain-state-revision','1',:payload,:payload_digest,:membership_digest,'2026-08-27T12:00:00Z')"""), {"domain": domain.id, "domain_revision": domain.current_revision_id, "payload": _canonical(payload), "payload_digest": hashlib.sha256(_canonical(payload).encode()).hexdigest(), "membership_digest": "m" * 64})
        await connection.execute(text("INSERT INTO molbio_ngs_domain_state_members(state_revision_id,receipt_id,role,ordinal,created_at) VALUES ('state-current',:receipt,'molecular_expected_construct',0,'2026-08-27T12:00:00Z')"), {"receipt": receipt_id})
        await connection.commit()
        await connection.execute(text("PRAGMA foreign_keys=ON"))

    async with experiment_factory() as experiment_session:
        acknowledgement = {"reopen_uri": f"/designer?molbio_operation_id={operation.id}", "metadata": {"title": "Saved alignment"}}
        for receipt_id, kind, entity_id, digest, ack in (
            ("operation-receipt", "molecular_operation", operation.id, "1" * 64, acknowledgement),
            ("pcr-receipt", "pcr_experiment_revision", "pcr-revision-1", "4" * 64, {"reopen_uri": "/designer?pcr_experiment_id=pcr-1&pcr_revision_id=pcr-revision-1", "metadata": {"title": "Saved PCR", "plasmid_sequence_id": sequence.id}}),
            ("result-receipt", "ngs_result_manifest", "result-1", "2" * 64, {"reopen_uri": "/ngs?job_id=job-1", "metadata": {"title": "Clone assessment", "summary": "Persisted clone result", "status": "ready", "plasmid_sequence_id": sequence.id}}),
            ("unlinked-operation", "molecular_operation", "global-only", "3" * 64, {"reopen_uri": "/designer?molbio_operation_id=global-only"}),
        ):
            experiment_session.add(ExperimentResource(id=receipt_id, kind="external_entity_receipt", workspace_id=project.id, lifecycle_owner_id=project.id))
            await experiment_session.flush()
            experiment_session.add(ExperimentExternalEntityReceipt(id=receipt_id, workspace_id=project.id, resource_id=receipt_id, store_id="molbio" if kind == "molecular_operation" else "core", entity_kind=kind, entity_id=entity_id, generation_or_revision="1", content_digest=digest, availability="available", verification_authority="test", acknowledgement_json=_canonical(ack)))
        experiment_session.add_all([
            ExperimentLineageEdge(id="operation-edge", workspace_id=project.id, source_resource_id=domain.id, target_resource_id="operation-receipt", edge_mode="attached", edge_key="operation"),
            ExperimentLineageEdge(id="pcr-edge", workspace_id=project.id, source_resource_id=domain.id, target_resource_id="pcr-receipt", edge_mode="attached", edge_key="pcr"),
            ExperimentLineageEdge(id="result-edge", workspace_id=project.id, source_resource_id=domain.id, target_resource_id="result-receipt", edge_mode="attached", edge_key="result"),
            ExperimentAuditEvent(id="activity-1", workspace_id=project.id, resource_id=domain.id, event_type="molecular_member_attached", generation=1, payload_json=_canonical({"receipt_id": receipt.receipt_id, "sequence_id": sequence.id, "name": sequence.name})),
        ])
        await experiment_session.commit()

    try:
        yield (experiment_factory, native_factory, molbio_factory, project, experiment, domain, revision)
    finally:
        await experiment_engine.dispose()
        await native_engine.dispose()
        await molbio_engine.dispose()


def _app(experiment_factory, native_factory, molbio_factory):
    app = FastAPI()
    app.include_router(router)
    async def experiments():
        async with experiment_factory() as session:
            yield session
    async def native():
        async with native_factory() as session:
            yield session
    async def molbio():
        async with molbio_factory() as session:
            yield session
    app.dependency_overrides[get_experiment_session] = experiments
    app.dependency_overrides[get_molbio_ngs_session] = native
    app.dependency_overrides[get_molbio_session] = molbio
    return app


@pytest.mark.asyncio
async def test_project_hub_read_model_is_exact_linked_typed_and_bulk_free(hub_stores):
    experiment_factory, native_factory, molbio_factory, project, experiment, domain, revision = hub_stores
    app = _app(experiment_factory, native_factory, molbio_factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/projects/{project.id}/experiments/{experiment.id}/domains/{domain.id}/project-hub", params={"state_revision_id": "state-current"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema"] == "bms.project-hub.v1"
    assert payload["project"]["name"] == "Syenex New Plasmids"
    assert payload["identity"]["selected_state_revision_id"] == "state-current"
    assert payload["identity"]["current_state_revision_id"] == "state-current"
    assert payload["plasmids"][0]["sequence_id"] == "sequence-pl1480"
    assert payload["plasmids"][0]["revision_id"] == revision.id
    assert payload["plasmids"][0]["receipt_id"]
    assert payload["plasmids"][0]["receipt_sha256"]
    assert payload["plasmids"][0]["content_digest"]
    assert payload["plasmids"][0]["source_store_id"] == "molbio"
    assert payload["plasmids"][0]["schema_name"]
    reopen_href = payload["plasmids"][0]["reopen_href"]
    assert f"workspace_id={project.id}" in reopen_href
    assert f"global_experiment_id={experiment.id}" in reopen_href
    assert f"domain_experiment_id={domain.id}" in reopen_href
    assert "state_revision_id=state-current" in reopen_href
    assert "section=plasmids" in reopen_href
    assert "molbio_sequence_id=sequence-pl1480" in reopen_href
    assert f"molbio_revision_id={revision.id}" in reopen_href
    assert payload["plasmids"][0]["saved_experiment_count"] == 2
    assert {item["title"] for item in payload["experiments"]} == {"Saved alignment", "Saved PCR"}
    assert all(item["plasmid_sequence_ids"] == ["sequence-pl1480"] for item in payload["experiments"])
    assert [item["summary"] for item in payload["results"]] == ["Persisted clone result"]
    assert payload["sequence_data"]["items"] == []
    assert payload["activity"][0]["summary"] == "PL1480 added to the project"
    encoded = response.content.lower()
    for forbidden in (b"aaccggtt", b"fastq", b"pod5", b"blow5", b"reference_aligned", b"query_aligned"):
        assert forbidden not in encoded
    assert len(response.content) < 256 * 1024


@pytest.mark.asyncio
async def test_project_hub_edit_info_atomically_advances_molecular_and_domain_revisions(hub_stores):
    experiment_factory, native_factory, molbio_factory, project, experiment, domain, revision = hub_stores
    app = _app(experiment_factory, native_factory, molbio_factory)
    request = {
        "expected_molecular_revision_id": revision.id,
        "expected_state_revision_id": "state-current",
        "expected_state_head_generation": 1,
        "idempotency_key": "edit-pl1480-1",
        "molecular_fields": {
            "name": "PL1480 renamed", "molecule_type": "dna", "topology": "circular",
            "description": "Updated project plasmid", "organism_host_context": "E. coli",
        },
        "project_metadata": {"project_tags": ["priority", "onboarding"], "project_notes": "Verify clone 4"},
    }
    url = f"/api/projects/{project.id}/experiments/{experiment.id}/domains/{domain.id}/project-hub/plasmids/sequence-pl1480/info"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(url, json=request)
        replay = await client.post(url, json=request)
        stale = await client.post(url, json={**request, "idempotency_key": "edit-pl1480-stale", "molecular_fields": {**request["molecular_fields"], "name": "stale overwrite"}})
    assert response.status_code == 200, response.text
    assert replay.status_code == 200, replay.text
    assert replay.json()["identity"] == response.json()["identity"]
    assert stale.status_code == 409
    payload = response.json()
    assert payload["identity"]["state_head_generation"] == 2
    assert payload["identity"]["current_state_revision_id"] != "state-current"
    plasmid = payload["plasmids"][0]
    assert plasmid["name"] == "PL1480 renamed"
    assert plasmid["revision_number"] == 2
    assert plasmid["project_tags"] == ["priority", "onboarding"]
    assert plasmid["project_notes"] == "Verify clone 4"
    async with molbio_factory() as session:
        document = await session.get(MolecularDocument, "sequence-pl1480")
        assert document.current_revision_id == plasmid["revision_id"]
        assert (await session.get(MolecularRevision, revision.id)) is not None
        metadata = (await session.execute(text("SELECT project_id,domain_experiment_id,active_state_revision_id FROM project_plasmid_metadata"))).one()
        assert tuple(metadata) == (project.id, domain.id, payload["identity"]["current_state_revision_id"])


@pytest.mark.asyncio
async def test_project_hub_edit_info_compensates_cross_store_failure_without_visible_partial_success(hub_stores, monkeypatch):
    from routers import ngs_molbio_n5

    experiment_factory, native_factory, molbio_factory, project, experiment, domain, revision = hub_stores
    async def fail_state_save(*_args, **_kwargs):
        raise RuntimeError("injected native-store failure")
    monkeypatch.setattr(ngs_molbio_n5, "save_state_revision", fail_state_save)
    app = _app(experiment_factory, native_factory, molbio_factory)
    url = f"/api/projects/{project.id}/experiments/{experiment.id}/domains/{domain.id}/project-hub/plasmids/sequence-pl1480/info"
    request = {
        "expected_molecular_revision_id": revision.id, "expected_state_revision_id": "state-current",
        "expected_state_head_generation": 1, "idempotency_key": "edit-compensation",
        "molecular_fields": {"name": "Must not leak", "molecule_type": "dna", "topology": "circular", "description": "failed", "organism_host_context": None},
        "project_metadata": {"project_tags": ["must-not-leak"], "project_notes": "must not leak"},
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        failed = await client.post(url, json=request)
        visible = await client.get(f"/api/projects/{project.id}/experiments/{experiment.id}/domains/{domain.id}/project-hub", params={"state_revision_id": "state-current"})
    assert failed.status_code == 500
    assert visible.status_code == 200, visible.text
    plasmid = visible.json()["plasmids"][0]
    assert plasmid["name"] == "PL1480"
    assert plasmid["revision_id"] == revision.id
    assert plasmid["project_tags"] == []
    async with molbio_factory() as session:
        document = await session.get(MolecularDocument, "sequence-pl1480")
        assert document.current_revision_id == revision.id
        assert (await session.execute(text("SELECT count(*) FROM project_plasmid_metadata"))).scalar_one() == 0
        assert (await session.execute(text("SELECT count(*) FROM molecular_revisions WHERE document_id='sequence-pl1480'"))).scalar_one() == 1
        assert (await session.execute(text("SELECT count(*) FROM molbio_audit_events WHERE event_kind='sequence.project_metadata_update'"))).scalar_one() == 0
        assert (await session.execute(text("SELECT count(*) FROM molbio_outbox_events WHERE event_kind='sequence.project_metadata_update'"))).scalar_one() == 0


@pytest.mark.asyncio
async def test_project_hub_keeps_unavailable_molecular_members_visible(hub_stores):
    experiment_factory, native_factory, molbio_factory, project, experiment, domain, _revision = hub_stores
    async with native_factory() as session:
        receipt = MolBioNGSMemberReceipt(
            receipt_id="missing-revision-receipt", source_store_id="molbio", entity_kind="molecular_revision",
            entity_id="missing-revision", source_generation_or_revision="1", content_digest="f" * 64,
            schema_name="bms.molecular-revision.v1", schema_version="1", availability="unavailable",
            reopen_destination=_canonical({"uri": "/designer?molbio_sequence_id=missing-sequence&molbio_revision_id=missing-revision"}),
            canonical_receipt="{}", receipt_sha256="e" * 64, created_at="2026-08-27T12:00:00Z",
        )
        session.add(receipt)
        await session.flush()
        session.add(MolBioNGSDomainStateMember(state_revision_id="state-current", receipt_id=receipt.receipt_id, role="molecular_expected_construct", ordinal=1, created_at="2026-08-27T12:00:00Z"))
        await session.commit()
    app = _app(experiment_factory, native_factory, molbio_factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/projects/{project.id}/experiments/{experiment.id}/domains/{domain.id}/project-hub", params={"state_revision_id": "state-current"})
    assert response.status_code == 200, response.text
    missing = next(item for item in response.json()["plasmids"] if item["revision_id"] == "missing-revision")
    assert missing["availability"] == "unavailable"
    assert missing["unavailable_reason"] == "Molecular member unavailable"
