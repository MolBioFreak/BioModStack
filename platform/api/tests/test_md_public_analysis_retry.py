"""Public receiving tests: real route/outbox/compiler/ledger; inert transport/science."""
import json
import shlex
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select
from database import Job, ExecutionTarget
from component_runtime import SourceIdentity, canonical_bytes
from services import nextflow
from services.remote_execution import executor, targets
from scripts.lib.component_adapter import runtime_from_environment, retry_component_workflow
from scripts.bms_md.spawn_analysis import spawn_analysis, QUALIFIED_RUNTIME_SHA256
from scripts.bms_md.collect_analysis import collect_analysis
from tools import bms_remote_worker as worker
import routers.md_results as routes
from test_md_results_trim import store, _tree, _seed, _bytes


def retained(tmp_path, monkeypatch, target, *, replicas=1):
    root, spec = _tree(tmp_path, monkeypatch)
    if replicas == 2:
        import shutil
        spec['replicas'] = 2
        first = root/'replicas/replica_0'
        second = root/'replicas/replica_1'
        shutil.copytree(first, second)
        for index, directory in enumerate((first, second)):
            manifest = json.loads((directory/'manifest.json').read_bytes())
            manifest.update(replica_index=index, seed=spec['random_seed'] + index, config=spec)
            (directory/'manifest.json').write_bytes(canonical_bytes(manifest))
        aggregate = json.loads((root/'manifest.json').read_bytes())
        entry = dict(aggregate['replicas'][0], replica_index=1)
        aggregate['replicas'].append(entry)
        aggregate['lineage']['completed_children'] = 2
        aggregate['lineage']['child_ids'].append('replica-child-1')
        (root/'manifest.json').write_bytes(canonical_bytes(aggregate))
    monkeypatch.setenv('BMS_RESULTS_DIR', str(root))
    import model_registry
    monkeypatch.setattr(model_registry, 'molecular_dynamics_feature_enabled', lambda: True)
    config = root / 'config.json'
    config.write_bytes(canonical_bytes(spec))
    params = dict(md_job_config=str(config), md_job_spec=spec)
    source = SourceIdentity.from_checkout(Path(__file__).resolve().parents[3])
    invocation = nextflow.compile_nextflow_invocation('molecular_dynamics', 'simulate', params,
        str(root), job_id='md-job-1', source_identity=source,
        execution_context=nextflow.NativeCompilerExecutionContext(gpu_id=0, gpu_ids=(0,), anarcii_execution_mode='cpu'))
    invocation.materialize_inputs(root)
    job = SimpleNamespace(id='md-job-1', root_job_id=None, model_id='molecular_dynamics',
        mode='simulate', params=params, provenance={}, status='running', assigned_gpu=0,
        execution_target_id=None if target == 'local' else target, child_output_dir=None)
    resources = targets.selected_plan_target_resources(SimpleNamespace(id=target),
        invocation.execution_plan, gpu_ids=[0], scratch_bytes=0)
    resources.update(gpu_id=0, admission_required=False)
    resources['admission'] = {'devices': []}
    path = root / 'context.json'
    context = nextflow.component_launch_context(invocation, job, command=invocation.command,
        context_path=path, artifact_root=root, working_directory=root, attempt_id='retained-attempt',
        target_id=target, lease_id='retained-lease', resources=resources)
    path.write_bytes(canonical_bytes(context))
    monkeypatch.setenv('BMS_COMPONENT_CONTEXT', str(path))
    runtime = runtime_from_environment(path)
    receipt = spawn_analysis(parent_job_id=job.id, parent_name='MD', aggregate_manifest=root/'manifest.json',
        api_url='http://unavailable.invalid', work_item_dir=root/'orchestration/analysis_work_items',
        runtime_sha256=QUALIFIED_RUNTIME_SHA256)
    child = receipt['children'][0]['id']
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    runtime.claim_root(owner_id='fixture', boot_id=boot)
    for row in receipt['children']:
        runtime.claim(row['id'], owner_id='fixture', boot_id=boot)
        runtime.fail(row['id'], owner_id='fixture', boot_id=boot, quiescent=True, reason='inert analysis failure',
            failure_receipt=dict(code='execution_failed', source='worker'))
    runtime.set_root_state('failed', owner_id='fixture', boot_id=boot, quiescent=True, generation=0)
    return root, spec, runtime, context, path, child, boot


