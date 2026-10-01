"""MD receiving boundaries on mapped scratch SQLite and inert native artifacts."""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from database import Base, Job, JobArtifact, MdAttemptSegment, MdReplicaRun, MdRun
from services.md.state import create_md_run, create_replica_attempt
import services.md.results as results
import services.md.completion as completion
import services.md.lifecycle as lifecycle
import services.md.reconcile as reconcile
import routers.md_results as routes
from test_md_analysis_results import test_replica_reporting_never_pools_frames_as_biological_replicates as _emit_tree


@pytest_asyncio.fixture
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'receiving.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield engine, maker
    await engine.dispose()


def _tree(tmp_path, monkeypatch):
    root = tmp_path / 'native'
    root.mkdir()
    _emit_tree(root, monkeypatch)
    spec = json.loads((root / 'replicas/replica_0/manifest.json').read_text())['config']
    return root, spec


async def _seed(maker, root, spec, *, phase='finalizing', status='running'):
    async with maker() as session:
        parent = Job(id='md-job-1', name='MD', model_id='molecular_dynamics', mode='simulate',
            status=status, queue_status=status, output_dir=str(root), params={'md_job_spec': spec})
        replica_child = Job(id='replica-child-0', name='replica', model_id='molecular_dynamics',
            mode='replica', parent_job_id=parent.id, child_stage='md_replica',
            status='completed', queue_status='completed', params={})
        analysis_child = Job(id='analysis-child-0', name='analysis', model_id='molecular_dynamics',
            mode='analyze', parent_job_id=parent.id, child_stage='md_analysis',
            output_dir=str(root / 'analysis'), status='completed', queue_status='completed',
            params={'md_replica_index': 0})
        session.add_all([parent, replica_child, analysis_child])
        await session.flush()
        run = await create_md_run(session, job=parent, normalized_request={
            **spec, 'schema': 'bms.md.job.v2', 'chemistry': {
                'profile_id': 'amber_ff19sb_opc_protein_v1', 'profile_sha256': 'a' * 64,
                'assurance': 'curated_profile'}})
        replica, segment = await create_replica_attempt(session, job_id=parent.id,
            replica_index=0, attempt=0, engine='gromacs', child_job_id=replica_child.id,
            execution_plan_sha256='b' * 64, compatibility_key='c' * 64)
        replica.state = segment.state = 'running'
        run.phase = phase
        await session.commit()
        return replica.id, segment.id


def _bytes(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob('*') if path.is_file()}


@pytest.mark.asyncio
async def test_finalization_uses_one_inventory_preserves_bytes_and_atomic_readback(store, tmp_path, monkeypatch):
    engine, maker = store
    root, spec = _tree(tmp_path, monkeypatch)
    replica_id, segment_id = await _seed(maker, root, spec)
    before = _bytes(root)
    counters = {'inventory': 0, 'loop_orm': 0}
    reader = results._load_inventory
    digest_reader = results._digest
    digest_calls = {}
    def digest(path):
        logical = path.relative_to(root).as_posix()
        digest_calls[logical] = digest_calls.get(logical, 0) + 1
        return digest_reader(path)
    monkeypatch.setattr(results, '_digest', digest)
    loop_thread = threading.get_ident()
    def inventory(*args, **kwargs):
        counters['inventory'] += 1
        return reader(*args, **kwargs)
    monkeypatch.setattr(results, '_load_inventory', inventory)
    monkeypatch.setattr(completion, '_load_inventory', inventory)
    def sql(*args):
        assert threading.get_ident() == loop_thread
        counters['loop_orm'] += 1
    event.listen(engine.sync_engine, 'before_cursor_execute', sql)
    async with maker() as session:
        parent = await session.get(Job, 'md-job-1')
        snapshot = await completion.validate_and_finalize_md_job(parent, session)
        first_digest_calls = dict(digest_calls)
        assert parent.status == parent.queue_status == 'completed'
        async with maker() as observer:
            assert (await observer.get(MdRun, parent.id)).phase == 'finalizing'
            assert list((await observer.scalars(select(JobArtifact))).all()) == []
        await session.commit()
        rows = list((await session.scalars(select(JobArtifact))).all())
        expected = {(item.replica_index, item.path.relative_to(root).as_posix(), item.sha256, item.bytes)
            for item in reader(parent)[2]}
        assert {(row.provenance['semantic_role'] and 0, row.logical_path, row.sha256, row.bytes)
            for row in rows} == expected
        assert any(row.provenance.get('semantic_roles') == ['analysis_topology', 'representative_structure']
            for row in rows)
        print('first finalization evidence:', json.dumps({
            'inventory_builds': counters['inventory'], 'unique_byte_rows': len(expected),
            'manifest_digest_calls': first_digest_calls,
            'snapshot': snapshot,
            'settings_sha256': hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest(),
            'artifact_bytes_sha256': hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
        }, sort_keys=True))
        assert counters['inventory'] == 1
        assert first_digest_calls['replicas/replica_0/manifest.json'] == 1
        assert first_digest_calls['manifest.json'] == 1
        assert first_digest_calls['md_completion_barrier.json'] == 1
        first = (parent.completed_at, (await session.get(MdRun, parent.id)).state_version)
        again = await completion.validate_and_finalize_md_job(parent, session)
        assert again == snapshot
        assert (parent.completed_at, (await session.get(MdRun, parent.id)).state_version) == first
        await session.commit()
    async with maker() as observer:
        assert (await observer.get(MdRun, 'md-job-1')).verification_status == 'verified'
        assert (await observer.get(MdReplicaRun, replica_id)).state == 'completed'
        assert (await observer.get(MdAttemptSegment, segment_id)).state == 'completed'
    assert _bytes(root) == before
    print('finalization counters:', counters, 'unique byte rows:', len(expected))


