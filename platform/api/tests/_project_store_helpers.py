"""Shared scratch experiment-store fixtures for Project tests."""
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
from sqlalchemy import event, select
from database import get_session as get_core_session
from experiment_database import (
    create_experiment_engine,
    create_experiment_session_factory,
    get_experiment_session,
)
from experiment_models import (
    ExperimentBase,
    ExperimentAggregateHead,
    ExperimentAuditEvent,
    ExperimentDomainAdapterReceipt,
    ExperimentExternalEntityReceipt,
    ExperimentLineageEdge,
    ExperimentLaunchContext,
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
from routers._projects_domain_ops import router as project_manager_router
from services.global_experiments.adapters import registry
from services.global_experiments.launch_contexts import (
    LaunchContextError,
    context_document,
    create_launch_context,
)
from services.ngs_molbio_preparation_authority import (
    _hierarchy_revision_reference_ids,
    _validate_hierarchy_producer_payload,
)


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
                preparation_id=run.preparation_id,
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
                preparation_id=run.preparation_id,
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


