"""Native Project launch through the real two-store owner and public Clone read."""
import asyncio
import copy
import json
import os
from pathlib import Path

import pytest

from test_md_typed_launch import project_context_preview_store, _AcceptingRegistry


@pytest.mark.parametrize("project_context_preview_store", ["prepared"], indirect=True)
@pytest.mark.parametrize("change", ["effective_request", "input_identity", "placement"])
def test_native_project_adapter_rejects_divergent_handoff(project_context_preview_store, monkeypatch, change):
    from database import Job
    from experiment_models import ExperimentLaunchContext
    from routers import jobs
    from sqlalchemy import select, func

    store = project_context_preview_store
    monkeypatch.setenv("BMS_FEATURE_MOLECULAR_DYNAMICS", "1")
    intent = store["native_intent"]
    preview = store["client"].post("/api/molecular-dynamics/launch-preview", json={
        "schema_version": "bms.md.launch-preview-request.v1", "intent": intent,
    })
    assert preview.status_code == 200, preview.text
    create_job = jobs.create_job
    async def divergent(job_data, *args, **kwargs):
        if change == "effective_request":
            job_data.params["md_job_spec"]["stages"][0]["mdp"]["nsteps"] = 7
        elif change == "input_identity":
            job_data.params["md_source_provenance"]["native_input_identity"] = {}
        else:
            job_data.execution_target_id = "vast:changed"
        return await create_job(job_data, *args, **kwargs)
    monkeypatch.setattr(jobs, "create_job", divergent)
    response = store["client"].post("/api/molecular-dynamics/launch", json={
        "schema_version": "bms.md.launch-request.v1", "intent": intent,
        "preview_digest": preview.json()["preview_digest"],
    }, headers={"x-bms-launch-context-id": intent["launch_context_id"]})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "launch_context_typed_md_adapter_invalid"
    async def readback():
        async with store["core_sessions"]() as core, store["experiment_sessions"]() as experiments:
            assert await core.scalar(select(func.count(Job.id))) == 1  # source only
            context = await experiments.get(ExperimentLaunchContext, intent["launch_context_id"])
            assert context.state == "reserved" and context.canonical_job_id is None
    asyncio.run(readback())


@pytest.mark.parametrize("project_context_preview_store", ["prepared", "compiled", "guided"], indirect=True)
def test_native_project_route_create_readback_and_clone(project_context_preview_store, monkeypatch):
    from database import Job
    from experiment_models import ExperimentLaunchContext, ExperimentRunAttempt
    from routers import jobs
    from services import gpu_orchestrator
    from services.global_experiments.launch_contexts import validate_bound_job
    from scripts.bms_md.contract import verify_input_snapshots

    store = project_context_preview_store
    intent = store["native_intent"]
    client = store["client"]
    context_id = store["ids"]["context"]
    monkeypatch.setenv("BMS_FEATURE_MOLECULAR_DYNAMICS", "1")
    monkeypatch.setattr(jobs, "get_registry", lambda: _AcceptingRegistry())
    monkeypatch.setattr(jobs, "get_results_dir", lambda: store["results_root"])
    monkeypatch.setattr(gpu_orchestrator, "estimate_vram", lambda *_a, **_kw: 0)
    preview_body = {"schema_version": "bms.md.launch-preview-request.v1", "intent": intent}
    changed = copy.deepcopy(preview_body)
    changed["intent"]["replicas"] += 1
    assert client.post("/api/molecular-dynamics/launch-preview", json=changed).status_code == 409
    preview = client.post("/api/molecular-dynamics/launch-preview", json=preview_body)
    assert preview.status_code == 200, preview.text
    body = {"schema_version": "bms.md.launch-request.v1", "intent": intent,
            "preview_digest": preview.json()["preview_digest"]}
    assert client.post("/api/molecular-dynamics/launch", json=body).status_code == 409
    response = client.post("/api/molecular-dynamics/launch", json=body,
                           headers={"x-bms-launch-context-id": context_id})
    assert response.status_code == 201, response.text
    job_id = response.json()["id"]

    async def readback():
        async with store["core_sessions"]() as core, store["experiment_sessions"]() as experiments:
            job = await core.get(Job, job_id)
            context = await experiments.get(ExperimentLaunchContext, context_id)
            attempt = await experiments.get(ExperimentRunAttempt, store["ids"]["attempt"])
            await validate_bound_job(experiments, context, job)
            assert context.state == "consumed" and context.canonical_job_id == job.id
            assert attempt.state == "dispatched"
            assert job.parent_job_id is None
            assert job.params["intent"] == intent
            assert job.params["md_job_spec"]["stages"] == preview.json()["effective_request"]["stages"]
            assert job.params["md_job_spec"].get("windows") == preview.json()["effective_request"].get("windows")
            verify_input_snapshots(job.params["md_job_spec"])
            if intent["input"]["kind"] == "guided":
                assert job.params["source_design_id"] == store["expected_design_id"]
                assert job.params["md_job_spec"]["preparation"]["neutralize"] is False
    asyncio.run(readback())
    detail = client.get(f"/api/jobs/{job_id}")
    assert detail.status_code == 200, detail.text
    wire = detail.json()
    assert wire["params"]["intent"] == intent
    assert wire["execution_target_id"] is None
    assert wire["execution_policy"] == intent["execution_policy"]
    # Optional producer evidence is consumed verbatim by the mounted shell test.
    if directory := os.environ.get("BMS_MD_CLONE_WIRES"):
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{intent['input']['kind']}.json").write_text(json.dumps(wire))
        if intent["input"]["kind"] == "guided":
            catalog = client.get("/api/molecular-dynamics/chemistry-profiles")
            inspection = client.post("/api/molecular-dynamics/starting-structures/inspect", json={
                "source_ref": intent["input"]["source_ref"], "chemistry_profile_id": intent["input"]["chemistry_profile_id"],
            })
            assert catalog.status_code == inspection.status_code == 200
            (path / "guided-source.json").write_text(json.dumps({"catalog": catalog.json(), "inspection": inspection.json()}))
