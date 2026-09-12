"""Compiler/resource/terminal integration, isolated native contracts only."""
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from component_runtime import SourceIdentity, canonical_bytes
from services import nextflow
from services.remote_execution.targets import selected_plan_target_resources
from test_msa_bundle_integration import offline_bundle


def _compile(model, mode, params, output):
    return nextflow.compile_nextflow_invocation(model, mode, params, str(output),
        job_id='parent', source_identity=SourceIdentity('a'*40, 'b'*40),
        execution_context=nextflow.NativeCompilerExecutionContext(
            gpu_id=0, gpu_ids=(0,), anarcii_execution_mode='cpu'))


@pytest.mark.parametrize('selected', [False, True])
def test_actual_selected_frustra_parent_admits_waiter_overlap(tmp_path, selected):
    invocation = _compile('boltz2', 'predict', dict(sequence='AAAA', run_frustrampnn=selected,
        boltz_use_msa=False), tmp_path)
    plan = invocation.execution_plan
    resources = selected_plan_target_resources(SimpleNamespace(id='local'), plan, gpu_ids=[0], scratch_bytes=0)
    retained = selected_plan_target_resources(SimpleNamespace(id='local'), plan.to_dict(), gpu_ids=[0], scratch_bytes=0)
    assert resources == retained
    waiters = [c for c in plan.metadata.static_components
               if c.authority.endswith(':SpawnWaitFrustraMPNNParentChildren')]
    assert len(waiters) == int(selected)
    assert bool(resources['coordinator_overlap']['cpus']) == selected
    assert resources['required']['cpus'] == resources['compute']['cpus'] + resources['coordinator_overlap']['cpus']
    from scripts.lib.component_adapter import native_resource_config
    config = native_resource_config(plan.to_dict(), resources, str(tmp_path/'compute.lock'), nextflow.PROJECT_ROOT)
    if selected:
        block = config.split("withName: 'SpawnWaitFrustraMPNNParentChildren'",1)[1].split('}',1)[0]
        assert 'maxForks = 1' in block and 'flock' not in block
    assert 'flock -x 198' in config


@pytest.mark.asyncio
@pytest.mark.parametrize('observation', ['absent', 'failed', 'present'])
async def test_optional_ngs_observation_still_calls_native_validator(monkeypatch, observation):
    from services import ont_ngs_completion
    receipt = {'fixture':'observation'}
    def fail():
        raise OSError('observation unavailable')
    factory = None if observation == 'absent' else fail if observation == 'failed' else lambda: receipt
    calls = []
    async def validate(job, *, resource_usage_receipt):
        calls.append((job, resource_usage_receipt))
        return {'native':'validated'}
    monkeypatch.setattr(ont_ngs_completion, 'validate_and_prepare_ont_fastq_qc_completion', validate)
    job = object()
    assert await nextflow._validate_ont_fastq_qc_terminal_completion(job, factory) == {'native':'validated'}
    assert calls == [(job, receipt if observation == 'present' else None)]
    async def invalid(*args, **kwargs):
        raise ValueError('invalid native science')
    monkeypatch.setattr(ont_ngs_completion, 'validate_and_prepare_ont_fastq_qc_completion', invalid)
    with pytest.raises(ValueError, match='invalid native science'):
        await nextflow._validate_ont_fastq_qc_terminal_completion(job, factory)


