"""Native publication -> Frustra -> whole importer regression, science doubled only."""
from __future__ import annotations

import base64
import hashlib
from io import StringIO
import json
from pathlib import Path
import shutil

from Bio.PDB import MMCIFIO, PDBParser
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, FrustraMPNNResult, Job
from services.frustrampnn.contracts import canonical_json_bytes
from services.frustrampnn.persistence import FrustraMPNNPersistenceError
from services.frustrampnn.settings import FrustraMPNNRequestedSettings, requested_settings_sha256
from services.result_ingester import ingest_job_results
from scripts import prepare_frustrampnn_candidate as preparer
from scripts import publish_remote_frustrampnn_bundle as publisher
from scripts import run_frustrampnn_component as component
from scripts import write_structure_producer_manifest as producer
from test_frustrampnn_component_phase3 import _mock_v2_runtime
from test_structure_prediction_frustrampnn_v2_transport import _two_model_pdb, _settings_transport_bytes


from test_remote_lifecycle_gaps import store


def make_publication(tmp_path, monkeypatch, *, historical=True, count=2, job_id='protenix-publication-parent'):
    """Reproduce nested native Protenix paths using the real publication writer."""
    task=tmp_path/'native-task'; predictions=task/'predictions'
    sample='sequence-A'
    for i in range(count):
        source=predictions/sample/'seed_42'/'predictions'/f'{sample}_sample_{i}.cif'
        source.parent.mkdir(parents=True,exist_ok=True)
        structure=PDBParser(QUIET=True).get_structure(f'sample-{i}',StringIO(_two_model_pdb().decode()))
        for atom in structure.get_atoms():atom.coord[0]+=i*.25
        writer=MMCIFIO();writer.set_structure(structure);writer.save(str(source))
        # PDB has no entity metadata; this single-entity fixture declares it.
        from Bio.PDB.MMCIF2Dict import MMCIF2Dict
        cif=MMCIF2Dict(str(source))
        cif['_atom_site.label_entity_id']=['1']*len(cif['_atom_site.id'])
        cif['_atom_site.id']=[str(n+1) for n in range(len(cif['_atom_site.id']))]
        cif['_atom_site.auth_atom_id']=list(cif['_atom_site.label_atom_id'])
        cif['_atom_site.auth_comp_id']=list(cif['_atom_site.label_comp_id'])
        writer.set_dict(cif);writer.save(str(source))
    metadata=dict(producer_artifact_id=sample,producer_artifact_key=sample,producer_sample=sample,
        producer_sequence='G',producer_fold=None,producer_rank=None,producer_submission_id=sample,
        producer_submission_name=sample,original_submission_identity={'id':sample,'name':sample})
    native=producer.build_manifest(predictions_root=predictions,producer_method='protenix',
        producer_sample=None,formats=['mmcif'])
    manifest=producer.sequence_manifest(native,metadata)
    raw=(json.dumps(manifest,sort_keys=True,separators=(',',':'))+'\n').encode()
    root=tmp_path/'returned';root.mkdir()
    producer.write_publication(manifest_bytes=raw,native_candidates=native['candidates'],manifest=manifest,
        predictions_root=predictions,publication_dir=root/'run/protenix/producer',
        published_structure_root='pdb_files/predictions')
    settings=FrustraMPNNRequestedSettings.model_validate(dict(schema_name='frustrampnn_settings',
        schema_version=2,settings_value_origin='operator_request',batching_enabled=False,structures_per_job=25,
        protein_selection={'mode':'selected_residues','entities':[], 'regions':[], 'residues':[
            {'entity_instance_id':'mmcif:1:A:A','source_entity_id':'1','label_asym_id':'A',
             'auth_asym_id':'A','auth_seq_id':10,'insertion_code':'A','sequence_index':1}]},
        source_structure={'selected_model_number':1,'preferred_altloc':''},
        classification_policy={'mode':'canonical','high_max':-1.0,'minimal_min':0.58}))
    _mock_v2_runtime(component,monkeypatch,tmp_path)
    outputs=[];requests=[]
    for i,(original,record) in enumerate(zip(native['candidates'],manifest['candidates'],strict=True)):
        source=predictions/original['producer_output_key']
        published=root/'pdb_files/predictions'/source.name
        published.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,published)
        published.with_name(f'{sample}_summary_confidence_sample_{i}.json').write_text(
            json.dumps({'plddt':90.0,'ptm':0.9,'ranking_score':0.18,'has_clash':False}))
        identity={k:record[k] for k in ('producer_method','producer_sample','producer_rank','producer_output_key')}
        if historical:identity['producer_output_key']=f'{sample}/{source.name}'
        identity_sha=preparer.producer_identity_sha256(identity)
        meta=dict(parent_job_id=job_id,parent_workflow_id='structure_prediction',
            producer_stage='structure_prediction:protenix',
            producer_candidate_key=f'frustrampnn/sources/protenix/{sample}.{identity_sha[:16]}.normalized.pdb',
            requiredness='required',**identity,producer_identity_sha256=identity_sha,
            producer_artifact_sha256=record['producer_artifact_sha256'],source_format='mmcif')
        work=tmp_path/f'frustra-{i}';work.mkdir()
        decoded=preparer._decode_metadata(base64.b64encode(canonical_json_bytes(meta)).decode(),source=source,request_version=3)
        preparer.prepare_candidate(source=source,output_pdb=work/'normalized.pdb',
            request_path=work/'request.json',metadata=decoded,request_version=3,
            structure_map_path=work/'map.json',settings_payload=_settings_transport_bytes(settings),
            settings_sha256=requested_settings_sha256(settings),settings_value_origin='operator_request')
        request=json.loads((work/'request.json').read_bytes());requests.append(request)
        bundle=work/'bundle'
        component.run_component(request=request,request_payload=canonical_json_bytes(request),
            source_structure=work/'normalized.pdb',structure_map=work/'map.json',
            output_dir=bundle,container=tmp_path/'mock.sif',physical_gpu_id=0)
        monkeypatch.setenv('BMS_REMOTE_EXECUTION','1')
        marker=publisher.publish_remote(source_bundle=bundle,allowed_root=root,
            destination=root/'frustrampnn/results'/request['candidate_id'],marker=work/'published.json')
        outputs.extend([str(root/marker['result']),str(root/marker['manifest'])])
    params=dict(run_frustrampnn=True,frustrampnn_requiredness='required',
        frustrampnn_settings=settings.model_dump(mode='json',exclude_none=False),
        sequence='G',sequence_name=sample,pred_method='protenix')
    job=Job(id=job_id,name='publication regression',model_id='protenix',mode='predict',params=params,
        status='running',queue_status='running',output_dir=str(root),
        stage_outputs={'frustrampnn':outputs},completed_stages=['frustrampnn'],awaiting_input=False)
    return job,root,requests,manifest


