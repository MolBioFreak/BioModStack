"""Real normalized JobCreate child preparation with scratch Project storage."""
import json
import pytest
from sqlalchemy import func, select
from schemas import JobCreate
from experiment_models import ExperimentWorkflowPreparation, ExperimentLaunchContext, ExperimentAggregateHead
from experiment_services import validate_preparation_authority, IdempotencyConflict, ValidationFailure
from services.global_experiments import workflow_setups
from services.global_experiments.launch_contexts import prepare_child_launch_contexts
from test_project_workflow_setups import setup_store, _project


async def destination(session):
    project = await _project(session)
    setup = await workflow_setups.create_workflow_setup(
        session, project_id=project.id, relationship_kind="primary", global_experiment_id=None,
        experiment_name="Destination", experiment_objective="Fold", domain_kind="protein_in_silico",
        capability_id="protein.structure_prediction.esmfold2", idempotency_key="setup")
    await workflow_setups.save_workflow_setup_draft(session, project_id=project.id,
        setup_context_id=setup["setup_context_id"], draft={"sequence": "MQIFVK"},
        expected_generation=0, idempotency_key="save")
    return await workflow_setups.prepare_workflow_setup_launch(session, project_id=project.id,
        setup_context_id=setup["setup_context_id"], expected_generation=1, idempotency_key="prepare")


def requests():
    return [JobCreate(name=f"Child {index}", model_id="esmfold2", mode="predict",
        params={"sequence": "MQIFVK", "pred_method": "esmfold2"}, parent_job_id="external-source")
        for index in range(2)]


@pytest.mark.asyncio
async def test_normalized_requests_fanout_and_durable_replay(setup_store):
    async with setup_store() as session:
        dest = await destination(session)
        context_id = dest["launch_context_id"]
        prepared = await prepare_child_launch_contexts(session,
            destination_launch_context_id=context_id, job_requests=requests(), idempotency_key="children")
        children = prepared["children"]
        assert len({child["launch_context_id"] for child in children}) == 2
        assert len({child["run_attempt_id"] for child in children}) == 2
        for child in children:
            request = JobCreate.model_validate(child["job_request"])
            assert request.parent_job_id == "external-source"
            assert request.launch_context_id == child["launch_context_id"] != context_id
            row = await session.get(ExperimentWorkflowPreparation, child["preparation_id"])
            await validate_preparation_authority(session, row)
            assert json.loads(row.scheduler_payload_json)["params"] == request.params
            context = await session.get(ExperimentLaunchContext, child["launch_context_id"])
            assert context.project_id == dest["project_id"]
            from services.global_experiments.launch_contexts import validate_prepared_child_job_request, LaunchContextError
            await validate_prepared_child_job_request(session, context, request)
            approved = request.model_copy(update={"execution_plan_approval": "a" * 64})
            await validate_prepared_child_job_request(session, context, approved)
            changed_parent = request.model_copy(update={"parent_job_id": "different-source"})
            with pytest.raises(LaunchContextError, match="immutable normalized Job authority"):
                await validate_prepared_child_job_request(session, context, changed_parent)
        parent = await session.get(ExperimentLaunchContext, context_id)
        assert parent.state == "issued" and parent.run_attempt_id is None
        await session.commit()
    async with setup_store() as session:
        assert await prepare_child_launch_contexts(session, destination_launch_context_id=context_id,
            job_requests=requests(), idempotency_key="children") == prepared
        changed = requests()
        changed[0].params["sequence"] = "AAAA"
        with pytest.raises(IdempotencyConflict):
            await prepare_child_launch_contexts(session, destination_launch_context_id=context_id,
                job_requests=changed, idempotency_key="children")
        row = await session.get(ExperimentWorkflowPreparation, children[0]["preparation_id"])
        tampered = json.loads(row.scheduler_payload_json)
        tampered["params"]["sequence"] = "AAAA"
        row.scheduler_payload_json = json.dumps(tampered)
        with session.no_autoflush:
            with pytest.raises(ValidationFailure, match="scheduler no longer matches"):
                await validate_preparation_authority(session, row)
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError, match="workflow preparation is immutable"):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_bad_later_child_rolls_back_entire_fanout(setup_store):
    async with setup_store() as session:
        dest = await destination(session)
        count = await session.scalar(select(func.count()).select_from(ExperimentAggregateHead))
        children = requests()
        children[1].mode = "not_a_mode"
        with pytest.raises(Exception):
            await prepare_child_launch_contexts(session, destination_launch_context_id=dest["launch_context_id"],
                job_requests=children, idempotency_key="bad")
        assert await session.scalar(select(func.count()).select_from(ExperimentAggregateHead)) == count
        assert await session.scalar(select(func.count()).select_from(ExperimentWorkflowPreparation)) == 1


@pytest.mark.asyncio
async def test_standalone_has_no_project_side_effects(setup_store):
    async with setup_store() as session:
        assert await prepare_child_launch_contexts(session, destination_launch_context_id=None,
            job_requests=requests(), idempotency_key="standalone") is None
        assert await session.scalar(select(func.count()).select_from(ExperimentAggregateHead)) == 0
