"""Fixed-path settings relay, retaining the full native projection."""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
from typing import Annotated, Literal
from services.bioxp.calibration_models import CalibrationSettingsRequest
from services.bioxp.errors import ConnectionStateError, RobotResponseError, RobotTransportError
from services.bioxp.runtime import BioXpRuntime
from .dependencies import get_bioxp_runtime, require_bioxp_mutation_access
from .operator_controls import _translate_robot_error

router = APIRouter(dependencies=[Depends(require_bioxp_mutation_access)])

class ManualTipSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_connection_generation: Annotated[int, Field(strict=True, ge=0)]
    tray: Literal[1, 2, 3, 4]

    @field_validator("tray", mode="before")
    @classmethod
    def strict_tray(cls, value):
        if type(value) is not int:
            raise ValueError("tray must be an integer")
        return value

class OperationParametersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_connection_generation: Annotated[int, Field(strict=True, ge=0)]
    CheckForStaticTipLoss: StrictBool | None = None
    LogPressure: StrictBool | None = None

    @model_validator(mode="after")
    def require_changed_flags(self):
        changed = self.model_fields_set - {"expected_connection_generation"}
        if not changed or any(getattr(self, key) is None for key in changed):
            raise ValueError("supply at least one Boolean pipette operation parameter; omit unchanged fields")
        return self

@router.post("/calibration-settings/manual-tip-set")
async def manual_tip_set(request: ManualTipSetRequest, runtime: BioXpRuntime = Depends(get_bioxp_runtime)):
    try:
        return await runtime.connection.request_active(
            "manual_tip_set", expected_generation=request.expected_connection_generation,
            require_fresh=False, json_data={"tray": request.tray})
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc

@router.get("/operation-parameters")
async def read_operation_parameters(
    expected_connection_generation: int = Query(ge=0), runtime: BioXpRuntime = Depends(get_bioxp_runtime),
):
    try:
        return await runtime.connection.request_active_v2_query(
            "operation_parameters", expected_generation=expected_connection_generation)
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc

@router.patch("/operation-parameters")
async def save_operation_parameters(request: OperationParametersRequest, runtime: BioXpRuntime = Depends(get_bioxp_runtime)):
    try:
        return await runtime.connection.request_active(
            "save_operation_parameters", expected_generation=request.expected_connection_generation,
            require_fresh=False,
            json_data=request.model_dump(mode="json", exclude_unset=True, exclude={"expected_connection_generation"}))
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc

@router.get("/calibration-settings")
async def read_calibration_settings(
    expected_connection_generation: int = Query(ge=0),
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
):
    try:
        return await runtime.connection.request_active_v2_query(
            "calibration_settings", expected_generation=expected_connection_generation)
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc

@router.patch("/calibration-settings")
async def save_calibration_settings(
    request: CalibrationSettingsRequest,
    runtime: BioXpRuntime = Depends(get_bioxp_runtime),
):
    try:
        return await runtime.connection.request_active(
            "save_calibration_settings", expected_generation=request.expected_connection_generation,
            require_fresh=False,
            json_data=request.model_dump(mode="json", exclude_unset=True, exclude={"expected_connection_generation"}))
    except (ConnectionStateError, RobotResponseError, RobotTransportError) as exc:
        raise _translate_robot_error(exc) from exc
