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
def test_optional_md_native_owner_queues_once_without_prediction(project_context_preview_store, monkeypatch, project):
    from routers import jobs
    from services import gpu_orchestrator
    store = project_context_preview_store
    monkeypatch.setenv('BMS_FEATURE_MOLECULAR_DYNAMICS', '1')
    monkeypatch.setattr(jobs, 'get_registry', lambda: _AcceptingRegistry())
    monkeypatch.setattr(jobs, 'get_results_dir', lambda: store['results_root'])
    monkeypatch.setattr(gpu_orchestrator, 'estimate_vram', lambda *_a, **_kw: 0)
    intent = deepcopy(store['native_intent'])
    async def exercise():
        token = jobs.current_launch_context_id.set(intent['launch_context_id'] if project else None)
        try:
            await submit_twice()
        finally:
            jobs.current_launch_context_id.reset(token)
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
                        'mode': 'simulate', 'params': {'intent': intent}}]}]}})
            first = await selected_round.submit(session, experiments, payload)
            assert not first['errors'], first
            assert len(first['steps']) == 1, first
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
            second = await selected_round.submit(session, experiments, payload)
            assert second['round_id'] == first['round_id']
            assert second['steps'][0]['job_id'] == child.id
            assert len(list(await session.scalars(select(Job).where(Job.model_id == 'molecular_dynamics')))) == 1
    asyncio.run(exercise())
