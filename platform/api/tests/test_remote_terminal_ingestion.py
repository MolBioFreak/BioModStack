"""Real SQLite/native finalizer and generation custody; inert importer/transport."""
import hashlib
import json
from pathlib import Path

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import text

from database import Design, ExecutionTarget, Job
from services.remote_execution import executor as ex, result_generation as gen
from test_remote_lifecycle_gaps import store
from test_remote_manual_result_pull import ready
from test_remote_result_generation import package


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['empty', 'transient', 'cancelled', 'identity', 'receipt'])
async def test_native_rejection_and_import_only_repair(store, tmp_path, monkeypatch, outcome):
    from services import result_ingester, analysis_autorun
    from routers import queue

    await ready(store)
    async with store() as session:
        job = await session.get(Job, 'job')
        job.output_dir = str(tmp_path / 'output')
        job.params = dict(job.params or {}, remote_result_policy='automatic')
        manifest, incoming, status = package(job)
        Path(job.output_dir).mkdir()
        (Path(job.output_dir) / 'old.txt').write_text('retained')
        contract = ex.resolve_job_result_contract(job)
        job.provenance = {'remote_execution_receipt': {
            **{k: v for k, v in ex._pull_identity(job).items() if k != 'schema'},
            'state': 'succeeded', 'exit_code': 0, 'error': 'retained native diagnostic',
            'remote_attempt_dir': '/fixture/attempt',
            'result_manifest_sha256': status.result_manifest_sha256,
            'expected_result_contract_sha256': hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}}
        target = await session.get(ExecutionTarget, 'target')
        target.leased_job_id = 'foreign-job'
        lease = target.lease_acquired_at
        await session.execute(text('CREATE TABLE test_projection (id TEXT PRIMARY KEY)'))
        await session.commit()
    attempts = []

    async def ingest(job_id, output_dir, session, **kwargs):
        assert kwargs['commit'] is False
        assert (Path(output_dir) / 'first.txt').read_text() == 'first'
        await session.execute(text("INSERT INTO test_projection VALUES ('projection')"))
        attempts.append(1)
        if len(attempts) == 1:
            if outcome == 'transient':
                raise RuntimeError('temporary importer failure')
            return 0  # Exercise the real generic finalizer's exact rejection.
        # Repaired inert importer now projects a contained derived structure.
        (Path(output_dir) / 'derived.pdb').write_text(
            'ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 90.00           C\nEND\n')
        session.add(Design(id='design', job_id=job_id, name='derived', pdb_path='derived.pdb'))
        await session.flush()
        return 1

    async def proof(*args):
        assert not attempts, 'Import retry must not query provider'

    async def remote_status(*args):
        assert not attempts, 'Import retry must not contact worker'
        return status

    async def collect(*args):
        assert not attempts, 'Import retry must reuse received bytes'
        ex._verify_result_package(incoming, args[1], status)
        return manifest, incoming

    monkeypatch.setattr(result_ingester, 'ingest_job_results', ingest)
    monkeypatch.setattr(analysis_autorun, 'schedule_viewer_minimum_analyses_for_job', lambda *_: None)
    monkeypatch.setattr(ex, '_prove_pull_endpoint', proof)
    monkeypatch.setattr(ex, 'remote_status', remote_status)
    monkeypatch.setattr(ex, 'collect_remote_results', collect)
    monkeypatch.setattr(queue, '_get_queue_enrichment', lambda jobs: {})
    if outcome in {'cancelled', 'identity', 'receipt'}:
        from services.result_state_integrity import NoDesignResults
        finalize = ex._finalize_pulled_results

        async def competing_finalizer(*args):
            try:
                return await finalize(*args)
            except NoDesignResults:
                # The real finalizer rolled back; another owner now wins.
                async with store() as other:
                    current = await other.get(Job, 'job')
                    if outcome == 'cancelled':
                        current.status = current.queue_status = 'cancelled'
                    elif outcome == 'identity':
                        current.remote_attempt_id = 'successor'
                        current.nextflow_run_id = 'remote:successor'
                    else:
                        current.provenance = dict(current.provenance, remote_execution_receipt={})
                    current.error_message = 'successor-owned'
                    await other.commit()
                raise
        monkeypatch.setattr(ex, '_finalize_pulled_results', competing_finalizer)
    tasks = BackgroundTasks()
    async with store() as session:
        await ex.request_remote_result_pull(session, await session.get(Job, 'job'), tasks)
    await tasks()
    async with store() as session:
        job = await session.get(Job, 'job')
        assert await session.scalar(text('SELECT count(*) FROM test_projection')) == 0
        if outcome in {'cancelled', 'identity', 'receipt'}:
            assert job.remote_state == 'returning'
            assert job.error_message == 'successor-owned'
            assert job.status == ('cancelled' if outcome == 'cancelled' else 'running')
            assert job.remote_attempt_id == ('successor' if outcome == 'identity' else 'attempt')
            assert (await session.get(ExecutionTarget, 'target')).leased_job_id == 'foreign-job'
            if outcome == 'receipt':
                assert job.provenance['remote_execution_receipt'] == {}
                assert (incoming / 'first.txt').read_text() == 'first'
            return
        assert (Path(job.output_dir) / 'old.txt').read_text() == 'retained'
        assert (incoming / 'first.txt').read_text() == 'first'
        assert not gen.journal_path(job).exists()
        assert 'remote_result_generation' not in job.provenance
        receipt = dict(job.provenance['remote_execution_receipt'])
        assert receipt['received_manifest_sha256'] == receipt['result_manifest_sha256']
        assert (receipt['state'], receipt['exit_code'], receipt['error']) == ('succeeded', 0, 'retained native diagnostic')
        if outcome == 'empty':
            assert (job.status, job.queue_status, job.remote_state) == ('failed', 'failed', 'result_import_failed')
            assert not job.awaiting_input and job.awaiting_stage is None and job.awaiting_payload == {}
            assert job.completed_at is not None
            assert 'workflow completed but result ingestion produced no designs' in job.error_message
            assert await queue.list_queue(session=session) == []
        else:
            assert (job.status, job.queue_status, job.remote_state) == ('awaiting_input', 'completed', 'result_pull_failed')
            assert job.awaiting_input and job.awaiting_stage == 'remote_results'
        target = await session.get(ExecutionTarget, 'target')
        assert (target.leased_job_id, target.lease_acquired_at) == ('foreign-job', lease)
        automatic = BackgroundTasks()
        assert not await ex.request_remote_result_pull(session, job, automatic, automatic=True)
        assert not await ex.reconcile_remote_job(session, job, background_tasks=automatic)
        assert not automatic.tasks
        retry = BackgroundTasks()
        assert await ex.request_remote_result_pull(session, job, retry)
    await retry()
    async with store() as session:
        job = await session.get(Job, 'job')
        assert (job.status, job.queue_status, job.remote_state) == ('completed', 'completed', 'ingested'), job.error_message
        assert await session.scalar(text('SELECT count(*) FROM test_projection')) == 1
        assert await session.get(Design, 'design') is not None
        assert len(attempts) == 2
        assert job.provenance['remote_execution_receipt']['state'] == 'succeeded'
        assert (await session.get(ExecutionTarget, 'target')).leased_job_id == 'foreign-job'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['cancelled', 'identity', 'receipt', 'published_failure'])
