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
    monkeypatch.setattr(cache, 'current_source_identity', lambda: ('a'*40, 'b'*40))
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
        assert all(item['model_id'] != 'boltz2' for item in catalog)
        assert (await client.get(prefix + '/artifact-inventory')).json() is None
        selection = {'kind':'model','model_id':'protenix'}
        for bad in ({**selection, 'url':'https://example.invalid'}, {**selection, 'path':'/etc/passwd'}):
            assert (await client.post(prefix + '/provision/preview', json=bad)).status_code == 422
        assert (await client.post(prefix + '/provision/preview', json={'kind':'model','model_id':'boltz2'})).status_code == 409
        response = await client.post(prefix + '/provision/preview', json=selection)
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview['scientific_ready'] is False
        assert len(preview['artifacts']) == 2
        assert preview['total_bytes'] == sum(p.stat().st_size for p in [assets[0]/'protenix.sif', assets[1]/'protenix/model.pt'])
        assert not calls and not uploads
        request = {**selection, 'preview_sha256':preview['preview_sha256']}
        assert (await client.post(prefix + '/provision', json={**request,'preview_sha256':'0'*64})).status_code == 409
        for expected_uploads in (2, 2, 3):
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
                obj = tmp_path / 'worker/cache/artifacts/v1/objects/sha256' / digest[:2] / digest
                assert hashlib.sha256(obj.read_bytes()).hexdigest() == digest
            if expected_uploads == 2 and len([r for r in calls if r['action'] == 'probe']) >= 4:
                obj.chmod(0o600)
                obj.write_bytes(b'corrupt')
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
async def test_readback_detects_post_ingest_corruption(assets, local_transport, tmp_path, monkeypatch):
    from types import SimpleNamespace
    import uuid
    connection = SimpleNamespace(remote_root=str(tmp_path / 'worker'))
    entries = cache.independent_plan(ProvisionSelection(kind='image', model_id='protenix'))
    original = cache._cache_artifacts
    async def corrupt_after_ingest(**kwargs):
        receipts = await original(**kwargs)
        digest = receipts[0]['sha256']
        obj = Path(connection.remote_root) / 'cache/artifacts/v1/objects/sha256' / digest[:2] / digest
        obj.chmod(0o600)
        obj.write_bytes(b'corrupt after ingest')
        return receipts
    monkeypatch.setattr(cache, '_cache_artifacts', corrupt_after_ingest)
    with pytest.raises(ValueError, match='verification failed'):
        await cache.provision_cache(connection=connection, entries=entries,
            operation_id=str(uuid.uuid4()), progress=cache._noop, check_fence=cache._noop)


def test_launch_and_independent_resolve_identical_reviewed_assets(assets, monkeypatch, tmp_path):
    data = tmp_path / 'data'
    (data / 'runtime/cm-api-python/current').mkdir(parents=True)
    monkeypatch.delenv('BMS_CM_API_RUNTIME_DIR', raising=False)
    monkeypatch.setattr(bundle,'get_container_dir',lambda: assets[0])
    monkeypatch.setattr(bundle,'get_weights_root',lambda: assets[1])
    monkeypatch.setattr(bundle,'get_data_root',lambda: data)
    launched = bundle._runtime_assets('protenix','predict',{})
    records = [r for path, prefix in launched if prefix != 'support-python'
               for r in bundle._records_for_source(path,prefix,'runtime')]
    provisioned = cache.independent_plan(ProvisionSelection(kind='model',model_id='protenix'))
    assert {(r.relative_path,r.sha256,r.size_bytes) for r in records} == {
        (r.remote_destination,r.sha256,r.size_bytes) for r in provisioned}
