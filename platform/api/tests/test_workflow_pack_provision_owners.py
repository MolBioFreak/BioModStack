"""Whole-pack preview retains each leaf's acquisition owner; offline only."""
import hashlib
import json
import os

import pytest

import model_registry
from services.remote_execution import cache
from services.remote_execution.contracts import WorkflowPackSelection
from test_remote_independent_provisioning import assets
from test_remote_preloading import store


@pytest.mark.parametrize('workflow_id', ['antibody_denovo', 'structure_prediction'])
def test_workflow_packs_do_not_select_template_search(workflow_id):
    groups = model_registry.workflow_pack_weight_groups(workflow_id)
    assert not any('templates' in name for name in groups)
    members = {member for group in groups.values() for member in group}
    assert 'protenix/mmcif' not in members
    assert not any(member.startswith('protenix/search_database/') for member in members)
    owners = model_registry.workflow_pack_weight_owners(workflow_id)
    assert all(owners[member] == 'protenix' for member in members if member.startswith('protenix/'))
    if workflow_id == 'structure_prediction':
        assert {'protenix/common/obsolete_to_successor.json',
                'protenix/common/release_date_cache.json'} <= members


def test_denovo_union_keeps_native_declared_owner_resolution_without_another_pack_projection(monkeypatch):
    monkeypatch.setattr(model_registry, 'workflow_pack_weight_groups',
                        lambda *_: pytest.fail('native union leaves already have acquisition ownership'))
    assert model_registry.workflow_pack_weight_owners('de_novo_design') == {}


