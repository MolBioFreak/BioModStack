from __future__ import annotations

import httpx
import pytest

from experiment_models import (
    ExperimentExternalEntityReceipt,
    ExperimentLineageEdge,
    ExperimentResource,
    ExperimentRevision,
    ExperimentRevisionEdge,
)
from experiment_services import canonical_json
from tests.test_project_manager_read_models import _app, _hierarchy, read_model_store


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


@pytest.mark.asyncio
async def test_frustrampnn_scope_requires_exact_revisions_and_uses_only_selected_domain_revision_edges(
    read_model_store,
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

    app = _app(factory)
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

    app = _app(factory)
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
) -> None:
    factory = read_model_store
    cases = [
        (10, "available", {"canonical_state": "succeeded", "statistics_analysis_state": "completed"}, "completed"),
        (11, "available", {"canonical_state": "failed", "diagnostic": "inference failed", "statistics_analysis_state": "not_started"}, "failed"),
        (12, "unavailable", {"canonical_state": "missing", "diagnostic": "expected result absent", "statistics_analysis_state": "not_started"}, "missing"),
        (13, "available", {"canonical_state": "not_run", "diagnostic": "policy skipped", "statistics_analysis_state": "not_started"}, "skipped"),
        (14, "available", {"canonical_state": "succeeded", "statistics_analysis_state": "failed", "statistics_analysis_diagnostic": "bounded analysis failure"}, "completed"),
    ]
    async with factory() as session:
        project, experiment, domain = await _hierarchy(session)
        for ordinal, (index, availability, metadata, _expected_state) in enumerate(cases):
            receipt_id, acknowledgement = await _add_receipt(
                session,
                project_id=project.id,
                index=index,
                availability=availability,
                metadata_overrides=metadata,
            )
            session.add(ExperimentRevisionEdge(
                revision_id=domain.current_revision_id,
                target_resource_id=receipt_id,
                role="source_receipt",
                ordinal=ordinal,
                expected_sha256=acknowledgement["content_digest"],
                metadata_json='{"authority":"server_resolved"}',
            ))
        await session.commit()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(factory)),
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
    assert [item["state"] for item in payload["items"]] == [case[3] for case in cases]
    assert [item["statistics_analysis"]["state"] for item in payload["items"]] == [
        "completed", "not_started", "not_started", "not_started", "failed"
    ]
    assert payload["items"][0]["source_identity"] == {
        "design_id": "design-10",
        "artifact_id": "artifact-10",
        "artifact_sha256": f"{30:064x}",
        "candidate_id": "candidate-10",
    }
    assert payload["items"][0]["operator_label"] == "Structure 10"
    assert payload["items"][1]["diagnostic"] == "inference failed"
    assert payload["items"][4]["statistics_analysis"]["diagnostic"] == "bounded analysis failure"
