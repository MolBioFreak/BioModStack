from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field

from services.bioxp.errors import RobotResponseError, RobotTransportError
from services.bioxp.runtime import BioXpRuntime

from .dependencies import get_bioxp_runtime, require_bioxp_mutation_access

router = APIRouter()


class FullLifecyclePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_generation: int = Field(ge=1)
    expected_machine_serial: Literal[206]
    expected_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_evidence_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


class FullLifecycleCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_generation: int = Field(ge=1)


def _validate_contract_for_plan(contract: dict[str, Any], request: FullLifecyclePlanRequest) -> None:
    if contract.get("plan_available") is not True:
        blockers = contract.get("plan_blockers")
        detail = "; ".join(str(item) for item in blockers) if isinstance(blockers, list) else "robot lifecycle plan is unavailable"
        raise HTTPException(status_code=409, detail=detail)
    if contract.get("machine_serial") != request.expected_machine_serial:
        raise HTTPException(status_code=409, detail="Robot lifecycle contract machine identity changed")
    if contract.get("registry_sha256") != request.expected_registry_sha256:
        raise HTTPException(status_code=409, detail="Robot lifecycle contract registry identity changed")
    if contract.get("evidence_lock_sha256") != request.expected_evidence_lock_sha256:
        raise HTTPException(status_code=409, detail="Robot lifecycle contract evidence-lock identity changed")
    if contract.get("source_authority_verified") is not True:
        raise HTTPException(status_code=502, detail="BioXP robot did not verify its lifecycle source authority")
    if contract.get("live_creation_enabled") is not False or contract.get("physical_commissioning_complete") is not False:
        raise HTTPException(status_code=502, detail="BioXP robot returned an unsafe lifecycle contract")


def _active_client(runtime: BioXpRuntime, *, expected_generation: int | None = None, require_fresh: bool = False):
    snapshot = runtime.connection.snapshot()
    client = runtime.connection.active_client
    if snapshot.active is not True or client is None:
        raise HTTPException(status_code=409, detail="An active BioXP target connection is required")
    if expected_generation is not None and snapshot.generation != expected_generation:
        raise HTTPException(status_code=409, detail="Expected connection generation does not match the active generation")
    if require_fresh and snapshot.observation_fresh is not True:
        raise HTTPException(status_code=409, detail="A fresh process-local BioXP status observation is required")
    return client


async def _robot_request(client: Any, route_name: str, **kwargs: Any) -> dict[str, Any]:
    try:
        response = await client.request(route_name, **kwargs)
    except RobotResponseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except RobotTransportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not isinstance(response, dict):
        raise HTTPException(status_code=502, detail="BioXP robot returned a non-object full-lifecycle response")
    return response


@router.get("/oem-full-lifecycle/contract")
async def get_full_lifecycle_contract(runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> dict[str, Any]:
    client = _active_client(runtime)
    return await _robot_request(client, "oem_full_lifecycle_contract")


@router.post(
    "/oem-full-lifecycle/runs",
    dependencies=[Depends(require_bioxp_mutation_access)],
)
async def plan_full_lifecycle(
    request: FullLifecyclePlanRequest,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    client = _active_client(runtime, expected_generation=request.expected_generation, require_fresh=True)
    contract = await _robot_request(client, "oem_full_lifecycle_contract")
    _validate_contract_for_plan(contract, request)
    payload = {
        "command": "initialize_oem_movement_lifecycle",
        "operator_ack": "INITIALIZE",
        "expected_machine_serial": request.expected_machine_serial,
        "expected_registry_sha256": request.expected_registry_sha256,
        "idempotency_key": request.idempotency_key,
        "mode": "dry_run",
    }
    response = await _robot_request(client, "plan_oem_full_lifecycle", json_data=payload)
    if (
        response.get("mode") != "dry_run"
        or response.get("run_state") != "planned"
        or response.get("physical_command_sent") is not False
        or response.get("physical_effect_verified") is not False
    ):
        raise HTTPException(status_code=502, detail="BioXP robot returned an unsafe or malformed full-lifecycle plan")
    return response


_RUN_ID = Path(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


@router.get("/oem-full-lifecycle/runs/{run_id}")
async def get_full_lifecycle_run(
    run_id: str = _RUN_ID,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    client = _active_client(runtime)
    return await _robot_request(client, "get_oem_full_lifecycle_run", path_params={"run_id": run_id})


@router.get("/oem-full-lifecycle/runs/{run_id}/ledger")
async def get_full_lifecycle_ledger(
    run_id: str = _RUN_ID,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    client = _active_client(runtime)
    return await _robot_request(client, "get_oem_full_lifecycle_ledger", path_params={"run_id": run_id})


@router.post(
    "/oem-full-lifecycle/runs/{run_id}/cancel",
    dependencies=[Depends(require_bioxp_mutation_access)],
)
async def cancel_full_lifecycle_run(
    request: FullLifecycleCancelRequest,
    run_id: str = _RUN_ID,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    client = _active_client(runtime, expected_generation=request.expected_generation, require_fresh=True)
    response = await _robot_request(
        client,
        "cancel_oem_full_lifecycle_run",
        path_params={"run_id": run_id},
    )
    if (
        response.get("run_id") != run_id
        or response.get("run_state") != "cancelled"
        or response.get("physical_command_sent") is not False
        or response.get("physical_effect_verified") is not False
    ):
        raise HTTPException(status_code=502, detail="BioXP robot returned an unsafe or malformed lifecycle cancellation")
    return response