@pytest.fixture(params=['antibody_denovo', 'structure_prediction'])
def pack(request, assets, monkeypatch):
    selection = WorkflowPackSelection(kind='workflow_pack', workflow_id=request.param)
    containers, weights = assets
    refs = model_registry.workflow_pack_dependencies(selection.workflow_id)
    for ref in refs:
        path = (containers if ref.kind == 'image' else weights) / ref.relative_path
        if ref.relative_path in {'boltz', 'esmfold2', 'rfantibody', 'protenix/mmcif', 'alphafold/params'}:
            path /= 'fixture.bin'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('inert ' + ref.relative_path).encode())

    # Existing optional data must not be swept into an input-free workflow pack.
    corpus = weights / 'protenix/mmcif/unselected.cif'
    corpus.parent.mkdir(parents=True, exist_ok=True)
    corpus.write_text('inert unselected template')
    seqres = weights / 'protenix/search_database/pdb_seqres_2022_09_28.fasta'
    seqres.parent.mkdir(parents=True, exist_ok=True)
    seqres.write_text('>unselected\nAAAA\n')

    # Install a real immutable acquisition layout. A None/workflow-ID owner
    # cannot authenticate this alias and must not quietly replace its bytes.
    payload = b'owner-pinned Boltz bytes'
    sha = hashlib.sha256(payload).hexdigest()
    dependency = {'kind': 'weights', 'relative_path': 'boltz'}
    members = [{'member_path': 'native.ckpt', 'manifest': {'sha256': sha, 'size_bytes': len(payload)}}]
    digest = hashlib.sha256(json.dumps({'dependency': dependency, 'members': members},
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    obj = weights / 'objects/sha256' / sha / 'runtime.sif'
    obj.parent.mkdir(parents=True)
    obj.write_bytes(payload)
    obj.chmod(0o400)
    obj.parent.chmod(0o500)
    layout = weights / 'layouts/boltz' / digest
    layout.mkdir(parents=True)
    (layout / 'native.ckpt').symlink_to(os.path.relpath(obj, layout))
    layout.chmod(0o500)
    (weights / 'boltz/fixture.bin').unlink()
    (weights / 'boltz').rmdir()
    (weights / 'boltz').symlink_to(layout)
    original = model_registry.model_acquisition_plan
    monkeypatch.setattr(model_registry, 'model_acquisition_plan', lambda model_id:
        {'artifacts': [dict(dependency=dependency, **entry) for entry in members]}
        if model_id == 'boltz2' else original(model_id))
    return selection, refs, obj


def test_full_pack_records_keep_leaf_owners_and_pinned_bytes(pack, monkeypatch):
    selection, refs, obj = pack
    observed = {}
    original = cache._declared_runtime_records
    def records(path, prefix, publication, **kwargs):
        observed[prefix] = kwargs['model_id']
        return original(path, prefix, publication, **kwargs)
    monkeypatch.setattr(cache, '_declared_runtime_records', records)
    entries = cache.independent_plan(selection, publication={})
    assert set(observed) == {('containers/' if ref.kind == 'image' else 'weights/') + ref.relative_path for ref in refs}
    assert observed['weights/boltz'] == 'boltz2'
    assert observed['weights/esmfold2'] == 'esmfold2'
    assert all(owner == 'protenix' for name, owner in observed.items() if name.startswith('weights/protenix/'))
    if selection.workflow_id == 'antibody_denovo':
        assert observed['weights/alphafold/params'] == 'bindcraft2'
        assert observed['weights/rfantibody'] == 'rfantibody'
        assert observed['weights/ppiflow/binder.ckpt'] == 'ppiflow_protein_binder'
    pinned = next(row for row in entries if row.remote_destination == 'weights/boltz/native.ckpt')
    assert pinned.source == obj and pinned.sha256 == obj.parent.name
    assert pinned.link_target is None
    assert len(entries) == len({row.remote_destination for row in entries})
    assert {row.remote_destination for row in entries if row.remote_destination.startswith('containers/')} == {
        'containers/' + ref.relative_path for ref in refs if ref.kind == 'image'}


@pytest.mark.asyncio
async def test_mounted_whole_pack_preview_with_pinned_acquisition(store, pack, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select
    from database import Job, get_session
    from routers.execution_targets import router
    from services.remote_execution.preloading import PreloadController
    import services.nextflow

    selection, refs, obj = pack
    monkeypatch.setattr(services.nextflow, 'compile_workflow_provision_request',
                        lambda *a, **kw: pytest.fail('input-free pack must not compile science'))
    app = FastAPI()
    app.include_router(router, prefix='/api/execution-targets')
    controller = PreloadController(store)
    app.state.preload_controller = controller
    async def sessions():
        async with store() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    async def jobs():
        async with store() as session:
            return list(await session.scalars(select(Job.id)))
    before = await jobs()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/execution-targets/vast:1/provision/preview', json=selection.model_dump())
        assert response.status_code == 200, response.text
        body = response.json()
        assert body['selection'] == selection.model_dump()
        assert body['blockers'] == [] and body['estimates_complete']
        assert body['scientific_ready'] is False and body['effective_params'] is None
        names = {row['name'] for row in body['artifacts']}
        assert not any('/mmcif/' in name or '/search_database/' in name for name in names)
        assert all(any(name == ('containers/' if ref.kind == 'image' else 'weights/') + ref.relative_path or
            name.startswith('weights/' + ref.relative_path + '/') for name in names) for ref in refs)
        assert next(row for row in body['artifacts'] if row['name'] == 'weights/boltz/native.ckpt')['sha256'] == obj.parent.name
        assert await jobs() == before
        assert not controller.tasks
    finally:
        await controller.close()


@pytest.mark.asyncio
async def test_catalog_offers_the_de_novo_pack_the_contract_accepts():
    """The registry builds a de_novo_design pack; the catalog and request contract must expose it."""
    from typing import get_args
    from routers.execution_targets import provision_catalog
    from services.remote_execution.contracts import WorkflowPackRequest
    packs = {item.workflow_id for item in await provision_catalog() if item.kind == 'workflow_pack'}
    assert packs == set(get_args(WorkflowPackSelection.model_fields['workflow_id'].annotation))
    assert 'de_novo_design' in packs
    assert model_registry.workflow_pack_dependencies('de_novo_design')
    WorkflowPackRequest(kind='workflow_pack', workflow_id='de_novo_design', preview_sha256='a' * 64)
