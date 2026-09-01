from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from services.bioxp.errors import (
    ConnectionStateError,
    ProfileStoreError,
    TargetPolicyError,
)
from services.bioxp.connection import mask_target_url
from services.bioxp.models import BioXpFreshnessSettings, BioXpProfile
from services.bioxp.runtime import BioXpRuntime, bioxp_connection_enabled

from .dependencies import get_bioxp_runtime, mutations_enabled, require_bioxp_mutation_access

router = APIRouter(dependencies=[Depends(require_bioxp_mutation_access)])


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
            "freshness_budget_seconds": runtime.connection.freshness_budget_seconds,
            "detail": str(exc),
        }
    if profile is None:
        return {
            "configured": False,
            "valid": True,
            "display_name": None,
            "target_url": None,
            "freshness_budget_seconds": runtime.connection.freshness_budget_seconds,
        }
    return {
        "configured": True,
        "valid": True,
        "display_name": profile.display_name,
        "target_url": mask_target_url(profile.api_url),
        "freshness_budget_seconds": profile.freshness_budget_seconds,
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


@router.put("/settings/freshness")
async def put_freshness_settings(
    settings: BioXpFreshnessSettings,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    """Change only BMS observation expiry; no robot command or reconnect occurs."""
    try:
        snapshot = await runtime.connection.set_freshness_budget_seconds(
            settings.freshness_budget_seconds,
        )
    except (ConnectionStateError, ProfileStoreError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _public_snapshot(snapshot)


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
