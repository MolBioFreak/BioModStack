import hashlib
import json
from urllib.parse import urlencode

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import get_session
from experiment_database import get_experiment_session
from experiment_models import ExperimentBase, ExperimentResource, ExperimentExternalEntityReceipt
from molbio_database import get_molbio_session
from molbio_models import MolBioBase, MolecularDocument, MolecularRevision
from routers import family_msa_results as routes
from services.global_experiments.adapters import MolBioRevisionAdapter
from experiment_services import create_project, create_global_experiment, create_domain_experiment, create_dataset, save_dataset_revision, derive_dataset_revision
from test_project_manager_adapters import _project_payload, _global_payload, _domain_payload


@pytest.mark.asyncio
async def test_source_routes_with_real_molecular_owner_and_paging(tmp_path, monkeypatch):
    pe = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'project.db'}")
    me = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'molbio.db'}")
    async with pe.begin() as c:
        await c.run_sync(ExperimentBase.metadata.create_all)
    async with me.begin() as c:
        await c.run_sync(MolBioBase.metadata.create_all)
    ps, ms = async_sessionmaker(pe, expire_on_commit=False), async_sessionmaker(me, expire_on_commit=False)
    digest = hashlib.sha256(b'ACGTAC').hexdigest()
    async with ms() as s:
        s.add(MolecularDocument(id='seq', document_kind='dna', name='example'))
        s.add(MolecularRevision(id='rev', document_id='seq', revision_number=1, change_kind='import', content_sha256=digest, content_length=6, snapshot={'name': 'example', 'sequence': 'ACGTAC', 'sequence_type': 'dna'}, provenance={}))
        await s.commit()
    owner = MolBioRevisionAdapter(molbio_session_factory=ms)
    entity = urlencode({'sequence_id': 'seq', 'revision_id': 'rev'})
    fresh = await owner.verify(None, entity)
    async with ps() as s:
        project = await create_project(s, _project_payload())
        experiment = await create_global_experiment(s, project.id, _global_payload())
        domain = await create_domain_experiment(s, project.id, experiment.id, _domain_payload('ngs_molbio'))
        dataset = await create_dataset(s, project.id, 'Pinned sequences', 'ngs_molbio.sequence_collection.v1', experiment_id=domain.id)
        s.add(ExperimentResource(id='q', kind='test', workspace_id=project.id))
        s.add(ExperimentExternalEntityReceipt(id='q', workspace_id=project.id, resource_id='q', store_id=fresh['store_id'], entity_kind=fresh['entity_kind'], entity_id=entity, generation_or_revision=str(fresh['entity_revision_id']), content_digest=fresh['content_digest'], availability='available', verification_authority=owner.adapter_id, acknowledgement_json=json.dumps(fresh)))
        # Exercise the immutable Project storage owner, not the typed Dataset
        # authoring endpoint: repeated receipt/role pairs here are intentional
        # paging fixtures and are not accepted by that endpoint.
        members = [{'identity': 'q' if i < 102 else 'unknown', 'role': 'sequence',
                    'value': {'receipt_id': 'q' if i < 102 else 'unknown'}} for i in range(103)]
        revision = await save_dataset_revision(s, dataset.aggregate_id, {'members': members}, expected_head_generation=dataset.head_generation)
        derived = await derive_dataset_revision(s, dataset.aggregate_id, revision.resource_id, [2, 0], expected_head_generation=dataset.head_generation)
        ambiguous = await derive_dataset_revision(s, dataset.aggregate_id, revision.resource_id, [0, 0], expected_head_generation=dataset.head_generation)
        project_id, dataset_id, revision_id = project.id, dataset.aggregate_id, revision.resource_id
        derived_id, ambiguous_id = derived.resource_id, ambiguous.resource_id
        await s.commit()
    async def project_session():
        async with ps() as s:
            yield s
    async def molecular_session():
        async with ms() as s:
            yield s
    async def core_session():
        yield None
    async def authorized(*args, **kwargs):
        return 'operator'
    monkeypatch.setattr(routes, '_require_mutation_owner', authorized)
    from services.global_experiments.adapters import registry
    original_get = registry.get
    monkeypatch.setattr(registry, 'get', lambda key: owner if key == owner.adapter_id else original_get(key))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_experiment_session] = project_session
    app.dependency_overrides[get_molbio_session] = molecular_session
    app.dependency_overrides[get_session] = core_session
    base = f'/api/family-msa/projects/{project_id}/datasets/{dataset_id}/revisions/{revision_id}/resources'
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            pages = []
            offset = 0
            while offset is not None:
                res = await client.get(base, params={'offset': offset, 'limit': 50})
                assert res.status_code == 200, res.text
                page = res.json(); pages.extend(page['items']); offset = page['next_offset']
            assert [x['ordinal'] for x in pages] == list(range(103))
            assert pages[-1]['receipt'] is None
            for item in pages:
                assert item['ancestry']['status'] == 'resolved'
                assert item['ancestry']['occurrence']['revision_id'] == revision_id
                assert item['ancestry']['occurrence']['ordinal'] == item['ordinal']
            res = await client.post(base + '/freeze', json={'ordinals': [102, 1, 0]})
            assert res.status_code == 200, res.text
            selected = res.json()['members']
            assert [x['ordinal'] for x in selected] == [0, 1, 102]
            assert [x['ancestry'] for x in selected] == [pages[i]['ancestry'] for i in (0, 1, 102)]
            derived_base = base.replace(revision_id, derived_id)
            derived_response = await client.post(derived_base + '/freeze', json={'ordinals': None})
            assert derived_response.status_code == 200, derived_response.text
            derived_members = derived_response.json()['members']
            assert [x['ancestry']['occurrence']['ordinal'] for x in derived_members] == [2, 0]
            assert all(x['ancestry']['occurrence']['revision_id'] == revision_id for x in derived_members)
            ambiguous_base = base.replace(revision_id, ambiguous_id)
            ambiguous_response = await client.get(ambiguous_base)
            assert ambiguous_response.status_code == 200, ambiguous_response.text
            assert [x['ancestry'] for x in ambiguous_response.json()['items']] == [
                {'status': 'unknown', 'reason': 'repeated_origin_ambiguous'}] * 2
            res = await client.get(base + '/1/molecular', params={'start': 1, 'end': 4})
            assert res.status_code == 200, res.text
            assert res.json()['sequence'] == 'CGT'
            res = await client.get(base + '/102/molecular')
            assert res.json()['status'] == 'unavailable'
            assert (await client.get(base + '/999/molecular')).status_code == 404
    finally:
        await pe.dispose(); await me.dispose()
