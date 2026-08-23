from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
import inspect
from types import SimpleNamespace
import sys
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
for root in (API_ROOT, REPO_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from routers import jobs, workflow_adapter
from database import Base, Job
from services import gpu_orchestrator, ngs_molbio_n5, resource_usage_evidence as resource_evidence
from services.execution_ownership import (
    attach_scheduler_gpu_assignment,
    cancellation_intent_requested,
    latest_execution_attempt,
    planned_execution_attempt,
    update_execution_attempt,
)
from services.global_experiments import launch_contexts
import workflow_job_runner


def _handoff(*, gpu_index: int | None = None, gpu_uuid: str | None = None) -> dict[str, object]:
    return resource_evidence.build_resource_admission_handoff(
        admission_id="admission-1",
        run_attempt_id="attempt-1",
        canonical_job_id="job-1",
        preparation_id="preparation-1",
        cpu_threads=1,
        dram_bytes=1024**3,
        gpu_index=gpu_index,
        gpu_uuid=gpu_uuid,
        policy_source="project-scheduler",
        policy_version="bms.resource-admission-policy.v1",
        owner="local-application-operator",
        lease_token="lease-1",
        source_revision="1" * 40,
        source_tree="2" * 40,
    )


def _prepared_dispatch(handoff: dict[str, object]) -> dict[str, object]:
    return resource_evidence.build_dispatch_materialization_authority(
        payload_sha256="3" * 64,
        handoff=handoff,
    )


def test_scheduler_materializes_gpu_dispatch_for_unpinned_admission() -> None:
    handoff = _handoff()
    prepared = _prepared_dispatch(handoff)

    assigned = resource_evidence.materialize_scheduler_dispatch_authority(
        prepared,
        handoff=handoff,
        gpu_index=2,
        gpu_uuid="GPU-2222",
    )

    assert assigned["schema"] == "bms.global-dispatch-materialization.v2"
    assert assigned["gpu_index"] == 2
    assert assigned["gpu_uuid"] == "GPU-2222"
    assert assigned["payload_sha256"] == prepared["payload_sha256"]
    assert assigned["admission_handoff_sha256"] == handoff["handoff_sha256"]
    assert resource_evidence.validate_dispatch_materialization_authority(
        assigned,
        expected_handoff=handoff,
    ) == assigned


def test_scheduler_dispatch_must_match_explicit_admission_gpu_constraint() -> None:
    handoff = _handoff(gpu_index=1, gpu_uuid="GPU-1111")
    prepared = _prepared_dispatch(handoff)

    with pytest.raises(
        resource_evidence.ResourceUsageEvidenceError,
        match="scheduler GPU assignment differs from admitted GPU constraint",
    ):
        resource_evidence.materialize_scheduler_dispatch_authority(
            prepared,
            handoff=handoff,
            gpu_index=2,
            gpu_uuid="GPU-2222",
        )


def test_scheduler_resolves_live_gpu_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "routers.gpu._query_smi_gpu_map",
        lambda: {2: {"uuid": "GPU-2222"}},
    )

    assert gpu_orchestrator._scheduler_gpu_uuid(2) == "GPU-2222"  # noqa: SLF001


@pytest.mark.parametrize(
    "job",
    [
        SimpleNamespace(queue_status="cancelling", params={}),
        SimpleNamespace(
            queue_status="running",
            params={
                "cancellation_receipt": {
                    "schema": "bms.workflow-cancellation.v1",
                    "state": "requested",
                }
            },
        ),
    ],
)
def test_cancellation_intent_fences_execution(job: SimpleNamespace) -> None:
    assert cancellation_intent_requested(job) is True


@pytest.mark.asyncio
async def test_runner_refuses_started_publication_after_cancellation_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = SimpleNamespace(
        id="job-1",
        queue_status="cancelling",
        params={
            "cancellation_receipt": {
                "schema": "bms.workflow-cancellation.v1",
                "state": "requested",
            }
        },
    )

    class Session:
        calls = 0

        async def execute(self, _statement: object) -> SimpleNamespace:
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace()
            return SimpleNamespace(scalar_one_or_none=lambda: job)

    @asynccontextmanager
    async def fake_session():
        yield Session()

    monkeypatch.setattr(workflow_job_runner.database, "async_session", fake_session)
    monkeypatch.setattr(
        workflow_job_runner.database,
        "launch_context_binding_ready",
        lambda _job: True,
    )

    with pytest.raises(
        workflow_job_runner.ExecutionOwnershipError,
        match="cancellation intent",
    ):
        await workflow_job_runner._load_authoritative_attempt(  # noqa: SLF001
            job_id="job-1",
            lane="development",
            unit_name="biomodstack-development-job-job-1-attempt-1.service",
            owner_nonce="nonce",
            invocation_id="invocation",
        )


