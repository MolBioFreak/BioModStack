import hashlib
import json
from urllib.parse import urlencode

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from experiment_models import ExperimentBase, ExperimentResource, ExperimentAggregateHead, ExperimentRevision, ExperimentDatasetRevisionMember, ExperimentExternalEntityReceipt
from experiment_services import ValidationFailure
from molbio_models import MolBioBase, MolecularDocument, MolecularRevision
from services.family_msa.sources import resolve_molecular_member
from services.global_experiments.adapters import MolBioRevisionAdapter


@pytest.mark.asyncio
async def test_real_molecular_owner_and_occurrence_read(tmp_path):
    pe = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'project.sqlite'}")
    me = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'molbio.sqlite'}")
    async with pe.begin() as c:
        await c.run_sync(ExperimentBase.metadata.create_all)
    async with me.begin() as c:
        await c.run_sync(MolBioBase.metadata.create_all)
    ps = async_sessionmaker(pe, expire_on_commit=False)
    ms = async_sessionmaker(me, expire_on_commit=False)
    digest = hashlib.sha256(b'ACGTAC').hexdigest()
    async with ms() as s:
        s.add(MolecularDocument(id='seq', document_kind='dna', name='same'))
        s.add(MolecularRevision(id='rev', document_id='seq', revision_number=1, change_kind='import', content_sha256=digest, content_length=6, snapshot={'name': 'same', 'sequence': 'ACGTAC', 'sequence_type': 'dna'}, provenance={}))
        await s.commit()
    owner = MolBioRevisionAdapter(molbio_session_factory=ms)
    entity = urlencode({'sequence_id': 'seq', 'revision_id': 'rev'})
    fresh = await owner.verify(None, entity)
    async with ps() as s:
        s.add_all([ExperimentResource(id=x, kind='test') for x in ('p', 'd', 'r', 'q')])
        s.add(ExperimentAggregateHead(aggregate_id='d', aggregate_kind='dataset', workspace_id='p', display_name='D'))
        s.add(ExperimentRevision(resource_id='r', subject_id='d', revision_number=1, schema_name='test', schema_version='1', canonical_payload='{}', payload_sha256='a'*64, dependency_graph_sha256='b'*64))
        s.add(ExperimentExternalEntityReceipt(id='q', workspace_id='p', resource_id='q', store_id=fresh['store_id'], entity_kind=fresh['entity_kind'], entity_id=entity, generation_or_revision=str(fresh['entity_revision_id']), content_digest=fresh['content_digest'], availability='available', verification_authority=owner.adapter_id, acknowledgement_json=json.dumps(fresh)))
        for i in range(2):
            s.add(ExperimentDatasetRevisionMember(revision_id='r', ordinal=i, role='sequence', semantic_identity='q', value_json='{}', content_sha256='d'*64))
        await s.commit()
    async with ps() as p, ms() as m:
        args = dict(project_id='p', dataset_id='d', revision_id='r', adapter=owner)
        a = await resolve_molecular_member(p, None, m, ordinal=0, **args)
        b = await resolve_molecular_member(p, None, m, ordinal=1, start=1, end=4, **args)
        assert (a['sequence'], b['sequence'], b['start'], b['end']) == ('ACGTAC', 'CGT', 1, 4)
        assert a['source_positions'] == [0, 1, 2, 3, 4, 5]
        assert b['source_positions'] == [1, 2, 3]
        assert a['source_identity'] == b['source_identity'] and a['ordinal'] != b['ordinal']
        with pytest.raises(ValidationFailure):
            await resolve_molecular_member(p, None, m, ordinal=1, start=0, end=7, **args)
        receipt = await p.get(ExperimentExternalEntityReceipt, 'q')
        receipt.content_digest = '0'*64
        assert (await resolve_molecular_member(p, None, m, ordinal=0, **args))['status'] == 'unavailable'
    await pe.dispose()
    await me.dispose()
