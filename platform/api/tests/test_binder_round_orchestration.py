"""Scratch-store round orchestration; no model execution or remote starts."""
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select

from database import Design, Job
from routers import jobs
from schemas import BinderRoundRequest, JobCreate, JobResponse
from services import binder_round as rounds
from services.binder_round_inputs import normalize_request
from test_binder_continuation import selected, PDB
from test_project_workflow_setups import setup_store


def envelope(**changes):
    result = {'schema_version': 1, 'enabled': True,
        'sequence_design': {'model_id': 'proteinmpnn', 'params': {'seqs_per_design': 3, 'mpnn_relax_max_cycles': 0}},
        'prediction': {'model_id': 'protenix', 'params': {'protenix_use_msa': False, 'protenix_n_sample': 2}},
        'binder_chains': ['A'], 'target_chains': ['B']}
    result.update(changes)
    return normalize_request(result).model_dump(mode='json')


async def fixture_root(selected, *, backbone=False, remote=False):
    _, session, root, _, tmp = selected
    target = tmp / 'target.pdb'
    target.write_text(PDB.replace('ALA A', 'GLY B'))
    root.model_id = 'ppiflow' if backbone else 'boltzgen'
    root.params = {'target_pdb': str(target)}
    root.provenance = {rounds.REQUEST: envelope()}
    root.execution_target_id = 'vast:round' if remote else None
    design = await session.get(Design, 'd0')
    design.artifact_class = 'binder_backbone' if backbone else 'binder_complex'
    if backbone:
        # A design-stage complex actually contains the declared fixed target.
        # The independent prediction target remains a separately owned input.
        Path(design.pdb_path).write_text(PDB.replace('END\n', '') + PDB.replace('ALA A', 'GLY B'))
    await session.commit()
    return session, root, tmp


@pytest.mark.asyncio
async def test_sequence_native_bypasses_redesign_and_uses_independent_target(selected, setup_store):
    session, root, _ = await fixture_root(selected)
    async with setup_store() as experiments:
        result = await rounds.reconcile_round(session, experiments, root.id)
        assert result['state'] == 'running', result
        assert len(result['steps']) == 1
        step = next(iter(result['steps'].values()))
        child = await session.get(Job, step['job_id'])
        assert child.model_id == 'protenix'
        assert child.params['complex_components'] == [
            {'id': 'A', 'type': 'protein', 'sequence': 'A'}, {'id': 'B', 'type': 'protein', 'sequence': 'G'}]
        assert child.params['protenix_n_sample'] == 2
        assert child.params['protenix_use_msa'] is False
        assert not child.params.get('input_pdb')
        assert child.provenance[rounds.STEP]['input_components'][1]['role'] == 'target'
        assert child.provenance[rounds.STEP]['source_design_id'] == 'd0'
        again = await rounds.reconcile_round(session, experiments, root.id)
        assert next(iter(again['steps'].values()))['job_id'] == child.id
        child.status = 'failed'
        await session.commit()
        result = await rounds.reconcile_round(session, experiments, root.id)
        assert result['state'] == 'completed_with_errors'
        assert root.status == 'completed'
        result = await rounds.reconcile_round(session, experiments, root.id, retry=True)
        assert result['state'] == 'running'
        assert len(list(await session.scalars(select(Job).where(Job.model_id == 'protenix')))) == 2
        assert (await session.get(Job, child.id)).status == 'failed'


@pytest.mark.asyncio
async def test_backbone_design_then_every_emitted_sequence_and_zero_yield(selected, setup_store):
    session, root, tmp = await fixture_root(selected, backbone=True)
    async with setup_store() as experiments:
        result = await rounds.reconcile_round(session, experiments, root.id)
        step = next(iter(result['steps'].values()))
        designer = await session.get(Job, step['job_id'])
        assert designer is not None, result
        assert designer.model_id == 'proteinmpnn'
        assert designer.params['seqs_per_design'] == 3
        assert designer.provenance[rounds.STEP]['stage'] == 'sequence_design'
        designer.status = 'completed'
        for number, residue in enumerate(['GLY', 'SER', 'VAL']):
            path = tmp / f'designed-{number}.pdb'
            path.write_text(PDB.replace('ALA', residue))
            session.add(Design(id=f'designed-{number}', name='same', job_id=designer.id,
                parent_design_id='d0', pdb_path=str(path)))
        await session.commit()
        result = await rounds.reconcile_round(session, experiments, root.id)
        predictions = list(await session.scalars(select(Job).where(Job.model_id == 'protenix')))
        assert len(predictions) == 3, result
        assert {c.params['complex_components'][0]['sequence'] for c in predictions} == {'G', 'S', 'V'}
        assert {c.provenance[rounds.STEP]['source_design_id'] for c in predictions} == {f'designed-{i}' for i in range(3)}
        assert all(c.provenance[rounds.STEP]['backbone_design_id'] == 'd0' for c in predictions)


