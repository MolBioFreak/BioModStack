from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, ValidationError

from services.bioxp.errors import ConnectionStateError, RobotResponseError, RobotTransportError
from services.bioxp.runtime import BioXpRuntime

from .dependencies import get_bioxp_runtime, require_bioxp_mutation_access

router = APIRouter()


class FullLifecyclePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_generation: StrictInt = Field(ge=1)
    expected_machine_serial: Literal[206]
    expected_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_evidence_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


class FullLifecycleCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_generation: StrictInt = Field(ge=1)
    expected_machine_serial: Literal[206]
    expected_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_evidence_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _LifecycleInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ownership_generation: StrictInt = Field(ge=0)
    can_ready: StrictBool
    board_test_mode: StrictBool
    pipette_exists: StrictBool | None
    initialize_system_producer: Literal["initializeEnvironment"]
    update_check_suppresses_initialize_system: StrictBool
    system_in_motion_at_entry: StrictBool
    enclosure_door_closed: StrictBool
    latch_closed: StrictBool
    saved_status: StrictInt
    ship_mode: Literal["", "PARK"]
    start_mode: Literal["DevMode", "WebMode", "LocalMode", "TradeShowMode"]
    tip_present: StrictBool
    self_test_due: StrictBool
    check_camera: StrictBool
    camera_installed: StrictBool
    is_development_machine: StrictBool
    deck_inspection: StrictBool


class _CanonicalPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: Literal["initialize_oem_movement_lifecycle"]
    operator_ack: Literal["INITIALIZE"]
    expected_generation: StrictInt = Field(ge=1)
    bms_connection_generation: StrictInt = Field(ge=1)
    expected_machine_serial: Literal[206]
    expected_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_evidence_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    mode: Literal["dry_run"]
    inputs: _LifecycleInputs


class _LifecycleStage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage_id: str = Field(min_length=1)
    source_anchor: str = Field(min_length=1)
    branch: str | None
    movement_ledger_stage: str | None
    status: Literal["pending", "running", "dry_run_simulated", "admitted", "acknowledged", "blocked", "cancelled"]
    would_command_hardware: StrictBool
    would_command_physical_motion: StrictBool
    physical_motion_commanded: StrictBool
    controller_acknowledged: StrictBool
    postcondition_verified: StrictBool
    physical_effect_verified: StrictBool
    started_at: StrictFloat | None = Field(default=None, ge=0)
    completed_at: StrictFloat | None = Field(default=None, ge=0)
    blocked_reason: str | None
    cleanup: str | None = None
    execution_semantics: str | None = None
    host_state: Literal["m_systemInmotion=true", "m_systemInmotion=false"] | None = None
    join_timeout_ms: StrictInt | None = Field(default=None, ge=1)
    movement_ledger_schema: Literal["bioxp.oem_initialize_motors_ledger.v1"] | None = None
    movement_ledger_stage_id: str | None = None
    parallel_branches: list[Literal["TC", "RC", "OC"]] | None = None
    producer: Literal["initializeEnvironment"] | None = None
    result_semantics: Literal["return_value_ignored_by_oem_caller"] | None = None
    source_predicate: str | None = None


class _SafetyDeviation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deviation_id: str
    oem_semantics: str
    linux_safety_policy: str
    live_execution_blocked: StrictBool


class _LifecycleRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    run_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    command: Literal["initialize_oem_movement_lifecycle"]
    idempotency_key: str
    request: _CanonicalPlanRequest
    run_state: Literal["planned", "running", "dry_run_non_ready", "cancelled", "blocked"]
    terminal_state: str | None
    planned_terminal_state: str
    current_stage: str | None
    expected_next_stage: str | None
    blocked_reason: str | None
    source_authority_verified: StrictBool
    configuration_verified: StrictBool
    evidence_lock_verified: StrictBool
    source_registry_identity_verified: StrictBool
    machine_configuration_verified: StrictBool
    transport_owner_verified: StrictBool
    controller_acknowledged: StrictBool
    postcondition_verified: StrictBool
    physical_motion_commanded: StrictBool
    physical_effect_verified: StrictBool
    safety_deviation: list[_SafetyDeviation]
    registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_lock_path: str
    evidence_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_lock_schema: Literal["bioxp.oem_evidence_lock.v4"]
    evidence_lock_identity_verified: StrictBool
    acquisition_id: str
    machine_serial: Literal[206]
    ownership_generation: StrictInt = Field(ge=0)
    transport_frames: list[dict[str, Any]]
    stages: list[_LifecycleStage] = Field(min_length=1)
    sequence: StrictInt = Field(ge=1)
    created_at: StrictFloat = Field(ge=0)
    updated_at: StrictFloat = Field(ge=0)


