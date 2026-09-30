"""Shared production control receivers; science/process leaves are explicitly inert."""
from dataclasses import replace
import hashlib
import json
import multiprocessing
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from component_runtime import SourceIdentity, ResultReference, canonical_bytes
from services import nextflow
from services.remote_execution.targets import selected_plan_target_resources
from scripts.lib import component_adapter as adapter
from scripts.bms_md import spawn_replicas
from tools import bms_remote_worker as worker


@pytest.fixture
def retained(tmp_path, monkeypatch, request):
    target_id=getattr(request,"param","local")
    if target_id != "local":
        tmp_path=tmp_path/"results"; tmp_path.mkdir()
    import model_registry
    monkeypatch.setattr(model_registry, 'molecular_dynamics_feature_enabled', lambda: True)
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path))
    config = dict(random_seed=71, replicas=2, execution=dict(gpu_id=0), engine='gromacs')
    config_path = tmp_path/'config.json'; config_path.write_bytes(canonical_bytes(config))
    params = dict(md_job_config=str(config_path), md_job_spec=config)
    invocation = nextflow.compile_nextflow_invocation('molecular_dynamics', 'simulate', params,
        str(tmp_path), job_id='parent', source_identity=SourceIdentity('a'*40, 'b'*40),
        execution_context=nextflow.NativeCompilerExecutionContext(gpu_id=0, gpu_ids=(0,), anarcii_execution_mode='cpu'))
    invocation.materialize_inputs(tmp_path)
    resources = selected_plan_target_resources(SimpleNamespace(id=target_id), invocation.execution_plan, gpu_ids=[0], scratch_bytes=0)
    resources.update(gpu_id=0, admission_required=False)
    job = SimpleNamespace(id='parent',root_job_id=None,model_id='molecular_dynamics',mode='simulate',
        params=params,provenance={},status='running',assigned_gpu=0,execution_target_id=None if target_id=='local' else target_id,child_output_dir=None)
    path=tmp_path/'context.json'
    context=nextflow.component_launch_context(invocation,job,command=invocation.command,
        context_path=path,artifact_root=tmp_path,working_directory=tmp_path,
        attempt_id='attempt',target_id=target_id,lease_id='original-lease',resources=resources)
    context['native_runtime']={'anarcii_execution_mode':'cpu','anarcii_gpu_id':None}
    path.write_bytes(canonical_bytes(context))
    monkeypatch.setenv('BMS_COMPONENT_CONTEXT',str(path))
    runtime=adapter.runtime_from_environment(path)
    metadata=tmp_path/'metadata.json'; metadata.write_bytes(canonical_bytes(config))
    receipt=spawn_replicas.spawn_replicas(parent_job_id='parent',parent_name='MD',normalized_config=config_path,
        metadata_path=metadata,preparation_bundle=tmp_path/'preparation',api_url='http://unavailable.invalid')
    return path, runtime, receipt, invocation, resources


def pause_fixture(retained):
    path, runtime, receipt, invocation, resources = retained
    boot=worker.boot_id()
    child=receipt['children'][0]['id']
    runtime.claim_root(owner_id='owner',boot_id=boot)
    runtime.set_root_state('running',owner_id='owner',boot_id=boot,quiescent=False,generation=0)
    runtime.claim(child,owner_id='owner',boot_id=boot)
    runtime.request_md_pause('pause-one',boot_id=boot)
    output=runtime.artifact_root/'components'/child.replace(':','-')/'native'
    output.mkdir(parents=True)
    data=b'inert checkpoint step 20'; sha=hashlib.sha256(data).hexdigest()
    checkpoint=output/'.bms-checkpoints'/'segment-zero'/(sha+'.cpt')
    checkpoint.parent.mkdir(parents=True); checkpoint.write_bytes(data)
    params=runtime.request(child).payload['params']
    body=dict(schema='bms.md.checkpoint-receipt.v1',checkpoint_path='production/production.cpt',
        execution_plan_sha256=params['md_execution_plan_sha256'],compatibility_key=params['md_compatibility_key'],
        step=20,time_ps=0.04,bytes=len(data),sha256=sha)
    (output/'production').mkdir(); (output/'production/production.cpt').write_bytes(data)
    (output/'md-checkpoint-receipt.json').write_bytes(canonical_bytes(body))
    value=dict(md_resume_checkpoint=str(checkpoint),md_resume_checkpoint_sha256=sha,
               md_resume_output_dir=str(output), receipt=body,receipt_bytes=len(canonical_bytes(body)),output_dir=str(output.parent))
    runtime.md_execution_paused(child, owner_id='owner', boot_id=boot, checkpoint=value)
    runtime.set_root_state('paused',owner_id='owner',boot_id=boot,quiescent=True,generation=0,
        md_checkpoints={child:value})
    return boot, child, value


