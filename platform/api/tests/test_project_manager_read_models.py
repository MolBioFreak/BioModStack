from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import pytest_asyncio
import rfc8785
from fastapi import FastAPI
from sqlalchemy import select

from database import get_session as get_core_session
from experiment_database import (
    create_experiment_engine,
    create_experiment_session_factory,
    get_experiment_session,
)
from experiment_models import (
    ExperimentBase,
    ExperimentAuditEvent,
    ExperimentDomainAdapterReceipt,
    ExperimentExternalEntityReceipt,
    ExperimentLineageEdge,
    ExperimentResearchRecord,
    ExperimentResource,
    ExperimentRevision,
    ExperimentRunAttempt,
    ExperimentRunGroup,
    ExperimentWorkflowPreparation,
    ExperimentWorkflowRun,
)
from experiment_services import (
    add_audit_event,
    canonical_json,
    create_domain_experiment,
    create_global_experiment,
    create_project,
    create_workflow,
    sha256_text,
)
from routers.project_manager import router as project_manager_router
from services.global_experiments.adapters import registry
from services.global_experiments.launch_contexts import create_launch_context
from services.global_experiments.read_models import _head_summary, build_project_manager_read_model
from services.ngs_molbio_preparation_authority import (
    _hierarchy_revision_reference_ids,
    _validate_hierarchy_producer_payload,
)


PAGE_LIMIT = 100
ATTACHMENT_TOTAL = PAGE_LIMIT + 5
RESULT_TOTAL = ATTACHMENT_TOTAL - 1


def test_project_head_summary_exposes_scope_and_defaults_legacy_projects_to_global() -> None:
    head = type("Head", (), {
        "aggregate_id": "project-1",
        "display_name": "Project",
        "lifecycle_state": "active",
        "head_generation": 1,
        "current_revision_id": "revision-1",
        "updated_at": "2026-08-27T00:00:00Z",
    })()
    typed_head = cast(Any, head)
    assert _head_summary(typed_head, {"name": "Project"})["project_scope"] == "global"
    assert _head_summary(typed_head, {"name": "Project", "project_scope": "ngs_molbio_local"})["project_scope"] == "ngs_molbio_local"


def test_current_hierarchy_schemas_and_protein_reference_roles_are_admitted() -> None:
    project_payload = {
        "schema": "bms.project.v2",
        "name": "DRT4",
        "description": "Protein project",
        "research_objective": "Prepare exact no-launch model handoffs",
        "owner": "operator",
        "contributors": [],
        "tags": ["protein"],
        "project_scope": "global",
        "status": "active",
        "needs_metadata_review": False,
    }
    global_payload = {
        "schema": "bms.global-experiment.v2",
        "name": "DRT4 Protein",
        "objective": "Prepare three accepted structure predictors",
        "scientific_question": "Are exact launch handoffs queue-ready?",
        "description": "No-launch preparation",
        "hypotheses": [],
        "success_criteria": ["Three exact launch contexts compile"],
        "shared_source_receipt_ids": [],
        "shared_dataset_ids": [],
        "status": "active",
        "priority": "medium",
        "needs_metadata_review": False,
    }
    domain_payload = {
        "schema": "bms.domain-experiment.v4",
        "domain_kind": "protein_in_silico",
        "domain_contract_version": "3",
        "name": "DRT4 Protein",
        "objective": "Prepare exact structure prediction handoffs",
        "status": "active",
        "tags": ["protein"],
        "created_by": "operator",
        "change_summary": "Activate DRT4 Protein Domain",
        "source_receipt_ids": ["receipt-1"],
        "dataset_revision_ids": ["dataset-revision-1"],
        "domain_payload": {
            "schema": "bms.protein-in-silico-experiment.v3",
            "experiment_mode": "exploration",
            "scientific_objective": "Prepare exact structure prediction handoffs.",
            "targets": [
                {
                    "target_id": "WP_031606642.1",
                    "label": "DRT4",
                    "role": "target",
                    "source_receipt_ids": ["receipt-1"],
                    "expected_content_sha256": "a" * 64,
                    "dataset_member_refs": [
                        {"dataset_revision_id": "dataset-revision-1", "member_id": "member-1"}
                    ],
                    "entity_map_reference": {
                        "schema": "bms.protein-entity-map-reference.v1",
                        "authority_kind": "governed_artifact_receipt",
                        "receipt_id": "sci_receipt_1",
                        "receipt_sha256": "b" * 64,
                        "content_sha256": "c" * 64,
                        "canonical_size_bytes": 128,
                        "entity_count": 1,
                        "residue_mapping_count": 540,
                        "display_entities": [
                            {
                                "entity_instance_id": "A",
                                "source_entity_id": "1",
                                "entity_type": "protein",
                                "label_asym_id": "A",
                                "auth_asym_id": "A",
                            }
                        ],
                    },
                }
            ],
            "design_constraints": [],
            "planned_capability_ids": ["protein.structure_prediction.protenix_v2"],
            "validation_capability_ids": ["protein.structure_prediction.esmfold2"],
            "comparison_groups": [],
            "acceptance_criteria": [],
            "evidence_plan": [],
        },
    }
    domain_payload["domain_payload_canonical_size_bytes"] = len(
        rfc8785.dumps(domain_payload["domain_payload"])
    )
    without_size = dict(domain_payload)
    domain_payload["canonical_size_bytes"] = len(rfc8785.dumps(without_size))

    _validate_hierarchy_producer_payload("workspace", project_payload)
    _validate_hierarchy_producer_payload("experiment", global_payload)
    assert _hierarchy_revision_reference_ids(global_payload) == []
    assert _hierarchy_revision_reference_ids(domain_payload) == [
        ("source_receipt", "receipt-1"),
        ("target_source_receipt:0", "receipt-1"),
        ("dataset_revision", "dataset-revision-1"),
    ]


@pytest_asyncio.fixture
async def read_model_store(tmp_path: Path):
    db_path = tmp_path / "experiments.db"
    engine = create_experiment_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as connection:
        await connection.run_sync(ExperimentBase.metadata.create_all)
    factory = create_experiment_session_factory(engine)
    try:
        yield factory
    finally:
        await engine.dispose()


def _project_payload() -> dict:
    return {
        "schema": "bms.project.v1",
        "name": "Pagination project",
        "description": "Project Manager pagination fixture",
        "research_objective": "Prove bounded stable pages",
        "owner": "operator",
        "contributors": [],
        "tags": [],
        "status": "active",
        "start_date": None,
        "target_end_date": None,
        "external_references": [],
        "created_by": "operator",
        "change_summary": "created",
    }


