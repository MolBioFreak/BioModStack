"""Closed execution policy through real admission/replay and scheduler scans."""
import asyncio
from copy import deepcopy
import pytest
from fastapi import BackgroundTasks, Request, Response
from pydantic import ValidationError
from database import Job, ExecutionTarget
from schemas import JobCreate, ExecutionPolicy
from routers import jobs
from services.remote_execution import executor as ex
from services.gpu_orchestrator import GPUOrchestrator
from test_core_protein_scientific_admission import admission, request
from test_remote_lifecycle_gaps import store
from test_remote_manual_result_pull import ready
from test_remote_return_policy_recovery import policy
from test_remote_diagnostics_backend import terminal

@pytest.mark.parametrize("bad", ["auto", True, None, 1, {}, "AUTOMATIC"])
def test_closed_policy(bad):
    with pytest.raises(ValidationError):
        request(execution_policy={"remote_result_policy": bad})

def test_unknown_and_scientific_lane_rejected():
    with pytest.raises(ValidationError):
        ExecutionPolicy(remote_result_policy="automatic", diagnostics="automatic")
    with pytest.raises(ValidationError):
        JobCreate(name="test", model_id="boltz2", mode="predict", params={"remote_result_policy": "automatic"})
    assert request().execution_policy.remote_result_policy == "manual"
    schema = JobCreate.model_json_schema()["$defs"]["ExecutionPolicy"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["remote_result_policy"]["enum"] == ["manual", "automatic"]

@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["manual", "automatic"])
async def test_admit_readback_clone_and_retry_persist_policy(admission, value):
    created = await jobs._create_job(request(execution_policy={"remote_result_policy": value}), BackgroundTasks(), admission)
    assert created.execution_policy.remote_result_policy == value
    assert "remote_result_policy" not in created.params
    admission.expire_all()
    source = await admission.get(Job, created.id)
    assert source.params["remote_result_policy"] == value
    assert "remote_result_policy" not in source.provenance["core_protein_requested_params"]
    # The clone round-trip uses the same public requested scientific params and typed policy.
    clone_request = JobCreate(
        name="saved-policy-clone", model_id=created.model_id, mode=created.mode,
        params=deepcopy(source.provenance["core_protein_requested_params"]),
        execution_policy=created.execution_policy,
    )
    cloned = await jobs._create_job(clone_request, BackgroundTasks(), admission)
    assert cloned.execution_policy.remote_result_policy == value
    source.status = source.queue_status = "failed"
    await admission.commit()
    replay = await jobs.resubmit_job(source.id, Request({"type":"http", "headers":[]}), Response(), admission)
    admission.expire_all()
    retried = await admission.get(Job, replay["new_job_id"])
    assert retried.params["remote_result_policy"] == value
    assert retried.params["sequence"] == "ACDEFGHIK"
    assert "remote_result_policy" not in retried.provenance["core_protein_requested_params"]
    resumed = await jobs.resume_job(created.id, Request({"type":"http", "headers":[]}), Response(), request=None, session=admission)
    admission.expire_all()
    assert (await admission.get(Job, resumed["new_job_id"])).params["remote_result_policy"] == value

@pytest.mark.asyncio
async def test_http_api_schema_admission_and_readback(admission):
    import httpx
    from fastapi import FastAPI
    from schemas import JobResponse
    app = FastAPI()
    app.include_router(jobs.router, prefix="/api/jobs")
    async def isolated_session():
        yield admission
    app.dependency_overrides[jobs.get_session] = isolated_session
    app.dependency_overrides[jobs.get_experiment_session] = isolated_session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        payload = request(execution_policy={"remote_result_policy":"automatic"}).model_dump()
        response = await client.post("/api/jobs", json=payload)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["execution_policy"] == {"remote_result_policy":"automatic"}
        assert "remote_result_policy" not in body["params"]
        readback = await client.get("/api/jobs/" + body["id"])
        assert readback.status_code == 200, readback.text
        assert readback.json()["execution_policy"] == body["execution_policy"]
        source = await admission.get(Job, body["id"])
        assert JobResponse.model_validate(source).execution_policy.remote_result_policy == "automatic"
        assert source.params["remote_result_policy"] == "automatic"
        payload["execution_policy"]["diagnostics"] = "automatic"
        assert (await client.post("/api/jobs", json=payload)).status_code == 422
        payload.pop("execution_policy")
        payload["remote_result_policy"] = "automatic"
        assert (await client.post("/api/jobs", json=payload)).status_code == 422

@pytest.mark.parametrize("value", ["manual", "automatic"])
def test_execution_metadata_never_reaches_nextflow(tmp_path, value):
    from services.nextflow import build_nextflow_command
    params = {"sequence":"ACDEFGHIK", "use_msa":False, "remote_result_policy":value}
    before = deepcopy(params)
    command = build_nextflow_command("boltz2", "predict", params, str(tmp_path), job_id="test")
    assert "--remote_result_policy" not in command
    assert params == before

@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["manual", "automatic"])
async def test_scheduler_restart_recovers_waiting_without_browser(store, monkeypatch, value):
    await ready(store)
    await policy(store, value)
    seen = []
    async def unavailable(*_):
        seen.append("proof")
        raise ex.RemoteExecutionError("offline")
    async def forbidden(*_, **__):
        pytest.fail("scheduler recovery cannot submit science or observe worker for manual waits")
    monkeypatch.setattr(ex, "_prove_pull_endpoint", unavailable)
    monkeypatch.setattr(ex, "remote_status", forbidden)
    monkeypatch.setattr(ex, "run_remote", forbidden)
    poller = GPUOrchestrator.__new__(GPUOrchestrator)
    poller.db_session_factory = store
    await poller.check_job_completions()
    await asyncio.gather(*list(ex._result_return_tasks))
    async with store() as session:
        current = await session.get(Job, "job")
        assert current.remote_state == ("result_pull_failed" if value == "automatic" else "results_available")
    assert seen == (["proof"] if value == "automatic" else [])
    await poller.check_job_completions()
    assert seen == (["proof"] if value == "automatic" else [])

@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["failed", "lost", "cancelled"])
async def test_scheduler_repairs_abandoned_diagnostics_locally(store, tmp_path, monkeypatch, state):
    await terminal(store, tmp_path)
    await policy(store, "automatic")
    async with store() as session:
        job = await session.get(Job, "job")
        job.status = job.queue_status = "cancelled" if state == "cancelled" else "failed"
        job.remote_state = state
        job.provenance = dict(job.provenance, remote_diagnostics={"state":"returning", **ex._pull_identity(job)})
        before = (job.status, job.queue_status, job.remote_state, job.completed_at, job.error_message, job.output_dir)
        await session.commit()
    async def forbidden(*_, **__):
        pytest.fail("diagnostic recovery must not transfer or contact worker")
    monkeypatch.setattr(ex, "remote_status", forbidden)
    monkeypatch.setattr(ex, "collect_remote_results", forbidden)
    monkeypatch.setattr(ex, "_prove_pull_endpoint", forbidden)
    poller = GPUOrchestrator.__new__(GPUOrchestrator)
    poller.db_session_factory = store
    await poller.check_job_completions()
    await poller.check_job_completions()
    async with store() as session:
        job = await session.get(Job, "job")
        assert job.provenance["remote_diagnostics"]["state"] == "failed"
        assert (job.status, job.queue_status, job.remote_state, job.completed_at, job.error_message, job.output_dir) == before
        assert (await session.get(ExecutionTarget, "target")).leased_job_id == "successor"
