from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import Header, HTTPException, Request

from services.bioxp.runtime import BioXpRuntime

_BIOXP_OPERATOR_HEADER = "X-BMS-BioXP-Operator-Token"
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


def _operator_credential() -> tuple[str | None, str | None]:
    token_file = os.getenv("BMS_BIOXP_OPERATOR_TOKEN_FILE", "").strip()
    if token_file:
        try:
            path = Path(token_file).expanduser()
            if not path.is_file():
                return None, "BioXP operator credential file is missing, unreadable, or empty"
            token = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None, "BioXP operator credential file is missing, unreadable, or empty"
        if not token:
            return None, "BioXP operator credential file is missing, unreadable, or empty"
        return token, None
    token = os.getenv("BMS_BIOXP_OPERATOR_TOKEN", "").strip()
    if not token:
        return None, "BioXP operator credential is not configured"
    return token, None


def _operator_token() -> str | None:
    return _operator_credential()[0]


def mutations_enabled() -> bool:
    return os.getenv("BMS_BIOXP_MUTATIONS_ENABLED", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def operator_token_configured() -> bool:
    return _operator_token() is not None


def require_bioxp_mutation_access(
    request: Request,
    x_bioxp_operator_token: str | None = Header(default=None, alias=_BIOXP_OPERATOR_HEADER),
    authorization: str | None = Header(default=None),
) -> None:
    if not _mutation_guard_required(request):
        return
    if not mutations_enabled():
        raise HTTPException(
            status_code=503,
            detail="BioXP mutations are disabled; set BMS_BIOXP_MUTATIONS_ENABLED=1 to authorize this lane",
        )
    expected, credential_error = _operator_credential()
    if expected is None:
        raise HTTPException(status_code=503, detail=credential_error)
    provided = x_bioxp_operator_token
    if provided is None and authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    if not provided:
        raise HTTPException(
            status_code=401,
            detail="BioXP operator credential is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="BioXP operator credential is invalid")


def get_bioxp_runtime(request: Request) -> BioXpRuntime:
    runtime = getattr(request.app.state, "bioxp_runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="BioXP runtime is not initialized")
    if not isinstance(runtime, BioXpRuntime):
        # Tests may use a structurally compatible runtime double.
        return runtime
    return runtime
