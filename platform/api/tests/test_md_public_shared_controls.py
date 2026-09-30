"""Public shared MD receiving boundaries; only scientific/transport leaves are inert."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from tools import bms_remote_worker as worker

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from test_md_shared_controls import retained, pause_fixture
from test_md_controls_trim import store
from component_runtime import canonical_bytes, ResultReference
from database import Job, MdRun, MdReplicaRun, MdCheckpoint, MdAttemptSegment, JobArtifact
from routers import molecular_dynamics
from services import nextflow
from services.md.state import create_md_run
from scripts.lib import component_adapter as adapter


def bind_snapshot(runtime, child, boot):
    request = runtime.request(child)
    output = runtime.artifact_root / 'components' / child.replace(':', '-')
    output.mkdir(parents=True, exist_ok=True)
    context = dict(runtime.context, child_id=child, child_output_dir=str(output))
    invocation = nextflow.compile_component_nextflow_invocation(request, context)
    snapshot = nextflow.component_native_parent_snapshot(invocation, request, context)
    runtime.bind_native_parent(child, snapshot, owner_id='owner', boot_id=boot)
    return snapshot


async def parent_run(session, retained):
    path, runtime, receipt, original, resources = retained
    parent = Job(id='parent', name='MD', model_id='molecular_dynamics', mode='simulate',
        status='running', queue_status='running', params={}, output_dir=str(runtime.artifact_root),
        provenance={'component_context_path': str(path)})
    session.add(parent)
    await session.flush()
    run = await create_md_run(session, job=parent, normalized_request=dict(
        schema='bms.md.job.v2', engine='gromacs', replicas=2, random_seed=71,
        chemistry=dict(profile_id='retained', profile_sha256='a'*64, assurance='curated_profile')))
    await session.commit()
    return parent, run


def app_for(session):
    app = FastAPI()
    app.include_router(molecular_dynamics.router)
    async def scratch_session():
        yield session
    app.dependency_overrides[molecular_dynamics.get_session] = scratch_session
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize('initial_running', [False, True])
@pytest.mark.parametrize('initial_phase', ['validating', 'preparing', 'replicas_running'])
async def test_public_first_pause_import_http_actions_and_real_resume_dispatch(store, retained, initial_running, initial_phase):
    session, _ = store
    parent, run = await parent_run(session, retained)
    path, runtime, receipt, original, resources = retained
    run.phase = initial_phase
    await session.commit()
    if initial_running:
        from tools.bms_remote_worker import boot_id
        boot = boot_id()
        child = receipt['children'][0]['id']
        assert runtime.claim_root(owner_id='owner', boot_id=boot)
        runtime.set_root_state('running', owner_id='owner', boot_id=boot, quiescent=False, generation=0)
        assert runtime.claim(child, owner_id='owner', boot_id=boot)
        bind_snapshot(runtime, child, boot)
        runtime.publish_projection(control_observation=True)
        await nextflow._project_local_components(parent, session, str(path))
        await session.commit()
        projected = await session.get(Job, child)
        assert projected.status=='running' and projected.queue_status=='completed'
        assert projected.nextflow_run_id is None
    boot, child, checkpoint = pause_fixture(retained)
    snapshot = bind_snapshot(runtime, child, boot)
    immutable = path.read_bytes()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(session)), base_url='http://test') as client:
        detail = (await client.get('/api/molecular-dynamics/runs/parent')).json()
        queue = (await client.get('/api/molecular-dynamics/runs')).json()
        assert 'pause' in detail['allowed_actions']
        assert 'pause' in queue['runs'][0]['allowed_actions']
        response = await client.post('/api/molecular-dynamics/runs/parent/pause', json={
            'expected_state_version': 0, 'idempotency_key': 'pause-one'})
        assert response.status_code == 200, response.text
        paused = response.json()
        assert paused['phase'] == 'paused' and 'resume_dynamics' in paused['allowed_actions']
        queue = (await client.get('/api/molecular-dynamics/runs')).json()
        assert 'resume_dynamics' in queue['runs'][0]['allowed_actions']
        children = list((await session.scalars(select(Job).where(Job.parent_job_id=='parent'))).all())
        assert [row.id for row in children] == [child]
        assert children[0].params == snapshot['params']
        assert children[0].nextflow_run_id is None and children[0].assigned_gpu is None
        accepted = list((await session.scalars(select(MdCheckpoint))).all())
        assert len(accepted)==1 and accepted[0].sha256==checkpoint['md_resume_checkpoint_sha256']
        command = {'expected_state_version': paused['state_version'], 'idempotency_key': 'resume-one'}
        response = await client.post('/api/molecular-dynamics/runs/parent/resume', json=command)
        assert response.status_code == 200, response.text
        replay = await client.post('/api/molecular-dynamics/runs/parent/resume', json=command)
        assert replay.status_code == 200 and replay.json()==response.json()
        segment = await session.get(MdAttemptSegment, response.json()['segment_ids'][0])
        assert segment.source_checkpoint_id == accepted[0].id
        assert parent.status == 'queued' and parent.provenance['component_md_resume']['state']=='queued'
        assert children[0].params == snapshot['params'] and children[0].queue_status=='completed'
        assert path.read_bytes()==immutable


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['completed', 'paused', 'failed'])
async def test_public_authenticated_continuation_import_preserves_checkpoint_segments(store, retained, outcome):
    session, _ = store
    parent, run = await parent_run(session, retained)
    path, runtime, receipt, original, resources = retained
    boot, child, checkpoint = pause_fixture(retained)
    snapshot = bind_snapshot(runtime, child, boot)
    from services.md.pause_actuator import pause_running_md_run
    from services.md.state import resume_run
    await pause_running_md_run(session, job_id='parent', expected_version=0, idempotency_key='pause-one')
    await session.commit()
    accepted = (await session.scalars(select(MdCheckpoint))).one()
    artifact = (await session.scalars(select(JobArtifact))).one()
    old_bytes = Path(artifact.storage_path).read_bytes()
    old_segment = await session.get(MdAttemptSegment, accepted.segment_id)
    segments = await resume_run(session, job_id='parent', expected_version=run.state_version, idempotency_key='resume-one')
    native = parent.provenance['component_md_resume']['checkpoints']
    state = adapter.resume_md_workflow(path, operation_id='resume-one', pause_operation_id='pause-one',
        boot_id=boot, continuation_lease_id='renewed', resources=resources, checkpoints=native)
    assert state['generation']==1
    generation = dict(generation=state['generation'], continuation_edge=state['continuation_edge'])
    assert runtime.claim_root(owner_id='owner', boot_id=boot)
    runtime.set_root_state('running', owner_id='owner', boot_id=boot, quiescent=False, **generation)
    assert runtime.claim(child, owner_id='owner', boot_id=boot)
    request = runtime.request(child)
    resumed_context = dict(runtime.context, child_id=child, child_output_dir=checkpoint['output_dir'],
        md_resume=native)
    resumed_invocation = nextflow.compile_component_nextflow_invocation(request, resumed_context)
    resumed_snapshot = nextflow.component_native_parent_snapshot(resumed_invocation, request, resumed_context)
    runtime.bind_native_parent(child, resumed_snapshot, owner_id='owner', boot_id=boot)
    assert runtime.native_parent(child)==snapshot
    from copy import deepcopy
    for changed_key in ('md_replica_seed', 'md_resume_checkpoint_sha256'):
        conflicting = deepcopy(resumed_snapshot)
        conflicting['params'][changed_key] = 999 if changed_key == 'md_replica_seed' else 'f'*64
        with pytest.raises(ValueError, match='immutable native parent snapshot conflicts'):
            runtime.bind_native_parent(child, conflicting, owner_id='owner', boot_id=boot)
        assert runtime.native_parent(child)==snapshot
    output = Path(checkpoint['output_dir'])
    if outcome=='completed':
        runtime.execution_finished(child, owner_id='owner', boot_id=boot, output_dir=str(output), exit_code=0)
        result = output/'inert-result'; result.write_bytes(b'inert native artifact')
        runtime.complete_validated_child(child, result={'output_dir':str(output)}, references=[
            ResultReference(child, result.relative_to(runtime.artifact_root).as_posix(),
                hashlib.sha256(result.read_bytes()).hexdigest(), result.stat().st_size, 'inert')])
        runtime.set_root_state('completed', owner_id='owner', boot_id=boot, quiescent=True, **generation)
    elif outcome == 'failed':
        runtime.fail(child, owner_id='owner', boot_id=boot, reason='inert scientific failure',
            quiescent=True, failure_receipt=dict(code='numerical_failure', source='scientific_runner'))
        runtime.set_root_state('failed', owner_id='owner', boot_id=boot, quiescent=True, **generation)
    else:
        runtime.request_md_pause('pause-two', boot_id=boot)
        updated = dict(checkpoint, receipt=dict(checkpoint['receipt'], step=40, time_ps=.08))
        native_root=Path(checkpoint['md_resume_output_dir'])
        (native_root/'md-checkpoint-receipt.json').write_bytes(canonical_bytes(updated['receipt']))
        runtime.md_execution_paused(child, owner_id='owner', boot_id=boot, checkpoint=updated)
        runtime.set_root_state('paused', owner_id='owner', boot_id=boot, quiescent=True, md_checkpoints={child:updated}, **generation)
    runtime.publish_projection()
    await nextflow._project_local_components(parent, session, str(path))
    await session.commit()
    projected = await session.get(Job, child)
    assert projected.provenance['component_projection']['state']==outcome
    assert projected.params==snapshot['params']
    if outcome=='failed':
        assert projected.provenance['failure_receipt']==dict(code='numerical_failure',source='scientific_runner')
    assert old_segment.state=='paused' and old_segment.end_step==20
    assert segments[0].state==outcome
    assert Path(artifact.storage_path).read_bytes()==old_bytes
    assert accepted.sha256==checkpoint['md_resume_checkpoint_sha256']
    if outcome == 'paused':
        parent.status = parent.queue_status = 'running'
        parent.paused = False
        await session.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(session)), base_url='http://test') as client:
            response = await client.post('/api/molecular-dynamics/runs/parent/pause', json=dict(
                expected_state_version=run.state_version, idempotency_key='pause-two'))
            assert response.status_code==200, response.text
            assert 'resume_dynamics' in response.json()['allowed_actions']
            checkpoints = list((await session.scalars(select(MdCheckpoint))).all())
            assert len(checkpoints)==2
            assert old_segment.state=='paused' and old_segment.end_step==20
            assert segments[0].state=='paused' and segments[0].end_step==40
            response = await client.post('/api/molecular-dynamics/runs/parent/resume', json=dict(
                expected_state_version=run.state_version, idempotency_key='resume-two'))
            assert response.status_code==200, response.text
            assert len(response.json()['segment_ids'])==1
    await nextflow._project_local_components(parent, session, str(path))
    all_segments = list((await session.scalars(select(MdAttemptSegment))).all())
    assert len(all_segments)==(3 if outcome=='paused' else 2)
    if outcome=='paused':
        assert max(all_segments, key=lambda item:item.segment_index).state=='queued'

@pytest.mark.asyncio
@pytest.mark.parametrize('retained',['vast:fixture'],indirect=True)
@pytest.mark.parametrize('lost_response',[False,True])
@pytest.mark.parametrize('completed_history',[False,True])
async def test_public_remote_first_projection_pause_resume_http(store,retained,monkeypatch,lost_response,completed_history):
    from datetime import datetime
    import shutil
    from database import Job,ExecutionTarget
    from services.remote_execution import executor,targets
    from services.remote_execution.transport import RemoteTransportError
    path,runtime,receipt,original,resources=retained
    session,_=store
    if completed_history:
        boot=worker.boot_id()
        sibling=receipt['children'][1]['id']
        runtime.claim_root(owner_id='owner',boot_id=boot)
        runtime.set_root_state('running',owner_id='owner',boot_id=boot,quiescent=False,generation=0)
        runtime.claim(sibling,owner_id='owner',boot_id=boot)
        snapshot=bind_snapshot(runtime,sibling,boot)
        output=Path(snapshot['output_dir'])
        artifact=output/'inert-completed-result';artifact.write_bytes(b'sealed inert history')
        runtime.execution_finished(sibling,owner_id='owner',boot_id=boot,output_dir=str(output),exit_code=0)
        runtime.complete_validated_child(sibling,result={'output_dir':str(output)},references=[
            ResultReference(sibling,artifact.relative_to(runtime.artifact_root).as_posix(),
                hashlib.sha256(artifact.read_bytes()).hexdigest(),artifact.stat().st_size,'inert')])
    boot,child,value=pause_fixture(retained)
    snapshot=bind_snapshot(runtime,child,boot)
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
    remote_receipt['component_context_identity'].update(source_identity=runtime.source_identity,
        plan_sha256=runtime.plan_sha256, artifact_root=str(runtime.artifact_root))
    parent.provenance=dict(parent.provenance,remote_execution_receipt=remote_receipt)
    session.add_all([target,parent]);await session.flush()
    from services.md.state import create_md_run
    run=await create_md_run(session,job=parent,normalized_request=dict(schema='bms.md.job.v2',
        engine='gromacs',replicas=2,random_seed=71,chemistry=dict(profile_id='retained',
        profile_sha256='a'*64,assurance='curated_profile')))
    await session.commit()
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
    client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(session)),base_url='http://test')
    response=await client.post('/api/molecular-dynamics/runs/parent/pause',json=dict(expected_state_version=0,idempotency_key='pause-one'))
    assert response.status_code==200,response.text
    assert 'resume_dynamics' in response.json()['allowed_actions']
    observation=parent.provenance['md_production_pause']['observation']
    projected=await session.get(Job,child)
    assert projected.params==snapshot['params'] and projected.nextflow_run_id is None
    assert projected.provenance['component_projection']['state']=='paused'
    if completed_history:
        historical=await session.get(Job,sibling)
        assert historical.provenance['component_projection']['state']=='completed'
        assert Path(historical.output_dir,'inert-completed-result').read_bytes()==b'sealed inert history'
    assert dispatched==['md-pause'] and transfers
    assert Path(observation['md_checkpoints'][child]['local_output_dir'],'production/production.cpt').read_bytes()==Path(value['md_resume_checkpoint']).read_bytes()
    await session.refresh(target);assert target.leased_job_id is None
    from services.remote_execution.transport import RemoteTransportError
    command=dict(expected_state_version=run.state_version,idempotency_key='resume-one')
    if lost_response:
        with pytest.raises(RemoteTransportError,match='response lost'):
            await client.post('/api/molecular-dynamics/runs/parent/resume',json=command)
        assert parent.remote_state=='md_resume_uncertain'
        await session.refresh(target);assert target.leased_job_id=='parent'
    result=await client.post('/api/molecular-dynamics/runs/parent/resume',json=command)
    assert result.status_code==200,result.text
    segment_id=result.json()['segment_ids'][0]
    assert len(spawns)==1
    await client.aclose()
    assert runtime.root_state()['continuation_edge']['md_resume'][child]['md_resume_segment_id']==segment_id
    assert parent.remote_state=='running' and parent.remote_attempt_id=='attempt'
    await session.refresh(target);assert target.leased_job_id=='parent'

@pytest.mark.asyncio
@pytest.mark.parametrize('changed', ['checkpoint', 'output', 'request', 'snapshot'])
async def test_public_continuation_rejects_custody_or_request_rewrite(store, retained, changed):
    from services.md.pause_actuator import pause_running_md_run
    from services.md.state import resume_run
    from services.result_ingester import ingest_component_projection
    session, _ = store
    parent, run = await parent_run(session, retained)
    path, runtime, receipt, original, resources = retained
    boot, child, checkpoint = pause_fixture(retained)
    snapshot = bind_snapshot(runtime, child, boot)
    await pause_running_md_run(session, job_id='parent', expected_version=0, idempotency_key='pause-one')
    await session.commit()
    accepted = (await session.scalars(select(MdCheckpoint))).one()
    old = (await session.scalars(select(JobArtifact))).one()
    old_bytes = Path(old.storage_path).read_bytes()
    await resume_run(session, job_id='parent', expected_version=run.state_version, idempotency_key='resume-one')
    state = adapter.resume_md_workflow(path, operation_id='resume-one', pause_operation_id='pause-one',
        boot_id=boot, continuation_lease_id='renewed', resources=resources,
        checkpoints=parent.provenance['component_md_resume']['checkpoints'])
    runtime.claim_root(owner_id='owner', boot_id=boot)
    runtime.set_root_state('running', owner_id='owner', boot_id=boot, quiescent=False,
        generation=1, continuation_edge=state['continuation_edge'])
    runtime.claim(child, owner_id='owner', boot_id=boot)
    output = Path(checkpoint['output_dir'])
    runtime.execution_finished(child, owner_id='owner', boot_id=boot, output_dir=str(output), exit_code=0)
    runtime.set_root_state('completed', owner_id='owner', boot_id=boot, quiescent=True,
        generation=1, continuation_edge=state['continuation_edge'])
    projection = runtime.publish_projection()
    envelope = json.loads(projection.read_bytes())
    row = envelope['components'][0]
    if changed=='checkpoint':
        envelope['root_state']['continuation_edge']['md_resume'][child]['md_resume_checkpoint_sha256']='f'*64
    elif changed=='output':
        envelope['root_state']['continuation_edge']['md_resume'][child]['md_resume_output_dir']='/foreign/output'
    elif changed=='request':
        row['request']['payload']['params']['md_replica_seed']=999
    else:
        row['native_parent']['params']['md_replica_seed']=999
    projection.write_bytes(canonical_bytes(envelope))
    expected = {key: envelope[key] for key in ('root_job_id','attempt_id','target_id','lease_id',
        'source_identity','plan_sha256','current_plan_sha256','generation')}
    expected.update(artifact_root=str(runtime.artifact_root),
        projection_relative_path=projection.relative_to(runtime.artifact_root).as_posix(),
        projection_sha256=hashlib.sha256(projection.read_bytes()).hexdigest())
    with pytest.raises(ValueError):
        await ingest_component_projection(parent,str(runtime.artifact_root),session,expected_context=expected)
    projected = await session.get(Job,child)
    assert projected.params==snapshot['params']
    assert projected.provenance['component_projection']['state']=='paused'
    assert accepted.sha256==checkpoint['md_resume_checkpoint_sha256']
    assert Path(old.storage_path).read_bytes()==old_bytes


@pytest.mark.asyncio
async def test_public_pause_running_adapter_process_import_and_resume(store, retained, monkeypatch):
    import asyncio
    import multiprocessing
    import sys
    from dataclasses import replace
    from test_md_shared_controls import _pump
    session, _ = store
    parent, run = await parent_run(session, retained)
    path, runtime, receipt, original, resources = retained
    context = json.loads(path.read_bytes())
    context['root_command'] = [sys.executable, '-c', 'import time; time.sleep(60)']
    path.write_bytes(canonical_bytes(context))
    real_compile = nextflow.compile_component_nextflow_invocation
    def inert_science(request, ctx):
        invocation = real_compile(request, ctx)
        output = Path(ctx['child_output_dir'])
        native = Path(invocation.native_parameters['md_resume_output_dir'])
        if ctx.get('md_resume'):
            resumed = ctx['md_resume'].get(request.component_id)
            command = [sys.executable, '-c',
                "from pathlib import Path;import sys;p=sys.argv[1];assert not p or Path(p).read_bytes()==b'inert public production bytes';Path(sys.argv[2]).write_bytes(b'inert continued result')",
                resumed['md_resume_checkpoint'] if resumed else '', str(output/'inert-result')]
            return replace(invocation, command=tuple(command))
        gmx = output/'inert-gmx'
        gmx.write_text('#!'+sys.executable+'\nprint("step = 20 t = 0.04")\n')
        gmx.chmod(0o755)
        script = """import signal,time,sys
