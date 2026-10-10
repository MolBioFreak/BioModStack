"""Actual scheduler/SQL claims, isolated SQLite; no provider or science calls."""
from datetime import datetime
import asyncio
import sqlite3
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import inspect, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, ExecutionTarget, Job
import services.gpu_orchestrator as scheduler
from migrations.enable_multiple_execution_targets import migrate
from test_managed_runtime_safety import critical_package


@pytest.mark.parametrize('shortage', [None, 'cpus', 'memory_bytes', 'scratch_bytes'])
def test_local_checkpoint_uses_actual_plan_and_current_local_authority(tmp_path, monkeypatch, shortage):
    from types import SimpleNamespace
    import json
    import shutil
    import biomodstack_local_resources as local
    from component_runtime import GeneratedInput
    from services.nextflow import component_checkpoint_resources
    component = SimpleNamespace(component_key='native', resources_json=json.dumps(
        dict(cpus=dict(value=4), memory=dict(value='12 GB'))))
    invocation = SimpleNamespace(execution_plan=SimpleNamespace(metadata=SimpleNamespace(
        static_components=(component,), dynamic_templates=())),
        generated_inputs=[GeneratedInput('new.json', b'new'), GeneratedInput('retained.json', b'old')])
    (tmp_path / 'retained.json').write_bytes(b'old')
    context = dict(target_id='local', artifact_root=str(tmp_path),
        resources=dict(gpu_ids=[], gpu_id=None, required=dict(cpus=1, memory_bytes=1, scratch_bytes=999999)))
    monkeypatch.setattr(local, 'applied_local_policy', lambda: local.LocalCapacity(8, 16*1024**3))
    monkeypatch.setattr(local, 'detect_local_capacity', lambda: local.LocalCapacity(
        1 if shortage == 'cpus' else 8, 1 if shortage == 'memory_bytes' else 16*1024**3))
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(free=0 if shortage == 'scratch_bytes' else 3))
    if shortage:
        with pytest.raises(ValueError, match='capacity'):
            component_checkpoint_resources(invocation, context)
    else:
        result = component_checkpoint_resources(invocation, context)
        assert result['required'] == dict(cpus=4, memory_bytes=12*1024**3, scratch_bytes=3)
        assert result['gpu_ids'] == [] and not result['admission_required']
        assert context['resources']['required']['cpus'] == 1


@pytest_asyncio.fixture
async def workers(tmp_path, monkeypatch):
    from services.remote_execution import targets
    async def telemetry(target):
        return dict(available=True, observed_at="fixture", gpus=[dict(index=i, uuid=f"GPU-{i}",
            memory_total_mb=100000, memory_used_mb=0, utilization=99) for i in range(4)])
    monkeypatch.setattr(targets, "remote_target_telemetry", telemetry)
    path = tmp_path / 'workers.db'
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    # Reproduce the deployed old constraint; only the production migration
    # may remove it. Fleet cases must run, never skip or drop it in test SQL.
    with sqlite3.connect(path) as legacy:
        legacy.execute("CREATE UNIQUE INDEX uq_execution_targets_one_active ON execution_targets(active) WHERE active = 1")
    migrate(path)
    async with engine.begin() as connection:
        indexes = await connection.run_sync(lambda sync: inspect(sync).get_indexes("execution_targets"))
    assert not any(index["name"] == "uq_execution_targets_one_active" for index in indexes)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        for ordinal in (1, 2):
            target = f"vast:{ordinal}"
            session.add(ExecutionTarget(id=target, provider="vast", provider_instance_id=str(ordinal),
                active=True, state="ready", capabilities={"gpu_count": 4}, provider_metadata={"inventory": {
                    "status": "complete", "present": True, "running": True,
                    "checked_at": datetime.utcnow().isoformat()}}))
            session.add(Job(id=f"job-{ordinal}", name=f"job-{ordinal}", params={},
                status="queued", queue_status="queued", paused=False, priority=3-ordinal,
                model_id="cpu-only", mode="run", vram_estimate_mb=0,
                execution_target_id=target, output_dir=str(tmp_path / f"output-{ordinal}")))
        await session.commit()
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_slow_target_control_does_not_block_other_target_or_release_terminal_lease(workers, monkeypatch):
    from services.remote_execution import executor
    blocked, second, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async with workers() as session:
        for i in (1, 2):
            target = await session.get(ExecutionTarget, f"vast:{i}")
            target.leased_job_id = f"job-{i}"
            job = await session.get(Job, f"job-{i}")
            job.status = job.queue_status = "failed"
        await session.commit()
    calls = []
    async def reconcile(session, job):
        calls.append(job.id)
        if job.id == "job-1":
            blocked.set()
            await release.wait()
        else:
            second.set()
    monkeypatch.setattr(executor, "reconcile_remote_job", reconcile)
    owner = scheduler.GPUOrchestrator(workers, lambda: [], lambda **kwargs: None)
    try:
        await owner.check_job_completions()
        await asyncio.wait_for(blocked.wait(), 2)
        await asyncio.wait_for(second.wait(), 2)
        await owner.check_job_completions()
        assert calls.count("job-1") == 1
        async with workers() as session:
            assert (await session.get(ExecutionTarget, "vast:1")).leased_job_id == "job-1"
    finally:
        release.set()
        await owner.stop()
    assert not owner._remote_reconciliation_tasks