def test_consumed_context_replay_accepts_assigned_dispatch_extension() -> None:
    handoff = _handoff()
    prepared = _prepared_dispatch(handoff)
    assigned = resource_evidence.materialize_scheduler_dispatch_authority(
        prepared,
        handoff=handoff,
        gpu_index=2,
        gpu_uuid="GPU-2222",
    )
    base = attach_scheduler_gpu_assignment({"sequence": "ACDE"}, 2)
    expected = resource_evidence.attach_dispatch_materialization_authority(
        resource_evidence.attach_resource_admission_handoff(base, handoff),
        prepared,
    )
    actual = resource_evidence.attach_dispatch_materialization_authority(
        resource_evidence.attach_resource_admission_handoff(base, handoff),
        assigned,
    )

    assert launch_contexts._resource_authority_matches_reserved(  # noqa: SLF001
        expected,
        actual,
    )


@pytest.mark.asyncio
async def test_scheduler_claim_persists_assigned_dispatch_gpu_authority(tmp_path: Path) -> None:
    handoff = _handoff()
    prepared = _prepared_dispatch(handoff)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'dispatch-claim.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        job = Job(
            id="job-1",
            name="governed-auto-gpu",
            status="queued",
            queue_status="queued",
            model_id="esmfold2",
            mode="predict",
            params={
                resource_evidence.GLOBAL_RESOURCE_ADMISSION_PARAM: handoff,
                resource_evidence.GLOBAL_DISPATCH_AUTHORITY_PARAM: prepared,
            },
            output_dir=str(tmp_path / "output"),
        )
        session.add(job)
        await session.commit()

        claimed = await gpu_orchestrator._claim_job_for_gpu(  # noqa: SLF001
            session,
            job,
            2,
            4096,
            gpu_uuid="GPU-2222",
        )
        assert claimed is not None
        await session.commit()

    async with factory() as session:
        persisted = await session.get(Job, "job-1")
        assert persisted is not None
        dispatch = persisted.params[resource_evidence.GLOBAL_DISPATCH_AUTHORITY_PARAM]
        assert dispatch["schema"] == "bms.global-dispatch-materialization.v2"
        assert dispatch["gpu_index"] == 2
        assert dispatch["gpu_uuid"] == "GPU-2222"
        assert persisted.assigned_gpu == 2
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent_change", ["params", "pin"])
async def test_scheduler_claim_loses_to_concurrent_authority_writer(
    tmp_path: Path,
    concurrent_change: str,
) -> None:
    handoff = _handoff()
    prepared = _prepared_dispatch(handoff)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / f'dispatch-claim-{concurrent_change}.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as seed:
        seed.add(
            Job(
                id="job-1",
                name="governed-auto-gpu",
                status="queued",
                queue_status="queued",
                model_id="esmfold2",
                mode="predict",
                params={
                    resource_evidence.GLOBAL_RESOURCE_ADMISSION_PARAM: handoff,
                    resource_evidence.GLOBAL_DISPATCH_AUTHORITY_PARAM: prepared,
                },
                output_dir=str(tmp_path / "output"),
            )
        )
        await seed.commit()

    async with factory() as stale_session, factory() as writer_session:
        stale_job = await stale_session.get(Job, "job-1")
        writer_job = await writer_session.get(Job, "job-1")
        assert stale_job is not None and writer_job is not None
        if concurrent_change == "params":
            writer_job.params = {**writer_job.params, "concurrent_authority": "preserve"}
        else:
            writer_job.pinned_gpu = 1
        await writer_session.commit()

        claimed = await gpu_orchestrator._claim_job_for_gpu(  # noqa: SLF001
            stale_session,
            stale_job,
            2,
            4096,
            gpu_uuid="GPU-2222",
        )
        assert claimed is None

    async with factory() as verify:
        persisted = await verify.get(Job, "job-1")
        assert persisted is not None
        assert persisted.status == "queued"
        assert persisted.queue_status == "queued"
        assert persisted.assigned_gpu is None
        if concurrent_change == "params":
            assert persisted.params["concurrent_authority"] == "preserve"
        else:
            assert persisted.pinned_gpu == 1
    await engine.dispose()


