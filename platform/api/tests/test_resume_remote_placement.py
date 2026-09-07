"""Offline HTTP-to-SQLite placement regressions; no scheduler or remote I/O."""
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import subprocess
import tarfile

from services.remote_execution.bundle import current_source_identity as real_source_identity

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, ExecutionTarget, Job, get_session
from routers import jobs
from services.remote_execution import bundle


@pytest_asyncio.fixture
async def store(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(jobs, 'get_results_dir', lambda: tmp_path / 'results')
    monkeypatch.setattr(jobs, '_raise_if_workflow_launches_disabled', lambda *_: None)
    monkeypatch.setattr(bundle, 'current_source_identity', lambda: ('a' * 40, 'b' * 40))
    app = FastAPI()
    app.include_router(jobs.router, prefix='/jobs')
    async def session_dependency():
        async with factory() as session:
            yield session
    app.dependency_overrides[get_session] = session_dependency
    async with factory() as session:
        session.add(ExecutionTarget(
            id='vast:new', provider='vast', provider_instance_id='new', active=True,
            state='ready', capabilities={'gpu_count': 1}, provider_metadata={'inventory': {
                'checked_at': datetime.utcnow().isoformat(), 'status': 'complete',
                'present': True, 'running': True,
            }},
        ))
        await session.commit()
    async with AsyncClient(transport=ASGITransport(app), base_url='http://fixture') as client:
        yield factory, client, tmp_path
    await engine.dispose()


async def source(store, target=None, model='boltz2', **extra):
    factory, _, root = store
    mode = extra.pop('mode', 'predict')
    output = root / 'original'
    output.mkdir()
    (output / 'sentinel').write_text('immutable artifact')
    async with factory() as session:
        job = Job(id='source', name='original', model_id=model, mode=mode,
                  status=extra.pop('status', 'cancelled'), queue_status='cancelled', output_dir=str(output),
                  execution_target_id=target,
                  execution_source_revision=extra.pop('execution_source_revision', 'a' * 40 if target else None),
                  execution_source_tree=extra.pop('execution_source_tree', 'b' * 40 if target else None),
                  pinned_gpu=3,
                  params={'sequence': 'ACDEFG', 'num_samples': 2, 'pinned_gpus': [3],
                          'gpu_ids': '3', 'bcp_gpu_ids': '3', 'gpu_id': 3,
                          'resume_job_id': 'ancestor', 'resume_root_job_id': 'ancestor',
                          'resume_work_dir': '/old/work', 'resume_source_dir': '/old/output',
                          'resume_stage_work_dir': '/old/task', 'resume_requested_stage': 'fold',
                          'msa_cache_dir': '/old/msa-cache', 'data_root': '/old/data',
                          'code_root': '/old/code', 'work_dir': '/old/work',
                          'msa_preferred_gpu_ids': [3], 'msa_excluded_gpus': [0],
                          'batch_name': 'old_batch'},
                  **extra)
        session.add(job)
        await session.commit()
        return {column.name: deepcopy(getattr(job, column.name)) for column in Job.__table__.columns}


@pytest.mark.asyncio
@pytest.mark.parametrize('old,target,model', [(None, 'vast:new', 'boltz2'),
    ('vast:old', None, 'protenix'), ('vast:old', 'vast:new', 'protenix'),
    ('vast:old', 'vast:new', 'esmfold2')])
async def test_changed_target_creates_fresh_root_without_touching_source(store, old, target, model):
    snapshot = await source(store, old, model)
    factory, client, root = store
    response = await client.post('/jobs/source/resume', json={
        'execution_target_id': target, 'param_overrides': {'num_samples': 4, 'pinned_gpus': [0]}})
    assert response.status_code == 200, response.text
    async with factory() as session:
        successor = await session.get(Job, response.json()['new_job_id'])
        assert successor.execution_target_id == target
        assert successor.output_dir != snapshot['output_dir']
        assert successor.parent_job_id is None
        assert successor.lineage_root_job_id in (None, successor.id)
        assert successor.params['reorchestrated_from_job_id'] == 'source'
        assert not any(key.startswith('resume_') for key in successor.params)
        assert successor.params.get('batch_name') != 'old_batch'
        assert successor.params['num_samples'] == 4
        assert successor.params['pinned_gpus'] == [0]
        assert successor.pinned_gpu is None
        assert all(key not in successor.params for key in (
            'gpu_ids', 'bcp_gpu_ids', 'gpu_id', 'msa_preferred_gpu_ids', 'msa_excluded_gpus',
            'msa_cache_dir', 'data_root', 'code_root', 'work_dir'))
        from services.nextflow import build_nextflow_command
        command = build_nextflow_command(successor.model_id, successor.mode,
            successor.params, successor.output_dir, successor.id)
        assert '-resume' not in command
        assert '/old/' not in ' '.join(command)
        assert command[command.index('--out_dir') + 1] == successor.output_dir
        assert successor.stage_work_dir is None and successor.remote_attempt_id is None
        assert successor.execution_source_revision == ('a' * 40 if target else None)
        assert successor.execution_source_tree == ('b' * 40 if target else None)
        original = await session.get(Job, 'source')
        assert {column.name: getattr(original, column.name) for column in Job.__table__.columns} == snapshot
    assert (root / 'original' / 'sentinel').read_text() == 'immutable artifact'


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [{}, {'execution_target_id': 'vast:new'}])
async def test_same_target_preserves_remote_identity_and_legacy_cache(store, payload):
    snapshot = await source(store, 'vast:new')
    factory, client, _ = store
    response = await client.post('/jobs/source/resume', json=payload)
    assert response.status_code == 200, response.text
    assert response.json()['source_changed'] is False
    assert response.json()['fresh_execution'] is False
    assert response.json()['placement_changed'] is False
    assert response.json()['resume_stage_mode'] == 'hint'
    async with factory() as session:
        successor = await session.get(Job, response.json()['new_job_id'])
        assert successor.execution_target_id == 'vast:new'
        assert successor.execution_source_revision == snapshot['execution_source_revision']
        assert successor.execution_source_tree == snapshot['execution_source_tree']
        assert successor.output_dir == snapshot['output_dir']
        assert successor.params['resume_source_dir'] == snapshot['output_dir']
        assert successor.pinned_gpu == 3
        original = await session.get(Job, 'source')
        assert {column.name: getattr(original, column.name) for column in Job.__table__.columns} == snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [{}, {'execution_target_id': 'vast:new'}])