def test_remote_device_zero_is_not_local_device_zero():
    from types import SimpleNamespace
    remote = SimpleNamespace(id="remote", execution_target_id="vast:2", queue_status="running")
    # Remote rows must be removed before local PID/GPU attribution or model limits.
    assert scheduler.collect_live_vram_by_job([remote], []) == {}
    assert scheduler.build_queue_scheduler_diagnostics([remote], [remote], [object()], {}) == {}


@pytest.mark.asyncio
async def test_claim_preserves_physical_assignment_and_rejects_endpoint_drift(workers):
    async with workers() as session:
        job = await session.get(Job, "job-2")
        target = await session.get(ExecutionTarget, "vast:2")
        identity = {key: getattr(target, key) for key in
                    ("host", "port", "username", "remote_root", "host_key_sha256")}
        bad = {**identity, "host": "replacement"}
        assert await scheduler._claim_remote_job(session, job, gpu_id=3, gpu_ids=[3],
            vram_estimate_mb=100, admission_snapshot={"target_identity": bad}) is None
        sample = {"target_identity": identity, "devices": [{"gpu_index": 3, "gpu_uuid": "GPU-two"}]}
        assert await scheduler._claim_remote_job(session, job, gpu_id=3, gpu_ids=[3],
            vram_estimate_mb=100, admission_snapshot=sample) is not None
        assignment = job.provenance["remote_execution_assignment"]
        assert assignment["root_job_id"] == "job-2" and assignment["lease_id"]
        assert assignment["execution_target_id"] == "vast:2"
        assert assignment["gpu_indices"] == [3]
        assert assignment["admission_snapshot"]["devices"][0]["gpu_index"] == 3
        assert assignment["admission_snapshot"]["devices"][0]["gpu_uuid"] == "GPU-3"


@pytest.mark.asyncio
async def test_stale_worker_does_not_hide_ready_fleet_member(workers):
    from services.remote_execution import targets
    async with workers() as session:
        first = await session.get(ExecutionTarget, "vast:1")
        first.provider_metadata = {"inventory": {"status": "unknown", "present": True}}
        await session.commit()
        listed = {row.id: row for row in await targets.list_targets(session)}
        assert set(listed) == {"vast:1", "vast:2"}
        assert not listed["vast:1"].capabilities["scheduling"]["new_work_ready"]
        assert listed["vast:2"].capabilities["scheduling"]["new_work_ready"]
        with pytest.raises(targets.ExecutionTargetError):
            await targets.get_ready_target(session, "vast:1")
        assert (await targets.get_target(session, "vast:1")).id == "vast:1"


@pytest.mark.asyncio
@pytest.mark.parametrize("shortage", [None, "cpu", "ram", "disk", "uuid"])
async def test_compiled_resources_bind_to_selected_target_only(workers, monkeypatch, shortage):
    from services.remote_execution import targets
    sample = {"available": True, "observed_at": "fixture", "cpu": {"allocated_cores": 8},
              "ram": {"limit_bytes": 1000, "used_bytes": 200},
              "disk": {"path": "/opt/biomodstack", "free_bytes": 500},
              "gpus": [{"index": 3, "uuid": "GPU-target-two"}]}
    if shortage == "cpu": sample["cpu"]["allocated_cores"] = 1
    if shortage == "ram": sample["ram"]["used_bytes"] = 950
    if shortage == "disk": sample["disk"]["free_bytes"] = 0
    if shortage == "uuid": sample["gpus"][0]["uuid"] = None
    async def telemetry(target):
        assert target.id == "vast:2"
        return sample
    monkeypatch.setattr(targets, "remote_target_telemetry", telemetry)
    async with workers() as session:
        target = await session.get(ExecutionTarget, "vast:2")
        target.remote_root = "/opt/biomodstack"
        kwargs: dict[str, Any] = dict(required_cpus=4, required_memory_bytes=100, required_scratch_bytes=200, gpu_ids=[3])
        if shortage:
            with pytest.raises(targets.ExecutionTargetError):
                await targets.admit_target_resources(target, **kwargs)
        else:
            receipt = await targets.admit_target_resources(target, **kwargs)
            assert receipt["execution_target_id"] == "vast:2"
            assert receipt["devices"] == [{"gpu_index": 3, "gpu_uuid": "GPU-target-two"}]
            assert receipt["required"] == {"cpus": 4, "memory_bytes": 100, "scratch_bytes": 200}


