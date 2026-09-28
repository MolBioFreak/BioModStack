"""Input-free one-click binder asset preparation; inert local fixtures only."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_registry import (model_runtime_dependencies, native_checkpoint_dependencies,
                            workflow_pack_dependencies, workflow_pack_weight_groups)
from services.remote_execution import cache
from services.remote_execution.contracts import WorkflowPackSelection
from test_remote_independent_provisioning import assets
from test_remote_preloading import store, settle
from test_remote_cache_integration import local_transport

SELECTION = WorkflowPackSelection(kind='workflow_pack', workflow_id='antibody_denovo')
TARGET = SimpleNamespace(id='one', host='worker', port=22, username='root',
                         remote_root='/worker', host_key_sha256='c' * 64)


@pytest.fixture
def binder_assets(assets, monkeypatch):
    containers, weights = assets
    import paths
    monkeypatch.setattr(paths, 'get_container_dir', lambda *args, **kwargs: containers)
    monkeypatch.setattr(paths, 'get_weights_root', lambda *args, **kwargs: weights)
    from subprocess import check_output
    source = Path(__file__).resolve().parents[3]
    identity = tuple(check_output(['git', '-C', str(source), 'rev-parse', ref], text=True).strip()
                     for ref in ('HEAD', 'HEAD^{tree}'))
    monkeypatch.setattr(cache, 'current_source_identity', lambda *args, **kwargs: identity)
    from services.remote_execution import preloading
    monkeypatch.setattr(preloading, 'current_source_identity', lambda *args, **kwargs: identity)
    # The central Frustra image owner points at a real 10 GiB SIF; isolate it
    # only for this inert transport fixture, not in production resolution.
    original = cache.resolve_image
    monkeypatch.setattr(cache, 'resolve_image', lambda name, root:
                        root / name if name == 'frustrampnn.sif' else original(name, root))
    for ref in workflow_pack_dependencies('antibody_denovo'):
        path = (containers if ref.kind == 'image' else weights) / ref.relative_path
        if ref.relative_path in {'rfantibody', 'boltz', 'esmfold2', 'protenix/mmcif'}:
            path /= 'fixture.bin'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('inert: ' + ref.relative_path).encode())
    return containers, weights


def test_binder_union_matches_native_generator_and_consumer_members():
    refs = {(ref.kind, ref.relative_path) for ref in workflow_pack_dependencies('antibody_denovo')}
    assert {(ref.kind, ref.relative_path) for ref in model_runtime_dependencies('bindcraft2')} == {
        ('image', 'bindcraft2.sif'), ('weights', 'alphafold/params')}
    assert len(refs) == len(workflow_pack_dependencies('antibody_denovo'))
    assert {p for k, p in refs if k == 'image'} == {
        'bindcraft2.sif', 'boltzgen.sif', 'ppiflow.sif', 'rfantibody.sif',
        'dl_binder_design.sif', 'fampnn.sif', 'caliby.sif', 'pyrosetta_tools.sif',
        'boltz2.sif', 'protenix.sif', 'esmfold2.sif', 'foundry.sif', 'frustrampnn.sif',
        'md-preparation-v1.sif', 'gromacs-md-2025.3.sif', 'md-analysis-1.0.0.sif',
    }
    groups = workflow_pack_weight_groups('antibody_denovo')
    assert set(groups) == {'bindcraft2', 'rfantibody', 'boltzgen', 'caliby_binder',
                           'boltz2', 'esmfold2', 'ppiflow_protein_binder',
                           'ppiflow_antibody_binder', 'ppiflow_nanobody_binder',
                           'protenix', 'protenix_templates'}
    selected, blockers = native_checkpoint_dependencies('RunBoltzGen', {
        'boltzgen_protocol': 'protein-small_molecule', 'boltzgen_checkpoint_mode': 'both'})
    assert not blockers
    assert groups['boltzgen'] == tuple(item.relative_path for item in selected)
    assert set(groups['protenix']) < set(groups['protenix_templates'])
    assert {(k, p) for k, p in refs if k == 'weights'} == {
        ('weights', p) for members in groups.values() for p in members}


@pytest.mark.asyncio
async def test_catalog_and_mounted_preview_without_job_or_scientific_compile(store, binder_assets, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from routers.execution_targets import router
    from database import get_session
    from services.remote_execution.preloading import PreloadController
    import services.nextflow

    monkeypatch.setattr(services.nextflow, 'compile_workflow_provision_request',
                        lambda *a, **kw: pytest.fail('binder pack must not compile a request'))
    app = FastAPI()
    app.include_router(router, prefix='/execution-targets')
    async def sessions():
        async with store() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    controller = PreloadController(store)
    app.state.preload_controller = controller
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            catalog = await client.get('/execution-targets/provision/catalog')
            assert catalog.status_code == 200
            assert SELECTION.model_dump() in catalog.json()
            assert {'kind': 'model', 'model_id': 'bindcraft2'} in catalog.json()
            preview = await client.post('/execution-targets/vast:1/provision/preview', json=SELECTION.model_dump())
            assert preview.status_code == 200, preview.text
            body = preview.json()
            assert body['selection'] == SELECTION.model_dump()
            assert body['blockers'] == [] and body['scientific_ready'] is False
            assert body['effective_params'] is None
            assert any(item['name'] == 'containers/bindcraft2.sif' for item in body['artifacts'])
            assert any(item['name'] == 'weights/ppiflow/binder.ckpt' for item in body['artifacts'])
        assert not controller.tasks
    finally:
        await controller.close()


def test_shared_bytes_inventoried_once_and_native_weight_views(binder_assets, monkeypatch):
    counts = {}
    original = cache._records_for_source
    def counted(path, prefix, role):
        counts[prefix] = counts.get(prefix, 0) + 1
        return original(path, prefix, role)
    monkeypatch.setattr(cache, '_records_for_source', counted)
    preview, entries = cache.independent_preview(SELECTION, TARGET)
    assert preview.blockers == []
    assert len(entries) == len({entry.remote_destination for entry in entries})
    assert all(count == 1 for count in counts.values())
    groups = cache.workflow_pack_weight_layouts(SELECTION, entries)
    assert set(groups) == set(workflow_pack_weight_groups('antibody_denovo'))
    for name, members in workflow_pack_weight_groups('antibody_denovo').items():
        assert {entry.remote_destination.removeprefix('weights/') for entry in groups[name]}
        assert all(any(entry.remote_destination == 'weights/' + member or
                       entry.remote_destination.startswith('weights/' + member + '/')
                       for member in members) for entry in groups[name])


def test_missing_optional_member_is_reported_without_substituting_subset(binder_assets):
    (binder_assets[1] / 'protenix/mmcif/fixture.bin').unlink()
    (binder_assets[1] / 'protenix/mmcif').rmdir()
    preview, _ = cache.independent_preview(SELECTION, TARGET)
    assert not preview.estimates_complete
    assert 'protenix/mmcif' in ' '.join(preview.blockers)


@pytest.mark.asyncio
async def test_one_action_installs_whole_binder_pack_without_job(store, binder_assets,
                                                                local_transport, tmp_path):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select, func
    from database import ExecutionTarget, Job, get_session
    from routers.execution_targets import router
    from services.remote_execution.preloading import PreloadController

    async with store() as session:
        before_jobs = await session.scalar(select(func.count()).select_from(Job))
        target = await session.get(ExecutionTarget, 'vast:1')
        target.remote_root = str(tmp_path / 'worker')
        await session.commit()
    app = FastAPI()
    app.include_router(router, prefix='/execution-targets')
    async def sessions():
        async with store() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    controller = PreloadController(store)
    app.state.preload_controller = controller
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            prefix = '/execution-targets/vast:1'
            response = await client.post(prefix + '/provision/preview', json=SELECTION.model_dump())
            assert response.status_code == 200, response.text
            preview = response.json()
            assert preview['blockers'] == []
            response = await client.post(prefix + '/provision', json={
                **SELECTION.model_dump(), 'preview_sha256': preview['preview_sha256']})
            assert response.status_code == 202, response.text
            await settle(controller)
            inventory = (await client.get(prefix + '/artifact-inventory')).json()
            assert inventory is not None, ((await client.get('/execution-targets')).json()[0]['preload']['message'])
            assert inventory['state'] == 'download_verified'
            assert inventory['artifacts'] == preview['artifacts']
            assert sum(Path(path).suffix == '.sif' for path in local_transport[1]) == sum(
                row['name'].startswith('containers/') for row in preview['artifacts'])
        async with store() as session:
            assert await session.scalar(select(func.count()).select_from(Job)) == before_jobs
    finally:
        await controller.close()
