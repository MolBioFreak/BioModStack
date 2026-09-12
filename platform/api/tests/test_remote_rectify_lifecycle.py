"""File SQLite + real worker control/checkpoint compiler; no SSH or model science.

Only transport, capacity inventory and final native executable are doubles.
The return lane's independently implemented local writer barrier is injected at
its declared interface; integrated return tests exercise its real implementation.
"""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from component_runtime import (ComponentRuntime, NativeComponent, NativeInvocation,
    SelectedExecutionMetadata, SelectedExecutionPlan, SourceIdentity, canonical_bytes)
from database import Base, ExecutionTarget, Job, get_session
from routers import jobs
from services import nextflow
from services.remote_execution import executor as ex, targets
from tools import bms_remote_worker as worker


@pytest_asyncio.fixture
async def lane(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root))
    from scripts.open_stage_gate import open_component_gate, component_checkpoint_projection
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'host.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(ex, 'async_session', factory)
    monkeypatch.setattr(ex, 'get_data_root', lambda: tmp_path)
    local_barrier = AsyncMock(return_value=True)
    monkeypatch.setattr(ex, 'cancel_local_result_transfer', local_barrier, raising=False)
    worker_cancel = ex.cancel_remote_job
    async def owned_cancel(job, *, guard_owned):
        assert guard_owned is True
        # This lane retains the original worker actuator; the return lane owns
        # its added local-transfer prelude. Validate the new controller contract
        # while exercising actual original worker stop/status transport here.
        return await worker_cancel(job)
    monkeypatch.setattr(ex, 'cancel_remote_job', owned_cancel)
    attempt = tmp_path / 'attempt'
    attempt.mkdir()
    artifacts = attempt / 'results'
    artifacts.mkdir()
    identity = SourceIdentity('a'*40, 'b'*40)
    runtime = ComponentRuntime(attempt / 'ledger.sqlite', artifact_root=artifacts,
        attempt_id='attempt', root_job_id='job', target_id='target', lease_id='original',
        source_identity=identity.__dict__)
    candidates = tmp_path / 'candidates'
    candidates.mkdir()
    (candidates / 'one.pdb').write_text('native retained structure bytes')
    opened = open_component_gate(runtime, job_id='job', stage='post_fampnn', payload={},
        directories={'candidate': candidates})
    boot = worker.boot_id()
    runtime.claim_root(owner_id='old', boot_id=boot)
    runtime.set_root_state('paused', owner_id='old', boot_id=boot, quiescent=True)
    checkpoint = component_checkpoint_projection(runtime)[0]
    devices = [dict(gpu_index=0, gpu_uuid='GPU-original')]
    admission = dict(schema='bms.target-resource-admission.v1', execution_target_id='target',
        devices=devices, required=dict(cpus=1, memory_bytes=1, scratch_bytes=0),
        available=dict(cpus=8, memory_bytes=16*1024**3, scratch_bytes=100000))
    resources = dict(gpu_ids=[0], required=dict(cpus=4, memory_bytes=12*1024**3, scratch_bytes=0),
        admission=deepcopy(admission))
    context = dict(attempt_id='attempt', root_job_id='job', target_id='target', lease_id='original',
        source_identity=identity.__dict__, artifact_root=str(artifacts), ledger_path=str(attempt / 'ledger.sqlite'),
        working_directory=str(root), resources=resources,
        parent=dict(id='job', model_id='antibody_design', mode='denovo', params={}, output_dir=str(artifacts)))
    context_path = attempt / 'context.json'
    context_path.write_text(json.dumps(context))
    worker.atomic_json(worker.envelope_path(attempt), dict(attempt_id='attempt', job_id='job',
        output_directory=str(artifacts), working_directory=str(root),
        environment={'BMS_COMPONENT_CONTEXT': str(context_path)}, files=[dict(relative_path='inputs/component-context.json',
            size_bytes=context_path.stat().st_size, sha256=worker.sha256_file(context_path))]))
    worker.atomic_json(worker.status_path(attempt), dict(attempt_id='attempt', job_id='job',
        boot_id=boot, generation=0, state='awaiting_input', quiescent=True, checkpoints=[checkpoint]))
    epoch = datetime.utcnow()
    receipt = dict(attempt_id='attempt', boot_id=boot, generation=0, source_revision='a'*40,
        source_tree='b'*40, execution_envelope_sha256='c'*64, lease_acquired_at=epoch.isoformat(),
        component_context_identity={k: context[k] for k in ('attempt_id', 'root_job_id', 'target_id', 'lease_id')})
    async with factory() as session:
        session.add(ExecutionTarget(id='target', provider='vast', provider_instance_id='1', active=True,
            state='ready', leased_job_id=None, lease_acquired_at=None))
        session.add(Job(id='job', name='job', model_id='antibody_design', mode='denovo', params={},
            status='awaiting_input', queue_status='completed', awaiting_input=True, awaiting_stage='post_fampnn',
            awaiting_payload={'component_checkpoint': checkpoint, 'component_checkpoints': [checkpoint]},
            execution_target_id='target', remote_attempt_id='attempt', nextflow_run_id='remote:attempt',
            remote_state='awaiting_input', execution_source_revision='a'*40, execution_source_tree='b'*40,
            execution_bundle_sha256='c'*64, output_dir=str(tmp_path / 'host-results'),
            provenance=dict(remote_execution_receipt=receipt, remote_execution_assignment={'resources': resources})))
        await session.commit()
    async def ready(session, *_):
        return await session.get(ExecutionTarget, 'target', populate_existing=True)
    async def capacity(*args, **kwargs):
        return deepcopy(admission)
    monkeypatch.setattr(targets, 'get_ready_target', ready)
    monkeypatch.setattr(targets, 'admit_target_resources', capacity)
    monkeypatch.setattr(ex, '_connection_for_attempt', lambda *_: (None, str(attempt)))
    monkeypatch.setattr(ex, '_worker_argv', lambda connection, command, directory, *args: [command, '--attempt-dir', directory, *args])
    # Preserve the real checkpoint selection compiler and real resource admission;
    # substitute only its final native scientific compiler/executable boundary.
    def native(model, mode, params, output, **kwargs):
        component = NativeComponent(component_key='native', authority='fixture:native', selection_json=b'{}',
            resources_json=canonical_bytes(dict(cpus=dict(value=4), memory=dict(value='12 GB'), gpu=dict(count=1))))
        metadata = SelectedExecutionMetadata('fixture', 'fixture', (component,), (), (), (), (), b'{}', None, None, ())
        plan = SelectedExecutionPlan(identity, 'fixture', model, mode, 'fixture.nf',
            canonical_bytes(kwargs.get('requested_params', params)), canonical_bytes(params),
            canonical_bytes({**params, 'out_dir': str(output)}), metadata)
        return replace(NativeInvocation.capture(model_id=model, mode=mode, command=['never-model-science'],
            requested=kwargs.get('requested_params', params), effective=params,
            native_parameters={**params, 'out_dir': str(output)}, entrypoint='fixture.nf'),
            source_identity=identity, execution_plan=plan)
    monkeypatch.setattr(nextflow, 'compile_nextflow_invocation', native)
    spawns = []
    def spawn(*args, **kwargs):
        # Model a live supervisor identity; real lock/claim duplicate execution
        # is independently exercised by the process test below.
        spawns.append(args)
        current = worker.load_json(worker.status_path(attempt))
        current.update(state='running', supervisor_pid=os.getpid(),
            supervisor_start_ticks=worker.process_start_ticks(os.getpid()), started_at=worker.utc_now())
        worker.atomic_json(worker.status_path(attempt), current)
        return SimpleNamespace(pid=os.getpid())
    real_popen = worker.subprocess.Popen  # current test's guarded native boundary
    monkeypatch.setattr(worker.subprocess, 'Popen', spawn)
    calls = []
    async def transport(connection, argv, **kwargs):
        args = worker.parser().parse_args(argv)
        calls.append(args.command)
        if args.command in {'checkpoint-status', 'checkpoint-resume'}:
            result = worker.checkpoint_control(attempt, attempt_id=args.attempt_id,
                expected_boot_id=args.expected_boot_id, lease_id=args.lease_id,
                checkpoint_id=args.checkpoint_id, checkpoint_sha256=args.checkpoint_sha256,
                operation_id=args.operation_id, decision=json.loads(args.decision_json),
                continuation_lease_id=args.continuation_lease_id,
                resource_admission=json.loads(args.resource_admission_json), observe_only=args.command == 'checkpoint-status')
        elif args.command == 'status':
            result = worker.status(attempt)
        elif args.command == 'cancel':
            result = worker.cancel(attempt, args.timeout_seconds)
        else:
            pytest.fail('unexpected control ' + args.command)
        return SimpleNamespace(stdout=json.dumps(result))
    monkeypatch.setattr(ex, 'run_remote', transport)
    async def review_transfer(connection, source, destination, paths, **kwargs):
        for relative in paths:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((artifacts / relative).read_bytes())
    monkeypatch.setattr(ex, 'rsync_selected_from_remote', review_transfer)
    app = FastAPI()
    app.include_router(jobs.router, prefix='/jobs')
    async def dependency():
        async with factory() as session:
            yield session
    app.dependency_overrides[get_session] = dependency
    monkeypatch.setattr(jobs, '_raise_if_workflow_launches_disabled', lambda *_: None)
    decision = dict(selected_artifacts=[r['relative_path'] for r in opened['checkpoint']['artifacts']
                                      if r['relative_path'].endswith('.pdb')])
    async with AsyncClient(transport=ASGITransport(app), base_url='http://offline') as client:
        yield SimpleNamespace(factory=factory, client=client, attempt=attempt, runtime=runtime, checkpoint=checkpoint,
            receipt=receipt, admission=admission, decision=decision, calls=calls, spawns=spawns,
            transport=transport, local_barrier=local_barrier, context_path=context_path, real_popen=real_popen)
    await engine.dispose()


