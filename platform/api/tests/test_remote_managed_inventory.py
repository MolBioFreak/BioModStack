"""Real helper/filesystem readback via mounted API, with no provider/network calls."""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import hashlib
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
from test_managed_runtime_safety import critical_package


@pytest_asyncio.fixture
async def mounted(store, assets, local_transport, tmp_path):
    async with store() as session:
        target = await session.get(ExecutionTarget, 'vast:1')
        target.remote_root = str(tmp_path / 'worker')
        await session.commit()
    async def unproven_quiescence(connection, operation_id):
        # This helper double has no durable remote operation witness. Never
        # contact SSH or infer remote quiescence from synchronous local return.
        return False
    controller = preloading.PreloadController(store, quiesce=unproven_quiescence)
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


def test_saved_inventory_includes_current_endpoint_critical_release(critical_package):
    from types import SimpleNamespace
    manifest, _, _, worker = critical_package
    target = SimpleNamespace(host='203.0.113.1', port=22, username='root', remote_root=str(worker),
                             host_key_sha256='a'*64, provider_metadata={})
    endpoint = mi.endpoint_digest(target)
    model = dict(selection=dict(kind='model', model_id='protenix'))
    target.provider_metadata = dict(critical_runtime_manifest=manifest,
        critical_runtime_endpoint_sha256=endpoint,
        managed_inventory=dict(endpoint_sha256=endpoint, manifests=[model]))
    assert mi.saved_manifests(target) == [model, manifest]
    target.provider_metadata['managed_inventory']['manifests'].append(manifest)
    assert mi.saved_manifests(target) == [model, manifest]
    target.host = '203.0.113.2'
    assert mi.saved_manifests(target) == []


def test_critical_readback_rejects_forged_compatibility(tmp_path, monkeypatch, critical_package):
    from test_managed_runtime_safety import install_critical_fixture
    manifest, _, _, _ = critical_package
    m, helper, root = install_critical_fixture(critical_package, monkeypatch)
    release = m.install(root, manifest, m.boot_id(), helper)['release']
    result = mi.ManagedInventory(observed_at=datetime.now(timezone.utc), boot_id=m.boot_id(), releases=[release])
    mi.validate_observation(result, [manifest])
    assert result.releases[0].critical is not None
    result.releases[0].critical.observed['machine'] = 'changed'
    with pytest.raises(ValueError, match='compatibility observation mismatch'):
        mi.validate_observation(result, [manifest])


async def provision(client, controller, kind='model'):
    selection = dict(kind=kind, model_id='protenix')
    response = await client.post('/vast:1/provision/preview', json=selection)
    assert response.status_code == 200, response.text
    preview = response.json()
    # Historical strict payloads remain readable during the scope migration.
    assert preview['scope'] in {'managed_asset_activation', 'selected_asset_download'}
    response = await client.post('/vast:1/provision', json=selection | {'preview_sha256': preview['preview_sha256']})
    assert response.status_code == 202, response.text
    await settle(controller)
    return preview


@pytest.fixture
def historical_release(store, assets):
    """Seed a real historical generation independently of DOWNLOAD."""
    from tools import bms_artifact_cache as helper, bms_managed_runtime as runtime
    from services.remote_execution.transport import RemoteConnection

    async def seed(kind='image'):
        async with store() as session:
            target = await session.get(ExecutionTarget, 'vast:1')
            worker = Path(target.remote_root)
            connection = RemoteConnection.from_target(target)
            manifests = mi.saved_manifests(target)
        storage = helper.Cache(worker / 'cache/artifacts/v1')
        rows = []
        sources = [('containers/protenix.sif', assets[0] / 'protenix.sif', 'runtime_image')]
        if kind == 'model':
            sources.append(('weights/protenix/model.pt', assets[1] / 'protenix/model.pt', None))
        for name, source, artifact_kind in sources:
            data = source.read_bytes()
            row = dict(name=name, sha256=hashlib.sha256(data).hexdigest(),
                       size_bytes=len(data), mode=0o644)
            if artifact_kind:
                row['kind'] = artifact_kind
            incoming = Path(storage.incoming_batch(str(uuid.uuid4()), uuid.uuid4().hex, create=True)) / 'asset'
            incoming.write_bytes(data)
            storage.ingest(row, incoming)
            rows.append(row)
        manifest = dict(selection=dict(kind=kind, model_id='protenix'),
                        source_revision='a'*40, source_tree='b'*40, artifacts=rows)
        runtime.install(worker / 'managed-assets/v1', manifest, runtime.boot_id(), helper)
        manifests = [m for m in manifests if m['selection'] != manifest['selection']] + [manifest]
        observed = await mi.observe_releases(connection, manifests, cache._noop)
        async with store() as session:
            target = await session.get(ExecutionTarget, 'vast:1')
            target.provider_metadata = copy.deepcopy(target.provider_metadata) | dict(
                managed_boot_id=str(observed.boot_id), managed_inventory=dict(
                    manifests=manifests, observation=observed.model_dump(mode='json'),
                    endpoint_sha256=mi.endpoint_digest(target)))
            await session.commit()
        return manifest
    return seed


