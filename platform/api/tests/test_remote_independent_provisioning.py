"""Real offline cache helper behind mounted API; no SSH/provider/scientific calls."""
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, func

from database import Job, ExecutionTarget, get_session
from routers.execution_targets import router
from services.remote_execution import cache, bundle, preloading
from services.remote_execution.contracts import ProvisionSelection
from test_remote_preloading import store, settle
from test_remote_cache_integration import local_transport


@pytest.fixture
def assets(tmp_path, monkeypatch):
    import paths
    containers, weights = tmp_path / 'containers', tmp_path / 'weights'
    containers.mkdir()
    (weights / 'protenix').mkdir(parents=True)
    (containers / 'protenix.sif').write_bytes(b'controlled test image')
    (weights / 'protenix' / 'model.pt').write_bytes(b'controlled test weights')
    monkeypatch.setattr(paths, 'get_container_dir', lambda: containers)
    monkeypatch.setattr(paths, 'get_weights_root', lambda: weights)
    # Imported readers must share the same conventional installation root.
    # Clear ambient selectors; explicit arbitrary paths are not retained authority.
    from services.remote_execution.images import IMAGE_SELECTORS
    for _, selector in IMAGE_SELECTORS.values():
        monkeypatch.delenv(selector, raising=False)
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(tmp_path / 'image-store'))
    data = tmp_path / 'data'
    monkeypatch.setenv('BMS_DATA', str(data))
    monkeypatch.setenv('BMS_STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setenv('HF_HOME', str(tmp_path / 'hf'))
    for module in (paths, bundle, cache):
        monkeypatch.setattr(module, 'get_data_root', lambda: data)
    monkeypatch.setattr(bundle, 'get_container_dir', lambda: containers)
    monkeypatch.setattr(bundle, 'get_weights_root', lambda: weights)
    identity = ('a'*40, 'b'*40)
    monkeypatch.setattr(cache, 'current_source_identity', lambda *args: identity)
    monkeypatch.setattr(preloading, 'current_source_identity', lambda *args: identity)
    # Deterministic inert source at the archive-owner seam, not a fake ingestion
    # receipt: real transport and expected-byte verification remain exercised.
    def archive(repo, data_root, revision, destination, *, extract=False):
        import gzip, io, tarfile
        assert revision == identity[0] and not extract
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as tar:
            member = tarfile.TarInfo('fixture.txt')
            payload = b'inert offline source fixture'
            member.size = len(payload)
            tar.addfile(member, io.BytesIO(payload))
        payload = gzip.compress(stream.getvalue(), mtime=0)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / '.bms-source.tar.gz').write_bytes(payload)
        return hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(cache, '_staged_source_archive', archive)
    return containers, weights