def test_local_retry_native_compile_and_immutable_resource_replay(tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path))
    import biomodstack_local_resources as local
    import model_registry
    monkeypatch.setattr(model_registry, 'molecular_dynamics_feature_enabled', lambda: True)
    from scripts.lib.component_adapter import runtime_from_environment, retry_component_workflow
    from scripts.bms_md import spawn_replicas
    config = dict(random_seed=71, replicas=1, execution=dict(gpu_id=0), engine='gromacs')
    config_path = tmp_path/'config.json'; config_path.write_bytes(canonical_bytes(config))
    metadata = tmp_path/'metadata.json'; metadata.write_bytes(canonical_bytes(config))
    params = dict(md_job_config=str(config_path), md_job_spec=config)
    invocation = _compile('molecular_dynamics', 'simulate', params, tmp_path)
    assert invocation.execution_plan.complete, invocation.execution_plan.to_dict()['blockers']
    resources = selected_plan_target_resources(SimpleNamespace(id='local'), invocation.execution_plan, gpu_ids=[0], scratch_bytes=0)
    resources.update(gpu_id=0, admission_required=False)
    job = SimpleNamespace(id='parent',root_job_id=None,model_id='molecular_dynamics',mode='simulate',
        params=params,provenance={},status='running',assigned_gpu=0,execution_target_id=None,child_output_dir=None)
    path=tmp_path/'context.json'
    context=nextflow.component_launch_context(invocation,job,command=invocation.command,
        context_path=path,artifact_root=tmp_path,working_directory=nextflow.PROJECT_ROOT,
        attempt_id='attempt',target_id='local',lease_id='original-lease',resources=resources)
    context['native_runtime']={'anarcii_execution_mode':'cpu','anarcii_gpu_id':None}
    path.write_bytes(canonical_bytes(context))
    monkeypatch.setenv('BMS_COMPONENT_CONTEXT',str(path))
    runtime=runtime_from_environment(path)
    receipt=spawn_replicas.spawn_replicas(parent_job_id='parent',parent_name='MD',normalized_config=config_path,
        metadata_path=metadata,preparation_bundle=tmp_path/'preparation',api_url='http://unavailable.invalid')
    child=receipt['children'][0]['id']
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    runtime.claim_root(owner_id='owner',boot_id=boot)
    runtime.claim(child,owner_id='owner',boot_id=boot)
    runtime.fail(child,owner_id='owner',boot_id=boot,quiescent=True,reason='spawn failed',
        failure_receipt=dict(code='spawn_rejected',source='scheduler_launch'))
    runtime.set_root_state('failed',owner_id='owner',boot_id=boot,quiescent=True,generation=0)
    monkeypatch.setattr(local,'applied_local_policy',lambda:local.LocalCapacity(128,512*1024**3))
    monkeypatch.setattr(local,'detect_local_capacity',lambda:local.LocalCapacity(128,512*1024**3))
    pending=dict(component_id=child,operation_id='retry-one',failure_code='spawn_rejected',actor='operator',continuation_lease_id='renewed-lease')
    original=path.read_bytes()
    retried=nextflow._compile_local_component_retry(job,(path,context,runtime,None,pending))
    edge=runtime.retry_status('retry-one')
    assert edge['retry_context']['resources']==edge['resources']
    assert edge['resources']['compute']==resources['compute']
    assert edge['resources']['coordinator_overlap']==resources['coordinator_overlap']
    assert edge['resources']['admission_required'] is False
    assert retried.execution_plan.plan_sha256==edge['plan_sha256']
    reopened=runtime_from_environment(path)
    assert reopened.plan_sha256==invocation.execution_plan.plan_sha256
    assert reopened.context['plan_sha256']==retried.execution_plan.plan_sha256
    import asyncio
    from services import result_ingester
    contexts = []
    async def capture_projection(*args, expected_context):
        contexts.append(expected_context)
    monkeypatch.setattr(result_ingester, 'ingest_component_projection', capture_projection)
    reopened.claim_root(owner_id='owner', boot_id=boot)
    replacement = reopened.group_children('parent:md_replica')[0]
    reopened.claim(replacement, owner_id='owner', boot_id=boot)
    reopened.fail(replacement, owner_id='owner', boot_id=boot, quiescent=True, reason='fixture execution rejected')
    reopened.set_root_state('failed', owner_id='owner', boot_id=boot, quiescent=True,
                            generation=1, continuation_edge=edge)
    asyncio.run(nextflow._project_local_components(job, object(), str(path)))
    assert contexts[0]['plan_sha256'] == invocation.execution_plan.plan_sha256
    assert contexts[0]['current_plan_sha256'] == retried.execution_plan.plan_sha256
    assert contexts[0]['current_plan_sha256'] != contexts[0]['plan_sha256']
    def no_second_admission(*args,**kwargs):
        raise AssertionError('replay must not acquire another resource reservation')
    monkeypatch.setattr(nextflow,'component_checkpoint_resources',no_second_admission)
    replay=nextflow._compile_local_component_retry(job,(path,reopened.context,reopened,None,pending))
    assert replay==retried and path.read_bytes()==original
    assert len(runtime.children(include_replaced=True))==2
    changed=json.loads(json.dumps(edge['resources'])); changed['compute']['cpus']+=1
    with pytest.raises(ValueError,match='replay ownership/resources'):
        retry_component_workflow(path,component_id=child,operation_id='retry-one',failure_code='spawn_rejected',
            actor='operator',boot_id=boot,continuation_lease_id='renewed-lease',resources=changed)


