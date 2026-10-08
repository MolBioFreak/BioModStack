from __future__ import annotations

import pytest
from pydantic import ValidationError
from fastapi.routing import APIRoute
from routers import project_manager

from routers.project_manager import WorkflowSetupCreateRequest, WorkflowSetupPrepareRequest


def test_create_request_uses_exact_nested_experiment_contract() -> None:
    paths = {route.path for route in project_manager.router.routes if isinstance(route, APIRoute)}
    assert {
        "/api/projects/{project_id}/workflow-setups",
        "/api/projects/{project_id}/workflow-setups/{setup_context_id}",
        "/api/projects/{project_id}/workflow-setups/{setup_context_id}/draft",
        "/api/projects/{project_id}/workflow-setups/{setup_context_id}/prepare-launch",
    } <= paths
    payload = WorkflowSetupCreateRequest.model_validate({
        "schema": "bms.project-workflow-setup.create.v1",
        "relationship_kind": "primary",
        "global_experiment_id": None,
        "experiment": {"name": "Fold target", "objective": "Predict structure"},
        "domain_kind": "protein_in_silico",
        "capability_id": "protein.structure_prediction.esmfold2",
    })
    assert payload.schema_id == "bms.project-workflow-setup.create.v1"
    assert payload.experiment is not None
    assert payload.experiment.name == "Fold target"
    with pytest.raises(ValidationError) as rejected:
        WorkflowSetupCreateRequest.model_validate({
            "schema": "bms.project-workflow-setup.create.v1",
            "relationship_kind": "primary",
            "global_experiment_id": None,
            "experiment_name": "legacy flat field",
            "experiment_objective": "must fail",
            "domain_kind": "protein_in_silico",
            "capability_id": "protein.structure_prediction.esmfold2",
        })

    errors = {(tuple(error["loc"]), error["type"]) for error in rejected.value.errors()}
    assert (("experiment_name",), "extra_forbidden") in errors
    assert (("experiment_objective",), "extra_forbidden") in errors


def test_prepare_requires_optimistic_generation() -> None:
    assert WorkflowSetupPrepareRequest.model_validate({"expected_generation": 4}).expected_generation == 4
    with pytest.raises(ValidationError):
        WorkflowSetupPrepareRequest.model_validate({})