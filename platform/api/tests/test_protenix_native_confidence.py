"""Generated mixed-complex fixtures; optional retained outputs are external/read-only."""
from copy import deepcopy
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import shutil

from Bio.PDB import MMCIFIO
from Bio.PDB.MMCIF2Dict import MMCIF2Dict
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job, get_session
from services import protenix_scientific_consumer as consumer
from services.result_ingester import _ingest_protenix_primary_publications
from scripts import write_structure_producer_manifest as producer


def mixed_bytes():
    rows = [('A','1','GLY','N'), ('A','1','GLY','CA'), ('A','1','GLY','C'),
            ('B','2','DA','P'), ('B','2','DA',"C1'"), ('C','3','MN','MN'),
            ('D','4','GTP','P'), ('D','4','GTP','O1'), ('D','4','GTP','C1')]
    cif = {'data_': 'generated_native_fixture', '_entity_poly.entity_id': ['1','2'],
           '_entity_poly.type': ['polypeptide(L)', 'polydeoxyribonucleotide'],
           '_entity_poly.pdbx_strand_id': ['A','B']}
    fields = dict(id=[], group_PDB=[], label_atom_id=[], auth_atom_id=[], type_symbol=[],
                  label_comp_id=[], auth_comp_id=[], label_asym_id=[], auth_asym_id=[],
                  label_entity_id=[], label_seq_id=[], auth_seq_id=[], pdbx_PDB_ins_code=[],
                  label_alt_id=[], pdbx_PDB_model_num=[], B_iso_or_equiv=[],
                  Cartn_x=[], Cartn_y=[], Cartn_z=[], occupancy=[])
    for i, (chain, entity, residue, atom) in enumerate(rows):
        values = [str(i+1), 'ATOM' if chain in ('A','B') else 'HETATM', atom, atom,
                  'MN' if atom == 'MN' else atom[0], residue, residue, chain, chain, entity,
                  '1','1','.','.','1',str(80+i+.25), str(i), '0','0','1']
        for key, value in zip(fields, values, strict=True): fields[key].append(value)
    cif.update({'_atom_site.'+key: value for key,value in fields.items()})
    out=StringIO(); writer=MMCIFIO(); writer.set_dict(cif); writer.save(out)
    full=dict(atom_plddt=[(80+i)/100 for i in range(len(rows))],
              atom_to_token_idx=[0,0,0,1,1,2,3,4,5], token_asym_id=[0,1,2,3,3,3],
              token_pair_pae=[[float(i*6+j)/10 for j in range(6)] for i in range(6)])
    summary=dict(chain_ptm=[.9,.7,0,.6], chain_pair_iptm=[[0.0 if i==j else .4 for j in range(4)] for i in range(4)])
    return out.getvalue().encode(), json.dumps(full).encode(), json.dumps(summary).encode()


def make_publication(tmp_path, *, custody='local'):
    source, full, summary = mixed_bytes()
    native=tmp_path/'native'; source_path=native/'complex/seed_42/predictions/complex_sample_0.cif'
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(source)
    names=producer.protenix_confidence_names(source_path.name)
    source_path.with_name(names['metrics']).write_bytes(summary)
    source_path.with_name(names['pae']).write_bytes(full)
    manifest=producer.build_manifest(predictions_root=native, producer_method='protenix', producer_sample='complex', formats=['mmcif'])
    raw=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
    root=tmp_path/'returned'; root.mkdir()
    producer.write_publication(manifest_bytes=raw, native_candidates=manifest['candidates'], manifest=manifest,
        predictions_root=native, publication_dir=root/'run/protenix/producer', published_structure_root='pdb_files/predictions')
    target=root/'pdb_files/predictions'; shutil.copytree(source_path.parent,target)
    job=Job(id='job',name='generated native fixture',model_id='protenix',mode='complex',params={},
            provenance={'core_protein_scientific_contract':1}, output_dir=str(root), status='completed')
    design=Design(id='candidate',job_id='job',name=source_path.stem,pdb_path=str(target/source_path.name),
                  json_path=str(target/names['metrics']), source_stage=None)
    if custody != 'local':
        from datetime import datetime, timezone
        from services.remote_execution.contracts import RemoteResultManifest
        from services.remote_execution.result_generation import identity
        if custody == 'retained_remote':
            publication_path = next((root/'run/protenix/producer').glob('*/publication.json'))
            publication = json.loads(publication_path.read_bytes())
            for binding in publication['bindings']: binding.pop('confidence')
            publication_path.write_text(json.dumps(publication))
        job.execution_target_id='target'; job.remote_attempt_id='attempt'
        job.execution_source_revision='a'*40; job.execution_source_tree='b'*40
        job.execution_bundle_sha256='c'*64
        manifest = RemoteResultManifest(job_id=job.id, attempt_id=job.remote_attempt_id, exit_code=0,
            completed_at=datetime.now(timezone.utc), source_revision=job.execution_source_revision,
            source_tree=job.execution_source_tree, execution_envelope_sha256=job.execution_bundle_sha256,
            artifacts=[dict(relative_path=p.relative_to(root).as_posix(), sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
                size_bytes=p.stat().st_size, role='result') for p in root.rglob('*') if p.is_file()])
        raw=manifest.model_dump_json().encode(); digest=hashlib.sha256(raw).hexdigest()
        (root/'result-manifest.json').write_bytes(raw)
        job.provenance={**job.provenance, 'remote_result_generation':identity(job,digest),
            'remote_execution_receipt':dict(result_manifest_sha256=digest,received_manifest_sha256=digest)}
    return job, design, root


