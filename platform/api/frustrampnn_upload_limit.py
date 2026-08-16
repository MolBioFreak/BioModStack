"""Pre-parser request-body admission for FrustraMPNN multipart uploads.

The 64 MiB file limit remains authoritative inside each route. This middleware
allows exactly 1 MiB of additional multipart boundary, part-header, and bounded
metadata overhead, so the maximum admitted HTTP request body is 65 MiB.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from services.frustrampnn.jobs import MAX_UPLOAD_BYTES


# Exact allowance for multipart boundaries, part headers, and bounded form metadata.
MULTIPART_METADATA_HEADER_ALLOWANCE_BYTES = 1 * 1024 * 1024
FRUSTRAMPNN_MULTIPART_BODY_LIMIT_BYTES = (
    MAX_UPLOAD_BYTES + MULTIPART_METADATA_HEADER_ALLOWANCE_BYTES
)
FRUSTRAMPNN_MULTIPART_UPLOAD_PATHS = frozenset(
    {
        "/api/frustrampnn/sources/inspect/upload",
        "/api/frustrampnn/settings/validate/upload",
        "/api/frustrampnn/jobs/uploads/analyze",
        "/api/frustrampnn/candidates/handoff",
    }
)

ASGIScope = dict[str, Any]
ASGIMessage = dict[str, Any]
ASGIReceive = Callable[[], Awaitable[ASGIMessage]]
ASGISend = Callable[[ASGIMessage], Awaitable[None]]
ASGIApp = Callable[[ASGIScope, ASGIReceive, ASGISend], Awaitable[None]]


class _RequestBodyTooLarge(Exception):
    """Stop downstream multipart parsing as soon as the body bound is crossed."""


def _is_multipart(headers: list[tuple[bytes, bytes]]) -> bool:
    return any(
        name.lower() == b"content-type"
        and value.split(b";", 1)[0].strip().lower() == b"multipart/form-data"
        for name, value in headers
    )


def _content_length_exceeds(
    headers: list[tuple[bytes, bytes]], limit_bytes: int
) -> bool:
    for name, value in headers:
        if name.lower() != b"content-length":
            continue
        try:
            if int(value.strip()) > limit_bytes:
                return True
        except ValueError:
            # The ASGI server owns malformed framing rejection. The streaming
            # counter still fails closed if actual body bytes cross the limit.
            continue
    return False


async def _send_413(send: ASGISend, *, limit_bytes: int) -> None:
    body = (
        "{\"detail\":\"FrustraMPNN multipart request body exceeds "
        f"the {limit_bytes}-byte limit\"}}"
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-length", str(len(body)).encode("ascii")),
                (b"content-type", b"application/json"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class FrustraMPNNUploadLimitMiddleware:
    """Stream-count only the four FrustraMPNN multipart upload request bodies."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_upload_bytes: int = MAX_UPLOAD_BYTES,
        multipart_allowance_bytes: int = MULTIPART_METADATA_HEADER_ALLOWANCE_BYTES,
    ) -> None:
        if max_upload_bytes < 0 or not (
            0 <= multipart_allowance_bytes <= 1024 * 1024
        ):
            raise ValueError("multipart body limits must be non-negative and allowance <= 1 MiB")
        self.app = app
        self.limit_bytes = max_upload_bytes + multipart_allowance_bytes

    async def __call__(
        self,
        scope: ASGIScope,
        receive: ASGIReceive,
        send: ASGISend,
    ) -> None:
        headers = scope.get("headers", [])
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path") not in FRUSTRAMPNN_MULTIPART_UPLOAD_PATHS
            or not _is_multipart(headers)
        ):
            await self.app(scope, receive, send)
            return

        if _content_length_exceeds(headers, self.limit_bytes):
            await _send_413(send, limit_bytes=self.limit_bytes)
            return

        total_bytes = 0
        response_started = False

        async def limited_receive() -> ASGIMessage:
            nonlocal total_bytes
            message = await receive()
            if message.get("type") == "http.request":
                total_bytes += len(message.get("body", b""))
                if total_bytes > self.limit_bytes:
                    raise _RequestBodyTooLarge
            return message

        async def tracked_send(message: ASGIMessage) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _RequestBodyTooLarge:
            # Multipart routes consume the complete request before starting a
            # response. Tracking prevents a second response if that invariant
            # is ever broken by a future downstream implementation.
            if not response_started:
                await _send_413(send, limit_bytes=self.limit_bytes)


__all__ = [
    "FRUSTRAMPNN_MULTIPART_BODY_LIMIT_BYTES",
    "FRUSTRAMPNN_MULTIPART_UPLOAD_PATHS",
    "FrustraMPNNUploadLimitMiddleware",
    "MULTIPART_METADATA_HEADER_ALLOWANCE_BYTES",
]
