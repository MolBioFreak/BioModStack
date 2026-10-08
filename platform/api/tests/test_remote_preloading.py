import asyncio
from datetime import datetime
from dataclasses import replace
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database import Base, ExecutionTarget, Job, get_session
from component_runtime import NativeInvocation, SourceIdentity, GeneratedInput
from routers.execution_targets import router
from services.remote_execution import preloading as p
from services.remote_execution.contracts import PreloadRequest
from services.remote_execution.progress import preload_idle_clause
from services.remote_execution.targets import deactivate_target, ExecutionTargetError


def saved_invocation(command=('nextflow', '--saved', '17')):
    return replace(NativeInvocation.capture(model_id='boltz2', mode='predict',
        command=tuple(command), requested={'science': 17}, effective={'science': 17},
        native_parameters={'science': 17}, entrypoint='test.nf',
        generated_inputs=(GeneratedInput('inputs/generated.json', b'{"science":17}'),)),
        source_identity=SourceIdentity('a' * 40, 'b' * 40))


def test_compile_recipe_collects_without_materializing(tmp_path, monkeypatch):
    from services import nextflow
    invocation = saved_invocation()
    job = SimpleNamespace(model_id='boltz2', mode='predict', params={'science': 17},
        child_output_dir=None, output_dir=str(tmp_path / 'uncreated'))
    collected = []
    def build(actual_job, params, output, *, materialize_inputs, native_invocations):
        assert actual_job is job
        assert params == job.params and params is not job.params
        assert output == job.output_dir
        assert materialize_inputs is False
        assert native_invocations is collected
        native_invocations.append(invocation)
        return list(invocation.command)
    monkeypatch.setattr(nextflow, 'build_job_nextflow_command', build)
    assert p.compile_recipe(job, native_invocations=collected) == list(invocation.command)
    assert collected == [invocation]
    assert not list(tmp_path.iterdir())


def test_compile_recipe_uses_real_compiler_without_biological_files(tmp_path, monkeypatch):
    from services import nextflow
    import json
    identity = SourceIdentity('a' * 40, 'b' * 40)
    monkeypatch.setattr(SourceIdentity, 'from_checkout', classmethod(lambda cls, root: identity))
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    monkeypatch.setattr('services.gpu_config.read_scheduler_config', lambda: {})
    monkeypatch.setattr('services.msa_server.read_server_settings', lambda: {})
    params = {'complex_components': [{'type': 'protein', 'id': 'A', 'sequence': 'ACDEFG'}],
              'protenix_use_msa': False}
    job = SimpleNamespace(id='recipe', model_id='protenix', mode='complex', params=params,
        child_output_dir=None, output_dir=str(tmp_path / 'not-materialized'), provenance={},
        execution_source_revision=identity.revision, execution_source_tree=identity.tree)
    collected = []
    command = p.compile_recipe(job, native_invocations=collected)
    assert len(collected) == 1
    invocation = collected[0]
    assert tuple(command) == invocation.command
    assert invocation.source_identity == identity
    assert json.loads(invocation.requested_json) == params
    assert invocation.native_parameters['protenix_use_msa'] is False
    assert [item.relative_path for item in invocation.generated_inputs] == ['complex_definition.json']
    assert invocation.generated_inputs[0].payload == json.dumps(
        {'components': params['complex_components']}, indent=2).encode('utf-8')
    assert not list(tmp_path.iterdir())


def test_compile_recipe_preserves_legacy_msa_rejection(tmp_path, monkeypatch):
    from services import nextflow
    job = SimpleNamespace(model_id='msa_batch', params={}, child_output_dir=None,
        output_dir=str(tmp_path / 'uncreated'))
    collected = []
    def forbidden(*args, **kwargs):
        pytest.fail('Legacy MSA must not use the Nextflow invocation compiler')
    monkeypatch.setattr(nextflow, 'build_job_nextflow_command', forbidden)
    # The unchanged legacy producer rejects local search before any input writes.
    with pytest.raises(Exception, match='(?i)local.*msa|msa.*local'):
        p.compile_recipe(job, native_invocations=collected)
    assert collected == []
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('identity', [None, SourceIdentity('c' * 40, 'b' * 40),
                                      SourceIdentity('a' * 40, 'c' * 40)])
def test_prewarm_plan_rejects_invocation_source_before_assets(tmp_path, monkeypatch, identity):
    from services.remote_execution import cache
    monkeypatch.setattr(cache, 'get_code_root', lambda: tmp_path)
    monkeypatch.setattr(cache, 'current_source_identity', lambda repo: ('a' * 40, 'b' * 40))
    def forbidden(*args, **kwargs):
        pytest.fail('Source mismatch must fail before dependency or asset work')
    monkeypatch.setattr(cache, 'compile_remote_dependencies', forbidden)
    monkeypatch.setattr(cache.subprocess, 'run', forbidden)
    invocation = replace(saved_invocation(), source_identity=identity)
    with pytest.raises(ValueError, match='source identity'):
        cache._prewarm_plan(SimpleNamespace(model_id='boltz2', mode='predict'),
            list(invocation.command), 'a' * 40, 'b' * 40, tmp_path,
            native_invocation=invocation)
    assert not list(tmp_path.iterdir())


