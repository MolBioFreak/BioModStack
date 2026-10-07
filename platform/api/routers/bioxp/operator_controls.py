"""BMS relay for the robot-owned BioXP operator plane.

BMS validates only the requests it sends. Robot replies are returned unchanged:
the robot owns action definitions, admission, receipts and reports, so BMS does
not re-validate or reshape them.
"""
from __future__ import annotations

import asyncio
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response

from services.bioxp.errors import ConnectionStateError, RobotResponseError, RobotTimeoutError, RobotTransportError
from services.bioxp.operator_requests import (
    OperatorActionInvokeRequest,
    OperatorActionRequestV2,
    OperatorAdmissionRequest,
    OperatorAssessmentRequest,
    OperatorDeckMoveInputsV1,
    OperatorDeckMoveToWellInputsV2,
    OperatorEmptyInputsV2,
    OperatorInterruptRequestV1,
    OperatorMoveAbsoluteInputsV2,
    OperatorMoveStepsInputsV2,
    OperatorMoveXYInputsV2,
    OperatorReportExportRequestV1,
    OperatorYMoveAbsoluteInputsV2,
    OperatorYMoveStepsInputsV2,
    PipetteApplicationPlanRequest,
    PipetteReadbackRequest,
)
from services.bioxp.runtime import BioXpRuntime

from .dependencies import get_bioxp_runtime, require_bioxp_mutation_access

router = APIRouter()

_ROBOT_ERRORS = (ConnectionStateError, RobotResponseError, RobotTransportError)
_INTERRUPT_ACTIONS = {"oem.x.stop", "oem.y.stop", "oem.z.stop", "oem.abort_all"}


def _is_exact_xz_action(action_id: str) -> bool:
    return action_id.startswith(("oem.x.", "oem.z."))


def _translate_robot_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RobotResponseError):
        status = exc.status_code if 400 <= exc.status_code <= 599 else 502
        detail = exc.detail
        if isinstance(detail, dict) and set(detail) == {"detail"}:
            detail = detail["detail"]
        return HTTPException(status_code=status, detail=detail)
    if isinstance(exc, ConnectionStateError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, RobotTimeoutError):
        return HTTPException(
            status_code=504,
            detail={
                "error": "bioxp_robot_timeout",
                "message": str(exc) or exc.__class__.__name__,
                "robot_response_received": False,
                "dispatch_state": exc.dispatch_state,
                "status_recovery": exc.status_recovery,
            },
        )
    return HTTPException(
        status_code=502,
        detail={
            "error": "bioxp_robot_transport_error",
            "message": str(exc) or exc.__class__.__name__,
            "robot_response_received": False,
        },
    )


async def _relay(call: Any) -> Any:
    try:
        return await call
    except _ROBOT_ERRORS as exc:
        raise _translate_robot_error(exc) from exc


def _query(runtime: BioXpRuntime, route: str, *, require_fresh: bool = True, **kwargs: Any) -> Any:
    return _relay(runtime.connection.request_active_query(
        route, expected_generation=runtime.connection.generation, require_fresh=require_fresh, **kwargs,
    ))


def _v2_query(runtime: BioXpRuntime, route: str, **kwargs: Any) -> Any:
    return _relay(runtime.connection.request_active_v2_query(
        route, expected_generation=runtime.connection.generation, **kwargs,
    ))


def _robot_request_body(request: Any) -> dict[str, Any]:
    return request.model_dump(exclude={"expected_connection_generation"}, mode="json")