@pytest.mark.parametrize('mutation', ['none', 'digest', 'artifact', 'sequence', 'seed', 'settings', 'extra_task', 'other_blocker'])
def test_prepared_protenix_plan_binds_only_verified_native_authority(offline_bundle, mutation):
    from services.model_msa_handoff import prepare_launch_msa
    from biomodstack_msa_handoff import digest
    from dataclasses import replace
    _, job, _, _ = offline_bundle
    original = nextflow.compile_job_nextflow_invocation(job, job.params, job.output_dir)
    original.materialize_inputs(Path(job.output_dir))
    params = prepare_launch_msa('protenix', {**job.params, **original.native_parameters},
                               Path(job.output_dir)/'prepared-msa')
    path = Path(params['protenix_prepared_msa_dir'])/'msa-inputs.json'
    manifest = json.loads(path.read_bytes())
    if mutation == 'other_blocker':
        from component_runtime import UnresolvedField
        plan = original.execution_plan
        original = replace(original, execution_plan=replace(plan, metadata=replace(plan.metadata,
            blockers=plan.metadata.blockers + (UnresolvedField('fixture', 'unrelated', 'fixture', 'remain blocked'),))))
    elif mutation == 'artifact':
        (path.parent/manifest['artifacts'][0]['path']).write_bytes(b'>query\nFOREIGN\n')
    elif mutation == 'digest':
        params['protenix_prepared_msa_sha256'] = '0'*64
    elif mutation != 'none':
        if mutation == 'sequence':
            manifest['model_input'][0]['sequences'][0]['proteinChain']['sequence'] = 'FOREIGN'
        elif mutation == 'seed':
            manifest['model_input'][0]['modelSeeds'] = [99]
        elif mutation == 'settings':
            manifest['settings']['protenix_n_sample'] = 99
        else:
            manifest['model_input'].append(manifest['model_input'][0])
        path.write_bytes(canonical_bytes(manifest))
        params['protenix_prepared_msa_sha256'] = digest(path.read_bytes())
    if mutation not in {'none', 'other_blocker'}:
        with pytest.raises(ValueError):
            nextflow._bind_protenix_msa_transport(original, params)
        assert not original.execution_plan.complete
        return
    bound = nextflow._bind_protenix_msa_transport(original, params)
    assert bound.requested_json == original.requested_json and bound.effective_json == original.effective_json
    assert bound.execution_plan.requested_sha256 == original.execution_plan.requested_sha256
    assert bound.execution_plan.effective_sha256 == original.execution_plan.effective_sha256
    service = next(s for s in bound.execution_plan.metadata.external_services if s.logical_id == 'protenix:msa')
    assert service.state == 'prepared'
    assert service.operation_identity == 'sha256:' + params['protenix_prepared_msa_sha256']
    assert bound.execution_plan.complete == (mutation == 'none')
    assert nextflow._bind_protenix_msa_transport(bound, params) == bound


