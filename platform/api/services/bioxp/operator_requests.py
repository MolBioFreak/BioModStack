"""Typed request bodies BMS accepts and forwards to the BioXP robot.

Robot replies are relayed unchanged; BMS validates only what it sends.
"""
from __future__ import annotations

import json
import math
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, StrictFloat, StrictInt, field_validator, model_validator


class PipetteReadbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    include_data: StrictBool = False


class PipetteLoadTipPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal["load_tip"]
    tip_tray: str = Field(min_length=1, max_length=120)
    tip_well: str = Field(min_length=1, max_length=32)
    tip_type: StrictInt
    tip_location: Literal[0, 1, 2, 3]
    home_z_after: StrictBool = True


class PipetteMoveToWastePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal["move_to_waste"]


class PipetteDetectFluidPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal["detect_fluid"]
    fluid_class: Literal["TC", "MS", "OC", "RC", "STRIP"]


class PipettePlungerUpPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal["plunger_up"]


class PipettePlungerDownPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal["plunger_down"]


PipetteApplicationPlanRequest = Annotated[
    PipetteLoadTipPlanRequest
    | PipetteMoveToWastePlanRequest
    | PipetteDetectFluidPlanRequest
    | PipettePlungerUpPlanRequest
    | PipettePlungerDownPlanRequest,
    Field(discriminator="operation"),
]


class OperatorActionInvokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_connection_generation: StrictInt = Field(ge=1)
    expected_ownership_generation: StrictInt = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    inputs: dict[str, Any] = Field(default_factory=dict, max_length=64)


class OperatorAdmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_connection_generation: StrictInt = Field(ge=1)
    expected_ownership_generation: StrictInt = Field(ge=1)
    inputs: dict[str, Any] = Field(default_factory=dict, max_length=64)


class OperatorAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_connection_generation: StrictInt = Field(ge=1)
    expected_ownership_generation: StrictInt = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    verdict: Literal["pass", "fail"]
    note: str = Field(min_length=1, max_length=4000)


NonnegativeStrictInt = Annotated[StrictInt, Field(ge=0)]


SignedInt32 = Annotated[StrictInt, Field(ge=-(2**31), le=2**31 - 1)]


class OperatorYMoveStepsInputsV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    steps: StrictInt = Field(ge=-2_147_483_648, le=2_147_483_647)


class OperatorYMoveAbsoluteInputsV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target_steps: StrictInt = Field(ge=-2_147_483_648, le=2_147_483_647)


class OperatorEmptyInputsV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class OperatorMoveStepsInputsV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    steps: SignedInt32


class OperatorMoveAbsoluteInputsV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    position_steps: SignedInt32


class OperatorMoveXYInputsV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    x: SignedInt32
    y: SignedInt32


class OperatorDeckMoveInputsV1(BaseModel):
    """Finite semantic deck intent; the robot remains coordinate authority."""

    model_config = ConfigDict(extra="forbid", strict=True)
    target: str = Field(min_length=1, max_length=160, pattern=r"^[A-Z0-9][A-Z0-9_]*$")
    camera_offset: StrictBool

    @model_validator(mode="after")
    def bind_optional_camera_offset(self):
        if self.camera_offset and self.target in {"LOC_PARK", "LOC_TC_BARCODE", "LOC_RC_BARCODE"}:
            raise ValueError("optional camera offset is only valid for ordinary deck destinations")
        return self


def _canonical_board_epoch_map(value: dict[str, int]) -> dict[str, int]:
    if any(not key.isdecimal() or str(int(key)) != key for key in value):
        raise ValueError("board epoch keys must be canonical nonnegative decimal board IDs")
    return value


class OperatorActionRequestV2(BaseModel):
    """BMS-local connection fence plus the exact normal robot v2 body."""

    model_config = ConfigDict(extra="forbid", strict=True)
    expected_connection_generation: StrictInt = Field(ge=1)
    schema_version: Literal["bioxp.operator_action_request.v2"]
    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_ownership_generation: NonnegativeStrictInt
    expected_board_epoch_by_board: dict[str, NonnegativeStrictInt]
    inputs: dict[str, JsonValue] = Field(default_factory=dict, max_length=16)

    @field_validator("idempotency_key")
    @classmethod
    def bounded_idempotency_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 128:
            raise ValueError("idempotency_key must be at most 128 bytes")
        return value

    @field_validator("inputs")
    @classmethod
    def bounded_wire_scalars(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if "steps" in value:
            steps = value["steps"]
            if type(steps) is not int or not (-(2**31) <= steps <= 2**31 - 1):
                raise ValueError("steps must be a signed int32 integer")
        return value

    @field_validator("expected_board_epoch_by_board")
    @classmethod
    def canonical_board_epoch_keys(cls, value: dict[str, int]) -> dict[str, int]:
        return _canonical_board_epoch_map(value)


class OperatorInterruptRequestV1(BaseModel):
    """BMS connection fence plus the durable robot STOP idempotency key."""

    model_config = ConfigDict(extra="forbid", strict=True)
    expected_connection_generation: StrictInt = Field(ge=1)
    schema_version: Literal["bioxp.operator_interrupt_request.v1"]
    idempotency_key: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=500)
    observed_ownership_generation: NonnegativeStrictInt | None
    observed_board_epoch_by_board: dict[str, NonnegativeStrictInt]

    @field_validator("idempotency_key")
    @classmethod
    def bounded_interrupt_idempotency_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 128:
            raise ValueError("idempotency_key must be at most 128 bytes")
        return value

    @field_validator("reason")
    @classmethod
    def bounded_reason_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 500:
            raise ValueError("reason must be at most 500 bytes")
        return value

    @field_validator("observed_board_epoch_by_board")
    @classmethod
    def canonical_observed_board_epoch_keys(cls, value: dict[str, int]) -> dict[str, int]:
        return _canonical_board_epoch_map(value)


class OperatorXYMoveAbsoluteInputsV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    x_steps: SignedInt32
    y_steps: SignedInt32


class OperatorMethodRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_connection_generation: StrictInt = Field(ge=1)
    schema_version: Literal["bioxp.operator_method_request.v1"]
    idempotency_key: str = Field(min_length=1, max_length=128)
    method_action_id: Literal["oem.xy.move_absolute", "oem.xy.home"]
    expected_ownership_generation: NonnegativeStrictInt
    expected_board_epoch_by_board: dict[str, NonnegativeStrictInt]
    inputs: OperatorXYMoveAbsoluteInputsV1 | OperatorEmptyInputsV2

    @field_validator("idempotency_key")
    @classmethod
    def bounded_method_idempotency_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 128:
            raise ValueError("idempotency_key must be at most 128 bytes")
        return value

    @field_validator("expected_board_epoch_by_board")
    @classmethod
    def canonical_method_board_epoch_keys(cls, value: dict[str, int]) -> dict[str, int]:
        return _canonical_board_epoch_map(value)

    @model_validator(mode="after")
    def bind_inputs_to_method(self):
        expected_type = (
            OperatorXYMoveAbsoluteInputsV1
            if self.method_action_id == "oem.xy.move_absolute"
            else OperatorEmptyInputsV2
        )
        if not isinstance(self.inputs, expected_type):
            raise ValueError("method inputs do not match method_action_id")
        return self


class OperatorReportExportRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    format: Literal["json", "csv"] = "json"
    start: StrictFloat | StrictInt | None = None
    end: StrictFloat | StrictInt | None = None
    status: str | None = None
    operation: str | None = None
    action: str | None = None
    channel: StrictInt | None = Field(default=None, ge=0, le=3)
    limit: StrictInt = Field(default=1000, ge=1, le=100000)
