#!/usr/bin/env python3
"""Durable execution-only worker used through the BMS SSH bridge."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import signal
import shutil
import stat
import subprocess
import sys
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

STATUS_FILE = "status.json"
ENVELOPE_FILE = "execution-envelope.json"
RESULT_MANIFEST_FILE = "result-manifest.json"
CANCEL_REQUEST_FILE = "cancel-request.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeError("manifest path escapes the bundle")
    return path


def atomic_json(path: Path, value: Any) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.name != STATUS_FILE:
        _write_atomic_json(path, value)
        return value
    # Every process publishing attempt status participates, including pollers.
    with path.with_suffix(path.suffix + ".lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if path.name == STATUS_FILE and path.is_file():
            current = load_json(path)
            if current.get("state") in {"cancelled", "succeeded", "failed", "lost"}:
                return current
            if value.get("state") == "prepared":
                return current
            value = dict(value)
            for key in ("workflow_pid", "workflow_start_ticks", "supervisor_pid", "supervisor_start_ticks", "started_at"):
                if current.get(key) is not None and value.get(key) is None:
                    value[key] = current[key]
            if current.get("state") == "cancelling":
                value["state"] = "cancelled" if value.get("state") in {"succeeded", "failed", "lost", "cancelled"} else "cancelling"
        _write_atomic_json(path, value)
        return value


def _write_atomic_json(path: Path, value: Any) -> None:
    payload = canonical_bytes(value) + b"\n"
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{path.name} must contain one JSON object")
    return value


def process_start_ticks(pid: int) -> int | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
        return int(fields[21])
    except (FileNotFoundError, IndexError, OSError, ValueError):
        return None


def process_matches(pid: Any, expected_ticks: Any) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if isinstance(expected_ticks, bool) or not isinstance(expected_ticks, int) or expected_ticks <= 0:
        return False
    return process_start_ticks(pid) == expected_ticks


def envelope_path(attempt_dir: Path) -> Path:
    return attempt_dir / ENVELOPE_FILE


def status_path(attempt_dir: Path) -> Path:
    return attempt_dir / STATUS_FILE


def verify_bundle(attempt_dir: Path) -> dict[str, Any]:
    envelope = load_json(envelope_path(attempt_dir))
    if envelope.get("schema") != "bms.remote-execution.v1":
        raise RuntimeError("unsupported execution envelope")
    if not isinstance(envelope.get("command"), list) or not envelope["command"]:
        raise RuntimeError("execution envelope has no command")
    bundle_root = attempt_dir / "bundle"
    for record in envelope.get("files", []):
        if not isinstance(record, dict):
            raise RuntimeError("invalid file record")
        relative = safe_relative(str(record.get("relative_path") or ""))
        path = bundle_root.joinpath(*relative.parts)
        link_target = record.get("link_target")
        if link_target is not None:
            if not isinstance(link_target, str) or not path.is_symlink():
                raise RuntimeError(f"bundle symlink is missing: {relative}")
            actual_target = os.readlink(path)
            payload = actual_target.encode("utf-8")
            if actual_target != link_target or len(payload) != record.get("size_bytes"):
                raise RuntimeError(f"bundle symlink mismatch: {relative}")
            if hashlib.sha256(payload).hexdigest() != str(record.get("sha256") or ""):
                raise RuntimeError(f"bundle symlink hash mismatch: {relative}")
            resolved = path.resolve()
            runtime_root = (bundle_root / "runtime").resolve()
            if resolved != runtime_root and runtime_root not in resolved.parents:
                raise RuntimeError(f"bundle symlink escapes runtime: {relative}")
            continue
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"bundle file is missing: {relative}")
        mode = record.get("mode", 0o644)
        if type(mode) is not int or not 0 <= mode <= 0o777 or path.stat().st_mode & 0o7777 != mode:
            raise RuntimeError(f"bundle file mode mismatch: {relative}")
        expected_size = record.get("size_bytes")
        expected_sha = str(record.get("sha256") or "")
        if path.stat().st_size != expected_size or sha256_file(path) != expected_sha:
            raise RuntimeError(f"bundle file hash mismatch: {relative}")
    native_authority = envelope.get("native_execution_authority")
    if native_authority is not None:
        inputs = [record for record in envelope.get("files", []) if record.get("role") == "input"]
        if not isinstance(native_authority, dict) or native_authority.get("input_files") != inputs:
            raise RuntimeError("native input authority differs from staged files")
        input_root = bundle_root / "inputs"
        if input_root.is_symlink():
            raise RuntimeError("native input root is a symlink")
        observed = set()
        if input_root.exists():
            for path in input_root.rglob("*"):
                if path.is_symlink():
                    raise RuntimeError("native input namespace contains a symlink")
                if path.is_file():
                    observed.add(path.relative_to(bundle_root).as_posix())
        if observed != {record["relative_path"] for record in inputs}:
            raise RuntimeError("native staged input namespace has missing or undeclared files")
    source_archives = [
        record
        for record in envelope.get("files", [])
        if isinstance(record, dict) and record.get("relative_path") == "source/.bms-source.tar"
    ]
    if len(source_archives) != 1 or source_archives[0].get("sha256") != envelope.get("source_archive_sha256"):
        raise RuntimeError("source archive identity does not match the execution envelope")
    working_directory = Path(str(envelope.get("working_directory") or ""))
    output_directory = Path(str(envelope.get("output_directory") or ""))
    if not working_directory.is_absolute() or not output_directory.is_absolute():
        raise RuntimeError("execution paths must be absolute")
    if not working_directory.is_dir():
        raise RuntimeError("working directory is unavailable")
    output_directory.mkdir(parents=True, exist_ok=True)
    return envelope


def base_status(envelope: dict[str, Any], state: str) -> dict[str, Any]:
    return {
        "schema": "bms.remote-attempt-status.v1",
        "attempt_id": str(envelope["attempt_id"]),
        "job_id": str(envelope["job_id"]),
        "state": state,
        "supervisor_pid": None,
        "supervisor_start_ticks": None,
        "workflow_pid": None,
        "workflow_start_ticks": None,
        "exit_code": None,
        "started_at": None,
        "completed_at": None,
        "result_manifest_sha256": None,
        "error": None,
    }


def owner_paths(attempt_dir):
    import uuid
    if str(uuid.UUID(attempt_dir.name)) != attempt_dir.name or attempt_dir.parent.name != "attempts":
        raise RuntimeError("invalid owned attempt namespace")
    root = attempt_dir.parent.parent / "attempt-owners" / attempt_dir.name
    if any(part.is_symlink() for part in (root, *root.parents, attempt_dir)):
        raise RuntimeError("unsafe attempt owner namespace")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root, root / "intent.json"


def owner_guard(attempt_dir, *, exclusive=False):
    # Lock/tombstone are OUTSIDE the removable payload and never unlinked.
    # Receivers inherit this descriptor: wrapper death is not receiver death.
    from contextlib import contextmanager
    @contextmanager
    def held():
        root, path = owner_paths(attempt_dir)
        fd = os.open(root / "operations.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            yield fd, path
        finally:
            os.close(fd)
    return held()


def check_owner(path, expected=None, phases=None):
    record = load_json(path)
    intent = record["intent"]
    if (expected is not None and hashlib.sha256(canonical_bytes(intent)).hexdigest() != expected
            or intent["machine_id"] != Path("/etc/machine-id").read_text().strip()
            or intent["storage_device"] != str(path.parent.stat().st_dev)
            or intent["attempt_id"] != path.parent.name
            or intent["storage_path"] != str(path.parents[2] / "attempts" / path.parent.name)
            or Path(intent["storage_path"]).exists() and str(Path(intent["storage_path"]).stat().st_dev) != intent["storage_device"]
            or phases is not None and record["phase"] not in phases):
        raise RuntimeError("remote attempt generation is fenced or changed")
    return record


def initialize_owner(attempt_dir, intent):
    with owner_guard(attempt_dir, exclusive=True) as (_, path):
        if (intent["attempt_id"] != attempt_dir.name or intent["storage_path"] != str(attempt_dir)
                or intent["machine_id"] != Path("/etc/machine-id").read_text().strip()
                or intent["storage_device"] != str(attempt_dir.parent.parent.stat().st_dev)):
            raise RuntimeError("remote staging intent identity mismatch")
        if path.exists():
            record = check_owner(path)
            if record["intent"] != intent:
                raise RuntimeError("remote staging intent already belongs to another generation")
            return record
        record = {"intent": intent, "phase": "staging"}
        atomic_json(path, record)  # Before the first mkdir/receive, even on empty recovery.
        return record


def stage_command(attempt_dir, expected, argv, *, reading=False):
    with owner_guard(attempt_dir) as (fd, path):
        record = check_owner(path, expected, {"fenced"} if reading else {"staging"})
        os.set_inheritable(fd, True)
        process = subprocess.Popen(argv, pass_fds=(fd,))
        # Sample-abort is not a filesystem quota; partial receives stay charged.
        while process.poll() is None:
            if not reading and owned_disk_bytes(attempt_dir) > record["intent"]["staging_disk_bytes"]:
                process.terminate()
                process.wait()
                raise RuntimeError("staging disk observation exceeded admission")
            time.sleep(0.2)
        if process.returncode:
            raise RuntimeError("owned staging command failed")


def arm_owner(attempt_dir, expected):
    with owner_guard(attempt_dir, exclusive=True) as (_, path):
        record = check_owner(path, expected, {"staging", "armed"})
        envelope = verify_bundle(attempt_dir)
        if envelope["resource_allocation"]["reservation_id"] != record["intent"]["compute_reservation_id"]:
            raise RuntimeError("scientific launch requires the exact compute admission")
        digest = sha256_file(envelope_path(attempt_dir))
        if record["phase"] == "armed" and record["envelope_sha256"] != digest:
            raise RuntimeError("armed envelope changed")
        record.update(phase="armed", envelope_sha256=digest)
        atomic_json(path, record)
        return record


def retire_fenced_anchor(record, path):
    creation = record.get("boundary_creation")
    if record["nonexecution"] and creation and not record.get("anchor_removed"):
        boundary = OwnedBoundary(record["intent"]["attempt_id"], {})
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if creation["unit"] != boundary.unit:
            raise RuntimeError("interrupted boundary creation identity changed")
        # No science was ever authorized, independently of manager state.
        # A reboot cannot authorize stopping a replacement invocation.
        if creation["boot_id"] == boot:
            state = boundary.control("show", boundary.unit, "--property=LoadState", "--value")
            if state != "not-found":
                marker = boundary.control("show", boundary.unit, "--property=Description", "--value")
                invocation = boundary.control("show", boundary.unit, "--property=InvocationID", "--value")
                if marker != creation["marker"] or not invocation or (
                        creation["invocation_id"] is not None and invocation != creation["invocation_id"]):
                    raise RuntimeError("interrupted boundary invocation is ambiguous")
                creation["invocation_id"] = invocation
                atomic_json(path, record)
                boundary.control("stop", boundary.unit)
        record["anchor_removed"] = True
        atomic_json(path, record)
    if not record["nonexecution"] and not record.get("anchor_removed"):
        identity = record["evidence"]["execution"]
        boundary = OwnedBoundary(record["intent"]["attempt_id"], record["evidence"]["allocation"]["effective"])
        if identity["boot_id"] == Path("/proc/sys/kernel/random/boot_id").read_text().strip():
            state = boundary.control("show", boundary.unit, "--property=LoadState", "--value")
            if state != "not-found":
                invocation = boundary.control("show", boundary.unit, "--property=InvocationID", "--value")
                active = boundary.control("show", boundary.unit, "--property=ActiveState", "--value")
                if invocation and invocation != identity["invocation_id"]:
                    raise RuntimeError("fenced boundary invocation changed")
                if active not in {"inactive", "failed"}:
                    if invocation != identity["invocation_id"]:
                        raise RuntimeError("fenced boundary invocation is ambiguous")
                    boundary.identity, boundary.path = identity, Path(identity["control_group"])
                    if boundary.sample()["populated"] != 0:
                        raise RuntimeError("fenced boundary is not quiescent")
                    boundary.close()
        record["anchor_removed"] = True
        atomic_json(path, record)


def fence_owner(attempt_dir, expected, *, remove=False):
    with owner_guard(attempt_dir, exclusive=True) as (_, path):
        record = check_owner(path, expected)
        if record["phase"] not in {"fenced", "removing", "removed"}:
            # Exclusive remote ownership excludes all receivers and supervisors.
            # It does NOT prove descendants dead; scientific authorization below
            # requires the original cgroup receipt/counters as a separate proof.
            evidence = None
            if record.get("execution_authorized"):
                current = status(attempt_dir)
                evidence = current.get("resource_receipt")
                if not evidence or evidence.get("quiescent") is not True:
                    raise RuntimeError("active or ambiguous scientific storage cannot be fenced")
            record.update(phase="fenced", evidence=evidence,
                nonexecution=not record.get("execution_authorized", False))
            atomic_json(path, record)  # Durable late-launch and late-receive fence.
        try:
            retire_fenced_anchor(record, path)
            record.pop("anchor_error", None)
            atomic_json(path, record)
        except Exception as exc:
            # The durable launch fence + nonexecution/original empty-science
            # proof releases COMPUTE even if manager anchor retirement fails.
            # Explicit deletion still refuses ambiguous boundary cleanup.
            record["anchor_error"] = f"{type(exc).__name__}: {exc}"[:1000]
            atomic_json(path, record)
            if remove:
                raise
        if remove:
            record["phase"] = "removing"
            atomic_json(path, record)
            if attempt_dir.exists():
                if attempt_dir.is_symlink():
                    raise RuntimeError("unsafe attempt payload")
                shutil.rmtree(attempt_dir)
            directory = os.open(attempt_dir.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            record["phase"] = "removed"
            atomic_json(path, record)
        proof = {"schema": "bms.remote-attempt-fence.v1", "intent": record["intent"],
            "intent_sha256": expected, "machine_id": Path("/etc/machine-id").read_text().strip(),
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "nonexecution": record["nonexecution"], "resource_receipt": record["evidence"],
            "phase": record["phase"], "quiescent": True,
            "boundary_cleanup": "pending" if record.get("anchor_error") else "complete",
            "resident_disk_bytes": owned_disk_bytes(attempt_dir) + owned_disk_bytes(path.parent)}
        return proof


def prepare(attempt_dir: Path) -> dict[str, Any]:
    envelope = verify_bundle(attempt_dir)
    status = base_status(envelope, "prepared")
    return atomic_json(status_path(attempt_dir), status)


def start(attempt_dir: Path) -> dict[str, Any]:
    with (attempt_dir / "start.lock").open("a+b") as start_lock:
        fcntl.flock(start_lock.fileno(), fcntl.LOCK_EX)
        if status_path(attempt_dir).is_file():
            current = status(attempt_dir)
        else:
            current = prepare(attempt_dir)
        if current.get("state") in {"cancelled", "succeeded", "failed", "lost"}:
            return current
        if current.get("state") == "running":
            return current
        if current.get("state") != "prepared":
            return status(attempt_dir)
        # A prepared receipt can survive a controller disconnect. Revalidate the
        # package before the idempotent start resumes it.
        verify_bundle(attempt_dir)
        with (attempt_dir / "supervisor.log").open("ab", buffering=0) as supervisor_log:
            process = subprocess.Popen(
                [sys.executable, os.path.realpath(__file__), "supervise", "--attempt-dir", str(attempt_dir)],
                stdin=subprocess.DEVNULL,
                stdout=supervisor_log,
                stderr=subprocess.STDOUT,
                close_fds=True,
                start_new_session=True,
            )
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            current = load_json(status_path(attempt_dir))
            if current.get("state") in {"cancelled", "succeeded", "failed", "lost"}:
                return current
            if (
                current.get("state") == "running"
                and current.get("supervisor_pid") == process.pid
                and isinstance(current.get("workflow_pid"), int)
            ):
                return current
            if process.poll() is not None:
                break
            time.sleep(0.1)
        current = load_json(status_path(attempt_dir))
        if current.get("state") in {"cancelled", "succeeded", "failed", "lost"}:
            return current
        raise RuntimeError("remote supervisor did not publish a durable launch receipt")


def build_result_manifest(attempt_dir: Path, envelope: dict[str, Any], exit_code: int) -> dict[str, Any]:
    output_root = Path(str(envelope["output_directory"]))
    artifacts: list[dict[str, Any]] = []
    for path in sorted(output_root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"result tree contains a symlink: {path.relative_to(output_root)}")
        if not path.is_file():
            continue
        if path.name == RESULT_MANIFEST_FILE:
            continue
        relative = path.relative_to(output_root).as_posix()
        artifacts.append(
            {
                "relative_path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "role": "log" if path.suffix in {".log", ".trace"} else "result",
            }
        )
    return {
        "schema": "bms.remote-result-manifest.v1",
        "attempt_id": str(envelope["attempt_id"]),
        "job_id": str(envelope["job_id"]),
        "exit_code": int(exit_code),
        "completed_at": utc_now(),
        "artifacts": artifacts,
        "source_revision": str(envelope["source_revision"]),
        "source_tree": str(envelope["source_tree"]),
        "execution_envelope_sha256": sha256_file(envelope_path(attempt_dir)),
        "resource_receipt_sha256": sha256_file(output_root / "_remote" / "resource-usage.json"),
        "native_execution_authority_sha256": (
            hashlib.sha256(canonical_bytes(envelope["native_execution_authority"])).hexdigest()
            if envelope.get("native_execution_authority") is not None else None
        ),
    }


class OwnedBoundary:
    """Transient delegated service, with an emptyable scientific child.

    Only this freshly created unit is modified. No slice quotas, cpusets,
    memory protection, persistent unit, or idle resource reservation is used.
    The trusted worker owns delegation; this is not an adversarial sandbox.
    """

    def __init__(self, attempt_id: str, limits: dict[str, Any]):
        import uuid
        identifier = str(uuid.UUID(attempt_id))
        self.unit = "bms-attempt-" + identifier + ".service"
        self.priv = [] if os.geteuid() == 0 else ["sudo", "-n"]
        self.path = None
        self.identity = None
        self.limits = limits

    def control(self, *args: str) -> str:
        return subprocess.run(self.priv + ["systemctl", *args], check=True,
                              capture_output=True, text=True, timeout=30).stdout.strip()

    def create(self, owner_path=None, owner_record=None, owner_fd=None) -> dict[str, Any]:
        for name in ("cpu_threads", "dram_bytes"):
            if type(self.limits.get(name)) is not int or self.limits[name] < 1:
                raise RuntimeError("invalid owned boundary allocation")
        if owner_record is not None:
            creation = {"unit": self.unit,
                "marker": "bms-owner:" + hashlib.sha256(canonical_bytes(owner_record["intent"])).hexdigest(),
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                "invocation_id": None}
            owner_record["boundary_creation"] = creation
            atomic_json(owner_path, owner_record)
        else:
            creation = None
        # systemd refuses a pre-existing unit. Never adopt by name alone.
        subprocess.run(self.priv + ["systemd-run", "--quiet", "--unit=" + self.unit,
            "--property=Type=exec", "--property=Delegate=cpu memory pids",
            "--property=User=" + str(os.getuid()), "--property=Group=" + str(os.getgid()),
            "--property=KillMode=control-group", "--property=TasksMax=infinity", "--property=Restart=no",
            *(["--property=Description=" + creation["marker"]] if creation else []),
            "/bin/sleep", "infinity"], check=True, capture_output=True, timeout=30,
            pass_fds=(() if owner_fd is None else (owner_fd,)))
        try:
            group = self.control("show", self.unit, "--property=ControlGroup", "--value")
            invocation = self.control("show", self.unit, "--property=InvocationID", "--value")
            if creation:
                if self.control("show", self.unit, "--property=Description", "--value") != creation["marker"]:
                    raise RuntimeError("owned creation generation changed")
                creation["invocation_id"] = invocation
                atomic_json(owner_path, owner_record)  # Original invocation, before child mkdir/counters.
            anchor = int(self.control("show", self.unit, "--property=MainPID", "--value"))
            if not group.startswith("/") or ".." in PurePosixPath(group).parts or not invocation or anchor <= 0:
                raise RuntimeError("systemd delegation identity unavailable")
            parent = Path("/sys/fs/cgroup") / group.lstrip("/")
            # Move only our own anchor, never an SSH session or sibling process.
            leaf = parent / "anchor"
            leaf.mkdir()
            (leaf / "cgroup.procs").write_text(str(anchor))
            (parent / "cgroup.subtree_control").write_text("+cpu +memory +pids")
            self.path = parent / "science"
            self.path.mkdir()
            (self.path / "cpu.max").write_text(f"{self.limits['cpu_threads'] * 100000} 100000")
            (self.path / "memory.max").write_text(str(self.limits["dram_bytes"]))
            (self.path / "memory.swap.max").write_text("0")
            (self.path / "memory.oom.group").write_text("1")
            for name in ("cpu.stat", "memory.peak", "memory.events", "pids.peak", "cgroup.events", "cgroup.kill"):
                if not (self.path / name).exists():
                    raise RuntimeError("required owned cgroup counter/control unavailable: " + name)
            self.identity = {"unit": self.unit, "invocation_id": invocation,
                "control_group": str(self.path), "inode": self.path.stat().st_ino,
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                "machine_id": Path("/etc/machine-id").read_text().strip()}
            self.sample()
            return self.identity
        except BaseException:
            # Recovery owns interrupted creation through its durable marker and
            # original invocation. Never stop a replacement unit by name alone.
            if not creation:
                self.control("stop", self.unit)
            raise

    def sample(self) -> dict[str, Any]:
        if self.path is None or self.path.stat().st_ino != self.identity["inode"]:
            raise RuntimeError("owned boundary identity changed")
        group = self.control("show", self.unit, "--property=ControlGroup", "--value")
        if str(Path("/sys/fs/cgroup") / group.lstrip("/") / "science") != str(self.path):
            raise RuntimeError("owned systemd control group changed")
        if self.control("show", self.unit, "--property=InvocationID", "--value") != self.identity["invocation_id"]:
            raise RuntimeError("owned systemd invocation changed")
        def pairs(name):
            return {k: int(v) for k, v in (line.split() for line in (self.path / name).read_text().splitlines())}
        quota, period = map(int, (self.path / "cpu.max").read_text().split())
        memory = int((self.path / "memory.max").read_text())
        if quota != self.limits["cpu_threads"] * period or memory != self.limits["dram_bytes"]:
            raise RuntimeError("owned boundary enforcement drift")
        if (self.path / "memory.swap.max").read_text().strip() != "0":
            raise RuntimeError("owned boundary swap enforcement drift")
        return {"cpu_usage_usec": pairs("cpu.stat")["usage_usec"],
            "memory_peak_bytes": int((self.path / "memory.peak").read_text()),
            "pids_peak": int((self.path / "pids.peak").read_text()),
            "memory_events": pairs("memory.events"),
            "populated": pairs("cgroup.events")["populated"],
            "cpu_max": [quota, period], "memory_max_bytes": memory, "swap_max_bytes": 0}

    def quiesce(self) -> dict[str, Any]:
        # Kills only this attempt's descendants, including reparented/set-session
        # children. Process-group absence is never used as quiescence proof.
        self.sample()  # Verify invocation/inode before any destructive control.
        (self.path / "cgroup.kill").write_text("1")
        deadline = time.monotonic() + 30
        while True:
            observed = self.sample()
            if observed["populated"] == 0:
                return observed
            if time.monotonic() >= deadline:
                raise RuntimeError("owned scientific boundary did not quiesce")
            time.sleep(0.1)

    def close(self):
        if self.identity and self.control("show", self.unit, "--property=InvocationID", "--value") != self.identity["invocation_id"]:
            raise RuntimeError("refusing to stop a replacement systemd invocation")
        self.control("stop", self.unit)


def owned_disk_bytes(root: Path) -> int:
    # Logical resident bytes, not allocated blocks or a kernel quota. Do not
    # follow bundle aliases into shared storage or count hard links twice.
    seen = set()
    total = 0
    if root.is_symlink():
        raise RuntimeError("unsafe observed storage root")
    def refuse(error):
        raise error
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=refuse):
        links = [name for name in dirs if (Path(directory) / name).is_symlink()]
        dirs[:] = [name for name in dirs if name not in links]
        for name in [*files, *links]:
            info = (Path(directory) / name).lstat()
            key = (info.st_dev, info.st_ino)
            if (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)) and key not in seen:
                seen.add(key)
                total += info.st_size
    return total


def resource_capability() -> dict[str, Any]:
    import uuid
    boundary = OwnedBoundary(str(uuid.uuid4()), {"cpu_threads": 1, "dram_bytes": 16777216})
    boundary.create()
    try:
        observed = boundary.quiesce()
        return {"schema": "bms.remote-owned-capability.v1", "backend": "systemd-delegated-cgroup-v2",
            "cpu": "kernel-quota", "dram": "kernel-memory-max-no-swap",
            "disk": "observed-logical-bytes-not-quota", "idle_reservation": False,
            "owner_protocol": "bms.remote-attempt-intent.v1",
            "machine_id": boundary.identity["machine_id"], "boot_id": boundary.identity["boot_id"],
            "quiescence": observed["populated"] == 0}
    finally:
        boundary.close()


def spawn_owned(boundary, envelope, environment, log):
    """Enrollment ACK then launch gate; parent death before ACK cannot run science.

    This closes the Popen-to-cgroup race without preexec_fn in a multithreaded
    controller. EOF at the gate aborts the launcher, never authorizes execution.
    """
    import select
    gate_read, gate_write = os.pipe()
    ack_read, ack_write = os.pipe()
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "exec-owned",
             "--cgroup", str(boundary.path), "--gate-fd", str(gate_read), "--ack-fd", str(ack_write),
             "--", *[str(value) for value in envelope["command"]]],
            cwd=str(envelope["working_directory"]), env=environment,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            pass_fds=(gate_read, ack_write), start_new_session=True)
        os.close(gate_read); gate_read = -1
        os.close(ack_write); ack_write = -1
        if not select.select([ack_read], [], [], 30)[0] or os.read(ack_read, 1) != b"E":
            raise RuntimeError("scientific launcher did not acknowledge cgroup enrollment")
        boundary.sample()
        os.write(gate_write, b"G")
        return process
    except BaseException:
        if process is not None:
            process.kill()
            process.wait()
        raise
    finally:
        for descriptor in (gate_read, gate_write, ack_read, ack_write):
            if descriptor >= 0:
                os.close(descriptor)


def make_resource_receipt(attempt_dir, envelope, boundary, observed, *, exit_code, complete):
    allocation = envelope["resource_allocation"]
    proof = {
        "schema": "bms.remote-resource-usage.v1", "job_id": envelope["job_id"],
        "run_attempt_id": envelope["attempt_id"], "admission_id": allocation["reservation_id"],
        "execution_envelope_sha256": sha256_file(envelope_path(attempt_dir)),
        "producer_source_revision": envelope["source_revision"], "producer_source_tree": envelope["source_tree"],
        "allocation": allocation, "execution": boundary.identity,
        "observed": {**observed, "finished_at": utc_now(),
                     "started_at": load_json(status_path(attempt_dir)).get("started_at")}, "quiescent": True,
        "disk": {"scope": "attempt-tree-logical-bytes", "enforcement": "sampled-abort-not-quota",
                 "resident_bytes": owned_disk_bytes(attempt_dir), "sample_interval_seconds": 2},
        "complete": complete and observed["memory_events"].get("oom_kill", 0) == 0, "outcome": "completed" if exit_code == 0 else "failed",
        "stage_terminal_states": {},
    }
    if (attempt_dir / CANCEL_REQUEST_FILE).exists():
        proof["outcome"] = "cancelled"
    stage_journal = Path(envelope["output_directory"]) / "_remote" / "stage-terminal.json"
    if stage_journal.exists():
        proof["stage_terminal_states"] = load_json(stage_journal)
    proof["receipt_sha256"] = hashlib.sha256(canonical_bytes(proof)).hexdigest()
    if proof["disk"]["resident_bytes"] > allocation["effective"]["disk_bytes"]:
        proof["complete"] = False
        proof.pop("receipt_sha256")
        proof["receipt_sha256"] = hashlib.sha256(canonical_bytes(proof)).hexdigest()
    return proof


def supervise(attempt_dir: Path) -> int:
    with owner_guard(attempt_dir) as (owner_fd, path):
        record = check_owner(path, phases={"armed"})
        if sha256_file(envelope_path(attempt_dir)) != record["envelope_sha256"]:
            raise RuntimeError("armed execution envelope changed")
        with (path.parent / "supervisor.lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            record = check_owner(path, phases={"armed"})
            if record.get("supervisor_started") or record.get("execution_authorized"):
                raise RuntimeError("this generation already started a supervisor; recover, do not relaunch")
            record["supervisor_started"] = True
            atomic_json(path, record)
            return _supervise_owned(attempt_dir, path, record, owner_fd)


def _supervise_owned(attempt_dir: Path, owner_path, owner_record, owner_fd) -> int:
    envelope = load_json(envelope_path(attempt_dir))
    if envelope.get("native_execution_authority") is not None:
        envelope = verify_bundle(attempt_dir)
    current = load_json(status_path(attempt_dir))
    if current.get("state") not in {"prepared", "cancelling"}:
        raise RuntimeError("remote attempt is not prepared for launch")
    if (
        str(current.get("attempt_id")) != str(envelope.get("attempt_id"))
        or str(current.get("job_id")) != str(envelope.get("job_id"))
    ):
        raise RuntimeError("prepared status does not match the execution envelope")
    current.update(
        {
            "state": "cancelling" if (attempt_dir / CANCEL_REQUEST_FILE).exists() else "running",
            "supervisor_pid": os.getpid(),
            "supervisor_start_ticks": process_start_ticks(os.getpid()),
            "started_at": current.get("started_at") or utc_now(),
        }
    )
    atomic_json(status_path(attempt_dir), current)
    allocation = envelope.get("resource_allocation")
    if (not isinstance(allocation, dict)
            or allocation.get("target_id") != envelope.get("execution_target_id")
            or allocation.get("storage_path") != str(attempt_dir)
            or allocation.get("owner") != "remote-attempt:" + envelope["job_id"] + ":" + envelope["attempt_id"]
            or allocation.get("machine_id") != Path("/etc/machine-id").read_text().strip()
            or allocation.get("storage_device") != str(attempt_dir.stat().st_dev)):
        raise RuntimeError("remote resource admission identity mismatch")
    boundary = OwnedBoundary(envelope["attempt_id"], allocation["effective"])
    boundary.create(owner_path, owner_record, owner_fd)
    atomic_json(attempt_dir / "resource-boundary.json", {"execution": boundary.identity, "allocation": allocation})
    environment = os.environ.copy()
    log_path = attempt_dir / "nextflow.log"
    exit_code = 1
    error: str | None = None
    try:
        for key, value in dict(envelope.get("environment") or {}).items():
            if not isinstance(key, str) or not isinstance(value, str) or "\x00" in key + value:
                raise RuntimeError("execution environment is invalid")
            environment[key] = value
        secret_path = attempt_dir / "secret-env.json"
        if secret_path.exists():
            mode = stat.S_IMODE(secret_path.stat().st_mode)
            if mode & 0o077:
                raise RuntimeError("attempt secret environment permissions are unsafe")
            secret_environment = load_json(secret_path)
            if not isinstance(secret_environment, dict):
                raise RuntimeError("attempt secret environment is invalid")
            for key, value in secret_environment.items():
                if not isinstance(key, str) or not isinstance(value, str) or "\x00" in key + value:
                    raise RuntimeError("attempt secret environment is invalid")
                environment[key] = value
            secret_path.unlink()
        if (attempt_dir / CANCEL_REQUEST_FILE).exists():
            exit_code = -15
        else:
            with log_path.open("ab", buffering=0) as log:
                owner_record["execution_authorized"] = True
                atomic_json(owner_path, owner_record)
                process = spawn_owned(boundary, envelope, environment, log)
                latest = load_json(status_path(attempt_dir))
                latest.update(
                    {
                        "workflow_pid": process.pid,
                        "workflow_start_ticks": process_start_ticks(process.pid),
                    }
                )
                if (attempt_dir / CANCEL_REQUEST_FILE).exists():
                    latest["state"] = "cancelling"
                atomic_json(status_path(attempt_dir), latest)
                terminate_deadline: float | None = None
                next_disk_observation = 0.0
                while process.poll() is None:
                    boundary.sample()
                    if time.monotonic() >= next_disk_observation:
                        if owned_disk_bytes(attempt_dir) > allocation["effective"]["disk_bytes"]:
                            raise RuntimeError("observed attempt disk allocation exceeded (not a kernel quota)")
                        next_disk_observation = time.monotonic() + 2.0
                    if (attempt_dir / CANCEL_REQUEST_FILE).exists():
                        if terminate_deadline is None:
                            try:
                                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                            except ProcessLookupError:
                                pass
                            terminate_deadline = time.monotonic() + 30.0
                        elif time.monotonic() >= terminate_deadline:
                            try:
                                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                    time.sleep(0.2)
                exit_code = int(process.wait())
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:4000]
        exit_code = 1
    # Receipt/release remains possible for failures and cancellation. Failure to
    # prove cgroup emptiness is deliberately nonterminal; a later poll retries.
    observed = boundary.quiesce()
    proof = make_resource_receipt(attempt_dir, envelope, boundary, observed,
                                  exit_code=exit_code, complete=error is None)
    resource_path = Path(envelope["output_directory"]) / "_remote" / "resource-usage.json"
    atomic_json(resource_path, proof)
    # Persist proof before destroying the kernel counters. Recovery can use this
    # exact immutable receipt after a supervisor/controller restart.
    latest = load_json(status_path(attempt_dir))
    latest["resource_receipt"] = proof
    atomic_json(status_path(attempt_dir), latest)
    boundary.close()
    manifest_sha: str | None = None
    try:
        remote_logs = Path(str(envelope["output_directory"])) / "_remote"
        remote_logs.mkdir(parents=True, exist_ok=True)
        if log_path.is_file():
            shutil.copy2(log_path, remote_logs / "nextflow.log")
        supervisor_log = attempt_dir / "supervisor.log"
        if supervisor_log.is_file():
            shutil.copy2(supervisor_log, remote_logs / "supervisor.log")
        # The returned binding cannot attest to a different staged input or
        # runtime generation than the one verified immediately before spawn.
        # This is a producer boundary check, not a native descriptor substitute.
        if envelope.get("native_execution_authority") is not None:
            verified = verify_bundle(attempt_dir)
            if canonical_bytes(verified) != canonical_bytes(envelope):
                raise RuntimeError("execution envelope changed during native execution")
        manifest = build_result_manifest(attempt_dir, envelope, exit_code)
        output_manifest = Path(str(envelope["output_directory"])) / RESULT_MANIFEST_FILE
        atomic_json(output_manifest, manifest)
        manifest_sha = sha256_file(output_manifest)
    except Exception as exc:
        manifest_error = f"{type(exc).__name__}: {exc}"[:4000]
        error = f"{error}; {manifest_error}"[:4000] if error else manifest_error
        exit_code = 1
    latest = load_json(status_path(attempt_dir))
    cancellation_requested = (
        (attempt_dir / CANCEL_REQUEST_FILE).exists()
        or latest.get("state") in {"cancelling", "cancelled"}
    )
    terminal_state = "cancelled" if cancellation_requested else ("succeeded" if exit_code == 0 else "failed")
    latest.update(
        {
            "state": terminal_state,
            "exit_code": exit_code,
            "completed_at": utc_now(),
            "result_manifest_sha256": manifest_sha,
            "error": error,
        }
    )
    atomic_json(status_path(attempt_dir), latest)
    return exit_code


def status(attempt_dir: Path) -> dict[str, Any]:
    with status_path(attempt_dir).with_suffix(".json.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        value = load_json(status_path(attempt_dir))
        cancellation_requested = (attempt_dir / CANCEL_REQUEST_FILE).exists()
        if cancellation_requested and value.get("state") == "running":
            value["state"] = "cancelling"
        if value.get("state") in {"running", "cancelling"}:
            workflow_alive = process_matches(value.get("workflow_pid"), value.get("workflow_start_ticks"))
            supervisor_alive = process_matches(value.get("supervisor_pid"), value.get("supervisor_start_ticks"))
            if not supervisor_alive and not value.get("resource_receipt"):
                record_path = attempt_dir / "resource-boundary.json"
                if record_path.is_file():
                    record = load_json(record_path)
                    envelope = load_json(envelope_path(attempt_dir))
                    if record.get("allocation") != envelope.get("resource_allocation"):
                        raise RuntimeError("recovered boundary admission changed")
                    boundary = OwnedBoundary(envelope["attempt_id"], record["allocation"]["effective"])
                    boundary.identity = record["execution"]
                    boundary.path = Path(boundary.identity["control_group"])
                    if (boundary.identity["unit"] != boundary.unit
                            or boundary.identity["boot_id"] != Path("/proc/sys/kernel/random/boot_id").read_text().strip()
                            or boundary.path.name != "science" or boundary.path.parent.name != boundary.unit):
                        raise RuntimeError("recovered boundary identity mismatch")
                    observed = boundary.quiesce() if cancellation_requested else boundary.sample()
                    if observed["populated"] == 0:
                        proof = make_resource_receipt(attempt_dir, envelope, boundary, observed,
                                                      exit_code=1, complete=False)
                        atomic_json(Path(envelope["output_directory"]) / "_remote" / "resource-usage.json", proof)
                        value["resource_receipt"] = proof
                        _write_atomic_json(status_path(attempt_dir), value)
                        boundary.close()
                        workflow_alive = False
            if not workflow_alive and not supervisor_alive and value.get("resource_receipt", {}).get("quiescent") is True:
                value.update(
                    {
                        "state": "cancelled" if cancellation_requested else "lost",
                        "completed_at": utc_now(),
                        "exit_code": -15 if cancellation_requested else value.get("exit_code"),
                        "error": (
                            value.get("error")
                            if cancellation_requested
                            else "Remote attempt owners disappeared without a terminal receipt"
                        ),
                    }
                )
            _write_atomic_json(status_path(attempt_dir), value)
        if value.get("resource_receipt", {}).get("quiescent") is True:
            owner_root, _ = owner_paths(attempt_dir)
            value["resident_disk_bytes"] = owned_disk_bytes(attempt_dir) + owned_disk_bytes(owner_root)
        return value


def cancel(attempt_dir: Path, timeout_seconds: float) -> dict[str, Any]:
    value = status(attempt_dir)
    if value.get("state") in {"cancelled", "succeeded", "failed", "lost"}:
        return value
    atomic_json(attempt_dir / CANCEL_REQUEST_FILE, {"requested_at": utc_now()})
    value["state"] = "cancelling"
    value = atomic_json(status_path(attempt_dir), value)

    deadline = time.monotonic() + max(1.0, timeout_seconds)
    signalled: tuple[int, int] | None = None
    current = value
    while time.monotonic() < deadline:
        current = status(attempt_dir)
        if current.get("state") in {"cancelled", "succeeded", "failed", "lost"}:
            return current
        pid = current.get("workflow_pid")
        ticks = current.get("workflow_start_ticks")
        identity = (pid, ticks) if isinstance(pid, int) and isinstance(ticks, int) else None
        if identity is not None and process_matches(*identity) and signalled != identity:
            try:
                os.killpg(os.getpgid(identity[0]), signal.SIGTERM)
                signalled = identity
            except ProcessLookupError:
                pass
        time.sleep(0.2)

    if signalled is not None and process_matches(*signalled):
        try:
            os.killpg(os.getpgid(signalled[0]), signal.SIGKILL)
        except ProcessLookupError:
            pass

    terminal_deadline = time.monotonic() + 10.0
    while time.monotonic() < terminal_deadline:
        current = status(attempt_dir)
        if current.get("state") in {"cancelled", "succeeded", "failed", "lost"}:
            return current
        time.sleep(0.2)
    return status(attempt_dir)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    sub = value.add_subparsers(dest="command", required=True)
    for name in ("intent", "arm", "fence", "remove-storage", "stage-command", "read-command"):
        command = sub.add_parser(name)
        command.add_argument("--attempt-dir", required=True)
        command.add_argument("--intent-sha256", required=name != "intent")
        if name in {"stage-command", "read-command"}:
            command.add_argument("argv", nargs=argparse.REMAINDER)
    for name in ("prepare", "run", "status", "collect", "supervise"):
        command = sub.add_parser(name)
        command.add_argument("--attempt-dir", required=True)
    sub.add_parser("resource-capability")
    enter = sub.add_parser("exec-owned")
    enter.add_argument("--cgroup", required=True)
    enter.add_argument("--gate-fd", required=True, type=int)
    enter.add_argument("--ack-fd", required=True, type=int)
    enter.add_argument("argv", nargs=argparse.REMAINDER)
    cancel_command = sub.add_parser("cancel")
    cancel_command.add_argument("--attempt-dir", required=True)
    cancel_command.add_argument("--timeout-seconds", type=float, default=30.0)
    return value


def main() -> int:
    args = parser().parse_args()
    if args.command == "resource-capability":
        print(json.dumps(resource_capability(), sort_keys=True))
        return 0
    if args.command == "exec-owned":
        (Path(args.cgroup) / "cgroup.procs").write_text(str(os.getpid()))
        os.write(args.ack_fd, b"E")
        os.close(args.ack_fd)
        authorized = os.read(args.gate_fd, 1) == b"G"
        os.close(args.gate_fd)
        if not authorized:
            raise RuntimeError("supervisor disappeared before scientific launch authorization")
        argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        os.execvpe(argv[0], argv, os.environ)
    attempt_dir = Path(os.path.abspath(args.attempt_dir))
    if any(part.is_symlink() for part in (attempt_dir, *attempt_dir.parents)):
        raise RuntimeError("attempt namespace traverses a symlink")
    if args.command == "intent":
        result = initialize_owner(attempt_dir, json.load(sys.stdin))
    elif args.command in {"fence", "remove-storage"}:
        result = fence_owner(attempt_dir, args.intent_sha256, remove=args.command == "remove-storage")
    elif args.command == "arm":
        result = arm_owner(attempt_dir, args.intent_sha256)
    elif args.command in {"stage-command", "read-command"}:
        argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        stage_command(attempt_dir, args.intent_sha256, argv, reading=args.command == "read-command")
        return 0
    else:
        result = None
    if result is not None:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    if not attempt_dir.is_dir():
        raise RuntimeError("attempt directory is unavailable")
    if args.command == "supervise":
        return supervise(attempt_dir)
    with owner_guard(attempt_dir) as (_, owner_path):
        phases = {"staging"} if args.command == "prepare" else (
            {"armed"} if args.command == "run" else {"staging", "armed", "fenced"})
        check_owner(owner_path, phases=phases)
        if args.command == "prepare":
            result = prepare(attempt_dir)
        elif args.command == "run":
            result = start(attempt_dir)
        elif args.command == "status":
            result = status(attempt_dir)
        elif args.command == "cancel":
            result = cancel(attempt_dir, args.timeout_seconds)
        elif args.command == "collect":
            result = load_json(Path(load_json(envelope_path(attempt_dir))["output_directory"]) / RESULT_MANIFEST_FILE)
        else:
            raise RuntimeError("unsupported command")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"[:4000]}), file=sys.stderr)
        raise SystemExit(1)
