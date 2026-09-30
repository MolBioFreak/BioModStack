"""Real SQLite/ASGI regression for bounded target status and retained readers."""
import asyncio
import copy
import hashlib
import json
from datetime import datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event

from database import ExecutionTarget, get_session
from routers.execution_targets import router
from services.remote_execution import targets, preloading, telemetry
from services.remote_execution.contracts import PreloadProgress
from test_remote_preloading import store


@pytest.fixture
def forbid_full_target_load(request):
    def forbid(target, context):
        pytest.fail('Routine read loaded the complete ExecutionTarget ORM/JSON record')
    def install():
        event.listen(ExecutionTarget, 'load', forbid)
        request.addfinalizer(lambda: event.remove(ExecutionTarget, 'load', forbid))
    return install


def record(index):
    return dict(name=f'weights/member-{index:06d}.bin', sha256=f'{index:064x}', size_bytes=index + 1)


async def seed(store, count, *, published=False, phase='source_download_ready'):
    now = datetime.utcnow().isoformat()
    rows = [record(index) for index in range(count)]
    states = [dict(row, state='verified' if index % 2 else 'pending') for index, row in enumerate(rows)]
    selection = dict(kind='model', model_id='protenix')
    async with store() as session:
        target = await session.get(ExecutionTarget, 'vast:1')
        metadata = copy.deepcopy(target.provider_metadata)
        metadata['preload'] = dict(operation_id='op', selection=selection, sequence=8,
            source_revision='a'*40, source_tree='b'*40, request_sha256='c'*64,
            phase=phase, message='Download observation', started_at=now, updated_at=now,
            artifacts=rows, artifact_progress=states)
        metadata['artifact_inventory'] = dict(operation_id='op', selection=selection,
            observed_at=now, artifacts=rows, endpoint_sha256=hashlib.sha256(json.dumps(
                (target.host, target.port, target.username, target.remote_root, target.host_key_sha256)).encode()).hexdigest())
        if published:
            metadata['preload_artifact_summary'] = targets.artifact_summary(metadata['preload'])
            metadata['preload_cached_artifact_count'] = count
        target.provider_metadata = metadata
        target.activated_at = datetime.utcnow()
        target.capabilities = dict(gpu_count=4, gpu_name='GPU', provider_verified=False, critical_runtime={'state': 'verified', 'files': rows, 'artifacts': rows},
            readiness={'architecture': 'x86_64', 'gpus': ['0, gpu, GPU, 100'], 'container_backend': 'udocker', 'cuda_container_verified': True},
            critical_runtime_binding={'release_sha256': 'd'*64, 'paths': {}, 'sha256': {}, 'environment': {}},
            managed_manifest={'artifacts': rows})
        await session.commit()
    return metadata


def app_for(store):
    app = FastAPI()
    app.include_router(router, prefix='/execution-targets')
    async def sessions():
        async with store() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize('published', [False, True])
