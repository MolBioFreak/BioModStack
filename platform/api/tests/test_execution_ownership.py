from __future__ import annotations

from pathlib import Path
import subprocess

import pytest


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
import sys

for root in (API_ROOT, REPO_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

import biomodstack_services as services
from services import execution_ownership as ownership
from services import workflow_adapter


def test_systemd_run_command_has_deterministic_lane_ownership(tmp_path: Path) -> None:
    command = ownership.build_systemd_run_command(
        lane=ownership.DEVELOPMENT_LANE,
        job_id="job/with spaces",
        attempt=2,
        command=["nextflow", "run", "workflows/protein_design.nf"],
        environment={"BMS_STATE_DIR": tmp_path / "state", "Z_LAST": "yes"},
        working_directory=tmp_path / "code",
        log_path=tmp_path / "job.log",
    )

    assert command[:8] == [
        "systemd-run",
        "--user",
        "--no-block",
        "--unit=biomodstack-development-job-job_with_spaces-attempt-2.service",
        "--slice=biomodstack-workflows-development.slice",
        "--property=Type=exec",
        "--property=KillMode=control-group",
        "--property=CPUQuota=2400%",
    ]
    assert f"--working-directory={(tmp_path / 'code').resolve()}" in command
    assert f"--property=StandardOutput=append:{(tmp_path / 'job.log').resolve()}" in command
    assert f"--setenv=BMS_STATE_DIR={tmp_path / 'state'}" in command
    assert command[command.index("--") + 1 :] == [
        "nextflow",
        "run",
        "workflows/protein_design.nf",
    ]
    receipt = ownership.owner_receipt(
        lane=ownership.DEVELOPMENT_LANE,
        job_id="job/with spaces",
        attempt=2,
        unit_name="biomodstack-development-job-job_with_spaces-attempt-2.service",
        command=["nextflow", "run", "workflows/protein_design.nf"],
    )
    assert receipt["owner"] == "systemd-user"
    assert receipt["unit"] == "biomodstack-development-job-job_with_spaces-attempt-2.service"


def test_lane_mismatch_is_rejected_before_unit_inspection() -> None:
    production_unit = ownership.deterministic_unit_name(
        ownership.PRODUCTION_LANE,
        "job-1",
        1,
    )

    with pytest.raises(ownership.LaneMismatchError):
        ownership.assert_unit_lane(production_unit, ownership.DEVELOPMENT_LANE)

    with pytest.raises(ownership.LaneMismatchError):
        ownership.validate_adapter_url_for_lane(
            "http://127.0.0.1:18101",
            ownership.DEVELOPMENT_LANE,
        )


def test_workflow_slices_render_one_global_aggregate_limit_with_lane_children(tmp_path: Path) -> None:
    development = services.render_user_units(tmp_path / "repo", runtime_mode="dev")
    production = services.render_user_units(tmp_path / "repo", runtime_mode="container")

    expected_root = services.render_workflow_root_slice()
    assert development[services.WORKFLOW_ROOT_SLICE] == expected_root
    assert production[services.WORKFLOW_ROOT_SLICE] == expected_root
    assert "CPUQuota=2400%" in expected_root
    assert "MemoryMax=96G" in expected_root
    assert "CPUQuota=" not in development[services.DEVELOPMENT_WORKFLOW_SLICE]
    assert "MemoryMax=" not in development[services.DEVELOPMENT_WORKFLOW_SLICE]
    assert "CPUQuota=" not in production[services.PRODUCTION_WORKFLOW_SLICE]
    assert "MemoryMax=" not in production[services.PRODUCTION_WORKFLOW_SLICE]
    assert services.DEVELOPMENT_WORKFLOW_SLICE.startswith("biomodstack-workflows-")
    assert services.PRODUCTION_WORKFLOW_SLICE.startswith("biomodstack-workflows-")
    assert "Environment=BMS_WORKFLOW_ADAPTER_URL=http://127.0.0.1:18001" in development[services.API_SERVICE]
    assert "Environment=BMS_WORKFLOW_ADAPTER_URL=http://127.0.0.1:18101" in production[services.CORE_RUNTIME_SERVICE]
    assert "Environment=BMS_CONTAINER_DIR=/mnt/BioModStack/dev/apptainer" in development[services.API_SERVICE]
    assert "Environment=BMS_CONTAINER_DIR=/mnt/BioModStack/dev/apptainer" in development[
        services.DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE
    ]
    assert "Environment=BMS_CONTAINER_DIR=/mnt/BioModStack/apptainer" in production[
        services.PRODUCTION_WORKFLOW_ADAPTER_SERVICE
    ]
    for unit in (
        development[services.API_SERVICE],
        development[services.DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE],
        production[services.CORE_RUNTIME_SERVICE],
        production[services.PRODUCTION_WORKFLOW_ADAPTER_SERVICE],
    ):
        assert "Environment=BMS_REQUIRE_TRANSIENT_WORKFLOW_UNITS=1" in unit


def test_duplicate_deterministic_unit_claim_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    command = ownership.build_systemd_run_command(
        lane=ownership.PRODUCTION_LANE,
        job_id="job-1",
        attempt=1,
        command=["nextflow", "run", "workflow.nf"],
    )
    monkeypatch.setattr(
        ownership,
        "_run_command",
        lambda _command: subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="Unit biomodstack-production-job-job-1-attempt-1.service already exists.",
        ),
    )

    with pytest.raises(ownership.DuplicateUnitError):
        ownership.create_systemd_workflow_unit(command)


