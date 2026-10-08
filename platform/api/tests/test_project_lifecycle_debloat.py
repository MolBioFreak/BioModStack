from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from experiment_models import ExperimentAggregateHead, ExperimentIdempotencyClaim, ExperimentRevision
from experiment_services import create_domain_experiment, create_global_experiment, create_workflow, archive_aggregate, restore_aggregate, ValidationFailure
from services.global_experiments import launch_contexts, workflow_setups
from routers import project_manager, projects
from test_project_workflow_setups import setup_store, _project
from test_project_manager_hierarchy import _domain_payload


async def hierarchy(session, kind="protein_in_silico"):
    project = await _project(session)
    experiment = await create_global_experiment(session, project.id, workflow_setups._global_payload("Explore", "Unknown outcome", "test-owner"))
    domain = await create_domain_experiment(session, project.id, experiment.id, _domain_payload(kind))
    return project, experiment, domain


@pytest.mark.asyncio
async def test_navigation_uses_exact_ownership_without_dashboard(setup_store, monkeypatch):
    from services.global_experiments import read_models
    monkeypatch.setattr(read_models, "build_project_manager_read_model", AsyncMock(side_effect=AssertionError("dashboard called")))
    async with setup_store() as session:
        project, experiment, domain = await hierarchy(session)
        workflow = await create_workflow(session, project.id, "Plan", "test", experiment_id=domain.id)
        other = await create_global_experiment(session, project.id, workflow_setups._global_payload("Other", "Other", "test-owner"))
        outsider = await create_domain_experiment(session, project.id, other.id, _domain_payload())
        for key in [f"project:{project.id}", f"global_experiment:{experiment.id}", f"domain_experiment:{domain.id}", f"workflow:{workflow.id}", f"virtual_folder:{domain.id}:results"]:
            await launch_contexts._validate_return_selection(session, project_id=project.id, global_experiment_id=experiment.id, selected_node_key=key)
        for key in [f"domain_experiment:{outsider.id}", f"global_experiment:{other.id}", f"virtual_folder:{domain.id}:bogus", "workflow:missing"]:
            with pytest.raises(launch_contexts.LaunchContextError):
                await launch_contexts._validate_return_selection(session, project_id=project.id, global_experiment_id=experiment.id, selected_node_key=key)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["protein_in_silico", "ngs_molbio"])
async def test_legacy_metadata_archive_restore_preserves_science(setup_store, monkeypatch, kind):
    monkeypatch.setattr(projects, "_require_mutation_owner", AsyncMock(return_value="test-owner"))
    monkeypatch.setattr(projects, "_issue_domain_revision_reverification", AsyncMock(side_effect=AssertionError("legacy binding upgrade")))
    async with setup_store() as session:
        project, experiment, domain = await hierarchy(session, kind)
        original = await session.get(ExperimentRevision, domain.current_revision_id)
        original_bytes = original.canonical_payload
        request = Request({"type": "http", "headers": []})
        result = await projects.patch_domain_experiment(project.id, experiment.id, domain.id, projects.DomainExperimentPatchRequest(expected_head_generation=domain.head_generation, name="Renamed"), request, session, session)
        assert json.loads((await session.get(ExperimentRevision, domain.current_revision_id)).canonical_payload)["domain_payload"] == json.loads(original_bytes)["domain_payload"]
        with pytest.raises(HTTPException) as caught:
            await projects.patch_domain_experiment(project.id, experiment.id, domain.id, projects.DomainExperimentPatchRequest(expected_head_generation=domain.head_generation, objective="Scientific change"), request, session, session)
        assert caught.value.status_code == 409
        await projects.archive_domain_experiment(project.id, experiment.id, domain.id, projects.LifecycleRequest(expected_head_generation=domain.head_generation), request, session, session)
        assert domain.lifecycle_state == "archived"
        await projects.restore_domain_experiment(project.id, experiment.id, domain.id, projects.LifecycleRequest(expected_head_generation=domain.head_generation), request, session, session)
        assert domain.lifecycle_state == "draft"
        assert original.canonical_payload == original_bytes
        restored = json.loads((await session.get(ExperimentRevision, domain.current_revision_id)).canonical_payload)
        assert restored["schema"] == "bms.domain-experiment.v1"
        assert restored["domain_payload"] == json.loads(original_bytes)["domain_payload"]


