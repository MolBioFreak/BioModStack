"""Weight-role eligibility for HF delivery, with the SDK child and HTTP doubled.

The controller broker's own role gate and object key are exercised here; the
archive-driven cache stager is covered by `test_hf_weight_archive.py`. No live
cloud request, credential or scientific asset is involved.
"""
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from services.remote_execution import cache
from services.remote_execution import hf_assets as broker
from services.remote_execution import hf_assets_worker as worker
from test_hf_asset_sources import Api, Http
from test_hf_weight_archive import build_archive, cloud, row, weight_bundle
from test_remote_cache_integration import local_transport  # noqa: F401  (fixture)


def drop_view(root):
    """Remove a published read-only weight view, leaving the verified objects."""
    for path in sorted(Path(root).rglob('*'), key=lambda item: len(item.parts), reverse=True):
        os.chmod(path.parent, 0o755)
        if path.is_dir() and not path.is_symlink():
            path.rmdir()
        else:
            path.unlink()
    os.chmod(Path(root).parent, 0o755)
    Path(root).rmdir()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in broker._KEYS + (broker.WEIGHTS_ARCHIVE_KEY,):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def configured(tmp_path, monkeypatch):
    token = tmp_path / 'token'
    token.write_text('hf_' + 'a' * 32)
    token.chmod(0o600)
    monkeypatch.setenv('BMS_HF_ASSET_BUCKET', 'owner/private-assets')
    monkeypatch.setenv('BMS_HF_TOKEN_FILE', str(token))
    return token


@pytest.fixture
def capability():
    expires = int(time.time()) + 600
    return {'url': f'https://us.aws.cdn.hf.co/object?Signature=abc&Policy=abc&Key-Pair-Id=test&Expires={expires}',
            'expires_at': expires}


def weight(tmp_path, data, role='weights'):
    source = tmp_path / 'weights.ckpt'
    source.write_bytes(data)
    return SimpleNamespace(source=source, role=role, sha256=hashlib.sha256(data).hexdigest(),
                           size_bytes=len(data))


def request(entry):
    return dict(role=entry.role, sha256=entry.sha256, size_bytes=entry.size_bytes, path=str(entry.source))


@pytest.mark.asyncio
async def test_weights_role_is_eligible_and_small_weights_stay_batched(configured, monkeypatch, tmp_path, capability):
    calls = []

    async def invoke(value, *, check_fence):
        calls.append(value)
        await check_fence()
        return capability

    monkeypatch.setattr(broker, '_invoke', invoke)
    entry = weight(tmp_path, b'w' * broker.MIN_BYTES)
    assert await broker.prepare_sources([entry], check_fence=broker._noop) == {(False, entry.sha256): capability}
    # The role travels as itself; the object key stays the one shared non-image
    # content address, so no second naming scheme appears in the bucket.
    assert calls == [dict(action='prepare', role='weights', sha256=entry.sha256,
                          size_bytes=entry.size_bytes, path=str(entry.source))]

    async def forbidden(*args, **kwargs):
        raise AssertionError('a below-floor weight file must not open an HTTP request')

    monkeypatch.setattr(broker, '_invoke', forbidden)
    small = weight(tmp_path, b'w' * (broker.MIN_BYTES - 1))
    assert await broker.prepare_sources([small], check_fence=broker._noop) == {}
    for role in ('input', 'result', '', 'weights '):
        with pytest.raises(broker.HFAssetError, match='identity or role is invalid'):
            await broker.prepare_sources([weight(tmp_path, b'w' * broker.MIN_BYTES, role=role)],
                                         check_fence=broker._noop)


def test_worker_role_gate_admits_weights_on_the_shared_artifact_key(configured, monkeypatch, tmp_path, capability):
    entry = weight(tmp_path, b'w' * broker.MIN_BYTES)
    monkeypatch.setenv('BMS_HF_ASSET_ALLOW_PUBLISH', '1')
    api, http = Api(entry, present=False), Http(capability)
    for _ in range(2):
        assert worker._prepare(request(entry), broker.configuration(), api, http, 'token') == capability
    assert len(api.uploads) == 1
    assert api.uploads[0][0][1] == f'sha256/{entry.sha256}/artifact'
    # The same gate still refuses a role that is not a delivery class.
    with pytest.raises(broker.HFAssetError, match='identity or role is invalid'):
        worker._prepare({**request(entry), 'role': 'input'}, broker.configuration(),
                        Api(entry), Http(capability), 'token')