from pathlib import Path
p=Path(sys.argv[1]);p.mkdir(parents=True,exist_ok=True)
def stop(*args):
    (p/'production').mkdir(exist_ok=True)
    (p/'production/production.cpt').write_bytes(b'inert public production bytes')
    raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
(p/'started').touch()
while True: time.sleep(.02)
"""
        command = [sys.executable, '-m', 'scripts.bms_md.checkpointing_runner',
            '--config', request.payload['params']['md_job_config'], '--output-dir', str(native),
            '--gmx-binary', str(gmx), '--', sys.executable, '-c', script, str(native)]
        return replace(invocation, command=tuple(command))
    monkeypatch.setattr(nextflow,'compile_component_nextflow_invocation',inert_science)
    monkeypatch.setattr(adapter,'resource_bound_command',lambda command,*args:command)
    process = multiprocessing.get_context('fork').Process(target=_pump,args=(path,))
    process.start()
    child = receipt['children'][0]['id']
    native = runtime.artifact_root/'components'/child.replace(':','-')/'native'
    try:
        for _ in range(500):
            if (native/'started').exists(): break
            await asyncio.sleep(.02)
        assert (native/'started').exists()
        assert runtime.root_state()['state']=='running'
        assert list((await session.scalars(select(MdReplicaRun))).all())==[]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(session)),base_url='http://test') as client:
            response = await client.post('/api/molecular-dynamics/runs/parent/pause',json=dict(
                expected_state_version=0,idempotency_key='public-running-pause'))
            assert response.status_code==200,response.text
            assert 'resume_dynamics' in response.json()['allowed_actions']
            process.join(10)
            assert not process.is_alive() and process.exitcode==75
            projected=await session.get(Job,child)
            assert projected.provenance['component_projection']['state']=='paused'
            assert projected.nextflow_run_id is None
            accepted=(await session.scalars(select(MdCheckpoint))).one()
            artifact=(await session.scalars(select(JobArtifact))).one()
            assert Path(artifact.storage_path).read_bytes()==b'inert public production bytes'
            response=await client.post('/api/molecular-dynamics/runs/parent/resume',json=dict(
                expected_state_version=run.state_version,idempotency_key='public-running-resume'))
            assert response.status_code==200,response.text
            segment=await session.get(MdAttemptSegment,response.json()['segment_ids'][0])
            assert segment.source_checkpoint_id==accepted.id
            assert parent.status=='queued' and projected.queue_status=='completed'
        real_collector = nextflow.compile_component_retry_invocation
        collector = '''import time
from pathlib import Path
from scripts.lib.component_adapter import runtime_from_environment
from component_runtime import ResultReference,file_identity
r=runtime_from_environment()
while True:
    rows=r.children()
    for row in rows:
        if row['status']=='execution_finished':
            p=Path(row['output_dir'])/'inert-result'
            sha,size=file_identity(p)
            r.complete_validated_child(row['id'],result={'output_dir':row['output_dir']},
                references=[ResultReference(row['id'],p.relative_to(r.artifact_root).as_posix(),sha,size,'inert')])
    if all(row['status']=='completed' for row in r.children()):break
    time.sleep(.02)
'''
        def inert_collector(*args, **kwargs):
            invocation = real_collector(*args, **kwargs)
            return replace(invocation, command=(sys.executable, '-c', collector))
        monkeypatch.setattr(nextflow, 'compile_component_retry_invocation', inert_collector)
        intent = parent.provenance['component_md_resume']
        adapter.resume_md_workflow(path, operation_id=intent['operation_id'],
            pause_operation_id=intent['pause_operation_id'], boot_id=worker.boot_id(),
            continuation_lease_id=intent['continuation_lease_id'], resources=resources,
            checkpoints=intent['checkpoints'])
        process = multiprocessing.get_context('fork').Process(target=_pump,args=(path,))
        process.start()
        for _ in range(500):
            if not process.is_alive():break
            await asyncio.sleep(.02)
        process.join(1)
        assert not process.is_alive() and process.exitcode==0
        assert runtime.root_state()['state']=='completed' and runtime.root_state()['generation']==1
        await nextflow._project_local_components(parent, session, str(path))
        await session.commit()
        assert projected.provenance['component_projection']['state']=='completed'
        assert segment.state=='completed'
        assert Path(artifact.storage_path).read_bytes()==b'inert public production bytes'
    finally:
        if process.is_alive(): process.terminate(); process.join(5)


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['validating', 'preparing'])
async def test_public_independent_preproduction_controls_remain_unchanged(store, retained, phase):
    session, _ = store
    parent, run = await parent_run(session, retained)
    parent.provenance = {}
    run.phase = phase
    await session.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(session)),base_url='http://test') as client:
        detail=(await client.get('/api/molecular-dynamics/runs/parent')).json()
        queue=(await client.get('/api/molecular-dynamics/runs')).json()
        assert 'pause' not in detail['allowed_actions']
        assert 'pause' not in queue['runs'][0]['allowed_actions']
        response=await client.post('/api/molecular-dynamics/runs/parent/pause',json=dict(
            expected_state_version=0,idempotency_key='independent-preproduction-pause'))
        assert response.status_code==409,response.text
        assert response.json()['detail']['code']=='MD_PAUSE_UNAVAILABLE'
        await session.refresh(run)
        assert run.phase==phase and run.state_version==0

