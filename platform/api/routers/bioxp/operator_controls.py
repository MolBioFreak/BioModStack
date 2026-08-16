from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from services.bioxp.errors import ConnectionStateError, RobotResponseError, RobotTransportError
from services.bioxp.operator_models import (
    OperatorActionHistory,
    OperatorActionInvokeRequest,
    OperatorActionReceipt,
    OperatorAdmission,
    OperatorAdmissionRequest,
    OperatorAssessmentRequest,
    OperatorControlCatalog,
    OperatorDashboard,
)
from services.bioxp.operator_semantic_quarantine import (
    OPERATOR_SEMANTIC_QUARANTINE_BY_ACTION_ID,
    OPERATOR_SEMANTIC_QUARANTINE_BY_PATH,
)
from services.bioxp.runtime import BioXpRuntime

from .dependencies import get_bioxp_runtime, require_bioxp_mutation_access

router = APIRouter()


def _quarantine_catalog_payload(payload: Any) -> Any:
    if not isinstance(payload, dict) or not isinstance(payload.get("actions"), list):
        return payload
    transformed = dict(payload)
    actions: list[Any] = []
    for raw_row in payload["actions"]:
        if not isinstance(raw_row, dict):
            actions.append(raw_row)
            continue
        path = str(raw_row.get("informational_path") or "")
        if path == "/oem/startup/status/{session_id}":
            continue
        row = dict(raw_row)
        if path == "/oem/startup/door_event" and isinstance(row.get("inputs"), list):
            row["inputs"] = [input_row for input_row in row["inputs"] if not isinstance(input_row, dict) or input_row.get("name") != "session_id"]
        quarantine_reason = OPERATOR_SEMANTIC_QUARANTINE_BY_PATH.get(path)
        if quarantine_reason is not None:
            reason = quarantine_reason
            row.update({
                "provider_available": False,
                "provider_unavailable_reason": reason,
                "available": False,
                "unavailable_reason": reason,
                "enabled": False,
                "disabled_reason": reason,
            })
        actions.append(row)
    transformed["actions"] = actions
    return transformed


def _quarantined_admission(action_id: str, ownership_generation: int, reason: str) -> OperatorAdmission:
    return _validate(OperatorAdmission, {
        "action_id": action_id,
        "ownership_generation": ownership_generation,
        "enabled": False,
        "disabled_reason": reason,
        "dependencies": [{
            "key": "source_grounded_semantics",
            "label": "Source-grounded physical semantics",
            "met": False,
            "reason": reason,
        }],
    })


async def _resolve_action_quarantine(
    runtime: BioXpRuntime,
    *,
    action_id: str,
    expected_connection_generation: int,
    expected_ownership_generation: int,
) -> str | None:
    # Safety interrupts must not wait for a fresh full catalog while a motion
    # transaction is still holding the robot's provider-state lock. The robot
    # invocation still enforces the connection and ownership generations and
    # dispatches only the finite X/Z stop and aggregate-abort actions.
    if action_id in {"oem.x.stop", "oem.abort_all", "oem.z.stop", "oem.z.abort"}:
        return None
    try:
        payload = await runtime.connection.request_active_query(
            "operator_control_catalog",
            expected_generation=expected_connection_generation,
            require_fresh=True,
        )
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc
    catalog = _validate(OperatorControlCatalog, payload)
    if catalog.ownership_generation != expected_ownership_generation:
        raise HTTPException(
            status_code=409,
            detail=(
                "BioXP robot ownership generation changed: "
                f"expected {expected_ownership_generation}, current {catalog.ownership_generation}"
            ),
        )
    for action in catalog.actions:
        if action.action_id == action_id:
            return OPERATOR_SEMANTIC_QUARANTINE_BY_PATH.get(action.informational_path)
    return None


def _translate_robot_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RobotResponseError):
        status = exc.status_code if 400 <= exc.status_code < 500 else 502
        return HTTPException(
            status_code=status,
            detail={
                "error": "bioxp_robot_response_error",
                "robot_status": exc.status_code,
                "robot_detail": exc.detail,
            },
        )
    if isinstance(exc, ConnectionStateError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc) or exc.__class__.__name__)


def _validate(model: type[Any], payload: Any) -> Any:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=502, detail="BioXP robot returned an invalid operator-control contract") from exc


