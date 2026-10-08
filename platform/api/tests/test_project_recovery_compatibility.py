"""Recovery preserves creation/criteria/restore policy while simplifying bookkeeping."""
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import TypeAdapter

from experiment_models import ExperimentRevision
from experiment_services import archive_aggregate, create_project, restore_aggregate
from routers import projects
from test_project_manager_hierarchy import _app, _experiment_payload, _project_payload, project_store


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, "v1", "v2"])
async def test_http_creation_and_organizational_lifecycle_keep_legacy_contracts(project_store, version):
    _, factory = project_store
    app = _app(factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        payload = {"name": "Compatible Project"}
        if version:
            payload["schema"] = f"bms.project.{version}"
        if version == "v2":
            payload["project_scope"] = "global"
        response = await client.post("/api/projects", json=payload)
        assert response.status_code == 201, response.text
        project = response.json()
        project_url = f"/api/projects/{project['id']}"
        global_payload = {"name": "Compatible Experiment"}
        if version:
            global_payload["schema"] = f"bms.global-experiment.{version}"
        response = await client.post(project_url + "/experiments", json=global_payload)
        assert response.status_code == 201, response.text
        experiment = response.json()
        for url, head in [(project_url + f"/experiments/{experiment['id']}", experiment), (project_url, project)]:
            edited = await client.patch(url, json={"expected_head_generation": head["head_generation"], "name": "Renamed"})
            assert edited.status_code == 200, edited.text
            archived = await client.post(url + "/archive", json={"expected_head_generation": edited.json()["head_generation"]})
            assert archived.status_code == 200, archived.text
            restored = await client.post(url + "/restore", json={"expected_head_generation": archived.json()["head_generation"]})
            assert restored.status_code == 200, restored.text
            assert restored.json()["lifecycle_state"] == "draft"
            readback = await client.get(url)
            assert readback.status_code == 200, readback.text
            assert readback.json()["name"] == "Renamed"
            stale = await client.post(url + "/archive", json={"expected_head_generation": head["head_generation"]})
            assert stale.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["draft", "planned", "active", "analysis", "review", "completed", "blocked"])
@pytest.mark.parametrize("criteria", [[""], ["  "], ["", "review"]])
async def test_http_success_criteria_preserve_current_string_domain(project_store, status, criteria):
    _, factory = project_store
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(factory)), base_url="http://test") as client:
        project = await client.post("/api/projects", json={"name": "Criteria compatibility"})
        assert project.status_code == 201, project.text
        payload = _experiment_payload()
        payload.update(status=status, success_criteria=criteria, review_summary="Reviewed", conclusion="Recorded")
        response = await client.post(f"/api/projects/{project.json()['id']}/experiments", json=payload)
        assert response.status_code == 201, response.text
        url = f"/api/projects/{project.json()['id']}/experiments/{response.json()['id']}/revisions"
        revisions = await client.get(url)
        assert revisions.status_code == 200
        assert revisions.json()["items"][0]["payload"]["success_criteria"] == criteria


@pytest.mark.asyncio
@pytest.mark.parametrize("predecessor", ["missing", "archived", "foreign_subject", "wrong_digest"])
async def test_restore_keeps_fallback_and_archived_payload_without_new_evidence_gate(project_store, monkeypatch, predecessor):
    _, factory = project_store
    async with factory() as session:
        head = await create_project(session, _project_payload())
        await archive_aggregate(session, head.id, expected_head_generation=head.head_generation)
        archived = await session.get(ExperimentRevision, head.current_revision_id)
        archived_payload = json.loads(archived.canonical_payload)
        prior_id = archived.parent_revision_id
        real_get = session.get
        # Model uncertain predecessor observation without tampering with immutable storage.
        async def observed_get(model, identity, *args, **kwargs):
            if model is ExperimentRevision and identity == prior_id:
                if predecessor == "missing":
                    return None
                return SimpleNamespace(subject_id="other" if predecessor == "foreign_subject" else head.id,
                    payload_sha256="wrong" if predecessor == "wrong_digest" else "unverified",
                    canonical_payload=json.dumps({"schema": "different historical schema", "name": "Do not copy predecessor", "status": "archived" if predecessor == "archived" else "draft"}))
            return await real_get(model, identity, *args, **kwargs)
        monkeypatch.setattr(session, "get", observed_get)
        restored = await restore_aggregate(session, head.id, expected_head_generation=head.head_generation)
        await session.commit()
        actual = json.loads((await real_get(ExperimentRevision, restored.current_revision_id)).canonical_payload)
        assert actual == {**archived_payload, "status": "draft", "change_summary": "restored"}


def test_domain_v2_creation_dto_is_still_accepted():
    payload = {"schema": "bms.domain-experiment.v2", "domain_kind": "ngs_molbio", "domain_contract_version": "2",
        "name": "Legacy authoring", "objective": "Observe", "status": "draft", "tags": [],
        "source_receipt_ids": [], "dataset_revision_ids": [], "change_summary": "created",
        "domain_payload": {"schema": "bms.ngs-molbio-experiment.v2", "experiment_mode": "analysis",
            "scientific_objective": "Observe", "planned_capability_ids": [], "grouping_intent": [],
            "acceptance_criteria": [], "evidence_plan": []}}
    assert TypeAdapter(projects.DomainExperimentCreateRequest).validate_python(payload).schema_ == payload["schema"]
