import hashlib
import json
from urllib.parse import urlencode

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import get_session
from experiment_database import get_experiment_session
from experiment_models import ExperimentBase, ExperimentResource, ExperimentAggregateHead, ExperimentRevision, ExperimentDatasetRevisionMember, ExperimentExternalEntityReceipt
from molbio_database import get_molbio_session
from molbio_models import MolBioBase, MolecularDocument, MolecularRevision
from routers import family_msa_results as routes
from services.global_experiments.adapters import MolBioRevisionAdapter


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
        s.add_all([ExperimentResource(id=x, kind='test') for x in ('p', 'd', 'r', 'q')])
        s.add(ExperimentAggregateHead(aggregate_id='d', aggregate_kind='dataset', workspace_id='p', display_name='D'))
        s.add(ExperimentRevision(resource_id='r', subject_id='d', revision_number=1, schema_name='test', schema_version='1', canonical_payload='{}', payload_sha256='a'*64, dependency_graph_sha256='b'*64))
        s.add(ExperimentExternalEntityReceipt(id='q', workspace_id='p', resource_id='q', store_id=fresh['store_id'], entity_kind=fresh['entity_kind'], entity_id=entity, generation_or_revision=str(fresh['entity_revision_id']), content_digest=fresh['content_digest'], availability='available', verification_authority=owner.adapter_id, acknowledgement_json=json.dumps(fresh)))
        for i in range(103):
            s.add(ExperimentDatasetRevisionMember(revision_id='r', ordinal=i, role='sequence', semantic_identity='q' if i < 102 else 'unknown', value_json='{}', content_sha256='d'*64))
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
    base = '/api/family-msa/projects/p/datasets/d/revisions/r/resources'
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
            res = await client.post(base + '/freeze', json={'ordinals': [102, 1, 0]})
            assert res.status_code == 200, res.text
            assert [x['ordinal'] for x in res.json()['members']] == [0, 1, 102]
            res = await client.get(base + '/1/molecular', params={'start': 1, 'end': 4})
            assert res.status_code == 200, res.text
            assert res.json()['sequence'] == 'CGT'
            res = await client.get(base + '/102/molecular')
            assert res.json()['status'] == 'unavailable'
            assert (await client.get(base + '/999/molecular')).status_code == 404
    finally:
        await pe.dispose(); await me.dispose()
