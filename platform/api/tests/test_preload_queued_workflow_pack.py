"""Real isolated SQLite lifecycle; remote byte transport is a bounded fixture.

No provider, SSH, scientific submission, launch, or live database access.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database import Base, ExecutionTarget, Job
from services.gpu_orchestrator import _claim_remote_job
from services.remote_execution import preloading as p, targets, cache, managed_inventory as managed
from services.remote_execution.bundle import CacheTransferArtifact
from services.remote_execution.contracts import WorkflowPackSelection, WorkflowPackRequest, ProvisionPreview

TARGET = 'vast:queued-fixture'
JOB = 'pending-fixture'
SOURCE = ('a' * 40, 'b' * 40)
BOOT = '12345678-1234-1234-1234-123456789abc'
SELECTION = WorkflowPackSelection(kind='workflow_pack', workflow_id='structure_prediction')


@pytest_asyncio.fixture
async def lane(tmp_path, monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'jobs.sqlite'))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add(ExecutionTarget(id=TARGET, provider='vast', provider_instance_id='queued-fixture',
            active=True, state='ready', host='fixture.invalid', port=22, username='root',
            remote_root='/worker', host_key_sha256='c' * 64,
            provider_metadata={'inventory': {'status': 'complete', 'present': True, 'running': True,
                'checked_at': datetime.utcnow().isoformat()}}))
        s.add(Job(id=JOB, name='generic pending', model_id='generic', mode='generic',
            params={'unchanged': 17}, provenance={'launch_approval': 'unchanged'},
            status='queued', queue_status='queued', paused=False, execution_target_id=TARGET))
        await s.commit()
    source = tmp_path / 'generic.bin'
    source.write_bytes(b'generic approved bytes')
    state = SimpleNamespace(factory=factory, source=source, previews=0, transfers=0, activations=0,
        transfer_entered=asyncio.Event(), transfer_release=asyncio.Event(), source_identity=SOURCE,
        quiescent=True, preview_digest=None)
    state.transfer_release.set()
    monkeypatch.setattr(p, 'current_source_identity', lambda: state.source_identity)

    def preview(selection, target, **kwargs):
        assert selection == SELECTION
        state.previews += 1
        data = source.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        digest = state.preview_digest or hashlib.sha256((sha + str(selection)).encode()).hexdigest()
        entries = [CacheTransferArtifact(source, 'weights/generic/model.bin', sha, len(data), 0o644, 'runtime')]
        return ProvisionPreview(selection=selection, preview_sha256=digest,
            artifacts=[dict(name=e.remote_destination, sha256=e.sha256, size_bytes=e.size_bytes) for e in entries],
            total_bytes=len(data), destination={'target_id': TARGET, 'remote_root': '/worker'}), entries

    async def helper(connection, request, fence):
        await fence()
        return {'boot_id': BOOT}

    async def transfer(**kw):
        assert kw['selection'] == SELECTION
        assert kw['source_identity'] == SOURCE
        state.forwarded_backend = kw['backend']
        state.transfers += 1
        state.transfer_entered.set()
        await state.transfer_release.wait()
        await kw['check_fence']()
        # Offline byte-check double; production worker ingest/probe is unchanged.
        for entry in kw['entries']:
            assert hashlib.sha256(entry.source.read_bytes()).hexdigest() == entry.sha256
        return dict(artifacts=[dict(name=e.remote_destination, sha256=e.sha256, size_bytes=e.size_bytes)
                              for e in kw['entries']],
                    preparation=dict(source='cached', weight_layouts=1, backend=kw['backend'],
                        images='ready' if kw['backend'] in {'udocker', 'apptainer'} else 'deferred_backend_unknown'))

    async def activate(connection, manifest, fence, publish, boot):
        await fence()
        state.activations += 1

    async def observe(connection, manifests, fence):
        await fence()
        return managed.ManagedInventory(observed_at=datetime.utcnow(), boot_id=BOOT, releases=[])

    async def quiesce(connection, operation):
        return state.quiescent

    monkeypatch.setattr(cache, 'independent_preview', preview)
    monkeypatch.setattr(cache, 'provision_cache', transfer)
    monkeypatch.setattr(managed, 'helper_call', helper)
    monkeypatch.setattr(managed, 'activate_release', activate)
    monkeypatch.setattr(managed, 'observe_releases', observe)
    state.controller = p.PreloadController(factory, quiesce=quiesce)
    yield state
    await state.controller.close()
    await engine.dispose()


async def approved(lane):
    async with lane.factory() as s:
        preview = await lane.controller.preview(s, TARGET, SELECTION)
    return WorkflowPackRequest(**SELECTION.model_dump(), preview_sha256=preview.preview_sha256)


async def start(lane, request=None, **kw):
    request = request or await approved(lane)
    async with lane.factory() as s:
        return await lane.controller.start(s, TARGET, request, **kw)


async def settle(lane):
    await asyncio.gather(*list(lane.controller.tasks.values()), return_exceptions=True)
    async with lane.factory() as s:
        target = await s.get(ExecutionTarget, TARGET)
        return deepcopy(target.provider_metadata['preload'])


async def claim(lane):
    async with lane.factory() as s:
        return await _claim_remote_job(s, await s.get(Job, JOB), gpu_id=None, gpu_ids=[], vram_estimate_mb=0)


@pytest.mark.asyncio
@pytest.mark.parametrize('paused', [False, True])
@pytest.mark.parametrize('remote_state', [None, 'queued', 'waiting_target', 'waiting_remote_worker',
    'waiting_remote_capacity', 'waiting_remote_gpu', 'waiting_remote_telemetry'])
async def test_queued_pack_prepares_without_job_mutation_or_third_preview(lane, paused, remote_state):
    async with lane.factory() as s:
        job = await s.get(Job, JOB)
        job.paused = paused
        job.remote_state = remote_state
        await s.commit()
        before = p.recipe_snapshot(job).__dict__
        assert await targets._has_nonterminal_jobs(s, TARGET)
        assert not await targets._has_preparation_conflicts(s, TARGET)
        with pytest.raises(targets.ExecutionTargetError):
            await targets.deactivate_target(s, TARGET)
    await start(lane)
    result = await settle(lane)
    assert result['phase'] == 'source_download_ready'
    assert result['selection'] == SELECTION.model_dump()
    assert result['job_id'] is None
    assert lane.previews == 2  # UI approval plus start; no third full inventory hash.
    assert lane.activations == 1
    async with lane.factory() as s:
        assert p.recipe_snapshot(await s.get(Job, JOB)).__dict__ == before
        assert (await s.get(ExecutionTarget, TARGET)).leased_job_id is None
    assert await claim(lane) == (None if paused else {'unchanged': 17})


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [
    {'status': 'running'}, {'queue_status': 'preparing'}, {'started_at': datetime(2020, 1, 1)},
    {'nextflow_run_id': 'run'}, {'remote_attempt_id': 'attempt'}, {'assigned_gpu': 0},
    {'remote_state': 'preparing'}, {'provenance': {'remote_execution_assignment': {'lease_id': 'owned'}}},
])
async def test_active_attempts_refuse_start_and_inventory_refresh(lane, change):
    async with lane.factory() as s:
        job = await s.get(Job, JOB)
        for key, value in change.items():
            setattr(job, key, value)
        await s.commit()
        assert await targets._has_preparation_conflicts(s, TARGET)
        with pytest.raises(targets.ExecutionTargetError, match='idle attached worker'):
            await lane.controller.start(s, TARGET, object())
        with pytest.raises(targets.ExecutionTargetError, match='idle attached worker'):
            await lane.controller.refresh_inventory(s, TARGET)
    assert lane.previews == 0


@pytest.mark.asyncio
async def test_lease_refuses_even_when_job_is_pristine(lane):
    async with lane.factory() as s:
        (await s.get(ExecutionTarget, TARGET)).leased_job_id = JOB
        await s.commit()
        with pytest.raises(targets.ExecutionTargetError, match='idle attached worker'):
            await lane.controller.start(s, TARGET, object())
        with pytest.raises(targets.ExecutionTargetError, match='idle attached worker'):
            await lane.controller.refresh_inventory(s, TARGET)


@pytest.mark.asyncio
async def test_pending_inventory_refresh_does_not_change_job(lane):
    async with lane.factory() as s:
        before = p.recipe_snapshot(await s.get(Job, JOB)).__dict__
        observed = await lane.controller.refresh_inventory(s, TARGET)
        assert str(observed.boot_id) == BOOT
    async with lane.factory() as s:
        assert p.recipe_snapshot(await s.get(Job, JOB)).__dict__ == before


@pytest.mark.asyncio
async def test_scheduler_wins_during_start_preview_then_preload_cas_refuses(lane, monkeypatch):
    request = await approved(lane)
    original = lane.controller._preview
    async def racing_preview(*args, **kwargs):
        result = await original(*args, **kwargs)
        assert await claim(lane) == {'unchanged': 17}
        return result
    monkeypatch.setattr(lane.controller, '_preview', racing_preview)
    with pytest.raises(targets.ExecutionTargetError, match='activity changed'):
        await start(lane, request)
    async with lane.factory() as s:
        assert (await s.get(ExecutionTarget, TARGET)).leased_job_id == JOB
        assert (await s.get(Job, JOB)).queue_status == 'preparing'
    assert lane.transfers == 0


@pytest.mark.asyncio
async def test_preload_wins_claim_then_terminal_handoff(lane):
    lane.transfer_release.clear()
    await start(lane)
    await asyncio.wait_for(lane.transfer_entered.wait(), 5)
    assert await claim(lane) is None
    lane.transfer_release.set()
    assert (await settle(lane))['phase'] == 'source_download_ready'
    assert await claim(lane) == {'unchanged': 17}
    async with lane.factory() as s:
        assert (await s.get(ExecutionTarget, TARGET)).leased_job_id == JOB
        assert (await s.get(Job, JOB)).queue_status == 'preparing'


@pytest.mark.asyncio
@pytest.mark.parametrize('quiet', [True, False])
async def test_cancel_retains_pending_job_and_existing_quiescence_rule(lane, quiet):
    lane.quiescent = quiet
    lane.transfer_release.clear()
    await start(lane)
    await asyncio.wait_for(lane.transfer_entered.wait(), 5)
    async with lane.factory() as s:
        row = await s.get(ExecutionTarget, TARGET)
        operation = row.provider_metadata['preload']['operation_id']
        await lane.controller.cancel(s, TARGET, operation)
    result = await settle(lane)
    assert result['phase'] == ('cancelled' if quiet else 'recovery_blocked')
    assert result['recovery_required'] is not quiet
    async with lane.factory() as s:
        job = await s.get(Job, JOB)
        assert (job.status, job.queue_status, job.remote_attempt_id, job.paused) == ('queued', 'queued', None, False)
    assert await claim(lane) == ({'unchanged': 17} if quiet else None)


@pytest.mark.asyncio
async def test_cancelled_pack_retries_same_selection_explicitly(lane):
    request = await approved(lane)
    lane.transfer_release.clear()
    await start(lane, request)
    await asyncio.wait_for(lane.transfer_entered.wait(), 5)
    async with lane.factory() as s:
        operation = (await s.get(ExecutionTarget, TARGET)).provider_metadata['preload']['operation_id']
        await lane.controller.cancel(s, TARGET, operation)
    lane.transfer_release.set()
    await start(lane, request, retry_operation_id=operation)
    result = await settle(lane)
    assert result['phase'] == 'source_download_ready'
    assert result['operation_id'] != operation
    assert result['selection'] == SELECTION.model_dump()


@pytest.mark.asyncio
async def test_changed_preview_digest_still_refuses(lane):
    request = await approved(lane)
    lane.preview_digest = 'f' * 64
    with pytest.raises(targets.ExecutionTargetError, match='preview changed'):
        await start(lane, request)
    assert lane.transfers == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['bytes', 'touch', 'source', 'endpoint'])
async def test_admitted_plan_preserves_source_and_endpoint_checks(lane, monkeypatch, mutation):
    original = lane.controller._run
    entered, release = asyncio.Event(), asyncio.Event()
    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        await original(*args, **kwargs)
    monkeypatch.setattr(lane.controller, '_run', delayed)
    await start(lane)
    await entered.wait()
    if mutation == 'bytes':
        lane.source.write_bytes(b'changed approved bytes')
    elif mutation == 'touch':
        lane.source.touch()
    elif mutation == 'source':
        lane.source_identity = ('d' * 40, 'e' * 40)
    else:
        async with lane.factory() as s:
            (await s.get(ExecutionTarget, TARGET)).host = 'replacement.invalid'
            await s.commit()
    release.set()
    assert (await settle(lane))['phase'] == ('source_download_ready' if mutation == 'touch' else 'failed')
    assert lane.previews == (3 if mutation in {'bytes', 'touch'} else 2)
    assert lane.transfers == (1 if mutation == 'touch' else 0)
    assert lane.activations == (1 if mutation == 'touch' else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize('backend', [None, 'udocker', 'apptainer'])
async def test_pack_passes_existing_backend_without_inference(lane, backend):
    async with lane.factory() as s:
        row = await s.get(ExecutionTarget, TARGET)
        row.capabilities = {'critical_runtime_binding': {'environment': {'BMS_CONTAINER_BACKEND': backend}}}
        await s.commit()
    await start(lane)
    result = await settle(lane)
    assert result['phase'] == 'source_download_ready'
    assert result['artifacts'][0]['name'] == 'weights/generic/model.bin'
    assert 'source cached and 1 shared weight layouts prepared' in result['message']
    assert 'scientific readiness remains unverified' in result['message']
    assert ('runtime images prepared' in result['message']) == (backend is not None)
    assert ('preparation deferred: attached backend is unknown' in result['message']) == (backend is None)
    assert lane.forwarded_backend == backend


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['checking', 'transferring', 'verifying', 'cancelling',
    'recovery_blocked', 'source_download_ready', 'failed', 'cancelled'])
async def test_existing_scheduler_phase_handoff_is_not_success_only(lane, phase):
    async with lane.factory() as s:
        target = await s.get(ExecutionTarget, TARGET)
        target.provider_metadata = dict(target.provider_metadata, preload={'phase': phase})
        await s.commit()
    assert await claim(lane) == (None if phase in p.PRELOAD_ACTIVE_PHASES else {'unchanged': 17})


@pytest.mark.asyncio
async def test_source_revision_changes_during_start_preview_refuse(lane, monkeypatch):
    request = await approved(lane)
    original = lane.controller._preview
    async def changed_source(*args, **kwargs):
        result = await original(*args, **kwargs)
        lane.source_identity = ('d' * 40, 'e' * 40)
        return result
    monkeypatch.setattr(lane.controller, '_preview', changed_source)
    with pytest.raises(targets.ExecutionTargetError, match='Source identity changed'):
        await start(lane, request)
    assert lane.transfers == 0


@pytest.mark.asyncio
async def test_inventory_refresh_loses_racing_claim_without_releasing_lease(lane, monkeypatch):
    original = managed.observe_releases
    async def racing_read(*args):
        result = await original(*args)
        assert await claim(lane) == {'unchanged': 17}
        return result
    monkeypatch.setattr(managed, 'observe_releases', racing_read)
    async with lane.factory() as s:
        with pytest.raises(targets.ExecutionTargetError, match='readback failed'):
            await lane.controller.refresh_inventory(s, TARGET)
    async with lane.factory() as s:
        target = await s.get(ExecutionTarget, TARGET)
        assert target.leased_job_id == JOB
        assert target.provider_metadata['managed_inventory']['refresh_failed'] is True


@pytest.mark.asyncio
async def test_provider_inventory_staleness_not_relaxed(lane):
    async with lane.factory() as s:
        row = await s.get(ExecutionTarget, TARGET)
        metadata = deepcopy(row.provider_metadata)
        metadata['inventory']['checked_at'] = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
        row.provider_metadata = metadata
        await s.commit()
        with pytest.raises(targets.ExecutionTargetError, match='current inventory'):
            await lane.controller.start(s, TARGET, object())
        with pytest.raises(targets.ExecutionTargetError, match='current provider inventory'):
            await lane.controller.refresh_inventory(s, TARGET)
