"""Bounded client for the root-owned Mk1D recovery socket.

The API never receives a command, service name, position, or shell input. It can
only request the one system-installed recovery transaction over a fixed AF_UNIX
socket and project its fixed non-secret receipt.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
from typing import Any

DEFAULT_RECONNECT_SOCKET = "/run/biomodstack/mk1d-reconnect.sock"
MAX_HELPER_RESPONSE_BYTES = 8192
DEFAULT_HELPER_TIMEOUT_SECONDS = 100.0


class ReconnectHelperUnavailable(RuntimeError):
    """The root-owned systemd socket/helper has not been installed."""


class ReconnectHelperProtocolError(RuntimeError):
    """The installed helper did not produce the narrow public receipt contract."""


def reconnect_socket_path() -> Path:
    """Return the explicitly configured recovery socket path.

    This deployment setting is never browser input. Relative values are rejected
    so a malformed runtime environment cannot redirect the privileged request
    into the application working tree.
    """
    value = os.getenv("BMS_MK1D_RECONNECT_SOCKET", DEFAULT_RECONNECT_SOCKET).strip()
    path = Path(value)
    if not value or not path.is_absolute():
        raise ReconnectHelperUnavailable("reconnect helper unavailable/not installed")
    return path


def helper_timeout_seconds() -> float:
    raw = os.getenv("BMS_MK1D_RECONNECT_TIMEOUT_SECONDS", str(DEFAULT_HELPER_TIMEOUT_SECONDS))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_HELPER_TIMEOUT_SECONDS
    return max(5.0, min(value, 120.0))


def _public_receipt(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise ReconnectHelperProtocolError("invalid reconnect helper receipt")
    schema = payload.get("schema")
    receipt_id = payload.get("receipt_id")
    status = payload.get("status")
    minknow = payload.get("minknow")
    host_agent_recreate = payload.get("host_agent_recreate")
    host_agent_health = payload.get("host_agent_health")
    values = (receipt_id, status, minknow, host_agent_recreate, host_agent_health)
    if schema != "bms.mk1d-reconnect-receipt.v1" or not all(isinstance(value, str) and value for value in values):
        raise ReconnectHelperProtocolError("invalid reconnect helper receipt")
    assert isinstance(receipt_id, str)
    assert isinstance(status, str)
    assert isinstance(minknow, str)
    assert isinstance(host_agent_recreate, str)
    assert isinstance(host_agent_health, str)
    if len(receipt_id) > 96 or status not in {"completed", "failed", "blocked", "busy"}:
        raise ReconnectHelperProtocolError("invalid reconnect helper receipt")
    if minknow not in {"not_attempted", "already_active", "started", "failed", "blocked"}:
        raise ReconnectHelperProtocolError("invalid reconnect helper receipt")
    if host_agent_recreate not in {"not_attempted", "requested", "failed"}:
        raise ReconnectHelperProtocolError("invalid reconnect helper receipt")
    if host_agent_health not in {"not_checked", "verified", "failed"}:
        raise ReconnectHelperProtocolError("invalid reconnect helper receipt")
    if status == "completed" and (host_agent_recreate != "requested" or host_agent_health != "verified"):
        raise ReconnectHelperProtocolError("invalid reconnect helper receipt")
    if status == "busy" and (
        receipt_id != "mk1d-reconnect-busy"
        or minknow != "not_attempted"
        or host_agent_recreate != "not_attempted"
        or host_agent_health != "not_checked"
    ):
        raise ReconnectHelperProtocolError("invalid reconnect helper receipt")
    return {
        "schema": schema,
        "receipt_id": receipt_id,
        "status": status,
        "minknow": minknow,
        "host_agent_recreate": host_agent_recreate,
        "host_agent_health": host_agent_health,
    }


def request_mk1d_reconnect() -> dict[str, str]:
    """Request exactly one root-owned recovery transaction over AF_UNIX.

    No subprocess is spawned here. The socket-activated helper ignores request
    data and exposes only its fixed receipt. The response is size-bounded.
    """
    path = reconnect_socket_path()
    if not path.exists() or not path.is_socket():
        raise ReconnectHelperUnavailable("reconnect helper unavailable/not installed")

    chunks: list[bytes] = []
    received = 0
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(helper_timeout_seconds())
            client.connect(str(path))
            client.sendall(b"RECONNECT\n")
            client.shutdown(socket.SHUT_WR)
            while True:
                chunk = client.recv(min(4096, MAX_HELPER_RESPONSE_BYTES - received + 1))
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
                if received > MAX_HELPER_RESPONSE_BYTES:
                    raise ReconnectHelperProtocolError("invalid reconnect helper receipt")
    except FileNotFoundError as exc:
        raise ReconnectHelperUnavailable("reconnect helper unavailable/not installed") from exc
    except (ConnectionRefusedError, PermissionError, TimeoutError, OSError) as exc:
        raise ReconnectHelperUnavailable("reconnect helper unavailable/not installed") from exc

    try:
        decoded = b"".join(chunks).decode("utf-8")
        payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReconnectHelperProtocolError("invalid reconnect helper receipt") from exc
    return _public_receipt(payload)
