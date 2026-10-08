"""
System administration routes for cache cleanup, runtime control, and install-profile maintenance.
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
for root in (REPO_ROOT, API_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from biomodstack_runtime_profile import install_profile_snapshot, save_install_profile
from biomodstack_services import (
    ServiceManagerError,
    resolve_runtime_mode,
    restart_all,
    restart_api,
    runtime_descriptor,
    runtime_port_settings,
    save_runtime_port_settings,
    start_all,
    start_runtime_target,
    stop_all,
)
from paths import get_db_path, get_results_dir, get_work_dir

router = APIRouter(prefix="/system", tags=["system"])

LOCAL_ADMIN_HOSTS = {None, "127.0.0.1", "::1", "localhost", "testclient"}


class CleanupResult(BaseModel):
    success: bool
    message: str
    files_before: int
    files_after: int
    space_freed: str


class DiskUsage(BaseModel):
    work_dir_size: str
    work_dir_files: int
    results_size: str
    results_files: int


class DbInfo(BaseModel):
    path: str
    exists: bool
    size_bytes: int
    journal_mode: str | None
    busy_timeout_ms: int | None


class InstallProfilePayload(BaseModel):
    data_root: str | None = None
    inputs_dir: str | None = None
    db_path: str | None = None
    container_dir: str | None = None
    weights_root: str | None = None
    colabfold_db: str | None = None
    msa_cache_dir: str | None = None
    sabdab_cache_dir: str | None = None
    container_state_path: str | None = None
    inputs_container_path: str | None = None
    db_container_path: str | None = None
    api_host_port: int | None = None
    dev_web_host_port: int | None = None
    web_host_port: int | None = None
    cors_origins: list[str] | None = None
    workflow_adapter_url: str | None = None
    compose_project_name: str | None = None
    core_runtime_mode: bool | None = None


class RuntimePortsPayload(BaseModel):
    dev_web_host_port: int | None = None
    prod_web_host_port: int | None = None


def _require_local_admin(request: Request) -> None:
    if request.client and request.client.host not in LOCAL_ADMIN_HOSTS:
        raise HTTPException(status_code=403, detail="BioModStack system-admin routes are limited to local requests")


def _resolve_runtime(runtime: str | None) -> str:
    try:
        return resolve_runtime_mode(runtime)
    except ServiceManagerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _install_profile_response(profile: Mapping[str, object] | None = None) -> dict[str, object]:
    return install_profile_snapshot(profile=profile)


def _run_runtime_action(
    request: Request,
    runtime: str | None,
    action: Callable[..., None],
) -> dict[str, object]:
    _require_local_admin(request)
    runtime_mode = _resolve_runtime(runtime)
    try:
        action(runtime_mode=runtime_mode)
        return runtime_descriptor(runtime_mode=runtime_mode)
    except (ServiceManagerError, FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/runtime-state")
async def get_runtime_state(request: Request, runtime: str | None = None):
    _require_local_admin(request)
    runtime_mode = _resolve_runtime(runtime)
    try:
        return runtime_descriptor(runtime_mode=runtime_mode)
    except (ServiceManagerError, FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/runtime/start")
async def start_runtime(request: Request, runtime: str | None = None):
    return _run_runtime_action(request, runtime, start_all)


@router.post("/runtime/start-target")
async def start_runtime_target_route(request: Request, target: str | None = None):
    _require_local_admin(request)
    normalized_target = str(target or "prod").strip().lower()
    try:
        start_runtime_target(target=normalized_target)
    except (ServiceManagerError, FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"target": normalized_target}


@router.post("/runtime/stop")
async def stop_runtime(request: Request, runtime: str | None = None):
    return _run_runtime_action(request, runtime, stop_all)


@router.post("/runtime/restart")
async def restart_runtime(request: Request, runtime: str | None = None):
    return _run_runtime_action(request, runtime, restart_all)


@router.post("/runtime/restart-api")
async def restart_runtime_api(request: Request, runtime: str | None = None):
    return _run_runtime_action(request, runtime, restart_api)


@router.get("/install-profile")
async def get_install_profile(request: Request):
    _require_local_admin(request)
    return _install_profile_response()


@router.get("/runtime-ports")
async def get_runtime_ports(request: Request):
    _require_local_admin(request)
    try:
        return runtime_port_settings()
    except ServiceManagerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/runtime-ports")
async def put_runtime_ports(request: Request, payload: RuntimePortsPayload):
    _require_local_admin(request)
    try:
        return save_runtime_port_settings(
            dev_web_host_port=payload.dev_web_host_port,
            prod_web_host_port=payload.prod_web_host_port,
        )
    except ServiceManagerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/install-profile")
async def put_install_profile(request: Request, payload: InstallProfilePayload):
    _require_local_admin(request)
    try:
        saved_profile = save_install_profile(payload.model_dump(exclude_none=True))
    except (TypeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _install_profile_response(saved_profile)


@router.get("/disk-usage", response_model=DiskUsage)
async def get_disk_usage():
    """Get disk usage for pipeline directories"""
    work_dir = get_work_dir()
    results_dir = get_results_dir()

    def get_dir_stats(path: Path) -> tuple[str, int]:
        if not path.exists():
            return "0B", 0
        try:
            file_count = sum(1 for _ in path.rglob("*") if _.is_file())
            result = subprocess.run(
                ["du", "-sh", str(path)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            size = result.stdout.split()[0] if result.returncode == 0 else "?"
            return size, file_count
        except Exception:
            return "?", 0

    work_size, work_files = get_dir_stats(work_dir)
    results_size, results_files = get_dir_stats(results_dir)

    return DiskUsage(
        work_dir_size=work_size,
        work_dir_files=work_files,
        results_size=results_size,
        results_files=results_files,
    )


@router.post("/cleanup-work", response_model=CleanupResult)
async def cleanup_work_directory(days: int = 30):
    """
    Clean up Nextflow work directory.

    Args:
        days: Delete files older than this many days. Use 0 for full purge.
    """
    work_dir = get_work_dir()

    if not work_dir.exists():
        return CleanupResult(
            success=True,
            message="Work directory does not exist",
            files_before=0,
            files_after=0,
            space_freed="0B",
        )

    files_before = sum(1 for _ in work_dir.rglob("*") if _.is_file())

    try:
        result = subprocess.run(
            ["du", "-sh", str(work_dir)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        size_before = result.stdout.split()[0] if result.returncode == 0 else "?"
    except Exception:
        size_before = "?"

    try:
        if days == 0:
            for item in work_dir.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            message = "Full purge completed"
        else:
            subprocess.run(
                ["find", str(work_dir), "-type", "f", "-mtime", f"+{days}", "-delete"],
                timeout=300,
            )
            subprocess.run(
                ["find", str(work_dir), "-type", "d", "-empty", "-delete"],
                timeout=60,
            )
            message = f"Deleted files older than {days} days"

        files_after = sum(1 for _ in work_dir.rglob("*") if _.is_file())

        try:
            result = subprocess.run(
                ["du", "-sh", str(work_dir)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            size_after = result.stdout.split()[0] if result.returncode == 0 else "?"
        except Exception:
            size_after = "?"

        return CleanupResult(
            success=True,
            message=message,
            files_before=files_before,
            files_after=files_after,
            space_freed=f"{size_before} → {size_after}",
        )

    except Exception as exc:
        return CleanupResult(
            success=False,
            message=f"Cleanup failed: {exc}",
            files_before=files_before,
            files_after=files_before,
            space_freed="0B",
        )


@router.get("/db-info", response_model=DbInfo)
async def get_db_info():
    db_path = get_db_path()
    if not db_path.exists():
        return DbInfo(
            path=str(db_path),
            exists=False,
            size_bytes=0,
            journal_mode=None,
            busy_timeout_ms=None,
        )

    journal_mode = None
    busy_timeout = None
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        conn.close()
    except Exception:
        journal_mode = None
        busy_timeout = None

    return DbInfo(
        path=str(db_path),
        exists=True,
        size_bytes=db_path.stat().st_size,
        journal_mode=journal_mode,
        busy_timeout_ms=busy_timeout,
    )
