"""Selected preparation through native owners and real offline cache transport."""
import json
import os
from itertools import combinations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, func

import model_registry
from database import Job, ExecutionTarget, get_session
from routers.execution_targets import router
from services.remote_execution import cache, preloading
from services.remote_execution.contracts import WorkflowPackSelection
from test_remote_independent_provisioning import assets
from test_remote_preloading import store, settle
from test_remote_cache_integration import local_transport


def refs(ids=None):
    return model_registry.workflow_pack_dependencies('structure_prediction', ids)


def keys(deps):
    return {(item.kind, item.relative_path) for item in deps}


def test_every_structure_subset_native_closure_and_full_compatibility():
    components = model_registry.workflow_pack_components('structure_prediction')
    ids = [item['model_id'] for item in components]
    assert {item['model_id'] for item in components if item['selected_by_default']} == {'protenix', 'frustrampnn'}
    assert keys(refs(ids)) == keys(refs())
    for count in range(len(ids) + 1):
        for subset in combinations(ids, count):
            actual = refs(subset)
            expected = set().union(*(keys(refs([name])) for name in subset))
            assert keys(actual) == expected
            assert len(actual) == len(expected)
            assert not any('mmcif' in item.relative_path or 'search_database' in item.relative_path for item in actual)
    assert keys(refs(['frustrampnn'])) == keys(model_registry.model_runtime_dependencies('frustrampnn', internal=True))
    assert keys(refs(['boltz_cp_experimental'])) == keys(model_registry.model_runtime_dependencies('boltz_cp_experimental'))
    assert not any('esm' in item.relative_path for item in refs(['protenix', 'frustrampnn']))
    with pytest.raises(ValueError, match='Unknown workflow'):
        WorkflowPackSelection(kind='workflow_pack', workflow_id='structure_prediction', component_ids=['made_up'])