async def resume(lane, decision):
    return await lane.client.post('/jobs/job/resume', json=dict(checkpoint_id=lane.checkpoint['checkpoint_id'],
        checkpoint_sha256=lane.checkpoint['checkpoint_sha256'], checkpoint_decision=decision))


@pytest.mark.asyncio
async def test_explicit_resume_cannot_overwrite_committed_cancellation(lane):
    async with lane.factory() as session:
        job = await session.get(Job, 'job')
        job.queue_status = 'cancelling'
        await session.commit()
    response = await resume(lane, lane.decision)
    assert response.status_code == 409, response.text
    assert not lane.calls and not lane.spawns
    async with lane.factory() as session:
        assert (await session.get(Job, 'job')).queue_status == 'cancelling'
        assert (await session.get(ExecutionTarget, 'target')).leased_job_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal', ['cancelled', 'failed', 'completed'])
async def test_terminal_root_cannot_replay_a_pending_checkpoint(lane, monkeypatch, terminal):
    async def disconnected(*args, **kwargs):
        raise ex.RemoteTransportError('post-intent disconnect')
    monkeypatch.setattr(ex, 'run_remote', disconnected)
    assert (await resume(lane, lane.decision)).status_code == 409
    async with lane.factory() as session:
        job = await session.get(Job, 'job')
        job.status = job.queue_status = terminal
        await session.commit()
    monkeypatch.setattr(ex, 'run_remote', lane.transport)
    async with lane.factory() as session:
        assert not await ex.reconcile_remote_job(session, await session.get(Job, 'job'))
    assert not lane.calls and not lane.spawns
    async with lane.factory() as session:
        assert (await session.get(Job, 'job')).status == terminal


