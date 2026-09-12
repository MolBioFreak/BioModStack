#!/usr/bin/env python3
"""Durable execution-only worker used through the BMS SSH bridge."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import time
import tempfile
from contextlib import suppress
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
            if any(current.get(key) != value.get(key) for key in ("job_id", "attempt_id")):
                raise RuntimeError("Stale writer attempt identity mismatch")
            if current.get("state") in {"cancelled", "succeeded", "failed", "lost"}:
                return current
            if value.get("state") == "prepared":
                return current
            value = dict(value)
            for key in ("workflow_pid", "workflow_start_ticks", "supervisor_pid", "supervisor_start_ticks", "started_at", "boot_id"):
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


def boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def attempt_writers(identity: dict[str, Any]) -> list[int]:
    """Fenced descendants; component markers retain scope across setsid/orphaning."""
    if not identity.get("boot_id"):
        raise RuntimeError("Attempt has no boot identity; quiescence is unknown")
    if identity["boot_id"] != boot_id():
        return []
    owner = identity.get("supervisor_pid")
    ticks = identity.get("supervisor_start_ticks")
    if type(owner) is not int or owner <= 0 or type(ticks) is not int or ticks <= 0:
        raise RuntimeError("Attempt start has no durable process owner")
    scope = identity.get("component_scope")
    if scope is not None and (set(scope) != {"BMS_COMPONENT_CONTEXT", "BMS_COMPONENT_JOB_ID", "BMS_COMPONENT_OUTPUT_DIR"}
                              or any(not isinstance(v, str) or not v for v in scope.values())):
        raise RuntimeError("Invalid component writer scope")
    rows = {}
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_text().rsplit(")", 1)[1].split()
            if fields[0] not in {"Z", "X"}:
                rows[int(path.parent.name)] = (int(fields[1]), int(fields[3]), int(fields[19]))
        except (FileNotFoundError, ProcessLookupError):
            continue
    if owner in rows and rows[owner][2] != ticks:
        raise RuntimeError("Attempt process owner identity changed; refuse stale control")
    if scope is None and owner not in rows:
        raise RuntimeError("Attempt supervisor disappeared; writer quiescence is unknown")
    known = identity.setdefault("known_writers", {})
    owned = {pid for pid, start in known.items() if pid in rows and rows[pid][2] == start}
    if owner in rows:
        owned.add(owner)
    if scope is not None:
        markers = {f"{key}={value}".encode() for key, value in scope.items()}
        for pid, (_, _, start) in rows.items():
            # A task predating this native launch cannot be its descendant.
            if start < ticks:
                continue
            try:
                directory = Path(f"/proc/{pid}")
                if directory.stat().st_uid != os.getuid():
                    continue
                environment = set((directory / "environ").read_bytes().split(b"\0"))
                if markers <= environment and process_matches(pid, start):
                    owned.add(pid)
            except (FileNotFoundError, ProcessLookupError):
                continue
            except PermissionError:
                # Same UID is not ownership (other user namespaces can deny
                # environ). Proven/recorded descendants remain in owned below.
                continue
    while True:
        found = {pid for pid, (parent, session, _) in rows.items()
                 if parent in owned or (scope is None and session == owner)}
        if found <= owned:
            break
        owned.update(found)
    writers = sorted((owned & rows.keys()) - ({owner} if scope is None else set()))
    known.update({pid: rows[pid][2] for pid in writers})
    return writers


def _pidfd_call(name: str, *args: int) -> int:
    # Some standalone Python builds omit pidfd wrappers despite kernel support.
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, name, None)
    if function is not None:
        result = function(*args)
    else:
        if os.uname().machine not in {"x86_64", "aarch64"}:
            raise RuntimeError("Race-safe writer signaling unavailable on this platform")
        number = {"pidfd_open": 434, "pidfd_send_signal": 424}[name]
        result = libc.syscall(ctypes.c_long(number), *(ctypes.c_long(arg) for arg in args))
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return result


def quiesce_writers(identity: dict[str, Any], timeout_seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        writers = attempt_writers(identity)
        if not writers:
            return True
        sig = signal.SIGTERM if time.monotonic() < deadline else signal.SIGKILL
        for pid in writers:
            ticks = identity["known_writers"][pid]
            try:
                # Bind the signal to this task, not a PID reusable after the check.
                fd = _pidfd_call("pidfd_open", pid, 0)
            except ProcessLookupError:
                continue
            try:
                if process_matches(pid, ticks):
                    _pidfd_call("pidfd_send_signal", fd, sig, 0, 0)
            except ProcessLookupError:
                pass
            finally:
                os.close(fd)
        if time.monotonic() >= deadline + 10.0:
            return False
        time.sleep(0.2)

def process_start_ticks(pid: int) -> int | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        return int(fields[19]) if fields[0] not in {"Z", "X"} else None
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
        "boot_id": boot_id(),
        "quiescent": False,
        "supervisor_pid": None,
        "supervisor_start_ticks": None,
        "workflow_pid": None,
        "workflow_start_ticks": None,
        "exit_code": None,
        "started_at": None,
        "completed_at": None,
        "result_manifest_sha256": None,
        "error": None,
        "generation": 0,
        "native_output_directory": str(envelope["output_directory"]),
        "control_group": None,
    }


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
        if (attempt_dir / "launch-claim.json").exists():
            return current  # The previous spawn may have arrived; never replay.
        # A prepared receipt can survive a controller disconnect. Revalidate the
        # package before the idempotent start resumes it.
        verify_bundle(attempt_dir)
        atomic_json(attempt_dir / "launch-claim.json", {
            "attempt_id": current["attempt_id"], "boot_id": boot_id(),
        })
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


def build_result_manifest(attempt_dir: Path, envelope: dict[str, Any], exit_code: int,
                          *, generation: int = 0) -> dict[str, Any]:
    if type(generation) is not int or generation < 0:
        raise RuntimeError("Invalid shared root generation")
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
        "generation": generation,
        "attempt_id": str(envelope["attempt_id"]),
        "job_id": str(envelope["job_id"]),
        "exit_code": int(exit_code),
        "completed_at": utc_now(),
        "artifacts": artifacts,
        "source_revision": str(envelope["source_revision"]),
        "source_tree": str(envelope["source_tree"]),
        "execution_envelope_sha256": sha256_file(envelope_path(attempt_dir)),
    }


def supervise(attempt_dir: Path) -> int:
    # Keep a sole owner through compilation, science, sealing and publication.
    with (attempt_dir / "supervisor.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _supervise_owned(attempt_dir)


def _component_checkpoint_runtime(envelope: dict):
    """Use only the launch-bound source/context, not SSH shell configuration."""
    value = (envelope.get("environment") or {}).get("BMS_COMPONENT_CONTEXT")
    if value is None:
        return None
    path = Path(value)
    if not path.is_absolute() or path.is_symlink():
        raise RuntimeError("Checkpoint context must be absolute and symlink-free")
    records = [record for record in envelope.get("files", [])
               if record.get("relative_path") == "inputs/component-context.json"]
    if (len(records) != 1 or path.stat().st_size != records[0].get("size_bytes")
            or sha256_file(path) != records[0].get("sha256")):
        raise RuntimeError("Checkpoint context differs from source-bound launch input")
    context = load_json(path)
    if (context.get("attempt_id") != envelope.get("attempt_id")
            or context.get("root_job_id") != envelope.get("job_id")
            or Path(context["artifact_root"]).resolve() != Path(envelope["output_directory"]).resolve()):
        raise RuntimeError("Checkpoint context does not match immutable attempt")
    source = Path(context["working_directory"])
    if not source.is_absolute() or source.resolve() != Path(envelope["working_directory"]).resolve():
        raise RuntimeError("Checkpoint source must match launch-bound source")
    sys.path.insert(0, str(source))
    sys.path.insert(0, str(source / "platform" / "api"))
    from scripts.lib.component_adapter import runtime_from_environment
    return runtime_from_environment(path)


def checkpoint_control(attempt_dir: Path, *, attempt_id: str, expected_boot_id: str,
                       lease_id: str, checkpoint_id: str, checkpoint_sha256: str,
                       decision: dict, continuation_lease_id: str, resource_admission: dict | None = None,
                       operation_id: str | None = None, observe_only: bool = False) -> dict:
    """Replay one explicit decision across ledger/status/spawn crash boundaries."""
    if not operation_id:
        raise RuntimeError("Checkpoint durable operation identity required")
    binding = dict(operation_id=operation_id, attempt_id=attempt_id, boot_id=expected_boot_id,
        original_lease_id=lease_id, checkpoint_id=checkpoint_id, checkpoint_sha256=checkpoint_sha256,
        decision=decision, continuation_lease_id=continuation_lease_id, resource_admission=resource_admission)
    with (attempt_dir / "start.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        current = status(attempt_dir)
        if (current.get("attempt_id") != attempt_id or current.get("boot_id") != expected_boot_id
                or expected_boot_id != boot_id()):
            raise RuntimeError("Checkpoint attempt/boot authority conflicts")
        runtime = _component_checkpoint_runtime(load_json(envelope_path(attempt_dir)))
        if runtime is None or runtime.lease_id != lease_id:
            raise RuntimeError("Checkpoint lease authority conflicts")
        operation = runtime.checkpoint_operation(operation_id)
        if operation is not None and operation['binding'] != binding:
            raise RuntimeError("Checkpoint immutable operation binding conflicts")
        if observe_only or (operation is not None and operation['state'] == 'rejected'):
            return dict(operation=operation, worker_status=current)
        if (attempt_dir / CANCEL_REQUEST_FILE).exists():
            raise RuntimeError("Cancelled attempt cannot continue")
        if operation is None:
            if current.get('state') != 'awaiting_input' or not current.get('quiescent'):
                raise RuntimeError("Checkpoint quiescence authority conflicts")
            checkpoint = runtime.checkpoint_status(checkpoint_id)
            if checkpoint["checkpoint_sha256"] != checkpoint_sha256:
                raise RuntimeError("Checkpoint artifact-set binding conflicts")
            context = runtime.context
            from services.nextflow import (compile_component_checkpoint_continuation,
                component_checkpoint_parent_snapshot, component_checkpoint_resources)
            try:
                invocation = compile_component_checkpoint_continuation(context, checkpoint, decision)
                runtime.resume_checkpoint(checkpoint_id, checkpoint_sha256=checkpoint_sha256,
                    decision=decision, actor="jobs.resume", boot_id=expected_boot_id,
                    invocation=invocation, continuation_lease_id=continuation_lease_id,
                    parent_snapshot=component_checkpoint_parent_snapshot(invocation, context),
                    resources=component_checkpoint_resources(invocation, context, resource_admission),
                    operation_binding=binding)
            except ValueError as exc:
                operation = runtime.reject_checkpoint_operation(binding, str(exc))
                return dict(operation=operation, worker_status=current)
            operation = runtime.checkpoint_operation(operation_id)
        edge, generation = operation['edge'], operation['generation']
        if current.get('generation', 0) == generation:
            if current.get('continuation_lease_id') != continuation_lease_id:
                raise RuntimeError('Checkpoint continuation generation conflicts')
            if current.get('state') != 'prepared' or current.get('supervisor_pid') is not None:
                return dict(operation=operation, worker_status=current)
        else:
            if (current.get('generation', 0) != generation - 1
                    or current.get('state') != 'awaiting_input' or not current.get('quiescent')):
                raise RuntimeError('Checkpoint predecessor generation conflicts')
            with status_path(attempt_dir).with_suffix(".json.lock").open("a+b") as status_lock:
                fcntl.flock(status_lock.fileno(), fcntl.LOCK_EX)
                latest = load_json(status_path(attempt_dir))
                if latest != current or (attempt_dir / CANCEL_REQUEST_FILE).exists():
                    raise RuntimeError("Checkpoint status changed during continuation authorization")
                latest.update(state="prepared", supervisor_pid=None, supervisor_start_ticks=None,
                    workflow_pid=None, workflow_start_ticks=None, quiescent=False, checkpoints=[],
                    continuation_lease_id=continuation_lease_id, exit_code=None, completed_at=None,
                    started_at=None, result_manifest_sha256=None, error=None, control_group=None,
                    generation=generation, plan_sha256=edge['plan_sha256'],
                    native_output_directory=edge['parent_snapshot']['output_dir'])
                _write_atomic_json(status_path(attempt_dir), latest)
        atomic_json(attempt_dir / ("checkpoint-launch-" + hashlib.sha256(operation_id.encode()).hexdigest() + ".json"), binding)
        # A claimed but not spawned command may be replayed. The existing
        # supervisor.lock plus prepared/no-PID check serializes actual execution;
        # a delayed duplicate supervisor cannot start a second root generation.
        with (attempt_dir / "supervisor.log").open("ab", buffering=0) as log:
            subprocess.Popen([sys.executable, os.path.realpath(__file__), "supervise", "--attempt-dir", str(attempt_dir)],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, close_fds=True, start_new_session=True)
        return dict(operation=operation, worker_status=load_json(status_path(attempt_dir)))


def component_retry_control(attempt_dir: Path, *, attempt_id: str, expected_boot_id: str,
                            lease_id: str, component_id: str, operation_id: str,
                            actor: str, failure_code: str | None = None,
                            continuation_lease_id: str | None = None,
                            resource_admission: dict | None = None, observe_only: bool = False) -> dict:
    """Authorize/replay one shared retry edge without replaying an uncertain spawn."""
    with (attempt_dir / "start.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        current = status(attempt_dir)
        if (current.get("attempt_id") != attempt_id or current.get("boot_id") != expected_boot_id
                or expected_boot_id != boot_id()):
            raise RuntimeError("Retry attempt/boot authority conflicts")
        envelope = load_json(envelope_path(attempt_dir))
        runtime = _component_checkpoint_runtime(envelope)
        if runtime is None or runtime.lease_id != lease_id:
            raise RuntimeError("Retry original lease authority conflicts")
        # A transport replay must observe the original edge before compiling or
        # considering a second reservation. Its generation may already be running.
        edge = runtime.retry_status(operation_id)
        if edge is not None and (edge["component_id"] != component_id or edge["actor"] != actor):
            raise RuntimeError("Retry operation identity conflicts")
        if observe_only:
            return {"retry": edge, "worker_status": current}
        if not isinstance(resource_admission, dict) or not continuation_lease_id or not failure_code:
            raise RuntimeError("Retry requires continuation lease and resource admission")
        if edge is not None and edge["continuation_lease_id"] != continuation_lease_id:
            raise RuntimeError("Retry continuation lease conflicts")
        claim = attempt_dir / ("retry-launch-" + hashlib.sha256(operation_id.encode()).hexdigest() + ".json")
        if claim.exists():
            if edge is None or load_json(claim) != {"operation_id": operation_id,
                    "attempt_id": attempt_id, "boot_id": expected_boot_id,
                    "continuation_lease_id": continuation_lease_id}:
                raise RuntimeError("Retry launch claim conflicts")
            return {**edge, "worker_status": current}
        if (current.get("state") != "failed" or not current.get("quiescent")) and not (
                edge is not None and current.get("state") == "prepared"
                and current.get("continuation_lease_id") == continuation_lease_id
                and current.get("supervisor_pid") is None):
            raise RuntimeError("Retry requires a failed quiescent predecessor")
        if (attempt_dir / CANCEL_REQUEST_FILE).exists():
            raise RuntimeError("Cancelled attempt cannot retry")
        if edge is None:
            verify_bundle(attempt_dir)
        from scripts.lib.component_adapter import retry_component_workflow
        edge = retry_component_workflow(Path(envelope["environment"]["BMS_COMPONENT_CONTEXT"]),
            component_id=component_id, operation_id=operation_id, failure_code=failure_code,
            actor=actor, boot_id=expected_boot_id, continuation_lease_id=continuation_lease_id,
            resources=resource_admission)
        output = Path(edge["parent_snapshot"]["output_dir"]).resolve()
        original = Path(envelope["output_directory"]).resolve()
        if output == original or not output.is_relative_to(original):
            raise RuntimeError("Retry output must be a distinct contained generation")
        with status_path(attempt_dir).with_suffix(".json.lock").open("a+b") as status_lock:
            fcntl.flock(status_lock.fileno(), fcntl.LOCK_EX)
            latest = load_json(status_path(attempt_dir))
            if latest != current or (attempt_dir / CANCEL_REQUEST_FILE).exists():
                raise RuntimeError("Retry status changed during authorization")
            latest.update(state="prepared", supervisor_pid=None, supervisor_start_ticks=None,
                workflow_pid=None, workflow_start_ticks=None, quiescent=False, checkpoints=[],
                continuation_lease_id=continuation_lease_id, exit_code=None, completed_at=None,
                started_at=None, result_manifest_sha256=None, error=None, control_group=None,
                generation=edge["generation"], plan_sha256=edge["plan_sha256"],
                native_output_directory=str(output))
            _write_atomic_json(status_path(attempt_dir), latest)
        atomic_json(claim, dict(operation_id=operation_id, attempt_id=attempt_id,
            boot_id=expected_boot_id, continuation_lease_id=continuation_lease_id))
        # Claim precedes spawn: any ambiguity retains ownership; never duplicate science.
        with (attempt_dir / "supervisor.log").open("ab", buffering=0) as log:
            subprocess.Popen([sys.executable, os.path.realpath(__file__), "supervise", "--attempt-dir", str(attempt_dir)],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, close_fds=True, start_new_session=True)
        return {**edge, "worker_status": status(attempt_dir)}


def _generation_envelope(envelope: dict, runtime: Any) -> dict:
    """Project native output only; transport retains the whole attempt root."""
    root = runtime.root_state() if runtime is not None else None
    edge = (root or {}).get("continuation_edge")
    if not edge:
        return dict(envelope)
    output = Path(edge["parent_snapshot"]["output_dir"]).resolve()
    original = Path(envelope["output_directory"]).resolve()
    if not output.is_relative_to(original):
        raise RuntimeError("Continuation output escapes original artifact custody")
    return {**envelope, "output_directory": str(output)}


def _source_helpers(envelope: dict) -> None:
    source = Path(envelope["working_directory"])
    if not source.is_absolute() or not source.is_dir():
        raise RuntimeError("Launch-bound source is unavailable")
    sys.path.insert(0, str(source))
    sys.path.insert(0, str(source / "platform" / "api"))


def _publish_root_diagnostics(attempt_dir: Path, envelope: dict, environment: dict,
                              offsets: dict[str, int]) -> None:
    from scripts.lib.native_diagnostics import redact_text
    destination = Path(envelope["output_directory"]) / "_remote"
    destination.mkdir(parents=True, exist_ok=True)
    if any(path.is_symlink() for path in (destination, *destination.parents)):
        raise RuntimeError("Diagnostic destination is a symlink")
    for source, name in ((attempt_dir / "nextflow.log", "nextflow.log"),
                         (attempt_dir / "supervisor.log", "supervisor.log"),
                         (Path(envelope["working_directory"]) / ".nextflow.log", "nextflow-internal.log"),
                         (Path(envelope["working_directory"]) / "component-root.log", "component-root.log")):
        if source.is_symlink():
            raise RuntimeError("Named diagnostic source is a symlink")
        if not source.is_file():
            continue
        if name == "nextflow-internal.log" and source.stat().st_mtime_ns == offsets.get("internal_mtime_ns"):
            continue  # No current-generation native launch wrote this file.
        with source.open("rb") as raw:
            raw.seek(offsets.get(name, 0))
            text = redact_text(raw.read().decode("utf-8", errors="replace"), environment=environment)
        # Exclusive copies: no previously sealed diagnostic/scientific bytes change.
        with (destination / name).open("x", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())


def _supervise_owned(attempt_dir: Path) -> int:
    import ctypes
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise RuntimeError("Cannot establish attempt child-subreaper ownership")
    envelope = load_json(envelope_path(attempt_dir))
    current = load_json(status_path(attempt_dir))
    if current.get("state") not in {"prepared", "cancelling"}:
        raise RuntimeError("remote attempt is not prepared for launch")
    if (
        str(current.get("attempt_id")) != str(envelope.get("attempt_id"))
        or str(current.get("job_id")) != str(envelope.get("job_id"))
    ):
        raise RuntimeError("prepared status does not match the execution envelope")
    if current.get("supervisor_pid") is not None:
        raise RuntimeError("Prior supervisor identity exists; launch cannot be replayed")
    if current.get("boot_id") != boot_id():
        raise RuntimeError("Attempt belongs to a prior boot; explicit recovery required")
    current.update(
        {
            "state": "cancelling" if (attempt_dir / CANCEL_REQUEST_FILE).exists() else "running",
            "supervisor_pid": os.getpid(),
            "supervisor_start_ticks": process_start_ticks(os.getpid()),
            "started_at": current.get("started_at") or utc_now(),
        }
    )
    atomic_json(status_path(attempt_dir), current)
    environment = os.environ.copy()
    log_path = attempt_dir / "nextflow.log"
    exit_code = 1
    error: str | None = None
    monitor = None
    diagnostic_offsets: dict[str, int] = {}
    publication_envelope = dict(envelope)
    try:
        if current.get("continuation_lease_id"):
            for source, name in ((attempt_dir / "nextflow.log", "nextflow.log"),
                                 (attempt_dir / "supervisor.log", "supervisor.log"),
                                 (Path(envelope["working_directory"]) / "component-root.log", "component-root.log")):
                if source.is_symlink():
                    raise RuntimeError("Named diagnostic source is a symlink")
                if source.is_file():
                    diagnostic_offsets[name] = source.stat().st_size
        internal_log = Path(envelope["working_directory"]) / ".nextflow.log"
        if internal_log.is_file():
            diagnostic_offsets["internal_mtime_ns"] = internal_log.stat().st_mtime_ns
        _source_helpers(envelope)
        runtime = _component_checkpoint_runtime(envelope)
        publication_envelope = _generation_envelope(envelope, runtime)
        Path(publication_envelope["output_directory"]).mkdir(parents=True, exist_ok=True)
        if (Path(publication_envelope["output_directory"]) / RESULT_MANIFEST_FILE).exists():
            raise RuntimeError("Refuse to relaunch into a sealed result generation")
        latest = load_json(status_path(attempt_dir))
        latest["native_output_directory"] = publication_envelope["output_directory"]
        latest["plan_sha256"] = runtime.context['plan_sha256'] if runtime is not None else None
        latest["generation"] = ((runtime.root_state() or {}).get("generation", 0) if runtime is not None else 0)
        if type(latest["generation"]) is not int or latest["generation"] < 0:
            raise RuntimeError("Invalid shared root generation")
        atomic_json(status_path(attempt_dir), latest)
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
        # Remote workflow metadata is envelope-owned, never worker-shell or
        # legacy secret-file authority. No BMS callback connectivity is needed.
        environment.update({
            "BMS_REMOTE_EXECUTION": "1",
            "BMS_REMOTE_ATTEMPT_ID": str(envelope["attempt_id"]),
            "BMS_REMOTE_JOB_ID": str(envelope["job_id"]),
            "BMS_REMOTE_OUTPUT_ROOT": str(publication_envelope["output_directory"]),
        })
        trusted_environment = envelope.get("environment") or {}
        for key in ("BMS_COMPONENT_CONTEXT", "APPTAINERENV_BMS_COMPONENT_CONTEXT",
                    "BMS_PORTABLE_INPUT_BINDINGS", "APPTAINERENV_BMS_PORTABLE_INPUT_BINDINGS"):
            if key in trusted_environment:
                environment[key] = trusted_environment[key]
            else:
                environment.pop(key, None)
        for key in ("API_BASE_URL", "BMS_REMOTE_API_BASE_URL", "BMS_STAGE_REPORT_TOKEN"):
            environment.pop(key, None)
        if runtime is not None:
            environment["BMS_TARGET_RESOURCES"] = json.dumps(runtime.context["resources"], sort_keys=True)
        if (attempt_dir / CANCEL_REQUEST_FILE).exists():
            exit_code = -15
        else:
            observation = envelope.get("resource_monitor")
            edge = (runtime.root_state() or {}).get("continuation_edge") if runtime is not None else None
            if edge is not None:
                observation = (edge.get("resources") or {}).get("resource_monitor")
            try:
                if observation is not None:
                    if not isinstance(observation, dict) or set(observation) != {"params", "execution"}:
                        raise RuntimeError("Resource monitor envelope is invalid")
                    from services.resource_usage_evidence import WorkflowResourceMonitor, remote_resource_execution_owner
                    params = observation["params"]
                    execution = dict(observation["execution"])
                    if set(execution) != {"generation", "attempt", "attempt_id"}:
                        raise RuntimeError("Resource execution must contain parent-issued identities only")
                    handoff = params.get("_global_resource_admission", {})
                    if (execution.get("attempt_id") != envelope["attempt_id"]
                            or any(handoff.get(key) != envelope[key] for key in ("source_revision", "source_tree"))):
                        raise RuntimeError("Resource observation source/attempt conflicts")
                    owner = remote_resource_execution_owner()
                    execution.update(owner)
                    latest = load_json(status_path(attempt_dir))
                    if any(latest.get(key) != owner[key] for key in ("boot_id", "supervisor_pid", "supervisor_start_ticks")):
                        raise RuntimeError("Resource observer differs from durable supervisor")
                    latest["control_group"] = owner["control_group"]
                    atomic_json(status_path(attempt_dir), latest)
                    monitor = WorkflowResourceMonitor.from_remote_execution(job_id=str(envelope["job_id"]),
                        params=params, execution=execution,
                        checkpoint_path=attempt_dir / "resource-observation.json")
                    monitor.start()
            except Exception:
                # Admission and process ownership are independently enforced;
                # invalid/unavailable observations are never accepted as evidence.
                if monitor is not None:
                    with suppress(Exception):
                        monitor.stop_sampling()
                monitor = None
            with log_path.open("ab", buffering=0) as log:
                process = subprocess.Popen(
                    [str(value) for value in envelope["command"]],
                    cwd=str(envelope["working_directory"]),
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                    start_new_session=False,
                )
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
                while process.poll() is None:
                    if (attempt_dir / CANCEL_REQUEST_FILE).exists():
                        if monitor is not None:
                            with suppress(Exception):
                                monitor.stop_sampling()
                        if not quiesce_writers(load_json(status_path(attempt_dir))):
                            raise RuntimeError("Cancellation writers remain active")
                    time.sleep(0.2)
                exit_code = int(process.wait())
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:4000]
        exit_code = 1
    # A returned root process does not prove its descendants stopped.
    if monitor is not None:
        with suppress(Exception):
            monitor.stop_sampling()
    if not quiesce_writers(load_json(status_path(attempt_dir))):
        raise RuntimeError("Attempt writers did not quiesce; retain ownership")
    while True:
        try:
            if os.waitpid(-1, os.WNOHANG)[0] == 0:
                break
        except ChildProcessError:
            break
    resource_receipt = None
    if monitor is not None:
        try:
            resource_receipt = monitor.finish(outcome=("cancelled" if (attempt_dir / CANCEL_REQUEST_FILE).exists()
                else "completed" if exit_code == 0 else "failed"))
            if resource_receipt.get("complete") is not True:
                resource_receipt = None
        except Exception:
            resource_receipt = None
    # Review is durable logical pause, not a successful result package. Keep
    # all existing process/boot and descendant quiescence fences above intact.
    if not (attempt_dir / CANCEL_REQUEST_FILE).exists():
        try:
            runtime = _component_checkpoint_runtime(envelope)
            if runtime is not None:
                from scripts.open_stage_gate import component_checkpoint_projection
                checkpoints = component_checkpoint_projection(runtime)
                root_state = runtime.root_state() or {}
                if checkpoints:
                    if root_state.get("state") != "paused":
                        raise RuntimeError("Pending checkpoint has no durable paused root")
                    latest = load_json(status_path(attempt_dir))
                    latest.update(state="awaiting_input", quiescent=True, exit_code=exit_code,
                                  checkpoints=checkpoints, result_manifest_sha256=None, error=None)
                    atomic_json(status_path(attempt_dir), latest)
                    return 0
        except Exception as exc:
            error = f"Checkpoint integrity: {type(exc).__name__}: {exc}"[:4000]
            exit_code = 1
    manifest_sha: str | None = None
    try:
        output_manifest = Path(envelope["output_directory"]) / RESULT_MANIFEST_FILE
        # The root manifest is the current transport index, not a native science
        # file. Preserve its exact previous bytes before advancing that pointer.
        if output_manifest.exists():
            if publication_envelope["output_directory"] == envelope["output_directory"]:
                raise RuntimeError("Refuse to rewrite a sealed native generation")
            if output_manifest.is_symlink():
                raise RuntimeError("Current result manifest is a symlink")
            previous = output_manifest.read_bytes()
            archive = output_manifest.parent / "_remote" / "result-manifests"
            if any(path.is_symlink() for path in (archive, *archive.parents)):
                raise RuntimeError("Previous manifest archive contains a symlink")
            archive.mkdir(parents=True, exist_ok=True)
            archived = archive / (hashlib.sha256(previous).hexdigest() + ".json")
            if archived.exists():
                if archived.read_bytes() != previous:
                    raise RuntimeError("Previous manifest archive conflicts")
            else:
                with archived.open("xb") as handle:
                    handle.write(previous)
                    handle.flush()
                    os.fsync(handle.fileno())
        if resource_receipt is not None:
            receipt_path = Path(publication_envelope["output_directory"]) / ".bms-resource-usage.json"
            created = False
            try:
                encoded = canonical_bytes(resource_receipt) + b"\n"
                with receipt_path.open("xb") as handle:
                    created = True
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
            except (OSError, TypeError, ValueError):
                if created:
                    with suppress(OSError):
                        receipt_path.unlink()
        _publish_root_diagnostics(attempt_dir, publication_envelope, environment, diagnostic_offsets)
        manifest = build_result_manifest(attempt_dir, envelope, exit_code,
            generation=load_json(status_path(attempt_dir))["generation"])
        atomic_json(output_manifest, manifest)
        manifest_sha = sha256_file(output_manifest)
    except Exception as exc:
        manifest_error = f"{type(exc).__name__}: {exc}"[:4000]
        error = f"{error}; {manifest_error}"[:4000] if error else manifest_error
        exit_code = 1
    if error is not None:
        try:
            from scripts.lib.native_diagnostics import redact_text
            error = redact_text(error, environment=environment)
        except Exception:
            error = "Worker failed; source-bound diagnostic filtering unavailable"
    latest = load_json(status_path(attempt_dir))
    cancellation_requested = (
        (attempt_dir / CANCEL_REQUEST_FILE).exists()
        or latest.get("state") in {"cancelling", "cancelled"}
    )
    terminal_state = "cancelled" if cancellation_requested else ("succeeded" if exit_code == 0 else "failed")
    latest.update(
        {
            "state": terminal_state,
            "quiescent": True,
            "exit_code": exit_code,
            "completed_at": utc_now(),
            "result_manifest_sha256": manifest_sha,
            "error": error,
        }
    )
    atomic_json(status_path(attempt_dir), latest)
    return exit_code


def workflow_activity(attempt_dir: Path, identity: dict[str, Any], *,
                      envelope: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Bounded advisory stage metadata; never scientific output or completion proof."""
    import re
    import stat
    from datetime import datetime, timezone

    try:
        envelope = envelope if envelope is not None else load_json(envelope_path(attempt_dir))
        declared = envelope.get('output_directory')
        if not isinstance(declared, str) or not Path(declared).is_absolute():
            return None
        transport_root = Path(declared).resolve()
        root = Path(identity.get('native_output_directory') or declared)
        if not root.is_absolute() or not root.resolve().is_relative_to(transport_root):
            return None
    except (OSError, ValueError, TypeError, RuntimeError):
        return None
    directory = root / '.bms-stage-receipts'
    if any(path.is_symlink() for path in (directory, *directory.parents)):
        return None
    try:
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return None
    latest = None
    states = {'start': 'started', 'complete': 'completed', 'failed': 'failed'}
    try:
        names = os.listdir(directory_fd)
        if len(names) > 1024:
            return None
        for name in names:
            if not name.endswith('.json'):
                continue
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
                try:
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                        continue
                    payload = json.loads(os.read(fd, 65537))
                finally:
                    os.close(fd)
                if (not isinstance(payload, dict)
                        or set(payload) != {'schema', 'job_id', 'attempt_id', 'stage', 'status', 'outputs'}
                        or payload['schema'] != 'bms.remote-stage-receipt.v1'
                        or payload['job_id'] != identity.get('job_id')
                        or payload['attempt_id'] != identity.get('attempt_id')
                        or not isinstance(payload['stage'], str)
                        or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', payload['stage'])
                        or payload['status'] not in states
                        or not isinstance(payload['outputs'], list)):
                    continue
                expected = payload['stage'] + ('.start.json' if payload['status'] == 'start' else '.terminal.json')
                if name != expected:
                    continue
                record = {'stage': payload['stage'], 'state': states[payload['status']],
                          'updated_at': datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat()}
                key = (info.st_mtime_ns, name)
                if latest is None or key > latest[0]:
                    latest = (key, record)
            except (OSError, ValueError, TypeError):
                continue
    finally:
        os.close(directory_fd)
    return latest[1] if latest else None


