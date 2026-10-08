"""Retained PLR native layout and ingester metadata through real HTTP/ORM.

Shapes follow Job3541d25d-96d0-4571-be9a-c4dc6e76071d: 8 backbones,
8 sequences, 8 ESMFold2 structures, 40 Protenix samples and one reference.
No response-enriched provenance is mistaken for persisted producer identity.
"""
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, Design, Job, get_session
from routers import designs


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_provenance", [None, {"fampnn": {"fampnn_avg_psce": 91.38}}, {"model_id": "protein_modification_experimental"}])
async def test_retained_plr_native_scope(tmp_path: Path, stored_provenance, monkeypatch):
    from services import plr_workflow_results

    def no_artifact_surface(*args, **kwargs):
        raise AssertionError("listing must not build/hash the workflow surface")
    monkeypatch.setattr(plr_workflow_results, "build_protein_local_redesign_result_surface", no_artifact_surface)
    monkeypatch.setattr(plr_workflow_results, "_sha256", no_artifact_surface)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'plr.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    root = tmp_path / 'retained_output'
    expected = {stage: [] for stage in ('rfd3', 'fampnn', 'esmfold2', 'protenix_v2')}
    rows = []
    for index in range(8):
        name = f'rfd3_input_protein_local_redesign_0_protein_local_redesign_0_0_model_{index}'
        seq = name + '_seq_0'
        layouts = [
            ('rfd3', name, f'collected/protein_local_redesign_backbones/{name}.pdb'),
            ('fampnn', seq, f'pdb_files/{seq}.pdb'),
            ('esmfold2', seq + '_000', f'validation/esmfold2/{seq}/esmfold2_results/{seq}_000.cif'),
            *[('protenix_v2', seq + f'_sample_{sample}', f'validation/protenix_v2/{seq}/{seq}_sample_{sample}.cif') for sample in range(5)],
        ]
        for stage, row_name, relative in layouts:
            identity = f'{stage}-{row_name}'
            expected[stage].append(identity)
            rows.append(Design(id=identity, job_id='plr', name=row_name, pdb_path=str(root / relative),
                               provenance=stored_provenance, stage_family='protein_local_redesign_validation',
                               stage_mode='region_redesign', artifact_class='validated_local_redesign_structure',
                               artifact_schema_version=1, review_profile_id='protein_local_redesign_validation_v1',
                               confidence_metrics={'fampnn': {'fampnn_avg_psce': 91.38}}, plddt_overall=80 + index))
    rows.append(Design(id='reference', job_id='plr', name='resolved_design_chain', pdb_path=str(root / 'inputs/protein_local_redesign/resolved_design_chain.pdb'), provenance=stored_provenance))
    async with factory() as session:
        session.add_all([
            Job(id='plr', name='TEST retained shape', status='completed', model_id='protein_modification_experimental', mode='region_redesign', params={}, output_dir=str(root)),
            Job(id='foreign', name='TEST foreign', status='completed', model_id='protein_modification_experimental', mode='region_redesign', params={}, output_dir=str(root)),
        ])
        session.add_all(rows)
        session.add(Design(id='foreign', job_id='foreign', name='foreign_seq_0', pdb_path=str(root / 'pdb_files/foreign_seq_0.pdb')))
        await session.commit()
    app = FastAPI()
    app.include_router(designs.router, prefix='/api/designs')
    async def sessions():
        async with factory() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    counts = {stage: len(ids) for stage, ids in expected.items()}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        base = {'job_id': 'plr', 'sort_by': 'name', 'sort_desc': False, 'limit': 2, 'include_summary': True}
        response = await client.get('/api/designs', params=base)
        assert response.status_code == 200, response.text
        assert response.json()['total'] == 65
        assert response.json()['model_counts'] == counts
        for stage, ids in expected.items():
            observed = []
            for offset in range(0, len(ids), 3):
                response = await client.get('/api/designs', params={**base, 'model_id': stage.upper(), 'offset': offset, 'limit': 3})
                assert response.status_code == 200, response.text
                body = response.json()
                assert body['total'] == len(ids)
                assert body['summary']['total'] == len(ids)
                assert body['model_counts'] == counts
                observed.extend(row['id'] for row in body['designs'])
                assert all(row['provenance']['producer_model_id'] == stage for row in body['designs'])
                if stored_provenance and 'model_id' in stored_provenance:
                    assert all(row['provenance']['model_id'] == stored_provenance['model_id'] for row in body['designs'])
            assert sorted(observed) == sorted(ids)
            exact = await client.post('/api/designs/query', json={**base, 'model_id': stage, 'design_ids': [ids[-1], 'foreign']})
            assert exact.status_code == 200, exact.text
            assert [row['id'] for row in exact.json()['designs']] == [ids[-1]]
            assert exact.json()['model_counts'] == counts
            direct = await client.get(f'/api/designs/{ids[-1]}', params={'job_id': 'plr'})
            assert direct.status_code == 200, direct.text
            assert direct.json()['provenance']['producer_model_id'] == stage
        for unknown in ('missing', 'protein_modification_experimental'):
            empty = await client.get('/api/designs', params={**base, 'model_id': unknown})
            assert empty.json()['total'] == 0
            assert empty.json()['designs'] == []
            assert empty.json()['model_counts'] == counts
        reference = await client.get('/api/designs/reference', params={'job_id': 'plr'})
        assert reference.status_code == 200, reference.text
        assert not (reference.json()['provenance'] or {}).get('producer_model_id')
        assert (await client.get('/api/designs/foreign', params={'job_id': 'plr'})).status_code == 404
    async with factory() as session:
        assert (await session.get(Design, 'reference')).provenance == stored_provenance
        assert (await session.execute(select(Design.provenance).where(Design.id == expected['rfd3'][0]))).scalar() == stored_provenance
    await engine.dispose()


