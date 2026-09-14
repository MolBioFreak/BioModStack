"""HF routing with the real cache publication owner; HTTP is an explicit double.

This does not qualify live cloud throughput or science. The worker downloader's
HTTP protocol has independent tests; this suite exercises its controller caller,
ordinary cache ingest/materialization and failure/renewal/fence boundaries.
"""
import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import uuid

import pytest

from services.remote_execution import cache
from services.remote_execution.bundle import CacheTransferArtifact
from test_remote_cache_integration import local_transport


def artifact(tmp_path, role='runtime', name='weights.bin'):
    data = b'bound-transport-fixture-not-scientific-weights'
    source = tmp_path / name
    source.write_bytes(data)
    return CacheTransferArtifact(source, '/worker/runtime/' + name,
        hashlib.sha256(data).hexdigest(), len(data), 0o644, role), data


async def stage(tmp_path, entries, progress=cache._noop, fence=cache._noop):
    return await cache._cache_artifacts(connection=SimpleNamespace(remote_root=str(tmp_path / 'worker')),
        artifacts=entries, operation_id=str(uuid.uuid4()), progress=progress,
        check_fence=fence, track_artifacts=True)


@pytest.fixture
def cloud(tmp_path, monkeypatch, local_transport):
    calls, _ = local_transport
    original = cache.run_remote
    issued, downloads = [], []
    behavior = {'expire': 0, 'fail': None, 'bytes': {}, 'after': None}
    monkeypatch.setattr(cache, 'HF_MIN_BYTES', 1)
    monkeypatch.setattr(cache.hf_assets, 'configuration', lambda: object())

    async def sources(entries, *, check_fence):
        await check_fence()
        issued.extend(entries)
        if behavior['fail'] == 'auth':
            raise ValueError('Hugging Face authentication failed')
        return {(e.role == 'image', e.sha256): {'url': 'https://us.aws.cdn.hf.co/fixture?Signature=opaque-read-capability',
                                               'expires_at': 2000000000} for e in entries}

    async def run(connection, argv, input_bytes=None, **kwargs):
        request = json.loads(input_bytes) if input_bytes and '-c' not in argv else {}
        if request.get('action') != 'acquire_hf':
            return await original(connection, argv, input_bytes=input_bytes, **kwargs)
        calls.append(request)
        downloads.append(request)
        if behavior['expire']:
            behavior['expire'] -= 1
            return SimpleNamespace(stdout=json.dumps({'state': 'source_expired'}))
        if behavior['fail'] == 'transport':
            raise RuntimeError('declared download transport failure')
        item = request['artifact']
        dest = Path(connection.remote_root) / 'cache/artifacts/v1/incoming' / request['operation_id'] / request['batch_id'] / item['sha256']
        dest.write_bytes(behavior['bytes'][item['sha256']])
        if behavior['after']:
            behavior['after']()
        return SimpleNamespace(stdout=json.dumps({'state': 'downloaded', 'sha256': item['sha256'],
                                                 'size_bytes': item['size_bytes']}))

    monkeypatch.setattr(cache.hf_assets, 'prepare_sources', sources)
    monkeypatch.setattr(cache, 'run_remote', run)
    return behavior, issued, downloads


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['runtime', 'image', 'source'])
async def test_hf_bulk_cache_and_retry_use_existing_owners(tmp_path, monkeypatch, local_transport, cloud, role):
    behavior, issued, downloads = cloud
    entry, data = artifact(tmp_path, role=role)
    behavior['bytes'][entry.sha256] = data
    updates = []
    async def progress(event):
        updates.append(event)
    receipts = await stage(tmp_path, [entry, entry], progress)
    calls, uploads = local_transport
    assert len(issued) == len(downloads) == 1
    assert uploads == []
    assert len(receipts) == 2
    assert any('Downloading artifact from Hugging Face' == e['message'] for e in updates)
    assert 'opaque-read-capability' not in json.dumps([updates, receipts])
    assert not list((tmp_path / 'worker/cache/artifacts/v1/incoming').glob('*/*'))
    if role == 'image':
        image = tmp_path / 'worker/cache/runtime-images/objects/sha256' / entry.sha256 / 'runtime.sif'
        assert image.read_bytes() == data and image.stat().st_nlink == 1
        assert image.stat().st_mode & 0o777 == 0o400
        references = json.loads((tmp_path / 'worker/cache/runtime-images/references/state.json').read_text())
        assert references['leases']
    def offline():
        raise AssertionError('A cache hit must not consult HF configuration or credentials')
    monkeypatch.setattr(cache.hf_assets, 'configuration', offline)
    await stage(tmp_path, [entry])
    assert len(downloads) == 1 and uploads == []
    assert any(r['action'] == 'ingest' for r in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('expiries', [1, 2])
async def test_expired_capability_refresh_is_finite(tmp_path, local_transport, cloud, expiries):
    behavior, issued, downloads = cloud
    entry, data = artifact(tmp_path)
    behavior.update(expire=expiries)
    behavior['bytes'][entry.sha256] = data
    if expiries == 1:
        await stage(tmp_path, [entry])
    else:
        with pytest.raises(ValueError, match='did not verify'):
            await stage(tmp_path, [entry])
    assert len(issued) == len(downloads) == 2
    assert local_transport[1] == []
    assert any(r['action'] == 'ingest' for r in local_transport[0]) == (expiries == 1)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['auth', 'transport', 'fence', 'corrupt'])
