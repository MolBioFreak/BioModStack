"""Controller-lane counter and lifetime proofs; no native execution."""
import asyncio
import threading
from pathlib import Path
import subprocess

import pytest
from database import ExecutionTarget
from services.remote_execution import bundle, preloading as p
from services.remote_execution.contracts import PreloadRequest
from test_remote_preloading import store, settle


@pytest.mark.asyncio
async def test_progress_does_not_rediscover_git_and_fence_is_projected(store, monkeypatch):
    calls = []
    monkeypatch.setattr(p, 'current_source_identity', lambda: (calls.append('git') or ('a'*40, 'b'*40)))
    original = p.get_target
    loads = []
    async def get(*args, **kwargs):
        loads.append('full')
        return await original(*args, **kwargs)
    monkeypatch.setattr(p, 'get_target', get)
    async def prewarm(**kwargs):
        before = len(calls)
        before_loads = len(loads)
        await asyncio.gather(*(kwargs['progress']({'phase':'transferring', 'message':f'Batch {n}'}) for n in range(10)))
        assert len(calls) == before
        assert len(loads) == before_loads
        await kwargs['check_fence']()
        assert len(calls) == before + 1
        assert len(loads) == before_loads
        return dict(source_revision='a'*40, source_tree='b'*40, artifacts=[])
    controller = p.PreloadController(store, prewarm=prewarm)
    async with store() as session:
        await controller.start(session, 'vast:1', PreloadRequest(job_id='recipe'))
    await settle(controller)
    async with store() as session:
        raw = (await session.get(ExecutionTarget, 'vast:1')).provider_metadata['preload']
        assert raw['phase'] == 'source_download_ready'
        assert raw['sequence'] == 11
    assert len(loads) == 1
    assert len(calls) == 4  # admission, initial fence, explicit acquisition, final fence


@pytest.mark.asyncio
async def test_preview_repeated_cancel_keeps_slot_until_thread_finishes(monkeypatch):
    from services.remote_execution import cache
    entered, released, exited = threading.Event(), threading.Event(), threading.Event()
    def preview(*args, **kwargs):
        entered.set()
        try:
            assert released.wait(5)
            return None, []
        finally:
            exited.set()
    monkeypatch.setattr(cache, 'independent_preview', preview)
    controller = p.PreloadController(None)
    task = asyncio.create_task(controller._preview(object(), object()))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        assert not exited.is_set()
        assert controller.preview_slots._value == 1
    finally:
        released.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert exited.is_set()
    assert controller.preview_slots._value == 2


@pytest.mark.asyncio
async def test_admitted_transport_never_loads_full_target_or_projects_inventory(store, monkeypatch):
    from services.remote_execution import managed_inventory, cache
    from services.remote_execution.contracts import PreloadProgress, ProvisionSelection
    from services.remote_execution.transport import RemoteConnection
    from datetime import datetime
    progress = PreloadProgress(operation_id='admitted', selection=ProvisionSelection(kind='image',model_id='boltz2'),
        source_revision='a'*40,source_tree='b'*40,request_sha256='d'*64,phase='checking',message='Planning',
        started_at=datetime.utcnow(),updated_at=datetime.utcnow())
    async with store() as session:
        target = await session.get(ExecutionTarget,'vast:1')
        target.provider_metadata = {**target.provider_metadata,'preload':progress.model_dump(mode='json')}
        target.capabilities = {'critical_runtime_binding':{'environment':{'BMS_CONTAINER_BACKEND':'udocker'}}}
        connection, expected = RemoteConnection.from_target(target), p.endpoint(target)
        await session.commit()
    def forbidden(*args, **kwargs):
        pytest.fail('Admitted transport loaded full target or projected inventory')
    monkeypatch.setattr(p,'get_target',forbidden)
    monkeypatch.setattr(managed_inventory,'project_inventory',forbidden)
    async def provision(**kwargs):
        assert kwargs['backend'] == 'udocker'
        await kwargs['check_fence']()
        await kwargs['progress']({'phase':'transferring','message':'Progress'})
        return []
    monkeypatch.setattr(cache,'provision_cache',provision)
    controller = p.PreloadController(store)
    await controller._run('vast:1',progress,None,None,connection,expected,admitted_plan=('d'*64,()))
    async with store() as session:
        assert (await session.get(ExecutionTarget,'vast:1')).provider_metadata['preload']['phase'] == 'source_download_ready'


