from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import sqlite3
from pathlib import Path

import experiment_migrations
import yaml

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
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
from routers.project_manager import router as project_manager_router
from services.global_experiments import workflow_setups
from services.global_experiments.read_models import build_project_manager_read_model
from services import protein_project_capabilities
from services.protein_project_capabilities import protein_capability_inventory
from template_registry import TemplateRegistry


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


def _de_novo_capabilities() -> tuple[dict, dict, Path]:
    rfd3_mode = next(
        record
        for record in protein_project_capabilities._CAPABILITIES
        if record["capability_id"] == "protein.de_novo.rfd3"
    )
    local_redesign = next(
        record
        for record in protein_project_capabilities._CAPABILITIES
        if record["capability_id"] == "protein.de_novo.local_redesign"
    )
    return rfd3_mode, local_redesign, Path(__file__).resolve().parents[1] / "config" / "templates"


async def _project_capability_api() -> dict:
    app = FastAPI()
    app.include_router(project_manager_router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/protein-project-capabilities")
    assert response.status_code == 200, response.text
    return response.json()


def _configure_legacy_protein_cad_catalog(
    monkeypatch, tmp_path: Path, *, enabled: bool = True, experimental: bool = False
) -> None:
    _, _, templates_dir = _de_novo_capabilities()
    promoted_templates_dir = tmp_path / "templates"
    shutil.copytree(templates_dir, promoted_templates_dir)
    promoted_yaml = promoted_templates_dir / "protein_cad_experimental.yaml"
    source_metadata = yaml.safe_load(
        (templates_dir / "protein_cad_experimental.yaml").read_text()
    )
    promoted_metadata = yaml.safe_load(promoted_yaml.read_text())
    promoted_metadata["enabled"] = enabled
    promoted_metadata["experimental"] = experimental
    assert promoted_metadata == {
        **source_metadata,
        "enabled": enabled,
        "experimental": experimental,
    }
    promoted_yaml.write_text(yaml.safe_dump(promoted_metadata, sort_keys=False))
    registry = TemplateRegistry(promoted_templates_dir)
    publication = registry.get_template("protein_cad_experimental")
    assert publication is not None and publication.enabled is enabled
    assert publication.experimental is experimental
    monkeypatch.setattr(protein_project_capabilities, "get_template_registry", lambda: registry)


@pytest.mark.asyncio
async def test_project_picker_never_promotes_a_de_novo_child_as_a_workflow(
    monkeypatch, tmp_path: Path
) -> None:
    rfd3_mode, local_redesign, templates_dir = _de_novo_capabilities()
    assert rfd3_mode["label"] == "RFD3 de novo design"
    assert rfd3_mode["product_taxonomy"]["workflow_id"] == "protein_modification_experimental"
    assert rfd3_mode["product_taxonomy"]["parent_capability_id"] is None
    assert rfd3_mode["exposure_state"] == "integrated_component"
    assert rfd3_mode["availability"]["state"] == "operational_as_child"
    assert rfd3_mode["canonical_source_destination"] == "/submit?template=protein_modification_experimental"
    assert rfd3_mode["workflow_adapter_id"] is None
    assert rfd3_mode["allowed_model_modes"] == []
    assert rfd3_mode["project_setup_destination"] is None
    assert rfd3_mode["project_native_owner_id"] is None
    assert rfd3_mode["publication_template_id"] is None
    assert local_redesign["product_taxonomy"]["workflow_id"] == "protein_modification_experimental"
    assert local_redesign["product_taxonomy"]["parent_capability_id"] is None
    assert local_redesign["exposure_state"] == "integrated_component"
    assert local_redesign["availability"]["state"] == "operational_as_child"
    assert local_redesign["project_setup_destination"] is None
    assert local_redesign["project_native_owner_id"] is None
    assert local_redesign["publication_template_id"] is None
    assert protein_project_capabilities._PARAMETER_SCHEMAS[
        "protein.de_novo.local_redesign"
    ]["x-bms-executable-authority"] == "typed_core_job_outside_project_manager"
    baseline_registry = TemplateRegistry(templates_dir)
    monkeypatch.setattr(
        protein_project_capabilities, "get_template_registry", lambda: baseline_registry
    )
    existing_inventory = await _project_capability_api()
    existing_ids = {
        item["capability_id"]
        for item in existing_inventory["capabilities"]
    }
    assert "protein.de_novo.local_redesign" not in existing_ids
    _configure_legacy_protein_cad_catalog(monkeypatch, tmp_path)

    inventory = await _project_capability_api()
    assert set(inventory) == {"schema", "capabilities"}
    assert inventory["schema"] == "bms.protein-project-workflow-picker.v1"
    ready_ids = {item["capability_id"] for item in inventory["capabilities"]}
    assert ready_ids == existing_ids
    assert protein_project_capabilities.protein_capability_record(
        "protein.de_novo.local_redesign"
    )["project_setup_state"] == "unavailable"
    for item in inventory["capabilities"]:
        assert set(item) == {
            "capability_id", "label", "state", "adapter_id", "native_owner_id",
            "setup_destination", "source_requirements", "follow_up_compatible_capability_ids",
        }
        assert item["state"] == "ready"
        assert item["adapter_id"].strip()
        assert item["native_owner_id"].strip()
        assert item["setup_destination"].startswith(
            f"/submit?template={item['native_owner_id']}"
        )
        assert isinstance(item["source_requirements"], list)
        assert set(item["follow_up_compatible_capability_ids"]) == ready_ids


def _ready_esmfold2_capability() -> dict:
    return next(
        record
        for record in protein_project_capabilities._CAPABILITIES
        if record["capability_id"] == "protein.structure_prediction.esmfold2"
    )


@pytest.mark.parametrize(
    "invalid_updates",
    [
        {"exposure_state": "integrated_component"},
        {"availability": {"state": "operational_outside_project_manager", "reason": "outside"}},
        {"availability": {"state": "operationally_disabled", "reason": "disabled"}},
        {"publication_template_id": "   "},
        {"publication_template_id": "does_not_exist"},
        {"workflow_adapter_id": "   "},
        {
            "workflow_adapter_id": " bms.core-job.esmfold2.adapter.v1 ",
            "result_adapter_ids": [" bms.core-job.esmfold2.adapter.v1 "],
        },
        {
            "workflow_adapter_id": "bms.core-job.unregistered.adapter.v1",
            "result_adapter_ids": ["bms.core-job.unregistered.adapter.v1"],
        },
        {"result_adapter_ids": []},
        {"result_adapter_ids": ["   "]},
        {"launch_mode": "scheduler_owned_child"},
        {"allowed_model_modes": [{}]},
        {
            "allowed_model_modes": [
                {"model_id": "esmfold2", "mode": "predict"},
                {"model_id": "esmfold2", "mode": "validate"},
            ],
        },
        {"project_setup_destination": "/submit?template="},
        {"project_setup_destination": "/submit?template=does_not_exist"},
        {
            "project_setup_destination": (
                "https://untrusted.invalid/submit?template=structure_prediction"
            )
        },
        {"project_native_owner_id": None},
        {"project_native_owner_id": " structure_prediction "},
        {
            "project_native_owner_id": "molecular_dynamics",
            "project_setup_destination": "/submit?template=molecular_dynamics",
        },
        {"parameter_schema_id": "bms.workflow-parameters.unknown.v1"},
        {
            "parameter_schema_id": protein_project_capabilities._PARAMETER_SCHEMAS[
                "protein.structure_prediction.boltz2"
            ]["$id"]
        },
    ],
)
def test_project_picker_rejects_invalid_workflow_contracts(
    monkeypatch, invalid_updates: dict
) -> None:
    capability = _ready_esmfold2_capability()
    for key, value in invalid_updates.items():
        monkeypatch.setitem(capability, key, value)

    ready_ids = {
        item["capability_id"]
        for item in protein_capability_inventory(project_ready_only=True)["capabilities"]
    }
    assert capability["capability_id"] not in ready_ids


def test_project_picker_rejects_schema_authority_outside_project_manager(
    monkeypatch,
) -> None:
    capability = _ready_esmfold2_capability()
    schema = protein_project_capabilities._PARAMETER_SCHEMAS[capability["capability_id"]]
    monkeypatch.setitem(
        schema, "x-bms-executable-authority", "typed_core_job_outside_project_manager"
    )
    ready_ids = {
        item["capability_id"]
        for item in protein_capability_inventory(project_ready_only=True)["capabilities"]
    }
    assert capability["capability_id"] not in ready_ids


def test_project_picker_rejects_disabled_canonical_workflow_publication(
    monkeypatch, tmp_path: Path
) -> None:
    capability = _ready_esmfold2_capability()
    _, _, templates_dir = _de_novo_capabilities()
    disabled_templates_dir = tmp_path / "templates"
    shutil.copytree(templates_dir, disabled_templates_dir)
    structure_yaml = disabled_templates_dir / "structure_prediction.yaml"
    metadata = yaml.safe_load(structure_yaml.read_text())
    metadata["enabled"] = False
    structure_yaml.write_text(yaml.safe_dump(metadata, sort_keys=False))
    registry = TemplateRegistry(disabled_templates_dir)
    publication = registry.get_template("structure_prediction")
    assert publication is not None and publication.enabled is False
    monkeypatch.setattr(protein_project_capabilities, "get_template_registry", lambda: registry)

    ready_ids = {
        item["capability_id"]
        for item in protein_capability_inventory(project_ready_only=True)["capabilities"]
    }
    assert capability["capability_id"] not in ready_ids


def test_unavailable_capabilities_remain_catalogued_but_hidden_from_project_picker() -> None:
    complete = protein_capability_inventory()
    ready_ids = {
        item["capability_id"]
        for item in protein_capability_inventory(project_ready_only=True)["capabilities"]
    }
    hidden_ids = {
        "protein.de_novo.rfd3",
        "protein.de_novo.local_redesign",
        "protein.sequence_design.fampnn",
        "protein.sequence_design.proteinmpnn",
        "protein.simulation.gromacs_md",
        "protein.analysis.frustrampnn",
    }
    assert hidden_ids.isdisjoint(ready_ids)
    complete_by_id = {item["capability_id"]: item for item in complete["capabilities"]}
    assert hidden_ids <= set(complete_by_id)
    assert all(complete_by_id[item_id]["project_setup_state"] == "unavailable" for item_id in hidden_ids)


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
        assert set(document) == {
            "schema", "setup_context_id", "project_id", "global_experiment_id",
            "domain_experiment_id", "relationship_kind", "capability_id", "state",
            "validation_state", "generation", "setup_destination", "return_uri",
        }
        assert document["schema"] == "bms.project-workflow-setup.create-response.v1"
        assert document["state"] == "open"
        assert document["validation_state"] == "incomplete"
        assert document["setup_destination"].startswith("/submit?template=")
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
        assert prepared["state"] == "open"
        assert prepared["launch_context_id"]
        assert prepared["preparation_id"]
        preparation = await session.get(ExperimentWorkflowPreparation, prepared["preparation_id"])
        assert preparation is not None
        assert json.loads(preparation.scheduler_payload_json) == {
            "name": "Target — ESMFold2 structure prediction",
            "model_id": "esmfold2",
            "mode": "predict",
            "params": json.loads(preparation.normalized_request_json),
        }
        assert await session.scalar(select(func.count()).select_from(ExperimentWorkflowPreparation)) == 1
        assert await session.scalar(select(func.count()).select_from(ExperimentLaunchContext)) == 1
        assert await session.scalar(select(func.count()).select_from(ExperimentRunGroup)) == 0


@pytest.mark.asyncio
async def test_managed_preparation_issues_no_typed_launch_context(setup_store, monkeypatch) -> None:
    capability = workflow_setups.protein_capability_record("protein.structure_prediction.esmfold2")
    capability["launch_mode"] = "managed_materialization"
    monkeypatch.setattr(workflow_setups, "protein_capability_record", lambda _capability_id: copy.deepcopy(capability))
    async with setup_store() as session:
        project = await _project(session)
        setup = await workflow_setups.create_workflow_setup(
            session, project_id=project.id, relationship_kind="primary",
            global_experiment_id=None, experiment_name="Managed target", experiment_objective="Fold",
            domain_kind="protein_in_silico", capability_id="protein.structure_prediction.esmfold2",
            idempotency_key="managed-create-0001",
        )
        await workflow_setups.save_workflow_setup_draft(
            session, project_id=project.id, setup_context_id=setup["setup_context_id"],
            draft={"sequence": "MQIFVK"}, expected_generation=0,
            idempotency_key="managed-draft-0001",
        )
        prepared = await workflow_setups.prepare_workflow_setup_launch(
            session, project_id=project.id, setup_context_id=setup["setup_context_id"],
            expected_generation=1, idempotency_key="managed-prepare-0001",
        )
        assert prepared["preparation_id"]
        assert "launch_context_id" not in prepared
        assert await session.scalar(select(func.count()).select_from(ExperimentLaunchContext)) == 0


@pytest.mark.asyncio
async def test_setup_becomes_submitted_only_after_authoritative_job_binding(setup_store) -> None:
    async with setup_store() as session:
        project = await _project(session)
        setup = await workflow_setups.create_workflow_setup(
            session, project_id=project.id, relationship_kind="primary",
            global_experiment_id=None, experiment_name="Bind target", experiment_objective="Fold",
            domain_kind="protein_in_silico", capability_id="protein.structure_prediction.esmfold2",
            idempotency_key="primary-bind-0001",
        )
        await workflow_setups.save_workflow_setup_draft(
            session, project_id=project.id, setup_context_id=setup["setup_context_id"],
            draft={"sequence": "MQIFVK"}, expected_generation=0,
            idempotency_key="draft-bind-0001",
        )
        prepared = await workflow_setups.prepare_workflow_setup_launch(
            session, project_id=project.id, setup_context_id=setup["setup_context_id"],
            expected_generation=1, idempotency_key="prepare-bind-0001",
        )
        assert prepared["state"] == "open"
        await workflow_setups.mark_workflow_setup_submitted(
            session, project_id=project.id, workflow_id=prepared["diagnostics"]["workflow_id"]
        )
        submitted = await workflow_setups.get_workflow_setup(
            session, project_id=project.id, setup_context_id=setup["setup_context_id"]
        )
        assert submitted["state"] == "submitted"
        with pytest.raises(ValidationFailure, match="submitted"):
            await workflow_setups.delete_workflow_setup(
                session, project_id=project.id, setup_context_id=setup["setup_context_id"],
                idempotency_key="delete-submitted-0001",
            )


@pytest.mark.asyncio
async def test_resume_is_hydrated_but_create_stays_navigation_bounded(setup_store) -> None:
    async with setup_store() as session:
        project = await _project(session)
        created = await workflow_setups.create_workflow_setup(
            session, project_id=project.id, relationship_kind="primary",
            global_experiment_id=None, experiment_name="Hydrated target",
            experiment_objective="Preserve exact native values",
            domain_kind="protein_in_silico",
            capability_id="protein.structure_prediction.esmfold2",
            idempotency_key="primary-hydrate-0001",
        )
        assert "draft" not in created and "diagnostics" not in created
        resumed = await workflow_setups.get_workflow_setup(
            session, project_id=project.id, setup_context_id=created["setup_context_id"]
        )
        assert resumed["schema"] == "bms.project-workflow-setup.detail.v1"
        assert resumed["relationship_kind"] == "primary"
        assert resumed["state"] == "open"
        assert resumed["validation_state"] == "incomplete"
        assert resumed["project_label"] == "Protein Project"
        assert resumed["experiment_label"] == "Hydrated target"
        assert resumed["workflow_label"] == "ESMFold2 structure prediction"
        assert resumed["draft"]["pred_method"] == "esmfold2"
        assert "sequence" not in resumed["draft"]
        assert resumed["field_errors"] == {}
        assert resumed["diagnostics"]["workflow_id"]


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
        setup_row = await session.get(ExperimentWorkflowSetupContext, setup["setup_context_id"])
        assert setup_row is not None
        assert task == {
            "setup_context_id": setup["setup_context_id"],
            "global_experiment_id": setup["global_experiment_id"],
            "experiment_name": "Familiar experiment",
            "workflow_id": setup_row.workflow_id,
            "workflow_name": "Familiar experiment — Boltz-2 structure prediction",
            "relationship_kind": "primary",
            "workflow_label": "Boltz-2 structure prediction",
            "setup_state": "open",
            "validation_state": "incomplete",
            "latest_run_state": None,
            "result_count": 0,
            "reopen_route": (
                f"/submit?template=structure_prediction&pred_method=boltz"
                f"&setup_context_id={setup['setup_context_id'].replace(':', '%3A')}"
                f"&project_id={project.id}"
            ),
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
