import asyncio
from datetime import datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database import Base, ExecutionTarget, Job, get_session
from routers.execution_targets import router
from services.remote_execution import preloading as p
from services.remote_execution.contracts import PreloadRequest
from services.remote_execution.progress import preload_idle_clause
from services.remote_execution.targets import deactivate_target, ExecutionTargetError


@pytest_asyncio.fixture
async def store(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.sqlite'}")
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(p, 'current_source_identity', lambda: ('a'*40, 'b'*40))
    monkeypatch.setattr(p, 'compile_recipe', lambda job: ['nextflow', '--saved', str(job.params['science'])])
    async with factory() as s:
        s.add(Job(id='recipe', name='recipe', model_id='boltz2', mode='predict',
            params={'science':17}, status='completed', queue_status='completed', output_dir='/managed/results/recipe'))
        s.add(ExecutionTarget(id='vast:1', provider='vast', provider_instance_id='1',
            active=True, state='ready', host='host', port=22, username='root', host_key_sha256='c'*64,
            provider_metadata={'inventory':{'status':'complete','present':True,'running':True,
                'checked_at':datetime.utcnow().isoformat()}}))
        await s.commit()
    yield factory
    await engine.dispose()


async def settle(controller):
    await asyncio.gather(*list(controller.tasks.values()))


@pytest.mark.asyncio
async def test_mounted_post_only_progress_and_no_job_mutation(store):
    gate = asyncio.Event()
    reached = asyncio.Event()
    calls = []
    async def prewarm(**kw):
        calls.append(kw)
        await kw['progress']({'phase':'transferring','artifact':'containers/frustrampnn.sif',
            'message':'Uploading containers/frustrampnn.sif'})
        reached.set()
        await gate.wait()
        await kw['check_fence']()
        return {'source_revision':'a'*40,'source_tree':'b'*40,'artifacts':[]}
    controller = p.PreloadController(store, prewarm=prewarm)
    app = FastAPI()
    app.include_router(router, prefix='/execution-targets')
    app.state.preload_controller = controller
    async def session():
        async with store() as s:
            yield s
    app.dependency_overrides[get_session] = session
    async with store() as s:
        before = p.recipe_digest(await s.get(Job,'recipe'))
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get('/execution-targets')).status_code == 200
        assert calls == []
        assert (await client.post('/execution-targets/vast:1/preload',json={'job_id':'recipe','path':'/etc/passwd'})).status_code == 422
        result = await client.post('/execution-targets/vast:1/preload',json={'job_id':'recipe'})
        assert result.status_code == 202, result.text
        await reached.wait()
        rows = (await client.get('/execution-targets')).json()
        assert rows[0]['preload']['artifact'] == 'containers/frustrampnn.sif'
        assert (await client.post('/execution-targets/vast:1/preload',json={'job_id':'recipe'})).status_code == 409
        async with store() as s:
            with pytest.raises(ExecutionTargetError):
                await deactivate_target(s,'vast:1')
            from sqlalchemy import update
            claimed = await s.execute(update(ExecutionTarget).where(ExecutionTarget.id=='vast:1',preload_idle_clause()).values(leased_job_id='other'))
            assert claimed.rowcount == 0
            await s.rollback()
        gate.set()
        await settle(controller)
        rows = (await client.get('/execution-targets')).json()
        assert rows[0]['preload']['phase'] == 'source_download_ready'
        assert 'launch still prepares support Python' in rows[0]['preload']['message']
    async with store() as s:
        assert p.recipe_digest(await s.get(Job,'recipe')) == before
        assert (await s.get(ExecutionTarget,'vast:1')).leased_job_id is None
    assert calls[0]['command'] == ['nextflow','--saved','17']


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['endpoint','recipe','source','inventory'])
async def test_completion_fails_closed_on_authority_change(store,monkeypatch,change):
    async def prewarm(**kw):
        async with store() as s:
            target = await s.get(ExecutionTarget,'vast:1')
            if change == 'endpoint': target.host = 'new-host'
            if change == 'recipe': (await s.get(Job,'recipe')).params = {'science':18}
            if change == 'inventory': target.provider_metadata = {**target.provider_metadata,'inventory':{'status':'unknown'}}
            await s.commit()
        if change == 'source': monkeypatch.setattr(p,'current_source_identity',lambda: ('d'*40,'e'*40))
        return {'source_revision':'a'*40,'source_tree':'b'*40}
    controller = p.PreloadController(store,prewarm=prewarm)
    async with store() as s: await controller.start(s,'vast:1',PreloadRequest(job_id='recipe'))
    await settle(controller)
    async with store() as s:
        assert (await s.get(ExecutionTarget,'vast:1')).provider_metadata['preload']['phase'] == 'failed'


@pytest.mark.asyncio
async def test_restart_marks_interruption_and_explicit_retry(store):
    async def prewarm(**kw):
        raise RuntimeError('secret-bearing stderr must not be projected')
    controller = p.PreloadController(store,prewarm=prewarm)
    async with store() as s: await controller.start(s,'vast:1',PreloadRequest(job_id='recipe'))
    await settle(controller)
    async with store() as s:
        row = await s.get(ExecutionTarget,'vast:1')
        metadata = dict(row.provider_metadata)
        metadata['preload'] = {**metadata['preload'],'phase':'transferring'}
        row.provider_metadata = metadata
        old = metadata['preload']['operation_id']
        await s.commit()
    restarted = p.PreloadController(store,prewarm=prewarm)
    await restarted.recover()
    assert not restarted.tasks
    async with store() as s:
        row = await s.get(ExecutionTarget,'vast:1')
        assert row.provider_metadata['preload']['phase'] == 'failed'
        assert 'restart' in row.provider_metadata['preload']['message']
        response = await restarted.start(s,'vast:1',PreloadRequest(job_id='recipe'))
        assert response.preload.operation_id != old
    await settle(restarted)


