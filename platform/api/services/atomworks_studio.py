"""Private transport only. No Studio/Torch imports in the BMS API environment."""
from __future__ import annotations

import hashlib
import os
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException, Request
from starlette.responses import Response

PREFIX = "/api/atomworks-studio"
MAX_BODY = 33 * 1024 * 1024  # upstream 32 MiB plus multipart envelope


def settings() -> tuple[str, str]:
    url = os.environ.get("BMS_ATOMWORKS_STUDIO_URL", "")
    secret = os.environ.get("BMS_ATOMWORKS_STUDIO_SECRET", "")
    try:
        parsed = urlsplit(url)
        parsed.port  # validate malformed configured port before constructing a request
    except ValueError as exc:
        raise HTTPException(503, "isolated Studio service URL is invalid") from exc
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or parsed.username or parsed.password or not secret):
        raise HTTPException(503, "isolated Studio service is not configured")
    return url.rstrip("/"), secret


async def forward(request: Request, path: str, actor: str) -> Response:
    base, secret = settings()
    # Never let a path turn the configured loopback target into an SSRF URL.
    if any(part in {".", ".."} for part in path.split("/")) or "\\" in path:
        raise HTTPException(400, "invalid Studio path")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY:
            raise HTTPException(413, "Studio request exceeds upload limit")
    headers = {
        "X-BMS-Studio-Secret": secret,
        "X-BMS-Studio-Owner": hashlib.sha256(actor.encode()).hexdigest(),
        "Accept-Encoding": "identity",
    }
    for key in ("content-type", "accept"):
        if key in request.headers:
            headers[key] = request.headers[key]
    transport = getattr(request.app.state, "atomworks_studio_transport", None)
    try:
        async with httpx.AsyncClient(transport=transport, timeout=180, trust_env=False) as client:
            result = await client.request(request.method, base + "/" + path,
                                          params=request.url.query,
                                          headers=headers, content=bytes(body), follow_redirects=False)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "isolated Studio service unavailable") from exc
    safe_headers = {key: value for key, value in result.headers.items()
                    if key in {"content-type", "content-disposition", "x-request-id"}}
    safe_headers.update({"Cache-Control": "private, no-store", "Vary": "Cookie, Authorization",
                         "Content-Security-Policy": "frame-ancestors 'self'; worker-src 'none'",
                         "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})
    # No upstream cookies, identity, redirects, etags or cache metadata escape.
    if 300 <= result.status_code < 400:
        raise HTTPException(502, "unexpected Studio redirect")
    return Response(result.content, status_code=result.status_code, headers=safe_headers)