@pytest.mark.asyncio
async def test_cumulative_inventory_rehashes_without_host_assets(mounted, assets, historical_release):
    client, controller, worker, (calls, uploads) = mounted
    assert (await client.get('/vast:1/runtime-inventory')).json() is None
    await historical_release('model')
    await historical_release('image')
    response = await client.get('/vast:1/runtime-inventory')
    inventory = response.json()
    assert inventory['state'] == 'current'
    assert len(inventory['releases']) == 2
    assert not inventory['scientific_ready'] and not inventory['critical_runtime_ready']
    assert all(r['state'] == 'verified' for r in inventory['releases'])
    assert not uploads
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
    assert not uploads
    assert not (worker / 'attempts').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage,expected', [('corrupt', 'corrupt'), ('mode', 'corrupt'),
    ('symlink', 'corrupt'), ('marker', 'unverified'), ('missing', 'missing')])
async def test_observation_reports_real_damage(mounted, historical_release, damage, expected):
    client, controller, worker, _ = mounted
    await historical_release('image')
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
async def test_managed_projection_stale_identities(mounted, store, historical_release, change):
    client, controller, _, (calls, _) = mounted
    await historical_release('image')
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
async def test_readback_failure_invalidates_but_preserves_previous(mounted, historical_release, monkeypatch):
    client, controller, _, _ = mounted
    await historical_release('image')
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
    assert after['releases'] == [dict(release, bounded_readiness='stale',
        native_readiness=dict(release['native_readiness'], state='stale'))
                                 for release in before['releases']]
    assert not after['scientific_ready']
    monkeypatch.setattr(cache, 'run_remote', original)
    assert (await client.post('/vast:1/runtime-inventory/refresh')).json()['state'] == 'current'


@pytest.mark.asyncio
async def test_endpoint_changes_during_readback_cannot_publish(mounted, store, historical_release, monkeypatch):
    client, controller, _, _ = mounted
    await historical_release('image')
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
async def test_incomplete_activation_preserves_previous_release(mounted, historical_release):
    from tools import bms_artifact_cache as helper, bms_managed_runtime as runtime
    client, _, worker, _ = mounted
    manifest = await historical_release('image')
    marker = worker / 'managed-assets/v1/active/image-protenix.json'
    prior = marker.read_bytes()
    replacement = copy.deepcopy(manifest)
    replacement['artifacts'][0]['sha256'] = hashlib.sha256(b'absent replacement').hexdigest()
    replacement['artifacts'][0]['size_bytes'] = len(b'absent replacement')
    with pytest.raises(ValueError, match='incomplete_shared_image'):
        runtime.install(worker / 'managed-assets/v1', replacement, runtime.boot_id(), helper)
    assert marker.read_bytes() == prior
    assert (await client.get('/vast:1/runtime-inventory')).json()['releases'][0]['state'] == 'verified'


@pytest.mark.asyncio
async def test_boot_change_before_activation_fails_closed(mounted, historical_release):
    from tools import bms_artifact_cache as helper, bms_managed_runtime as runtime
    _, _, worker, _ = mounted
    manifest = await historical_release('image')
    marker = worker / 'managed-assets/v1/active/image-protenix.json'
    prior = marker.read_bytes()
    with pytest.raises(ValueError, match='worker_boot_changed'):
        runtime.install(worker / 'managed-assets/v1', manifest, str(uuid.uuid4()), helper)
    assert marker.read_bytes() == prior


@pytest.mark.asyncio
async def test_cancel_during_acquisition_retains_cache_not_readiness(mounted, monkeypatch):
    client, controller, worker, _ = mounted
    reached, image_published = asyncio.Event(), asyncio.Event()
    original = cache.rsync_to_remote
    remote = cache.run_remote
    async def readback(connection, argv, input_bytes=None, **kwargs):
        result = await remote(connection, argv, input_bytes=input_bytes, **kwargs)
        if input_bytes and '-c' not in argv:
            request = json.loads(input_bytes)
            if request['action'] == 'ingest' and request['artifact'].get('kind') == 'runtime_image':
                image_published.set()
        return result
    async def blocked(connection, source, destination, **kwargs):
        # Acquisition fan-out is concurrent; let the image publish before
        # cancelling the still-blocked weight batch, not by assuming ordering.
        if Path(source).is_dir():
            await image_published.wait()
            reached.set()
            await asyncio.Event().wait()
        return await original(connection, source, destination, **kwargs)
    monkeypatch.setattr(cache, 'run_remote', readback)
    monkeypatch.setattr(cache, 'rsync_to_remote', blocked)
    selection = dict(kind='model', model_id='protenix')
    preview = (await client.post('/vast:1/provision/preview', json=selection)).json()
    response = await client.post('/vast:1/provision', json=selection | {
        'preview_sha256': preview['preview_sha256']})
    assert response.status_code == 202, response.text
    await asyncio.wait_for(reached.wait(), timeout=10)
    await controller.close()
    progress = (await client.get('')).json()[0]['preload']
    assert progress['phase'] == 'recovery_blocked'
    assert progress['cancel_requested'] and progress['recovery_required']
    assert not (worker / 'managed-assets').exists()
    assert (await client.get('/vast:1/runtime-inventory')).json() is None
    image = next(r for r in preview['artifacts'] if r['name'].endswith('.sif'))
    assert (worker / 'cache/runtime-images/objects/sha256' / image['sha256'] / 'runtime.sif').exists()
    weight = next(r for r in preview['artifacts'] if r['name'].endswith('model.pt'))
    assert not (worker / 'cache/artifacts/v1/objects/sha256' / weight['sha256'][:2] / weight['sha256']).exists()
    assert (await client.get('/vast:1/artifact-inventory')).json() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy', [False, True])