def test_warm_archive_one_read_same_size_corruption_rejected(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    (repo / 'fixture.txt').write_text('inert source')
    subprocess.run(['git', '-C', str(repo), 'add', 'fixture.txt'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '-qm', 'fixture'], check=True)
    revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    data = tmp_path / 'data'
    digest = bundle._staged_source_archive(repo, data, revision, tmp_path / 'cold', extract=False)
    archive = data / 'remote-execution/source-archives' / (revision + '.tar.gz')
    original_open = Path.open
    reads = []
    class Reader:
        def __init__(self, handle): self.handle = handle
        def __enter__(self): return self
        def __exit__(self, *args): self.handle.close()
        def read(self, size=-1):
            value = self.handle.read(size)
            reads.append(len(value))
            return value
    def opened(path, mode='r', *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        return Reader(handle) if path == archive and mode == 'rb' else handle
    monkeypatch.setattr(Path, 'open', opened)
    assert bundle._staged_source_archive(repo, data, revision, tmp_path / 'warm', extract=False) == digest
    assert sum(reads) == archive.stat().st_size
    content = archive.read_bytes()
    archive.write_bytes(bytes([content[0] ^ 1]) + content[1:])
    with pytest.raises(bundle.RemoteBundleError, match='changed during staging'):
        bundle._staged_source_archive(repo, data, revision, tmp_path / 'corrupt', extract=False)
    assert not (tmp_path / 'corrupt' / 'fixture.txt').exists()
    assert bundle._staged_source_archive(repo, data, revision, tmp_path / 'retry', extract=False) == digest


@pytest.mark.parametrize('model,mode,entrypoint', [
    ('bindcraft2','campaign','workflows/bindcraft2.nf'),
    ('boltzgen','protein_binder','workflows/boltzgen_generation.nf'),
    ('antibody_denovo','antibody_denovo_pipeline','workflows/antibody_denovo.nf'),
    ('ppiflow','protein_binder','workflows/ppiflow_generation.nf'),
])
def test_c01_saved_download_launch_only_coupling_reproduced(tmp_path, monkeypatch, model, mode, entrypoint):
    from dataclasses import replace
    from types import SimpleNamespace
    from model_registry import selected_execution_metadata
    from component_runtime import NativeInvocation, SelectedExecutionPlan, SourceIdentity, UnresolvedField
    from services.remote_execution import cache
    params = {'protenix_use_msa':False}
    if model == 'boltzgen':
        params['boltzgen_protocol'] = 'protein-anything'
    metadata = selected_execution_metadata(model, mode, params, entrypoint)
    assert metadata.dependency_closure_complete
    metadata = replace(metadata, blockers=metadata.blockers + (
        UnresolvedField(model, 'result_retrieval', 'fixture retained-result owner',
                        'Inert non-download launch blocker', ('launch',)),))
    assert metadata.dependency_closure_complete
    assert not metadata.blockers_for('provision')
    invocation = NativeInvocation.capture(model_id=model, mode=mode, command=['nextflow',entrypoint],
        requested=params, effective=params, native_parameters=params, entrypoint=entrypoint)
    identity = SourceIdentity('a'*40,'b'*40)
    plan = SelectedExecutionPlan(identity, model, model, mode, entrypoint,
        invocation.requested_json, invocation.effective_json, invocation.native_parameters_json, metadata)
    invocation = replace(invocation, source_identity=identity, execution_plan=plan)
    monkeypatch.setattr(cache,'get_code_root',lambda:tmp_path)
    monkeypatch.setattr(cache,'current_source_identity',lambda root:('a'*40,'b'*40))
    def forbidden(*args, **kwargs):
        pytest.fail('Coupling probe must not stage source or inputs')
    monkeypatch.setattr(cache,'_staged_source_archive',forbidden)
    with pytest.raises(bundle.RemoteBundleError, match='complete selected native execution plan'):
        cache._prewarm_plan(SimpleNamespace(model_id=model,mode=mode), list(invocation.command),
            'a'*40,'b'*40,tmp_path,native_invocation=invocation)
    # Actual launch keeps exactly this existing refusal.
    with pytest.raises(bundle.RemoteBundleError, match='complete selected native execution plan'):
        bundle.compile_remote_dependencies(model,mode,list(invocation.command),native_invocation=invocation)
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['active', 'state', 'lease', 'operation', 'cancel', 'endpoint', 'inventory', 'recipe'])
async def test_compact_progress_fence_preserves_each_authority_guard(store, change):
    async def prewarm(**kwargs):
        async with store() as session:
            row = await session.get(ExecutionTarget, 'vast:1')
            metadata = dict(row.provider_metadata)
            if change == 'active': row.active = False
            if change == 'state': row.state = 'unavailable'
            if change == 'lease': row.leased_job_id = 'recipe'
            if change == 'endpoint': row.port = 23
            if change == 'operation': metadata['preload'] = {**metadata['preload'], 'operation_id':'successor'}
            if change == 'cancel': metadata['preload'] = {**metadata['preload'], 'cancel_requested':True}
            if change == 'inventory': metadata['inventory'] = {**metadata['inventory'], 'running':False}
            if change == 'recipe':
                from database import Job
                (await session.get(Job, 'recipe')).params = {'science':18}
            row.provider_metadata = metadata
            await session.commit()
        await kwargs['progress']({'phase':'transferring','message':'Must not publish'})
        pytest.fail('Changed authority admitted progress')
    async def quiesce(*args): return True
    controller = p.PreloadController(store, prewarm=prewarm, quiesce=quiesce)
    async with store() as session:
        await controller.start(session,'vast:1',PreloadRequest(job_id='recipe'))
    await asyncio.gather(*list(controller.tasks.values()),return_exceptions=True)
    async with store() as session:
        raw = (await session.get(ExecutionTarget,'vast:1')).provider_metadata['preload']
        assert raw['message'] != 'Must not publish'
        if change == 'operation':
            assert raw['operation_id'] == 'successor'
            assert raw['phase'] == 'checking'
        else:
            assert raw['phase'] == ('cancelled' if change == 'cancel' else 'failed')
