from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
for root in (API_ROOT, REPO_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import database
from database import Base, Job
from routers import workflow_adapter as adapter_router
from services import execution_ownership as ownership
from services import nextflow
import workflow_job_runner as runner


class _Result:
    def __init__(self, job=None, jobs=None):
        self.job = job
        self.jobs = jobs if jobs is not None else ([job] if job is not None else [])

    def scalar_one_or_none(self):
        return self.job

    def scalars(self):
        return self

    def all(self):
        return self.jobs


class _Session:
    def __init__(self, job, jobs=None):
        self.job = job
        self.jobs = jobs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def execute(self, _query):
        return _Result(self.job, self.jobs)

    async def commit(self):
        return None


def _unit(lane: str = "development", job_id: str = "job-1") -> str:
    return ownership.deterministic_unit_name(lane, job_id, 1)


def _job(*, status: str = "running", model_id: str = "boltz2", job_id: str = "job-1", receipt_state: str = "started"):
    unit = _unit(job_id=job_id)
    receipt = ownership.planned_execution_attempt(
        lane="development",
        job_id=job_id,
        generation=1,
        attempt=1,
        unit=unit,
        owner_nonce="nonce-1",
        request_fingerprint_value="fingerprint-1",
    )
    receipt.update({"state": receipt_state})
    if receipt_state == "started":
        receipt.update({"invocation_id": "invocation-1", "started_at": ownership.utc_timestamp()})
    return SimpleNamespace(
        id=job_id,
        model_id=model_id,
        mode="predict",
        params=ownership.append_execution_attempt({}, receipt),
        output_dir="/lane/results/job-1",
        status=status,
        queue_status="running",
        started_at=SimpleNamespace(),
        completed_at=None,
        nextflow_run_id=unit,
        error_message=None,
    )


@pytest.fixture
def transient_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_LANE", "development")
    monkeypatch.setenv("BMS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("BMS_DB_PATH", str(tmp_path / "state" / "biomodstack.db"))
    monkeypatch.setenv("BMS_WORK", str(tmp_path / "state" / "work"))
    monkeypatch.setenv("BMS_RESULTS_DIR", str(tmp_path / "state" / "results"))
    monkeypatch.setenv("BMS_TRANSIENT_WORKFLOW_UNIT", "1")
    monkeypatch.setenv("BMS_TRANSIENT_WORKFLOW_UNIT_NAME", _unit())
    monkeypatch.setenv("BMS_TRANSIENT_WORKFLOW_OWNER_NONCE", "nonce-1")
    monkeypatch.setenv("INVOCATION_ID", "invocation-1")


def test_runner_starts_from_planned_receipt_without_adapter_wait(
    monkeypatch: pytest.MonkeyPatch,
    transient_identity,
) -> None:
    job = _job(receipt_state="planned")
    monkeypatch.setattr(database, "async_session", lambda: _Session(job))
    properties = ownership.UnitProperties(
        "active",
        "running",
        "",
        "42",
        "0",
        "success",
        ownership.workflow_slice_for_lane("development"),
        "invocation-1",
    )
    monkeypatch.setattr(runner, "show_unit_properties", lambda *_args: properties)
    launch_calls = []

    async def fake_launch(**kwargs):
        launch_calls.append(kwargs)
        job.status = "completed"
        job.queue_status = "completed"

    monkeypatch.setattr(nextflow, "launch_nextflow_job", fake_launch)
    assert asyncio.run(runner.run_workflow_job("job-1", "development")) == 0
    assert launch_calls
    assert ownership.latest_execution_attempt(job.params)["state"] == "completed"


def test_runner_completion_does_not_depend_on_adapter_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    transient_identity,
) -> None:
    job = _job()
    monkeypatch.setattr(database, "async_session", lambda: _Session(job))
    properties = ownership.UnitProperties(
        "active",
        "running",
        "",
        "42",
        "0",
        "success",
        ownership.workflow_slice_for_lane("development"),
        "invocation-1",
    )
    monkeypatch.setattr(runner, "show_unit_properties", lambda *_args: properties)
    launch_calls = []

    async def fake_launch(**kwargs):
        launch_calls.append(kwargs)
        job.status = "completed"
        job.queue_status = "completed"

    monkeypatch.setattr(nextflow, "launch_nextflow_job", fake_launch)

    assert asyncio.run(runner.run_workflow_job("job-1", "development")) == 0
    assert launch_calls[0]["model_id"] == "boltz2"
    assert launch_calls[0]["output_dir"] == "/lane/results/job-1"
    assert ownership.latest_execution_attempt(job.params)["state"] == "completed"


def test_runner_uses_authoritative_msa_job_and_keeps_it_inside_unit(
    monkeypatch: pytest.MonkeyPatch,
    transient_identity,
) -> None:
    job = _job(model_id="msa_batch")
    job.params["gpu_id"] = 2
    monkeypatch.setattr(database, "async_session", lambda: _Session(job))
    properties = ownership.UnitProperties(
        "active",
        "running",
        "",
        "42",
        "0",
        "success",
        ownership.workflow_slice_for_lane("development"),
        "invocation-1",
    )
    monkeypatch.setattr(runner, "show_unit_properties", lambda *_args: properties)
    launch_calls = []

    async def fake_launch(**kwargs):
        launch_calls.append(kwargs)
        job.status = "completed"
        job.queue_status = "completed"

    monkeypatch.setattr(nextflow, "launch_nextflow_job", fake_launch)
    monkeypatch.setattr(
        nextflow,
        "create_systemd_workflow_unit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("nested systemd claim")),
        raising=False,
    )

    assert asyncio.run(runner.run_workflow_job("job-1", "development")) == 0
    assert launch_calls[0]["model_id"] == "msa_batch"
    assert launch_calls[0]["params"] == {"gpu_id": 2}


@pytest.mark.asyncio
async def test_runner_completion_cannot_overwrite_concurrent_operator_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'runner-cancel.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    started_job = _job(job_id="runner-cancel")
    async with factory() as session:
        session.add(
            Job(
                id=started_job.id,
                name="runner-cancel",
                model_id=started_job.model_id,
                mode=started_job.mode,
                params=started_job.params,
                output_dir=started_job.output_dir,
                status="running",
                queue_status="running",
                nextflow_run_id=started_job.nextflow_run_id,
                assigned_gpu=2,
            )
        )
        await session.commit()

    worker_read = asyncio.Event()
    operator_done = asyncio.Event()

    class RacingSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            result = await super().execute(statement, *args, **kwargs)
            if "SELECT" in str(statement).upper() and "jobs" in str(statement).lower():
                worker_read.set()
                await asyncio.sleep(0.05)
            return result

    racing_factory = sessionmaker(engine, class_=RacingSession, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session", racing_factory)

    async def cancel_after_worker_read() -> None:
        await worker_read.wait()
        async with factory() as operator_session:
            job = await operator_session.get(Job, "runner-cancel")
            assert job is not None
            params = ownership.update_execution_attempt(
                job.params,
                lane="development",
                generation=1,
                attempt=1,
                unit=_unit(job_id="runner-cancel"),
                owner_nonce="nonce-1",
                changes={
                    "state": "cancelled",
                    "invocation_id": "invocation-1",
                    "terminal_at": ownership.utc_timestamp(),
                    "terminal_reason": "operator cancellation",
                },
            )
            job.params = ownership.release_scheduler_gpu_assignment(params)
            job.status = "cancelled"
            job.queue_status = "cancelled"
            job.assigned_gpu = None
            job.error_message = "cancelled by operator"
            await operator_session.commit()
        operator_done.set()

    cancel_task = asyncio.create_task(cancel_after_worker_read())
    state = await runner._finish_attempt(
        job_id="runner-cancel",
        lane="development",
        unit_name=_unit(job_id="runner-cancel"),
        owner_nonce="nonce-1",
        invocation_id="invocation-1",
        state="completed",
        reason="late worker completion",
    )
    await cancel_task
    assert state == "completed"

    async with factory() as session:
        persisted = await session.get(Job, "runner-cancel")
        assert persisted is not None
        receipt = ownership.latest_execution_attempt(persisted.params)
        assert receipt is not None
        assert receipt["state"] == "cancelled"
        assert receipt["terminal_reason"] == "operator cancellation"
        assert persisted.status == "cancelled"
        assert persisted.queue_status == "cancelled"
        assert persisted.error_message == "cancelled by operator"
        assert persisted.assigned_gpu is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_launch_returns_same_live_unit_without_spawning(
    monkeypatch: pytest.MonkeyPatch,
    transient_identity,
) -> None:
    payload = adapter_router.WorkflowAdapterLaunchRequest(
        job_id="job-1",
        model_id="boltz2",
        mode="predict",
        params={"gpu_id": 1},
        output_dir="/lane/results/job-1",
    )
    job = _job()
    job.params[ownership.EXECUTION_ATTEMPTS_PARAM][0]["request_fingerprint"] = adapter_router._launch_request_fingerprint(
        payload,
        "development",
    )
    monkeypatch.setattr(database, "async_session", lambda: _Session(job))
    properties = ownership.UnitProperties(
        "active",
        "running",
        "",
        "42",
        "0",
        "success",
        ownership.workflow_slice_for_lane("development"),
        "invocation-1",
    )
    monkeypatch.setattr(adapter_router, "show_unit_properties", lambda *_args: properties)
    monkeypatch.setattr(
        adapter_router,
        "create_systemd_workflow_unit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate spawned")),
    )

    response = await adapter_router.workflow_adapter_launch(
        payload,
        SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")),
    )
    assert response.nextflow_run_id == _unit()
    assert response.launch_mode == "transient-systemd"