async def seed(maker, root, spec, context, path, target):
    await _seed(maker, root, spec, status='failed', phase='failed')
    async with maker() as session:
        parent = await session.get(Job, 'md-job-1')
        _, _, _, aggregate_sha, set_sha = routes._current_dynamics_generation(parent)
        # No analysis Job projection: the route must resolve the actual retained request.
        await session.execute(delete(Job).where(Job.id == 'analysis-child-0'))
        parent.params = {**context['parent']['params'], 'md_job_spec': spec}
        provenance = dict(md=dict(aggregate_manifest_sha256=aggregate_sha, replica_manifest_set_sha256=set_sha),
                          component_context_path=str(path))
        if target != 'local':
            parent.execution_target_id = target
            parent.remote_attempt_id = context['attempt_id']
            parent.execution_source_revision = context['source_identity']['revision']
            parent.execution_source_tree = context['source_identity']['tree']
            parent.execution_bundle_sha256 = 'c'*64
            parent.nextflow_run_id = 'retained-run'
            parent.remote_state = 'failed'
            provenance.update(execution_plan_approval=dict(approval_digest='retained', plan=context['execution_plan']),
                remote_execution_assignment=dict(resources=context['resources']),
                remote_execution_receipt=dict(component_context_identity=context, boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                    source_revision=parent.execution_source_revision, source_tree=parent.execution_source_tree,
                    execution_envelope_sha256=parent.execution_bundle_sha256, generation=0, plan_sha256=context['plan_sha256']))
            session.add(ExecutionTarget(id=target, provider='vast', provider_instance_id='fixture',
                active=True, state='ready', remote_root=str(root), capabilities={}))
        parent.provenance = provenance
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize('target,lose_response', [('local', False), ('vast:fixture', False), ('vast:fixture', True)])
async def test_public_route_real_receiving_dispatch_and_analysis_only_compiler(store, tmp_path, monkeypatch, target, lose_response):
    _, maker = store
    root, spec, runtime, context, path, child, boot = retained(tmp_path, monkeypatch, target)
    await seed(maker, root, spec, context, path, target)
    if target != 'local':
        runtime.publish_projection()
        async with maker() as session:
            parent = await session.get(Job, 'md-job-1')
            provenance = dict(parent.provenance); provenance.pop('component_context_path')
            parent.provenance = provenance
            await session.commit()
    before = _bytes(root/'replicas')
    original_params = {**context['parent']['params'], 'md_job_spec': spec}
    calls = []
    if target != 'local':
        attempt = root/'attempt'; attempt.mkdir()
        envelope = dict(job_id='md-job-1', attempt_id=context['attempt_id'],
                        environment={'BMS_COMPONENT_CONTEXT': str(path)}, output_directory=str(root),
                        path_map={'code_root': str(Path(__file__).resolve().parents[3])})
        from component_runtime import file_identity
        sha, size = file_identity(path)
        envelope.update(working_directory=context['working_directory'],
            files=[dict(relative_path='inputs/component-context.json', sha256=sha, size_bytes=size)])
        worker.envelope_path(attempt).write_bytes(canonical_bytes(envelope))
        worker.status_path(attempt).write_bytes(canonical_bytes(dict(schema='bms.remote-attempt-status.v1',
            attempt_id=context['attempt_id'], job_id='md-job-1', state='failed', boot_id=boot, quiescent=True,
            generation=0, plan_sha256=context['plan_sha256'])))
        # Only transport, bundle-delivery and process-spawn leaves are inert.
        monkeypatch.setattr(worker, 'verify_bundle', lambda *_: envelope)
        monkeypatch.setattr(worker.subprocess, 'Popen', lambda *a, **k: calls.append('inert-supervisor'))
        monkeypatch.setattr(executor, '_connection_for_attempt', lambda *_: (SimpleNamespace(), attempt))
        monkeypatch.setattr(executor, '_worker_argv', lambda connection, command, directory, *args: [command, *args])
        async def verify(*a, **k): pass
        monkeypatch.setattr(executor, '_verify_remote_runner', verify)
        async def ready(session, identity): return await session.get(ExecutionTarget, identity)
        monkeypatch.setattr(executor, 'get_ready_target', ready)
        async def admit(*a, **k): return {'devices': []}
        monkeypatch.setattr(targets, 'admit_target_resources', admit)
        async def transport(connection, argv, **kwargs):
            command, *args = argv
            options = dict(zip(args[::2], args[1::2]))
            calls.append(command)
            payload = worker.component_retry_control(attempt, attempt_id=options['--attempt-id'],
                expected_boot_id=options['--expected-boot-id'], lease_id=options['--lease-id'],
                component_id=options['--component-id'], operation_id=options['--operation-id'], actor=options['--actor'],
                observe_only=command == 'component-retry-status', failure_code=options.get('--failure-code'),
                continuation_lease_id=options.get('--continuation-lease-id'),
                resource_admission=json.loads(options['--resource-admission-json']) if '--resource-admission-json' in options else None)
            if lose_response and command == 'component-retry':
                from services.remote_execution.transport import RemoteTransportError
                raise RemoteTransportError('inert lost response after actual worker authorization')
            return SimpleNamespace(stdout=json.dumps(payload))
        monkeypatch.setattr(executor, 'run_remote', transport)
    async with maker() as session:
        if lose_response:
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as uncertain:
                await routes.retry_md_analysis('md-job-1', session)
            assert uncertain.value.detail['code'] == 'MD_ANALYSIS_RETRY_ACTUATION_UNCERTAIN'
            parent = await session.get(Job, 'md-job-1', populate_existing=True)
            assert parent.provenance['component_retry']['state'] == 'uncertain'
            issued_operation = parent.provenance['component_retry']['operation_id']
        reply = await routes.retry_md_analysis('md-job-1', session)
        parent = await session.get(Job, 'md-job-1')
        if lose_response:
            assert parent.provenance['component_retry']['operation_id'] == issued_operation
        pending = parent.provenance['component_retry']
        assert pending['component_id'] == child
        assert parent.params == original_params
        if target == 'local':
            assert parent.status == 'queued'
            # Existing scheduler handoff: no ledger mutation until GPU reacquisition.
            assert runtime.retry_status(pending['operation_id']) is None
            parent.status = parent.queue_status = 'running'; parent.assigned_gpu = 0
            invocation = nextflow._compile_local_component_retry(parent,
                (path, context, runtime, None, pending))
        else:
            assert parent.provenance['component_retry']['state'] == 'accepted'
            edge = runtime.retry_status(pending['operation_id'])
            invocations = []
            retry_component_workflow(path, component_id=child, operation_id=pending['operation_id'],
                failure_code='execution_failed', actor=pending['actor'], boot_id=boot,
                continuation_lease_id=edge['continuation_lease_id'], resources=edge['resources'], native_invocations=invocations)
            invocation = invocations[0]
        edge = runtime.retry_status(pending['operation_id'])
        assert reply['created_child_ids'] == [edge['child_job_id']]
        assert reply['scheduled_replica_indices'] == [0] and reply['remaining_replica_indices'] == []
        assert invocation.native_parameters.get('md_analysis_retry_spawn_receipt')
        assert not invocation.native_parameters.get('md_retry_spawn_receipt')
        keys = {row.component_key for row in invocation.execution_plan.metadata.static_components}
        assert keys == {'MD_ASSERT_REPLICA_OUTCOME', 'MD_WAIT_FOR_ANALYSIS', 'MD_COLLECT_ANALYSIS',
                        'MD_ASSERT_ANALYSIS_OUTCOME', 'MD_COMPLETION_BARRIER'}
        assert runtime.request(edge['child_job_id']).payload == runtime.request(child).payload
        # Same-operation compiler replay reads analysis receipt, preserving bytes.
        invocations = []
        replay = retry_component_workflow(path, component_id=child, operation_id=pending['operation_id'],
            failure_code='execution_failed', actor=pending['actor'], boot_id=boot,
            continuation_lease_id=edge['continuation_lease_id'], resources=edge['resources'], native_invocations=invocations)
        assert replay == edge and invocations[0].command == invocation.command
        assert invocations[0].generated_inputs == invocation.generated_inputs
        monkeypatch.delenv('BMS_COMPONENT_CONTEXT')
        status = root/'inert-scientific-status.json'
        status.write_bytes(canonical_bytes(dict(total=1, completed=1, failed=0, cancelled=0,
            child_ids=[edge['child_job_id']], child_output_dirs=[str(root/'analysis')])))
        generation = Path(edge['parent_snapshot']['output_dir'])
        result = collect_analysis(status, root/'manifest.json', generation,
            spawn_receipt=Path(invocation.native_parameters['md_analysis_retry_spawn_receipt']))
        assert result['status'] == 'completed'
        assert _bytes(generation/'replicas') == before == _bytes(root/'replicas')
        assert (generation/'manifest.json').read_bytes() == (root/'manifest.json').read_bytes()
        assert len(list((await session.scalars(select(Job))).all())) == 2  # No standalone leaf.
        # Real mapped publication consumes the collector generation, not original output.
        from services.md.lifecycle import _publish_barrier
        from services.md.completion import validate_and_finalize_md_job
        from database import JobArtifact, MdRun
        _publish_barrier(generation, parent.id)
        session.add(Job(id=edge['child_job_id'], name='analysis projection', model_id='molecular_dynamics',
            mode='analyze', parent_job_id=parent.id, child_stage='md_analysis', status='completed',
            queue_status='completed', execution_target_id=parent.execution_target_id,
            params=runtime.request(edge['child_job_id']).payload['params'], output_dir=str(generation/'analysis')))
        parent.child_output_dir = str(generation)
        await session.commit()
        await validate_and_finalize_md_job(parent, session)
        await session.commit()
        assert (await session.get(MdRun, parent.id)).phase == parent.status == 'completed'
        assert list((await session.scalars(select(JobArtifact))).all())
        assert parent.params == original_params
    if target != 'local':
        assert calls.count('component-retry') == calls.count('inert-supervisor') == 1
        assert calls.count('component-retry-status') == 2


