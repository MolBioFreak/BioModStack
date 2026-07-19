from __future__ import annotations

import asyncio
import os
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from sqlalchemy import text

from database import engine
from runtime_policy import core_runtime_mode_enabled, workflow_launches_allowed
from services.assay_analytical_store import create_analytical_engine
from services.workflow_adapter import workflow_adapter_base_url


_TRUE = {"1", "true", "yes", "on"}


async def core_database_readiness() -> tuple[bool, str]:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True, "ready"
    except Exception as exc:  # noqa: BLE001 - readiness must report degradation, not crash.
        return False, _failure_status(exc)


async def analytical_database_readiness() -> tuple[bool, str]:
    analytical_engine = create_analytical_engine()
    try:
        async with analytical_engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True, "ready"
    except Exception as exc:  # noqa: BLE001 - readiness must report degradation, not crash.
        return False, _failure_status(exc)
    finally:
        await analytical_engine.dispose()


async def http_readiness(url: str) -> tuple[bool, str]:
    def _probe() -> tuple[bool, str]:
        try:
            with urllib_request.urlopen(url, timeout=0.75) as response:  # noqa: S310 - operator-configured local readiness URL.
                code = int(getattr(response, "status", 200))
            return (200 <= code < 400), f"http_{code}"
        except (OSError, urllib_error.URLError, ValueError) as exc:
            return False, _failure_status(exc)

    return await asyncio.to_thread(_probe)


def _failure_status(exc: BaseException) -> str:
    return f"unavailable:{exc.__class__.__name__}"


def _feature_enabled(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE


def _check(*, required: bool, ready: bool, status: str, **extra: Any) -> dict[str, Any]:
    return {"required": required, "ready": ready, "status": status, **extra}


async def collect_runtime_readiness(*, molbio: dict[str, Any]) -> dict[str, Any]:
    container_mode = core_runtime_mode_enabled()
    mode = "container" if container_mode else "native"

    core_ready, core_status = await core_database_readiness()
    molbio_ready = molbio.get("status") == "healthy" or molbio.get("ready") is True

    analytical_required = _feature_enabled("BMS_FEATURE_ASSAY_DB")
    if analytical_required:
        analytical_ready, analytical_status = await analytical_database_readiness()
    else:
        analytical_ready, analytical_status = True, "not_required"

    adapter_url = workflow_adapter_base_url()
    adapter_required = container_mode
    if adapter_url:
        adapter_ready, adapter_status = await http_readiness(f"{adapter_url}/api/workflow-adapter/health")
    elif adapter_required:
        adapter_ready, adapter_status = False, "not_configured"
    else:
        adapter_ready, adapter_status = True, "not_required"

    frontend_url = os.getenv("BMS_FRONTEND_HEALTH_URL", "").strip()
    if frontend_url:
        frontend_ready, frontend_status = await http_readiness(frontend_url)
        frontend_required = True
    else:
        frontend_ready, frontend_status = True, "not_configured"
        frontend_required = False

    launch_allowed = workflow_launches_allowed()
    checks = {
        "process_liveness": _check(required=True, ready=True, status="alive"),
        "core_database": _check(required=True, ready=core_ready, status=core_status),
        "molbio_database": _check(
            required=True,
            ready=molbio_ready,
            status="ready" if molbio_ready else str(molbio.get("status", "unavailable")),
        ),
        "analytical_database": _check(
            required=analytical_required,
            ready=analytical_ready,
            status=analytical_status,
        ),
        "workflow_adapter": _check(
            required=adapter_required,
            ready=adapter_ready,
            status=adapter_status,
        ),
        "frontend": _check(required=frontend_required, ready=frontend_ready, status=frontend_status),
        "workflow_launch": {
            "required": True,
            "ready": launch_allowed,
            "allowed": launch_allowed,
            "status": "allowed" if launch_allowed else "blocked",
        },
    }
    overall_ready = all(
        bool(check["ready"])
        for check in checks.values()
        if bool(check.get("required", False))
    )
    return {"mode": mode, "ready": overall_ready, "checks": checks}
