from __future__ import annotations

import asyncio
import hashlib
import math
from ipaddress import ip_address

import httpx
import pytest

from services.bioxp.errors import RobotTransportError
from services.bioxp.robot_client import BioXpRobotClient, DEFAULT_ROBOT_ROUTES
from services.bioxp.target_policy import ValidatedBioXpTarget


MAX_CAMERA_IMAGE_BYTES = 8 * 1024 * 1024
JPEG_BYTES = b"\xff\xd8jpeg\xff\xd9"


def target() -> ValidatedBioXpTarget:
    return ValidatedBioXpTarget(
        api_url="http://robot:8123",
        scheme="http",
        hostname="robot",
        port=8123,
        resolved_addresses=(ip_address("100.64.0.10"),),
    )


def image_headers(content: bytes) -> dict[str, str]:
    digest = hashlib.sha256(content).hexdigest()
    return {
        "Content-Type": "image/jpeg",
        "Content-Length": str(len(content)),
        "ETag": f'"{digest}"',
        "X-Content-SHA256": digest,
    }


class CameraTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return await self.handler(request)


class CountingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], *, delay: float = 0.0) -> None:
        self.chunks = chunks
        self.delay = delay
        self.yielded = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.yielded += 1
            yield chunk


async def response(request: httpx.Request, *, content: bytes = JPEG_BYTES, headers=None, json=None):
    return httpx.Response(
        200,
        content=content if json is None else None,
        headers=headers,
        json=json,
        request=request,
    )


def canonical_status() -> dict[str, object]:
    return {
        "schema_version": "bioxp.camera_status.v1",
        "available": True,
        "frame_sequence": 42,
        "frame_captured_at": "2026-07-27T12:00:00Z",
        "frame_age_seconds": 0.25,
        "freshness_budget_seconds": 2.0,
        "provider_generation": 7,
        "dropped_frames": 3,
        "content_sha256": "a" * 64,
        "detail": None,
    }


def test_camera_client_uses_only_fixed_routes_and_bounded_timeouts():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/camera/status":
            return await response(request, json=canonical_status())
        content = JPEG_BYTES
        return await response(request, content=content, headers=image_headers(content))

    transport = CameraTransport(handler)
    client = BioXpRobotClient(target(), transport=transport)

    status = asyncio.run(client.camera_status())
    latest = asyncio.run(client.camera_latest())
    snapshot = asyncio.run(client.camera_snapshot())

    assert status == canonical_status()
    assert latest.content == JPEG_BYTES
    assert snapshot.content == JPEG_BYTES
    assert [(item.method, item.url.path) for item in transport.requests] == [
        ("GET", "/camera/status"),
        ("GET", "/camera/frame/latest"),
        ("POST", "/camera/snapshot"),
    ]
    assert all(not item.url.query for item in transport.requests)
    assert transport.requests[0].extensions["timeout"]["connect"] <= 3.0
    assert transport.requests[0].extensions["timeout"]["read"] <= 5.0
    assert transport.requests[1].extensions["timeout"]["read"] <= 5.0
    assert transport.requests[2].extensions["timeout"]["read"] <= 15.0
    asyncio.run(client.close())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"device": "/dev/video0"}),
        lambda payload: payload.update({"available": "true"}),
        lambda payload: payload.update({"frame_sequence": -1}),
        lambda payload: payload.update({"content_sha256": "not-a-hash"}),
        lambda payload: payload.update({"available": False}),
        lambda payload: payload.update({"frame_age_seconds": math.inf}),
        lambda payload: payload.update({"frame_captured_at": 1785153600}),
        lambda payload: payload.update({"frame_captured_at": "1785153600"}),
    ],
)
def test_camera_status_schema_fails_closed_on_malformed_or_contradictory_json(mutate):
    payload = canonical_status()
    mutate(payload)

    async def handler(request: httpx.Request) -> httpx.Response:
        return await response(request, json=payload)

    client = BioXpRobotClient(target(), transport=CameraTransport(handler))
    with pytest.raises(RobotTransportError, match="camera status"):
        asyncio.run(client.camera_status())
    asyncio.run(client.close())


def test_camera_image_rejects_invalid_content_type_before_returning_bytes():
    async def handler(request: httpx.Request) -> httpx.Response:
        return await response(
            request,
            content=b"not-an-image",
            headers={"Content-Type": "application/json", "Content-Length": "12"},
        )

    client = BioXpRobotClient(target(), transport=CameraTransport(handler))
    with pytest.raises(RobotTransportError, match="JPEG"):
        asyncio.run(client.camera_latest())
    asyncio.run(client.close())