@pytest.mark.asyncio
@pytest.mark.parametrize("unresolved", [False, True])
async def test_selected_plan_binds_native_resources(monkeypatch, unresolved):
    from types import SimpleNamespace
    from native_components import native_resource_policy
    from services.remote_execution import targets
    components = [SimpleNamespace(component_key="cpu", resources_json=native_resource_policy({}, "CPU")),
                  SimpleNamespace(component_key="gpu", resources_json=native_resource_policy({}, "gpu"))]
    if unresolved:
        components[1].resources_json = None
    plan = SimpleNamespace(metadata=SimpleNamespace(static_components=tuple(components), dynamic_templates=()))
    def forbidden(*args, **kwargs):
        pytest.fail("pure selected-plan projection must not probe or admit")
    monkeypatch.setattr(targets, "admit_target_resources", forbidden)
    target = SimpleNamespace(id="vast:2")
    if unresolved:
        with pytest.raises(targets.ExecutionTargetError, match="gpu"):
            targets.selected_plan_target_resources(target, plan, gpu_ids=[3], scratch_bytes=200)
    else:
        result = targets.selected_plan_target_resources(target, plan, gpu_ids=[3], scratch_bytes=200)
        assert result["required"] == dict(cpus=4, memory_bytes=12 * 1024**3, scratch_bytes=200)
        assert result["gpu_ids"] == [3] and result["admission_required"]
        assert [row["component_key"] for row in result["components"]] == ["cpu", "gpu"]
        with pytest.raises(targets.ExecutionTargetError, match="GPU requirement"):
            targets.selected_plan_target_resources(target, plan, gpu_ids=[], scratch_bytes=200)


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", ["target_changed", "job_changed"])
async def test_losing_claim_does_not_expire_other_workers_or_hold_target(workers, conflict):
    async with workers() as session:
        first = await session.get(Job, "job-1")
        second = await session.get(Job, "job-2")
        second.name = "pending-unrelated-edit"
        async with workers() as writer:
            if conflict == "target_changed":
                await writer.execute(update(ExecutionTarget).where(ExecutionTarget.id == "vast:1")
                                     .values(active=False))
            else:
                await writer.execute(update(Job).where(Job.id == "job-1").values(paused=True))
            await writer.commit()
        assert await scheduler._claim_remote_job(session, first, gpu_id=None, vram_estimate_mb=0) is None
        assert not inspect(second).expired_attributes
        assert second.id == "job-2"

        assert await scheduler._claim_remote_job(session, second, gpu_id=None, vram_estimate_mb=0) == {}
    async with workers() as verify:
        first_target = await verify.get(ExecutionTarget, "vast:1")
        second_target = await verify.get(ExecutionTarget, "vast:2")
        assert first_target.leased_job_id is None
        assert second_target.leased_job_id is None
        assert (await verify.get(Job, "job-2")).provenance["remote_execution_assignment"]["policy"] == "vram_packing"
        assert (await verify.get(Job, "job-2")).name == "pending-unrelated-edit"


@pytest.mark.asyncio
async def test_checkpoint_executor_transmits_fresh_same_target_admission(workers, monkeypatch):
    from types import SimpleNamespace
    import json
    from unittest.mock import AsyncMock
    from services.remote_execution import executor, targets
    from tools import bms_remote_worker as worker
    devices = [dict(gpu_index=3, gpu_uuid='GPU-two')]
    observed = dict(schema='bms.target-resource-admission.v1', execution_target_id='vast:2', devices=devices,
        required=dict(cpus=1, memory_bytes=1, scratch_bytes=0),
        available=dict(cpus=8, memory_bytes=16*1024**3, scratch_bytes=50))
    admission = AsyncMock(return_value=observed)
    monkeypatch.setattr(targets, 'admit_target_resources', admission)
    monkeypatch.setattr(executor, '_connection_for_attempt', lambda *args: (object(), '/fixture/attempt'))
    monkeypatch.setattr(executor, '_worker_argv', lambda connection, command, directory, *args:
        [command, '--attempt-dir', directory, *args])
    async def control(connection, argv, **kwargs):
        args = worker.parser().parse_args(argv)
        assert json.loads(args.resource_admission_json) == observed
        assert args.expected_boot_id == 'boot' and args.lease_id == 'lease'
        binding = dict(operation_id=args.operation_id, attempt_id=args.attempt_id, boot_id=args.expected_boot_id,
            original_lease_id=args.lease_id, checkpoint_id=args.checkpoint_id, checkpoint_sha256=args.checkpoint_sha256,
            decision=json.loads(args.decision_json), continuation_lease_id=args.continuation_lease_id,
            resource_admission=json.loads(args.resource_admission_json))
        status = dict(attempt_id='attempt', job_id='job-2', boot_id='boot', state='awaiting_input',
            generation=0, quiescent=True)
        operation = None
        if args.command == 'checkpoint-resume':
            status.update(state='running', generation=1, continuation_lease_id=args.continuation_lease_id,
                plan_sha256='b'*64, native_output_directory='generations/review', quiescent=False)
            operation = dict(binding=binding, state='accepted', generation=1,
                edge=dict(plan_sha256='b'*64, parent_snapshot={'output_dir': 'generations/review'}))
        return SimpleNamespace(stdout=json.dumps(dict(operation=operation, worker_status=status)))
    monkeypatch.setattr(executor, 'run_remote', control)
    async with workers() as session:
        job = await session.get(Job, 'job-2')
        job.remote_attempt_id = 'attempt'
        job.status, job.queue_status, job.awaiting_input = 'awaiting_input', 'completed', True
        job.provenance = dict(remote_execution_receipt=dict(boot_id='boot', generation=0,
            source_revision=job.execution_source_revision, source_tree=job.execution_source_tree,
            execution_envelope_sha256=job.execution_bundle_sha256,
            component_context_identity=dict(root_job_id='job-2', attempt_id='attempt', target_id='vast:2', lease_id='lease')),
            remote_execution_assignment=dict(resources=dict(gpu_ids=[3], admission=dict(devices=devices),
                required=dict(cpus=999, memory_bytes=999, scratch_bytes=999))))
        await session.commit()
        checkpoint = dict(attempt_id='attempt', target_id='vast:2', lease_id='lease',
            checkpoint_id='review', checkpoint_sha256='a'*64)
        result = await executor.request_remote_checkpoint_resume(session, job, checkpoint, {'continue': True})
        assert result['state'] == 'continuing'
        assert admission.call_args.kwargs == dict(required_cpus=1, required_memory_bytes=1,
            required_scratch_bytes=0, gpu_ids=[3], minimum_gpu_memory_mb=0)


