"""Exact shared MD hunks require the documented full-module overlay until parent integration."""
import asyncio
from copy import deepcopy
import pytest
from sqlalchemy import select
from database import Job
from schemas import SelectedRoundRequest
from services import binder_selected_round as selected_round, binder_round as rounds
from test_md_typed_launch import project_context_preview_store, _AcceptingRegistry


@pytest.mark.parametrize('project_context_preview_store', ['prepared', 'compiled', 'guided'], indirect=True)
@pytest.mark.parametrize('project', [False, True])
@pytest.mark.parametrize('initial', [False, True])
def test_md_round_repeated_invocations_retry_cold(project_context_preview_store, monkeypatch, project, initial):
    from routers import jobs
    from services import gpu_orchestrator
    store = project_context_preview_store
    monkeypatch.setenv('BMS_FEATURE_MOLECULAR_DYNAMICS', '1')
    monkeypatch.setattr(jobs, 'get_registry', lambda: _AcceptingRegistry())
    monkeypatch.setattr(jobs, 'get_results_dir', lambda: store['results_root'])
    monkeypatch.setattr(gpu_orchestrator, 'estimate_vram', lambda *_a, **_kw: 0)
    intent = deepcopy(store['native_intent'])
    async def exercise():
        if project:
            from test_project_normalized_child_requests import destination
            async with store['experiment_sessions']() as experiments:
                from experiment_models import ExperimentResourceAdmissionPolicy
                from services.ngs_molbio_n5 import ADMISSION_POLICY_VERSION
                from biomodstack_local_resources import applied_local_policy
                policy = applied_local_policy()
                experiments.add(ExperimentResourceAdmissionPolicy(policy_id='managed-workflows',
                    policy_version=ADMISSION_POLICY_VERSION, cpu_thread_limit=policy.cpu_threads,
                    dram_byte_limit=policy.memory_bytes))
                await experiments.flush()
                dest = await destination(experiments)
                await experiments.commit()
                intent['launch_context_id'] = dest['launch_context_id']
        token = jobs.current_launch_context_id.set(intent['launch_context_id'] if project else None)
        try:
            await submit_twice()
        finally:
            jobs.current_launch_context_id.reset(token)
    def project_round(value):
        if not initial:
            return value
        return {**value, 'round_id': None, 'steps': [
            {**step['metadata'], **{k: v for k, v in step.items() if k != 'metadata'}}
            for step in value['steps'].values()]}
    async def reconcile(session, experiments, root_id, round_id=None, retry=False):
        value = (await rounds.reconcile_round(session, experiments, root_id, retry=retry) if initial else
                 await rounds.reconcile_selected(session, experiments, root_id, round_id, retry=retry))
        return project_round(value)
    async def submit_twice():
        async with store['core_sessions']() as session, store['experiment_sessions']() as experiments:
            root = await session.scalar(select(Job))
            payload = SelectedRoundRequest.model_validate({'operation': 'round', 'source_job_id': root.id,
                'idempotency_key': 'md-native', 'execution_target_id': None,
                'launch_context_id': intent['launch_context_id'] if project else None,
                'execution_policy': intent['execution_policy'],
                'selection': {'candidate_refs': [{'kind': 'design', 'job_id': root.id, 'design_id': store['expected_design_id']}]},
                'round_plan': {'schema_version': 2, 'stages': [{'id': 'md', 'kind': 'md',
                    'input': {'selected': True}, 'models': [{'invocation_id': 'gromacs', 'model_id': 'molecular_dynamics',
                        'mode': 'simulate', 'params': {'intent': intent}},
                        {'invocation_id': 'gromacs-repeat', 'model_id': 'molecular_dynamics',
                         'mode': 'simulate', 'params': {'intent': intent}}]}]}})
            if initial:
                from database import Design
                from schemas import SequenceDesignRequest
                await session.delete(await session.get(Design, store['other_design_id']))
                root.status = 'completed'
                root.provenance = {**(root.provenance or {}),
                    'launch_context_id': payload.launch_context_id,
                    rounds.SEQUENCE_REQUEST: SequenceDesignRequest(schema_version=2, enabled=True,
                        round_plan=payload.round_plan).model_dump(mode='json')}
                await session.commit()
                first = await reconcile(session, experiments, root.id)
            else:
                first = await selected_round.submit(session, experiments, payload)
            assert not first['errors'], first
            assert len(first['steps']) == 2, first
            step = first['steps'][0]
            assert step['job_id'], step.get('error', first)
            child = await session.get(Job, step['job_id'])
            assert child.model_id == 'molecular_dynamics' and child.mode == 'simulate'
            assert child.provenance[rounds.STEP]['source_ref']['design_id'] == store['expected_design_id']
            assert child.provenance[rounds.STEP]['parent_step_ids'] == []
            assert child.execution_target_id is None
            assert child.params['intent']['stages'] == intent['stages']
            assert child.params['intent']['analysis'] == intent['analysis']
            assert child.params['intent']['input']['kind'] == intent['input']['kind']
            if project:
                from experiment_models import ExperimentLaunchContext
                from experiment_services import load_workflow_plan_authority
                from services.global_experiments.launch_contexts import _native_md_materialization_matches
                context = await experiments.get(ExperimentLaunchContext, child.provenance['launch_context_id'])
                _, contract = await load_workflow_plan_authority(experiments, context.workflow_id)
                assert contract['capability']['result_adapter_ids'] == ['bms.md.result-reference.adapter.v1']
                expected = contract['capability']['normalized_job_request']['params']
                assert _native_md_materialization_matches(child, expected, child.params)
                altered = deepcopy(child.params)
                altered['md_job_spec']['replicas'] = 9
                assert not _native_md_materialization_matches(child, expected, altered)
                altered = deepcopy(child.params)
                first_input = next(k for k in altered['md_job_spec']['input'] if k.endswith('_sha256'))
                altered['md_job_spec']['input'][first_input] = '0' * 64
                assert not _native_md_materialization_matches(child, expected, altered)
            second = (await reconcile(session, experiments, root.id) if initial else
                      await selected_round.submit(session, experiments, payload))
            assert second['round_id'] == first['round_id']
            assert second['steps'][0]['job_id'] == child.id
            assert len(list(await session.scalars(select(Job).where(Job.model_id == 'molecular_dynamics')))) == 2
            child.status = 'failed'
            child_id = child.id
            root_id = root.id
            await session.commit()
        # New sessions must use the durable request rather than a warm object.
        async with store['core_sessions']() as session, store['experiment_sessions']() as experiments:
            retried = await reconcile(session, experiments, root_id, first['round_id'], retry=True)
            assert not retried['errors'], retried
            replacements = [s for s in retried['steps'] if s.get('retry_of_job_id') == child_id]
            assert len(replacements) == 1, retried
            replacement = replacements[0]
            assert replacement['job_id'] and replacement['job_id'] != child_id, replacement
            new = await session.get(Job, replacement['job_id'])
            assert new.name == new.params['intent']['name']
            assert new.params['intent']['stages'] == intent['stages']
            assert new.params['intent']['analysis'] == intent['analysis']
            assert (await session.get(Job, child_id)).status == 'failed'
            if project:
                old = await session.get(Job, child_id)
                assert new.provenance['launch_context_id'] != old.provenance['launch_context_id']
            assert len(list(await session.scalars(select(Job).where(Job.model_id == 'molecular_dynamics')))) == 3
            replay = await reconcile(session, experiments, root_id, first['round_id'], retry=True)
            assert len(list(await session.scalars(select(Job).where(Job.model_id == 'molecular_dynamics')))) == 3
    asyncio.run(exercise())