# Full passive readers use the existing generation lease, not action admission.
@router.get("/operator-controls/readers/settings", response_model=None)
async def operator_reader_settings(
    expected_connection_generation: int = Query(gt=0),
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> Any:
    return await _relay(runtime.connection.request_active_query(
        "oem_machine_config", expected_generation=expected_connection_generation,
        require_fresh=False,
    ))


@router.get("/operator-controls/readers/position-table", response_model=None)
async def operator_reader_position_table(
    expected_connection_generation: int = Query(gt=0),
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> Any:
    return await _relay(runtime.connection.request_active_query(
        "oem_position_table", expected_generation=expected_connection_generation,
        require_fresh=False,
    ))


@router.get("/operator-controls/updates", response_model=None)
async def operator_updates(
    request: Request,
    response: Response,
    expected_connection_generation: int = Query(gt=0),
    after_sequence: int | None = Query(default=None, ge=0),
    after_pose_sequence: int | None = Query(default=None, ge=0),
    wait_s: float = Query(default=25.0, ge=0, le=25, allow_inf_nan=False),
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> Any:
    response.headers["Cache-Control"] = "no-store"
    call = asyncio.create_task(_relay(runtime.connection.operator_updates(
        expected_generation=expected_connection_generation,
        after_sequence=after_sequence, after_pose_sequence=after_pose_sequence, wait_s=wait_s,
    )))

    async def disconnected() -> None:
        while (await request.receive())["type"] != "http.disconnect":
            pass

    disconnect = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait((call, disconnect), return_when=asyncio.FIRST_COMPLETED)
        if call in done:
            return call.result()
        raise asyncio.CancelledError()
    finally:
        call.cancel()
        disconnect.cancel()
        await asyncio.gather(call, disconnect, return_exceptions=True)


# --- V2 canonical operator plane -------------------------------------------------

_V2_NORMAL_INPUT_TYPES = {
    "meta.activate_motion": OperatorEmptyInputsV2,
    "meta.recover_motion_non_homing": OperatorEmptyInputsV2,
    "oem.x.manual_panel_home": OperatorEmptyInputsV2,
    "oem.x.move_steps": OperatorMoveStepsInputsV2,
    "oem.x.move_absolute": OperatorMoveAbsoluteInputsV2,
    "oem.y.move_steps": OperatorYMoveStepsInputsV2,
    "oem.y.move_absolute": OperatorYMoveAbsoluteInputsV2,
    "oem.y.manual_panel_home": OperatorEmptyInputsV2,
    "oem.z.manual_home": OperatorEmptyInputsV2,
    "oem.z.diagnostic_home_axis": OperatorEmptyInputsV2,
    "oem.z.clear": OperatorEmptyInputsV2,
    "oem.z.move_steps": OperatorMoveStepsInputsV2,
    "oem.z.move_absolute": OperatorMoveAbsoluteInputsV2,
    "oem.xy.move_absolute": OperatorMoveXYInputsV2,
    "oem.xy.home": OperatorEmptyInputsV2,
    "oem.deck.collect_authority": OperatorEmptyInputsV2,
    "oem.deck.move_to_location": OperatorDeckMoveInputsV1,
    "oem.deck.move_to_well": OperatorDeckMoveToWellInputsV2,
}


@router.get("/operator-controls/v2/catalog", response_model=None)
async def operator_control_catalog_v2(runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> Any:
    return await _v2_query(runtime, "operator_control_catalog_v2",
                           params={"schema_version": "bioxp.operator_control_catalog.v2"})


@router.get("/operator-controls/v2/dashboard", response_model=None)
async def operator_dashboard_v2(runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> Any:
    return await _v2_query(runtime, "operator_dashboard_v2",
                           params={"schema_version": "bioxp.operator_dashboard.v2"})


@router.post(
    "/operator-controls/v2/actions/{action_id}",
    response_model=None,
    status_code=202,
    dependencies=[Depends(require_bioxp_mutation_access)],
)
async def invoke_operator_action_v2(
    action_id: str,
    request: OperatorActionRequestV2,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> Any:
    input_type = _V2_NORMAL_INPUT_TYPES.get(action_id)
    if input_type is None:
        raise HTTPException(status_code=404, detail="Unknown BMS v2 normal operator action")
    try:
        inputs = input_type.model_validate(request.inputs).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Action inputs do not match the action schema") from exc
    body = request.model_dump(exclude={"expected_connection_generation", "inputs"}, mode="json")
    body["inputs"] = inputs
    return await _relay(runtime.connection.request_active_v2_enqueue(
        "invoke_operator_action_v2",
        expected_generation=request.expected_connection_generation,
        path_params={"action_id": action_id},
        json_data=body,
    ))


@router.post(
    "/operator-controls/v2/interrupts/{action_id}",
    response_model=None,
    dependencies=[Depends(require_bioxp_mutation_access)],
)
async def interrupt_operator_action_v1(
    action_id: str,
    request: OperatorInterruptRequestV1,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> Any:
    """Stops are always relayed and always safe to press again."""
    if action_id not in _INTERRUPT_ACTIONS:
        raise HTTPException(status_code=404, detail="Unknown BMS v2 interrupt action")
    return await _relay(runtime.connection.request_active_safety_interrupt(
        "interrupt_operator_action_v1",
        expected_generation=request.expected_connection_generation,
        path_params={"action_id": action_id},
        json_data=_robot_request_body(request),
    ))


@router.get("/operator-controls/v2/requests/{key}", response_model=None)
async def operator_command_request_v2(
    key: str,
    expected_connection_generation: int = Query(gt=0),
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> Any:
    """Resolve an admission key to its command, then return the current receipt."""
    if not 1 <= len(key) <= 200:
        raise HTTPException(status_code=422, detail="Invalid command request key")
    identity = await _relay(runtime.connection.request_active_v2_query(
        "operator_command_identity", expected_generation=expected_connection_generation,
        path_params={"key": key},
    ))
    command_id = identity.get("command_id") if isinstance(identity, dict) else None
    if not isinstance(command_id, str) or not command_id:
        raise HTTPException(status_code=502, detail="BioXP robot returned no command for this request key")
    return await _relay(runtime.connection.request_active_v2_query(
        "operator_action_receipt_v2", expected_generation=expected_connection_generation,
        path_params={"command_id": command_id}, params={"detail": False},
    ))


@router.get("/operator-controls/v2/receipts/{command_id}", response_model=None)
async def operator_action_receipt_v2(
    command_id: str,
    detail: bool = Query(default=False),
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> Any:
    return await _v2_query(runtime, "operator_action_receipt_v2",
                           path_params={"command_id": command_id}, params={"detail": detail})


@router.get("/operator-controls/v2/commands/{command_id}", response_model=None)
async def operator_command_status_v2(
    command_id: str,
    detail: bool = Query(default=False),
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> Any:
    return await _v2_query(runtime, "operator_command_status_v2",
                           path_params={"command_id": command_id}, params={"detail": detail})


# --- Legacy (V1) operator plane, still used by manual and Advanced controls ------

@router.get("/operator-controls/catalog", response_model=None)
async def operator_control_catalog(
    z_target_steps: int | None = Query(default=None, ge=-2147483648, le=2147483647),
    view: Literal["full", "metadata", "assessment"] = Query(default="full"),
    assessment_base: str | None = Query(default=None, max_length=64),
    canonical_assessment_base: str | None = Query(default=None, max_length=64),
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> Any:
    generation = runtime.connection.generation
    view_params = {"view": view} if view != "full" else {}
    queries = [asyncio.create_task(query) for query in (
        runtime.connection.request_active_query(
            "operator_control_catalog",
            params={**view_params,
                    **({"assessment_base": assessment_base} if view == "assessment" and isinstance(assessment_base, str) else {}),
                    **({"z_target_steps": z_target_steps} if z_target_steps is not None else {})} or None,
            expected_generation=generation,
            require_fresh=False,
        ),
        runtime.connection.request_active_v2_query(
            "operator_control_catalog_v2",
            expected_generation=generation,
            params={"schema_version": "bioxp.operator_control_catalog.v2", **view_params,
                    **({"assessment_base": canonical_assessment_base}
                       if view == "assessment" and isinstance(canonical_assessment_base, str) else {})},
        ),
    )]
    try:
        catalog, canonical = await asyncio.gather(*queries)
    except _ROBOT_ERRORS as exc:
        raise _translate_robot_error(exc) from exc
    finally:
        # A failed sibling must not strand the other query's connection lease.
        for query in queries:
            if not query.done():
                query.cancel()
        await asyncio.gather(*queries, return_exceptions=True)
    if view != "full" and any(not isinstance(body, dict) or body.get("catalog_view") != view for body in (catalog, canonical)):
        raise HTTPException(426, detail="Robot catalog split-view release required; no full-poll fallback")
    if view == "assessment" and any(
        isinstance(requested, str) and (not isinstance(body, dict) or "assessment_revision" not in body)
        for requested, body in ((assessment_base, catalog), (canonical_assessment_base, canonical))
    ):
        raise HTTPException(426, detail="Robot catalog update release required; no full-poll fallback")
    if isinstance(catalog, dict):
        catalog = {**catalog, "canonical": canonical}
    return catalog


@router.get("/operator-controls/dashboard", response_model=None)
async def operator_dashboard(runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> Any:
    return await _query(runtime, "operator_dashboard", require_fresh=False)


@router.post(
    "/operator-controls/actions/{action_id}/admission",
    response_model=None,
    dependencies=[Depends(require_bioxp_mutation_access)],
)
async def operator_action_admission(
    action_id: str,
    request: OperatorAdmissionRequest,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> Any:
    return await _relay(runtime.connection.request_active_query(
        "operator_action_admission",
        expected_generation=request.expected_connection_generation,
        require_fresh=not _is_exact_xz_action(action_id),
        path_params={"action_id": action_id},
        json_data={"expected_generation": request.expected_ownership_generation, "inputs": request.inputs},
    ))


@router.post(
    "/operator-controls/actions/{action_id}",
    response_model=None,
    dependencies=[Depends(require_bioxp_mutation_access)],
)
async def invoke_operator_action(
    action_id: str,
    request: OperatorActionInvokeRequest,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> Any:
    body = {
        "expected_generation": request.expected_ownership_generation,
        "idempotency_key": request.idempotency_key,
        "inputs": request.inputs,
    }
    common = {
        "expected_generation": request.expected_connection_generation,
        "path_params": {"action_id": action_id},
        "json_data": body,
    }
    connection = runtime.connection
    if action_id in _INTERRUPT_ACTIONS:
        call = connection.request_active_safety_interrupt("invoke_operator_action", **common)
    elif _is_exact_xz_action(action_id):
        call = connection.request_active_oem_action("invoke_operator_action", **common)
    else:
        call = connection.request_active("invoke_operator_action", require_fresh=True, **common)
    return await _relay(call)


@router.get("/operator-controls/history", response_model=None)
async def operator_action_history(
    limit: int = Query(default=100, ge=1, le=200),
    cursor: str | None = Query(default=None, min_length=1, max_length=1024),
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> Any:
    return await _query(runtime, "operator_action_history",
                        params={"limit": limit, **({"cursor": cursor} if cursor is not None else {})})


@router.get("/operator-controls/receipts/{command_id}", response_model=None)
async def operator_action_receipt(command_id: str, runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> Any:
    return await _query(runtime, "operator_action_receipt", require_fresh=False,
                        path_params={"command_id": command_id})


@router.post(
    "/operator-controls/receipts/{command_id}/assessment",
    response_model=None,
    dependencies=[Depends(require_bioxp_mutation_access)],
)
async def assess_operator_action(
    command_id: str,
    request: OperatorAssessmentRequest,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> Any:
    return await _relay(runtime.connection.request_active(
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
    ))


# --- Direct pipette queries -------------------------------------------------------

def _direct_liquid_ingress(request: Request, allowed: set[str]) -> None:
    if (set(request.query_params) != allowed
            or any(len(request.query_params.getlist(name)) != 1 for name in allowed)
            or len(request.headers.getlist("idempotency-key")) != 1):
        raise HTTPException(status_code=422, detail="Invalid or ambiguous direct-liquid parameters")


_IDEMPOTENCY_HEADER = Header(..., alias="Idempotency-Key", pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{7,199}$")


@router.get("/operator-controls/pipettes/requests", response_model=None)
async def pipette_request_lookup(
    request: Request,
    response: Response,
    request_kind: Literal["readback", "application_plan"] = Query(...),
    expected_connection_generation: int = Query(..., ge=1),
    idempotency_key: str = _IDEMPOTENCY_HEADER,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> Any:
    _direct_liquid_ingress(request, {"request_kind", "expected_connection_generation"})
    response.headers["Cache-Control"] = "no-store"
    try:
        return await runtime.connection.request_active_query(
            "pipette_request_lookup", expected_generation=expected_connection_generation,
            require_fresh=False, params={"request_kind": request_kind},
            json_data={"idempotency_key": idempotency_key},
        )
    except RobotResponseError as exc:
        # "conflict" and "unavailable" lookups are answers, not failures.
        if exc.status_code not in {409, 503}:
            raise _translate_robot_error(exc) from exc
        response.status_code = exc.status_code
        return exc.detail
    except _ROBOT_ERRORS as exc:
        raise _translate_robot_error(exc) from exc


@router.post("/operator-controls/pipettes/readback", response_model=None)
async def pipette_readback(
    request: PipetteReadbackRequest,
    http_request: Request,
    expected_connection_generation: int = Query(..., ge=1),
    idempotency_key: str = _IDEMPOTENCY_HEADER,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> Any:
    """Explicit hardware queries, not activation, initialization or liquid motion."""
    _direct_liquid_ingress(http_request, {"expected_connection_generation"})
    return await _relay(runtime.connection.request_active_query(
        "pipette_readback",
        expected_generation=expected_connection_generation,
        require_fresh=True,
        json_data={**request.model_dump(), "idempotency_key": idempotency_key},
    ))


@router.get("/operator-controls/pipettes/application/status", response_model=None)
async def pipette_application_status(runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> Any:
    return await _query(runtime, "pipette_application_status")


@router.post("/operator-controls/pipettes/application/plan", response_model=None)
async def pipette_application_plan(
    request: PipetteApplicationPlanRequest,
    http_request: Request,
    expected_connection_generation: int = Query(..., ge=1),
    idempotency_key: str = _IDEMPOTENCY_HEADER,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
) -> Any:
    """No-motion planning; unlike readback, no hardware query."""
    _direct_liquid_ingress(http_request, {"expected_connection_generation"})
    return await _relay(runtime.connection.request_active(
        "pipette_application_plan",
        expected_generation=expected_connection_generation,
        require_fresh=True,
        json_data={**request.model_dump(exclude_none=True), "idempotency_key": idempotency_key},
    ))


# --- Robot-owned reports (read-only relay) ------------------------------------------

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
    lifecycle_attempt_id: str | None = None,
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
    values = dict(locals())
    return {key: value for key, value in values.items() if value is not None}


def _paging(limit: int, cursor: str | None) -> dict[str, Any]:
    return {"limit": limit, **({"cursor": cursor} if cursor else {})}


def _bms_download_link(item: Any) -> Any:
    """Point export downloads at the BMS relay route instead of the robot."""
    if isinstance(item, dict) and item.get("download") and isinstance(item.get("export_id"), str):
        return {**item, "download": f"/api/bioxp/operator-controls/reports/exports/{quote(item['export_id'], safe='')}/download"}
    return item


@router.get("/operator-controls/reports/summary", response_model=None)
async def operator_report_summary(runtime: BioXpRuntime = Depends(get_bioxp_runtime),
                                  report_filters: dict[str, Any] = Depends(_report_params)) -> Any:
    return await _query(runtime, "operator_report_summary", params=report_filters)


@router.get("/operator-controls/reports/commands", response_model=None)
async def operator_report_commands(runtime: BioXpRuntime = Depends(get_bioxp_runtime),
                                   report_filters: dict[str, Any] = Depends(_report_params)) -> Any:
    return await _query(runtime, "operator_report_commands", params=report_filters)


@router.get("/operator-controls/reports/commands/{command_id}", response_model=None)
async def operator_report_command_detail(command_id: str, runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> Any:
    return await _query(runtime, "operator_report_command_detail", path_params={"command_id": command_id})


@router.get("/operator-controls/reports/commands/{command_id}/transitions", response_model=None)
async def operator_report_command_transitions(command_id: str, runtime: BioXpRuntime = Depends(get_bioxp_runtime),
                                              limit: int = Query(default=100, ge=1, le=1000),
                                              cursor: str | None = None) -> Any:
    return await _query(runtime, "operator_report_command_transitions",
                        params=_paging(limit, cursor), path_params={"command_id": command_id})


@router.get("/operator-controls/reports/commands/{command_id}/evidence", response_model=None)
async def operator_report_command_evidence(command_id: str, runtime: BioXpRuntime = Depends(get_bioxp_runtime),
                                           limit: int = Query(default=100, ge=1, le=1000),
                                           cursor: str | None = None) -> Any:
    return await _query(runtime, "operator_report_command_evidence",
                        params=_paging(limit, cursor), path_params={"command_id": command_id})


@router.get("/operator-controls/reports/pipette", response_model=None)
async def operator_report_pipette(runtime: BioXpRuntime = Depends(get_bioxp_runtime),
                                  report_filters: dict[str, Any] = Depends(_report_params)) -> Any:
    return await _query(runtime, "operator_report_pipette", params=report_filters)


@router.get("/operator-controls/reports/pipette/{pipette_operation_id}", response_model=None)
async def operator_report_pipette_detail(pipette_operation_id: str,
                                         runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> Any:
    return await _query(runtime, "operator_report_pipette_detail",
                        path_params={"pipette_operation_id": pipette_operation_id})


@router.get("/operator-controls/reports/pipette/{pipette_operation_id}/channels", response_model=None)
async def operator_report_pipette_channels(pipette_operation_id: str, runtime: BioXpRuntime = Depends(get_bioxp_runtime),
                                           limit: int = Query(default=100, ge=1, le=1000),
                                           cursor: str | None = None) -> Any:
    return await _query(runtime, "operator_report_pipette_channels",
                        params=_paging(limit, cursor), path_params={"pipette_operation_id": pipette_operation_id})


@router.get("/operator-controls/reports/pipette/{pipette_operation_id}/exchanges", response_model=None)
async def operator_report_pipette_exchanges(pipette_operation_id: str, runtime: BioXpRuntime = Depends(get_bioxp_runtime),
                                            limit: int = Query(default=100, ge=1, le=1000),
                                            cursor: str | None = None) -> Any:
    return await _query(runtime, "operator_report_pipette_exchanges",
                        params=_paging(limit, cursor), path_params={"pipette_operation_id": pipette_operation_id})


@router.get("/operator-controls/reports/events", response_model=None)
async def operator_report_events(runtime: BioXpRuntime = Depends(get_bioxp_runtime),
                                 report_filters: dict[str, Any] = Depends(_report_params)) -> Any:
    return await _query(runtime, "operator_report_events", params=report_filters)


@router.get("/operator-controls/reports/events/{event_id}", response_model=None)
async def operator_report_event_detail(event_id: str, runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> Any:
    return await _query(runtime, "operator_report_event_detail", path_params={"event_id": event_id})


@router.get("/operator-controls/reports/pressure-streams", response_model=None)
async def operator_report_pressure_streams(runtime: BioXpRuntime = Depends(get_bioxp_runtime),
                                           report_filters: dict[str, Any] = Depends(_report_params)) -> Any:
    return await _query(runtime, "operator_report_pressure_streams", params=report_filters)


@router.get("/operator-controls/reports/pressure-streams/{stream_session_id}", response_model=None)
async def operator_report_pressure_detail(stream_session_id: str,
                                          runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> Any:
    return await _query(runtime, "operator_report_pressure_detail",
                        path_params={"stream_session_id": stream_session_id})


@router.get("/operator-controls/reports/pressure-streams/{stream_session_id}/samples", response_model=None)
async def operator_report_pressure_samples(stream_session_id: str, runtime: BioXpRuntime = Depends(get_bioxp_runtime),
                                           report_filters: dict[str, Any] = Depends(_report_params)) -> Any:
    return await _query(runtime, "operator_report_pressure_samples", params=report_filters,
                        path_params={"stream_session_id": stream_session_id})


@router.get("/operator-controls/audit-health", response_model=None)
async def operator_report_audit_health(runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> Any:
    return await _query(runtime, "operator_report_audit_health")


@router.post("/operator-controls/reports/exports", response_model=None)
async def operator_report_export_create(body: OperatorReportExportRequestV1,
                                        runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> Any:
    receipt = await _relay(runtime.connection.request_active(
        "operator_report_export_create",
        expected_generation=runtime.connection.generation,
        require_fresh=True,
        json_data=body.model_dump(exclude_none=True),
    ))
    return _bms_download_link(receipt)


@router.get("/operator-controls/reports/exports", response_model=None)
async def operator_report_export_list(runtime: BioXpRuntime = Depends(get_bioxp_runtime),
                                      limit: int = Query(default=100, ge=1, le=1000)) -> Any:
    listing = await _query(runtime, "operator_report_export_list", params={"limit": limit})
    if isinstance(listing, dict) and isinstance(listing.get("items"), list):
        listing = {**listing, "items": [_bms_download_link(item) for item in listing["items"]]}
    return listing


@router.get("/operator-controls/reports/exports/{export_id}", response_model=None)
async def operator_report_export_detail(export_id: str, runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> Any:
    return _bms_download_link(await _query(runtime, "operator_report_export_detail",
                                           path_params={"export_id": export_id}))


@router.get("/operator-controls/reports/exports/{export_id}/download", response_model=None)
async def operator_report_export_download(export_id: str, runtime: BioXpRuntime = Depends(get_bioxp_runtime)) -> Response:
    artifact = await _relay(runtime.connection.request_active_bytes(
        "operator_report_export_download",
        expected_generation=runtime.connection.generation,
        require_fresh=True,
        path_params={"export_id": export_id},
    ))
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