def test_actual_continuation_compiler_same_roster_and_no_preparation(retained):
    path, runtime, receipt, original, resources=retained
    boot, child, value=pause_fixture(retained)
    immutable=path.read_bytes()
    state=adapter.resume_md_workflow(path,operation_id='resume-one',pause_operation_id='pause-one',
        boot_id=boot,continuation_lease_id='renewed',resources=resources)
    edge=state['continuation_edge']
    assert state['state']=='resume_ready' and state['generation']==1
    assert edge['md_resume'][child]==value
    assert runtime.group_children('parent:md_replica')==tuple(row['id'] for row in receipt['children'])
    assert runtime.pending()==tuple(row['id'] for row in receipt['children'])
    assert runtime.request(child).payload['params']['md_replica_seed']==71
    native=json.loads(Path(edge['native_parameters']['md_retry_spawn_receipt']).read_bytes())
    assert [row['id'] for row in native['children']]==[row['id'] for row in receipt['children']]
    assert [row['replica_seed'] for row in native['children']]==[71,72]
    assert 'md_retry_spawn_receipt' in edge['native_parameters']
    authorities=[row['authority'] for row in edge['execution_plan']['metadata']['static_components']]
    assert not any('Prepare' in row for row in authorities)
    assert any('ANALYSIS' in row.upper() or 'ANALYZE' in row.upper() for row in authorities)
    assert path.read_bytes()==immutable
    assert adapter.resume_md_workflow(path,operation_id='resume-one',pause_operation_id='pause-one',
        boot_id=boot,continuation_lease_id='renewed',resources=resources)==state
    assert len(runtime.children(include_replaced=True))==2


@pytest.mark.parametrize('change',['boot','lease','checkpoint','science'])
def test_production_continuation_conflicts_preserve_pause(retained, change):
    path,runtime,receipt,original,resources=retained
    boot,child,value=pause_fixture(retained)
    checkpoints={child:dict(value)}
    if change=='boot': boot='foreign-boot'
    if change=='checkpoint': checkpoints[child]['md_resume_checkpoint_sha256']='c'*64
    if change=='science': checkpoints[child]['random_seed']=9
    lease='' if change=='lease' else 'renewed'
    with pytest.raises((ValueError,KeyError)):
        adapter.resume_md_workflow(path,operation_id='resume-one',pause_operation_id='pause-one',
            boot_id=boot,continuation_lease_id=lease,resources=resources,checkpoints=checkpoints)
    assert runtime.root_state()['state']=='paused'
    assert runtime.child_status(child)['status']=='paused'


def test_worker_pause_receiver_and_boot_lease_fences(retained,tmp_path,monkeypatch):
    path,runtime,receipt,original,resources=retained
    child=receipt['children'][0]['id']; boot=worker.boot_id()
    runtime.claim_root(owner_id='owner',boot_id=boot)
    runtime.set_root_state('running',owner_id='owner',boot_id=boot,quiescent=False)
    runtime.claim(child,owner_id='owner',boot_id=boot)
    envelope={'environment':{'BMS_COMPONENT_CONTEXT':str(path)}, 'attempt_id':'attempt', 'job_id':'parent',
        'output_directory':str(tmp_path), 'working_directory':str(tmp_path),
        'files':[dict(relative_path='inputs/component-context.json', size_bytes=path.stat().st_size,
                      sha256=hashlib.sha256(path.read_bytes()).hexdigest())]}
    monkeypatch.setattr(worker,'status',lambda _:dict(attempt_id='attempt',boot_id=boot,state='running'))
    worker.atomic_json(worker.envelope_path(tmp_path),envelope)
    kwargs=dict(attempt_id='attempt',expected_boot_id=boot,lease_id='original-lease',operation_id='pause-one')
    state=worker.md_production_control(tmp_path,**kwargs)
    assert state['md_pause']['component_ids']==[child]
    assert worker.md_production_control(tmp_path,**kwargs)==state
    assert not runtime.claim(receipt['children'][1]['id'],owner_id='owner',boot_id=boot)
    for changed in (dict(kwargs,expected_boot_id='wrong'),dict(kwargs,lease_id='wrong'),dict(kwargs,operation_id='other')):
        with pytest.raises((ValueError,RuntimeError)):
            worker.md_production_control(tmp_path,**changed)