def _global_payload() -> dict:
    return {
        "schema": "bms.global-experiment.v1",
        "name": "Focused experiment",
        "objective": "Exercise pages",
        "scientific_question": "Are cursors truthful?",
        "hypothesis": None,
        "description": "Cursor fixture",
        "status": "active",
        "priority": "normal",
        "tags": [],
        "shared_source_receipt_ids": [],
        "shared_dataset_ids": [],
        "comparison_plan": None,
        "success_criteria": ["Every row is reachable"],
        "review_summary": None,
        "conclusion": None,
        "created_by": "operator",
        "change_summary": "created",
    }


def _domain_payload() -> dict:
    return {
        "schema": "bms.domain-experiment.v1",
        "domain_kind": "protein_in_silico",
        "domain_contract_version": "1",
        "name": "Focused domain",
        "objective": "Exercise pages",
        "status": "active",
        "tags": [],
        "source_receipt_ids": [],
        "dataset_ids": [],
        "created_by": "operator",
        "change_summary": "created",
        "domain_payload": {
            "schema": "bms.protein-in-silico-experiment.v1",
            "experiment_mode": "analysis",
            "targets": [],
            "scientific_objective": "Exercise pages",
            "design_constraints": [],
            "planned_capabilities": [],
            "comparison_groups": [],
            "validation_strategy": [],
        },
    }


async def _hierarchy(session):
    project = await create_project(session, _project_payload())
    global_experiment = await create_global_experiment(session, project.id, _global_payload())
    domain = await create_domain_experiment(
        session,
        project.id,
        global_experiment.id,
        _domain_payload(),
    )
    await session.flush()
    return project, global_experiment, domain


async def _add_reverification_receipt(
    session,
    *,
    project_id: str,
    global_experiment_id: str,
    domain_id: str,
    source_receipt_id: str,
    digest: str,
    reverified_at: datetime,
    receipt_token: str,
    payload_overrides: dict | None = None,
) -> None:
    valid_until = reverified_at + timedelta(hours=24)
    normalized_request = {
        "operation": "reverify_source",
        "project_id": project_id,
        "global_experiment_id": global_experiment_id,
        "domain_experiment_id": domain_id,
        "source_receipt_id": source_receipt_id,
    }
    normalized_request_sha256 = sha256_text(canonical_json(normalized_request))
    resource_id = f"resource-reverify-{receipt_token}"
    body = {
        "schema": "bms.global.source-reverification-receipt.v1",
        "reverification_receipt_id": resource_id,
        "project_id": project_id,
        "global_experiment_id": global_experiment_id,
        "domain_experiment_id": domain_id,
        "adapter_id": "test.adapter.v1",
        "adapter_version": "1",
        "source_receipt_id": source_receipt_id,
        "source_digest": digest,
        "verified_at": reverified_at.isoformat(),
        "valid_until": valid_until.isoformat(),
        "normalized_request_sha256": normalized_request_sha256,
    }
    if payload_overrides:
        body.update(payload_overrides)
    session.add(ExperimentResource(
        id=resource_id,
        kind="domain_adapter_receipt",
        workspace_id=project_id,
        lifecycle_owner_id=domain_id,
        created_at=reverified_at.isoformat(),
    ))
    await session.flush()
    session.add(ExperimentDomainAdapterReceipt(
        resource_id=resource_id,
        workspace_id=project_id,
        domain_experiment_id=domain_id,
        adapter_id="test.adapter.v1",
        adapter_version="1",
        operation_kind="reverify_source",
        normalized_request_sha256=normalized_request_sha256,
        receipt_json=canonical_json(body),
        created_at=reverified_at.isoformat(),
    ))
    await session.flush()


async def _add_source_receipt(
    session,
    *,
    project_id: str,
    global_experiment_id: str,
    domain_id: str,
    receipt_id: str,
    digest: str,
    created_at: str,
    reverified_at: datetime | None = None,
) -> None:
    acknowledgement = {
        "schema": "bms.global.external-entity-receipt.v1",
        "store_id": "core",
        "entity_kind": "typed_core_job_result",
        "entity_id": f"job-{receipt_id}",
        "entity_revision_id": f"revision-{receipt_id}",
        "content_digest": digest,
        "contract_digest": digest,
        "availability": "available",
        "source_build_revision": "test-build",
        "verified_at": created_at,
        "verifier_id": "test.adapter.v1",
        "reopen_uri": f"/designs/job-{receipt_id}",
        "metadata": {},
    }
    session.add(ExperimentResource(
        id=receipt_id,
        kind="external_entity_receipt",
        workspace_id=project_id,
        lifecycle_owner_id=project_id,
        created_at=created_at,
    ))
    await session.flush()
    session.add_all([
        ExperimentExternalEntityReceipt(
            id=receipt_id,
            workspace_id=project_id,
            resource_id=receipt_id,
            store_id="core",
            entity_kind="typed_core_job_result",
            entity_id=f"job-{receipt_id}",
            generation_or_revision=f"revision-{receipt_id}",
            content_digest=digest,
            availability="available",
            verification_authority="test.adapter.v1",
            acknowledgement_json=canonical_json(acknowledgement),
            created_at=created_at,
        ),
        ExperimentLineageEdge(
            id=f"lineage-{receipt_id}",
            workspace_id=project_id,
            source_resource_id=domain_id,
            target_resource_id=receipt_id,
            edge_mode="references",
            edge_key=f"attachment:test.adapter.v1:{receipt_id}:references",
            metadata_json="{}",
            created_at=created_at,
        ),
    ])
    await session.flush()
    if reverified_at is None:
        return
    await _add_reverification_receipt(
        session,
        project_id=project_id,
        global_experiment_id=global_experiment_id,
        domain_id=domain_id,
        source_receipt_id=receipt_id,
        digest=digest,
        reverified_at=reverified_at,
        receipt_token=receipt_id,
    )


def _acknowledgement(index: int) -> dict:
    digest = f"{index:064x}"
    return {
        "schema": "bms.global.external-entity-receipt.v1",
        "store_id": "core",
        "entity_kind": "ngs_reference_set",
        "entity_id": f"result-{index:03d}",
        "entity_revision_id": f"revision-{index:03d}",
        "content_digest": digest,
        "contract_digest": digest,
        "source_build_revision": "test-build",
        "verified_at": "2026-08-09T12:00:00Z",
        "verifier_id": "test.adapter.v1",
        "reopen_uri": f"/results/{index:03d}",
        "metadata": {},
    }


