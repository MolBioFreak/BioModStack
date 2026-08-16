"""Producer-owned resource-use evidence for one systemd workflow unit."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Any, Mapping

from services.execution_ownership import (
    ExecutionOwnershipError,
    TRANSIENT_WORKFLOW_UNIT_NAME_ENV,
    TRANSIENT_WORKFLOW_OWNER_NONCE_ENV,
    params_mapping,
    show_unit_properties,
    workflow_nvidia_device_allow_paths,
)


GLOBAL_RESOURCE_ADMISSION_PARAM = "_global_resource_admission"
GLOBAL_RESOURCE_ADMISSION_SCHEMA = "bms.global-resource-admission-handoff.v1"
GLOBAL_DISPATCH_AUTHORITY_PARAM = "_global_dispatch_authority"
GLOBAL_DISPATCH_AUTHORITY_SCHEMA = "bms.global-dispatch-materialization.v1"
RESOURCE_USAGE_RECEIPTS_PARAM = "resource_usage_receipts"
RESOURCE_USAGE_RECEIPT_SCHEMA = "bms.workflow-resource-usage.v1"
RESOURCE_CHECKPOINT_SCHEMA = "bms.workflow-resource-usage-checkpoint.v1"
_RESOURCE_POLL_INTERVAL_SECONDS = 0.2
_MAX_GPU_ROWS = 32
_MAX_CHECKPOINT_BYTES = 256 * 1024
_HANDOFF_KEYS = frozenset({
    "schema", "admission_id", "run_attempt_id", "canonical_job_id",
    "preparation_id", "cpu_threads", "dram_bytes", "gpu_index", "gpu_uuid",
    "policy_source", "policy_version", "owner", "lease_token_sha256",
    "source_revision", "source_tree", "handoff_sha256",
})


class ResourceUsageEvidenceError(RuntimeError):
    """Resource-use authority is absent, malformed, or inconsistent."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _device_allow_paths(value: str) -> tuple[str, ...]:
    paths = re.findall(r"/dev/[^\s;]+", str(value or ""))
    return tuple(sorted(set(paths)))


def _expected_device_allow_paths(handoff: Mapping[str, Any]) -> tuple[str, ...]:
    gpu_index = handoff.get("gpu_index")
    if gpu_index is None:
        return ()
    try:
        return workflow_nvidia_device_allow_paths(gpu_index)
    except ExecutionOwnershipError as exc:
        raise ResourceUsageEvidenceError(
            "authoritative NVIDIA device allowlist is unavailable"
        ) from exc


def _validated_recorded_device_allow_paths(
    handoff: Mapping[str, Any],
    value: Any,
) -> list[str]:
    if not isinstance(value, list) or len(value) > 132 or any(
        not isinstance(path, str) or not path for path in value
    ):
        raise ResourceUsageEvidenceError("producer device allowlist is invalid")
    paths = sorted(set(value))
    if paths != value:
        raise ResourceUsageEvidenceError("producer device allowlist is not canonical")
    gpu_index = handoff.get("gpu_index")
    if gpu_index is None:
        if paths:
            raise ResourceUsageEvidenceError("CPU-only producer receipt permits physical devices")
        return paths
    required = {f"/dev/nvidia{gpu_index}", "/dev/nvidiactl", "/dev/nvidia-uvm"}
    if not required.issubset(paths):
        raise ResourceUsageEvidenceError("GPU producer receipt omits required NVIDIA devices")
    allowed = required | {"/dev/nvidia-uvm-tools"}
    if len(paths) > len(allowed) or any(path not in allowed for path in paths):
        raise ResourceUsageEvidenceError("GPU producer receipt permits an unexpected physical device")
    return paths


def _required_text(value: Mapping[str, Any], key: str, *, maximum: int = 512) -> str:
    candidate = value.get(key)
    if not isinstance(candidate, str) or not candidate or len(candidate) > maximum:
        raise ResourceUsageEvidenceError(f"resource admission field {key!r} is invalid")
    return candidate


def validate_resource_admission_handoff(value: object) -> dict[str, Any] | None:
    """Validate a server-owned admission handoff stored on a canonical Job."""

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ResourceUsageEvidenceError("global resource admission handoff must be an object")
    handoff = {str(key): child for key, child in value.items()}
    if set(handoff) != _HANDOFF_KEYS:
        raise ResourceUsageEvidenceError("global resource admission handoff fields are not exact")
    if handoff.get("schema") != GLOBAL_RESOURCE_ADMISSION_SCHEMA:
        raise ResourceUsageEvidenceError("global resource admission handoff schema is invalid")
    for key in (
        "admission_id",
        "run_attempt_id",
        "canonical_job_id",
        "preparation_id",
        "policy_source",
        "policy_version",
        "owner",
        "lease_token_sha256",
        "source_revision",
        "source_tree",
    ):
        _required_text(handoff, key)
    for key in ("source_revision", "source_tree"):
        source_id = handoff[key]
        if len(source_id) != 40 or any(character not in "0123456789abcdef" for character in source_id):
            raise ResourceUsageEvidenceError("resource admission source identity is invalid")
    cpu_threads = handoff.get("cpu_threads")
    dram_bytes = handoff.get("dram_bytes")
    gpu_index = handoff.get("gpu_index")
    gpu_uuid = handoff.get("gpu_uuid")
    if type(cpu_threads) is not int or not 1 <= cpu_threads <= 24:
        raise ResourceUsageEvidenceError("resource admission cpu_threads is invalid")
    if type(dram_bytes) is not int or not 1 <= dram_bytes <= 96 * 1024**3:
        raise ResourceUsageEvidenceError("resource admission dram_bytes is invalid")
    if gpu_index is not None and (type(gpu_index) is not int or gpu_index < 0):
        raise ResourceUsageEvidenceError("resource admission gpu_index is invalid")
    if (gpu_index is None) != (gpu_uuid is None):
        raise ResourceUsageEvidenceError("resource admission GPU index and UUID must be paired")
    if gpu_uuid is not None:
        _required_text(handoff, "gpu_uuid", maximum=255)
    digest = _required_text(handoff, "handoff_sha256", maximum=64)
    lease_digest = handoff["lease_token_sha256"]
    if (
        len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        or len(lease_digest) != 64
        or any(character not in "0123456789abcdef" for character in lease_digest)
    ):
        raise ResourceUsageEvidenceError("global resource admission digest syntax is invalid")
    unsigned = dict(handoff)
    unsigned.pop("handoff_sha256", None)
    if digest != _sha256(_canonical_json(unsigned)):
        raise ResourceUsageEvidenceError("global resource admission handoff digest is invalid")
    return handoff