@pytest.mark.parametrize('change_tree', [False, True])
async def test_same_worker_source_drift_admits_current_fresh_bundle(store, monkeypatch, payload, change_tree):
    factory, client, root = store
    repo = root / 'repo'
    repo.mkdir()
    def git(*args):
        return subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    git('init')
    git('config', 'user.email', 'fixture@example.invalid')
    git('config', 'user.name', 'Offline fixture')
    (repo / 'source.txt').write_text('old code')
    git('add', 'source.txt')
    git('commit', '-m', 'old')
    old_revision, old_tree = real_source_identity(repo)
    if change_tree:
        (repo / 'source.txt').write_text('current code')
        git('add', 'source.txt')
    git('commit', '--allow-empty', '-m', 'current')
    current = real_source_identity(repo)
    assert old_revision != current[0]
    assert (old_tree != current[1]) == change_tree
    monkeypatch.setattr(bundle, 'get_code_root', lambda: repo)
    monkeypatch.setattr(bundle, 'current_source_identity', real_source_identity)
    for name, path in [('get_data_root', root), ('get_results_dir', root / 'results'),
                       ('get_container_dir', root / 'containers'), ('get_weights_root', root / 'weights')]:
        monkeypatch.setattr(bundle, name, lambda path=path: path)
    snapshot = await source(store, 'vast:new', 'protenix',
                            execution_source_revision=old_revision, execution_source_tree=old_tree)
    # GPU 3 is a valid same-worker pin; this test does not run the scheduler.
    async with factory() as session:
        target = await session.get(ExecutionTarget, 'vast:new')
        target.capabilities = {'gpu_count': 4}
        target.remote_root = '/opt/biomodstack'
        await session.commit()
    response = await client.post('/jobs/source/resume', json=payload)
    assert response.status_code == 200, response.text
    async with factory() as session:
        successor = await session.get(Job, response.json()['new_job_id'])
        target = await session.get(ExecutionTarget, 'vast:new')
        # Stop only AFTER the real equality fence, git archive and safe extraction.
        # No dependency discovery, runtime assets, transport or scientific execution.
        class ArchiveBoundaryReached(Exception):
            pass
        def stop_after_archive(*_args):
            raise ArchiveBoundaryReached()
        monkeypatch.setattr(bundle, 'compile_remote_dependencies', stop_after_archive)
        with pytest.raises(ArchiveBoundaryReached):
            bundle.prepare_remote_bundle(job=successor, target=target, command=['nextflow'])
        archives = list((root / 'remote-execution' / 'staging').glob('*/source/.bms-source.tar'))
        assert len(archives) == 1
        with tarfile.open(archives[0]) as archive:
            archived_source = archive.extractfile('source.txt')
            assert archived_source is not None
            assert archived_source.read() == (repo / 'source.txt').read_bytes()
        assert (successor.execution_source_revision, successor.execution_source_tree) == current
        assert successor.output_dir != snapshot['output_dir']
        assert successor.lineage_root_job_id == successor.id
        assert successor.parent_job_id is None
        assert successor.params['reorchestrated_from_job_id'] == 'source'
        assert not any(key.startswith('resume_') for key in successor.params)
        assert all(key not in successor.params for key in ('msa_cache_dir', 'data_root', 'code_root', 'work_dir'))
        assert successor.params.get('batch_name') != 'old_batch'
        assert successor.pinned_gpu == 3
        for key in ('pinned_gpus', 'gpu_ids', 'bcp_gpu_ids', 'gpu_id',
                    'msa_preferred_gpu_ids', 'msa_excluded_gpus', 'sequence', 'num_samples'):
            assert successor.params[key] == snapshot['params'][key]
        original = await session.get(Job, 'source')
        assert {column.name: getattr(original, column.name) for column in Job.__table__.columns} == snapshot
    assert (root / 'original' / 'sentinel').read_text() == 'immutable artifact'
    assert response.json()['placement_changed'] is False
    assert response.json()['source_changed'] is True
    assert response.json()['fresh_execution'] is True
    assert response.json()['resume_stage_mode'] == 'fresh'
    assert 'source' in response.json()['resume_stage_note'].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize('extra,model', [({'parent_job_id': 'parent'}, 'boltz2'),
    ({'awaiting_input': True}, 'protenix'),
    ({'status': 'awaiting_input', 'awaiting_input': False}, 'protenix'), ({}, 'rf3')])