@pytest.mark.parametrize("declared_length", [MAX_CAMERA_IMAGE_BYTES + 1, 999_999_999])
def test_camera_image_rejects_oversized_content_length_without_buffering(declared_length):
    async def handler(request: httpx.Request) -> httpx.Response:
        headers = image_headers(JPEG_BYTES)
        headers["Content-Length"] = str(declared_length)
        return await response(request, content=JPEG_BYTES, headers=headers)

    client = BioXpRobotClient(target(), transport=CameraTransport(handler))
    with pytest.raises(RobotTransportError, match="size limit"):
        asyncio.run(client.camera_latest())
    asyncio.run(client.close())


def test_camera_image_verifies_content_hash_and_etag():
    async def handler(request: httpx.Request) -> httpx.Response:
        headers = image_headers(JPEG_BYTES)
        headers["X-Content-SHA256"] = "b" * 64
        headers["ETag"] = '"' + "b" * 64 + '"'
        return await response(request, content=JPEG_BYTES, headers=headers)

    client = BioXpRobotClient(target(), transport=CameraTransport(handler))
    with pytest.raises(RobotTransportError, match="hash"):
        asyncio.run(client.camera_snapshot())
    asyncio.run(client.close())


def test_camera_image_rejects_declared_length_mismatch():
    async def handler(request: httpx.Request) -> httpx.Response:
        content = b"\xff\xd8jpeg\xff\xd9"
        headers = image_headers(content)
        headers["Content-Length"] = str(len(content) + 1)
        return await response(request, content=content, headers=headers)

    client = BioXpRobotClient(target(), transport=CameraTransport(handler))
    with pytest.raises(RobotTransportError, match="content length"):
        asyncio.run(client.camera_latest())
    asyncio.run(client.close())


def test_camera_image_rejects_non_jpeg_bytes_even_with_matching_provenance():
    async def handler(request: httpx.Request) -> httpx.Response:
        content = b"not-a-jpeg"
        return await response(request, content=content, headers=image_headers(content))

    client = BioXpRobotClient(target(), transport=CameraTransport(handler))
    with pytest.raises(RobotTransportError, match="JPEG framing"):
        asyncio.run(client.camera_latest())
    asyncio.run(client.close())


def test_camera_timeout_is_reported_as_bounded_transport_failure():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("camera stalled", request=request)

    client = BioXpRobotClient(target(), transport=CameraTransport(handler))
    with pytest.raises(RobotTransportError, match="timed out"):
        asyncio.run(client.camera_latest())
    asyncio.run(client.close())


def test_camera_image_requires_declared_content_length():
    async def handler(request: httpx.Request) -> httpx.Response:
        headers = image_headers(JPEG_BYTES)
        del headers["Content-Length"]
        return httpx.Response(
            200,
            headers=headers,
            stream=CountingStream([JPEG_BYTES]),
            request=request,
        )

    client = BioXpRobotClient(target(), transport=CameraTransport(handler))
    with pytest.raises(RobotTransportError, match="content length"):
        asyncio.run(client.camera_latest())
    asyncio.run(client.close())


def test_camera_image_rejects_plus_prefixed_content_length_grammar():
    async def handler(request: httpx.Request) -> httpx.Response:
        headers = image_headers(JPEG_BYTES)
        headers["Content-Length"] = f"+{len(JPEG_BYTES)}"
        return httpx.Response(
            200,
            headers=headers,
            stream=CountingStream([JPEG_BYTES]),
            request=request,
        )

    client = BioXpRobotClient(target(), transport=CameraTransport(handler))
    with pytest.raises(RobotTransportError, match="content length"):
        asyncio.run(client.camera_latest())
    asyncio.run(client.close())


def test_camera_image_rejects_bare_noncanonical_etag():
    async def handler(request: httpx.Request) -> httpx.Response:
        headers = image_headers(JPEG_BYTES)
        headers["ETag"] = hashlib.sha256(JPEG_BYTES).hexdigest()
        return await response(request, content=JPEG_BYTES, headers=headers)

    client = BioXpRobotClient(target(), transport=CameraTransport(handler))
    with pytest.raises(RobotTransportError, match="ETag"):
        asyncio.run(client.camera_latest())
    asyncio.run(client.close())