@pytest.mark.asyncio
@pytest.mark.parametrize('historical',[True,False])
async def test_whole_native_import_preserves_publication_and_frustra_identity(tmp_path,monkeypatch,historical):
    job,root,requests,manifest=make_publication(tmp_path,monkeypatch,historical=historical)
    before={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    engine=create_async_engine(f'sqlite+aiosqlite:///{tmp_path/"case.sqlite"}')
    try:
        async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
        sessions=async_sessionmaker(engine,expire_on_commit=False)
        async with sessions() as session:
            session.add(job);await session.commit()
            assert await ingest_job_results(job.id,str(root),session)==2
        async with sessions() as session:
            designs=list((await session.execute(select(Design).where(Design.job_id==job.id))).scalars())
            results=list((await session.execute(select(FrustraMPNNResult).where(FrustraMPNNResult.parent_job_id==job.id))).scalars())
            assert len(designs)==len(results)==2
            assert {r.design_id for r in results}=={r.id for r in designs}
            assert {r.candidate_id for r in results}=={r['candidate_id'] for r in requests}
            assert {d.provenance['native_producer']['producer_output_key'] for d in designs}=={c['producer_output_key'] for c in manifest['candidates']}
            snapshots={d.id:d.provenance['native_producer'] for d in designs}
            from services.result_ingester import _ingest_explicit_frustrampnn_results
            loaded=await session.get(Job,job.id)
            assert await _ingest_explicit_frustrampnn_results(loaded,root,session,commit=False)==0
            await session.commit()
            assert {d.id:d.provenance['native_producer'] for d in designs}==snapshots
        assert all(hashlib.sha256((root/name).read_bytes()).hexdigest()==sha for name,sha in before.items())
    finally:await engine.dispose()


@pytest.mark.parametrize('fault',[
    'absent_inventory','missing_native_metadata','foreign_sample','foreign_rank',
    'foreign_prefix','format','original_hash','undeclared_native_candidate','wrong_design_path',
    'duplicate_inventory','publication_bytes','manifest_bytes','structure_bytes',
    'manifest_symlink','duplicate_binding','wrong_bound_path','foreign_manifest_descriptor',
])
def test_legacy_recovery_requires_complete_exact_publication_custody(tmp_path,monkeypatch,fault):
    from copy import deepcopy
    from types import SimpleNamespace
    from services.core_protein_result_contract import _artifact
    from services.frustrampnn.persistence import _verified_legacy_protenix_sequence_link
    from services.frustrampnn.manifests import ManifestValidationError

    job,root,requests,manifest=make_publication(tmp_path,monkeypatch,count=1)
    publication_path=next((root/'run/protenix/producer').glob('*/publication.json'))
    publication=json.loads(publication_path.read_bytes())
    manifest_path=root/publication['producer_manifest']['relative_path']
    structure_path=root/publication['bindings'][0]['published_relative_path']
    item={'publication':_artifact(root,str(publication_path))[0],
          'producer_manifest':_artifact(root,str(manifest_path))[0]}
    job.provenance={'protenix_primary_publication':[item]}
    native=deepcopy(manifest['candidates'][0])
    declared=deepcopy(requests[0]['producer_provenance'])
    design=Design(id='primary',job_id=job.id,name=structure_path.stem,pdb_path=str(structure_path))
    assert _verified_legacy_protenix_sequence_link(job,design,root,native,declared)
    if fault=='absent_inventory':job.provenance={}
    elif fault=='missing_native_metadata':native.pop('producer_artifact_key')
    elif fault=='foreign_sample':declared['producer_sample']='foreign'
    elif fault=='foreign_rank':declared['producer_rank']=999
    elif fault=='foreign_prefix':declared['producer_output_key']='foreign/'+structure_path.name
    elif fault=='format':declared['original_source_format']='pdb'
    elif fault=='original_hash':declared['original_source_sha256']='0'*64
    elif fault=='undeclared_native_candidate':native['producer_sequence']='A'
    elif fault=='wrong_design_path':
        foreign=root/'foreign'/structure_path.name;foreign.parent.mkdir();shutil.copyfile(structure_path,foreign)
        design.pdb_path=str(foreign)
    elif fault=='duplicate_inventory':job.provenance={'protenix_primary_publication':[item,item]}
    elif fault=='publication_bytes':publication_path.write_bytes(publication_path.read_bytes()+b' ')
    elif fault=='manifest_bytes':manifest_path.write_bytes(manifest_path.read_bytes()+b' ')
    elif fault=='structure_bytes':structure_path.write_bytes(structure_path.read_bytes()+b'# changed\n')
    elif fault=='manifest_symlink':
        retained=tmp_path/'same-manifest.json';shutil.copyfile(manifest_path,retained)
        manifest_path.unlink();manifest_path.symlink_to(retained)
    elif fault in {'duplicate_binding','wrong_bound_path','foreign_manifest_descriptor'}:
        if fault=='duplicate_binding':publication['bindings'].append(deepcopy(publication['bindings'][0]))
        elif fault=='wrong_bound_path':publication['bindings'][0]['published_relative_path']='foreign/'+structure_path.name
        else:publication['producer_manifest']['relative_path']='foreign/producer_candidates.json'
        publication_path.write_bytes(canonical_json_bytes(publication))
        item['publication']=_artifact(root,str(publication_path))[0]
    try:
        accepted=_verified_legacy_protenix_sequence_link(job,design,root,native,declared)
    except (FrustraMPNNPersistenceError,ManifestValidationError):
        accepted=False
    assert not accepted, fault


@pytest.mark.asyncio
@pytest.mark.parametrize('pull_mode',['manual','automatic'])
async def test_builtin_return_native_import_failure_rollback_then_retained_retry(store,tmp_path,monkeypatch,pull_mode):
    from fastapi import BackgroundTasks
    from database import ExecutionTarget
    from services import analysis_autorun
    from services.frustrampnn import persistence
    from services.remote_execution import executor as ex, result_generation as gen
    from services.remote_execution.contracts import RemoteResultManifest
    from services.remote_stage_receipts import write_remote_stage_receipt
    from test_remote_manual_result_pull import ready, success

    import uuid
    attempt=str(uuid.uuid4())
    fixture,root,requests,_=make_publication(tmp_path,monkeypatch,count=1,job_id='job')
    async with store() as session:
        job=await session.get(Job,'job')
        job.remote_attempt_id=attempt;job.nextflow_run_id=f'remote:{attempt}'
        await session.commit()
    for key,value in {'BMS_REMOTE_EXECUTION':'1','BMS_REMOTE_JOB_ID':'job',
                      'BMS_REMOTE_ATTEMPT_ID':attempt,'BMS_REMOTE_OUTPUT_ROOT':str(root)}.items():
        monkeypatch.setenv(key,value)
    write_remote_stage_receipt(job_id='job',stage='frustrampnn',status='complete',
        outputs=[Path(path).relative_to(root).as_posix() for path in fixture.stage_outputs['frustrampnn']],
        job_root_relative=True)
    before={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    await ready(store)
    async with store() as session:
        job=await session.get(Job,'job')
        job.output_dir=str(tmp_path/'published')
        job.model_id=fixture.model_id;job.mode=fixture.mode
        job.params=dict(fixture.params,remote_result_policy=pull_mode)
        status=success().model_copy(update={'attempt_id':attempt})
        manifest=RemoteResultManifest(job_id=job.id,attempt_id=job.remote_attempt_id,
            source_revision=job.execution_source_revision,source_tree=job.execution_source_tree,
            execution_envelope_sha256=job.execution_bundle_sha256,exit_code=0,completed_at=status.completed_at,
            artifacts=[dict(relative_path=name,size_bytes=(root/name).stat().st_size,sha256=sha,role='result')
                for name,sha in sorted(before.items())])
        raw=manifest.model_dump_json().encode();digest=hashlib.sha256(raw).hexdigest()
        incoming=gen.staging_path(job,digest)
        shutil.copytree(root,incoming);(incoming/'result-manifest.json').write_bytes(raw)
        status=status.model_copy(update={'result_manifest_sha256':digest})
        contract=ex.resolve_job_result_contract(job)
        job.provenance={'remote_execution_receipt':{
            **{key:value for key,value in ex._pull_identity(job).items() if key!='schema'},
            'state':status.state,'exit_code':0,'remote_attempt_dir':'/fixture/attempt',
            'result_manifest_sha256':digest,
            'expected_result_contract_sha256':hashlib.sha256(canonical_json_bytes(contract)).hexdigest()}}
        Path(job.output_dir).mkdir();(Path(job.output_dir)/'prior-good.txt').write_text('retained')
        await session.commit()
    calls=[]
    async def proof(*_):calls.append('proof')
    async def remote_status(*_):calls.append('status');return status
    async def collect(session,job,status):
        calls.append('collect');ex._verify_result_package(incoming,job,status);return manifest,incoming
    monkeypatch.setattr(ex,'_prove_pull_endpoint',proof)
    monkeypatch.setattr(ex,'remote_status',remote_status)
    monkeypatch.setattr(ex,'collect_remote_results',collect)
    monkeypatch.setattr(analysis_autorun,'schedule_viewer_minimum_analyses_for_job',lambda *_:None)
    for retry in range(2):
        tasks=BackgroundTasks()
        async with store() as session:
            job=await session.get(Job,'job')
            if pull_mode=='automatic' and retry==0:
                assert await ex.reconcile_remote_job(session,job,background_tasks=tasks)
            else:
                assert not await ex.reconcile_remote_job(session,job,background_tasks=BackgroundTasks())
                await ex.request_remote_result_pull(session,job,tasks)
        with monkeypatch.context() as scoped:
            if retry==0:
                # Reproduce the old exact-key-only association at the actual owner;
                # all native ingestion, transactions and publication remain real.
                scoped.setattr(persistence,'_verified_legacy_protenix_sequence_link',lambda *_:False)
            await tasks()
        async with store() as session:
            job=await session.get(Job,'job')
            count=await session.scalar(select(func.count()).select_from(Design).where(Design.job_id=='job'))
            frustra_count=await session.scalar(select(func.count()).select_from(FrustraMPNNResult).where(FrustraMPNNResult.parent_job_id=='job'))
            assert calls==['proof','status','collect'], calls
            if retry==0:
                assert count==frustra_count==0
                assert job.remote_state=='result_pull_failed' and job.awaiting_input
                assert (Path(job.output_dir)/'prior-good.txt').read_text()=='retained'
                assert all(hashlib.sha256((incoming/name).read_bytes()).hexdigest()==sha for name,sha in before.items())
            else:
                assert (job.status,job.remote_state,job.awaiting_input)==('completed','ingested',False),job.error_message
                assert count==frustra_count==1
                assert not gen.journal_path(job).exists()
                assert all(hashlib.sha256((Path(job.output_dir)/name).read_bytes()).hexdigest()==sha for name,sha in before.items())
                assert (await session.get(ExecutionTarget,'target')).leased_job_id=='other-job'
                with pytest.raises(ex.RemoteExecutionError):
                    await ex.request_remote_result_pull(session,job,BackgroundTasks())