def test_adapter_validates_scheduler_dispatch_gpu_for_unpinned_admission() -> None:
    handoff = _handoff()
    assigned = resource_evidence.materialize_scheduler_dispatch_authority(
        _prepared_dispatch(handoff),
        handoff=handoff,
        gpu_index=2,
        gpu_uuid="GPU-2222",
    )
    job = SimpleNamespace(id="job-1", pinned_gpu=None, assigned_gpu=2)

    workflow_adapter._validate_resource_gpu_authority(  # noqa: SLF001
        job,
        handoff,
        assigned,
        inventory={2: {"uuid": "GPU-2222"}},
    )


def test_runner_receipt_exposes_scheduler_dispatch_gpu_identity() -> None:
    handoff = _handoff()
    assigned = resource_evidence.materialize_scheduler_dispatch_authority(
        _prepared_dispatch(handoff),
        handoff=handoff,
        gpu_index=2,
        gpu_uuid="GPU-2222",
    )
    monitor = resource_evidence.WorkflowResourceMonitor(
        job_id="job-1",
        lane="development",
        generation=1,
        attempt=1,
        unit_name="unit.service",
        owner_nonce="owner",
        expected_invocation_id="invocation",
        handoff=handoff,
        dispatch_authority=assigned,
        gpu_index=2,
        gpu_uuid="GPU-2222",
    )

    receipt = monitor.finish(outcome="failed")

    assert receipt["schema"] == "bms.workflow-resource-usage.v2"
    assert receipt["dispatch"] == {"gpu_index": 2, "gpu_uuid": "GPU-2222"}


def test_pre_spawn_failure_receipt_is_terminal_zero_use_evidence() -> None:
    handoff = _handoff()
    prepared = _prepared_dispatch(handoff)
    job = SimpleNamespace(
        id="job-1",
        status="failed",
        params={
            resource_evidence.GLOBAL_RESOURCE_ADMISSION_PARAM: handoff,
            resource_evidence.GLOBAL_DISPATCH_AUTHORITY_PARAM: prepared,
        },
        error_message="adapter rejected before unit creation",
    )

    job.params = resource_evidence.attach_pre_spawn_nonexecution_receipt(
        job,
        finished_at="2026-08-23T19:21:43Z",
    )
    receipt = resource_evidence.validate_producer_resource_usage_receipt(job, handoff)

    assert receipt["schema"] == "bms.workflow-resource-nonexecution.v1"
    assert receipt["outcome"] == "launch_rejected_before_spawn"
    assert receipt["cpu_usage_usec"] == 0
    assert receipt["memory_peak_bytes"] == 0
    assert receipt["gpu_peak_by_uuid"] == {}
    assert receipt["complete"] is True


def test_terminal_planning_receipt_drives_digest_bound_zero_use_evidence() -> None:
    handoff = _handoff()
    assigned = resource_evidence.materialize_scheduler_dispatch_authority(
        _prepared_dispatch(handoff),
        handoff=handoff,
        gpu_index=2,
        gpu_uuid="GPU-2222",
    )
    planned = planned_execution_attempt(
        lane="development",
        job_id="job-1",
        generation=1,
        attempt=1,
        unit="biomodstack-development-job-job-1-attempt-1.service",
        owner_nonce="nonce",
        request_fingerprint_value="f" * 64,
    )
    params = update_execution_attempt(
        {"execution_attempts": [planned]},
        lane="development",
        generation=1,
        attempt=1,
        unit="biomodstack-development-job-job-1-attempt-1.service",
        owner_nonce="nonce",
        changes={
            "state": "launch_rejected_before_spawn",
            "terminal_at": "2026-08-23T19:21:43Z",
            "terminal_reason": "systemd-run rejected the unit",
            "unit_absence": {
                "state": "not-found",
                "source": "systemd",
                "verified_at": "2026-08-23T19:21:43Z",
            },
        },
    )
    params[resource_evidence.GLOBAL_RESOURCE_ADMISSION_PARAM] = handoff
    params[resource_evidence.GLOBAL_DISPATCH_AUTHORITY_PARAM] = assigned
    terminal_planning = latest_execution_attempt(params)
    assert terminal_planning is not None
    job = SimpleNamespace(
        id="job-1",
        status="failed",
        nextflow_run_id=None,
        params=params,
        error_message="systemd-run rejected the unit",
    )

    job.params = resource_evidence.attach_pre_spawn_nonexecution_receipt(
        job,
        finished_at="2026-08-23T19:21:43Z",
        planning_receipt=terminal_planning,
    )
    receipt = resource_evidence.validate_producer_resource_usage_receipt(job, handoff)

    assert receipt["schema"] == "bms.workflow-resource-nonexecution.v2"
    assert receipt["producer"] == "bms.workflow_adapter"
    assert len(receipt["planning_receipt_sha256"]) == 64
    assert receipt["complete"] is True


