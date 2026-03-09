import os
import asyncio
import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, Dict, Any

router = APIRouter()

# In-memory Linkage State. No default connection is assumed to prevent "sims/fake fallbacks".
# If the user has not set it via the UI, we fall back to the environment variable if present.
# Otherwise, we explicitly fail.
_GLOBAL_LINKAGE_URL: Optional[str] = os.getenv("BIOXP_SERVER_URL", None)

# Remote daemon host. SSH key auth must be pre-configured.
ROBOT_SSH_USER = os.getenv("BIOXP_SSH_USER", "molbiofreak")
ROBOT_SSH_HOST = os.getenv("BIOXP_SSH_HOST", "robot")
ROBOT_DAEMON_PORT = int(os.getenv("BIOXP_DAEMON_PORT", "8123"))
ROBOT_REPO_DIR = os.getenv("BIOXP_REPO_DIR", "~/bioxp_re")

class LinkageRequest(BaseModel):
    url: str

def get_current_url() -> str:
    if not _GLOBAL_LINKAGE_URL:
         raise HTTPException(
             status_code=400,
             detail="Hardware Node URL is not configured. Please set the linkage in the BioXP Cockpit."
         )
    return _GLOBAL_LINKAGE_URL


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
    return {"url": _GLOBAL_LINKAGE_URL}

@router.post("/linkage")
async def set_linkage(req: LinkageRequest):
    global _GLOBAL_LINKAGE_URL
    url = req.url.strip()
    if url and not url.startswith("http"):
        url = f"http://{url}"
    _GLOBAL_LINKAGE_URL = url
    return {"url": _GLOBAL_LINKAGE_URL, "status": "updated"}

@router.post("/linkage/disconnect")
async def disconnect_linkage():
    global _GLOBAL_LINKAGE_URL
    _GLOBAL_LINKAGE_URL = None
    return {"url": None, "status": "disconnected"}


# ── Remote Daemon Control ──────────────────────────────────────────────

@router.get("/daemon/status")
async def daemon_status():
    """Check if the uvicorn process is running on the remote host."""
    result = await _ssh_exec("pgrep -af '[u]vicorn.*bioxp.api' || echo '__NOT_RUNNING__'")
    is_running = "__NOT_RUNNING__" not in result["stdout"]
    return {
        "running": is_running,
        "host": ROBOT_SSH_HOST,
        "port": ROBOT_DAEMON_PORT,
        "detail": result["stdout"] if is_running else None,
    }

@router.post("/daemon/start")
async def daemon_start():
    """Start the BioXP API daemon on the remote host via SSH."""
    # First check if already running
    check = await _ssh_exec("pgrep -af '[u]vicorn.*bioxp.api' || echo '__NOT_RUNNING__'")
    if "__NOT_RUNNING__" not in check["stdout"]:
        return {"status": "already_running", "detail": check["stdout"]}

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
    await asyncio.sleep(1.5)
    verify = await _ssh_exec("pgrep -af '[u]vicorn.*bioxp.api' || echo '__NOT_RUNNING__'")
    is_running = "__NOT_RUNNING__" not in verify["stdout"]

    return {
        "status": "started" if is_running else "failed",
        "pid": pid,
        "detail": verify["stdout"],
    }

@router.post("/daemon/stop")
async def daemon_stop():
    """Stop the BioXP API daemon on the remote host."""
    result = await _ssh_exec("pkill -f '[u]vicorn.*bioxp.api' && echo 'STOPPED' || echo 'NOT_RUNNING'")
    return {
        "status": "stopped" if "STOPPED" in result["stdout"] else "not_running",
        "detail": result["stdout"],
    }


async def proxy_request(method: str, path: str, json_data: Optional[Dict[str, Any]] = None):
    """Generic HTTPX proxy function to forward raw requests to the BioXP Node."""
    base_url = get_current_url()
    url = f"{base_url}{path}"
    timeout = 65.0 # Max hardware timeout for moving axes absolute (60s)
    
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method == "GET":
                response = await client.get(url)
            elif method == "POST":
                response = await client.post(url, json=json_data)
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

@router.get("/status")
async def get_status():
    return await proxy_request("GET", "/status")

@router.get("/motion/axis/{axis}/status")
async def get_axis_status(axis: str):
    return await proxy_request("GET", f"/motion/axis/{axis}/status")

@router.post("/motion/interlock/prepare")
async def prepare_interlock():
    return await proxy_request("POST", "/motion/interlock/prepare")

@router.post("/motion/clear_lock")
async def clear_lock():
    return await proxy_request("POST", "/motion/clear_lock")

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

@router.post("/thermal/set_temp")
async def set_thermal_temp(request: Request):
    return await proxy_request("POST", "/thermal/set_temp", await request.json())

@router.post("/chiller/baseline")
async def chiller_baseline():
    return await proxy_request("POST", "/chiller/baseline")

@router.post("/chiller/set_temp")
async def set_chiller_temp(request: Request):
    return await proxy_request("POST", "/chiller/set_temp", await request.json())

@router.post("/camera/snapshot")
async def camera_snapshot(request: Request):
    return await proxy_request("POST", "/camera/snapshot", await request.json())
