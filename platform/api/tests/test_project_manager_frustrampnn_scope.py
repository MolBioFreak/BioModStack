from __future__ import annotations

import hashlib

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import (
    Base,
    Design,
    FrustraMPNNResult,
    FrustraMPNNStatisticsAnalysis,
    Job,
    get_session,
)
from experiment_database import get_experiment_session
from experiment_models import (
    ExperimentExternalEntityReceipt,
    ExperimentLineageEdge,
    ExperimentResource,
    ExperimentRevision,
    ExperimentRevisionEdge,
)
from experiment_services import canonical_json
from routers.project_manager import router as project_manager_router
from services.frustrampnn.contracts import canonical_json_bytes
from tests.test_project_manager_read_models import _hierarchy, read_model_store


async def _add_receipt(
    session,
    *,
    project_id: str,
    index: int,
    generation_or_revision: str | None = None,
    acknowledgement_revision: str | None = None,
    availability: str = "available",
    metadata_overrides: dict | None = None,
) -> tuple[str, dict]:
    receipt_id = f"frustra-receipt-{index}"
    metadata = {
        "parent_job_id": f"job-{index}",
        "invocation_id": f"invocation-{index}",
        "candidate_id": f"candidate-{index}",
        "operator_label": f"Structure {index}",
        "design_id": f"design-{index}",
        "source_artifact_id": f"artifact-{index}",
        "source_artifact_sha256": f"{index + 20:064x}",
        "canonical_state": "succeeded",
        "statistics_analysis_state": "completed",
        "manifest_sha256": f"{index:064x}",
    }
    metadata.update(metadata_overrides or {})
    revision = acknowledgement_revision or f"revision-{index}"
    acknowledgement = {
        "schema": "bms.global.external-entity-receipt.v1",
        "store_id": "core",
        "entity_kind": "frustrampnn_result",
        "entity_id": f"parent_job_id=job-{index}&invocation_id=invocation-{index}",
        "entity_revision_id": revision,
        "content_digest": f"{index:064x}",
        "availability": availability,
        "contract_digest": f"{index + 10:064x}",
        "source_build_revision": "test-build",
        "verified_at": "2026-08-26T12:00:00Z",
        "verifier_id": "bms.frustrampnn.result-reference.adapter.v1",
        "reopen_uri": f"/designs/job-{index}?frustrampnn_invocation_id=invocation-{index}",
        "metadata": metadata,
    }
    session.add(ExperimentResource(
        id=receipt_id,
        kind="external_entity_receipt",
        workspace_id=project_id,
        lifecycle_owner_id=project_id,
        created_at="2026-08-26T12:00:00Z",
    ))
    await session.flush()
    session.add(ExperimentExternalEntityReceipt(
        id=receipt_id,
        workspace_id=project_id,
        resource_id=receipt_id,
        store_id="core",
        entity_kind="frustrampnn_result",
        entity_id=acknowledgement["entity_id"],
        generation_or_revision=generation_or_revision or revision,
        content_digest=acknowledgement["content_digest"],
        availability=availability,
        verification_authority=acknowledgement["verifier_id"],
        acknowledgement_json=canonical_json(acknowledgement),
        created_at="2026-08-26T12:00:00Z",
    ))
    return receipt_id, acknowledgement


async def _request_scope(client, *, project, experiment, domain, **query):
    return await client.get(
        f"/api/projects/{project.id}/experiments/{experiment.id}/domains/{domain.id}/frustrampnn-results",
        params=query,
    )


@pytest_asyncio.fixture
async def core_store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'core.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def _cross_store_app(experiment_factory, core_factory) -> FastAPI:
    app = FastAPI()
    app.include_router(project_manager_router)

    async def override_experiment_session():
        async with experiment_factory() as session:
            yield session

    async def override_core_session():
        async with core_factory() as session:
            yield session

    app.dependency_overrides[get_experiment_session] = override_experiment_session
    app.dependency_overrides[get_session] = override_core_session
    return app