def test_prewarm_plan_forwards_actual_invocation_before_assets(tmp_path, monkeypatch):
    from services.remote_execution import cache
    invocation = saved_invocation()
    monkeypatch.setattr(cache, 'get_code_root', lambda: tmp_path)
    monkeypatch.setattr(cache, 'current_source_identity', lambda repo: ('a' * 40, 'b' * 40))
    class DependencyBoundaryReached(Exception):
        pass
    def dependencies(model_id, mode, command, *, native_invocation):
        assert (model_id, mode) == ('boltz2', 'predict')
        assert native_invocation is invocation
        assert tuple(command) == invocation.command
        raise DependencyBoundaryReached
    monkeypatch.setattr(cache, 'compile_remote_dependencies', dependencies)
    with pytest.raises(DependencyBoundaryReached):
        cache._prewarm_plan(SimpleNamespace(model_id='boltz2', mode='predict'),
            list(invocation.command), 'a' * 40, 'b' * 40, tmp_path,
            native_invocation=invocation)
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_prewarm_cache_forwards_invocation_without_inputs(tmp_path, monkeypatch):
    from services.remote_execution import cache
    invocation = saved_invocation()
    entries = []
    def plan(job, command, revision, tree, directory, *, native_invocation):
        assert native_invocation is invocation
        assert tuple(command) == invocation.command
        assert (revision, tree) == ('a' * 40, 'b' * 40)
        assert not list(directory.iterdir())
        return entries
    async def artifacts(**kwargs):
        assert kwargs['artifacts'] is entries
        assert 'materialize' not in kwargs
        return []
    def forbidden(*args, **kwargs):
        pytest.fail('Prewarm must not materialize generated inputs')
    monkeypatch.setattr(cache, '_prewarm_plan', plan)
    monkeypatch.setattr(cache, '_cache_artifacts', artifacts)
    monkeypatch.setattr(NativeInvocation, 'materialize_inputs', forbidden)
    result = await cache.prewarm_cache(connection=object(), job=object(),
        command=list(invocation.command), source_revision='a' * 40, source_tree='b' * 40,
        operation_id='test', progress=cache._noop, check_fence=cache._noop,
        native_invocation=invocation)
    assert result['artifacts'] == []
    assert not list(tmp_path.iterdir())


