"""Real isolated DB authority tests; all remote effects are mocked."""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from database import Job, ExecutionTarget
from services.remote_execution import executor as ex
from test_remote_lifecycle_gaps import store, preparing


async def terminal(store, tmp_path):
    async with store() as s:
        j = await s.get(Job, 'job')
        j.status = j.queue_status = j.remote_state = 'failed'
        j.completed_at = datetime(2025, 1, 1)
        j.error_message = 'science failed'
        j.output_dir = str(tmp_path / 'science')
        Path(j.output_dir).mkdir()
        (Path(j.output_dir) / 'untouched').write_text('science')
        j.provenance = {'remote_execution_receipt': {'state': 'failed', 'exit_code': 1,
            'result_manifest_sha256': 'd' * 64}}
        (await s.get(ExecutionTarget, 'target')).leased_job_id = 'successor'
        await s.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize('race', [None, 'receipt', 'attempt', 'failure'])
async def test_diagnostic_archive_never_mutates_science_or_successor_lease(store, tmp_path, monkeypatch, race):
    await terminal(store, tmp_path)
    async def prove(*a, **kw):
        pass
    async def status(*a):
        return SimpleNamespace(state='failed', exit_code=1, result_manifest_sha256='d' * 64)
    async def collect(*a):
        if race in {'receipt', 'attempt'}:
            async with store() as other:
                j = await other.get(Job, 'job')
                if race == 'receipt':
                    j.provenance = dict(j.provenance, remote_execution_receipt=dict(
                        j.provenance['remote_execution_receipt'], ssh_host='changed'))
                else:
                    j.remote_attempt_id = 'successor-attempt'
                await other.commit()
        if race == 'failure':
            raise ex.RemoteExecutionError('bad archive')
        incoming = tmp_path / 'incoming'
        incoming.mkdir()
        (incoming / 'log').write_text('diagnostic')
        return SimpleNamespace(), incoming
    monkeypatch.setattr(ex, '_prove_pull_endpoint', prove)
    monkeypatch.setattr(ex, 'remote_status', status)
    monkeypatch.setattr(ex, 'collect_remote_results', collect)
    tasks = BackgroundTasks()
    async with store() as s:
        await ex.request_remote_diagnostic_pull(s, await s.get(Job, 'job'), tasks)
    duplicate = BackgroundTasks()
    async with store() as s:
        await ex.request_remote_diagnostic_pull(s, await s.get(Job, 'job'), duplicate)
    assert not duplicate.tasks
    await tasks()
    async with store() as s:
        j = await s.get(Job, 'job')
        assert (j.status, j.queue_status, j.remote_state) == ('failed', 'failed', 'failed')
        assert j.completed_at == datetime(2025, 1, 1)
        assert j.error_message == 'science failed'
        assert (Path(j.output_dir) / 'untouched').read_text() == 'science'
        assert (await s.get(ExecutionTarget, 'target')).leased_job_id == 'successor'
        record = j.provenance['remote_diagnostics']
        assert record['state'] == ('returned' if race is None else 'failed' if race == 'failure' else 'returning')
        if race is None:
            assert (Path(record['output_dir']) / 'log').read_text() == 'diagnostic'
            done = BackgroundTasks()
            await ex.request_remote_diagnostic_pull(s, j, done)
            assert not done.tasks
        else:
            assert record['output_dir'] is None


@pytest.mark.asyncio
async def test_abandoned_diagnostic_claim_can_be_explicitly_restarted(store, tmp_path):
    await terminal(store, tmp_path)
    async with store() as s:
        j = await s.get(Job, 'job')
        j.provenance = dict(j.provenance, remote_diagnostics=dict(state='returning',
            identity=ex._pull_identity(j), result_manifest_sha256='d' * 64))
        await s.commit()
    tasks = BackgroundTasks()
    async with store() as s:
        await ex.request_remote_diagnostic_pull(s, await s.get(Job, 'job'), tasks)
    assert len(tasks.tasks) == 1
    # Simulate process death releasing the kernel guard, not a running transfer.
    tasks.tasks[0].args[3].__exit__(None, None, None)