@pytest.mark.asyncio
@pytest.mark.parametrize('denial', ['shape', 'foreign', 'cpus', 'memory_bytes'])
async def test_rejected_http_continuation_is_correctable_and_releases_reservation(lane, denial):
    decision = lane.decision
    if denial == 'shape': decision = {'continue': True}
    elif denial == 'foreign': decision = {'selected_artifacts': ['foreign.pdb']}
    else: lane.admission['available'][denial] = 1
    response = await resume(lane, decision)
    assert response.status_code == 409, response.text
    assert 'rejected' in response.text
    async with lane.factory() as session:
        job = await session.get(Job, 'job')
        assert job.awaiting_input and job.status == 'awaiting_input'
        assert job.provenance['remote_execution_receipt']['generation'] == 0
        assert job.provenance['remote_checkpoint_operation']['state'] == 'rejected'
        first = job.provenance['remote_checkpoint_operation']['binding']['operation_id']
        assert (await session.get(ExecutionTarget, 'target')).leased_job_id is None
    assert lane.runtime.root_state()['state'] == 'paused'
    assert lane.runtime.checkpoint_status(lane.checkpoint['checkpoint_id'])['decision'] is None
    assert not lane.spawns
    lane.admission['available'].update(cpus=8, memory_bytes=16*1024**3)
    response = await resume(lane, lane.decision)
    assert response.status_code == 200, response.text
    async with lane.factory() as session:
        job = await session.get(Job, 'job')
        assert job.provenance['remote_checkpoint_operation']['binding']['operation_id'] != first
        assert job.provenance['remote_execution_receipt']['generation'] == 1
        assert (await session.get(ExecutionTarget, 'target')).leased_job_id == 'job'
    assert len(lane.spawns) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('crash', ['host-intent', 'ledger', 'status', 'claim', 'lost-response'])