async def _add_attachment_note_activity_rows(session, *, project_id: str, domain_id: str) -> None:
    for index in range(ATTACHMENT_TOTAL):
        stamp = f"2026-08-09T12:{index // 60:02d}:{index % 60:02d}Z"
        receipt_id = f"receipt-{index:03d}"
        record_id = f"record-{index:03d}"
        acknowledgement = _acknowledgement(index)
        session.add_all(
            [
                ExperimentResource(
                    id=receipt_id,
                    kind="external_entity_receipt",
                    workspace_id=project_id,
                    lifecycle_owner_id=project_id,
                    created_at=stamp,
                ),
                ExperimentResource(
                    id=record_id,
                    kind="research_record",
                    workspace_id=project_id,
                    lifecycle_owner_id=project_id,
                    created_at=stamp,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                ExperimentExternalEntityReceipt(
                    id=receipt_id,
                    workspace_id=project_id,
                    resource_id=receipt_id,
                    store_id="core",
                    entity_kind="ngs_reference_set",
                    entity_id=acknowledgement["entity_id"],
                    generation_or_revision=acknowledgement["entity_revision_id"],
                    content_digest=acknowledgement["content_digest"],
                    availability="available",
                    verification_authority="test.adapter.v1",
                    acknowledgement_json=canonical_json(acknowledgement),
                    created_at=stamp,
                ),
                ExperimentLineageEdge(
                    id=f"edge-{index:03d}",
                    workspace_id=project_id,
                    source_resource_id=domain_id,
                    target_resource_id=receipt_id,
                    edge_mode="references" if index == ATTACHMENT_TOTAL - 1 else "produced",
                    edge_key=(
                        f"references:{index:03d}"
                        if index == ATTACHMENT_TOTAL - 1
                        else f"produced:{index:03d}"
                    ),
                    metadata_json="{}",
                    created_at=stamp,
                ),
                ExperimentResearchRecord(
                    resource_id=record_id,
                    workspace_id=project_id,
                    subject_resource_id=domain_id,
                    record_kind="note",
                    body=f"note {index:03d}",
                    author="operator",
                    source_receipt_ids_json="[]",
                    created_at=stamp,
                ),
                ExperimentAuditEvent(
                    id=f"page-audit-{index:03d}",
                    workspace_id=project_id,
                    resource_id=domain_id,
                    event_type="page_fixture",
                    generation=index,
                    payload_json=json.dumps({"index": index}),
                    created_at=stamp,
                ),
            ]
        )
    await session.commit()


def _node_keys(read_model: dict, node_type: str) -> set[str]:
    return {
        node["node_key"]
        for node in read_model["map"]["nodes"]
        if node["node_type"] == node_type
    }


def _app(factory) -> FastAPI:
    app = FastAPI()
    app.include_router(project_manager_router)

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_experiment_session] = override_session

    async def override_core_session():
        yield cast(Any, object())

    app.dependency_overrides[get_core_session] = override_core_session
    return app


@pytest.mark.asyncio
async def test_protein_domain_tree_exposes_revision_edit_action(read_model_store):
    async with read_model_store() as session:
        project, global_experiment, domain = await _hierarchy(session)
        read_model = await build_project_manager_read_model(
            session,
            project_id=project.id,
            focus_id=global_experiment.id,
            selected_node_key=f"domain_experiment:{domain.id}",
            map_limit=PAGE_LIMIT,
            lineage_limit=PAGE_LIMIT,
            result_limit=PAGE_LIMIT,
            note_limit=PAGE_LIMIT,
            activity_limit=PAGE_LIMIT,
        )

    domain_node = next(
        node
        for node in read_model["tree"]["nodes"]
        if node["node_key"] == f"domain_experiment:{domain.id}"
    )
    assert domain_node["allowed_actions"] == ["edit", "attach", "add_note", "archive"]


@pytest.mark.asyncio
async def test_source_reverification_receipt_renews_project_reconciliation(read_model_store):
    verified_at = datetime.now(timezone.utc)
    source_digest = "a" * 64
    async with read_model_store() as session:
        project, global_experiment, domain = await _hierarchy(session)
        source_receipt_id = "receipt-reverified"

        acknowledgement = {
            "schema": "bms.global.external-entity-receipt.v1",
            "store_id": "core",
            "entity_kind": "typed_core_job_result",
            "entity_id": "job-drt4",
            "entity_revision_id": "revision-drt4",
            "content_digest": source_digest,
            "contract_digest": source_digest,
            "availability": "available",
            "source_build_revision": "test-build",
            "verified_at": "2026-08-01T00:00:00Z",
            "verifier_id": "test.adapter.v1",
            "reopen_uri": "/designs/job-drt4",
            "metadata": {},
        }
        session.add(ExperimentResource(
            id=source_receipt_id,
            kind="external_entity_receipt",
            workspace_id=project.id,
            lifecycle_owner_id=project.id,
            created_at="2026-08-01T00:00:00Z",
        ))
        await session.flush()
        session.add_all([
            ExperimentExternalEntityReceipt(
                id=source_receipt_id,
                workspace_id=project.id,
                resource_id=source_receipt_id,
                store_id="core",
                entity_kind="typed_core_job_result",
                entity_id="job-drt4",
                generation_or_revision="revision-drt4",
                content_digest=source_digest,
                availability="available",
                verification_authority="test.adapter.v1",
                acknowledgement_json=canonical_json(acknowledgement),
                created_at="2026-08-01T00:00:00Z",
            ),
            ExperimentLineageEdge(
                id="lineage-reverified",
                workspace_id=project.id,
                source_resource_id=domain.id,
                target_resource_id=source_receipt_id,
                edge_mode="references",
                edge_key="attachment:test.adapter.v1:receipt-reverified:references",
                metadata_json="{}",
                created_at="2026-08-01T00:00:00Z",
            ),
        ])
        await session.flush()
        await _add_reverification_receipt(
            session,
            project_id=project.id,
            global_experiment_id=global_experiment.id,
            domain_id=domain.id,
            source_receipt_id=source_receipt_id,
            digest=source_digest,
            reverified_at=verified_at,
            receipt_token="positive",
        )

        read_model = await build_project_manager_read_model(
            session,
            project_id=project.id,
            focus_id=global_experiment.id,
            selected_node_key=f"domain_experiment:{domain.id}",
            map_limit=PAGE_LIMIT,
            lineage_limit=PAGE_LIMIT,
            result_limit=PAGE_LIMIT,
            note_limit=PAGE_LIMIT,
            decision_limit=PAGE_LIMIT,
            dataset_limit=PAGE_LIMIT,
            activity_limit=PAGE_LIMIT,
        )

    assert read_model["reconciliation"] == {
        "state": "current",
        "last_verified_at": verified_at.isoformat(),
        "reason": None,
    }


