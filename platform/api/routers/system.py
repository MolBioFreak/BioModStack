"""
System administration routes for cache cleanup, runtime control, and install-profile maintenance.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
for root in (REPO_ROOT, API_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from biomodstack_runtime_profile import install_profile_snapshot, save_install_profile  # noqa: E402
from services.workflow_adapter import (  # noqa: E402
    WorkflowAdapterRequestError,
    request_via_workflow_adapter,
    workflow_adapter_enabled,
)
from biomodstack_services import (  # noqa: E402
    CORE_RUNTIME_SERVICE,
    WORKFLOW_ADAPTER_SERVICE,
    ServiceManagerError,
    resolve_runtime_mode,
    restart_all,
    restart_api,
    runtime_descriptor,
    runtime_port_settings,
    save_runtime_port_settings,
    start_api,
    start_all,
    start_runtime_target,
    stop_api,
    stop_all,
)
from paths import get_db_path, get_results_dir, get_work_dir  # noqa: E402
from services import db_service, stats_tools  # noqa: E402

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
    features: dict[str, bool] | None = None


class RuntimePortsPayload(BaseModel):
    dev_web_host_port: int | None = None
    prod_web_host_port: int | None = None


class InstallFeaturesPayload(BaseModel):
    features: dict[str, bool]


DEV_INSTALL_FEATURES: dict[str, bool] = {
    "bioxp": True,
    "stats_tools": True,
    "assay_db": True,
}


class RuntimeStartTargetPayload(BaseModel):
    target: str | None = None


class StatsToolsActionPayload(BaseModel):
    tail: int | None = 120


class DbServiceActionPayload(BaseModel):
    tail: int | None = 120
    advanced: bool = False
    i_know_this_disables_db_backed_features: bool = Field(False, alias="i_know_this_disables_db_backed_features")


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


def _core_runtime_mode_enabled() -> bool:
    normalized = str(os.getenv("BMS_CORE_RUNTIME_MODE") or "").strip().lower()
    return normalized not in {"", "0", "false", "no", "off"}


def _system_control_should_proxy_to_workflow_adapter() -> bool:
    return _core_runtime_mode_enabled() and workflow_adapter_enabled()


def _mark_current_api_ready(payload: dict[str, object]) -> dict[str, object]:
    """Normalize runtime status for responses served by this API process.

    `runtime_descriptor()` is also used by CLI/status paths, where probing
    http://127.0.0.1:8000/api/health is appropriate.  From inside the FastAPI
    route that is currently serving `/api/system/runtime-state`, however, that
    same self-HTTP probe can time out on a single-worker server even though the
    API is obviously reachable.  The route is live if it is returning this
    payload, so mark API health as ready and derive container-runtime service
    readiness from the live HTTP surfaces instead of container-local systemd.
    """
    if not isinstance(payload, dict):
        return payload

    normalized = dict(payload)
    health_raw = normalized.get("health")
    if not isinstance(health_raw, dict):
        return normalized

    health = dict(health_raw)
    if "api_ready" in health:
        health["api_ready"] = True
    normalized["health"] = health

    services_raw = normalized.get("services")
    if normalized.get("runtime_mode") != "container":
        if isinstance(services_raw, list) and services_raw and all(isinstance(service, dict) for service in services_raw):
            services: list[object] = []
            for service in services_raw:
                item = dict(service)
                name = str(item.get("name") or "")
                if name == "biomodstack-frontend.service":
                    systemd_active = bool(item.get("active"))
                    http_ready = bool(health.get("frontend_ready"))
                    item["systemd_active"] = systemd_active
                    item["active"] = http_ready
                    item["active_source"] = "http-health" if http_ready else "http-health-failed"
                services.append(item)
            normalized["services"] = services
            normalized["runtime_ready"] = all(bool(value) for value in health.values())
            normalized["runtime_active"] = all(bool(service.get("active")) for service in services if isinstance(service, dict)) and bool(normalized["runtime_ready"])
        return normalized

    if not isinstance(services_raw, list):
        return normalized

    derived_ready = {
        WORKFLOW_ADAPTER_SERVICE: bool(health.get("adapter_ready")),
        CORE_RUNTIME_SERVICE: bool(health.get("api_ready") and health.get("frontend_ready")),
    }
    services: list[object] = []
    for service in services_raw:
        if not isinstance(service, dict):
            services.append(service)
            continue
        item = dict(service)
        name = str(item.get("name") or "")
        if name in derived_ready:
            systemd_active = bool(item.get("active"))
            http_ready = bool(derived_ready[name])
            item["systemd_active"] = systemd_active
            item["active"] = http_ready
            item["active_source"] = "http-health" if http_ready else "http-health-failed"
        services.append(item)
    normalized["services"] = services
    if services and all(isinstance(service, dict) for service in services):
        normalized["runtime_active"] = all(bool(service.get("active")) for service in services if isinstance(service, dict))
        normalized["runtime_ready"] = normalized["runtime_active"]
    return normalized


def _start_runtime_target_via_workflow_adapter(target: str) -> dict[str, object]:
    response = request_via_workflow_adapter(
        "POST",
        "/api/workflow-adapter/runtime/start-target",
        {"target": target},
    )
    if isinstance(response, dict):
        return response
    return {"target": target, "control_mode": "host-adapter"}


def _runtime_target_for_mode(runtime_mode: str) -> str:
    return "dev" if runtime_mode == "dev" else "prod"


def _runtime_state_via_workflow_adapter(runtime_mode: str) -> dict[str, object]:
    response = request_via_workflow_adapter(
        "GET",
        f"/api/workflow-adapter/runtime/state?runtime={runtime_mode}",
    )
    if isinstance(response, dict):
        return response
    return {"runtime_mode": runtime_mode, "control_mode": "host-adapter"}


def _runtime_action_via_workflow_adapter(action_name: str, runtime_mode: str) -> dict[str, object]:
    if action_name == "start":
        return _start_runtime_target_via_workflow_adapter(_runtime_target_for_mode(runtime_mode))
    response = request_via_workflow_adapter(
        "POST",
        f"/api/workflow-adapter/runtime/{action_name}",
        {"runtime": runtime_mode},
    )
    if isinstance(response, dict):
        return response
    return {"runtime_mode": runtime_mode, "action": action_name, "control_mode": "host-adapter"}


def _run_runtime_action(
    request: Request,
    runtime: str | None,
    action_name: str,
    action: Callable[..., None],
) -> dict[str, object]:
    _require_local_admin(request)
    runtime_mode = _resolve_runtime(runtime)
    try:
        if _system_control_should_proxy_to_workflow_adapter():
            return _mark_current_api_ready(_runtime_action_via_workflow_adapter(action_name, runtime_mode))
        action(runtime_mode=runtime_mode)
        return _mark_current_api_ready(runtime_descriptor(runtime_mode=runtime_mode))
    except WorkflowAdapterRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except (ServiceManagerError, FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/stats-tools")
async def get_stats_tools_status(request: Request, tail: int = 120):
    _require_local_admin(request)
    try:
        return stats_tools.describe_stats_tools(tail=tail)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/stats-tools/{action}")
async def run_stats_tools_lifecycle_action(
    request: Request,
    action: str,
    payload: StatsToolsActionPayload | None = None,
    tail: int | None = None,
):
    _require_local_admin(request)
    requested_tail = int(tail if tail is not None else (payload.tail if payload and payload.tail is not None else 120))
    try:
        return stats_tools.run_stats_tools_action(action, tail=requested_tail)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/db-service")
async def get_db_service_status(request: Request, tail: int = Query(120, ge=1, le=500)):
    _require_local_admin(request)
    try:
        return db_service.describe_db_service(tail=tail)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/db-service/{action}")
async def run_db_service_lifecycle_action(
    request: Request,
    action: str,
    payload: DbServiceActionPayload | None = None,
    tail: int | None = Query(None, ge=1, le=500),
    i_know_this_disables_db_backed_features: bool = Query(False, alias="i-know-this-disables-db-backed-features"),
):
    _require_local_admin(request)
    requested_tail = int(tail if tail is not None else (payload.tail if payload and payload.tail is not None else 120))
    advanced = bool(
        i_know_this_disables_db_backed_features
        or (payload.advanced if payload else False)
        or (payload.i_know_this_disables_db_backed_features if payload else False)
    )
    try:
        return db_service.run_db_service_action(action, tail=requested_tail, advanced=advanced)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/runtime-state")
async def get_runtime_state(request: Request, runtime: str | None = None):
    _require_local_admin(request)
    runtime_mode = _resolve_runtime(runtime)
    try:
        if _system_control_should_proxy_to_workflow_adapter():
            try:
                return _mark_current_api_ready(_runtime_state_via_workflow_adapter(runtime_mode))
            except WorkflowAdapterRequestError as exc:
                if exc.status_code != 404:
                    raise
            except RuntimeError:
                pass
        return _mark_current_api_ready(runtime_descriptor(runtime_mode=runtime_mode))
    except WorkflowAdapterRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except (ServiceManagerError, FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/runtime/start")
async def start_runtime(request: Request, runtime: str | None = None):
    return _run_runtime_action(request, runtime, "start", start_all)


@router.post("/runtime/start-api")
async def start_runtime_api(request: Request, runtime: str | None = None):
    return _run_runtime_action(request, runtime, "start-api", start_api)


@router.post("/runtime/start-target")
async def start_runtime_target_route(
    request: Request,
    payload: RuntimeStartTargetPayload | None = None,
    target: str | None = None,
):
    _require_local_admin(request)
    normalized_target = str(target or (payload.target if payload else None) or "prod").strip().lower()
    try:
        if _system_control_should_proxy_to_workflow_adapter():
            return _start_runtime_target_via_workflow_adapter(normalized_target)
        start_runtime_target(target=normalized_target)
    except WorkflowAdapterRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except (ServiceManagerError, FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"target": normalized_target}


@router.post("/runtime/stop")
async def stop_runtime(request: Request, runtime: str | None = None):
    return _run_runtime_action(request, runtime, "stop", stop_all)


@router.post("/runtime/stop-api")
async def stop_runtime_api(request: Request, runtime: str | None = None):
    return _run_runtime_action(request, runtime, "stop-api", stop_api)


@router.post("/runtime/restart")
async def restart_runtime(request: Request, runtime: str | None = None):
    return _run_runtime_action(request, runtime, "restart", restart_all)


@router.post("/runtime/restart-api")
async def restart_runtime_api(request: Request, runtime: str | None = None):
    return _run_runtime_action(request, runtime, "restart-api", restart_api)


@router.get("/install-profile")
async def get_install_profile(request: Request):
    _require_local_admin(request)
    return _install_profile_response()


def _effective_runtime_features(request: Request, configured: Mapping[str, object]) -> dict[str, object]:
    """Return only feature surfaces mounted in this running API process."""
    effective = dict(configured)
    if "bioxp" in effective:
        effective["bioxp"] = any(
            str(getattr(route, "path", "")).startswith("/api/bioxp")
            for route in request.app.routes
        )
    return effective


@router.get("/features")
async def get_install_features(request: Request):
    _require_local_admin(request)
    snapshot = _install_profile_response()
    resolved = snapshot.get("resolved") if isinstance(snapshot, Mapping) else {}
    features = resolved.get("features") if isinstance(resolved, Mapping) else None
    configured = features if isinstance(features, Mapping) else {}
    return {
        "features": _effective_runtime_features(request, configured),
        "configured_features": configured,
        "dev_features": DEV_INSTALL_FEATURES,
    }


@router.put("/features")
async def put_install_features(request: Request, payload: InstallFeaturesPayload):
    _require_local_admin(request)
    snapshot = _install_profile_response()
    profile = dict(snapshot.get("profile") or {}) if isinstance(snapshot, Mapping) else {}
    current_features = dict(profile.get("features") or {}) if isinstance(profile.get("features"), Mapping) else {}
    current_features.update(payload.features)
    profile["features"] = current_features
    try:
        saved_profile = save_install_profile(profile)
    except (TypeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    updated = _install_profile_response(saved_profile)
    resolved = updated.get("resolved") if isinstance(updated, Mapping) else {}
    features = resolved.get("features") if isinstance(resolved, Mapping) else None
    configured = features if isinstance(features, Mapping) else {}
    return {
        "features": _effective_runtime_features(request, configured),
        "configured_features": configured,
        "dev_features": DEV_INSTALL_FEATURES,
    }


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