def test_camera_error_body_is_bounded_before_rejection():
    stream = CountingStream([b"x" * (1024 * 1024) for _ in range(9)])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, stream=stream, request=request)

    client = BioXpRobotClient(target(), transport=CameraTransport(handler))
    with pytest.raises(RobotTransportError, match="error response exceeded"):
        asyncio.run(client.camera_latest())
    assert stream.yielded <= 1
    asyncio.run(client.close())


def test_camera_status_body_is_bounded_before_json_validation():
    stream = CountingStream([b"x" * (1024 * 1024) for _ in range(9)])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=stream,
            request=request,
        )

    client = BioXpRobotClient(target(), transport=CameraTransport(handler))
    with pytest.raises(RobotTransportError, match="camera status"):
        asyncio.run(client.camera_status())
    assert stream.yielded <= 1
    asyncio.run(client.close())


def test_camera_transaction_has_absolute_wall_clock_deadline():
    stream = CountingStream([b"x"] * 100, delay=0.01)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers=image_headers(JPEG_BYTES),
            stream=stream,
            request=request,
        )

    routes = dict(DEFAULT_ROBOT_ROUTES)
    routes["camera_latest"] = ("GET", "/camera/frame/latest", 0.03)
    client = BioXpRobotClient(target(), routes=routes, transport=CameraTransport(handler))
    with pytest.raises(RobotTransportError, match="timed out"):
        asyncio.run(client.camera_latest())
    assert stream.yielded < 100
    asyncio.run(client.close())


def camera_stream_payload(*, state: str = "live", active: bool = True) -> dict[str, object]:
    return {
        "schema_version": "bioxp.camera_stream.v1",
        "state": state,
        "active": active,
        "stream_id": "stream-1",
        "camera_ownership_epoch": 4,
        "device": "/dev/video0",
        "fps": 8 if active else None,
        "quality": 7 if active else None,
        "width": 640 if active else None,
        "height": 480 if active else None,
        "frames_emitted": 2,
        "dropped_frames": 0,
        "latest_frame_at": "2026-07-27T12:00:01Z" if active else None,
        "last_error": None,
    }


def test_camera_stream_controls_use_fixed_registry_routes_and_strict_projection():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/stream/start"):
            payload = camera_stream_payload()
        elif request.url.path.endswith("/stream/state"):
            payload = camera_stream_payload()
        elif request.url.path.endswith("/stream/stop"):
            payload = camera_stream_payload(state="off", active=False)
        else:
            raise AssertionError(request.url.path)
        return await response(request, json=payload)

    transport = CameraTransport(handler)
    client = BioXpRobotClient(target(), transport=transport)

    started = asyncio.run(client.camera_stream_start())
    state = asyncio.run(client.camera_stream_state())
    stopped = asyncio.run(client.camera_stream_stop())

    assert started["state"] == "live"
    assert state["camera_ownership_epoch"] == 4
    assert stopped["state"] == "off"
    assert [(item.method, item.url.path) for item in transport.requests] == [
        ("POST", "/camera/stream/start"),
        ("GET", "/camera/stream/state"),
        ("POST", "/camera/stream/stop"),
    ]
    assert all(not item.url.query for item in transport.requests)
    assert all("device" not in payload for payload in (started, state, stopped))
    assert transport.requests[0].read() == b"{}"
    assert transport.requests[2].read() == b"{}"
    asyncio.run(client.close())


def test_camera_mjpeg_context_validates_content_type_and_yields_bytes():
    jpeg_part = b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 6\r\n\r\n\xff\xd8x\xff\xd9\r\n"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame"},
            stream=CountingStream([jpeg_part[:10], jpeg_part[10:]]),
            request=request,
        )

    client = BioXpRobotClient(target(), transport=CameraTransport(handler))

    async def consume():
        async with client.camera_mjpeg_stream() as chunks:
            return [chunk async for chunk in chunks]

    chunks = asyncio.run(consume())
    assert b"".join(chunks) == jpeg_part
    asyncio.run(client.close())


def test_camera_mjpeg_context_rejects_non_multipart_response():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=JPEG_BYTES, request=request)

    client = BioXpRobotClient(target(), transport=CameraTransport(handler))

    async def consume():
        async with client.camera_mjpeg_stream():
            pass

    with pytest.raises(RobotTransportError, match="multipart"):
        asyncio.run(consume())
    asyncio.run(client.close())
