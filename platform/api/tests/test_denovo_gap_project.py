"""Integrated private HTTP persistence and Project-bound queue dispatch, not inference."""
import copy
import hashlib
import json
import os
from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient
from database import Job
from experiment_models import ExperimentWorkflowPreparation, ExperimentLaunchContext
from test_project_workflow_setups import setup_store, _project_payload
from test_project_generation_bound_jobs import isolated_core
from test_project_setup_job_preparation import isolate_applied_local_policy
from test_denovo_project_http import app_for, envelope
from test_binder_continuation import PDB


def authored(case, tmp, monkeypatch):
    from routers import jobs, files
    import paths
    roots={'inputs':tmp/'owned/inputs','bms_results':tmp/'owned/results'}
    for module in (paths,jobs,files): monkeypatch.setattr(module,'get_allowed_roots',lambda:roots)
    source=roots['inputs']/'source.pdb'; source.write_text('MODEL        2\n'+PDB.replace('END\n','ENDMDL\nEND\n'))
    request={'name':case,'model_id':'protein_modification_experimental','mode':'de_novo_design',
        'execution_target_id':None,'execution_policy':{'remote_result_policy':'manual'},'params':{}}
    capability='protein.de_novo.'+case
    if case=='rfd3':
        request['params']={'generator':'rfd3','min_length':40,'max_length':40,'seed':0,
            'generation_mode':'unconditional_monomer','skip_existing':False,
            'conditioning':{'symmetry':{'id':'C2','is_symmetric_motif':False,'is_unsym_motif':False}}}
    elif case in ('la-override','la-named'):
        capability='protein.de_novo.laproteina'
        request['params']={'generator':'laproteina','backend':'laproteina','design_task':'motif_scaffolding',
            'num_designs':1,'seed':0,'laproteina_preset':'motif_idx_aa',
            'pcad_laproteina_settings_json':{'contig_string':'A1-1','motif_atom_spec':None}}
        if case=='la-override':request['params']['laproteina_motif_pdb']=str(source)
        else:request['params']['laproteina_motif_task_name']='1YCR_AA'
    elif case=='disco':
        owner=roots['inputs']/'jobs.json'; owner.write_text('[{"name":"original","sequences":[]}]')
        ligand=roots['inputs']/'ligand.sdf'; ligand.write_text('inert transport bytes\n')
        request['params']={'generator':'disco','backend':'disco','design_task':'custom_json','seed':0,
            'pcad_disco_jobs_source_path':str(owner),'pcad_disco_input_json_path':str(roots['inputs']/'missing.json'),
            'pcad_disco_jobs_json':[{'name':'edited','sequences':[{'ligand':{'ligand':'FILE_ligand.sdf','count':1}}]}]}
    elif case=='la-evaluate':
        from test_laproteina_evaluation_route import payload
        request.update(payload()); request['execution_policy']={'remote_result_policy':'manual'}
        samples=roots['inputs']/'native'; samples.mkdir(); (samples/'motif_info.csv').write_text('name,ancestry\noriginal,retained\n')
        (roots['inputs']/'original.yaml').write_text('generation:\n  metric:\n    compute_codesignability: true\n')
        capability='protein.native.laproteina.evaluate'
    else:
        evidence=Path(os.environ['BMS_GAP_LANE_EVIDENCE'])
        if case=='rfd4-edited':
            draft=json.loads((evidence/'rfd4/template-capture.json').read_text())['rfd4_proteina:generate']
            return 'protein.native.rfd4_proteina.generate',draft
        wire=json.loads((evidence/'results/results-native_CandidateRef_only__actual_Results_emits_matching_ordinary_retained_source_and_receiver_keeps_seed_zero.json').read_text())
        request=next(r['body'] for r in wire if r['url']=='/api/jobs')
        # Mounted symbolic artifact/hash placeholders are completed with private
        # physical bindings. Native request/seed/resume slot remain byte-for-byte.
        state=roots['inputs']/'B.pt'; state.write_bytes(b'INERT retained state B; never unpickled')
        request.pop('source_structure')
        request['params']['native_sources']={'selected':{'path':'inputs/source.pdb','model_number':2,'expected_sha256':hashlib.sha256(source.read_bytes()).hexdigest()},
            'resume':{'kind':'file','path':'inputs/B.pt','expected_sha256':hashlib.sha256(state.read_bytes()).hexdigest()}}
        capability='protein.native.rfd4_proteina.generate'
    return capability,envelope(request)


