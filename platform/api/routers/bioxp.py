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


# In-memory Linkage State. Falls back to persisted file, then env var, otherwise remains unset.
_GLOBAL_LINKAGE_URL: Optional[str] = (
    _read_persisted_linkage()
    or _configured_default_linkage_url()
)


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
    "/motion/reference/status": True,
    "/motion/reference/mark_referenced": True,
    "/motion/reference/mark_desynced": True,
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

BMS_PROXIED_ROUTES: Dict[str, bool] = dict(ROBOT_LOCAL_EXPECTED_ROUTES)


def _runtime_status_payload(
    *,
    linkage_configured: bool,
    linked_runtime_reachable: bool,
    hardware_connected: bool,
    detail: str,
    proxy_error: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    runtime_url = _GLOBAL_LINKAGE_URL if linkage_configured else None
    return {
        "running": linked_runtime_reachable,
        "healthy": linked_runtime_reachable,
        "stale_process": False,
        "host": ROBOT_SSH_HOST,
        "port": ROBOT_DAEMON_PORT,
        "runtime_url": runtime_url,
        "linkage_configured": linkage_configured,
        "linked_runtime_reachable": linked_runtime_reachable,
        "hardware_connected": hardware_connected,
        "admin_control_available": False,
        "maintenance_mode": "robot-local",
        "recommended_url": _recommended_linkage_url(),
        "detail": detail,
        "proxy_error": proxy_error,
        "inferred_via_proxy": False,
    }


# ── Linkage Endpoints ──────────────────────────────────────────────────

@router.get("/linkage")
async def get_linkage():
    return {
        "url": _GLOBAL_LINKAGE_URL,
        "configured": bool(_GLOBAL_LINKAGE_URL),
        "recommended_url": _recommended_linkage_url(),
    }

@router.post("/linkage")
async def set_linkage(req: LinkageRequest):
    global _GLOBAL_LINKAGE_URL
    url = _canonicalize_linkage_url(req.url)
    _GLOBAL_LINKAGE_URL = url
    _persist_linkage(_GLOBAL_LINKAGE_URL)
    return {
        "url": _GLOBAL_LINKAGE_URL,
        "configured": bool(_GLOBAL_LINKAGE_URL),
        "recommended_url": _recommended_linkage_url(),
        "status": "updated",
    }

@router.post("/linkage/disconnect")
async def disconnect_linkage():
    global _GLOBAL_LINKAGE_URL
    _GLOBAL_LINKAGE_URL = None
    _persist_linkage(None)
    return {
        "url": None,
        "configured": False,
        "recommended_url": _recommended_linkage_url(),
        "status": "disconnected",
    }


# ── Runtime Status / Deprecated Maintenance Compatibility ───────────────

@router.get("/daemon/status")
@router.get("/runtime/status")
async def daemon_status():
    """Report linked BioXP runtime reachability without SSH/process inspection."""
    if not _GLOBAL_LINKAGE_URL:
        return _runtime_status_payload(
            linkage_configured=False,
            linked_runtime_reachable=False,
            hardware_connected=False,
            detail="No BioXP linkage is configured yet. Connect BMS to the robot-local runtime URL first.",
        )

    try:
        payload = await proxy_request("GET", "/status", timeout=10.0)
        if not isinstance(payload, dict):
            payload = {"status": "error", "raw_payload": payload}
        hardware_connected = bool(payload.get("hardware_connected"))
        detail = payload.get("status_error") or payload.get("startup_error")
        if not detail:
            detail = (
                "Linked BioXP runtime responded to /status and reported hardware connectivity."
                if hardware_connected
                else "Linked BioXP runtime responded to /status, but hardware is not yet connected."
            )
        return _runtime_status_payload(
            linkage_configured=True,
            linked_runtime_reachable=True,
            hardware_connected=hardware_connected,
            detail=str(detail),
        )
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
        payload = await proxy_request("GET", "/status")
        if not isinstance(payload, dict):
            payload = {"status": "error", "raw_payload": payload}
        payload["linkage_configured"] = True
        payload["linkage_url"] = _GLOBAL_LINKAGE_URL
        payload["recommended_url"] = _recommended_linkage_url()
        return payload
    except HTTPException as exc:
        return {
            "status": "offline" if exc.status_code in (503, 504) else "error",
            "transport": "proxy",
            "hardware_connected": False,
            "linkage_configured": True,
            "linkage_url": _GLOBAL_LINKAGE_URL,
            "recommended_url": _recommended_linkage_url(),
            "startup_error": None,
            "proxy_error": {
                "status_code": exc.status_code,
                "detail": exc.detail,
            },
        }

@router.post("/reconnect")
async def reconnect_runtime():
    return await proxy_request("POST", "/reconnect")


@router.get("/capabilities")
async def bioxp_capabilities():
    return {
        "linkage_url": _GLOBAL_LINKAGE_URL,
        "linkage_configured": bool(_GLOBAL_LINKAGE_URL),
        "recommended_url": _recommended_linkage_url(),
        "robot_local_expected_routes": dict(sorted(ROBOT_LOCAL_EXPECTED_ROUTES.items())),
        "bms_proxy_routes": dict(sorted(BMS_PROXIED_ROUTES.items())),
        "notes": [
            "BMS links to the robot-local BioXP runtime and exposes only the routes listed as proxied.",
            "Route parity is a control-plane capability statement; hardware readiness still comes from runtime status/preflight responses.",
        ],
    }


@router.get("/motion/reference/status")
async def motion_reference_status(axes: str = "x,y,z,g,door"):
    return await proxy_request("GET", "/motion/reference/status", params={"axes": axes}, timeout=20.0)


@router.post("/motion/reference/mark_referenced")
async def motion_reference_mark_referenced(request: Request):
    return await proxy_request("POST", "/motion/reference/mark_referenced", await request.json(), timeout=30.0)


@router.post("/motion/reference/mark_desynced")
async def motion_reference_mark_desynced(request: Request):
    return await proxy_request("POST", "/motion/reference/mark_desynced", await request.json(), timeout=30.0)


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
    return await proxy_request("POST", "/motion/axes/current", await request.json(), timeout=35.0)

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
    return await proxy_request("POST", "/motion/axis/relative", await request.json())

@router.post("/motion/axis/absolute")
async def move_axis_absolute(request: Request):
    return await proxy_request("POST", "/motion/axis/absolute", await request.json())

@router.post("/motion/axis/home")
async def home_axis(request: Request):
    return await proxy_request("POST", "/motion/axis/home", await request.json())

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