async def test_durable_same_operation_recovers_crash_boundaries(lane, monkeypatch, crash):
    original_transport = lane.transport
    original_write = worker._write_atomic_json
    original_atomic = worker.atomic_json
    original_resume = ComponentRuntime.resume_checkpoint
    tripped = False
    def fail_once():
        nonlocal tripped
        if not tripped:
            tripped = True
            raise ex.RemoteTransportError('controller/worker connection lost at ' + crash)
    async def transport(connection, argv, **kwargs):
        if crash == 'host-intent': fail_once()
        result = await original_transport(connection, argv, **kwargs)
        if crash == 'lost-response' and argv[0] == 'checkpoint-resume': fail_once()
        return result
    def authorize(self, *args, **kwargs):
        result = original_resume(self, *args, **kwargs)
        if crash == 'ledger': fail_once()
        return result
    def write(path, value):
        original_write(path, value)
        if crash == 'status' and path == worker.status_path(lane.attempt) and value.get('state') == 'prepared': fail_once()
    def atomic(path, value):
        result = original_atomic(path, value)
        if crash == 'claim' and path.name.startswith('checkpoint-launch-'): fail_once()
        return result
    monkeypatch.setattr(ex, 'run_remote', transport)
    monkeypatch.setattr(ComponentRuntime, 'resume_checkpoint', authorize)
    monkeypatch.setattr(worker, '_write_atomic_json', write)
    monkeypatch.setattr(worker, 'atomic_json', atomic)
    response = await resume(lane, lane.decision)
    assert response.status_code == 409, response.text
    assert tripped
    async with lane.factory() as session:
        job = await session.get(Job, 'job')
        intent = deepcopy(job.provenance['remote_checkpoint_operation'])
        assert job.provenance['remote_execution_receipt']['generation'] == 0
        assert (await session.get(ExecutionTarget, 'target')).leased_job_id == 'job'
    assert (await resume(lane, {'continue': True})).status_code == 409
    # Fresh independent physical DB connection simulates restarted poller.
    async with lane.factory() as session:
        assert await ex.reconcile_remote_job(session, await session.get(Job, 'job'))
    async with lane.factory() as session:
        job = await session.get(Job, 'job')
        assert job.provenance['remote_checkpoint_operation']['binding'] == intent['binding']
        assert job.provenance['remote_checkpoint_operation']['state'] == 'accepted'
        assert job.provenance['remote_execution_receipt']['generation'] == 1
    assert len(lane.spawns) == 1
    assert lane.runtime.root_state()['generation'] == 1
    binding = intent['binding']
    with pytest.raises(RuntimeError, match='immutable'):
        worker.checkpoint_control(lane.attempt, attempt_id=binding['attempt_id'],
            expected_boot_id=binding['boot_id'], lease_id=binding['original_lease_id'],
            checkpoint_id=binding['checkpoint_id'], checkpoint_sha256=binding['checkpoint_sha256'],
            decision={'continue': True}, continuation_lease_id=binding['continuation_lease_id'],
            resource_admission=binding['resource_admission'], operation_id=binding['operation_id'])


