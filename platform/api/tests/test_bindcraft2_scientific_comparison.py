"""Scratch native publication -> real HTTP APIs -> fresh readback, no inference."""
import csv
import hashlib

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job, JobArtifact, get_session
from services.bindcraft2_publication import publish_native_results
from services.bindcraft2_scientific_consumer import local_qa
from services.core_protein_scientific_contract import revision_for_job
from test_bindcraft2_publication import campaign, table


# Hand-authored mmCIF QA, deliberately NOT a B-factor confidence fixture.
ATOMS = '''loop_
_atom_site.id
_atom_site.label_atom_id
_atom_site.auth_atom_id
_atom_site.type_symbol
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_seq_id
_atom_site.auth_asym_id
_atom_site.auth_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.pdbx_PDB_model_num
_atom_site.label_alt_id
_atom_site.label_entity_id
_atom_site.B_iso_or_equiv
1 CA CA C ALA X 1 A 42 B 2 . 1 12.0
2 N N N ALA X 1 A 42 B 2 . 1 99.0
3 CA CA C GLY Y 1 A 42 C 3 . 2 33.0
#
'''
QA = '''loop_
_ma_qa_metric.id
_ma_qa_metric.name
_ma_qa_metric.type
_ma_qa_metric.mode
1 pLDDT pLDDT local
2 PAE_mean other local
#
loop_
_ma_qa_metric_local.model_id
_ma_qa_metric_local.label_asym_id
_ma_qa_metric_local.label_seq_id
_ma_qa_metric_local.label_comp_id
_ma_qa_metric_local.metric_id
_ma_qa_metric_local.metric_value
2 X 1 ALA 1 85.3
3 Y 1 GLY 1 67.1
2 X 1 ALA 2 7.5
#
'''


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('mode', ['present', 'absent', 'collision', 'unmapped'])
def test_exact_qa_identity(mode):
    atoms = ATOMS
    qa = QA
    if mode == 'absent':
        qa = ''
    if mode == 'collision':
        atoms = atoms.replace('2 N N N ALA X 1 A 42 B', '2 N N N ALA X 1 A 42 C')
    if mode == 'unmapped':
        qa = qa.replace('2 X 1 ALA 1', '1 X 1 ALA 1')
    raw = ('data_test\n' + atoms + qa).encode()
    if mode in ('collision', 'unmapped'):
        with pytest.raises(ValueError):
            local_qa(raw)
    elif mode == 'absent':
        assert local_qa(raw) is None
    else:
        axis, values = local_qa(raw)
        assert values == [0.853, 0.6709999999999999]
        assert [(r['selected_model'], r['auth_asym_id'], r['auth_seq_id'], r['label_asym_id'],
                 r['label_seq_id'], r['insertion_code']) for r in axis] == [(2, 'A', 42, 'X', 1, 'B'), (3, 'A', 42, 'Y', 1, 'C')]


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['present', 'absent', 'collision'])
async def test_publication_actual_api_fresh_readback(tmp_path, mode):
    from routers.designs import router as designs_router
    from routers.analytics import router as analytics_router
    root = tmp_path / 'campaign'
    campaign(root)
    for p in (root / '3_Ranked').glob('*.cif'):
        original = p.read_text().replace('_atom_site.id 1\n', '')
        atoms = ATOMS
        qa = QA
        if p.name.endswith('stateA.cif'):
            if mode == 'absent':
                qa = ''  # alternate stateB HAS QA: do not borrow it
            elif mode == 'collision':
                atoms = atoms.replace('2 N N N ALA X 1 A 42 B', '2 N N N ALA X 1 A 42 C')
        if p.name.endswith('stateB.cif'):
            qa = qa.replace('85.3', '12.5')
        p.write_text(original + atoms + qa)
    rejected = root / '2_Refolded/Complexes/t_candidate1.cif'
    rejected.parent.mkdir(parents=True)
    rejected.write_text('data_rejected\n' + ATOMS + QA)
    monomer = root / '2_Refolded/BinderMonomer/t_candidate1_monomer.cif'
    monomer.parent.mkdir(parents=True)
    monomer.write_text('data_monomer\n' + ATOMS)
    before = snapshot(root)
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "db.sqlite"}')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            job = Job(id='bc', name='BC2', status='failed', model_id='bindcraft2', mode='campaign',
                      params={}, output_dir=str(root))
            session.add(job)
            await session.flush()
            assert await publish_native_results(job, root, session, commit=True) == 1
            identity = (await session.scalar(select(Design))).id
            artifacts = {a.storage_path: a.id for a in (await session.scalars(select(JobArtifact))).all()}
            alternate_id = artifacts[str(root / '3_Ranked/t_seq0_stateB.cif')]
            rejected_id = artifacts[str(rejected)]
            monomer_id = artifacts[str(monomer)]
            assert revision_for_job(job) is None
        app = FastAPI()
        app.include_router(designs_router, prefix='/api/designs')
        app.include_router(analytics_router, prefix='/api/analytics')
        async def dependency():
            async with factory() as session:
                yield session
        app.dependency_overrides[get_session] = dependency
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            point_response = await client.get('/api/analytics/job/bc/designs?include_children=false')
            assert point_response.status_code == 200, point_response.text
            point = point_response.json()[0]
            assert point['id'] == identity
            assert point['metrics'] == {'i_pTM': 0.9}
            assert point['metric_descriptors']['i_pAE']['unit'] == 'dimensionless'
            assert point['metric_states']['pLDDT']['state'] == 'unavailable'
            assert point['metric_sources']['i_pTM']['artifact_sha256'] == before['2_Refolded/!_Refolded.csv']
            assert point['metric_sources']['i_pTM']['candidate_id'] == '[null,"t_candidate2"]'
            summary = await client.get('/api/analytics/job/bc?include_children=false')
            assert summary.status_code == 200, summary.text
            assert summary.json()['scientific_cohorts'][0]['design_ids'] == [identity]
            detail = await client.get('/api/designs/' + identity)
            assert detail.status_code == 200, detail.text
            doc = detail.json()['scientific_structure_document']
            assert doc['contentSha256'] == before['3_Ranked/t_seq0_stateA.cif']
            assert detail.json()['core_protein_scientific_contract'] is None
            structure = await client.get(f'/api/designs/{identity}/pdb')
            assert structure.status_code == 200, structure.text
            assert hashlib.sha256(structure.content).hexdigest() == doc['contentSha256']
            response = await client.get(f'/api/designs/{identity}/residue-metrics')
            assert response.status_code == 200, response.text
            profile = response.json()
            if mode == 'present':
                assert profile['status'] == 'ok'
                assert profile['document'] == doc
                assert profile['values'] == [0.853, 0.6709999999999999]
                assert profile['axis']['stored_units'] == 'percent'
                assert profile['axis']['residues'][0]['insertion_code'] == 'B'
            else:
                assert profile['status'] == 'unavailable'
                assert profile['document'] is None
                assert profile['reason'] == ('native_local_qa_not_published' if mode == 'absent' else 'missing_or_invalid_bindcraft2_native_evidence')
            assert (await client.get(f'/api/designs/{identity}/residue-metrics')).json() == profile
            # Explicit alternates have their own document namespace and bytes;
            # the primary profile is never returned for another requested state.
            selector = f'?artifact_id={alternate_id}&target_state=stateB'
            alternate = (await client.get(f'/api/designs/{identity}/residue-metrics' + selector)).json()
            assert alternate['status'] == 'ok', alternate
            assert alternate['values'][0] == 0.125
            assert alternate['document']['documentId'] == 'artifact:' + alternate_id
            alternate_detail = (await client.get(f'/api/designs/{identity}' + selector)).json()
            assert alternate_detail['scientific_structure_document'] == alternate['document']
            alternate_bytes = await client.get(f'/api/designs/{identity}/pdb' + selector)
            assert hashlib.sha256(alternate_bytes.content).hexdigest() == alternate['document']['contentSha256']
            by_state = (await client.get(f'/api/designs/{identity}/residue-metrics?target_state=stateB')).json()
            assert by_state == alternate
            wrong_state = (await client.get(f'/api/designs/{identity}/residue-metrics?artifact_id={alternate_id}&target_state=stateA')).json()
            assert wrong_state['status'] == 'unavailable'
            # Native rejected artifact has real QA but no accepted Design and no
            # extra producer metadata requirement. Monomer does not inherit it.
            native_url = f'/api/designs/native/bc/{rejected_id}'
            native_doc = (await client.get(native_url + '/document')).json()
            assert native_doc['status'] == 'ok', native_doc
            native = (await client.get(native_url + '/residue-metrics')).json()
            assert native['status'] == 'ok', native
            assert native['design_id'] is None
            assert native['document'] == native_doc['document']
            assert native['document']['candidateId'] == f'native:bc:{rejected_id}'
            assert native['document']['documentId'] == 'artifact:' + rejected_id
            assert native['values'][0] == 0.853
            assert (await client.get(native_url + '/residue-metrics')).json() == native
            no_qa = (await client.get(f'/api/designs/native/bc/{monomer_id}/residue-metrics')).json()
            assert no_qa['status'] == 'unavailable' and no_qa['design_id'] is None
            assert no_qa['reason'] == 'native_local_qa_not_published'
            foreign = (await client.get(f'/api/designs/native/foreign/{rejected_id}/residue-metrics')).json()
            assert foreign['status'] == 'unavailable'
            wrong_design = (await client.get(f'/api/designs/{identity}/residue-metrics?artifact_id={rejected_id}')).json()
            assert wrong_design['status'] == 'unavailable'
            chains = await client.get(f'/api/designs/{identity}/chain-metrics')
            assert chains.status_code == 200, chains.text
            assert chains.json()['status'] == 'unavailable'
            assert chains.json()['reason'] == 'native_chain_metrics_not_published'
            for url in ['/api/designs?job_id=bc&include_children=false', '/api/designs/by-job/bc?include_children=false']:
                listed = await client.get(url)
                assert listed.status_code == 200, listed.text
                assert listed.json()['designs'][0]['scientific_structure_document'] == doc
        assert snapshot(root) == before
        async with factory() as session:
            job = await session.get(Job, 'bc')
            assert job.status == 'failed' and revision_for_job(job) is None
            assert len((await session.scalars(select(Design))).all()) == 1
            # Cached compact columns do not override native authority.
            row = await session.get(Design, identity)
            row.residue_plddt = [100.0]
            row.plddt_overall = 100.0
            await session.commit()
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            assert (await client.get(f'/api/designs/{identity}/residue-metrics')).json() == profile
            assert (await client.get('/api/analytics/job/bc/designs?include_children=false')).json()[0] == point
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_same_name_arms_batch_custody_and_changed_document(tmp_path, monkeypatch):
    from routers.analytics import router
    from routers.designs import router as designs_router
    from services import bindcraft2_scientific_consumer as consumer
    root = tmp_path / 'sweep'
    for arm, value in [('one', '0.25'), ('two', '0.75')]:
        campaign(root / arm)
        path = root / arm / '2_Refolded/!_Refolded.csv'
        with path.open() as handle:
            rows = list(csv.DictReader(handle))
        rows[1]['i_pTM'] = value + ';0.99'
        for row in rows:
            row.update(i_pAE='0.19', Binder_RMSD='1.7', pTM='nan',
                       targets='stateA;stateB', target_weights='1;1')
        table(path, rows)
    table(root / 'sweep.csv', [{'arm': 'one'}, {'arm': 'two'}])
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "db.sqlite"}')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            job = Job(id='bc', name='BC2', status='completed', model_id='bindcraft2', mode='campaign',
                      params={}, output_dir=str(root))
            session.add(job)
            await session.flush()
            assert await publish_native_results(job, root, session, commit=True) == 2
        calls = []
        real_read = consumer.read_published_native_results
        async def counted_read(job, session):
            calls.append(job.id)
            return await real_read(job, session)
        monkeypatch.setattr(consumer, 'read_published_native_results', counted_read)
        app = FastAPI()
        app.include_router(router, prefix='/api/analytics')
        app.include_router(designs_router, prefix='/api/designs')
        async def dependency():
            async with factory() as session:
                yield session
        app.dependency_overrides[get_session] = dependency
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get('/api/analytics/job/bc/designs?include_children=false')
            assert response.status_code == 200, response.text
            points = response.json()
            assert calls == ['bc']
            assert len(points) == len({p['id'] for p in points}) == 2
            assert {p['name'] for p in points} == {'t_seq0'}
            assert {p['metrics']['i_pTM'] for p in points} == {0.25, 0.75}
            assert {p['metric_sources']['i_pTM']['candidate_id'] for p in points} == {'["one","t_candidate2"]', '["two","t_candidate2"]'}
            assert all(p['metrics']['i_pAE'] == 0.19 and p['metrics']['Binder_RMSD'] == 1.7 for p in points)
            assert all(p['metric_states']['pTM']['state'] == 'invalid' for p in points)
            # A different selected file cannot inherit the old document or its QA.
            async with factory() as session:
                design = await session.get(Design, points[0]['id'])
                design.pdb_path = str(root / 'one/3_Ranked/t_seq0_stateB.cif')
                await session.commit()
            profile = await client.get('/api/designs/' + points[0]['id'] + '/residue-metrics')
            assert profile.status_code == 200 and profile.json()['status'] == 'unavailable'
            detail = await client.get('/api/designs/' + points[0]['id'])
            assert detail.json()['scientific_structure_document'] is None
            response = await client.get('/api/analytics/job/bc/designs?include_children=false')
            assert all(p['metrics'] == {} for p in response.json())
    finally:
        await engine.dispose()