async def store(tmp_path, job, design, *, ingest=True):
    engine=create_async_engine(f'sqlite+aiosqlite:///{tmp_path/"isolated.sqlite"}')
    async with engine.begin() as connection: await connection.run_sync(Base.metadata.create_all)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with factory() as session:
        session.add(job)
        if design is not None: session.add(design)
        await session.commit()
        if ingest:
            await _ingest_protenix_primary_publications(job,Path(job.output_dir),session)
            await session.commit()
    return engine, factory


def app_for(session):
    from routers.designs import router
    app=FastAPI(); app.include_router(router,prefix='/designs')
    async def dependency(): yield session
    app.dependency_overrides[get_session]=dependency
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize('custody',['local','remote'])
async def test_fresh_writer_through_canonical_primary_ingestion(tmp_path,custody):
    from services.result_ingester import ingest_job_results
    job,_design,root=make_publication(tmp_path,custody=custody)
    engine,factory=await store(tmp_path,job,None,ingest=False)
    try:
        async with factory() as session:
            assert await ingest_job_results(job.id,str(root),session)==1
        async with factory() as session:
            row=(await session.scalars(select(Design))).one()
            assert row.source_stage is None and row.provenance['native_producer']['producer_method']=='protenix'
            async with AsyncClient(transport=ASGITransport(app=app_for(session)),base_url='http://test') as client:
                metric=(await client.get(f'/designs/{row.id}/residue-metrics')).json()
                assert metric['status']=='ok' and metric['metric']=='atom_plddt'
                assert metric['values']==json.loads(mixed_bytes()[1])['atom_plddt']
                assert (await client.get(f'/designs/{row.id}/pae')).json()['native_shape']==[6,6]
            assert await ingest_job_results(job.id,str(root),session)==0
            assert len((await session.scalars(select(Design))).all())==1
    finally: await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('custody',['local','remote','retained_remote'])
async def test_native_publication_api_roundtrip(tmp_path,custody):
    job, design, root=make_publication(tmp_path,custody=custody)
    engine,factory=await store(tmp_path,job,design)
    try:
        async with factory() as session:
            async with AsyncClient(transport=ASGITransport(app=app_for(session)),base_url='http://test') as client:
                responses={name: await client.get(f'/designs/candidate/{name}') for name in ('pdb','residue-metrics','chain-metrics','pae')}
                assert all(r.status_code==200 for r in responses.values()), {k:r.text for k,r in responses.items()}
                assert responses['pdb'].content==mixed_bytes()[0]
                assert responses['pdb'].headers['content-type']=='chemical/x-mmcif'
                atoms=responses['residue-metrics'].json(); chains=responses['chain-metrics'].json(); pae=responses['pae'].json()
                assert atoms['metric']=='atom_plddt' and atoms['units']=='fraction'
                assert atoms['values']==json.loads(mixed_bytes()[1])['atom_plddt']
                assert len(atoms['axis']['residues'])==9 and 'derived_residue_plddt' not in atoms
                assert len(pae['row_axis']['residues'])==6 and pae['native_shape']==[6,6]
                assert [r.get('label_atom_id') for r in pae['row_axis']['residues']]==[None,None,'MN','P','O1','C1']
                assert pae['pae_matrix']==json.loads(mixed_bytes()[1])['token_pair_pae']
                assert list(chains['chains_ptm'].values())==[.9,.7,0,.6]
                assert [c['chain_id'] for c in chains['chain_index_map']]==list('ABCD')
                detail=await client.get('/designs/candidate')
                assert detail.status_code==200,detail.text
                assert detail.json()['scientific_structure_document']==atoms['document']
                assert not session.dirty and not session.new
            from services.core_protein_scientific_contract import verified_native_spatial_design, compute_persisted_pae
            row=await session.get(Design,'candidate')
            selected=await verified_native_spatial_design(row,session)
            small,_,_=await compute_persisted_pae(row,{'max_size':3},session,selected=selected)
            assert small['sampled_row_indices']==[0,2,5] and small['native_shape']==[6,6]
            assert small['pae_matrix']==[[selected['native']['pae'][i][j] for j in [0,2,5]] for i in [0,2,5]]
    finally: await engine.dispose()