class _ContractProvider(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_contract: bool
    implemented: bool | Literal["receipt_evaluator", "typed_plan"]
    live_bound: bool
    commissioned: bool


class _ContractSafetyBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    caller_supplied_motion_parameters: Literal[False]
    dry_run_commands_hardware: Literal[False]
    queue_acceptance_is_execution: Literal[False]
    physical_effect_verified: Literal[False]


class _InitializeSystemProducer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    producer: str
    source_anchor: str


class _SafeLifecycleContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["bioxp.oem_full_lifecycle_contract.v1"]
    command: Literal["initialize_oem_movement_lifecycle"]
    machine_serial: Literal[206]
    ownership_generation: StrictInt | None
    registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_lock_path: str = Field(exclude=True)
    evidence_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_lock_schema: Literal["bioxp.oem_evidence_lock.v4"]
    evidence_lock_identity_verified: bool
    acquisition_id: str
    evidence_lock_verified: bool
    source_registry_identity_verified: bool
    machine_configuration_verified: bool
    source_authority_verified: Literal[False]
    initialize_system_producers: list[_InitializeSystemProducer]
    plan_available: bool
    plan_blockers: list[str]
    live_creation_enabled: Literal[False]
    physical_commissioning_complete: Literal[False]
    providers: dict[str, _ContractProvider]
    safety_boundary: _ContractSafetyBoundary


def _validate_contract_authority(contract: dict[str, Any], request: FullLifecyclePlanRequest) -> None:
    if contract.get("machine_serial") != request.expected_machine_serial:
        raise HTTPException(status_code=409, detail="Robot lifecycle contract machine identity changed")
    if contract.get("registry_sha256") != request.expected_registry_sha256:
        raise HTTPException(status_code=409, detail="Robot lifecycle contract registry identity changed")
    if contract.get("evidence_lock_sha256") != request.expected_evidence_lock_sha256:
        raise HTTPException(status_code=409, detail="Robot lifecycle contract evidence-lock identity changed")
    if (
        contract.get("evidence_lock_verified") is not True
        or contract.get("source_registry_identity_verified") is not True
        or contract.get("machine_configuration_verified") is not True
    ):
        raise HTTPException(
            status_code=502,
            detail="BioXP robot did not verify its lifecycle evidence and machine configuration authorities",
        )
    if contract.get("live_creation_enabled") is not False or contract.get("physical_commissioning_complete") is not False:
        raise HTTPException(status_code=502, detail="BioXP robot returned an unsafe lifecycle contract")


def _validate_contract_for_plan(contract: dict[str, Any], request: FullLifecyclePlanRequest) -> None:
    _validate_contract_authority(contract, request)
    if contract.get("plan_available") is not True:
        blockers = contract.get("plan_blockers")
        detail = "; ".join(str(item) for item in blockers) if isinstance(blockers, list) else "robot lifecycle plan is unavailable"
        raise HTTPException(status_code=409, detail=detail)


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


def _parse_lifecycle_run(response: dict[str, Any]) -> _LifecycleRun:
    try:
        run = _LifecycleRun.model_validate(response)
    except ValidationError as exc:
        raise HTTPException(status_code=502, detail="BioXP robot returned a malformed full-lifecycle run") from exc
    if run.transport_frames != []:
        raise HTTPException(status_code=502, detail="BioXP robot returned unexpected transport frames")
    return run


def _parse_safe_contract(response: dict[str, Any]) -> dict[str, Any]:
    try:
        contract = _SafeLifecycleContract.model_validate(response)
    except ValidationError as exc:
        raise HTTPException(status_code=502, detail="BioXP robot returned an unsafe or malformed lifecycle contract") from exc
    return contract.model_dump()


def _validate_planned_run(
    response: dict[str, Any],
    *,
    outbound: dict[str, Any],
    expected_machine_serial: int,
    expected_registry_sha256: str,
    expected_evidence_lock_sha256: str,
) -> None:
    run = _parse_lifecycle_run(response)
    canonical_echo = run.request.model_dump(exclude={"inputs"})
    if canonical_echo != outbound:
        raise HTTPException(status_code=502, detail="BioXP robot returned a mismatched lifecycle request echo")
    if (
        run.run_state != "planned"
        or run.terminal_state is not None
        or run.source_authority_verified is not False
        or run.configuration_verified is not False
        or run.transport_owner_verified is not False
        or run.physical_motion_commanded is not False
        or run.physical_effect_verified is not False
        or run.machine_serial != expected_machine_serial
        or run.registry_sha256 != expected_registry_sha256
        or run.evidence_lock_sha256 != expected_evidence_lock_sha256
        or run.evidence_lock_verified is not True
        or run.evidence_lock_identity_verified is not True
        or run.source_registry_identity_verified is not True
        or run.machine_configuration_verified is not True
        or run.controller_acknowledged is not False
        or run.postcondition_verified is not False
        or run.ownership_generation != outbound["expected_generation"]
        or run.request.inputs.ownership_generation != outbound["expected_generation"]
        or any(
            stage.status != "pending"
            or
            stage.physical_motion_commanded
            or stage.controller_acknowledged
            or stage.postcondition_verified
            or stage.physical_effect_verified
            for stage in run.stages
        )
    ):
        raise HTTPException(status_code=502, detail="BioXP robot returned an unsafe full-lifecycle plan")


async def _leased_robot_request(
    runtime: BioXpRuntime,
    route_name: str,
    *,
    expected_generation: int,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        response = await runtime.connection.request_active(
            route_name,
            expected_generation=expected_generation,
            require_fresh=True,
            **kwargs,
        )
    except ConnectionStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    return _parse_safe_contract(await _robot_request(client, "oem_full_lifecycle_contract"))


@router.post(
    "/oem-full-lifecycle/runs",
    dependencies=[Depends(require_bioxp_mutation_access)],
)
async def plan_full_lifecycle(
    request: FullLifecyclePlanRequest,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    contract = _parse_safe_contract(await _leased_robot_request(
        runtime, "oem_full_lifecycle_contract", expected_generation=request.expected_generation,
    ))
    _validate_contract_for_plan(contract, request)
    robot_generation = contract.get("ownership_generation")
    if type(robot_generation) is not int or robot_generation < 1:
        raise HTTPException(status_code=502, detail="BioXP robot returned an invalid ownership generation")
    payload = {
        "command": "initialize_oem_movement_lifecycle",
        "operator_ack": "INITIALIZE",
        "expected_generation": robot_generation,
        "bms_connection_generation": request.expected_generation,
        "expected_machine_serial": request.expected_machine_serial,
        "expected_registry_sha256": request.expected_registry_sha256,
        "expected_evidence_lock_sha256": request.expected_evidence_lock_sha256,
        "idempotency_key": request.idempotency_key,
        "mode": "dry_run",
    }
    response = await _leased_robot_request(
        runtime,
        "plan_oem_full_lifecycle",
        expected_generation=request.expected_generation,
        json_data=payload,
    )
    _validate_planned_run(
        response,
        outbound=payload,
        expected_machine_serial=request.expected_machine_serial,
        expected_registry_sha256=request.expected_registry_sha256,
        expected_evidence_lock_sha256=request.expected_evidence_lock_sha256,
    )
    return response


_RUN_ID = Path(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


@router.get("/oem-full-lifecycle/runs/{run_id}")
async def get_full_lifecycle_run(
    run_id: str = _RUN_ID,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    client = _active_client(runtime)
    response = await _robot_request(client, "get_oem_full_lifecycle_run", path_params={"run_id": run_id})
    run = _parse_lifecycle_run(response)
    if run.run_id != run_id:
        raise HTTPException(status_code=502, detail="BioXP robot returned a mismatched lifecycle run")
    return response


@router.get("/oem-full-lifecycle/runs/{run_id}/ledger")
async def get_full_lifecycle_ledger(
    run_id: str = _RUN_ID,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    client = _active_client(runtime)
    response = await _robot_request(client, "get_oem_full_lifecycle_ledger", path_params={"run_id": run_id})
    run = _parse_lifecycle_run(response)
    if run.run_id != run_id:
        raise HTTPException(status_code=502, detail="BioXP robot returned a mismatched lifecycle ledger")
    return response


@router.post(
    "/oem-full-lifecycle/runs/{run_id}/cancel",
    dependencies=[Depends(require_bioxp_mutation_access)],
)
async def cancel_full_lifecycle_run(
    request: FullLifecycleCancelRequest,
    run_id: str = _RUN_ID,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> dict[str, Any]:
    admitted_response = await _leased_robot_request(
        runtime,
        "get_oem_full_lifecycle_run",
        expected_generation=request.expected_generation,
        path_params={"run_id": run_id},
    )
    admitted = _parse_lifecycle_run(admitted_response)
    _validate_planned_run(
        admitted_response,
        outbound=admitted.request.model_dump(exclude={"inputs"}),
        expected_machine_serial=request.expected_machine_serial,
        expected_registry_sha256=request.expected_registry_sha256,
        expected_evidence_lock_sha256=request.expected_evidence_lock_sha256,
    )
    if (
        admitted.run_id != run_id
        or admitted.request.bms_connection_generation != request.expected_generation
        or admitted.machine_serial != request.expected_machine_serial
        or admitted.registry_sha256 != request.expected_registry_sha256
        or admitted.evidence_lock_sha256 != request.expected_evidence_lock_sha256
    ):
        raise HTTPException(status_code=409, detail="Lifecycle cancellation authority does not match the admitted run")
    cancel_payload = {
        "expected_generation": admitted.request.expected_generation,
        "bms_connection_generation": request.expected_generation,
        "expected_machine_serial": request.expected_machine_serial,
        "expected_registry_sha256": request.expected_registry_sha256,
        "expected_evidence_lock_sha256": request.expected_evidence_lock_sha256,
    }
    response = await _leased_robot_request(
        runtime,
        "cancel_oem_full_lifecycle_run",
        expected_generation=request.expected_generation,
        path_params={"run_id": run_id},
        json_data=cancel_payload,
    )
    run = _parse_lifecycle_run(response)
    admitted_canonical = admitted.model_dump()
    cancelled_canonical = run.model_dump()
    for mutable_field in ("run_state", "terminal_state", "expected_next_stage", "updated_at", "sequence"):
        admitted_canonical.pop(mutable_field)
        cancelled_canonical.pop(mutable_field)
    if (
        run.run_id != run_id
        or run.run_state != "cancelled"
        or run.terminal_state != "cancelled"
        or run.expected_next_stage is not None
        or cancelled_canonical != admitted_canonical
        or run.source_authority_verified is not False
        or run.configuration_verified is not False
        or run.transport_owner_verified is not False
        or run.controller_acknowledged is not False
        or run.postcondition_verified is not False
        or run.physical_motion_commanded is not False
        or run.physical_effect_verified is not False
        or run.machine_serial != request.expected_machine_serial
        or run.registry_sha256 != request.expected_registry_sha256
        or run.evidence_lock_sha256 != request.expected_evidence_lock_sha256
        or run.evidence_lock_verified is not True
        or run.evidence_lock_identity_verified is not True
        or run.source_registry_identity_verified is not True
        or run.machine_configuration_verified is not True
        or run.request.expected_generation != admitted.request.expected_generation
        or run.request.bms_connection_generation != request.expected_generation
        or run.request.inputs.ownership_generation != admitted.request.expected_generation
        or run.ownership_generation != admitted.request.expected_generation
        or run.request.expected_machine_serial != request.expected_machine_serial
        or run.request.expected_registry_sha256 != request.expected_registry_sha256
        or run.request.expected_evidence_lock_sha256 != request.expected_evidence_lock_sha256
        or any(
            stage.status != "pending"
            or
            stage.physical_motion_commanded
            or stage.controller_acknowledged
            or stage.postcondition_verified
            or stage.physical_effect_verified
            for stage in run.stages
        )
    ):
        raise HTTPException(status_code=502, detail="BioXP robot returned an unsafe or malformed lifecycle cancellation")
    return response