def status(attempt_dir: Path) -> dict[str, Any]:
    with status_path(attempt_dir).with_suffix(".json.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        value = load_json(status_path(attempt_dir))
        previous = dict(value)
        envelope = load_json(envelope_path(attempt_dir))
        if any(value.get(key) != envelope.get(key) for key in ("job_id", "attempt_id")):
            raise RuntimeError("Attempt status identity mismatch")
        rebooted = bool(value.get("boot_id") and value["boot_id"] != boot_id())
        cancellation_requested = (attempt_dir / CANCEL_REQUEST_FILE).exists()
        supervisor_alive = (not rebooted and process_matches(
            value.get("supervisor_pid"), value.get("supervisor_start_ticks")))
        if value.get("state") not in {"cancelled", "succeeded", "failed", "lost"}:
            if rebooted:
                value.update(state="lost", quiescent=True, completed_at=utc_now(),
                             error="Worker rebooted; explicit supported recovery required")
            elif cancellation_requested and value.get("state") == "awaiting_input":
                # The former subreaper sealed the pause after joining every writer.
                # Keep that proof; absence of a supervisor alone is insufficient.
                runtime = _component_checkpoint_runtime(envelope)
                root = runtime.root_state() if runtime is not None else None
                if (runtime is not None and value.get("quiescent") and not supervisor_alive
                        and not process_matches(value.get("workflow_pid"), value.get("workflow_start_ticks"))
                        and root and root["state"] in {"paused", "resume_ready"} and root.get("quiescent")
                        and root.get("boot_id") == value.get("boot_id")):
                    runtime.request_cancel()
                    value.update(state="cancelled", quiescent=True, completed_at=utc_now(), exit_code=-15)
                else:
                    value.update(quiescent=False, error="Paused cancellation awaits durable writer quiescence")
            elif cancellation_requested and value.get('state') == 'prepared' and value.get('continuation_lease_id'):
                runtime = _component_checkpoint_runtime(envelope)
                root = runtime.root_state() if runtime is not None else None
                edge = (root or {}).get('continuation_edge') or {}
                if (root and root['state'] == 'resume_ready' and root.get('quiescent')
                        and root.get('boot_id') == value.get('boot_id')
                        and root.get('generation') == value.get('generation')
                        and edge.get('continuation_lease_id') == value['continuation_lease_id']
                        and value.get('supervisor_pid') is None and value.get('workflow_pid') is None):
                    runtime.request_cancel()
                    value.update(state='cancelled', quiescent=True, completed_at=utc_now(), exit_code=-15)
            elif cancellation_requested and value.get("state") == "prepared" and not (attempt_dir / "launch-claim.json").exists():
                value.update(state="cancelled", quiescent=True, completed_at=utc_now(), exit_code=-15)
            elif value.get("state") in {"running", "cancelling"} and not supervisor_alive:
                # Orphans may have escaped their session before owner death.
                # No same-boot absence inference grants release authority.
                value.update(state="lost", quiescent=False, completed_at=utc_now(),
                             error="Attempt supervisor disappeared; writer quiescence is unknown")
            elif cancellation_requested:
                value["state"] = "cancelling"
        elif rebooted and not value.get("quiescent"):
            value["quiescent"] = True
        if value != previous:
            _write_atomic_json(status_path(attempt_dir), value)
        # Even terminal publication is not permission to race the publisher.
        if supervisor_alive:
            value["quiescent"] = False
        value.pop('activity', None)
        if value.get('state') in {'running', 'cancelling'}:
            activity = workflow_activity(attempt_dir, value, envelope=envelope)
            if activity is not None:
                value['activity'] = activity
        return value


def cancel(attempt_dir: Path, timeout_seconds: float) -> dict[str, Any]:
    # Serialize durable intent with the entire start handoff, not just status.
    with (attempt_dir / "start.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        value = status(attempt_dir)
        if value.get("state") in {"cancelled", "succeeded", "failed", "lost"} and value.get("quiescent"):
            return value
        atomic_json(attempt_dir / CANCEL_REQUEST_FILE, {
            "requested_at": utc_now(), "attempt_id": value["attempt_id"],
            "boot_id": value.get("boot_id"),
        })
    # The subreaper is the sole stop/join/seal authority. Never kill its process
    # group from an SSH poller: doing so kills the diagnostic publisher too.
    deadline = time.monotonic() + max(1.0, timeout_seconds) + 10.0
    while time.monotonic() < deadline:
        current = status(attempt_dir)
        if current.get("state") in {"cancelled", "succeeded", "failed", "lost"}:
            if current.get("quiescent") or current.get("state") == "lost":
                return current
        time.sleep(0.2)
    return status(attempt_dir)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    sub = value.add_subparsers(dest="command", required=True)
    for name in ("prepare", "run", "status", "collect", "supervise"):
        command = sub.add_parser(name)
        command.add_argument("--attempt-dir", required=True)
    cancel_command = sub.add_parser("cancel")
    cancel_command.add_argument("--attempt-dir", required=True)
    cancel_command.add_argument("--timeout-seconds", type=float, default=30.0)
    for name in ("checkpoint-resume", "checkpoint-status"):
        checkpoint = sub.add_parser(name)
        for field in ("attempt-dir", "attempt-id", "expected-boot-id", "lease-id", "checkpoint-id",
                      "checkpoint-sha256", "operation-id", "resource-admission-json", "decision-json", "continuation-lease-id"):
            checkpoint.add_argument("--" + field, required=True)
    for name in ("component-retry", "component-retry-status"):
        retry = sub.add_parser(name)
        for field in ("attempt-dir", "attempt-id", "expected-boot-id", "lease-id", "component-id", "operation-id", "actor"):
            retry.add_argument("--" + field, required=True)
        if name == "component-retry":
            for field in ("failure-code", "continuation-lease-id", "resource-admission-json"):
                retry.add_argument("--" + field, required=True)
    return value


def main() -> int:
    args = parser().parse_args()
    attempt_dir = Path(args.attempt_dir).resolve()
    if not attempt_dir.is_dir():
        raise RuntimeError("attempt directory is unavailable")
    if args.command == "prepare":
        result = prepare(attempt_dir)
    elif args.command == "run":
        result = start(attempt_dir)
    elif args.command == "status":
        result = status(attempt_dir)
    elif args.command == "cancel":
        result = cancel(attempt_dir, args.timeout_seconds)
    elif args.command in {"checkpoint-resume", "checkpoint-status"}:
        result = checkpoint_control(attempt_dir, attempt_id=args.attempt_id,
            expected_boot_id=args.expected_boot_id, lease_id=args.lease_id,
            checkpoint_id=args.checkpoint_id, checkpoint_sha256=args.checkpoint_sha256,
            decision=json.loads(args.decision_json), continuation_lease_id=args.continuation_lease_id,
            resource_admission=json.loads(args.resource_admission_json), operation_id=args.operation_id,
            observe_only=args.command == 'checkpoint-status')
    elif args.command in {"component-retry", "component-retry-status"}:
        observe = args.command == "component-retry-status"
        result = component_retry_control(attempt_dir, attempt_id=args.attempt_id,
            expected_boot_id=args.expected_boot_id, lease_id=args.lease_id,
            component_id=args.component_id, operation_id=args.operation_id, actor=args.actor,
            observe_only=observe, failure_code=None if observe else args.failure_code,
            continuation_lease_id=None if observe else args.continuation_lease_id,
            resource_admission=None if observe else json.loads(args.resource_admission_json))
    elif args.command == "collect":
        result = load_json(Path(load_json(envelope_path(attempt_dir))["output_directory"]) / RESULT_MANIFEST_FILE)
    elif args.command == "supervise":
        return supervise(attempt_dir)
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
