"""Real Project reservation -> canonical queued Jobs; no scheduler/model execution."""
import copy
import json
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Job
from experiment_models import ExperimentLaunchContext, ExperimentRunAttempt
from schemas import JobCreate
from routers import jobs
from services.global_experiments.launch_contexts import validate_bound_job
from test_project_workflow_setups import setup_store
from test_project_normalized_child_requests import destination
from test_boltzgen_generation_launch import target


@pytest_asyncio.fixture
async def isolated_core(tmp_path, monkeypatch):
    import paths
    root = tmp_path / "owned"
    inputs = root / "inputs"
    results = root / "results"
    inputs.mkdir(parents=True)
    results.mkdir()
    # Only storage placement changes. Resource/source authority and the actual
    # creation, reservation, binding and publication owners are never mocked.
    monkeypatch.setenv("BMS_DATA", str(root))
    monkeypatch.setenv("BMS_RESULTS_DIR", str(results))
    monkeypatch.setattr(jobs, "get_results_dir", lambda: results)
    monkeypatch.setattr(jobs, "get_inputs_dir", lambda: inputs)
    monkeypatch.setattr(paths, "get_results_dir", lambda: results)
    monkeypatch.setattr(paths, "get_inputs_dir", lambda: inputs)
    monkeypatch.setattr(paths, "get_data_root", lambda: root)
    monkeypatch.setattr(jobs, "get_allowed_roots", lambda: {"inputs": tmp_path, "bms_results": results})
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'bound-jobs.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def native_request(target, model, mode, index):
    if model == "boltzgen":
        params = {"target_pdb": str(target), "alpha": 0, "min_plddt": None, "filter_biased": False}
    else:
        params = {"target_pdb": str(target), "samples_per_target": 2, "self_condition": False}
        if mode == "protein_binder":
            params.update(binder_chain="B", dataset_seed=0, translation_corrupt=False)
        else:
            params.update(framework_pdb=str(target), antigen_chain="A", heavy_chain="H", specified_hotspots="A1")
            if mode == "antibody_binder":
                params["light_chain"] = "L"
    return JobCreate(name=f"Native child {index}", model_id=model, mode=mode,
                     params=params, parent_job_id="source")


@pytest.mark.asyncio
@pytest.mark.parametrize("model,mode", [
    ("ppiflow", "protein_binder"), ("ppiflow", "antibody_binder"),
    ("ppiflow", "nanobody_binder"), ("boltzgen", "protein_binder"),
    ("boltzgen", "peptide_binder"), ("boltzgen", "nanobody_binder"),
])
async def test_reserved_native_children_create_bind_replay_and_reopen(setup_store, isolated_core, target, model, mode):
    async with setup_store() as exp, isolated_core() as core:
        dest = await destination(exp)
        await exp.commit()
        source = Job(id="source", name="Existing source", model_id=model, mode=mode,
                     params={}, status="completed", provenance={"source_evidence": "unchanged"})
        core.add(source)
        await core.commit()
        original_source = copy.deepcopy(source.provenance)
        requests = [native_request(target, model, mode, index) for index in range(2)]
        # Retain BackgroundTasks without invoking them: this exercises canonical
        # queue insertion, not science, scheduler dispatch, a worker or a live DB.
        tasks = BackgroundTasks()
        try:
            responses = await jobs.submit_selected_child_jobs(
                requests, tasks, core, exp, destination_launch_context_id=dest["launch_context_id"],
                idempotency_key="native-children", response_context={"operation": "generation"},
            )
        except HTTPException as exc:
            if isinstance(exc.detail, dict) and exc.detail.get("code") == "resource_source_revision_unavailable":
                pytest.skip("Canonical resource/Job path requires genuine frozen source binding; no authority is mocked")
            raise
        assert len(responses) == len({response.id for response in responses}) == 2
        ids = [response.id for response in responses]
        contexts = [response.launch_context_id for response in responses]
        assert len(set(contexts)) == 2 and dest["launch_context_id"] not in contexts
        for response in responses:
            context = await exp.get(ExperimentLaunchContext, response.launch_context_id)
            attempt = await exp.get(ExperimentRunAttempt, context.run_attempt_id)
            job = await core.get(Job, response.id)
            await validate_bound_job(exp, context, job)
            assert context.project_id == dest["project_id"]
            assert context.state == "consumed" and context.canonical_job_id == job.id
            assert attempt.scheduler_job_id == job.id
            assert job.parent_job_id == source.id
            assert job.status == job.queue_status == "queued"
            assert job.provenance["launch_context_id"] == context.launch_context_id
            assert response.launch_context_binding and response.return_uri
            if model == "ppiflow":
                assert Path(job.params["ppiflow_generation_request"]).is_dir()
                assert job.params["self_condition"] is False
            else:
                assert job.params["boltzgen_filter_biased"] is False
                from services.global_experiments.launch_contexts import LaunchContextError
                saved_params = copy.deepcopy(job.params)
                saved_provenance = copy.deepcopy(job.provenance)
                # Owned materialization is allowed, not arbitrary science or
                # request mutation. Both requested and effective views matter.
                for key, changed in (("boltzgen_alpha", 0.7), ("boltzgen_metrics_override", "design_ptm=9")):
                    job.params = {**saved_params, key: changed}
                    with pytest.raises(LaunchContextError, match="bound Workflow Revision"):
                        await validate_bound_job(exp, context, job)
                job.params = saved_params
                changed_provenance = copy.deepcopy(saved_provenance)
                changed_provenance["core_protein_requested_params"]["boltzgen_alpha"] = 0.7
                job.provenance = changed_provenance
                with pytest.raises(LaunchContextError, match="bound Workflow Revision"):
                    await validate_bound_job(exp, context, job)
                job.provenance = saved_provenance
                await validate_bound_job(exp, context, job)
        assert source.provenance == original_source
        assert (await exp.get(ExperimentLaunchContext, dest["launch_context_id"])).state == "issued"
        replay = await jobs.submit_selected_child_jobs(
            requests, BackgroundTasks(), core, exp, destination_launch_context_id=dest["launch_context_id"],
            idempotency_key="native-children", response_context={"operation": "generation"},
        )
        assert [response.id for response in replay] == ids
        assert await core.scalar(select(func.count(Job.id)).where(Job.parent_job_id == source.id)) == 2
    async with setup_store() as exp, isolated_core() as core:
        for identity, context_id in zip(ids, contexts):
            job = await core.get(Job, identity)
            context = await exp.get(ExperimentLaunchContext, context_id)
            await validate_bound_job(exp, context, job)
            binding = json.loads(context.binding_receipt_json)
            assert binding["canonical_job_id"] == job.id