@pytest.mark.asyncio
@pytest.mark.parametrize('remote_state', ['results_available', 'result_pull_failed', 'returning'])
async def test_pending_return_without_lease_allows_preload_and_detach(store, remote_state):
    async with store() as s:
        job = await s.get(Job, 'recipe')
        job.execution_target_id = 'vast:1'
        job.status = 'awaiting_input'
        job.awaiting_stage = 'remote_results'
        job.remote_state = remote_state
        await s.commit()
    async def prewarm(**kw):
        return {'source_revision': 'a'*40, 'source_tree': 'b'*40}
    controller = p.PreloadController(store, prewarm=prewarm)
    async with store() as s:
        await controller.start(s, 'vast:1', PreloadRequest(job_id='recipe'))
    await settle(controller)
    async with store() as s:
        assert (await deactivate_target(s, 'vast:1')).active is False
        assert (await s.get(Job, 'recipe')).status == 'awaiting_input'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['attempt', 'job_terminal', 'remote_terminal', 'progress_terminal', 'lease'])
async def test_job_progress_rejects_delayed_callback_after_db_race(store, change):
    from sqlalchemy import update
    from services.remote_execution.progress import publish_job_progress
    async with store() as s:
        job = await s.get(Job, 'recipe')
        job.status = 'running'
        job.remote_state = 'running'
        job.remote_attempt_id = 'old-attempt'
        job.execution_target_id = 'vast:1'
        (await s.get(ExecutionTarget, 'vast:1')).leased_job_id = job.id
        await s.commit()
    async with store() as stale:
        job = await stale.get(Job, 'recipe')
        assert await publish_job_progress(stale, job, phase='running', artifact=None, message='Running')
        async with store() as writer:
            if change == 'attempt':
                await writer.execute(update(Job).where(Job.id == job.id).values(remote_attempt_id='new-attempt'))
            elif change == 'job_terminal':
                await writer.execute(update(Job).where(Job.id == job.id).values(status='completed'))
            elif change == 'remote_terminal':
                await writer.execute(update(Job).where(Job.id == job.id).values(remote_state='succeeded'))
            elif change == 'lease':
                await writer.execute(update(ExecutionTarget).values(leased_job_id=None))
            else:
                row = await writer.get(ExecutionTarget, 'vast:1')
                row.provider_metadata = {**row.provider_metadata, 'progress': {
                    **row.provider_metadata['progress'], 'phase': 'completed'}}
            await writer.commit()
        assert not await publish_job_progress(stale, job, phase='transferring', artifact=None, message='Delayed')
    async with store() as s:
        progress = (await s.get(ExecutionTarget, 'vast:1')).provider_metadata['progress']
        assert progress['message'] == 'Running'


@pytest.mark.asyncio
@pytest.mark.parametrize('phase,operation', [('failed', 'old'), ('source_download_ready', 'old'), ('checking', 'new')])
async def test_preload_publish_cannot_overwrite_terminal_or_new_operation(store, phase, operation):
    from services.remote_execution.contracts import PreloadProgress
    now = datetime.utcnow()
    old = PreloadProgress(operation_id='old', job_id='recipe', source_revision='a'*40,
        source_tree='b'*40, request_sha256='c'*64, phase='checking', message='Checking',
        started_at=now, updated_at=now)
    controller = p.PreloadController(store)
    async with store() as stale:
        await stale.get(ExecutionTarget, 'vast:1')
        async with store() as writer:
            row = await writer.get(ExecutionTarget, 'vast:1')
            row.provider_metadata = {**row.provider_metadata, 'preload':
                old.model_copy(update={'phase': phase, 'operation_id': operation}).model_dump(mode='json')}
            await writer.commit()
        with pytest.raises(ExecutionTargetError, match='superseded'):
            await controller._publish(stale, 'vast:1', old)
    async with store() as s:
        current = (await s.get(ExecutionTarget, 'vast:1')).provider_metadata['preload']
        assert (current['phase'], current['operation_id']) == (phase, operation)


@pytest.mark.asyncio
@pytest.mark.parametrize('reason,expected', [
    ('Remote transport timed out', 'Remote transport timed out'),
    ('password=TOPSECRET /private/path stderr', 'failed during transferring'),
])
async def test_failure_reason_is_useful_but_never_raw_stderr(store, reason, expected):
    async def prewarm(**kw):
        await kw['progress']({'phase': 'transferring', 'message': 'Uploading', 'artifact': 'runtime'})
        raise RuntimeError(reason)
    controller = p.PreloadController(store, prewarm=prewarm)
    async with store() as s:
        await controller.start(s, 'vast:1', PreloadRequest(job_id='recipe'))
    await settle(controller)
    async with store() as s:
        current = (await s.get(ExecutionTarget, 'vast:1')).provider_metadata['preload']
        assert expected in current['message']
        assert 'TOPSECRET' not in current['message']
        assert current['artifact'] is None