@pytest.mark.parametrize('count', [1, 10000, 89115])
async def test_large_status_is_compact_without_bulk_python_decode_or_receipt_validation(store, monkeypatch, published, count, forbid_full_target_load):
    await seed(store, count, published=published)
    forbid_full_target_load()
    original_loads = json.loads
    decoded_sizes = []
    def bounded_loads(value, *args, **kwargs):
        if isinstance(value, (str, bytes)):
            decoded_sizes.append(len(value))
            assert len(value) < 20000, 'Routine read decoded a bulk JSON collection'
        return original_loads(value, *args, **kwargs)
    def forbidden(cls, value):
        pytest.fail('Routine status validated a retained artifact receipt')
    from services.remote_execution import contracts
    # Nested receipt validation invokes this global from the compiled validator.
    def forbidden_receipt_path(*args, **kwargs):
        pytest.fail('Routine status invoked the full receipt path validator')
    monkeypatch.setattr(contracts, '_clean_relative_posix_path', forbidden_receipt_path)
    monkeypatch.setattr(PreloadProgress, 'model_validate', classmethod(forbidden))
    from services.remote_execution.contracts import ObservedArtifactInventory
    monkeypatch.setattr(ObservedArtifactInventory, 'model_validate', classmethod(forbidden))
    monkeypatch.setattr(json, 'loads', bounded_loads)
    async with AsyncClient(transport=ASGITransport(app=app_for(store)), base_url='http://test') as client:
        for _ in range(2):
            listing = await client.get('/execution-targets')
            assert listing.status_code == 200, listing.text
            assert len(listing.content) < 5000
            result = listing.json()[0]
            verified_sizes = [index + 1 for index in range(count) if index % 2]
            assert result['preload']['artifact_summary'] == dict(total_count=count, verified_count=len(verified_sizes),
                total_bytes=sum(range(1, count + 1)), verified_bytes=sum(verified_sizes))
            assert result['preload']['cached_artifact_count'] == count
            assert result['artifact_inventory']['artifact_count'] == count
            assert 'artifacts' not in result['preload'] and 'artifact_progress' not in result['preload']
            assert result['capabilities']['critical_runtime'] == {'state': 'verified'}
            assert result['capabilities']['provider_verified'] is False
            assert result['capabilities']['readiness']['cuda_container_verified'] is True
            assert result['capabilities']['readiness']['gpus'] == ['0, gpu, GPU, 100']
            direct = await client.get('/execution-targets/vast:1')
            assert direct.json() == result
            sample = await client.get('/execution-targets/active/telemetry?execution_target_id=vast:1')
            assert sample.json()['target'] == result
    assert decoded_sizes and max(decoded_sizes) < 20000


@pytest.mark.asyncio
async def test_full_details_and_pages_keep_exact_order_counts_and_observation(store):
    original = await seed(store, 317)
    async with AsyncClient(transport=ASGITransport(app=app_for(store)), base_url='http://test') as client:
        root = '/execution-targets/vast:1'
        detail = (await client.get(root + '/details')).json()
        assert detail['preload']['artifacts'] == original['preload']['artifacts']
        assert detail['preload']['artifact_progress'] == original['preload']['artifact_progress']
        assert detail['capabilities']['critical_runtime']['files'] == original['preload']['artifacts']
        full = (await client.get(root + '/artifact-inventory')).json()
        assert full['artifacts'] == original['artifact_inventory']['artifacts']
        assert full['state'] == 'download_verified'
        for endpoint, collection in [(root+'/artifact-inventory/artifacts', 'cached'),
                                    (root+'/preload/op/artifacts?collection=cached', 'cached'),
                                    (root+'/preload/op/artifacts?collection=progress', 'progress')]:
            combined = []
            for offset in [0, 100, 200, 300, 400]:
                separator = '&' if '?' in endpoint else '?'
                response = await client.get(endpoint + f'{separator}offset={offset}')
                assert response.status_code == 200, response.text
                page = response.json()
                assert (page['total_count'], page['offset'], page['limit'], page['operation_id']) == (317, offset, 100, 'op')
                if '/preload/' in endpoint:
                    assert page['sequence'] == 8
                combined.extend(page['items'])
            expected = original['preload']['artifact_progress' if collection == 'progress' else 'artifacts']
            assert combined == expected
        for suffix in ['?offset=-1', '?limit=0', '?limit=251', '?limit=1.5']:
            assert (await client.get(root+'/artifact-inventory/artifacts'+suffix)).status_code == 422
        assert (await client.get(root+'/preload/op/artifacts?collection=other')).status_code == 422
        assert (await client.get(root+'/preload/old/artifacts')).status_code == 404
        assert (await client.get('/execution-targets/missing/artifact-inventory/artifacts')).status_code == 404
    async with store() as session:
        assert (await session.get(ExecutionTarget, 'vast:1')).provider_metadata == original


