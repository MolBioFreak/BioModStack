"""Operations lane: real publication custody and scratch ordinary Jobs, no science."""
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import select

from database import Job, JobArtifact
from services import binder_round as rounds, binder_selected_round as selected_round
from test_sequence_native_publication import store, emitted_result, seed
from test_binder_continuation import selected
from test_binder_selected_round import source, request
from test_project_workflow_setups import setup_store


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['disco', 'laproteina', 'rfd4_proteina', 'complexa'])
async def test_generic_publication_enumerates_exact_bodyless_records(selected, tmp_path, model):
    from test_denovo_canonical_return import native_tree
    from services.rfd4_proteina_results import publish_native_generation, read_published_generation_page
    client, session, root, tmp = await source(selected)
    output = tmp / 'native-return'
    native_tree(output, model, 'partial', compressed=False)
    root.model_id = 'protein_cad_experimental' if model in {'disco', 'laproteina'} else model
    root.mode = 'design' if model in {'disco', 'laproteina'} else 'generate'
    root.params = {'backend': model}
    root.output_dir = str(output)
    await publish_native_generation(session, root, output)
    await session.commit()
    page = await read_published_generation_page(root, session)
    refs = await selected_round.output_refs(session, root)
    assert all(row['candidate_ref'] in refs for row in page['records'])
    for row in page['records']:
        if row['candidate_ref']['kind'] == 'native_sequence':
            resolved = await selected_round.resolve_ref(session, root, row['candidate_ref'], output_format='native')
            assert resolved['sequence'] == row['sequence'] == 'ACDE'
            assert 'path' not in resolved


@pytest.mark.asyncio
async def test_returned_native_sequence_new_intent_after_cleanup_queues_real_child(selected, setup_store):
    from test_denovo_canonical_return import native_tree
    from services.rfd4_proteina_results import publish_native_generation
    from schemas import SelectedRoundRequest
    from sqlalchemy.ext.asyncio import async_sessionmaker
    client, session, root, tmp = await source(selected)
    output = tmp / 'native-return'
    native_tree(output, 'disco', 'partial', compressed=False)
    root.model_id, root.mode, root.params, root.output_dir = 'protein_cad_experimental', 'design', {'backend': 'disco'}, str(output)
    await publish_native_generation(session, root, output)
    await session.commit()
    ref = next(r for r in await selected_round.output_refs(session, root) if r['kind'] == 'native_sequence')
    payload = {'operation': 'round', 'source_job_id': root.id, 'idempotency_key': 'before-cleanup',
        'execution_target_id': None, 'selection': {'candidate_refs': [ref]},
        'round_plan': {'schema_version': 2, 'stages': [{'id': 'blind', 'kind': 'prediction',
            'input': {'selected': True, 'scope': 'whole_composition'}, 'models': [
                {'invocation_id': 'predict', 'model_id': 'esmfold2', 'mode': 'predict', 'params': {}}]}]}}
    first = await client.post('/api/binder-continuation/selected', json=payload)
    assert first.status_code == 201, first.text
    assert first.json()['round']['steps'][0]['job_id'], first.text
    for artifact in await session.scalars(select(JobArtifact).where(JobArtifact.owner_job_id == root.id)):
        Path(artifact.storage_path).unlink(missing_ok=True)
    sessions = async_sessionmaker(session.bind, expire_on_commit=False)
    payload['idempotency_key'] = 'fresh-after-cleanup'
    async with sessions() as fresh, setup_store() as experiments:
        second = await selected_round.submit(fresh, experiments, SelectedRoundRequest.model_validate(payload))
        assert second['round_id'] != first.json()['round_id']
        step = second['steps'][0]
        assert step['source_ref'] == ref and step['job_id']
        child = await fresh.get(Job, step['job_id'])
        assert child.params['sequence'] == 'ACDE'
        assert child.execution_target_id is None
        assert child.params['complex_components'][0]['sequence'] == 'ACDE'
        assert not child.params.get('input_pdb')


