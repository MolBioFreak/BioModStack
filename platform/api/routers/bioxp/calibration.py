"""Fixed-path settings relay, retaining the full native projection."""
from fastapi import APIRouter, Depends, Query
from services.bioxp.calibration_models import CalibrationSettingsRequest
from services.bioxp.errors import ConnectionStateError, RobotResponseError, RobotTransportError
from services.bioxp.runtime import BioXpRuntime
from .dependencies import get_bioxp_runtime, require_bioxp_mutation_access
from .operator_controls import _translate_robot_error

router = APIRouter(dependencies=[Depends(require_bioxp_mutation_access)])

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
