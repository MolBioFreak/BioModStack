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
async def workers(tmp_path):
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
                active=True, state="ready", provider_metadata={"inventory": {
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
        assert assignment["admission_snapshot"]["devices"] == sample["devices"]


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
@pytest.mark.parametrize("conflict", ["lease", "job_changed"])
async def test_losing_claim_does_not_expire_other_workers_or_hold_target(workers, conflict):
    async with workers() as session:
        first = await session.get(Job, "job-1")
        second = await session.get(Job, "job-2")
        second.name = "pending-unrelated-edit"
        async with workers() as writer:
            if conflict == "lease":
                await writer.execute(update(ExecutionTarget).where(ExecutionTarget.id == "vast:1")
                                     .values(leased_job_id="predecessor"))
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
        assert first_target.leased_job_id == ("predecessor" if conflict == "lease" else None)
        assert second_target.leased_job_id == "job-2"
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
            assert target.leased_job_id == job.id
            assert job.queue_status == "preparing" and job.started_at is None
            assert "gpu_id" not in kwargs["params"]
            launched.append((job.id, target.id))
    await scheduler.GPUOrchestrator(workers, lambda: [], launch)._process_cycle()
    assert launched == ([("job-2", "vast:2")] if busy_first else
                        [("job-1", "vast:1"), ("job-2", "vast:2")])
    async with workers() as verify:
        retained = await verify.get(Job, "retained")
        assert retained.remote_state == "results_available"
        if busy_first:
            first = await verify.get(Job, "job-1")
            target = await verify.get(ExecutionTarget, "vast:1")
            assert first.queue_status == "queued"
            assert target.leased_job_id == "uncertain-predecessor"


@pytest.mark.asyncio
async def test_same_target_concurrent_claims_have_one_winner_and_leave_other_worker_idle(workers):
    import asyncio
    async with workers() as session:
        await session.execute(update(Job).where(Job.id == "job-2").values(execution_target_id="vast:1"))
        await session.commit()
    async def claim(identifier):
        async with workers() as session:
            job = await session.get(Job, identifier)
            return await scheduler._claim_remote_job(session, job, gpu_id=None, vram_estimate_mb=0)
    results = await asyncio.gather(claim("job-1"), claim("job-2"))
    assert sum(result is not None for result in results) == 1
    async with workers() as session:
        owner = (await session.get(ExecutionTarget, "vast:1")).leased_job_id
        assert owner in {"job-1", "job-2"}
        assert (await session.get(Job, owner)).queue_status == "preparing"
        other = "job-2" if owner == "job-1" else "job-1"
        assert (await session.get(Job, other)).queue_status == "queued"
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
            return SimpleNamespace(model_dump=lambda **kw: {"state": "ready"})
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
        assert (await session.execute(select(ExecutionTarget.__table__).where(ExecutionTarget.id == "vast:1"))).one() == before
        assert (await session.execute(select(Job.__table__).order_by(Job.id))).all() == jobs_before
        assert (await session.get(ExecutionTarget, "vast:2")).leased_job_id is None