@pytest.mark.asyncio
@pytest.mark.parametrize('foreign', [None, 'attempt', 'boot', 'generation'])
async def test_first_checkpoint_observation_persists_boot_and_can_continue(lane, foreign):
    async with lane.factory() as session:
        job = await session.get(Job, 'job')
        job.status = job.queue_status = 'running'
        job.awaiting_input = False
        job.remote_state = 'launch_uncertain'
        receipt = dict(lane.receipt)
        if foreign != 'boot': receipt.pop('boot_id')
        else: receipt['boot_id'] = 'foreign-boot'
        job.provenance = dict(job.provenance, remote_execution_receipt=receipt)
        target = await session.get(ExecutionTarget, 'target')
        target.leased_job_id, target.lease_acquired_at = 'job', datetime.fromisoformat(receipt['lease_acquired_at'])
        await session.commit()
    if foreign in {'attempt', 'generation'}:
        status = worker.load_json(worker.status_path(lane.attempt))
        status['attempt_id' if foreign == 'attempt' else 'generation'] = 'foreign' if foreign == 'attempt' else 3
        worker._write_atomic_json(worker.status_path(lane.attempt), status)
    async with lane.factory() as session:
        if foreign:
            with pytest.raises((RuntimeError, ex.RemoteExecutionError)):
                await ex.reconcile_remote_job(session, await session.get(Job, 'job'))
        else:
            assert await ex.reconcile_remote_job(session, await session.get(Job, 'job'))
    async with lane.factory() as session:
        job = await session.get(Job, 'job')
        target = await session.get(ExecutionTarget, 'target')
        if foreign:
            assert not job.awaiting_input and target.leased_job_id == 'job'
            return
        assert job.awaiting_input and target.leased_job_id is None
        assert job.provenance['remote_execution_receipt']['boot_id'] == worker.boot_id()
        assert job.awaiting_payload['component_checkpoint'] == lane.checkpoint
        review = await ex.retrieve_remote_checkpoint_review(session, job, lane.checkpoint)
        assert (review / lane.decision['selected_artifacts'][0]).read_text() == 'native retained structure bytes'
    assert (await resume(lane, lane.decision)).status_code == 200
    assert len(lane.spawns) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('race', [None, 'lease', 'source', 'local-writer'])
async def test_cancel_intent_redelivers_original_attempt_and_never_releases_successor(lane, monkeypatch, race):
    from services import job_control
    from fastapi import HTTPException
    async def lost(*args, **kwargs):
        return False
    monkeypatch.setattr(job_control, 'cancel_nextflow_job', lost)
    # The production cancellation core commits before its failed actuator.
    async with lane.factory() as session:
        job = await session.get(Job, 'job')
        job.status = job.queue_status = 'running'
        job.awaiting_input = False
        job.remote_state = 'running'
        target = await session.get(ExecutionTarget, 'target')
        target.leased_job_id = 'job'
        target.lease_acquired_at = datetime.fromisoformat(lane.receipt['lease_acquired_at'])
        await session.commit()
        with pytest.raises(HTTPException):
            await job_control.cancel_job_lineage('job', session)
    calls = 0
    async def transport(connection, argv, **kwargs):
        nonlocal calls
        if argv[0] == 'cancel':
            calls += 1
            if calls == 1:
                raise ex.RemoteTransportError('SSH failed before delivery')
            if race in {'lease', 'source'}:
                async with lane.factory() as other:
                    if race == 'lease':
                        (await other.get(ExecutionTarget, 'target')).leased_job_id = 'successor'
                    else:
                        (await other.get(Job, 'job')).execution_source_tree = 'd'*40
                    await other.commit()
        return await lane.transport(connection, argv, **kwargs)
    monkeypatch.setattr(ex, 'run_remote', transport)
    async with lane.factory() as session:
        assert not await ex.reconcile_remote_job(session, await session.get(Job, 'job'))
    async with lane.factory() as session:
        assert (await session.get(ExecutionTarget, 'target')).leased_job_id == 'job'
        assert (await session.get(Job, 'job')).queue_status == 'cancelling'
    if race == 'local-writer': lane.local_barrier.return_value = False
    async with lane.factory() as session:
        changed = await ex.reconcile_remote_job(session, await session.get(Job, 'job'))
        assert changed is (race is None)
    assert calls == 2
    assert 'cancel' in lane.calls
    async with lane.factory() as session:
        job = await session.get(Job, 'job')
        target = await session.get(ExecutionTarget, 'target')
        if race is None:
            assert job.status == 'cancelled' and target.leased_job_id is None
            assert lane.local_barrier.await_args.args[0].id == job.id
            assert lane.local_barrier.await_args.kwargs == {'guard_owned': True}
        else:
            assert job.queue_status == 'cancelling'
            assert target.leased_job_id == ('successor' if race == 'lease' else 'job')


