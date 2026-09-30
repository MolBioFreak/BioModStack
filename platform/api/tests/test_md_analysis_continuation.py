"""Inert scientific fixtures; real native compiler, ledger and mapped finalizer."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from component_runtime import SourceIdentity, canonical_bytes
from database import Job, JobArtifact, MdRun
from services import nextflow
from scripts.bms_md.spawn_analysis import spawn_analysis, prepare_analysis_retry, QUALIFIED_RUNTIME_SHA256
from scripts.bms_md.collect_analysis import collect_analysis
from scripts.lib.component_adapter import runtime_from_environment
from test_md_results_trim import store, _tree, _seed, _bytes
from services.md.lifecycle import _publish_barrier
from services.md.completion import validate_and_finalize_md_job


def _retained(tmp_path, monkeypatch, target):
    root, spec = _tree(tmp_path, monkeypatch)
    monkeypatch.setenv('BMS_RESULTS_DIR', str(root))
    import model_registry
    monkeypatch.setattr(model_registry, 'molecular_dynamics_feature_enabled', lambda: True)
    config = root / 'config.json'
    config.write_bytes(canonical_bytes(spec))
    params = dict(md_job_config=str(config), md_job_spec=spec)
    invocation = nextflow.compile_nextflow_invocation('molecular_dynamics', 'simulate', params,
        str(root), job_id='md-job-1', source_identity=SourceIdentity('a'*40, 'b'*40),
        execution_context=nextflow.NativeCompilerExecutionContext(gpu_id=0, gpu_ids=(0,),
            anarcii_execution_mode='cpu'))
    assert invocation.execution_plan.complete
    invocation.materialize_inputs(root)
    job = SimpleNamespace(id='md-job-1', root_job_id=None, model_id='molecular_dynamics',
        mode='simulate', params=params, provenance={}, status='running', assigned_gpu=0,
        execution_target_id=None if target == 'local' else target, child_output_dir=None)
    from services.remote_execution.targets import selected_plan_target_resources
    resources = selected_plan_target_resources(SimpleNamespace(id=target),
        invocation.execution_plan, gpu_ids=[0], scratch_bytes=0)
    resources.update(gpu_id=0, admission_required=False)
    path = root / 'context.json'
    context = nextflow.component_launch_context(invocation, job, command=invocation.command,
        context_path=path, artifact_root=root, working_directory=root,
        attempt_id='retained-attempt', target_id=target, lease_id='retained-lease', resources=resources)
    path.write_bytes(canonical_bytes(context))
    monkeypatch.setenv('BMS_COMPONENT_CONTEXT', str(path))
    runtime = runtime_from_environment(path)
    receipt = spawn_analysis(parent_job_id=job.id, parent_name='MD',
        aggregate_manifest=root/'manifest.json', api_url='http://unavailable.invalid',
        work_item_dir=root/'orchestration/analysis_work_items', runtime_sha256=QUALIFIED_RUNTIME_SHA256)
    child = receipt['children'][0]['id']
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    runtime.claim_root(owner_id='fixture', boot_id=boot)
    runtime.claim(child, owner_id='fixture', boot_id=boot)
    runtime.fail(child, owner_id='fixture', boot_id=boot, quiescent=True,
        reason='explicit inert analysis failure', failure_receipt=dict(code='execution_failed', source='worker'))
    runtime.set_root_state('failed', owner_id='fixture', boot_id=boot, quiescent=True, generation=0)
    replacement, retry = prepare_analysis_retry(runtime, component_id=child,
        operation_id='operator-retry', failure_code='execution_failed')
    return root, spec, runtime, replacement, retry, context


@pytest.mark.parametrize('target', ['local', 'vast:fixture'])
def test_native_analysis_adapter_and_actual_compiler_never_select_simulation(tmp_path, monkeypatch, target):
    root, _spec, runtime, replacement, receipt, context = _retained(tmp_path, monkeypatch, target)
    assert replacement.payload == runtime.request(
        runtime.group_children('md-job-1:md_analysis')[0]).payload
    assert len(runtime.children()) == 1
    again, replay = prepare_analysis_retry(runtime, component_id=runtime.group_children('md-job-1:md_analysis')[0],
        operation_id='operator-retry', failure_code='execution_failed')
    assert (again, replay) == (replacement, receipt)
    receipt_path = root/'retry.json'; receipt_path.write_bytes(canonical_bytes(receipt))
    params = dict(context['parent']['params'], md_job_spec=_spec,
        md_analysis_retry_spawn_receipt=str(receipt_path))
    invocation = nextflow.compile_nextflow_invocation('molecular_dynamics', 'simulate', params,
        str(root/'generations/retry'), job_id='md-job-1',
        source_identity=SourceIdentity(**context['source_identity']),
        execution_context=nextflow.NativeCompilerExecutionContext(gpu_id=0, gpu_ids=(0,), anarcii_execution_mode='cpu'))
    assert invocation.execution_plan.complete, invocation.execution_plan.to_dict()['blockers']
    keys = {component.component_key for component in invocation.execution_plan.metadata.static_components}
    assert 'MD_COMPLETION_BARRIER' in keys
    assert not keys & {'MD_PREPARE_CONFIG', 'MD_SPAWN_REPLICAS', 'MD_GROMACS_REPLICA', 'MD_OPENMM_REPLICA', 'MD_SPAWN_ANALYSIS'}
    assert invocation.native_parameters['md_analysis_retry_spawn_receipt'] == str(receipt_path)
    manifest = root/'replicas/replica_0/manifest.json'
    manifest.write_bytes(manifest.read_bytes() + b' ')
    with pytest.raises(ValueError, match='dynamics identity changed'):
        prepare_analysis_retry(runtime, component_id=runtime.group_children('md-job-1:md_analysis')[0],
            operation_id='second', failure_code='execution_failed')


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel_during_readback', [False, True])
async def test_fresh_analysis_collection_and_real_finalizer_retain_dynamics(
    store, tmp_path, monkeypatch, cancel_during_readback,
):
    _engine, maker = store
    root, spec, runtime, replacement, receipt, _context = _retained(tmp_path, monkeypatch, 'local')
    # Inert output fixture replaces only the scientific kernel; native collection
    # and publication below are production implementations, not handoff mocks.
    monkeypatch.delenv('BMS_COMPONENT_CONTEXT')
    receipt_path = root/'retry.json'; receipt_path.write_bytes(canonical_bytes(receipt))
    status_path = root/'status.json'
    status_path.write_bytes(canonical_bytes(dict(total=1, completed=1, failed=0, cancelled=0,
        child_ids=[replacement.component_id], child_output_dirs=[str(root/'analysis')])))
    before = _bytes(root/'replicas')
    generation = root/'generations/retry'
    result = collect_analysis(status_path, root/'manifest.json', generation, spawn_receipt=receipt_path)
    assert result['status'] == 'completed'
    assert _bytes(generation/'replicas') == before == _bytes(root/'replicas')
    assert (generation/'manifest.json').read_bytes() == (root/'manifest.json').read_bytes()
    _publish_barrier(generation, 'md-job-1')
    await _seed(maker, root, spec)
    async with maker() as session:
        parent = await session.get(Job, 'md-job-1')
        session.add(Job(id=replacement.component_id, name='retry', model_id='molecular_dynamics',
            mode='analyze', parent_job_id=parent.id, child_stage='md_analysis',
            params=replacement.payload['params'], status='completed', queue_status='completed',
            output_dir=str(generation/'analysis')))
        parent.child_output_dir = str(generation)
        await session.commit()
    if cancel_during_readback:
        import asyncio
        import threading
        from services.md import completion
        entered, release = threading.Event(), threading.Event()
        original = completion._prepare_completion
        def held(record):
            snapshot = original(record)
            entered.set()
            assert release.wait(10), 'fixture readback was not released'
            return snapshot
        monkeypatch.setattr(completion, '_prepare_completion', held)
        async with maker() as session:
            parent = await session.get(Job, 'md-job-1')
            pending = asyncio.create_task(validate_and_finalize_md_job(parent, session))
            try:
                assert await asyncio.to_thread(entered.wait, 10)
                async with maker() as cancel_owner:
                    cancelled = await cancel_owner.get(Job, 'md-job-1')
                    run = await cancel_owner.get(MdRun, 'md-job-1')
                    cancelled.status = cancelled.queue_status = run.phase = 'cancelled'
                    await cancel_owner.commit()
            finally:
                release.set()
            with pytest.raises(completion.MDResultError, match='ownership changed'):
                await pending
            await session.rollback()
    else:
        async with maker() as session:
            parent = await session.get(Job, 'md-job-1')
            await validate_and_finalize_md_job(parent, session)
            await session.commit()
    async with maker() as observer:
        expected = 'cancelled' if cancel_during_readback else 'completed'
        assert (await observer.get(Job, 'md-job-1')).status == expected
        assert (await observer.get(MdRun, 'md-job-1')).phase == expected
        assert bool(list((await observer.scalars(select(JobArtifact))).all())) is not cancel_during_readback
