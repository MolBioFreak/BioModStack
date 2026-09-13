"""Bounded cache uploads through the real helper and declared local transport."""
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import uuid

import pytest

from services.remote_execution import cache
from services.remote_execution.bundle import CacheTransferArtifact
from test_remote_cache_integration import local_transport
from test_artifact_cache import module, identity


def entries(tmp_path, count=5):
    result = []
    for index in range(count):
        source = tmp_path / str(index)
        data = str(index).encode() * 3
        source.write_bytes(data)
        result.append(CacheTransferArtifact(source, '/worker/runtime/' + str(index),
            hashlib.sha256(data).hexdigest(), len(data), 0o644, 'runtime'))
    return result


async def stage(tmp_path, artifacts, fence=cache._noop):
    return await cache._cache_artifacts(connection=SimpleNamespace(remote_root=str(tmp_path / 'worker')),
        artifacts=artifacts, operation_id=str(uuid.uuid4()), progress=cache._noop, check_fence=fence)


@pytest.mark.asyncio
@pytest.mark.parametrize('bound', ['count', 'bytes'])
async def test_bounded_deduplicated_real_uploads(tmp_path, monkeypatch, local_transport, bound):
    artifacts = entries(tmp_path)
    monkeypatch.setattr(cache, 'BATCH_COUNT' if bound == 'count' else 'BATCH_BYTES', 2 if bound == 'count' else 6)
    receipts = await stage(tmp_path, artifacts + artifacts)
    calls, uploads = local_transport
    assert len(receipts) == 10
    assert len(uploads) == 3
    assert [len(r['artifacts']) for r in calls if r['action'] == 'probe'] == ([2, 2, 1] if bound == 'count' else [5])
    assert [len(r['artifacts']) for r in calls if r['action'] == 'ingest_many'] == [2, 2, 1]
    assert not list((tmp_path / 'worker/cache/artifacts/v1/incoming').glob('*/*'))
    await stage(tmp_path, artifacts)
    assert len(uploads) == 3


@pytest.mark.asyncio
async def test_production_count_bound(tmp_path, local_transport):
    artifacts = entries(tmp_path, 2050)
    updates, fences = [], []
    async def progress(event):
        updates.append(event)
    async def fence():
        fences.append(True)
    receipts = await cache._cache_artifacts(
        connection=SimpleNamespace(remote_root=str(tmp_path / 'worker')),
        artifacts=artifacts + artifacts[:3], operation_id=str(uuid.uuid4()),
        progress=progress, check_fence=fence, track_artifacts=True)
    assert len(receipts) == 2053
    assert len(updates) < 12 and len(fences) < 50
    assert len(updates[-1]['artifact_progress']) == 2053
    assert all(row['state'] == 'verified' for row in updates[-1]['artifact_progress'])
    calls, uploads = local_transport
    assert len(uploads) == 2
    assert [len(r['artifacts']) for r in calls if r['action'] == 'probe'] == [2048, 2]
    assert [len(r['artifacts']) for r in calls if r['action'] == 'ingest_many'] == [2048, 2]
    assert max(len(json.dumps(r).encode()) for r in calls) < 8 * 1024 * 1024


@pytest.mark.asyncio
@pytest.mark.parametrize('route', ['copy', 'oversized'])
async def test_copy_fallback_and_oversized_direct(tmp_path, monkeypatch, local_transport, route):
    import errno
    artifacts = entries(tmp_path, 1)
    if route == 'copy':
        def cross_device(*args, **kwargs):
            raise OSError(errno.EXDEV, 'declared cross-device staging fixture')
        monkeypatch.setattr(cache.os, 'link', cross_device)
    else:
        monkeypatch.setattr(cache, 'BATCH_BYTES', 1)
    await stage(tmp_path, artifacts)
    calls, uploads = local_transport
    assert len(uploads) == 1
    assert (uploads == [str(artifacts[0].source)]) == (route == 'oversized')
    assert any(r['action'] == ('ingest' if route == 'oversized' else 'ingest_many') for r in calls)