@pytest.mark.asyncio
@pytest.mark.parametrize('crash', ['ledger', 'status', 'claim'])
@pytest.mark.parametrize('cancel_after', [False, True])
async def test_worker_process_exit_leaves_replayable_operation(lane, monkeypatch, crash, cancel_after):
    import multiprocessing
    # Stop the host after its durable intent and before sending a command.
    async def disconnected(*args, **kwargs):
        raise ex.RemoteTransportError('post-intent controller exit')
    monkeypatch.setattr(ex, 'run_remote', disconnected)
    assert (await resume(lane, lane.decision)).status_code == 409
    async with lane.factory() as session:
        intent = (await session.get(Job, 'job')).provenance['remote_checkpoint_operation']
    binding = intent['binding']
    def child():
        original_resume = ComponentRuntime.resume_checkpoint
        original_write = worker._write_atomic_json
        original_atomic = worker.atomic_json
        def authorize(self, *args, **kwargs):
            result = original_resume(self, *args, **kwargs)
            if crash == 'ledger': os._exit(71)
            return result
        def write(path, value):
            original_write(path, value)
            if crash == 'status' and path == worker.status_path(lane.attempt) and value.get('state') == 'prepared':
                os._exit(72)
        def atomic(path, value):
            result = original_atomic(path, value)
            if crash == 'claim' and path.name.startswith('checkpoint-launch-'):
                os._exit(73)
            return result
        ComponentRuntime.resume_checkpoint = authorize
        worker._write_atomic_json = write
        worker.atomic_json = atomic
        worker.checkpoint_control(lane.attempt, attempt_id=binding['attempt_id'], expected_boot_id=binding['boot_id'],
            lease_id=binding['original_lease_id'], checkpoint_id=binding['checkpoint_id'],
            checkpoint_sha256=binding['checkpoint_sha256'], decision=binding['decision'],
            continuation_lease_id=binding['continuation_lease_id'], resource_admission=binding['resource_admission'],
            operation_id=binding['operation_id'])
        os._exit(99)
    process = multiprocessing.get_context('fork').Process(target=child)
    process.start()
    process.join(10)
    if process.is_alive():
        process.kill()
        process.join(5)
        pytest.fail('worker crash probe did not exit')
    assert process.exitcode == {'ledger': 71, 'status': 72, 'claim': 73}[crash]
    assert lane.runtime.checkpoint_operation(binding['operation_id'])['state'] == 'accepted'
    assert lane.runtime.root_state()['generation'] == 1
    monkeypatch.setattr(ex, 'run_remote', lane.transport)
    if cancel_after:
        async with lane.factory() as session:
            job = await session.get(Job, 'job')
            job.queue_status = 'cancelling'
            await session.commit()
    async with lane.factory() as session:
        assert await ex.reconcile_remote_job(session, await session.get(Job, 'job'))
    async with lane.factory() as session:
        job = await session.get(Job, 'job')
        assert job.status == ('cancelled' if cancel_after else 'running')
        if cancel_after:
            assert (await session.get(ExecutionTarget, 'target')).leased_job_id is None
    assert len(lane.spawns) == (0 if cancel_after else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['capacity', 'devices', None])
async def test_recovered_prepared_launch_rechecks_current_resources(lane, monkeypatch, change):
    status = worker.load_json(worker.status_path(lane.attempt))
    status.update(state='prepared', quiescent=False, checkpoints=[])
    worker._write_atomic_json(worker.status_path(lane.attempt), status)
    async with lane.factory() as session:
        job = await session.get(Job, 'job')
        job.status, job.queue_status, job.remote_state, job.awaiting_input = 'queued', 'preparing', 'prepared', False
        target = await session.get(ExecutionTarget, 'target')
        target.leased_job_id = 'job'
        target.lease_acquired_at = datetime.fromisoformat(lane.receipt['lease_acquired_at'])
        await session.commit()
    monkeypatch.setattr(ex, '_verify_launch_runner', AsyncMock())
    requirements = []
    async def capacity(target, **kwargs):
        requirements.append(kwargs)
        if change == 'capacity':
            raise ex.ExecutionTargetError('current capacity insufficient')
        admission = deepcopy(lane.admission)
        if change == 'devices': admission['devices'][0]['gpu_uuid'] = 'replacement'
        return admission
    monkeypatch.setattr(targets, 'admit_target_resources', capacity)
    runs = []
    async def transport(connection, argv, **kwargs):
        if argv[0] != 'run': return await lane.transport(connection, argv, **kwargs)
        runs.append(argv)
        return SimpleNamespace(stdout=json.dumps(dict(status, state='running', started_at=worker.utc_now())))
    monkeypatch.setattr(ex, 'run_remote', transport)
    async with lane.factory() as session:
        if change:
            with pytest.raises((ex.ExecutionTargetError, ex.RemoteExecutionError), match='capacity|devices'):
                await ex.reconcile_remote_job(session, await session.get(Job, 'job'))
        else:
            assert await ex.reconcile_remote_job(session, await session.get(Job, 'job'))
    assert requirements == [dict(required_cpus=4, required_memory_bytes=12*1024**3,
        required_scratch_bytes=0, gpu_ids=[0], minimum_gpu_memory_mb=0)]
    assert len(runs) == (0 if change else 1)
    async with lane.factory() as session:
        assert (await session.get(ExecutionTarget, 'target')).leased_job_id == 'job'