async def test_hf_errors_do_not_fallback_or_publish(tmp_path, local_transport, cloud, failure):
    behavior, issued, downloads = cloud
    entry, data = artifact(tmp_path)
    behavior['bytes'][entry.sha256] = b'corrupt' if failure == 'corrupt' else data
    behavior['fail'] = failure
    fenced = False
    def after():
        nonlocal fenced
        fenced = True
    behavior['after'] = after
    async def fence():
        if failure == 'fence' and fenced:
            raise asyncio.CancelledError()
    with pytest.raises((RuntimeError, ValueError, asyncio.CancelledError, subprocess.CalledProcessError)):
        await stage(tmp_path, [entry], fence=fence)
    calls, uploads = local_transport
    assert not uploads
    assert not any(r['action'] == 'remove_incoming' for r in calls)
    if failure != 'corrupt':
        assert not any(r['action'] == 'ingest' for r in calls)
    else:
        assert not any(p.is_file() for p in (tmp_path / 'worker/cache/artifacts/v1/objects').rglob('*'))


@pytest.mark.asyncio
async def test_small_files_keep_batching_and_inputs_never_mirror(tmp_path, monkeypatch, local_transport, cloud):
    behavior, issued, downloads = cloud
    small, _ = artifact(tmp_path)
    biological, _ = artifact(tmp_path, role='input', name='sample.fa')
    monkeypatch.setattr(cache, 'HF_MIN_BYTES', small.size_bytes + 1)
    await stage(tmp_path, [small, biological])
    assert not issued and not downloads
    assert len(local_transport[1]) == 1
    # Unsupported input roles must stay ineligible even if large.
    biological = replace(biological, sha256=hashlib.sha256(b'biological').hexdigest(), size_bytes=len(b'biological'))
    biological.source.write_bytes(b'biological')
    monkeypatch.setattr(cache, 'HF_MIN_BYTES', 1)
    await stage(tmp_path, [biological])
    assert not issued and not downloads


@pytest.mark.asyncio
async def test_same_bulk_entry_without_hf_keeps_ssh_compatibility(tmp_path, monkeypatch, local_transport, cloud):
    entry, _ = artifact(tmp_path, role='image')
    monkeypatch.setattr(cache.hf_assets, 'configuration', lambda: None)
    await stage(tmp_path, [entry])
    assert local_transport[1] == [str(entry.source)]
    assert not cloud[1] and not cloud[2]
