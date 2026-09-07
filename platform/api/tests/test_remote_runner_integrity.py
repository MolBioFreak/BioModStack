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
from test_remote_lifecycle_gaps import store, preparing


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [None, 'interrupt', 'corrupt'])
async def test_attachment_never_transfers_over_published_artifacts(attachment_store, monkeypatch, tmp_path, failure):
    session, factory = attachment_store
    inventory(monkeypatch, ['49674511'])
    root = tmp_path / 'remote'
    published = root / 'runner'
    published.mkdir(parents=True)
    names = ['bms_remote_worker.py', 'nextflow']
    for name in names:
        (published / name).write_bytes(b'old verified bytes')
    launcher = tmp_path / 'nextflow'
    launcher.write_bytes(b'new nextflow fixture')
    from services import nextflow
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: str(launcher))
    monkeypatch.setattr(nextflow, 'resolve_nextflow_version', lambda: '25.10.1')
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
        if argv[:2] == ['bash', '-s']:
            return SimpleNamespace(stdout='')
        if argv[0] == 'env': return SimpleNamespace(stdout='nextflow version 25.10.1')
        if argv[0] == 'apptainer': return SimpleNamespace(stdout='BMS_CUDA_OK')
        result = subprocess.run(argv, capture_output=True, text=True)
        if result.returncode:
            raise targets.RemoteTransportError('local simulated remote command failed')
        return result
    for name, value in [('capture_host_key', capture), ('persist_host_key', noop), ('probe_readiness', probe), ('rsync_to_remote', transfer), ('run_remote', run)]:
        monkeypatch.setattr(targets, name, value)
    request = ExecutionTargetActivateRequest(provider_instance_id='49674511', remote_root=str(root))
    if failure:
        with pytest.raises(asyncio.CancelledError if failure == 'interrupt' else targets.ExecutionTargetError):
            await targets.activate_target(session, request)
        assert all((published / name).read_bytes() == b'old verified bytes' for name in names)
        row = await targets.get_target(session, 'vast:49674511')
        assert not row.active
        if failure == 'interrupt':
            # The lifespan controller owns cancellation publication.
            await targets.fail_setup(session, row.id, row.provider_metadata['setup']['started_at'], 'interrupted')
        old_destinations = list(transfers)
        failure = None
        result = await targets.activate_target(session, request)
        assert result.active and result.state == 'ready'
        final_bytes = {name: (published / name).read_bytes() for name in names}
        assert set(old_destinations).isdisjoint(transfers[len(old_destinations):])
        # An old rsync receiver can finish AFTER new readiness. Its partial
        # rename must still target only the old attempt's private pathname.
        for destination in old_destinations:
            partial = Path(destination + '.partial')
            partial.write_bytes(b'late truncated prefix')
            partial.replace(destination)
        assert {name: (published / name).read_bytes() for name in names} == final_bytes
    else:
        result = await targets.activate_target(session, request)
        assert result.active and result.state == 'ready'
        assert (published / 'nextflow').read_bytes() == launcher.read_bytes()
        assert result.capabilities['runner_sha256'] == hashlib.sha256((published / names[0]).read_bytes()).hexdigest()
        assert result.capabilities['nextflow_launcher_sha256'] == hashlib.sha256(launcher.read_bytes()).hexdigest()
    assert transfers and all(Path(p).parent != published for p in transfers)


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
                await ex.launch_remote_job(s, await s.get(Job, 'job'), command=['must-not-launch'])
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
