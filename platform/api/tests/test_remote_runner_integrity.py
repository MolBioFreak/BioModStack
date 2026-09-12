"""Offline runner publication and persisted launch integrity regressions."""
import asyncio
import hashlib
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from database import ExecutionTarget, Job
from services.remote_execution import targets, executor as ex
from services.remote_execution.contracts import ExecutionTargetActivateRequest
from test_vast_inventory_reconciliation import store as attachment_store, inventory
from test_remote_lifecycle_gaps import store, preparing, lifecycle_invocation
from test_managed_runtime_safety import critical_package


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [None, 'interrupt', 'corrupt'])
async def test_attachment_never_transfers_over_published_artifacts(
        attachment_store, monkeypatch, tmp_path, critical_package, failure):
    from services.remote_execution import cache, managed_inventory as mi
    from test_managed_runtime_safety import load
    session, factory = attachment_store
    _, _, observed, root = critical_package
    inventory(monkeypatch, ['49674511'])
    published = root / 'runner'
    published.mkdir(parents=True)
    names = ['bms_remote_worker.py', 'nextflow']
    for name in names:
        (published / name).write_bytes(b'old verified bytes')
    m, c = load('bms_managed_runtime'), load('bms_artifact_cache')
    monkeypatch.setattr(m, 'observed_compatibility', lambda: observed)
    async def helper(conn, request, fence):
        await fence()
        managed = root / 'managed-assets/v1'
        action = request['action']
        if action == 'boot': result = {}
        elif action == 'admit': result = m.admit(managed, request['manifest'], request['boot_id'], c)
        elif action == 'install': result = m.install(managed, request['manifest'], request['boot_id'], c)
        elif action == 'observe': result = {'releases': [m.observe(managed, v, c) for v in request['manifests']]}
        elif action == 'bounded_check':
            releases = [m.observe(managed, v, c) for v in request['manifests']]
            for release in releases:
                release['native_readiness'] = m.bounded_native_check(release)
            result = {'releases': releases}
        else: raise AssertionError(action)
        await fence()
        return result | {'boot_id': m.boot_id()}
    async def capture(*_): return ('fixture key', 'a' * 64)
    async def noop(*_, **__): pass
    async def probe(*_): return {'gpus': ['fixture']}
    transfers = []
    async def transfer(conn, source, destination, **kw):
        transfers.append(destination)
        Path(destination).write_bytes(Path(source).read_bytes()[:5] if failure else Path(source).read_bytes())
        assert all((published / name).read_bytes() == b'old verified bytes' for name in names)
        if failure == 'interrupt':
            raise asyncio.CancelledError()
    async def run(conn, argv, **kw):
        async with factory() as other:
            assert not (await targets.get_target(other, 'vast:49674511')).active
        if argv[:2] == ['bash', '-s']: return SimpleNamespace(stdout='')
        if argv[0] == 'apptainer': return SimpleNamespace(stdout='BMS_CUDA_OK')
        result = subprocess.run(argv, input=kw.get('input_bytes'), capture_output=True)
        if result.returncode:
            raise targets.RemoteTransportError('local simulated remote command failed')
        return SimpleNamespace(stdout=result.stdout.decode())
    for name, value in [('capture_host_key', capture), ('persist_host_key', noop),
                        ('probe_readiness', probe), ('run_remote', run)]:
        monkeypatch.setattr(targets, name, value)
    monkeypatch.setattr(cache, 'run_remote', run)
    monkeypatch.setattr(cache, 'rsync_to_remote', transfer)
    monkeypatch.setattr(mi, 'helper_call', helper)
    request = ExecutionTargetActivateRequest(provider_instance_id='49674511', remote_root=str(root))
    old_destinations = []
    if failure:
        with pytest.raises(asyncio.CancelledError if failure == 'interrupt' else targets.ExecutionTargetError):
            await targets.activate_target(session, request)
        row = await targets.get_target(session, 'vast:49674511')
        assert not row.active
        assert not (root / 'managed-assets/v1/active/critical_runtime-worker.json').exists()
        if failure == 'interrupt':
            await targets.fail_setup(session, row.id, row.provider_metadata['setup']['started_at'], 'interrupted')
        old_destinations = list(transfers)
        failure = None
    result = await targets.activate_target(session, request)
    assert result.active and result.state == 'ready'
    binding = result.capabilities['critical_runtime_binding']
    final_bytes = {key: Path(path).read_bytes() for key, path in binding['paths'].items()}
    assert set(old_destinations).isdisjoint(transfers[len(old_destinations):])
    for destination in old_destinations:
        partial = Path(destination + '.partial')
        partial.write_bytes(b'late truncated prefix')
        partial.replace(destination)
    assert {key: Path(path).read_bytes() for key, path in binding['paths'].items()} == final_bytes
    assert {key: hashlib.sha256(value).hexdigest() for key, value in final_bytes.items()} == binding['sha256']
    assert all((published / name).read_bytes() == b'old verified bytes' for name in names)
    assert transfers and all('/incoming/' in p for p in transfers)


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['prepare', 'run', 'status', 'cancel'])
async def test_pinned_generation_drives_launch_and_retained_attempt_control(
        monkeypatch, critical_package, command):
    from copy import deepcopy
    from services.remote_execution import critical_runtime as cr
    from services.remote_execution.transport import RemoteConnection
    manifest, _, _, worker_root = critical_package
    binding = cr.runtime_binding(str(worker_root), manifest)
    target = SimpleNamespace(id='target', provider_instance_id='1', host='203.0.113.1', port=22, username='root',
        remote_root=str(worker_root), active=True, state='ready',
        capabilities={'critical_runtime_binding': binding},
        provider_metadata={'inventory': {'status': 'complete', 'present': True, 'running': True,
            'checked_at': datetime.utcnow().isoformat()}})
    connection = RemoteConnection.from_target(target)
    calls = []
    async def remote(conn, argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout=''.join(binding['sha256'][key]+'  '+binding['paths'][key]+'\n'
                               for key in ('runner', 'nextflow', 'python')))
    monkeypatch.setattr(ex, 'run_remote', remote)
    await ex._verify_remote_runner(connection, target)
    assert calls == [['sha256sum', *[binding['paths'][key] for key in ('runner', 'nextflow', 'python')]]]
    receipt = dict(attempt_id='original', remote_root=str(worker_root),
                   remote_attempt_dir=str(worker_root / 'attempts/original'),
                   critical_runtime_binding=deepcopy(binding))
    # Changing the attachment cannot redirect control of the existing attempt.
    target.capabilities = {'critical_runtime_binding': dict(binding, paths={
        key: value.replace(manifest['critical']['installation_id'], 'f'*64)
        for key, value in binding['paths'].items()})}
    job = SimpleNamespace(remote_attempt_id='original', provenance={'remote_execution_receipt': receipt})
    connection, attempt_dir = ex._connection_for_attempt(target, job)
    argv = ex._worker_argv(connection, command, attempt_dir)
    assert argv == [binding['paths']['python'], binding['paths']['runner'], command, '--attempt-dir', receipt['remote_attempt_dir']]
    assert connection.runtime_binding == binding