@pytest.mark.asyncio
async def test_mounted_independent_readback_reuse_corruption_and_staleness(store, assets, local_transport, tmp_path):
    async with store() as s:
        await s.execute(delete(Job))
        target = await s.get(ExecutionTarget, 'vast:1')
        target.remote_root = str(tmp_path / 'worker')
        await s.commit()
    controller = preloading.PreloadController(store)
    app = FastAPI()
    app.include_router(router, prefix='/execution-targets')
    app.state.preload_controller = controller
    async def sessions():
        async with store() as s:
            yield s
    app.dependency_overrides[get_session] = sessions
    calls, uploads = local_transport
    prefix = '/execution-targets/vast:1'
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        catalog = (await client.get('/execution-targets/provision/catalog')).json()
        assert {'kind':'model','model_id':'protenix'} in catalog
        assert {'kind':'model','model_id':'boltz2'} in catalog
        assert (await client.get(prefix + '/artifact-inventory')).json() is None
        selection = {'kind':'model','model_id':'protenix'}
        for bad in ({**selection, 'url':'https://example.invalid'}, {**selection, 'path':'/etc/passwd'}):
            assert (await client.post(prefix + '/provision/preview', json=bad)).status_code == 422
        blocked = await client.post(prefix + '/provision/preview', json={'kind':'model','model_id':'boltz2'})
        assert blocked.status_code == 200
        assert blocked.json()['blockers']
        response = await client.post(prefix + '/provision/preview', json=selection)
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview['scientific_ready'] is False
        assert len(preview['artifacts']) == 2
        assert preview['total_bytes'] == sum(p.stat().st_size for p in [assets[0]/'protenix.sif', assets[1]/'protenix/model.pt'])
        assert not calls and not uploads
        request = {**selection, 'preview_sha256':preview['preview_sha256']}
        assert (await client.post(prefix + '/provision', json={**request,'preview_sha256':'0'*64})).status_code == 409
        for expected_uploads in (2, 2, 2):
            response = await client.post(prefix + '/provision', json=request)
            assert response.status_code == 202, response.text
            await settle(controller)
            inventory = (await client.get(prefix + '/artifact-inventory')).json()
            assert inventory is not None, (await client.get('/execution-targets')).text
            assert inventory['state'] == 'download_verified'
            assert inventory['scientific_ready'] is False
            assert inventory['artifacts'] == preview['artifacts']
            assert len(uploads) == expected_uploads
            for artifact in inventory['artifacts']:
                digest = artifact['sha256']
                obj = (tmp_path / 'worker/cache/runtime-images/objects/sha256' / digest / 'runtime.sif'
                       if artifact['name'].endswith('.sif') else
                       tmp_path / 'worker/cache/artifacts/v1/objects/sha256' / digest[:2] / digest)
                assert hashlib.sha256(obj.read_bytes()).hexdigest() == digest
            # A damaged published image fails reuse without remediation. Keep the
            # mounted failure/retained-inventory contract separate from silent bytes.
            image = next(a for a in inventory['artifacts'] if a['name'].endswith('.sif'))
            image_obj = tmp_path / 'worker/cache/runtime-images/objects/sha256' / image['sha256'] / 'runtime.sif'
            assert image_obj.stat().st_mode & 0o777 == 0o400
        image_obj.chmod(0o600)
        image_obj.write_bytes(b'damaged published image')
        response = await client.post(prefix + '/provision', json=request)
        assert response.status_code == 202, response.text
        await settle(controller)
        async with store() as s:
            target = await s.get(ExecutionTarget, 'vast:1')
            assert target.provider_metadata['preload']['phase'] == 'recovery_blocked'
        assert image_obj.read_bytes() == b'damaged published image'
        assert len(uploads) == 2
        assert (await client.get(prefix + '/artifact-inventory')).json()['artifacts'] == preview['artifacts']
        assert not (tmp_path / 'worker/attempts').exists()
        async with store() as s:
            assert await s.scalar(select(func.count()).select_from(Job)) == 0
            target = await s.get(ExecutionTarget,'vast:1')
            raw = dict(target.provider_metadata)
            raw['artifact_inventory'] = {**raw['artifact_inventory'], 'observed_at': (datetime.utcnow()-timedelta(hours=1)).isoformat()}
            target.provider_metadata = raw
            await s.commit()
        assert (await client.get(prefix + '/artifact-inventory')).json()['state'] == 'stale'
    await controller.close()


def test_explicit_image_selection_requires_real_retained_reference(assets, tmp_path, monkeypatch):
    import os
    from services.remote_execution.images import image_reference
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / 'scripts'))
    from publish_runtime_images import publish_references
    root = Path(os.environ['BMS_RUNTIME_IMAGE_STORE'])
    source = assets[0] / 'protenix.sif'
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setenv('BMS_PROTENIX_CONTAINER_PATH', str(source))
    with pytest.raises(ValueError, match='not a retained managed reference'):
        cache.independent_plan(ProvisionSelection(kind='image', model_id='protenix'))
    publish_references(root, 'development', {
        'BMS_PROTENIX_CONTAINER_PATH': {'source': str(source), 'sha256': digest}})
    selected = root / 'objects/sha256' / digest / 'runtime.sif'
    monkeypatch.setenv('BMS_PROTENIX_CONTAINER_PATH', str(selected))
    assert image_reference('protenix.sif', assets[0]) == (selected, digest)
    entries = cache.independent_plan(ProvisionSelection(kind='image', model_id='protenix'))
    assert len(entries) == 1 and entries[0].source == selected and entries[0].sha256 == digest
    selected.parent.chmod(0o700)
    selected.unlink()
    selected.symlink_to(source)
    selected.parent.chmod(0o500)
    with pytest.raises((ValueError, RuntimeError, OSError)):
        cache.independent_plan(ProvisionSelection(kind='image', model_id='protenix'))


