import os
import socket
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from paths import get_data_root
from services import bioxp_interlink

router = APIRouter()


def _default_linkage_state_path() -> Path:
    override = os.getenv("BIOXP_LINKAGE_STATE_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return get_data_root() / "bioxp_linkage_url"


# BioXP runtime host defaults. The robot should own the runtime locally; BMS only links to it.
ROBOT_SSH_HOST = os.getenv("BIOXP_SSH_HOST", "robot")
ROBOT_DAEMON_PORT = int(os.getenv("BIOXP_DAEMON_PORT", "8123"))
LINKAGE_STATE_PATH = _default_linkage_state_path()

class LinkageRequest(BaseModel):
    url: str


class InterlinkSettingsRequest(BaseModel):
    robot_api_url: str
    robot_ssh_host: str = "robot"
    connection_mode: str = "direct_http"
    display_name: str = "BioXP3200"


class InterlinkLifecycleRequest(BaseModel):
    operator_ack: Optional[str] = None
    reason: Optional[str] = None
    sudo_password: Optional[str] = None
    watch_until_ready: bool = False
    tail: Optional[int] = None


def _normalize_url(url: Optional[str]) -> Optional[str]:
    if url is None:
        return None
    normalized = url.strip()
    if not normalized:
        return None
    if not normalized.startswith("http://") and not normalized.startswith("https://"):
        normalized = f"http://{normalized}"
    return normalized.rstrip("/")


def _format_linkage_host(host: str) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _build_linkage_url(
    host: str,
    *,
    scheme: str = "http",
    port: Optional[int] = None,
    path: str = "",
    query: str = "",
    fragment: str = "",
) -> str:
    netloc_host = _format_linkage_host(host)
    effective_port = ROBOT_DAEMON_PORT if port is None else port
    netloc = netloc_host if effective_port is None else f"{netloc_host}:{effective_port}"
    return urlunsplit((scheme or "http", netloc, path, query, fragment)).rstrip("/")


def _configured_default_linkage_url() -> Optional[str]:
    configured = _normalize_url(os.getenv("BIOXP_SERVER_URL"))
    if not configured:
        return None
    parsed = urlsplit(configured)
    host = parsed.hostname
    if not host:
        return configured
    if host == ROBOT_SSH_HOST:
        host = _resolve_host_for_linkage(host)
    return _build_linkage_url(
        host,
        scheme=parsed.scheme,
        port=parsed.port,
        path=parsed.path,
        query=parsed.query,
        fragment=parsed.fragment,
    )


def _recommended_linkage_url() -> str:
    configured = _configured_default_linkage_url()
    if configured:
        return configured
    host = _preferred_runtime_host()
    return _build_linkage_url(host)


def _preferred_runtime_host() -> str:
    override = os.getenv("BIOXP_LINKAGE_HOST") or os.getenv("BIOXP_RUNTIME_HOST")
    if override:
        return override.strip()
    return _resolve_host_for_linkage(ROBOT_SSH_HOST)


def _resolve_host_for_linkage(host: str) -> str:
    try:
        records = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except Exception:
        return host
    for family, _, _, _, sockaddr in records:
        address = sockaddr[0]
        if family == socket.AF_INET and address:
            return address
    for _, _, _, _, sockaddr in records:
        address = sockaddr[0]
        if address:
            return address
    return host


def _canonicalize_linkage_url(url: Optional[str]) -> Optional[str]:
    normalized = _normalize_url(url)
    if not normalized:
        return None
    parsed = urlsplit(normalized)
    if parsed.hostname != ROBOT_SSH_HOST:
        return normalized
    configured_default = _configured_default_linkage_url()
    if configured_default:
        configured = urlsplit(configured_default)
        host = configured.hostname or _preferred_runtime_host()
        return _build_linkage_url(
            host,
            scheme=parsed.scheme,
            port=parsed.port if parsed.port is not None else configured.port,
            path=parsed.path,
            query=parsed.query,
            fragment=parsed.fragment,
        )
    preferred_host = _preferred_runtime_host()
    return _build_linkage_url(
        preferred_host,
        scheme=parsed.scheme,
        port=parsed.port,
        path=parsed.path,
        query=parsed.query,
        fragment=parsed.fragment,
    )


def _read_persisted_linkage() -> Optional[str]:
    try:
        if LINKAGE_STATE_PATH.exists():
            raw_value = LINKAGE_STATE_PATH.read_text(encoding="utf-8").strip()
            canonical = _canonicalize_linkage_url(raw_value)
            if canonical and canonical != _normalize_url(raw_value):
                try:
                    LINKAGE_STATE_PATH.write_text(canonical, encoding="utf-8")
                except Exception:
                    pass
            return canonical
    except Exception:
        return None
    return None


def _persist_linkage(url: Optional[str]) -> None:
    LINKAGE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if url:
        LINKAGE_STATE_PATH.write_text(url, encoding="utf-8")
    elif LINKAGE_STATE_PATH.exists():
        LINKAGE_STATE_PATH.unlink()


# In-memory active interlink state. A saved profile or env recommendation is not
# activated on import; operators must press BIOXP LINK → Connect each session.
_GLOBAL_LINKAGE_URL: Optional[str] = None


def get_current_url() -> str:
    if not _GLOBAL_LINKAGE_URL:
         raise HTTPException(
             status_code=400,
             detail="Hardware Node URL is not configured. Please set the linkage in the BioXP Cockpit."
         )
    return _GLOBAL_LINKAGE_URL


def _maintenance_disabled_detail() -> str:
    return (
        "Robot-local BioXP runtime supervision is outside the normal BMS cockpit path. "
        "Use the robot-local bioxp-api.service or a maintenance runbook instead of starting/stopping uvicorn from BMS."
    )


ROBOT_LOCAL_EXPECTED_ROUTES: Dict[str, bool] = {
    "/status": True,
    "/oem-compat/capabilities/test-prep": True,
    "/oem-compat/protocols/import/dry-run": True,
    "/oem-compat/scripts/translate/dry-run": True,
    "/oem-compat/startup/dry-run": True,
    "/oem/initial_check": True,
    "/oem/startup/request": True,
    "/oem/startup/status/latest": True,
    "/oem/startup/status/{session_id}": True,
    "/oem/startup/door_event": True,
    "/oem/runtime/status": True,
    "/oem/runtime/state": True,
    "/oem/runtime/worker/status": True,
    "/oem/runtime/recover": True,
    "/oem/runtime/emergency_stop": True,
    "/oem/runtime/events/latest": True,
    "/oem/runtime/events/door": True,
    "/oem/runtime/events/pause": True,
    "/oem/runtime/events/resume": True,
    "/oem/runtime/readiness/prepare-to-run-job/dry-run": True,
    "/oem/runtime/commands/initializeSystem": True,
    "/oem/runtime/commands/PrepareToRunJob": True,
    "/oem/runtime/commands/validateJob": True,
    "/oem/runtime/commands/enqueue": True,
    "/oem/runtime/commands/abortjob": True,
    "/oem/runtime/commands/unlockProcess": True,
    "/oem/runtime/commands/wakefrompause": True,
    "/oem/runtime/commands/history": True,
    "/oem/motion_worker/status": True,
    "/oem/motion_worker/run_next": True,
    "/oem/motion_worker/abort": True,
    "/oem/switch_audit": True,
    "/motion/oem/startup_step": True,
    "/motion/oem/home_xy": True,
    "/motion/oem/rehome": True,
    "/motion/oem/initialize_motion": True,
    "/motion/range/status": True,
    "/motion/interlock/prepare": True,
    "/motion/interlock/override": True,
    "/motion/arm/strict_startup": True,
    "/motion/hard_reset": True,
    "/motion/reference/status": True,

    "/motion/axes/current": True,
    "/liquid/status": True,
    "/liquid/init": True,
    "/liquid/tip": True,
    "/liquid/aspirate": True,
    "/liquid/dispense": True,
    "/liquid/mix": True,
    "/camera/stream_state": True,
    "/vision/inspect": True,
    "/vision/barcode/read": True,
}

MANUAL_MOTION_ROUTES: Dict[str, bool] = {
    "/motion/axis/relative": True,
    "/motion/axis/absolute": True,
    "/motion/axis/zero": True,
    # Real robot-local manual switch-search home. This is intentionally separate
    # from /motion/axis/zero so the UI cannot silently replace home with zero.
    "/motion/axis/home": True,
}
DIRECT_LIQUID_COMMAND_ROUTES: Dict[str, bool] = {
    "/liquid/init": True,
    "/liquid/tip": True,
    "/liquid/aspirate": True,
    "/liquid/dispense": True,
    "/liquid/mix": True,
}
BMS_PROXIED_ROUTES: Dict[str, bool] = {
    **ROBOT_LOCAL_EXPECTED_ROUTES,
    **MANUAL_MOTION_ROUTES,
}
COMMISSIONING_ONLY_ROUTES: Dict[str, bool] = {
    "/motion/interlock/prepare": True,
    "/motion/interlock/override": True,
    **MANUAL_MOTION_ROUTES,
    **DIRECT_LIQUID_COMMAND_ROUTES,
}
DISABLED_ROUTES: Dict[str, bool] = {
    "/daemon/start": True,
    "/daemon/stop": True,
}

OPERATION_REQUIRED_ROUTES: Dict[str, list[str]] = {
    "prepare_safe": [
        "/status",
        "/motion/power/status",
        "/latch/status",
        "/motion/axes/status",
        "/motion/power/enable",
        "/motion/interlock/prepare",
        "/motion/arm/strict_startup",
    ],
    "latch_lock": ["/latch/status", "/latch/lock"],
    "latch_unlock": ["/latch/status", "/latch/unlock"],
    "head_clear_lock": ["/motion/axis/{axis}/status", "/motion/clear_lock"],
    "head_lift_increment": ["/motion/axis/{axis}/status", "/motion/axis/relative"],
    "micro_move_proof": ["/motion/axis/{axis}/status", "/motion/axis/relative"],

    "emergency_stop": ["/oem/runtime/emergency_stop"],
    "prepare_to_run_job_readiness": ["/oem/runtime/readiness/prepare-to-run-job/dry-run"],
}

OEM_IDLE_STANDBY_CURRENT = 10
OEM_MAX_RUN_CURRENT = 31
REFERENCE_OK_STATES = {"referenced", "synced", "known"}
GANTRY_CURRENT_AXES = {"x", "y", "z"}
MOTION_GUARDED_AXES = {"x", "y", "z", "g", "door"}


def _coerce_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _axis_from_payload(payload: Dict[str, Any]) -> str:
    axis = str(payload.get("axis") or "").lower().strip()
    if axis not in MOTION_GUARDED_AXES:
        raise HTTPException(status_code=400, detail=f"axis must be one of {sorted(MOTION_GUARDED_AXES)}")
    return axis


def _truthy_override(payload: Dict[str, Any], *names: str) -> bool:
    return any(bool(payload.get(name)) for name in names)


def _extract_row(payload: Any, axis: str) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    rows = payload.get("rows")
    if isinstance(rows, dict) and isinstance(rows.get(axis), dict):
        return rows[axis]
    axes = payload.get("axes")
    if isinstance(axes, dict) and isinstance(axes.get(axis), dict):
        return axes[axis]
    if isinstance(payload.get(axis), dict):
        return payload[axis]
    return {}


def _extract_position(row: Dict[str, Any]) -> Optional[int]:
    for key in ("position", "position_steps", "current_position", "current_position_steps"):
        value = _coerce_int(row.get(key))
        if value is not None:
            return value
    status = row.get("status")
    if isinstance(status, dict):
        position = status.get("position")
        if isinstance(position, dict):
            value = _coerce_int(position.get("position") or position.get("steps"))
            if value is not None:
                return value
    return None


def _first_present(mapping: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping.get(key) is not None:
            return mapping.get(key)
    return None


def _extract_limits(row: Dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
    limits = row.get("limits")
    if isinstance(limits, dict):
        min_value = _coerce_int(_first_present(limits, "min", "min_steps", "lower"))
        max_value = _coerce_int(_first_present(limits, "max", "max_steps", "upper"))
        if min_value is not None or max_value is not None:
            return min_value, max_value
    min_value = _coerce_int(_first_present(row, "min", "min_steps", "absolute_min", "lower"))
    max_value = _coerce_int(_first_present(row, "max", "max_steps", "absolute_max", "upper"))
    return min_value, max_value


def _is_axis_referenced(row: Dict[str, Any]) -> bool:
    state = str(row.get("state") or row.get("reference_state") or "unknown").lower()
    return state in REFERENCE_OK_STATES or bool(row.get("referenced"))


async def _motion_reference_row(axis: str) -> Dict[str, Any]:
    payload = await proxy_request("GET", "/motion/reference/status", timeout=20.0)
    return _extract_row(payload, axis)


async def _motion_range_row(axis: str) -> Dict[str, Any]:
    payload = await proxy_request("GET", "/motion/range/status", timeout=20.0)
    return _extract_row(payload, axis)


async def _validate_absolute_motion_payload(payload: Dict[str, Any]) -> None:
    axis = _axis_from_payload(payload)
    target = _coerce_int(_first_present(payload, "position_steps", "target_position", "position"))
    if target is None:
        raise HTTPException(status_code=400, detail="position_steps must be an integer")
    reference_row = await _motion_reference_row(axis)
    if not _is_axis_referenced(reference_row):
        state = str(reference_row.get("state") or reference_row.get("reference_state") or "unknown")
        raise HTTPException(
            status_code=409,
            detail=f"Refusing absolute {axis.upper()} move: reference state is {state}; mark/verify reference before absolute travel.",
        )
    range_row = await _motion_range_row(axis)
    min_steps, max_steps = _extract_limits(range_row)
    if min_steps is None or max_steps is None:
        raise HTTPException(
            status_code=409,
            detail=f"Refusing absolute {axis.upper()} move: configured range is unavailable from robot-local /motion/range/status.",
        )
    lower, upper = sorted((min_steps, max_steps))
    if not lower <= target <= upper:
        raise HTTPException(
            status_code=409,
            detail=f"Refusing absolute {axis.upper()} move: target {target} is outside runtime range {lower}..{upper}.",
        )


async def _validate_relative_motion_payload(payload: Dict[str, Any]) -> None:
    axis = _axis_from_payload(payload)
    steps = _coerce_int(_first_present(payload, "steps", "delta_steps"))
    if steps is None:
        raise HTTPException(status_code=400, detail="steps must be an integer")
    reference_row = await _motion_reference_row(axis)
    range_row = await _motion_range_row(axis)
    current_position = _extract_position(range_row)
    min_steps, max_steps = _extract_limits(range_row)
    referenced = _is_axis_referenced(reference_row)
    if axis == "z" and steps > 0 and not referenced and current_position is not None and current_position < 0:
        raise HTTPException(
            status_code=409,
            detail="Z positive/down move blocked while reference is unknown and controller position is negative; lift Z with a safe negative/up recovery path first.",
        )
    if axis == "door" and (min_steps is None or max_steps is None):
        raise HTTPException(
            status_code=409,
            detail="Refusing door relative move: configured range is unavailable from robot-local /motion/range/status.",
        )
    if current_position is not None and min_steps is not None and max_steps is not None:
        target = current_position + steps
        lower, upper = sorted((min_steps, max_steps))
        if not lower <= target <= upper:
            raise HTTPException(
                status_code=409,
                detail=f"Refusing relative {axis.upper()} move: target {target} is outside runtime range {lower}..{upper}.",
            )


def _validate_home_payload(payload: Dict[str, Any]) -> None:
    _axis_from_payload(payload)
    if not payload.get("capture_bundle"):
        raise HTTPException(
            status_code=400,
            detail="Switch-search home requires capture_bundle=true plus an operator_note describing native before/after camera/operator confirmation.",
        )
    note = str(payload.get("operator_note") or "").strip()
    if not note:
        raise HTTPException(
            status_code=400,
            detail="Switch-search home requires operator_note with before/after camera/operator confirmation context.",
        )


def _validated_axes_current_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    axes_value = payload.get("axes") or ["x", "y", "z"]
    if isinstance(axes_value, str):
        axes = [axis.strip().lower() for axis in axes_value.split(",") if axis.strip()]
    elif isinstance(axes_value, list):
        axes = [str(axis).lower().strip() for axis in axes_value]
    else:
        raise HTTPException(status_code=400, detail="axes must be a list or comma-separated string")
    invalid_axes = sorted(set(axes) - GANTRY_CURRENT_AXES)
    if invalid_axes:
        raise HTTPException(status_code=400, detail=f"axes current route is limited to gantry axes {sorted(GANTRY_CURRENT_AXES)}; invalid={invalid_axes}")
    run_current = _coerce_int(payload.get("run_current"))
    if run_current is None:
        run_current = OEM_IDLE_STANDBY_CURRENT
    standby_current = _coerce_int(payload.get("standby_current"))
    if standby_current is None:
        standby_current = OEM_IDLE_STANDBY_CURRENT
    for name, value in {"run_current": run_current, "standby_current": standby_current}.items():
        if not 0 <= value <= OEM_MAX_RUN_CURRENT:
            raise HTTPException(status_code=400, detail=f"{name} must be within TMCL current bounds 0..{OEM_MAX_RUN_CURRENT}")
    if standby_current > OEM_IDLE_STANDBY_CURRENT and not (
        _operator_ack(payload) and _truthy_override(payload, "commissioning_override", "allow_hot_standby_current")
    ):
        raise HTTPException(
            status_code=400,
            detail=f"standby_current above OEM idle {OEM_IDLE_STANDBY_CURRENT} requires operator_ack=true and commissioning_override=true.",
        )
    sanitized = dict(payload)
    sanitized.update({"axes": axes, "run_current": run_current, "standby_current": standby_current})
    return sanitized


def _runtime_status_payload(
    *,
    linkage_configured: bool,
    linked_runtime_reachable: bool,
    hardware_connected: bool,
    detail: str,
    proxy_error: Optional[Dict[str, Any]] = None,
    runtime_url: Optional[str] = None,
    linkage_active: Optional[bool] = None,
) -> Dict[str, Any]:
    if runtime_url is None and linkage_configured:
        runtime_url = _GLOBAL_LINKAGE_URL
    if linkage_active is None:
        linkage_active = bool(_GLOBAL_LINKAGE_URL)
    return {
        "running": linked_runtime_reachable,
        "healthy": linked_runtime_reachable,
        "stale_process": False,
        "host": ROBOT_SSH_HOST,
        "port": ROBOT_DAEMON_PORT,
        "runtime_url": runtime_url,
        "linkage_configured": linkage_configured,
        "linkage_active": linkage_active,
        "linked_runtime_reachable": linked_runtime_reachable,
        "hardware_connected": hardware_connected,
        "admin_control_available": False,
        "maintenance_mode": "robot-local",
        "recommended_url": _recommended_linkage_url(),
        "detail": detail,
        "proxy_error": proxy_error,
        "inferred_via_proxy": False,
    }


LOCAL_ADMIN_HOSTS = {None, "127.0.0.1", "::1", "localhost", "testclient"}


def _require_local_admin(request: Request) -> None:
    if request.client and request.client.host not in LOCAL_ADMIN_HOSTS:
        raise HTTPException(status_code=403, detail="BioXP interlink lifecycle routes are limited to local admin requests")


def _request_model_dump(payload: BaseModel | None) -> Dict[str, Any]:
    if payload is None:
        return {}
    return payload.model_dump(exclude_none=True)


def _interlink_state_response() -> Dict[str, Any]:
    return bioxp_interlink.describe_state(recommended_url=_recommended_linkage_url())


async def _probe_active_interlink(timeout: float = 18.0) -> None:
    try:
        payload = await proxy_request("GET", "/status", timeout=timeout)
        if isinstance(payload, dict) and not bool(payload.get("hardware_connected")) and bool(payload.get("runtime_available", True)):
            try:
                power_payload = await proxy_request("GET", "/motion/power/status", timeout=45.0)
                if isinstance(power_payload, dict) and bool(power_payload.get("hardware_connected")):
                    payload = {
                        **payload,
                        "status": "ok" if not payload.get("startup_error") and not payload.get("status_error") else payload.get("status", "degraded"),
                        "hardware_connected": True,
                        "targeted_power_readback": power_payload,
                        "hardware_connected_inferred_via": "/motion/power/status",
                    }
            except HTTPException:
                pass
        bioxp_interlink.record_probe_result(payload if isinstance(payload, dict) else {"raw_payload": payload})
    except HTTPException as exc:
        bioxp_interlink.record_probe_result(
            None,
            {"status_code": exc.status_code, "detail": exc.detail},
        )


# ── Governed Interlink Endpoints ───────────────────────────────────────

@router.get("/interlink/state")
async def get_interlink_state(probe: bool = False):
    if probe and _GLOBAL_LINKAGE_URL:
        await _probe_active_interlink(timeout=18.0)
    return _interlink_state_response()


@router.put("/interlink/settings")
async def save_interlink_settings(request: Request, payload: InterlinkSettingsRequest):
    _require_local_admin(request)
    # Persist only the operator profile. This is intentionally quiet: no robot
    # polling, no USB recovery, no auto-activation, and no motion side effects.
    bioxp_interlink.save_profile(payload.model_dump(exclude_none=True))
    return _interlink_state_response()


@router.delete("/interlink/settings")
async def forget_interlink_settings(request: Request):
    global _GLOBAL_LINKAGE_URL
    _require_local_admin(request)
    bioxp_interlink.forget_profile()
    bioxp_interlink.reset_session()
    _GLOBAL_LINKAGE_URL = None
    return _interlink_state_response()


@router.post("/interlink/connect")
async def connect_interlink(request: Request, payload: InterlinkSettingsRequest | None = None):
    global _GLOBAL_LINKAGE_URL
    _require_local_admin(request)
    if payload is not None and payload.robot_api_url:
        profile = bioxp_interlink.save_profile(payload.model_dump(exclude_none=True))
    else:
        profile = bioxp_interlink.load_profile()
        if not profile:
            raise HTTPException(status_code=400, detail="Save a BioXP interlink profile before connecting")
    _GLOBAL_LINKAGE_URL = _canonicalize_linkage_url(profile["robot_api_url"])
    if not _GLOBAL_LINKAGE_URL:
        raise HTTPException(status_code=400, detail="BioXP robot_api_url is empty or invalid")
    bioxp_interlink.activate_session(_GLOBAL_LINKAGE_URL)
    await _probe_active_interlink(timeout=18.0)
    return _interlink_state_response()


@router.post("/interlink/disconnect")
async def disconnect_interlink(request: Request):
    global _GLOBAL_LINKAGE_URL
    _require_local_admin(request)
    bioxp_interlink.deactivate_session("operator-disconnect")
    _GLOBAL_LINKAGE_URL = None
    return _interlink_state_response()


@router.post("/interlink/diagnostics")
async def interlink_diagnostics(request: Request, probe: bool = True):
    _require_local_admin(request)
    if probe and _GLOBAL_LINKAGE_URL:
        await _probe_active_interlink(timeout=18.0)
    return _interlink_state_response()


@router.post("/interlink/logs")
async def interlink_logs(request: Request, payload: InterlinkLifecycleRequest | None = None):
    _require_local_admin(request)
    try:
        return bioxp_interlink.collect_logs(_request_model_dump(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/interlink/runtime-reset")
async def interlink_runtime_reset(request: Request, payload: InterlinkLifecycleRequest | None = None):
    global _GLOBAL_LINKAGE_URL
    _require_local_admin(request)
    try:
        result = bioxp_interlink.runtime_reset(_request_model_dump(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result.get("supported", True) is not False:
        _GLOBAL_LINKAGE_URL = None
    return result


@router.post("/interlink/robot-reboot")
async def interlink_robot_reboot(request: Request, payload: InterlinkLifecycleRequest | None = None):
    global _GLOBAL_LINKAGE_URL
    _require_local_admin(request)
    try:
        result = bioxp_interlink.robot_reboot(_request_model_dump(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result.get("supported", True) is not False:
        _GLOBAL_LINKAGE_URL = None
    return result


# ── Legacy linkage compatibility endpoints ─────────────────────────────

@router.get("/linkage")
async def get_linkage():
    return {
        "url": _GLOBAL_LINKAGE_URL,
        "configured": bool(_GLOBAL_LINKAGE_URL),
        "recommended_url": _recommended_linkage_url(),
    }

@router.post("/linkage")
async def set_linkage(req: LinkageRequest):
    _ = req
    raise HTTPException(
        status_code=409,
        detail="Legacy /api/bioxp/linkage mutation is disabled. Save settings with /interlink/settings, then explicitly connect with /interlink/connect from the BIOXP LINK panel.",
    )

@router.post("/linkage/disconnect")
async def disconnect_linkage():
    raise HTTPException(
        status_code=409,
        detail="Legacy /api/bioxp/linkage/disconnect mutation is disabled. Use /interlink/disconnect from the governed BIOXP LINK panel.",
    )


# ── Runtime Status / Deprecated Maintenance Compatibility ───────────────

@router.get("/daemon/status")
@router.get("/runtime/status")
async def daemon_status():
    """Report linked BioXP runtime reachability without SSH/process inspection."""
    if not _GLOBAL_LINKAGE_URL:
        profile = bioxp_interlink.load_profile()
        if profile:
            return _runtime_status_payload(
                linkage_configured=True,
                linkage_active=False,
                linked_runtime_reachable=False,
                hardware_connected=False,
                runtime_url=profile.get("robot_api_url"),
                detail=(
                    "BioXP interlink profile is saved but inactive. Press Connect to activate the BMS proxy; "
                    "no robot polling runs until then."
                ),
            )
        return _runtime_status_payload(
            linkage_configured=False,
            linkage_active=False,
            linked_runtime_reachable=False,
            hardware_connected=False,
            detail="No BioXP interlink profile is saved. Enter the robot API URL and Save settings or Connect.",
        )

    try:
        payload = await proxy_request("GET", "/status", timeout=35.0)
        if not isinstance(payload, dict):
            payload = {"status": "error", "raw_payload": payload}
        hardware_connected = bool(payload.get("hardware_connected"))
        targeted_power: Optional[Dict[str, Any]] = None
        if not hardware_connected and bool(payload.get("runtime_available", True)):
            # The robot-local aggregate /status can be conservative/degraded while
            # service-owned targeted controller readbacks are healthy. Use the same
            # motion-power route the cockpit relies on before telling operators the
            # hardware is disconnected.
            try:
                power_payload = await proxy_request("GET", "/motion/power/status", timeout=45.0)
                if isinstance(power_payload, dict):
                    targeted_power = power_payload
                    hardware_connected = bool(power_payload.get("hardware_connected"))
            except HTTPException:
                targeted_power = None
        detail = payload.get("status_error") or payload.get("startup_error")
        if not detail:
            detail = (
                "Linked BioXP runtime responded and targeted controller readback reports hardware connectivity."
                if hardware_connected and targeted_power
                else "Linked BioXP runtime responded to /status and reported hardware connectivity."
                if hardware_connected
                else "Linked BioXP runtime responded to /status, but hardware is not yet connected."
            )
        result = _runtime_status_payload(
            linkage_configured=True,
            linked_runtime_reachable=True,
            hardware_connected=hardware_connected,
            detail=str(detail),
        )
        if targeted_power is not None:
            result["targeted_power_readback"] = targeted_power
            result["inferred_via_proxy"] = True
        return result
    except HTTPException as exc:
        return _runtime_status_payload(
            linkage_configured=True,
            linked_runtime_reachable=False,
            hardware_connected=False,
            detail=str(exc.detail),
            proxy_error={
                "status_code": exc.status_code,
                "detail": exc.detail,
            },
        )


@router.post("/daemon/start")
async def daemon_start():
    """Deprecated: robot-local runtime lifecycle must not be controlled from BMS."""
    raise HTTPException(status_code=409, detail=_maintenance_disabled_detail())


@router.post("/daemon/stop")
async def daemon_stop():
    """Deprecated: robot-local runtime lifecycle must not be controlled from BMS."""
    raise HTTPException(status_code=409, detail=_maintenance_disabled_detail())


async def proxy_request(
    method: str,
    path: str,
    json_data: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = 65.0,
):
    """Generic HTTPX proxy function to forward raw requests to the BioXP Node."""
    base_url = get_current_url()
    url = f"{base_url}{path}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method == "GET":
                response = await client.get(url, params=params)
            elif method == "POST":
                response = await client.post(url, json=json_data, params=params)
            else:
                raise HTTPException(status_code=405, detail="Method not supported by proxy")

            response.raise_for_status()
            return response.json()

    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot connect to BioXP hardware node at {base_url}. Is the robot-local bioxp.api runtime reachable?"
        )
    except httpx.ReadTimeout:
        raise HTTPException(
            status_code=504,
            detail=f"BioXP hardware node timed out responding to {method} {path}"
        )
    except httpx.HTTPStatusError as e:
        # Pass through the hardware node's explicit error response if it has one
        try:
            error_detail = e.response.json()
        except Exception:
            error_detail = e.response.text
        raise HTTPException(status_code=e.response.status_code, detail=error_detail)


async def _request_json_or_empty(request: Request) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _operator_ack(payload: Dict[str, Any]) -> bool:
    return bool(payload.get("operator_ack") or payload.get("acknowledged") or payload.get("operator_confirmed"))


def _require_operator_ack(payload: Dict[str, Any], operation: str) -> None:
    if not _operator_ack(payload):
        raise HTTPException(
            status_code=400,
            detail=f"{operation} requires operator_ack=true after physical clear-path confirmation.",
        )


def _operation_report(
    operation: str,
    *,
    risk: str,
    payload: Dict[str, Any],
    before: Optional[Dict[str, Any]] = None,
    actions: Optional[list[Dict[str, Any]]] = None,
    after: Optional[Dict[str, Any]] = None,
    notes: Optional[list[str]] = None,
) -> Dict[str, Any]:
    return {
        "schema_version": "bioxp.bms_operation_report.v1",
        "operation": operation,
        "risk": risk,
        "operator_ack": _operator_ack(payload),
        "operator": payload.get("operator") or payload.get("operator_id") or "bms-cockpit",
        "physical_confirmation_required": True,
        "truth_level": "controller_only_until_operator_confirms",
        "before": before or {},
        "actions": actions or [],
        "after": after or {},
        "notes": notes or [
            "BMS proxied named operation over the robot-local BioXP API; BMS did not supervise or restart the robot daemon.",
            "Controller readbacks do not prove physical motion; operator observation remains authoritative.",
        ],
    }


async def _probe_operation(path: str, *, method: str = "GET", json_data: Optional[Dict[str, Any]] = None, params: Optional[Dict[str, Any]] = None, timeout: float = 12.0) -> Dict[str, Any]:
    try:
        data = await proxy_request(method, path, json_data=json_data, params=params, timeout=timeout)
        return {"ok": True, "path": path, "data": data}
    except HTTPException as exc:
        return {"ok": False, "path": path, "error": {"status_code": exc.status_code, "detail": exc.detail}}


def _is_axis_idle(axis_payload: Dict[str, Any]) -> bool:
    try:
        speed = axis_payload.get("status", {}).get("speed", {}).get("speed")
    except Exception:
        return False
    return speed in (0, 0.0, None)


def _skipped_probe(path: str, reason: str) -> Dict[str, Any]:
    return {"ok": None, "path": path, "skipped": True, "reason": reason}


async def proxy_stream(path: str, request: Request, params: Optional[Dict[str, Any]] = None):
    base_url = get_current_url()
    url = f"{base_url}{path}"
    client = httpx.AsyncClient(timeout=None)
    try:
        upstream = await client.send(
            client.build_request("GET", url, params=params),
            stream=True,
        )
        upstream.raise_for_status()
    except httpx.ConnectError:
        await client.aclose()
        raise HTTPException(
            status_code=503,
            detail=f"Cannot connect to BioXP hardware node at {base_url}. Is the robot-local bioxp.api runtime reachable?"
        )
    except httpx.HTTPStatusError as exc:
        body = await exc.response.aread()
        content_type = exc.response.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                detail = exc.response.json()
            except Exception:
                detail = body.decode("utf-8", errors="replace").strip() or exc.response.reason_phrase
        else:
            detail = body.decode("utf-8", errors="replace").strip() or exc.response.reason_phrase
        await exc.response.aclose()
        await client.aclose()
        raise HTTPException(status_code=exc.response.status_code, detail=detail)
    except Exception:
        await client.aclose()
        raise

    async def iterator():
        try:
            async for chunk in upstream.aiter_bytes():
                if await request.is_disconnected():
                    break
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    content_type = upstream.headers.get("content-type", "application/octet-stream")
    headers = {
        "Cache-Control": "no-cache, no-store, no-transform",
        "Pragma": "no-cache",
        # Prevent reverse-proxy buffering (Tailscale Serve, nginx, etc.)
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    if upstream.headers.get("x-bioxp-camera-device"):
        headers["X-BioXp-Camera-Device"] = upstream.headers["x-bioxp-camera-device"]

    return StreamingResponse(
        iterator(),
        media_type=content_type,
        headers=headers,
    )

@router.get("/status")
async def get_status():
    if not _GLOBAL_LINKAGE_URL:
        return {
            "status": "not_configured",
            "transport": "proxy",
            "hardware_connected": False,
            "linkage_configured": False,
            "linkage_url": None,
            "recommended_url": _recommended_linkage_url(),
            "startup_error": None,
            "proxy_error": {
                "status_code": 400,
                "detail": "Hardware Node URL is not configured. Enter the robot runtime URL and press Connect.",
            },
        }

    try:
        payload = await proxy_request("GET", "/status", timeout=18.0)
        if not isinstance(payload, dict):
            payload = {"status": "error", "raw_payload": payload}
        if not bool(payload.get("hardware_connected")) and bool(payload.get("runtime_available", True)):
            # Keep the main cockpit status aligned with runtime/status and interlink
            # semantics: the aggregate robot /status can stay conservative/degraded
            # while a service-owned targeted controller readback proves the API still
            # has board-level contact. This is connectivity only, not physical motion proof.
            try:
                power_payload = await proxy_request("GET", "/motion/power/status", timeout=45.0)
                if isinstance(power_payload, dict) and bool(power_payload.get("hardware_connected")):
                    payload = {
                        **payload,
                        "status": "ok" if not payload.get("startup_error") and not payload.get("status_error") else payload.get("status", "degraded"),
                        "hardware_connected": True,
                        "targeted_power_readback": power_payload,
                        "hardware_connected_inferred_via": "/motion/power/status",
                    }
            except HTTPException:
                pass
        payload["linkage_configured"] = True
        payload["linkage_url"] = _GLOBAL_LINKAGE_URL
        payload["recommended_url"] = _recommended_linkage_url()
        return payload
    except HTTPException as exc:
        # /status is a slow, hardware-touching aggregate on this robot and can time out
        # even while the robot-local FastAPI and specific controller routes are alive.
        # Keep the cockpit usable by falling back to the cheap OpenAPI control-plane probe;
        # do not claim hardware motion readiness from this fallback.
        control_plane_reachable = False
        try:
            openapi = await proxy_request("GET", "/openapi.json", timeout=5.0)
            control_plane_reachable = isinstance(openapi, dict) and isinstance(openapi.get("paths"), dict)
        except HTTPException:
            control_plane_reachable = False
        return {
            "status": "degraded" if control_plane_reachable else ("offline" if exc.status_code in (503, 504) else "error"),
            "transport": "proxy",
            "runtime_available": control_plane_reachable,
            "control_plane_reachable": control_plane_reachable,
            "hardware_connected": False,
            "linkage_configured": True,
            "linkage_url": _GLOBAL_LINKAGE_URL,
            "recommended_url": _recommended_linkage_url(),
            "startup_error": None,
            "status_error": "Hardware aggregate /status timed out; use OEM recover/arm or targeted readbacks. This is not physical-motion proof.",
            "proxy_error": {
                "status_code": exc.status_code,
                "detail": exc.detail,
            },
        }

@router.post("/reconnect")
async def reconnect_runtime():
    return await proxy_request("POST", "/reconnect")


async def robot_capabilities() -> Dict[str, Any]:
    openapi_paths: Dict[str, bool] = {}
    openapi_error: Optional[Dict[str, Any]] = None
    if _GLOBAL_LINKAGE_URL:
        try:
            openapi = await proxy_request("GET", "/openapi.json", timeout=12.0)
            raw_paths = openapi.get("paths", {}) if isinstance(openapi, dict) else {}
            if isinstance(raw_paths, dict):
                openapi_paths = {str(path): True for path in raw_paths.keys()}
        except HTTPException as exc:
            openapi_error = {"status_code": exc.status_code, "detail": exc.detail}
    supported_expected = {
        path: bool(openapi_paths.get(path))
        for path in sorted(ROBOT_LOCAL_EXPECTED_ROUTES)
    }
    missing_expected = {
        path: True
        for path, expected in sorted(ROBOT_LOCAL_EXPECTED_ROUTES.items())
        if expected and openapi_paths and not openapi_paths.get(path)
    }
    unexpected_supported = {
        path: True
        for path in sorted(openapi_paths)
        if path not in ROBOT_LOCAL_EXPECTED_ROUTES and path != "/openapi.json"
    }
    return {
        "schema_version": "bioxp.robot_capabilities.v1",
        "linkage_url": _GLOBAL_LINKAGE_URL,
        "linkage_configured": bool(_GLOBAL_LINKAGE_URL),
        "recommended_url": _recommended_linkage_url(),
        "robot_openapi_reachable": bool(openapi_paths),
        "openapi_error": openapi_error,
        "supported_routes": supported_expected,
        "missing_expected_routes": missing_expected,
        "unexpected_robot_routes": unexpected_supported,
        "bms_proxy_routes": dict(sorted(BMS_PROXIED_ROUTES.items())),
        "notes": [
            "This is a route-diff control-plane check against the linked robot-local API.",
            "The raw FastAPI port may live inside Docker; BMS clients should use named /api/bioxp proxy routes, not container-internal ports.",
            "Route support does not prove hardware readiness or physical motion.",
        ],
    }


@router.get("/robot/capabilities")
async def get_robot_capabilities():
    return await robot_capabilities()


@router.get("/capabilities")
async def bioxp_capabilities():
    robot_diff = await robot_capabilities()
    return {
        "linkage_url": _GLOBAL_LINKAGE_URL,
        "linkage_configured": bool(_GLOBAL_LINKAGE_URL),
        "recommended_url": _recommended_linkage_url(),
        "robot_hardware_assumption": "functional_under_oem",
        "truth_source": "robot_local_oem_compat_layer",
        "bms_role": "thin_operator_surface",
        "robot_local_expected_routes": dict(sorted(ROBOT_LOCAL_EXPECTED_ROUTES.items())),
        "robot_capability_diff": robot_diff,
        "bms_proxy_routes": dict(sorted(BMS_PROXIED_ROUTES.items())),
        "default_operator_routes": dict(sorted({
            path: enabled
            for path, enabled in BMS_PROXIED_ROUTES.items()
            if not path.startswith("/motion/interlock/")
            and path not in MANUAL_MOTION_ROUTES
            and path not in DIRECT_LIQUID_COMMAND_ROUTES
        }.items())),
        "manual_motion_routes": dict(sorted(MANUAL_MOTION_ROUTES.items())),
        "commissioning_only_routes": dict(sorted(COMMISSIONING_ONLY_ROUTES.items())),
        "disabled_routes": dict(sorted(DISABLED_ROUTES.items())),
        "notes": [
            "BMS links to the robot-local BioXP runtime and exposes only the routes listed as proxied.",
            "named BMS proxy routes are the stable client surface; clients must not depend on raw container-internal FastAPI ports.",
            "Default operator UI is OEM-first: startup/runtime, protocol, liquid readback, range/switch readback, thermal, chiller, camera, and vision. Raw axis movement and direct pipette commands are commissioning-only.",
            "Daemon lifecycle routes are disabled by design because BMS must not own the robot-local service process.",
            "Route parity is a control-plane capability statement; hardware readiness still comes from runtime status/preflight responses.",
            "The robot is treated as functional under OEM control; BMS should not present unresolved Linux parity work as a bad-component verdict.",
        ],
    }


@router.get("/operations/capabilities")
async def bioxp_operations_capabilities():
    openapi_paths: Dict[str, bool] = {}
    openapi_error: Optional[Dict[str, Any]] = None
    if _GLOBAL_LINKAGE_URL:
        try:
            openapi = await proxy_request("GET", "/openapi.json", timeout=35.0)
            raw_paths = openapi.get("paths", {}) if isinstance(openapi, dict) else {}
            if isinstance(raw_paths, dict):
                openapi_paths = {str(path): True for path in raw_paths.keys()}
        except HTTPException as exc:
            openapi_error = {"status_code": exc.status_code, "detail": exc.detail}
    route_source = openapi_paths or {**BMS_PROXIED_ROUTES, "/motion/axis/{axis}/status": True}

    def route_available(route: str) -> bool:
        if route_source.get(route):
            return True
        if "{axis}" in route:
            return any(route_source.get(route.replace("{axis}", axis)) for axis in ("x", "y", "z", "g", "door"))
        for axis in ("x", "y", "z", "g", "door"):
            templated = route.replace(f"/{axis}/", "/{axis}/")
            axis_prefix_templated = route.replace(f"/{axis}/", "/{axis}/")
            if templated != route and route_source.get(templated):
                return True
            if axis_prefix_templated != route and route_source.get(axis_prefix_templated):
                return True
        return False

    operations: Dict[str, Dict[str, Any]] = {}
    for name, routes in OPERATION_REQUIRED_ROUTES.items():
        availability = {route: route_available(route) for route in routes}
        risk = "low" if name == "prepare_to_run_job_readiness" else ("high" if name in {"head_clear_lock", "head_lift_increment", "micro_move_proof"} else "medium")
        operations[name] = {
            "available": all(availability.values()),
            "required_routes": availability,
            "risk": risk,
            "operator_ack_required": name not in {"emergency_stop", "prepare_to_run_job_readiness"},
        }

    return {
        "schema_version": "bioxp.bms_operations_capabilities.v1",
        "linkage_url": _GLOBAL_LINKAGE_URL,
        "linkage_configured": bool(_GLOBAL_LINKAGE_URL),
        "robot_openapi_reachable": bool(openapi_paths),
        "openapi_error": openapi_error,
        "operations": operations,
        "safety_boundary": "robot-local FastAPI owns hardware/runtime; BMS exposes named, gated service operations only.",
    }


@router.get("/operations/readiness")
async def bioxp_operations_readiness(request: Request):
    # Keep the default service-tab readiness light. The robot-local API serializes
    # hardware access behind a USB runtime lock; continuously polling every slow
    # status/latch/axes endpoint from the UI can occupy that lock and make user
    # operations look hung. Use ?full=true only for an explicit deep diagnostic.
    full_probe = (request.query_params.get("full") or request.query_params.get("mode") or "").lower() in {"1", "true", "full", "hardware"}
    status = await _probe_operation("/status", timeout=35.0) if full_probe else _skipped_probe("/status", "lightweight readiness avoids occupying the robot USB runtime lock; pass ?full=true for a deep hardware poll.")
    power = await _probe_operation("/motion/power/status", timeout=45.0) if full_probe else _skipped_probe("/motion/power/status", "skipped in default readiness so the cockpit stays responsive; explicit service operations collect power readback before movement.")
    latch = await _probe_operation("/latch/status", timeout=35.0) if full_probe else _skipped_probe("/latch/status", "skipped in lightweight readiness; service operations collect before/after latch readback when invoked.")
    axes = await _probe_operation("/motion/axes/status", params={"axes": "x,y,z,g,door"}, timeout=45.0) if full_probe else _skipped_probe("/motion/axes/status", "skipped in lightweight readiness; movement operations collect axis readback when invoked.")
    reference = await _probe_operation("/motion/reference/status", params={"axes": "x,y,z,g,door"}, timeout=20.0) if full_probe else _skipped_probe("/motion/reference/status", "skipped in default readiness; use full=true or a named motion recipe for hardware reference readback.")
    status_data = status.get("data") if isinstance(status.get("data"), dict) else {}
    power_data = power.get("data") if isinstance(power.get("data"), dict) else {}
    return {
        "schema_version": "bioxp.bms_operations_readiness.v1",
        "linkage_url": _GLOBAL_LINKAGE_URL,
        "runtime_reachable": bool(power.get("ok") or reference.get("ok") or status.get("ok")),
        "hardware_connected": bool(power_data.get("hardware_connected") or status_data.get("hardware_connected")),
        "full_probe": full_probe,
        "layers": {"status": status, "power": power, "latch": latch, "axes": axes, "reference": reference},
        "notes": [
            "Default readiness intentionally avoids hammering every hardware endpoint.",
            "Readiness is a bundle of controller readbacks, not physical proof.",
            "Movement operations still require operator clear-path acknowledgement.",
        ],
    }


@router.post("/operations/motion/prepare-safe")
async def bioxp_operation_prepare_safe(request: Request):
    payload = await _request_json_or_empty(request)
    _require_operator_ack(payload, "prepare_safe")
    before = {
        "axes": await _probe_operation("/motion/axes/status", params={"axes": "x,y,z,g,door"}, timeout=25.0),
        "reference": await _probe_operation("/motion/reference/status", params={"axes": "x,y,z,g,door"}, timeout=10.0),
        "latch": await _probe_operation("/latch/status", timeout=18.0),
    }
    axes_data = before["axes"].get("data", {}) if before["axes"].get("ok") else {}
    rows = axes_data.get("rows", {}) if isinstance(axes_data, dict) else {}
    moving_axes = [axis for axis, row in rows.items() if isinstance(row, dict) and not _is_axis_idle(row)] if isinstance(rows, dict) else []
    if moving_axes:
        raise HTTPException(status_code=409, detail={"message": "Refusing prepare-safe while axes report nonzero speed.", "moving_axes": moving_axes})

    actions = [
        {"name": "strict_startup_no_homing", "result": await proxy_request("POST", "/motion/arm/strict_startup", json_data={"run_homing": False, "operator": payload.get("operator", "bms-cockpit"), "source": "bms-operation-prepare-safe"}, timeout=190.0)},
    ]
    after = {
        "axes": await _probe_operation("/motion/axes/status", params={"axes": "x,y,z,g,door"}, timeout=25.0),
        "reference": await _probe_operation("/motion/reference/status", params={"axes": "x,y,z,g,door"}, timeout=10.0),
        "latch": await _probe_operation("/latch/status", timeout=18.0),
    }
    return _operation_report(
        "prepare_safe",
        risk="medium",
        payload=payload,
        before=before,
        actions=actions,
        after=after,
        notes=[
            "Uses the robot-local strict-startup/no-homing recipe directly; avoids the slow /motion/power/status aggregate that is currently timing out.",
            "No intentional axis travel is commanded by this recipe, but it energizes/arms the motion gate.",
            "Controller arm/readback success is not physical movement proof.",
        ],
    )


@router.post("/operations/head/clear-lock")
async def bioxp_operation_head_clear_lock(request: Request):
    payload = await _request_json_or_empty(request)
    _require_operator_ack(payload, "head_clear_lock")
    before = {"z": await _probe_operation("/motion/axis/z/status", timeout=25.0), "reference": await _probe_operation("/motion/reference/status", params={"axes": "z"}, timeout=10.0)}
    actions = [{"name": "clear_lock", "result": await proxy_request("POST", "/motion/clear_lock", timeout=120.0)}]
    after = {"z": await _probe_operation("/motion/axis/z/status", timeout=25.0), "reference": await _probe_operation("/motion/reference/status", params={"axes": "z"}, timeout=10.0)}
    return _operation_report(
        "head_clear_lock",
        risk="medium_high",
        payload=payload,
        before=before,
        actions=actions,
        after=after,
        notes=[
            "Calls the robot-local all-up/clear-lock primitive instead of a generic single-direction jog.",
            "This is live Z/head motion; operator physical observation is authoritative.",
        ],
    )


@router.post("/operations/head/lift-increment")
async def bioxp_operation_head_lift_increment(request: Request):
    payload = await _request_json_or_empty(request)
    _require_operator_ack(payload, "head_lift_increment")
    steps_abs = int(payload.get("steps_abs") or 500)
    if steps_abs not in {500, 1000, 2500}:
        raise HTTPException(status_code=400, detail="steps_abs must be one of 500, 1000, or 2500 for supervised head lift increments.")
    before = {"z": await _probe_operation("/motion/axis/z/status", timeout=35.0)}
    move_payload = {"axis": "z", "steps": -abs(steps_abs), "reuse_prepared": False, "capture_bundle": True, "operator_note": payload.get("operator_note") or "BMS head lift increment"}
    actions = [{"name": "z_lift_increment", "result": await proxy_request("POST", "/motion/axis/relative", json_data=move_payload, timeout=65.0)}]
    after = {"z": await _probe_operation("/motion/axis/z/status", timeout=35.0)}
    return _operation_report("head_lift_increment", risk="medium_high", payload=payload, before=before, actions=actions, after=after)


@router.post("/operations/motion/micro-move-proof")
async def bioxp_operation_micro_move_proof(request: Request):
    payload = await _request_json_or_empty(request)
    _require_operator_ack(payload, "micro_move_proof")
    axis = str(payload.get("axis") or "x").lower()
    if axis not in {"x", "y", "z", "g", "door"}:
        raise HTTPException(status_code=400, detail="axis must be one of x, y, z, g, or door.")
    steps = int(payload.get("steps") or 100)
    if abs(steps) > 500:
        raise HTTPException(status_code=400, detail="micro_move_proof is capped at +/-500 steps.")
    before = {"axis": await _probe_operation(f"/motion/axis/{axis}/status", timeout=35.0)}
    move_payload = {"axis": axis, "steps": steps, "reuse_prepared": False, "capture_bundle": True, "operator_note": payload.get("operator_note") or "BMS micro-move proof"}
    actions = [{"name": "micro_move", "result": await proxy_request("POST", "/motion/axis/relative", json_data=move_payload, timeout=65.0)}]
    after = {"axis": await _probe_operation(f"/motion/axis/{axis}/status", timeout=35.0)}
    return _operation_report("micro_move_proof", risk="high", payload=payload, before=before, actions=actions, after=after)


async def _latch_operation(action: str, payload: Dict[str, Any]):
    _require_operator_ack(payload, f"latch_{action}")
    before = {"latch": await _probe_operation("/latch/status", timeout=35.0)}
    actions = [{"name": f"latch_{action}", "result": await proxy_request("POST", f"/latch/{action}", timeout=20.0)}]
    after = {"latch": await _probe_operation("/latch/status", timeout=35.0)}
    return _operation_report(f"latch_{action}", risk="medium", payload=payload, before=before, actions=actions, after=after)


@router.post("/operations/latch/lock")
async def bioxp_operation_latch_lock(request: Request):
    return await _latch_operation("lock", await _request_json_or_empty(request))


@router.post("/operations/latch/unlock")
async def bioxp_operation_latch_unlock(request: Request):
    return await _latch_operation("unlock", await _request_json_or_empty(request))


@router.post("/operations/emergency-stop")
async def bioxp_operation_emergency_stop(request: Request):
    payload = await _request_json_or_empty(request)
    before = {"runtime": await _probe_operation("/oem/runtime/status", timeout=35.0)}
    actions = [{"name": "oem_runtime_emergency_stop", "result": await proxy_request("POST", "/oem/runtime/emergency_stop", json_data={"operator": payload.get("operator", "bms-cockpit"), "source": "bms-operation-emergency-stop"}, timeout=30.0)}]
    after = {"runtime": await _probe_operation("/oem/runtime/status", timeout=35.0)}
    return _operation_report("emergency_stop", risk="emergency", payload={**payload, "operator_ack": True}, before=before, actions=actions, after=after, notes=["Emergency stop proxied to robot-local OEM runtime. Verify actual motion stop physically."])


@router.get("/capabilities/oem-test-prep")
async def bioxp_oem_test_prep_capabilities():
    return await proxy_request("GET", "/oem-compat/capabilities/test-prep", timeout=20.0)




@router.post("/oem-compat/startup/dry-run")
async def oem_compat_startup_dry_run(request: Request):
    return await proxy_request("POST", "/oem-compat/startup/dry-run", await request.json(), timeout=45.0)


@router.post("/oem-compat/protocols/import/dry-run")
async def oem_compat_protocol_import_dry_run(request: Request):
    return await proxy_request("POST", "/oem-compat/protocols/import/dry-run", await request.json(), timeout=45.0)


@router.post("/oem-compat/scripts/translate/dry-run")
async def oem_compat_script_translate_dry_run(request: Request):
    return await proxy_request("POST", "/oem-compat/scripts/translate/dry-run", await request.json(), timeout=45.0)


@router.post("/oem/initial_check")
async def oem_initial_check(request: Request):
    return await proxy_request("POST", "/oem/initial_check", await request.json(), timeout=90.0)


@router.post("/oem/startup/request")
async def oem_startup_request(request: Request):
    return await proxy_request("POST", "/oem/startup/request", await request.json(), timeout=190.0)


@router.get("/oem/startup/status/latest")
async def oem_startup_status_latest():
    return await proxy_request("GET", "/oem/startup/status/latest", timeout=30.0)


@router.get("/oem/startup/status/{session_id}")
async def oem_startup_status(session_id: str):
    return await proxy_request("GET", f"/oem/startup/status/{session_id}", timeout=30.0)


@router.post("/oem/startup/door_event")
async def oem_startup_door_event(request: Request):
    return await proxy_request("POST", "/oem/startup/door_event", await request.json(), timeout=45.0)


@router.get("/oem/runtime/status")
async def oem_runtime_status():
    return await proxy_request("GET", "/oem/runtime/status", timeout=30.0)


@router.get("/oem/runtime/state")
async def oem_runtime_state():
    return await proxy_request("GET", "/oem/runtime/state", timeout=30.0)


@router.get("/oem/runtime/worker/status")
async def oem_runtime_worker_status():
    return await proxy_request("GET", "/oem/runtime/worker/status", timeout=30.0)


@router.post("/oem/runtime/recover")
async def oem_runtime_recover(request: Request):
    return await proxy_request("POST", "/oem/runtime/recover", await request.json(), timeout=90.0)


@router.post("/oem/runtime/emergency_stop")
async def oem_runtime_emergency_stop(request: Request):
    return await proxy_request("POST", "/oem/runtime/emergency_stop", await request.json(), timeout=30.0)


@router.get("/oem/runtime/events/latest")
async def oem_runtime_events_latest():
    return await proxy_request("GET", "/oem/runtime/events/latest", timeout=30.0)


@router.post("/oem/runtime/events/door")
async def oem_runtime_event_door(request: Request):
    return await proxy_request("POST", "/oem/runtime/events/door", await request.json(), timeout=45.0)


@router.post("/oem/runtime/events/pause")
async def oem_runtime_event_pause(request: Request):
    return await proxy_request("POST", "/oem/runtime/events/pause", await request.json(), timeout=45.0)


@router.post("/oem/runtime/events/resume")
async def oem_runtime_event_resume(request: Request):
    return await proxy_request("POST", "/oem/runtime/events/resume", await request.json(), timeout=45.0)


@router.post("/oem/runtime/readiness/prepare-to-run-job/dry-run")
async def oem_runtime_prepare_to_run_job_readiness_dry_run(request: Request):
    return await proxy_request("POST", "/oem/runtime/readiness/prepare-to-run-job/dry-run", await request.json(), timeout=90.0)


@router.post("/oem/runtime/commands/{command_name}")
async def oem_runtime_command(command_name: str, request: Request):
    allowed = {"initializeSystem", "PrepareToRunJob", "validateJob", "enqueue", "abortjob", "unlockProcess", "wakefrompause"}
    if command_name not in allowed:
        raise HTTPException(status_code=404, detail=f"Unsupported OEM runtime command: {command_name}")
    return await proxy_request("POST", f"/oem/runtime/commands/{command_name}", await request.json(), timeout=90.0)


@router.get("/oem/runtime/commands/history")
async def oem_runtime_command_history(limit: int = 20):
    return await proxy_request("GET", "/oem/runtime/commands/history", params={"limit": limit}, timeout=30.0)


@router.get("/oem/motion_worker/status")
async def oem_motion_worker_status():
    return await proxy_request("GET", "/oem/motion_worker/status", timeout=30.0)


@router.post("/oem/motion_worker/run_next")
async def oem_motion_worker_run_next(request: Request):
    return await proxy_request("POST", "/oem/motion_worker/run_next", await request.json(), timeout=90.0)


@router.post("/oem/motion_worker/abort")
async def oem_motion_worker_abort(request: Request):
    return await proxy_request("POST", "/oem/motion_worker/abort", await request.json(), timeout=45.0)


@router.post("/oem/switch_audit")
async def oem_switch_audit(request: Request):
    return await proxy_request("POST", "/oem/switch_audit", await request.json(), timeout=45.0)


@router.post("/motion/oem/startup_step")
async def motion_oem_startup_step(request: Request):
    return await proxy_request("POST", "/motion/oem/startup_step", await request.json(), timeout=90.0)


@router.post("/motion/oem/home_xy")
async def motion_oem_home_xy(request: Request):
    return await proxy_request("POST", "/motion/oem/home_xy", await request.json(), timeout=120.0)


@router.post("/motion/oem/rehome")
async def motion_oem_rehome(request: Request):
    return await proxy_request("POST", "/motion/oem/rehome", await request.json(), timeout=180.0)


@router.post("/motion/oem/initialize_motion")
async def motion_oem_initialize_motion(request: Request):
    return await proxy_request("POST", "/motion/oem/initialize_motion", await request.json(), timeout=180.0)


@router.get("/motion/range/status")
async def motion_range_status():
    return await proxy_request("GET", "/motion/range/status", timeout=30.0)


@router.get("/motion/reference/status")
async def motion_reference_status(axes: str = "x,y,z,g,door"):
    return await proxy_request("GET", "/motion/reference/status", params={"axes": axes}, timeout=20.0)


@router.post("/motion/reference/mark_referenced")
async def motion_reference_mark_referenced():
    raise HTTPException(
        status_code=410,
        detail="BMS reference marking is disabled. Controller reference state is robot-local and must be changed only by OEM/reference routes on the robot.",
    )


@router.post("/motion/reference/mark_desynced")
async def motion_reference_mark_desynced():
    raise HTTPException(
        status_code=410,
        detail="BMS reference marking is disabled. Controller reference state is robot-local and must be changed only by OEM/reference routes on the robot.",
    )


@router.get("/liquid/status")
async def liquid_status():
    return await proxy_request("GET", "/liquid/status", timeout=20.0)


@router.post("/liquid/init")
async def liquid_init(request: Request):
    return await proxy_request("POST", "/liquid/init", await request.json(), timeout=45.0)


@router.post("/liquid/tip")
async def liquid_tip(request: Request):
    return await proxy_request("POST", "/liquid/tip", await request.json(), timeout=45.0)


@router.post("/liquid/aspirate")
async def liquid_aspirate(request: Request):
    return await proxy_request("POST", "/liquid/aspirate", await request.json(), timeout=45.0)


@router.post("/liquid/dispense")
async def liquid_dispense(request: Request):
    return await proxy_request("POST", "/liquid/dispense", await request.json(), timeout=45.0)


@router.post("/liquid/mix")
async def liquid_mix(request: Request):
    return await proxy_request("POST", "/liquid/mix", await request.json(), timeout=120.0)

@router.get("/motion/axis/{axis}/status")
async def get_axis_status(axis: str):
    return await proxy_request("GET", f"/motion/axis/{axis}/status")

@router.get("/motion/axes/status")
async def get_axes_status(axes: str = "x,y,z"):
    return await proxy_request("GET", "/motion/axes/status", params={"axes": axes})

@router.post("/motion/interlock/prepare")
async def prepare_interlock():
    return await proxy_request("POST", "/motion/interlock/prepare")

@router.get("/motion/interlock/override")
async def motion_interlock_override_status():
    return await proxy_request("GET", "/motion/interlock/override", timeout=35.0)

@router.post("/motion/interlock/override")
async def motion_interlock_override(request: Request):
    payload = await _request_json_or_empty(request)
    if str(payload.get("operator_ack") or "") != "INTERLOCK_OVERRIDE":
        raise HTTPException(
            status_code=409,
            detail="Interlock override requires operator_ack exactly INTERLOCK_OVERRIDE. This bypasses latch/24V sense only for commissioning.",
        )
    if payload.get("enabled") is True and not str(payload.get("reason") or "").strip():
        raise HTTPException(status_code=400, detail="Enabling interlock override requires a non-empty reason.")
    return await proxy_request("POST", "/motion/interlock/override", payload, timeout=35.0)

@router.get("/motion/power/status")
async def motion_power_status():
    return await proxy_request("GET", "/motion/power/status", timeout=30.0)

@router.post("/motion/power/enable")
async def motion_power_enable():
    return await proxy_request("POST", "/motion/power/enable", timeout=40.0)

@router.post("/motion/power/diag")
async def motion_power_diag():
    return await proxy_request("POST", "/motion/power/diag", timeout=55.0)

@router.post("/motion/axes/current")
async def motion_axes_current(request: Request):
    payload = _validated_axes_current_payload(await _request_json_or_empty(request))
    return await proxy_request("POST", "/motion/axes/current", payload, timeout=35.0)

@router.post("/motion/arm/strict_startup")
async def motion_arm_strict_startup(request: Request):
    return await proxy_request("POST", "/motion/arm/strict_startup", await request.json(), timeout=190.0)

@router.post("/motion/hard_reset")
async def motion_hard_reset(request: Request):
    return await proxy_request("POST", "/motion/hard_reset", await request.json(), timeout=90.0)

@router.post("/motion/clear_lock")
async def clear_lock():
    return await proxy_request("POST", "/motion/clear_lock")

@router.get("/latch/status")
async def latch_status():
    return await proxy_request("GET", "/latch/status")

@router.post("/latch/lock")
async def latch_lock():
    return await proxy_request("POST", "/latch/lock")

@router.post("/latch/unlock")
async def latch_unlock():
    return await proxy_request("POST", "/latch/unlock")

@router.post("/led/off")
async def led_off():
    return await proxy_request("POST", "/led/off")

@router.post("/led/on")
async def led_on():
    return await proxy_request("POST", "/led/on")

@router.post("/led/pct")
async def led_pct(request: Request):
    return await proxy_request("POST", "/led/pct", await request.json())

@router.post("/led/rgb")
async def led_rgb(request: Request):
    return await proxy_request("POST", "/led/rgb", await request.json())

@router.post("/motion/axis/relative")
async def move_axis_relative(request: Request):
    payload = await _request_json_or_empty(request)
    await _validate_relative_motion_payload(payload)
    return await proxy_request("POST", "/motion/axis/relative", payload)

@router.post("/motion/axis/absolute")
async def move_axis_absolute(request: Request):
    payload = await _request_json_or_empty(request)
    await _validate_absolute_motion_payload(payload)
    return await proxy_request("POST", "/motion/axis/absolute", payload)

@router.post("/motion/axis/zero")
async def move_axis_zero(request: Request):
    return await proxy_request("POST", "/motion/axis/zero", await request.json())

@router.post("/motion/axis/home")
async def home_axis(request: Request):
    payload = await _request_json_or_empty(request)
    _validate_home_payload(payload)
    # BMS is only the reachable proxy here; robot-local FastAPI owns the actual
    # homing implementation and must return the motion/audit evidence. Keep this
    # route separate from /motion/axis/zero so Home is never silently rewritten to
    # controller-zero.
    return await proxy_request("POST", "/motion/axis/home", payload, timeout=90.0)

@router.post("/thermal/baseline")
async def thermal_baseline():
    return await proxy_request("POST", "/thermal/baseline")

@router.get("/thermal/snapshot")
async def thermal_snapshot():
    return await proxy_request("GET", "/thermal/snapshot")

@router.post("/thermal/set_temp")
async def set_thermal_temp(request: Request):
    return await proxy_request("POST", "/thermal/set_temp", await request.json())

@router.post("/thermal/fan")
async def set_thermal_fan(request: Request):
    return await proxy_request("POST", "/thermal/fan", await request.json())

@router.post("/thermal/pwm")
async def set_thermal_pwm(request: Request):
    return await proxy_request("POST", "/thermal/pwm", await request.json())

@router.post("/thermal/rates")
async def set_thermal_rates(request: Request):
    return await proxy_request("POST", "/thermal/rates", await request.json())

@router.post("/thermal/fast_profile")
async def thermal_fast_profile():
    return await proxy_request("POST", "/thermal/fast_profile")

@router.post("/thermal/hard_reset")
async def thermal_hard_reset():
    return await proxy_request("POST", "/thermal/hard_reset")

@router.post("/chiller/baseline")
async def chiller_baseline():
    return await proxy_request("POST", "/chiller/baseline")

@router.get("/chiller/snapshot")
async def chiller_snapshot():
    return await proxy_request("GET", "/chiller/snapshot")

@router.post("/chiller/set_temp")
async def set_chiller_temp(request: Request):
    return await proxy_request("POST", "/chiller/set_temp", await request.json())

@router.post("/chiller/fan")
async def set_chiller_fan(request: Request):
    return await proxy_request("POST", "/chiller/fan", await request.json())

@router.post("/chiller/pwm")
async def set_chiller_pwm(request: Request):
    return await proxy_request("POST", "/chiller/pwm", await request.json())

@router.post("/chiller/rates")
async def set_chiller_rates(request: Request):
    return await proxy_request("POST", "/chiller/rates", await request.json())

@router.post("/chiller/hard_reset")
async def chiller_hard_reset():
    return await proxy_request("POST", "/chiller/hard_reset")

@router.get("/camera/devices")
async def camera_devices():
    return await proxy_request("GET", "/camera/devices")

@router.get("/camera/controls")
async def camera_controls(device: str = "/dev/video0"):
    return await proxy_request("GET", "/camera/controls", params={"device": device})

@router.post("/camera/control")
async def camera_control(request: Request):
    return await proxy_request("POST", "/camera/control", await request.json(), timeout=20.0)

@router.post("/camera/snapshot")
async def camera_snapshot(request: Request):
    return await proxy_request("POST", "/camera/snapshot", await request.json())

@router.post("/camera/stream_health")
async def camera_stream_health(request: Request):
    return await proxy_request("POST", "/camera/stream_health", await request.json(), timeout=45.0)

@router.post("/camera/auto_recover")
async def camera_auto_recover(request: Request):
    return await proxy_request("POST", "/camera/auto_recover", await request.json(), timeout=90.0)

@router.post("/camera/reset")
async def camera_reset(request: Request):
    return await proxy_request("POST", "/camera/reset", await request.json(), timeout=20.0)

@router.post("/camera/stop")
async def camera_stop(request: Request):
    return await proxy_request("POST", "/camera/stop", await request.json(), timeout=20.0)


@router.get("/camera/stream_state")
async def camera_stream_state():
    return await proxy_request("GET", "/camera/stream_state", timeout=20.0)


@router.post("/vision/inspect")
async def vision_inspect(request: Request):
    return await proxy_request("POST", "/vision/inspect", await request.json(), timeout=45.0)


@router.post("/vision/barcode/read")
async def vision_barcode_read(request: Request):
    return await proxy_request("POST", "/vision/barcode/read", await request.json(), timeout=45.0)

@router.post("/protocol/compile")
async def protocol_compile(request: Request):
    return await proxy_request("POST", "/protocol/compile", await request.json(), timeout=45.0)

@router.post("/protocol/execute")
async def protocol_execute(request: Request):
    return await proxy_request("POST", "/protocol/execute", await request.json(), timeout=90.0)

@router.get("/protocol/jobs")
async def protocol_jobs(limit: int = 20):
    return await proxy_request("GET", "/protocol/jobs", params={"limit": limit}, timeout=30.0)

@router.get("/protocol/jobs/{job_id}")
async def protocol_job_detail(job_id: str):
    return await proxy_request("GET", f"/protocol/jobs/{job_id}", timeout=30.0)

@router.post("/protocol/jobs/{job_id}/review")
async def protocol_job_review(job_id: str, request: Request):
    return await proxy_request("POST", f"/protocol/jobs/{job_id}/review", await request.json(), timeout=90.0)

@router.get("/camera/mjpeg")
async def camera_mjpeg(
    request: Request,
    device: str = "/dev/video0",
    fps: int = 8,
    quality: int = 7,
    width: int = 640,
    height: int = 480,
):
    return await proxy_stream(
        "/camera/mjpeg",
        request=request,
        params={
            "device": device,
            "fps": fps,
            "quality": quality,
            "width": width,
            "height": height,
        },
    )