def _pump(path):
    raise SystemExit(adapter.run_component_workflow(path))


def test_real_inert_process_pause_and_same_component_continuation(retained,tmp_path,monkeypatch):
    path,runtime,receipt,original,resources=retained
    # Only the native scientific subprocesses are replaced. The root pump,
    # process fences, SQLite ledger, snapshots and continuation compiler are real.
    context=json.loads(path.read_bytes())
    context['root_command']=[sys.executable,'-c','import time; time.sleep(60)']
    path.write_bytes(canonical_bytes(context))
    original_compile=nextflow.compile_component_nextflow_invocation
    def compile_leaf(request,ctx):
        # The closed replica compiler is tested separately; here scientific
        # inputs are inert and only its exact process lifecycle is exercised.
        output=Path(ctx['child_output_dir']); native=output/'native'
        params=request.payload['params']
        continuation=ctx.get('md_resume',{}).get(request.component_id)
        if continuation:
            script="from pathlib import Path; import sys; p=Path(sys.argv[1]); assert p.read_bytes()==b'inert production bytes'; Path(sys.argv[2]).write_bytes(b'inert native result')"
            command=[sys.executable,'-c',script,continuation['md_resume_checkpoint'],str(output/'result.bin')]
        elif ctx.get('md_resume'):
            command=[sys.executable,'-c',"from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b'inert native result')",str(output/'result.bin')]
        else:
            runtime_config=json.loads(Path(params['md_job_config']).read_bytes())
            runtime_config['execution']['scheduler_gpu_id']='0'
            runtime_config_path=output/'runtime-config.json'
            runtime_config_path.write_bytes(canonical_bytes(runtime_config))
            gmx=output/'inert-gmx'
            gmx.write_text('#!'+sys.executable+'\nprint("step = 20 t = 0.04")\n')
            gmx.chmod(0o755)
            script='''import signal,time,sys
from pathlib import Path
p=Path(sys.argv[1]);p.mkdir(parents=True,exist_ok=True)
def stop(*args):
    (p/'production').mkdir(exist_ok=True)
    (p/'production/production.cpt').write_bytes(b'inert production bytes')
    raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
(p/'started').touch()
while True: time.sleep(.02)
'''
            command=[sys.executable,'-m','scripts.bms_md.checkpointing_runner',
                '--config',str(runtime_config_path),'--output-dir',str(native),'--gmx-binary',str(gmx),
                '--',sys.executable,'-c',script,str(native)]
        invocation=replace(original,command=tuple(command),model_id='molecular_dynamics',mode='replica',
            native_parameters_json=canonical_bytes(dict(out_dir=str(output),md_resume_output_dir=str(native))),
            generated_inputs=(),execution_plan=None)
        return invocation
    def snapshot(invocation,request,ctx):
        output=Path(ctx['child_output_dir'])
        return dict(id=request.component_id,params=dict(request.payload['params'],md_resume_output_dir=str(output/'native')),output_dir=str(output))
    monkeypatch.setattr(nextflow,'compile_component_nextflow_invocation',compile_leaf)
    monkeypatch.setattr(nextflow,'component_native_parent_snapshot',snapshot)
    monkeypatch.setattr(adapter,'resource_bound_command',lambda command,*args: command)
    ctx=multiprocessing.get_context('fork')
    process=ctx.Process(target=_pump,args=(path,)); process.start()
    child=receipt['children'][0]['id']; native=tmp_path/'components'/child.replace(':','-')/'native'
    try:
        deadline=time.monotonic()+10
        while not (native/'started').exists() and time.monotonic()<deadline: time.sleep(.02)
        assert (native/'started').exists()
        runtime.request_md_pause('pause-one',boot_id=worker.boot_id())
        process.join(10)
        assert not process.is_alive() and process.exitcode==75
        state=runtime.root_state()
        assert state['state']=='paused' and state['quiescent']
        assert runtime.child_status(child)['status']=='paused'
        assert runtime.child_status(receipt['children'][1]['id'])['status']=='queued'
        checkpoint=state['md_checkpoints'][child]
        assert Path(checkpoint['md_resume_checkpoint']).read_bytes()==b'inert production bytes'
        # Compile the real collector edge, then replace ONLY its native process
        # in this inert receiving test. The compiler contract is tested above.
        compile_collector=nextflow.compile_component_retry_invocation
        collector_script = '''import time
from pathlib import Path
from scripts.lib.component_adapter import runtime_from_environment
from component_runtime import ResultReference,file_identity
r=runtime_from_environment()
while True:
    rows=r.children()
    for row in rows:
        if row['status']=='execution_finished':
            p=Path(row['output_dir'])/'result.bin'
            sha,size=file_identity(p)
            r.complete_validated_child(row['id'], result={'output_dir':row['output_dir']},
                references=[ResultReference(row['id'],p.relative_to(r.artifact_root).as_posix(),sha,size,'inert-test-result')])
    if all(row['status']=='completed' for row in r.children()): break
    time.sleep(.02)
'''
        def inert_collector(*args,**kwargs):
            invocation=compile_collector(*args,**kwargs)
            return replace(invocation,command=(sys.executable,'-c',collector_script))
        monkeypatch.setattr(nextflow,'compile_component_retry_invocation',inert_collector)
        state=adapter.resume_md_workflow(path,operation_id='resume-one',pause_operation_id='pause-one',
            boot_id=worker.boot_id(),continuation_lease_id='renewed',resources=resources)
        process=ctx.Process(target=_pump,args=(path,));process.start();process.join(10)
        assert not process.is_alive() and process.exitcode==0
        assert runtime.root_state()['state']=='completed'
        assert all(row['status']=='completed' for row in runtime.children())
        assert (native.parent/'result.bin').read_bytes()==b'inert native result'
        assert len(runtime.children(include_replaced=True))==2
        assert runtime.request(child).payload['params']['md_replica_seed']==71
        assert Path(checkpoint['md_resume_checkpoint']).read_bytes()==b'inert production bytes'
    finally:
        if process.is_alive(): process.terminate(); process.join(5)