async def test_terminal_import_retry_refreshes_authority(store, monkeypatch, change):
    await ready(store)
    async with store() as session:
        job = await session.get(Job, 'job')
        job.status = job.queue_status = 'failed'
        job.remote_state = 'result_import_failed'
        job.awaiting_input, job.awaiting_stage, job.awaiting_payload = False, None, {}
        job.provenance = {'remote_execution_receipt': {
            **{k: v for k, v in ex._pull_identity(job).items() if k != 'schema'},
            'state': 'succeeded', 'exit_code': 0,
            'received_manifest_sha256': 'd' * 64, 'result_manifest_sha256': 'd' * 64}}
        await session.commit()
    async with store() as stale:
        job = await stale.get(Job, 'job')
        async with store() as other:
            current = await other.get(Job, 'job')
            if change == 'cancelled':
                current.status = current.queue_status = 'cancelled'
            elif change == 'identity':
                current.remote_attempt_id = 'successor'
                current.nextflow_run_id = 'remote:successor'
            elif change == 'published_failure':
                current.remote_state = 'returned_ingestion_failed'
            else:
                current.provenance = {'remote_execution_receipt': {}}
            await other.commit()
        tasks = BackgroundTasks()
        with pytest.raises(ex.RemoteExecutionError):
            await ex.request_remote_result_pull(stale, job, tasks)
        assert not tasks.tasks
    async with store() as session:
        job = await session.get(Job, 'job')
        assert job.remote_state == ('returned_ingestion_failed' if change == 'published_failure' else 'result_import_failed')
        assert job.status == ('cancelled' if change == 'cancelled' else 'failed')
