"""Actual cloning Save -> Project attach -> persisted Open -> exact product.

Private migrated SQLite stores; only the pre-existing Project hierarchy is seeded
with its native services. No molecular operations, edges or receipts are seeded.
HTTP is exercised in-process through the production ASGI routers (not a browser).
"""
from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select, text

from experiment_database import create_experiment_engine, create_experiment_session_factory, get_experiment_session
from experiment_migrations import run_all
from experiment_models import ExperimentAuditEvent
from experiment_services import create_domain_experiment, create_global_experiment, create_project
from molbio_models import MolecularOperationOutput, MolecularRevision
from routers import molbio_golden_gate_design, project_manager, projects
from services.assembly.golden_gate_workflow import freeze_selection
from services.assembly.golden_gate_workflow_types import SaveDesignRequest, WorkflowResult
from test_golden_gate_design_core import A, B, request as golden_gate_request
from services.global_experiments.adapters import registry
from test_acceptance_cloning import BB, CASSETTE, OLD_MARKER, design_and_save
from test_assembly_product_content import extract, feature, source_fragment
from test_cloning_connected_api_acceptance import empty_store
from test_molbio_branches import make_client


async def hierarchy(sessions):
    """Existing Project/Experiment/Domain authority, not operation test doubles."""
    async with sessions() as session:
        project = await create_project(session, {
            "schema": "bms.project.v1", "name": "Generic cloning", "description": "HTTP regression",
            "research_objective": "Retain cloning products", "owner": "operator", "contributors": [],
            "tags": [], "status": "active", "needs_metadata_review": False, "start_date": None,
            "target_end_date": None, "external_references": [], "created_by": "operator", "change_summary": "created",
        })
        experiment = await create_global_experiment(session, project.id, {
            "schema": "bms.global-experiment.v1", "name": "Build", "objective": "Retain products",
            "scientific_question": "Can the saved product reopen?", "hypothesis": None, "description": "",
            "status": "active", "priority": "normal", "tags": [], "shared_source_receipt_ids": [],
            "shared_dataset_ids": [], "comparison_plan": None, "success_criteria": ["Exact product"],
            "review_summary": None, "conclusion": None, "needs_metadata_review": False,
            "created_by": "operator", "change_summary": "created",
        })
        domain = await create_domain_experiment(session, project.id, experiment.id, {
            "schema": "bms.domain-experiment.v1", "domain_kind": "ngs_molbio", "domain_contract_version": "1",
            "name": "Molecular build", "objective": "Retain products", "status": "active", "tags": [],
            "source_receipt_ids": [], "dataset_ids": [], "created_by": "operator", "change_summary": "created",
            "domain_payload": {"schema": "bms.ngs-molbio-experiment.v1"},
        })
        session.add(ExperimentAuditEvent(
            id="test-owner", workspace_id=project.id, resource_id=project.id,
            event_type="workspace_owner_bound", generation=0, payload_json='{"principal_id":"operator"}',
        ))
        await session.commit()
        return project.id, experiment.id, domain.id


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["gibson_plain", "gibson_preoverlapped", "ligation", "golden_gate"])
async def test_new_cloning_operation_attaches_and_project_open_pins_product(tmp_path, monkeypatch, method):
    engine, molecular = await empty_store(tmp_path / "molbio.db")
    project_path = tmp_path / "projects.db"
    await asyncio.to_thread(run_all, project_path, verify_data=False)
    project_engine = create_experiment_engine(f"sqlite+aiosqlite:///{project_path}")
    experiments = create_experiment_session_factory(project_engine)
    # Redirect the real registered adapter's store dependency, not its verifier.
    adapter_id = "bms.molbio.operation-reference.adapter.v1"
    monkeypatch.setattr(registry.get(adapter_id), "_molbio_session_factory", molecular)
    try:
        pid, eid, did = await hierarchy(experiments)
        async with make_client(molecular) as client:
            app = client._transport.app
            app.include_router(project_manager.router)
            app.include_router(projects.router)
            app.include_router(molbio_golden_gate_design.router)

            @app.middleware("http")
            async def authenticate(request, call_next):
                request.state.authenticated_principal = {"id": "operator", "roles": ["operator"]}
                return await call_next(request)

            async def project_session():
                async with experiments() as session:
                    yield session

            app.dependency_overrides[get_experiment_session] = project_session
            # The molecular operation adapter does not query core; keep even its
            # unused dependency on a private session rather than a live core DB.
            app.dependency_overrides[project_manager.get_core_session] = project_session
            source = await client.post("/api/sequences/", json={
                "name": "Generic recipient", "sequence": BB + OLD_MARKER, "sequence_type": "dna",
                "molecule_strandedness": "double", "is_circular": True,
                "features": [feature("ori", 100, 400)],
            })
            assert source.status_code == 200, source.text
            sid = source.json()["id"]
            if method.startswith("gibson"):
                preoverlapped = method == "gibson_preoverlapped"
                payload = {
                    "fragments": [
                        {"id": "bb", "name": "backbone", "sequence": BB, "preparation": "pcr",
                         "source_sequence_id": sid, "source_revision": 1, "source_start": 0, "source_end": len(BB)},
                        {"id": "cas", "name": "cassette", "preparation": "ready_linear",
                         "sequence": BB[-30:] + CASSETTE + BB[:30] if preoverlapped else CASSETTE}],
                    "circular": True, "overlap": 30, "target_tm": 60.0, "min_anneal": 13,
                }
                _, saved = await design_and_save(client, payload)
                expected = BB + CASSETTE
            elif method == "golden_gate":
                design = await client.post("/api/molbio/assembly/golden-gate/design",
                                           json=golden_gate_request(kind="synthesis").model_dump(mode="json"))
                assert design.status_code == 200, design.text
                result = WorkflowResult.model_validate(design.json())
                assert result.selected_solution_id is not None
                response = await client.post("/api/molbio/assembly/golden-gate/design/save", json=SaveDesignRequest(
                    selection=freeze_selection(result, result.selected_solution_id), name="Generic Golden Gate",
                    idempotency_key="project-golden-gate").model_dump(mode="json"))
                assert response.status_code == 200, response.text
                saved = (await client.get(f"/api/sequences/{response.json()['product_document_id']}")).json()
                expected = "AATG" + A + "GGAG" + B
            else:
                response = await client.post("/api/molbio/assembly/ligation/save", json={
                    "fragments": [source_fragment("backbone", sid, BB, 0, len(BB)),
                                  {"id": "insert", "name": "insert", "sequence": CASSETTE,
                                   "left_end": {"type": "blunt"}, "right_end": {"type": "blunt"}}],
                    "circular": False, "new_name": "Generic ligation",
                })
                assert response.status_code == 200, response.text
                saved = response.json()["saved_sequence"]
                expected = BB + CASSETTE
            assert len(saved["sequence"]) == len(expected)
            assert expected in saved["sequence"] * (2 if method.startswith("gibson") else 1)
            assert extract(saved["sequence"], saved["features"][0]) == (B if method == "golden_gate" else BB[100:400])
            async with molecular() as session:
                product = (await session.scalars(select(MolecularRevision).where(
                    MolecularRevision.document_id == saved["id"]))).one()
                output = (await session.scalars(select(MolecularOperationOutput).where(
                    MolecularOperationOutput.revision_id == product.id,
                    MolecularOperationOutput.role == "product"))).one()
                operation_id = output.operation_id
                assert (await session.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'"))).all()
            exact = await client.get(f"/api/sequences/{product.document_id}/revisions/{product.id}")
            assert exact.status_code == 200, exact.text
            product_snapshot = exact.json()["snapshot"]
            assert extract(product_snapshot["sequence"], product_snapshot["features"][0]) == (
                B if method == "golden_gate" else BB[100:400])
            native = await client.get(f"/api/molbio/operations/{operation_id}")
            assert native.status_code == 200, native.text
            original_operation = native.json()
            project = await client.get(f"/api/projects/{pid}")
            assert project.status_code == 200, project.text
            attached = await client.post(f"/api/projects/{pid}/experiments/{eid}/domains/{did}/attach", json={
                "adapter_id": adapter_id, "entity_id": operation_id,
                "operation": "link_output", "role": "produced", "note": "Saved cloning product",
                "expected_head_generation": project.json()["head_generation"],
            })
            assert attached.status_code == 201, attached.text
            attachment = attached.json()
            source_receipt = attachment["source_receipt"]
            uri = source_receipt["reopen_uri"]
            query = {key: value[0] for key, value in parse_qs(urlsplit(uri).query).items()}
            assert query == {"molbio_sequence_id": product.document_id,
                             "molbio_revision_id": product.id, "molbio_operation_id": operation_id}
            lineage = source_receipt["metadata"]["native_lineage"]
            assert {item["relation"] for item in lineage} == {"uses_input", "produced"}
            assert any(item["role"] == "product" for item in lineage)
            assert len(lineage) == len(original_operation["inputs"]) + len(original_operation["outputs"])
            assert {(item["relation"], item["role"], parse_qs(item["entity_id"])["revision_id"][0])
                    for item in lineage} == {
                        (relation, edge["role"], edge["revision_id"])
                        for key, relation in (("inputs", "uses_input"), ("outputs", "produced"))
                        for edge in original_operation[key]}
            if method == "golden_gate":
                assert original_operation["outputs"][0]["revision_id"] != product.id
            if method.startswith("gibson"):
                assert any(item["role"] == "designed_fragment" for item in lineage)
            # Advance the shelf after attachment: Project Open must still choose
            # the retained product revision, not current shelf DNA or a child.
            updated = await client.put(f"/api/sequences/{saved['id']}", json={"name": "Later revision"})
            assert updated.status_code == 200, updated.text
            await engine.dispose()
            await project_engine.dispose()
            surface = await client.get(f"/api/projects/{pid}/receipts/{attachment['source_receipt_id']}/surface")
            assert surface.status_code == 200, surface.text
            body = surface.json()
            assert body["readiness"] == "ready" and "open" in body["available_actions"]
            assert body["route"]["path"] == "/designer"
            assert body["route"]["query"] == query
            for edge in original_operation["outputs"]:
                retained = await client.get(f"/api/sequences/{edge['document_id']}/revisions/{edge['revision_id']}")
                assert retained.status_code == 200, retained.text
            opened = await client.get(f"/api/sequences/{query['molbio_sequence_id']}/revisions/{query['molbio_revision_id']}")
            assert opened.status_code == 200, opened.text
            assert opened.json()["snapshot"]["sequence"] == saved["sequence"]
            assert opened.json()["snapshot"] == product_snapshot
            assert opened.json()["snapshot"]["name"] == saved["name"]
            assert (await client.get(f"/api/molbio/operations/{operation_id}")).json() == original_operation
    finally:
        await engine.dispose()
        await project_engine.dispose()