def test_actual_replica_compiler_receives_retained_checkpoint(retained):
    path,runtime,receipt,original,resources=retained
    boot,child,value=pause_fixture(retained)
    request=runtime.request(child)
    context=dict(runtime.context,child_id=child,child_output_dir=value['output_dir'],md_resume={child:value})
    invocation=nextflow.compile_component_nextflow_invocation(request,context)
    native=invocation.native_parameters
    assert native['md_resume_checkpoint']==value['md_resume_checkpoint']
    assert native['md_resume_checkpoint_sha256']==value['md_resume_checkpoint_sha256']
    assert native['md_resume_output_dir']==value['md_resume_output_dir']
    assert native['md_replica_seed']==71
    assert '--md_resume_checkpoint' in invocation.command
    assert request.payload['params']==runtime.request(child).payload['params']


from test_md_controls_trim import store


@pytest.mark.asyncio
async def test_mapped_shared_pause_and_root_only_resume(store,retained,monkeypatch):
    from database import Job, MdRun, MdCheckpoint, MdAttemptSegment
    from services.md.state import create_md_run,create_replica_attempt
    from services.md.pause_actuator import pause_running_md_run,resume_shared_md_run
    from sqlalchemy import select
    session,_=store
    path,runtime,receipt,original,resources=retained
    boot,child_id,value=pause_fixture(retained)
    parent=Job(id='parent',name='MD',model_id='molecular_dynamics',mode='simulate',
        status='running',queue_status='running',params={},provenance={'component_context_path':str(path)})
    session.add(parent); await session.flush()
    run=await create_md_run(session,job=parent,normalized_request={'schema':'bms.md.job.v2','chemistry':{
        'profile_id':'retained-profile','profile_sha256':'a'*64,'assurance':'curated_profile'}})
    run.phase='replicas_running'
    rows=[]
    for row in receipt['children']:
        native_id=row['id']; params=runtime.request(native_id).payload['params']
        child=Job(id='projection-'+str(row['replica_index']),name='replica',parent_job_id='parent',
            model_id='molecular_dynamics',mode='replica',child_stage='md_replica',params=params,
            status='running' if row['replica_index']==0 else 'queued',queue_status='completed',
            provenance={'component_id':native_id,'component_projection':{'root_job_id':'parent','state':'running'}})
        session.add(child);await session.flush()
        replica,segment=await create_replica_attempt(session,job_id='parent',child_job_id=child.id,
            replica_index=row['replica_index'],attempt=0,engine='gromacs',
            execution_plan_sha256=params['md_execution_plan_sha256'],compatibility_key=params['md_compatibility_key'])
        replica.state=segment.state=child.status
        rows.append((child,replica,segment))
    await session.commit()
    paused=await pause_running_md_run(session,job_id='parent',expected_version=0,idempotency_key='pause-one')
    await session.commit()
    assert paused.phase=='paused' and parent.paused
    accepted=list((await session.scalars(select(MdCheckpoint))).all())
    assert len(accepted)==1 and accepted[0].sha256==value['md_resume_checkpoint_sha256']
    assert rows[1][1].state=='queued'
    segments=await resume_shared_md_run(session,job_id='parent',expected_version=paused.state_version,idempotency_key='resume-one')
    assert len(segments)==1 and segments[0].source_checkpoint_id==accepted[0].id
    assert parent.status=='queued' and parent.queue_status=='queued' and parent.pinned_gpu==0
    assert rows[0][0].queue_status=='completed' and rows[0][0].nextflow_run_id is None
    assert rows[1][0].queue_status=='completed'
    assert runtime.root_state()['state']=='paused'  # scheduler, not API, owns launch
    saved=parent.provenance['component_md_resume']
    assert nextflow._receive_local_md_production_pause(parent,str(path))
    assert parent.status=='paused' and parent.assigned_gpu is None
    parent.status=parent.queue_status='running';parent.assigned_gpu=0;parent.paused=False
    import biomodstack_local_resources as local_resources
    capacity=SimpleNamespace(cpu_threads=128,memory_bytes=512*1024**3)
    monkeypatch.setattr(local_resources,'applied_local_policy',lambda:capacity)
    monkeypatch.setattr(local_resources,'detect_local_capacity',lambda:capacity)
    monkeypatch.setattr(SourceIdentity,'from_checkout',classmethod(lambda cls,root:SourceIdentity('a'*40,'b'*40)))
    retained_launch=nextflow._local_checkpoint_resume(parent)
    launched=nextflow._compile_local_component_retry(parent,retained_launch)
    assert nextflow._compile_local_component_retry(parent,nextflow._local_checkpoint_resume(parent))==launched
    resources=runtime.root_state()['continuation_edge']['resources']
    invocations=[]
    state=adapter.resume_md_workflow(path,operation_id='resume-one',pause_operation_id='pause-one',boot_id=boot,
        continuation_lease_id=saved['continuation_lease_id'],resources=resources,checkpoints=saved['checkpoints'],native_invocations=invocations)
    assert state['state']=='resume_ready' and invocations
    replay=[]
    adapter.resume_md_workflow(path,operation_id='resume-one',pause_operation_id='pause-one',boot_id=boot,
        continuation_lease_id=saved['continuation_lease_id'],resources=resources,checkpoints=saved['checkpoints'],native_invocations=replay)
    assert replay==invocations


