"""Receiving-owner proofs only; native kernels and external runners stay inert."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import BackgroundTasks
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from component_runtime import SourceIdentity
from database import Base, Job, MdRun
from routers import jobs
from schemas import JobCreate
from services import gpu_orchestrator as gpu, nextflow
from services.md import lifecycle
from services.remote_execution.targets import ExecutionTargetError, selected_plan_target_resources
from test_md_job_v2_contract import _catalog, _v2_spec


@pytest_asyncio.fixture
async def store(tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "receiver.db"}')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


def spec():
    catalog = _catalog()
    view = catalog.view()
    return _v2_spec(view.get_profile('gmx_amber99sb_ildn_tip3p_smoke_v1'), view.catalog_digest)


def plan(params):
    return nextflow.build_selected_execution_plan(model_id='molecular_dynamics', mode='simulate',
        entrypoint=nextflow.MODEL_MODE_WORKFLOW_ENTRYPOINTS[('molecular_dynamics', 'simulate')],
        requested=params, effective=params, native_parameters={},
        source_identity=SourceIdentity('a' * 40, 'b' * 40),
        metadata_settings=nextflow._native_plan_metadata_settings('molecular_dynamics', params))


@pytest.mark.parametrize('engine', ['gromacs', 'openmm'])
@pytest.mark.parametrize('target', ['local', 'worker:fixture'])
def test_selected_descendant_projection_requires_one_shared_gpu(engine, target):
    request = spec()
    request['engine'] = engine
    params = {'md_job_spec': request}
    before = copy.deepcopy(params)
    selected = plan(params)
    resources = selected_plan_target_resources(SimpleNamespace(id=target), selected, gpu_ids=[0], scratch_bytes=0)
    assert resources['gpu_ids'] == [0]
    assert max(row['gpu_count'] for row in resources['components']) == 1
    assert any(row['gpu_count'] == 0 for row in resources['components'])
    with pytest.raises(ExecutionTargetError, match='exceeds assigned'):
        selected_plan_target_resources(SimpleNamespace(id=target), selected, gpu_ids=[], scratch_bytes=0)
    assert gpu.md_root_vram_estimate(params, 8192) == 8192
    assert gpu.md_root_vram_estimate(params, 8192, selected_plan=selected.to_dict()) == 8192
    assert params == before


@pytest.mark.asyncio
async def test_real_create_job_v2_reserves_descendants_and_initializes(store, tmp_path, monkeypatch):
    from services.md.launch_contract import normalize_md_job_spec, materialize_md_job_spec
    catalog = _catalog()
    monkeypatch.setattr(jobs, 'require_molecular_dynamics_feature', lambda _: None)
    monkeypatch.setattr(jobs, '_raise_if_workflow_launches_disabled', lambda _: None)
    monkeypatch.setattr(jobs, 'get_registry', lambda: SimpleNamespace(reload=lambda: None, validate_job_params=lambda *a: []))
    monkeypatch.setattr(jobs, 'get_results_dir', lambda: tmp_path / 'results')
    monkeypatch.setattr(jobs, 'normalize_md_job_spec', lambda **kw: normalize_md_job_spec(**kw, chemistry_catalog=catalog))
    monkeypatch.setattr(jobs, 'materialize_md_job_spec', lambda **kw: materialize_md_job_spec(**kw, chemistry_catalog=catalog))
    request = spec()
    async with store() as session:
        response = await jobs.create_job(JobCreate(name='md-resource-root', model_id='molecular_dynamics',
            mode='simulate', params={'md_job_spec': request}, pinned_gpu=3), BackgroundTasks(), session,
            experiment_session=None, _md_input_resolver=lambda value: value)
        root = await session.get(Job, response.id)
        durable = await session.get(MdRun, root.id)
        assert root.vram_estimate_mb > 0
        assert root.pinned_gpu == 3
        assert root.assigned_gpu is None
        assert root.parent_job_id is None
        assert durable is not None
        assert root.params['md_job_spec']['random_seed'] == request['random_seed']
        assert root.params['md_job_spec']['stages'] == request['stages']
        assert len(list((await session.scalars(select(Job))).all())) == 1


@pytest.mark.asyncio
async def test_accepted_v1_caller_hands_unchanged_normalized_request_to_existing_initializer(store, tmp_path, monkeypatch):
    # This pins caller ownership; actual v1 storage is controls lane's receiver.
    request = spec()
    request['schema'] = 'bms.md.job.v1'
    request.pop('chemistry')
    monkeypatch.setattr(jobs, 'require_molecular_dynamics_feature', lambda _: None)
    monkeypatch.setattr(jobs, '_raise_if_workflow_launches_disabled', lambda _: None)
    monkeypatch.setattr(jobs, 'get_registry', lambda: SimpleNamespace(reload=lambda: None, validate_job_params=lambda *a: []))
    monkeypatch.setattr(jobs, 'get_results_dir', lambda: tmp_path / 'results')
    monkeypatch.setattr(jobs, 'normalize_md_job_spec', lambda **kw: kw['params']['md_job_spec'])
    monkeypatch.setattr(jobs, 'materialize_md_job_spec', lambda **kw: dict(kw['params']))
    initialize = AsyncMock()
    monkeypatch.setattr(jobs, 'create_md_run', initialize)
    async with store() as session:
        await jobs.create_job(JobCreate(name='accepted-legacy-caller', model_id='molecular_dynamics',
            mode='simulate', params={'md_job_spec': request}), BackgroundTasks(), session, experiment_session=None)
        initialize.assert_awaited_once()
        assert initialize.call_args.kwargs['normalized_request'] == request
        assert 'chemistry' not in initialize.call_args.kwargs['normalized_request']


@pytest.mark.parametrize('device', [0, 3])
def test_compiler_binds_physical_execution_without_changing_saved_science(tmp_path, monkeypatch, device):
    request = spec()
    path = tmp_path / 'inputs' / 'md_job_config.json'
    path.parent.mkdir()
    payload = json.dumps(request).encode()
    path.write_bytes(payload)
    params = {'md_job_spec': request, 'md_job_config': str(path), 'gpu_id': device}
    before = copy.deepcopy(params)
    invocation = nextflow.compile_nextflow_invocation('molecular_dynamics', 'simulate', params,
        str(tmp_path), job_id='md-root', source_identity=SourceIdentity('a' * 40, 'b' * 40))
    generated = next(row for row in invocation.generated_inputs if row.relative_path == 'inputs/md_execution_config.json')
    effective = json.loads(generated.payload)
    assert effective['execution']['gpu_id'] == str(device)
    effective['execution']['gpu_id'] = request['execution']['gpu_id']
    assert effective == request
    assert path.read_bytes() == payload
    assert params == before
    # The real engine module retains singleton logical mapping, separately from
    # the physical device transported by the compiler and component context.
    engine_module = nextflow.PROJECT_ROOT / 'modules/experimental/molecular_dynamics/gromacs_replica.nf'
    assert '--gpu-id 0' in engine_module.read_text()
    job = SimpleNamespace(id='md-root', model_id='molecular_dynamics', mode='simulate',
        assigned_gpu=device, params={'pinned_gpus': [7]}, provenance={}, status='running')
    env = {'BMS_WORK': str(tmp_path / 'work')}
    command = nextflow._component_launch_command(invocation, job, list(invocation.command), env,
        attempt=0, output_dir=str(tmp_path))
    context = json.loads(Path(env['BMS_COMPONENT_CONTEXT']).read_text())
    assert context['resources']['gpu_ids'] == [device]
    assert context['resources']['gpu_id'] == device
    assert context['root_job_id'] == job.id
    assert 'component_adapter.py' in command[1]


@pytest.mark.parametrize('phase', ['validating', 'preparing', 'replicas_queued', 'replicas_running',
    'checkpointing', 'paused', 'cancelling', 'reconciling', 'finalizing'])
@pytest.mark.asyncio
async def test_generic_recovery_cannot_fail_durable_owned_simulate_root(store, phase):
    async with store() as session:
        root = Job(id='root', name='MD', model_id='molecular_dynamics', mode='simulate',
            status='running', queue_status='running', params={})
        session.add(root)
        await session.flush()
        from services.md.state import create_md_run
        durable = await create_md_run(session, job=root, normalized_request={**spec(), 'job_id': root.id,
            'chemistry': {'profile_id': 'inert-profile', 'profile_sha256': 'a' * 64, 'assurance': 'smoke_fixture'}})
        durable.phase = phase
        await session.commit()
        root.status = root.queue_status = 'failed'
        assert await gpu._commit_reconciled_job_mutations(session) == 0
        await session.commit()
        current = await session.get(Job, 'root')
        assert current.status == current.queue_status == 'running'
        assert (await session.get(MdRun, 'root')).phase == phase


@pytest.mark.parametrize('state', ['cancelled', 'awaiting_input', 'durable_cancelling', 'running'])
@pytest.mark.asyncio
async def test_late_callback_reloads_parent_command_ownership(store, monkeypatch, state):
    from services.md.state import create_md_run
    async with store() as session:
        root = Job(id='root', name='MD', model_id='molecular_dynamics', mode='simulate',
            status='running', queue_status='running', params={})
        session.add(root)
        await session.flush()
        await create_md_run(session, job=root, normalized_request={**spec(), 'job_id': root.id,
            'chemistry': {'profile_id': 'inert-profile', 'profile_sha256': 'a' * 64, 'assurance': 'smoke_fixture'}})
        await session.commit()
        async with store() as operator:
            if state == 'durable_cancelling':
                await operator.execute(update(MdRun).where(MdRun.job_id == 'root').values(phase='cancelling'))
            elif state != 'running':
                await operator.execute(update(Job).where(Job.id == 'root').values(status=state,
                    queue_status='cancelled' if state == 'cancelled' else 'running', awaiting_input=state == 'awaiting_input'))
            await operator.commit()
        finalizer = AsyncMock(return_value={'status': 'completed'})
        monkeypatch.setattr(lifecycle, 'reconcile_md_analysis_parent', finalizer)
        outcome = await nextflow.reconcile_md_analysis_parent_if_current('root', session)
        if state == 'running':
            finalizer.assert_awaited_once_with('root', session)
        else:
            finalizer.assert_not_awaited()
            assert outcome == {'status': 'preserved'}


@pytest.mark.asyncio
async def test_scheduler_reprojects_legacy_cpu_root_and_claims_one_gpu(store, tmp_path):
    request = spec()
    async with store() as session:
        session.add(Job(id='root', name='MD', model_id='molecular_dynamics', mode='simulate',
            status='queued', queue_status='queued', params={'md_job_spec': request},
            output_dir=str(tmp_path), vram_estimate_mb=0))
        await session.commit()
    launched = []
    async def launch(**kwargs):
        launched.append(kwargs)
    device = SimpleNamespace(index=0, name='fixture-gpu', memory_used_mb=0, memory_total_mb=49152,
        memory_free_mb=49152, utilization=0, temperature=30, processes=[])
    owner = gpu.GPUOrchestrator(store, lambda: [device], launch)
    await owner._process_cycle()
    assert len(launched) == 1
    async with store() as session:
        root = await session.get(Job, 'root')
        assert root.assigned_gpu == 0
        assert root.vram_estimate_mb > 0
        assert root.queue_status == 'running'
        assert len(list((await session.scalars(select(Job))).all())) == 1


@pytest.mark.asyncio
async def test_remote_scheduler_claims_descendant_gpu_on_root_only(store, tmp_path, monkeypatch):
    from datetime import datetime
    from database import ExecutionTarget
    from services.remote_execution import targets
    request = spec()
    selected = plan({'md_job_spec': request})
    async with store() as session:
        session.add(ExecutionTarget(id='vast:fixture', provider='vast', provider_instance_id='fixture',
            active=True, state='ready', capabilities={'gpu_count': 4}, provider_metadata={'inventory': {
                'status': 'complete', 'present': True, 'running': True,
                'checked_at': datetime.utcnow().isoformat()}}))
        session.add(Job(id='root', name='MD', model_id='molecular_dynamics', mode='simulate',
            status='queued', queue_status='queued', params={'md_job_spec': request},
            output_dir=str(tmp_path), vram_estimate_mb=0, execution_target_id='vast:fixture',
            provenance={'execution_plan_approval': {'plan': selected.to_dict()}}))
        await session.commit()
    async def telemetry(target):
        assert target.id == 'vast:fixture'
        return {'available': True, 'observed_at': 'inert', 'gpus': [
            {'index': 3, 'uuid': 'GPU-fixture', 'memory_total_mb': 49152, 'memory_used_mb': 0}]}
    monkeypatch.setattr(targets, 'remote_target_telemetry', telemetry)
    launched = []
    async def launch(**kwargs):
        launched.append(kwargs)
    owner = gpu.GPUOrchestrator(store, lambda: [], launch)
    await owner._process_cycle()
    assert len(launched) == 1
    async with store() as session:
        root = await session.get(Job, 'root')
        assert root.assigned_gpu == 3
        assert root.vram_estimate_mb > 0
        assert root.queue_status == 'preparing'
        assignment = root.provenance['remote_execution_assignment']
        assert assignment['gpu_indices'] == [3]
        assert assignment['root_job_id'] == root.id
        assert len(list((await session.scalars(select(Job))).all())) == 1
