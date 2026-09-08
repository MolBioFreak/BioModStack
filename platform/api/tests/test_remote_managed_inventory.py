"""Real helper/filesystem readback via mounted API, with no provider/network calls."""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import uuid

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from database import ExecutionTarget, get_session
from routers.execution_targets import router
from services.remote_execution import cache, managed_inventory as mi, preloading
from test_remote_cache_integration import local_transport
from test_remote_independent_provisioning import assets
from test_remote_preloading import store, settle


@pytest_asyncio.fixture
async def mounted(store, assets, local_transport, tmp_path):
    async with store() as session:
        target = await session.get(ExecutionTarget, 'vast:1')
        target.remote_root = str(tmp_path / 'worker')
        await session.commit()
    controller = preloading.PreloadController(store)
    app = FastAPI()
    app.include_router(router, prefix='/targets')
    app.state.preload_controller = controller
    async def sessions():
        async with store() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test/targets/', follow_redirects=True) as client:
        yield client, controller, tmp_path / 'worker', local_transport
    await controller.close()


async def provision(client, controller, kind='model'):
    selection = dict(kind=kind, model_id='protenix')
    response = await client.post('/vast:1/provision/preview', json=selection)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview['scope'] == 'managed_asset_activation'
    response = await client.post('/vast:1/provision', json=selection | {'preview_sha256': preview['preview_sha256']})
    assert response.status_code == 202, response.text
    await settle(controller)
    return preview


@pytest.mark.asyncio
async def test_cumulative_inventory_rehashes_without_host_assets(mounted, assets):
    client, controller, worker, (calls, uploads) = mounted
    assert (await client.get('/vast:1/runtime-inventory')).json() is None
    await provision(client, controller, 'model')
    await provision(client, controller, 'image')
    response = await client.get('/vast:1/runtime-inventory')
    inventory = response.json()
    assert inventory['state'] == 'current'
    assert len(inventory['releases']) == 2
    assert not inventory['scientific_ready'] and not inventory['critical_runtime_ready']
    assert all(r['state'] == 'verified' for r in inventory['releases'])
    assert len(uploads) == 2
    # Both selections share precisely one independently verified immutable SIF.
    images = list(worker.rglob('*.sif'))
    assert len(images) == 1
    assert 'cache/runtime-images/objects/sha256' in str(images[0])
    assert images[0].stat().st_nlink == 1
    assert not list((worker / 'managed-assets').rglob('*.sif'))
    before = len(calls)
    assert (await client.get('/vast:1/runtime-inventory')).json() == inventory
    assert len(calls) == before  # GET never SSHs or rewrites evidence.
    model = inventory['releases'][0]
    generation = worker / 'managed-assets/v1/releases' / model['release_sha256']
    for row in model['artifacts']:
        path = (worker / 'cache/runtime-images/objects/sha256' / row['sha256'] / 'runtime.sif'
                if row['name'].startswith('containers/') else generation / row['name'])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']
        assert path.stat().st_mode & 0o222 == 0
    # Observation uses saved authoritative manifests, not local model availability.
    (assets[0] / 'protenix.sif').unlink()
    (assets[1] / 'protenix/model.pt').unlink()
    missing = generation / 'weights/protenix/model.pt'
    missing.unlink()
    response = await client.post('/vast:1/runtime-inventory/refresh')
    assert response.status_code == 200, response.text
    refreshed = response.json()
    assert refreshed['state'] == 'current'
    assert [r['state'] for r in refreshed['releases']] == ['partial', 'verified']
    assert len(uploads) == 2
    assert not (worker / 'attempts').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage,expected', [('corrupt', 'corrupt'), ('mode', 'corrupt'),
    ('symlink', 'corrupt'), ('marker', 'unverified'), ('missing', 'missing')])
async def test_observation_reports_real_damage(mounted, damage, expected):
    client, controller, worker, _ = mounted
    await provision(client, controller, 'image')
    result = (await client.get('/vast:1/runtime-inventory')).json()['releases'][0]
    path = worker / 'cache/runtime-images/objects/sha256' / result['artifacts'][0]['sha256'] / 'runtime.sif'
    if damage in {'symlink', 'missing'}:
        path.parent.chmod(0o700)
    if damage == 'corrupt':
        path.chmod(0o600)
        path.write_bytes(b'corrupt')
    elif damage == 'mode':
        path.chmod(0o600)
    elif damage == 'symlink':
        data = path.read_bytes()
        outside = worker.parent / 'outside'
        outside.write_bytes(data)
        path.unlink()
        path.symlink_to(outside)
    elif damage == 'marker':
        (worker / 'managed-assets/v1/active/image-protenix.json').unlink()
    else:
        path.unlink()
    response = await client.post('/vast:1/runtime-inventory/refresh')
    assert response.status_code == 200, response.text
    assert response.json()['releases'][0]['state'] == expected
    assert response.json()['scientific_ready'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['expired', 'future', 'endpoint', 'host_key', 'root', 'boot', 'inactive', 'provider_stopped'])