@pytest.fixture
def pack_assets(assets, monkeypatch):
    for ref in refs():
        path = (assets[0] if ref.kind == 'image' else assets[1]) / ref.relative_path
        if ref.relative_path in {'boltz', 'esmfold2'}:
            path /= 'fixture.bin'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('inert selected preparation ' + ref.relative_path).encode())
    # Frustra's central pin is import-bound, unlike conventional image roots.
    # Keep the real identity/path validator with an explicitly inert private pin.
    import hashlib
    from dataclasses import replace
    from services.frustrampnn import runtime
    path = assets[0] / 'frustrampnn.sif'
    monkeypatch.setattr(runtime, 'FRUSTRAMPNN_RUNTIME_IDENTITY', replace(
        runtime.FRUSTRAMPNN_RUNTIME_IDENTITY, configured_sif_path=str(path),
        sif_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    return assets


def test_selected_layouts_never_publish_partial_combined_consumers(pack_assets):
    selection = WorkflowPackSelection(kind='workflow_pack', workflow_id='structure_prediction', component_ids=['protenix', 'frustrampnn'])
    entries = cache.independent_plan(selection, publication={})
    groups = cache.workflow_pack_weight_layouts(selection, entries)
    assert set(groups) == {'protenix', 'protenix_anchored'}
    assert all(groups.values())
    assert not any('esm' in entry.remote_destination or 'boltz' in entry.remote_destination for entry in entries)


@pytest.mark.asyncio
async def test_catalog_preview_start_readback_exact_subset(store, pack_assets, local_transport, tmp_path):
    async with store() as session:
        await session.execute(delete(Job))
        target = await session.get(ExecutionTarget, 'vast:1')
        target.remote_root = str(tmp_path / 'worker')
        await session.commit()
    controller = preloading.PreloadController(store)
    app = FastAPI()
    app.include_router(router, prefix='/api/execution-targets')
    app.state.preload_controller = controller
    async def sessions():
        async with store() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    prefix = '/api/execution-targets/vast:1'
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            catalog = (await client.get('/api/execution-targets/provision/catalog')).json()
            pack = next(item for item in catalog if item.get('workflow_id') == 'structure_prediction')
            assert pack['components'] == list(model_registry.workflow_pack_components('structure_prediction'))
            selection = {key: pack[key] for key in ('kind', 'workflow_id')}
            selection['component_ids'] = sorted(item['model_id'] for item in pack['components'] if item['selected_by_default'])
            schema = app.openapi()['components']['schemas']['WorkflowPackSelection']
            assert 'component_ids' in schema['properties']
            full = (await client.post(prefix + '/provision/preview', json={key: selection[key] for key in ('kind', 'workflow_id')})).json()
            response = await client.post(prefix + '/provision/preview', json=selection)
            assert response.status_code == 200, response.text
            preview = response.json()
            assert preview['selection'] == selection
            assert not preview['blockers'] and preview['effective_params'] is None
            assert preview['total_bytes'] < full['total_bytes']
            assert preview['total_bytes'] == sum(item['size_bytes'] for item in preview['artifacts'])
            assert not any('esm' in item['name'] or 'boltz' in item['name'] for item in preview['artifacts'])
            request = {**selection, 'preview_sha256': preview['preview_sha256']}
            # Selection is bound at start, including an explicitly empty list.
            for changed in ([], ['esmfold2'], None):
                rejected = await client.post(prefix + '/provision', json={**request, 'component_ids': changed})
                assert rejected.status_code == 409, rejected.text
            assert not local_transport[0] and not local_transport[1]
            response = await client.post(prefix + '/provision', json=request)
            assert response.status_code == 202, response.text
            await settle(controller)
            inventory = (await client.get(prefix + '/artifact-inventory')).json()
            assert inventory['state'] == 'download_verified'
            assert inventory['selection'] == selection
            assert inventory['artifacts'] == preview['artifacts']
            # A later disjoint preparation must not remove previously downloaded bytes.
            other = {**selection, 'component_ids': ['esmfold2']}
            other_preview = (await client.post(prefix + '/provision/preview', json=other)).json()
            started = await client.post(prefix + '/provision', json={**other, 'preview_sha256': other_preview['preview_sha256']})
            assert started.status_code == 202, started.text
            await settle(controller)
            other_inventory = (await client.get(prefix + '/artifact-inventory')).json()
            assert other_inventory['state'] == 'download_verified'
            assert other_inventory['selection'] == other
            assert other_inventory['artifacts'] == other_preview['artifacts']
            empty = (await client.post(prefix + '/provision/preview', json={**selection, 'component_ids': []})).json()
            assert empty['selection']['component_ids'] == [] and empty['artifacts'] == []
            for artifact in inventory['artifacts']:
                digest = artifact['sha256']
                obj = (tmp_path / 'worker/cache/runtime-images/objects/sha256' / digest / 'runtime.sif'
                       if artifact['name'].endswith('.sif') else
                       tmp_path / 'worker/cache/artifacts/v1/objects/sha256' / digest[:2] / digest)
                assert obj.exists()
            async with store() as session:
                assert await session.scalar(select(func.count()).select_from(Job)) == 0
            # Optional actual mounted Axios payloads are replayed through this same API.
            wire = os.environ.get('BMS_PROVISION_COMPONENT_WIRE')
            if wire:
                for body in json.loads(open(wire).read()):
                    body = {key: value for key, value in body.items() if key != 'preview_sha256'}
                    response = await client.post(prefix + '/provision/preview', json=body)
                    assert response.status_code == 200, response.text
                    actual = response.json()
                    assert actual['selection'].get('component_ids') == body.get('component_ids')
                    expected = {(kind, ('containers/' if kind == 'image' else 'weights/') + name)
                                for kind, name in keys(refs(body.get('component_ids')))}
                    assert {(item['kind'], item['name']) for item in actual['dependencies']} == expected
    finally:
        await controller.close()
