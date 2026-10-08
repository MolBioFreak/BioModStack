"""Selective recovery of archived Project lifecycle coverage; no new admission policy."""
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from experiment_models import ExperimentRevision
from experiment_services import create_domain_experiment, create_global_experiment
from services.global_experiments import workflow_setups
from routers import projects
from test_project_workflow_setups import setup_store, _project
from test_project_manager_hierarchy import _domain_payload


async def hierarchy(session, kind="protein_in_silico"):
    project = await _project(session)
    experiment = await create_global_experiment(session, project.id, workflow_setups._global_payload("Explore", "Unknown outcome"))
    domain = await create_domain_experiment(session, project.id, experiment.id, _domain_payload(kind))
    return project, experiment, domain


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