@pytest.mark.parametrize('fault',['map','asym','atom_values','pae_shape','negative_pae','nan','reordered_atoms','foreign_chain','duplicate_atom','polymer_ligand','chain_shape'])
def test_native_mapping_rejects_bad_source_contract(fault):
    source, full_raw, summary_raw=mixed_bytes()
    full=json.loads(full_raw); summary=json.loads(summary_raw)
    cif=MMCIF2Dict(StringIO(source.decode()))
    if fault=='map': full['atom_to_token_idx'][1]=1
    elif fault=='asym': full['token_asym_id'][-1]=2
    elif fault=='atom_values': full['atom_plddt'][0]=.1
    elif fault=='pae_shape': full['token_pair_pae'][0].pop()
    elif fault=='negative_pae': full['token_pair_pae'][0][0]=-1
    elif fault=='nan': full['atom_plddt'][0]=float('nan')
    elif fault=='reordered_atoms':
        for key,values in cif.items():
            if key.startswith('_atom_site.'): values[0],values[1]=values[1],values[0]
    elif fault=='foreign_chain': cif['_atom_site.label_asym_id'][1]='Z'
    elif fault=='duplicate_atom':
        cif['_atom_site.label_atom_id'][1]=cif['_atom_site.label_atom_id'][0]
        cif['_atom_site.auth_atom_id'][1]=cif['_atom_site.auth_atom_id'][0]
    elif fault=='polymer_ligand': cif['_atom_site.label_comp_id'][-3:]=cif['_atom_site.auth_comp_id'][-3:]=['GLY']*3
    elif fault=='chain_shape': summary['chain_pair_iptm'][0].pop()
    out=StringIO(); writer=MMCIFIO(); writer.set_dict(cif); writer.save(out)
    # A ligand whose CCD happens to be a standard residue still has atom tokens.
    if fault=='polymer_ligand':
        result=consumer.derive_native_identity(out.getvalue().encode(),json.dumps(full).encode(),json.dumps(summary).encode(),dict(candidate_id='c',document_id='c'))
        assert len(result['token_axis']['residues'])==6
    else:
        with pytest.raises((ValueError,KeyError)):
            consumer.derive_native_identity(out.getvalue().encode(),json.dumps(full).encode(),json.dumps(summary).encode(),dict(candidate_id='c',document_id='c'))


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['structure','metrics','pae','publication','producer_manifest','symlink','candidate','path','inventory','missing_full','missing_metrics','missing_both'])
async def test_custody_rejects_mutation_without_reblessing(tmp_path,fault):
    job, design, root=make_publication(tmp_path)
    engine,factory=await store(tmp_path,job,design)
    try:
        async with factory() as session:
            row=await session.get(Design,'candidate'); owner=await session.get(Job,'job')
            before=deepcopy(owner.provenance)
            selected=await consumer.verified_native_design(row,session)
            paths={key:Path(value['path']) for key,value in selected['artifacts'].items()}
            paths.update({key:Path(value['path']) for key,value in owner.provenance['protenix_primary_publication'][0].items()})
            if fault in paths: paths[fault].write_bytes(paths[fault].read_bytes()+b' ')
            elif fault=='symlink':
                retained=root/'same.json';shutil.copyfile(paths['pae'],retained);paths['pae'].unlink();paths['pae'].symlink_to(retained)
            elif fault=='candidate': row.provenance={**row.provenance,'native_producer':dict(row.provenance['native_producer'],producer_output_key='foreign_sample_0.cif')}
            elif fault=='path': row.pdb_path=str(paths['metrics'])
            elif fault=='inventory': owner.provenance={**owner.provenance,'protenix_primary_publication':owner.provenance['protenix_primary_publication']*2}
            elif fault=='missing_full': paths['pae'].unlink()
            elif fault=='missing_metrics': paths['metrics'].unlink()
            elif fault=='missing_both': paths['metrics'].unlink();paths['pae'].unlink()
            with pytest.raises((ValueError,KeyError,OSError)):
                await consumer.verified_native_design(row,session)
            result=await consumer.compute_persisted_native_metric(row,'residue_plddt',session)
            assert result.status=='unavailable' and result.design_id==row.id
            assert result.reason=='missing_or_invalid_protenix_native_evidence'
            if fault!='inventory': assert owner.provenance==before
            if fault=='structure':
                async with AsyncClient(transport=ASGITransport(app=app_for(session)),base_url='http://test') as client:
                    assert (await client.get('/designs/candidate/pdb')).status_code==409
            if fault in ('metrics','pae','missing_full','missing_metrics','missing_both','symlink'):
                # Optional confidence failure must not break verified structure viewing.
                assert (await consumer.verified_native_design(row,session,structure_only=True))['snapshots']['structure']==mixed_bytes()[0]
                async with AsyncClient(transport=ASGITransport(app=app_for(session)),base_url='http://test') as client:
                    assert (await client.get('/designs/candidate/pdb')).content==mixed_bytes()[0]
                    detail=await client.get('/designs/candidate')
                    assert detail.status_code==200 and detail.json()['scientific_structure_document'] is not None
                    for metric in ('residue-metrics','chain-metrics','pae'):
                        response=await client.get('/designs/candidate/'+metric)
                        assert response.status_code==200 and response.json()['status']=='unavailable'
    finally: await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['manifest_bytes','manifest_symlink','full_bytes','generation','attempt','source_tree','absent_generation'])
