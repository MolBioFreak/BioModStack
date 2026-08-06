from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from services.bioxp.errors import ConnectionStateError, RobotResponseError, RobotTransportError
from services.bioxp.runtime import BioXpRuntime

from .dependencies import get_bioxp_runtime, require_bioxp_mutation_access

router = APIRouter()
_CAMERA_QUERY_FIELDS = frozenset({"expected_generation"})


class CameraSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_generation: StrictInt = Field(ge=1)


def _reject_unknown_query(request: Request) -> None:
    unknown = set(request.query_params.keys()) - _CAMERA_QUERY_FIELDS
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported BioXP camera query fields: {sorted(unknown)}",
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