@pytest.mark.asyncio
async def test_remote_review_is_retained_and_get_never_launches(selected, setup_store, monkeypatch):
    session, root, tmp = await fixture_root(selected, remote=True)
    async with setup_store() as experiments:
        first = await rounds.reconcile_round(session, experiments, root.id)
        step = next(iter(first['steps'].values()))
        assert step['state'] == 'review_required', first
        assert step['request']['binder_round_step']['root_job_id'] == root.id
        Path((root.params or {})['target_pdb']).unlink()
        second = await rounds.reconcile_round(session, experiments, root.id)
        assert second['steps'] == first['steps']
        async def fail(*args, **kwargs):
            raise AssertionError('GET cannot submit')
        monkeypatch.setattr(jobs, 'submit_selected_child_jobs', fail)
        response = await selected[0].get(f'/api/binder-continuation/{root.id}/round')
        assert response.status_code == 200
        assert response.json()['steps'] == first['steps']
        # The retained reference is checked independently of target availability.
        request = JobCreate.model_validate(step['request'])
        metadata, child_id, existing = await rounds.bind_step(session, request)
        assert metadata['root_job_id'] == root.id and existing is None
        request.params['protenix_n_sample'] = 19
        with pytest.raises(HTTPException, match='retained request changed'):
            await rounds.bind_step(session, request)
        await session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['omitted', 'disabled', 'cancelled', 'empty'])
async def test_no_retro_launch_cancel_generation_only_zero_yield(selected, setup_store, mode):
    session, root, _ = await fixture_root(selected)
    if mode == 'omitted':
        root.provenance = {}
    elif mode == 'disabled':
        root.provenance = {rounds.REQUEST: envelope(enabled=False)}
    elif mode == 'cancelled':
        root.status = 'cancelled'
    else:
        await session.delete(await session.get(Design, 'd0'))
    await session.commit()
    async with setup_store() as experiments:
        result = await rounds.reconcile_round(session, experiments, root.id)
        assert not result['steps']
        assert result['state'] == {'omitted':'not_requested', 'disabled':'generation_only',
                                  'cancelled':'cancelled', 'empty':'completed'}[mode]


def test_typed_defaults_and_blind_conditioning_are_not_native_generator_params():
    saved = envelope()
    assert saved['sequence_design']['params']['seqs_per_design'] == 3
    assert 'mpnn_temperature' in saved['sequence_design']['params']
    job = Job(id='test', name='test', model_id='boltzgen', mode='protein-anything', status='completed',
              params={}, provenance={rounds.REQUEST:saved})
    assert JobResponse.model_validate(job).binder_round.model_dump(mode='json') == saved
    request = JobCreate(name='test', model_id='boltzgen', mode='protein-anything', params={}, binder_round=saved)
    assert not request.params
    saved['prediction']['params']['protenix_use_template'] = True
    with pytest.raises(ValueError, match='pose conditioning'):
        normalize_request(saved)


@pytest.mark.asyncio
@pytest.mark.parametrize('remote', [False, True])
async def test_project_child_uses_existing_destination_owner(selected, setup_store, remote):
    from test_project_normalized_child_requests import destination
    from experiment_models import ExperimentLaunchContext
    session, root, _ = await fixture_root(selected, remote=remote)
    async with setup_store() as experiments:
        dest = await destination(experiments)
        await experiments.commit()
        root.provenance = {**root.provenance, 'launch_context_id': dest['launch_context_id']}
        await session.commit()
        result = await rounds.reconcile_round(session, experiments, root.id)
        step = next(iter(result['steps'].values()))
        if remote:
            assert step['state'] == 'review_required', result
            context_id = step['request']['launch_context_id']
            assert context_id != dest['launch_context_id']
            assert step['request']['binder_round_step']['root_job_id'] == root.id
        else:
            if step.get('state') == 'error' and 'resource_source_revision_unavailable' in step.get('error', ''):
                assert not list(await session.scalars(select(Job).where(Job.model_id == 'protenix')))
                pytest.skip('Real Project resource admission requires parent release binding; no authority mocked')
            assert step['state'] == 'queued', step.get('error', result)
            child = await session.get(Job, step['job_id'])
            context_id = child.provenance['launch_context_id']
            assert child.provenance[rounds.STEP]['source_design_id'] == 'd0'
        context = await experiments.get(ExperimentLaunchContext, context_id)
        assert context.project_id == dest['project_id']
        assert context.run_attempt_id
        again = await rounds.reconcile_round(session, experiments, root.id)
        assert again['steps'] == result['steps']


