from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError

from services.bioxp.errors import ConnectionStateError, RobotResponseError, RobotTransportError
from services.bioxp.runtime import BioXpRuntime

from .dependencies import get_bioxp_runtime, require_bioxp_mutation_access

router = APIRouter()
_CAMERA_QUERY_FIELDS = frozenset({"expected_generation"})


class CameraSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_generation: StrictInt = Field(ge=1)


class CameraStreamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_generation: StrictInt = Field(ge=1)


class CameraStreamPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["bioxp.camera_stream.v1"]
    state: Literal["off", "starting", "live", "error"]
    active: StrictBool
    stream_id: str | None = None
    camera_ownership_epoch: StrictInt = Field(ge=0)
    device: str | None = None
    fps: StrictInt | None = Field(default=None, ge=1, le=30)
    quality: StrictInt | None = Field(default=None, ge=2, le=15)
    width: StrictInt | None = Field(default=None, ge=160, le=1920)
    height: StrictInt | None = Field(default=None, ge=120, le=1080)
    frames_emitted: StrictInt = Field(ge=0)
    dropped_frames: StrictInt = Field(ge=0)
    latest_frame_at: str | None = None
    last_error: str | None = Field(default=None, max_length=1000)
    idempotent: StrictBool | None = None
    ok: StrictBool | None = None
    replacement: StrictBool | None = None
    queue_max_frames: StrictInt | None = Field(default=None, ge=1, le=2)
    mjpeg_url: str | None = None
    session: dict[str, Any] | None = None
    freshness: dict[str, Any] | None = None
    provenance: str | None = None


def _reject_unknown_query(request: Request) -> None:
    unknown = set(request.query_params.keys()) - _CAMERA_QUERY_FIELDS
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported BioXP camera query fields: {sorted(unknown)}",
        )


def _reject_stream_command_query(request: Request) -> None:
    if request.query_params:
        raise HTTPException(
            status_code=422,
            detail="BioXP camera stream control does not accept query tuning fields",
        )


async def _leased_camera_call(
    runtime: BioXpRuntime,
    *,
    expected_generation: int,
    method_name: str,
) -> Any:
    try:
        async with runtime.connection.active_query_lease(
            expected_generation=expected_generation,
            require_fresh=False,
        ) as client:
            method = getattr(client, method_name, None)
            if not callable(method):
                raise RobotTransportError("Connected BioXP client does not implement the camera contract")
            return await cast(Callable[[], Awaitable[Any]], method)()
    except ConnectionStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RobotResponseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except RobotTransportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _stream_response(payload: Any, *, connection_generation: int) -> dict[str, Any]:
    try:
        stream = CameraStreamPayload.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=502, detail="Connected BioXP client returned a malformed camera stream payload") from exc
    return {
        "schema_version": stream.schema_version,
        "state": stream.state,
        "active": stream.active,
        "stream_id": stream.stream_id,
        "camera_ownership_epoch": stream.camera_ownership_epoch,
        "fps": stream.fps,
        "quality": stream.quality,
        "width": stream.width,
        "height": stream.height,
        "frames_emitted": stream.frames_emitted,
        "dropped_frames": stream.dropped_frames,
        "latest_frame_at": stream.latest_frame_at,
        "last_error": stream.last_error,
        "idempotent": stream.idempotent,
        "connection_generation": connection_generation,
    }


def _raise_camera_exception(exc: Exception) -> None:
    if isinstance(exc, ConnectionStateError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, RobotResponseError):
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    if isinstance(exc, RobotTransportError):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    raise exc