@pytest.mark.asyncio
async def test_adapter_unit_creation_rejection_publishes_typed_zero_use_before_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handoff = _handoff()
    assigned = resource_evidence.materialize_scheduler_dispatch_authority(
        _prepared_dispatch(handoff),
        handoff=handoff,
        gpu_index=2,
        gpu_uuid="GPU-2222",
    )
    unit_name = "biomodstack-development-job-job-1-attempt-1.service"
    planned = planned_execution_attempt(
        lane="development",
        job_id="job-1",
        generation=1,
        attempt=1,
        unit=unit_name,
        owner_nonce="nonce",
        request_fingerprint_value="f" * 64,
    )
    job = SimpleNamespace(
        id="job-1",
        status="running",
        queue_status="running",
        params=attach_scheduler_gpu_assignment(
            {
                resource_evidence.GLOBAL_RESOURCE_ADMISSION_PARAM: handoff,
                resource_evidence.GLOBAL_DISPATCH_AUTHORITY_PARAM: assigned,
                "execution_attempts": [planned],
            },
            2,
        ),
        assigned_gpu=2,
        nextflow_run_id=unit_name,
        error_message=None,
        completed_at=None,
    )

    class Session:
        calls = 0
        committed = False

        async def execute(self, _statement: object) -> SimpleNamespace:
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace()
            return SimpleNamespace(scalar_one_or_none=lambda: job)

        async def rollback(self) -> None:
            raise AssertionError("exact no-unit rejection must not roll back")

        async def commit(self) -> None:
            self.committed = True

    session = Session()

    @asynccontextmanager
    async def fake_session():
        yield session

    def absent_unit(_unit: str, _lane: str) -> None:
        raise workflow_adapter.UnitNotFoundError("unit is absent")

    monkeypatch.setattr(workflow_adapter.database, "async_session", fake_session)
    monkeypatch.setattr(workflow_adapter, "show_unit_properties", absent_unit)

    published = await workflow_adapter._publish_planned_unit_creation_rejection(  # noqa: SLF001
        job_id="job-1",
        lane="development",
        generation=1,
        attempt=1,
        unit_name=unit_name,
        owner_nonce="nonce",
        reason="systemd-run exited with 1",
    )

    assert published is True
    assert session.committed is True
    assert job.status == "failed"
    assert job.assigned_gpu is None
    assert job.nextflow_run_id is None
    receipt = resource_evidence.validate_producer_resource_usage_receipt(job, handoff)
    assert receipt["schema"] == "bms.workflow-resource-nonexecution.v2"


def test_pre_spawn_failure_refuses_any_execution_attempt_authority() -> None:
    handoff = _handoff()
    assigned = resource_evidence.materialize_scheduler_dispatch_authority(
        _prepared_dispatch(handoff),
        handoff=handoff,
        gpu_index=2,
        gpu_uuid="GPU-2222",
    )
    job = SimpleNamespace(
        id="job-1",
        status="failed",
        params={
            resource_evidence.GLOBAL_RESOURCE_ADMISSION_PARAM: handoff,
            resource_evidence.GLOBAL_DISPATCH_AUTHORITY_PARAM: assigned,
            "execution_attempts": [{"state": "planned", "invocation_id": None}],
        },
        error_message="adapter response failed after planning",
    )

    with pytest.raises(
        resource_evidence.ResourceUsageEvidenceError,
        match="execution authority",
    ):
        resource_evidence.attach_pre_spawn_nonexecution_receipt(
            job,
            finished_at="2026-08-23T19:21:43Z",
        )