async def test_retained_return_authority_cannot_be_recreated(tmp_path,fault):
    job,design,root=make_publication(tmp_path,custody='retained_remote')
    engine,factory=await store(tmp_path,job,design)
    try:
        async with factory() as session:
            row=await session.get(Design,'candidate'); owner=await session.get(Job,'job')
            selected=await consumer.verified_native_design(row,session)
            manifest=root/'result-manifest.json'
            if fault=='manifest_bytes': manifest.write_bytes(manifest.read_bytes()+b' ')
            elif fault=='manifest_symlink':
                target=root/'copy.json';shutil.copyfile(manifest,target);manifest.unlink();manifest.symlink_to(target)
            elif fault=='full_bytes':
                p=Path(selected['artifacts']['pae']['path']);p.write_bytes(p.read_bytes()+b' ')
            elif fault=='generation':
                owner.provenance={**owner.provenance,'remote_result_generation':dict(owner.provenance['remote_result_generation'],job_id='foreign')}
            elif fault=='attempt': owner.remote_attempt_id='foreign'
            elif fault=='source_tree': owner.execution_source_tree='d'*40
            elif fault=='absent_generation': owner.provenance={k:v for k,v in owner.provenance.items() if k!='remote_result_generation'}
            with pytest.raises((ValueError,KeyError,OSError)):
                await consumer.verified_native_design(row,session)
            assert (await consumer.compute_persisted_pae(row,{},session))[0]['status']=='unavailable'
    finally: await engine.dispose()


def test_native_entity_and_copy_indices_follow_pinned_annotation_owner():
    source,full,summary=mixed_bytes()
    cif=MMCIF2Dict(StringIO(source.decode()))
    # Lexical order 1,10,2 differs from integer-1; two nonpolymer copies share 10.
    labels={'1':'2','2':'1','3':'10','4':'10'}
    cif['_atom_site.label_entity_id']=[labels[v] for v in cif['_atom_site.label_entity_id']]
    cif['_entity_poly.entity_id']=['2','1']
    out=StringIO(); writer=MMCIFIO();writer.set_dict(cif);writer.save(out)
    native=consumer.derive_native_identity(out.getvalue().encode(),full,summary,dict(candidate_id='c',document_id='c'))
    assert [c['native_entity_id'] for c in native['chain_index_map']]==[2,0,1,1]
    assert [c['native_sym_id'] for c in native['chain_index_map']]==[0,0,0,1]
    assert all(c['output_asym_id'] is None for c in native['chain_index_map'])