@pytest.mark.asyncio
async def test_weight_archive_setting_declares_an_object_without_enabling_hf(configured, monkeypatch):
    assert broker.weights_archive() is None
    digest = hashlib.sha256(b'packed shared weight tree').hexdigest()
    monkeypatch.setenv(broker.WEIGHTS_ARCHIVE_KEY, f'{digest}:{broker.MIN_BYTES}')
    assert broker.weights_archive() == (digest, broker.MIN_BYTES)
    for value in ('', f'{digest}', f'{digest}:', f'{digest}:0', f'{digest}:-1',
                  f'{digest.upper()}:{broker.MIN_BYTES}', f'../{digest}:{broker.MIN_BYTES}',
                  f'{digest}:{broker.MIN_BYTES}:extra', f'{digest[1:]}:{broker.MIN_BYTES}'):
        monkeypatch.setenv(broker.WEIGHTS_ARCHIVE_KEY, value)
        with pytest.raises(broker.HFAssetError):
            broker.weights_archive()
    # The setting is parsed for form; the existing delivery floor still decides
    # whether an object of that size is obtained from HF at all, so a declaration
    # below the floor never becomes an HTTP request of its own.
    monkeypatch.setenv(broker.WEIGHTS_ARCHIVE_KEY, f'{digest}:1')
    assert broker.weights_archive() == (digest, 1)

    async def forbidden(*args, **kwargs):
        raise AssertionError('a below-floor declaration must not open an HTTP request')

    monkeypatch.setattr(broker, '_invoke', forbidden)
    below = SimpleNamespace(source=Path('/declared-archive'), role='weights', sha256=digest, size_bytes=1)
    assert await broker.prepare_sources([below], check_fence=broker._noop) == {}
    # Naming the archive is not a way to enable a cloud route: with every other
    # HF setting absent the deployment still reports the unchanged SSH mode.
    for key in broker._KEYS:
        monkeypatch.delenv(key, raising=False)
    status = broker.readiness()
    assert status['configured'] is False and status['mode'] == 'ssh'
    assert broker.configuration() is None


@pytest.mark.asyncio
async def test_completed_pass_is_reused_instead_of_downloaded_again(tmp_path, monkeypatch, local_transport):
    """A retried stage installs from the recorded pass; the archive is not re-fetched."""
    members = {'boltz/model.ckpt': b'M' * (64 * 1024)}
    absent = {'row': row('protenix/large.ckpt', b'O' * (32 * 1024)), 'payload': b'O' * (32 * 1024)}
    connection, bundle = weight_bundle(tmp_path, members, absent)
    path, digest, size = build_archive(tmp_path / 'weights.tar', members)
    payloads = {digest: path.read_bytes(), absent['row']['sha256']: absent['payload']}
    _, downloads, calls, uploads = cloud(monkeypatch, local_transport, payloads)
    monkeypatch.setenv(broker.WEIGHTS_ARCHIVE_KEY, f'{digest}:{size}')
    # The delivery floor is exercised on its own; these fixtures are tiny.
    monkeypatch.setattr(cache, 'HF_MIN_BYTES', 4096)

    receipts = await cache.stage_cached_bundle(connection=connection, bundle=bundle)
    assert [request['artifact']['sha256'] for request in downloads] == [digest, absent['row']['sha256']]
    declared = {(item.sha256, item.size_bytes) for item in bundle.runtime_weights}
    assert {(item['sha256'], item['size_bytes']) for item in receipts} >= declared

    # The view is discarded, never the verified objects or the archive's record.
    drop_view(Path(bundle.envelope.environment['BMS_WEIGHTS']))
    connection, retry = weight_bundle(tmp_path, members, absent)
    delivered = await cache.stage_cached_bundle(connection=connection, bundle=retry)
    # The retry answered from the worker's own record: no second archive fetch.
    assert [request['artifact']['sha256'] for request in downloads] == [digest, absent['row']['sha256']]
    declared = {(item.sha256, item.size_bytes) for item in retry.runtime_weights}
    assert {(item['sha256'], item['size_bytes']) for item in delivered} >= declared
    assert len(uploads) == 2  # each bundle's listing, and no weight member
    assert all(Path(entry).name.startswith('bms-cache-batch-') for entry in uploads)
    root = Path(retry.envelope.environment['BMS_WEIGHTS'])
    assert (root / 'boltz/model.ckpt').read_bytes() == members['boltz/model.ckpt']
    assert (root / 'protenix/large.ckpt').read_bytes() == absent['payload']
