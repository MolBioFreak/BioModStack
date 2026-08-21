from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
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
    OperatorActionReceiptV2,
    OperatorActionReceiptDetailV2,
    OperatorControlCatalogV2,
    OperatorDashboardV2,
    OperatorActionHistoryV2,
    OperatorDashboard,
    PipetteReadbackRequest,
    PipetteReadbackResponse,
    PipetteApplicationPlanRequest,
    PipetteApplicationPlanResponse,
    PipetteApplicationStatus,
    OperatorReportSummaryV1,
    OperatorReportCommandPageV1,
    OperatorReportCommandDetailV1,
    OperatorReportTransitionsV1,
    OperatorReportPipettePageV1,
    OperatorReportPipetteDetailV1,
    OperatorReportPipetteChannelsV1,
    OperatorReportPipetteExchangesV1,
    OperatorReportEventsV1,
    OperatorReportEventV1,
    OperatorReportPressureStreamsV1,
    OperatorReportPressureDetailV1,
    OperatorReportPressureSamplesV1,
    OperatorReportExportV1,
    OperatorReportAuditHealthV1,
    OperatorReportExportRequestV1,
    OperatorReportExportMetadataV1,
)
from services.bioxp.operator_semantic_quarantine import (
    OPERATOR_SEMANTIC_QUARANTINE_BY_ACTION_ID,
    OPERATOR_SEMANTIC_QUARANTINE_BY_PATH,
)
from services.bioxp.runtime import BioXpRuntime

from .dependencies import get_bioxp_runtime, require_bioxp_mutation_access

router = APIRouter()


def _report_params(
    *,
    status: str | None = None,
    operation: str | None = None,
    action: str | None = None,
    channel: int | None = Query(default=None, ge=0, le=3),
    entrypoint: str | None = None,
    caller_class: str | None = None,
    control_class: str | None = None,
    protocol_job_id: str | None = None,
    protocol_action_id: str | None = None,
    lifecycle_stage_id: str | None = None,
    outcome: str | None = None,
    event_source: str | None = None,
    pressure_stream_id: str | None = None,
    delivery_verified: bool | None = None,
    controller_acknowledged: bool | None = None,
    completion_verified: bool | None = None,
    hardware_postcondition_verified: bool | None = None,
    physical_effect_verified: bool | None = None,
    evidence_state: str | None = None,
    command_id: str | None = None,
    pipette_operation_id: str | None = None,
    connection_generation: int | None = Query(default=None, ge=0),
    ownership_generation: int | None = Query(default=None, ge=0),
    event_kind: str | None = None,
    start: float | None = None,
    end: float | None = None,
    limit: int | None = Query(default=100, ge=1, le=1000),
    cursor: str | None = None,
) -> dict[str, Any]:
    values = {
        "status": status,
        "operation": operation,
        "action": action,
        "channel": channel,
        "entrypoint": entrypoint,
        "caller_class": caller_class,
        "control_class": control_class,
        "protocol_job_id": protocol_job_id,
        "protocol_action_id": protocol_action_id,
        "lifecycle_stage_id": lifecycle_stage_id,
        "outcome": outcome,
        "event_source": event_source,
        "pressure_stream_id": pressure_stream_id,
        "delivery_verified": delivery_verified,
        "controller_acknowledged": controller_acknowledged,
        "completion_verified": completion_verified,
        "hardware_postcondition_verified": hardware_postcondition_verified,
        "physical_effect_verified": physical_effect_verified,
        "evidence_state": evidence_state,
        "command_id": command_id,
        "pipette_operation_id": pipette_operation_id,
        "connection_generation": connection_generation,
        "ownership_generation": ownership_generation,
        "event_kind": event_kind,
        "start": start,
        "end": end,
        "limit": limit,
        "cursor": cursor,
    }
    return {key: value for key, value in values.items() if value is not None}


async def _proxy_operator_report(
    runtime: BioXpRuntime,
    route_name: str,
    *,
    params: dict[str, Any] | None = None,
    path_params: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        snapshot = runtime.connection.snapshot()
        payload = await runtime.connection.request_active_query(
            route_name,
            expected_generation=snapshot.generation,
            require_fresh=True,
            params=params,
            path_params=path_params,
        )
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="BioXP robot returned an invalid report contract")
    return payload


async def _proxy_operator_report_model(
    runtime: BioXpRuntime,
    route_name: str,
    model: type[Any],
    *,
    params: dict[str, Any] | None = None,
    path_params: dict[str, str] | None = None,
) -> Any:
    return _validate(model, await _proxy_operator_report(runtime, route_name, params=params, path_params=path_params))


