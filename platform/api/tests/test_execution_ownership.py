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


def test_workflow_unit_discovery_requests_plain_systemd_output(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, ...]] = []

    def fake_systemctl(*args: str) -> subprocess.CompletedProcess[str]:
        captured.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(ownership, "_systemctl_user", fake_systemctl)

    assert ownership.discover_active_workflow_units(ownership.DEVELOPMENT_LANE) == {}
    assert "--plain" in captured[0]


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

    assert command[:7] == [
        "systemd-run",
        "--user",
        "--no-block",
        "--unit=biomodstack-development-job-job_with_spaces-attempt-2.service",
        "--slice=biomodstack-workflows-development.slice",
        "--property=Type=exec",
        "--property=KillMode=control-group",
    ]
    from biomodstack_local_resources import applied_local_policy
    assert f"--property=CPUQuota={applied_local_policy().cpu_threads * 100}%" in command
    assert "--property=CPUAccounting=yes" in command
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
    from biomodstack_local_resources import configured_local_policy
    assert f"CPUQuota={configured_local_policy().cpu_threads * 100}%" in expected_root
    assert f"MemoryMax={configured_local_policy().memory_bytes}" in expected_root
    assert "CPUQuota=" not in development[services.DEVELOPMENT_WORKFLOW_SLICE]
    assert "MemoryMax=" not in development[services.DEVELOPMENT_WORKFLOW_SLICE]
    assert "CPUQuota=" not in production[services.PRODUCTION_WORKFLOW_SLICE]
    assert "MemoryMax=" not in production[services.PRODUCTION_WORKFLOW_SLICE]
    assert services.DEVELOPMENT_WORKFLOW_SLICE.startswith("biomodstack-workflows-")
    assert services.PRODUCTION_WORKFLOW_SLICE.startswith("biomodstack-workflows-")
    assert "Environment=BMS_WORKFLOW_ADAPTER_URL=http://127.0.0.1:18001" in development[services.API_SERVICE]
    assert "Environment=BMS_WORKFLOW_ADAPTER_URL=http://127.0.0.1:18101" in production[services.CORE_RUNTIME_SERVICE]
    assert "Environment=BMS_CONTAINER_DIR=/mnt/BioModStack/apptainer" in development[services.API_SERVICE]
    assert "Environment=BMS_CONTAINER_DIR=/mnt/BioModStack/apptainer" in development[
        services.DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE
    ]
    assert (
        "Environment=BMS_NGS_RUNTIME_SIF=/mnt/BioModStack/dev/apptainer/dorado-v1.3.1-samtools-v1.24.sif"
        in development[services.API_SERVICE]
    )
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


def test_cancellation_accepts_explicitly_absent_transient_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = ownership.deterministic_unit_name(ownership.DEVELOPMENT_LANE, "gone-job", 1)
    command_calls: list[list[str]] = []
    stop_calls: list[str] = []
    kill_calls: list[str] = []

    def absent_unit(command: list[str]) -> subprocess.CompletedProcess[str]:
        command_calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="LoadState=not-found\nActiveState=inactive\n",
            stderr="",
        )

    monkeypatch.setattr(ownership, "_run_command", absent_unit)
    monkeypatch.setattr(ownership, "_stop_unit", stop_calls.append)
    monkeypatch.setattr(ownership, "_kill_unit", kill_calls.append)

    assert ownership.cancel_systemd_workflow_unit(unit, ownership.DEVELOPMENT_LANE) is True
    assert len(command_calls) == 1
    assert stop_calls == []
    assert kill_calls == []


