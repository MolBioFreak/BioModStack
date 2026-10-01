"""Receiving proofs with scratch stores and inert process/transport leaves."""
from __future__ import annotations

import hashlib
import json
import signal
import sqlite3
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[3]
for path in (ROOT, ROOT / 'platform/api'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from database import Base, Job, JobArtifact, MdAttemptSegment, MdCheckpoint, MdReplicaRun, MdRun
from services.md.cancel_actuator import cancel_running_md_run
from services.md.pause_actuator import pause_running_md_run
from services.md.read_model import md_run_snapshot
from services.md.state import canonical_sha256, create_md_run, create_replica_attempt, reconcile_component_projection
from scripts.bms_md import checkpointing_runner as runner


@pytest_asyncio.fixture
async def store(tmp_path):
    path = tmp_path / 'controls.sqlite'
    engine = create_async_engine(f'sqlite+aiosqlite:///{path}')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session, path
    await engine.dispose()


def request():
    return {'schema': 'bms.md.job.v2', 'chemistry': {
        'profile_id': 'retained-profile', 'profile_sha256': 'a' * 64, 'assurance': 'curated_profile'}}


async def setup(store, *, status='running', shared=True, count=1, runner_id='root-run'):
    session, path = store
    parent = Job(id='root', name='MD', model_id='molecular_dynamics', mode='simulate',
                 status=status, queue_status=status, nextflow_run_id=runner_id, params={},
                 provenance={'component_context_path': '/retained/context.json'} if shared else {})
    session.add(parent)
    await session.flush()
    run = await create_md_run(session, job=parent, normalized_request=request())
    run.phase = 'replicas_running' if status == 'running' else 'replicas_queued'
    rows = []
    for index in range(count):
        child = Job(id=f'child-{index}', name='replica', model_id='molecular_dynamics', mode='replica',
                    parent_job_id=parent.id, child_stage='md_replica', status=status, queue_status=status,
                    params={}, provenance={'component_projection': {'root_job_id': parent.id,
                    'component_id': f'component-{index}', 'state': status}} if shared else {})
        session.add(child)
        await session.flush()
        replica, segment = await create_replica_attempt(session, job_id=parent.id, child_job_id=child.id,
            replica_index=index, attempt=0, engine='gromacs', execution_plan_sha256='b' * 64,
            compatibility_key='c' * 64)
        replica.state = segment.state = status
        rows.append((child, replica, segment))
    await session.commit()
    return parent, run, rows


@pytest.mark.asyncio
@pytest.mark.parametrize('status,projected', [('queued', True), ('queued', False), ('running', True), ('running', False)])
async def test_shared_cancel_uses_real_lineage_root_owner(store, monkeypatch, status, projected):
    session, path = store
    parent, run, rows = await setup(store, status=status, count=int(projected),
                                    runner_id='root-run' if status == 'running' else None)
    calls = []
    async def stop(identity):
        calls.append(identity)
        # Native intent is committed before physical stop and no writer retained.
        with sqlite3.connect(path, timeout=0.1) as conn:
            assert conn.execute('select phase from md_runs').fetchone()[0] == 'cancelling'
            conn.execute("update jobs set error_message='stop-observation' where id='root'")
        return True
    monkeypatch.setattr('services.job_control.cancel_nextflow_job', stop)
    result = await cancel_running_md_run(session, job_id='root', expected_version=0, idempotency_key='cancel')
    await session.commit()
    assert result.phase == 'cancelled'
    assert calls == (['root-run'] if status == 'running' else [])
    session.expunge_all()
    assert (await session.get(Job, 'root')).status == 'cancelled'
    for child, replica, segment in rows:
        actual = await session.get(MdReplicaRun, replica.id)
        assert actual.state == 'cancelled' and actual.completed_at is not None
        assert (await session.get(MdAttemptSegment, segment.id)).completed_at is not None
        assert (await session.get(Job, child.id)).nextflow_run_id is None


@pytest.mark.asyncio
async def test_shared_cancel_retains_unverified_stop_as_orphan_not_queue(store, monkeypatch):
    session, _ = store
    parent, run, rows = await setup(store)
    async def stop(identity):
        return False
    monkeypatch.setattr('services.job_control.cancel_nextflow_job', stop)
    await cancel_running_md_run(session, job_id='root', expected_version=0, idempotency_key='uncertain-stop')
    await session.commit()
    session.expunge_all()
    replica = await session.get(MdReplicaRun, rows[0][1].id)
    assert replica.state == 'orphaned' and replica.active is False
    assert replica.failure['code'] == 'cancel_stop_unverified'
    parent = await session.get(Job, 'root')
    assert parent.params['cancellation_receipt']['remote_stop_verified'] is False


@pytest.mark.asyncio
async def test_historical_cancel_fresh_read_mapped_timestamps(store):
    session, _ = store
    parent, run, rows = await setup(store, shared=False)
    child, replica, segment = rows[0]
    child.nextflow_run_id = 'independent-run'
    await session.commit()
    calls = []
    async def stop(identity):
        calls.append(identity)
        return True
    await cancel_running_md_run(session, job_id='root', expected_version=0,
                                idempotency_key='historic', cancel_worker=stop)
    await session.commit()
    session.expunge_all()
    assert calls == ['root-run', 'independent-run']
    assert (await session.get(MdReplicaRun, replica.id)).completed_at is not None
    assert (await session.get(MdAttemptSegment, segment.id)).completed_at is not None


@pytest.mark.asyncio
async def test_two_checkpoint_receivers_release_writer_and_retain_first_on_restart(store, tmp_path, monkeypatch):
    session, path = store
    parent, run, rows = await setup(store, shared=False, count=2)
    for index, (child, _, _) in enumerate(rows):
        child.nextflow_run_id = f'worker-{index}'
        child.stage_work_dir = str(tmp_path / f'work-{index}')
        (Path(child.stage_work_dir) / f'replica_{index}').mkdir(parents=True)
        child.params = {'md_replica_index': index}
    await session.commit()
    calls = []
    fail_second = True
    async def receipt(roots, **kwargs):
        index = int(roots[0].name.rsplit('_', 1)[1])
        with sqlite3.connect(path, timeout=0.1) as conn:
            conn.execute("update jobs set error_message='concurrent' where id='root'")
            if index == 1:
                assert conn.execute('select count(*) from md_checkpoints').fetchone()[0] == 1
        if index == 1 and fail_second:
            return False
        checkpoint = roots[0] / 'production/production.cpt'
        checkpoint.parent.mkdir()
        data = f'checkpoint-{index}'.encode()
        checkpoint.write_bytes(data)
        (roots[0] / 'md-checkpoint-receipt.json').write_text(json.dumps({
            'schema': 'bms.md.checkpoint-receipt.v1', 'checkpoint_path': 'production/production.cpt',
            'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'step': 50, 'time_ps': 0.1,
            'execution_plan_sha256': 'b' * 64, 'compatibility_key': 'c' * 64}))
        return True
    async def stop(identity):
        calls.append(identity)
        return True
    monkeypatch.setattr('services.md.pause_actuator._wait_for_receipt', receipt)
    from services.md.state import MdStateError
    with pytest.raises(MdStateError, match='post-boundary checkpoint'):
        await pause_running_md_run(session, job_id='root', expected_version=0,
                                  idempotency_key='pause', cancel_worker=stop)
    await session.rollback()
    assert (await session.get(MdRun, 'root', populate_existing=True)).phase == 'checkpointing'
    first = await session.scalar(select(MdCheckpoint))
    assert first is not None
    first_id = first.id
    fail_second = False
    paused = await pause_running_md_run(session, job_id='root', expected_version=0,
                                       idempotency_key='pause', cancel_worker=stop)
    await session.commit()
    assert paused.phase == 'paused' and calls == ['worker-0', 'worker-1']
    checkpoints = list((await session.scalars(select(MdCheckpoint))).all())
    assert len(checkpoints) == 2 and first_id in {row.id for row in checkpoints}
    artifacts = list((await session.scalars(select(JobArtifact))).all())
    assert {Path(row.storage_path).read_bytes() for row in artifacts} == {b'checkpoint-0', b'checkpoint-1'}


@pytest.mark.parametrize('exits', [False, True])
def test_single_deadline_covers_direct_child_and_remaining_group(tmp_path, monkeypatch, exits):
    (tmp_path / '.bms-pause-boundary.json').write_text('{}')
    clock = [0.0]
    signals = []
    class Child:
        pid = 777
        polls = 0
        def poll(self):
            self.polls += 1
            return 0 if exits and self.polls > 2 else None
        def wait(self):
            return -signal.SIGKILL
    child = Child()
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: child)
    monkeypatch.setattr(runner.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(runner.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(runner.os, 'killpg', lambda pg, sig: signals.append(sig))
    receipts = []
    monkeypatch.setattr(runner, 'write_checkpoint_receipt', lambda **k: receipts.append(k))
    with pytest.raises(runner.CheckpointingRunnerError, match='did not stop before timeout'):
        runner.run_checkpointable_command(command=['inert'], config_path=tmp_path / 'config',
            output_dir=tmp_path, stop_timeout_seconds=0.2)
    assert signal.SIGTERM in signals and signal.SIGKILL in signals
    assert clock[0] < 0.3 and not receipts


def test_natural_success_never_emits_pause_receipt(tmp_path, monkeypatch):
    class Child:
        pid = 777
        def poll(self):
            return 0
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: Child())
    def forbidden(**kwargs):
        raise AssertionError('natural success is not a pause')
    monkeypatch.setattr(runner, 'write_checkpoint_receipt', forbidden)
    assert runner.run_checkpointable_command(command=['inert'], config_path=tmp_path / 'config', output_dir=tmp_path) == 0


@pytest.mark.asyncio
async def test_accepted_v1_prepared_openmm_durable_receiver_and_projection(store, tmp_path):
    from services.md.launch_contract import normalize_md_job_spec
    session, _ = store
    coordinates, topology = tmp_path / 'input.pdb', tmp_path / 'input.xml'
    coordinates.write_text('inert fixture coordinates')
    topology.write_text('inert fixture topology')
    from scripts.bms_md.contract import normalize_job_config
    raw = normalize_job_config({'schema': 'bms.md.job.v1', 'job_id': 'legacy', 'engine': 'openmm',
           'replicas': 1, 'random_seed': 4321,
           'input': {'coordinates': str(coordinates), 'topology': str(topology)},
           'preparation': {'chemistry_assurance': 'external_unreviewed'},
           'stages': {name: {'enabled': False} for name in ('minimization', 'nvt', 'npt')}})
    raw['stages']['production']['timestep_fs'] = 2.0
    normalized = normalize_md_job_spec(params={'md_job_spec': raw}, job_id='legacy', resolve_runtime_path=lambda x: x)
    before = json.dumps(normalized, sort_keys=True)
    parent = Job(id='legacy', name='MD', model_id='molecular_dynamics', mode='simulate', status='running', params={})
    session.add(parent)
    await session.flush()
    run = await create_md_run(session, job=parent, normalized_request=normalized)
    child = Job(id='legacy-child', name='replica', model_id='molecular_dynamics', mode='replica',
        parent_job_id=parent.id, child_stage='md_replica', status='running', params={
        'md_replica_index': 0, 'md_attempt': 0, 'md_replica_seed': 4321, 'md_engine': 'openmm',
        'md_execution_plan_sha256': 'b' * 64, 'md_compatibility_key': 'c' * 64},
        provenance={'component_projection': {'root_job_id': parent.id, 'state': 'uncertain'}})
    session.add(child)
    await session.flush()
    await reconcile_component_projection(session, parent, [child])
    await session.commit()
    session.expunge_all()
    run = await session.get(MdRun, 'legacy')
    assert json.dumps(run.normalized_request, sort_keys=True) == before
    assert run.request_sha256 == canonical_sha256(normalized)
    assert run.chemistry_assurance == 'legacy_unavailable' and run.chemistry_profile_sha256 == 'unavailable'
    assert run.verification_status == 'not_run'
    replica = await session.scalar(select(MdReplicaRun))
    assert replica.state == 'orphaned' and not replica.active
    detail = await md_run_snapshot(session, 'legacy')
    assert detail is not None
    assert detail['engine'] == 'openmm' and detail['chemistry']['assurance'] == 'legacy_unavailable'


@pytest.mark.asyncio
async def test_remote_cancel_reaches_existing_nextflow_dispatch(store, monkeypatch):
    session, _ = store
    parent, run, rows = await setup(store, runner_id='remote:retained-attempt')
    parent.execution_target_id = 'vast:fixture'
    parent.remote_attempt_id = 'retained-attempt'
    await session.commit()
    calls = []
    async def stop(identity, **kwargs):
        calls.append((identity, kwargs))
        return True
    monkeypatch.setattr('services.remote_execution.executor.cancel_remote_run_id', stop)
    await cancel_running_md_run(session, job_id='root', expected_version=0, idempotency_key='remote-cancel')
    await session.commit()
    assert calls == [('remote:retained-attempt', {'graceful_timeout_seconds': 30.0})]
    assert run.phase == 'cancelled'


def test_real_inert_child_ignoring_term_is_killed_and_reaped(tmp_path):
    import os
    import threading
    import time
    ready = tmp_path / 'ready'
    failures = []
    def boundary():
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not ready.exists():
            failures.append('child never started')
        (tmp_path / '.bms-pause-boundary.json').write_text('{}')
    publisher = threading.Thread(target=boundary)
    publisher.start()
    code = ('import os,signal,time; from pathlib import Path; '
            'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
            f'Path({str(ready)!r}).write_text(str(os.getpid())); '
            'time.sleep(30)')
    try:
        with pytest.raises(runner.CheckpointingRunnerError, match='did not stop before timeout'):
            runner.run_checkpointable_command(command=[sys.executable, '-c', code],
                config_path=tmp_path / 'config', output_dir=tmp_path, stop_timeout_seconds=0.15)
    finally:
        publisher.join(timeout=6)
    assert not failures and not publisher.is_alive()
    with pytest.raises(ProcessLookupError):
        os.kill(int(ready.read_text()), 0)
    assert not (tmp_path / 'md-checkpoint-receipt.json').exists()


@pytest.mark.asyncio
async def test_accepted_v1_structure_preserves_legacy_profile_claims(store):
    from scripts.bms_md.contract import normalize_job_config
    from services.md.chemistry_catalog import ChemistryCatalog, RuntimeProbeResult
    from services.md.launch_contract import normalize_md_job_spec
    api = ROOT / 'platform/api'
    catalog = ChemistryCatalog(config_dir=api / 'config/md_chemistry_profiles',
        probe=lambda: RuntimeProbeResult(runtime_id='gromacs-2025.3', runtime_version='2025.3',
            available=True, asset_ids=frozenset({'amber99sb-ildn.ff'}), checked_at='2026-07-29T00:00:00Z',
            sif_sha256='97c117ea07496c0d1b13d80be84d33345b89063b47ccfb83f6cbff0145f1385b'))
    view = catalog.view()
    profile = view.get_profile('gmx_amber99sb_ildn_tip3p_smoke_v1')
    raw = normalize_job_config({'schema': 'bms.md.job.v1', 'job_id': 'legacy-structure',
        'engine': 'gromacs', 'replicas': 1, 'random_seed': 1234,
        'input': {'structure': str(api / 'tests/fixtures/md/1AKI.pdb')},
        'preparation': dict(profile['v1_preparation'], chemistry_profile_id=profile['id'],
            chemistry_profile_sha256=profile['profile_sha256'], chemistry_assurance=profile['assurance'],
            chemistry_profile_scope='smoke_auto'),
        'stages': {'production': {'steps': 5000, 'timestep_fs': 2}}})
    normalized = normalize_md_job_spec(params={'md_job_spec': raw}, job_id='legacy-structure',
        resolve_runtime_path=lambda x: x, chemistry_catalog=catalog, chemistry_view=view)
    session, _ = store
    parent = Job(id='legacy-structure', name='MD', model_id='molecular_dynamics', mode='simulate', params={})
    session.add(parent)
    await session.flush()
    await create_md_run(session, job=parent, normalized_request=normalized)
    await session.commit()
    session.expunge_all()
    run = await session.get(MdRun, parent.id)
    assert run.normalized_request == normalized
    assert run.normalized_request['preparation']['chemistry_assurance'] == 'smoke_fixture'
    assert run.chemistry_assurance == 'legacy_unavailable'
    assert run.request_sha256 == canonical_sha256(normalized)