@pytest.mark.asyncio
async def test_multistate_multijob_samples_have_deterministic_distinct_children(selected, setup_store):
    session, root, tmp = await fixture_root(selected)
    root.model_id = 'bindcraft2'
    root.params = {'bindcraft2_settings': {'targets': [
        {'name': name, 'target_path': root.params['target_pdb'], 'chains': ['B']} for name in ['state1', 'state2']]}}
    request = deepcopy(root.provenance[rounds.REQUEST])
    request['prediction']['params']['num_parallel_jobs'] = 2
    root.provenance = {rounds.REQUEST: request}
    await session.commit()
    async with setup_store() as experiments:
        result = await rounds.reconcile_round(session, experiments, root.id)
        assert len(result['steps']) == 4, result
        children = list(await session.scalars(select(Job).where(Job.model_id == 'protenix')))
        assert len(children) == 4
        assert {(c.provenance[rounds.STEP]['target_state'], c.provenance[rounds.STEP]['sample_index'])
                for c in children} == {('state1',0), ('state1',1), ('state2',0), ('state2',1)}
        assert all(c.params['protenix_n_sample'] == 2 for c in children)
        for child in children:
            child.status = 'completed'
        await session.commit()
        await rounds.reconcile_round(session, experiments, root.id)
        # Terminal progression stops source materialization entirely.
        Path(root.params['bindcraft2_settings']['targets'][0]['target_path']).unlink()
        result = await rounds.reconcile_round(session, experiments, root.id)
        assert result['state'] == 'completed'


@pytest.mark.asyncio
async def test_partial_submission_failure_retries_only_missing_child(selected, setup_store, monkeypatch):
    session, root, tmp = await fixture_root(selected)
    session.add(Design(id='extra', job_id=root.id, name='extra', artifact_class='binder_complex',
        pdb_path=str(tmp / 'state-0.pdb')))
    await session.commit()
    original = jobs.create_job
    failed = False
    async def fail_once(request, *args, **kwargs):
        nonlocal failed
        if request.params['source_design_id'] == 'extra' and not failed:
            failed = True
            raise HTTPException(422, 'fixture native submission failure')
        return await original(request, *args, **kwargs)
    monkeypatch.setattr(jobs, 'create_job', fail_once)
    async with setup_store() as experiments:
        result = await rounds.reconcile_round(session, experiments, root.id)
        assert result['state'] == 'needs_retry'
        before = set(await session.scalars(select(Job.id).where(Job.model_id == 'protenix')))
        assert len(before) == 1
        result = await rounds.reconcile_round(session, experiments, root.id, retry=True)
        after = set(await session.scalars(select(Job.id).where(Job.model_id == 'protenix')))
        assert len(after) == 2 and before < after
        assert result['state'] == 'running'
        assert (await session.get(Job, 'root')).status == 'completed'


def test_residue_mapping_keeps_author_number_and_insertion_without_conditioning(tmp_path):
    from services.binder_round_inputs import source_components
    path = tmp_path / 'insertions.pdb'
    path.write_text(PDB.replace('A   1 ', 'a  17B'))
    component = source_components(path, ['a'], 'binder')[0]
    assert component['source_residues'] == [{'chain_id': 'a', 'residue_number': 17, 'insertion_code': 'B'}]


@pytest.mark.asyncio
async def test_concurrent_reconciliation_and_cancelled_submission_recover_atomically(selected, setup_store, monkeypatch):
    import asyncio
    from sqlalchemy.ext.asyncio import async_sessionmaker
    session, root, _ = await fixture_root(selected)
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    original = jobs.create_job
    async def cancel_after_insert(*args, **kwargs):
        await original(*args, **kwargs)
        raise asyncio.CancelledError()
    monkeypatch.setattr(jobs, 'create_job', cancel_after_insert)
    async with setup_store() as experiments:
        with pytest.raises(asyncio.CancelledError):
            await rounds.reconcile_round(session, experiments, 'root')
        assert not list(await session.scalars(select(Job).where(Job.model_id == 'protenix')))
        persisted = await rounds.read_round(session, 'root')
        step_id = next(iter(persisted['steps']))
        assert persisted['steps'][step_id]['state'] == 'prepared'
        await session.rollback()
    monkeypatch.setattr(jobs, 'create_job', original)
    async def poll():
        async with factory() as core, setup_store() as experiments:
            return await rounds.reconcile_round(core, experiments, 'root')
    await asyncio.gather(poll(), poll())
    async with factory() as core:
        children = list(await core.scalars(select(Job).where(Job.model_id == 'protenix')))
        assert len(children) == 1 and children[0].id == step_id
        assert children[0].provenance[rounds.STEP]['source_design_id'] == 'd0'