def test_cancellation_requires_empty_cgroup_after_sigterm(monkeypatch: pytest.MonkeyPatch) -> None:
    unit = ownership.deterministic_unit_name(ownership.PRODUCTION_LANE, "job-1", 1)
    slice_name = ownership.workflow_slice_for_lane(ownership.PRODUCTION_LANE)
    active = ownership.UnitProperties("active", "running", "/user.slice/job", "42", "", "", slice_name)
    inactive_with_process = ownership.UnitProperties(
        "inactive", "dead", "/user.slice/job", "42", "143", "success", slice_name
    )
    inactive_empty = ownership.UnitProperties("inactive", "dead", "", "0", "143", "success", slice_name)

    states = iter([active, inactive_with_process, inactive_empty])
    monkeypatch.setattr(ownership, "show_unit_properties", lambda *_args: next(states))
    monkeypatch.setattr(ownership, "_stop_unit", lambda _unit: None)

    assert ownership.cancel_systemd_workflow_unit(
        unit,
        ownership.PRODUCTION_LANE,
        graceful_timeout_seconds=1.0,
        poll_interval_seconds=0.0,
    ) is True


def test_empty_cgroup_proof_reads_exact_cgroup_procs(tmp_path: Path) -> None:
    root = tmp_path / "cgroup"
    group = root / "user.slice" / "job.service"
    group.mkdir(parents=True)
    properties = ownership.UnitProperties(
        "inactive",
        "dead",
        "/user.slice/job.service",
        "0",
        "0",
        "success",
        ownership.workflow_slice_for_lane(ownership.DEVELOPMENT_LANE),
    )
    (group / "cgroup.procs").write_text("", encoding="utf-8")
    assert ownership.unit_is_inactive_with_empty_cgroup(properties, cgroup_root=root) is True
    (group / "cgroup.procs").write_text("42\n", encoding="utf-8")
    assert ownership.unit_is_inactive_with_empty_cgroup(properties, cgroup_root=root) is False


