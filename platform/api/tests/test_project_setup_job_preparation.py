"""Real scratch-store setup/Plan/attempt authority; no mocked validators."""
import json
import pytest
from sqlalchemy import select
from experiment_models import ExperimentWorkflowPreparation, ExperimentLaunchContext
from experiment_services import create_run_group, validate_preparation_authority
from services.global_experiments import workflow_setups
from services.global_experiments.launch_contexts import validate_bound_job_request
from test_project_workflow_setups import setup_store, _project


@pytest.fixture(autouse=True)
def isolate_applied_local_policy():
    # Other fixtures change the installation home; do not inherit their cached
    # process policy. Resolve the real policy without mocking source/admission.
    from biomodstack_local_resources import applied_local_policy
    applied_local_policy.cache_clear()
    try:
        yield
    finally:
        applied_local_policy.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("include_admission", [False, True])
async def test_setup_reopen_prepare_reserve_and_validate_job(setup_store, include_admission):
    async with setup_store() as session:
        project = await _project(session)
        project_id = project.id
        setup = await workflow_setups.create_workflow_setup(
            session, project_id=project_id, relationship_kind="primary",
            global_experiment_id=None, experiment_name="Target", experiment_objective="Fold",
            domain_kind="protein_in_silico", capability_id="protein.structure_prediction.esmfold2",
            idempotency_key="setup")
        setup_id = setup["setup_context_id"]
        saved = await workflow_setups.save_workflow_setup_draft(
            session, project_id=project_id, setup_context_id=setup_id,
            draft={"sequence": "MQIFVK"}, expected_generation=0, idempotency_key="save")
        await session.commit()
    async with setup_store() as session:
        reopened = await workflow_setups.get_workflow_setup(
            session, project_id=project_id, setup_context_id=setup_id)
        assert reopened["draft"] == saved["draft"]
        prepared = await workflow_setups.prepare_workflow_setup_launch(
            session, project_id=project_id, setup_context_id=setup_id,
            expected_generation=1, idempotency_key="prepare")
        preparation = await session.get(ExperimentWorkflowPreparation, prepared["preparation_id"])
        await validate_preparation_authority(session, preparation)
        group = await create_run_group(session, project_id, [preparation.resource_id],
            idempotency_key="run", launch_context_ids={preparation.resource_id: prepared["launch_context_id"]})
        assert group.resource_id
        assert await workflow_setups.prepare_workflow_setup_launch(
            session, project_id=project_id, setup_context_id=setup_id,
            expected_generation=1, idempotency_key="prepare") == prepared
        from schemas import JobCreate
        from routers.jobs import normalize_job_request
        scheduler = json.loads(preparation.scheduler_payload_json)
        normalized = normalize_job_request(JobCreate(**scheduler))
        assert normalized.params == scheduler["params"]
        context = await session.get(ExperimentLaunchContext, prepared["launch_context_id"])
        from services.global_experiments.launch_contexts import LaunchContextError
        with pytest.raises(LaunchContextError, match="does not match"):
            await validate_bound_job_request(session, context, job_name=scheduler["name"],
                model_id=scheduler["model_id"], mode=scheduler["mode"],
                params={**normalized.params, "sequence": "AAAA"}, pinned_gpu=None)
        if not include_admission:
            return
        from services.ngs_molbio_n5 import reserve_run_group, ResourceAdmissionDenied
        try:
            await reserve_run_group(session, group_id=group.resource_id,
                domain_id=setup["domain_experiment_id"], actor="scratch-test")
        except ResourceAdmissionDenied as exc:
            if exc.code == "resource_source_revision_unavailable":
                pytest.skip("Real resource admission blocked: candidate lacks a frozen successor runtime record; bound Job validation NOT verified")
            raise
        context = await session.get(ExperimentLaunchContext, prepared["launch_context_id"])
        assert context.state == "reserved" and context.run_attempt_id
        scheduler = json.loads(preparation.scheduler_payload_json)
        params = await validate_bound_job_request(session, context,
            job_name=scheduler["name"], model_id=scheduler["model_id"], mode=scheduler["mode"],
            params=scheduler["params"], pinned_gpu=None)
        assert all(params[key] == value for key, value in scheduler["params"].items())
        assert group.resource_id
        assert await workflow_setups.prepare_workflow_setup_launch(
            session, project_id=project_id, setup_context_id=setup_id,
            expected_generation=1, idempotency_key="prepare") == prepared