@pytest.mark.asyncio
async def test_project_reconciliation_reads_every_attached_source_beyond_page_limits(
    read_model_store,
):
    verified_at = datetime.now(timezone.utc)
    async with read_model_store() as session:
        project, global_experiment, domain = await _hierarchy(session)
        await _add_source_receipt(
            session,
            project_id=project.id,
            global_experiment_id=global_experiment.id,
            domain_id=domain.id,
            receipt_id="receipt-stale-beyond-page",
            digest="d" * 64,
            created_at="2026-08-01T00:00:00+00:00",
        )
        await _add_source_receipt(
            session,
            project_id=project.id,
            global_experiment_id=global_experiment.id,
            domain_id=domain.id,
            receipt_id="receipt-current-first-page",
            digest="e" * 64,
            created_at="2026-08-02T00:00:00+00:00",
            reverified_at=verified_at,
        )
        await session.commit()

        read_model = await build_project_manager_read_model(
            session,
            project_id=project.id,
            focus_id=global_experiment.id,
            selected_node_key=f"domain_experiment:{domain.id}",
            map_limit=1,
            lineage_limit=1,
            result_limit=1,
            note_limit=1,
            decision_limit=1,
            dataset_limit=1,
            activity_limit=1,
        )

    assert set(read_model["source_receipt_ids"]) == {
        "receipt-stale-beyond-page",
        "receipt-current-first-page",
    }
    assert read_model["reconciliation"]["state"] == "stale"
    assert read_model["reconciliation"]["reason"] == "persisted verification is historical and has no bounded freshness or re-verification receipt"


@pytest.mark.asyncio
async def test_selected_domain_reconciliation_excludes_other_domain_receipts(
    read_model_store,
):
    verified_at = datetime.now(timezone.utc)
    async with read_model_store() as session:
        project, global_experiment, selected_domain = await _hierarchy(session)
        other_domain = await create_domain_experiment(
            session,
            project.id,
            global_experiment.id,
            {**_domain_payload(), "name": "Other domain"},
        )
        await _add_source_receipt(
            session,
            project_id=project.id,
            global_experiment_id=global_experiment.id,
            domain_id=selected_domain.id,
            receipt_id="receipt-selected-domain",
            digest="f" * 64,
            created_at="2026-08-02T00:00:00+00:00",
            reverified_at=verified_at,
        )
        await _add_source_receipt(
            session,
            project_id=project.id,
            global_experiment_id=global_experiment.id,
            domain_id=other_domain.id,
            receipt_id="receipt-other-domain",
            digest="1" * 64,
            created_at="2026-08-01T00:00:00+00:00",
        )
        await session.commit()

        read_model = await build_project_manager_read_model(
            session,
            project_id=project.id,
            focus_id=global_experiment.id,
            selected_node_key=f"domain_experiment:{selected_domain.id}",
            map_limit=PAGE_LIMIT,
            lineage_limit=PAGE_LIMIT,
            result_limit=PAGE_LIMIT,
            note_limit=PAGE_LIMIT,
            decision_limit=PAGE_LIMIT,
            dataset_limit=PAGE_LIMIT,
            activity_limit=PAGE_LIMIT,
        )

    assert read_model["source_receipt_ids"] == ["receipt-selected-domain"]
    assert read_model["reconciliation"]["state"] == "current"


@pytest.mark.asyncio
async def test_reverification_from_another_domain_cannot_renew_shared_source(
    read_model_store,
):
    verified_at = datetime.now(timezone.utc)
    async with read_model_store() as session:
        project, global_experiment, verifying_domain = await _hierarchy(session)
        selected_domain = await create_domain_experiment(
            session,
            project.id,
            global_experiment.id,
            {**_domain_payload(), "name": "Selected domain"},
        )
        await _add_source_receipt(
            session,
            project_id=project.id,
            global_experiment_id=global_experiment.id,
            domain_id=verifying_domain.id,
            receipt_id="receipt-shared-domains",
            digest="2" * 64,
            created_at="2026-08-02T00:00:00+00:00",
            reverified_at=verified_at,
        )
        session.add(ExperimentLineageEdge(
            id="lineage-shared-selected-domain",
            workspace_id=project.id,
            source_resource_id=selected_domain.id,
            target_resource_id="receipt-shared-domains",
            edge_mode="references",
            edge_key="attachment:test.adapter.v1:receipt-shared-domains:selected-domain",
            metadata_json="{}",
            created_at="2026-08-03T00:00:00+00:00",
        ))
        await session.commit()

        read_model = await build_project_manager_read_model(
            session,
            project_id=project.id,
            focus_id=global_experiment.id,
            selected_node_key=f"domain_experiment:{selected_domain.id}",
            map_limit=PAGE_LIMIT,
            lineage_limit=PAGE_LIMIT,
            result_limit=PAGE_LIMIT,
            note_limit=PAGE_LIMIT,
            decision_limit=PAGE_LIMIT,
            dataset_limit=PAGE_LIMIT,
            activity_limit=PAGE_LIMIT,
        )

    assert read_model["source_receipt_ids"] == ["receipt-shared-domains"]
    assert read_model["reconciliation"]["state"] == "stale"


@pytest.mark.asyncio
async def test_mismatched_reverification_envelope_cannot_renew_source(
    read_model_store,
):
    verified_at = datetime.now(timezone.utc)
    async with read_model_store() as session:
        project, global_experiment, domain = await _hierarchy(session)
        other_domain = await create_domain_experiment(
            session,
            project.id,
            global_experiment.id,
            {**_domain_payload(), "name": "Other domain"},
        )
        await _add_source_receipt(
            session,
            project_id=project.id,
            global_experiment_id=global_experiment.id,
            domain_id=domain.id,
            receipt_id="receipt-envelope-mismatch",
            digest="3" * 64,
            created_at="2026-08-02T00:00:00+00:00",
        )
        await _add_reverification_receipt(
            session,
            project_id=project.id,
            global_experiment_id=global_experiment.id,
            domain_id=domain.id,
            source_receipt_id="receipt-envelope-mismatch",
            digest="3" * 64,
            reverified_at=verified_at,
            receipt_token="envelope-mismatch",
            payload_overrides={"domain_experiment_id": other_domain.id},
        )
        await session.commit()

        read_model = await build_project_manager_read_model(
            session,
            project_id=project.id,
            focus_id=global_experiment.id,
            selected_node_key=f"domain_experiment:{domain.id}",
        )

    assert read_model["reconciliation"]["state"] == "stale"