async def _add_selected_design_membership(
    session,
    *,
    project_id: str,
    domain_revision_id: str,
    ordinal: int,
) -> None:
    design_id = f"selected-design-{ordinal}"
    receipt_id = f"selected-design-receipt-{ordinal}"
    digest = f"{ordinal + 40:064x}"
    acknowledgement = {
        "schema": "bms.global.external-entity-receipt.v1",
        "store_id": "core",
        "entity_kind": "design",
        "entity_id": design_id,
        "entity_revision_id": digest,
        "content_digest": digest,
        "contract_digest": digest,
        "source_build_revision": "test-build",
        "verified_at": "2026-08-26T12:00:00Z",
        "verifier_id": "bms.core.protein-result-reference.adapter.v1",
        "availability": "available",
        "reopen_uri": f"/designs/source-job-{ordinal}",
        "metadata": {
            "job_id": f"source-job-{ordinal}",
            "design_id": design_id,
            "canonical_state": "completed",
        },
    }
    session.add(ExperimentResource(
        id=receipt_id,
        kind="external_entity_receipt",
        workspace_id=project_id,
        lifecycle_owner_id=project_id,
        created_at="2026-08-26T12:00:00Z",
    ))
    await session.flush()
    session.add_all([
        ExperimentExternalEntityReceipt(
            id=receipt_id,
            workspace_id=project_id,
            resource_id=receipt_id,
            store_id="core",
            entity_kind="design",
            entity_id=design_id,
            generation_or_revision=digest,
            content_digest=digest,
            availability="available",
            verification_authority="bms.core.protein-result-reference.adapter.v1",
            acknowledgement_json=canonical_json(acknowledgement),
            created_at="2026-08-26T12:00:00Z",
        ),
        ExperimentRevisionEdge(
            revision_id=domain_revision_id,
            target_resource_id=receipt_id,
            role="source_receipt",
            ordinal=ordinal,
            expected_sha256=digest,
            metadata_json='{"authority":"server_resolved"}',
        ),
    ])


