import asyncio
import os
from pathlib import Path
from typing import Optional, Dict, Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.responses import StreamingResponse

router = APIRouter()

# Remote daemon host. SSH key auth must be pre-configured.
ROBOT_SSH_USER = os.getenv("BIOXP_SSH_USER", "molbiofreak")
ROBOT_SSH_HOST = os.getenv("BIOXP_SSH_HOST", "robot")
ROBOT_DAEMON_PORT = int(os.getenv("BIOXP_DAEMON_PORT", "8123"))
ROBOT_REPO_DIR = os.getenv("BIOXP_REPO_DIR", "~/bioxp_re")
LINKAGE_STATE_PATH = Path(os.getenv("BIOXP_LINKAGE_STATE_PATH", str(Path.home() / ".biomodstack" / "bioxp_linkage_url")))

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


def _recommended_linkage_url() -> str:
    return f"http://{ROBOT_SSH_HOST}:{ROBOT_DAEMON_PORT}"


def _read_persisted_linkage() -> Optional[str]:
    try:
        if LINKAGE_STATE_PATH.exists():
            value = LINKAGE_STATE_PATH.read_text(encoding="utf-8").strip()
            return _normalize_url(value)
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
    or _normalize_url(os.getenv("BIOXP_SERVER_URL"))
)


def get_current_url() -> str:
    if not _GLOBAL_LINKAGE_URL:
         raise HTTPException(
             status_code=400,
             detail="Hardware Node URL is not configured. Please set the linkage in the BioXP Cockpit."
         )
    return _GLOBAL_LINKAGE_URL


async def _daemon_probe() -> dict:
    probe_cmd = (
        f"if curl -fsS --max-time 3 http://127.0.0.1:{ROBOT_DAEMON_PORT}/status >/dev/null; then "
        f"echo '__HEALTHY__'; else echo '__UNHEALTHY__'; fi; "
        f"pgrep -af '[u]vicorn.*bioxp.api' || echo '__NO_PIDS__'"
    )
    result = await _ssh_exec(probe_cmd, timeout_s=8.0)
    healthy = "__HEALTHY__" in result["stdout"]
    lines = []
    for line in result["stdout"].splitlines():
        stripped = line.strip()
        if not stripped or stripped in {"__HEALTHY__", "__UNHEALTHY__", "__NO_PIDS__"}:
            continue
        lines.append(stripped)
    return {
        "healthy": healthy,
        "detail": "\n".join(lines) if lines else None,
    }


async def _ssh_exec(cmd: str, timeout_s: float = 10.0) -> dict:
    """Run a command on the robot via SSH. Returns stdout, stderr, and return code."""
    ssh_cmd = [
        "ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no",
        f"{ROBOT_SSH_USER}@{ROBOT_SSH_HOST}", cmd
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *ssh_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        return {
            "stdout": stdout.decode().strip(),
            "stderr": stderr.decode().strip(),
            "returncode": proc.returncode,
        }
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="SSH command timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SSH execution failed: {str(e)}")


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
    url = _normalize_url(req.url)
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


# ── Remote Daemon Control ──────────────────────────────────────────────

@router.get("/daemon/status")
async def daemon_status():
    """Check if the uvicorn process is actually healthy on the remote host."""
    try:
        probe = await _daemon_probe()
        return {
            "running": probe["healthy"],
            "healthy": probe["healthy"],
            "stale_process": bool(probe["detail"]) and not probe["healthy"],
            "host": ROBOT_SSH_HOST,
            "port": ROBOT_DAEMON_PORT,
            "detail": probe["detail"],
            "inferred_via_proxy": False,
            "probe_error": None,
        }
    except HTTPException as exc:
        try:
            payload = await proxy_request("GET", "/status", timeout=10.0)
            if isinstance(payload, dict) and payload.get("hardware_connected"):
                detail = "SSH daemon probe unavailable; inferred running from live BioXP proxy status."
                if exc.detail:
                    detail = f"{detail} Probe error: {exc.detail}"
                return {
                    "running": True,
                    "healthy": True,
                    "stale_process": False,
                    "host": ROBOT_SSH_HOST,
                    "port": ROBOT_DAEMON_PORT,
                    "detail": detail,
                    "inferred_via_proxy": True,
                    "probe_error": {
                        "status_code": exc.status_code,
                        "detail": exc.detail,
                    },
                }
        except HTTPException:
            pass
        raise

@router.post("/daemon/start")
async def daemon_start():
    """Start the BioXP API daemon on the remote host via SSH."""
    probe = await _daemon_probe()
    if probe["healthy"]:
        return {"status": "already_running", "detail": probe["detail"]}

    if probe["detail"]:
        await _ssh_exec("pkill -f '[u]vicorn.*bioxp.api' || true", timeout_s=10.0)
        await asyncio.sleep(1.0)

    # Start daemon in a detached screen/nohup so it survives SSH disconnect
    start_cmd = (
        f"cd {ROBOT_REPO_DIR} && "
        f"source .venv/bin/activate && "
        f"nohup env PYTHONPATH=src uvicorn bioxp.api:app --host 0.0.0.0 --port {ROBOT_DAEMON_PORT} "
        f"> /tmp/bioxp-api.log 2>&1 & "
        f"echo $!"
    )
    result = await _ssh_exec(start_cmd, timeout_s=15.0)
    pid = result["stdout"].strip().split("\n")[-1]

    # Brief wait then verify
    await asyncio.sleep(2.0)
    verify = await _daemon_probe()

    return {
        "status": "started" if verify["healthy"] else "failed",
        "pid": pid,
        "detail": verify["detail"],
    }

@router.post("/daemon/stop")
async def daemon_stop():
    """Stop the BioXP API daemon on the remote host."""
    result = await _ssh_exec("pkill -f '[u]vicorn.*bioxp.api' && echo 'STOPPED' || echo 'NOT_RUNNING'")
    return {
        "status": "stopped" if "STOPPED" in result["stdout"] else "not_running",
        "detail": result["stdout"],
    }


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
            detail=f"Cannot connect to BioXP hardware node at {base_url}. Is the bioxp-api.service running?"
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
            detail=f"Cannot connect to BioXP hardware node at {base_url}. Is the bioxp-api.service running?"
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
                "detail": "Hardware Node URL is not configured. Enter the daemon URL and press Connect.",
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
