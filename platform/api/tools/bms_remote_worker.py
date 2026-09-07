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
    }


def supervise(attempt_dir: Path) -> int:
    envelope = load_json(envelope_path(attempt_dir))
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
                process = subprocess.Popen(
                    [str(value) for value in envelope["command"]],
                    cwd=str(envelope["working_directory"]),
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                    start_new_session=True,
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
                terminate_deadline: float | None = None
                while process.poll() is None:
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
    manifest_sha: str | None = None
    try:
        remote_logs = Path(str(envelope["output_directory"])) / "_remote"
        remote_logs.mkdir(parents=True, exist_ok=True)
        if log_path.is_file():
            shutil.copy2(log_path, remote_logs / "nextflow.log")
        supervisor_log = attempt_dir / "supervisor.log"
        if supervisor_log.is_file():
            shutil.copy2(supervisor_log, remote_logs / "supervisor.log")
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
            if not workflow_alive and not supervisor_alive:
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
    for name in ("prepare", "run", "status", "collect", "supervise"):
        command = sub.add_parser(name)
        command.add_argument("--attempt-dir", required=True)
    cancel_command = sub.add_parser("cancel")
    cancel_command.add_argument("--attempt-dir", required=True)
    cancel_command.add_argument("--timeout-seconds", type=float, default=30.0)
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