@pytest.mark.asyncio
async def test_conflicting_size_before_any_remote(tmp_path, local_transport):
    from dataclasses import replace
    item = entries(tmp_path, 1)[0]
    with pytest.raises(ValueError, match='Conflicting'):
        await stage(tmp_path, [item, replace(item, size_bytes=999)])
    assert local_transport == ([], [])
    assert not (tmp_path / 'worker').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['corrupt', 'ssh', 'cancel', 'fence'])
async def test_uncertain_batches_retained_and_retry(tmp_path, monkeypatch, local_transport, failure):
    artifacts = entries(tmp_path, 2)
    original = cache.rsync_to_remote
    transferred = False
    async def interrupted(connection, source, destination, **kwargs):
        nonlocal transferred
        await original(connection, source, destination, **kwargs)
        transferred = True
        if failure == 'corrupt':
            (Path(destination) / artifacts[-1].sha256).write_bytes(b'bad')
        elif failure == 'ssh':
            raise RuntimeError('ssh lost')
        elif failure == 'cancel':
            raise asyncio.CancelledError()
    async def fence():
        if transferred and failure == 'fence':
            raise RuntimeError('fenced')
    monkeypatch.setattr(cache, 'rsync_to_remote', interrupted)
    with pytest.raises((RuntimeError, subprocess.CalledProcessError, asyncio.CancelledError)):
        await stage(tmp_path, artifacts, fence)
    calls, uploads = local_transport
    assert not any(r['action'] == 'remove_incoming' for r in calls)
    retained = list((tmp_path / 'worker/cache/artifacts/v1/incoming').glob('*/*'))
    assert len(retained) == 1 and list(retained[0].iterdir())
    monkeypatch.setattr(cache, 'rsync_to_remote', original)
    await stage(tmp_path, artifacts)
    assert retained[0].exists()
    assert len(uploads) == 2
    if failure == 'corrupt':
        # First object published before failure; retry transfers only remaining object.
        assert len([r for r in calls if r['action'] == 'ingest_many'][-1]['artifacts']) == 1


@pytest.mark.asyncio
async def test_images_direct_and_authenticated_peer(tmp_path, local_transport):
    source = tmp_path / 'image.sif'
    # Transport identity fixture, never executed or claimed to be a valid SIF.
    data = b'image-not-executed'
    source.write_bytes(data)
    item = CacheTransferArtifact(source, '/worker/image', hashlib.sha256(data).hexdigest(), len(data), 0o644, 'image')
    await stage(tmp_path, [item, item])
    calls, uploads = local_transport
    assert uploads == [str(source)]
    assert not any(r['action'] == 'ingest_many' for r in calls)
    assert (tmp_path / 'worker/cache/runtime-images/objects/sha256' / item.sha256 / 'runtime.sif').read_bytes() == data
    peers = list((tmp_path / 'worker/runner').glob('cache-*/runtime_image_views.py'))
    assert len(peers) == 1
    assert peers[0].read_bytes() == (Path(cache.__file__).parents[4] / 'scripts/lib/runtime_image_views.py').read_bytes()


@pytest.mark.parametrize('component', ['operation', 'batch'])
def test_incoming_nofollow(tmp_path, component):
    authority = module.Cache(tmp_path / 'cache')
    operation, batch = str(uuid.uuid4()), uuid.uuid4().hex
    outside = tmp_path / 'outside'
    outside.mkdir()
    path = tmp_path / 'cache/incoming' / operation
    if component == 'batch':
        path.mkdir()
        path = path / batch
    path.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        authority.incoming_batch(operation, batch, create=True)
    assert list(outside.iterdir()) == []


def test_helper_rejects_batch_bounds_and_images(tmp_path):
    authority = module.Cache(tmp_path / 'cache')
    operation, batch = str(uuid.uuid4()), uuid.uuid4().hex
    authority.incoming_batch(operation, batch, create=True)
    for items in ([identity(b'a')] * 2049,
                  [dict(identity(b'a'), size_bytes=256 * 1024 * 1024 + 1)],
                  [dict(identity(b'a'), kind='runtime_image')]):
        with pytest.raises(ValueError, match='invalid_ingest_batch'):
            authority.ingest_many(items, operation, batch)
