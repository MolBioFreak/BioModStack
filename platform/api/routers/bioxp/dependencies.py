from __future__ import annotations

import os

from fastapi import HTTPException, Request

from services.bioxp.runtime import BioXpRuntime

SAFE_LOCAL_MUTATIONS = frozenset(
    {
        "/protocols/compile",
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


def require_bioxp_mutation_access(request: Request) -> None:
    if not _mutation_guard_required(request):
        return
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