def test_pre_spawn_failure_refuses_external_owner_identity() -> None:
    handoff = _handoff()
    assigned = resource_evidence.materialize_scheduler_dispatch_authority(
        _prepared_dispatch(handoff),
        handoff=handoff,
        gpu_index=2,
        gpu_uuid="GPU-2222",
    )
    job = SimpleNamespace(
        id="job-1",
        status="failed",
        nextflow_run_id="bms-workflow-development-job-1-1.service",
        params={
            resource_evidence.GLOBAL_RESOURCE_ADMISSION_PARAM: handoff,
            resource_evidence.GLOBAL_DISPATCH_AUTHORITY_PARAM: assigned,
        },
        error_message="adapter response failed after owner publication",
    )

    with pytest.raises(
        resource_evidence.ResourceUsageEvidenceError,
        match="external owner",
    ):
        resource_evidence.attach_pre_spawn_nonexecution_receipt(
            job,
            finished_at="2026-08-23T19:21:43Z",
        )


def test_scheduler_launch_failure_uses_existing_terminal_timestamp() -> None:
    handoff = _handoff()
    assigned = resource_evidence.materialize_scheduler_dispatch_authority(
        _prepared_dispatch(handoff),
        handoff=handoff,
        gpu_index=2,
        gpu_uuid="GPU-2222",
    )
    job = SimpleNamespace(
        id="job-1",
        status="cancelled",
        queue_status="cancelled",
        completed_at="2026-08-23T19:20:00Z",
        error_message="operator cancellation",
        nextflow_run_id=None,
        assigned_gpu=2,
        params=attach_scheduler_gpu_assignment(
            {
                resource_evidence.GLOBAL_RESOURCE_ADMISSION_PARAM: handoff,
                resource_evidence.GLOBAL_DISPATCH_AUTHORITY_PARAM: assigned,
            },
            2,
        ),
    )

    published = gpu_orchestrator._publish_pre_spawn_launch_failure(  # noqa: SLF001
        job,
        error=RuntimeError("late adapter rejection"),
        completed_at="2026-08-23T19:21:43Z",
    )

    assert published is True
    receipt = resource_evidence.validate_producer_resource_usage_receipt(job, handoff)
    assert receipt["finished_at"] == "2026-08-23T19:20:00Z"


def test_scheduler_launch_failure_with_external_owner_preserves_claim() -> None:
    handoff = _handoff()
    assigned = resource_evidence.materialize_scheduler_dispatch_authority(
        _prepared_dispatch(handoff),
        handoff=handoff,
        gpu_index=2,
        gpu_uuid="GPU-2222",
    )
    params = attach_scheduler_gpu_assignment(
        {
            resource_evidence.GLOBAL_RESOURCE_ADMISSION_PARAM: handoff,
            resource_evidence.GLOBAL_DISPATCH_AUTHORITY_PARAM: assigned,
        },
        2,
    )
    job = SimpleNamespace(
        id="job-1",
        status="running",
        queue_status="running",
        completed_at=None,
        error_message=None,
        nextflow_run_id="bms-workflow-development-job-1-1.service",
        assigned_gpu=2,
        params=params,
    )

    published = gpu_orchestrator._publish_pre_spawn_launch_failure(  # noqa: SLF001
        job,
        error=RuntimeError("adapter response failed after owner publication"),
        completed_at="2026-08-23T19:21:43Z",
    )

    assert published is False
    assert job.status == "running"
    assert job.queue_status == "running"
    assert job.assigned_gpu == 2
    assert job.params == params


