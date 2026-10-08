"""Preparation route contracts with real producers and no remote transport."""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from database import ExecutionTarget, Job, get_session
from routers.execution_targets import router
from services.remote_execution import preloading
from test_remote_independent_provisioning import assets
from test_remote_preloading import store


@pytest.mark.asyncio
async def test_missing_host_assets_are_actionable_and_cannot_start(store, assets):
    async with store() as session:
        await session.execute(delete(Job))
        await session.commit()
    controller = preloading.PreloadController(store)
    app = FastAPI()
    app.include_router(router, prefix='/execution-targets')
    app.state.preload_controller = controller
    async def sessions():
        async with store() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    prefix = '/execution-targets/vast:1/provision'
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            selection = {'kind': 'model', 'model_id': 'boltz2'}
            response = await client.post(prefix + '/preview', json=selection)
            assert response.status_code == 200, response.text
            preview = response.json()
            assert preview['blockers']
            import json, os
            from pathlib import Path
            if evidence := os.environ.get('BMS_PREPARATION_WIRE_DIR'):
                Path(evidence, 'blocked-preview.json').write_text(json.dumps(preview, indent=2))
            assert 'host_asset_unavailable' in preview['blockers'][0]
            assert preview['estimates_complete'] is False
            assert preview['destination']['target_id'] == 'vast:1'
            assert {r['name'] for r in preview['dependencies']} == {'containers/boltz2.sif', 'weights/boltz'}
            assert all(r['sha256'] is None and r['size_bytes'] is None for r in preview['dependencies'])
            request = {**selection, 'preview_sha256': preview['preview_sha256']}
            rejected = await client.post(prefix, json=request)
            assert rejected.status_code == 409
            assert 'blockers' in rejected.json()['detail']
            assert not controller.tasks
            (assets[0] / 'boltz2.sif').write_bytes(b'controlled image')
            (assets[1] / 'boltz').mkdir()
            (assets[1] / 'boltz/model.pt').write_bytes(b'controlled weights')
            ready = (await client.post(prefix + '/preview', json=selection)).json()
            assert ready['blockers'] == []
            assert ready['estimates_complete'] is True
            assert ready['preview_sha256'] != preview['preview_sha256']
            assert (await client.post(prefix, json=request)).status_code == 409
    finally:
        await controller.close()


@pytest.mark.asyncio
async def test_scoped_image_catalog_is_not_composed_model_closure(assets, monkeypatch):
    from routers.execution_targets import provision_catalog
    from services.remote_execution import cache
    from services.remote_execution.contracts import ProvisionSelection
    from types import SimpleNamespace
    monkeypatch.setenv('BMS_FEATURE_MOLECULAR_DYNAMICS', '1')
    catalog = [r.model_dump() for r in await provision_catalog()]
    target = SimpleNamespace(id='one', host='worker', port=22, username='root', remote_root='/worker', host_key_sha256='c'*64)
    for model, image in [('boltzgen', 'boltzgen.sif'), ('protein_local_redesign', 'foundry.sif'),
                         ('molecular_dynamics', 'gromacs-md-2025.3.sif'),
                         ('boltz_cp_experimental', 'fold-cp.sif'), ('confornets_experimental', 'confornets.sif')]:
        assert {'kind': 'image', 'model_id': model} in catalog
        assert {'kind': 'model', 'model_id': model} not in catalog
        (assets[0] / image).write_bytes(b'controlled image')
        image_preview, entries = cache.independent_preview(ProvisionSelection(kind='image', model_id=model), target)
        assert not image_preview.blockers and len(entries) == 1
        assert entries[0].remote_destination == 'containers/' + image
        blocked, entries = cache.independent_preview(ProvisionSelection(kind='model', model_id=model), target)
        assert blocked.blockers[0].startswith('binding_unavailable:') and not entries
    assert not any(r['model_id'] in {'diffdock', 'oligo_design', 'antibody_child'} for r in catalog)


@pytest.mark.asyncio
@pytest.mark.parametrize('model, settings', [
    ('protenix', {'protenix_use_msa': False, 'run_frustrampnn': False, 'seeds': [7]}),
    ('esmfold2', {'pred_method': 'esmfold2', 'structure_validator': 'esmfold2',
                 'model_variant': 'fast', 'local_files_only': True, 'run_frustrampnn': False,
                 'frustrampnn_requiredness': 'required'}),
])
async def test_unsaved_workflow_missing_assets_keeps_native_settings(assets, monkeypatch, model, settings):
    from services.remote_execution import cache, bundle
    from services.remote_execution.contracts import WorkflowProvisionSelection
    from types import SimpleNamespace
    # Real typed request normalization and native compiler, no saved Job.
    monkeypatch.setattr(bundle, 'get_container_dir', lambda: assets[0])
    monkeypatch.setattr(bundle, 'get_weights_root', lambda: assets[1])
    from services import nextflow
    from component_runtime import SourceIdentity
    from paths import get_code_root
    identity = SourceIdentity.from_checkout(get_code_root())
    source = (identity.revision, identity.tree)
    monkeypatch.setattr(cache, 'current_source_identity', lambda: source)
    selection = WorkflowProvisionSelection(kind='workflow', workflow_request=dict(
        name='Unsaved', model_id=model, mode='predict', params={'sequence': 'ACDE', **settings}))
    (assets[0] / 'protenix.sif').unlink()
    target = SimpleNamespace(id='one', host='worker', port=22, username='root', remote_root='/worker', host_key_sha256='c'*64)
    preview, entries = cache.independent_preview(selection, target)
    assert preview.blockers[0].startswith('host_asset_unavailable:') and not entries
    assert preview.plan_sha256
    assert any(d.name == f'containers/{model}.sif' for d in preview.dependencies)
    for key, value in settings.items():
        assert preview.selection.workflow_request.params[key] == value
    import json
    expected = nextflow.compile_workflow_provision_request(selection.workflow_request).execution_plan
    assert preview.effective_params == json.loads(expected.effective_json)