def test_systemd_inspection_failure_is_not_classified_as_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = ownership.deterministic_unit_name(ownership.DEVELOPMENT_LANE, "unknown-job", 1)

    monkeypatch.setattr(
        ownership,
        "_run_command",
        lambda command: subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="Failed to connect to bus: No medium found",
        ),
    )

    with pytest.raises(ownership.SystemdCommandError, match="Failed to connect to bus"):
        ownership.cancel_systemd_workflow_unit(unit, ownership.DEVELOPMENT_LANE)


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


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("schema", "bms.workflow-execution-attempt.v0"),
        ("state", "failed"),
        ("lane", "Development"),
        ("generation", True),
        ("attempt", 0),
        (
            "unit",
            ownership.deterministic_unit_name(ownership.DEVELOPMENT_LANE, "job-1", 2),
        ),
        ("owner_nonce", ""),
        ("request_fingerprint", ""),
        ("planned_at", "invalid"),
        ("invocation_id", ""),
        ("started_at", "invalid"),
    ],
)
def test_latest_started_execution_attempt_requires_complete_canonical_receipt(
    field: str,
    invalid_value: object,
) -> None:
    unit = ownership.deterministic_unit_name(ownership.DEVELOPMENT_LANE, "job-1", 1)
    receipt = ownership.planned_execution_attempt(
        lane=ownership.DEVELOPMENT_LANE,
        job_id="job-1",
        generation=1,
        attempt=1,
        unit=unit,
        owner_nonce="nonce-1",
        request_fingerprint_value="fingerprint-1",
        planned_at="2026-08-09T23:04:24.418747Z",
    )
    receipt.update(
        {
            "state": "started",
            "invocation_id": "invocation-1",
            "started_at": "2026-08-09T23:04:24.446389Z",
        }
    )
    params = ownership.append_execution_attempt({}, receipt)

    assert ownership.latest_started_execution_attempt(params) == receipt

    malformed = dict(receipt)
    malformed[field] = invalid_value
    with pytest.raises(ownership.ExecutionOwnershipError):
        ownership.latest_started_execution_attempt(
            {ownership.EXECUTION_ATTEMPTS_PARAM: [malformed]}
        )


def test_scheduler_gpu_assignment_round_trip() -> None:
    assigned = ownership.attach_scheduler_gpu_assignment({"gpu_id": 7, "run_frustrampnn": True}, 2)
    assert assigned["gpu_id"] == 2
    assert ownership.release_scheduler_gpu_assignment(assigned) == {
        "gpu_id": 7,
        "run_frustrampnn": True,
    }


def test_terminal_execution_attempt_cannot_return_to_started() -> None:
    unit = ownership.deterministic_unit_name(ownership.DEVELOPMENT_LANE, "job-1", 1)
    planned = ownership.planned_execution_attempt(
        lane=ownership.DEVELOPMENT_LANE,
        job_id="job-1",
        generation=1,
        attempt=1,
        unit=unit,
        owner_nonce="nonce-1",
        request_fingerprint_value="fingerprint-1",
    )
    params = ownership.append_execution_attempt({}, planned)
    params = ownership.update_execution_attempt(
        params,
        lane=ownership.DEVELOPMENT_LANE,
        generation=1,
        attempt=1,
        unit=unit,
        owner_nonce="nonce-1",
        changes={"state": "completed", "invocation_id": "invocation-1"},
    )
    with pytest.raises(ownership.ExecutionOwnershipError, match="terminal"):
        ownership.update_execution_attempt(
            params,
            lane=ownership.DEVELOPMENT_LANE,
            generation=1,
            attempt=1,
            unit=unit,
            owner_nonce="nonce-1",
            changes={"state": "started"},
        )


def test_transient_runner_command_has_only_job_and_lane_arguments() -> None:
    source = (API_ROOT / "workflow_job_runner.py").read_text(encoding="utf-8")
    assert "--job-id" in source
    assert "--lane" in source
    assert "model_id" in source
    assert "output_dir" in source


def test_msa_batch_is_guarded_by_transient_runner_mode() -> None:
    source = (API_ROOT / "services" / "nextflow.py").read_text(encoding="utf-8")
    import ast

    tree = ast.parse(source)
    launcher = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "launch_nextflow_job")
    guarded_branch = next(
        node for node in ast.walk(launcher)
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "model_id == 'msa_batch' and transient_runner"
    )
    assert "await launch_msa_batch_job(job_id, params, output_dir)" in ast.unparse(guarded_branch)
    direct_launcher = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "launch_msa_batch_job")
    direct_source = ast.unparse(direct_launcher)
    assert "if not transient_workflow_runner_mode():" in direct_source
    assert "msa_batch execution is only permitted inside the transient workflow runner" in direct_source
