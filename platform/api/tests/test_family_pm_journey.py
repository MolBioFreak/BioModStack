import pytest
from test_project_workflow_setups import setup_store, _project_payload
from test_native_protein_launch_authority_regression import native_core_store
from test_denovo_project_http import app_for
from routers import projects, jobs
from database import Job
from experiment_models import ExperimentLaunchContext
from httpx import AsyncClient, ASGITransport

@pytest.mark.asyncio
@pytest.mark.parametrize('model,domain,molecule,seq', [('family_msa_protein','protein_in_silico','protein','ACDE'),('family_msa_nucleic','ngs_molbio','dna','ACGT')])
async def test_journey(setup_store,native_core_store,model,domain,molecule,seq):
    app=app_for(setup_store,native_core_store); app.include_router(projects.router); app.include_router(jobs.router, prefix='/api/jobs')
    @app.middleware('http')
    async def launch_transport(request, call_next):
        token = jobs.current_launch_context_id.set(request.headers.get('X-BMS-Launch-Context'))
        try: return await call_next(request)
        finally: jobs.current_launch_context_id.reset(token)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        r=await c.post('/api/projects',json=_project_payload('Scratch family')); assert r.status_code==201, r.text
        pid=r.json()['id']; root=f'/api/projects/{pid}/workflow-setups'
        r=await c.post(root,json={'schema':'bms.project-workflow-setup.create.v1','relationship_kind':'primary','experiment':{'name':'Family','objective':'Scratch'},'domain_kind':domain,'capability_id':model},headers={'Idempotency-Key':'create'}); assert r.status_code==201, r.text
        setup=r.json(); url=root+'/'+setup['setup_context_id']
        fasta=f'>a\n{seq}\n>b\n{seq}\n'
        params={'roster':{'molecule':molecule,'rows':[{'row_id':'row_00000001','header':'a','sequence':seq},{'row_id':'row_00000002','header':'b','sequence':seq}],'source_format':'fasta','source_text':fasta},'settings':{'engine':'mafft','threads':1,'strategy':'fft_ns_2','treeout':False}}
        request={'name':'Family alignment','model_id':model,'mode':'family_msa','params':params}
        r=await c.put(url+'/draft',json={'expected_generation':0,'draft':{'native_job_request':request,'editor_state':{'molecule':molecule,'engine':'mafft','fasta':fasta}}},headers={'Idempotency-Key':'save'}); assert r.status_code==200, r.text
        r=await c.get(url); assert r.status_code==200, r.text; print('DRAFT READBACK', model, r.json()['draft'])
        r=await c.post(url+'/prepare-launch',json={'expected_generation':1},headers={'Idempotency-Key':'prepare'}); assert r.status_code==200, r.text
        prepared=r.json(); domainpath=f"/api/projects/{pid}/experiments/{setup['global_experiment_id']}/domains/{setup['domain_experiment_id']}"
        r=await c.post(domainpath+'/run-groups',json={'preparation_launches':[{'preparation_id':prepared['preparation_id'],'launch_context_id':prepared['launch_context_id']}]},headers={'Idempotency-Key':'run'}); assert r.status_code==201, r.text
        context_id=prepared['launch_context_id']
        r=await c.get('/api/launch-contexts/'+context_id); assert r.status_code==200, r.text
        scheduler=r.json()['pinned_scheduler']
        r=await c.post('/api/jobs',json={**scheduler,'launch_context_id':context_id},headers={'X-BMS-Launch-Context':context_id}); assert r.status_code==201, r.text
        job_id=r.json()['id']
        r=await c.post('/api/launch-contexts/'+context_id+'/bind',json={'job_id':job_id}); assert r.status_code==200, r.text
        assert r.json()['return_uri'].startswith('/projects/')
        # Simulate a lost bind response: repeat against the actual PM authority.
        r=await c.post('/api/launch-contexts/'+context_id+'/bind',json={'job_id':job_id}); assert r.status_code==200, r.text
        r=await c.get('/api/launch-contexts/'+context_id); assert r.status_code==200, r.text
        assert r.json()['canonical_job_id']==job_id
        async with native_core_store() as core:
            row=await core.get(Job,job_id); assert row and row.model_id==model and row.mode=='family_msa'
        async with setup_store() as store:
            ctx=await store.get(ExperimentLaunchContext,context_id); assert ctx.canonical_job_id==job_id
        print('CANONICAL',model,job_id,ctx.state)