@pytest.mark.asyncio
@pytest.mark.parametrize("timestamp_mode", ["future", "timezone-less"])
async def test_untrustworthy_reverification_timestamps_cannot_renew_source(
    read_model_store,
    timestamp_mode: str,
):
    now = datetime.now(timezone.utc)
    reverified_at = now + timedelta(hours=1) if timestamp_mode == "future" else now
    overrides = None
    if timestamp_mode == "timezone-less":
        overrides = {
            "verified_at": reverified_at.replace(tzinfo=None).isoformat(),
            "valid_until": (reverified_at + timedelta(hours=24)).replace(tzinfo=None).isoformat(),
        }
    async with read_model_store() as session:
        project, global_experiment, domain = await _hierarchy(session)
        await _add_source_receipt(
            session,
            project_id=project.id,
            global_experiment_id=global_experiment.id,
            domain_id=domain.id,
            receipt_id=f"receipt-time-{timestamp_mode}",
            digest="4" * 64,
            created_at="2026-08-02T00:00:00+00:00",
        )
        await _add_reverification_receipt(
            session,
            project_id=project.id,
            global_experiment_id=global_experiment.id,
            domain_id=domain.id,
            source_receipt_id=f"receipt-time-{timestamp_mode}",
            digest="4" * 64,
            reverified_at=reverified_at,
            receipt_token=f"time-{timestamp_mode}",
            payload_overrides=overrides,
        )
        await session.commit()

        read_model = await build_project_manager_read_model(
            session,
            project_id=project.id,
            focus_id=global_experiment.id,
            selected_node_key=f"domain_experiment:{domain.id}",
        )

    assert read_model["reconciliation"]["state"] != "current"


@pytest.mark.asyncio
async def test_latest_reverification_selection_cannot_starve_other_sources(
    read_model_store,
):
    now = datetime.now(timezone.utc)
    repeated_at = now - timedelta(minutes=10)
    other_at = now - timedelta(minutes=20)
    async with read_model_store() as session:
        project, global_experiment, domain = await _hierarchy(session)
        await _add_source_receipt(
            session,
            project_id=project.id,
            global_experiment_id=global_experiment.id,
            domain_id=domain.id,
            receipt_id="receipt-repeated",
            digest="5" * 64,
            created_at="2026-08-02T00:00:00+00:00",
            reverified_at=repeated_at,
        )
        await _add_source_receipt(
            session,
            project_id=project.id,
            global_experiment_id=global_experiment.id,
            domain_id=domain.id,
            receipt_id="receipt-other-latest",
            digest="6" * 64,
            created_at="2026-08-01T00:00:00+00:00",
            reverified_at=other_at,
        )
        normalized_sha256 = sha256_text("latest-selection")
        resources = []
        receipts = []
        for index in range(1000):
            verified_at = repeated_at + timedelta(microseconds=index + 1)
            resource_id = f"resource-reverify-repeated-{index:04d}"
            body = {
                "schema": "bms.global.source-reverification-receipt.v1",
                "reverification_receipt_id": resource_id,
                "project_id": project.id,
                "global_experiment_id": global_experiment.id,
                "domain_experiment_id": domain.id,
                "adapter_id": "test.adapter.v1",
                "adapter_version": "1",
                "source_receipt_id": "receipt-repeated",
                "source_digest": "5" * 64,
                "verified_at": verified_at.isoformat(),
                "valid_until": (verified_at + timedelta(hours=24)).isoformat(),
                "normalized_request_sha256": normalized_sha256,
            }
            resources.append(ExperimentResource(
                id=resource_id,
                kind="domain_adapter_receipt",
                workspace_id=project.id,
                lifecycle_owner_id=domain.id,
                created_at=verified_at.isoformat(),
            ))
            receipts.append(ExperimentDomainAdapterReceipt(
                resource_id=resource_id,
                workspace_id=project.id,
                domain_experiment_id=domain.id,
                adapter_id="test.adapter.v1",
                adapter_version="1",
                operation_kind="reverify_source",
                normalized_request_sha256=normalized_sha256,
                receipt_json=canonical_json(body),
                created_at=verified_at.isoformat(),
            ))
        session.add_all(resources)
        await session.flush()
        session.add_all(receipts)
        await session.commit()

        read_model = await build_project_manager_read_model(
            session,
            project_id=project.id,
            focus_id=global_experiment.id,
            selected_node_key=f"domain_experiment:{domain.id}",
        )

    assert read_model["reconciliation"]["state"] == "current"