def _bms_export_download(model: Any, export_id: str) -> Any:
    return model.model_copy(update={"download": f"/api/bioxp/operator-controls/reports/exports/{export_id}/download"})


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


def _resolve_action_quarantine(action_id: str) -> str | None:
    # Safety interrupts must not wait for a fresh full catalog while a motion
    # transaction is still holding the robot's provider-state lock. The robot
    # invocation still enforces the connection and ownership generations and
    # dispatches only the finite X/Z stop and aggregate-abort actions.
    if action_id in {"oem.x.stop", "oem.abort_all", "oem.z.stop", "oem.z.abort", "oem.y.stop"}:
        return None
    # The action-ID quarantine registry is the local authority for these two
    # routes. The robot admission request below still enforces both connection
    # and ownership generations. Avoid a full live catalog read before every
    # admission because the cockpit requests several signed X admissions at
    # once and the catalog itself acquires the robot provider-state lock.
    return OPERATOR_SEMANTIC_QUARANTINE_BY_ACTION_ID.get(action_id)


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


@router.get("/operator-controls/v2/catalog", response_model=OperatorControlCatalogV2)
async def operator_control_catalog_v2(
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> OperatorControlCatalogV2:
    snapshot = runtime.connection.snapshot()
    try:
        payload = await runtime.connection.request_active_query(
            "operator_control_catalog_v2",
            expected_generation=snapshot.generation,
            require_fresh=True,
        )
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc
    return _validate(OperatorControlCatalogV2, payload)


@router.get("/operator-controls/v2/dashboard", response_model=OperatorDashboardV2)
async def operator_dashboard_v2(
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> OperatorDashboardV2:
    snapshot = runtime.connection.snapshot()
    try:
        payload = await runtime.connection.request_active_query(
            "operator_dashboard_v2",
            expected_generation=snapshot.generation,
            require_fresh=True,
        )
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc
    return _validate(OperatorDashboardV2, payload)


@router.post(
    "/operator-controls/v2/actions/{action_id}",
    response_model=OperatorActionReceiptV2,
    dependencies=[Depends(require_bioxp_mutation_access)],
)
async def invoke_operator_action_v2(
    action_id: str,
    request: OperatorActionInvokeRequest,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> OperatorActionReceiptV2:
    action_payload = {"expected_generation": request.expected_ownership_generation, "idempotency_key": request.idempotency_key, "inputs": request.inputs}
    try:
        if action_id in {"oem.x.stop", "oem.abort_all", "oem.z.stop", "oem.z.abort", "oem.y.stop"}:
            payload = await runtime.connection.request_active_safety_interrupt(
                "invoke_operator_action_v2",
                expected_generation=request.expected_connection_generation,
                path_params={"action_id": action_id},
                json_data=action_payload,
            )
        else:
            payload = await runtime.connection.request_active(
                "invoke_operator_action_v2",
                expected_generation=request.expected_connection_generation,
                require_fresh=True,
                path_params={"action_id": action_id},
                json_data=action_payload,
            )
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc
    receipt = _validate(OperatorActionReceiptV2, payload)
    if receipt.action_id != action_id or receipt.command_id == "":
        raise HTTPException(status_code=502, detail="BioXP robot returned a mismatched v2 operator-action receipt")
    return receipt


@router.get("/operator-controls/v2/history", response_model=OperatorActionHistoryV2)
async def operator_action_history_v2(
    limit: int = Query(default=100, ge=1, le=200),
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> OperatorActionHistoryV2:
    snapshot = runtime.connection.snapshot()
    try:
        payload = await runtime.connection.request_active_query(
            "operator_action_history_v2",
            expected_generation=snapshot.generation,
            require_fresh=True,
            params={"limit": limit},
        )
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc
    return _validate(OperatorActionHistoryV2, payload)


@router.get("/operator-controls/v2/receipts/{command_id}", response_model=None)
async def operator_action_receipt_v2(
    command_id: str,
    detail: bool = Query(default=False),
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> OperatorActionReceiptV2 | OperatorActionReceiptDetailV2:
    snapshot = runtime.connection.snapshot()
    try:
        payload = await runtime.connection.request_active_query(
            "operator_action_receipt_v2",
            expected_generation=snapshot.generation,
            require_fresh=True,
            path_params={"command_id": command_id},
            params={"detail": detail},
        )
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc
    model = OperatorActionReceiptDetailV2 if detail else OperatorActionReceiptV2
    receipt = _validate(model, payload)
    if receipt.command_id != command_id:
        raise HTTPException(status_code=502, detail="BioXP robot returned a mismatched v2 command receipt")
    return receipt


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


@router.post("/operator-controls/pipettes/readback", response_model=PipetteReadbackResponse)
async def pipette_readback(
    request: PipetteReadbackRequest,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> PipetteReadbackResponse:
    snapshot = runtime.connection.snapshot()
    try:
        payload = await runtime.connection.request_active_query(
            "pipette_readback",
            expected_generation=snapshot.generation,
            require_fresh=True,
            json_data=request.model_dump(),
        )
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc
    return _validate(PipetteReadbackResponse, payload)


@router.get("/operator-controls/pipettes/application/status", response_model=PipetteApplicationStatus)
async def pipette_application_status(
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> PipetteApplicationStatus:
    snapshot = runtime.connection.snapshot()
    try:
        payload = await runtime.connection.request_active_query(
            "pipette_application_status",
            expected_generation=snapshot.generation,
            require_fresh=True,
        )
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc
    return _validate(PipetteApplicationStatus, payload)


@router.post("/operator-controls/pipettes/application/plan", response_model=PipetteApplicationPlanResponse)
async def pipette_application_plan(
    request: PipetteApplicationPlanRequest,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> PipetteApplicationPlanResponse:
    snapshot = runtime.connection.snapshot()
    try:
        payload = await runtime.connection.request_active(
            "pipette_application_plan",
            expected_generation=snapshot.generation,
            require_fresh=True,
            json_data=request.model_dump(exclude_none=True),
        )
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc
    return _validate(PipetteApplicationPlanResponse, payload)


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
    quarantine_reason = _resolve_action_quarantine(action_id)
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
        if action_id in {"oem.x.stop", "oem.abort_all", "oem.z.stop", "oem.z.abort", "oem.y.stop"}:
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
    limit: int = Query(default=100, ge=1, le=200),
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> OperatorActionHistory:
    snapshot = runtime.connection.snapshot()
    try:
        payload = await runtime.connection.request_active_query(
            "operator_action_history",
            expected_generation=snapshot.generation,
            require_fresh=True,
            params={"limit": limit},
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


@router.get("/operator-controls/reports/summary", response_model=OperatorReportSummaryV1)
async def operator_report_summary(
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
    report_filters: dict[str, Any] = Depends(_report_params),
) -> OperatorReportSummaryV1:
    return await _proxy_operator_report_model(
        runtime,
        "operator_report_summary",
        OperatorReportSummaryV1,
        params=report_filters,
    )


@router.get("/operator-controls/reports/commands", response_model=OperatorReportCommandPageV1)
async def operator_report_commands(
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
    report_filters: dict[str, Any] = Depends(_report_params),
) -> OperatorReportCommandPageV1:
    return await _proxy_operator_report_model(
        runtime,
        "operator_report_commands",
        OperatorReportCommandPageV1,
        params=report_filters,
    )


@router.get("/operator-controls/reports/commands/{command_id}", response_model=OperatorReportCommandDetailV1)
async def operator_report_command_detail(command_id: str, runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> OperatorReportCommandDetailV1:
    return await _proxy_operator_report_model(runtime, "operator_report_command_detail", OperatorReportCommandDetailV1, path_params={"command_id": command_id})


@router.get("/operator-controls/reports/commands/{command_id}/transitions", response_model=OperatorReportTransitionsV1)
async def operator_report_command_transitions(command_id: str, runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> OperatorReportTransitionsV1:
    return await _proxy_operator_report_model(runtime, "operator_report_command_transitions", OperatorReportTransitionsV1, path_params={"command_id": command_id})


@router.get("/operator-controls/reports/pipette", response_model=OperatorReportPipettePageV1)
async def operator_report_pipette(
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
    report_filters: dict[str, Any] = Depends(_report_params),
) -> OperatorReportPipettePageV1:
    return await _proxy_operator_report_model(
        runtime,
        "operator_report_pipette",
        OperatorReportPipettePageV1,
        params=report_filters,
    )


@router.get("/operator-controls/reports/pipette/{pipette_operation_id}", response_model=OperatorReportPipetteDetailV1)
async def operator_report_pipette_detail(pipette_operation_id: str, runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> OperatorReportPipetteDetailV1:
    return await _proxy_operator_report_model(runtime, "operator_report_pipette_detail", OperatorReportPipetteDetailV1, path_params={"pipette_operation_id": pipette_operation_id})


@router.get("/operator-controls/reports/pipette/{pipette_operation_id}/channels", response_model=OperatorReportPipetteChannelsV1)
async def operator_report_pipette_channels(pipette_operation_id: str, runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> OperatorReportPipetteChannelsV1:
    return await _proxy_operator_report_model(runtime, "operator_report_pipette_channels", OperatorReportPipetteChannelsV1, path_params={"pipette_operation_id": pipette_operation_id})


@router.get("/operator-controls/reports/pipette/{pipette_operation_id}/exchanges", response_model=OperatorReportPipetteExchangesV1)
async def operator_report_pipette_exchanges(pipette_operation_id: str, runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> OperatorReportPipetteExchangesV1:
    return await _proxy_operator_report_model(runtime, "operator_report_pipette_exchanges", OperatorReportPipetteExchangesV1, path_params={"pipette_operation_id": pipette_operation_id})


@router.get("/operator-controls/reports/events", response_model=OperatorReportEventsV1)
async def operator_report_events(
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
    report_filters: dict[str, Any] = Depends(_report_params),
) -> OperatorReportEventsV1:
    return await _proxy_operator_report_model(
        runtime,
        "operator_report_events",
        OperatorReportEventsV1,
        params=report_filters,
    )


@router.get("/operator-controls/reports/events/{event_id}", response_model=OperatorReportEventV1)
async def operator_report_event_detail(event_id: str, runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> OperatorReportEventV1:
    return await _proxy_operator_report_model(runtime, "operator_report_event_detail", OperatorReportEventV1, path_params={"event_id": event_id})


@router.get("/operator-controls/reports/pressure-streams", response_model=OperatorReportPressureStreamsV1)
async def operator_report_pressure_streams(
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
    report_filters: dict[str, Any] = Depends(_report_params),
) -> OperatorReportPressureStreamsV1:
    return await _proxy_operator_report_model(runtime, "operator_report_pressure_streams", OperatorReportPressureStreamsV1, params=report_filters)


@router.get("/operator-controls/reports/pressure-streams/{stream_session_id}", response_model=OperatorReportPressureDetailV1)
async def operator_report_pressure_detail(stream_session_id: str, runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> OperatorReportPressureDetailV1:
    return await _proxy_operator_report_model(runtime, "operator_report_pressure_detail", OperatorReportPressureDetailV1, path_params={"stream_session_id": stream_session_id})


@router.get("/operator-controls/reports/pressure-streams/{stream_session_id}/samples", response_model=OperatorReportPressureSamplesV1)
async def operator_report_pressure_samples(
    stream_session_id: str,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
    report_filters: dict[str, Any] = Depends(_report_params),
) -> OperatorReportPressureSamplesV1:
    return await _proxy_operator_report_model(runtime, "operator_report_pressure_samples", OperatorReportPressureSamplesV1, params=report_filters, path_params={"stream_session_id": stream_session_id})


@router.get("/operator-controls/audit-health", response_model=OperatorReportAuditHealthV1)
async def operator_report_audit_health(runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> OperatorReportAuditHealthV1:
    return await _proxy_operator_report_model(runtime, "operator_report_audit_health", OperatorReportAuditHealthV1)


@router.post("/operator-controls/reports/exports", response_model=OperatorReportExportV1)
async def operator_report_export_create(
    body: OperatorReportExportRequestV1,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> OperatorReportExportV1:
    try:
        snapshot = runtime.connection.snapshot()
        payload = await runtime.connection.request_active(
            "operator_report_export_create",
            expected_generation=snapshot.generation,
            require_fresh=True,
            json_data=body.model_dump(exclude_none=True),
        )
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc
    receipt = _validate(OperatorReportExportV1, payload)
    return _bms_export_download(receipt, receipt.export_id)


@router.get("/operator-controls/reports/exports/{export_id}", response_model=OperatorReportExportMetadataV1)
async def operator_report_export_detail(export_id: str, runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> OperatorReportExportMetadataV1:
    metadata = await _proxy_operator_report_model(runtime, "operator_report_export_detail", OperatorReportExportMetadataV1, path_params={"export_id": export_id})
    return _bms_export_download(metadata, export_id)


@router.get("/operator-controls/reports/exports/{export_id}/download", response_model=None)
async def operator_report_export_download(export_id: str, runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> Response:
    snapshot = runtime.connection.snapshot()
    try:
        artifact = await runtime.connection.request_active_bytes(
            "operator_report_export_download",
            expected_generation=snapshot.generation,
            require_fresh=True,
            path_params={"export_id": export_id},
        )
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc
    content_type = artifact.content_type.split(";", 1)[0].strip()
    if not content_type or any(ord(char) < 32 for char in content_type):
        content_type = "application/octet-stream"
    return Response(
        content=artifact.content,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="bioxp-report-{export_id}"',
            "X-Content-SHA256": artifact.sha256,
        },
    )