@pytest.mark.asyncio
async def test_idle_ready_worker_and_retained_results_acquire_no_lease(workers, monkeypatch):
    async with workers() as session:
        await session.execute(update(Job).values(paused=True))
        session.add(Job(id="retained-idle", name="retained-idle", model_id="cpu-only", mode="run", params={}, status="awaiting_input",
            queue_status="awaiting_input", execution_target_id="vast:1",
            awaiting_stage="remote_results", remote_state="results_available"))
        await session.commit()
    monkeypatch.setattr(scheduler, "read_scheduler_config", lambda: {"global": {"enabled": True}})
    async def launch(**kwargs):
        pytest.fail("idle worker or retained results must not launch science")
    await scheduler.GPUOrchestrator(workers, lambda: [], launch)._process_cycle()
    async with workers() as verify:
        for ordinal in (1, 2):
            target = await verify.get(ExecutionTarget, f"vast:{ordinal}")
            assert target.leased_job_id is None and target.lease_acquired_at is None
        retained = await verify.get(Job, "retained-idle")
        assert retained.remote_state == "results_available"


@pytest.mark.asyncio
@pytest.mark.parametrize("busy_first", [False, True])
async def test_cycle_routes_independent_workers_without_idle_reservations(workers, monkeypatch, busy_first):
    async with workers() as session:
        targets = [await session.get(ExecutionTarget, f"vast:{i}") for i in (1, 2)]
        assert all(target.leased_job_id is None for target in targets)
        if busy_first:
            targets[0].leased_job_id = "uncertain-predecessor"
        # Retained results do not reserve idle worker capacity or cause a pull.
        session.add(Job(id="retained", name="retained", model_id="cpu-only", mode="run", params={}, status="awaiting_input",
            queue_status="awaiting_input", execution_target_id="vast:2",
            awaiting_stage="remote_results", remote_state="results_available"))
        await session.commit()
    monkeypatch.setattr(scheduler, "read_scheduler_config", lambda: {"global": {"enabled": True}})
    launched = []
    async def launch(**kwargs):
        async with workers() as verify:
            job = await verify.get(Job, kwargs["job_id"])
            target = await verify.get(ExecutionTarget, job.execution_target_id)
            from services.remote_execution.claims import job_has_claim
            assert job_has_claim(target, job)
            assert job.queue_status == "preparing" and job.started_at is None
            assert "gpu_id" not in kwargs["params"]
            launched.append((job.id, target.id))
    await scheduler.GPUOrchestrator(workers, lambda: [], launch)._process_cycle()
    assert launched == [("job-1", "vast:1"), ("job-2", "vast:2")]
    async with workers() as verify:
        retained = await verify.get(Job, "retained")
        assert retained.remote_state == "results_available"
        if busy_first:
            first = await verify.get(Job, "job-1")
            target = await verify.get(ExecutionTarget, "vast:1")
            assert first.queue_status == "preparing"
            assert target.leased_job_id == "uncertain-predecessor"


@pytest.mark.asyncio
async def test_same_target_concurrent_cpu_claims_share_and_leave_other_worker_idle(workers):
    import asyncio
    async with workers() as session:
        await session.execute(update(Job).where(Job.id == "job-2").values(execution_target_id="vast:1"))
        await session.commit()
    async def claim(identifier):
        async with workers() as session:
            job = await session.get(Job, identifier)
            return await scheduler._claim_remote_job(session, job, gpu_id=None, vram_estimate_mb=0)
    results = await asyncio.gather(claim("job-1"), claim("job-2"))
    assert sum(result is not None for result in results) == 2
    async with workers() as session:
        assert (await session.get(ExecutionTarget, "vast:1")).leased_job_id is None
        for identifier in ("job-1", "job-2"):
            assert (await session.get(Job, identifier)).queue_status == "preparing"
        assert (await session.get(ExecutionTarget, "vast:2")).leased_job_id is None


