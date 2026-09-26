"""Input-free structure pack planning; inert files, no provider/scientific work."""
from collections import Counter
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from model_registry import (get_registry, native_checkpoint_dependencies,
                            workflow_pack_dependencies, workflow_pack_weight_groups)
from services.remote_execution import cache, managed_inventory
from services.remote_execution.contracts import (WorkflowPackSelection, WorkflowPackRequest,
                                                ProvisionSelection, WorkflowRuntimeSelection)
from test_remote_independent_provisioning import assets
from test_remote_preloading import store


SELECTION = WorkflowPackSelection(kind='workflow_pack', workflow_id='structure_prediction')
TARGET = SimpleNamespace(id='one', host='worker', port=22, username='root',
                         remote_root='/worker', host_key_sha256='c' * 64)


@pytest.fixture
def pack_assets(assets):
    containers, weights = assets
    for ref in workflow_pack_dependencies('structure_prediction'):
        path = (containers if ref.kind == 'image' else weights) / ref.relative_path
        if ref.relative_path in {'boltz', 'esmfold2', 'protenix/mmcif'}:
            path = path / 'fixture.bin'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('inert fixture: ' + ref.relative_path).encode())
    for ref in native_checkpoint_dependencies('RunBoltz', {})[0]:
        path = weights / ref.relative_path
        if path.name == 'mols':
            path /= 'inert.pkl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'inert selected Boltz member')
    for variant in ('fast', 'full'):
        (weights / 'esmfold2' / (variant + '.bin')).write_bytes(variant.encode())
    (weights / 'esmfold2' / 'alias.bin').symlink_to('fast.bin')
    return containers, weights


@pytest.mark.asyncio
async def test_mounted_preview_without_job_inputs_or_mutation(store, pack_assets):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select
    from database import Job, get_session
    from routers.execution_targets import router
    from services.remote_execution.preloading import PreloadController
    app = FastAPI()
    app.include_router(router, prefix='/execution-targets')
    controller = PreloadController(store)
    app.state.preload_controller = controller
    async def sessions():
        async with store() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    async def snapshot():
        async with store() as session:
            return [(j.id, j.status, j.params) for j in (await session.scalars(select(Job))).all()]
    before = await snapshot()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/execution-targets/vast:1/provision/preview', json=SELECTION.model_dump())
            assert response.status_code == 200, response.text
            body = response.json()
            assert body['selection'] == SELECTION.model_dump()
            assert body['blockers'] == [] and body['scientific_ready'] is False
            assert body['effective_params'] is None
            rejected = await client.post('/execution-targets/vast:1/provision/preview',
                                         json={**SELECTION.model_dump(), 'sequence': 'AAA'})
            assert rejected.status_code == 422
        assert await snapshot() == before
        assert not controller.tasks
    finally:
        await controller.close()


def test_exact_pack_refs_and_native_optional_members():
    refs = [(r.kind, r.relative_path) for r in workflow_pack_dependencies('structure_prediction')]
    assert len(refs) == len(set(refs))
    assert {p for k, p in refs if k == 'image'} == {
        'boltz2.sif', 'fold-cp.sif', 'protenix.sif', 'esmfold2.sif', 'frustrampnn.sif'}
    optional, _ = native_checkpoint_dependencies('ProtenixPredict', {'protenix_use_template': True})
    assert {p for k, p in refs if k == 'weights'} == {
        'boltz', 'esmfold2', *(d.relative_path for d in optional)}
    assert ('weights', 'protenix/mmcif') in refs
    assert get_registry().get_model('frustrampnn') is None
    assert get_registry().get_internal_model_definition('frustrampnn').enabled
    with pytest.raises(ValueError):
        workflow_pack_dependencies('unknown')


@pytest.mark.parametrize('extra', [dict(sequence='AAA'), dict(model_id='boltz2'),
                                  dict(workflow_request={}), dict(url='https://example.invalid')])
def test_closed_input_free_wire(extra):
    with pytest.raises(ValidationError):
        WorkflowPackSelection.model_validate({**SELECTION.model_dump(), **extra})
    request = WorkflowPackRequest(**SELECTION.model_dump(), preview_sha256='a' * 64)
    assert request.workflow_id == 'structure_prediction'
    with pytest.raises(ValidationError):
        WorkflowPackSelection(kind='workflow_pack', workflow_id='protein_design')


@pytest.mark.asyncio
async def test_catalog_advertises_pack_not_internal_model():
    from routers.execution_targets import provision_catalog
    rows = [row.model_dump() for row in await provision_catalog()]
    assert SELECTION.model_dump() in rows
    assert not any(row.get('model_id') == 'frustrampnn' for row in rows)
    assert {'kind': 'image', 'model_id': 'boltz_cp_experimental'} in rows
    assert {'kind': 'model', 'model_id': 'boltz_cp_experimental'} not in rows


