"""Mounted active log route with real bounded reader; SSH only is doubled."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import pytest_asyncio

from database import ExecutionTarget, Job
from services.remote_execution import executor as ex
from services.remote_execution.contracts import RemoteAttemptStatus
from services.remote_execution.transport import CommandResult, RemoteTransportError
from tools import bms_remote_log_reader as reader
from test_remote_lifecycle_gaps import store
from test_remote_rectify_return import mounted


@pytest_asyncio.fixture
async def active(store, tmp_path, monkeypatch):
    from tools import bms_remote_worker as worker
    child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE,
        env={**os.environ, 'SERVICE_TOKEN': 'envelope-value', 'API_KEY': 'secret-file-value',
             'INHERITED_PASSWORD': 'inherited-value'})
    root = tmp_path / 'remote'
    attempt = root / 'attempts' / 'attempt'
    attempt.mkdir(parents=True)
    status = RemoteAttemptStatus(job_id='job', attempt_id='attempt', state='running',
        generation=1, diagnostic_offsets={}, boot_id=worker.boot_id(), continuation_lease_id='continuation',
        workflow_pid=child.pid, workflow_start_ticks=worker.process_start_ticks(child.pid),
        plan_sha256='d' * 64, native_output_directory=str(attempt / 'results' / 'continued'))
    (attempt / 'status.json').write_text(status.model_dump_json())
    (attempt / 'execution-envelope.json').write_text(json.dumps(dict(job_id='job', attempt_id='attempt',
        working_directory=str(attempt), environment={})))
    (attempt / 'nextflow.log').write_text('old\nactive nextflow\n')
    (attempt / 'supervisor.log').write_text('active supervisor\n')
    async with store() as s:
        target = await s.get(ExecutionTarget, 'target')
        target.host, target.port, target.username, target.remote_root = 'worker.invalid', 22, 'user', str(root)
        job = await s.get(Job, 'job')
        job.provenance = {'remote_execution_receipt': dict(
            attempt_id='attempt', execution_target_id='target', provider_instance_id='1',
            ssh_host=target.host, ssh_port=22, ssh_username='user', remote_root=str(root),
            remote_attempt_dir=str(attempt), state='running', generation=1, boot_id=worker.boot_id(),
            continuation_lease_id='continuation')}
        await s.commit()
    calls = []
    async def rpc(connection, argv, **kwargs):
        calls.append(argv)
        assert kwargs['timeout'] == 30
        assert connection.host == 'worker.invalid'
        if argv[2] == 'status':
            return CommandResult(0, (attempt / 'status.json').read_text(), '')
        assert argv[1] == '-c', 'log reads must not prepare/start/collect/cancel'
        result = subprocess.run([sys.executable, *argv[1:]], capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise RemoteTransportError(result.stderr)
        return CommandResult(result.returncode, result.stdout, result.stderr)
    monkeypatch.setattr(ex, 'run_remote', rpc)
    try:
        yield attempt, status, calls, rpc
    finally:
        child.stdin.close()
        child.wait(timeout=10)


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['prepared', 'running', 'cancelling', 'awaiting_input'])
@pytest.mark.parametrize('generation', [0, 1])
async def test_active_logs_use_governed_observation_not_terminal_authority(store, active, monkeypatch, state, generation):
    attempt, status, calls, _ = active
    status = status.model_copy(update={'state': state, 'generation': generation})
    (attempt / 'status.json').write_text(status.model_dump_json())
    async with store() as s:
        job = await s.get(Job, 'job')
        job.provenance = {'remote_execution_receipt': dict(job.provenance['remote_execution_receipt'], generation=generation)}
        if state == 'awaiting_input':
            job.status = 'awaiting_input'
        await s.commit()
        original = (job.status, job.remote_state, job.provenance)
    def forbidden(*_, **__):
        pytest.fail('Active logs cannot ask terminal result authority')
    monkeypatch.setattr(ex, 'retained_result_view', forbidden)
    async with mounted(store) as client:
        response = await client.get('/api/jobs/job/logs?tail=1')
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['nextflow_log'] == 'active nextflow', data
    assert data['command_log'] == 'active supervisor'
    assert data['nextflow_log_source'] == 'remote_live'
    assert data['remote_attempt_identity']['generation'] == generation
    assert data['exit_code'] is None and 'remote_result_identity' not in data
    assert 'remote_read_error' not in data
    assert len(calls) == 2
    async with store() as s:
        job = await s.get(Job, 'job')
        assert (job.status, job.remote_state, job.provenance) == original
        assert (await s.get(ExecutionTarget, 'target')).leased_job_id == 'job'


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('job_id', 'other'), ('attempt_id', 'other'),
    ('generation', 2), ('boot_id', 'other'), ('continuation_lease_id', 'other')])
async def test_stale_worker_observation_rejected_before_log_read(store, active, field, value):
    attempt, status, calls, _ = active
    (attempt / 'status.json').write_text(status.model_copy(update={field: value}).model_dump_json())
    async with mounted(store) as client:
        data = (await client.get('/api/jobs/job/logs')).json()
    assert data['remote_read_error']
    assert data['nextflow_log'] is None and data['command_log'] is None
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['before', 'during'])
@pytest.mark.parametrize('change', ['host', 'attempt', 'generation'])
async def test_controller_identity_drift_never_exposes_logs(store, active, monkeypatch, boundary, change):
    _, _, calls, rpc = active
    async def drift():
        async with store() as s:
            job = await s.get(Job, 'job')
            if change == 'host':
                (await s.get(ExecutionTarget, 'target')).host = 'other.invalid'
            elif change == 'attempt':
                job.remote_attempt_id = 'other'
            else:
                job.provenance = {'remote_execution_receipt': dict(job.provenance['remote_execution_receipt'], generation=2)}
            await s.commit()
    async def racing(connection, argv, **kwargs):
        result = await rpc(connection, argv, **kwargs)
        if argv[1] == '-c':
            await drift()
        return result
    if boundary == 'before':
        await drift()
    else:
        monkeypatch.setattr(ex, 'run_remote', racing)
    async with mounted(store) as client:
        data = (await client.get('/api/jobs/job/logs')).json()
    assert data['remote_read_error'], data
    assert data['nextflow_log'] is None and data['command_log'] is None
    if boundary == 'before' and change in {'host', 'attempt'}:
        assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['unavailable', 'malformed', 'terminal', 'missing_identity'])
async def test_pending_logs_do_not_prepare_or_fall_back_to_local(store, active, monkeypatch, fault):
    attempt, status, calls, rpc = active
    async with store() as s:
        job = await s.get(Job, 'job')
        job.remote_state = 'staging'
        if fault == 'missing_identity':
            job.remote_attempt_id = None
        await s.commit()
    if fault == 'terminal':
        (attempt / 'status.json').write_text(status.model_copy(update={'state': 'failed'}).model_dump_json())
    async def failing(connection, argv, **kwargs):
        if fault == 'unavailable':
            calls.append(argv)
            raise RemoteTransportError('SSH unavailable')
        if fault == 'malformed':
            calls.append(argv)
            return CommandResult(0, 'not JSON', '')
        return await rpc(connection, argv, **kwargs)
    monkeypatch.setattr(ex, 'run_remote', failing)
    async with mounted(store) as client:
        data = (await client.get('/api/jobs/job/logs')).json()
    assert data['nextflow_log_source'] == 'remote_pending'
    assert data['remote_read_error']
    assert data['nextflow_log'] is None and data['command_log'] is None
    assert len(calls) <= 1


@pytest.mark.asyncio
@pytest.mark.parametrize('member', ['nextflow.log', 'supervisor.log', 'status.json', 'ancestor', 'fifo'])
async def test_no_follow_reader_rejects_symlinks_and_nonregular_files(store, active, tmp_path, member):
    attempt, _, _, _ = active
    if member == 'ancestor':
        real = attempt.with_name('real')
        attempt.rename(real)
        attempt.symlink_to(real, target_is_directory=True)
    else:
        path = attempt / ('nextflow.log' if member == 'fifo' else member)
        path.unlink()
        if member == 'fifo':
            os.mkfifo(path)
        else:
            outside = tmp_path / 'outside'
            outside.write_text('private unrelated log')
            path.symlink_to(outside)
    async with mounted(store) as client:
        data = (await client.get('/api/jobs/job/logs')).json()
    assert data['remote_read_error'], data
    assert data['nextflow_log'] is None and data['command_log'] is None
    assert 'private unrelated log' not in str(data)


@pytest.mark.asyncio
@pytest.mark.parametrize('tail,lines', [(-1, 1), (2, 2), (999999, 5000)])
async def test_live_log_bounds_and_missing_optional_log(store, active, tail, lines):
    attempt, _, _, _ = active
    (attempt / 'nextflow.log').write_text('line\n' * 10000)
    (attempt / 'supervisor.log').unlink()
    async with mounted(store) as client:
        data = (await client.get('/api/jobs/job/logs', params={'tail': tail})).json()
    assert len(data['nextflow_log'].splitlines()) == lines, data
    assert data['command_log'] is None
    (attempt / 'nextflow.log').write_bytes(b'x' * (2 * reader.MAX_LOG_BYTES))
    async with mounted(store) as client:
        data = (await client.get('/api/jobs/job/logs')).json()
    assert len(data['nextflow_log']) == reader.MAX_LOG_BYTES


@pytest.mark.asyncio
async def test_worker_generation_change_during_transport_rejected(store, active, monkeypatch):
    attempt, status, _, rpc = active
    async def racing(connection, argv, **kwargs):
        if argv[1] == '-c':
            (attempt / 'status.json').write_text(status.model_copy(update={'generation': 2}).model_dump_json())
        return await rpc(connection, argv, **kwargs)
    monkeypatch.setattr(ex, 'run_remote', racing)
    async with mounted(store) as client:
        data = (await client.get('/api/jobs/job/logs')).json()
    assert data['remote_read_error']
    assert data['nextflow_log'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', ['null', '{"nextflow_log": 1, "command_log": null}',
    '{"nextflow_log": null}', '{"nextflow_log": null, "command_log": null, "extra": true}'])
async def test_malformed_log_payload_is_not_projected(store, active, monkeypatch, payload):
    _, _, _, rpc = active
    async def malformed(connection, argv, **kwargs):
        if argv[1] == '-c':
            return CommandResult(0, payload, '')
        return await rpc(connection, argv, **kwargs)
    monkeypatch.setattr(ex, 'run_remote', malformed)
    async with mounted(store) as client:
        data = (await client.get('/api/jobs/job/logs')).json()
    assert data['remote_read_error']
    assert data['nextflow_log'] is None and data['command_log'] is None


@pytest.mark.asyncio
async def test_reader_rechecks_generation_after_reading_logs(active, monkeypatch):
    attempt, status, _, _ = active
    identity = {key: getattr(status, key) for key in reader.IDENTITY_FIELDS}
    original = reader.os.open
    def replace_status(path, flags, *args, **kwargs):
        if path == 'supervisor.log':
            (attempt / 'status.json').write_text(status.model_copy(update={'generation': 2}).model_dump_json())
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(reader.os, 'open', replace_status)
    with pytest.raises(ValueError, match='identity changed'):
        reader.read_logs(str(attempt), identity, 200)


@pytest.mark.asyncio
async def test_status_symlink_with_valid_bytes_is_not_followed(store, active):
    attempt, _, _, _ = active
    path = attempt / 'status.json'
    path.rename(attempt / 'other-status.json')
    path.symlink_to(attempt / 'other-status.json')
    async with mounted(store) as client:
        data = (await client.get('/api/jobs/job/logs')).json()
    assert data['remote_read_error']
    assert data['nextflow_log'] is None


@pytest.mark.asyncio
async def test_missing_job_does_not_contact_worker(store, active):
    _, _, calls, _ = active
    async with mounted(store) as client:
        response = await client.get('/api/jobs/missing/logs')
    assert response.status_code == 404
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize('authenticated', [False, True])
async def test_live_logs_keep_strict_ssh_and_pinned_runtime(store, active, monkeypatch, tmp_path, authenticated):
    import shlex
    from services.remote_execution import transport
    _, _, calls, rpc = active
    binding = {'paths': {'python': '/pinned/python', 'runner': '/pinned/worker.py'}}
    async with store() as s:
        job = await s.get(Job, 'job')
        job.provenance = {'remote_execution_receipt': dict(job.provenance['remote_execution_receipt'],
            critical_runtime_binding=binding)}
        target = await s.get(ExecutionTarget, 'target')
        connection, _ = ex._connection_for_attempt(target, job)
        await s.commit()
    monkeypatch.setattr(transport, 'private_key_path', lambda: tmp_path / 'test-key')
    monkeypatch.setattr(transport, 'known_hosts_path', lambda: tmp_path / 'test-known-hosts')
    async def ssh(argv, **kwargs):
        assert argv[0] == 'ssh'
        assert 'StrictHostKeyChecking=yes' in argv and 'BatchMode=yes' in argv
        assert f'UserKnownHostsFile={tmp_path / "test-known-hosts"}' in argv
        assert argv[-2] == 'user@worker.invalid'
        remote = shlex.split(argv[-1])
        assert remote[0] == '/pinned/python'
        if not authenticated:
            return CommandResult(255, '', 'authentication failure')
        return await rpc(connection, remote, **kwargs)
    monkeypatch.setattr(transport, '_run', ssh)
    monkeypatch.setattr(ex, 'run_remote', transport.run_remote)
    async with mounted(store) as client:
        data = (await client.get('/api/jobs/job/logs')).json()
    if authenticated:
        assert data['nextflow_log'] == 'old\nactive nextflow', data
        assert len(calls) == 2
    else:
        assert 'authentication failed' in data['remote_read_error']
        assert data['nextflow_log'] is None
        assert not calls