def test_model_requires_weights_image_does_not_and_preview_binds_bytes(assets, tmp_path):
    from types import SimpleNamespace
    target = SimpleNamespace(id='one',host='worker',port=22,username='root',remote_root='/worker',host_key_sha256='c'*64)
    selection = ProvisionSelection(kind='model', model_id='protenix')
    first, entries = cache.independent_preview(selection, target)
    (assets[1]/'protenix/model.pt').write_bytes(b'changed')
    second, _ = cache.independent_preview(selection, target)
    assert second.preview_sha256 != first.preview_sha256
    (assets[1]/'protenix/model.pt').unlink()
    with pytest.raises(bundle.RemoteBundleError, match='empty'):
        cache.independent_plan(selection)
    assert len(cache.independent_plan(ProvisionSelection(kind='image',model_id='protenix'))) == 1
    (assets[0]/'protenix.sif').unlink()
    (assets[0]/'protenix.sif').symlink_to('/etc/passwd')
    with pytest.raises(ValueError, match='contained'):
        cache.independent_plan(ProvisionSelection(kind='image',model_id='protenix'))


@pytest.mark.asyncio
@pytest.mark.parametrize('corruption', ['bytes', 'size'])
async def test_cold_ingestion_refuses_wrong_bytes_or_size(assets, local_transport, tmp_path, monkeypatch, corruption):
    from types import SimpleNamespace
    import uuid
    import subprocess
    connection = SimpleNamespace(remote_root=str(tmp_path / 'worker'))
    entries = cache.independent_plan(ProvisionSelection(kind='image', model_id='protenix'))
    original = cache.rsync_to_remote
    async def corrupt_upload(connection, source, destination, **kwargs):
        await original(connection, source, destination, **kwargs)
        Path(destination).write_bytes(b'x' * (entries[0].size_bytes + (corruption == 'size')))
    monkeypatch.setattr(cache, 'rsync_to_remote', corrupt_upload)
    with pytest.raises(subprocess.CalledProcessError):
        await cache.provision_cache(connection=connection, entries=entries,
            operation_id=str(uuid.uuid4()), progress=cache._noop, check_fence=cache._noop)
    obj = tmp_path / 'worker/cache/runtime-images/objects/sha256' / entries[0].sha256 / 'runtime.sif'
    assert not obj.exists()


@pytest.mark.asyncio
async def test_readback_detects_post_ingest_corruption(assets, local_transport, tmp_path):
    # Warm reuse is not a recurring byte audit. Explicit maintenance is.
    from types import SimpleNamespace
    import uuid
    from tools import bms_artifact_cache as tool
    connection = SimpleNamespace(remote_root=str(tmp_path / 'worker'))
    entries = cache.independent_plan(ProvisionSelection(kind='image', model_id='protenix'))
    async def download():
        return await cache.provision_cache(connection=connection, entries=entries,
            operation_id=str(uuid.uuid4()), progress=cache._noop, check_fence=cache._noop)
    receipts = await download()
    store = tool.Cache(tmp_path / 'worker/cache/artifacts/v1')
    obj = store.image_store / 'objects/sha256' / entries[0].sha256 / 'runtime.sif'
    inode = obj.stat().st_ino
    obj.chmod(0o600)
    obj.write_bytes(b'x' * entries[0].size_bytes)
    obj.chmod(0o400)
    assert await download() == receipts
    assert obj.stat().st_ino == inode and obj.read_bytes() == b'x' * entries[0].size_bytes
    assert len(local_transport[1]) == 1
    with pytest.raises(RuntimeError, match='SHA-256'):
        tool.runtime_images().verify_image(obj, entries[0].sha256)