@pytest.mark.asyncio
async def test_crash_after_committed_child_reconciles_by_atomic_step_provenance(selected, setup_store, monkeypatch):
    import asyncio
    session, root, _ = await fixture_root(selected)
    original = jobs.submit_selected_child_jobs
    async def crash_after_commit(*args, **kwargs):
        await original(*args, **kwargs)
        raise asyncio.CancelledError()
    monkeypatch.setattr(jobs, 'submit_selected_child_jobs', crash_after_commit)
    async with setup_store() as experiments:
        with pytest.raises(asyncio.CancelledError):
            await rounds.reconcile_round(session, experiments, 'root')
        children = list(await session.scalars(select(Job).where(Job.model_id == 'protenix')))
        assert len(children) == 1
        child_id = children[0].id
        await session.rollback()
        monkeypatch.setattr(jobs, 'submit_selected_child_jobs', original)
        result = await rounds.reconcile_round(session, experiments, 'root')
        assert next(iter(result['steps'].values()))['job_id'] == child_id
        assert len(list(await session.scalars(select(Job).where(Job.model_id == 'protenix')))) == 1


@pytest.mark.asyncio
async def test_create_and_resubmit_preserve_round_envelope(selected):
    from fastapi import Request, Response
    session = selected[1]
    request = JobCreate(name='round-request', model_id='protenix', mode='complex',
        params={'sequence': 'AG', 'protenix_use_msa': False}, binder_round=envelope(enabled=False))
    response = await jobs.create_job(request, BackgroundTasks(), session)
    created = await session.get(Job, response.id)
    assert created.provenance[rounds.REQUEST] == envelope(enabled=False)
    assert 'binder_round' not in created.params
    created.status = 'failed'
    await session.commit()
    response = await jobs.resubmit_job(created.id, Request({'type':'http', 'headers':[]}), Response(), session)
    copies = list(await session.scalars(select(Job).where(Job.name == 'round-request_resubmit')))
    assert len(copies) == 1
    assert copies[0].provenance[rounds.REQUEST] == envelope(enabled=False)
    assert rounds.PROGRESS not in copies[0].provenance


@pytest.mark.asyncio
@pytest.mark.parametrize('designer,count_key', [('fampnn','seqs_per_design'), ('caliby_binder','caliby_num_seqs_per_pdb')])
async def test_other_approved_designers_keep_native_count_and_zero_yield(selected, setup_store, designer, count_key):
    session, root, _ = await fixture_root(selected, backbone=True)
    request = envelope(sequence_design={'model_id': designer, 'params': {count_key: 5}})
    root.provenance = {rounds.REQUEST: request}
    await session.commit()
    async with setup_store() as experiments:
        result = await rounds.reconcile_round(session, experiments, 'root')
        step = next(iter(result['steps'].values()))
        assert step['state'] == 'queued', result
        child = await session.get(Job, step['job_id'])
        assert child.model_id == designer and child.params[count_key] == 5
        child.status = 'completed'
        await session.commit()
        result = await rounds.reconcile_round(session, experiments, 'root')
        assert result['state'] == 'completed'
        assert len(result['steps']) == 1


@pytest.mark.asyncio
async def test_scheduler_recovers_predictions_and_skips_terminal_input_work(selected, setup_store, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from services import analysis_autorun
    session, root, _ = await fixture_root(selected)
    async with setup_store() as experiments:
        result = await rounds.reconcile_round(session, experiments, root.id)
        child_id = next(iter(result['steps'].values()))['job_id']
        child = await session.get(Job, child_id)
        child.status = 'completed'
        await session.commit()
        result = await rounds.reconcile_round(session, experiments, root.id)
        assert result['state'] == 'completed'
    calls = []
    monkeypatch.setattr(analysis_autorun, 'schedule_viewer_minimum_analyses_for_job', lambda id: calls.append(id))
    async def forbidden(*args, **kwargs):
        raise AssertionError('terminal rounds do not materialize sources')
    monkeypatch.setattr(rounds, 'reconcile_round', forbidden)
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    await rounds.recover_rounds(factory, setup_store)
    assert calls == [child_id]
