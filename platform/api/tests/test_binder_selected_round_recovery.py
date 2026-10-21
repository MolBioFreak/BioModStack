"""Real retained-plan and canonical insertion boundaries; no science execution."""
import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import Job
from routers import jobs
from services import binder_round as rounds, binder_selected_round as selected_round
from test_binder_continuation import selected
from test_project_workflow_setups import setup_store
from test_binder_selected_round import source, request


@pytest.mark.asyncio
@pytest.mark.parametrize('after_commit', [False, True])
async def test_interrupted_insertion_completed_native_child_recovers(selected, setup_store, monkeypatch, after_commit):
    from services import protonpottsmpnn_design as native, analysis_autorun
    from services.protonpottsmpnn_publication import publish_native_results
    client, session, root, tmp = await source(selected)
    payload = request('interrupted')
    payload['round_plan']['stages'][0]['models'] = [{'model_id': 'protonpottsmpnn', 'params': {'binder_chain': 'A'}}]
    payload['round_plan']['stages'][1]['models'] = payload['round_plan']['stages'][1]['models'][:1]
    original = jobs.submit_selected_child_jobs
    async def interrupted(*args, **kwargs):
        if after_commit:
            await original(*args, **kwargs)
        raise asyncio.CancelledError('process interrupted at insertion boundary')
    monkeypatch.setattr(jobs, 'submit_selected_child_jobs', interrupted)
    with pytest.raises(asyncio.CancelledError):
        await client.post('/api/binder-continuation/selected', json=payload)
    await session.rollback()
    root = await session.get(Job, 'root', populate_existing=True)
    retained = rounds.progress_of(root)['selected_rounds'][0]
    identity = retained['round_id']
    assert all(s['job_id'] is None for s in retained['steps'].values())
    children = list(await session.scalars(select(Job).where(Job.provenance[rounds.STEP]['round_id'].as_string() == identity)))
    assert len(children) == int(after_commit)
    monkeypatch.setattr(jobs, 'submit_selected_child_jobs', original)
    sessions = async_sessionmaker(session.bind, expire_on_commit=False)
    monkeypatch.setattr(analysis_autorun, 'schedule_viewer_minimum_analyses_for_job', lambda *a: None)
    await session.rollback()
    if not after_commit:
        await rounds.recover_rounds(sessions, setup_store)
    producer = await session.scalar(select(Job).where(Job.model_id == 'protonpottsmpnn'))
    folder = Path(producer.output_dir) / native.DIRECTORY
    folder.mkdir(parents=True, exist_ok=True)
    document = {'contract': native.CONTRACT, 'request': native.read_prepared_request(producer.mode, producer.params),
        'designs': [{'design_id': 'criteria0-s0', 'criteria_index': 0, 'native_design_id': 's0',
                     'native': {'binder_chain': 'A', 'canonical_sequence': 'HD'}}], 'artifacts': ['manifest.json']}
    document['source'] = document['request']['source']
    (folder / 'manifest.json').write_text(json.dumps(document))
    await publish_native_results(producer, Path(producer.output_dir), session)
    producer.status = 'completed'
    await session.commit()
    await rounds.recover_rounds(sessions, setup_store)
    await rounds.recover_rounds(sessions, setup_store)
    async with sessions() as fresh:
        children = list(await fresh.scalars(select(Job).where(Job.provenance[rounds.STEP]['round_id'].as_string() == identity)))
        assert len(children) == 2
        predictor = next(c for c in children if c.model_id == 'protenix')
        assert predictor.params['complex_components'][0]['sequence'] == 'HD'
        assert predictor.provenance[rounds.STEP]['parent_step_ids'] == [next(c.id for c in children if c.model_id == 'protonpottsmpnn')]


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['failed', 'cancelled'])
async def test_failed_root_before_tick_get_is_truthful_and_readonly(selected, status):
    from test_binder_round_orchestration import envelope
    client, session, root, tmp = await source(selected)
    root.status = status
    root.provenance = {rounds.REQUEST: envelope()}
    await session.commit()
    writes = []
    def observe(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE'}:
            writes.append(statement)
    event.listen(session.bind.sync_engine, 'before_cursor_execute', observe)
    try:
        response = await client.get('/api/binder-continuation/root/round')
        assert response.status_code == 200
        assert response.json()['state'] == status
        assert response.json()['outcome'] == 'no_follow_on'
        assert not writes
    finally:
        event.remove(session.bind.sync_engine, 'before_cursor_execute', observe)


@pytest.mark.asyncio
@pytest.mark.parametrize('publication', ['missing', 'empty', 'corrupt'])
async def test_publication_disposition_is_branch_local(selected, setup_store, publication):
    from services import protonpottsmpnn_design as native
    from services.protonpottsmpnn_publication import publish_native_results
    client, session, root, tmp = await source(selected)
    payload = request('publication-' + publication)
    independent = payload['round_plan']['stages'][0].copy()
    independent['id'] = 'independent'
    payload['round_plan']['stages'][0] = {**independent, 'id': 'redesign', 'models': [
        {'model_id': 'protonpottsmpnn', 'params': {'binder_chain': 'A'}}]}
    payload['round_plan']['stages'].append(independent)
    response = await client.post('/api/binder-continuation/selected', json=payload)
    assert response.status_code == 201, response.text
    identity = response.json()['round_id']
    producer = await session.scalar(select(Job).where(Job.model_id == 'protonpottsmpnn'))
    sibling = await session.scalar(select(Job).where(Job.model_id == 'proteinmpnn'))
    sibling_id = sibling.id
    folder = Path(producer.output_dir) / native.DIRECTORY
    if publication != 'missing':
        folder.mkdir(parents=True, exist_ok=True)
        document = {'contract': native.CONTRACT, 'request': native.read_prepared_request(producer.mode, producer.params),
            'designs': [], 'artifacts': ['manifest.json']}
        document['source'] = document['request']['source']
        (folder / 'manifest.json').write_text(json.dumps(document))
        await publish_native_results(producer, Path(producer.output_dir), session)
        if publication == 'corrupt':
            (folder / 'manifest.json').write_text('{broken')
    producer.status = sibling.status = 'completed'
    await session.commit()
    async with setup_store() as experiments:
        result = await selected_round.reconcile(session, experiments, 'root', identity)
    disposition = next(iter(result['dependencies'].values()))
    assert disposition['state'] == {'missing': 'awaiting_publication', 'empty': 'valid_empty', 'corrupt': 'publication_error'}[publication]
    assert result['state'] == {'missing': 'running', 'empty': 'completed', 'corrupt': 'needs_retry'}[publication]
    assert len(result['steps']) == 2
    assert (await session.get(Job, sibling_id)).status == 'completed'
    assert (await session.get(Job, 'root')).status == 'cancelled'
    read = await client.get('/api/binder-continuation/root/round')
    assert read.status_code == 200, read.text
    if publication == 'empty':
        # Fully expanded terminal progress has no dependency on producer bytes.
        (folder / 'manifest.json').unlink()
        async with setup_store() as experiments:
            again = await selected_round.reconcile(session, experiments, 'root', identity)
        assert again['state'] == 'completed'
        assert again['dependencies'] == result['dependencies']


@pytest.mark.asyncio
async def test_corrupt_publication_does_not_block_prepared_sibling(selected, setup_store, monkeypatch):
    from services import protonpottsmpnn_design as native
    from services.protonpottsmpnn_publication import publish_native_results
    client, session, root, tmp = await source(selected)
    payload = request('corrupt-with-prepared-sibling')
    independent = {**payload['round_plan']['stages'][0], 'id': 'independent'}
    payload['round_plan']['stages'][0] = {**independent, 'id': 'redesign', 'models': [
        {'model_id': 'protonpottsmpnn', 'params': {'binder_chain': 'A'}}]}
    payload['round_plan']['stages'].append(independent)
    original = jobs.submit_selected_child_jobs
    async def interrupted(*args, **kwargs):
        await original(*args, **kwargs)
        raise asyncio.CancelledError()
    monkeypatch.setattr(jobs, 'submit_selected_child_jobs', interrupted)
    with pytest.raises(asyncio.CancelledError):
        await client.post('/api/binder-continuation/selected', json=payload)
    await session.rollback()
    monkeypatch.setattr(jobs, 'submit_selected_child_jobs', original)
    root = await session.get(Job, 'root')
    retained = rounds.progress_of(root)['selected_rounds'][0]
    assert len(retained['steps']) == 2
    assert await session.scalar(select(Job).where(Job.model_id == 'proteinmpnn')) is None
    producer = await session.scalar(select(Job).where(Job.model_id == 'protonpottsmpnn'))
    folder = Path(producer.output_dir) / native.DIRECTORY
    folder.mkdir(parents=True, exist_ok=True)
    document = {'contract': native.CONTRACT, 'request': native.read_prepared_request(producer.mode, producer.params),
        'designs': [], 'artifacts': ['manifest.json']}
    document['source'] = document['request']['source']
    (folder / 'manifest.json').write_text(json.dumps(document))
    await publish_native_results(producer, Path(producer.output_dir), session)
    (folder / 'manifest.json').write_text('{broken')
    producer.status = 'completed'
    await session.commit()
    async with setup_store() as experiments:
        result = await selected_round.reconcile(session, experiments, 'root', retained['round_id'])
        assert result['state'] == 'needs_retry'
        assert next(iter(result['dependencies'].values()))['state'] == 'publication_error'
        sibling = await session.scalar(select(Job).where(Job.model_id == 'proteinmpnn'))
        assert sibling is not None and sibling.status == 'queued'
        (folder / 'manifest.json').write_text(json.dumps(document))
        repaired = await selected_round.reconcile(session, experiments, 'root', retained['round_id'], retry=True)
        assert repaired['state'] == 'running'
        assert len(list(await session.scalars(select(Job).where(Job.model_id == 'proteinmpnn')))) == 1
        assert not repaired['errors']


@pytest.mark.asyncio
async def test_proton_roles_bound_before_native_validation(selected):
    from database import Design
    client, session, root, tmp = await source(selected)
    design = await session.get(Design, 'd0')
    path = Path(design.pdb_path)
    path.write_text(path.read_text().replace('ALA A', 'ALA Q'))
    design.provenance = {**design.provenance, 'binder_chains': ['Q'], 'target_chains': ['B']}
    await session.commit()
    payload = request('non-a-role')
    payload['round_plan']['stages'] = payload['round_plan']['stages'][:1]
    payload['round_plan']['stages'][0]['models'] = [{'model_id': 'protonpottsmpnn', 'params': {}}]
    response = await client.post('/api/binder-continuation/selected', json=payload)
    assert response.status_code == 201, response.text
    assert response.json()['state'] == 'running', response.text
    child = await session.get(Job, response.json()['launched_jobs'][0]['id'])
    assert child.params['binder_chain'] == 'Q'


@pytest.mark.parametrize('explicit', [{}, {'fampnn_temperature': 0.2}])
def test_migrate_supplied_sequence_settings_before_defaults(explicit):
    from schemas import SelectedRoundRequest
    payload = request()
    payload['round_plan']['stages'] = payload['round_plan']['stages'][:1]
    payload['round_plan']['stages'][0]['models'] = [{'model_id': 'fampnn', 'params': {
        'fampnn_extra_config': 'temperature=0.71 exclude_cys=false psce_threshold=null unrelated=7', **explicit}}]
    params = selected_round.normalize(SelectedRoundRequest.model_validate(payload)).round_plan.stages[0].models[0].params
    assert params['fampnn_temperature'] == explicit.get('fampnn_temperature', 0.71)
    assert params['fampnn_exclude_cys'] is False
    assert params['fampnn_psce_threshold'] is None
    assert params['fampnn_extra_config'] == 'unrelated=7'
