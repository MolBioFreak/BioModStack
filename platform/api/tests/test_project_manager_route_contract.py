from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast
import pytest
from fastapi import Request
from fastapi.routing import APIRoute

import experiment_services
from experiment_services import workflow_plan_capability_contract
from routers import project_manager
from services.global_experiments import launch_contexts
from routers.projects import router


def test_domain_activity_uses_the_frozen_singular_route() -> None:
    paths = {route.path for route in router.routes if isinstance(route, APIRoute)}
    assert "/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/activity" in paths
    assert "/api/projects/{project_id}/experiments/{experiment_id}/domains/{domain_id}/activities" not in paths


def test_protein_domain_exposes_each_accepted_structure_predictor() -> None:
    revision = SimpleNamespace(canonical_payload=json.dumps({
        "domain_kind": "protein_in_silico",
        "domain_payload": {"experiment_mode": "prediction"},
    }))
    experiment_mode, inventory = project_manager._domain_capability_authority(cast(Any, revision))

    assert experiment_mode == "prediction"
    accepted = [
        item for item in inventory["capabilities"]
        if project_manager._capability_is_allowed_for_domain(item, experiment_mode)
    ]
    assert [item["capability_id"] for item in accepted] == [
        "protein.structure_prediction.boltz2",
        "protein.structure_prediction.esmfold2",
        "protein.structure_prediction.protenix_v2",
    ]
    assert [workflow_plan_capability_contract(item["capability_id"])["allowed_model_modes"] for item in accepted] == [
        [{"model_id": "boltz2", "mode": "predict"}],
        [{"model_id": "esmfold2", "mode": "predict"}],
        [{"model_id": "protenix", "mode": "predict"}],
    ]


def test_protein_plan_draft_binds_source_receipt_and_native_request() -> None:
    builder = cast(Any, getattr(experiment_services, "initial_workflow_plan_payload", None))
    assert callable(builder), "server-owned Plan draft builder is missing"
    contract = workflow_plan_capability_contract("protein.structure_prediction.esmfold2")
    draft = cast(dict[str, Any], builder(
        plan_name="1UBQ ESMFold2 governed run",
        capability_contract=contract,
        domain_payload={
            "domain_kind": "protein_in_silico",
            "domain_payload": {"targets": [{"source_receipt_ids": ["receipt-1"]}]},
        },
    ))

    assert draft["source_receipt_ids"] == ["receipt-1"]
    assert "sequence" not in draft["parameters"]
    assert draft["scheduler"] == {
        "name": "1UBQ ESMFold2 governed run",
        "model_id": "esmfold2",
        "mode": "predict",
        "params": {**draft["parameters"], "workflow_adapter": "bms.core-job.esmfold2.adapter.v1"},
    }


def test_bound_native_job_injects_only_server_owned_workflow_adapter() -> None:
    normalizer = cast(Any, getattr(launch_contexts, "normalize_bound_job_params", None))
    assert callable(normalizer), "bound Job parameter normalizer is missing"
    expected = {"sequence": "MQIFVK", "workflow_adapter": "bms.core-job.esmfold2.adapter.v1"}
    assert normalizer(
        supplied_params={"sequence": "MQIFVK"},
        expected_params=expected,
    ) == expected
    with pytest.raises(launch_contexts.LaunchContextError, match="workflow adapter"):
        normalizer(
            supplied_params={"sequence": "MQIFVK", "workflow_adapter": "wrong"},
            expected_params=expected,
        )


@pytest.mark.asyncio
async def test_attach_route_forwards_expected_project_generation(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_attach(*_args, **kwargs):
        captured.update(kwargs)
        return {"source_receipt_id": "receipt-1"}

    async def allow_owner(*_args, **_kwargs):
        return "operator"

    class FakeSession:
        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

    monkeypatch.setattr(project_manager, "attach_verified_entity", fake_attach)
    monkeypatch.setattr(project_manager, "_require_mutation_owner", allow_owner)
    payload = project_manager.AttachRequest(
        adapter_id="adapter-1",
        entity_id="entity-1",
        operation="attach_reference",
        role="references",
        note=None,
        expected_head_generation=7,
    )
    response = await project_manager.attach_domain_entity(
        project_id="project-1",
        experiment_id="global-1",
        domain_id="domain-1",
        payload=payload,
        request=Request({"type": "http", "method": "POST", "path": "/", "headers": []}),
        experiment_session=FakeSession(),
        core_session=FakeSession(),
    )

    assert response == {"source_receipt_id": "receipt-1"}
    assert captured["expected_head_generation"] == 7