@pytest.mark.asyncio
@pytest.mark.parametrize('case',['rfd3','la-override','la-named','disco','rfd4-edited','rfd4-retained','la-evaluate'])
async def test_gap_http_save_twice_cold_prepare_and_bound_dispatch(case,tmp_path,monkeypatch,isolated_core,setup_store,isolate_applied_local_policy):
    if case.startswith('rfd4') and not os.environ.get('BMS_GAP_LANE_EVIDENCE'):
        pytest.skip('Requires actual mounted lane envelopes')
    capability,draft=authored(case,tmp_path,monkeypatch)
    from routers import jobs,projects
    app=app_for(setup_store,isolated_core); app.include_router(projects.router); app.include_router(jobs.router,prefix='/api/jobs')
    @app.middleware('http')
    async def launch(request,call_next):
        token=jobs.current_launch_context_id.set(request.headers.get('X-BMS-Launch-Context'))
        try:return await call_next(request)
        finally:jobs.current_launch_context_id.reset(token)
    async with AsyncClient(transport=ASGITransport(app),base_url='http://private') as client:
        response=await client.post('/api/projects',json=_project_payload('Gap integrated'))
        assert response.status_code==201,response.text
        project_id=response.json()['id']; root=f'/api/projects/{project_id}/workflow-setups'
        response=await client.post(root,json={'schema':'bms.project-workflow-setup.create.v1','relationship_kind':'primary',
            'experiment':{'name':case,'objective':'Exact receiving'},'domain_kind':'protein_in_silico','capability_id':capability},headers={'Idempotency-Key':'create'})
        assert response.status_code==201,response.text
        url=root+'/'+response.json()['setup_context_id']
        for n in (0,1):
            response=await client.put(url+'/draft',json={'expected_generation':n,'draft':draft},headers={'Idempotency-Key':f'save-{n}'})
            assert response.status_code==200,response.text
            assert response.json()['draft']=={**draft['editor_state'],'native_job_request':draft['native_job_request']}
        template=await client.post('/api/user-templates',json={'name':case,'model_id':draft['native_job_request']['model_id'],'mode':draft['native_job_request']['mode'],'params':draft})
        assert template.status_code in (200,201),template.text
        tid=template.json()['id']
        response=await client.put('/api/user-templates/'+tid,json={'params':draft}); assert response.status_code==200,response.text
    await setup_store.kw['bind'].dispose(); await isolated_core.kw['bind'].dispose()
    async with AsyncClient(transport=ASGITransport(app),base_url='http://cold') as client:
        assert (await client.get('/api/user-templates/'+tid)).json()['params']==draft
        cold=await client.get(url); assert cold.json()['draft']['native_job_request']==draft['native_job_request']
        prepared=await client.post(url+'/prepare-launch',json={'expected_generation':2},headers={'Idempotency-Key':'prepare'})
        assert prepared.status_code==200,prepared.text
        prepared=prepared.json()
        async with setup_store() as exp:
            row=await exp.get(ExperimentWorkflowPreparation,prepared['preparation_id'])
            scheduler=json.loads(row.scheduler_payload_json)
            from experiment_services import create_run_group,load_workflow_plan_authority
            await create_run_group(exp,project_id,[row.resource_id],idempotency_key='run',launch_context_ids={row.resource_id:prepared['launch_context_id']})
            authority=await load_workflow_plan_authority(exp,prepared['diagnostics']['workflow_id'])
            submission=copy.deepcopy(authority[1]['capability']['normalized_job_request'])
            submission['params']['workflow_adapter']=authority[1]['capability']['workflow_adapter_id']
            await exp.commit()
        response=await client.post('/api/jobs',json={**submission,'launch_context_id':prepared['launch_context_id']},headers={'X-BMS-Launch-Context':prepared['launch_context_id']})
        assert response.status_code==201,response.text
        job_id=response.json()['id']; assert response.json()['launch_context_binding']
        assert (await client.get(url)).json()['state']=='submitted'
    async with isolated_core() as core,setup_store() as exp:
        job=await core.get(Job,job_id); context=await exp.get(ExperimentLaunchContext,prepared['launch_context_id'])
        from services.global_experiments.launch_contexts import validate_bound_job
        await validate_bound_job(exp,context,job)
        assert context.canonical_job_id==job_id
        evidence_root=os.environ.get('BMS_GAP_INTEGRATED_EVIDENCE')
        if evidence_root:
            out=Path(evidence_root); out.mkdir(parents=True,exist_ok=True)
            (out/(case+'-project.json')).write_text(json.dumps({'authored':draft,
                'preparation':prepared,'scheduler':scheduler,'plan':authority[1],
                'job_id':job_id,'job_params':job.params,'launch_context_id':context.launch_context_id},indent=2))
        for key in ('rfd4_request','native_sources','laproteina_evaluation_request'):
            if key in submission['params']:assert job.params[key]==submission['params'][key]==scheduler['params'][key]
        if case=='rfd4-retained':
            assert job.params['rfd4_request']['settings']['seed']==0
            assert job.params['native_sources']['selected']['model_number']==2
            assert job.params['native_sources']['resume']['expected_sha256']==draft['native_job_request']['params']['native_sources']['resume']['expected_sha256']
            from paths import resolve_allowed_path
            assert Path(resolve_allowed_path(job.params['native_sources']['resume']['path'])).read_bytes()==b'INERT retained state B; never unpickled'
        if case=='rfd3':
            assert job.params['rfd3_generation_request']['execution']['skip_existing'] is False
            assert job.params['rfd3_generation_request']['conditioning']==draft['native_job_request']['params']['conditioning']
            from services.global_experiments.launch_contexts import LaunchContextError
            original=copy.deepcopy(job.params)
            for field,value in [('job_id','wrong-job'),('conditioning',{}),('seed',99)]:
                changed=copy.deepcopy(original)
                changed['rfd3_generation_request'][field]=value
                job.params=changed
                with pytest.raises(LaunchContextError,match='Job does not match'):
                    await validate_bound_job(exp,context,job)
                job.params=copy.deepcopy(original)
            await validate_bound_job(exp,context,job)
