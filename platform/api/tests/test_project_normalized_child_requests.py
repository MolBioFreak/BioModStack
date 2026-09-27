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
@pytest.mark.parametrize("preallocate", [False, True])
async def test_normalized_requests_fanout_and_durable_replay(setup_store, preallocate):
    async with setup_store() as session:
        dest = await destination(session)
        context_id = dest["launch_context_id"]
        attempts = ["b58c11e2-1511-4b42-8cda-5e1bd9867e21", "19decb38-788a-46e2-aaf4-623553b8e43f"] if preallocate else None
        prepared = await prepare_child_launch_contexts(session,
            destination_launch_context_id=context_id, job_requests=requests(), idempotency_key="children",
            preallocated_attempt_ids=attempts)
        children = prepared["children"]
        assert len({child["launch_context_id"] for child in children}) == 2
        assert len({child["run_attempt_id"] for child in children}) == 2
        if attempts:
            from experiment_models import ExperimentRunAttempt
            from experiment_services import scheduler_job_id_for_attempt
            assert [child["run_attempt_id"] for child in children] == attempts
            for attempt_id in attempts:
                attempt = await session.get(ExperimentRunAttempt, attempt_id)
                assert attempt.scheduler_job_id == scheduler_job_id_for_attempt(attempt_id)
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
            job_requests=requests(), idempotency_key="children", preallocated_attempt_ids=attempts) == prepared
        changed = requests()
        changed[0].params["sequence"] = "AAAA"
        with pytest.raises(IdempotencyConflict):
            await prepare_child_launch_contexts(session, destination_launch_context_id=context_id,
                job_requests=changed, idempotency_key="children", preallocated_attempt_ids=attempts)
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
@pytest.mark.parametrize('remote', [False, True], ids=['local', 'remote'])
async def test_frustrampnn_project_identity_is_chosen_before_snapshots(setup_store, tmp_path, monkeypatch, remote):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from database import Base, Job, ExecutionTarget
    from experiment_models import ExperimentRunAttempt
    from experiment_services import new_id, scheduler_job_id_for_attempt
    from services.frustrampnn import jobs as frustra
    from services.frustrampnn.settings import default_settings
    from test_frustrampnn_child_jobs import _pdb
    import hashlib
    import paths
    monkeypatch.setattr(frustra, 'get_results_dir', lambda: tmp_path / 'results')
    monkeypatch.setattr(paths, 'get_results_dir', lambda: tmp_path / 'results')
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "frustra.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    core_sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with setup_store() as session, core_sessions() as core:
            worker_id = 'vast:project-frustra' if remote else None
            if remote:
                from datetime import datetime
                core.add(ExecutionTarget(id=worker_id, provider='vast', provider_instance_id='project-frustra',
                    active=True, state='ready', provider_metadata={'inventory': {
                        'status': 'complete', 'present': True, 'running': True,
                        'checked_at': datetime.utcnow().isoformat()}}))
                await core.commit()
            dest = await destination(session)
            attempts = [new_id('run_attempt'), new_id('run_attempt')]
            children = []
            for attempt in attempts:
                children.append(await frustra.create_child_job(core,
                    selections=[frustra.upload_selection(filename='source.pdb', payload=_pdb(),
                        expected_sha256=hashlib.sha256(_pdb()).hexdigest())],
                    source_parent=None, trigger='binder_selected', requested_settings=default_settings(),
                    preallocated_job_id=scheduler_job_id_for_attempt(attempt), prepare_only=True,
                    execution_target_id=worker_id))
            requests_ = [frustra.prepared_child_request(child) for child in children]
            result = await prepare_child_launch_contexts(session,
                destination_launch_context_id=dest['launch_context_id'], job_requests=requests_,
                native_entrypoints=['workflows/frustrampnn_analysis.nf'] * 2,
                preallocated_attempt_ids=attempts, idempotency_key='frustra', core_session=core)
            for prepared, child, attempt_id in zip(result['children'], children, attempts):
                attempt = await session.get(ExperimentRunAttempt, attempt_id)
                assert attempt.scheduler_job_id == child.id
                assert prepared['run_attempt_id'] == attempt_id
                assert frustra.load_prepared_child(JobCreate.model_validate(prepared['job_request'])).id == child.id
                assert await core.get(Job, child.id) is None
            assert len({row['launch_context_id'] for row in result['children']}) == 2
            from fastapi import BackgroundTasks, HTTPException
            from routers import jobs
            async def submit():
                return await jobs.submit_selected_child_jobs(requests_, BackgroundTasks(), core, session,
                    destination_launch_context_id=dest['launch_context_id'], idempotency_key='frustra',
                    preallocated_attempt_ids=attempts, response_context={})
            if remote:
                with pytest.raises(HTTPException) as review:
                    await submit()
                assert review.value.detail['code'] == 'remote_prepared_job_review_required'
                inserted = []
                for payload in review.value.detail['job_requests']:
                    request = JobCreate.model_validate(payload)
                    token = jobs.current_launch_context_id.set(request.launch_context_id)
                    try:
                        preview = await jobs.preview_job_execution_plan(request, core, session)
                        assert preview['admissible'], preview['blockers']
                        request.execution_plan_approval = preview['approval_digest']
                        child = await jobs.create_job(request.model_copy(deep=True), BackgroundTasks(), core, experiment_session=session)
                        inserted.append(child)
                        # A resumed partial batch reopens its already inserted child.
                        replay = await jobs.create_job(request.model_copy(deep=True), BackgroundTasks(), core, experiment_session=session)
                        assert replay.id == child.id
                    finally:
                        jobs.current_launch_context_id.reset(token)
            else:
                inserted = await submit()
            assert [row.id for row in inserted] == [child.id for child in children]
            for child in children:
                stored = await core.get(Job, child.id)
                assert stored.params[frustra.ENVELOPE_KEY]['execution_owner_job_id'] == stored.id
                assert stored.execution_target_id == worker_id
            await session.commit()
        async with setup_store() as session:
            assert await prepare_child_launch_contexts(session,
                destination_launch_context_id=dest['launch_context_id'], job_requests=requests_,
                native_entrypoints=['workflows/frustrampnn_analysis.nf'] * 2,
                preallocated_attempt_ids=attempts, idempotency_key='frustra') == result
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_standalone_has_no_project_side_effects(setup_store):
    async with setup_store() as session:
        assert await prepare_child_launch_contexts(session, destination_launch_context_id=None,
            job_requests=requests(), idempotency_key="standalone") is None
        assert await session.scalar(select(func.count()).select_from(ExperimentAggregateHead)) == 0
