"""Terminal receiving call-boundary tests; not native/scientific parity.

Custody's observation implementation is a separate lane. The declaration below
is deliberately test-only: it records calls, never pretends to parse candidates.
"""
import json
import hashlib
import shutil
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import pytest
from database import Job
from fastapi import BackgroundTasks
from services import nextflow
from services.remote_execution import executor as ex
from services.remote_execution.contracts import RemoteResultManifest
from tools import bms_remote_worker as worker
from test_remote_lifecycle_gaps import store, receipt


@pytest.fixture
def observation_boundary(monkeypatch):
    module = ModuleType('services.binder_result_observation')
    module.observe_retained_binder_results = AsyncMock(return_value={})
    monkeypatch.setitem(sys.modules, module.__name__, module)
    # Any accidental scientific retry or success finalization is an error.
    async def forbidden(*args, **kwargs):
        pytest.fail('terminal observation called a launch/success owner')
    monkeypatch.setattr(ex, 'launch_remote_job', forbidden)
    monkeypatch.setattr(nextflow, 'launch_nextflow_job', forbidden)
    monkeypatch.setattr(ex, '_prove_pull_endpoint', forbidden)
    monkeypatch.setattr(ex, 'remote_status', forbidden)
    monkeypatch.setattr('services.result_state_integrity.finalize_successful_job', forbidden)
    return module.observe_retained_binder_results


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['failed', 'cancelled'])
@pytest.mark.parametrize('model', ['bindcraft2', 'boltzgen', 'rfantibody', 'ppiflow',
                                  'fampnn', 'proteinmpnn', 'binder_refinement', 'protenix', 'boltz2'])
async def test_local_terminal_receiving_boundary(store, tmp_path, observation_boundary, model, state):
    output = tmp_path / 'retained'
    output.mkdir()
    async with store() as session:
        job = await session.get(Job, 'job')
        job.execution_target_id = None
        job.model_id = model
        job.status = job.queue_status = state
        job.output_dir = str(output)
        await session.commit()
        await nextflow._observe_local_terminal_binder_results(session, job.id, str(output))
        observation_boundary.assert_awaited_once_with(session, job, output, commit=False)
        await session.commit()
    async with store() as session:
        job = await session.get(Job, 'job')
        assert (job.status, job.queue_status) == (state, state)


@pytest.mark.asyncio
@pytest.mark.parametrize('stopped', [True, False])
async def test_actual_local_cancellation_owner_observes_only_stopped_tree(
        store, tmp_path, monkeypatch, observation_boundary, stopped):
    from services import job_control

    monkeypatch.setattr(job_control, 'cancel_nextflow_job', AsyncMock(return_value=stopped))
    async with store() as session:
        job = await session.get(Job, 'job')
        job.execution_target_id = None
        job.remote_attempt_id = job.remote_state = None
        job.nextflow_run_id = 'inert-local-unit'
        job.output_dir = str(tmp_path)
        await session.commit()
        await job_control.cancel_job_lineage(job.id, session)
    async with store() as session:
        job = await session.get(Job, 'job')
        assert (job.status, job.queue_status) == ('cancelled', 'cancelled')
    assert observation_boundary.await_count == int(stopped)


@pytest.mark.asyncio
async def test_local_parser_failure_cannot_rollback_terminal_state(store, tmp_path, observation_boundary):
    async with store() as session:
        job = await session.get(Job, 'job')
        job.execution_target_id = None
        job.status = job.queue_status = 'failed'
        job.output_dir = str(tmp_path)
        await session.flush()
        observation_boundary.side_effect = ValueError('inert parser failure')
        await nextflow._observe_local_terminal_binder_results(session, job.id, str(tmp_path))
        await session.commit()
    async with store() as session:
        assert (await session.get(Job, 'job')).status == 'failed'