async def test_source_drift_continuation_denied_before_writes(store, extra, model):
    snapshot = await source(store, 'vast:new', model, execution_source_revision='c' * 40, **extra)
    factory, client, root = store
    response = await client.post('/jobs/source/resume', json={})
    assert response.status_code == 409, response.text
    assert 'source' in response.json()['detail'].lower()
    assert 'new' in response.json()['detail'].lower()
    async with factory() as session:
        rows = (await session.scalars(select(Job))).all()
        assert len(rows) == 1
        assert {column.name: getattr(rows[0], column.name) for column in Job.__table__.columns} == snapshot
    assert not (root / 'results').exists()
    assert (root / 'original' / 'sentinel').read_text() == 'immutable artifact'


@pytest.mark.asyncio
async def test_same_worker_missing_source_identity_still_denied(store):
    await source(store, 'vast:new', execution_source_tree=None)
    factory, client, root = store
    response = await client.post('/jobs/source/resume', json={})
    assert response.status_code == 409
    assert 'missing its immutable source identity' in response.json()['detail']
    async with factory() as session:
        assert len((await session.scalars(select(Job))).all()) == 1
    assert not (root / 'results').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['stale', 'inactive', 'unavailable', 'missing', 'identity'])
async def test_invalid_placement_rejected_before_writes(store, failure, monkeypatch):
    await source(store)
    factory, client, root = store
    async with factory() as session:
        target = await session.get(ExecutionTarget, 'vast:new')
        if failure == 'stale':
            target.provider_metadata = {'inventory': {**target.provider_metadata['inventory'],
                'checked_at': (datetime.utcnow() - timedelta(minutes=5)).isoformat()}}
        elif failure == 'inactive':
            target.active = False
        elif failure == 'unavailable':
            target.state = 'unavailable'
        await session.commit()
    if failure == 'identity':
        def unavailable():
            raise RuntimeError('no source identity')
        monkeypatch.setattr(bundle, 'current_source_identity', unavailable)
    response = await client.post('/jobs/source/resume', json={
        'execution_target_id': 'missing' if failure == 'missing' else 'vast:new'})
    assert response.status_code == (503 if failure == 'identity' else 422), response.text
    async with factory() as session:
        assert len((await session.scalars(select(Job))).all()) == 1
    assert not (root / 'results').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('extra,model', [({}, 'rf3'), ({}, 'nanopore'), ({'parent_job_id': 'parent'}, 'boltz2'),
    ({'awaiting_input': True}, 'protenix')])
async def test_unsupported_placement_change_rejected(store, extra, model):
    await source(store, model=model, **extra)
    _, client, _ = store
    response = await client.post('/jobs/source/resume', json={'execution_target_id': 'vast:new'})
    assert response.status_code in (403, 422), response.text


@pytest.mark.asyncio
async def test_fold_cp_terminal_root_can_change_placement(store):
    await source(store, 'vast:old', 'boltz_cp_experimental', mode='design')
    factory, client, _ = store
    response = await client.post('/jobs/source/resume', json={'execution_target_id': 'vast:new'})
    assert response.status_code == 200, response.text
    async with factory() as session:
        successor = await session.get(Job, response.json()['new_job_id'])
        assert successor.execution_target_id == 'vast:new'
        assert successor.model_id == 'boltz_cp_experimental' and successor.mode == 'design'
        assert 'resume_work_dir' not in successor.params
        assert successor.params.get('gpu_ids') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('fresh_cause', ['placement', 'source'])
