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
    assert after['releases'] == [dict(release, bounded_readiness='stale',
        native_readiness=dict(release['native_readiness'], state='stale'))
                                 for release in before['releases']]
    assert not after['scientific_ready']
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
    assert listing[0]['preload']['phase'] == 'recovery_blocked'
    assert listing[0]['preload']['recovery_required'] is True
    assert (await client.get('/vast:1/runtime-inventory')).json()['state'] == 'stale'
    # Uncertain writers retain ownership: even refresh cannot bypass recovery.
    assert (await client.post('/vast:1/runtime-inventory/refresh')).status_code == 409
    assert marker.read_bytes() == prior
    retained = (await client.get('/vast:1/runtime-inventory')).json()['releases'][0]
    assert retained['state'] == 'verified' and retained['bounded_readiness'] == 'stale'
    for artifact in retained['artifacts']:
        cached = worker / 'cache/runtime-images/objects/sha256' / artifact['sha256'] / 'runtime.sif'
        assert hashlib.sha256(cached.read_bytes()).hexdigest() == artifact['sha256']


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
    progress = (await client.get('')).json()[0]['preload']
    assert progress['phase'] == 'recovery_blocked'
    assert progress['recovery_required'] is True


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
    progress = (await client.get('')).json()[0]['preload']
    assert progress['phase'] == 'recovery_blocked'
    assert progress['recovery_required'] is True
    assert not (worker / 'managed-assets/v1/active/image-protenix.json').exists()
    assert (await client.get('/vast:1/runtime-inventory')).json() is None
    digest = preview['artifacts'][0]['sha256']
    assert (worker / 'cache/runtime-images/objects/sha256' / digest / 'runtime.sif').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['nonobject', 'bad_time', 'manifest_identity', 'false_ready',
                                  'image_owner', 'image_size', 'image_missing', 'image_extra'])
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
        elif damage == 'false_ready': raw['observation']['releases'][0]['artifacts'][0]['state'] = 'missing'
        else:
            release = raw['observation']['releases'][0]
            reference = release['image_reference']
            if damage == 'image_owner': reference['owner'] = 'managed-release:' + 'f'*64
            elif damage == 'image_size': next(iter(reference['identities'].values()))['size'] += 1
            elif damage == 'image_missing': release['image_reference'] = None
            else: reference['unapproved'] = True
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
    progress = (await client.get('')).json()[0]['preload']
    assert progress['phase'] == 'recovery_blocked'
    assert progress['recovery_required'] is True
    assert not (worker / 'managed-assets/v1/active/image-protenix.json').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('native', [None, {'state': 'ready'}, 'invalid'])
async def test_optional_native_readiness_never_hides_asset_inventory(mounted, store, native):
    client, controller, _, _ = mounted
    await provision(client, controller, 'model')
    async with store() as session:
        target = await session.get(ExecutionTarget, 'vast:1')
        metadata = copy.deepcopy(target.provider_metadata)
        release = metadata['managed_inventory']['observation']['releases'][0]
        if native is None:
            release.pop('native_readiness', None)
        else:
            release['native_readiness'] = native
        target.provider_metadata = metadata
        await session.commit()
    inventory = (await client.get('/vast:1/runtime-inventory')).json()
    assert inventory['state'] == 'current'
    release = inventory['releases'][0]
    assert release['state'] == 'verified'  # Provisioning is not scientific admission.
    assert release['native_readiness']['state'] == 'blocked'
    assert release['native_readiness']['blockers'] == ['critical_release_not_verified']
    assert 'selected_image_native_preflight_binding' in release['native_readiness']['missing_authorities']
    assert inventory['scientific_ready'] is False


