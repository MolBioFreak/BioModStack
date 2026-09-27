"""Scratch-store fan-out reservation over existing pinned Plan authority."""
import json
import pytest
from sqlalchemy import select, func
from experiment_models import ExperimentWorkflowDraft, ExperimentRunAttempt, ExperimentLaunchContext
from experiment_services import (
    create_project, create_global_experiment, create_domain_experiment, create_workflow,
    persist_workflow_plan_authority, save_workflow_draft, save_workflow_revision,
    prepare_workflow, PLAN_LAUNCH_AUTHORITY_FIELDS, PLAN_LAUNCH_AUTHORITY_GENERATION_FIELDS,
    IdempotencyConflict,
)
from services.global_experiments.launch_contexts import (
    create_prepared_launch_context, prepare_child_launch_contexts, resolve_launch_context, LaunchContextError,
)
from test_project_manager_adapters import stores, _project_payload, _global_payload, _domain_payload


async def prepared(session, project, experiment, domain, name):
    plan = await create_workflow(session, project.id, name, 'protein.structure_prediction.esmfold2', experiment_id=domain.id)
    authority, contract = await persist_workflow_plan_authority(session, workflow_id=plan.id,
        workspace_id=project.id, domain_experiment_id=domain.id,
        expected_domain_revision_id=domain.current_revision_id, capability_id='protein.structure_prediction.esmfold2')
    draft = await session.scalar(select(ExperimentWorkflowDraft).where(ExperimentWorkflowDraft.workflow_id == plan.id))
    payload = json.loads(draft.canonical_payload)
    payload['parameters']['sequence'] = 'ACDEFGHIK'
    payload['scheduler']['params'] = {**payload['parameters'], 'workflow_adapter': payload['adapter_id']}
    await save_workflow_draft(session, plan.id, payload, expected_generation=draft.generation)
    revision = await save_workflow_revision(session, plan.id, expected_head_generation=plan.head_generation)
    # Existing protein Plan contract carries these legacy connector slots. They
    # are fixture authority, not an assertion of a live connector deployment.
    launch = {key: 1 if key in PLAN_LAUNCH_AUTHORITY_GENERATION_FIELDS else 'fixture-authority'
              for key in PLAN_LAUNCH_AUTHORITY_FIELDS}
    launch.update(project_id=project.id, global_experiment_id=experiment.id, domain_id=domain.id,
                  domain_revision_id=domain.current_revision_id, capability_contract_sha256=authority.capability_contract_sha256)
    preparation = await prepare_workflow(session, revision.resource_id, {'launch_authority': launch})
    assert preparation.validation_status == 'valid', preparation.validation_receipt_json
    return preparation


@pytest.mark.asyncio
async def test_distinct_attempts_replay_and_explicit_destination(stores):
    _, experiments, _ = stores
    async with experiments() as session:
        project = await create_project(session, _project_payload())
        experiment = await create_global_experiment(session, project.id, _global_payload())
        domain = await create_domain_experiment(session, project.id, experiment.id, _domain_payload('protein_in_silico'))
        parent = await prepared(session, project, experiment, domain, 'destination')
        context = await create_prepared_launch_context(session, project_id=project.id,
            global_experiment_id=experiment.id, domain_experiment_id=domain.id,
            preparation_id=parent.resource_id,
            return_uri=f'/projects/{project.id}?focus={experiment.id}&selected=domain_experiment:{domain.id}')
        children = [await prepared(session, project, experiment, domain, name) for name in ('child-a', 'child-b')]
        kwargs = dict(destination_launch_context_id=context.launch_context_id,
                      preparation_ids=[row.resource_id for row in children], idempotency_key='fanout')
        response = await prepare_child_launch_contexts(session, **kwargs)
        assert response == await prepare_child_launch_contexts(session, **kwargs)
        assert len({row['launch_context_id'] for row in response['children']}) == 2
        assert len({row['run_attempt_id'] for row in response['children']}) == 2
        assert context.state == 'issued' and context.run_attempt_id is None
        for row in response['children']:
            child = await resolve_launch_context(session, row['launch_context_id'])
            attempt = await session.get(ExperimentRunAttempt, child.run_attempt_id)
            assert child.project_id == project.id
            assert attempt.scheduler_job_id
        with pytest.raises(IdempotencyConflict):
            await prepare_child_launch_contexts(session, **{**kwargs, 'preparation_ids': list(reversed(kwargs['preparation_ids']))})
        with pytest.raises(LaunchContextError):
            await prepare_child_launch_contexts(session, **{**kwargs, 'preparation_ids': [parent.resource_id], 'idempotency_key': 'parent-reuse'})
        assert await session.scalar(select(func.count()).select_from(ExperimentRunAttempt)) == 2
        count_before = await session.scalar(select(func.count()).select_from(ExperimentLaunchContext))
        extra = await prepared(session, project, experiment, domain, 'rollback-child')
        with pytest.raises(LaunchContextError):
            await prepare_child_launch_contexts(session, destination_launch_context_id=context.launch_context_id,
                preparation_ids=[extra.resource_id, 'missing-preparation'], idempotency_key='atomic-failure')
        assert await session.scalar(select(func.count()).select_from(ExperimentLaunchContext)) == count_before
        # Consume an actual reserved child binding, then select that consumed
        # context as the explicit destination for another round. It is not
        # claimed or rebound by the preparation helper.
        from services.global_experiments.launch_contexts import claim_launch_context, consume_launch_context
        consumed_id = response['children'][0]['launch_context_id']
        consumed, token = await claim_launch_context(session, consumed_id)
        attempt = await session.get(ExperimentRunAttempt, consumed.run_attempt_id)
        consumed, binding = await consume_launch_context(session, launch_context_id=consumed_id,
            claim_token=token, canonical_job_id=attempt.scheduler_job_id, canonical_batch_id=None)
        next_round = await prepare_child_launch_contexts(session, destination_launch_context_id=consumed_id,
            preparation_ids=[extra.resource_id], idempotency_key='next-round')
        assert consumed.state == 'consumed'
        assert json.loads(consumed.binding_receipt_json) == binding
        assert next_round['children'][0]['launch_context_id'] != consumed_id
        await session.commit()


@pytest.mark.asyncio
async def test_standalone_never_resolves_or_creates_project_authority():
    assert await prepare_child_launch_contexts(None, destination_launch_context_id=None,
                                              preparation_ids=[], idempotency_key='standalone') is None