@pytest.mark.asyncio
@pytest.mark.parametrize('race', [None, 'attachment', 'lease', 'claim'])
@pytest.mark.parametrize('resume', [False, True])
async def test_launch_mismatch_invalidates_only_observed_ready_generation(store, monkeypatch, race, resume):
    await preparing(store)
    async with store() as s:
        target = await s.get(ExecutionTarget, 'target')
        target.active, target.state = True, 'ready'
        target.host, target.port, target.username, target.remote_root = '203.0.113.1', 22, 'root', '/opt/bms'
        target.activated_at = datetime.utcnow()
        target.capabilities = {'runner_sha256': 'a' * 64, 'nextflow_launcher_sha256': 'b' * 64}
        target.provider_metadata = {'setup': {'started_at': 'old', 'phase': 'ready'}, 'inventory': {
            'status': 'complete', 'present': True, 'running': True, 'checked_at': datetime.utcnow().isoformat()}}
        if resume:
            j = await s.get(Job, 'job')
            j.nextflow_run_id, j.remote_attempt_id, j.remote_state = 'remote:attempt', 'attempt', 'staging'
        await s.commit()
    if resume:
        from test_remote_lifecycle_gaps import receipt
        async def status(*_): return receipt('prepared')
        monkeypatch.setattr(ex, 'remote_status', status)
    calls = []
    async def run(conn, argv, **kw):
        calls.append(argv)
        assert argv[0] == 'sha256sum'
        if race:
            async with store() as other:
                t = await other.get(ExecutionTarget, 'target')
                if race == 'attachment':
                    t.provider_metadata = {**t.provider_metadata, 'setup': {'started_at': 'new', 'phase': 'ready'}}
                    t.activated_at = datetime.utcnow()
                elif race == 'lease':
                    t.leased_job_id = 'new-job'
                else:
                    j = await other.get(Job, 'job')
                    j.provenance = {'remote_execution_assignment': {'claimed_at': 'successor'}}
                await other.commit()
        return SimpleNamespace(stdout=f"{'a' * 64} worker\n{'c' * 64} nextflow\n")
    monkeypatch.setattr(ex, 'run_remote', run)
    async with store() as s:
        with pytest.raises(ex.RemoteExecutionError, match='identity changed'):
            if resume:
                await ex.reconcile_remote_job(s, await s.get(Job, 'job'))
            else:
                await ex.launch_remote_job(s, await s.get(Job, 'job'), command=['must-not-launch'],
                                           native_invocation=lifecycle_invocation(['must-not-launch']))
    async with store() as s:
        t, j = await s.get(ExecutionTarget, 'target'), await s.get(Job, 'job')
        if race:
            assert t.active and t.state == 'ready' and t.last_error is None
        else:
            assert not t.active and t.state == 'unavailable'
            assert 'Attach' in t.last_error and 'integrity' in t.last_error
            assert j.status == ('queued' if resume else 'failed')
            assert t.leased_job_id == ('job' if resume else None)
        if race in {'lease', 'claim'}:
            assert j.status == 'queued'
            assert t.leased_job_id == ('new-job' if race == 'lease' else 'job')
        assert j.nextflow_run_id == ('remote:attempt' if resume else None)
        assert j.remote_attempt_id == ('attempt' if resume else None)
    assert len(calls) == 1
