"""Browser mutation-origin CSRF check for tailnet/local API routes."""
from __future__ import annotations

import os
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.middleware.cors import CORSMiddleware


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _origin(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        return parsed.scheme, parsed.hostname.casefold(), parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


def browser_mutation_origin(request: Request) -> None:
    """Reject browser CSRF, without adding credentials to tailnet/agent use.

    Respect the existing CORS origin owner and configured frontend.
    Non-browser callers without Origin/Fetch Metadata retain their API path.
    """
    supplied = request.headers.get("origin")
    fetch_site = request.headers.get("sec-fetch-site", "").casefold()
    if supplied is None:
        if fetch_site in {"cross-site", "same-site"}:
            raise _error(403, "origin_denied", "cross-site browser mutations are not allowed")
        return
    trusted = {_origin(str(request.base_url)), _origin(os.environ.get("BMS_FRONTEND_HEALTH_URL", ""))}
    for middleware in request.app.user_middleware:
        if middleware.cls is CORSMiddleware:
            trusted.update(_origin(value) for value in middleware.kwargs.get("allow_origins", []))
    trusted.discard(None)
    if _origin(supplied) not in trusted:
        raise _error(403, "origin_denied", "untrusted browser origin cannot perform mutation")