async def test_download_completes_without_managed_install_or_audit(mounted, store, historical_release, monkeypatch, legacy):
    client, controller, worker, (calls, _) = mounted
    if legacy:
        await historical_release('image')
        async with store() as session:
            target = await session.get(ExecutionTarget, 'vast:1')
            metadata = copy.deepcopy(target.provider_metadata)
            metadata['managed_inventory']['observation']['observed_at'] = (
                datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            target.provider_metadata = metadata
            await session.commit()
    async with store() as session:
        target = await session.get(ExecutionTarget, 'vast:1')
        previous = copy.deepcopy(target.provider_metadata.get('managed_inventory'))
        prior_boot = target.provider_metadata.get('managed_boot_id')
    before = (await client.get('/vast:1/runtime-inventory')).json()
    if legacy:
        assert before['state'] == 'stale'
    async def forbidden(*args, **kwargs):
        pytest.fail('DOWNLOAD must not install, activate or audit managed releases')
    monkeypatch.setattr(mi, 'helper_call', forbidden)
    monkeypatch.setattr(mi, 'activate_release', forbidden)
    monkeypatch.setattr(mi, 'observe_releases', forbidden)
    start_calls = len(calls)
    preview = await provision(client, controller, 'model')
    progress = (await client.get('')).json()[0]['preload']
    assert progress['phase'] == 'source_download_ready'
    assert progress['message'] == 'Downloads complete'
    assert progress['artifacts'] == preview['artifacts']
    assert (await client.get('/vast:1/artifact-inventory')).json()['state'] == 'download_verified'
    assert not any(c['action'] in {'boot', 'admit', 'install', 'activate', 'bounded_check', 'native_check'}
                   for c in calls[start_calls:])
    assert (await client.get('/vast:1/runtime-inventory')).json() == before
    async with store() as session:
        target = await session.get(ExecutionTarget, 'vast:1')
        assert target.provider_metadata.get('managed_inventory') == previous
        assert target.provider_metadata.get('managed_boot_id') == prior_boot
    assert not list((worker / 'managed-assets').rglob('model.pt'))
    if not legacy:
        assert not (worker / 'managed-assets').exists()


@pytest.mark.parametrize('outcome', ['passed', 'failed'])
def test_historical_typed_native_probe_remains_projectable(outcome):
    boot = uuid.uuid4()
    release = mi.ManagedRelease(selection=dict(kind='model', model_id='rfantibody'),
        release_sha256='a'*64, source_revision='b'*40, source_tree='c'*40,
        state='verified', artifacts=[dict(name='containers/rfantibody.sif', sha256='d'*64,
                                        size_bytes=1, state='verified')])
    probe = mi.NativeProbeEvidence(authority='scripts/check_rfantibody_runtime.py:run_preflight',
        outcome=outcome, gpu_id=0, gpu_uuid='historical-fixture-gpu',
        observed_at=datetime.now(timezone.utc), release_sha256=release.release_sha256,
        image_sha256='d'*64, script_sha256='e'*64, source_revision=release.source_revision,
        source_tree=release.source_tree, boot_id=boot)
    release.native_readiness = mi.NativeReadiness(state='unverified',
        authority=probe.authority, probe=probe)
    # Exercise serialized historical evidence, not a new provisioning probe.
    retained = mi.ManagedRelease.model_validate_json(release.model_dump_json())
    result = mi.project_native_readiness(retained, current=True, critical_ready=True, boot=boot)
    assert result.probe == probe
    assert ('native_runtime_preflight_failed' in result.blockers) == (outcome == 'failed')
    stale = mi.project_native_readiness(retained, current=False, critical_ready=True, boot=boot)
    assert stale.state == 'stale' and stale.probe == probe
    rebooted = mi.project_native_readiness(retained, current=True, critical_ready=True, boot=uuid.uuid4())
    assert rebooted.probe is None


def test_removed_download_probe_keeps_native_task_script():
    assert not hasattr(mi, 'run_native_readiness_check')
    root = Path(__file__).resolve().parents[3]
    native = (root / 'modules/rfantibody.nf').read_text()
    assert 'check_rfantibody_runtime.py' in native
    assert (root / 'scripts/check_rfantibody_runtime.py').is_file()


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
