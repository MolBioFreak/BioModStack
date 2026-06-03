from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


PROBE_FRESH_WINDOW_SECONDS = 60

from paths import get_data_root

SCHEMA_VERSION = "bms.bioxp_interlink_profile.v1"
PROFILE_PATH = get_data_root() / "bioxp_interlink_profile.json"
DEFAULT_SERVICE_NAME = "bioxp-api.service"
FORBIDDEN_COMMAND_TOKENS = (
    "killall",
    "pkill",
    "nohup",
    "uvicorn",
    "usbreset",
    "strict_startup",
    "homing",
    "motion/arm",
    "recover_motion",
)

_SESSION: dict[str, Any] = {
    "active": False,
    "robot_api_url": None,
    "reachable": None,
    "hardware_connected": None,
    "maintenance_state": None,
    "last_probe_at": None,
    "last_status": None,
    "last_error": None,
    "lifecycle_action": None,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_probe_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_probe_fresh(value: Any, *, now: datetime | None = None) -> bool:
    parsed = _parse_probe_timestamp(value)
    if parsed is None:
        return False
    current = now or datetime.now(timezone.utc)
    age_seconds = (current - parsed).total_seconds()
    return 0 <= age_seconds <= PROBE_FRESH_WINDOW_SECONDS


def _normalize_url(url: str | None) -> str | None:
    if url is None:
        return None
    value = str(url).strip()
    if not value:
        return None
    if not value.startswith("http://") and not value.startswith("https://"):
        value = f"http://{value}"
    return value.rstrip("/")


def _profile_path() -> Path:
    return Path(PROFILE_PATH).expanduser().resolve()


def load_profile() -> dict[str, Any] | None:
    path = _profile_path()
    try:
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    url = _normalize_url(raw.get("robot_api_url") or raw.get("url"))
    if not url:
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "robot_api_url": url,
        "robot_ssh_host": str(raw.get("robot_ssh_host") or "robot"),
        "connection_mode": str(raw.get("connection_mode") or "direct_http"),
        "display_name": str(raw.get("display_name") or "BioXP3200"),
        "auto_connect_on_launch": False,
    }


def save_profile(settings: dict[str, Any]) -> dict[str, Any]:
    url = _normalize_url(settings.get("robot_api_url") or settings.get("url"))
    if not url:
        raise ValueError("robot_api_url is required for BioXP interlink settings")
    profile = {
        "schema_version": SCHEMA_VERSION,
        "robot_api_url": url,
        "robot_ssh_host": str(settings.get("robot_ssh_host") or "robot"),
        "connection_mode": str(settings.get("connection_mode") or "direct_http"),
        "display_name": str(settings.get("display_name") or "BioXP3200"),
        "auto_connect_on_launch": False,
    }
    path = _profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return profile


def forget_profile() -> None:
    path = _profile_path()
    try:
        if path.exists():
            path.unlink()
    except FileNotFoundError:
        pass


def reset_session() -> None:
    _SESSION.update(
        {
            "active": False,
            "robot_api_url": None,
            "reachable": None,
            "hardware_connected": None,
            "maintenance_state": None,
            "last_probe_at": None,
            "last_status": None,
            "last_error": None,
            "lifecycle_action": None,
        }
    )


def activate_session(robot_api_url: str) -> None:
    _SESSION.update(
        {
            "active": True,
            "robot_api_url": _normalize_url(robot_api_url),
            "reachable": None,
            "hardware_connected": None,
            "maintenance_state": None,
            "last_probe_at": None,
            "last_status": None,
            "last_error": None,
            "lifecycle_action": None,
        }
    )


def deactivate_session(lifecycle_action: str | None = None) -> None:
    _SESSION.update(
        {
            "active": False,
            "reachable": None,
            "hardware_connected": None,
            "maintenance_state": None,
            "last_error": None,
            "lifecycle_action": lifecycle_action,
        }
    )