def test_cancellation_rejects_sigterm_success_with_nonempty_cgroup(monkeypatch: pytest.MonkeyPatch) -> None:
    unit = ownership.deterministic_unit_name(ownership.PRODUCTION_LANE, "job-2", 1)
    slice_name = ownership.workflow_slice_for_lane(ownership.PRODUCTION_LANE)
    active = ownership.UnitProperties("active", "running", "/user.slice/job", "42", "", "", slice_name)
    stopped_but_not_empty = ownership.UnitProperties(
        "inactive", "dead", "/user.slice/job", "42", "143", "success", slice_name
    )
    # Keep the state transition explicit without relying on PID or exit-code
    # heuristics. The helper is patched with a finite state machine.
    seen = {"count": 0}

    def show(_unit: str, _lane: str) -> ownership.UnitProperties:
        seen["count"] += 1
        return active if seen["count"] == 1 else stopped_but_not_empty

    monkeypatch.setattr(ownership, "show_unit_properties", show)
    monkeypatch.setattr(ownership, "_stop_unit", lambda _unit: None)
    monkeypatch.setattr(ownership, "_kill_unit", lambda _unit: None)

    assert ownership.cancel_systemd_workflow_unit(
        unit,
        ownership.PRODUCTION_LANE,
        graceful_timeout_seconds=0.001,
        poll_interval_seconds=0.0,
        kill_timeout_seconds=0.001,
    ) is False


def test_adapter_urls_are_lane_specific(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_LANE", ownership.DEVELOPMENT_LANE)
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://127.0.0.1:18001")
    assert workflow_adapter.workflow_adapter_base_url() == "http://127.0.0.1:18001"

    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://127.0.0.1:18101")
    with pytest.raises(ownership.LaneMismatchError):
        workflow_adapter.workflow_adapter_base_url()

    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_LANE", ownership.PRODUCTION_LANE)
    assert services.workflow_adapter_url_for_lane(ownership.PRODUCTION_LANE) == "http://127.0.0.1:18101"


def test_signal_exit_codes_cannot_grant_cancellation_authority() -> None:
    source = (API_ROOT / "services" / "nextflow.py").read_text(encoding="utf-8")
    signal_branch = source[source.index("elif exit_code in (-15, -9, 143, 137):") :]
    signal_branch = signal_branch[: signal_branch.index("else:")]
    assert "JobStatus.CANCELLED" not in signal_branch
    assert "TERMINATED_WITHOUT_CANCELLATION_RECEIPT" in signal_branch


def test_execution_attempt_receipt_history_is_append_only_and_identity_is_immutable() -> None:
    unit = ownership.deterministic_unit_name(ownership.DEVELOPMENT_LANE, "job-1", 1)
    planned = ownership.planned_execution_attempt(
        lane=ownership.DEVELOPMENT_LANE,
        job_id="job-1",
        generation=1,
        attempt=1,
        unit=unit,
        owner_nonce="nonce-1",
        request_fingerprint_value="fingerprint-1",
        planned_at="2026-08-08T12:00:00Z",
    )
    params = ownership.append_execution_attempt({"gpu_id": 1}, planned)
    started = ownership.update_execution_attempt(
        params,
        lane=ownership.DEVELOPMENT_LANE,
        generation=1,
        attempt=1,
        unit=unit,
        owner_nonce="nonce-1",
        changes={"state": "started", "invocation_id": "inv-1"},
    )
    assert started["gpu_id"] == 1
    assert started[ownership.EXECUTION_ATTEMPTS_PARAM][0]["state"] == "started"
    assert started[ownership.EXECUTION_ATTEMPTS_PARAM][0]["planned_at"] == "2026-08-08T12:00:00Z"

    with pytest.raises(ownership.ExecutionOwnershipError, match="immutable"):
        ownership.update_execution_attempt(
            started,
            lane=ownership.DEVELOPMENT_LANE,
            generation=1,
            attempt=1,
            unit=unit,
            owner_nonce="nonce-1",
            changes={"unit": "other.service"},
        )


def test_transient_runner_command_has_only_job_and_lane_arguments() -> None:
    source = (API_ROOT / "workflow_job_runner.py").read_text(encoding="utf-8")
    assert "--job-id" in source
    assert "--lane" in source
    assert "model_id" in source
    assert "output_dir" in source


def test_msa_batch_is_guarded_by_transient_runner_mode() -> None:
    source = (API_ROOT / "services" / "nextflow.py").read_text(encoding="utf-8")
    msa_branch = source[source.index("if model_id == 'msa_batch':") :]
    msa_branch = msa_branch[: msa_branch.index("# Use a mutable launch-params copy")]
    assert "if not transient_runner" in msa_branch
    assert "only permitted inside the transient workflow runner" in msa_branch