def test_scheduler_launch_failure_with_cancellation_intent_preserves_claim() -> None:
    handoff = _handoff()
    assigned = resource_evidence.materialize_scheduler_dispatch_authority(
        _prepared_dispatch(handoff),
        handoff=handoff,
        gpu_index=2,
        gpu_uuid="GPU-2222",
    )
    params = attach_scheduler_gpu_assignment(
        {
            resource_evidence.GLOBAL_RESOURCE_ADMISSION_PARAM: handoff,
            resource_evidence.GLOBAL_DISPATCH_AUTHORITY_PARAM: assigned,
            "cancellation_receipt": {
                "schema": "bms.job-cancellation-receipt.v1",
                "state": "requested",
            },
        },
        2,
    )
    job = SimpleNamespace(
        id="job-1",
        status="running",
        queue_status="cancelling",
        completed_at=None,
        error_message=None,
        nextflow_run_id=None,
        assigned_gpu=2,
        params=params,
    )

    published = gpu_orchestrator._publish_pre_spawn_launch_failure(  # noqa: SLF001
        job,
        error=RuntimeError("adapter response failed during cancellation"),
        completed_at="2026-08-23T19:21:43Z",
    )

    assert published is False
    assert job.status == "running"
    assert job.queue_status == "cancelling"
    assert job.assigned_gpu == 2
    assert job.params == params


def test_adapter_serializes_cancellation_check_through_systemd_creation() -> None:
    source = inspect.getsource(workflow_adapter.workflow_adapter_launch)
    planned_commit = source.index("# Commit the durable planned receipt")
    locked_transaction = source.index('sqlalchemy.text("BEGIN IMMEDIATE")', planned_commit)
    cancellation_check = source.index("cancellation_intent_requested(job)", locked_transaction)
    unit_creation = source.index("create_systemd_workflow_unit", cancellation_check)
    started_commit = source.index("await session.commit()", unit_creation)

    assert planned_commit < locked_transaction
    assert locked_transaction < cancellation_check < unit_creation < started_commit


def test_scheduler_launch_failure_with_planned_attempt_preserves_claim() -> None:
    handoff = _handoff()
    assigned = resource_evidence.materialize_scheduler_dispatch_authority(
        _prepared_dispatch(handoff),
        handoff=handoff,
        gpu_index=2,
        gpu_uuid="GPU-2222",
    )
    params = attach_scheduler_gpu_assignment(
        {
            resource_evidence.GLOBAL_RESOURCE_ADMISSION_PARAM: handoff,
            resource_evidence.GLOBAL_DISPATCH_AUTHORITY_PARAM: assigned,
            "execution_attempts": [{"state": "planned", "invocation_id": None}],
        },
        2,
    )
    job = SimpleNamespace(
        id="job-1",
        status="running",
        queue_status="running",
        completed_at=None,
        error_message=None,
        assigned_gpu=2,
        params=params,
    )

    published = gpu_orchestrator._publish_pre_spawn_launch_failure(  # noqa: SLF001
        job,
        error=RuntimeError("adapter response failed after planning"),
        completed_at="2026-08-23T19:21:43Z",
    )

    assert published is False
    assert job.status == "running"
    assert job.queue_status == "running"
    assert job.assigned_gpu == 2
    assert job.params == params


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent_change", ["attempt", "owner"])
async def test_pre_spawn_failure_publication_loses_to_concurrent_execution_authority(
    tmp_path: Path,
    concurrent_change: str,
) -> None:
    handoff = _handoff()
    assigned = resource_evidence.materialize_scheduler_dispatch_authority(
        _prepared_dispatch(handoff),
        handoff=handoff,
        gpu_index=2,
        gpu_uuid="GPU-2222",
    )
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / f'pre-spawn-{concurrent_change}.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    params = attach_scheduler_gpu_assignment(
        {
            resource_evidence.GLOBAL_RESOURCE_ADMISSION_PARAM: handoff,
            resource_evidence.GLOBAL_DISPATCH_AUTHORITY_PARAM: assigned,
        },
        2,
    )
    async with factory() as seed:
        seed.add(
            Job(
                id="job-1",
                name="governed-auto-gpu",
                status="running",
                queue_status="running",
                model_id="esmfold2",
                mode="predict",
                assigned_gpu=2,
                params=params,
                output_dir=str(tmp_path / "output"),
            )
        )
        await seed.commit()

    async with factory() as stale_session, factory() as writer_session:
        stale_job = await stale_session.get(Job, "job-1")
        writer_job = await writer_session.get(Job, "job-1")
        assert stale_job is not None and writer_job is not None
        if concurrent_change == "attempt":
            writer_job.params = {
                **writer_job.params,
                "execution_attempts": [{"state": "planned", "invocation_id": "invocation-1"}],
            }
        else:
            writer_job.nextflow_run_id = "bms-workflow-development-job-1-1.service"
        await writer_session.commit()

        published = await gpu_orchestrator._persist_pre_spawn_launch_failure(  # noqa: SLF001
            stale_session,
            stale_job,
            error=RuntimeError("adapter response failed"),
            completed_at=datetime(2026, 8, 23, 19, 21, 43),
        )
        assert published is None

    async with factory() as verify:
        persisted = await verify.get(Job, "job-1")
        assert persisted is not None
        assert persisted.status == "running"
        assert persisted.queue_status == "running"
        assert persisted.assigned_gpu == 2
        if concurrent_change == "attempt":
            assert persisted.params["execution_attempts"][0]["invocation_id"] == "invocation-1"
        else:
            assert persisted.nextflow_run_id == "bms-workflow-development-job-1-1.service"
    await engine.dispose()


