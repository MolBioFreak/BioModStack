"""Return-lane integration: real local writer/process control and mounted Job reads.

SSH/scientific infrastructure only is doubled. No worker/provider access.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest
from fastapi import BackgroundTasks, FastAPI

from database import Job, get_session
from routers import jobs
from services.remote_execution import executor as ex, result_generation as gen, transport
from services.remote_execution.contracts import RemoteResultManifest
from test_remote_lifecycle_gaps import store
from test_remote_manual_result_pull import ready, success
from test_remote_transport_recovery import WRITER, wait_for, record


def mounted(store):
    app = FastAPI()
    app.include_router(jobs.router, prefix='/api/jobs')
    async def session():
        async with store() as s:
            yield s
    app.dependency_overrides[get_session] = session
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')


def sealed(job, *, failed=False, generation=1, prior_logs=True):
    contents = {'_remote/nextflow.log': b'old generation',
                'continued/_remote/nextflow.log': b'current generation',
                'continued/_remote/supervisor.log': b'native supervisor',
                'continued/partial.pdb': b'ATOM partial native bytes'}
    if not prior_logs:
        contents.pop('_remote/nextflow.log')
    manifest = RemoteResultManifest(job_id=job.id, attempt_id=job.remote_attempt_id,
        source_revision=job.execution_source_revision, source_tree=job.execution_source_tree,
        execution_envelope_sha256=job.execution_bundle_sha256, generation=generation,
        exit_code=1 if failed else 0, completed_at=success().completed_at,
        artifacts=[dict(relative_path=p, size_bytes=len(b), sha256=hashlib.sha256(b).hexdigest(), role='result')
                   for p, b in contents.items()])
    raw = manifest.model_dump_json().encode()
    digest = hashlib.sha256(raw).hexdigest()
    status = success().model_copy(update=dict(result_manifest_sha256=digest, generation=generation,
        native_output_directory='/fixture/attempt/results/continued', boot_id='worker-boot',
        state='failed' if failed else 'succeeded', exit_code=manifest.exit_code))
    receipt = dict(ex._pull_identity(job), remote_attempt_dir='/fixture/attempt',
        local_result_root=job.output_dir, state=status.state, exit_code=status.exit_code,
        result_manifest_sha256=digest, generation=generation, boot_id=status.boot_id,
        native_output_directory=status.native_output_directory, terminal_status=status.model_dump(mode='json'))
    job.provenance = dict(remote_execution_receipt=receipt)
    incoming = gen.staging_path(job, digest)
    incoming.mkdir(parents=True)
    (incoming / 'result-manifest.json').write_bytes(raw)
    for name, data in contents.items():
        path = incoming / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return manifest, incoming, status, contents


@pytest.mark.asyncio
@pytest.mark.parametrize('diagnostics', [True, False])
@pytest.mark.parametrize('prior_logs', [True, False])
async def test_returned_native_logs_list_download_and_history(store, tmp_path, monkeypatch, diagnostics, prior_logs):
    async with store() as s:
        job = await s.get(Job, 'job')
        job.output_dir = str(tmp_path / 'science')
        job.status = job.queue_status = 'failed' if diagnostics else 'completed'
        if diagnostics:
            Path(job.output_dir).mkdir()
            (Path(job.output_dir) / 'prior-good.pdb').write_bytes(b'prior scientific output')
        manifest, incoming, status, contents = sealed(job, failed=diagnostics, prior_logs=prior_logs)
        await s.commit()
        if diagnostics:
            async def collect(*_):
                return manifest, incoming
            async def remote(*_):
                return status
            async def prove(*_, **__):
                pass
            monkeypatch.setattr(ex, 'collect_remote_results', collect)
            monkeypatch.setattr(ex, 'remote_status', remote)
            monkeypatch.setattr(ex, '_prove_pull_endpoint', prove)
            tasks = BackgroundTasks()
            await ex.request_remote_diagnostic_pull(s, job, tasks)
        else:
            root, _ = gen.publish(job, incoming)
            job.child_output_dir = str(root / 'continued')
            job.provenance = dict(job.provenance, remote_execution_receipt=dict(
                job.provenance['remote_execution_receipt'], published_output_dir=job.child_output_dir))
            await s.commit()
            gen.recover(job)
    if diagnostics:
        await tasks()
    async def forbidden(*_, **__):
        pytest.fail('read surfaces must not contact worker')
    monkeypatch.setattr(ex, 'run_remote', forbidden)
    async with mounted(store) as client:
        logs = await client.get('/api/jobs/job/logs')
        assert logs.status_code == 200, logs.text
        data = logs.json()
        assert data['nextflow_log'] == 'current generation', data
        assert data['nextflow_log_source'] == ('remote_diagnostics' if diagnostics else 'remote_returned')
        assert data['remote_result_identity']['generation'] == 1
        listed = await client.get('/api/jobs/job/remote-artifacts', params={'diagnostics': diagnostics})
        assert listed.status_code == 200, listed.text
        rows = listed.json()['artifacts']
        assert len(rows) == len(contents)
        for row in rows:
            response = await client.get(row['download_url'])
            assert response.status_code == 200, response.text
            assert response.content == contents[row['relative_path']]
            assert hashlib.sha256(response.content).hexdigest() == row['sha256']
        if prior_logs:
            old = next(r for r in rows if r['relative_path'] == '_remote/nextflow.log')
            assert old['scope'] == 'history'
        else:
            assert all(r['scope'] == 'current' for r in rows)
        params = dict(path='../outside', attempt_id='attempt', manifest_sha256=status.result_manifest_sha256, diagnostics=diagnostics)
        assert (await client.get('/api/jobs/job/remote-artifacts/download', params=params)).status_code == 409
        params.update(path=rows[0]['relative_path'], attempt_id='foreign')
        assert (await client.get('/api/jobs/job/remote-artifacts/download', params=params)).status_code == 409
        params.update(attempt_id='attempt', manifest_sha256='0' * 64)
        assert (await client.get('/api/jobs/job/remote-artifacts/download', params=params)).status_code == 409
        async with store() as s:
            job = await s.get(Job, 'job')
            assert job.status == ('failed' if diagnostics else 'completed')
            if diagnostics:
                assert (Path(job.output_dir) / 'prior-good.pdb').read_bytes() == b'prior scientific output'
            root, _, _, _ = ex.retained_result_view(job, diagnostics=diagnostics)
        selected = root / rows[0]['relative_path']
        selected.unlink()
        selected.symlink_to(tmp_path / 'outside')
        assert (await client.get(rows[0]['download_url'])).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize('automatic', [False, True])
@pytest.mark.parametrize('boundary', ['active', 'pre-spawn', 'before-fence'])
async def test_job_delete_stops_real_selected_transport_and_retains_staging(store, tmp_path, monkeypatch, automatic, boundary):
    await ready(store)
    async with store() as s:
        job = await s.get(Job, 'job')
        job.output_dir = str(tmp_path / 'science')
        job.params = dict(remote_result_policy='automatic' if automatic else 'manual')
        manifest, incoming, status, _ = sealed(job, generation=0)
        raw = (incoming / 'result-manifest.json').read_text()
        (incoming / 'continued/partial.pdb').unlink()
        await s.commit()
    async def proof(*_, **__):
        pass
    async def remote(*_, **__):
        return status
    async def rpc(_, argv, **__):
        return SimpleNamespace(stdout=status.model_dump_json() if argv[0] == 'cancel' else raw)
    monkeypatch.setattr(ex, '_prove_pull_endpoint', proof)
    monkeypatch.setattr(ex, 'remote_status', remote)
    monkeypatch.setattr(ex, 'run_remote', rpc)
    monkeypatch.setattr(ex, '_connection_for_attempt', lambda *_: (transport.RemoteConnection('target', 'localhost', 22, 'user', '/fixture'), '/fixture/attempt'))
    monkeypatch.setattr(ex, '_worker_argv', lambda _, op, *args: [op])
    monkeypatch.setattr(transport, '_ssh_base', lambda *_: ['ssh', 'fixture'])
    executable = tmp_path / 'bin/rsync'
    executable.parent.mkdir()
    executable.write_text('#!' + sys.executable + '\n' + WRITER.replace('root = Path(sys.argv[1])', 'root = Path(sys.argv[-1])'))
    executable.chmod(0o700)
    monkeypatch.setenv('PATH', str(executable.parent) + os.pathsep + os.environ['PATH'])
    entered, release = asyncio.Event(), asyncio.Event()
    original = transport._run_owned
    async def launch(*args, **kwargs):
        if boundary == 'pre-spawn':
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)
    monkeypatch.setattr(transport, '_run_owned', launch)
    original_collect = ex.collect_remote_results
    class CollectorSession:
        # Forward the real physical DB connection; pause only after the actual
        # prelaunch refresh/commit and BEFORE the ownership marker exists.
        def __init__(self, session):
            self.session, self.refreshed = session, False
        def __getattr__(self, name):
            return getattr(self.session, name)
        async def refresh(self, job):
            await self.session.refresh(job)
            self.refreshed = True
        async def commit(self):
            await self.session.commit()
            if self.refreshed:
                entered.set()
                await release.wait()
    async def collect(session, *args):
        return await original_collect(CollectorSession(session), *args)
    if boundary == 'before-fence':
        monkeypatch.setattr(ex, 'collect_remote_results', collect)
    tasks = BackgroundTasks()
    async with store() as s:
        assert await ex.request_remote_result_pull(s, await s.get(Job, 'job'), tasks, automatic=automatic)
    pull = asyncio.create_task(tasks())
    cancel = None
    try:
        if boundary != 'active':
            await asyncio.wait_for(entered.wait(), 5)
            if boundary == 'before-fence':
                assert not gen.transfer_marker(incoming).exists()
        else:
            await asyncio.to_thread(wait_for, lambda: (incoming / 'ready').exists() and 'writer' in record(incoming))
        async with mounted(store) as client:
            cancel = asyncio.create_task(client.delete('/api/jobs/job'))
            if boundary != 'active':
                # Synchronize on actual durable intent, not host scheduling speed.
                for _ in range(300):
                    async with store() as independent:
                        committed = (await independent.get(Job, 'job')).queue_status == 'cancelling'
                    if committed:
                        break
                    await asyncio.sleep(.01)
                assert committed, 'DELETE did not commit cancellation intent'
                assert not cancel.done(), 'absent supervisor is not quiescence while producer owns guard'
                release.set()
            response = await asyncio.wait_for(cancel, 8)
            assert response.status_code == 200, response.text
        await asyncio.wait_for(pull, 5)
        partial = (incoming / 'partial').read_bytes() if (incoming / 'partial').exists() else b''
        await asyncio.sleep(.1)
        assert not (incoming / 'partial').exists() or (incoming / 'partial').read_bytes() == partial
        assert (incoming / '_remote/nextflow.log').read_bytes() == b'old generation'
        async with store() as s:
            job = await s.get(Job, 'job')
            assert job.status == 'cancelled'
            assert 'remote_result_generation' not in job.provenance
            assert not Path(job.output_dir).exists()
    finally:
        release.set()
        if not pull.done():
            pull.cancel()
        await asyncio.gather(pull, return_exceptions=True)
        if cancel is not None:
            if not cancel.done():
                cancel.cancel()
            await asyncio.gather(cancel, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['attempt', 'digest', 'manifest', 'plan', 'generation', 'native_root', 'published_root'])
async def test_retained_locator_rejects_changed_authority(store, tmp_path, change):
    async with store() as s:
        job = await s.get(Job, 'job')
        job.output_dir = str(tmp_path / 'science')
        job.status = job.queue_status = 'completed'
        _, incoming, _, _ = sealed(job)
        root, _ = gen.publish(job, incoming)
        job.child_output_dir = str(root / 'continued')
        receipt = dict(job.provenance['remote_execution_receipt'], published_output_dir=job.child_output_dir)
        job.provenance = dict(job.provenance, remote_execution_receipt=receipt)
        gen.recover(job)
        if change == 'attempt':
            job.remote_attempt_id = 'foreign'
        elif change == 'digest':
            receipt['result_manifest_sha256'] = '0' * 64
        elif change == 'manifest':
            (root / 'result-manifest.json').write_text('{}')
        elif change == 'plan':
            receipt['plan_sha256'] = '0' * 64
        elif change == 'generation':
            receipt['generation'] = 2
            receipt['terminal_status'] = dict(receipt['terminal_status'], generation=2)
        elif change == 'native_root':
            receipt['native_output_directory'] = '/foreign/results'
            receipt['terminal_status'] = dict(receipt['terminal_status'], native_output_directory='/foreign/results')
        elif change == 'published_root':
            receipt['published_output_dir'] = str(tmp_path / 'foreign')
        job.provenance = dict(job.provenance, remote_execution_receipt=receipt)
        await s.commit()
    async with mounted(store) as client:
        response = await client.get('/api/jobs/job/remote-artifacts')
        assert response.status_code == 409, response.text
        logs = (await client.get('/api/jobs/job/logs')).json()
        assert logs['nextflow_log'] is None
        assert logs['remote_read_error']


@pytest.mark.asyncio
async def test_recovery_actuator_with_existing_controller_guard_is_idempotent(store, tmp_path, monkeypatch):
    async with store() as s:
        job = await s.get(Job, 'job')
        job.output_dir = str(tmp_path / 'science')
        job.queue_status = 'cancelling'
        job.provenance = {'remote_execution_receipt': {'result_manifest_sha256': 'a' * 64}}
        await s.commit()
    monkeypatch.setattr(ex, '_connection_for_attempt', lambda *_: (None, '/fixture/attempt'))
    monkeypatch.setattr(ex, '_worker_argv', lambda _, op, *args: [op])
    async def rpc(*_, **__):
        return SimpleNamespace(stdout=success().model_dump_json())
    monkeypatch.setattr(ex, 'run_remote', rpc)
    with ex._controller_attempt_guard(job.id) as owned:
        assert owned
        for _ in range(2):
            assert await asyncio.wait_for(ex.cancel_remote_job(job, guard_owned=True), 1)


def test_cross_process_control_long_path_and_foreign_destination(tmp_path, monkeypatch):
    from test_remote_result_generation import job_at
    monkeypatch.setattr(ex, 'get_data_root', lambda: tmp_path)
    job = job_at(tmp_path / ('long' * 45))
    job.provenance = {'remote_execution_receipt': {'result_manifest_sha256': 'a' * 64}}
    incoming = gen.staging_path(job, 'a' * 64)
    incoming.mkdir(parents=True)
    pid = os.fork()
    if pid == 0:
        try:
            with ex._controller_attempt_guard(job.id) as owned:
                assert owned
                gen.begin_transfer(incoming)
                asyncio.run(transport._run_owned([sys.executable, '-c', WRITER, str(incoming), 'fork'], incoming, timeout=15))
            os._exit(0)
        except BaseException:
            os._exit(1)
    try:
        wait_for(lambda: (incoming / 'ready').exists() and 'writer' in record(incoming))
        assert asyncio.run(transport.cancel_owned_transfer(incoming.with_name('foreign'))) is None
        active = record(incoming)
        stale = dict(active, supervisor=dict(active['supervisor'], start_ticks=active['supervisor']['start_ticks'] - 1))
        gen.durable_json(gen.transfer_marker(incoming), stale)
        assert asyncio.run(transport.cancel_owned_transfer(incoming)) is False
        assert record(incoming)['phase'] == 'supervising'
        gen.durable_json(gen.transfer_marker(incoming), active)
        assert asyncio.run(ex.cancel_local_result_transfer(job))
        assert os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]) == 0
        pid = None
        assert record(incoming)['phase'] == 'quiescent'
        before = (incoming / 'partial').read_bytes()
        assert asyncio.run(ex.cancel_local_result_transfer(job))  # restart consumes retained receipt
        assert not gen.transfer_marker(incoming).exists()
        assert (incoming / 'partial').read_bytes() == before
        with ex._controller_attempt_guard(job.id) as owned:
            assert owned
            assert not asyncio.run(ex.cancel_local_result_transfer(job, timeout=.1))
            assert asyncio.run(ex.cancel_local_result_transfer(job, guard_owned=True))
    finally:
        if pid is not None:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
