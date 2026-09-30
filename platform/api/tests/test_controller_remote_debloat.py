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


def test_transport_snapshot_does_not_project_inventory(monkeypatch):
    from services.remote_execution import managed_inventory
    def forbidden(*args):
        pytest.fail('Transport snapshot projected inventory')
    monkeypatch.setattr(managed_inventory, 'project_inventory', forbidden)
    from types import SimpleNamespace
    target = SimpleNamespace(id='vast:1', host='host', port=22, username='root', remote_root='/remote',
        host_key_sha256='a'*64, capabilities={'critical_runtime_binding':{'environment':{'BMS_CONTAINER_BACKEND':'udocker'}},
        'unrelated': ['large']*1000})
    snapshot = p.TargetSnapshot.capture(target, inventory=False)
    assert snapshot.managed_inventory is None
    assert snapshot.capabilities == {'critical_runtime_binding': target.capabilities['critical_runtime_binding']}


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
