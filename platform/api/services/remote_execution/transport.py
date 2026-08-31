"""OpenSSH and rsync transport for an activated execution target."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence

from paths import get_data_root


class RemoteTransportError(RuntimeError):
    pass


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
        return cls(str(getattr(target, "id", "")), host, port, username, remote_root)


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


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


async def _run(argv: Sequence[str], *, input_bytes: bytes | None = None, timeout: float = 60) -> CommandResult:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if input_bytes is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(input_bytes), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RemoteTransportError("Remote transport timed out") from None
    return CommandResult(
        returncode=int(process.returncode or 0),
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )


async def capture_host_key(host: str, port: int) -> tuple[str, str]:
    scan = await _run(
        ["ssh-keyscan", "-t", "ed25519", "-p", str(port), "-T", "10", host],
        timeout=15,
    )
    lines = sorted(
        line.strip()
        for line in scan.stdout.splitlines()
        if line.strip() and not line.startswith("#")
    )
    if scan.returncode != 0 or not lines:
        raise RemoteTransportError("Unable to read the remote SSH host key")
    line = lines[0]
    parts = line.split()
    if len(parts) < 3:
        raise RemoteTransportError("Remote SSH host key is malformed")
    fingerprint = _host_key_digest(parts[2])
    return line, fingerprint


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


async def run_remote(
    connection: RemoteConnection,
    argv: Sequence[str],
    *,
    timeout: float = 60,
    input_bytes: bytes | None = None,
) -> CommandResult:
    if not argv or any("\x00" in str(value) for value in argv):
        raise RemoteTransportError("Invalid remote command")
    remote_command = " ".join(shlex.quote(str(value)) for value in argv)
    result = await _run(
        [*_ssh_base(connection), remote_command],
        input_bytes=input_bytes,
        timeout=timeout,
    )
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1:] or ["remote command failed"]
        raise RemoteTransportError(detail[0][:500])
    return result


async def rsync_to_remote(
    connection: RemoteConnection,
    source: Path,
    destination: str,
    *,
    delete: bool = True,
    timeout: float = 3600,
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
        "--chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r",
    ]
    if source.is_dir() and delete:
        rsync_options.append("--delete")
    result = await _run(
        [
            *rsync_options,
            "--rsh",
            ssh_command,
            str(source) + ("/" if source.is_dir() else ""),
            f"{connection.username}@{connection.host}:{destination}",
        ],
        timeout=timeout,
    )
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
        result = await _run(
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
            timeout=timeout,
        )
    finally:
        if list_path is not None:
            list_path.unlink(missing_ok=True)
    if result is None or result.returncode != 0:
        detail = result.stderr.strip() if result is not None else ""
        raise RemoteTransportError((detail or "rsync selected download failed")[-500:])


async def probe_readiness(connection: RemoteConnection) -> dict[str, object]:
    root = connection.remote_root
    canonical_data_root = str(get_data_root())
    script = (
        "set -eu; "
        f"mkdir -p {shlex.quote(root)}/{{revisions,runtimes,attempts,incoming,cache}}; "
        f"test -w {shlex.quote(root)}; "
        f"mkdir -p {shlex.quote(canonical_data_root)}; test -w {shlex.quote(canonical_data_root)}; "
        f"for p in apptainer weights runtime; do d={shlex.quote(canonical_data_root)}/\"$p\"; "
        "test ! -e \"$d\" || test -L \"$d\" || { echo \"occupied:$d\"; exit 21; }; done; "
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