async def test_marked_fresh_reorchestration_blocked_while_inactive(store, monkeypatch, fresh_cause):
    from services import core_protein_scientific_contract as contract
    monkeypatch.setattr(contract, 'ACTIVATED_CALLERS', frozenset())
    snapshot = await source(store, 'vast:new',
        execution_source_revision=('c' if fresh_cause == 'source' else 'a') * 40,
        provenance={contract.REVISION_KEY: contract.REVISION})
    factory, client, root = store
    payload = {'execution_target_id': None} if fresh_cause == 'placement' else {}
    response = await client.post('/jobs/source/resume', json=payload)
    assert response.status_code == 422, response.text
    assert 'marked source requires an active scientific caller' in response.json()['detail']
    async with factory() as session:
        rows = (await session.scalars(select(Job))).all()
        assert len(rows) == 1
        assert {column.name: getattr(rows[0], column.name) for column in Job.__table__.columns} == snapshot
    assert not (root / 'results').exists()
    assert (root / 'original' / 'sentinel').read_text() == 'immutable artifact'


@pytest.mark.asyncio
@pytest.mark.parametrize('marked', [False, True])
@pytest.mark.parametrize('active', [False, True])
async def test_true_cached_resume_preserves_scientific_cohort(store, monkeypatch, marked, active):
    from services import core_protein_scientific_contract as contract
    monkeypatch.setattr(contract, 'ACTIVATED_CALLERS',
                        frozenset({('boltz2', 'predict')}) if active else frozenset())
    provenance = {contract.REVISION_KEY: contract.REVISION,
                  'core_protein_requested_params': {'sequence': 'ACDEFG'}} if marked else {}
    snapshot = await source(store, 'vast:new', provenance=provenance)
    factory, client, root = store
    response = await client.post('/jobs/source/resume', json={})
    assert response.status_code == 200, response.text
    assert response.json()['fresh_execution'] is False
    async with factory() as session:
        successor = await session.get(Job, response.json()['new_job_id'])
        assert contract.revision_for_job(successor) == (contract.REVISION if marked else None)
        assert successor.params.get(contract.REVISION_KEY) == (contract.REVISION if marked else None)
        assert successor.provenance == provenance
        assert successor.output_dir == snapshot['output_dir']
        assert successor.params['resume_source_dir'] == snapshot['output_dir']
        assert successor.execution_source_revision == snapshot['execution_source_revision']
        original = await session.get(Job, 'source')
        assert {column.name: getattr(original, column.name) for column in Job.__table__.columns} == snapshot
    assert not (root / 'results').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('fresh_cause', ['placement', 'source'])
@pytest.mark.parametrize('invalid', [False, True])
async def test_unmarked_fresh_reorchestration_uses_current_admission(store, monkeypatch, fresh_cause, invalid):
    from services import core_protein_scientific_contract as contract
    monkeypatch.setattr(contract, 'ACTIVATED_CALLERS', frozenset({('boltz2', 'predict')}))
    snapshot = await source(store, 'vast:new',
        execution_source_revision=('c' if fresh_cause == 'source' else 'a') * 40)
    factory, client, root = store
    payload: dict = {'execution_target_id': None} if fresh_cause == 'placement' else {}
    # Use real current registry validation, not a mock admission success.
    if invalid:
        payload['param_overrides'] = {'boltz_recycling_steps': -1}
    response = await client.post('/jobs/source/resume', json=payload)
    if invalid:
        assert response.status_code == 422, response.text
        assert response.json()['detail']['validation_errors']
        assert not (root / 'results').exists()
    else:
        assert response.status_code == 200, response.text
        assert response.json()['fresh_execution'] is True
    async with factory() as session:
        rows = (await session.scalars(select(Job))).all()
        assert len(rows) == (1 if invalid else 2)
        if not invalid:
            successor = await session.get(Job, response.json()['new_job_id'])
            assert contract.revision_for_job(successor) == contract.REVISION
            assert successor.params[contract.REVISION_KEY] == contract.REVISION
            assert successor.output_dir != snapshot['output_dir']
            assert successor.lineage_root_job_id == successor.id
            assert successor.params['reorchestrated_from_job_id'] == 'source'
            assert not any(key.startswith('resume_') for key in successor.params)
            assert successor.execution_target_id == (None if fresh_cause == 'placement' else 'vast:new')
        original = await session.get(Job, 'source')
        assert {column.name: getattr(original, column.name) for column in Job.__table__.columns} == snapshot
    assert (root / 'original' / 'sentinel').read_text() == 'immutable artifact'


@pytest.mark.asyncio
async def test_placement_cannot_be_hidden_in_scientific_overrides(store):
    await source(store)
    _, client, _ = store
    response = await client.post('/jobs/source/resume', json={
        'param_overrides': {'execution_target_id': 'vast:new'}})
    assert response.status_code == 422, response.text
