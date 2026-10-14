"""Real scratch SQLite/router receiving. Scientific executables never run."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import select

from database import Design, Job
from routers import jobs
from schemas import SelectedRoundRequest
from services import binder_round as rounds
from services import binder_selected_round as selected_round
from test_binder_continuation import selected, PDB
from test_project_workflow_setups import setup_store


def request(key='intent-1'):
    return {'operation': 'round', 'source_job_id': 'root', 'idempotency_key': key,
        'selection': {'candidate_refs': [{'kind': 'design', 'job_id': 'root', 'design_id': 'd0'}]},
        'execution_target_id': None,
        'round_plan': {'schema_version': 1, 'stages': [
            {'id': 'redesign', 'input': {'selected': True}, 'kind': 'sequence_design',
             'models': [{'model_id': 'proteinmpnn', 'params': {'design_chain': 'A', 'seqs_per_design': 2, 'mpnn_relax_max_cycles': 0}}]},
            {'id': 'blind', 'input': {'stage_id': 'redesign'}, 'kind': 'prediction',
             'models': [{'model_id': 'protenix', 'params': {'protenix_use_msa': False, 'protenix_n_sample': 2}},
                        {'model_id': 'boltz2', 'params': {'boltz_use_msa': False}}]}]}}


async def source(selected):
    client, session, root, _, tmp = selected
    target = tmp / 'independent.pdb'
    target.write_text(PDB.replace('ALA A', 'GLY B'))
    root.model_id = 'boltzgen'
    root.status = 'cancelled'  # Historical root lifecycle must not cancel a new intent.
    root.execution_target_id = 'vast:old-deleted-source'
    root.params = {'target_pdb': str(target)}
    d = await session.get(Design, 'd0')
    d.artifact_class = 'binder_complex'
    d.provenance = {**d.provenance, 'binder_chains': ['A'], 'target_chains': ['B']}
    await session.commit()
    return client, session, root, tmp


def evidence(name, payload):
    directory = os.environ.get('BMS_SELECTED_ROUND_EVIDENCE')
    if directory:
        Path(directory).mkdir(parents=True, exist_ok=True)
        (Path(directory) / name).write_text(json.dumps(payload, indent=2, default=str))


@pytest.mark.asyncio
async def test_http_redesign_every_output_multiple_predictors_repeat_and_replay(selected, setup_store):
    client, session, root, tmp = await source(selected)
    payload = request()
    result = await client.post('/api/binder-continuation/selected', json=payload)
    assert result.status_code == 201, result.text
    first = result.json()
    evidence('submitted.json', first)
    assert first['state'] == 'running', first
    assert first['selected_design_count'] == 1
    assert len(first['launched_jobs']) == 1
    designer = await session.get(Job, first['launched_jobs'][0]['id'])
    assert designer.model_id == 'proteinmpnn'  # Explicit redesign of sequence-bearing input.
    assert designer.execution_target_id is None
    assert designer.params['design_chain'] == 'A'
    designer.status = 'completed'
    (tmp / 'independent.pdb').unlink()  # Descendants use the frozen independent target.
    for i, residue in enumerate(['SER', 'VAL']):
        path = tmp / f'new-{i}.pdb'
        path.write_text(PDB.replace('ALA', residue))
        session.add(Design(id=f'new-{i}', job_id=designer.id, name='same native display name',
            pdb_path=str(path), artifact_class='designed_structure', parent_design_id='d0',
            provenance={'binder_chains': ['A'], 'target_chains': ['B']}))
    await session.commit()
    async with setup_store() as experiments:
        returned = await selected_round.reconcile(session, experiments, 'root', first['round_id'])
    assert len(returned['steps']) == 5, returned
    prediction_steps = [s for s in returned['steps'] if s['stage'] == 'prediction']
    assert len(prediction_steps) == 4
    evidence('progressed.json', returned)
    for step in prediction_steps:
        assert step['job_id'], step
        child = await session.get(Job, step['job_id'])
        assert child.params['complex_components'][1]['sequence'] == 'G'
        assert child.params['complex_components'][0]['sequence'] in {'S', 'V'}
        assert not child.params.get('input_pdb')
        assert step['source_ref']['design_id'] in {'new-0', 'new-1'}
        assert step['parent_step_ids'] == [first['round']['steps'][0]['step_id']]
    replay = await client.post('/api/binder-continuation/selected', json=payload)
    assert replay.status_code == 201, replay.text
    assert replay.json()['round_id'] == first['round_id']
    payload['round_plan']['stages'][0]['models'][0]['params']['seqs_per_design'] = 3
    conflict = await client.post('/api/binder-continuation/selected', json=payload)
    assert conflict.status_code == 409
    for n in [2, 3]:
        next_request = request(f'intent-{n}')
        next_request['selection']['candidate_refs'] = [{'kind': 'design', 'job_id': designer.id, 'design_id': 'new-0'}]
        next_request['round_plan']['stages'] = next_request['round_plan']['stages'][:1]
        submitted = await client.post('/api/binder-continuation/selected', json=next_request)
        assert submitted.status_code == 201, submitted.text
        assert submitted.json()['state'] == 'running', submitted.text
    read = await client.get('/api/binder-continuation/root/round')
    assert read.status_code == 200
    body = read.json()
    evidence('round-readback.json', body)
    assert len(body['selected_rounds']) == 3
    assert body['steps'] == {} and body['state'] == 'not_requested'
    assert (await session.get(Job, 'root')).status == 'cancelled'
    assert len(list(await session.scalars(select(Job).where(Job.provenance[rounds.STEP]['round_id'].as_string().is_not(None))))) == 7


@pytest.mark.asyncio
async def test_remote_preparation_and_crash_after_committed_insert(selected, setup_store, monkeypatch):
    client, session, root, tmp = await source(selected)
    remote = request('remote')
    remote['execution_target_id'] = 'vast:new-worker'
    result = await client.post('/api/binder-continuation/selected', json=remote)
    assert result.status_code == 201, result.text
    assert result.json()['state'] == 'review_required', result.text
    step = result.json()['round']['steps'][0]
    assert step['review']['response_context']['round_id'] == result.json()['round_id'] if 'response_context' in step['review'] else step['review']['round_id'] == result.json()['round_id']
    evidence('remote-review.json', result.json())
    original = jobs.submit_selected_child_jobs
    async def lost_response(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError('lost post-commit response')
    monkeypatch.setattr(jobs, 'submit_selected_child_jobs', lost_response)
    local = request('crash')
    result = await client.post('/api/binder-continuation/selected', json=local)
    assert result.status_code == 201, result.text
    identity = result.json()['round_id']
    monkeypatch.setattr(jobs, 'submit_selected_child_jobs', original)
    retry = await client.post('/api/binder-continuation/selected', json=local)
    assert retry.status_code == 201, retry.text
    assert retry.json()['round_id'] == identity
    children = list(await session.scalars(select(Job).where(Job.provenance[rounds.STEP]['round_id'].as_string() == identity)))
    assert len(children) == 1


@pytest.mark.asyncio
async def test_saved_set_exact_membership_ranking_final_and_legacy_selectors(selected):
    client, session, root, tmp = await source(selected)
    client._transport.app.include_router(jobs.router, prefix='/api/jobs')
    saved = await client.post('/api/jobs/root/review-filter-sets', json={
        'name': 'Selected rejected and unranked', 'candidate_refs': request()['selection']['candidate_refs'],
        'revision': 2, 'ranking': {'metric': 'native', 'direction': 'desc', 'missing': 'last'},
        'final_designation': {'final': True}})
    assert saved.status_code == 200, saved.text
    entry = saved.json()['filter_set']
    assert entry['ranking']['missing'] == 'last'
    session.expire_all()
    fresh = await session.get(Job, 'root')
    assert fresh.saved_selection_sets[0]['final_designation'] == {'final': True}
    payload = request()
    payload['selection'] = {'saved_set_id': entry['id'], 'revision': 2}
    result = await client.post('/api/binder-continuation/selected', json=payload)
    assert result.status_code == 201, result.text
    assert result.json()['selected_design_count'] == 1
    payload = request('legacy-selector')
    payload['design_ids'] = ['d0']
    result = await client.post('/api/binder-continuation/selected', json=payload)
    assert result.status_code == 201, result.text
    assert len(result.json()['round']['steps']) == 1
    payload['idempotency_key'] = 'disagree'
    payload['design_ids'] = ['d1']
    result = await client.post('/api/binder-continuation/selected', json=payload)
    assert result.status_code == 422


@pytest.mark.asyncio
async def test_native_sequence_publication_fans_out_without_fake_designs(selected, setup_store):
    from services import protonpottsmpnn_design as native
    from services.protonpottsmpnn_publication import publish_native_results
    client, session, root, tmp = await source(selected)
    payload = request('native-sequences')
    payload['round_plan']['stages'][0]['models'] = [{'model_id': 'protonpottsmpnn', 'params': {'binder_chain': 'A'}}]
    result = await client.post('/api/binder-continuation/selected', json=payload)
    assert result.status_code == 201, result.text
    body = result.json()
    assert body['state'] == 'running', body
    producer = await session.get(Job, body['launched_jobs'][0]['id'])
    folder = Path(producer.output_dir) / native.DIRECTORY
    folder.mkdir(parents=True, exist_ok=True)
    document = {'contract': native.CONTRACT, 'request': native.read_prepared_request(producer.mode, producer.params),
        'designs': [{'design_id': f'criteria0-s{i}', 'criteria_index': 0, 'native_design_id': f's{i}',
                     'native': {'binder_chain': 'A', 'canonical_sequence': sequence}}
                    for i, sequence in enumerate(['HD', 'DE'])], 'artifacts': ['manifest.json']}
    document['source'] = document['request']['source']
    (folder / 'manifest.json').write_text(json.dumps(document))
    await publish_native_results(producer, Path(producer.output_dir), session)
    producer.status = 'completed'
    await session.commit()
    async with setup_store() as experiments:
        returned = await selected_round.reconcile(session, experiments, 'root', body['round_id'])
    assert len(returned['steps']) == 5, returned
    assert not list(await session.scalars(select(Design).where(Design.job_id == producer.id)))
    native_refs = returned['steps'][0]['outputs']
    assert len(native_refs) == 2
    assert all(r['kind'] == 'native_sequence' for r in native_refs)
    for step in returned['steps'][1:]:
        assert step['state'] == 'queued', step
        child = await session.get(Job, step['job_id'])
        assert child.params['complex_components'][0]['sequence'] in {'HD', 'DE'}
        assert child.params['complex_components'][1]['sequence'] == 'G'
        assert child.params['iteration_source_design_ids'] == []
    repeat = request('native-requeue')
    repeat['selection']['candidate_refs'] = [native_refs[1]]
    repeat['round_plan']['stages'] = [repeat['round_plan']['stages'][1]]
    repeat['round_plan']['stages'][0]['input'] = {'selected': True}
    queued = await client.post('/api/binder-continuation/selected', json=repeat)
    assert queued.status_code == 201, queued.text
    assert queued.json()['selected_sequence_count'] == 1
    assert len(queued.json()['launched_jobs']) == 2
    evidence('native-sequence-requeue.json', queued.json())


@pytest.mark.asyncio
async def test_explicit_maturation_and_parallel_designers_are_not_inserted_or_skipped(selected):
    client, session, root, tmp = await source(selected)
    payload = request('optional')
    payload['round_plan']['stages'] = [
        {'id': 'mature-only', 'input': {'selected': True}, 'kind': 'maturation',
         'model_id': 'binder_refinement', 'mode': 'refine', 'params': {
             'binder_chains': 'A', 'target_chains': 'B', 'maturation_repack_enabled': True,
             'maturation_flow_enabled': False, 'maturation_anchors_enabled': False}},
        payload['round_plan']['stages'][0]]
    payload['round_plan']['stages'][1]['models'].append({'model_id': 'fampnn', 'params': {'design_chain': 'A', 'target_chain': 'B'}})
    result = await client.post('/api/binder-continuation/selected', json=payload)
    assert result.status_code == 201, result.text
    assert {s['model_id'] for s in result.json()['round']['steps']} == {'binder_refinement', 'proteinmpnn', 'fampnn'}
    assert all(s['parent_step_ids'] == [] for s in result.json()['round']['steps'])
    assert len(result.json()['launched_jobs']) == 3, result.text


@pytest.mark.asyncio
async def test_maturation_output_roles_masked_backbone_and_explicit_post_flow_design(selected, setup_store):
    client, session, root, tmp = await source(selected)
    payload = request('maturation-roles')
    designer = payload['round_plan']['stages'][0]
    designer['input'] = {'stage_id': 'mature'}
    designer['models'][0]['params'].pop('design_chain')
    payload['round_plan']['stages'].insert(0, {
        'id': 'mature', 'input': {'selected': True}, 'kind': 'maturation',
        'model_id': 'binder_refinement', 'mode': 'refine', 'params': {
            'maturation_repack_enabled': True, 'maturation_flow_enabled': False,
            'maturation_anchors_enabled': False}})
    result = await client.post('/api/binder-continuation/selected', json=payload)
    assert result.status_code == 201, result.text
    maturation = await session.get(Job, result.json()['launched_jobs'][0]['id'])
    maturation.status = 'completed'
    # The native publication receiver's exact output classes/roles, not source
    # roles. These inert coordinates are not a claim of actual flow inference.
    path = tmp / 'flow-output.pdb'
    path.write_text(PDB.replace('END\n', '').replace('ALA A', 'GLY A') + PDB.replace('ALA A', 'ALA B'))
    session.add(Design(id='flow-output', name='flow sample', job_id=maturation.id,
        pdb_path=str(path), artifact_class='backbone_complex', provenance={
            'binder_chains': ['B'], 'target_chains': ['A'],
            'sequence_semantics': 'movable_positions_masked_alanine'}))
    await session.commit()
    (tmp / 'independent.pdb').unlink()
    async with setup_store() as experiments:
        retained = await selected_round.reconcile(session, experiments, 'root', result.json()['round_id'])
    assert len(retained['steps']) == 2, retained
    step = retained['steps'][1]
    child = await session.get(Job, step['job_id'])
    assert child.model_id == 'proteinmpnn'
    assert child.params['design_chain'] == 'B'
    assert step['independent_targets'][0]['components'][0]['sequence'] == 'G'
    assert step['source_ref']['design_id'] == 'flow-output'
    assert not any(s['stage'] == 'prediction' for s in retained['steps'])


@pytest.mark.asyncio
async def test_readback_has_every_retained_output_beyond_top_thirty(selected, setup_store):
    client, session, root, tmp = await source(selected)
    payload = request('all-outputs')
    payload['round_plan']['stages'] = payload['round_plan']['stages'][:1]
    response = await client.post('/api/binder-continuation/selected', json=payload)
    assert response.status_code == 201, response.text
    producer = await session.get(Job, response.json()['launched_jobs'][0]['id'])
    producer.status = 'failed'  # Available partial output is still an output.
    for index in range(35):
        path = tmp / f'partial-{index}.pdb'
        path.write_text(PDB)
        session.add(Design(id=f'partial-{index:03}', job_id=producer.id, name='same', pdb_path=str(path)))
    await session.commit()
    read = await client.get('/api/binder-continuation/root/round')
    outputs = read.json()['selected_rounds'][0]['steps'][0]['outputs']
    assert len(outputs) == 35
    assert {r['design_id'] for r in outputs} == {f'partial-{i:03}' for i in range(35)}
    from services.binder_evidence import binder_evidence
    projected = await binder_evidence(session, root, offset=0, limit=50)
    assert projected['selected_rounds'][0]['steps'][0]['outputs'] == outputs


@pytest.mark.asyncio
async def test_exact_alternate_document_and_model_retains_native_bytes(selected, monkeypatch):
    from test_binder_continuation import alternate_document_fixture
    client, session, root, tmp = await source(selected)
    import paths
    monkeypatch.setattr(paths, 'get_inputs_dir', lambda: tmp / 'inputs')
    path, original, original_digest = await alternate_document_fixture(selected)
    payload = request('exact-model')
    payload['selection']['candidate_refs'] = [{'kind': 'design', 'job_id': 'round1', 'design_id': 'd1',
        'document': {'artifact_id': 'alternate', 'target_state': 'alternate-state', 'model': 1}}]
    payload['round_plan']['stages'] = payload['round_plan']['stages'][:1]
    payload['round_plan']['stages'][0]['models'][0]['params']['design_chain'] = 'a'
    result = await client.post('/api/binder-continuation/selected', json=payload)
    assert result.status_code == 201, result.text
    assert result.json()['state'] == 'running', result.text
    child = await session.get(Job, result.json()['launched_jobs'][0]['id'])
    manifest = json.loads(Path(child.params['selected_input_manifest']).read_text())
    item = manifest['designs'][0]
    assert item['selected_model'] == 1
    assert item['selected_document']['artifact_id'] == 'alternate'
    assert Path(item['native_source_structure_path']).read_bytes() == original
    assert item['native_source_structure_sha256'] == original_digest
    assert Path(child.params['input_pdb']).is_file()
    assert (await session.get(Design, 'd1')).pdb_path == str(tmp / 'state-1.pdb')


@pytest.mark.asyncio
async def test_bc2_passed_rejected_native_and_bodyless_observation_sets(selected):
    from test_bindcraft2_publication import campaign
    from services.result_ingester import ingest_job_results
    from database import JobArtifact
    client, session, root, tmp = await source(selected)
    directory = tmp / 'bc2'
    campaign(directory)
    # The accounting fixture's CIF headers are not molecular bodies. Supply a
    # genuine inert structure before publication so the real converter runs.
    from Bio.PDB import MMCIFIO, PDBParser
    from Bio.PDB.MMCIF2Dict import MMCIF2Dict
    import io
    structure = PDBParser(QUIET=True).get_structure('fixture', io.StringIO(PDB.replace('END\n', '') + PDB.replace('ALA A', 'CYS B')))
    for path in (directory / '3_Ranked').glob('*.cif'):
        stamps = {k:v for k,v in MMCIF2Dict(str(path)).items() if k.startswith('_bindcraft.')}
        writer = MMCIFIO()
        writer.set_structure(structure)
        writer.save(str(path))
        document = MMCIF2Dict(str(path))
        document.update(stamps)
        writer.set_dict(document)
        writer.save(str(path))
    (directory / '2_Refolded' / 't_candidate1.pdb').write_text(PDB)
    owner = Job(id='bc2', name='BC2', model_id='bindcraft2', mode='campaign', status='completed',
                params={}, output_dir=str(directory))
    session.add(owner)
    await session.commit()
    await ingest_job_results(owner.id, str(directory), session)
    native = await session.scalar(select(JobArtifact).where(JobArtifact.owner_job_id == 'bc2',
        JobArtifact.logical_path == 'bindcraft2/native/2_Refolded/t_candidate1.pdb'))
    table = await session.scalar(select(JobArtifact).where(JobArtifact.owner_job_id == 'bc2',
        JobArtifact.logical_path == 'bindcraft2/native/2_Refolded/!_Refolded.csv'))
    accepted = await session.scalar(select(Design).where(Design.job_id == 'bc2'))
    payload = request('bc2-explicit-redesign')
    payload['source_job_id'] = 'bc2'
    payload['round_plan']['stages'][0]['models'][0]['params'].pop('design_chain')
    payload['selection']['candidate_refs'] = [
        {'kind': 'design', 'job_id': 'bc2', 'design_id': accepted.id},
        {'kind': 'native_document', 'job_id': 'bc2', 'artifact_id': native.id}]
    payload['round_plan']['stages'] = payload['round_plan']['stages'][:1]
    submitted = await client.post('/api/binder-continuation/selected', json=payload)
    assert submitted.status_code == 201, submitted.text
    assert len(submitted.json()['launched_jobs']) == 2, submitted.text
    accepted_child = await session.get(Job, submitted.json()['launched_jobs'][0]['id'])
    assert accepted_child.params['design_chain'] == 'B'
    assert submitted.json()['selected_design_count'] == submitted.json()['selected_native_count'] == 1
    assert len(list(await session.scalars(select(Design).where(Design.job_id == 'bc2')))) == 1
    client._transport.app.include_router(jobs.router, prefix='/api/jobs')
    observation = {'kind': 'native_observation', 'job_id': 'bc2', 'publication_ref': table.id,
                   'native_record_id': 't_candidate1'}
    saved = await client.post('/api/jobs/bc2/review-filter-sets', json={'candidate_refs': [observation],
        'ranking': {'order': ['t_candidate1']}, 'final_designation': {'final': False}})
    assert saved.status_code == 200, saved.text
    assert saved.json()['filter_set']['candidate_refs'][0]['native_record_id'] == 't_candidate1'
    evidence('bc2-selected-redesign.json', submitted.json())


@pytest.mark.asyncio
async def test_recovery_retry_fresh_observational_read_and_legacy_isolation(selected, setup_store, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlalchemy import event
    from services import analysis_autorun
    from test_binder_round_orchestration import envelope
    client, session, root, tmp = await source(selected)
    payload = request('recover')
    payload['round_plan']['stages'] = payload['round_plan']['stages'][:1]
    result = await client.post('/api/binder-continuation/selected', json=payload)
    assert result.status_code == 201
    identity = result.json()['round_id']
    child_id = result.json()['launched_jobs'][0]['id']
    child = await session.get(Job, child_id)
    child.status = 'failed'
    await session.commit()
    sessions = async_sessionmaker(session.bind, expire_on_commit=False)
    monkeypatch.setattr(analysis_autorun, 'schedule_viewer_minimum_analyses_for_job', lambda *args: None)
    await rounds.recover_rounds(sessions, setup_store)
    session.expire_all()
    read = await client.get('/api/binder-continuation/root/round', params={'round_id': identity})
    assert read.json()['selected_rounds'][0]['state'] == 'completed_with_errors'
    retry = await client.post('/api/binder-continuation/root/round/retry', params={'round_id': identity})
    assert retry.status_code == 200, retry.text
    steps = retry.json()['steps']
    assert len(steps) == 2
    old, replacement = steps
    assert old['state'] == 'failed'
    assert replacement['retry_of_job_id'] == child_id
    assert replacement['job_id'] != child_id
    assert (await session.get(Job, child_id)).status == 'failed'
    writes = []
    def observe(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split()[0].upper() in {'UPDATE', 'INSERT', 'DELETE'}:
            writes.append(statement)
    event.listen(session.bind.sync_engine, 'before_cursor_execute', observe)
    try:
        async with sessions() as fresh:
            projected = await rounds.read_round(fresh, 'root', round_id=identity)
            assert len(projected['selected_rounds']) == 1
            assert len(projected['selected_rounds'][0]['steps']) == 2
        assert not writes
    finally:
        event.remove(session.bind.sync_engine, 'before_cursor_execute', observe)
    # The initial v1 planner must ignore selected sequence-design descendants.
    root = await session.get(Job, 'root')
    root.status = 'completed'
    root.provenance = {**root.provenance, rounds.REQUEST: envelope()}
    replacement_job = await session.get(Job, replacement['job_id'])
    replacement_job.status = 'completed'
    path = tmp / 'selected-descendant.pdb'
    path.write_text(PDB)
    session.add(Design(id='selected-descendant', job_id=replacement_job.id, name='selected descendant',
                       pdb_path=str(path), artifact_class='designed_structure'))
    await session.commit()
    async with setup_store() as experiments:
        initial = await rounds.reconcile_round(session, experiments, 'root')
    assert all(s['metadata']['source_job_id'] == 'root' for s in initial['steps'].values())
    assert len(initial['selected_rounds']) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('remote', [False, True])
async def test_selected_round_uses_real_project_destination_and_child_attempt(selected, setup_store, remote):
    from test_project_normalized_child_requests import destination
    from experiment_models import ExperimentLaunchContext
    client, session, root, tmp = await source(selected)
    async with setup_store() as experiments:
        dest = await destination(experiments)
        await experiments.commit()
    payload = request('project-placement')
    payload['launch_context_id'] = dest['launch_context_id']
    payload['execution_target_id'] = 'vast:new-round-worker' if remote else None
    payload['round_plan']['stages'] = payload['round_plan']['stages'][:1]
    result = await client.post('/api/binder-continuation/selected', json=payload)
    assert result.status_code == 201, result.text
    step = result.json()['round']['steps'][0]
    if remote:
        assert step['state'] == 'review_required', result.text
        retained = step['review'].get('job_request') or step['review']['job_requests'][0]
        context_id = retained['launch_context_id']
        assert retained['execution_target_id'] == 'vast:new-round-worker'
    else:
        assert step['state'] == 'queued', result.text
        child = await session.get(Job, step['job_id'])
        context_id = child.provenance['launch_context_id']
        assert child.execution_target_id is None
    async with setup_store() as experiments:
        context = await experiments.get(ExperimentLaunchContext, context_id)
        assert context.project_id == dest['project_id']
        assert context.run_attempt_id
    assert context_id != dest['launch_context_id']
    again = await client.post('/api/binder-continuation/selected', json=payload)
    assert again.status_code == 201, again.text
    assert again.json()['round_id'] == result.json()['round_id']
    evidence('project-remote.json' if remote else 'project-local.json', again.json())


@pytest.mark.parametrize('explicit', [{}, {'design_chain': 'X'}, {'design_chain': ''}])
def test_approved_role_defaults_preserve_explicit_values_and_clears(explicit):
    supplied = {'design_chain': 'A', 'target_chain': 'B', **explicit}
    result = selected_round.role_defaults('fampnn', supplied, explicit,
        {'binder_chains': ['B'], 'target_chains': ['A']})
    assert result['design_chain'] == explicit.get('design_chain', 'B')
    assert result['target_chain'] == 'A'
    assert supplied['target_chain'] == 'B'


@pytest.mark.parametrize('mutation', ['dependency', 'duplicate', 'unknown', 'pose'])
def test_closed_plan_and_model_settings(mutation):
    payload = request()
    if mutation == 'dependency':
        payload['round_plan']['stages'][0]['input'] = {'stage_id': 'blind'}
    elif mutation == 'duplicate':
        payload['round_plan']['stages'][1]['id'] = 'redesign'
    elif mutation == 'unknown':
        payload['round_plan']['stages'][0]['models'][0]['params']['invented_setting'] = True
    else:
        payload['round_plan']['stages'][1]['models'][0]['params']['protenix_use_template'] = True
    with pytest.raises(ValueError):
        selected_round.normalize(SelectedRoundRequest.model_validate(payload))
