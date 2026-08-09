from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pytest


UPLOAD_PATHS = (
    "/api/frustrampnn/sources/inspect/upload",
    "/api/frustrampnn/settings/validate/upload",
    "/api/frustrampnn/jobs/uploads/analyze",
    "/api/frustrampnn/candidates/handoff",
)


def _registered_middleware_class():
    import main

    matches = [
        item.cls
        for item in main.app.user_middleware
        if item.cls.__name__ == "FrustraMPNNUploadLimitMiddleware"
    ]
    assert len(matches) == 1, "FrustraMPNN upload limiter is not registered exactly once"
    return matches[0]


async def _drive_asgi(
    app,
    *,
    path: str,
    chunks: Iterable[bytes],
    content_length: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    body_chunks = list(chunks)
    messages = iter(
        [
            {
                "type": "http.request",
                "body": chunk,
                "more_body": index < len(body_chunks) - 1,
            }
            for index, chunk in enumerate(body_chunks)
        ]
    )
    receive_calls = 0
    sent: list[dict[str, Any]] = []
    headers = [(b"content-type", b"multipart/form-data; boundary=bounded")]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode("ascii")))

    async def receive() -> dict[str, Any]:
        nonlocal receive_calls
        receive_calls += 1
        try:
            return next(messages)
        except StopIteration:
            return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
        },
        receive,
        send,
    )
    return sent, receive_calls


def _downstream_probe(state: dict[str, Any]):
    async def downstream(scope, receive, send) -> None:
        state["invoked"] = True
        while True:
            message = await receive()
            if message["type"] != "http.request" or not message.get("more_body", False):
                break
        state["handler_reached"] = True
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    return downstream


@pytest.mark.asyncio
@pytest.mark.parametrize("path", UPLOAD_PATHS)
async def test_oversized_content_length_is_rejected_before_receive_or_downstream(path: str) -> None:
    middleware_class = _registered_middleware_class()
    state: dict[str, Any] = {}
    app = middleware_class(
        _downstream_probe(state),
        max_upload_bytes=8,
        multipart_allowance_bytes=4,
    )

    sent, receive_calls = await _drive_asgi(
        app, path=path, chunks=[b"not-read"], content_length=13
    )

    assert state == {}
    assert receive_calls == 0
    assert [message["type"] for message in sent] == [
        "http.response.start",
        "http.response.body",
    ]
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
@pytest.mark.parametrize("path", UPLOAD_PATHS)
async def test_chunked_overflow_is_rejected_before_handler_and_without_double_response(path: str) -> None:
    middleware_class = _registered_middleware_class()
    state: dict[str, Any] = {}
    app = middleware_class(
        _downstream_probe(state),
        max_upload_bytes=8,
        multipart_allowance_bytes=4,
    )

    sent, receive_calls = await _drive_asgi(
        app, path=path, chunks=[b"123456", b"789012", b"3"]
    )

    assert state == {"invoked": True}
    assert receive_calls == 3
    assert [message["type"] for message in sent] == [
        "http.response.start",
        "http.response.body",
    ]
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
@pytest.mark.parametrize("path", UPLOAD_PATHS)
async def test_request_at_total_body_limit_passes_to_downstream(path: str) -> None:
    middleware_class = _registered_middleware_class()
    state: dict[str, Any] = {}
    app = middleware_class(
        _downstream_probe(state),
        max_upload_bytes=8,
        multipart_allowance_bytes=4,
    )

    sent, receive_calls = await _drive_asgi(
        app, path=path, chunks=[b"12345", b"6789012"]
    )

    assert state == {"invoked": True, "handler_reached": True}
    assert receive_calls == 2
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_oversized_body_on_unrelated_path_is_unaffected() -> None:
    middleware_class = _registered_middleware_class()
    state: dict[str, Any] = {}
    app = middleware_class(
        _downstream_probe(state),
        max_upload_bytes=8,
        multipart_allowance_bytes=4,
    )

    sent, receive_calls = await _drive_asgi(
        app,
        path="/api/jobs/imports/external/upload",
        chunks=[b"1234567890123"],
        content_length=13,
    )

    assert state == {"invoked": True, "handler_reached": True}
    assert receive_calls == 1
    assert sent[0]["status"] == 204
