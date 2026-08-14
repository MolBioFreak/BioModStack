"""Lane-scoped systemd ownership for detached Nextflow jobs.

This module is deliberately small and independent of the workflow registry.  It
owns only the execution boundary: lane identity, transient unit construction,
unit-property discovery, and cancellation proof.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import os
import re
import stat
import subprocess
import time
from typing import Any
from urllib.parse import urlparse


DEVELOPMENT_LANE = "development"
PRODUCTION_LANE = "production"
VALID_LANES = frozenset({DEVELOPMENT_LANE, PRODUCTION_LANE})
LANE_ENV = "BMS_WORKFLOW_ADAPTER_LANE"
RESULTS_DIR_ENV = "BMS_RESULTS_DIR"
RESULTS_ROOT_ENV = "BMS_RESULTS_ROOT"
TRANSIENT_WORKFLOW_UNIT_ENV = "BMS_TRANSIENT_WORKFLOW_UNIT"
TRANSIENT_WORKFLOW_UNIT_NAME_ENV = "BMS_TRANSIENT_WORKFLOW_UNIT_NAME"
TRANSIENT_WORKFLOW_OWNER_NONCE_ENV = "BMS_TRANSIENT_WORKFLOW_OWNER_NONCE"
EXECUTION_ATTEMPTS_PARAM = "execution_attempts"
EXECUTION_ATTEMPT_SCHEMA = "bms.workflow-execution-attempt.v1"
EXECUTION_ATTEMPT_TERMINAL_STATES = frozenset(
    {"completed", "failed", "cancelled", "interrupted_owner", "rejected"}
)
EXECUTION_ATTEMPT_IMMUTABLE_FIELDS = frozenset(
    {
        "schema",
        "lane",
        "generation",
        "attempt",
        "unit",
        "owner_nonce",
        "request_fingerprint",
        "planned_at",
    }
)
SCHEDULER_GPU_ASSIGNMENT_PARAM = "_scheduler_gpu_assignment"
SCHEDULER_GPU_ASSIGNMENT_SCHEMA = "bms.scheduler-gpu-assignment.v1"

LANE_ADAPTER_PORTS: dict[str, int] = {
    DEVELOPMENT_LANE: 18001,
    PRODUCTION_LANE: 18101,
}
JOB_CPU_QUOTA = "2400%"
WORKFLOW_MEMORY_MAX = "96G"
UNIT_PREFIX = "biomodstack"
CGROUP_ROOT = Path("/sys/fs/cgroup")

_UNIT_NAME_RE = re.compile(
    r"^biomodstack-(development|production)-job-(?P<job>.+)-attempt-(?P<attempt>[1-9][0-9]*)\.service$"
)
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ExecutionOwnershipError(RuntimeError):
    """Base error for fail-closed execution ownership operations."""


class LaneIdentityError(ExecutionOwnershipError):
    """The adapter or caller has no valid explicit lane identity."""


class LaneMismatchError(ExecutionOwnershipError):
    """A request, URL, unit, or unit property belongs to another lane."""


class DuplicateUnitError(ExecutionOwnershipError):
    """The deterministic unit already exists and cannot be claimed again."""


class UnitNotFoundError(ExecutionOwnershipError):
    """systemd could not prove the requested unit exists."""


class SystemdCommandError(ExecutionOwnershipError):
    """A systemd command failed without proving a duplicate claim."""


@dataclass(frozen=True)
class AdapterIdentity:
    lane: str
    state_dir: Path
    db_path: Path
    work_dir: Path
    results_root: Path


@dataclass(frozen=True)
class UnitIdentity:
    lane: str
    job_id: str
    attempt: int
    unit_name: str
    slice_name: str


@dataclass(frozen=True)
class UnitProperties:
    active_state: str
    sub_state: str
    control_group: str
    main_pid: str
    exec_main_status: str
    result: str
    slice_name: str
    invocation_id: str = ""
    load_state: str = ""
    cpu_quota_per_sec_usec: str = ""
    memory_max: str = ""
    cpu_accounting: str = ""
    memory_accounting: str = ""
    tasks_accounting: str = ""
    device_policy: str = ""
    device_allow: str = ""

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "UnitProperties":
        return cls(
            active_state=str(values.get("ActiveState", "")),
            sub_state=str(values.get("SubState", "")),
            control_group=str(values.get("ControlGroup", "")),
            main_pid=str(values.get("MainPID", "")),
            exec_main_status=str(values.get("ExecMainStatus", "")),
            result=str(values.get("Result", "")),
            slice_name=str(values.get("Slice", "")),
            invocation_id=str(values.get("InvocationID", "")),
            load_state=str(values.get("LoadState", "")),
            cpu_quota_per_sec_usec=str(values.get("CPUQuotaPerSecUSec", "")),
            memory_max=str(values.get("MemoryMax", "")),
            cpu_accounting=str(values.get("CPUAccounting", "")),
            memory_accounting=str(values.get("MemoryAccounting", "")),
            tasks_accounting=str(values.get("TasksAccounting", "")),
            device_policy=str(values.get("DevicePolicy", "")),
            device_allow=str(values.get("DeviceAllow", "")),
        )


def normalize_lane(value: object) -> str:
    lane = str(value or "").strip().lower()
    if lane not in VALID_LANES:
        raise LaneIdentityError(
            f"{LANE_ENV} must be exactly one of: {', '.join(sorted(VALID_LANES))}"
        )
    return lane


def lane_for_runtime_mode(runtime_mode: object) -> str:
    mode = str(runtime_mode or "").strip().lower()
    if mode == "dev":
        return DEVELOPMENT_LANE
    if mode in {"container", "prod", "production"}:
        return PRODUCTION_LANE
    raise LaneIdentityError(f"No execution lane is defined for runtime mode {runtime_mode!r}")


def configured_lane(*, required: bool = True) -> str | None:
    raw_lane = os.getenv(LANE_ENV)
    if raw_lane is None or not raw_lane.strip():
        if required:
            raise LaneIdentityError(f"{LANE_ENV} is required; refusing an unowned adapter")
        return None
    return normalize_lane(raw_lane)


def adapter_url_for_lane(lane: str) -> str:
    normalized = normalize_lane(lane)
    return f"http://127.0.0.1:{LANE_ADAPTER_PORTS[normalized]}"


def validate_adapter_url_for_lane(url: str, lane: str) -> str:
    """Require the lane's dedicated listener before an API can call an adapter."""
    normalized_lane = normalize_lane(lane)
    normalized_url = str(url or "").strip().rstrip("/")
    parsed = urlparse(normalized_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.port is None:
        raise LaneMismatchError(f"Invalid workflow-adapter URL for {normalized_lane}: {url!r}")
    expected_port = LANE_ADAPTER_PORTS[normalized_lane]
    if parsed.port != expected_port:
        raise LaneMismatchError(
            f"Workflow-adapter URL {normalized_url!r} is not owned by lane {normalized_lane} "
            f"(expected port {expected_port})"
        )
    return normalized_url


def adapter_identity_from_environment() -> AdapterIdentity:
    """Read all adapter ownership roots without applying any fallback."""
    lane = configured_lane()
    raw_values = {
        "BMS_STATE_DIR": os.getenv("BMS_STATE_DIR"),
        "BMS_DB_PATH": os.getenv("BMS_DB_PATH"),
        "BMS_WORK": os.getenv("BMS_WORK"),
        RESULTS_DIR_ENV: os.getenv(RESULTS_DIR_ENV) or os.getenv(RESULTS_ROOT_ENV),
    }
    missing = [name for name, value in raw_values.items() if not str(value or "").strip()]
    if missing:
        raise LaneIdentityError(
            f"{LANE_ENV}={lane} requires explicit lane-local roots: {', '.join(missing)}"
        )

    paths = {name: Path(str(value)).expanduser() for name, value in raw_values.items()}
    if any(not path.is_absolute() for path in paths.values()):
        raise LaneIdentityError("Adapter ownership roots must be absolute paths")
    return AdapterIdentity(
        lane=lane,
        state_dir=paths["BMS_STATE_DIR"].resolve(),
        db_path=paths["BMS_DB_PATH"].resolve(),
        work_dir=paths["BMS_WORK"].resolve(),
        results_root=paths[RESULTS_DIR_ENV].resolve(),
    )


def workflow_slice_for_lane(lane: str) -> str:
    return f"{UNIT_PREFIX}-workflows-{normalize_lane(lane)}.slice"


def deterministic_unit_name(lane: str, job_id: str, attempt: int) -> str:
    normalized_lane = normalize_lane(lane)
    try:
        normalized_attempt = int(attempt)
    except (TypeError, ValueError) as exc:
        raise ExecutionOwnershipError(f"Invalid workflow attempt: {attempt!r}") from exc
    if normalized_attempt < 1:
        raise ExecutionOwnershipError("Workflow attempt must be at least 1")
    token = re.sub(r"[^A-Za-z0-9_.:-]", "_", str(job_id or "").strip())
    if not token:
        raise ExecutionOwnershipError("Workflow job id is required for deterministic ownership")
    return f"{UNIT_PREFIX}-{normalized_lane}-job-{token}-attempt-{normalized_attempt}.service"


def unit_identity(lane: str, job_id: str, attempt: int) -> UnitIdentity:
    normalized_lane = normalize_lane(lane)
    unit_name = deterministic_unit_name(normalized_lane, job_id, attempt)
    return UnitIdentity(
        lane=normalized_lane,
        job_id=str(job_id),
        attempt=int(attempt),
        unit_name=unit_name,
        slice_name=workflow_slice_for_lane(normalized_lane),
    )


def utc_timestamp(value: datetime | None = None) -> str:
    current = value or datetime.utcnow()
    return current.replace(microsecond=current.microsecond).isoformat() + "Z"


def _json_safe(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def request_fingerprint(payload: Mapping[str, object]) -> str:
    """Return a stable digest for the adapter request identity."""
    encoded = json.dumps(
        _json_safe(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def params_mapping(value: object) -> dict[str, Any]:
    """Copy a Job.params value without retaining an ORM/JSON mutable alias."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = {}
    else:
        parsed = value
    if not isinstance(parsed, Mapping):
        return {}
    return {str(key): item for key, item in parsed.items()}


def attach_scheduler_gpu_assignment(params: object, gpu_id: int) -> dict[str, Any]:
    """Add a scheduler GPU and retain the user value for terminal cleanup."""
    original = params_mapping(params)
    if SCHEDULER_GPU_ASSIGNMENT_PARAM in original:
        raise ExecutionOwnershipError("scheduler GPU assignment metadata already exists")
    had_user_value = "gpu_id" in original
    assigned = dict(original)
    assigned["gpu_id"] = int(gpu_id)
    assigned[SCHEDULER_GPU_ASSIGNMENT_PARAM] = {
        "schema": SCHEDULER_GPU_ASSIGNMENT_SCHEMA,
        "assigned_gpu": int(gpu_id),
        "original_gpu_id_present": had_user_value,
        "original_gpu_id": original.get("gpu_id") if had_user_value else None,
    }
    return assigned


def release_scheduler_gpu_assignment(params: object) -> dict[str, Any]:
    """Remove scheduler ownership and restore any user-supplied GPU value."""
    normalized = params_mapping(params)
    marker = normalized.pop(SCHEDULER_GPU_ASSIGNMENT_PARAM, None)
    if marker is None:
        return normalized
    if not isinstance(marker, Mapping) or marker.get("schema") != SCHEDULER_GPU_ASSIGNMENT_SCHEMA:
        raise ExecutionOwnershipError("scheduler GPU assignment metadata is invalid")
    if bool(marker.get("original_gpu_id_present")):
        if "original_gpu_id" not in marker:
            raise ExecutionOwnershipError("scheduler GPU assignment lacks its original value")
        normalized["gpu_id"] = marker["original_gpu_id"]
    else:
        normalized.pop("gpu_id", None)
    return normalized


def execution_attempts(params: object) -> list[dict[str, Any]]:
    values = params_mapping(params).get(EXECUTION_ATTEMPTS_PARAM, [])
    if not isinstance(values, list):
        return []
    return [dict(item) for item in values if isinstance(item, Mapping)]


def latest_execution_attempt(
    params: object,
    *,
    lane: str | None = None,
) -> dict[str, Any] | None:
    entries = execution_attempts(params)
    if lane is not None:
        normalized_lane = normalize_lane(lane)
        entries = [entry for entry in entries if str(entry.get("lane", "")) == normalized_lane]
    return entries[-1] if entries else None


def _required_receipt_text(receipt: Mapping[str, object], field: str) -> str:
    value = receipt.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ExecutionOwnershipError(
            f"Execution-attempt receipt requires nonempty string field {field!r}"
        )
    return value.strip()


def _required_receipt_timestamp(receipt: Mapping[str, object], field: str) -> str:
    value = _required_receipt_text(receipt, field)
    if not value.endswith("Z"):
        raise ExecutionOwnershipError(
            f"Execution-attempt receipt field {field!r} must be a UTC timestamp"
        )
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ExecutionOwnershipError(
            f"Execution-attempt receipt field {field!r} must be a valid UTC timestamp"
        ) from exc
    return value


def latest_started_execution_attempt(params: object) -> dict[str, Any] | None:
    """Return the absolute newest complete started receipt or fail closed."""

    values = params_mapping(params).get(EXECUTION_ATTEMPTS_PARAM, [])
    if not isinstance(values, list):
        raise ExecutionOwnershipError(
            f"{EXECUTION_ATTEMPTS_PARAM} must remain an append-only list"
        )
    if not values:
        return None

    newest = values[-1]
    if not isinstance(newest, Mapping):
        raise ExecutionOwnershipError(
            "Newest execution-attempt receipt is not a mapping"
        )
    receipt = dict(newest)

    if receipt.get("schema") != EXECUTION_ATTEMPT_SCHEMA:
        raise ExecutionOwnershipError(
            f"Newest execution-attempt receipt must use schema {EXECUTION_ATTEMPT_SCHEMA!r}"
        )
    if receipt.get("state") != "started":
        raise ExecutionOwnershipError(
            "Newest execution-attempt receipt must be in state 'started' for liveness reconciliation"
        )

    lane = normalize_lane(receipt.get("lane"))
    if receipt.get("lane") != lane:
        raise ExecutionOwnershipError(
            "Newest execution-attempt receipt lane must be canonical"
        )

    generation = receipt.get("generation")
    attempt = receipt.get("attempt")
    if type(generation) is not int or generation < 1:
        raise ExecutionOwnershipError(
            "Newest execution-attempt receipt generation must be a positive integer"
        )
    if type(attempt) is not int or attempt < 1:
        raise ExecutionOwnershipError(
            "Newest execution-attempt receipt attempt must be a positive integer"
        )

    unit_name = _required_receipt_text(receipt, "unit")
    identity = parse_unit_identity(unit_name)
    if identity.lane != lane or identity.attempt != attempt:
        raise ExecutionOwnershipError(
            "Newest execution-attempt receipt unit identity conflicts with its lane or attempt"
        )

    _required_receipt_text(receipt, "owner_nonce")
    _required_receipt_text(receipt, "request_fingerprint")
    _required_receipt_timestamp(receipt, "planned_at")
    _required_receipt_text(receipt, "invocation_id")
    _required_receipt_timestamp(receipt, "started_at")
    return receipt


def execution_attempt_is_terminal(receipt: Mapping[str, object] | None) -> bool:
    return str((receipt or {}).get("state", "")).strip().lower() in EXECUTION_ATTEMPT_TERMINAL_STATES


def next_execution_identity(params: object, lane: str) -> tuple[int, int]:
    normalized_lane = normalize_lane(lane)
    entries = execution_attempts(params)
    generations = [
        int(entry["generation"])
        for entry in entries
        if str(entry.get("generation", "")).isdigit()
    ]
    attempts = [
        int(entry["attempt"])
        for entry in entries
        if entry.get("lane") == normalized_lane and str(entry.get("attempt", "")).isdigit()
    ]
    return max(generations, default=0) + 1, max(attempts, default=0) + 1


def _receipt_integer(value: object, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def append_execution_attempt(
    params: object,
    receipt: Mapping[str, object],
) -> dict[str, Any]:
    """Append one receipt while preserving every prior receipt byte-for-byte in value."""
    normalized = params_mapping(params)
    prior = normalized.get(EXECUTION_ATTEMPTS_PARAM, [])
    if prior is None:
        prior = []
    if not isinstance(prior, list):
        raise ExecutionOwnershipError(
            f"{EXECUTION_ATTEMPTS_PARAM} must remain an append-only list"
        )
    normalized[EXECUTION_ATTEMPTS_PARAM] = [
        dict(item) if isinstance(item, Mapping) else item for item in prior
    ]
    normalized[EXECUTION_ATTEMPTS_PARAM].append(dict(receipt))
    return normalized


def update_execution_attempt(
    params: object,
    *,
    lane: str,
    generation: int,
    attempt: int,
    unit: str,
    owner_nonce: str,
    changes: Mapping[str, object],
) -> dict[str, Any]:
    """Update mutable receipt state without permitting identity mutation."""
    normalized = params_mapping(params)
    entries = normalized.get(EXECUTION_ATTEMPTS_PARAM, [])
    if not isinstance(entries, list):
        raise ExecutionOwnershipError(
            f"{EXECUTION_ATTEMPTS_PARAM} must remain an append-only list"
        )
    normalized_entries: list[object] = [
        dict(item) if isinstance(item, Mapping) else item for item in entries
    ]
    target_index: int | None = None
    for index in range(len(normalized_entries) - 1, -1, -1):
        candidate = normalized_entries[index]
        if not isinstance(candidate, Mapping):
            continue
        if (
            str(candidate.get("lane", "")) == normalize_lane(lane)
            and _receipt_integer(candidate.get("generation")) == int(generation)
            and _receipt_integer(candidate.get("attempt")) == int(attempt)
            and str(candidate.get("unit", "")) == str(unit)
            and str(candidate.get("owner_nonce", "")) == str(owner_nonce)
        ):
            target_index = index
            break
    if target_index is None:
        raise UnitNotFoundError(
            f"Execution attempt {unit!r} with owner nonce {owner_nonce!r} was not found"
        )
    illegal = EXECUTION_ATTEMPT_IMMUTABLE_FIELDS.intersection(changes)
    for field in illegal:
        if str(changes[field]) != str(normalized_entries[target_index].get(field)):  # type: ignore[union-attr]
            raise ExecutionOwnershipError(f"Execution attempt identity field is immutable: {field}")
    updated: dict[str, Any] = dict(normalized_entries[target_index])  # type: ignore[arg-type]
    current_state = str(updated.get("state", "")).strip().lower()
    requested_state = str(changes.get("state", current_state)).strip().lower()
    if current_state in EXECUTION_ATTEMPT_TERMINAL_STATES and requested_state != current_state:
        raise ExecutionOwnershipError(
            f"Execution attempt is terminal and cannot transition from {current_state!r} to {requested_state!r}"
        )
    updated.update(dict(changes))
    normalized_entries[target_index] = updated
    normalized[EXECUTION_ATTEMPTS_PARAM] = normalized_entries
    return normalized


def strip_execution_metadata(params: object) -> dict[str, Any]:
    """Remove adapter bookkeeping before params enter a workflow command."""
    normalized = params_mapping(params)
    normalized.pop(EXECUTION_ATTEMPTS_PARAM, None)
    normalized.pop("cancellation_receipt", None)
    normalized.pop(SCHEDULER_GPU_ASSIGNMENT_PARAM, None)
    return normalized


def planned_execution_attempt(
    *,
    lane: str,
    job_id: str,
    generation: int,
    attempt: int,
    unit: str,
    owner_nonce: str,
    request_fingerprint_value: str,
    planned_at: str | None = None,
) -> dict[str, Any]:
    identity = unit_identity(lane, job_id, attempt)
    if unit != identity.unit_name:
        raise LaneMismatchError(
            f"Execution attempt unit {unit!r} does not match {identity.unit_name!r}"
        )
    return {
        "schema": EXECUTION_ATTEMPT_SCHEMA,
        "state": "planned",
        "lane": identity.lane,
        "generation": int(generation),
        "attempt": identity.attempt,
        "unit": identity.unit_name,
        "owner_nonce": str(owner_nonce),
        "request_fingerprint": str(request_fingerprint_value),
        "planned_at": planned_at or utc_timestamp(),
    }


def _claim_lock_path(state_dir: Path | str, lane: str, job_id: str) -> Path:
    root = Path(state_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    token = re.sub(r"[^A-Za-z0-9_.:-]", "_", str(job_id or "").strip())
    if not token:
        raise ExecutionOwnershipError("Workflow job id is required for a claim lock")
    return root / f".workflow-{normalize_lane(lane)}-{token}.lock"


def acquire_workflow_claim_lock(state_dir: Path | str, lane: str, job_id: str):
    """Acquire a cross-process lane/job claim lock without polling systemd."""
    import fcntl

    handle = _claim_lock_path(state_dir, lane, job_id).open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except Exception:
        handle.close()
        raise
    return handle


def release_workflow_claim_lock(handle) -> None:
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


@contextmanager
def workflow_claim_lock(state_dir: Path | str, lane: str, job_id: str):
    """Serialize receipt planning across adapter processes for one lane/job."""
    handle = acquire_workflow_claim_lock(state_dir, lane, job_id)
    try:
        yield
    finally:
        release_workflow_claim_lock(handle)


def parse_unit_identity(unit_name: str) -> UnitIdentity:
    match = _UNIT_NAME_RE.fullmatch(str(unit_name or ""))
    if match is None:
        raise LaneMismatchError(f"Not a BioModStack workflow unit: {unit_name!r}")
    lane = normalize_lane(match.group(1))
    job_id = match.group("job")
    attempt = int(match.group("attempt"))
    return UnitIdentity(
        lane=lane,
        job_id=job_id,
        attempt=attempt,
        unit_name=str(unit_name),
        slice_name=workflow_slice_for_lane(lane),
    )


def assert_unit_lane(unit_name: str, lane: str) -> UnitIdentity:
    identity = parse_unit_identity(unit_name)
    normalized_lane = normalize_lane(lane)
    if identity.lane != normalized_lane:
        raise LaneMismatchError(
            f"Workflow unit {unit_name!r} belongs to {identity.lane}, not {normalized_lane}"
        )
    return identity


def workflow_nvidia_device_allow_paths(gpu_index: int) -> tuple[str, ...]:
    """Resolve the bounded NVIDIA character-device allowlist for one GPU."""
    if type(gpu_index) is not int or gpu_index < 0:
        raise ExecutionOwnershipError("workflow GPU device index is invalid")
    required = [
        Path(f"/dev/nvidia{gpu_index}"),
        Path("/dev/nvidiactl"),
        Path("/dev/nvidia-uvm"),
    ]
    optional = [Path("/dev/nvidia-uvm-tools")]
    paths = required + [path for path in optional if path.exists()]
    for path in paths:
        try:
            mode = path.stat().st_mode
        except OSError as exc:
            raise ExecutionOwnershipError(f"required NVIDIA device is unavailable: {path}") from exc
        if not stat.S_ISCHR(mode):
            raise ExecutionOwnershipError(f"NVIDIA device authority is not a character device: {path}")
    return tuple(str(path) for path in paths)


def build_systemd_run_command(
    *,
    lane: str,
    job_id: str,
    attempt: int,
    command: Sequence[str],
    environment: Mapping[str, object] | None = None,
    working_directory: Path | str | None = None,
    log_path: Path | str | None = None,
    cpu_threads: int | None = None,
    memory_max_bytes: int | None = None,
    deny_physical_devices: bool = False,
    allowed_physical_devices: Sequence[str] | None = None,
) -> list[str]:
    """Build the only supported command shape for a new workflow job."""
    identity = unit_identity(lane, job_id, attempt)
    if not command:
        raise ExecutionOwnershipError("A workflow command is required")
    if cpu_threads is not None and (
        type(cpu_threads) is not int or not 1 <= cpu_threads <= 24
    ):
        raise ExecutionOwnershipError("workflow CPU admission must be between 1 and 24 threads")
    if memory_max_bytes is not None and (
        type(memory_max_bytes) is not int or not 1 <= memory_max_bytes <= 96 * 1024**3
    ):
        raise ExecutionOwnershipError("workflow memory admission must be between 1 byte and 96 GiB")
    if type(deny_physical_devices) is not bool:
        raise ExecutionOwnershipError("workflow device-denial policy must be boolean")
    allowed_devices: tuple[str, ...] | None = None
    if allowed_physical_devices is not None:
        if deny_physical_devices:
            raise ExecutionOwnershipError("workflow device authority cannot deny and allow physical devices")
        allowed_devices = tuple(str(item) for item in allowed_physical_devices)
        if (
            not allowed_devices
            or len(allowed_devices) > 132
            or len(set(allowed_devices)) != len(allowed_devices)
            or any(not item.startswith("/dev/") or any(character.isspace() for character in item) for item in allowed_devices)
        ):
            raise ExecutionOwnershipError("workflow physical-device allowlist is malformed")
    cpu_quota = f"{cpu_threads * 100}%" if cpu_threads is not None else JOB_CPU_QUOTA
    memory_max = str(memory_max_bytes) if memory_max_bytes is not None else WORKFLOW_MEMORY_MAX

    rendered = [
        "systemd-run",
        "--user",
        "--no-block",
        f"--unit={identity.unit_name}",
        f"--slice={identity.slice_name}",
        "--property=Type=exec",
        "--property=KillMode=control-group",
        "--property=CPUAccounting=yes",
        "--property=MemoryAccounting=yes",
        "--property=TasksAccounting=yes",
        f"--property=CPUQuota={cpu_quota}",
        f"--property=MemoryMax={memory_max}",
    ]
    if deny_physical_devices or allowed_devices is not None:
        rendered.append("--property=DevicePolicy=closed")
        if deny_physical_devices:
            rendered.append("--property=DeviceAllow=")
        else:
            rendered.extend(
                f"--property=DeviceAllow={device} rw" for device in allowed_devices or ()
            )
    if working_directory is not None:
        rendered.append(f"--working-directory={Path(working_directory).resolve()}")
    if log_path is not None:
        resolved_log = Path(log_path).resolve()
        rendered.extend(
            [
                f"--property=StandardOutput=append:{resolved_log}",
                f"--property=StandardError=append:{resolved_log}",
            ]
        )
    for key, value in sorted((environment or {}).items()):
        if value is None or not _ENV_NAME_RE.fullmatch(str(key)):
            continue
        rendered.append(f"--setenv={key}={value}")
    rendered.extend(["--", *(str(item) for item in command)])
    return rendered


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True)


def create_systemd_workflow_unit(command: Sequence[str]) -> str:
    """Atomically claim a deterministic unit through systemd-run."""
    completed = _run_command(command)
    if completed.returncode == 0:
        try:
            unit_index = list(command).index("--unit")
            return str(command[unit_index + 1])
        except (ValueError, IndexError):
            for item in command:
                if item.startswith("--unit="):
                    return item.split("=", 1)[1]
            raise ExecutionOwnershipError("systemd-run command omitted a deterministic unit")

    detail = "\n".join(item for item in (completed.stdout, completed.stderr) if item).strip()
    lowered = detail.lower()
    if any(marker in lowered for marker in ("already exists", "already loaded", "unit exists", "file exists")):
        raise DuplicateUnitError(detail or "deterministic workflow unit already exists")
    raise SystemdCommandError(detail or f"systemd-run exited with {completed.returncode}")


_SHOW_PROPERTIES = (
    "LoadState",
    "ActiveState",
    "SubState",
    "ControlGroup",
    "MainPID",
    "ExecMainStatus",
    "Result",
    "Slice",
    "InvocationID",
    "CPUQuotaPerSecUSec",
    "MemoryMax",
    "CPUAccounting",
    "MemoryAccounting",
    "TasksAccounting",
    "DevicePolicy",
    "DeviceAllow",
)


def _parse_systemd_properties(raw: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in str(raw or "").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


def show_unit_properties(unit_name: str, lane: str) -> UnitProperties:
    identity = assert_unit_lane(unit_name, lane)
    command = [
        "systemctl",
        "--user",
        "show",
        identity.unit_name,
        "--no-pager",
        f"--property={','.join(_SHOW_PROPERTIES)}",
    ]
    completed = _run_command(command)
    if completed.returncode != 0:
        raise SystemdCommandError(
            f"Could not inspect workflow unit {identity.unit_name}: "
            f"{(completed.stderr or completed.stdout).strip()}"
        )
    properties = UnitProperties.from_mapping(_parse_systemd_properties(completed.stdout))
    if properties.load_state == "not-found":
        raise UnitNotFoundError(f"Workflow unit {identity.unit_name} is explicitly absent")
    if properties.slice_name != identity.slice_name:
        raise LaneMismatchError(
            f"Workflow unit {identity.unit_name} has slice {properties.slice_name!r}; "
            f"expected {identity.slice_name!r}"
        )
    return properties


def unit_has_empty_cgroup(
    properties: UnitProperties | Mapping[str, object],
    *,
    cgroup_root: Path = CGROUP_ROOT,
) -> bool:
    if isinstance(properties, UnitProperties):
        control_group = properties.control_group
    else:
        control_group = str(properties.get("ControlGroup", ""))
    normalized = control_group.strip()
    if not normalized:
        return True
    root = cgroup_root.resolve()
    candidate = (root / normalized.lstrip("/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise LaneMismatchError(f"ControlGroup escapes cgroup root: {control_group!r}") from exc
    try:
        process_ids = (candidate / "cgroup.procs").read_text(encoding="utf-8").split()
    except (FileNotFoundError, PermissionError, OSError):
        return False
    return not process_ids


def unit_is_inactive_with_empty_cgroup(
    properties: UnitProperties | Mapping[str, object],
    *,
    cgroup_root: Path = CGROUP_ROOT,
) -> bool:
    active_state = (
        properties.active_state
        if isinstance(properties, UnitProperties)
        else str(properties.get("ActiveState", ""))
    )
    return active_state == "inactive" and unit_has_empty_cgroup(
        properties,
        cgroup_root=cgroup_root,
    )


def _systemctl_user(*args: str) -> subprocess.CompletedProcess[str]:
    return _run_command(["systemctl", "--user", *args])


def _stop_unit(unit_name: str) -> None:
    _systemctl_user("stop", unit_name)


def _kill_unit(unit_name: str) -> None:
    _systemctl_user("kill", "--kill-who=all", "--signal=SIGKILL", unit_name)


def cancel_systemd_workflow_unit(
    unit_name: str,
    lane: str,
    *,
    graceful_timeout_seconds: float = 5.0,
    poll_interval_seconds: float = 0.25,
    kill_timeout_seconds: float = 8.0,
) -> bool:
    """Cancel only after systemd proves inactive state and an empty cgroup."""
    assert_unit_lane(unit_name, lane)
    try:
        initial = show_unit_properties(unit_name, lane)
    except UnitNotFoundError:
        return True
    if unit_is_inactive_with_empty_cgroup(initial):
        return True

    try:
        _stop_unit(unit_name)
    except ExecutionOwnershipError:
        # Property polling below remains the authority. A failed stop command
        # cannot be treated as cancellation success.
        pass

    def wait_for_proof(deadline: float) -> bool:
        while time.monotonic() <= deadline:
            try:
                properties = show_unit_properties(unit_name, lane)
            except UnitNotFoundError:
                return True
            if unit_is_inactive_with_empty_cgroup(properties):
                return True
            time.sleep(max(0.0, poll_interval_seconds))
        return False

    if wait_for_proof(time.monotonic() + max(0.0, float(graceful_timeout_seconds))):
        return True

    try:
        _kill_unit(unit_name)
    except ExecutionOwnershipError:
        pass
    return wait_for_proof(time.monotonic() + max(0.0, float(kill_timeout_seconds)))


def wait_for_unit_invocation(
    unit_name: str,
    lane: str,
    *,
    timeout_seconds: float = 10.0,
    poll_interval_seconds: float = 0.05,
) -> UnitProperties:
    """Read the exact systemd InvocationID after a transient unit is accepted."""
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    last_properties: UnitProperties | None = None
    while time.monotonic() <= deadline:
        last_properties = show_unit_properties(unit_name, lane)
        if last_properties.invocation_id:
            return last_properties
        time.sleep(max(0.0, float(poll_interval_seconds)))
    if last_properties is None:
        raise UnitNotFoundError(f"Could not inspect workflow unit {unit_name}")
    raise ExecutionOwnershipError(
        f"Workflow unit {unit_name} was accepted without an exact InvocationID"
    )


def _list_workflow_unit_names(lane: str) -> list[str]:
    normalized_lane = normalize_lane(lane)
    pattern = f"{UNIT_PREFIX}-{normalized_lane}-job-*.service"
    completed = _systemctl_user("list-units", "--all", "--no-legend", "--no-pager", pattern)
    if completed.returncode != 0:
        raise SystemdCommandError((completed.stderr or completed.stdout).strip() or "systemd unit discovery failed")
    names: list[str] = []
    for line in completed.stdout.splitlines():
        fields = line.split()
        if not fields:
            continue
        candidate = fields[0]
        try:
            assert_unit_lane(candidate, normalized_lane)
        except LaneMismatchError as exc:
            raise LaneMismatchError(str(exc)) from exc
        names.append(candidate)
    return sorted(set(names))


def discover_active_workflow_units(lane: str) -> dict[str, str]:
    """Discover new jobs only through exact systemd unit names and properties."""
    normalized_lane = normalize_lane(lane)
    discovered: dict[str, str] = {}
    for unit_name in _list_workflow_unit_names(normalized_lane):
        properties = show_unit_properties(unit_name, normalized_lane)
        if properties.active_state not in {"active", "activating", "reloading"}:
            continue
        identity = parse_unit_identity(unit_name)
        existing = discovered.get(identity.job_id)
        if existing is not None and existing != unit_name:
            raise DuplicateUnitError(
                f"Multiple active deterministic units claim job {identity.job_id} in {normalized_lane}"
            )
        discovered[identity.job_id] = unit_name
    return discovered


def owner_receipt(
    *,
    lane: str,
    job_id: str,
    attempt: int,
    unit_name: str,
    command: Sequence[str],
) -> dict[str, Any]:
    identity = unit_identity(lane, job_id, attempt)
    if unit_name != identity.unit_name:
        raise LaneMismatchError(
            f"Owner receipt unit {unit_name!r} does not match deterministic unit {identity.unit_name!r}"
        )
    return {
        "owner": "systemd-user",
        "lane": identity.lane,
        "unit": identity.unit_name,
        "slice": identity.slice_name,
        "job_id": identity.job_id,
        "attempt": identity.attempt,
        "type": "exec",
        "kill_mode": "control-group",
        "cpu_quota": JOB_CPU_QUOTA,
        "command": [str(item) for item in command],
    }


def is_legacy_numeric_run_id(value: object) -> bool:
    return bool(re.fullmatch(r"[1-9][0-9]*", str(value or "").strip()))