def record_probe_result(payload: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> None:
    payload = payload if isinstance(payload, dict) else None
    maintenance_state = None
    if payload:
        for key in ("maintenance_state", "maintenance", "maintenance_usb_release"):
            candidate = payload.get(key)
            if isinstance(candidate, dict):
                maintenance_state = candidate
                break
    _SESSION.update(
        {
            "reachable": payload is not None and error is None,
            "hardware_connected": bool(payload.get("hardware_connected")) if payload else False if error else None,
            "maintenance_state": maintenance_state,
            "last_probe_at": _now_iso(),
            "last_status": payload,
            "last_error": error,
        }
    )


def describe_state(*, recommended_url: str, runtime_note: str | None = None) -> dict[str, Any]:
    profile = load_profile()
    active = bool(_SESSION.get("active") and _SESSION.get("robot_api_url"))
    configured = bool(profile)
    robot_api_url = _SESSION.get("robot_api_url") if active else (profile or {}).get("robot_api_url")
    raw_reachable = _SESSION.get("reachable") if active else None
    raw_hardware_connected = _SESSION.get("hardware_connected") if active else None
    last_probe_at = _SESSION.get("last_probe_at") if active else None
    probe_fresh = _is_probe_fresh(last_probe_at) if active else False
    probe_stale = bool(active and raw_reachable is True and not probe_fresh)
    exposed_reachable = raw_reachable
    exposed_hardware_connected = raw_hardware_connected
    if probe_stale:
        # A previous successful probe is useful audit history, but it is not live
        # reachability truth. Fail closed for callers that only look at booleans.
        exposed_reachable = None
        exposed_hardware_connected = None
    if runtime_note is None:
        if active:
            if raw_reachable is True and probe_fresh:
                runtime_note = "Active operator-initiated BioXP interlink is reachable."
            elif raw_reachable is True:
                runtime_note = "Previous BioXP interlink success is stale; refresh diagnostics before trusting robot or hardware state."
            elif raw_reachable is False:
                runtime_note = "Active BioXP interlink is degraded or unreachable; inspect diagnostics before motion work."
            else:
                runtime_note = "Active BioXP interlink has not been probed yet in this session."
        elif configured:
            runtime_note = "Saved profile is inactive. Press Connect to start polling the robot."
        else:
            runtime_note = "No BioXP interlink profile is saved. Enter the robot API URL and Save settings or Connect."
    return {
        "component": "bioxp-interlink",
        "configured": configured,
        "active": active,
        "connection_mode": (profile or {}).get("connection_mode", "direct_http"),
        "display_name": (profile or {}).get("display_name", "BioXP3200"),
        "robot_api_url": robot_api_url,
        "robot_ssh_host": (profile or {}).get("robot_ssh_host", "robot"),
        "recommended_url": recommended_url,
        "reachable": exposed_reachable if active else None,
        "hardware_connected": exposed_hardware_connected if active else None,
        "last_probe_reachable": raw_reachable if active else None,
        "last_probe_hardware_connected": raw_hardware_connected if active else None,
        "probe_fresh": probe_fresh if active else None,
        "probe_stale": probe_stale if active else None,
        "probe_fresh_window_seconds": PROBE_FRESH_WINDOW_SECONDS,
        "maintenance_state": _SESSION.get("maintenance_state") if active else None,
        "last_probe_at": last_probe_at,
        "last_status": _SESSION.get("last_status") if active else None,
        "last_error": _SESSION.get("last_error") if active else None,
        "lifecycle_action": _SESSION.get("lifecycle_action"),
        "control_mode": "bms-thin-proxy",
        "runtime_note": runtime_note,
        "auto_connect_on_launch": False,
    }


def run_command(command: list[str], *, input_text: str | None = None, timeout: float = 90.0) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\r", "").replace("\x00", "")
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if "password" in key.lower() or "secret" in key.lower() else _redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _safe_command(command: list[str]) -> None:
    command_text = " ".join(command).lower()
    for token in FORBIDDEN_COMMAND_TOKENS:
        if token.lower() in command_text:
            raise ValueError(f"Unsafe BioXP lifecycle command refused: {token}")


def _require_profile() -> dict[str, Any]:
    profile = load_profile()
    if not profile:
        raise ValueError("BioXP interlink profile is not configured; save robot settings first")
    return profile


def _require_ack(payload: dict[str, Any], expected: str) -> None:
    if str(payload.get("operator_ack") or "").strip() != expected:
        raise ValueError(f"This BioXP lifecycle action requires operator_ack={expected!r}")


def _ssh_base(profile: dict[str, Any]) -> list[str]:
    return ["ssh", "-o", "BatchMode=yes", str(profile.get("robot_ssh_host") or "robot")]


def _sudo_fragment(payload: dict[str, Any]) -> tuple[list[str], str | None]:
    password = payload.get("sudo_password")
    if password:
        return ["sudo", "-S"], f"{password}\n"
    return ["sudo", "-n"], None


def _command_available(command: list[str], command_runner: Callable[..., dict[str, Any]] | None = None) -> bool:
    # Tests inject a runner and should not depend on host/container PATH.
    if command_runner is not None:
        return True
    return bool(command and shutil.which(command[0]))


def _unsupported_command_payload(
    *,
    action: str,
    profile: dict[str, Any],
    command: list[str],
    reason: str | None = None,
    tail: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "bms.bioxp_interlink_lifecycle.v1",
        "action": action,
        "ok": False,
        "supported": False,
        "active": bool(_SESSION.get("active")),
        "configured": True,
        "robot_api_url": profile["robot_api_url"],
        "robot_ssh_host": profile["robot_ssh_host"],
        "reason": reason or "BMS API container cannot execute the robot SSH lifecycle/log command.",
        "command_preview": command,
        "command_result": {
            "returncode": 127,
            "stdout": "",
            "stderr": f"{command[0]} executable is not available in the BMS API runtime",
        },
        "notes": [
            "No robot command was executed.",
            "Use the robot-local runtime/API directly, or deploy an HTTP log/lifecycle endpoint that BMS can call without shelling out to SSH.",
        ],
    }
    if tail is not None:
        payload["tail"] = tail
        payload["schema_version"] = "bms.bioxp_interlink_logs.v1"
    return payload


def runtime_reset(payload: dict[str, Any], *, command_runner: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    _require_ack(payload, "RESET BIOXP RUNTIME")
    profile = _require_profile()
    sudo, input_text = _sudo_fragment(payload)
    command = [*_ssh_base(profile), *sudo, "systemctl", "restart", DEFAULT_SERVICE_NAME]
    _safe_command(command)
    if not _command_available(command, command_runner):
        return _unsupported_command_payload(
            action="runtime-reset",
            profile=profile,
            command=command,
            reason="Runtime reset is unavailable because the BMS API runtime cannot execute SSH.",
        )
    deactivate_session("runtime-reset")
    runner = command_runner or run_command
    result = runner(command, input_text=input_text, timeout=90.0)
    return {
        "schema_version": "bms.bioxp_interlink_lifecycle.v1",
        "action": "runtime-reset",
        "operator_ack": "RESET BIOXP RUNTIME",
        "active": False,
        "configured": True,
        "robot_api_url": profile["robot_api_url"],
        "robot_ssh_host": profile["robot_ssh_host"],
        "sudo_password": "[REDACTED]" if payload.get("sudo_password") else None,
        "reason": str(payload.get("reason") or "operator requested BioXP runtime reset"),
        "command_preview": command,
        "command_result": _redact(result),
        "notes": [
            "Scoped to robot-local bioxp-api.service restart via SSH/systemd.",
            "This action does not home, arm, recover motion, or move axes.",
        ],
    }


def robot_reboot(payload: dict[str, Any], *, command_runner: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    _require_ack(payload, "REBOOT ROBOT")
    profile = _require_profile()
    sudo, input_text = _sudo_fragment(payload)
    command = [*_ssh_base(profile), *sudo, "reboot"]
    _safe_command(command)
    if not _command_available(command, command_runner):
        return _unsupported_command_payload(
            action="robot-reboot",
            profile=profile,
            command=command,
            reason="Robot reboot is unavailable because the BMS API runtime cannot execute SSH.",
        )
    deactivate_session("robot-reboot")
    runner = command_runner or run_command
    result = runner(command, input_text=input_text, timeout=60.0)
    return {
        "schema_version": "bms.bioxp_interlink_lifecycle.v1",
        "action": "robot-reboot",
        "operator_ack": "REBOOT ROBOT",
        "active": False,
        "configured": True,
        "robot_api_url": profile["robot_api_url"],
        "robot_ssh_host": profile["robot_ssh_host"],
        "sudo_password": "[REDACTED]" if payload.get("sudo_password") else None,
        "reason": str(payload.get("reason") or "operator requested robot OS reboot"),
        "command_preview": command,
        "command_result": _redact(result),
        "notes": [
            "Full robot OS reboot requested via SSH/systemd scope.",
            "BMS leaves the BioXP interlink inactive; reconnect explicitly after the robot returns.",
            "This action does not home, arm, recover motion, or move axes.",
        ],
    }


def collect_logs(payload: dict[str, Any], *, command_runner: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    profile = _require_profile()
    tail = int(payload.get("tail") or 120)
    tail = max(1, min(500, tail))
    command = [*_ssh_base(profile), "journalctl", "-u", DEFAULT_SERVICE_NAME, "-n", str(tail), "--no-pager"]
    _safe_command(command)
    if not _command_available(command, command_runner):
        return _unsupported_command_payload(
            action="logs",
            profile=profile,
            command=command,
            reason="Robot service logs are unavailable because the BMS API runtime cannot execute SSH.",
            tail=tail,
        )
    runner = command_runner or run_command
    result = runner(command, input_text=None, timeout=45.0)
    return {
        "schema_version": "bms.bioxp_interlink_logs.v1",
        "action": "logs",
        "active": bool(_SESSION.get("active")),
        "configured": True,
        "robot_api_url": profile["robot_api_url"],
        "tail": tail,
        "command_preview": command,
        "command_result": _redact(result),
    }