def test_worker_resume_receiver_single_spawn_and_explicit_uncertainty(retained,tmp_path,monkeypatch):
    path,runtime,receipt,original,resources=retained
    boot,child,value=pause_fixture(retained)
    envelope={'environment':{'BMS_COMPONENT_CONTEXT':str(path)},'attempt_id':'attempt','job_id':'parent',
        'output_directory':str(tmp_path),'working_directory':str(tmp_path),
        'files':[dict(relative_path='inputs/component-context.json',size_bytes=path.stat().st_size,
                      sha256=hashlib.sha256(path.read_bytes()).hexdigest())]}
    worker.atomic_json(worker.envelope_path(tmp_path),envelope)
    worker.atomic_json(worker.status_path(tmp_path),dict(attempt_id='attempt',boot_id=boot,state='paused',quiescent=True))
    monkeypatch.setattr(worker,'status',lambda path:worker.load_json(worker.status_path(path)))
    spawns=[]
    def spawn(*args,**kwargs): spawns.append(args[0]); raise OSError('inert uncertain spawn response')
    monkeypatch.setattr(worker.subprocess,'Popen',spawn)
    kwargs=dict(attempt_id='attempt',expected_boot_id=boot,lease_id='original-lease',operation_id='resume-one',
        resume=True,pause_operation_id='pause-one',continuation_lease_id='renewed',resources=resources)
    with pytest.raises(OSError,match='inert uncertain'):
        worker.md_production_control(tmp_path,**kwargs)
    assert len(spawns)==1 and worker.load_json(worker.status_path(tmp_path))['state']=='prepared'
    state=worker.md_production_control(tmp_path,**kwargs)
    assert len(spawns)==1 and state['state']=='resume_ready'
    with pytest.raises(RuntimeError,match='identity conflicts'):
        worker.md_production_control(tmp_path,**dict(kwargs,continuation_lease_id='other'))
    assert len(spawns)==1