@pytest.mark.asyncio
async def test_cancel_redelivery_stops_real_worker_writer_after_controller_restart(lane, monkeypatch):
    import sys
    import tarfile
    from services import job_control
    from fastapi import HTTPException
    monkeypatch.setattr(worker.subprocess, 'Popen', lane.real_popen)
    source = lane.attempt / 'bundle/source'
    source.mkdir(parents=True)
    archive = source / '.bms-source.tar'
    with tarfile.open(archive, 'w'):
        pass
    output = lane.attempt / 'results'
    marker = output / 'writer.txt'
    script = ("import time; from pathlib import Path; p=Path(" + repr(str(marker)) + "); "
              "p.write_text('started')\nwhile True:\n with p.open('a') as f: f.write('x')\n time.sleep(.02)")
    envelope = dict(schema='bms.remote-execution.v1', job_id='job', attempt_id='attempt',
        source_revision='a'*40, source_tree='b'*40, source_archive_sha256=worker.sha256_file(archive),
        files=[dict(relative_path='source/.bms-source.tar', sha256=worker.sha256_file(archive),
            size_bytes=archive.stat().st_size, mode=archive.stat().st_mode & 0o777)],
        command=[sys.executable, '-c', script], working_directory=str(source),
        output_directory=str(output), environment={})
    worker.atomic_json(worker.envelope_path(lane.attempt), envelope)
    worker._write_atomic_json(worker.status_path(lane.attempt), worker.base_status(envelope, 'prepared'))
    process = lane.real_popen([sys.executable, worker.__file__, 'supervise', '--attempt-dir', str(lane.attempt)],
        stdout=worker.subprocess.PIPE, stderr=worker.subprocess.PIPE, text=True)
    try:
        for _ in range(100):
            if marker.exists(): break
            if process.poll() is not None:
                pytest.fail(str(process.communicate()))
            await asyncio.sleep(.05)
        assert marker.exists()
        async with lane.factory() as session:
            job = await session.get(Job, 'job')
            job.status = job.queue_status = job.remote_state = 'running'
            job.awaiting_input = False
            target = await session.get(ExecutionTarget, 'target')
            target.leased_job_id = 'job'
            target.lease_acquired_at = datetime.fromisoformat(lane.receipt['lease_acquired_at'])
            await session.commit()
            monkeypatch.setattr(job_control, 'cancel_nextflow_job', AsyncMock(return_value=False))
            with pytest.raises(HTTPException):
                await job_control.cancel_job_lineage('job', session)
        # End the original controller Session; the writer is demonstrably live.
        before = marker.stat().st_size
        await asyncio.sleep(.1)
        assert marker.stat().st_size > before
        async with lane.factory() as session:
            assert await ex.reconcile_remote_job(session, await session.get(Job, 'job'))
        process.wait(timeout=10)
        status = worker.status(lane.attempt)
        assert status['state'] == 'cancelled' and status['quiescent']
        stopped = marker.stat().st_size
        await asyncio.sleep(.1)
        assert marker.stat().st_size == stopped
        async with lane.factory() as session:
            assert (await session.get(Job, 'job')).status == 'cancelled'
            assert (await session.get(ExecutionTarget, 'target')).leased_job_id is None
        assert lane.calls.count('cancel') == 1
    finally:
        if process.poll() is None:
            worker.cancel(lane.attempt, .1)
            process.wait(timeout=15)
        process.communicate(timeout=5)