@pytest_asyncio.fixture
async def store(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.sqlite'}")
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(p, 'current_source_identity', lambda: ('a'*40, 'b'*40))
    def compile_saved(job, *, native_invocations=None):
        invocation = saved_invocation(['nextflow', '--saved', str(job.params['science'])])
        if native_invocations is not None:
            native_invocations.append(invocation)
        return list(invocation.command)
    monkeypatch.setattr(p, 'compile_recipe', compile_saved)
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
    assert calls[0]['native_invocation'].command == tuple(calls[0]['command'])
    assert calls[0]['native_invocation'].native_parameters == {'science': 17}


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
        assert (await s.get(ExecutionTarget,'vast:1')).provider_metadata['preload']['phase'] == 'recovery_blocked'


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
        assert row.provider_metadata['preload']['phase'] == 'recovery_blocked'
        assert 'restart' in row.provider_metadata['preload']['message']
        with pytest.raises(ExecutionTargetError):
            await restarted.start(s,'vast:1',PreloadRequest(job_id='recipe'))
    async def confirmed_quiescence(connection, operation_id):
        assert operation_id == old
        return True
    restarted.quiesce = confirmed_quiescence
    async with store() as s:
        response = await restarted.cancel(s, 'vast:1', old)
        assert response.preload.phase == 'cancelled'
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
        assert current['phase'] == 'recovery_blocked'
        assert 'quiescence unconfirmed' in current['message']
        assert 'TOPSECRET' not in current['message']
        assert current['artifact'] is None


def test_workflow_selection_preserves_legacy_and_rejects_acquisition():
    from pydantic import ValidationError
    from schemas import JobCreate
    from services.remote_execution.contracts import ProvisionSelection, WorkflowProvisionSelection
    from services.remote_execution.cache import validate_workflow_provision_authority
    assert ProvisionSelection(kind='image', model_id='protenix').model_dump() == {
        'kind': 'image', 'model_id': 'protenix'}
    request = JobCreate(name='Unsaved', model_id='protenix', mode='predict',
        params={'input_path': '/managed/input.pdb', 'reference': {'sha256': 'a'*64, 'source_path': '/managed/input.pdb'}})
    selection = WorkflowProvisionSelection(kind='workflow', workflow_request=request)
    validate_workflow_provision_authority(selection.workflow_request.params)
    for params in ({'bcp_container_path': '/etc/passwd'}, {'runtime_assets': []},
                   {'stages': [{'image_sha256': 'a'*64}]}, {'command': ['sh']}):
        with pytest.raises(ValueError, match='server-owned'):
            validate_workflow_provision_authority(params)
    with pytest.raises(ValidationError):
        WorkflowProvisionSelection(kind='workflow', workflow_request=request, acquisition_url='https://invalid')


@pytest.mark.parametrize('extra', ['command', 'runtime_assets', 'acquisition_url'])
def test_native_workflow_union_rejects_acquisition_and_closed_request_extras(extra):
    from pydantic import ValidationError
    from services.remote_execution.contracts import WorkflowProvisionSelection
    body = {'name': 'Native', 'backend': 'external_import', 'ordered_seeds': [0],
        'samples_per_seed': 1, 'feature_policy': {}, 'runtime_policy': {}, 'analysis_policy': {}}
    wire = {'kind': 'workflow', 'workflow_request': {'workflow_type': 'conformational_mapping', 'request': body}}
    selection = WorkflowProvisionSelection.model_validate(wire)
    assert WorkflowProvisionSelection.model_validate_json(selection.model_dump_json()) == selection
    with pytest.raises(ValidationError):
        WorkflowProvisionSelection.model_validate({**wire, extra: 'untrusted'})
    with pytest.raises(ValidationError):
        WorkflowProvisionSelection.model_validate({**wire, 'workflow_request': {
            **wire['workflow_request'], extra: 'untrusted'}})
    with pytest.raises(ValidationError):
        WorkflowProvisionSelection.model_validate({**wire, 'workflow_request': {
            **wire['workflow_request'], 'request': {**body, extra: 'untrusted'}}})


def test_workflow_preview_forwards_shared_invocation_and_scrubs_worker_manifest(tmp_path, monkeypatch):
    import json
    from services import nextflow
    from services.remote_execution import cache, managed_inventory as mi
    from services.remote_execution.contracts import WorkflowProvisionSelection
    selection = WorkflowProvisionSelection(kind='workflow', workflow_request=dict(
        name='Unsaved', model_id='protenix', mode='predict', params={'sequence': 'ACDE'}))
    plan = nextflow.build_selected_execution_plan(model_id='protenix', mode='predict',
        entrypoint='workflows/structure_prediction.nf', requested={'science': 17},
        effective={'science': 17}, native_parameters={'science': 17},
        source_identity=SourceIdentity('a'*40, 'b'*40))
    invocation = SimpleNamespace(model_id='protenix', mode='predict',
        source_identity=plan.source_identity, native_parameters={'science': 17},
        effective_json=plan.effective_json, execution_plan=plan)
    compiled = []
    def compile_request(request):
        compiled.append(request)
        return invocation
    asset = tmp_path / 'model.pt'
    asset.write_bytes(b'controlled dependency fixture')
    def runtime_assets(model, mode, params, *, include_support, native_invocation):
        assert native_invocation is invocation
        assert (model, mode, params, include_support) == ('protenix', 'predict', {'science': 17}, False)
        return [(asset, 'weights/model.pt'), (asset, 'weights/model.pt')]
    monkeypatch.setattr(nextflow, 'compile_workflow_provision_request', compile_request)
    monkeypatch.setattr(cache, '_runtime_assets', runtime_assets)
    monkeypatch.setattr(cache, 'current_source_identity', lambda: ('a'*40, 'b'*40))
    target = SimpleNamespace(id='target', host='worker', port=22, username='root',
        remote_root='/worker', host_key_sha256='e'*64)
    preview, entries = cache.independent_preview(selection, target)
    assert compiled == [selection.workflow_request]
    assert len(entries) == 1
    assert preview.effective_params == {'science': 17}
    assert preview.plan_sha256 == plan.plan_sha256
    assert preview.asset_states[0].state == 'unknown'
    assert preview.transfer_bytes == asset.stat().st_size
    assert preview.storage_bytes == 2 * asset.stat().st_size
    manifest = mi.manifest_for(selection, entries, ('a'*40, 'b'*40))
    assert manifest['selection']['kind'] == 'workflow'
    assert len(manifest['selection']['model_id']) == 64
    wire = json.dumps(manifest)
    assert 'ACDE' not in wire and 'workflow_request' not in wire and 'sequence' not in wire
    assert [path.name for path in tmp_path.iterdir()] == ['model.pt']


@pytest.mark.asyncio
async def test_native_controller_authorizes_before_preview_and_reuses_plan_on_run_retry(store, monkeypatch):
    from starlette.requests import Request
    from services import nextflow
    from services.remote_execution import managed_inventory as mi
    from services.remote_execution.contracts import WorkflowProvisionSelection, WorkflowProvisionRequest, ProvisionPreview
    selection = WorkflowProvisionSelection(kind='workflow', workflow_request={
        'workflow_type': 'conformational_mapping', 'request': {
            'name': 'Unsaved native', 'backend': 'external_import', 'ordered_seeds': [0],
            'samples_per_seed': 1, 'feature_policy': {}, 'runtime_policy': {}, 'analysis_policy': {}}})
    request = Request({'type': 'http', 'headers': []})
    plans, previews = [], []
    async def compile_native(native, actual_request, session):
        assert actual_request is request and session is not None
        assert native is selection.workflow_request or native == selection.workflow_request
        plan = nextflow.build_selected_execution_plan(model_id='protenix', mode='predict',
            entrypoint='workflows/structure_prediction.nf', requested={'science': 17},
            effective={'science': 17}, native_parameters={'science': 17},
            source_identity=SourceIdentity('a'*40, 'b'*40))
        plans.append(plan)
        return plan
    monkeypatch.setattr(nextflow, 'compile_native_workflow_provision_request', compile_native)
    controller = p.PreloadController(store, quiesce=lambda *args: asyncio.sleep(0, result=True))
    async def preview(selected, target, *, compiled_plan=None):
        assert compiled_plan is plans[-1]
        previews.append(compiled_plan)
        return ProvisionPreview(selection=selected, preview_sha256='d'*64, artifacts=[], total_bytes=0), []
    monkeypatch.setattr(controller, '_preview', preview)
    async def unavailable_transport(*args, **kwargs):
        # Exercise the real _run rebind/approval path, but never perform remote IO.
        raise RuntimeError('isolated transport unavailable')
    monkeypatch.setattr(mi, 'helper_call', unavailable_transport)
    async with store() as session:
        approved = await controller.preview(session, 'vast:1', selection, http_request=request)
        mutation = WorkflowProvisionRequest(**selection.model_dump(), preview_sha256=approved.preview_sha256)
        response = await controller.start(session, 'vast:1', mutation, http_request=request)
    await asyncio.gather(*tuple(controller.tasks.values()))
    assert len(plans) == 2 and previews[-2] is previews[-1] is plans[-1]
    async with store() as session:
        target = await session.get(ExecutionTarget, 'vast:1')
        assert target.provider_metadata['preload']['phase'] == 'failed'
        assert 'compiled_plan' not in target.provider_metadata['preload']
        assert target.provider_metadata['preload']['job_id'] is None
        response = await controller.start(session, 'vast:1', mutation,
            retry_operation_id=response.preload.operation_id, http_request=request)
    await asyncio.gather(*tuple(controller.tasks.values()))
    assert len(plans) == 3 and previews[-2] is previews[-1] is plans[-1]
    async with store() as session:
        stale = mutation.model_copy(update={'preview_sha256': 'e'*64})
        with pytest.raises(ExecutionTargetError, match='preview changed'):
            await controller.start(session, 'vast:1', stale, http_request=request)
    assert len(plans) == 4 and not controller.tasks
    await controller.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('quiet', [False, True])
async def test_cancel_retains_artifact_evidence_and_blocks_until_quiescent(store, quiet):
    reached = asyncio.Event()
    receipt = dict(name='weights/verified.pt', sha256='d'*64, size_bytes=4)
    async def prewarm(**kw):
        await kw['progress'](dict(phase='transferring', artifact='weights/pending.pt', message='Transferring',
            artifact_progress=[dict(receipt, state='verified'),
                dict(name='weights/pending.pt', sha256='e'*64, size_bytes=7, state='transferring')]))
        reached.set()
        await asyncio.Event().wait()
    observed = []
    async def quiesce(connection, operation_id):
        observed.append(operation_id)
        return quiet
    controller = p.PreloadController(store, prewarm=prewarm, quiesce=quiesce)
    async with store() as session:
        response = await controller.start(session, 'vast:1', PreloadRequest(job_id='recipe'))
    await reached.wait()
    operation = response.preload.operation_id
    async with store() as session:
        response = await controller.cancel(session, 'vast:1', operation)
        assert response.preload.phase == ('cancelled' if quiet else 'recovery_blocked')
        assert response.preload.recovery_required is (not quiet)
        assert response.preload.cancel_requested
        assert response.preload.sequence >= 3
        assert [row.state for row in response.preload.artifact_progress] == ['verified', 'interrupted']
        if not quiet:
            with pytest.raises(ExecutionTargetError):
                await controller.start(session, 'vast:1', PreloadRequest(job_id='recipe'))
    assert observed and all(value == operation for value in observed)
    assert not controller.tasks
    await controller.close()