@pytest.mark.asyncio
@pytest.mark.parametrize('projection_ingested', [False, True])
@pytest.mark.parametrize('failure', [False, True])
async def test_local_md_completion_orders_native_validation_before_promotion(monkeypatch, projection_ingested, failure):
    from services.md import completion
    from services import result_ingester, result_state_integrity
    events = []
    job = SimpleNamespace(child_output_dir='original', provenance={
        'component_context_path':'private-context', 'component_retry_execution': {'output_dir':'generation-1'}})
    async def ingest(*args): events.append('ingest')
    async def validate(current, session):
        assert current.child_output_dir == 'generation-1'
        events.append('native')
        if failure: raise ValueError('native rejection fixture')
    async def promote(*args): events.append('promote')
    monkeypatch.setattr(nextflow, '_project_local_components', ingest)
    monkeypatch.setattr(completion, 'validate_and_finalize_md_job', validate)
    monkeypatch.setattr(result_state_integrity, 'finalize_component_projection', promote)
    if failure:
        with pytest.raises(ValueError, match='native rejection'):
            await nextflow._finalize_local_md_job(job, object(), 'original', projection_ingested=projection_ingested)
        assert job.child_output_dir == 'original'
    else:
        await nextflow._finalize_local_md_job(job, object(), 'original', projection_ingested=projection_ingested)
        assert job.child_output_dir == 'generation-1'
    assert events == ([] if projection_ingested else ['ingest']) + ['native'] + ([] if failure else ['promote'])


@pytest.mark.asyncio
@pytest.mark.parametrize('history', [True, False])
async def test_process_loss_md_recovery_invokes_native_component_completion(tmp_path, monkeypatch, history):
    from datetime import datetime, timedelta
    import subprocess
    from database import Base, Job
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from services import gpu_orchestrator as orchestrator, analysis_autorun
    from unittest.mock import AsyncMock
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path/'recovery.sqlite'))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    (tmp_path/'child').mkdir()
    (tmp_path/'child/nextflow.log').write_text('Nextflow native launch started\n')
    async with sessions() as session:
        session.add(Job(id='md-recover-child', name='MD recovery child', model_id='molecular_dynamics',
            mode='simulate', parent_job_id=None, status='running', queue_status='running',
            output_dir=str(tmp_path/'child'), params={}, provenance={},
            started_at=datetime.utcnow()-timedelta(minutes=10)))
        await session.commit()
    monkeypatch.setattr(orchestrator, '_read_nextflow_history_statuses',
        lambda ids: {'md-recover-child':('OK','')} if history else {})
    monkeypatch.setattr(orchestrator, 'nextflow_history_status_for_run_dir', lambda *args: None)
    monkeypatch.setattr(orchestrator, 'nextflow_history_status', lambda *args: 'OK')
    monkeypatch.setattr(orchestrator, 'workflow_adapter_enabled', lambda: False)
    monkeypatch.setattr(nextflow, 'get_running_jobs', lambda: {})
    monkeypatch.setattr(nextflow, 'maybe_trigger_mutation_seed_refinement', AsyncMock())
    monkeypatch.setattr(analysis_autorun, 'schedule_viewer_minimum_analyses_for_job', lambda *args: None)
    run = subprocess.run
    monkeypatch.setattr(subprocess, 'run', lambda command, **kwargs:
        SimpleNamespace(returncode=0,stdout='') if command == ['ps','aux'] else run(command,**kwargs))
    calls=[]
    async def native(current, session, output_dir):
        calls.append((current.id, output_dir))
        current.status=current.queue_status='completed'
    monkeypatch.setattr(nextflow, '_finalize_local_md_job', native)
    try:
        await orchestrator.GPUOrchestrator(sessions, lambda: [], AsyncMock()).check_job_completions()
        assert calls == [('md-recover-child', str(tmp_path/'child'))]
        async with sessions() as session:
            assert (await session.get(Job,'md-recover-child')).status == 'completed'
    finally:
        await engine.dispose()