def test_scheduler_launch_failure_publishes_nonexecution_before_releasing_gpu() -> None:
    handoff = _handoff()
    prepared = _prepared_dispatch(handoff)
    job = SimpleNamespace(
        id="job-1",
        status="running",
        queue_status="running",
        completed_at=None,
        error_message=None,
        assigned_gpu=2,
        params=attach_scheduler_gpu_assignment(
            {
                resource_evidence.GLOBAL_RESOURCE_ADMISSION_PARAM: handoff,
                resource_evidence.GLOBAL_DISPATCH_AUTHORITY_PARAM: prepared,
            },
            2,
        ),
    )

    published = gpu_orchestrator._publish_pre_spawn_launch_failure(  # noqa: SLF001
        job,
        error=RuntimeError("adapter rejected before unit creation"),
        completed_at="2026-08-23T19:21:43Z",
    )

    assert published is True
    assert job.status == "failed"
    assert job.queue_status == "failed"
    assert job.assigned_gpu is None
    assert "gpu_id" not in job.params
    receipt = resource_evidence.validate_producer_resource_usage_receipt(job, handoff)
    assert receipt["schema"] == "bms.workflow-resource-nonexecution.v1"


@pytest.mark.asyncio
async def test_startup_recovery_requires_persisted_terminal_timestamp() -> None:
    handoff = _handoff()
    job = SimpleNamespace(
        id="job-1",
        status="failed",
        completed_at=None,
        error_message="adapter rejected before unit creation",
        params={
            resource_evidence.GLOBAL_RESOURCE_ADMISSION_PARAM: handoff,
            resource_evidence.GLOBAL_DISPATCH_AUTHORITY_PARAM: _prepared_dispatch(handoff),
        },
    )

    class CoreSession:
        async def commit(self) -> None:
            raise AssertionError("missing terminal timestamp must not be persisted")

    with pytest.raises(
        resource_evidence.ResourceUsageEvidenceError,
        match="terminal timestamp",
    ):
        await ngs_molbio_n5._recover_terminal_nonexecution_evidence(  # noqa: SLF001
            CoreSession(),
            job,
        )


