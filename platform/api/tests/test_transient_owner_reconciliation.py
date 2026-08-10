from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services import execution_ownership, gpu_orchestrator, nextflow, result_state_integrity


@pytest.mark.asyncio
async def test_completion_reconciler_waits_for_transient_owner_before_stale_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job_id = "job-transient-owner-123"
    unit_name = execution_ownership.deterministic_unit_name("development", job_id, 1)
    output_dir = tmp_path / job_id
    output_dir.mkdir()
    receipt = execution_ownership.planned_execution_attempt(
        lane="development",
        job_id=job_id,
        generation=1,
        attempt=1,
        unit=unit_name,
        owner_nonce="owner-nonce-1",
        request_fingerprint_value="request-fingerprint-1",
    )
    receipt.update({"state": "started", "invocation_id": "invocation-1"})
    job = SimpleNamespace(
        id=job_id,
        name="adapter-owned-job",
        model_id="ont_fastq_qc",
        mode="ont_fastq_qc",
        status="running",
        queue_status="running",
        started_at=datetime.utcnow() - timedelta(seconds=301),
        assigned_gpu=None,
        child_output_dir=None,
        output_dir=str(output_dir),
        nextflow_run_id=unit_name,
        current_stage="constructverify",
        stage_progress=None,
        stage_work_dir=None,
        error_message=None,
        completed_at=None,
        awaiting_input=False,
        awaiting_stage=None,
        parent_job_id=None,
        params={execution_ownership.EXECUTION_ATTEMPTS_PARAM: [receipt]},
    )

    class _Rows:
        def all(self) -> list[SimpleNamespace]:
            return [job]

    class _Result:
        def scalars(self) -> _Rows:
            return _Rows()

    class _Session:
        dirty: list[object] = []

        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def execute(self, *_args, **_kwargs) -> _Result:
            return _Result()

    async def fake_commit_reconciled_job_mutations(_session: object) -> int:
        return 1

    finalization_calls: list[str] = []

    async def fake_finalize_successful_job(
        candidate: SimpleNamespace,
        _output_dir: str,
        _session: object,
    ) -> SimpleNamespace:
        finalization_calls.append(str(candidate.id))
        candidate.status = "completed"
        candidate.queue_status = "completed"
        candidate.completed_at = datetime.utcnow()
        return SimpleNamespace(completed=True, design_count=0)

    systemd_queries: list[tuple[str, str]] = []
    terminal_history_by_job: dict[str, tuple[str, str]] = {}
    adapter_lane_value: str | None = "development"
    owner_state = "active"
    unit_invocation_id = "invocation-1"
    force_nonempty_cgroup = False
    query_error = False

    def fake_show_unit_properties(unit: str, lane: str) -> execution_ownership.UnitProperties:
        systemd_queries.append((unit, lane))
        if query_error:
            raise execution_ownership.ExecutionOwnershipError("systemd user bus unavailable")
        return execution_ownership.UnitProperties(
            active_state=owner_state,
            sub_state="running" if owner_state == "active" else "dead",
            control_group=(
                f"/user.slice/{unit}"
                if owner_state == "active" or force_nonempty_cgroup
                else ""
            ),
            main_pid="42" if owner_state == "active" else "0",
            exec_main_status="0",
            result="success",
            slice_name=execution_ownership.workflow_slice_for_lane(lane),
            invocation_id=unit_invocation_id,
        )

    monkeypatch.setattr(gpu_orchestrator, "workflow_adapter_enabled", lambda: True)
    monkeypatch.setattr(gpu_orchestrator, "workflow_adapter_lane", lambda: adapter_lane_value)
    monkeypatch.setattr(
        gpu_orchestrator,
        "_read_nextflow_history_statuses",
        lambda _job_ids: dict(terminal_history_by_job),
    )
    monkeypatch.setattr(gpu_orchestrator, "nextflow_history_status_for_run_dir", lambda *_args: None)
    monkeypatch.setattr(gpu_orchestrator, "nextflow_history_status", lambda _job: None)
    monkeypatch.setattr(gpu_orchestrator, "has_stage_gate", lambda _job: False)
    monkeypatch.setattr(gpu_orchestrator, "show_unit_properties", fake_show_unit_properties, raising=False)
    monkeypatch.setattr(
        gpu_orchestrator,
        "_commit_reconciled_job_mutations",
        fake_commit_reconciled_job_mutations,
    )
    monkeypatch.setattr(
        result_state_integrity,
        "finalize_successful_job",
        fake_finalize_successful_job,
    )
    monkeypatch.setattr(nextflow, "get_running_jobs", lambda: {})

    orchestrator = gpu_orchestrator.GPUOrchestrator(
        db_session_factory=lambda: _Session(),
        get_gpu_stats_fn=lambda: [],
        launch_nextflow_job_fn=lambda **_kwargs: None,
    )

    await orchestrator.check_job_completions()

    assert systemd_queries == [(unit_name, "development")]
    assert job.status == "running"
    assert job.queue_status == "running"
    assert job.completed_at is None
    assert job.error_message is None

    job.started_at = datetime.utcnow() - timedelta(seconds=1)
    terminal_history_by_job[job_id] = ("OK", "1s")
    await orchestrator.check_job_completions()

    assert finalization_calls == []
    assert job.status == "running"
    assert job.queue_status == "running"
    assert job.completed_at is None
    assert job.error_message is None

    terminal_history_by_job.clear()
    job.started_at = datetime.utcnow() - timedelta(seconds=301)
    query_error = True
    await orchestrator.check_job_completions()

    assert job.status == "running"
    assert job.queue_status == "running"
    assert job.completed_at is None
    assert job.error_message is None

    query_error = False
    adapter_lane_value = None
    await orchestrator.check_job_completions()

    assert job.status == "running"
    assert job.queue_status == "running"

    adapter_lane_value = "development"
    owner_state = "inactive"
    force_nonempty_cgroup = True
    await orchestrator.check_job_completions()

    assert job.status == "running"
    assert job.queue_status == "running"

    force_nonempty_cgroup = False
    owner_state = "failed"
    await orchestrator.check_job_completions()

    assert job.status == "running"
    assert job.queue_status == "running"

    owner_state = "inactive"
    unit_invocation_id = "different-invocation"
    await orchestrator.check_job_completions()

    assert job.status == "running"
    assert job.queue_status == "running"

    saved_params = job.params
    job.params = {}
    await orchestrator.check_job_completions()

    assert job.status == "running"
    assert job.queue_status == "running"

    wrong_lane_receipt = execution_ownership.planned_execution_attempt(
        lane="production",
        job_id=job_id,
        generation=2,
        attempt=1,
        unit=execution_ownership.deterministic_unit_name("production", job_id, 1),
        owner_nonce="owner-nonce-production",
        request_fingerprint_value="request-fingerprint-production",
    )
    wrong_lane_receipt.update(
        {"state": "started", "invocation_id": "production-invocation"}
    )
    job.params = {
        execution_ownership.EXECUTION_ATTEMPTS_PARAM: [receipt, wrong_lane_receipt]
    }
    unit_invocation_id = "invocation-1"
    await orchestrator.check_job_completions()

    assert job.status == "running"
    assert job.queue_status == "running"

    other_job_id = "other-job-transient-owner-456"
    other_job_receipt = execution_ownership.planned_execution_attempt(
        lane="development",
        job_id=other_job_id,
        generation=3,
        attempt=1,
        unit=execution_ownership.deterministic_unit_name("development", other_job_id, 1),
        owner_nonce="owner-nonce-other-job",
        request_fingerprint_value="request-fingerprint-other-job",
    )
    other_job_receipt.update(
        {"state": "started", "invocation_id": "other-job-invocation"}
    )
    job.params = {
        execution_ownership.EXECUTION_ATTEMPTS_PARAM: [receipt, other_job_receipt]
    }
    await orchestrator.check_job_completions()

    assert job.status == "running"
    assert job.queue_status == "running"

    job.params = saved_params
    await orchestrator.check_job_completions()

    assert systemd_queries == [
        (unit_name, "development"),
        (unit_name, "development"),
        (unit_name, "development"),
        (unit_name, "development"),
        (unit_name, "development"),
        (unit_name, "development"),
        (unit_name, "development"),
    ]
    assert job.status == "failed"
    assert job.queue_status == "failed"
    assert job.completed_at is not None
    assert job.error_message == (
        "Reconciled as failed: no active process and no terminal "
        ".nextflow/history status (expected OK/ERR)"
    )