@pytest.mark.asyncio
async def test_frustrampnn_scope_keeps_selected_design_when_no_child_execution_exists(
    read_model_store,
    core_store,
) -> None:
    experiment_factory = read_model_store
    async with experiment_factory() as session:
        project, experiment, domain = await _hierarchy(session)
        await _add_selected_design_membership(
            session,
            project_id=project.id,
            domain_revision_id=domain.current_revision_id,
            ordinal=6,
        )
        await session.commit()

    async with core_store() as session:
        session.add_all([
            Job(
                id="source-job-6",
                name="Source 6",
                status="completed",
                queue_status="completed",
                model_id="boltz2",
                mode="structure_prediction",
                params={},
            ),
            Design(
                id="selected-design-6",
                job_id="source-job-6",
                name="Structure 6",
                pdb_path="/fixture/selected-design-6.pdb",
            ),
        ])
        await session.commit()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_cross_store_app(experiment_factory, core_store)),
        base_url="http://test",
    ) as client:
        response = await _request_scope(
            client,
            project=project,
            experiment=experiment,
            domain=domain,
            global_experiment_revision_id=experiment.current_revision_id,
            domain_revision_id=domain.current_revision_id,
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["count"] == 1
    assert payload["items"] == [{
        "result_receipt_id": "selected-design-receipt-6",
        "parent_job_id": None,
        "invocation_id": None,
        "candidate_id": "selected-design-6",
        "operator_label": "Structure 6",
        "source_identity": {
            "design_id": "selected-design-6",
            "artifact_id": "selected-design-6",
            "artifact_sha256": f"{46:064x}",
            "candidate_id": "selected-design-6",
        },
        "state": "missing",
        "diagnostic": "No FrustraMPNN execution exists for this selected Design.",
        "statistics_analysis": {"state": "not_started", "diagnostic": None},
        "manifest_sha256": None,
        "content_digest": f"{46:064x}",
        "reopen_uri": None,
    }]


@pytest.mark.asyncio
async def test_frustrampnn_scope_requires_exact_revisions_and_uses_only_selected_domain_revision_edges(
    read_model_store,
    core_store,
) -> None:
    factory = read_model_store
    async with factory() as session:
        project, experiment, domain = await _hierarchy(session)
        selected_receipt_id, selected_ack = await _add_receipt(
            session, project_id=project.id, index=1
        )
        aggregate_receipt_id, _aggregate_ack = await _add_receipt(
            session, project_id=project.id, index=2
        )
        other_revision_receipt_id, _other_ack = await _add_receipt(
            session, project_id=project.id, index=3
        )
        other_revision_resource_id = "other-domain-revision"
        session.add(ExperimentResource(
            id=other_revision_resource_id,
            kind="revision",
            workspace_id=project.id,
            lifecycle_owner_id=domain.id,
            created_at="2026-08-26T12:00:00Z",
        ))
        await session.flush()
        session.add(ExperimentRevision(
            resource_id=other_revision_resource_id,
            subject_id=domain.id,
            revision_number=2,
            parent_revision_id=domain.current_revision_id,
            schema_name="bms.domain-experiment.v1",
            schema_version="1",
            canonical_payload="{}",
            payload_sha256="4" * 64,
            dependency_graph_sha256="5" * 64,
            provenance_json="{}",
            created_at="2026-08-26T12:00:00Z",
        ))
        await session.flush()
        session.add_all([
            ExperimentRevisionEdge(
                revision_id=domain.current_revision_id,
                target_resource_id=selected_receipt_id,
                role="source_receipt",
                ordinal=0,
                expected_sha256=selected_ack["content_digest"],
                metadata_json='{"authority":"server_resolved"}',
            ),
            ExperimentRevisionEdge(
                revision_id=other_revision_resource_id,
                target_resource_id=other_revision_receipt_id,
                role="source_receipt",
                ordinal=0,
                expected_sha256=f"{3:064x}",
                metadata_json='{"authority":"server_resolved"}',
            ),
            ExperimentLineageEdge(
                id="aggregate-leak-edge",
                workspace_id=project.id,
                source_resource_id=domain.id,
                target_resource_id=aggregate_receipt_id,
                edge_mode="produced",
                edge_key=f"produced:{aggregate_receipt_id}",
                metadata_json="{}",
                created_at="2026-08-26T12:00:00Z",
            ),
        ])
        await session.commit()

    app = _cross_store_app(factory, core_store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        missing_revisions = await _request_scope(
            client, project=project, experiment=experiment, domain=domain
        )
        wrong_global_revision = await _request_scope(
            client,
            project=project,
            experiment=experiment,
            domain=domain,
            global_experiment_revision_id=domain.current_revision_id,
            domain_revision_id=domain.current_revision_id,
        )
        wrong_domain_revision = await _request_scope(
            client,
            project=project,
            experiment=experiment,
            domain=domain,
            global_experiment_revision_id=experiment.current_revision_id,
            domain_revision_id=experiment.current_revision_id,
        )
        response = await _request_scope(
            client,
            project=project,
            experiment=experiment,
            domain=domain,
            global_experiment_revision_id=experiment.current_revision_id,
            domain_revision_id=domain.current_revision_id,
        )
    assert missing_revisions.status_code == 422
    assert wrong_global_revision.status_code == 404
    assert wrong_domain_revision.status_code == 404
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload == {
        "schema": "bms.project-frustrampnn-result-scope.v1",
        "project_id": project.id,
        "global_experiment_id": experiment.id,
        "global_experiment_revision_id": experiment.current_revision_id,
        "domain_experiment_id": domain.id,
        "domain_revision_id": domain.current_revision_id,
        "items": [{
            "result_receipt_id": "frustra-receipt-1",
            "parent_job_id": "job-1",
            "invocation_id": "invocation-1",
            "candidate_id": "candidate-1",
            "operator_label": "Structure 1",
            "source_identity": {
                "design_id": "design-1",
                "artifact_id": "artifact-1",
                "artifact_sha256": f"{21:064x}",
                "candidate_id": "candidate-1",
            },
            "state": "completed",
            "diagnostic": None,
            "statistics_analysis": {"state": "completed", "diagnostic": None},
            "manifest_sha256": f"{1:064x}",
            "content_digest": f"{1:064x}",
            "reopen_uri": "/designs/job-1?frustrampnn_invocation_id=invocation-1",
        }],
        "count": 1,
        "bounded": True,
    }


@pytest.mark.asyncio
async def test_frustrampnn_scope_rejects_receipt_revision_acknowledgement_mismatch(
    read_model_store,
    core_store,
) -> None:
    factory = read_model_store
    async with factory() as session:
        project, experiment, domain = await _hierarchy(session)
        receipt_id, acknowledgement = await _add_receipt(
            session,
            project_id=project.id,
            index=4,
            generation_or_revision="persisted-revision",
            acknowledgement_revision="claimed-revision",
        )
        session.add(ExperimentRevisionEdge(
            revision_id=domain.current_revision_id,
            target_resource_id=receipt_id,
            role="source_receipt",
            ordinal=0,
            expected_sha256=acknowledgement["content_digest"],
            metadata_json='{"authority":"server_resolved"}',
        ))
        await session.commit()

    app = _cross_store_app(factory, core_store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await _request_scope(
            client,
            project=project,
            experiment=experiment,
            domain=domain,
            global_experiment_revision_id=experiment.current_revision_id,
            domain_revision_id=domain.current_revision_id,
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "frustrampnn_scope_receipt_invalid"


@pytest.mark.asyncio
async def test_frustrampnn_scope_projects_all_selected_revision_terminal_and_analysis_states(
    read_model_store,
    core_store,
) -> None:
    experiment_factory = read_model_store
    cases = [
        (0, "completed", "completed", "completed", None),
        (1, "failed", "failed", "not_started", "inference failed"),
        (2, "completed", "missing", "not_started", "expected persisted result absent"),
        (3, "cancelled", "skipped", "not_started", "policy skipped"),
        (4, "completed", "completed", "failed", None),
    ]
    async with experiment_factory() as session:
        project, experiment, domain = await _hierarchy(session)
        for ordinal, _child_status, _expected_state, _statistics_state, _diagnostic in cases:
            await _add_selected_design_membership(
                session,
                project_id=project.id,
                domain_revision_id=domain.current_revision_id,
                ordinal=ordinal,
            )
        await session.commit()

    async with core_store() as session:
        for ordinal, child_status, _expected_state, statistics_state, diagnostic in cases:
            design_id = f"selected-design-{ordinal}"
            child_id = f"frustra-child-{ordinal}"
            invocation_id = f"frustrampnn:{child_id}:1"
            candidate_id = f"candidate-{ordinal}"
            source_sha = f"{ordinal + 60:064x}"
            session.add_all([
                Job(
                    id=f"source-job-{ordinal}",
                    name=f"Source {ordinal}",
                    status="completed",
                    queue_status="completed",
                    model_id="boltz2",
                    mode="structure_prediction",
                    params={"run_frustrampnn": False} if ordinal == 3 else {},
                ),
                Design(
                    id=design_id,
                    job_id=f"source-job-{ordinal}",
                    name=f"Structure {ordinal}",
                    pdb_path=f"/fixture/{design_id}.pdb",
                ),
                Job(
                    id=child_id,
                    name=f"FrustraMPNN {ordinal}",
                    status=child_status,
                    queue_status=child_status,
                    model_id="frustrampnn",
                    mode="analyze",
                    params={
                        "_frustrampnn_child_v1": {
                            "schema_name": "bms.frustrampnn.scheduler-child.v1",
                            "schema_version": 1,
                            "execution_owner_job_id": child_id,
                            "selection": [{
                                "selection_ordinal": 0,
                                "design_id": design_id,
                                "candidate_id": candidate_id,
                                "invocation_id": invocation_id,
                                "sha256": source_sha,
                            }],
                            "component_invocation_ids": [invocation_id],
                        },
                    },
                    child_stage="frustrampnn",
                    parent_job_id=f"source-job-{ordinal}",
                    error_message=diagnostic,
                ),
            ])
            if ordinal in {0, 4}:
                manifest = {
                    "schema_name": "frustrampnn_result_manifest",
                    "parent_job_id": child_id,
                    "invocation_id": invocation_id,
                    "candidate_id": candidate_id,
                    "request_sha256": f"{ordinal + 70:064x}",
                    "source_sha256": source_sha,
                }
                summary = {"schema_name": "frustrampnn_summary", "candidate_id": candidate_id}
                manifest_sha = hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()
                summary_sha = hashlib.sha256(canonical_json_bytes(summary)).hexdigest()
                session.add(FrustraMPNNResult(
                    parent_job_id=child_id,
                    invocation_id=invocation_id,
                    parent_workflow_id="frustrampnn_analysis",
                    candidate_id=candidate_id,
                    design_id=design_id,
                    requiredness="required",
                    request_sha256=f"{ordinal + 70:064x}",
                    source_artifact_id=design_id,
                    source_artifact_sha256=source_sha,
                    manifest_sha256=manifest_sha,
                    manifest_json=manifest,
                    summary_sha256=summary_sha,
                    summary_json=summary,
                    runtime_identity_json={},
                    assigned_gpu_json={},
                    terminal_result_json={"status": "completed"},
                ))
                session.add(FrustraMPNNStatisticsAnalysis(
                    analysis_id=f"statistics-{ordinal}",
                    parent_job_id=child_id,
                    invocation_id=invocation_id,
                    core_artifact_id=f"artifact-{ordinal}",
                    core_bundle_relative_path=f"frustrampnn/results/{candidate_id}",
                    core_landscape_sha256=f"{ordinal + 80:064x}",
                    core_manifest_sha256=manifest_sha,
                    state=statistics_state,
                    attempt_count=1,
                    formula_version="1",
                    policy_version="1",
                    package_version="1",
                    schema_version=1,
                    diagnostic=(
                        "bounded analysis failure" if statistics_state == "failed" else None
                    ),
                ))
        await session.commit()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_cross_store_app(experiment_factory, core_store)),
        base_url="http://test",
    ) as client:
        response = await _request_scope(
            client,
            project=project,
            experiment=experiment,
            domain=domain,
            global_experiment_revision_id=experiment.current_revision_id,
            domain_revision_id=domain.current_revision_id,
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [item["state"] for item in payload["items"]] == [case[2] for case in cases]
    assert [item["statistics_analysis"]["state"] for item in payload["items"]] == [
        "completed", "not_started", "not_started", "not_started", "failed"
    ]
    assert payload["items"][0]["source_identity"] == {
        "design_id": "selected-design-0",
        "artifact_id": "selected-design-0",
        "artifact_sha256": f"{60:064x}",
        "candidate_id": "candidate-0",
    }
    assert payload["items"][0]["operator_label"] == "Structure 0"
    assert payload["items"][1]["diagnostic"] == "inference failed"
    assert payload["items"][2]["diagnostic"] == "expected persisted result absent"
    assert payload["items"][3]["diagnostic"] == "FrustraMPNN was explicitly disabled for this source workflow."
    assert payload["items"][4]["statistics_analysis"]["diagnostic"] == "bounded analysis failure"
    assert payload["items"][0]["result_receipt_id"] == "selected-design-receipt-0"
    assert payload["items"][0]["manifest_sha256"]
    assert [item["manifest_sha256"] for item in payload["items"][1:4]] == [None, None, None]
    assert [item["content_digest"] for item in payload["items"]] == [
        f"{ordinal + 40:064x}" for ordinal in range(5)
    ]
