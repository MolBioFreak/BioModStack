"""Isolated editor ASGI entrypoint. Run with the prepared Python 3.12 environment.

All paths (including static files and health) require the BMS transport secret.
The public ingress must point to BMS, never to this listener.
"""
from __future__ import annotations

import os
import secrets

from starlette.responses import JSONResponse


def create_app():
    secret = os.environ.get("BMS_ATOMWORKS_STUDIO_SECRET", "")
    if not secret:
        raise RuntimeError("BMS_ATOMWORKS_STUDIO_SECRET is required")
    # Set before import: upstream captures these at module initialization.
    os.environ["ANNOTATION_STUDIO_IDENTITY_HEADER"] = "X-BMS-Studio-Owner"
    os.environ["ANNOTATION_STUDIO_TRUST_PROXY_IDENTITY"] = "1"
    from annotation_studio.main import app as upstream

    async def app(scope, receive, send):
        if scope["type"] != "http":
            await upstream(scope, receive, send)
            return
        headers = dict(scope["headers"])
        supplied = headers.get(b"x-bms-studio-secret", b"")
        owner = headers.get(b"x-bms-studio-owner", b"").decode("latin1")
        if not secrets.compare_digest(secret.encode("utf-8"), supplied) or not owner:
            await JSONResponse({"detail": "private BMS Studio transport required"}, 401,
                               headers={"Cache-Control": "no-store"})(scope, receive, send)
            return
        # Refuse worker scripts even if someone bypasses the entry-page opt-out.
        if scope["path"].rsplit("/", 1)[-1] in {"sw.js", "service-worker.js"}:
            await JSONResponse({"detail": "Studio service worker disabled"}, 404)(scope, receive, send)
            return
        async def private_send(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [(k, v) for k, v in message["headers"]
                            if k.lower() not in {b"cache-control", b"content-security-policy"}] + [
                    (b"cache-control", b"private, no-store"),
                    (b"content-security-policy", b"frame-ancestors 'self'; worker-src 'none'")]}
            await send(message)
        await upstream(scope, receive, private_send)
    return app
