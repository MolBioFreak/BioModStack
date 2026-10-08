"""Explicit service-only recovery; never talks to the native robot API."""
from __future__ import annotations

import asyncio
import os
import re
import threading
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from .connection import BioXpConnectionService
from .errors import ProfileStoreError

UNIT = "bioxp-api.service"
RESTART_COMMAND = (
    "sudo -n systemctl restart bioxp-api.service && systemctl show bioxp-api.service "
    "--property=ActiveState --property=SubState --property=InvocationID --property=MainPID"
)
RESTART_TIMEOUT_SECONDS = 60.0
# Deliberately supports only ordinary SSH aliases/hosts and optional user names.
# No URI, option, port suffix, whitespace or shell syntax is accepted.
_TARGET = re.compile(r"(?:[A-Za-z_][A-Za-z0-9_-]*@)?[A-Za-z0-9][A-Za-z0-9.-]*\Z")


class ServiceRestartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ServiceAvailability(BaseModel):
    available: bool
    detail: str | None
    unit: Literal["bioxp-api.service"] = UNIT
    restart_in_progress: bool


class ServiceRestartResult(BaseModel):
    restarted: Literal[True] = True
    unit: Literal["bioxp-api.service"] = UNIT
    active_state: str
    sub_state: str
    invocation_id: str
    pid: int


class ServiceControlError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _api_identity(value: str) -> tuple[str, str, int]:
    """Local URL normalization only: no DNS, allowlist or live observation."""
    raw = value.strip()
    parsed = urlsplit(raw if "://" in raw else f"http://{raw}")
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("invalid API URL")
    port = parsed.port
    if port == 0:
        raise ValueError("invalid API port")
    return parsed.scheme, parsed.hostname.lower().rstrip("."), port or (443 if parsed.scheme == "https" else 80)


def _configured_target(connection: BioXpConnectionService) -> str:
    target = os.environ.get("BMS_BIOXP_SERVICE_SSH_TARGET", "")
    api_url = os.environ.get("BMS_BIOXP_SERVICE_API_URL", "")
    if not target or not api_url:
        raise ServiceControlError(503, "Robot service restart is not configured on the BMS server")
    if not _TARGET.fullmatch(target):
        raise ServiceControlError(503, "Robot service restart SSH configuration is invalid")
    try:
        profile = connection.load_profile()
        if profile is None:
            raise ServiceControlError(503, "Robot service restart requires a saved robot profile")
        matches = _api_identity(api_url) == _api_identity(profile.api_url)
    except (ValueError, ProfileStoreError) as exc:
        raise ServiceControlError(503, "Robot service restart API binding or saved profile is invalid") from exc
    if not matches:
        raise ServiceControlError(503, "Robot service restart is not configured for the saved robot profile")
    return target


def _parse_observation(stdout: bytes) -> ServiceRestartResult:
    try:
        rows = [line.split("=", 1) for line in stdout.decode("utf-8").splitlines()]
        if len(rows) != 4 or any(len(row) != 2 for row in rows):
            raise ValueError("invalid properties")
        values = dict(rows)
        if set(values) != {"ActiveState", "SubState", "InvocationID", "MainPID"}:
            raise ValueError("invalid properties")
        if (not re.fullmatch(r"[a-z-]+", values["ActiveState"])
                or not re.fullmatch(r"[a-z-]+", values["SubState"])
                or not re.fullmatch(r"(?:[0-9a-f]{32})?", values["InvocationID"])
                or not re.fullmatch(r"[0-9]+", values["MainPID"])):
            raise ValueError("invalid property value")
        # Preserve even inactive/failed/zero-PID observations: exit-zero restart
        # is not a claim of API readiness, hardware state or lasting health.
        return ServiceRestartResult(
            active_state=values["ActiveState"], sub_state=values["SubState"],
            invocation_id=values["InvocationID"], pid=int(values["MainPID"]),
        )
    except (ValueError, UnicodeError) as exc:
        raise ServiceControlError(502, "Robot service restart returned an invalid observation; remote outcome is uncertain. No automatic retry was made") from exc


class BioXpServiceControl:
    def __init__(self) -> None:
        # Nonblocking acquisition: concurrent HTTP requests never queue a restart.
        self._restart_lock = threading.Lock()

    def availability(self, connection: BioXpConnectionService) -> ServiceAvailability:
        detail = None
        try:
            _configured_target(connection)
        except ServiceControlError as exc:
            detail = exc.detail
        return ServiceAvailability(
            available=detail is None, detail=detail,
            restart_in_progress=self._restart_lock.locked(),
        )

    async def restart(self, connection: BioXpConnectionService) -> ServiceRestartResult:
        if not self._restart_lock.acquire(blocking=False):
            raise ServiceControlError(409, "A robot service restart is already in progress")
        process = None
        spawn = None
        try:
            target = _configured_target(connection)
            async with asyncio.timeout(RESTART_TIMEOUT_SECONDS):
                spawn = asyncio.create_task(asyncio.create_subprocess_exec(
                    "ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                    "-o", "StrictHostKeyChecking=yes", "-o", "ConnectionAttempts=1",
                    "--", target, RESTART_COMMAND,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                ))
                process = await asyncio.shield(spawn)
                stdout, _ = await process.communicate()
                if process.returncode != 0:
                    raise ServiceControlError(502, "Robot service restart SSH command failed; remote outcome is uncertain. No automatic retry was made")
                return _parse_observation(stdout)
        except (TimeoutError, OSError) as exc:
            raise ServiceControlError(502, "Robot service restart transport failed or timed out; remote outcome is uncertain. No automatic retry was made") from exc
        finally:
            async def reap() -> None:
                # Join a launch interrupted before it returned its process handle.
                child = process
                if child is None and spawn is not None:
                    try:
                        child = await spawn
                    except OSError:
                        return
                if child is not None and child.returncode is None:
                    # Killing SSH cannot roll back a remote systemd action.
                    try:
                        child.kill()
                    except ProcessLookupError:
                        pass
                    await child.wait()

            try:
                cleanup = asyncio.create_task(reap())
                cancelled = False
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        cancelled = True
                cleanup.result()
                if cancelled:
                    raise asyncio.CancelledError
            finally:
                self._restart_lock.release()


service_control = BioXpServiceControl()