@router.post(
    "/camera/stream/start",
    dependencies=[Depends(require_bioxp_mutation_access)],
)
async def start_camera_stream(
    http_request: Request,
    request: CameraStreamRequest,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    _reject_stream_command_query(http_request)
    payload = await _leased_camera_call(
        runtime,
        expected_generation=request.expected_generation,
        method_name="camera_stream_start",
    )
    return _stream_response(payload, connection_generation=request.expected_generation)


@router.get("/camera/stream/state")
async def get_camera_stream_state(
    request: Request,
    expected_generation: int = Query(ge=1),
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    _reject_unknown_query(request)
    payload = await _leased_camera_call(
        runtime,
        expected_generation=expected_generation,
        method_name="camera_stream_state",
    )
    return _stream_response(payload, connection_generation=expected_generation)


@router.post(
    "/camera/stream/stop",
    dependencies=[Depends(require_bioxp_mutation_access)],
)
async def stop_camera_stream(
    http_request: Request,
    request: CameraStreamRequest,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    _reject_stream_command_query(http_request)
    payload = await _leased_camera_call(
        runtime,
        expected_generation=request.expected_generation,
        method_name="camera_stream_stop",
    )
    return _stream_response(payload, connection_generation=request.expected_generation)


async def _iter_validated_mjpeg(chunks: Any):
    buffer = bytearray()
    boundary = b"--frame"
    max_header_bytes = 16 * 1024
    max_frame_bytes = 8 * 1024 * 1024
    async for chunk in chunks:
        if not isinstance(chunk, (bytes, bytearray)):
            raise RobotTransportError("BioXP camera stream yielded a non-byte chunk")
        buffer.extend(chunk)
        if len(buffer) > max_frame_bytes + max_header_bytes:
            raise RobotTransportError("BioXP camera stream frame exceeded the size limit")
        while True:
            start = buffer.find(boundary)
            if start < 0:
                if len(buffer) > max_header_bytes:
                    raise RobotTransportError("BioXP camera stream boundary was not found")
                break
            if start:
                del buffer[:start]
            header_start = len(boundary)
            if buffer[header_start:header_start + 2] == b"\r\n":
                header_start += 2
            header_end = buffer.find(b"\r\n\r\n", header_start)
            if header_end < 0:
                if len(buffer) > max_header_bytes:
                    raise RobotTransportError("BioXP camera stream headers exceeded the size limit")
                break
            header_block = bytes(buffer[header_start:header_end]).split(b"\r\n")
            headers: dict[bytes, bytes] = {}
            for line in header_block:
                if b":" not in line:
                    raise RobotTransportError("BioXP camera stream contained a malformed part header")
                name, value = line.split(b":", 1)
                headers[name.strip().lower()] = value.strip()
            if headers.get(b"content-type", b"").lower() != b"image/jpeg":
                raise RobotTransportError("BioXP camera stream part was not JPEG")
            declared = headers.get(b"content-length")
            if declared is None or not declared.isdigit():
                raise RobotTransportError("BioXP camera stream part had no valid content length")
            length = int(declared)
            if length < 4 or length > max_frame_bytes:
                raise RobotTransportError("BioXP camera stream part exceeded the size limit")
            body_start = header_end + 4
            body_end = body_start + length
            if len(buffer) < body_end + 2:
                break
            if buffer[body_end:body_end + 2] != b"\r\n":
                raise RobotTransportError("BioXP camera stream part had invalid framing")
            body = bytes(buffer[body_start:body_end])
            if not (body.startswith(b"\xff\xd8") and body.endswith(b"\xff\xd9")):
                raise RobotTransportError("BioXP camera stream part had invalid JPEG framing")
            yield bytes(buffer[:body_end + 2])
            del buffer[:body_end + 2]


@router.get("/camera/mjpeg")
async def proxy_camera_mjpeg(
    request: Request,
    expected_generation: int = Query(ge=1),
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> StreamingResponse:
    _reject_unknown_query(request)
    lease = runtime.connection.active_query_lease(
        expected_generation=expected_generation,
        require_fresh=False,
    )
    try:
        client = await lease.__aenter__()
        stream_context_factory = getattr(client, "camera_mjpeg_stream", None)
        if not callable(stream_context_factory):
            raise RobotTransportError("Connected BioXP client does not implement the camera stream contract")
        stream_context = stream_context_factory()
        chunks = await stream_context.__aenter__()
    except Exception as exc:
        try:
            await lease.__aexit__(type(exc), exc, exc.__traceback__)
        except Exception:
            pass
        _raise_camera_exception(exc)
        raise AssertionError("unreachable")

    async def iterator():
        try:
            async for part in _iter_validated_mjpeg(chunks):
                yield part
        finally:
            await stream_context.__aexit__(None, None, None)
            await lease.__aexit__(None, None, None)

    return StreamingResponse(
        iterator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-BioXP-Connection-Generation": str(expected_generation),
        },
    )


def _image_response(image: Any, *, connection_generation: int) -> Response:
    return Response(
        content=image.content,
        media_type=image.content_type,
        headers={
            "ETag": image.etag,
            "X-Content-SHA256": image.sha256,
            "X-BioXP-Connection-Generation": str(connection_generation),
            "Cache-Control": "no-store",
        },
    )


@router.get("/camera/status")
async def get_camera_status(
    request: Request,
    expected_generation: int = Query(ge=1),
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    _reject_unknown_query(request)
    status = await _leased_camera_call(
        runtime,
        expected_generation=expected_generation,
        method_name="camera_status",
    )
    available = status["available"] is True
    frame_age = status["frame_age_seconds"]
    freshness_budget = status["freshness_budget_seconds"]
    state = (
        "unavailable"
        if not available
        else "live"
        if frame_age <= freshness_budget
        else "stale"
    )
    return {
        "schema_version": status["schema_version"],
        "state": state,
        "available": available,
        "frame_sequence": status["frame_sequence"],
        "frame_captured_at": status["frame_captured_at"],
        "frame_age_seconds": frame_age,
        "freshness_budget_seconds": freshness_budget,
        "provider_generation": status["provider_generation"],
        "dropped_frames": status["dropped_frames"],
        "content_sha256": status["content_sha256"],
        "detail": status["detail"],
        "connection_generation": expected_generation,
    }


@router.get("/camera/frame/latest", response_class=Response)
async def get_latest_camera_frame(
    request: Request,
    expected_generation: int = Query(ge=1),
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> Response:
    _reject_unknown_query(request)
    image = await _leased_camera_call(
        runtime,
        expected_generation=expected_generation,
        method_name="camera_latest",
    )
    return _image_response(image, connection_generation=expected_generation)


@router.post(
    "/camera/snapshot",
    response_class=Response,
    dependencies=[Depends(require_bioxp_mutation_access)],
)
async def capture_camera_snapshot(
    request: CameraSnapshotRequest,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> Response:
    image = await _leased_camera_call(
        runtime,
        expected_generation=request.expected_generation,
        method_name="camera_snapshot",
    )
    return _image_response(image, connection_generation=request.expected_generation)