@pytest.mark.asyncio
@pytest.mark.parametrize('state,target', [('running', None), ('completed', None), ('failed', 'target')])
async def test_local_boundary_does_not_observe_other_owners(store, tmp_path, observation_boundary, state, target):
    async with store() as session:
        job = await session.get(Job, 'job')
        job.status, job.execution_target_id = state, target
        await session.commit()
        await nextflow._observe_local_terminal_binder_results(session, job.id, str(tmp_path))
        observation_boundary.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['failed', 'cancelled'])
@pytest.mark.parametrize('generation', [0, 2])
async def test_worker_manifest_offline_return_preserves_all_samples_and_current_root(
        store, tmp_path, observation_boundary, state, generation):
    """Real worker manifest + copied local transport + authenticated receiver.

    These are explicit inert transport records, NOT native writer/parser output.
    Every initial/selected model sample is accounted for, including old generations.
    """
    attempt = tmp_path / 'worker'
    source = attempt / 'results'
    source.mkdir(parents=True)
    native = source / 'generation-2' if generation else source
    names = [f'{model}/sample-{n}/inert.json'
             for model in ('bindcraft2', 'boltzgen', 'rfantibody', 'ppiflow',
                           'fampnn', 'proteinmpnn', 'binder_refinement', 'protenix', 'boltz2')
             for n in range(4)]
    for name in names:
        path = native / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'fixture': 'inert transport only', 'sample': name}))
    if generation:
        (source / 'older-generation.json').write_text('{"fixture":"retained history"}')
    envelope = dict(output_directory=str(source), attempt_id='attempt', job_id='job',
                    source_revision='a' * 40, source_tree='b' * 40)
    worker.envelope_path(attempt).write_text(json.dumps(envelope))
    raw = worker.build_result_manifest(attempt, envelope, 1, generation=generation)
    encoded = json.dumps(raw).encode()
    (source / 'result-manifest.json').write_bytes(encoded)
    status = receipt(state).model_copy(update=dict(exit_code=1, generation=generation,
        completed_at=RemoteResultManifest.model_validate(raw).completed_at,
        native_output_directory=str(native), result_manifest_sha256=hashlib.sha256(encoded).hexdigest()))
    destination = tmp_path / 'returned'
    shutil.copytree(source, destination)
    shutil.rmtree(attempt)  # worker is offline before receiving/replay
    async with store() as session:
        job = await session.get(Job, 'job')
        job.status = job.queue_status = job.remote_state = state
        job.execution_bundle_sha256 = raw['execution_envelope_sha256']
        job.provenance = {'remote_execution_receipt': {
            **status.model_dump(mode='json'), 'remote_attempt_dir': str(attempt),
            'terminal_status': status.model_dump(mode='json')}}
        archive = ex._diagnostic_destination(job.id, ex._pull_identity(job), status.result_manifest_sha256)
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(destination, archive)
        destination = archive
        manifest = ex._verify_result_package(destination, job, status)
        assert len(manifest.artifacts) == len(names) + bool(generation)
        for artifact in manifest.artifacts:
            assert hashlib.sha256((destination / artifact.relative_path).read_bytes()).hexdigest() == artifact.sha256
        await session.commit()
    observation_boundary.side_effect = [ValueError('inert parser interruption'), {}]
    for expected_state in ('failed', 'returned'):
        tasks = BackgroundTasks()
        async with store() as session:
            await ex.request_remote_diagnostic_pull(session, await session.get(Job, 'job'), tasks)
        await tasks()
        async with store() as session:
            job = await session.get(Job, 'job')
            assert job.provenance['remote_diagnostics']['state'] == expected_state
            assert (job.status, job.queue_status, job.remote_state) == (state, state, state)
    expected = destination / 'generation-2' if generation else destination
    assert observation_boundary.await_count == 2
    assert all(call.args[2] == expected and call.kwargs == {'commit': False}
               for call in observation_boundary.await_args_list)
    async with store() as session:
        job = await session.get(Job, 'job')
        assert (job.status, job.queue_status, job.remote_state) == (state, state, state)
