import hashlib
import json

import pytest
import httpx
from fastapi import FastAPI
from database import get_session
from experiment_database import get_experiment_session
from routers import family_msa_results as routes
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database import Base, Design, Job
from experiment_models import ExperimentBase, ExperimentExternalEntityReceipt, ExperimentResource
from experiment_services import create_project, create_global_experiment, create_domain_experiment, create_dataset, save_dataset_revision
from services.family_msa.sources import resolve_design_member
from services.global_experiments.adapters import CoreProteinResultAdapter
from test_project_manager_adapters import _project_payload, _global_payload, _domain_payload


@pytest.mark.asyncio
async def test_design_chain_real_dataset_occurrences(tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_DATA', str(tmp_path))
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path / 'results'))
    root = tmp_path / 'results' / 'job'
    root.mkdir(parents=True)
    path = root / 'design.pdb'
    path.write_text('MODEL        7\nATOM      1  CA  ALA A  10      10.000  10.000  10.000  1.00 20.00           C\nATOM      2  CA  GLY A  10A     11.000  10.000  10.000  1.00 20.00           C\nATOM      3  CA  SER B   3      12.000  10.000  10.000  1.00 20.00           C\nATOM      4  CA  TYR B   9B     13.000  10.000  10.000  1.00 20.00           C\nENDMDL\nMODEL        8\nATOM      5  CA  TRP A  99      10.000  10.000  10.000  1.00 20.00           C\nENDMDL\nEND\n')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {'schema': 'bms.review-artifacts.v1', 'structure': {'kind':'structure','state':'ready','path':str(path),'sha256':digest,'bytes':path.stat().st_size}, 'roles':{'result_role':'structure'}}
    core = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "core.db"}')
    project = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "project.db"}')
    async with core.begin() as c: await c.run_sync(Base.metadata.create_all)
    async with project.begin() as c: await c.run_sync(ExperimentBase.metadata.create_all)
    cs, ps = async_sessionmaker(core, expire_on_commit=False), async_sessionmaker(project, expire_on_commit=False)
    try:
        async with cs() as c, ps() as p:
            c.add_all([Job(id='job', name='job', status='completed', model_id='shape', mode='predict', params={}, output_dir=str(root)), Design(id='design',job_id='job',name='protein',pdb_path=str(path),artifact_class='shape_candidate',review_profile_id='shape_blueprint',review_contract_version=1,review_contract_source='producer',review_artifact_manifest=manifest,review_role_map={'result_role':'structure'})])
            await c.commit()
            adapter = CoreProteinResultAdapter()
            fresh = await adapter.verify(c, 'design')
            pr = await create_project(p, _project_payload())
            experiment = await create_global_experiment(p, pr.id, _global_payload())
            domain = await create_domain_experiment(p, pr.id, experiment.id, _domain_payload('protein_in_silico'))
            dataset = await create_dataset(p, pr.id, 'proteins', 'protein_in_silico.design_set.v1', experiment_id=domain.id)
            p.add(ExperimentResource(id='q',kind='test',workspace_id=pr.id))
            p.add(ExperimentExternalEntityReceipt(id='q',workspace_id=pr.id,resource_id='q',store_id=fresh['store_id'],entity_kind=fresh['entity_kind'],entity_id='design',generation_or_revision=str(fresh['entity_revision_id']),content_digest=fresh['content_digest'],availability='available',verification_authority=adapter.adapter_id,acknowledgement_json=json.dumps(fresh)))
            revision = await save_dataset_revision(p, dataset.aggregate_id, {'members':[{'identity':'q','role':'design','value':{'receipt_id':'q'}} for _ in range(2)]}, expected_head_generation=dataset.head_generation)
            await p.commit()
            args = dict(project_id=pr.id,dataset_id=dataset.aggregate_id,revision_id=revision.resource_id)
            inventory = await resolve_design_member(p,c,ordinal=0,**args)
            assert inventory['chains'] == [{'chain_id':'A','length':2},{'chain_id':'B','length':2}]
            first = await resolve_design_member(p,c,ordinal=0,chain='A',**args)
            second = await resolve_design_member(p,c,ordinal=1,chain='B',start=1,end=2,**args)
            assert (first['sequence'],second['sequence'],second['source_positions']) == ('AG','Y',[1])
            mapping = first['structure_residue_map']
            assert mapping['source_identity'] == 'design' and mapping['source_digest'] == digest
            assert [(r['observed_index'], r['sequence_number'], r['insertion_code'], r['chain_id'], r['model_index'], r['model_serial']) for r in mapping['entries']] == [(0,10,'','A',0,7),(1,10,'A','A',0,7)]
            assert [(r['observed_index'],r['sequence_number'],r['insertion_code']) for r in second['structure_residue_map']['entries']] == [(1,9,'B')]
            assert first['ordinal'] != second['ordinal'] and first['source_identity'] == second['source_identity']
            async def project_session():
                async with ps() as session: yield session
            async def core_session():
                async with cs() as session: yield session
            async def authorized(*args, **kwargs): return 'operator'
            monkeypatch.setattr(routes, '_require_mutation_owner', authorized)
            app = FastAPI(); app.include_router(routes.router)
            app.dependency_overrides[get_experiment_session] = project_session
            app.dependency_overrides[get_session] = core_session
            base = f'/api/family-msa/projects/{pr.id}/datasets/{dataset.aggregate_id}/revisions/{revision.resource_id}/resources'
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                response = await client.get(base + '/1/design', params={'chain':'B','start':1,'end':2})
                assert response.status_code == 200, response.text
                assert response.json()['sequence'] == 'Y' and response.json()['source_positions'] == [1]
                assert response.json()['structure_residue_map'] == second['structure_residue_map']
            path.unlink()
            assert (await resolve_design_member(p,c,ordinal=0,chain='A',**args))['status'] == 'unavailable'
    finally:
        await core.dispose(); await project.dispose()