def test_preview_deduplicates_before_inventory_without_scientific_compilation(pack_assets, monkeypatch):
    import services.nextflow
    monkeypatch.setattr(services.nextflow, 'compile_workflow_provision_request',
                        lambda *a, **kw: pytest.fail('pack must not compile a scientific request'))
    counts = Counter()
    original = cache._records_for_source
    def counted(path, prefix, role):
        counts[prefix] += 1
        return original(path, prefix, role)
    monkeypatch.setattr(cache, '_records_for_source', counted)
    preview, entries = cache.independent_preview(SELECTION, TARGET)
    assert preview.blockers == []
    assert preview.effective_params is None and preview.plan_sha256 is None
    assert preview.scientific_ready is False
    assert counts['weights/boltz'] == 1
    assert counts['weights/esmfold2'] == 1
    assert all(count == 1 for count in counts.values())
    assert len(entries) == len({e.remote_destination for e in entries})
    assert any(e.link_target == 'fast.bin' for e in entries)
    assert {'weights/esmfold2/fast.bin', 'weights/esmfold2/full.bin'} <= {
        e.remote_destination for e in entries}
    first = preview.preview_sha256
    (pack_assets[1] / 'boltz/fixture.bin').write_bytes(b'changed inert fixture')
    assert cache.independent_preview(SELECTION, TARGET)[0].preview_sha256 != first


def test_optional_missing_asset_is_reported_without_acquisition(pack_assets):
    (pack_assets[1] / 'protenix/common/release_date_cache.json').unlink()
    preview, _ = cache.independent_preview(SELECTION, TARGET)
    assert not preview.estimates_complete
    assert preview.blockers[0].startswith('host_asset_unavailable:')
    assert 'protenix/common/release_date_cache.json' in preview.blockers[0]


def test_layout_groups_match_native_members_without_rescan(pack_assets, monkeypatch):
    from tools.bms_artifact_cache import weight_layout
    entries = cache.independent_plan(SELECTION)
    monkeypatch.setattr(cache, '_records_for_source', lambda *a: pytest.fail('layout must reuse inventory'))
    groups = cache.workflow_pack_weight_layouts(SELECTION, entries)
    assert groups['boltz'] == groups['fold_cp']
    assert groups['esmfold2_fast'] == groups['esmfold2_full']
    metadata = workflow_pack_weight_groups('structure_prediction')
    assert set(groups) == set(metadata)
    def names(group):
        return {e.remote_destination.removeprefix('weights/') for e in groups[group]}
    assert names('protenix') == set(metadata['protenix'])
    assert names('protenix_anchored') == set(metadata['protenix_anchored'])
    assert names('boltz_protenix') == names('boltz') | names('protenix')
    native_boltz = tuple(d.relative_path for d in native_checkpoint_dependencies('RunBoltz', {})[0])
    assert metadata['boltz_native'] == native_boltz
    assert names('boltz_native') < names('boltz')
    assert names('boltz_native_protenix') == names('boltz_native') | names('protenix')
    assert names('protenix') < names('protenix_anchored') < names('protenix_templates')
    for rows in groups.values():
        digest, layout, _ = weight_layout([
            dict(name=e.remote_destination.removeprefix('weights/'), sha256=e.sha256,
                 size_bytes=e.size_bytes, mode=0o777 if e.link_target is not None else e.mode,
                 **({'target': e.link_target} if e.link_target is not None else {})) for e in rows])
        assert len(digest) == 64 and layout


def test_manifest_uses_existing_opaque_worker_wire(pack_assets):
    from tools import bms_managed_runtime, bms_artifact_cache
    entries = cache.independent_plan(SELECTION)
    manifest = managed_inventory.manifest_for(SELECTION, entries, ('a' * 40, 'b' * 40))
    assert WorkflowRuntimeSelection.model_validate(manifest['selection']).kind == 'workflow'
    assert 'workflow_pack' not in str(manifest)
    bms_managed_runtime.validate_manifest(manifest, bms_artifact_cache)
    assert manifest == managed_inventory.manifest_for(SELECTION, entries, ('a' * 40, 'b' * 40))


def test_conflicting_identity_still_rejected_and_image_scope_unchanged(pack_assets, monkeypatch):
    image = ProvisionSelection(kind='image', model_id='boltz_cp_experimental')
    assert [e.remote_destination for e in cache.independent_plan(image)] == ['containers/fold-cp.sif']
    entries = cache.independent_plan(SELECTION)
    monkeypatch.setattr(cache, 'independent_plan', lambda _: [*entries, replace(entries[0], sha256='0' * 64)])
    with pytest.raises(ValueError, match='Conflicting managed dependency destinations'):
        cache.independent_preview(SELECTION, TARGET)