@pytest.mark.asyncio
async def test_native_membership_is_job_bound_and_literal(tmp_path):
    from services.plr_workflow_results import design_model_identity_expression, design_producer_model_id

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'membership.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    job = Job(id='plr', name='TEST', model_id='protein_local_redesign', mode='local_redesign', output_dir='/results/job_a', params={})
    cases = [
        ('/results/job_a/pdb_files/model_seq_0.pdb', 'model_seq_0', 'fampnn'),
        ('validation/protenix_v2/model/sample_0.cif', 'sample_0', 'protenix_v2'),
        ('/results/job_b/pdb_files/model_seq_0.pdb', 'model_seq_0', None),
        ('/results/job_a_extra/pdb_files/model_seq_0.pdb', 'model_seq_0', None),
        ('/results/job_a/pdbXfiles/model_seq_0.pdb', 'model_seq_0', None),
        ('/results/job_a/pdb_files/source.pdb', 'source', None),
        ('/results/job_a/pdb_files/modelXseqY0.pdb', 'modelXseqY0', None),
        ('/results/job_a/validation/protenixXv2/sample_0.cif', 'sample_0', None),
        ('/results/job_a/validation/protenix_v2/../../foreign.cif', 'sample_0', None),
        ('../validation/protenix_v2/sample_0.cif', 'sample_0', None),
        ('/results/job_a/Validation/protenix_v2/sample_0.cif', 'sample_0', None),
    ]
    async with factory() as session:
        session.add(job)
        for index, (path, name, expected) in enumerate(cases):
            row = Design(id=str(index), job_id=job.id, name=name, pdb_path=path, provenance=None)
            session.add(row)
            assert design_producer_model_id(job, row) == expected
        await session.commit()
        actual = dict((await session.execute(select(Design.id, design_model_identity_expression()))).all())
        assert actual == {str(index): expected for index, (_, _, expected) in enumerate(cases)}
        foreign = Job(id='foreign', model_id=job.model_id, mode=job.mode, output_dir=job.output_dir, params={})
        assert design_producer_model_id(foreign, await session.get(Design, '0')) is None
    await engine.dispose()