def build_dispatch_materialization_authority(
    *,
    payload_sha256: str,
    handoff: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind canonical Job params to the exact sealed dispatch envelope."""

    validated_handoff = validate_resource_admission_handoff(handoff)
    if validated_handoff is None:
        raise ResourceUsageEvidenceError("dispatch materialization requires resource admission authority")
    authority: dict[str, Any] = {
        "schema": GLOBAL_DISPATCH_AUTHORITY_SCHEMA,
        "payload_sha256": payload_sha256,
        "run_attempt_id": validated_handoff["run_attempt_id"],
        "canonical_job_id": validated_handoff["canonical_job_id"],
        "admission_handoff_sha256": validated_handoff["handoff_sha256"],
    }
    authority["authority_sha256"] = _sha256(_canonical_json(authority))
    return validate_dispatch_materialization_authority(authority, expected_handoff=validated_handoff)


def validate_dispatch_materialization_authority(
    value: object,
    *,
    expected_handoff: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ResourceUsageEvidenceError("global dispatch materialization authority must be an object")
    authority = {str(key): child for key, child in value.items()}
    if set(authority) != {
        "schema", "payload_sha256", "run_attempt_id", "canonical_job_id",
        "admission_handoff_sha256", "authority_sha256",
    } or authority.get("schema") != GLOBAL_DISPATCH_AUTHORITY_SCHEMA:
        raise ResourceUsageEvidenceError("global dispatch materialization authority fields are not exact")
    for key in (
        "payload_sha256", "run_attempt_id", "canonical_job_id",
        "admission_handoff_sha256", "authority_sha256",
    ):
        _required_text(authority, key)
    for key in ("payload_sha256", "admission_handoff_sha256", "authority_sha256"):
        candidate = authority[key]
        if len(candidate) != 64 or any(character not in "0123456789abcdef" for character in candidate):
            raise ResourceUsageEvidenceError("global dispatch materialization digest syntax is invalid")
    unsigned = dict(authority)
    digest = unsigned.pop("authority_sha256")
    if digest != _sha256(_canonical_json(unsigned)):
        raise ResourceUsageEvidenceError("global dispatch materialization authority digest is invalid")
    if expected_handoff is not None:
        handoff = validate_resource_admission_handoff(expected_handoff)
        if handoff is None or (
            authority["run_attempt_id"] != handoff["run_attempt_id"]
            or authority["canonical_job_id"] != handoff["canonical_job_id"]
            or authority["admission_handoff_sha256"] != handoff["handoff_sha256"]
        ):
            raise ResourceUsageEvidenceError("global dispatch materialization authority diverges from admission")
    return authority


def build_resource_admission_handoff(
    *,
    admission_id: str,
    run_attempt_id: str,
    canonical_job_id: str,
    preparation_id: str,
    cpu_threads: int,
    dram_bytes: int,
    gpu_index: int | None,
    gpu_uuid: str | None,
    policy_source: str,
    policy_version: str,
    owner: str,
    lease_token: str,
    source_revision: str,
    source_tree: str,
) -> dict[str, Any]:
    handoff: dict[str, Any] = {
        "schema": GLOBAL_RESOURCE_ADMISSION_SCHEMA,
        "admission_id": admission_id,
        "run_attempt_id": run_attempt_id,
        "canonical_job_id": canonical_job_id,
        "preparation_id": preparation_id,
        "cpu_threads": cpu_threads,
        "dram_bytes": dram_bytes,
        "gpu_index": gpu_index,
        "gpu_uuid": gpu_uuid,
        "policy_source": policy_source,
        "policy_version": policy_version,
        "owner": owner,
        "lease_token_sha256": _sha256(lease_token),
        "source_revision": source_revision,
        "source_tree": source_tree,
    }
    handoff["handoff_sha256"] = _sha256(_canonical_json(handoff))
    validate_resource_admission_handoff(handoff)
    return handoff


def attach_resource_admission_handoff(params: object, handoff: Mapping[str, Any]) -> dict[str, Any]:
    normalized = params_mapping(params)
    validated = validate_resource_admission_handoff(handoff)
    if validated is None:
        raise ResourceUsageEvidenceError("resource admission handoff is required")
    existing = normalized.get(GLOBAL_RESOURCE_ADMISSION_PARAM)
    if existing is not None and existing != validated:
        raise ResourceUsageEvidenceError("canonical Job already has different resource admission authority")
    normalized[GLOBAL_RESOURCE_ADMISSION_PARAM] = validated
    return normalized


def attach_dispatch_materialization_authority(
    params: object,
    authority: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = params_mapping(params)
    handoff = validate_resource_admission_handoff(normalized.get(GLOBAL_RESOURCE_ADMISSION_PARAM))
    if handoff is None:
        raise ResourceUsageEvidenceError("canonical Job resource admission authority is required")
    validated = validate_dispatch_materialization_authority(authority, expected_handoff=handoff)
    existing = normalized.get(GLOBAL_DISPATCH_AUTHORITY_PARAM)
    if existing is not None and existing != validated:
        raise ResourceUsageEvidenceError("canonical Job already has different dispatch authority")
    normalized[GLOBAL_DISPATCH_AUTHORITY_PARAM] = validated
    return normalized


def strip_resource_execution_metadata(params: object) -> dict[str, Any]:
    normalized = params_mapping(params)
    normalized.pop(GLOBAL_RESOURCE_ADMISSION_PARAM, None)
    normalized.pop(GLOBAL_DISPATCH_AUTHORITY_PARAM, None)
    normalized.pop(RESOURCE_USAGE_RECEIPTS_PARAM, None)
    return normalized


def _read_int(path: Path) -> int:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw or raw == "max":
        raise ValueError(f"no numeric value at {path.name}")
    return int(raw)


def _systemd_duration_usec(value: str) -> int:
    token = str(value or "").strip()
    for suffix, multiplier in (("ms", 1_000), ("us", 1), ("s", 1_000_000)):
        if token.endswith(suffix):
            try:
                return int(float(token[: -len(suffix)]) * multiplier)
            except ValueError as exc:
                raise ResourceUsageEvidenceError("systemd CPU quota is malformed") from exc
    try:
        return int(token)
    except ValueError as exc:
        raise ResourceUsageEvidenceError("systemd CPU quota is malformed") from exc


def _read_cpu_stat(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2:
            values[parts[0]] = int(parts[1])
    required = {"usage_usec", "user_usec", "system_usec", "nr_periods", "nr_throttled", "throttled_usec"}
    if not required.issubset(values):
        raise ValueError("cpu.stat lacks required cgroup-v2 accounting fields")
    return {key: values[key] for key in sorted(required)}


def _cgroup_pids(cgroup_path: Path) -> set[int]:
    pids: set[int] = set()
    for procs in cgroup_path.rglob("cgroup.procs"):
        for raw in procs.read_text(encoding="utf-8").splitlines():
            if raw.isdigit():
                pids.add(int(raw))
    return pids


def _current_unified_cgroup() -> str:
    matches: list[str] = []
    for line in Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines():
        hierarchy, controllers, separator_path = line.split(":", 2)
        if hierarchy == "0" and controllers == "" and separator_path.startswith("/"):
            matches.append(separator_path)
    if len(matches) != 1:
        raise ResourceUsageEvidenceError("runner has no exact unified cgroup authority")
    return matches[0]


def _cgroup_directory(control_group: str) -> Path:
    root = Path("/sys/fs/cgroup").resolve()
    candidate = (root / control_group.lstrip("/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ResourceUsageEvidenceError("systemd cgroup authority escapes the cgroup root") from exc
    return candidate


def _gpu_rows_for_pids(pids: set[int]) -> list[dict[str, Any]]:
    if not pids:
        return []
    completed = subprocess.run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,gpu_uuid,used_gpu_memory",
            "--format=csv,noheader,nounits",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
    )
    if completed.returncode != 0:
        raise ResourceUsageEvidenceError("nvidia-smi resource query failed")
    rows: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 3 or not parts[0].isdigit():
            raise ResourceUsageEvidenceError("nvidia-smi returned malformed process identity")
        pid = int(parts[0])
        if pid not in pids:
            continue
        if not parts[1]:
            raise ResourceUsageEvidenceError("nvidia-smi returned empty GPU UUID")
        try:
            used_bytes = int(parts[2]) * 1024 * 1024
        except ValueError as exc:
            raise ResourceUsageEvidenceError("nvidia-smi returned invalid used_memory") from exc
        if used_bytes < 0:
            raise ResourceUsageEvidenceError("nvidia-smi returned negative used_memory")
        rows.append({"pid": pid, "gpu_uuid": parts[1], "used_memory_bytes": used_bytes})
    if len(rows) > _MAX_GPU_ROWS:
        raise ResourceUsageEvidenceError("GPU process evidence exceeds its bounded row limit")
    return rows


def _gpu_process_peak_rows(peaks: Mapping[tuple[int, str], int]) -> list[dict[str, Any]]:
    return [
        {"pid": pid, "gpu_uuid": gpu_uuid, "used_memory_bytes": used_memory_bytes}
        for (pid, gpu_uuid), used_memory_bytes in sorted(peaks.items())
    ]


def _gpu_uuid_peaks(peaks: Mapping[tuple[int, str], int]) -> dict[str, int]:
    by_uuid: dict[str, int] = {}
    for (_pid, gpu_uuid), used_memory_bytes in peaks.items():
        by_uuid[gpu_uuid] = max(by_uuid.get(gpu_uuid, 0), used_memory_bytes)
    return dict(sorted(by_uuid.items()))


def _validate_gpu_process_peak_rows(value: object) -> dict[tuple[int, str], int]:
    if not isinstance(value, list):
        raise ResourceUsageEvidenceError("GPU process evidence must be a list")
    if len(value) > _MAX_GPU_ROWS:
        raise ResourceUsageEvidenceError("GPU process evidence exceeds its bounded row limit")
    peaks: dict[tuple[int, str], int] = {}
    for row in value:
        if not isinstance(row, Mapping) or set(row) != {"pid", "gpu_uuid", "used_memory_bytes"}:
            raise ResourceUsageEvidenceError("GPU process evidence fields are not exact")
        pid = row.get("pid")
        gpu_uuid = row.get("gpu_uuid")
        used_memory_bytes = row.get("used_memory_bytes")
        if (
            type(pid) is not int
            or pid < 1
            or not isinstance(gpu_uuid, str)
            or not gpu_uuid
            or len(gpu_uuid) > 255
            or type(used_memory_bytes) is not int
            or used_memory_bytes < 0
        ):
            raise ResourceUsageEvidenceError("GPU process evidence values are invalid")
        key = (pid, gpu_uuid)
        if key in peaks:
            raise ResourceUsageEvidenceError("GPU process evidence identity is duplicated")
        peaks[key] = used_memory_bytes
    return peaks


def _checkpoint_path(job_id: str, generation: int, attempt: int) -> Path:
    state_dir = Path(os.environ["BMS_STATE_DIR"]).resolve()
    token = "".join(character if character.isalnum() or character in "-_." else "_" for character in job_id)
    return state_dir / "resource-usage" / f"{token}-{generation}-{attempt}.json"


@dataclass
class WorkflowResourceMonitor:
    job_id: str
    lane: str
    generation: int
    attempt: int
    unit_name: str
    owner_nonce: str
    expected_invocation_id: str
    handoff: dict[str, Any]
    dispatch_authority: dict[str, Any]
    started_at: str = field(default_factory=_timestamp)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _control_group: str = ""
    _invocation_id: str = ""
    _samples: int = 0
    _monitor_failures: int = 0
    _gpu_peak_by_pid_uuid: dict[tuple[int, str], int] = field(default_factory=dict)
    _gpu_observed: bool = False
    _enforcement: dict[str, Any] = field(default_factory=dict)
    _last_accounting: dict[str, Any] | None = None

    @classmethod
    def from_job(cls, job: Any) -> "WorkflowResourceMonitor | None":
        params = params_mapping(getattr(job, "params", {}))
        handoff = validate_resource_admission_handoff(params.get(GLOBAL_RESOURCE_ADMISSION_PARAM))
        if handoff is None:
            return None
        dispatch_authority = validate_dispatch_materialization_authority(
            params.get(GLOBAL_DISPATCH_AUTHORITY_PARAM),
            expected_handoff=handoff,
        )
        if handoff["canonical_job_id"] != str(job.id):
            raise ResourceUsageEvidenceError("resource admission handoff binds another Job")
        attempts = params.get("execution_attempts")
        if not isinstance(attempts, list) or not attempts or not isinstance(attempts[-1], Mapping):
            raise ResourceUsageEvidenceError("resource monitor has no started execution-attempt authority")
        execution = attempts[-1]
        generation = execution.get("generation")
        attempt = execution.get("attempt")
        lane = execution.get("lane")
        unit_name = os.getenv(TRANSIENT_WORKFLOW_UNIT_NAME_ENV, "")
        owner_nonce = os.getenv(TRANSIENT_WORKFLOW_OWNER_NONCE_ENV, "")
        if type(generation) is not int or type(attempt) is not int or generation < 1 or attempt < 1:
            raise ResourceUsageEvidenceError("resource monitor execution identity is invalid")
        if lane not in {"development", "production"}:
            raise ResourceUsageEvidenceError("resource monitor execution lane is invalid")
        if execution.get("unit") != unit_name or execution.get("owner_nonce") != owner_nonce:
            raise ResourceUsageEvidenceError("resource monitor environment disagrees with Job execution authority")
        expected_invocation_id = execution.get("invocation_id")
        if not isinstance(expected_invocation_id, str) or not expected_invocation_id:
            raise ResourceUsageEvidenceError("resource monitor execution InvocationID is absent")
        return cls(
            job_id=str(job.id),
            lane=lane,
            generation=generation,
            attempt=attempt,
            unit_name=unit_name,
            owner_nonce=owner_nonce,
            expected_invocation_id=expected_invocation_id,
            handoff=handoff,
            dispatch_authority=dispatch_authority,
        )

    def start(self) -> None:
        properties = show_unit_properties(self.unit_name, self.lane)
        if not properties.control_group or not properties.invocation_id:
            raise ResourceUsageEvidenceError("systemd unit lacks cgroup or InvocationID authority")
        process_cgroup = _current_unified_cgroup()
        cgroup = _cgroup_directory(properties.control_group)
        cgroup_pids = _cgroup_pids(cgroup)
        environment_invocation_id = str(os.getenv("INVOCATION_ID") or "")
        expected_cpu_usec = int(self.handoff["cpu_threads"]) * 1_000_000
        expected_memory = int(self.handoff["dram_bytes"])
        expected_gpu_visibility = "" if self.handoff["gpu_index"] is None else str(self.handoff["gpu_index"])
        actual_gpu_visibility = os.environ.get("CUDA_VISIBLE_DEVICES")
        try:
            actual_cpu_usec = _systemd_duration_usec(properties.cpu_quota_per_sec_usec)
            actual_memory = int(properties.memory_max)
        except (ResourceUsageEvidenceError, ValueError):
            actual_cpu_usec = -1
            actual_memory = -1
        invocation_id_matches = (
            properties.invocation_id
            == self.expected_invocation_id
            == environment_invocation_id
        )
        control_group_matches = properties.control_group == process_cgroup
        main_pid_matches = properties.main_pid.isdigit() and int(properties.main_pid) == os.getpid()
        runner_in_cgroup = os.getpid() in cgroup_pids
        actual_device_allow_paths = _device_allow_paths(properties.device_allow)
        expected_device_allow_paths = _expected_device_allow_paths(self.handoff)
        device_allow_exact = (
            properties.device_policy == "closed"
            and actual_device_allow_paths == tuple(sorted(expected_device_allow_paths))
        )
        cpu_only_device_denial = self.handoff["gpu_index"] is None and device_allow_exact
        gpu_device_denial = self.handoff["gpu_index"] is not None and device_allow_exact
        self._enforcement = {
            "cpu_accounting": properties.cpu_accounting == "yes",
            "memory_accounting": properties.memory_accounting == "yes",
            "tasks_accounting": properties.tasks_accounting == "yes",
            "cpu_quota_per_sec_usec": actual_cpu_usec,
            "memory_max_bytes": actual_memory,
            "expected_cpu_quota_per_sec_usec": expected_cpu_usec,
            "expected_memory_max_bytes": expected_memory,
            "cuda_visible_devices": actual_gpu_visibility,
            "expected_cuda_visible_devices": expected_gpu_visibility,
            "invocation_id_matches": invocation_id_matches,
            "control_group_matches": control_group_matches,
            "main_pid_matches": main_pid_matches,
            "runner_in_cgroup": runner_in_cgroup,
            "device_policy": properties.device_policy,
            "device_allow_is_empty": not properties.device_allow.strip(),
            "device_allow_paths": list(actual_device_allow_paths),
            "expected_device_allow_paths": list(expected_device_allow_paths),
            "device_allow_exact": device_allow_exact,
            "cpu_only_device_denial": cpu_only_device_denial,
            "gpu_device_denial": gpu_device_denial,
        }
        mismatches = [
            key
            for key, matches in (
                ("CPUAccounting", self._enforcement["cpu_accounting"]),
                ("MemoryAccounting", self._enforcement["memory_accounting"]),
                ("TasksAccounting", self._enforcement["tasks_accounting"]),
                ("CPUQuotaPerSecUSec", actual_cpu_usec == expected_cpu_usec),
                ("MemoryMax", actual_memory == expected_memory),
                ("CUDA_VISIBLE_DEVICES", actual_gpu_visibility == expected_gpu_visibility),
                ("InvocationID", invocation_id_matches),
                ("ControlGroup", control_group_matches and runner_in_cgroup and main_pid_matches),
                ("DevicePolicy", device_allow_exact),
            )
            if not matches
        ]
        if mismatches:
            self._monitor_failures += 1
            raise ResourceUsageEvidenceError(
                "systemd resource authority differs before scientific launch: "
                + ", ".join(mismatches)
            )
        self._control_group = properties.control_group
        self._invocation_id = properties.invocation_id
        try:
            accounting = self._snapshot()
            self._last_accounting = accounting
            self._write_checkpoint(accounting)
        except (OSError, ValueError, subprocess.SubprocessError, ResourceUsageEvidenceError) as exc:
            self._monitor_failures += 1
            raise ResourceUsageEvidenceError(
                "cgroup resource accounting is unavailable before scientific launch"
            ) from exc
        self._thread = threading.Thread(target=self._run, name=f"resource-monitor-{self.job_id}", daemon=True)
        self._thread.start()

    def _snapshot(self) -> dict[str, Any]:
        cgroup = _cgroup_directory(self._control_group)
        cpu = _read_cpu_stat(cgroup / "cpu.stat")
        memory_current = _read_int(cgroup / "memory.current")
        memory_peak = _read_int(cgroup / "memory.peak")
        pids_peak = _read_int(cgroup / "pids.peak")
        gpu_rows = _gpu_rows_for_pids(_cgroup_pids(cgroup)) if self.handoff["gpu_index"] is not None else []
        for row in gpu_rows:
            pid = int(row["pid"])
            gpu_uuid = row["gpu_uuid"]
            key = (pid, gpu_uuid)
            if (
                key not in self._gpu_peak_by_pid_uuid
                and len(self._gpu_peak_by_pid_uuid) >= _MAX_GPU_ROWS
            ):
                raise ResourceUsageEvidenceError("GPU process evidence exceeds its bounded row limit")
            self._gpu_peak_by_pid_uuid[key] = max(
                self._gpu_peak_by_pid_uuid.get(key, 0), int(row["used_memory_bytes"])
            )
            if gpu_uuid == self.handoff["gpu_uuid"]:
                self._gpu_observed = True
        self._samples += 1
        return {
            "cpu": cpu,
            "memory_current_bytes": memory_current,
            "memory_peak_bytes": memory_peak,
            "pids_peak": pids_peak,
        }

    def _write_checkpoint(self, accounting: Mapping[str, Any]) -> None:
        payload = {
            "schema": RESOURCE_CHECKPOINT_SCHEMA,
            "job_id": self.job_id,
            "generation": self.generation,
            "attempt": self.attempt,
            "unit": self.unit_name,
            "owner_nonce_sha256": _sha256(self.owner_nonce),
            "invocation_id": self._invocation_id,
            "control_group_sha256": _sha256(self._control_group),
            "admission_handoff_sha256": self.handoff["handoff_sha256"],
            "dispatch_payload_sha256": self.dispatch_authority["payload_sha256"],
            "started_at": self.started_at,
            "observed_at": _timestamp(),
            "enforcement": dict(self._enforcement),
            "accounting": dict(accounting),
            "gpu_peak_by_uuid": _gpu_uuid_peaks(self._gpu_peak_by_pid_uuid),
            "gpu_peak_by_pid_uuid": _gpu_process_peak_rows(self._gpu_peak_by_pid_uuid),
            "sample_count": self._samples,
            "monitor_failures": self._monitor_failures,
        }
        payload["checkpoint_sha256"] = _sha256(_canonical_json(payload))
        path = _checkpoint_path(self.job_id, self.generation, self.attempt)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(_canonical_json(payload), encoding="utf-8")
        os.replace(temporary, path)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                accounting = self._snapshot()
                self._last_accounting = accounting
                self._write_checkpoint(accounting)
            except (OSError, ValueError, subprocess.SubprocessError, ResourceUsageEvidenceError):
                self._monitor_failures += 1
                if self._last_accounting is not None:
                    try:
                        self._write_checkpoint(self._last_accounting)
                    except OSError:
                        pass
            self._stop.wait(_RESOURCE_POLL_INTERVAL_SECONDS)

    def finish(self, *, outcome: str) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        accounting: dict[str, Any] | None = None
        if self._control_group:
            try:
                accounting = self._snapshot()
                self._last_accounting = accounting
                self._write_checkpoint(accounting)
            except (OSError, ValueError, subprocess.SubprocessError, ResourceUsageEvidenceError):
                self._monitor_failures += 1
        gpu_required = self.handoff["gpu_index"] is not None
        gpu_process_peaks = _gpu_process_peak_rows(self._gpu_peak_by_pid_uuid)
        gpu_uuid_peaks = _gpu_uuid_peaks(self._gpu_peak_by_pid_uuid)
        gpu_exact = (
            (
                not gpu_required
                and not gpu_process_peaks
            )
            or (
                gpu_required
                and self._gpu_observed
                and bool(gpu_process_peaks)
                and set(gpu_uuid_peaks) == {self.handoff["gpu_uuid"]}
            )
        )
        gpu_usage_disposition = (
            "cpu_only"
            if not gpu_required
            else "admitted_used"
            if gpu_exact
            else "admitted_use_not_exactly_observed"
        )
        complete = accounting is not None and self._samples > 0 and self._monitor_failures == 0 and gpu_exact
        receipt: dict[str, Any] = {
            "schema": RESOURCE_USAGE_RECEIPT_SCHEMA,
            "producer": "bms.workflow_job_runner",
            "producer_source_revision": self.handoff["source_revision"],
            "producer_source_tree": self.handoff["source_tree"],
            "job_id": self.job_id,
            "run_attempt_id": self.handoff["run_attempt_id"],
            "admission_id": self.handoff["admission_id"],
            "preparation_id": self.handoff["preparation_id"],
            "execution": {
                "generation": self.generation,
                "attempt": self.attempt,
                "unit": self.unit_name,
                "owner_nonce_sha256": _sha256(self.owner_nonce),
                "invocation_id": self._invocation_id,
                "control_group_sha256": _sha256(self._control_group),
            },
            "admission": {
                key: self.handoff[key]
                for key in (
                    "cpu_threads",
                    "dram_bytes",
                    "gpu_index",
                    "gpu_uuid",
                    "policy_source",
                    "policy_version",
                    "owner",
                )
            },
            "enforcement": {
                **self._enforcement,
                "gpu_visibility": self.handoff["gpu_index"],
            },
            "observed": {
                "started_at": self.started_at,
                "finished_at": _timestamp(),
                "sample_interval_seconds": _RESOURCE_POLL_INTERVAL_SECONDS,
                "sample_count": self._samples,
                "monitor_failures": self._monitor_failures,
                "accounting": accounting,
                "gpu_peak_by_uuid": gpu_uuid_peaks,
                "gpu_peak_by_pid_uuid": gpu_process_peaks,
                "gpu_usage_disposition": gpu_usage_disposition,
            },
            "outcome": outcome,
            "complete": complete,
            "incompleteness_code": None if complete else "producer_resource_evidence_incomplete",
            "admission_handoff_sha256": self.handoff["handoff_sha256"],
            "dispatch_payload_sha256": self.dispatch_authority["payload_sha256"],
            "dispatch_authority_sha256": self.dispatch_authority["authority_sha256"],
        }
        receipt["receipt_sha256"] = _sha256(_canonical_json(receipt))
        return receipt


def _validate_resource_usage_receipt_document(candidate: Mapping[str, Any]) -> dict[str, Any]:
    receipt = {str(key): value for key, value in candidate.items()}
    if set(receipt) != {
        "schema", "producer", "producer_source_revision", "producer_source_tree",
        "job_id", "run_attempt_id", "admission_id", "preparation_id", "execution",
        "admission", "enforcement", "observed", "outcome", "complete",
        "incompleteness_code", "admission_handoff_sha256", "dispatch_payload_sha256",
        "dispatch_authority_sha256", "receipt_sha256",
    } or receipt.get("schema") != RESOURCE_USAGE_RECEIPT_SCHEMA:
        raise ResourceUsageEvidenceError("resource usage receipt fields are not exact")
    if len(_canonical_json(receipt).encode("utf-8")) > _MAX_CHECKPOINT_BYTES:
        raise ResourceUsageEvidenceError("resource usage receipt exceeds bounded size")
    execution = receipt.get("execution")
    admission = receipt.get("admission")
    enforcement = receipt.get("enforcement")
    observed = receipt.get("observed")
    if not isinstance(execution, Mapping) or set(execution) != {
        "generation", "attempt", "unit", "owner_nonce_sha256", "invocation_id", "control_group_sha256",
    }:
        raise ResourceUsageEvidenceError("resource usage receipt execution fields are not exact")
    if not isinstance(admission, Mapping) or set(admission) != {
        "cpu_threads", "dram_bytes", "gpu_index", "gpu_uuid", "policy_source", "policy_version", "owner",
    }:
        raise ResourceUsageEvidenceError("resource usage receipt admission fields are not exact")
    if not isinstance(enforcement, Mapping) or set(enforcement) != {
        "cpu_accounting", "memory_accounting", "tasks_accounting",
        "cpu_quota_per_sec_usec", "memory_max_bytes",
        "expected_cpu_quota_per_sec_usec", "expected_memory_max_bytes",
        "cuda_visible_devices", "expected_cuda_visible_devices", "gpu_visibility",
        "invocation_id_matches", "control_group_matches", "main_pid_matches",
        "runner_in_cgroup", "device_policy", "device_allow_is_empty",
        "device_allow_paths", "expected_device_allow_paths", "device_allow_exact",
        "cpu_only_device_denial", "gpu_device_denial",
    }:
        raise ResourceUsageEvidenceError("resource usage receipt enforcement fields are not exact")
    if not isinstance(observed, Mapping) or set(observed) != {
        "started_at", "finished_at", "sample_interval_seconds", "sample_count",
        "monitor_failures", "accounting", "gpu_peak_by_uuid",
        "gpu_peak_by_pid_uuid", "gpu_usage_disposition",
    }:
        raise ResourceUsageEvidenceError("resource usage receipt observed fields are not exact")
    digest = receipt.get("receipt_sha256")
    unsigned = dict(receipt)
    unsigned.pop("receipt_sha256", None)
    if not isinstance(digest, str) or digest != _sha256(_canonical_json(unsigned)):
        raise ResourceUsageEvidenceError("resource usage receipt digest is invalid")
    return receipt


def attach_resource_usage_receipt(params: object, receipt: Mapping[str, Any]) -> dict[str, Any]:
    normalized = params_mapping(params)
    candidate = _validate_resource_usage_receipt_document(receipt)
    receipts = normalized.get(RESOURCE_USAGE_RECEIPTS_PARAM, [])
    if not isinstance(receipts, list) or any(not isinstance(item, Mapping) for item in receipts):
        raise ResourceUsageEvidenceError("resource usage receipt history is malformed")
    identity = candidate.get("execution")
    if not isinstance(identity, Mapping):
        raise ResourceUsageEvidenceError("resource usage receipt execution identity is invalid")
    key = (candidate.get("job_id"), identity.get("generation"), identity.get("attempt"))
    for existing in receipts:
        existing_execution = existing.get("execution")
        existing_key = (
            existing.get("job_id"),
            existing_execution.get("generation") if isinstance(existing_execution, Mapping) else None,
            existing_execution.get("attempt") if isinstance(existing_execution, Mapping) else None,
        )
        if existing_key == key:
            if dict(existing) != candidate:
                raise ResourceUsageEvidenceError("resource usage receipt identity has conflicting bytes")
            return normalized
    normalized[RESOURCE_USAGE_RECEIPTS_PARAM] = [dict(item) for item in receipts] + [candidate]
    return normalized


def attach_cancelled_resource_receipt_from_checkpoint(
    job: Any,
    *,
    finished_at: str,
) -> dict[str, Any] | None:
    """Recover producer-owned accounting after the execution owner is cancelled."""

    params = params_mapping(getattr(job, "params", {}))
    handoff = validate_resource_admission_handoff(params.get(GLOBAL_RESOURCE_ADMISSION_PARAM))
    if handoff is None:
        return None
    dispatch_authority = validate_dispatch_materialization_authority(
        params.get(GLOBAL_DISPATCH_AUTHORITY_PARAM),
        expected_handoff=handoff,
    )
    executions = params.get("execution_attempts")
    if not isinstance(executions, list) or not executions or not isinstance(executions[-1], Mapping):
        raise ResourceUsageEvidenceError("cancelled resource evidence has no execution authority")
    execution = executions[-1]
    generation = execution.get("generation")
    attempt = execution.get("attempt")
    if type(generation) is not int or type(attempt) is not int:
        raise ResourceUsageEvidenceError("cancelled resource execution identity is invalid")
    existing_receipts = params.get(RESOURCE_USAGE_RECEIPTS_PARAM, [])
    if isinstance(existing_receipts, list):
        for existing in existing_receipts:
            identity = existing.get("execution") if isinstance(existing, Mapping) else None
            if (
                isinstance(identity, Mapping)
                and identity.get("generation") == generation
                and identity.get("attempt") == attempt
            ):
                return params
    path = _checkpoint_path(str(job.id), generation, attempt)
    try:
        raw_checkpoint = path.read_bytes()
        if len(raw_checkpoint) > _MAX_CHECKPOINT_BYTES:
            raise ResourceUsageEvidenceError("cancelled resource checkpoint exceeds bounded size")
        checkpoint = json.loads(raw_checkpoint.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(checkpoint, dict):
        return None
    checkpoint_digest = checkpoint.get("checkpoint_sha256")
    unsigned_checkpoint = dict(checkpoint)
    unsigned_checkpoint.pop("checkpoint_sha256", None)
    if (
        set(checkpoint) != {
            "schema", "job_id", "generation", "attempt", "unit",
            "owner_nonce_sha256", "invocation_id", "control_group_sha256",
            "admission_handoff_sha256", "dispatch_payload_sha256", "started_at",
            "observed_at", "enforcement", "accounting", "gpu_peak_by_uuid",
            "gpu_peak_by_pid_uuid", "sample_count", "monitor_failures", "checkpoint_sha256",
        }
        or checkpoint.get("schema") != RESOURCE_CHECKPOINT_SCHEMA
        or not isinstance(checkpoint_digest, str)
        or checkpoint_digest != _sha256(_canonical_json(unsigned_checkpoint))
        or checkpoint.get("job_id") != str(job.id)
        or checkpoint.get("generation") != generation
        or checkpoint.get("attempt") != attempt
        or checkpoint.get("unit") != execution.get("unit")
        or checkpoint.get("owner_nonce_sha256") != _sha256(str(execution.get("owner_nonce") or ""))
        or checkpoint.get("invocation_id") != execution.get("invocation_id")
        or checkpoint.get("admission_handoff_sha256") != handoff["handoff_sha256"]
        or checkpoint.get("dispatch_payload_sha256") != dispatch_authority["payload_sha256"]
    ):
        raise ResourceUsageEvidenceError("cancelled resource checkpoint identity diverged")
    accounting = checkpoint.get("accounting")
    enforcement = checkpoint.get("enforcement")
    gpu_peaks = checkpoint.get("gpu_peak_by_uuid")
    gpu_process_peak_rows = checkpoint.get("gpu_peak_by_pid_uuid")
    gpu_process_peaks = _validate_gpu_process_peak_rows(gpu_process_peak_rows)
    computed_gpu_peaks = _gpu_uuid_peaks(gpu_process_peaks)
    sample_count = checkpoint.get("sample_count")
    monitor_failures = checkpoint.get("monitor_failures")
    gpu_required = handoff["gpu_index"] is not None
    gpu_exact = (
        isinstance(gpu_peaks, dict)
        and gpu_peaks == computed_gpu_peaks
        and (
            (
                not gpu_required
                and not gpu_process_peaks
            )
            or (
                gpu_required
                and bool(gpu_process_peaks)
                and set(computed_gpu_peaks) == {handoff["gpu_uuid"]}
            )
        )
    )
    gpu_usage_disposition = (
        "cpu_only"
        if not gpu_required
        else "admitted_used"
        if gpu_exact
        else "admitted_use_not_exactly_observed"
    )
    complete = (
        isinstance(accounting, dict)
        and isinstance(enforcement, dict)
        and type(sample_count) is int
        and sample_count > 0
        and monitor_failures == 0
        and gpu_exact
    )
    receipt: dict[str, Any] = {
        "schema": RESOURCE_USAGE_RECEIPT_SCHEMA,
        "producer": "bms.workflow_job_runner",
        "producer_source_revision": handoff["source_revision"],
        "producer_source_tree": handoff["source_tree"],
        "job_id": str(job.id),
        "run_attempt_id": handoff["run_attempt_id"],
        "admission_id": handoff["admission_id"],
        "preparation_id": handoff["preparation_id"],
        "execution": {
            "generation": generation,
            "attempt": attempt,
            "unit": execution.get("unit"),
            "owner_nonce_sha256": checkpoint.get("owner_nonce_sha256"),
            "invocation_id": checkpoint.get("invocation_id"),
            "control_group_sha256": checkpoint.get("control_group_sha256"),
        },
        "admission": {
            key: handoff[key]
            for key in (
                "cpu_threads",
                "dram_bytes",
                "gpu_index",
                "gpu_uuid",
                "policy_source",
                "policy_version",
                "owner",
            )
        },
        "enforcement": {
            **enforcement,
            "gpu_visibility": handoff["gpu_index"],
        } if isinstance(enforcement, dict) else {},
        "observed": {
            "started_at": checkpoint.get("started_at"),
            "finished_at": finished_at,
            "sample_interval_seconds": _RESOURCE_POLL_INTERVAL_SECONDS,
            "sample_count": sample_count,
            "monitor_failures": monitor_failures,
            "accounting": accounting,
            "gpu_peak_by_uuid": gpu_peaks,
            "gpu_peak_by_pid_uuid": gpu_process_peak_rows,
            "gpu_usage_disposition": gpu_usage_disposition,
        },
        "outcome": "cancelled",
        "complete": complete,
        "incompleteness_code": None if complete else "producer_resource_evidence_incomplete",
        "admission_handoff_sha256": handoff["handoff_sha256"],
        "dispatch_payload_sha256": dispatch_authority["payload_sha256"],
        "dispatch_authority_sha256": dispatch_authority["authority_sha256"],
    }
    receipt["receipt_sha256"] = _sha256(_canonical_json(receipt))
    return attach_resource_usage_receipt(params, receipt)


def validate_producer_resource_usage_receipt(
    job: Any,
    expected_handoff: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one terminal producer receipt against source admission authority."""

    handoff = validate_resource_admission_handoff(expected_handoff)
    if handoff is None:
        raise ResourceUsageEvidenceError("expected resource admission handoff is required")
    params = params_mapping(getattr(job, "params", {}))
    if params.get(GLOBAL_RESOURCE_ADMISSION_PARAM) != handoff:
        raise ResourceUsageEvidenceError("canonical Job resource admission handoff diverged")
    dispatch_authority = validate_dispatch_materialization_authority(
        params.get(GLOBAL_DISPATCH_AUTHORITY_PARAM),
        expected_handoff=handoff,
    )
    executions = params.get("execution_attempts")
    receipts = params.get(RESOURCE_USAGE_RECEIPTS_PARAM)
    if (
        not isinstance(executions, list)
        or not executions
        or any(not isinstance(item, Mapping) for item in executions)
        or not isinstance(receipts, list)
        or any(not isinstance(item, Mapping) for item in receipts)
    ):
        raise ResourceUsageEvidenceError("producer execution/resource receipt history is unavailable")
    latest = executions[-1]
    matches = [
        dict(item)
        for item in receipts
        if item.get("run_attempt_id") == handoff["run_attempt_id"]
        and item.get("admission_id") == handoff["admission_id"]
    ]
    if len(matches) != 1:
        raise ResourceUsageEvidenceError("producer resource receipt cardinality is not exact")
    receipt = _validate_resource_usage_receipt_document(matches[0])
    execution = receipt.get("execution")
    if not isinstance(execution, Mapping):
        raise ResourceUsageEvidenceError("producer resource receipt execution identity is absent")
    if (
        receipt.get("schema") != RESOURCE_USAGE_RECEIPT_SCHEMA
        or receipt.get("producer") != "bms.workflow_job_runner"
        or receipt.get("producer_source_revision") != handoff["source_revision"]
        or receipt.get("producer_source_tree") != handoff["source_tree"]
        or receipt.get("job_id") != str(job.id)
        or receipt.get("preparation_id") != handoff["preparation_id"]
        or receipt.get("admission_handoff_sha256") != handoff["handoff_sha256"]
        or receipt.get("dispatch_payload_sha256") != dispatch_authority["payload_sha256"]
        or receipt.get("dispatch_authority_sha256") != dispatch_authority["authority_sha256"]
        or execution.get("generation") != latest.get("generation")
        or execution.get("attempt") != latest.get("attempt")
        or execution.get("unit") != latest.get("unit")
        or execution.get("invocation_id") != latest.get("invocation_id")
        or execution.get("owner_nonce_sha256") != _sha256(str(latest.get("owner_nonce") or ""))
        or not isinstance(execution.get("control_group_sha256"), str)
        or len(execution["control_group_sha256"]) != 64
        or any(
            character not in "0123456789abcdef"
            for character in execution["control_group_sha256"]
        )
    ):
        raise ResourceUsageEvidenceError("producer resource receipt identity diverged")
    enforcement = receipt.get("enforcement")
    observed = receipt.get("observed")
    if not isinstance(enforcement, Mapping) or not isinstance(observed, Mapping):
        raise ResourceUsageEvidenceError("producer resource enforcement/accounting evidence is absent")
    expected_device_allow_paths = _validated_recorded_device_allow_paths(
        handoff,
        enforcement.get("expected_device_allow_paths"),
    )
    if (
        enforcement.get("cpu_accounting") is not True
        or enforcement.get("memory_accounting") is not True
        or enforcement.get("tasks_accounting") is not True
        or enforcement.get("cpu_quota_per_sec_usec") != handoff["cpu_threads"] * 1_000_000
        or enforcement.get("expected_cpu_quota_per_sec_usec") != handoff["cpu_threads"] * 1_000_000
        or enforcement.get("memory_max_bytes") != handoff["dram_bytes"]
        or enforcement.get("expected_memory_max_bytes") != handoff["dram_bytes"]
        or enforcement.get("gpu_visibility") != handoff["gpu_index"]
        or enforcement.get("cuda_visible_devices")
        != ("" if handoff["gpu_index"] is None else str(handoff["gpu_index"]))
        or enforcement.get("expected_cuda_visible_devices")
        != ("" if handoff["gpu_index"] is None else str(handoff["gpu_index"]))
        or enforcement.get("invocation_id_matches") is not True
        or enforcement.get("control_group_matches") is not True
        or enforcement.get("main_pid_matches") is not True
        or enforcement.get("runner_in_cgroup") is not True
        or enforcement.get("device_policy") != "closed"
        or enforcement.get("device_allow_paths") != expected_device_allow_paths
        or enforcement.get("expected_device_allow_paths") != expected_device_allow_paths
        or enforcement.get("device_allow_exact") is not True
        or enforcement.get("device_allow_is_empty") is not (not expected_device_allow_paths)
        or enforcement.get("cpu_only_device_denial") is not (handoff["gpu_index"] is None)
        or enforcement.get("gpu_device_denial") is not (handoff["gpu_index"] is not None)
    ):
        raise ResourceUsageEvidenceError("producer resource enforcement differs from admission")
    accounting = observed.get("accounting")
    if not isinstance(accounting, Mapping):
        raise ResourceUsageEvidenceError("producer cgroup accounting evidence is absent")
    cpu = accounting.get("cpu")
    if (
        not isinstance(cpu, Mapping)
        or any(type(cpu.get(key)) is not int or cpu[key] < 0 for key in (
            "usage_usec", "user_usec", "system_usec", "nr_periods", "nr_throttled", "throttled_usec"
        ))
        or type(accounting.get("memory_peak_bytes")) is not int
        or accounting["memory_peak_bytes"] < 0
        or type(accounting.get("pids_peak")) is not int
        or accounting["pids_peak"] < 0
        or type(observed.get("sample_count")) is not int
        or observed["sample_count"] < 1
        or observed.get("monitor_failures") != 0
    ):
        raise ResourceUsageEvidenceError("producer cgroup accounting values are incomplete")
    gpu_peaks = observed.get("gpu_peak_by_uuid")
    if not isinstance(gpu_peaks, Mapping):
        raise ResourceUsageEvidenceError("producer GPU accounting evidence is malformed")
    gpu_process_peaks = _validate_gpu_process_peak_rows(observed.get("gpu_peak_by_pid_uuid"))
    computed_gpu_peaks = _gpu_uuid_peaks(gpu_process_peaks)
    if dict(gpu_peaks) != computed_gpu_peaks:
        raise ResourceUsageEvidenceError("producer GPU UUID peaks diverge from cgroup PID evidence")
    gpu_disposition = observed.get("gpu_usage_disposition")
    if handoff["gpu_index"] is None:
        if gpu_process_peaks or gpu_disposition != "cpu_only":
            raise ResourceUsageEvidenceError("CPU-only admission observed GPU use")
    elif (
        gpu_disposition != "admitted_used"
        or not gpu_process_peaks
        or set(computed_gpu_peaks) != {handoff["gpu_uuid"]}
        or any(gpu_uuid != handoff["gpu_uuid"] for _pid, gpu_uuid in gpu_process_peaks)
    ):
        raise ResourceUsageEvidenceError(
            "GPU evidence does not bind cgroup process IDs to the admitted physical GPU"
        )
    raw_terminal_state = str(getattr(job, "status", "")).lower()
    terminal_state = (
        "completed"
        if raw_terminal_state in {"completed", "succeeded", "awaiting_input"}
        else "cancelled"
        if raw_terminal_state in {"cancelled", "canceled"}
        else raw_terminal_state
    )
    if receipt.get("outcome") != terminal_state or terminal_state not in {"completed", "failed", "cancelled"}:
        raise ResourceUsageEvidenceError("producer resource receipt does not bind terminal Job state")
    if receipt.get("complete") is not True or receipt.get("incompleteness_code") is not None:
        raise ResourceUsageEvidenceError("producer resource receipt is incomplete")
    return receipt


__all__ = [
    "GLOBAL_DISPATCH_AUTHORITY_PARAM",
    "GLOBAL_DISPATCH_AUTHORITY_SCHEMA",
    "GLOBAL_RESOURCE_ADMISSION_PARAM",
    "GLOBAL_RESOURCE_ADMISSION_SCHEMA",
    "RESOURCE_USAGE_RECEIPTS_PARAM",
    "RESOURCE_USAGE_RECEIPT_SCHEMA",
    "ResourceUsageEvidenceError",
    "WorkflowResourceMonitor",
    "attach_cancelled_resource_receipt_from_checkpoint",
    "attach_dispatch_materialization_authority",
    "attach_resource_admission_handoff",
    "attach_resource_usage_receipt",
    "build_dispatch_materialization_authority",
    "build_resource_admission_handoff",
    "strip_resource_execution_metadata",
    "validate_dispatch_materialization_authority",
    "validate_producer_resource_usage_receipt",
    "validate_resource_admission_handoff",
]
