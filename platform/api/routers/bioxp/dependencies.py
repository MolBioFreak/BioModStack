from __future__ import annotations

import os

from fastapi import HTTPException, Request

from services.bioxp.runtime import BioXpRuntime, bioxp_connection_enabled

SAFE_LOCAL_MUTATIONS = frozenset(
    {
        "/protocols/compile",
    }
)
CONNECTION_MUTATIONS = frozenset(
    {
        "/connection/connect",
        "/connection/disconnect",
        "/connection/probe",
    }
)


def _relative_path(request: Request) -> str:
    marker = "/api/bioxp"
    path = request.url.path
    return path.split(marker, 1)[1] or "/" if marker in path else path


def _mutation_guard_required(request: Request) -> bool:
    if request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return False
    return _relative_path(request) not in SAFE_LOCAL_MUTATIONS


def mutations_enabled() -> bool:
    return os.getenv("BMS_BIOXP_MUTATIONS_ENABLED", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _is_exact_xz_action_request(request: Request, relative_path: str) -> bool:
    action_id = request.path_params.get("action_id")
    if not isinstance(action_id, str) or not action_id.startswith(("oem.x.", "oem.z.")):
        return False
    return relative_path in {
        f"/operator-controls/actions/{action_id}",
        f"/operator-controls/actions/{action_id}/admission",
    }


def require_bioxp_mutation_access(request: Request) -> None:
    if not _mutation_guard_required(request):
        return
    relative_path = _relative_path(request)
    if _is_exact_xz_action_request(request, relative_path):
        return
    if relative_path in CONNECTION_MUTATIONS:
        if bioxp_connection_enabled():
            return
        raise HTTPException(
            status_code=503,
            detail=(
                "BioXP connection access is disabled; set "
                "BMS_BIOXP_CONNECTION_ENABLED=1 to authorize status-only connection management"
            ),
        )
    if not mutations_enabled():
        raise HTTPException(
            status_code=503,
            detail="BioXP mutations are disabled; set BMS_BIOXP_MUTATIONS_ENABLED=1 to authorize this lane",
        )


def get_bioxp_runtime(request: Request) -> BioXpRuntime:
    runtime = getattr(request.app.state, "bioxp_runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="BioXP runtime is not initialized")
    if not isinstance(runtime, BioXpRuntime):
        # Tests may use a structurally compatible runtime double.
        return runtime
    return runtime
