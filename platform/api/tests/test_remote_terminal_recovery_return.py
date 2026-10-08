"""Recovered worker bytes through real controller, transfer and authorized reads.

Only SSH/rsync transport and endpoint proof are offline doubles. Sealing,
manifest verification, reconciliation, DB fences and native return are real.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks

from database import Job, ExecutionTarget
from services.remote_execution import executor as ex
from tools import bms_remote_worker as worker
from test_remote_lifecycle_gaps import store
from test_remote_rectify_return import mounted
from test_remote_terminal_recovery import attempt


@pytest.mark.asyncio
@pytest.mark.parametrize('state,reboot', [('running', True), ('prepared', False)])
async def test_recovered_terminal_explicit_return_and_native_download(store, tmp_path, monkeypatch, state, reboot):
    directory = tmp_path / 'worker-attempt'
    envelope = attempt(directory, state, reboot=reboot)
    if not reboot:
        worker.atomic_json(directory / worker.CANCEL_REQUEST_FILE, {'attempt_id': 'attempt'})
    calls = []
    async def rpc(_, argv, **kwargs):
        calls.append(argv[0])
        if argv[0] == 'status':
            return SimpleNamespace(stdout=json.dumps(worker.status(directory)))
        if argv[:2] == ['python3', '-c']:
            # Exact production bounded manifest-reader argv; no fabricated JSON.
            path = Path(argv[3])
            assert path == directory / 'results/result-manifest.json'
            raw = path.read_bytes()
            assert len(raw) <= int(argv[4])
            return SimpleNamespace(stdout=raw.decode())
        pytest.fail(f'unexpected operation {argv[0]}')
    async def transfer(_, source, destination, paths, **kwargs):
        calls.append('transfer')
        assert source.rstrip('/') == str(directory / 'results')
        for relative in paths:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((directory / 'results' / relative).read_bytes())
    async def proof(*args, **kwargs):
        assert kwargs == {'diagnostics': True}
    monkeypatch.setattr(ex, '_connection_for_attempt', lambda *_: (None, str(directory)))
    monkeypatch.setattr(ex, '_worker_argv', lambda _, op, *args: [op])
    monkeypatch.setattr(ex, 'run_remote', rpc)
    monkeypatch.setattr(ex, 'rsync_selected_from_remote', transfer)
    monkeypatch.setattr(ex, '_prove_pull_endpoint', proof)
    async with store() as session:
        job = await session.get(Job, 'job')
        target = await session.get(ExecutionTarget, 'target')
        job.execution_bundle_sha256 = worker.sha256_file(worker.envelope_path(directory))
        job.output_dir = str(tmp_path / 'untouched-science')
        Path(job.output_dir).mkdir()
        (Path(job.output_dir) / 'prior-good').write_text('keep native science')
        job.provenance = dict(remote_execution_receipt=dict(remote_attempt_dir=str(directory),
            lease_acquired_at=target.lease_acquired_at.isoformat(), boot_id=envelope.get('boot_id', 'previous-boot'),
            generation=0))
        await session.commit()
        assert await ex.reconcile_remote_job(session, job)
    assert calls == ['status']  # Polling is not authorization to retrieve.
    async with store() as session:
        job = await session.get(Job, 'job')
        assert job.status == 'failed' and job.remote_state != 'failed_integrity'
        receipt = job.provenance['remote_execution_receipt']
        assert receipt['state'] == ('lost' if reboot else 'cancelled')
        assert receipt['result_manifest_sha256']
        original = (job.status, job.error_message, job.completed_at)
        assert (await session.get(ExecutionTarget, 'target')).leased_job_id is None
        (await session.get(ExecutionTarget, 'target')).leased_job_id = 'successor'
        await session.commit()
        tasks = BackgroundTasks()
        await ex.request_remote_diagnostic_pull(session, job, tasks)
    await tasks()
    async with store() as session:
        job = await session.get(Job, 'job')
        assert job.provenance['remote_diagnostics']['state'] == 'returned', job.provenance
        assert (job.status, job.error_message, job.completed_at) == original
        assert (await session.get(ExecutionTarget, 'target')).leased_job_id == 'successor'
        assert (Path(job.output_dir) / 'prior-good').read_text() == 'keep native science'
        duplicate = BackgroundTasks()
        await ex.request_remote_diagnostic_pull(session, job, duplicate)
        assert not duplicate.tasks
    before = list(calls)
    async with mounted(store) as client:
        listed = await client.get('/api/jobs/job/remote-artifacts', params={'diagnostics': True})
        assert listed.status_code == 200, listed.text
        rows = listed.json()['artifacts']
        log = next(row for row in rows if row['relative_path'] == '_remote/recovery/nextflow.log')
        downloaded = await client.get(log['download_url'])
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.text == 'failure API_TOKEN=[REDACTED]\n'
    assert calls == before