async def test_managed_projection_stale_identities(mounted, store, change):
    client, controller, _, (calls, _) = mounted
    await provision(client, controller, 'image')
    async with store() as session:
        row = await session.get(ExecutionTarget, 'vast:1')
        metadata = copy.deepcopy(row.provider_metadata)
        if change in {'expired', 'future'}:
            when = datetime.now(timezone.utc) + timedelta(hours=1 if change == 'future' else -1)
            metadata['managed_inventory']['observation']['observed_at'] = when.isoformat()
        elif change == 'endpoint': row.host = 'different'
        elif change == 'host_key': row.host_key_sha256 = 'd'*64
        elif change == 'root': row.remote_root += '-different'
        elif change == 'boot': metadata['managed_boot_id'] = str(uuid.uuid4())
        elif change == 'inactive': row.active = False
        elif change == 'provider_stopped': metadata['inventory']['running'] = False
        row.provider_metadata = metadata
        await session.commit()
    before = len(calls)
    response = await client.get('/vast:1/runtime-inventory')
    assert response.status_code == 200
    assert response.json()['state'] == 'stale'
    assert len(calls) == before
    if change in {'endpoint', 'host_key', 'root'}:
        assert (await client.post('/vast:1/runtime-inventory/refresh')).status_code == 409
        assert len(calls) == before


@pytest.mark.asyncio
async def test_readback_failure_invalidates_but_preserves_previous(mounted, monkeypatch):
    client, controller, _, _ = mounted
    await provision(client, controller, 'image')
    before = (await client.get('/vast:1/runtime-inventory')).json()
    original = cache.run_remote
    async def unavailable(*args, **kwargs):
        raise RuntimeError('secret diagnostic must not escape')
    monkeypatch.setattr(cache, 'run_remote', unavailable)
    response = await client.post('/vast:1/runtime-inventory/refresh')
    assert response.status_code == 409
    assert 'secret' not in response.text
    after = (await client.get('/vast:1/runtime-inventory')).json()
    assert after['state'] == 'stale'
    assert after['releases'] == before['releases']
    monkeypatch.setattr(cache, 'run_remote', original)
    assert (await client.post('/vast:1/runtime-inventory/refresh')).json()['state'] == 'current'


@pytest.mark.asyncio
async def test_endpoint_changes_during_readback_cannot_publish(mounted, store, monkeypatch):
    client, controller, _, _ = mounted
    await provision(client, controller, 'image')
    original = mi.observe_releases
    async def replaced(*args):
        observed = await original(*args)
        async with store() as session:
            target = await session.get(ExecutionTarget, 'vast:1')
            target.host = 'replacement'
            await session.commit()
        return observed
    monkeypatch.setattr(mi, 'observe_releases', replaced)
    assert (await client.post('/vast:1/runtime-inventory/refresh')).status_code == 409
    assert (await client.get('/vast:1/runtime-inventory')).json()['state'] == 'stale'


@pytest.mark.asyncio
async def test_incomplete_activation_preserves_previous_release(mounted, assets, monkeypatch):
    client, controller, worker, _ = mounted
    await provision(client, controller, 'image')
    marker = worker / 'managed-assets/v1/active/image-protenix.json'
    prior = marker.read_bytes()
    (assets[0] / 'protenix.sif').write_bytes(b'new approved local image')
    original = mi.helper_call
    async def incomplete(connection, request, fence):
        if request['action'] == 'install':
            digest = request['manifest']['artifacts'][0]['sha256']
            path = worker / 'cache/runtime-images/objects/sha256' / digest / 'runtime.sif'
            path.parent.chmod(0o700)
            path.unlink()
        return await original(connection, request, fence)
    monkeypatch.setattr(mi, 'helper_call', incomplete)
    await provision(client, controller, 'image')
    assert marker.read_bytes() == prior
    listing = (await client.get('')).json()
    assert listing[0]['preload']['phase'] == 'failed'
    assert (await client.get('/vast:1/runtime-inventory')).json()['state'] == 'stale'
    # Explicit refresh recovers the still-valid prior release without reactivation.
    assert (await client.post('/vast:1/runtime-inventory/refresh')).json()['releases'][0]['state'] == 'verified'


@pytest.mark.asyncio
async def test_boot_change_before_activation_fails_closed(mounted, monkeypatch):
    client, controller, worker, _ = mounted
    original = mi.helper_call
    async def reboot(connection, request, fence):
        if request['action'] == 'install':
            request = request | {'boot_id': str(uuid.uuid4())}
        return await original(connection, request, fence)
    monkeypatch.setattr(mi, 'helper_call', reboot)
    await provision(client, controller, 'image')
    assert not (worker / 'managed-assets/v1/active/image-protenix.json').exists()
    assert (await client.get('')).json()[0]['preload']['phase'] == 'failed'