@pytest.mark.asyncio
async def test_startup_recovery_commits_terminal_nonexecution_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handoff = _handoff()
    prepared = _prepared_dispatch(handoff)
    job = SimpleNamespace(
        id="job-1",
        status="cancelled",
        completed_at="2026-08-23T19:21:43Z",
        error_message="cancelled before adapter unit creation",
        params={
            resource_evidence.GLOBAL_RESOURCE_ADMISSION_PARAM: handoff,
            resource_evidence.GLOBAL_DISPATCH_AUTHORITY_PARAM: prepared,
        },
    )

    class CoreSession:
        committed = False

        async def execute(self, _statement: object) -> SimpleNamespace:
            return SimpleNamespace(rowcount=1)

        async def commit(self) -> None:
            self.committed = True

        async def refresh(self, _job: object) -> None:
            return None

    session = CoreSession()
    monkeypatch.setattr(ngs_molbio_n5, "_systemd_units_for_job", lambda _job_id: [])
    recovered = await ngs_molbio_n5._recover_terminal_nonexecution_evidence(  # noqa: SLF001
        session,
        job,
    )

    assert recovered is True
    assert session.committed is True
    receipt = resource_evidence.validate_producer_resource_usage_receipt(job, handoff)
    assert receipt["schema"] == resource_evidence.RESOURCE_HISTORICAL_NONEXECUTION_RECEIPT_SCHEMA
    assert job.params[resource_evidence.HISTORICAL_OWNER_ABSENCE_PARAM]["matched_units"] == []
    assert receipt["outcome"] == "launch_rejected_before_spawn"
    assert ngs_molbio_n5._producer_receipt_finished_at(receipt) == "2026-08-23T19:21:43Z"  # noqa: SLF001


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent_change", ["attempt", "owner"])
async def test_startup_recovery_loses_to_concurrent_execution_authority(
    tmp_path: Path,
    concurrent_change: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handoff = _handoff()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / f'startup-recovery-{concurrent_change}.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(ngs_molbio_n5, "_systemd_units_for_job", lambda _job_id: [])
    async with factory() as seed:
        seed.add(
            Job(
                id="job-1",
                name="governed-auto-gpu",
                status="failed",
                queue_status="failed",
                model_id="esmfold2",
                mode="predict",
                completed_at=datetime(2026, 8, 23, 19, 21, 43),
                error_message="adapter rejected before unit creation",
                params={
                    resource_evidence.GLOBAL_RESOURCE_ADMISSION_PARAM: handoff,
                    resource_evidence.GLOBAL_DISPATCH_AUTHORITY_PARAM: _prepared_dispatch(handoff),
                },
                output_dir=str(tmp_path / "output"),
            )
        )
        await seed.commit()

    async with factory() as stale_session, factory() as writer_session:
        stale_job = await stale_session.get(Job, "job-1")
        writer_job = await writer_session.get(Job, "job-1")
        assert stale_job is not None and writer_job is not None
        if concurrent_change == "attempt":
            writer_job.params = {
                **writer_job.params,
                "execution_attempts": [{"state": "planned", "invocation_id": "invocation-1"}],
            }
        else:
            writer_job.nextflow_run_id = "bms-workflow-development-job-1-1.service"
        await writer_session.commit()

        recovered = await ngs_molbio_n5._recover_terminal_nonexecution_evidence(  # noqa: SLF001
            stale_session,
            stale_job,
        )
        assert recovered is None

    async with factory() as verify:
        persisted = await verify.get(Job, "job-1")
        assert persisted is not None
        receipts = persisted.params.get(resource_evidence.RESOURCE_USAGE_RECEIPTS_PARAM, [])
        assert receipts == []
        if concurrent_change == "attempt":
            assert persisted.params["execution_attempts"][0]["invocation_id"] == "invocation-1"
        else:
            assert persisted.nextflow_run_id == "bms-workflow-development-job-1-1.service"
    await engine.dispose()


@pytest.mark.asyncio
async def test_startup_recovery_refuses_active_systemd_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handoff = _handoff()
    job = SimpleNamespace(
        id="job-1",
        status="failed",
        completed_at="2026-08-23T19:21:43Z",
        error_message="adapter rejected before unit creation",
        nextflow_run_id=None,
        params={
            resource_evidence.GLOBAL_RESOURCE_ADMISSION_PARAM: handoff,
            resource_evidence.GLOBAL_DISPATCH_AUTHORITY_PARAM: _prepared_dispatch(handoff),
        },
    )
    monkeypatch.setattr(
        ngs_molbio_n5,
        "_systemd_units_for_job",
        lambda _job_id: ["bms-development-job-job-1-attempt-1.service"],
    )

    with pytest.raises(
        resource_evidence.ResourceUsageEvidenceError,
        match="owner is still present",
    ):
        await ngs_molbio_n5._recover_terminal_nonexecution_evidence(  # noqa: SLF001
            SimpleNamespace(),
            job,
        )


def test_typed_sequence_field_drives_job_sequence_length() -> None:
    sequence = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"

    assert jobs._resolve_job_sequence_length(None, {"sequence": sequence}) == len(sequence)  # noqa: SLF001


def test_explicit_sequence_length_remains_authoritative() -> None:
    assert jobs._resolve_job_sequence_length(99, {"sequence": "ACDE"}) == 99  # noqa: SLF001