@pytest.mark.asyncio
async def test_reverify_source_route_persists_a_bounded_freshness_receipt(
    read_model_store,
    monkeypatch,
):
    source_digest = "c" * 64
    acknowledgement = {
        "schema": "bms.global.external-entity-receipt.v1",
        "store_id": "core",
        "entity_kind": "typed_core_job_result",
        "entity_id": "job-drt4",
        "entity_revision_id": "revision-drt4",
        "content_digest": source_digest,
        "contract_digest": source_digest,
        "availability": "available",
        "source_build_revision": "test-build",
        "verified_at": "2026-08-01T00:00:00Z",
        "verifier_id": "test.adapter.v1",
        "reopen_uri": "/designs/job-drt4",
        "metadata": {},
    }
    verified_source = dict(acknowledgement)

    class FakeAdapter:
        adapter_id = "test.adapter.v1"
        adapter_version = "1"
        domain_kind = "protein_in_silico"

        async def verify(self, _core_session, entity_id: str):
            assert entity_id == "job-drt4"
            return dict(verified_source)

    monkeypatch.setattr(registry, "get", lambda adapter_id: FakeAdapter())
    monkeypatch.setenv("BMS_CM_TRUSTED_PROXY_SECRET", "test-proxy-secret")
    async with read_model_store() as session:
        project, global_experiment, domain = await _hierarchy(session)
        add_audit_event(
            session,
            workspace_id=project.id,
            resource_id=project.id,
            event_type="workspace_owner_bound",
            generation=project.head_generation,
            payload={"principal_id": "local-application-operator"},
        )
        source_receipt_id = "receipt-route-reverify"
        session.add(ExperimentResource(
            id=source_receipt_id,
            kind="external_entity_receipt",
            workspace_id=project.id,
            lifecycle_owner_id=project.id,
            created_at="2026-08-01T00:00:00Z",
        ))
        await session.flush()
        session.add_all([
            ExperimentExternalEntityReceipt(
                id=source_receipt_id,
                workspace_id=project.id,
                resource_id=source_receipt_id,
                store_id="core",
                entity_kind="typed_core_job_result",
                entity_id="job-drt4",
                generation_or_revision="revision-drt4",
                content_digest=source_digest,
                availability="available",
                verification_authority="test.adapter.v1",
                acknowledgement_json=canonical_json(acknowledgement),
                created_at="2026-08-01T00:00:00Z",
            ),
            ExperimentLineageEdge(
                id="lineage-route-reverify",
                workspace_id=project.id,
                source_resource_id=domain.id,
                target_resource_id=source_receipt_id,
                edge_mode="references",
                edge_key="attachment:test.adapter.v1:receipt-route-reverify:references",
                metadata_json="{}",
                created_at="2026-08-01T00:00:00Z",
            ),
        ])
        await session.commit()

    app = _app(read_model_store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            f"/api/projects/{project.id}/experiments/{global_experiment.id}/domains/"
            f"{domain.id}/source-receipts/{source_receipt_id}/reverify",
            headers={"X-BMS-CM-Proxy-Secret": "test-proxy-secret"},
        )
        verified_source["availability"] = "unavailable"
        unavailable_response = await client.post(
            f"/api/projects/{project.id}/experiments/{global_experiment.id}/domains/"
            f"{domain.id}/source-receipts/{source_receipt_id}/reverify",
            headers={"X-BMS-CM-Proxy-Secret": "test-proxy-secret"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema"] == "bms.global.source-reverification-receipt.v1"
    assert payload["source_receipt_id"] == source_receipt_id
    assert payload["source_digest"] == source_digest
    verified_at = datetime.fromisoformat(payload["verified_at"])
    valid_until = datetime.fromisoformat(payload["valid_until"])
    assert valid_until - verified_at == timedelta(hours=24)
    async with read_model_store() as session:
        persisted_rows = (
            await session.scalars(
                select(ExperimentDomainAdapterReceipt).where(
                    ExperimentDomainAdapterReceipt.operation_kind == "reverify_source"
                )
            )
        ).all()
    assert len(persisted_rows) == 1
    assert json.loads(persisted_rows[0].receipt_json) == payload
    assert unavailable_response.status_code == 422


@pytest.mark.asyncio
async def test_attachment_lineage_result_note_and_activity_pages_are_truthful_and_repeat_context(
    read_model_store,
):
    async with read_model_store() as session:
        project, global_experiment, domain = await _hierarchy(session)
        await _add_attachment_note_activity_rows(
            session,
            project_id=project.id,
            domain_id=domain.id,
        )
        activity_total = len(
            (
                await session.execute(
                    select(ExperimentAuditEvent).where(
                        ExperimentAuditEvent.resource_id == domain.id
                    )
                )
            ).scalars().all()
        )

        first = await build_project_manager_read_model(
            session,
            project_id=project.id,
            focus_id=global_experiment.id,
            selected_node_key=f"domain_experiment:{domain.id}",
            map_limit=PAGE_LIMIT,
            lineage_limit=PAGE_LIMIT,
            result_limit=PAGE_LIMIT,
            note_limit=PAGE_LIMIT,
            activity_limit=PAGE_LIMIT,
        )
        pages = first["pagination"]
        assert first["counts"]["attached_entities"] == ATTACHMENT_TOTAL
        assert len(first["map"]["nodes"]) == PAGE_LIMIT
        assert first["map"]["truncated"] is True
        visible_map_keys = {node["node_key"] for node in first["map"]["nodes"]}
        assert all(
            edge["source_node_key"] in visible_map_keys
            and edge["target_node_key"] in visible_map_keys
            for edge in first["map"]["edges"]
        )
        first_map_receipts = len(pages["map"]["items"])
        assert first_map_receipts < PAGE_LIMIT
        assert len(_node_keys(first, "external_entity_receipt")) == first_map_receipts
        assert len(pages["lineage"]["items"]) == PAGE_LIMIT
        assert len(pages["results"]["items"]) == PAGE_LIMIT
        assert len(pages["notes"]["items"]) == PAGE_LIMIT
        assert len(pages["activity"]["items"]) == PAGE_LIMIT
        assert all(pages[name]["next_cursor"] is not None for name in ("map", "lineage", "results", "notes", "activity"))

        second = await build_project_manager_read_model(
            session,
            project_id=project.id,
            focus_id=global_experiment.id,
            selected_node_key=f"domain_experiment:{domain.id}",
            map_cursor=pages["map"]["next_cursor"],
            lineage_cursor=pages["lineage"]["next_cursor"],
            result_cursor=pages["results"]["next_cursor"],
            note_cursor=pages["notes"]["next_cursor"],
            activity_cursor=pages["activity"]["next_cursor"],
            map_limit=PAGE_LIMIT,
            lineage_limit=PAGE_LIMIT,
            result_limit=PAGE_LIMIT,
            note_limit=PAGE_LIMIT,
            activity_limit=PAGE_LIMIT,
        )
        second_pages = second["pagination"]

        stable_context = {
            f"project:{project.id}",
            f"global_experiment:{global_experiment.id}",
            f"domain_experiment:{domain.id}",
        }
        assert stable_context <= _node_keys(first, "project") | _node_keys(first, "global_experiment") | _node_keys(first, "domain_experiment")
        assert stable_context <= _node_keys(second, "project") | _node_keys(second, "global_experiment") | _node_keys(second, "domain_experiment")
        assert _node_keys(first, "external_entity_receipt").isdisjoint(
            _node_keys(second, "external_entity_receipt")
        )
        assert len(_node_keys(second, "external_entity_receipt")) == ATTACHMENT_TOTAL - first_map_receipts
        assert len(
            _node_keys(first, "external_entity_receipt")
            | _node_keys(second, "external_entity_receipt")
        ) == ATTACHMENT_TOTAL
        assert len(second_pages["lineage"]["items"]) == ATTACHMENT_TOTAL - PAGE_LIMIT
        assert len(second_pages["results"]["items"]) == RESULT_TOTAL - PAGE_LIMIT
        assert len(second_pages["notes"]["items"]) == ATTACHMENT_TOTAL - PAGE_LIMIT
        assert len(second_pages["activity"]["items"]) == activity_total - PAGE_LIMIT
        assert all(
            second_pages[name]["next_cursor"] is None
            for name in ("map", "lineage", "results", "notes", "activity")
        )
        assert {
            item["edge_key"] for item in pages["lineage"]["items"]
        }.isdisjoint({item["edge_key"] for item in second_pages["lineage"]["items"]})
        assert {
            item["receipt_id"] for item in pages["results"]["items"]
        }.isdisjoint({item["receipt_id"] for item in second_pages["results"]["items"]})
        assert f"receipt-{ATTACHMENT_TOTAL - 1:03d}" not in {
            item["receipt_id"]
            for item in pages["results"]["items"] + second_pages["results"]["items"]
        }
        selected_receipt = f"receipt-{ATTACHMENT_TOTAL - 1:03d}"
        return_uri = (
            f"/projects/{project.id}?focus={global_experiment.id}"
            f"&selected=external_entity_receipt%3A{selected_receipt}"
        )
        launch_context = await create_launch_context(
            session,
            project_id=project.id,
            global_experiment_id=global_experiment.id,
            domain_experiment_id=domain.id,
            workflow_id=None,
            workflow_revision_id=None,
            return_uri=return_uri,
        )
        assert launch_context.return_uri == return_uri
        assert {
            item["resource_id"] for item in pages["notes"]["items"]
        }.isdisjoint({item["resource_id"] for item in second_pages["notes"]["items"]})
        assert {
            item["id"] for item in pages["activity"]["items"]
        }.isdisjoint({item["id"] for item in second_pages["activity"]["items"]})


@pytest.mark.asyncio
async def test_summary_route_transports_independent_page_limits_and_cursors(read_model_store):
    async with read_model_store() as session:
        project, global_experiment, domain = await _hierarchy(session)
        await _add_attachment_note_activity_rows(
            session,
            project_id=project.id,
            domain_id=domain.id,
        )

    app = _app(read_model_store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        first_response = await client.get(
            f"/api/projects/{project.id}/summary",
            params={
                "focus_id": global_experiment.id,
                "selected_node_key": f"domain_experiment:{domain.id}",
                "map_limit": 2,
                "lineage_limit": 2,
                "result_limit": 2,
                "note_limit": 2,
                "activity_limit": 2,
            },
        )
        assert first_response.status_code == 200, first_response.text
        first_pages = first_response.json()["pagination"]
        assert len(first_pages["map"]["items"]) <= 2
        assert first_pages["map"]["next_cursor"] is not None
        assert all(
            len(first_pages[name]["items"]) == 2
            for name in ("lineage", "results", "notes", "activity")
        )

        second_response = await client.get(
            f"/api/projects/{project.id}/summary",
            params={
                "focus_id": global_experiment.id,
                "selected_node_key": f"domain_experiment:{domain.id}",
                "map_limit": 2,
                "lineage_limit": 2,
                "result_limit": 2,
                "note_limit": 2,
                "activity_limit": 2,
                "map_cursor": first_pages["map"]["next_cursor"],
                "lineage_cursor": first_pages["lineage"]["next_cursor"],
                "result_cursor": first_pages["results"]["next_cursor"],
                "note_cursor": first_pages["notes"]["next_cursor"],
                "activity_cursor": first_pages["activity"]["next_cursor"],
            },
        )
        assert second_response.status_code == 200, second_response.text
        second_pages = second_response.json()["pagination"]
        assert {
            item["edge_key"] for item in first_pages["lineage"]["items"]
        }.isdisjoint({item["edge_key"] for item in second_pages["lineage"]["items"]})

        invalid = await client.get(
            f"/api/projects/{project.id}/summary",
            params={"focus_id": global_experiment.id, "note_cursor": "map:not-a-note-cursor"},
        )
        assert invalid.status_code == 422


async def _add_workflow_runs(session, *, project_id: str, domain_experiment_id: str):
    workflow = await create_workflow(
        session,
        project_id,
        "Selectable workflow",
        "test_family",
        experiment_id=domain_experiment_id,
    )
    workflow_id = workflow.id
    revision_id = "workflow-revision-selectable"
    preparation_id = "preparation-selectable"
    run_group_id = "run-group-selectable"
    session.add_all(
        [
            ExperimentResource(
                id=revision_id,
                kind="revision",
                workspace_id=project_id,
                lifecycle_owner_id=workflow_id,
                created_at="2026-08-09T13:00:00Z",
            ),
            ExperimentResource(
                id=preparation_id,
                kind="workflow_preparation",
                workspace_id=project_id,
                lifecycle_owner_id=workflow_id,
                created_at="2026-08-09T13:00:00Z",
            ),
            ExperimentResource(
                id=run_group_id,
                kind="run_group",
                workspace_id=project_id,
                lifecycle_owner_id=workflow_id,
                created_at="2026-08-09T13:00:00Z",
            ),
        ]
    )
    await session.flush()
    session.add(
        ExperimentRevision(
            resource_id=revision_id,
            subject_id=workflow_id,
            revision_number=1,
            schema_name="bms.workflow.v1",
            schema_version="1",
            canonical_payload=canonical_json(
                {
                    "name": "Selectable workflow",
                    "workflow_family": "test_family",
                    "target_label": "PLM-07",
                }
            ),
            payload_sha256="1" * 64,
            dependency_graph_sha256="2" * 64,
            provenance_json="{}",
            created_at="2026-08-09T13:00:00Z",
        )
    )
    workflow.current_revision_id = revision_id
    workflow.head_generation = 1
    await session.flush()
    session.add_all(
        [
            ExperimentWorkflowPreparation(
                resource_id=preparation_id,
                workspace_id=project_id,
                workflow_revision_id=revision_id,
                normalized_request_json="{}",
                normalized_request_sha256="3" * 64,
                scheduler_payload_json="{}",
                validation_status="valid",
                validation_receipt_json="{}",
                created_at="2026-08-09T13:00:00Z",
            ),
            ExperimentRunGroup(
                resource_id=run_group_id,
                workspace_id=project_id,
                launch_idempotency_key="selectable-runs",
                request_sha256="4" * 64,
                state="running",
                generation=1,
                created_at="2026-08-09T13:00:00Z",
                updated_at="2026-08-09T13:00:00Z",
            ),
        ]
    )
    await session.flush()
    runs = []
    for index in range(3):
        run_id = f"workflow-run-{index}"
        session.add(
            ExperimentResource(
                id=run_id,
                kind="workflow_run",
                workspace_id=project_id,
                lifecycle_owner_id=workflow_id,
                created_at="2026-08-09T13:01:00Z",
            )
        )
        await session.flush()
        run = ExperimentWorkflowRun(
            resource_id=run_id,
            workspace_id=project_id,
            run_group_id=run_group_id,
            preparation_id=preparation_id,
            node_id=f"node-{index}",
            requiredness="required",
            state="running",
            generation=1,
            created_at="2026-08-09T13:01:00Z",
        )
        session.add(run)
        runs.append(run)
    await session.commit()
    return workflow, runs


async def _add_retry_attempts(session, *, project_id: str, run: ExperimentWorkflowRun) -> None:
    for attempt_id, created_at in (
        ("run-attempt-failed", "2026-08-09T13:02:00Z"),
        ("run-attempt-completed", "2026-08-09T13:04:00Z"),
    ):
        session.add(
            ExperimentResource(
                id=attempt_id,
                kind="run_attempt",
                workspace_id=project_id,
                lifecycle_owner_id=run.resource_id,
                created_at=created_at,
            )
        )
    await session.flush()
    session.add_all(
        [
            ExperimentRunAttempt(
                resource_id="run-attempt-failed",
                workspace_id=project_id,
                workflow_run_id=run.resource_id,
                attempt_number=1,
                scheduler_job_id="canonical-job-failed",
                state="failed",
                external_binding_receipt_json=canonical_json(
                    {"receipt_id": "binding-receipt-failed", "adapter_id": "test.adapter.v1"}
                ),
                terminal_receipt_json=canonical_json(
                    {"error_message": "transient scheduler failure", "output_count": 0}
                ),
                created_at="2026-08-09T13:02:00Z",
            ),
            ExperimentRunAttempt(
                resource_id="run-attempt-completed",
                workspace_id=project_id,
                workflow_run_id=run.resource_id,
                attempt_number=2,
                scheduler_job_id="canonical-job-completed",
                state="completed",
                external_binding_receipt_json=canonical_json(
                    {
                        "receipt_id": "binding-receipt-completed",
                        "adapter_id": "test.adapter.v1",
                        "canonical_state": "succeeded",
                        "stage": "finalized",
                        "progress": {"kind": "fraction", "value": 1.0},
                        "started_at": "2026-08-09T13:04:00Z",
                        "elapsed_seconds": 75,
                    }
                ),
                runtime_identity_json=canonical_json({"gpu": "GPU 0"}),
                terminal_receipt_json=canonical_json(
                    {"completed_at": "2026-08-09T13:05:15Z", "output_count": 12}
                ),
                created_at="2026-08-09T13:04:00Z",
            ),
        ]
    )
    run.state = "completed"
    run.generation = 2
    await session.commit()


@pytest.mark.asyncio
async def test_each_workflow_run_is_one_canonical_run_and_retry_attempts_are_not_replicas(
    read_model_store,
):
    async with read_model_store() as session:
        project, global_experiment, domain = await _hierarchy(session)
        _workflow, runs = await _add_workflow_runs(
            session,
            project_id=project.id,
            domain_experiment_id=domain.id,
        )
        await _add_retry_attempts(session, project_id=project.id, run=runs[0])

        read_model = await build_project_manager_read_model(
            session,
            project_id=project.id,
            focus_id=global_experiment.id,
            run_limit=10,
        )

        assert len(read_model["runs"]["items"]) == 3
        item = next(item for item in read_model["runs"]["items"] if item["run_id"] == runs[0].resource_id)
        assert set(item) == {
            "run_id",
            "workflow_id",
            "canonical_job_id",
            "workflow_type",
            "target_label",
            "canonical_state",
            "normalized_state",
            "stage",
            "progress",
            "started_at",
            "elapsed_seconds",
            "replica_index",
            "batch_or_run_group_id",
            "output_count",
            "condition",
            "receipt_id",
            "output_receipt_ids",
            "adapter_id",
            "available_actions",
            "canonical_surface",
            "canonical_surfaces",
            "attempts",
        }
        assert item == {
            "run_id": runs[0].resource_id,
            "workflow_id": _workflow.id,
            "canonical_job_id": "canonical-job-completed",
            "workflow_type": "test_family",
            "target_label": "PLM-07",
            "canonical_state": "succeeded",
            "normalized_state": "completed",
            "stage": "finalized",
            "progress": {"kind": "fraction", "value": 1.0},
            "started_at": "2026-08-09T13:04:00Z",
            "elapsed_seconds": 75,
            "replica_index": None,
            "batch_or_run_group_id": "run-group-selectable",
            "output_count": 12,
            "condition": {"severity": "none", "code": None, "message": None},
            "receipt_id": "binding-receipt-completed",
            "output_receipt_ids": [],
            "adapter_id": "test.adapter.v1",
            "available_actions": ["view_lineage"],
            "canonical_surface": None,
            "canonical_surfaces": [],
            "attempts": [
                {
                    "attempt_id": "run-attempt-failed",
                    "attempt_number": 1,
                    "canonical_job_id": "canonical-job-failed",
                    "canonical_state": "failed",
                    "binding_receipt": {
                        "adapter_id": "test.adapter.v1",
                        "receipt_id": "binding-receipt-failed",
                    },
                    "runtime_identity": None,
                    "terminal_receipt": {
                        "error_message": "transient scheduler failure",
                        "output_count": 0,
                    },
                },
                {
                    "attempt_id": "run-attempt-completed",
                    "attempt_number": 2,
                    "canonical_job_id": "canonical-job-completed",
                    "canonical_state": "completed",
                    "binding_receipt": {
                        "adapter_id": "test.adapter.v1",
                        "canonical_state": "succeeded",
                        "elapsed_seconds": 75,
                        "progress": {"kind": "fraction", "value": 1.0},
                        "receipt_id": "binding-receipt-completed",
                        "stage": "finalized",
                        "started_at": "2026-08-09T13:04:00Z",
                    },
                    "runtime_identity": {"gpu": "GPU 0"},
                    "terminal_receipt": {
                        "completed_at": "2026-08-09T13:05:15Z",
                        "output_count": 12,
                    },
                },
            ],
        }
        assert "replicas" not in item


@pytest.mark.asyncio
async def test_runs_use_stable_cursor_pages_and_workflow_run_map_nodes_are_selectable(read_model_store):
    async with read_model_store() as session:
        project, global_experiment, domain = await _hierarchy(session)
        workflow, _runs = await _add_workflow_runs(
            session,
            project_id=project.id,
            domain_experiment_id=domain.id,
        )

        first = await build_project_manager_read_model(
            session,
            project_id=project.id,
            focus_id=global_experiment.id,
            selected_node_key=f"workflow:{workflow.id}",
            run_limit=2,
        )
        assert first["selection"]["node_type"] == "workflow"
        assert len(first["runs"]["items"]) == 2
        assert first["runs"]["next_cursor"] is not None
        assert f"workflow:{workflow.id}" in _node_keys(first, "workflow")
        first_run_keys = _node_keys(first, "workflow_run")
        assert len(first_run_keys) == 2

        second = await build_project_manager_read_model(
            session,
            project_id=project.id,
            focus_id=global_experiment.id,
            selected_node_key=next(iter(first_run_keys)),
            run_cursor=first["runs"]["next_cursor"],
            run_limit=2,
        )
        assert second["selection"]["node_type"] == "workflow_run"
        assert len(second["runs"]["items"]) == 1
        assert second["runs"]["next_cursor"] is None
        assert {
            item["run_id"] for item in first["runs"]["items"]
        }.isdisjoint({item["run_id"] for item in second["runs"]["items"]})
        assert {
            f"project:{project.id}",
            f"global_experiment:{global_experiment.id}",
            f"domain_experiment:{domain.id}",
            f"workflow:{workflow.id}",
        } <= {
            node["node_key"] for node in second["map"]["nodes"]
        }

        persisted_ids = {
            row.resource_id
            for row in (await session.execute(select(ExperimentWorkflowRun))).scalars().all()
        }
        paged_ids = {
            item["run_id"] for item in first["runs"]["items"] + second["runs"]["items"]
        }
        assert paged_ids == persisted_ids