@pytest.mark.asyncio
async def test_startup_reconciliation_retains_active_runner_after_adapter_restart(
    monkeypatch: pytest.MonkeyPatch,
    transient_identity,
) -> None:
    job = _job()
    monkeypatch.setattr(database, "async_session", lambda: _Session(job, [job]))
    properties = ownership.UnitProperties(
        "active",
        "running",
        "",
        "42",
        "0",
        "success",
        ownership.workflow_slice_for_lane("development"),
        "invocation-1",
    )
    monkeypatch.setattr(adapter_router, "show_unit_properties", lambda *_args: properties)

    report = await adapter_router.reconcile_workflow_adapter_startup()
    assert report["active"] == 1
    assert report["interrupted"] == 0
    assert job.status == "running"


@pytest.mark.asyncio
async def test_startup_reconciliation_adopts_active_invocation_for_planned_attempt(
    monkeypatch: pytest.MonkeyPatch,
    transient_identity,
) -> None:
    job = _job(receipt_state="planned")
    monkeypatch.setattr(database, "async_session", lambda: _Session(job, [job]))
    properties = ownership.UnitProperties(
        "active",
        "running",
        "",
        "42",
        "0",
        "success",
        ownership.workflow_slice_for_lane("development"),
        "invocation-1",
    )
    monkeypatch.setattr(adapter_router, "show_unit_properties", lambda *_args: properties)

    report = await adapter_router.reconcile_workflow_adapter_startup()

    assert report == {"active": 1, "interrupted": 0, "critical": 0}
    receipt = ownership.latest_execution_attempt(job.params)
    assert receipt is not None
    assert receipt["state"] == "started"
    assert receipt["invocation_id"] == "invocation-1"