@pytest.mark.asyncio
async def test_publication_aggregates_are_atomic_and_do_not_alter_full_progress(store):
    await seed(store, 6, phase='transferring')
    async with store() as session:
        target = await targets.get_target(session, 'vast:1')
        progress = PreloadProgress.model_validate(target.provider_metadata['preload'])
        progress.artifact_progress[0].state = 'verified'
        controller = preloading.PreloadController(store)
        await controller._publish(session, 'vast:1', progress)
    async with store() as session:
        target = await targets.get_target(session, 'vast:1')
        assert target.provider_metadata['preload']['sequence'] == 9
        assert target.provider_metadata['preload_artifact_summary'] == dict(total_count=6, verified_count=4,
            total_bytes=21, verified_bytes=13)
        assert target.provider_metadata['preload_cached_artifact_count'] == 6
        assert target.provider_metadata['preload']['artifact_progress'][0]['state'] == 'verified'
        assert len(target.provider_metadata['preload']['artifacts']) == 6
        assert (await targets.target_status(session, 'vast:1')).preload.artifact_summary.verified_bytes == 13


@pytest.mark.asyncio
async def test_sampler_uses_scalar_snapshots_without_bulk_decode(store, monkeypatch, forbid_full_target_load):
    await seed(store, 10000)
    forbid_full_target_load()
    original_loads = json.loads
    def bounded_loads(value, *args, **kwargs):
        assert len(value) < 20000
        return original_loads(value, *args, **kwargs)
    monkeypatch.setattr(json, 'loads', bounded_loads)
    sampled = asyncio.Event()
    stop = asyncio.Event()
    sampler = telemetry.RemoteTelemetry()
    async def collect(target, entry):
        assert target.capabilities['critical_runtime_binding']['release_sha256'] == 'd'*64
        assert not hasattr(target, '_sa_instance_state')
        assert 'preload' not in target.provider_metadata
        assert 'json_each' not in str(targets.status_query(include_observations=False))
        assert targets.telemetry_eligible(target)
        sampled.set()
        stop.set()
    monkeypatch.setattr(sampler, 'collect', collect)
    task = asyncio.create_task(sampler.run(store, stop))
    await asyncio.wait_for(sampled.wait(), timeout=10)
    await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['expired', 'future', 'endpoint', 'operation', 'phase', 'bad_time', 'nonobject'])
async def test_compact_inventory_preserves_endpoint_and_freshness_without_writes(store, case):
    metadata = await seed(store, 3)
    async with store() as session:
        target = await targets.get_target(session, 'vast:1')
        raw = copy.deepcopy(metadata)
        if case == 'expired': raw['artifact_inventory']['observed_at'] = '2000-01-01T00:00:00'
        elif case == 'future': raw['artifact_inventory']['observed_at'] = '2100-01-01T00:00:00'
        elif case == 'endpoint': raw['artifact_inventory']['endpoint_sha256'] = '0'*64
        elif case == 'operation': raw['artifact_inventory']['operation_id'] = 'older'
        elif case == 'phase': raw['preload']['phase'] = 'failed'
        elif case == 'bad_time': raw['artifact_inventory']['observed_at'] = 'invalid'
        elif case == 'nonobject': raw['artifact_inventory'] = ['invalid']
        target.provider_metadata = raw
        await session.commit()
    async with store() as session:
        status = await targets.target_status(session, 'vast:1')
        if case in {'bad_time', 'nonobject'}:
            assert status.artifact_inventory is None
        else:
            assert status.artifact_inventory.state == 'stale'
        assert (await targets.get_target(session, 'vast:1')).provider_metadata == raw


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [0, 7])
async def test_legacy_cached_only_and_empty_progress_summary(store, count):
    metadata = await seed(store, count)
    async with store() as session:
        target = await targets.get_target(session, 'vast:1')
        raw = copy.deepcopy(metadata)
        raw['preload'].pop('artifact_progress')
        raw['preload'].pop('sequence')
        target.provider_metadata = raw
        await session.commit()
    async with store() as session:
        status = await targets.target_status(session, 'vast:1')
        assert status.preload.sequence == 0
        assert status.preload.artifact_summary.model_dump() == dict(total_count=count,
            verified_count=count, total_bytes=sum(range(1, count + 1)), verified_bytes=sum(range(1, count + 1)))
        page = await targets.artifact_page(session, 'vast:1', operation_id='op', collection='progress')
        assert page.items == [] and page.total_count == 0 and page.sequence == 0


