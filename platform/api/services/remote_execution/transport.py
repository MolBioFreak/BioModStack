"""OpenSSH and rsync transport for an activated execution target."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import shlex
import signal
import tempfile
import sys
import time

from .result_generation import durable_json, transfer_marker
from .transfer_supervisor import SCHEMA, process_identity
from dataclasses import dataclass
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Sequence

from paths import get_data_root


class RemoteTransportError(RuntimeError):
    pass


MAX_DIAGNOSTIC_BYTES = 512
HELPER_FAILURE_MESSAGES = {
    'request_too_large': 'The helper request exceeds its control-message budget; verify the by-reference producer before retrying.',
    'document_too_large': 'The referenced manifest exceeds the document budget; reduce or revise the dependency closure before retrying.',
    'invalid_reference': 'The controller supplied an invalid document reference; rebuild the preview and verify the producer.',
    'document_unavailable': 'The referenced document is missing; verify staging and obtain a fresh preview before retrying.',
    'document_identity_mismatch': 'Document integrity verification failed; investigate the changed bytes and re-preview before retrying.',
    'invalid_weight_layout_document': 'The weight-layout document is incompatible; verify the source and helper versions.',
    'invalid_request_document': 'The referenced document is invalid; verify the source and helper versions.',
}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate diagnostic key')
        result[key] = value
    return result


def helper_failure_code(stderr: str) -> str | None:
    """Decode only the final closed helper envelope; ignore untrusted prose."""
    line = stderr.rstrip('\r\n').rsplit('\n', 1)[-1]
    if not line or len(line.encode('utf-8')) > MAX_DIAGNOSTIC_BYTES:
        return None
    try:
        value = json.loads(line, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        return None
    if (not isinstance(value, dict) or set(value) != {'state', 'error'}
            or value['state'] != 'failed' or not isinstance(value['error'], str)
            or value['error'] not in HELPER_FAILURE_MESSAGES):
        return None
    return value['error']


class RemoteHelperError(RemoteTransportError):
    """A known worker error, safe to persist without retaining remote stderr."""
    def __init__(self, code: str):
        if not isinstance(code, str) or code not in HELPER_FAILURE_MESSAGES:
            raise ValueError('Unknown remote helper failure code')
        self.code = code
        self.user_message = f'Remote helper [{code}]: {HELPER_FAILURE_MESSAGES[code]}'
        super().__init__(self.user_message)


class RemoteExecutionTimeout(RemoteTransportError):
    """An established remote command exceeded its execution budget."""


class RemoteHostKeyUnavailable(RemoteTransportError):
    """No key received; only initial, unpinned discovery may try another route."""


class RemoteConnectionError(RemoteTransportError):
    """SSH could not establish an authenticated command channel."""


def _host_key_digest(encoded_key: str) -> str:
    try:
        key_bytes = base64.b64decode(encoded_key.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise RemoteTransportError("Remote SSH host key is malformed") from exc
    return hashlib.sha256(key_bytes).hexdigest()


@dataclass(frozen=True, slots=True)
class RemoteConnection:
    target_id: str
    host: str
    port: int
    username: str
    remote_root: str
    runtime_binding: dict | None = None
    provision_operation_id: str | None = None

    @classmethod
    def from_target(cls, target: object) -> "RemoteConnection":
        host = str(getattr(target, "host", "") or "").strip()
        port = getattr(target, "port", None)
        username = str(getattr(target, "username", "") or "root").strip()
        remote_root = str(getattr(target, "remote_root", "") or "/opt/biomodstack").rstrip("/")
        if not host or not isinstance(port, int) or not 1 <= port <= 65535:
            raise RemoteTransportError("Execution target has no valid SSH endpoint")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", username):
            raise RemoteTransportError("Execution target has an invalid SSH username")
        if not re.fullmatch(r"[A-Za-z0-9:._-]+", host) or host.startswith("-"):
            raise RemoteTransportError("Execution target has an invalid SSH host")
        root_path = PurePosixPath(remote_root)
        if (
            not root_path.is_absolute()
            or root_path == PurePosixPath("/")
            or remote_root != root_path.as_posix()
            or any(not re.fullmatch(r"[A-Za-z0-9._-]+", part) for part in root_path.parts[1:])
        ):
            raise RemoteTransportError("Execution target has an invalid remote root")
        capabilities = getattr(target, "capabilities", None) or {}
        return cls(str(getattr(target, "id", "")), host, port, username, remote_root,
                   deepcopy(capabilities.get("critical_runtime_binding")))


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    command_started: float | None = None


def known_hosts_path() -> Path:
    path = Path(
        os.getenv(
            "BMS_REMOTE_KNOWN_HOSTS",
            str(Path.home() / ".config" / "biomodstack" / "remote_known_hosts"),
        )
    ).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    os.chmod(path, 0o600)
    return path


def private_key_path() -> Path:
    configured = os.getenv("BMS_REMOTE_SSH_KEY", "").strip()
    if not configured:
        raise RemoteTransportError("BMS_REMOTE_SSH_KEY is not configured")
    path = Path(configured).expanduser()
    if not path.is_file():
        raise RemoteTransportError("Configured remote SSH key is unavailable")
    return path


def _ssh_base(connection: RemoteConnection) -> list[str]:
    return [
        "ssh",
        "-F", "/dev/null",
        "-o", "IdentitiesOnly=yes",
        "-o", "IdentityAgent=none",
        "-o", "PreferredAuthentications=publickey",
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "ControlMaster=no",
        "-o", "ControlPath=none",
        "-i",
        str(private_key_path()),
        "-p",
        str(connection.port),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts_path()}",
        f"{connection.username}@{connection.host}",
    ]


async def _run_owned(argv: Sequence[str], destination: Path, *, timeout: float) -> CommandResult:
    """Artifact transfers with a durable, API-death-aware lifecycle."""
    marker = transfer_marker(destination)
    # Only the collector's freshly prepared legacy boot fence may launch. Do
    # not overwrite an active or ambiguous supervisor record on a direct retry.
    if not marker.exists() or json.loads(marker.read_text()) != {
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    }:
        raise RemoteTransportError("Result transport requires a freshly prepared ownership fence")
    durable_json(marker, dict(schema=SCHEMA, phase="starting",
                             destination=str(destination.resolve()),
                             boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                             controller=process_identity(os.getpid())))
    read_fd, write_fd = os.pipe()
    task = None
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(Path(__file__).with_name("transfer_supervisor.py")),
            str(marker), str(read_fd), *argv, pass_fds=(read_fd,),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True,
        )
        os.close(read_fd)
        read_fd = -1
        task = asyncio.create_task(process.communicate())
        try:
            stdout, stderr = await asyncio.wait_for(asyncio.shield(task), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            # Closing our private pipe asks the still-owned supervisor to stop.
            # Never signal a recovered PID, nor kill the receipt authority.
            os.close(write_fd)
            write_fd = -1
            cleanup = asyncio.create_task(asyncio.wait_for(asyncio.shield(task), 5))
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
                except asyncio.TimeoutError:
                    break
            try:
                cleanup.result()
            except asyncio.TimeoutError:
                pass  # receipt absent => retained fence; no byte reclamation
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise RemoteTransportError("Remote transport timed out") from None
        return CommandResult(int(process.returncode or 0),
                             stdout.decode("utf-8", errors="replace"),
                             stderr.decode("utf-8", errors="replace"))
    finally:
        for fd in (read_fd, write_fd):
            if fd >= 0:
                os.close(fd)


async def cancel_owned_transfer(destination: Path, *, timeout: float = 5.0) -> bool | None:
    """Address the durable destination owner, never signal a PID from disk.

    None means no endpoint was reachable, not proof of writer quiescence. The
    caller must then hold the producer guard and verify the retained fence.
    """
    import socket
    import struct
    from .transfer_supervisor import control_address

    marker = transfer_marker(destination)
    def request():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as control:
            control.settimeout(timeout)
            try:
                control.connect(control_address(marker))
            except (ConnectionRefusedError, FileNotFoundError):
                return None
            try:
                record = json.loads(marker.read_text())
                pid, uid, _ = struct.unpack('3i', control.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if (uid != os.getuid() or record.get('schema') != SCHEMA
                        or record.get('destination') != str(destination.resolve())
                        or record.get('boot_id') != Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                        or record.get('supervisor') != process_identity(pid)):
                    return False
                control.sendall(b'cancel\n')
                if control.recv(64) != b'quiescent\n':
                    return False
                # ACK follows fsync, but is not itself the quiescence authority.
                # The producer may already have consumed the record; in that
                # case the caller must obtain its guard before accepting absence.
                try:
                    receipt = json.loads(marker.read_text())
                except FileNotFoundError:
                    return None
                from .result_generation import validate_transfer_receipt
                validate_transfer_receipt(receipt, destination)
                if any(receipt.get(key) != record.get(key) for key in
                       ('boot_id', 'controller', 'supervisor', 'destination')):
                    return False
                return True
            except (OSError, ValueError, KeyError):
                return False
    return await asyncio.to_thread(request)


async def _run(argv: Sequence[str], *, input_bytes: bytes | None = None, timeout: float = 60,
               establishment_timeout: float | None = None) -> CommandResult:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if input_bytes is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    command_started = None
    establishing = establishment_timeout is not None
    # Drain stderr from launch, including while awaiting the authenticated shell.
    # Never parse/log SSH debug output or allow it to fill the pipe.
    stderr_task = asyncio.create_task(process.stderr.read()) if establishing else None
    try:
        if establishing:
            marker = await asyncio.wait_for(process.stdout.readline(), establishment_timeout)
            if marker != b"BMS_COMMAND_READY\n":
                await asyncio.wait_for(process.wait(), timeout=2)
                raise RemoteConnectionError("Remote SSH connection or authentication failed")
            command_started = time.monotonic()
            establishing = False
            async def communicate():
                if input_bytes is not None:
                    process.stdin.write(input_bytes)
                    await process.stdin.drain()
                    process.stdin.close()
                stdout = await process.stdout.read()
                await process.wait()
                return stdout, await stderr_task
            stdout, stderr = await asyncio.wait_for(communicate(), timeout)
        else:
            stdout, stderr = await asyncio.wait_for(process.communicate(input_bytes), timeout=timeout)
    except BaseException as exc:
        async def stop_group() -> None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
        cleanup = asyncio.create_task(stop_group())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
        cleanup.result()
        if isinstance(exc, asyncio.CancelledError):
            raise
        if not isinstance(exc, asyncio.TimeoutError):
            raise
        if establishment_timeout is not None:
            if establishing:
                raise RemoteConnectionError("Remote SSH establishment timed out") from None
            raise RemoteExecutionTimeout("Remote command execution timed out") from None
        raise RemoteTransportError("Remote transport timed out") from None
    finally:
        if stderr_task is not None:
            if not stderr_task.done():
                stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
    return CommandResult(
        command_started=command_started,
        returncode=int(process.returncode or 0),
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )


async def capture_host_key(host: str, port: int) -> tuple[str, str]:
    try:
        scan = await _run(
            ["ssh-keyscan", "-t", "ed25519", "-p", str(port), "-T", "10", host],
            timeout=15,
        )
    except RemoteTransportError as exc:
        if str(exc) != "Remote transport timed out":
            raise
        raise RemoteHostKeyUnavailable("Unable to read the remote SSH host key") from exc
    lines = sorted(
        line.strip()
        for line in scan.stdout.splitlines()
        if line.strip() and not line.startswith("#")
    )
    if not lines:
        raise RemoteHostKeyUnavailable("Unable to read the remote SSH host key")
    if scan.returncode != 0:
        raise RemoteTransportError("Remote SSH host key is malformed")
    line = lines[0]
    parts = line.split()
    if len(parts) < 3:
        raise RemoteTransportError("Remote SSH host key is malformed")
    fingerprint = _host_key_digest(parts[2])
    return line, fingerprint


async def host_key_is_pinned(host: str, port: int) -> bool:
    # Include keys retained after an authentication/root-check failure, before
    # the target's authenticated attachment fingerprint could be committed.
    token = host if port == 22 else f"[{host}]:{port}"
    found = await _run(["ssh-keygen", "-F", token, "-f", str(known_hosts_path())], timeout=5)
    if found.returncode not in (0, 1):
        raise RemoteTransportError("Unable to inspect pinned SSH host keys")
    return found.returncode == 0


async def persist_host_key(line: str, fingerprint: str) -> None:
    path = known_hosts_path()
    existing = path.read_text(encoding="utf-8").splitlines()
    host_token = line.split()[0]
    matching = [value for value in existing if value.split(maxsplit=1)[0] == host_token]
    if matching:
        existing_fingerprints = {
            _host_key_digest(value.split()[2])
            for value in matching
            if len(value.split()) >= 3
        }
        if existing_fingerprints != {fingerprint}:
            raise RemoteTransportError("Remote SSH host key changed")
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line.rstrip() + "\n")
    os.chmod(path, 0o600)


# Only fixed messages from our checked-in bootstrap script, never remote logs.
BOOTSTRAP_ERRORS = frozenset(re.findall(r"fail '([^']+)'", Path(__file__).with_name("bootstrap_worker.sh").read_text()))


def _controlled_remote_failure(stdout: str) -> str | None:
    for line in reversed(stdout.splitlines()):
        if line.startswith("BMS_SETUP_ERROR:") and line.removeprefix("BMS_SETUP_ERROR:") in BOOTSTRAP_ERRORS:
            return line.removeprefix("BMS_SETUP_ERROR:")
        missing = re.fullmatch(r"missing:([A-Za-z0-9][A-Za-z0-9._+-]{0,63})", line.strip())
        if missing:
            return f"Remote readiness prerequisite is missing: {missing.group(1)}"
        occupied = re.fullmatch(r"occupied:(/[A-Za-z0-9._/-]{1,499})", line.strip())
        if occupied:
            return f"Remote readiness path is occupied: {occupied.group(1)}"
    return None


def _provision_argv(connection: RemoteConnection, operation_id: str, mode: str,
                    argv: Sequence[str] = ()) -> list[str]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", operation_id):
        raise RemoteTransportError("Invalid provisioning operation identity")
    # No bootstrap upload outside the fence. The exact checked-in stdlib source
    # travels over the already authenticated SSH command channel; caller stdin
    # remains untouched (helper bytes, JSON, or the rsync receiver protocol).
    source = Path(__file__).with_name("_provision_transport.py").read_bytes()
    encoded = base64.b64encode(source).decode("ascii")
    loader = "import base64;exec(compile(base64.b64decode(" + repr(encoded) + "),'<bms-provision-transport>','exec'))"
    return ["python3", "-c", loader, mode, connection.remote_root, operation_id, *map(str, argv)]


async def quiesce_provision(connection: RemoteConnection, operation_id: str) -> bool:
    """Fence late admissions and prove remote provisioning groups have stopped.

    This is intentionally independent of the bound connection's operation so
    restart reconciliation can stop its persisted predecessor. Unknown receipts,
    SSH failure and uncertain process identity retain backend recovery ownership.
    """
    try:
        argv = _provision_argv(connection, operation_id, "quiesce")
        result = await _run([*_ssh_base(connection), shlex.join(argv)], timeout=60)
        value = json.loads(result.stdout)
        return (result.returncode == 0 and isinstance(value, dict)
                and value.get("schema") == "bms.provision-transport.v1"
                and value.get("operation_id") == operation_id
                and value.get("quiescent") is True)
    except (RemoteTransportError, OSError, ValueError):
        return False


async def run_remote(
    connection: RemoteConnection,
    argv: Sequence[str],
    *,
    timeout: float = 60,
    input_bytes: bytes | None = None,
    establishment_timeout: float | None = None,
) -> CommandResult:
    if not argv or any("\x00" in str(value) for value in argv):
        raise RemoteTransportError("Invalid remote command")
    if connection.provision_operation_id is not None:
        argv = _provision_argv(connection, connection.provision_operation_id, "run", argv)
    remote_command = " ".join(shlex.quote(str(value)) for value in argv)
    options = {}
    if establishment_timeout is not None:
        # The shell marker precedes exec and stdin consumption. Only callers
        # explicitly opting into phased deadlines change their timeout contract.
        remote_command = "printf 'BMS_COMMAND_READY\\n'; exec " + remote_command
        options['establishment_timeout'] = establishment_timeout
    result = await _run(
        [*_ssh_base(connection), remote_command],
        input_bytes=input_bytes,
        timeout=timeout,
        **options,
    )
    if result.returncode == 255:
        raise RemoteConnectionError("Remote SSH connection or authentication failed")
    if result.returncode != 0:
        code = helper_failure_code(result.stderr)
        if code is not None:
            raise RemoteHelperError(code)
        controlled = _controlled_remote_failure(result.stdout)
        detail = result.stderr.strip().splitlines()[-1:] or ["remote command failed"]
        raise RemoteTransportError(controlled or detail[0][:500])
    return result


async def rsync_to_remote(
    connection: RemoteConnection,
    source: Path,
    destination: str,
    *,
    delete: bool = True,
    timeout: float = 3600,
    ownership_directory: Path | None = None,
) -> None:
    source = source.resolve()
    ssh_command = " ".join(
        shlex.quote(value)
        for value in _ssh_base(connection)[:-1]
    )
    rsync_options = [
        "rsync",
        "--archive",
        "--partial",
        "--protect-args",
    ]
    if source.is_dir() and delete:
        rsync_options.append("--delete")
    if connection.provision_operation_id is not None:
        receiver = _provision_argv(connection, connection.provision_operation_id, "run", ["rsync"])
        rsync_options.append("--rsync-path=" + shlex.join(receiver))
    argv = [*rsync_options, '--rsh', ssh_command,
            str(source) + ('/' if source.is_dir() else ''),
            f'{connection.username}@{connection.host}:{destination}']
    result = (await _run_owned(argv, ownership_directory, timeout=timeout)
              if ownership_directory is not None else await _run(argv, timeout=timeout))
    if result.returncode != 0:
        raise RemoteTransportError((result.stderr.strip() or "rsync upload failed")[-500:])


async def rsync_from_remote(
    connection: RemoteConnection,
    source: str,
    destination: Path,
    *,
    timeout: float = 3600,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    ssh_command = " ".join(shlex.quote(value) for value in _ssh_base(connection)[:-1])
    result = await _run(
        [
            "rsync",
            "--archive",
            "--partial",
            "--protect-args",
            "--rsh",
            ssh_command,
            f"{connection.username}@{connection.host}:{source.rstrip('/')}/",
            str(destination.resolve()) + "/",
        ],
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RemoteTransportError((result.stderr.strip() or "rsync download failed")[-500:])


async def rsync_selected_from_remote(
    connection: RemoteConnection,
    source: str,
    destination: Path,
    relative_paths: list[str],
    *,
    max_file_bytes: int,
    timeout: float = 3600,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    ssh_command = " ".join(shlex.quote(value) for value in _ssh_base(connection)[:-1])
    list_path: Path | None = None
    result: CommandResult | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="bms-rsync-files-", delete=False) as handle:
            list_path = Path(handle.name)
            for relative_path in relative_paths:
                handle.write(relative_path.encode("utf-8") + b"\0")
        # The result collector has already fenced this generation. Other
        # selected-download users retain their existing transport contract.
        async def run_selected(argv):
            if transfer_marker(destination).exists():
                return await _run_owned(argv, destination, timeout=timeout)
            return await _run(argv, timeout=timeout)

        result = await run_selected(
            [
                "rsync",
                "--archive",
                "--partial",
                "--protect-args",
                "--from0",
                f"--files-from={list_path}",
                f"--max-size={int(max_file_bytes)}",
                "--rsh",
                ssh_command,
                f"{connection.username}@{connection.host}:{source.rstrip('/')}/",
                str(destination.resolve()) + "/",
            ],
        )
    finally:
        if list_path is not None:
            list_path.unlink(missing_ok=True)
    if result is None or result.returncode != 0:
        detail = result.stderr.strip() if result is not None else ""
        raise RemoteTransportError((detail or "rsync selected download failed")[-500:])


async def probe_readiness(connection: RemoteConnection) -> dict[str, object]:
    root = connection.remote_root
    script = (
        "set -eu; "
        f"mkdir -p {shlex.quote(root)}/{{revisions,runtimes,attempts,incoming,cache}}; "
        f"test -w {shlex.quote(root)}; "
        "for c in python3 bash rsync tar sha256sum java apptainer nvidia-smi; do command -v \"$c\" >/dev/null || { echo \"missing:$c\"; exit 20; }; done; "
        "python3 -c 'import json,platform,shutil,subprocess; "
        "g=subprocess.run([\"nvidia-smi\",\"--query-gpu=index,uuid,name,memory.total\",\"--format=csv,noheader,nounits\"],capture_output=True,text=True,check=True); "
        "print(json.dumps({\"architecture\":platform.machine(),\"free_bytes\":shutil.disk_usage(\"/\").free,\"gpus\":[x.strip() for x in g.stdout.splitlines() if x.strip()]}))'"
    )
    result = await run_remote(connection, ["bash", "-lc", script], timeout=45)
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RemoteTransportError("Remote readiness probe returned invalid output") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("gpus"), list):
        raise RemoteTransportError("Remote readiness probe is incomplete")
    return payload
