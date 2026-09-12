"""Real attempt-ledger/export/host SQLite regressions; no science or transport."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from component_runtime import (
    ComponentRequest, ComponentRuntime, NativeInvocation, ResultReference,
    SelectedExecutionMetadata, SelectedExecutionPlan, SourceIdentity,
)
from database import Base, Job, MdAttemptSegment, MdReplicaRun, MdRun
from services.result_ingester import ingest_component_projection
from services.result_state_integrity import finalize_component_projection
from services.md.state import MdStateError, retry_replica_attempt

SOURCE = SourceIdentity('a' * 40, 'b' * 40)
ORIGINAL = 'c' * 64
OWNER = dict(owner_id='fixture-owner', boot_id='fixture-boot')


@pytest_asyncio.fixture
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'host.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        parent = Job(id='root', name='MD fixture', model_id='molecular_dynamics',
            mode='simulate', status='failed', queue_status='failed', params={},
            execution_target_id='worker', remote_attempt_id='attempt',
            execution_source_revision=SOURCE.revision, execution_source_tree=SOURCE.tree,
            output_dir=str(tmp_path))
        session.add(parent)
        await session.flush()
        session.add(MdRun(job_id=parent.id, normalized_request={
            'replicas': 1, 'random_seed': 123, 'engine': 'gromacs'},
            request_sha256='d'*64, chemistry_profile_id='fixture',
            chemistry_profile_sha256='e'*64, chemistry_assurance='fixture', phase='failed'))
        await session.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


def runtime(tmp_path):
    return ComponentRuntime(tmp_path / 'worker.sqlite', attempt_id='attempt',
        root_job_id='root', target_id='worker', lease_id='original-lease',
        artifact_root=tmp_path, source_identity=SOURCE.__dict__, plan_sha256=ORIGINAL)


def request(attempt=0):
    return ComponentRequest.capture(parent_job_id='root', stage='md_replica',
        child_key=f'replica-0-attempt-{attempt}', payload=dict(model_id='molecular_dynamics',
        mode='replica', params=dict(md_replica_index=0, md_attempt=attempt,
        md_replica_seed=123, md_engine='gromacs', md_execution_plan_sha256='d'*64,
        md_compatibility_key='e'*64)))


def execute_child(ledger, req, *, state='failed', receipt=None):
    child = ledger.submit(req)
    ledger.claim(child, **OWNER)
    output = ledger.artifact_root / 'components' / child.replace(':', '-')
    output.mkdir(parents=True, exist_ok=True)
    ledger.bind_native_parent(child, dict(id=child, parent_job_id=req.parent_job_id,
        child_stage=req.stage, model_id=req.payload['model_id'], mode=req.payload['mode'],
        params=req.payload['params'], provenance={}, execution_target_id='worker',
        output_dir=str(output)), **OWNER)
    if state == 'failed':
        ledger.fail(child, reason='fixture failure', quiescent=True,
            failure_receipt=receipt, **OWNER)
    else:
        ledger.execution_finished(child, output_dir=str(output), exit_code=0, **OWNER)
        if state == 'completed':
            artifact = output / 'native.json'
            artifact.write_bytes(b'{"native_fixture":true}')
            ref = ResultReference(child, artifact.relative_to(ledger.artifact_root).as_posix(),
                hashlib.sha256(artifact.read_bytes()).hexdigest(), artifact.stat().st_size, 'fixture.v1')
            ledger.complete_validated_child(child, result={'output_dir': str(output)}, references=[ref])
    return child


def publish(ledger, *, state='failed', generation=0, edge=None):
    ledger.claim_root(**OWNER)
    detail = dict(quiescent=True, generation=generation)
    if edge is not None:
        detail['continuation_edge'] = edge
    ledger.set_root_state(state, **OWNER, **detail)
    path = ledger.publish_projection()
    envelope = ledger.export_projection()
    context = {key: envelope[key] for key in ('root_job_id', 'attempt_id', 'target_id',
        'lease_id', 'source_identity', 'plan_sha256', 'current_plan_sha256', 'generation')}
    context.update(artifact_root=str(ledger.artifact_root),
        projection_relative_path=path.relative_to(ledger.artifact_root).as_posix(),
        projection_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    return context


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [
    ('source_identity', {'revision': 'f'*40, 'tree': 'b'*40}),
    ('attempt_id', 'stale-attempt'), ('lease_id', 'stale-lease'), ('target_id', 'foreign'),
    ('plan_sha256', 'f'*64), ('current_plan_sha256', 'f'*64), ('generation', 1),
    ('projection_sha256', 'f'*64),
])
async def test_export_import_rejects_stale_authority(store, tmp_path, field, value):
    ledger = runtime(tmp_path)
    execute_child(ledger, request())
    context = publish(ledger)
    async with store() as session:
        parent = await session.get(Job, 'root')
        with pytest.raises(ValueError, match='binding conflicts|digest conflicts'):
            await ingest_component_projection(parent, str(tmp_path), session,
                expected_context={**context, field: value})
        assert list((await session.scalars(select(Job.id))).all()) == ['root']


@pytest.mark.asyncio
@pytest.mark.parametrize('receipt,expected', [
    ({'code': 'spawn_rejected', 'source': 'scheduler_launch', 'message': 'not started'}, 'spawn_rejected'),
    ({'code': 'spawn_rejected', 'source': 'scientific_runner'}, 'execution_failed'),
    ({'code': 'numerical_failure', 'source': 'scheduler_launch'}, 'execution_failed'),
    (None, 'execution_failed'),
])
async def test_failure_receipt_reaches_native_allowlist_without_success(store, tmp_path, receipt, expected):
    ledger = runtime(tmp_path)
    identity = execute_child(ledger, request(), receipt=receipt)
    context = publish(ledger)
    async with store() as session:
        parent = await session.get(Job, 'root')
        children = await ingest_component_projection(parent, str(tmp_path), session, expected_context=context)
        child = children[0]
        replica = await session.scalar(select(MdReplicaRun))
        assert replica.failure['code'] == expected
        assert child.status == 'failed' and replica.active is False
        assert child.provenance.get('failure_receipt') == receipt
        completed_at = child.completed_at
        from services.md.read_model import md_queue_snapshot, md_run_snapshot
        queue = (await md_queue_snapshot(session, limit=10))['runs'][0]
        detail = await md_run_snapshot(session, 'root')
        assert ('retry_dynamics' in queue['allowed_actions']) == (expected == 'spawn_rejected')
        assert ('retry_dynamics' in detail['allowed_actions']) == (expected == 'spawn_rejected')
        run = await session.get(MdRun, 'root')
        run.controls_blocked = True
        assert 'retry_dynamics' not in (await md_queue_snapshot(session, limit=10))['runs'][0]['allowed_actions']
        assert 'retry_dynamics' not in (await md_run_snapshot(session, 'root'))['allowed_actions']
        run.controls_blocked = False
        if expected == 'execution_failed':
            with pytest.raises(MdStateError, match='scientific review'):
                await retry_replica_attempt(session, job_id='root', replica_index=0,
                    expected_version=0, idempotency_key='denied')
        with pytest.raises(ValueError, match='native parent completion'):
            await finalize_component_projection(parent, session)
        await session.commit()
    async with store() as session:
        parent = await session.get(Job, 'root')
        await ingest_component_projection(parent, str(tmp_path), session, expected_context=context)
        child = await session.get(Job, identity)
        assert child.completed_at == completed_at
        assert child.status == 'failed' and parent.status == 'failed'
        assert len(list((await session.scalars(select(MdReplicaRun))).all())) == 1


def retry_generation(ledger):
    # Typed recorder plan exercises the ledger, not scientific compilation/admission.
    from component_runtime import NativeComponent, NativeArtifactRole, SelectedDependency
    metadata = SelectedExecutionMetadata(availability='fixture', settings_authority=__file__,
        static_components=(NativeComponent('recorder', __file__, b'{}',
            dependency_ids=('python',), input_role_ids=('input',), output_role_ids=('output',),
            resources_json=b'{"cpus":1}', lifecycle_authority=__file__,
            lifecycle_json=b'{"max_retries":0}'),), dynamic_templates=(),
        dependencies=(SelectedDependency('python', 'support_python', None, __file__),),
        artifact_roles=tuple(NativeArtifactRole(key, 'recorder', direction, 'fixture', __file__)
            for key, direction in [('input', 'input'), ('output', 'output')]),
        external_services=(), result_contract_json=b'{}', admission_authority=__file__,
        retrieval_authority=__file__, blockers=(), closure_reviewed=True, descriptors_reviewed=True)
    assert metadata.complete
    plan = SelectedExecutionPlan(SOURCE, 'fixture', 'fixture', 'fixture', 'fixture.nf',
        b'{}', b'{}', b'{}', metadata)
    invocation = replace(NativeInvocation.capture(model_id='fixture', mode='fixture',
        command=['never-executed'], requested={}, effective={}, native_parameters={},
        entrypoint='fixture.nf'), source_identity=SOURCE, execution_plan=plan)
    resources = {'execution_target_id': 'worker'}
    return dict(replacement=request(1), operation_id='retry-once', actor='fixture',
        boot_id=OWNER['boot_id'], invocation=invocation, continuation_lease_id='renewed-lease',
        parent_snapshot={'id': 'root', 'output_dir': str(ledger.artifact_root / 'generation-1')},
        resources=resources, retry_context={'resources': resources})


@pytest.mark.asyncio
async def test_replacement_generation_keeps_original_plan_and_history(store, tmp_path, monkeypatch):
    ledger = runtime(tmp_path)
    old = execute_child(ledger, request(), receipt={'code': 'spawn_rejected', 'source': 'scheduler_launch'})
    initial = publish(ledger)
    async with store() as session:
        parent = await session.get(Job, 'root')
        await ingest_component_projection(parent, str(tmp_path), session, expected_context=initial)
        await session.commit()
    kwargs = retry_generation(ledger)
    from services.remote_execution import executor
    calls = []
    async def retained_retry(session, parent, *, component_id, operation_id, actor, failure_code):
        assert component_id == old and operation_id == 'retry-once' and failure_code == 'spawn_rejected'
        calls.append(operation_id)
        return ledger.retry_component(component_id, **kwargs)
    monkeypatch.setattr(executor, 'retry_component_execution', retained_retry)
    async with store() as session:
        first = await retry_replica_attempt(session, job_id='root', replica_index=0,
            expected_version=0, idempotency_key='retry-once')
        assert first.child_job_id is None
        repeated = await retry_replica_attempt(session, job_id='root', replica_index=0,
            expected_version=0, idempotency_key='retry-once')
        assert repeated.id == first.id
        assert len(list((await session.scalars(select(Job))).all())) == 2
        source_segment = await session.scalar(select(MdAttemptSegment).where(
            MdAttemptSegment.replica_run_id == first.id))
        assert source_segment.source_segment_id is not None
        await session.commit()
    edge = ledger.retry_status('retry-once')
    assert ledger.retry_component(old, **kwargs) == edge
    new = execute_child(ledger, request(1), state='completed')
    current = publish(ledger, state='completed', generation=1, edge=edge)
    assert current['plan_sha256'] == ORIGINAL
    assert current['current_plan_sha256'] != ORIGINAL
    assert current['lease_id'] == 'original-lease'
    async with store() as session:
        parent = await session.get(Job, 'root')
        children = await ingest_component_projection(parent, str(tmp_path), session, expected_context=current)
        assert {child.id for child in children} == {old, new}
        assert (await session.get(Job, old)).status == 'failed'
        assert (await session.get(Job, new)).status == 'paused'
        replicas = list((await session.scalars(select(MdReplicaRun).order_by(MdReplicaRun.attempt))).all())
        assert [(r.attempt, r.state, r.active) for r in replicas] == [(0, 'failed', False), (1, 'completed', False)]
        # Shared promotion alone requires a native parent verdict; diagnostics cannot supply it.
        parent.status = parent.queue_status = 'completed'
        await finalize_component_projection(parent, session)
        stamp = (await session.get(Job, new)).completed_at
        await session.commit()
    async with store() as session:
        parent = await session.get(Job, 'root')
        await ingest_component_projection(parent, str(tmp_path), session, expected_context=current)
        await finalize_component_projection(parent, session)
        assert (await session.get(Job, new)).completed_at == stamp
        assert (await session.get(Job, old)).status == 'failed'
        assert len(list((await session.scalars(select(MdAttemptSegment))).all())) == 2
        assert len(list((await session.scalars(select(Job))).all())) == 3
        before_replay = len(calls)
        bound = await retry_replica_attempt(session, job_id='root', replica_index=0,
            expected_version=0, idempotency_key='retry-once')
        assert bound.child_job_id == new and len(calls) == before_replay
        from services.md.read_model import md_queue_snapshot, md_run_snapshot
        queue = (await md_queue_snapshot(session, limit=10))['runs'][0]
        detail = await md_run_snapshot(session, 'root')
        assert queue['replica_summary'] == {'completed': 1}
        assert 'retry_dynamics' not in queue['allowed_actions']
        assert 'retry_dynamics' not in detail['allowed_actions']


@pytest.mark.asyncio
async def test_zero_exit_diagnostic_never_promotes_child(store, tmp_path):
    ledger = runtime(tmp_path)
    identity = execute_child(ledger, request(), state='execution_finished')
    context = publish(ledger)
    async with store() as session:
        parent = await session.get(Job, 'root')
        await ingest_component_projection(parent, str(tmp_path), session, expected_context=context)
        parent.status = parent.queue_status = 'completed'
        await finalize_component_projection(parent, session)
        child = await session.get(Job, identity)
        assert child.status == child.queue_status == 'paused'
        assert child.completed_at is None


@pytest.mark.asyncio
async def test_full_native_completion_and_reopened_idempotence(store, tmp_path, monkeypatch):
    from test_md_analysis_results import test_replica_reporting_never_pools_frames_as_biological_replicates
    from services.md.completion import validate_and_finalize_md_job
    from services.md.read_model import md_run_snapshot
    from database import JobArtifact
    root = tmp_path / 'native'
    root.mkdir()
    # Reuse the existing native-format fixture, including its scientific denial
    # controls. Opaque fixture trajectory/parquet bytes are not real inference.
    test_replica_reporting_never_pools_frames_as_biological_replicates(root, monkeypatch)
    ledger = ComponentRuntime(root / 'worker.sqlite', attempt_id='attempt',
        root_job_id='md-job-1', target_id='worker', lease_id='original-lease',
        artifact_root=root, source_identity=SOURCE.__dict__, plan_sha256=ORIGINAL)
    spec = json.loads((root / 'replicas/replica_0/manifest.json').read_text())['config']
    replica_req = ComponentRequest.capture(parent_job_id='md-job-1', stage='md_replica',
        child_key='replica-0', payload=dict(model_id='molecular_dynamics', mode='replica',
        params={**request().payload['params'], 'md_replica_seed': spec['random_seed']}))
    replica_id = execute_child(ledger, replica_req, state='completed')
    analysis_req = ComponentRequest.capture(parent_job_id='md-job-1', stage='md_analysis',
        child_key='analysis-0', payload=dict(model_id='molecular_dynamics', mode='analyze', params={}))
    analysis_id = execute_child(ledger, analysis_req, state='completed')
    aggregate_path = root / 'manifest.json'
    aggregate = json.loads(aggregate_path.read_text())
    aggregate['lineage']['child_ids'] = [replica_id]
    aggregate_path.write_text(json.dumps(aggregate))
    aggregate_sha = hashlib.sha256(aggregate_path.read_bytes()).hexdigest()
    collection_path = root / 'analysis/manifest.json'
    collection = json.loads(collection_path.read_text())
    collection.update(child_ids=[analysis_id], aggregate_manifest_sha256=aggregate_sha)
    collection_path.write_text(json.dumps(collection))
    barrier_path = root / 'md_completion_barrier.json'
    barrier = json.loads(barrier_path.read_text())
    barrier.update(aggregate_manifest_sha256=aggregate_sha,
        analysis_manifest_sha256=hashlib.sha256(collection_path.read_bytes()).hexdigest())
    barrier_path.write_text(json.dumps(barrier))
    context = publish(ledger, state='completed')
    async with store() as session:
        parent = Job(id='md-job-1', name='Native MD', model_id='molecular_dynamics', mode='simulate',
            status='running', queue_status='running', output_dir=str(root), params={'md_job_spec': spec},
            execution_target_id='worker', remote_attempt_id='attempt',
            execution_source_revision=SOURCE.revision, execution_source_tree=SOURCE.tree)
        session.add(parent)
        await session.flush()
        run = MdRun(job_id=parent.id, normalized_request=spec, request_sha256='d'*64,
            chemistry_profile_id='fixture', chemistry_profile_sha256='e'*64,
            chemistry_assurance='fixture', phase='finalizing')
        session.add(run)
        await session.flush()
        await ingest_component_projection(parent, str(root), session, expected_context=context)
        snapshot = await validate_and_finalize_md_job(parent, session)
        assert snapshot['replica_child_ids'] == [replica_id]
        await finalize_component_projection(parent, session)
        await session.commit()
        stamps = (parent.completed_at, (await session.get(Job, replica_id)).completed_at,
            (await session.scalar(select(MdReplicaRun).where(MdReplicaRun.md_job_id == parent.id))).completed_at)
        version = run.state_version
        count = len(list((await session.scalars(select(JobArtifact))).all()))
        assert count > 0
    async with store() as session:
        parent = await session.get(Job, 'md-job-1')
        await ingest_component_projection(parent, str(root), session, expected_context=context)
        await validate_and_finalize_md_job(parent, session)
        await finalize_component_projection(parent, session)
        await session.commit()
        run = await session.get(MdRun, parent.id)
        replica = await session.scalar(select(MdReplicaRun).where(MdReplicaRun.md_job_id == parent.id))
        assert (parent.completed_at, (await session.get(Job, replica_id)).completed_at, replica.completed_at) == stamps
        assert run.state_version == version
        assert replica.state == 'completed' and replica.active is False
        assert len(list((await session.scalars(select(JobArtifact))).all())) == count
        topology = await session.scalar(select(JobArtifact).where(JobArtifact.owner_job_id == replica_id,
            JobArtifact.logical_path == 'replicas/replica_0/final.pdb'))
        assert topology.provenance['semantic_roles'] == ['analysis_topology', 'representative_structure']
        view = await md_run_snapshot(session, parent.id)
        assert view['phase'] == 'completed'


@pytest.mark.asyncio
@pytest.mark.parametrize('tamper', ['initial-plan', 'missing-edge', 'foreign-edge', 'duplicate-child', 'escaping-path', 'artifact-bytes'])
async def test_authenticated_publication_still_requires_native_identity(store, tmp_path, tamper):
    ledger = runtime(tmp_path)
    execute_child(ledger, request(), state='completed')
    context = publish(ledger, state='completed')
    path = tmp_path / context['projection_relative_path']
    envelope = json.loads(path.read_text())
    if tamper == 'initial-plan':
        envelope['current_plan_sha256'] = context['current_plan_sha256'] = 'f'*64
    elif tamper in {'missing-edge', 'foreign-edge'}:
        envelope['generation'] = context['generation'] = 1
        envelope['root_state']['generation'] = 1
        if tamper == 'foreign-edge':
            envelope['root_state']['continuation_edge'] = {'plan_sha256': 'f'*64}
    elif tamper == 'duplicate-child':
        envelope['components'].append(envelope['components'][0])
    elif tamper == 'escaping-path':
        envelope['components'][0]['output_relative_path'] = '../foreign'
    else:
        reference = envelope['components'][0]['result']['references'][0]
        (tmp_path / reference['relative_path']).write_bytes(b'changed native bytes')
    path.write_text(json.dumps(envelope))
    context['projection_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    async with store() as session:
        parent = await session.get(Job, 'root')
        with pytest.raises(ValueError):
            await ingest_component_projection(parent, str(tmp_path), session, expected_context=context)
        assert list((await session.scalars(select(Job.id))).all()) == ['root']


@pytest.mark.asyncio
async def test_same_generation_replay_cannot_rewrite_failure_history(store, tmp_path):
    ledger = runtime(tmp_path)
    identity = execute_child(ledger, request(), receipt={'code': 'spawn_rejected', 'source': 'scheduler_launch'})
    context = publish(ledger)
    async with store() as session:
        parent = await session.get(Job, 'root')
        await ingest_component_projection(parent, str(tmp_path), session, expected_context=context)
        await session.commit()
    path = tmp_path / context['projection_relative_path']
    envelope = json.loads(path.read_text())
    envelope['components'][0]['failure_receipt']['code'] = 'different'
    path.write_text(json.dumps(envelope))
    context['projection_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    async with store() as session:
        parent = await session.get(Job, 'root')
        with pytest.raises(ValueError, match='sealed execution history'):
            await ingest_component_projection(parent, str(tmp_path), session, expected_context=context)
        assert (await session.get(Job, identity)).provenance['failure_receipt']['code'] == 'spawn_rejected'
