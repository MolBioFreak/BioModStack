from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from services.bioxp.errors import (
    ConnectionStateError,
    ProfileStoreError,
    RobotResponseError,
    RobotTransportError,
    TargetPolicyError,
)
from services.bioxp.connection import mask_target_url
from services.bioxp.models import BioXpProfile
from services.bioxp.runtime import BioXpRuntime, bioxp_connection_enabled

from .dependencies import get_bioxp_runtime, mutations_enabled, require_bioxp_mutation_access

router = APIRouter(dependencies=[Depends(require_bioxp_mutation_access)])


class RecoverMotionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_generation: int = Field(ge=1)
    operator_reason: str = Field(min_length=1, max_length=2000)


def _public_snapshot(snapshot: Any) -> dict[str, Any]:
    payload = snapshot.model_dump(mode="json")
    payload["target_url"] = payload.pop("masked_target")
    payload["fresh"] = payload.pop("observation_fresh")
    payload["hardware_fresh"] = payload.pop("hardware_observation_fresh")
    payload["hardware_stale"] = payload.pop("hardware_observation_stale")
    return payload


def _safe_profile(runtime: BioXpRuntime) -> dict[str, Any]:
    try:
        profile = runtime.connection.load_profile()
    except ProfileStoreError as exc:
        return {
            "configured": True,
            "valid": False,
            "display_name": None,
            "target_url": None,
            "detail": str(exc),
        }
    if profile is None:
        return {
            "configured": False,
            "valid": True,
            "display_name": None,
            "target_url": None,
        }
    return {
        "configured": True,
        "valid": True,
        "display_name": profile.display_name,
        "target_url": mask_target_url(profile.api_url),
    }


@router.get("/profile")
async def get_profile(runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> dict[str, Any]:
    return _safe_profile(runtime)


@router.put("/profile")
async def put_profile(
    profile: BioXpProfile,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    try:
        await runtime.connection.save_profile(profile)
    except (TargetPolicyError, ProfileStoreError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _safe_profile(runtime)


@router.delete("/profile")
async def delete_profile(runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> dict[str, bool]:
    await runtime.connection.forget_profile()
    return {"forgotten": True}


@router.get("/status")
async def get_status(runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> dict[str, Any]:
    snapshot = runtime.connection.snapshot()
    return {
        "connection": _public_snapshot(snapshot),
        "startup_warnings": list(runtime.startup_warnings),
        "connection_access": {
            "enabled": bioxp_connection_enabled(),
            "server_setting": "BMS_BIOXP_CONNECTION_ENABLED=1",
            "hardware_effects_authorized": False,
        },
        "mutation_access": {
            "enabled": mutations_enabled(),
            "server_setting": "BMS_BIOXP_MUTATIONS_ENABLED=1",
            "secret_required": False,
        },
        "legacy_job_migration": runtime.legacy_jobs.model_dump(),
    }


@router.post("/connection/connect")
async def connect(runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> dict[str, Any]:
    try:
        snapshot = await runtime.connection.connect()
    except (ConnectionStateError, ProfileStoreError, TargetPolicyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _public_snapshot(snapshot)


@router.post("/connection/disconnect")
async def disconnect(runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> dict[str, Any]:
    return _public_snapshot(await runtime.connection.disconnect())


@router.post("/connection/probe")
async def probe(runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> dict[str, Any]:
    try:
        snapshot = await runtime.connection.probe_status_only()
    except (ConnectionStateError, TargetPolicyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _public_snapshot(snapshot)


@router.post("/connection/recover-motion-non-homing")
async def recover_motion_non_homing(
    request: RecoverMotionRequest,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    """Typed thin relay; the robot remains the sole recovery authority."""
    try:
        result = await runtime.connection.request_active(
            "recover_motion_non_homing",
            expected_generation=request.expected_generation,
            require_fresh=True,
            json_data={
                "run_homing": False,
                "operator_ack": "RECOVER_MOTION",
                "operator_reason": request.operator_reason,
            },
        )
        await runtime.connection.probe_status_only()
    except RobotResponseError as exc:
        status = exc.status_code if 400 <= exc.status_code < 500 else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except ConnectionStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RobotTransportError, TargetPolicyError) as exc:
        raise HTTPException(status_code=502, detail=str(exc) or exc.__class__.__name__) from exc
    return result