@pytest.mark.asyncio
async def test_attachment_controller_admits_distinct_workers_but_not_duplicate_setup(workers, monkeypatch):
    import asyncio
    from services.remote_execution import targets, vast
    from services.remote_execution.contracts import ExecutionTargetActivateRequest, ExecutionTargetInventoryResponse
    release = asyncio.Event()
    async def finish(*args):
        await release.wait()
    async def inventory(_session):
        return ExecutionTargetInventoryResponse(provider="vast", available=True, credential_configured=True, message="fixture",
            instances=[vast._normalize({"id": str(i), "actual_status": "running", "ssh_host": f"203.0.113.{i}", "ssh_port": 22}) for i in (1, 2)])
    monkeypatch.setattr(targets, "finish_activation", finish)
    monkeypatch.setattr(targets, "refresh_vast_targets", inventory)
    controller = targets.AttachmentController(workers)
    try:
        async with workers() as session:
            for i in (1, 2):
                target = await session.get(ExecutionTarget, f"vast:{i}")
                target.host, target.port = f"203.0.113.{i}", 22
            await session.commit()
            for i in (1, 2):
                result = await controller.attach(session, ExecutionTargetActivateRequest(provider_instance_id=str(i)))
                assert result.state == "probing"
            assert set(controller.tasks) == {"vast:1", "vast:2"}
            with pytest.raises(targets.ExecutionTargetError, match="already in progress"):
                await controller.attach(session, ExecutionTargetActivateRequest(provider_instance_id="1"))
            for i in (1, 2):
                assert (await session.get(ExecutionTarget, f"vast:{i}")).leased_job_id is None
    finally:
        release.set()
        await asyncio.gather(*list(controller.tasks.values()))
        await controller.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("other_activity", ["idle", "lease", "preload", "nonterminal"])
async def test_attachment_is_target_scoped_and_preserves_other_active_worker(workers, monkeypatch, tmp_path, other_activity, critical_package):
    from types import SimpleNamespace
    from sqlalchemy import select
    from services.remote_execution import targets, vast
    from services.remote_execution.contracts import ExecutionTargetActivateRequest, ExecutionTargetInventoryResponse
    from services import nextflow
    async with workers() as session:
        first = await session.get(ExecutionTarget, "vast:1")
        if other_activity == "lease":
            first.leased_job_id = "uncertain-predecessor"
        if other_activity == "preload":
            first.provider_metadata = {**first.provider_metadata, "preload": {"phase": "transferring"}}
        await session.execute(update(Job).values(status="completed", queue_status="completed"))
        if other_activity == "nonterminal":
            await session.execute(update(Job).where(Job.id == "job-1").values(status="running", queue_status="running"))
        second = await session.get(ExecutionTarget, "vast:2")
        second.host, second.port, second.username = "203.0.113.2", 22, "root"
        second.active, second.state = False, "discovered"
        await session.commit()
        before = (await session.execute(select(ExecutionTarget.__table__).where(ExecutionTarget.id == "vast:1"))).one()
        jobs_before = (await session.execute(select(Job.__table__).order_by(Job.id))).all()
        instance = vast._normalize({"id": "2", "actual_status": "running", "ssh_host": second.host, "ssh_port": 22})
        async def inventory(_session):
            return ExecutionTargetInventoryResponse(provider="vast", available=True, credential_configured=True, message="fixture", instances=[instance])
        async def capture(*args): return ("fixture host key", "a" * 64)
        async def noop(*args, **kwargs): pass
        async def probe(*args): return {"ok": True}
        async def run(connection, command, **kwargs):
            if command[0] == "sh": return SimpleNamespace(stdout="BMS_ATTACHED\nBMS_TELEMETRY\n")
            if command[0] == "env": return SimpleNamespace(stdout="nextflow version 25.10.1\n")
            if command[0] == "apptainer": return SimpleNamespace(stdout="BMS_CUDA_OK\n")
            return SimpleNamespace(stdout="fixturehash worker\nfixturehash nextflow\n")
        launcher = tmp_path / "nextflow"
        launcher.write_text("fixture")
        monkeypatch.setattr(nextflow, "resolve_nextflow_executable", lambda: str(launcher))
        monkeypatch.setattr(targets, "refresh_vast_targets", inventory)
        monkeypatch.setattr(targets, "capture_host_key", capture)
        monkeypatch.setattr(targets, "persist_host_key", noop)
        monkeypatch.setattr(targets, "probe_readiness", probe)
        monkeypatch.setattr(targets, "rsync_to_remote", noop)
        monkeypatch.setattr(targets, "run_remote", run)
        monkeypatch.setattr(targets, "_sha256_file", lambda *args: "fixturehash")
        from services.remote_execution import critical_runtime, managed_inventory, cache
        manifest, artifacts, _, _ = critical_package
        async def helper(*args, **kwargs): return {"boot_id": "fixture-boot"}
        async def activate(*args, **kwargs):
            # Typed fixture for the actual qualified-release boundary. No CUDA
            # executes here; real backend qualification belongs to its own tests.
            return managed_inventory.ManagedRelease(
                selection=manifest['selection'],
                critical=dict(requirements=manifest['critical']['requirements'],
                    observed=dict(backend='apptainer', cuda='BMS_CUDA_OK',
                                  nextflow='BMS_NEXTFLOW_INTERPRETERS_OK'), compatible=True),
                release_sha256=managed_inventory.release_digest(manifest),
                source_revision=manifest['source_revision'], source_tree=manifest['source_tree'],
                state='verified', artifacts=[dict(
                    **{key: row[key] for key in ('name', 'sha256', 'size_bytes')}, state='verified')
                    for row in manifest['artifacts']])
        async def observe(*args, **kwargs):
            return SimpleNamespace(boot_id="fixture-boot", critical_runtime_ready=True,
                                   model_dump=lambda **kw: {"boot_id": "fixture-boot"})
        monkeypatch.setattr(critical_runtime, "project_runtime", lambda *args: (manifest, artifacts))
        monkeypatch.setattr(managed_inventory, "helper_call", helper)
        monkeypatch.setattr(managed_inventory, "activate_release", activate)
        monkeypatch.setattr(managed_inventory, "observe_releases", observe)
        monkeypatch.setattr(cache, "_cache_artifacts", noop)
        response = await targets.activate_target(session, ExecutionTargetActivateRequest(provider_instance_id="2"))
        assert response.active and response.state == "ready"
        assert response.capabilities['readiness']['container_backend'] == 'apptainer'
        assert response.capabilities['readiness']['container_qualification'] == 'BMS_CUDA_OK'
        assert (await session.execute(select(ExecutionTarget.__table__).where(ExecutionTarget.id == "vast:1"))).one() == before
        assert (await session.execute(select(Job.__table__).order_by(Job.id))).all() == jobs_before
        assert (await session.get(ExecutionTarget, "vast:2")).leased_job_id is None


