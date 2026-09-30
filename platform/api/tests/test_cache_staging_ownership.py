"""Actual acquisition probes and cancellation-owned local writers; no final audit."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import uuid

import pytest

from services.remote_execution import cache
from test_cache_batches import entries


async def provision(artifacts, fence=cache._noop):
    return await cache.provision_cache(connection=SimpleNamespace(remote_root='/worker'),
        entries=artifacts, operation_id=str(uuid.uuid4()), progress=cache._noop,
        check_fence=fence)


@pytest.fixture
def acquisition_probes(monkeypatch):
    async def helper(*args):
        return '/helper'

    monkeypatch.setattr(cache, '_install_helper', helper)
    monkeypatch.setattr(cache, 'BATCH_COUNT', 1)
    monkeypatch.setattr(cache, 'PROBE_CONCURRENCY', 2)


@pytest.mark.asyncio
async def test_acquisition_probe_pages_overlap_are_bounded_and_keep_fences(tmp_path, monkeypatch, acquisition_probes):
    artifacts = entries(tmp_path, 5)
    artifacts[1] = replace(artifacts[1], role='image')
    started, release = asyncio.Event(), asyncio.Event()
    active = peak = 0
    requests, fences = [], []

    async def fence():
        fences.append(asyncio.current_task())

    async def run(connection, argv, *, input_bytes, **kwargs):
        nonlocal active, peak
        request = json.loads(input_bytes)
        assert request['action'] == 'probe', 'warm hits must not transfer or audit again'
        requests.append(request)
        active += 1
        peak = max(peak, active)
        if active == 2:
            started.set()
        try:
            await release.wait()
            return SimpleNamespace(stdout=json.dumps({'artifacts': [
                dict(row, state='cache_hit') for row in request['artifacts']]}))
        finally:
            active -= 1

    monkeypatch.setattr(cache, 'run_remote', run)
    alias = replace(artifacts[0], link_target='opaque-alias')
    task = asyncio.create_task(provision(artifacts + artifacts[:1] + [alias], fence))
    try:
        await asyncio.wait_for(started.wait(), 5)
        assert len(requests) == peak == 2
        assert not task.done()
    finally:
        release.set()
    receipts = await task
    assert receipts == [dict(name=e.remote_destination.removeprefix('/worker/'),
                             sha256=e.sha256, size_bytes=e.size_bytes)
                        for e in artifacts + artifacts[:1]]
    assert active == 0 and peak == 2
    # Exactly one acquisition lookup per unique object, not a second final sweep.
    assert len(requests) == 5
    assert sum('kind' in r['artifacts'][0] for r in requests) == 1
    assert len(fences) == 12  # Page pairs, acquisition-owner fence, provision-owner fence.
    assert fences[-2:] == [task, task]
    for owner in set(fences[:-2]):
        assert fences.count(owner) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['fence', 'cancel'])
async def test_acquisition_probe_failure_or_cancel_drains_siblings(tmp_path, monkeypatch, acquisition_probes, fault):
    artifacts = entries(tmp_path, 2)
    both_started, release = asyncio.Event(), asyncio.Event()
    owners, drained, returned = set(), set(), set()

    async def fence():
        if fault == 'fence' and asyncio.current_task() in returned:
            raise RuntimeError('fenced acquisition probe')

    async def run(connection, argv, *, input_bytes, **kwargs):
        request = json.loads(input_bytes)
        assert request['action'] == 'probe'
        owner = asyncio.current_task()
        owners.add(owner)
        if len(owners) == 2:
            both_started.set()
        row = dict(request['artifacts'][0], state='cache_hit')
        try:
            await release.wait()
            if row['sha256'] != artifacts[0].sha256 or fault == 'cancel':
                await asyncio.Event().wait()
            returned.add(owner)
            return SimpleNamespace(stdout=json.dumps({'artifacts': [row]}))
        finally:
            drained.add(owner)

    monkeypatch.setattr(cache, 'run_remote', run)
    task = asyncio.create_task(provision(artifacts, fence))
    try:
        await asyncio.wait_for(both_started.wait(), 5)
        if fault == 'cancel':
            task.cancel()
        release.set()
        expected = asyncio.CancelledError if fault == 'cancel' else RuntimeError
        with pytest.raises(expected):
            await asyncio.wait_for(task, 5)
        assert drained == owners and len(owners) == 2
        assert all(owner.done() for owner in owners)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['sha256', 'size_bytes', 'state', 'missing'])
async def test_acquisition_bad_hf_response_drains_siblings(tmp_path, monkeypatch, acquisition_probes, fault):
    artifacts = entries(tmp_path, 2)
    monkeypatch.setattr(cache, 'HF_MIN_BYTES', 1)
    monkeypatch.setattr(cache, 'TRANSFER_CONCURRENCY', 2)
    monkeypatch.setattr(cache.hf_assets, 'configuration', lambda: object())

    async def sources(batch, **kwargs):
        return {(False, e.sha256): {'opaque': True} for e in batch}

    monkeypatch.setattr(cache.hf_assets, 'prepare_sources', sources)
    both_started, release = asyncio.Event(), asyncio.Event()
    owners, drained, actions = set(), set(), []

    async def run(connection, argv, *, input_bytes, **kwargs):
        request = json.loads(input_bytes)
        actions.append(request['action'])
        if request['action'] == 'probe':
            return SimpleNamespace(stdout=json.dumps({'artifacts': [
                dict(row, state='missing') for row in request['artifacts']]}))
        if request['action'] == 'prepare_incoming':
            return SimpleNamespace(stdout='{}')
        assert request['action'] == 'acquire_hf', 'bad acquisition must not ingest or sweep'
        owner = asyncio.current_task()
        owners.add(owner)
        if len(owners) == 2:
            both_started.set()
        row = dict(request['artifact'], state='downloaded')
        try:
            await release.wait()
            if row['sha256'] != artifacts[0].sha256:
                await asyncio.Event().wait()
            if fault == 'sha256':
                row['sha256'] = 'f' * 64
            elif fault == 'size_bytes':
                row['size_bytes'] += 1
            elif fault == 'state':
                row['state'] = 'missing'
            else:
                row = {}
            return SimpleNamespace(stdout=json.dumps(row))
        finally:
            drained.add(owner)

    monkeypatch.setattr(cache, 'run_remote', run)
    task = asyncio.create_task(provision(artifacts))
    try:
        await asyncio.wait_for(both_started.wait(), 5)
        release.set()
        with pytest.raises(ValueError, match='acquisition did not verify'):
            await asyncio.wait_for(task, 5)
        assert drained == owners and len(owners) == 2
        assert all(owner.done() for owner in owners)
        assert actions.count('probe') == actions.count('acquire_hf') == 2
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('advisory', ['kind', 'extra'])
async def test_acquisition_response_advisory_fields_do_not_add_final_audit(tmp_path, monkeypatch, acquisition_probes, advisory):
    artifacts = entries(tmp_path, 1)
    monkeypatch.setattr(cache, 'HF_MIN_BYTES', 1)
    monkeypatch.setattr(cache.hf_assets, 'configuration', lambda: object())

    async def sources(batch, **kwargs):
        return {(False, e.sha256): {'opaque': True} for e in batch}

    monkeypatch.setattr(cache.hf_assets, 'prepare_sources', sources)
    actions = []

    async def run(connection, argv, *, input_bytes, **kwargs):
        request = json.loads(input_bytes)
        actions.append(request['action'])
        if request['action'] == 'probe':
            response = {'artifacts': [dict(row, state='missing') for row in request['artifacts']]}
        elif request['action'] == 'acquire_hf':
            # The acquisition protocol authenticates digest/size/state, not an
            # extra schema gate inherited from the deleted whole-object audit.
            response = dict(request['artifact'], state='downloaded')
            response[advisory] = 'runtime_image' if advisory == 'kind' else {'opaque': True}
        else:
            assert request['action'] in {'prepare_incoming', 'ingest', 'remove_incoming'}
            response = {}
        return SimpleNamespace(stdout=json.dumps(response))

    monkeypatch.setattr(cache, 'run_remote', run)
    receipts = await provision(artifacts)
    assert len(receipts) == 1 and receipts[0]['sha256'] == artifacts[0].sha256
    assert actions == ['probe', 'prepare_incoming', 'acquire_hf', 'ingest', 'remove_incoming']


@pytest.mark.asyncio
@pytest.mark.parametrize('route', ['link', 'copy'])
@pytest.mark.parametrize('writer_fails', [False, True])
async def test_staging_thread_joined_before_cleanup_on_repeated_cancel(tmp_path, monkeypatch, route, writer_fails):
    artifacts = entries(tmp_path, 1)
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release, finished = threading.Event(), threading.Event()
    paths, owners, requests, uploads = [], [], [], []
    thread_ids = []
    original = cache.os.link if route == 'link' else cache.shutil.copyfile

    def blocked(source, destination, **kwargs):
        thread_ids.append(threading.get_ident())
        paths.append(Path(destination).parent)
        loop.call_soon_threadsafe(started.set)
        try:
            assert release.wait(5), 'test did not release writer'
            assert paths[0].is_dir(), 'cleanup raced with owned writer'
            if writer_fails:
                raise ValueError('opaque writer failure during cancellation')
            return original(source, destination, **kwargs)
        finally:
            finished.set()

    if route == 'copy':
        def cross_device(*args, **kwargs):
            raise OSError('cross-device fixture')
        monkeypatch.setattr(cache.os, 'link', cross_device)
        monkeypatch.setattr(cache.shutil, 'copyfile', blocked)
    else:
        monkeypatch.setattr(cache.os, 'link', blocked)

    async def fence():
        owners.append(asyncio.current_task())

    async def run(connection, argv, *, input_bytes, **kwargs):
        request = json.loads(input_bytes)
        requests.append(request['action'])
        return SimpleNamespace(stdout=json.dumps({'artifacts': [dict(row, state='missing')
            for row in request.get('artifacts', [])]}))

    async def upload(*args, **kwargs):
        uploads.append(args)

    monkeypatch.setattr(cache, 'run_remote', run)
    monkeypatch.setattr(cache, 'rsync_to_remote', upload)
    task = asyncio.create_task(cache._cache_artifacts(
        connection=SimpleNamespace(remote_root='/worker'), artifacts=artifacts,
        operation_id=str(uuid.uuid4()), progress=cache._noop, check_fence=fence, helper='/helper'))
    try:
        await asyncio.wait_for(started.wait(), 5)
        writer_owner = owners[-1]
        assert thread_ids == [thread_ids[0]] and thread_ids[0] != threading.get_ident()
        # Cancel the batch itself twice: neither cancellation may release its
        # TemporaryDirectory or cancel the shielded to_thread task.
        writer_owner.cancel()
        await asyncio.sleep(0)
        writer_owner.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not writer_owner.done()
        assert paths[0].is_dir() and not finished.is_set()
        assert not uploads and requests == ['probe', 'prepare_incoming']
    finally:
        release.set()
        if not task.done():
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    assert finished.is_set() and not paths[0].exists()
    assert not uploads and requests == ['probe', 'prepare_incoming']


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['size', 'symlink', 'copy_symlink'])
async def test_thread_staging_retains_source_checks(tmp_path, monkeypatch, fault):
    artifacts = entries(tmp_path, 1)
    source = artifacts[0].source
    if fault == 'size':
        source.write_bytes(b'changed-size')
    elif fault == 'symlink':
        source.unlink()
        source.symlink_to(tmp_path / 'absent')
    else:
        def cross_device(*args, **kwargs):
            raise OSError('cross-device fixture')
        def unsafe_copy(src, dest, **kwargs):
            Path(dest).symlink_to(src)
        monkeypatch.setattr(cache.os, 'link', cross_device)
        monkeypatch.setattr(cache.shutil, 'copyfile', unsafe_copy)
    requests = []

    async def run(connection, argv, *, input_bytes, **kwargs):
        request = json.loads(input_bytes)
        requests.append(request['action'])
        return SimpleNamespace(stdout=json.dumps({'artifacts': [dict(row, state='missing')
            for row in request.get('artifacts', [])]}))

    async def upload(*args, **kwargs):
        pytest.fail('invalid source uploaded')

    monkeypatch.setattr(cache, 'run_remote', run)
    monkeypatch.setattr(cache, 'rsync_to_remote', upload)
    with pytest.raises(ValueError, match='Cache source'):
        await cache._cache_artifacts(connection=SimpleNamespace(remote_root='/worker'),
            artifacts=artifacts, operation_id=str(uuid.uuid4()), progress=cache._noop,
            check_fence=cache._noop, helper='/helper')
    assert requests == ['probe', 'prepare_incoming']