@pytest.mark.asyncio
async def test_unlisted_history_is_not_projected_and_existing_empty_fleet_semantics_remain(store, monkeypatch):
    await seed(store, 10000)
    async with store() as session:
        target = await targets.get_target(session, 'vast:1')
        target.active = False
        raw = copy.deepcopy(target.provider_metadata)
        raw['inventory']['present'] = False
        raw['preload']['message'] = 'x' * 1000000  # Hidden history cannot break the visible fleet.
        target.provider_metadata = raw
        await session.commit()
    original_loads = json.loads
    def bounded_loads(value, *args, **kwargs):
        assert len(value) < 20000
        return original_loads(value, *args, **kwargs)
    monkeypatch.setattr(json, 'loads', bounded_loads)
    async with store() as session:
        assert await targets.list_targets(session) == []


@pytest.mark.asyncio
async def test_scientific_selection_is_exact_in_status_details_and_invalid_receipt_is_read_only(store):
    metadata = await seed(store, 2)
    selection = dict(kind='workflow', workflow_request=dict(name='Retained native request',
        model_id='protenix', mode='predict', params={'sequence': 'ACDE', 'seed': 17, 'protenix_use_msa': False}))
    async with store() as session:
        target = await targets.get_target(session, 'vast:1')
        raw = copy.deepcopy(metadata)
        raw['preload']['selection'] = raw['artifact_inventory']['selection'] = selection
        raw['artifact_inventory']['artifacts'] = copy.deepcopy(raw['artifact_inventory']['artifacts'])
        raw['artifact_inventory']['artifacts'][0]['name'] = '../escape'
        target.provider_metadata = raw
        await session.commit()
    async with AsyncClient(transport=ASGITransport(app=app_for(store)), base_url='http://test') as client:
        root = '/execution-targets/vast:1'
        status = (await client.get(root)).json()
        details = (await client.get(root+'/details')).json()
        assert status['preload']['selection'] == details['preload']['selection']
        assert status['preload']['selection']['workflow_request']['params'] == selection['workflow_request']['params']
        assert status['artifact_inventory']['artifact_count'] == 2
        assert (await client.get(root+'/artifact-inventory')).json() is None
        response = await client.get(root+'/artifact-inventory/artifacts')
        assert response.status_code == 404
        assert response.json()['detail'] == 'Stored artifact page is invalid'
        # A later valid page remains readable: no whole-collection validation.
        page = (await client.get(root+'/artifact-inventory/artifacts?offset=1&limit=1')).json()
        assert page['total_count'] == 2 and page['items'] == [record(1)]
    async with store() as session:
        assert (await targets.get_target(session, 'vast:1')).provider_metadata == raw


@pytest.mark.asyncio
async def test_absent_inventory_page_is_empty_without_worker_action(store):
    async with store() as session:
        page = await targets.artifact_page(session, 'vast:1')
        assert page.model_dump() == dict(items=[], total_count=0, offset=0, limit=100, operation_id=None)


@pytest.mark.asyncio
async def test_legacy_missing_progress_state_does_not_claim_verification(store):
    metadata = await seed(store, 2)
    async with store() as session:
        target = await targets.get_target(session, 'vast:1')
        raw = copy.deepcopy(metadata)
        raw['preload']['artifact_progress'][0].pop('state')
        target.provider_metadata = raw
        await session.commit()
    async with store() as session:
        status = await targets.target_status(session, 'vast:1')
        assert status.preload.artifact_summary.model_dump() == dict(
            total_count=2, verified_count=1, total_bytes=3, verified_bytes=2)
        with pytest.raises(targets.ExecutionTargetError, match='Stored artifact page is invalid'):
            await targets.artifact_page(session, 'vast:1', operation_id='op', collection='progress')
        assert (await targets.get_target(session, 'vast:1')).provider_metadata == raw