@pytest.mark.asyncio
@pytest.mark.parametrize('cancelled', [False, True])
async def test_analysis_callback_uses_finalizer_and_never_resurrects_cancelled_parent(
    store, tmp_path, monkeypatch, cancelled,
):
    _engine, maker = store
    root, spec = _tree(tmp_path, monkeypatch)
    replica_id, segment_id = await _seed(maker, root, spec,
        status='cancelled' if cancelled else 'running', phase='cancelled' if cancelled else 'finalizing')
    (root / 'analysis/manifest.json').unlink()
    (root / 'md_completion_barrier.json').unlink()
    async with maker() as session:
        reply = await lifecycle.reconcile_md_analysis_parent('md-job-1', session)
        await session.commit()
    async with maker() as observer:
        parent, run = await observer.get(Job, 'md-job-1'), await observer.get(MdRun, 'md-job-1')
        if cancelled:
            assert reply['status'] == 'cancelled'
            assert parent.status == run.phase == 'cancelled'
            assert list((await observer.scalars(select(JobArtifact))).all()) == []
        else:
            assert reply['status'] == parent.status == run.phase == 'completed'
            assert (await observer.get(MdReplicaRun, replica_id)).state == 'completed'
            assert (await observer.get(MdAttemptSegment, segment_id)).state == 'completed'
            assert len(list((await observer.scalars(select(JobArtifact))).all())) > 0


@pytest.mark.asyncio
async def test_stable_paused_ticks_zero_dml_then_uncertain_projection_uses_lease(store, tmp_path, monkeypatch):
    engine, maker = store
    root, spec = _tree(tmp_path, monkeypatch)
    replica_id, segment_id = await _seed(maker, root, spec, status='paused', phase='paused')
    async with maker() as session:
        child = await session.get(Job, 'replica-child-0')
        child.status = child.queue_status = 'paused'
        child.paused = True
        (await session.get(MdReplicaRun, replica_id)).state = 'paused'
        (await session.get(MdAttemptSegment, segment_id)).state = 'paused'
        await session.commit()
    dml = []
    def count(_conn, _cursor, statement, *_):
        if statement.lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE'}:
            dml.append(statement)
    event.listen(engine.sync_engine, 'before_cursor_execute', count)
    # A real other writer remains held throughout stable observation ticks.
    async with engine.connect() as lock:
        await lock.exec_driver_sql('BEGIN IMMEDIATE')
        async with maker() as session:
            for _ in range(3):
                receipt = await reconcile.reconcile_md_state(session, owner_id='observer', apply=True)
                assert receipt['change_count'] == 0
                await session.commit()
        assert dml == []
        await lock.rollback()
    async with maker() as session:
        child = await session.get(Job, 'replica-child-0')
        child.provenance = {'component_projection': {'state': 'uncertain'}}
        await session.commit()
    dml.clear()
    async with maker() as session:
        await reconcile.reconcile_md_state(session, owner_id='observer', apply=True)
        await session.commit()
    assert any('md_reconciler_lease' in statement.lower() for statement in dml)
    async with maker() as session:
        assert (await session.get(MdReplicaRun, replica_id)).state == 'orphaned'
    print('paused ticks DML=0; actual uncertainty recovery DML=', len(dml))


@pytest.mark.asyncio
@pytest.mark.parametrize('descriptor', [False, True])
async def test_async_leaf_heartbeat_repeated_cancellation_drains_and_closes_late_handle(
    store, monkeypatch, descriptor,
):
    _engine, maker = store
    async with maker() as session:
        session.add(Job(id='reader', name='reader', model_id='molecular_dynamics', mode='simulate',
            status='running', params={}))
        await session.commit()
    entered, release = threading.Event(), threading.Event()
    state = {'thread': None, 'timeout': False}
    import io
    handle = io.BytesIO(b'owned')
    def leaf(record, *_):
        assert not isinstance(record, Job)
        state['thread'] = threading.get_ident()
        entered.set()
        state['timeout'] = not release.wait(2)
        if descriptor:
            return SimpleNamespace(name='artifact', bytes=5), handle
        return {'schema': 'fixture'}
    monkeypatch.setattr(routes, 'open_verified_artifact' if descriptor else 'summary', leaf)
    async with maker() as session:
        request = Request({'type': 'http', 'method': 'GET', 'path': '/', 'headers': []})
        task = asyncio.create_task(routes.get_md_artifact_content('reader', 'id', request, session)
            if descriptor else routes.get_md_summary('reader', session))
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(.005)
            assert entered.is_set()
            assert state['thread'] != threading.get_ident()
            heartbeat = 0
            for _ in range(3):
                await asyncio.sleep(.005)
                heartbeat += 1
            task.cancel()
            await asyncio.sleep(.005)
            task.cancel()
            await asyncio.sleep(.005)
            assert heartbeat == 3 and not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not state['timeout']
    if descriptor:
        assert handle.closed


