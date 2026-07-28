from __future__ import annotations

import asyncio
import hashlib
from ipaddress import ip_address

import httpx
import pytest

from services.bioxp.errors import RobotTransportError
from services.bioxp.robot_client import BioXpRobotClient
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
