"""Private independent-session custody races and real retained-source acquisition."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job
from paths import resolve_allowed_path
from services import binder_source_materialization as sources
from test_binder_source_materialization import source_api, stores, PDB
from test_denovo_source_custody_closeout import registered_source


@asynccontextmanager
async def old_unserialized_guard(value, session):
    # Baseline executes the real old cleanup/persistence race, not a missing API.
    yield


def guard(value, session):
    return getattr(sources, 'resequence_submission_guard', old_unserialized_guard)(value, session)


def preparation(root):
    directory = root / 'inputs/design_selections/protein_local_redesign/prepared'
    directory.mkdir(parents=True)
    (directory / 'input.pdb').write_text(PDB)
    return directory, sources.begin_resequence_preparation(directory)


@pytest.mark.asyncio
@pytest.mark.parametrize('reference', ['receipt', 'legacy', 'nested', 'template'])
async def test_cleanup_waits_for_independent_async_create_and_commit(source_api, reference):
    _, session, _, root = source_api
    directory, receipt = preparation(root)
    values = {'receipt': {'resequence_preparation': receipt},
              'legacy': {'backbone_input_pdbs': str(directory)},
              'nested': {'other': [{'path': str(directory / 'input.pdb')}]},
              'template': {'designer_template': str(directory) + '_designer.json'}}
    params = values[reference]
    entered, commit = asyncio.Event(), asyncio.Event()
    async def create():
        async with AsyncSession(bind=session.bind) as writer:
            async with guard(params, writer):
                writer.add(Job(id='created', name='fixture', model_id='protein_mpnn',
                               mode='region_redesign', params=params))
                await writer.flush()
                entered.set()
                await commit.wait()
                await writer.commit()
    task = asyncio.create_task(create())
    await entered.wait()
    cleanup = asyncio.create_task(sources.cleanup_resequence_preparation(receipt, session))
    await asyncio.sleep(.08)  # event-loop heartbeat and independent cleanup opportunity
    waited = not cleanup.done()
    commit.set()
    await task
    removed = await cleanup
    assert waited, 'cleanup passed an uncommitted source claimant'
    assert not removed and (directory / 'input.pdb').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('finish', ['commit', 'rollback', 'close'])
async def test_prepared_child_return_does_not_release_transaction_lock(source_api, finish):
    _, session, _, root = source_api
    directory, receipt = preparation(root)
    writer = AsyncSession(bind=session.bind)
    try:
        for index in range(2):
            async with guard({'backbone_input_pdbs': str(directory)}, writer):
                writer.add(Job(id=f'child-{index}', name='child', model_id='protein_mpnn',
                               mode='region_redesign', params={'backbone_input_pdbs': str(directory)}))
                await writer.flush()
        cleanup = asyncio.create_task(sources.cleanup_resequence_preparation(receipt, session))
        await asyncio.sleep(.08)
        waited = not cleanup.done()
        await getattr(writer, finish)()
        removed = await cleanup
        assert waited, '_commit=False helper return released custody before transaction end'
        assert removed == (finish != 'commit')
        if finish != 'commit' and hasattr(sources, '_preparation_lock_path'):
            assert sources._preparation_lock_path(directory).exists()
    finally:
        await writer.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('finish', ['commit', 'rollback'])
@pytest.mark.parametrize('nested', [False, True])
async def test_active_guard_survives_intermediate_transaction_end(source_api, finish, nested):
    _, session, _, root = source_api
    directory, receipt = preparation(root)
    async with AsyncSession(bind=session.bind) as writer:
        async with guard(receipt, writer):
            if nested:
                async with guard(receipt, writer):
                    await getattr(writer, finish)()
            else:
                await getattr(writer, finish)()
            cleaner = asyncio.create_task(sources.cleanup_resequence_preparation(receipt, session))
            await asyncio.sleep(.05)
            waited = not cleaner.done()
            # Real source read after an intermediate commit/rollback, before
            # any new transaction exists. The lexical guard still owns custody.
            exists = (directory / 'input.pdb').exists()
        assert await asyncio.wait_for(cleaner, 2)
        assert waited and exists


@pytest.mark.asyncio
async def test_cancelled_holder_and_waiter_release_without_blocking_loop(source_api):
    _, session, _, root = source_api
    directory, receipt = preparation(root)
    entered = asyncio.Event()
    async def hold():
        async with AsyncSession(bind=session.bind) as writer:
            async with guard(receipt, writer):
                writer.add(Job(id='cancelled', name='cancelled', model_id='protein_mpnn',
                               mode='region_redesign', params={'source': str(directory)}))
                await writer.flush()
                entered.set()
                await asyncio.Event().wait()
    owner = asyncio.create_task(hold())
    await entered.wait()
    waiter = asyncio.create_task(hold())
    await asyncio.sleep(.03)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    assert await asyncio.wait_for(sources.cleanup_resequence_preparation(receipt, session), 2)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['explicit', 'legacy', 'singular'])
async def test_real_acquisition_holds_sources_across_await(source_api, monkeypatch, kind):
    _, session, _, root = source_api
    directory, receipt = preparation(root)
    target = directory.with_name('acquired'); target.mkdir()
    request = sources.StructureSourceRequest(path=sources.to_allowed_relative(directory / 'input.pdb'))
    from routers import files
    original = files.materialize_structure_source
    entered, resume = asyncio.Event(), asyncio.Event()

    async def delayed(*args, **kwargs):
        entered.set()
        await resume.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(files, 'materialize_structure_source', delayed)
    async with AsyncSession(bind=session.bind) as writer:
        if kind == 'explicit':
            acquisition = sources.prepare_resequence_sources([request], writer, target, ())
        else:
            retained = {'source_structure': request.model_dump(exclude_none=True)} if kind == 'singular' else {}
            acquisition = sources.prepare_resequence_existing_basket(str(directory), retained, writer, target, ())
        task = asyncio.create_task(acquisition)
        await entered.wait()
        cleaner = asyncio.create_task(sources.cleanup_resequence_preparation(receipt, session))
        await asyncio.sleep(.05)
        waited = not cleaner.done()
        resume.set()
        result = await task
        assert len(result) == 1 and resolve_allowed_path(result[0]['path']).is_file()
        assert waited
        await writer.rollback()
        assert await asyncio.wait_for(cleaner, 2)


@pytest.mark.asyncio
async def test_opposing_prepared_batches_lock_union_then_nested_children(source_api):
    _, session, _, root = source_api
    first, receipt = preparation(root)
    second = first.with_name('second'); second.mkdir()
    second_receipt = sources.begin_resequence_preparation(second)
    entered, resume = asyncio.Event(), asyncio.Event()
    async def batch(index, values):
        async with AsyncSession(bind=session.bind) as writer:
            async with sources.resequence_submission_guard(values, writer):
                if index == 0:
                    entered.set()
                    await resume.wait()
                for child, value in enumerate(values):
                    async with sources.resequence_submission_guard(value, writer):
                        writer.add(Job(id=f'batch-{index}-{child}', name='child', model_id='protein_mpnn',
                                       mode='region_redesign', params=value))
                        await writer.flush()
                        # Both union locks survive each intermediate child commit.
                        await writer.commit()
    one = asyncio.create_task(batch(0, [receipt, second_receipt]))
    await entered.wait()
    two = asyncio.create_task(batch(1, [second_receipt, receipt]))
    await asyncio.sleep(.05)
    assert not two.done()
    resume.set()
    await asyncio.wait_for(asyncio.gather(one, two), 3)
    assert not await sources.cleanup_resequence_preparation(receipt, session)
    assert not await sources.cleanup_resequence_preparation(second_receipt, session)


@pytest.mark.asyncio
async def test_absent_marker_leaves_old_submission_behavior(source_api):
    _, session, _, root = source_api
    directory = root / 'inputs/legacy'
    directory.mkdir()
    async with sources.resequence_submission_guard({'backbone_input_pdbs': str(directory)}, session):
        assert not session.in_transaction()
    assert not list(directory.parent.glob('*.resequence.lock'))
    session.add(Job(id='unrelated', name='old', model_id='protein_mpnn', mode='region_redesign', params={}))
    await session.flush()
    with pytest.raises(RuntimeError):
        async with sources.resequence_submission_guard({'path': str(directory)}, session):
            raise RuntimeError('old caller handles this')
    assert session.in_transaction()  # absent-marker exceptions do not roll back old callers
    await session.rollback()


@pytest.mark.asyncio
async def test_clone_retained_exact_order_models_and_dead_producer(source_api):
    _, session, _, root = source_api
    request, original, _, _, owner, artifact = await registered_source(session, root)
    basket = root / 'inputs/design_selections/protein_local_redesign/basket'
    basket.mkdir(parents=True)
    second = request.model_copy(update={'model_number': 3,
        'candidate': request.candidate.model_copy(update={'model': 3})})
    roster = await sources.prepare_resequence_sources([request, second, second], session, basket, ())
    await session.delete(artifact)
    await session.delete(owner)
    await session.commit()
    original.unlink()
    (basket / 'unselected.pdb').write_text(PDB)
    target = basket.with_name('clone'); target.mkdir()
    retained = {'resequence_sources': list(reversed(roster)), 'execution_target_id': 'DO-NOT-COPY',
                'runtime': {'untrusted': True}, 'remote_claim': 'DO-NOT-COPY'}
    cloned = await sources.prepare_resequence_existing_basket(str(basket), retained, session, target, ())
    assert len(cloned) == 3
    import json
    import hashlib
    for item in cloned:
        assert not {'source_positions', 'author_residues', 'declared_chemistry', 'native_recipe'} & item.keys()
        payload = resolve_allowed_path(item['source_custody_path']).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == item['source_custody_sha256']
        full = json.loads(payload)
        assert full['source_positions'] and full['author_residues']
        assert full['source_structure'] == item['source_structure']
    assert [item['source_request'] for item in cloned] == [item['source_structure'] for item in reversed(roster)]
    assert [item['original_sha256'] for item in cloned] == [item['original_sha256'] for item in reversed(roster)]
    assert [item['model_number'] for item in cloned] == [3, 3, 7]
    from Bio.PDB import PDBParser
    parser = PDBParser(QUIET=True)
    assert [float(next(parser.get_structure('clone', str(resolve_allowed_path(item['path']))).get_atoms()).coord[0])
            for item in cloned] == [3.0, 3.0, 7.0]
    assert all(item['source_identity']['owner_job_id'] == 'producer' for item in cloned)
    assert all('runtime' not in item and 'execution_target_id' not in item for item in cloned)
    assert len(list(target.glob('*.pdb'))) == 3
    assert (basket / 'unselected.pdb').exists()


@pytest.mark.asyncio
async def test_clone_legacy_root_only_and_governed_source_checks(source_api):
    _, session, _, root = source_api
    basket = root / 'inputs/basket'; basket.mkdir()
    (basket / 'b.pdb').write_text(PDB)
    (basket / 'a.pdb').write_text(PDB)
    (basket / 'nested').mkdir(); (basket / 'nested/not-selected.pdb').write_text(PDB)
    target = root / 'inputs/clone'; target.mkdir()
    cloned = await sources.prepare_resequence_existing_basket(str(basket), {}, session, target, ())
    assert [Path(item['source_path']).name for item in cloned] == ['a.pdb', 'b.pdb']
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        await sources.prepare_resequence_existing_basket(str(basket), {}, session, target, (basket,))
    assert error.value.status_code == 403
    with pytest.raises(ValueError):
        await sources.prepare_resequence_existing_basket('/outside/path', {}, session, target, ())


@pytest.mark.asyncio
async def test_roster_retains_full_ledger_only_in_existing_custody_file(source_api):
    _, session, _, root = source_api
    source = root / 'inputs/source.pdb'; source.write_text(PDB)
    target = root / 'inputs/basket'; target.mkdir()
    roster = await sources.prepare_resequence_sources(
        [sources.StructureSourceRequest(path='inputs/source.pdb')], session, target, ())
    assert 'source_positions' not in roster[0], 'full atom ledger is duplicated into Job params'
    import json
    full = json.loads(resolve_allowed_path(roster[0]['source_custody_path']).read_text())
    assert full['source_positions'] and full['native_recipe']
    assert full['source_structure'] == roster[0]['source_structure']


@pytest.mark.asyncio
async def test_cleanup_projects_only_claim_columns_and_resolves_artifacts(source_api):
    _, session, _, root = source_api
    directory, receipt = preparation(root)
    from services.scientific_artifacts import publish_json_payload
    reference = await publish_json_payload(session, owner_kind='job', owner_id='claim',
        role='params', schema_id='test.source-claim', payload={'nested': [str(directory / 'input.pdb')]})
    session.add(Job(id='claim', name='claim', model_id='protein_mpnn', mode='region_redesign', params=reference))
    await session.commit()
    from sqlalchemy import event
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(session.bind.sync_engine, 'before_cursor_execute', capture)
    try:
        assert not await sources.cleanup_resequence_preparation(receipt, session)
    finally:
        event.remove(session.bind.sync_engine, 'before_cursor_execute', capture)
    selects = [statement for statement in statements if 'FROM jobs' in statement]
    assert selects
    assert all('jobs.params' in s and 'jobs.provenance' in s and 'jobs.status' not in s for s in selects)


@pytest.mark.asyncio
async def test_cleanup_waiters_keep_same_inode_after_removal(source_api):
    _, session, _, root = source_api
    directory, receipt = preparation(root)
    writer = AsyncSession(bind=session.bind)
    async with sources.resequence_submission_guard(receipt, writer):
        lock_path = sources._preparation_lock_path(directory)
        inode = lock_path.stat().st_ino
        cleaners = [asyncio.create_task(sources.cleanup_resequence_preparation(receipt, session)) for _ in range(2)]
        await asyncio.sleep(.04)
        assert all(not task.done() for task in cleaners)
    await writer.rollback()
    assert sorted(await asyncio.gather(*cleaners)) == [False, True]
    assert lock_path.stat().st_ino == inode
    async with sources.resequence_submission_guard(receipt, writer):
        assert lock_path.stat().st_ino == inode
    await writer.close()