@pytest.mark.asyncio
async def test_cancel_before_activation_retains_cache_not_readiness(mounted, monkeypatch):
    client, controller, worker, _ = mounted
    reached = asyncio.Event()
    original = mi.helper_call
    async def blocked(connection, request, fence):
        if request['action'] == 'install':
            reached.set()
            await asyncio.Event().wait()
        return await original(connection, request, fence)
    monkeypatch.setattr(mi, 'helper_call', blocked)
    selection = dict(kind='image', model_id='protenix')
    preview = (await client.post('/vast:1/provision/preview', json=selection)).json()
    assert (await client.post('/vast:1/provision', json=selection | {
        'preview_sha256': preview['preview_sha256']})).status_code == 202
    await asyncio.wait_for(reached.wait(), timeout=10)
    await controller.close()
    assert (await client.get('')).json()[0]['preload']['phase'] == 'failed'
    assert not (worker / 'managed-assets/v1/active/image-protenix.json').exists()
    assert (await client.get('/vast:1/runtime-inventory')).json() is None
    digest = preview['artifacts'][0]['sha256']
    assert (worker / 'cache/runtime-images/objects/sha256' / digest / 'runtime.sif').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['nonobject', 'bad_time', 'manifest_identity', 'false_ready'])
async def test_malformed_managed_metadata_is_not_readiness(mounted, store, damage):
    client, controller, _, _ = mounted
    await provision(client, controller, 'image')
    async with store() as session:
        row = await session.get(ExecutionTarget, 'vast:1')
        metadata = copy.deepcopy(row.provider_metadata)
        raw = metadata['managed_inventory']
        if damage == 'nonobject': metadata['managed_inventory'] = ['invalid']
        elif damage == 'bad_time': raw['observation']['observed_at'] = 'invalid'
        elif damage == 'manifest_identity': raw['manifests'][0]['source_tree'] = 'f'*40
        else: raw['observation']['releases'][0]['artifacts'][0]['state'] = 'missing'
        row.provider_metadata = metadata
        await session.commit()
        before = copy.deepcopy(metadata)
    assert (await client.get('/vast:1/runtime-inventory')).json() is None
    async with store() as session:
        assert (await session.get(ExecutionTarget, 'vast:1')).provider_metadata == before


@pytest.mark.asyncio
async def test_legacy_copied_image_metadata_cannot_certify_shared_storage(mounted, store):
    client, controller, _, _ = mounted
    await provision(client, controller, 'image')
    async with store() as session:
        target = await session.get(ExecutionTarget, 'vast:1')
        metadata = copy.deepcopy(target.provider_metadata)
        saved = metadata['managed_inventory']
        legacy = saved['manifests'][0]
        del legacy['artifacts'][0]['kind']
        saved['observation']['releases'][0]['release_sha256'] = mi.release_digest(legacy)
        target.provider_metadata = metadata
        await session.commit()
    assert (await client.get('/vast:1/runtime-inventory')).json() is None
    assert (await client.post('/vast:1/runtime-inventory/refresh')).status_code == 409


@pytest.mark.asyncio
async def test_space_preflight_failure_happens_before_asset_upload(mounted, monkeypatch):
    client, controller, worker, (_, uploads) = mounted
    original = mi.helper_call
    actions = []
    async def low_space(connection, request, fence):
        actions.append(request['action'])
        if request['action'] == 'admit':
            raise OSError(28, 'private worker diagnostic')
        return await original(connection, request, fence)
    monkeypatch.setattr(mi, 'helper_call', low_space)
    await provision(client, controller, 'image')
    assert actions == ['boot', 'admit']
    assert uploads == []
    assert (await client.get('')).json()[0]['preload']['phase'] == 'failed'
    assert not (worker / 'managed-assets/v1/active/image-protenix.json').exists()


def test_helper_rejects_unsafe_manifest_and_symlink_parent(tmp_path):
    from tools import bms_artifact_cache as helper
    from tools import bms_managed_runtime as runtime
    data = b'image'
    manifest = dict(selection=dict(kind='model', model_id='protenix'), source_revision='a'*40,
        source_tree='b'*40, artifacts=[dict(name='weights/protenix.sif', sha256=hashlib.sha256(data).hexdigest(),
                                          size_bytes=len(data), mode=0o644)])
    root = tmp_path / 'managed'
    release = root / 'releases' / runtime.validate_manifest(manifest, helper)
    release.mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'protenix.sif').write_bytes(data)
    (release / 'weights').symlink_to(outside, target_is_directory=True)
    assert runtime.observe(root, manifest, helper)['state'] == 'corrupt'
    with pytest.raises(ValueError, match='incomplete_release'):
        runtime.activate(root, manifest, runtime.boot_id(), helper)
    for path in ('../escape', '/etc/passwd', 'containers/../escape', 'containers//foo'):
        bad = copy.deepcopy(manifest)
        bad['artifacts'][0]['name'] = path
        with pytest.raises(ValueError):
            runtime.validate_manifest(bad, helper)