async def gpu_competitors(workers, monkeypatch, *, count=3, capacity=24000, gpu_count=1, used=0):
    from services.remote_execution import targets
    async def telemetry(target):
        return dict(available=True, observed_at="inert", gpus=[dict(index=i, uuid=f"GPU-{i}",
            memory_total_mb=capacity, memory_used_mb=used, utilization=99) for i in range(gpu_count)])
    monkeypatch.setattr(targets, "remote_target_telemetry", telemetry)
    monkeypatch.setattr(scheduler, "read_scheduler_config", lambda: {
        "global": {"enabled": True, "target_vram_fill": .75, "vram_safety_margin_mb": 0,
                   "busy_threshold": .01, "msa_preferred_gpu_ids": [7]},
        "overrides": {"0": {"disabled": True, "target_vram_fill": .01}},
        "workflow_pins": {"cpu-only": 7}, "batch_locks": {"batch": 7}})
    monkeypatch.setitem(scheduler.GPU_CAPABILITIES, 0, {"supports_heavy": False, "supports_protenix": False})
    async with workers() as s:
        target = await s.get(ExecutionTarget, "vast:1")
        target.capabilities = {"gpu_count": gpu_count}
        for ordinal in range(1, count + 1):
            job = await s.get(Job, f"job-{ordinal}")
            if job is None:
                job = Job(id=f"job-{ordinal}", name=f"job-{ordinal}", model_id="cpu-only", mode="run",
                          status="queued", queue_status="queued", paused=False, params={}, priority=0)
                s.add(job)
            job.execution_target_id = "vast:1"
            job.vram_estimate_mb = 8000
            job.pinned_gpu = 0
        await s.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["preparing", "cancelling", "failed"])
async def test_same_gpu_fit_overflow_and_durable_reservations_across_cycles(workers, monkeypatch, state):
    from sqlalchemy import select
    from services.remote_execution.claims import outstanding_claim_clause, has_target_claims, target_idle_clause
    from services.remote_execution import executor
    await gpu_competitors(workers, monkeypatch)
    launched = []
    async def launch(**kwargs):
        launched.append(kwargs["job_id"])
    owner = scheduler.GPUOrchestrator(workers, lambda: [], launch)
    await owner._process_cycle()
    assert launched == ["job-1", "job-2"]
    async with workers() as s:
        first = await s.get(Job, "job-1")
        first.queue_status = state
        if state == "failed":
            first.status = "failed"
        await s.commit()
    await owner._process_cycle()
    assert launched == ["job-1", "job-2"]
    async with workers() as s:
        assert await has_target_claims(s, "vast:1")
        assert not (await s.execute(select(ExecutionTarget.id).where(
            ExecutionTarget.id == "vast:1", target_idle_clause()))).first()
        assert set((await s.execute(select(Job.id).where(outstanding_claim_clause()))).scalars()) == {"job-1", "job-2"}
        first = await s.get(Job, "job-1")
        sibling = await s.get(Job, "job-2")
        sibling_claim = dict(sibling.provenance)
        target = await s.get(ExecutionTarget, "vast:1")
        target.provider_metadata = {**target.provider_metadata, "job_progress": {
            "job-1": {"phase": "staging"}, "job-2": {"phase": "staging"}}}
        await s.commit()
        # Existing quiescent/prestart publication owner releases just this claim.
        assert await executor._publish_remote_transition(s, first,
            {"status": "failed", "queue_status": "failed"}, release_lease=True)
        assert first.provenance["remote_execution_assignment"]["released_at"]
        await s.refresh(sibling)
        await s.refresh(target)
        assert sibling.provenance == sibling_claim
        assert target.provider_metadata["job_progress"] == {"job-2": {"phase": "staging"}}
    await owner._process_cycle()
    assert launched == ["job-1", "job-2", "job-3"]


