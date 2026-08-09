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

import database
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


def _job(*, status: str = "running", model_id: str = "boltz2", job_id: str = "job-1"):
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
    receipt.update({"state": "started", "invocation_id": "invocation-1"})
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