@pytest.mark.asyncio
async def test_multiple_failed_lanes_remain_explicit_exact_operations(store, tmp_path, monkeypatch):
    _, maker = store
    root, spec, runtime, context, path, child, boot = retained(tmp_path, monkeypatch, 'local', replicas=2)
    await seed(maker, root, spec, context, path, 'local')
    before = _bytes(root/'replicas')
    async with maker() as session:
        first = await routes.retry_md_analysis('md-job-1', session)
        assert first['scheduled_replica_indices'] == [0]
        assert first['remaining_replica_indices'] == [1]
        parent = await session.get(Job, 'md-job-1')
        pending = parent.provenance['component_retry']
        parent.status = parent.queue_status = 'running'; parent.assigned_gpu = 0
        nextflow._compile_local_component_retry(parent, (path, context, runtime, None, pending))
        edge = runtime.retry_status(pending['operation_id'])
        # Inert native leaf joins and seals just this replacement. The other failed
        # lane keeps the root failed, and no owner automatically retries it.
        assert runtime.claim_root(owner_id='fixture', boot_id=boot)
        runtime.claim(edge['child_job_id'], owner_id='fixture', boot_id=boot)
        from component_runtime import ResultReference, file_identity
        result_path = root/'inert-analysis-success.json'
        result_path.write_bytes(b'{"inert":true}')
        sha, size = file_identity(result_path)
        runtime.complete(edge['child_job_id'], owner_id='fixture', boot_id=boot,
            result={}, references=[ResultReference(edge['child_job_id'], result_path.name, sha, size, 'fixture')])
        runtime.set_root_state('failed', owner_id='fixture', boot_id=boot, quiescent=True,
            generation=edge['generation'], continuation_edge=edge)
        parent.status = parent.queue_status = 'failed'
        # A shared root failure can precede the optional MD lifecycle projection.
        # The failed retained owner, not stale 'retrying' presentation, owns retry.
        assert parent.provenance['md']['analysis_state'] == 'retrying'
        await session.commit()
        second = await routes.retry_md_analysis('md-job-1', session)
        assert second['scheduled_replica_indices'] == [1]
        assert second['remaining_replica_indices'] == []
        assert second['created_child_ids'] != first['created_child_ids']
        assert parent.provenance['component_retry']['operation_id'] != pending['operation_id']
        assert sorted(row['status'] for row in runtime.children(parent.id, 'md_analysis')) == ['completed', 'failed']
        second_pending = parent.provenance['component_retry']
        parent.status = parent.queue_status = 'running'; parent.assigned_gpu = 0
        second_invocation = nextflow._compile_local_component_retry(parent, (path, context, runtime, None, second_pending))
        second_edge = runtime.retry_status(second_pending['operation_id'])
        assert second_edge['generation'] == 2
        assert second_edge['child_job_id'] == second['created_child_ids'][0]
        assert sorted(row['status'] for row in runtime.children(parent.id, 'md_analysis')) == ['completed', 'queued']
        assert 'md_analysis_retry_spawn_receipt' in second_invocation.native_parameters
        assert _bytes(root/'replicas') == before