def test_native_readiness_uses_shared_dependencies_without_certifying_models():
    from model_registry import INDEPENDENT_RUNTIME_MODELS, get_registry, model_runtime_dependencies
    for model_id in sorted(INDEPENDENT_RUNTIME_MODELS):
        model = get_registry().get_model(model_id)
        if model is None or not model.enabled or not model.public_launch:
            with pytest.raises(ValueError):
                model_runtime_dependencies(model_id)
            continue  # Preserve actual public admission; parent-native closure is distinct.
        dependencies = model_runtime_dependencies(model_id)
        artifacts = [dict(name=('containers/' + d.relative_path if d.kind == 'image' else
                                'weights/' + d.relative_path + '/fixture.bin'),
                          sha256='a' * 64, size_bytes=1, state='verified') for d in dependencies]
        release = mi.ManagedRelease.model_validate(dict(selection=dict(kind='model', model_id=model_id),
            release_sha256='b' * 64, source_revision='c' * 40, source_tree='d' * 40,
            state='verified', artifacts=artifacts))
        ready = mi.project_native_readiness(release, current=True, critical_ready=True)
        assert ready.state == 'unverified', model_id
        assert not ready.blockers
        assert ready.missing_authorities == ['selected_image_native_preflight_binding']
        assert mi.project_native_readiness(release, current=False, critical_ready=True).state == 'stale'
        release.artifacts.pop()
        blocked = mi.project_native_readiness(release, current=True, critical_ready=True)
        assert blocked.state == 'blocked', model_id
        assert any(b.startswith('dependency_not_verified:') for b in blocked.blockers)


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', [None, 'boot', 'image', 'source', 'failed', 'cancel', 'reported_gpu', 'no_gpu'])
async def test_explicit_native_probe_uses_native_script_and_bound_readback(monkeypatch, damage):
    from types import SimpleNamespace
    from services import nextflow
    from services.remote_execution import bundle, transport
    source = Path(__file__).resolve().parents[3]
    manifest = dict(selection=dict(kind='workflow', model_id='e' * 64),
        source_revision='a' * 40, source_tree='b' * 40,
        artifacts=[dict(name='containers/rfantibody.sif', kind='runtime_image',
                        sha256='c' * 64, size_bytes=1, mode=0o444)])
    digest = mi.release_digest(manifest)
    boot = uuid.uuid4()
    identity = dict(sha256='c' * 64, size=1, device=1, inode=1, mtime_ns=1, ctime_ns=1)
    observed = mi.ManagedInventory.model_validate(dict(observed_at=datetime.now(timezone.utc),
        boot_id=boot, releases=[dict(selection=manifest['selection'], release_sha256=digest,
            source_revision=manifest['source_revision'], source_tree=manifest['source_tree'],
            state='verified', artifacts=[dict(name='containers/rfantibody.sif',
                sha256='c' * 64, size_bytes=1, state='verified')],
            image_reference=dict(store_root='/worker/cache/runtime-images',
                owner='managed-release:' + digest, lease_token='d' * 32, identities={'c' * 64: identity}))]))
    calls, reads, identities = [], [], []
    monkeypatch.setattr(nextflow, 'get_code_root', lambda: source)
    def source_identity(root):
        identities.append(root)
        return ('f' * 40, 'b' * 40) if damage == 'source' and len(identities) > 1 else ('a' * 40, 'b' * 40)
    monkeypatch.setattr(bundle, 'current_source_identity', source_identity)
    async def readback(connection, manifests, fence):
        await fence()
        reads.append(manifests)
        result = observed.model_copy(deep=True)
        if len(reads) > 1:
            if damage == 'boot': result.boot_id = uuid.uuid4()
            if damage == 'image':
                reference = result.releases[0].image_reference
                assert reference is not None
                reference.identities['c' * 64].inode += 1
        return result
    monkeypatch.setattr(mi, 'observe_releases', readback)
    async def command(connection, argv, **kwargs):
        calls.append((connection, argv, kwargs))
        if damage == 'cancel': raise asyncio.CancelledError()
        if argv[0] == 'nvidia-smi':
            return SimpleNamespace(returncode=0, stdout='' if damage == 'no_gpu' else
                '7, GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee\n')
        return SimpleNamespace(returncode=1 if damage == 'failed' else 0, stdout='[RFA-PREFLIGHT] OK\n')
    monkeypatch.setattr(transport, 'run_remote', command)
    async def fence(): pass
    connection = SimpleNamespace(provision_operation_id='owned-provision')
    if damage in {'boot', 'image', 'source', 'cancel'}:
        with pytest.raises(asyncio.CancelledError if damage == 'cancel' else ValueError):
            await mi.run_native_readiness_check(connection, manifest, fence, gpu_id=0)
    else:
        result = await mi.run_native_readiness_check(connection, manifest, fence,
            gpu_id=None if damage in {'reported_gpu', 'no_gpu'} else 0)
        release = result.releases[0]
        assert release.native_readiness is not None
        probe = release.native_readiness.probe
        if damage == 'no_gpu':
            assert probe is None and result.scientific_ready is False
            assert release.state == 'verified'
            assert 'native_probe_gpu_identity_not_reported' in release.native_readiness.missing_authorities
            assert len(calls) == 1 and calls[0][1][0] == 'nvidia-smi'
            return
        assert probe is not None
        assert probe.outcome == ('failed' if damage == 'failed' else 'passed') and probe.boot_id == boot
        assert probe.gpu_id == (7 if damage == 'reported_gpu' else 0)
        assert probe.observed_at == result.observed_at
        assert release.state == 'verified'  # Native failure does not erase installed assets.
        assert probe.release_sha256 == digest and probe.image_sha256 == 'c' * 64
        assert result.scientific_ready is False
        projection = mi.project_native_readiness(release, current=True, critical_ready=True, boot=boot)
        assert projection.state == ('blocked' if damage == 'failed' else 'unverified') and projection.probe == probe
        assert mi.project_native_readiness(release, current=True, critical_ready=True, boot=uuid.uuid4()).probe is None
    assert len(calls) == (2 if damage == 'reported_gpu' else 1)
    _, argv, kwargs = calls[-1]
    device = 'GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee' if damage == 'reported_gpu' else '0'
    assert argv == ['apptainer', 'exec', '--nv', '--env', 'CUDA_DEVICE_ORDER=PCI_BUS_ID',
                    '--env', f'CUDA_VISIBLE_DEVICES={device}', '--writable-tmpfs',
                    '/worker/cache/runtime-images/objects/sha256/' + 'c' * 64 + '/runtime.sif', 'python3', '-']
    assert kwargs == dict(timeout=120, input_bytes=(source / 'scripts/check_rfantibody_runtime.py').read_bytes())


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