def test_launch_and_independent_resolve_identical_reviewed_assets(assets, monkeypatch, tmp_path):
    # Match the native Protenix checkpoint/common-member contract rather than a
    # generic model.pt that the actual selected workflow never consumes.
    (assets[1] / 'protenix/model.pt').unlink()
    for member in ('checkpoint/protenix-v2.pt', 'common/components.cif',
                   'common/components.cif.rdkit_mol.pkl',
                   'common/clusters-by-entity-40.txt', 'common/obsolete_release_date.csv'):
        path = assets[1] / 'protenix' / member
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('controlled native asset ' + member).encode())
    data = tmp_path / 'data'
    (data / 'runtime/cm-api-python/current').mkdir(parents=True)
    monkeypatch.delenv('BMS_CM_API_RUNTIME_DIR', raising=False)
    monkeypatch.setattr(bundle,'get_container_dir',lambda: assets[0])
    monkeypatch.setattr(bundle,'get_weights_root',lambda: assets[1])
    monkeypatch.setattr(bundle,'get_data_root',lambda: data)
    from component_runtime import SourceIdentity
    from services.nextflow import build_selected_execution_plan
    settings = {'pred_method': 'protenix', 'protenix_use_msa': False, 'run_frustrampnn': False}
    plan = build_selected_execution_plan(model_id='protenix', mode='predict',
        entrypoint='workflows/structure_prediction.nf', requested=settings,
        effective=settings, native_parameters={}, source_identity=SourceIdentity('a'*40, 'b'*40))
    launched = bundle._runtime_assets('protenix', 'predict', {}, selected_plan=plan)
    records = [r for path, prefix in launched if prefix != 'support-python'
               for r in bundle._records_for_source(path,prefix,'runtime')]
    provisioned = cache.independent_plan(ProvisionSelection(kind='model',model_id='protenix'))
    assert {(r.relative_path,r.sha256,r.size_bytes) for r in records} == {
        (r.remote_destination,r.sha256,r.size_bytes) for r in provisioned}


@pytest.mark.asyncio
async def test_caliby_catalog_and_default_prewarm_match_selected_launch(assets, monkeypatch):
    from routers.execution_targets import provision_catalog
    from component_runtime import SourceIdentity
    from services.nextflow import build_selected_execution_plan
    catalog = {(row.kind, row.model_id) for row in await provision_catalog()
               if row.kind in {'model', 'image'}}
    assert {('model', 'caliby_binder'), ('image', 'caliby_binder')} <= catalog
    # The current registry also advertises the retained experimental subtype.
    assert ('model', 'caliby_experimental') in catalog
    containers, weights = assets
    (containers / 'caliby.sif').write_bytes(b'fixture caliby image; not executable')
    checkpoint = weights / 'caliby/model_params/caliby/soluble_caliby_v1.ckpt'
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b'fixture selected checkpoint')
    # Unselected installed alternatives must not be scanned or provisioned.
    (checkpoint.parent / 'caliby.ckpt').write_bytes(b'unselected fixture checkpoint')
    (weights / 'caliby/model_params/af2').mkdir()
    (weights / 'caliby/model_params/af2/unselected').write_bytes(b'optional fixture')
    monkeypatch.setattr(bundle, 'get_container_dir', lambda: containers)
    monkeypatch.setattr(bundle, 'get_weights_root', lambda: weights)
    params = {'caliby_model_name': 'soluble_caliby_v1', 'caliby_run_self_consistency_eval': False}
    plan = build_selected_execution_plan(model_id='caliby_binder', mode='design',
        entrypoint='workflows/caliby_binder.nf', requested=params, effective=params,
        native_parameters=params, source_identity=SourceIdentity('a'*40, 'b'*40))
    launched = bundle._runtime_assets('caliby_binder', 'design', params, selected_plan=plan,
                                     only_kinds=frozenset({'image', 'weights'}))
    records = [r for path, prefix in launched for r in bundle._records_for_source(path, prefix, 'runtime')]
    provisioned = cache.independent_plan(ProvisionSelection(kind='model', model_id='caliby_binder'))
    assert {(r.relative_path, r.sha256, r.size_bytes) for r in records} == {
        (r.remote_destination, r.sha256, r.size_bytes) for r in provisioned}
    assert {r.remote_destination for r in provisioned} == {
        'containers/caliby.sif', 'weights/caliby/model_params/caliby/soluble_caliby_v1.ckpt'}
    checkpoint.unlink()
    with pytest.raises((ValueError, FileNotFoundError, bundle.RemoteBundleError)):
        cache.independent_plan(ProvisionSelection(kind='model', model_id='caliby_binder'))
    assert len(cache.independent_plan(ProvisionSelection(kind='image', model_id='caliby_binder'))) == 1


@pytest.mark.parametrize('path', ['/absolute', '../escape', 'nested/../escape', 'nested//member', 'nested/./member'])
def test_runtime_dependency_members_remain_contained(path):
    from model_registry import RuntimeDependencyRef
    with pytest.raises(ValueError):
        RuntimeDependencyRef(kind='weights', relative_path=path)