@pytest.mark.asyncio
@pytest.mark.parametrize('retained',['vast:fixture'],indirect=True)
@pytest.mark.parametrize('lost_response',[False,True])
async def test_remote_controller_to_actual_worker_pause_resume(store,retained,monkeypatch,lost_response):
    from datetime import datetime
    import shutil
    from database import Job,ExecutionTarget
    from services.remote_execution import executor,targets
    from services.remote_execution.transport import RemoteTransportError
    path,runtime,receipt,original,resources=retained
    session,_=store
    boot,child,value=pause_fixture(retained)
    attempt=runtime.artifact_root.parent
    epoch=datetime.utcnow()
    devices=[{'gpu_index':0,'gpu_uuid':'inert-physical-GPU'}]
    admission={'schema':'bms.target-resource-admission.v1','execution_target_id':'vast:fixture',
        'devices':devices,'available':{'cpus':128,'memory_bytes':512*1024**3,'scratch_bytes':1024**3}}
    resources['admission']=admission
    envelope={'environment':{'BMS_COMPONENT_CONTEXT':str(path)},'attempt_id':'attempt','job_id':'parent',
        'output_directory':str(runtime.artifact_root),'working_directory':str(runtime.artifact_root),
        'files':[dict(relative_path='inputs/component-context.json',size_bytes=path.stat().st_size,
                      sha256=hashlib.sha256(path.read_bytes()).hexdigest())]}
    worker.atomic_json(worker.envelope_path(attempt),envelope)
    worker.atomic_json(worker.status_path(attempt),dict(attempt_id='attempt',job_id='parent',boot_id=boot,
        state='paused',quiescent=True,generation=0))
    monkeypatch.setattr(worker,'status',lambda path:worker.load_json(worker.status_path(path)))
    spawns=[]
    monkeypatch.setattr(worker.subprocess,'Popen',lambda *args,**kwargs:spawns.append(args[0]))
    target=ExecutionTarget(id='vast:fixture',provider='vast',provider_instance_id='fixture',
        state='ready',active=True,host='inert.invalid',port=22,username='inert',remote_root=str(attempt),
        host_key_sha256='d'*64,leased_job_id='parent',lease_acquired_at=epoch)
    remote_receipt=dict(boot_id=boot,source_revision='a'*40,source_tree='b'*40,execution_envelope_sha256='e'*64,
        lease_acquired_at=epoch.isoformat(),component_context_identity=dict(root_job_id='parent',target_id='vast:fixture',
        attempt_id='attempt',lease_id='original-lease'))
    output=attempt/'controller-output';output.mkdir()
    parent=Job(id='parent',name='MD',model_id='molecular_dynamics',mode='simulate',status='running',queue_status='running',
        execution_target_id=target.id,remote_attempt_id='attempt',remote_state='running',
        execution_source_revision='a'*40,execution_source_tree='b'*40,execution_bundle_sha256='e'*64,
        output_dir=str(output),params={},provenance={'remote_execution_receipt':remote_receipt,
            'remote_execution_assignment':{'resources':resources}})
    session.add_all([target,parent]);await session.commit()
    monkeypatch.setattr(executor,'_connection_for_attempt',lambda target,job:(object(),str(attempt)))
    monkeypatch.setattr(executor,'_worker_argv',lambda conn,command,directory,*args:[command,*args])
    async def verified(*args,**kwargs): pass
    monkeypatch.setattr(executor,'_verify_remote_runner',verified)
    async def ready(session,identity): return await session.get(ExecutionTarget,identity,populate_existing=True)
    monkeypatch.setattr(targets,'get_ready_target',ready)
    async def admit(*args,**kwargs): return admission
    monkeypatch.setattr(targets,'admit_target_resources',admit)
    dispatched=[];lost=[lost_response]
    async def transport(conn,argv,**kwargs):
        dispatched.append(argv[0]); fields=dict(zip(argv[1::2],argv[2::2]))
        resume=argv[0]=='md-resume'
        state=worker.md_production_control(attempt,attempt_id=fields['--attempt-id'],expected_boot_id=fields['--expected-boot-id'],
            lease_id=fields['--lease-id'],operation_id=fields['--operation-id'],resume=resume,
            pause_operation_id=fields.get('--pause-operation-id'),continuation_lease_id=fields.get('--continuation-lease-id'),
            resources=json.loads(fields['--resources-json']) if resume else None,
            checkpoints=json.loads(fields['--checkpoints-json']) if resume else None)
        if resume and lost[0]:
            lost[0]=False
            raise RemoteTransportError('inert response lost after worker acceptance')
        return SimpleNamespace(stdout=json.dumps(state))
    monkeypatch.setattr(executor,'run_remote',transport)
    async def observed(session,job,**kwargs): return SimpleNamespace(**worker.status(attempt))
    monkeypatch.setattr(executor,'remote_status',observed)
    transfers=[]
    async def copy(conn,source,destination,selected,**kwargs):
        transfers.append(selected)
        for relative in selected:
            path=destination/relative;path.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(Path(source)/relative,path)
    monkeypatch.setattr(executor,'rsync_selected_from_remote',copy)
    observation=await executor.pause_md_production(session,parent,operation_id='pause-one')
    assert dispatched==['md-pause'] and transfers
    assert Path(observation['md_checkpoints'][child]['local_output_dir'],'production/production.cpt').read_bytes()==Path(value['md_resume_checkpoint']).read_bytes()
    await session.refresh(target);assert target.leased_job_id is None
    parent.provenance=dict(parent.provenance,md_production_pause=dict(operation_id='pause-one',observation=observation))
    await session.commit()
    kwargs=dict(operation_id='resume-one',checkpoints={child:{'md_resume_segment_id':'committed-segment-one'}})
    if lost_response:
        with pytest.raises(RemoteTransportError,match='response lost'):
            await executor.resume_md_production(session,parent,**kwargs)
        assert parent.remote_state=='md_resume_uncertain'
        await session.refresh(target);assert target.leased_job_id=='parent'
    result=await executor.resume_md_production(session,parent,**kwargs)
    assert result['state']=='continuing' and len(spawns)==1
    assert runtime.root_state()['continuation_edge']['md_resume'][child]['md_resume_segment_id']=='committed-segment-one'
    assert parent.remote_state=='running' and parent.remote_attempt_id=='attempt'
    await session.refresh(target);assert target.leased_job_id=='parent'
