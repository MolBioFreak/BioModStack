"""Preparation with a Dataset input reads the Domain revision the way Save wrote it."""
from __future__ import annotations

import pytest

import experiment_services as services
from experiment_services import (
    create_dataset,
    create_domain_experiment,
    create_global_experiment,
    create_project,
    create_workflow,
    prepare_workflow,
    save_dataset_revision,
    save_workflow_draft,
    save_workflow_revision,
)
from test_experiment_workspace_meta_layer import _workflow_payload
from test_project_manager_hierarchy import _experiment_payload, _project_payload, project_store  # noqa: F401


def _domain_payload() -> dict:
    return {
        "schema": "bms.domain-experiment.v2",
        "domain_kind": "ngs_molbio",
        "domain_contract_version": "2",
        "name": "Domain A",
        "objective": "Verify constructs",
        "status": "active",
        "tags": [],
        "source_receipt_ids": [],
        "dataset_revision_ids": [],
        "created_by": "operator",
        "change_summary": "created",
        "domain_payload": {
            "schema": "bms.ngs-molbio-experiment.v2", "experiment_mode": "pcr_validation",
            "scientific_objective": "Verify constructs", "planned_capability_ids": ["molbio.pcr"],
            "grouping_intent": [], "acceptance_criteria": [], "evidence_plan": [],
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("graph", ["current", "legacy"])
async def test_prepare_with_dataset_input_accepts_saved_domain_revision(project_store, monkeypatch, graph):
    _path, factory = project_store
    async with factory() as session:
        project = await create_project(session, {
            **_project_payload(), "schema": "bms.project.v2", "project_scope": "global", "status": "active",
        })
        experiment = await create_global_experiment(
            session, project.id, {**_experiment_payload(), "status": "active"},
        )
        with monkeypatch.context() as patcher:
            if graph == "legacy":
                # Domain revisions saved before the parent Global binding joined the
                # dependency graph keep their digest; the migration only added the edge.
                canonical_json = services.canonical_json
                patcher.setattr(services, "canonical_json", lambda value: canonical_json(
                    {**value, "references": [row for row in value["references"]
                                             if row["role"] != "parent_global_revision"]}
                    if isinstance(value, dict) and set(value) == {"nodes", "edges", "references"}
                    else value
                ))
            domain = await create_domain_experiment(session, project.id, experiment.id, _domain_payload())
        workflow = await create_workflow(
            session, project.id, "Generic workflow", "generic_test", experiment_id=domain.id,
        )
        dataset = await create_dataset(
            session, project.id, "Constructs", "ngs_molbio.molecular_construct_cohort.v1",
            experiment_id=domain.id,
        )
        await save_workflow_draft(session, workflow.id, _workflow_payload(), expected_generation=0)
        workflow_revision = await save_workflow_revision(session, workflow.id, expected_head_generation=0)
        dataset_revision = await save_dataset_revision(
            session, dataset.id,
            {"schema": "bms.dataset-revision.v1", "change_summary": "created", "members": []},
            expected_head_generation=0,
        )
        await session.refresh(dataset)
        dataset.lifecycle_state = "active"  # as the Project Dataset owner does after Save
        await session.flush()

        preparation = await prepare_workflow(
            session, workflow_revision.id,
            {"input_dataset_revision_ids": [dataset_revision.resource_id]},
        )

        assert preparation.validation_status == "valid"