@pytest.mark.asyncio
async def test_shared_claim_race_revalidates_budget_under_target_write(workers, monkeypatch):
    from sqlalchemy import select
    from services.remote_execution.claims import shared_claim_clause
    await gpu_competitors(workers, monkeypatch, count=4)
    loaded = asyncio.Event()
    readers = 0
    async def claim(identifier):
        nonlocal readers
        async with workers() as s:
            job = await s.get(Job, identifier)
            readers += 1
            if readers == 4:
                loaded.set()
            await loaded.wait()
            return await scheduler._claim_remote_job(s, job, gpu_id=0, gpu_ids=[0], vram_estimate_mb=8000)
    results = await asyncio.gather(*(claim(f"job-{i}") for i in range(1, 5)))
    assert sum(value is not None for value in results) == 2
    async with workers() as s:
        owners = (await s.execute(select(Job).where(shared_claim_clause()))).scalars().all()
        assert len(owners) == 2
        assert all(job.queue_status == "preparing" for job in owners)
        assert (await s.get(ExecutionTarget, "vast:1")).leased_job_id is None


@pytest.mark.asyncio
async def test_shared_claim_keeps_legacy_attempt_receipt_epoch_and_pointer(workers, monkeypatch):
    from services.remote_execution import executor
    from services.remote_execution.claims import job_has_claim
    await gpu_competitors(workers, monkeypatch, count=2, capacity=32000)
    epoch = datetime(2020, 1, 1)
    legacy_receipt = {"lease_acquired_at": epoch.isoformat(), "attempt_id": "retained-attempt"}
    async with workers() as s:
        target = await s.get(ExecutionTarget, "vast:1")
        target.leased_job_id, target.lease_acquired_at = "job-1", epoch
        legacy = await s.get(Job, "job-1")
        legacy.status = legacy.queue_status = "running"
        legacy.assigned_gpu = 0
        legacy.provenance = {"remote_execution_receipt": legacy_receipt}
        legacy.remote_attempt_id = "retained-attempt"
        await s.commit()
        current = await s.get(Job, "job-2")
        assert await scheduler._claim_remote_job(s, current, gpu_id=0, gpu_ids=[0], vram_estimate_mb=8000)
        assert job_has_claim(target, current) and job_has_claim(target, legacy)
        current.status = current.queue_status = "running"
        await s.commit()
        assert await executor._acquire_remote_terminal_fence(s, current)
        assert current.remote_state == "validating_return"
        assert await executor._publish_remote_transition(s, current,
            {"status": "failed", "queue_status": "failed"}, release_lease=True)
        await s.refresh(target)
        await s.refresh(legacy)
        assert target.leased_job_id == "job-1" and target.lease_acquired_at == epoch
        assert legacy.provenance == {"remote_execution_receipt": legacy_receipt}
        assert legacy.remote_attempt_id == "retained-attempt"
        assert await executor._publish_remote_transition(s, legacy,
            {"status": "completed", "queue_status": "completed"}, release_lease=True)
        await s.refresh(target)
        assert target.leased_job_id is None


@pytest.mark.asyncio
async def test_remote_requested_gpu_set_reserves_every_device(workers, monkeypatch):
    await gpu_competitors(workers, monkeypatch, gpu_count=2, capacity=16000)
    async with workers() as s:
        first = await s.get(Job, "job-1")
        assert await scheduler._claim_remote_job(s, first, gpu_id=0, gpu_ids=[0, 1], vram_estimate_mb=8000)
        assert first.provenance["remote_execution_assignment"]["gpu_indices"] == [0, 1]
        second = await s.get(Job, "job-2")
        for index in (0, 1):
            assert await scheduler._claim_remote_job(s, second, gpu_id=index, gpu_ids=[index], vram_estimate_mb=8000) is None


@pytest.mark.asyncio
async def test_aggregate_usage_is_not_attributed_to_each_shared_claim(workers, monkeypatch):
    await gpu_competitors(workers, monkeypatch, capacity=24000, used=4000)
    async with workers() as s:
        first = await s.get(Job, "job-1")
        second = await s.get(Job, "job-2")
        assert await scheduler._claim_remote_job(s, first, gpu_id=0, vram_estimate_mb=8000)
        # 18GB admissible - 4GB aggregate - 8GB claimed leaves only 6GB.
        assert await scheduler._claim_remote_job(s, second, gpu_id=0, vram_estimate_mb=8000) is None