@pytest.mark.asyncio
@pytest.mark.parametrize('model,mode,outputs', [
    ('caliby_experimental', 'ensemble_design', True),
    ('caliby_experimental', 'sidechain_pack', True),
    ('ligandmpnn', 'ligand_aware', True),
    ('ligandmpnn', 'ligand_aware', False),
])
async def test_exact_retained_native_source_after_cleanup_fresh_session(tmp_path, monkeypatch, store, model, mode, outputs):
    from services.result_state_integrity import finalize_successful_job
    folder, _, _, _, params = emitted_result(tmp_path, monkeypatch, model, mode, write_outputs=outputs)
    await seed(store, folder, model, mode, params, remote=True)
    async with store() as session:
        owner = await session.get(Job, 'native')
        await finalize_successful_job(owner, str(folder), session)
        await session.commit()
        refs = await selected_round.output_refs(session, owner)
        rows = [await selected_round.resolve_ref(session, owner, ref, output_format='native') for ref in refs]
        progress = rounds.progress_of(owner)
        progress['selection'] = {'resolved': rows}
        rounds.save(owner, progress)
        await session.commit()
        # Normal producer cleanup removes registered source files, not retained
        # source snapshots. No synthetic numerical result or Design is inserted.
        for artifact in await session.scalars(select(JobArtifact).where(JobArtifact.owner_job_id == owner.id)):
            Path(artifact.storage_path).unlink(missing_ok=True)
    async with store() as fresh:
        owner = await fresh.get(Job, 'native')
        for ref, expected in zip(refs, rows):
            restored = await selected_round.resolve_ref(fresh, owner, ref, output_format='native')
            assert restored == expected
            if outputs:
                assert Path(restored['path']).is_file()
            else:
                assert restored['sequence'] and 'path' not in restored
        owner.retry_count = (owner.retry_count or 0) + 1
        await fresh.commit()
        with pytest.raises(ValueError, match='identity changed'):
            await selected_round.resolve_ref(fresh, owner, refs[0], output_format='native')


@pytest.mark.asyncio
async def test_retained_source_does_not_hide_live_or_snapshot_tampering(tmp_path, monkeypatch, store):
    from services.result_state_integrity import finalize_successful_job
    folder, _, _, _, params = emitted_result(tmp_path, monkeypatch, 'caliby_experimental', 'sidechain_pack')
    await seed(store, folder, 'caliby_experimental', 'sidechain_pack', params, remote=True)
    async with store() as session:
        owner = await session.get(Job, 'native')
        await finalize_successful_job(owner, str(folder), session)
        await session.commit()
        ref = (await selected_round.output_refs(session, owner))[0]
        row = await selected_round.resolve_ref(session, owner, ref, output_format='native')
        progress = rounds.progress_of(owner)
        progress['selection'] = {'resolved': [row]}
        rounds.save(owner, progress)
        await session.commit()
        artifact = await session.get(JobArtifact, ref['artifact_id'])
        Path(artifact.storage_path).write_text('tampered live bytes')
        with pytest.raises((ValueError, OSError)):
            await selected_round.resolve_ref(session, owner, ref, output_format='native')
        Path(artifact.storage_path).unlink()
        Path(row['path']).write_text('tampered retained bytes')
        with pytest.raises(FileNotFoundError, match='retained candidate'):
            await selected_round.resolve_ref(session, owner, ref, output_format='native')


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['failed', 'cancelled'])
async def test_failed_predecessor_is_not_valid_empty_and_does_not_stall(selected, setup_store, status):
    client, session, root, tmp = await source(selected)
    payload = request('unsuccessful-' + status)
    response = await client.post('/api/binder-continuation/selected', json=payload)
    assert response.status_code == 201, response.text
    child = await session.get(Job, response.json()['launched_jobs'][0]['id'])
    child.status = status
    await session.commit()
    async with setup_store() as experiments:
        result = await selected_round.reconcile(session, experiments, 'root', response.json()['round_id'])
    assert result['state'] == 'completed_with_errors', result
    assert len(result['steps']) == 1
    assert {d['state'] for d in result['dependencies'].values()} == {'predecessor_' + status}
    assert (await session.get(Job, 'root')).status == 'cancelled'
