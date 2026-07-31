from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from services.bioxp.errors import ConnectionStateError, ProfileStoreError, TargetPolicyError
from services.bioxp.connection import mask_target_url
from services.bioxp.command_policy import (
    lifecycle_stage_reasons,
    maintenance_state_reasons,
    ownership_state_reasons,
    required_lifecycle_state_reasons,
)
from services.bioxp.models import BioXpProfile
from services.bioxp.operator_semantic_quarantine import EMERGENCY_STOP_QUARANTINE_REASON
from services.bioxp.runtime import BioXpRuntime, bioxp_connection_enabled

from .dependencies import (
    get_bioxp_runtime,
    mutations_enabled,
    require_bioxp_mutation_access,
)

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
    available: list[str] = []
    unavailable: dict[str, str] = {}
    for name, definition in runtime.commands.registry.items():
        reason: str | None = None
        if not definition.enabled or definition.route_key is None:
            reason = definition.disabled_reason or "robot mapping is disabled"
        elif not mutations_enabled():
            reason = "mutations are disabled"
        elif not snapshot.active:
            reason = "connection is not active"
        elif snapshot.command_active and name != "stop_axis_diagnostic":
            reason = "another normal command is active"
        elif maintenance_reasons := maintenance_state_reasons(definition, snapshot.maintenance_state):
            reason = "; ".join(maintenance_reasons)
        elif ownership_reasons := ownership_state_reasons(definition, snapshot.ownership):
            reason = "; ".join(ownership_reasons)
        elif required_reasons := required_lifecycle_state_reasons(
            definition, snapshot.startup_lifecycle
        ):
            reason = "; ".join(required_reasons)
        elif lifecycle_reasons := lifecycle_stage_reasons(definition, snapshot.startup_lifecycle):
            reason = "; ".join(lifecycle_reasons)
        elif definition.requires_fresh_observation and snapshot.observation_fresh is not True:
            reason = "fresh readiness evidence is unavailable"
        elif definition.requires_runtime_ready and snapshot.runtime_ready is not True:
            reason = "runtime is not ready"
        elif definition.requires_hardware_ready and snapshot.hardware_ready is not True:
            reason = "hardware is not ready"
        elif definition.requires_runtime_inactive and snapshot.runtime_ready is True:
            reason = "USB runtime is already active for the managed service"
        elif definition.required_capability is not None and definition.required_capability not in snapshot.capabilities:
            reason = f"capability {definition.required_capability!r} is unavailable"
        if reason is None:
            available.append(name)
        else:
            unavailable[name] = reason
    available_controls: list[str] = []
    return {
        "connection": _public_snapshot(snapshot),
        "available_commands": available,
        "available_controls": available_controls,
        "unavailable_commands": unavailable,
        "emergency_stop": {
            "delivery_available": False,
            "physical_effect_verifiable": False,
            "reason": EMERGENCY_STOP_QUARANTINE_REASON,
        },
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


@router.get("/logs")
async def get_local_logs(runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> dict[str, Any]:
    return {
        "scope": "bms-local-command-history",
        "remote_logs_collected": False,
        "entries": [entry.model_dump(mode="json") for entry in runtime.commands.history()],
    }
