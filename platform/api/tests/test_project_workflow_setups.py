from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import experiment_migrations

import pytest
import pytest_asyncio
from sqlalchemy import func, select

import experiment_models
from experiment_database import create_experiment_engine, create_experiment_session_factory
from experiment_models import (
    ExperimentAggregateHead,
    ExperimentLaunchContext,
    ExperimentRunGroup,
    ExperimentWorkflowPreparation,
    ExperimentWorkflowSetupContext,
)
from experiment_services import RevisionConflict, ValidationFailure, create_project
from services.global_experiments import workflow_setups
from services.global_experiments.read_models import build_project_manager_read_model
from services.protein_project_capabilities import protein_capability_inventory


READY_CAPABILITIES = {
    "protein.structure_prediction.boltz2",
    "protein.structure_prediction.esmfold2",
    "protein.structure_prediction.protenix_v2",
    "protein.de_novo.local_redesign",
    "protein.conformational_mapping.protenix_v2",
    "protein.conformational_mapping.confornets",
}


def test_setup_context_service_module_is_registered() -> None:
    assert importlib.util.find_spec("services.global_experiments.workflow_setups") is not None


def test_migration_v21_persists_and_attests_workflow_setup_contexts(tmp_path: Path) -> None:
    db_path = tmp_path / "migrated.db"
    experiment_migrations.run_all(db_path)
    connection = sqlite3.connect(db_path)
    try:
        assert experiment_migrations.LATEST_MIGRATION_VERSION == 21
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='workflow_setup_contexts'"
        ).fetchone() == ("workflow_setup_contexts",)
        assert connection.execute(
            "SELECT version, name FROM experiment_schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone() == (21, "project_workflow_setup_context_authority")
        assert experiment_migrations.attest_schema(connection)["ok"] is True
    finally:
        connection.close()


def test_setup_context_service_exposes_lifecycle_operations() -> None:
    assert all(
        callable(getattr(workflow_setups, name, None))
        for name in (
            "create_workflow_setup",
            "get_workflow_setup",
            "save_workflow_setup_draft",
            "prepare_workflow_setup_launch",
            "delete_workflow_setup",
        )
    )


def test_setup_context_persistence_authority_is_registered() -> None:
    model = getattr(experiment_models, "ExperimentWorkflowSetupContext", None)
    assert model is not None
    assert model.__tablename__ == "workflow_setup_contexts"
    assert {
        "setup_context_id",
        "project_id",
        "global_experiment_id",
        "domain_experiment_id",
        "workflow_id",
        "relationship_kind",
        "capability_id",
        "capability_contract_json",
        "capability_contract_sha256",
        "setup_destination",
        "draft_json",
        "draft_sha256",
        "generation",
        "validation_state",
        "lifecycle_state",
        "created_at",
        "updated_at",
        "submitted_at",
        "deleted_at",
    } <= set(model.__table__.columns.keys())


def test_project_picker_exposes_exactly_six_ready_capabilities() -> None:
    inventory = protein_capability_inventory(project_ready_only=True)
    assert {item["capability_id"] for item in inventory["capabilities"]} == READY_CAPABILITIES
    for item in inventory["capabilities"]:
        assert item["project_setup_state"] == "ready"
        assert item["project_setup_adapter_id"]
        assert item["safe_setup_destination"].startswith("/projects/workflow-setup/")
        assert isinstance(item["source_requirements"], list)
        assert set(item["follow_up_compatible_capability_ids"]) <= READY_CAPABILITIES


def test_unavailable_capabilities_remain_catalogued_but_hidden_from_project_picker() -> None:
    complete = protein_capability_inventory()
    hidden = [item for item in complete["capabilities"] if item["capability_id"] not in READY_CAPABILITIES]
    assert hidden
    assert all(item["project_setup_state"] != "ready" for item in hidden)


@pytest_asyncio.fixture
async def setup_store(tmp_path: Path):
    db_path = tmp_path / "setups.db"
    experiment_migrations.run_all(db_path)
    engine = create_experiment_engine(f"sqlite+aiosqlite:///{db_path}")
    factory = create_experiment_session_factory(engine)
    try:
        yield factory
    finally:
        await engine.dispose()


def _project_payload(name: str) -> dict:
    return {
        "schema": "bms.project.v1",
        "name": name,
        "description": "Project workflow setup tests",
        "research_objective": "Configure Protein workflows without execution",
        "owner": "operator",
        "contributors": [],
        "tags": ["protein"],
        "status": "active",
        "start_date": None,
        "target_end_date": None,
        "external_references": [],
        "created_by": "operator",
        "change_summary": "created",
    }


async def _project(session, name: str = "Protein Project"):
    project = await create_project(session, _project_payload(name))
    await session.flush()
    return project


@pytest.mark.asyncio
async def test_primary_setup_atomically_creates_shell_context_and_no_execution(setup_store) -> None:
    async with setup_store() as session:
        project = await _project(session)
        document = await workflow_setups.create_workflow_setup(
            session,
            project_id=project.id,
            relationship_kind="primary",
            global_experiment_id=None,
            experiment_name="Fold target",
            experiment_objective="Predict the target structure",
            domain_kind="protein_in_silico",
            capability_id="protein.structure_prediction.esmfold2",
            idempotency_key="primary-create-0001",
        )
        replay = await workflow_setups.create_workflow_setup(
            session,
            project_id=project.id,
            relationship_kind="primary",
            global_experiment_id=None,
            experiment_name="Fold target",
            experiment_objective="Predict the target structure",
            domain_kind="protein_in_silico",
            capability_id="protein.structure_prediction.esmfold2",
            idempotency_key="primary-create-0001",
        )
        assert replay == document
        assert document["schema"] == "bms.project-workflow-setup.v1"
        assert document["state"] == "open"
        assert document["validation_state"] == "incomplete"
        assert document["setup_destination"].startswith("/projects/workflow-setup/")
        assert document["return_uri"].startswith(f"/projects/{project.id}?")
        row = await session.get(ExperimentWorkflowSetupContext, document["setup_context_id"])
        assert row is not None
        assert row.relationship_kind == "primary"
        assert await session.scalar(select(func.count()).select_from(ExperimentWorkflowPreparation)) == 0
        assert await session.scalar(select(func.count()).select_from(ExperimentLaunchContext)) == 0
        assert await session.scalar(select(func.count()).select_from(ExperimentRunGroup)) == 0


@pytest.mark.asyncio
async def test_primary_failure_rolls_back_without_orphan_experiment(
    setup_store, monkeypatch
) -> None:
    async with setup_store() as session:
        project = await _project(session)
        project_id = project.id
        await session.commit()
        original_create_domain = workflow_setups.create_domain_experiment

        async def create_domain_then_fail(*args, **kwargs):
            await original_create_domain(*args, **kwargs)
            raise ValidationFailure("forced post-experiment failure")

        monkeypatch.setattr(
            workflow_setups, "create_domain_experiment", create_domain_then_fail
        )
        with pytest.raises(ValidationFailure, match="forced post-experiment failure"):
            await workflow_setups.create_workflow_setup(
                session, project_id=project_id, relationship_kind="primary",
                global_experiment_id=None, experiment_name="Rollback", experiment_objective="No orphan",
                domain_kind="protein_in_silico", capability_id="protein.structure_prediction.boltz2",
                idempotency_key="failed-primary-0001",
            )
        await session.rollback()
        orphan_count = await session.scalar(
            select(func.count()).select_from(ExperimentAggregateHead).where(
                ExperimentAggregateHead.aggregate_kind == "experiment",
                ExperimentAggregateHead.workspace_id == project_id,
            )
        )
        assert orphan_count == 0


@pytest.mark.asyncio
async def test_follow_up_reuses_owned_global_experiment_and_cross_project_fails_closed(setup_store) -> None:
    async with setup_store() as session:
        project = await _project(session, "One")
        other = await _project(session, "Two")
        primary = await workflow_setups.create_workflow_setup(
            session, project_id=project.id, relationship_kind="primary",
            global_experiment_id=None, experiment_name="Target", experiment_objective="Fold",
            domain_kind="protein_in_silico", capability_id="protein.structure_prediction.boltz2",
            idempotency_key="primary-one-0001",
        )
        follow_up = await workflow_setups.create_workflow_setup(
            session, project_id=project.id, relationship_kind="follow_up",
            global_experiment_id=primary["global_experiment_id"], experiment_name=None,
            experiment_objective=None, domain_kind="protein_in_silico",
            capability_id="protein.conformational_mapping.confornets",
            idempotency_key="follow-up-one-0001",
        )
        assert follow_up["global_experiment_id"] == primary["global_experiment_id"]
        assert follow_up["relationship_kind"] == "follow_up"
        with pytest.raises(ValidationFailure, match="Project"):
            await workflow_setups.get_workflow_setup(
                session, project_id=other.id, setup_context_id=primary["setup_context_id"]
            )
        with pytest.raises(ValidationFailure, match="Project"):
            await workflow_setups.create_workflow_setup(
                session, project_id=other.id, relationship_kind="follow_up",
                global_experiment_id=primary["global_experiment_id"], experiment_name=None,
                experiment_objective=None, domain_kind="protein_in_silico",
                capability_id="protein.structure_prediction.protenix_v2",
                idempotency_key="cross-project-0001",
            )


@pytest.mark.asyncio
async def test_draft_save_is_optimistic_idempotent_and_adapter_validated(setup_store) -> None:
    async with setup_store() as session:
        project = await _project(session)
        setup = await workflow_setups.create_workflow_setup(
            session, project_id=project.id, relationship_kind="primary",
            global_experiment_id=None, experiment_name="Target", experiment_objective="Fold",
            domain_kind="protein_in_silico", capability_id="protein.structure_prediction.esmfold2",
            idempotency_key="primary-save-0001",
        )
        saved = await workflow_setups.save_workflow_setup_draft(
            session, project_id=project.id, setup_context_id=setup["setup_context_id"],
            draft={"sequence": "MQIFVK"}, expected_generation=0,
            idempotency_key="draft-save-0001",
        )
        replay = await workflow_setups.save_workflow_setup_draft(
            session, project_id=project.id, setup_context_id=setup["setup_context_id"],
            draft={"sequence": "MQIFVK"}, expected_generation=0,
            idempotency_key="draft-save-0001",
        )
        assert replay == saved
        assert saved["generation"] == 1
        assert saved["validation_state"] == "ready"
        assert saved["draft"]["sequence"] == "MQIFVK"
        assert saved["draft"]["pred_method"] == "esmfold2"
        with pytest.raises(RevisionConflict):
            await workflow_setups.save_workflow_setup_draft(
                session, project_id=project.id, setup_context_id=setup["setup_context_id"],
                draft={"sequence": "AAAA"}, expected_generation=0,
                idempotency_key="draft-save-0002",
            )


@pytest.mark.asyncio
async def test_prepare_launch_freezes_authorities_without_run_or_job_submission(setup_store) -> None:
    async with setup_store() as session:
        project = await _project(session)
        setup = await workflow_setups.create_workflow_setup(
            session, project_id=project.id, relationship_kind="primary",
            global_experiment_id=None, experiment_name="Target", experiment_objective="Fold",
            domain_kind="protein_in_silico", capability_id="protein.structure_prediction.esmfold2",
            idempotency_key="primary-prepare-0001",
        )
        await workflow_setups.save_workflow_setup_draft(
            session, project_id=project.id, setup_context_id=setup["setup_context_id"],
            draft={"sequence": "MQIFVK"}, expected_generation=0,
            idempotency_key="draft-prepare-0001",
        )
        prepared = await workflow_setups.prepare_workflow_setup_launch(
            session, project_id=project.id, setup_context_id=setup["setup_context_id"],
            expected_generation=1, idempotency_key="prepare-launch-0001",
        )
        assert prepared["state"] == "submitted"
        assert prepared["launch_context_id"]
        assert prepared["preparation_id"]
        assert await session.scalar(select(func.count()).select_from(ExperimentWorkflowPreparation)) == 1
        assert await session.scalar(select(func.count()).select_from(ExperimentLaunchContext)) == 1
        assert await session.scalar(select(func.count()).select_from(ExperimentRunGroup)) == 0


@pytest.mark.asyncio
async def test_task_first_read_model_projects_setup_relationship_and_actions(setup_store) -> None:
    async with setup_store() as session:
        project = await _project(session)
        setup = await workflow_setups.create_workflow_setup(
            session, project_id=project.id, relationship_kind="primary",
            global_experiment_id=None, experiment_name="Familiar experiment",
            experiment_objective="Configure folding", domain_kind="protein_in_silico",
            capability_id="protein.structure_prediction.boltz2",
            idempotency_key="primary-task-read-0001",
        )
        read_model = await build_project_manager_read_model(session, project_id=project.id)
        task = next(item for item in read_model["tasks"] if item["setup_context_id"] == setup["setup_context_id"])
        assert task == {
            "setup_context_id": setup["setup_context_id"],
            "global_experiment_id": setup["global_experiment_id"],
            "experiment_name": "Familiar experiment",
            "workflow_id": setup["workflow_id"],
            "workflow_name": "Familiar experiment — Boltz-2 structure prediction",
            "relationship_kind": "primary",
            "workflow_label": "Boltz-2 structure prediction",
            "setup_state": "open",
            "validation_state": "incomplete",
            "latest_run_state": None,
            "result_count": 0,
            "reopen_route": f"/projects/{project.id}/workflow-setups/{setup['setup_context_id']}",
            "allowed_actions": ["resume", "edit", "delete"],
        }
        assert "tree" in read_model and "map" in read_model and "selection" in read_model


@pytest.mark.asyncio
async def test_delete_is_bounded_to_unsubmitted_setup_and_idempotent(setup_store) -> None:
    async with setup_store() as session:
        project = await _project(session)
        setup = await workflow_setups.create_workflow_setup(
            session, project_id=project.id, relationship_kind="primary",
            global_experiment_id=None, experiment_name="Delete me", experiment_objective="No launch",
            domain_kind="protein_in_silico", capability_id="protein.structure_prediction.boltz2",
            idempotency_key="primary-delete-0001",
        )
        deleted = await workflow_setups.delete_workflow_setup(
            session, project_id=project.id, setup_context_id=setup["setup_context_id"],
            idempotency_key="delete-setup-0001",
        )
        replay = await workflow_setups.delete_workflow_setup(
            session, project_id=project.id, setup_context_id=setup["setup_context_id"],
            idempotency_key="delete-setup-0001",
        )
        assert replay == deleted
        assert deleted["state"] == "deleted"
        assert await session.scalar(select(func.count()).select_from(ExperimentRunGroup)) == 0
