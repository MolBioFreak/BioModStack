"""Saved historical inventories: actual SQLite JSON projections and ASGI wire."""
import copy
from datetime import datetime, timedelta, timezone
import json
import uuid

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event

from database import ExecutionTarget, get_session
from routers.execution_targets import router
from services.remote_execution import managed_inventory as mi
from test_remote_preloading import store


@pytest_asyncio.fixture
async def compact(store):
    app = FastAPI()
    app.include_router(router, prefix='/targets')
    async def sessions():
        async with store() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test/targets/') as client:
        yield client, store


async def seed(store, count=3):
    artifacts = [dict(name=f'weights/legacy/file-{i:06d}', sha256='d'*64,
                      size_bytes=i + 1, state='verified') for i in range(count)]
    manifest = dict(selection=dict(kind='image', model_id='protenix'),
                    source_revision='a'*40, source_tree='b'*40,
                    artifacts=[dict(name=a['name'], sha256=a['sha256'], size_bytes=a['size_bytes'], mode=0o644)
                               for a in artifacts])
    release = dict(selection=manifest['selection'], source_revision='a'*40, source_tree='b'*40,
                   release_sha256=mi.release_digest(manifest), state='verified', artifacts=artifacts)
    observed = dict(observed_at=datetime.now(timezone.utc).isoformat(), boot_id=str(uuid.uuid4()), releases=[release])
    async with store() as session:
        target = await session.get(ExecutionTarget, 'vast:1')
        metadata = copy.deepcopy(target.provider_metadata)
        metadata.update(managed_boot_id=observed['boot_id'], managed_inventory=dict(
            endpoint_sha256=mi.endpoint_digest(target), observation=observed, manifests=[manifest]))
        target.provider_metadata = metadata
        await session.commit()
    return release


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [3, 107449])
async def test_compact_summary_and_one_ordered_page_without_full_hydration(compact, monkeypatch, count):
    client, store = compact
    release = await seed(store, count)
    full = await client.get('/vast:1/runtime-inventory')
    assert full.status_code == 200 and len(full.json()['releases'][0]['artifacts']) == count
    expected_native = full.json()['releases'][0]['native_readiness']
    def forbidden(*args, **kwargs):
        pytest.fail('compact saved reader must not validate/hydrate full inventory')
    monkeypatch.setattr(mi, 'project_inventory', forbidden)
    monkeypatch.setattr(mi, 'validate_observation', forbidden)
    monkeypatch.setattr(mi.ManagedInventory, 'model_validate', forbidden)
    from sqlalchemy.orm import Session
    def forbid_target_load(session, target):
        if isinstance(target, ExecutionTarget):
            forbidden()
    event.listen(Session, 'loaded_as_persistent', forbid_target_load)
    try:
        response = await client.get('/vast:1/runtime-inventory/summary')
        assert response.status_code == 200, response.text
        summary = response.json()
        assert summary['state'] == 'current'
        assert summary['releases'][0]['artifact_count'] == count
        assert summary['releases'][0]['native_readiness'] == expected_native
        assert 'artifacts' not in summary['releases'][0]
        assert len(response.content) < 3000
        params = dict(observation_id=summary['observation_id'], release_sha256=release['release_sha256'])
        page = await client.get('/vast:1/runtime-inventory/artifacts', params=params | dict(offset=1, limit=2))
        assert page.status_code == 200, page.text
        assert page.json()['total'] == count
        assert [a['name'] for a in page.json()['artifacts']] == ['weights/legacy/file-000001', 'weights/legacy/file-000002']
        assert len(page.content) < 1000
        print(json.dumps({'fixture_artifacts': count, 'full_bytes': len(full.content),
                          'summary_bytes': len(response.content), 'page_2_bytes': len(page.content)}))
        assert (await client.get('/vast:1/runtime-inventory/artifacts', params=params | dict(limit=251))).status_code == 422
        assert (await client.get('/vast:1/runtime-inventory/artifacts', params=params | dict(offset=-1))).status_code == 422
        assert (await client.get('/vast:1/runtime-inventory/artifacts', params=params | dict(observation_id='0'*64))).status_code == 409
        assert (await client.get('/vast:1/runtime-inventory/artifacts', params=params | dict(release_sha256='0'*64))).status_code == 409
        assert (await client.get('/vast:1/runtime-inventory/artifacts', params=params | dict(offset=count))).json()['artifacts'] == []
    finally:
        event.remove(Session, 'loaded_as_persistent', forbid_target_load)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['age', 'future', 'endpoint', 'boot', 'provider', 'failed', 'inactive', 'state'])