@pytest.mark.asyncio
@pytest.mark.parametrize('control', ['cancelled', 'review'])
async def test_public_retry_publication_race_keeps_second_session_control(store, tmp_path, monkeypatch, control):
    _, maker = store
    root, spec, runtime, context, path, child, boot = retained(tmp_path, monkeypatch, 'local')
    await seed(maker, root, spec, context, path, 'local')
    publish = executor._publish_remote_transition
    async def racing(session, parent, values, **kwargs):
        if (values.get('provenance') or {}).get('md', {}).get('analysis_state') == 'retrying':
            async with maker() as owner:
                current = await owner.get(Job, parent.id)
                if control == 'cancelled':
                    current.status = current.queue_status = 'cancelled'
                else:
                    current.status = current.queue_status = 'awaiting_input'
                    current.awaiting_input = True
                await owner.commit()
        return await publish(session, parent, values, **kwargs)
    monkeypatch.setattr(executor, '_publish_remote_transition', racing)
    async with maker() as session:
        reply = await routes.retry_md_analysis('md-job-1', session)
        assert reply['created_child_ids'] == []
        assert reply['status'] == ('cancelled' if control == 'cancelled' else 'ownership_changed')
    async with maker() as observer:
        parent = await observer.get(Job, 'md-job-1')
        assert parent.status == ('cancelled' if control == 'cancelled' else 'awaiting_input')
        assert parent.provenance['md'].get('analysis_state') != 'retrying'
        assert runtime.retry_status(parent.provenance['component_retry']['operation_id']) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('control', ['cancelled', 'review'])
async def test_public_retry_keeps_cancel_and_review_authority(store, tmp_path, monkeypatch, control):
    _, maker = store
    root, spec, runtime, context, path, child, boot = retained(tmp_path, monkeypatch, 'local')
    await seed(maker, root, spec, context, path, 'local')
    async with maker() as session:
        parent = await session.get(Job, 'md-job-1')
        if control == 'cancelled': parent.status = parent.queue_status = 'cancelled'
        else: parent.awaiting_input = True
        await session.commit()
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as refused:
            await routes.retry_md_analysis('md-job-1', session)
        assert refused.value.status_code == 409
        assert len(runtime.children()) == 1 and not parent.provenance.get('component_retry')