@pytest.mark.asyncio
async def test_finish_attempt_rejects_mismatched_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _job()
    monkeypatch.setattr(database, "async_session", lambda: _Session(job))

    await runner._finish_attempt(
        job_id="job-1",
        lane="development",
        unit_name=_unit(),
        owner_nonce="nonce-1",
        invocation_id="different-invocation",
        state="completed",
    )

    receipt = ownership.latest_execution_attempt(job.params)
    assert receipt is not None
    assert receipt["state"] == "started"
    assert receipt["invocation_id"] == "invocation-1"


@pytest.mark.asyncio
async def test_startup_reconciliation_marks_invocation_mismatch_critical_and_retains_unit(
    monkeypatch: pytest.MonkeyPatch,
    transient_identity,
) -> None:
    job = _job()
    monkeypatch.setattr(database, "async_session", lambda: _Session(job, [job]))
    properties = ownership.UnitProperties(
        "active",
        "running",
        "",
        "42",
        "0",
        "success",
        ownership.workflow_slice_for_lane("development"),
        "different-invocation",
    )
    monkeypatch.setattr(adapter_router, "show_unit_properties", lambda *_args: properties)

    report = await adapter_router.reconcile_workflow_adapter_startup()
    assert report["critical"] == 1
    assert report["interrupted"] == 0
    assert job.status == "running"
    assert job.queue_status == "ownership_conflict"
    receipt = ownership.latest_execution_attempt(job.params)
    assert receipt is not None
    assert receipt["state"] == "ownership_conflict"
    assert receipt["unit"] == _unit()


@pytest.mark.asyncio
async def test_startup_reconciliation_marks_missing_unit_without_relaunch(
    monkeypatch: pytest.MonkeyPatch,
    transient_identity,
) -> None:
    job = _job()
    monkeypatch.setattr(database, "async_session", lambda: _Session(job, [job]))
    monkeypatch.setattr(
        adapter_router,
        "show_unit_properties",
        lambda *_args: (_ for _ in ()).throw(ownership.UnitNotFoundError("missing")),
    )
    monkeypatch.setattr(
        adapter_router,
        "create_systemd_workflow_unit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stale unit relaunched")),
    )

    report = await adapter_router.reconcile_workflow_adapter_startup()
    assert report["interrupted"] == 1
    assert job.status == "failed"
    assert ownership.latest_execution_attempt(job.params)["terminal_reason"] == "INTERRUPTED_OWNER"