@router.get("/operator-controls/catalog", response_model=OperatorControlCatalog)
async def operator_control_catalog(
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> OperatorControlCatalog:
    snapshot = runtime.connection.snapshot()
    try:
        payload = await runtime.connection.request_active_query(
            "operator_control_catalog",
            expected_generation=snapshot.generation,
            require_fresh=True,
        )
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc
    return _validate(OperatorControlCatalog, _quarantine_catalog_payload(payload))


@router.get("/operator-controls/dashboard", response_model=OperatorDashboard)
async def operator_dashboard(
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> OperatorDashboard:
    snapshot = runtime.connection.snapshot()
    try:
        payload = await runtime.connection.request_active_query(
            "operator_dashboard",
            expected_generation=snapshot.generation,
            require_fresh=True,
        )
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc
    return _validate(OperatorDashboard, payload)


@router.post(
    "/operator-controls/actions/{action_id}/admission",
    response_model=OperatorAdmission,
    dependencies=[Depends(require_bioxp_mutation_access)],
)
async def operator_action_admission(
    action_id: str,
    request: OperatorAdmissionRequest,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> OperatorAdmission:
    quarantine_reason = await _resolve_action_quarantine(
        runtime,
        action_id=action_id,
        expected_connection_generation=request.expected_connection_generation,
        expected_ownership_generation=request.expected_ownership_generation,
    )
    if quarantine_reason is not None:
        return _quarantined_admission(
            action_id,
            request.expected_ownership_generation,
            quarantine_reason,
        )
    try:
        payload = await runtime.connection.request_active_query(
            "operator_action_admission",
            expected_generation=request.expected_connection_generation,
            require_fresh=True,
            path_params={"action_id": action_id},
            json_data={"expected_generation": request.expected_ownership_generation, "inputs": request.inputs},
        )
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc
    admission = _validate(OperatorAdmission, payload)
    if admission.action_id != action_id:
        raise HTTPException(status_code=502, detail="BioXP robot returned a mismatched action admission")
    return admission


@router.post(
    "/operator-controls/actions/{action_id}",
    response_model=OperatorActionReceipt,
    dependencies=[Depends(require_bioxp_mutation_access)],
)
async def invoke_operator_action(
    action_id: str,
    request: OperatorActionInvokeRequest,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> OperatorActionReceipt:
    quarantine_reason = OPERATOR_SEMANTIC_QUARANTINE_BY_ACTION_ID.get(action_id)
    if quarantine_reason is not None:
        raise HTTPException(status_code=409, detail=quarantine_reason)
    action_payload = {
        "expected_generation": request.expected_ownership_generation,
        "idempotency_key": request.idempotency_key,
        "inputs": request.inputs,
    }
    try:
        if action_id in {"oem.x.stop", "oem.abort_all", "oem.z.stop", "oem.z.abort"}:
            payload = await runtime.connection.request_active_safety_interrupt(
                "invoke_operator_action",
                expected_generation=request.expected_connection_generation,
                path_params={"action_id": action_id},
                json_data=action_payload,
            )
        else:
            payload = await runtime.connection.request_active(
                "invoke_operator_action",
                expected_generation=request.expected_connection_generation,
                require_fresh=True,
                path_params={"action_id": action_id},
                json_data=action_payload,
            )
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc
    receipt = _validate(OperatorActionReceipt, payload)
    if receipt.action_id != action_id or receipt.idempotency_key != request.idempotency_key:
        raise HTTPException(status_code=502, detail="BioXP robot returned a mismatched operator-action receipt")
    return receipt


@router.get("/operator-controls/history", response_model=OperatorActionHistory)
async def operator_action_history(
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> OperatorActionHistory:
    snapshot = runtime.connection.snapshot()
    try:
        payload = await runtime.connection.request_active_query(
            "operator_action_history",
            expected_generation=snapshot.generation,
            require_fresh=True,
        )
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc
    return _validate(OperatorActionHistory, payload)


@router.get("/operator-controls/receipts/{command_id}", response_model=OperatorActionReceipt)
async def operator_action_receipt(
    command_id: str,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> OperatorActionReceipt:
    snapshot = runtime.connection.snapshot()
    try:
        payload = await runtime.connection.request_active_query(
            "operator_action_receipt",
            expected_generation=snapshot.generation,
            require_fresh=True,
            path_params={"command_id": command_id},
        )
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc
    receipt = _validate(OperatorActionReceipt, payload)
    if receipt.command_id != command_id:
        raise HTTPException(status_code=502, detail="BioXP robot returned a mismatched command receipt")
    return receipt


@router.post(
    "/operator-controls/receipts/{command_id}/assessment",
    response_model=OperatorActionReceipt,
    dependencies=[Depends(require_bioxp_mutation_access)],
)
async def assess_operator_action(
    command_id: str,
    request: OperatorAssessmentRequest,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> OperatorActionReceipt:
    try:
        payload = await runtime.connection.request_active(
            "assess_operator_action",
            expected_generation=request.expected_connection_generation,
            require_fresh=True,
            path_params={"command_id": command_id},
            json_data={
                "expected_generation": request.expected_ownership_generation,
                "idempotency_key": request.idempotency_key,
                "verdict": request.verdict,
                "note": request.note,
            },
        )
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc
    receipt = _validate(OperatorActionReceipt, payload)
    if receipt.command_id != command_id:
        raise HTTPException(status_code=502, detail="BioXP robot returned a mismatched assessed receipt")
    return receipt