@pytest.mark.asyncio
async def test_stage_uses_cache_and_only_explicit_support_and_inputs(tmp_path, monkeypatch):
    source, support, weights, inputs = [SimpleNamespace(remote_destination=p) for p in
        ['/r/source', '/r/runtime/support-python', '/r/runtime/weights', '/r/input']]
    bundle = SimpleNamespace(remote_runtime_dir='/r/runtime', remote_source_dir='/r/source',
        remote_attempt_dir='/r/attempt', local_attempt_dir=tmp_path,
        source_transfer=source, runtime_transfers=(support, weights), input_transfers=(inputs,))
    seen = []
    async def cache(**kw):
        assert kw['bundle'] is bundle
        await kw['check_fence']()
        await kw['progress']({'phase': 'checking', 'artifact': None, 'message': 'checking'})
        seen.append('cache')
    async def transfer(c, t):
        seen.append(t)
    async def noop(*a, **kw):
        pass
    monkeypatch.setattr('services.remote_execution.cache.stage_cached_bundle', cache)
    monkeypatch.setattr(ex, '_transfer_plan', transfer)
    monkeypatch.setattr(ex, 'run_remote', noop)
    monkeypatch.setattr(ex, 'rsync_to_remote', noop)
    await ex._stage_bundle(None, bundle)
    assert seen == ['cache', support, inputs]


@pytest.mark.asyncio
@pytest.mark.parametrize('supersede', ['lease', 'attempt', 'cancel'])
async def test_launch_cache_progress_is_durable_and_fenced_without_ssh_transaction(store, tmp_path, monkeypatch, supersede):
    await preparing(store)
    async def ready(s, *_):
        return await s.get(ExecutionTarget, 'target')
    async def noop(*a, **kw):
        pass
    b = SimpleNamespace(attempt_id='attempt', envelope_sha256='hash', remote_attempt_dir='/attempt',
        envelope=SimpleNamespace(source_revision='rev', source_tree='tree'))
    monkeypatch.setattr(ex, 'get_ready_target', ready)
    monkeypatch.setattr(ex.RemoteConnection, 'from_target', lambda *_: None)
    monkeypatch.setattr(ex, '_verify_remote_runner', noop)
    monkeypatch.setattr(ex, 'prepare_remote_bundle', lambda **kw: b)
    monkeypatch.setattr(ex, '_remote_receipt', lambda *a, **kw: {'state': 'staging'})
    monkeypatch.setattr(ex, '_archive_envelope', lambda *_: None)
    monkeypatch.setattr(ex, '_cleanup_local_bundle', lambda *_: None)
    async with store() as launch_session:
        async def stage(c, bundle, progress, check_fence):
            assert not launch_session.in_transaction()
            await progress(dict(phase='checking', artifact='weights/model', message='Verifying cached artifact'))
            async with store() as other:
                target = await other.get(ExecutionTarget, 'target')
                assert target.provider_metadata['progress']['phase'] == 'checking'
                assert target.provider_metadata['progress']['operation_id'] == 'attempt'
                job = await other.get(Job, 'job')
                if supersede == 'lease':
                    target.leased_job_id = 'successor'
                elif supersede == 'attempt':
                    job.remote_attempt_id = 'successor'
                else:
                    job.status, job.queue_status = 'cancelled', 'cancelled'
                await other.commit()
            await check_fence()
            pytest.fail('superseded cache operation was allowed to continue')
        monkeypatch.setattr(ex, '_stage_bundle', stage)
        with pytest.raises(ex.RemoteExecutionError, match='superseded'):
            await ex.launch_remote_job(launch_session, await launch_session.get(Job, 'job'), command=['true'])
    async with store() as s:
        j = await s.get(Job, 'job')
        assert j.error_message is None
        assert j.status == ('cancelled' if supersede == 'cancel' else 'queued')


@pytest.mark.parametrize('candidate_id', [None, 'candidate-1'])
def test_worker_parent_candidate_preserves_authority_and_rejects_null(tmp_path, monkeypatch, candidate_id):
    import base64
    import json
    import runpy
    script = Path(__file__).resolve().parents[3] / 'scripts/stage_frustrampnn_parent_candidate.py'
    metadata = dict(candidate_id=candidate_id, parent_job_id='parent-job',
        parent_workflow_id='parent-workflow', producer_stage='structure_prediction',
        producer_candidate_key='candidate-key', requiredness='required')
    source = tmp_path / 'source.cif'
    source.write_text('unchanged science')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('sys.argv', [str(script), '--source', str(source), '--metadata-base64',
        base64.b64encode(json.dumps(metadata).encode()).decode()])
    if candidate_id is None:
        with pytest.raises(ValueError, match='terminal candidate authority is invalid'):
            runpy.run_path(str(script), run_name='__main__')
        assert not list(tmp_path.glob('candidate_*'))
    else:
        runpy.run_path(str(script), run_name='__main__')
        destination = tmp_path / ('candidate_' + candidate_id)
        assert json.loads((destination / 'metadata.json').read_text()) == metadata
        assert (destination / 'source.cif').read_bytes() == source.read_bytes()