@pytest.mark.asyncio
async def test_remote_public_retry_reaches_retained_shared_owner_not_leaf_admission(store, tmp_path, monkeypatch):
    _engine, maker = store
    root, spec = _tree(tmp_path, monkeypatch)
    await _seed(maker, root, spec, status='failed', phase='failed')
    async with maker() as session:
        parent = await session.get(Job, 'md-job-1')
        _root, _aggregate, _records, aggregate_sha, set_sha = routes._current_dynamics_generation(parent)
        parent.execution_target_id = 'retained-target'
        parent.remote_attempt_id = 'retained-attempt'
        parent.provenance = {'md': {'aggregate_manifest_sha256': aggregate_sha,
            'replica_manifest_set_sha256': set_sha}, 'execution_plan_approval': {
                'approval_digest': 'retained-approval', 'plan': {'complete': True, 'metadata': {
                    'dynamic_templates': [{'expansion_json': {'child_model': 'molecular_dynamics',
                        'child_mode': 'analyze', 'child_stage': 'md_analysis'}}]}}}}
        child = await session.get(Job, 'analysis-child-0')
        child.status = child.queue_status = 'failed'
        child.params = {**child.params, 'md_replica_manifest_set_sha256': set_sha}
        child.execution_target_id = parent.execution_target_id
        await session.commit()
    calls = []
    import services.remote_execution.executor as executor
    import routers.jobs as jobs
    async def shared(session, parent, **kwargs):
        assert parent.id == 'md-job-1' and parent.status == 'failed'
        assert parent.remote_attempt_id == 'retained-attempt'
        assert kwargs['component_id'] == 'analysis-child-0'
        calls.append(kwargs)
        parent.status = parent.queue_status = 'queued'
        await session.commit()
        return {'child_job_id': 'retained-replacement'}
    async def forbid(*args, **kwargs):
        raise AssertionError('Standalone analyze root admission is forbidden')
    monkeypatch.setattr(executor, 'retry_component_execution', shared)
    monkeypatch.setattr(jobs, 'create_job', forbid)
    async with maker() as session:
        reply = await routes.retry_md_analysis('md-job-1', session)
        assert reply['created_child_ids'] == ['retained-replacement']
    assert len(calls) == 1 and calls[0]['operation_id'].startswith('md-analysis:')
    async with maker() as session:
        assert len(list((await session.scalars(select(Job))).all())) == 3
        parent = await session.get(Job, 'md-job-1')
        assert parent.status == 'queued'
        assert parent.provenance['md']['analysis_state'] == 'retrying'
        assert parent.params == {'md_job_spec': spec}

@pytest.mark.asyncio
@pytest.mark.parametrize('control', ['cancelled', 'awaiting_input'])
async def test_terminal_cas_preserves_control_arriving_during_filesystem_validation(
    store, tmp_path, monkeypatch, control,
):
    _engine, maker = store
    root, spec = _tree(tmp_path, monkeypatch)
    await _seed(maker, root, spec)
    entered, release = threading.Event(), threading.Event()
    original = results.completion_barrier
    state = {'thread': None, 'timeout': False}
    def blocked(*args, **kwargs):
        state['thread'] = threading.get_ident()
        entered.set()
        state['timeout'] = not release.wait(2)
        return original(*args, **kwargs)
    monkeypatch.setattr(results, 'completion_barrier', blocked)
    monkeypatch.setattr(completion, 'completion_barrier', blocked)
    async with maker() as session:
        parent = await session.get(Job, 'md-job-1')
        task = asyncio.create_task(completion.validate_and_finalize_md_job(parent, session))
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(.005)
            assert entered.is_set() and state['thread'] != threading.get_ident()
            async with maker() as controller:
                current = await controller.get(Job, parent.id)
                if control == 'cancelled':
                    current.status = current.queue_status = 'cancelled'
                    (await controller.get(MdRun, parent.id)).phase = 'cancelled'
                else:
                    current.awaiting_input = True
                await controller.commit()
        finally:
            release.set()
        with pytest.raises(results.MDResultError, match='ownership changed'):
            await task
        await session.rollback()
    assert not state['timeout']
    async with maker() as observer:
        parent = await observer.get(Job, 'md-job-1')
        assert parent.status == ('cancelled' if control == 'cancelled' else 'running')
        assert parent.awaiting_input is (control == 'awaiting_input')
        assert list((await observer.scalars(select(JobArtifact))).all()) == []