@pytest.mark.asyncio
async def test_same_target_slow_reconciliation_does_not_stall_sibling(workers, monkeypatch):
    from services.remote_execution import executor
    await gpu_competitors(workers, monkeypatch, count=2)
    async with workers() as s:
        for i in (1, 2):
            job = await s.get(Job, f"job-{i}")
            assert await scheduler._claim_remote_job(s, job, gpu_id=0, vram_estimate_mb=8000)
            job.status = job.queue_status = "failed"
            await s.commit()
    blocked, other, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def reconcile(s, job):
        if job.id == "job-1":
            blocked.set()
            await release.wait()
        else:
            other.set()
    monkeypatch.setattr(executor, "reconcile_remote_job", reconcile)
    poller = scheduler.GPUOrchestrator(workers, lambda: [], lambda **kwargs: None)
    try:
        await poller.check_job_completions()
        await asyncio.wait_for(blocked.wait(), 2)
        await asyncio.wait_for(other.wait(), 2)
    finally:
        release.set()
        await poller.stop()


@pytest.mark.asyncio
async def test_continuation_reacquires_through_same_vram_owner(workers, monkeypatch):
    from services.remote_execution import executor
    await gpu_competitors(workers, monkeypatch, count=3, capacity=16000)
    resources = dict(gpu_ids=[0], required=dict(cpus=1, memory_bytes=1, scratch_bytes=0))
    async with workers() as s:
        first = await s.get(Job, "job-1")
        sibling = await s.get(Job, "job-2")
        assert await scheduler._claim_remote_job(s, first, gpu_id=0, vram_estimate_mb=8000)
        first.provenance = {**first.provenance, "remote_execution_assignment": {
            **first.provenance["remote_execution_assignment"], "resources": resources}}
        await s.commit()
        prior_lease = first.provenance["remote_execution_assignment"]["lease_id"]
        assert await executor._publish_remote_transition(s, first,
            {"status": "awaiting_input", "queue_status": "completed"}, release_lease=True)
        assert await scheduler._claim_remote_job(s, sibling, gpu_id=0, vram_estimate_mb=8000)
        with pytest.raises(executor.RemoteExecutionError, match="insufficient VRAM"):
            await executor._reacquire_remote_claim(s, first, await s.get(ExecutionTarget, "vast:1"), resources)
    async with workers() as s:
        sibling = await s.get(Job, "job-2")
        assert await executor._publish_remote_transition(s, sibling,
            {"status": "failed", "queue_status": "failed"}, release_lease=True)
        first = await s.get(Job, "job-1")
        assignment = await executor._reacquire_remote_claim(s, first,
            await s.get(ExecutionTarget, "vast:1"), resources)
        assert assignment["lease_id"] != prior_lease
        assert assignment["resources"] == resources and not assignment.get("released_at")
        assert await executor._publish_remote_transition(s, first, {
            "provenance": {**first.provenance, "remote_execution_assignment": assignment},
            "status": "running", "queue_status": "running"}, require_lease=False)
        third = await s.get(Job, "job-3")
        assert await scheduler._claim_remote_job(s, third, gpu_id=0, vram_estimate_mb=8000) is None


@pytest.mark.asyncio
async def test_stale_shared_lease_cannot_publish_or_release_successor(workers, monkeypatch):
    from services.remote_execution import executor
    await gpu_competitors(workers, monkeypatch, count=2)
    async with workers() as stale:
        old = await stale.get(Job, "job-1")
        assert await scheduler._claim_remote_job(stale, old, gpu_id=0, vram_estimate_mb=8000)
        async with workers() as writer:
            current = await writer.get(Job, "job-1")
            current.provenance = {**current.provenance, "remote_execution_assignment": {
                **current.provenance["remote_execution_assignment"], "lease_id": "successor"}}
            await writer.commit()
        await executor._release_remote_target_lease(stale, old)
        await stale.commit()
        assert not await executor._publish_remote_transition(stale, old,
            {"status": "failed", "queue_status": "failed"}, release_lease=True)
    async with workers() as s:
        current = await s.get(Job, "job-1")
        assignment = current.provenance["remote_execution_assignment"]
        assert assignment["lease_id"] == "successor" and not assignment.get("released_at")
        assert current.queue_status == "preparing"


def test_packer_local_allowlist_and_target_capabilities_are_distinct():
    job = scheduler.JobInfo(id="heavy", name="heavy", model_type="protenix", vram_estimate_mb=1000,
        sequence_length=100, priority=1, pinned_gpu=None, pinned_gpus=[0, 1], created_at=datetime.utcnow())
    gpus = [scheduler.GPUState(index=i, name="inert", memory_used_mb=0, memory_total_mb=24000,
        memory_free_mb=24000, utilization=0, temperature=0) for i in (0, 1)]
    config = {"global": {"target_vram_fill": .75, "vram_safety_margin_mb": 0}}
    packed = scheduler.pack_jobs_to_gpus([job], gpus, .75, config,
        gpu_capabilities={0: {"supports_protenix": False}, 1: {"supports_protenix": True}})
    assert len(packed) == 1 and packed[0][1] == 1
    assert job.pinned_gpus == [0, 1]  # Local pin list remains an allowlist, not a set reservation.
