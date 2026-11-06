"""Scratch request→prepared child→receipt checks at the shared submit boundary."""
import hashlib
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from database import Job, Design, get_session
from experiment_database import get_experiment_session
from routers import frustrampnn as routes
from routers import jobs as shared_jobs
from services.frustrampnn import jobs
from test_frustrampnn_child_jobs import child_db, _pdb


@pytest.mark.asyncio
@pytest.mark.parametrize("target_fields", [{}, {"execution_target_id": None}, {"execution_target_id": "selected-worker"}])
async def test_upload_and_reanalysis_forward_destination_independently(child_db, monkeypatch, target_fields):
    sessions, _ = child_db
    observed = []
    async def submit(requests, background_tasks, session, experiment_session, **kwargs):
        observed.append((requests, kwargs))
        children = []
        for request in requests:
            child = jobs.load_prepared_child(request)
            session.add(child)
            children.append(child)
        await session.commit()
        return [SimpleNamespace(id=child.id) for child in children]
    monkeypatch.setattr(shared_jobs, "submit_selected_child_jobs", submit)
    app = FastAPI()
    app.include_router(routes.router)
    async def dependency():
        async with sessions() as session:
            yield session
    app.dependency_overrides[get_session] = dependency
    app.dependency_overrides[get_experiment_session] = dependency
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        form = {"launch_context_id": "destination-independent", "idempotency_key": "upload-intent",
                **{key: value or "" for key, value in target_fields.items()}}
        response = await client.post("/api/frustrampnn/jobs/uploads/analyze", data=form,
                                     files={"pdb_file": ("source.pdb", _pdb(), "chemical/x-pdb")})
        assert response.status_code == 202, response.text
        child_id = response.json()["job_id"]
        requests, kwargs = observed[-1]
        assert kwargs["destination_launch_context_id"] == "destination-independent"
        assert requests[0].execution_target_id == target_fields.get("execution_target_id")
        assert ("execution_target_id" in requests[0].model_fields_set) == ("execution_target_id" in target_fields)
        async with sessions() as session:
            child = await session.get(Job, child_id)
            assert child.execution_target_id == target_fields.get("execution_target_id")
            assert child.params[jobs.ENVELOPE_KEY]["trigger"] == "upload_analyze"
            child.status = child.queue_status = "completed"
            await session.commit()
        response = await client.post(f"/api/frustrampnn/jobs/{child_id}/reanalyze",
            json={"launch_context_id": "reanalysis-destination", **target_fields})
        assert response.status_code == 202, response.text
        new_id = response.json()["job_id"]
        assert new_id != child_id
        assert observed[-1][1]["destination_launch_context_id"] == "reanalysis-destination"
        async with sessions() as session:
            child = await session.get(Job, new_id)
            assert child.params[jobs.ENVELOPE_KEY]["supersedes_child_job_id"] == child_id
            assert child.execution_target_id == target_fields.get("execution_target_id")


@pytest.mark.asyncio
async def test_selected_designs_explicit_local_does_not_inherit_parent_worker(child_db, monkeypatch):
    sessions, results = child_db
    parent_root = results / "source-parent"
    parent_root.mkdir()
    pdb = parent_root / "design.pdb"
    pdb.write_bytes(_pdb())
    async with sessions() as session:
        parent = Job(id="source-parent", name="source", status="completed", queue_status="completed",
                     model_id="boltz2", mode="predict", params={}, output_dir=str(parent_root),
                     execution_target_id="source-worker")
        session.add(parent)
        session.add(Design(id="design-one", job_id=parent.id, name="selected design", pdb_path=str(pdb)))
        await session.commit()
    observed = []
    async def submit(requests, background_tasks, session, experiment_session, **kwargs):
        observed.extend(requests)
        children = [jobs.load_prepared_child(request) for request in requests]
        for child in children:
            session.add(child)
        await session.commit()
        return [SimpleNamespace(id=child.id) for child in children]
    monkeypatch.setattr(shared_jobs, "submit_selected_child_jobs", submit)
    app = FastAPI()
    app.include_router(routes.router)
    async def dependency():
        async with sessions() as session:
            yield session
    app.dependency_overrides[get_session] = dependency
    app.dependency_overrides[get_experiment_session] = dependency
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/frustrampnn/jobs/source-parent/analyze", json={
            "selections": [{"design_id": "design-one", "source_sha256": hashlib.sha256(_pdb()).hexdigest()}],
            "execution_target_id": None,
        })
        assert response.status_code == 202, response.text
        assert observed[0].execution_target_id is None
        assert "execution_target_id" in observed[0].model_fields_set
        child_id = response.json()["child_jobs"][0]["job_id"]
        async with sessions() as session:
            child = await session.get(Job, child_id)
            assert child.execution_target_id is None
            assert child.parent_job_id == "source-parent"
            assert child.params[jobs.ENVELOPE_KEY]["trigger"] == "design_analyze"