@pytest.mark.asyncio
async def test_selected_snapshot_and_atom_wire_refuse_foreign_identity(tmp_path):
    from services.scientific_viewer_contract import ScientificAtomMetric
    job,design,root=make_publication(tmp_path)
    engine,factory=await store(tmp_path,job,design)
    try:
        async with factory() as session:
            row=await session.get(Design,'candidate')
            selected=await consumer.verified_native_design(row,session)
            payload=(await consumer.compute_persisted_native_metric(row,'residue_plddt',session,selected=selected)).model_dump(mode='json')
            for fault in ('candidate','hash','atom_name','positions','bool'):
                value=deepcopy(payload)
                if fault=='candidate':value['document']['candidateId']='foreign'
                elif fault=='hash':value['axis']['source_sha256']='f'*64
                elif fault=='atom_name':value['axis']['residues'][0]['label_atom_id']=''
                elif fault=='positions':
                    value['axis']['residues'].reverse();value['native_positions'].reverse();value['values'].reverse()
                elif fault=='bool':value['values'][0]=True
                with pytest.raises(ValueError):ScientificAtomMetric.model_validate(value)
            foreign=dict(selected,design_id='foreign')
            assert (await consumer.compute_persisted_native_metric(row,'residue_plddt',session,selected=foreign)).status=='unavailable'
            assert (await consumer.compute_persisted_pae(row,{},session,selected=foreign))[0]['status']=='unavailable'
            from services.analysis_subprocess import _dispatch_ipsae_interface
            assert (await _dispatch_ipsae_interface(row,{},session,selected=selected))[0]['reason']=='unsupported_model_native_spatial_metric'
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_retained_external_publication_no_rewrite(tmp_path):
    fixture=os.environ.get('BMS_PROTENIX_RETAINED_AUDIT')
    if not fixture: pytest.skip('external read-only retained Protenix audit not selected')
    audit=Path(fixture)
    j=json.loads((audit/'rerun-completed.json').read_bytes())
    d=json.loads((audit/'confidence-rca/design-live.json').read_bytes())
    keys=('id','name','model_id','mode','params','provenance','output_dir','status','execution_target_id',
          'execution_source_revision','execution_source_tree','execution_bundle_sha256','remote_attempt_id')
    job=Job(**{k:j[k] for k in keys})
    internal=json.loads(Path(os.environ['BMS_PROTENIX_RETAINED_INTERNAL']).read_bytes())
    assert internal['id']==d['id'] and internal['job_id']==job.id and internal['pdb_path']==d['pdb_path']
    design=Design(**internal,name=d['name'],provenance=d['provenance'],confidence_metrics=d['confidence_metrics'])
    engine,factory=await store(tmp_path,job,design,ingest=False)
    try:
        async with factory() as session:
            row=await session.get(Design,design.id)
            selected=await consumer.verified_native_design(row,session)
            assert len(selected['native']['atom_axis']['residues'])==4746
            assert len(selected['native']['token_axis']['residues'])==588
            assert len({r['label_atom_id'] for r in selected['native']['token_axis']['residues'] if r['chain_id']=='E'})==32
            async with AsyncClient(transport=ASGITransport(app=app_for(session)),base_url='http://test') as client:
                for name in ('residue-metrics','chain-metrics','pae'):
                    response=await client.get(f'/designs/{row.id}/{name}?max_size=588')
                    assert response.status_code==200,response.text
                    payload=response.json(); assert payload['status']=='ok',payload
                    if name == 'pae':
                        assert payload['size'] == 588
                        assert payload['pae_matrix'] == selected['native']['pae']
                    output=os.environ.get('BMS_PROTENIX_RESPONSE_DIR')
                    if output:
                        Path(output).mkdir(parents=True,exist_ok=True)
                        (Path(output)/(name+'.json')).write_text(json.dumps(payload))
                response=await client.get(f'/designs/{row.id}/pdb')
                assert response.status_code==200 and response.content==selected['snapshots']['structure']
            for key,artifact in selected['artifacts'].items():
                assert hashlib.sha256(Path(artifact['path']).read_bytes()).hexdigest()==artifact['sha256']
            assert not session.dirty
    finally: await engine.dispose()
