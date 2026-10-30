"""Idle reconcile sweeps must not append events; terminal state lands at the log cap.

Real scratch SQLite stores and the real reconcile owner; inert Job fixtures, no
scientific execution.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from test_native_protein_launch_authority_regression import (  # noqa: F401
    native_core_store, native_http, policy_cache, prepared_setup,
)
from test_project_workflow_setups import setup_store  # noqa: F401


async def _running_bc2(native_http, setup_store, native_core_store):
    from database import Job
    from experiment_models import ExperimentWorkflowPreparation
    from routers import project_manager as pm
    from services.global_experiments.launch_contexts import claim_launch_context, consume_launch_context

    project_id, _setup, prepared, domain_path = await prepared_setup(native_http, 'bindcraft2')
    response = await native_http.post(domain_path + '/run-groups', json={
        'preparation_launches': [{'preparation_id': prepared['preparation_id'],
                                  'launch_context_id': prepared['launch_context_id']}]},
        headers={'Idempotency-Key': 'idle-run'})
    assert response.status_code == 201, response.text
    group_doc = response.json()
    attempt_doc = group_doc['runs'][0]['attempts'][0]
    async with setup_store() as session, native_core_store() as core:
        preparation = await session.get(ExperimentWorkflowPreparation, prepared['preparation_id'])
        scheduler = json.loads(preparation.scheduler_payload_json)
        job = Job(id=attempt_doc['canonical_job_id'], name=scheduler['name'],
                  model_id=scheduler['model_id'], mode=scheduler['mode'], params=scheduler['params'],
                  status='running', queue_status='running', provenance={},
                  started_at=datetime.now(timezone.utc) - timedelta(hours=1))
        core.add(job)
        await core.flush()
        context, token = await claim_launch_context(session, prepared['launch_context_id'])
        context, binding = await consume_launch_context(
            session, launch_context_id=context.launch_context_id, claim_token=token,
            canonical_job_id=job.id, canonical_batch_id=None)
        await pm._project_bound_job(session, core, context, job, binding)
        await session.commit()
        await core.commit()
    return project_id, group_doc['run_group_id'], attempt_doc['attempt_id'], job.id


async def _reconcile(setup_store, native_core_store, project_id, group_id):
    from experiment_services import reconcile_run_group

    async with setup_store() as session, native_core_store() as core:
        group = await reconcile_run_group(session, core, project_id, group_id)
        await session.commit()
        return group.state, group.generation


async def _live_events(setup_store):
    from experiment_models import ExperimentRunEvent

    async with setup_store() as session:
        return await session.scalar(select(func.count()).select_from(ExperimentRunEvent).where(
            ExperimentRunEvent.event_type == 'core_live_projected'))


@pytest.mark.asyncio
async def test_unchanged_running_job_appends_no_events_across_sweeps(
        native_http, setup_store, native_core_store):
    import asyncio

    project_id, group_id, _attempt, _job = await _running_bc2(native_http, setup_store, native_core_store)
    first = await _reconcile(setup_store, native_core_store, project_id, group_id)
    events = await _live_events(setup_store)
    assert first[0] == 'running' and events == 1
    # Real wall-clock seconds pass between sweeps (a stored elapsed-seconds
    # counter would change); the receipt must not change with them.
    for _ in range(2):
        await asyncio.sleep(1.1)
        assert await _reconcile(setup_store, native_core_store, project_id, group_id) == first
    assert await _live_events(setup_store) == 1


@pytest.mark.asyncio
async def test_terminal_projection_lands_when_status_log_is_at_cap(
        native_http, setup_store, native_core_store, monkeypatch):
    from database import Job
    from experiment_models import ExperimentRunAttempt
    from services import ngs_molbio_n5

    project_id, group_id, attempt_id, job_id = await _running_bc2(native_http, setup_store, native_core_store)
    assert (await _reconcile(setup_store, native_core_store, project_id, group_id))[0] == 'running'
    # The open status stream now holds exactly as many chunks as the cap.
    monkeypatch.setattr(ngs_molbio_n5, '_ATTEMPT_LOG_CHUNK_LIMIT', 1)
    async with native_core_store() as core:
        job = await core.get(Job, job_id)
        job.status = job.queue_status = 'failed'
        job.completed_at = datetime.now(timezone.utc)
        job.error_message = 'Inert terminal fixture'
        await core.commit()
    state, generation = await _reconcile(setup_store, native_core_store, project_id, group_id)
    assert state == 'failed'
    async with setup_store() as session:
        attempt = await session.get(ExperimentRunAttempt, attempt_id)
        assert attempt.state == 'failed' and attempt.terminal_receipt_json
    # Terminal and idle afterwards: further sweeps change nothing.
    assert await _reconcile(setup_store, native_core_store, project_id, group_id) == (state, generation)


@pytest.mark.asyncio
async def test_cap_still_refuses_open_chunks_but_accepts_the_closing_one(
        native_http, setup_store, native_core_store, monkeypatch):
    from services import ngs_molbio_n5

    project_id, group_id, attempt_id, _job = await _running_bc2(native_http, setup_store, native_core_store)
    await _reconcile(setup_store, native_core_store, project_id, group_id)  # one status chunk
    monkeypatch.setattr(ngs_molbio_n5, '_ATTEMPT_LOG_CHUNK_LIMIT', 1)
    async with setup_store() as session:
        with pytest.raises(ngs_molbio_n5.ValidationFailure, match='chunk limit'):
            await ngs_molbio_n5.append_attempt_log_chunk(
                session, attempt_id=attempt_id, stream_name='status', content='{"open":2}')
    async with setup_store() as session:
        chunk = await ngs_molbio_n5.append_attempt_log_chunk(
            session, attempt_id=attempt_id, stream_name='status', content='{"final":true}', close=True)
        assert chunk.sequence_number == 1
        await session.commit()


def test_late_output_check_is_bounded_and_reacts_to_new_provenance():
    from types import SimpleNamespace

    import experiment_services as es

    es._late_output_checks.clear()
    job = SimpleNamespace(provenance={'a': 1})
    # Sweep every 2 s for an hour with unchanged provenance.
    due = [t for t in range(0, 3600, 2) if es._late_output_check_due('attempt', job, now=float(t))]
    assert due[:2] == [0, 2]  # one immediate retry after a transient failure
    assert len(due) < 15  # instead of 1800 full verifications
    gaps = [b - a for a, b in zip(due, due[1:])]
    assert max(gaps) <= es._LATE_OUTPUT_RECHECK_SECONDS + 2
    # A late publication changes provenance and is checked on the next sweep.
    job.provenance = {'a': 1, 'published': True}
    assert es._late_output_check_due('attempt', job, now=3600.0)
    es._late_output_checks.clear()