@pytest.mark.asyncio
async def test_handoff_replays_original_claim_before_mutable_authority(setup_store, monkeypatch):
    owner = AsyncMock(return_value="test-owner")
    monkeypatch.setattr(project_manager, "_require_mutation_owner", owner)
    monkeypatch.setattr(project_manager, "_domain_hierarchy", AsyncMock(side_effect=AssertionError("mutable hierarchy consulted")))
    async with setup_store() as session:
        project, experiment, domain = await hierarchy(session)
        preparation_id = "preparation:historical"
        uri = f"/projects/{project.id}?focus={experiment.id}&selected=domain_experiment%3A{domain.id}"
        normalized = {"project_id": project.id, "experiment_id": experiment.id, "domain_id": domain.id, "preparation_id": preparation_id, "return_uri": uri}
        digest = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        response = {"schema": "bms.launch-context.v2", "launch_context_id": project.id, "state": "issued", "run_attempt_id": None}
        session.add(ExperimentIdempotencyClaim(scope=f"handoff:{hashlib.sha256(preparation_id.encode()).hexdigest()}", idempotency_key="recover", request_sha256=digest, result_resource_id=project.id, response_json=json.dumps(response)))
        await session.commit()
        request = Request({"type": "http", "headers": [(b"idempotency-key", b"recover")]})
        result = await project_manager.issue_prepared_handoff(project.id, experiment.id, domain.id, preparation_id, project_manager.PreparedHandoffRequest(return_uri=uri), request, session, session, session)
        assert result == response
        owner.assert_awaited_once()
        with pytest.raises(HTTPException) as caught:
            await project_manager.issue_prepared_handoff(project.id, experiment.id, domain.id, preparation_id, project_manager.PreparedHandoffRequest(return_uri=uri + "changed"), request, session, session, session)
        assert caught.value.status_code == 409
        await session.refresh(project)
        await session.refresh(experiment)
        await session.refresh(domain)
        owner.side_effect = HTTPException(status_code=403)
        with pytest.raises(HTTPException) as denied:
            await project_manager.issue_prepared_handoff(project.id, experiment.id, domain.id, preparation_id, project_manager.PreparedHandoffRequest(return_uri=uri), request, session, session, session)
        assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_current_exploratory_setup_has_real_contract_and_reversible_lifecycle(setup_store):
    async with setup_store() as session:
        project = await _project(session)
        setup = await workflow_setups.create_workflow_setup(session, project_id=project.id, actor_id="test-owner", relationship_kind="primary", global_experiment_id=None, experiment_name="Explore", experiment_objective="Not known yet", domain_kind="protein_in_silico", capability_id="protein.structure_prediction.esmfold2", idempotency_key="exploratory")
        domain = await session.get(ExperimentAggregateHead, setup["domain_experiment_id"])
        payload = json.loads((await session.get(ExperimentRevision, domain.current_revision_id)).canonical_payload)
        assert payload["schema"] == "bms.domain-experiment.v4"
        assert payload["domain_payload"]["targets"] == []
        assert payload["domain_payload"]["acceptance_criteria"] == []
        assert payload["domain_payload"]["evidence_plan"] == []
        await archive_aggregate(session, domain.id, expected_head_generation=domain.head_generation)
        await restore_aggregate(session, domain.id, expected_head_generation=domain.head_generation)
        assert domain.lifecycle_state == "active"
        from services.ngs_molbio_capabilities import validate_domain_experiment, NgsMolBioCapabilityError
        for status in ["planned", "active"]:
            exploratory = copy.deepcopy(payload)
            exploratory["status"] = status
            exploratory["domain_payload"]["planned_capability_ids"] = []
            exploratory = projects._complete_domain_v4_attestations(exploratory)
            validate_domain_experiment(exploratory)
        malformed = copy.deepcopy(payload)
        malformed["domain_payload"]["acceptance_criteria"] = [{"bogus": True}]
        malformed = projects._complete_domain_v4_attestations(malformed)
        with pytest.raises(NgsMolBioCapabilityError):
            validate_domain_experiment(malformed)


@pytest.mark.parametrize("status", ["planned", "active"])
def test_ngs_exploratory_current_contract_keeps_supplied_data_validation(status):
    from services.ngs_molbio_capabilities import validate_domain_experiment, NgsMolBioCapabilityError
    payload = workflow_setups._domain_payload("NGS exploration", "Unknown outcome", "analysis", "test-owner")
    payload["domain_kind"] = "ngs_molbio"
    payload["status"] = status
    payload["domain_payload"] = {
        "schema": "bms.ngs-molbio-experiment.v2", "experiment_mode": "analysis",
        "scientific_objective": "Unknown outcome", "planned_capability_ids": [],
        "grouping_intent": [], "acceptance_criteria": [], "evidence_plan": [],
    }
    validate_domain_experiment(projects._complete_domain_v4_attestations(payload))
    payload["domain_payload"]["evidence_plan"] = [{"fake": True}]
    with pytest.raises(NgsMolBioCapabilityError):
        validate_domain_experiment(projects._complete_domain_v4_attestations(payload))


def test_new_authoring_models_reject_historical_schema_labels():
    from pydantic import ValidationError
    for model, label in [(projects.ProjectCreateRequest, "bms.project.v1"), (projects.GlobalExperimentCreateRequest, "bms.global-experiment.v1"), (projects.DomainExperimentCreateRequest, "bms.domain-experiment.v2")]:
        with pytest.raises(ValidationError):
            model.model_validate({"schema": label, "name": "Historical creation"})