async def test_freshness_matches_full_reader(compact, change):
    client, store = compact
    await seed(store)
    async with store() as session:
        target = await session.get(ExecutionTarget, 'vast:1')
        metadata = copy.deepcopy(target.provider_metadata)
        if change in {'age', 'future'}:
            metadata['managed_inventory']['observation']['observed_at'] = (
                datetime.now(timezone.utc) + timedelta(hours=1 if change == 'future' else -1)).isoformat()
        if change == 'endpoint': target.host = 'changed'
        if change == 'boot': metadata['managed_boot_id'] = str(uuid.uuid4())
        if change == 'provider': metadata['inventory']['running'] = False
        if change == 'failed': metadata['managed_inventory']['refresh_failed'] = True
        if change == 'inactive': target.active = False
        if change == 'state': target.state = 'discovered'
        target.provider_metadata = metadata
        await session.commit()
    full = (await client.get('/vast:1/runtime-inventory')).json()
    summary = (await client.get('/vast:1/runtime-inventory/summary')).json()
    assert full['state'] == summary['state'] == 'stale'
    assert full['critical_runtime_ready'] == summary['critical_runtime_ready']
    assert full['blockers'] == summary['blockers']
    assert full['releases'][0]['native_readiness'] == summary['releases'][0]['native_readiness']


@pytest.mark.asyncio
async def test_optional_native_damage_identity_change_and_absence(compact):
    client, store = compact
    assert (await client.get('/vast:1/runtime-inventory/summary')).json() is None
    assert (await client.get('/missing/runtime-inventory/summary')).status_code == 404
    release = await seed(store)
    before = (await client.get('/vast:1/runtime-inventory/summary')).json()
    async with store() as session:
        target = await session.get(ExecutionTarget, 'vast:1')
        metadata = copy.deepcopy(target.provider_metadata)
        metadata['managed_inventory']['observation']['releases'][0]['native_readiness'] = {'probe': 'malformed'}
        target.provider_metadata = metadata
        await session.commit()
    after = (await client.get('/vast:1/runtime-inventory/summary')).json()
    assert after == before
    await seed(store)
    assert (await client.get('/vast:1/runtime-inventory/artifacts', params=dict(
        observation_id=before['observation_id'], release_sha256=release['release_sha256']))).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['passed', 'failed', 'unbound'])
async def test_compact_native_projection_preserves_only_bound_optional_probe(compact, outcome):
    client, store = compact
    await seed(store)
    async with store() as session:
        target = await session.get(ExecutionTarget, 'vast:1')
        metadata = copy.deepcopy(target.provider_metadata)
        observation = metadata['managed_inventory']['observation']
        release = observation['releases'][0]
        release['selection'] = dict(kind='model', model_id='rfantibody')
        # Historical RFantibody probes remain displayable even where its current
        # independent closure is unavailable; this does not enable the model.
        release['artifacts'] = [dict(name='containers/rfantibody.sif', sha256='d'*64,
                                     size_bytes=1, state='verified')]
        release['native_readiness'] = dict(state='unverified', authority='historical', probe=dict(
            authority='scripts/check_rfantibody_runtime.py:run_preflight', outcome='failed' if outcome == 'failed' else 'passed',
            release_sha256=release['release_sha256'], image_sha256='d'*64, script_sha256='e'*64,
            source_revision=release['source_revision'], source_tree=release['source_tree'],
            boot_id=str(uuid.uuid4()) if outcome == 'unbound' else observation['boot_id']))
        expected = mi.project_native_readiness(mi.ManagedRelease.model_validate(release), current=True,
            critical_ready=False, boot=uuid.UUID(observation['boot_id'])).model_dump(mode='json')
        target.provider_metadata = metadata
        await session.commit()
    summary = (await client.get('/vast:1/runtime-inventory/summary')).json()
    assert summary['releases'][0]['native_readiness'] == expected
    assert bool(expected['probe']) == (outcome != 'unbound')
    assert summary['scientific_ready'] is False
